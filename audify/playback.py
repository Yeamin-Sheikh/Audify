"""
Low-latency streaming speech pipeline for Audify.

Edge TTS audio is decoded and played while it is still downloading, instead
of waiting for a finished MP3 file:

    producer (asyncio loop)      decoder thread            player (worker thread)
    Edge TTS websocket     --->  miniaudio MP3 -> PCM  --->  pygame channel queue
         MP3 bytes                    PCM blocks               gapless playback

Response-time optimizations:
  * A persistent _ReusableTCPConnector caches DNS lookups (speech.platform.bing.com)
    and reuses SSL state across chunks and across clipboard copies, avoiding a cold
    DNS + SSL context setup on every chunk.
  * Startup pre-warming computes and caches the Sec-MS-GEC v2 token (5-minute LRU
    window in edge-tts 7.3.1+) and warms the DNS cache before the first Ctrl+C.
  * Multi-chunk sessions pipeline up to 2 chunks concurrently via asyncio.Semaphore(2),
    so chunk N+1 finishes its WebSocket handshake and buffers audio while chunk N plays.
  * 50ms decode blocks (DECODE_BLOCK_SECONDS = 0.05) minimize time-to-first-audio.

Everything stays in memory (no temp files). A session can be cancelled at
any point (new text copied, stop hotkey) and every stage winds down promptly.
"""
from __future__ import annotations

import asyncio
import queue
import socket
import threading
from typing import Callable

import aiohttp
import edge_tts
from edge_tts.communicate import _SSL_CTX
from edge_tts.drm import DRM
import miniaudio

# Network limits: fail fast when offline instead of hanging the reader
CONNECT_TIMEOUT = 5    # seconds to open the websocket
RECEIVE_TIMEOUT = 10   # seconds of silence from the server before giving up
MAX_ATTEMPTS = 3

# Number of chunks to synthesize concurrently so subsequent chunks are already
# buffered in memory before the currently playing chunk finishes
MAX_CONCURRENT_CHUNKS = 2

# 50ms of audio per decoded block: starts playback ~50ms faster than 100ms blocks
DECODE_BLOCK_SECONDS = 0.05

_SPEECH_HOST = "speech.platform.bing.com"
_SPEECH_PORT = 443


class _ReusableTCPConnector(aiohttp.TCPConnector):
    """Persistent TCPConnector that ignores per-session close() calls from edge-tts.

    edge_tts.Communicate creates an aiohttp.ClientSession(connector=self.connector)
    with connector_owner=True on each chunk, which normally closes the connector
    when the chunk finishes and discards the DNS cache. Overriding close() as a
    no-op keeps the DNS cache (ttl_dns_cache=300) warm across all chunks and sessions.
    """

    async def close(self, *, abort_ssl: bool = False) -> None:
        # Intentionally keep the shared connector open across Communicate.stream() calls.
        return None


_shared_connector: _ReusableTCPConnector | None = None
_shared_connector_loop: asyncio.AbstractEventLoop | None = None


def _get_shared_connector() -> _ReusableTCPConnector:
    """Return (or lazily create) the persistent TCPConnector bound to the running loop."""
    global _shared_connector, _shared_connector_loop
    loop = asyncio.get_running_loop()
    if _shared_connector is None or _shared_connector_loop is not loop:
        _shared_connector = _ReusableTCPConnector(
            limit=8,
            ttl_dns_cache=300,
            use_dns_cache=True,
            ssl=_SSL_CTX,
            enable_cleanup_closed=True,
        )
        _shared_connector_loop = loop
    return _shared_connector


def prewarm_edge_tts(loop: asyncio.AbstractEventLoop) -> None:
    """Pre-compute the Sec-MS-GEC v2 token and resolve the Edge TTS host in the background.

    Called once at daemon startup so the very first Ctrl+C skips cold token
    generation and cold DNS resolution.
    """

    async def _warm() -> None:
        try:
            # 1. Warm the 5-minute LRU cache for Sec-MS-GEC v2 token in edge-tts 7.3.1+
            DRM.generate_sec_ms_gec()
            # 2. Create the shared connector on the event loop
            _get_shared_connector()
            # 3. Warm the OS and asyncio DNS cache for speech.platform.bing.com
            await loop.getaddrinfo(
                _SPEECH_HOST, _SPEECH_PORT, family=socket.AF_INET, type=socket.SOCK_STREAM
            )
        except Exception:
            pass

    asyncio.run_coroutine_threadsafe(_warm(), loop)


def rate_to_speed(rate: str) -> float:
    """Edge-style rate ("+50%") -> speed multiplier (1.5)."""
    try:
        return 1.0 + int(rate.strip().rstrip("%")) / 100.0
    except ValueError:
        return 1.0


_MALE_VOICE_MARKERS: tuple[str, ...] = (
    "Andrew", "Brian", "Christopher", "Guy", "Steffan",
    "Eric", "Roger", "Ryan", "Thomas", "Pradeep", "Bashkar", "Madhur", "Asad", "Hamed",
)


def resolve_voice_for_text(text: str, default_voice: str) -> str:
    """Select native Bangladeshi Bengali or Indian Hindi neural voice when script is detected."""
    bn_count = 0
    hi_count = 0
    for ch in text:
        cp = ord(ch)
        if cp in (0x0964, 0x0965):
            continue
        if 0x0980 <= cp <= 0x09FF:
            bn_count += 1
        elif 0x0900 <= cp <= 0x097F:
            hi_count += 1
    if bn_count == 0 and hi_count == 0:
        return default_voice
    is_male = any(m.lower() in default_voice.lower() for m in _MALE_VOICE_MARKERS)
    if bn_count >= hi_count:
        return default_voice if default_voice.lower().startswith("bn-") else ("bn-BD-PradeepNeural" if is_male else "bn-BD-NabanitaNeural")
    return default_voice if default_voice.lower().startswith("hi-") else ("hi-IN-MadhurNeural" if is_male else "hi-IN-SwaraNeural")


def resolve_rate_for_voice(voice: str, configured_rate: str) -> str:
    """Keep Bengali and Hindi neural voices at natural +25% cadence."""
    if voice.lower().startswith(("bn-", "hi-")):
        try:
            if int(configured_rate.strip().rstrip("%")) > 25:
                return "+25%"
        except Exception:
            return "+25%"
    return configured_rate


class _ChunkSource(miniaudio.StreamableSource):
    """Hands one chunk's MP3 bytes to the decoder as they arrive from the network.

    Returns whatever is buffered rather than waiting for the full request size,
    otherwise the decoder's 64KB reads would stall until the whole chunk arrived.
    """

    def __init__(self, feed: queue.Queue, cancelled: threading.Event) -> None:
        self._feed = feed
        self._cancelled = cancelled
        self._buf = b""
        self._done = False

    def read(self, num_bytes: int) -> bytes:
        while not self._buf and not self._done:
            if self._cancelled.is_set():
                return b""
            try:
                item = self._feed.get(timeout=0.05)
            except queue.Empty:
                continue
            if item is None:
                self._done = True
            else:
                self._buf = item
        out, self._buf = self._buf[:num_bytes], self._buf[num_bytes:]
        return out


class SpeechSession:
    """Synthesizes, decodes and buffers one piece of text, chunk by chunk.

    Decoded audio arrives on ``pcm`` as ``(chunk_index, pcm_bytes)`` items,
    followed by ``None`` once everything has been decoded (or cancelled).
    """

    def __init__(
        self,
        chunks: list[str],
        voice: str,
        rate: str,
        loop: asyncio.AbstractEventLoop,
        mixer_format: tuple[int, int],
        resolve_text: Callable[[str], str] = lambda t: t,
        on_error: Callable[[str], None] | None = None,
    ) -> None:
        self.chunks = chunks
        self.voice = voice
        self.rate = rate
        self.pcm: queue.Queue[tuple[int, bytes] | None] = queue.Queue()
        self.cancelled = threading.Event()
        self._loop = loop
        self._sample_rate, self._channels = mixer_format
        self._resolve_text = resolve_text
        self._on_error = on_error
        self._feeds: list[queue.Queue[bytes | None]] = [queue.Queue() for _ in chunks]
        self.failed = False  # set when online generation failed for any chunk

    def start(self) -> None:
        asyncio.run_coroutine_threadsafe(self._produce(), self._loop)
        threading.Thread(target=self._decode, daemon=True, name="tts-decoder").start()

    def cancel(self) -> None:
        self.cancelled.set()

    # -- Producer: Edge TTS -> MP3 bytes (runs on the shared asyncio loop) ------

    async def _produce(self) -> None:
        """Stream chunks with bounded concurrency so chunk N+1 prefetches while chunk N plays."""
        failed: list[str] = []
        sem = asyncio.Semaphore(MAX_CONCURRENT_CHUNKS)
        connector = _get_shared_connector()

        async def _run_chunk(i: int, raw_text: str) -> None:
            try:
                async with sem:
                    if self.cancelled.is_set():
                        return
                    resolved = self._resolve_text(raw_text)
                    if not resolved.strip():
                        return
                    error = await self._stream_chunk(i, resolved, connector)
                    if error:
                        failed.append(error)
            finally:
                self._feeds[i].put(None)

        try:
            tasks = [
                asyncio.create_task(_run_chunk(i, text))
                for i, text in enumerate(self.chunks)
            ]
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            # Ensure every feed is closed so the decoder never hangs
            for feed in self._feeds:
                feed.put(None)

        self.failed = bool(failed)
        if failed and not self.cancelled.is_set() and self._on_error:
            self._on_error(failed[-1])

    async def _stream_chunk(
        self,
        index: int,
        text: str,
        connector: aiohttp.BaseConnector,
    ) -> str | None:
        """Stream one chunk into its feed. Returns an error message, or None on success."""
        last_error = "unknown error"
        for attempt in range(1, MAX_ATTEMPTS + 1):
            sent_audio = False
            try:
                chunk_voice = resolve_voice_for_text(text, self.voice)
                chunk_rate = resolve_rate_for_voice(chunk_voice, self.rate)
                communicate = edge_tts.Communicate(
                    text,
                    chunk_voice,
                    rate=chunk_rate,
                    boundary="SentenceBoundary",
                    connector=connector,
                    connect_timeout=CONNECT_TIMEOUT,
                    receive_timeout=RECEIVE_TIMEOUT,
                )
                async for message in communicate.stream():
                    if self.cancelled.is_set():
                        return None
                    if message["type"] == "audio":
                        self._feeds[index].put(message["data"])
                        sent_audio = True
                return None
            except Exception as e:
                last_error = str(e) or type(e).__name__
                if sent_audio:
                    # Part of this chunk is already playing; a retry would repeat it
                    return f"Speech cut off: {last_error}"
            if attempt < MAX_ATTEMPTS:
                await asyncio.sleep(0.35 * attempt)
                if self.cancelled.is_set():
                    return None
        return f"Speech generation failed: {last_error}"

    # -- Decoder: MP3 bytes -> PCM blocks (own thread) --------------------------

    def _decode(self) -> None:
        frames = max(256, int(self._sample_rate * DECODE_BLOCK_SECONDS))
        try:
            for i, feed in enumerate(self._feeds):
                if self.cancelled.is_set():
                    break
                source = _ChunkSource(feed, self.cancelled)
                try:
                    stream = miniaudio.stream_any(
                        source,
                        source_format=miniaudio.FileFormat.MP3,
                        output_format=miniaudio.SampleFormat.SIGNED16,
                        nchannels=self._channels,
                        sample_rate=self._sample_rate,
                        frames_to_read=frames,
                    )
                    for block in stream:
                        if self.cancelled.is_set():
                            break
                        self.pcm.put((i, block.tobytes()))
                except miniaudio.DecodeError:
                    pass  # chunk produced no audio (generation failed); move on
                except Exception as e:
                    print(f"[WARN] Decoding chunk {i + 1} failed: {e}")
        finally:
            self.pcm.put(None)
