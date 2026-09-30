"""
Streaming speech pipeline for Audify.

Edge TTS audio is decoded and played while it is still downloading, instead
of waiting for a finished MP3 file:

    producer (asyncio loop)   decoder thread            player (worker thread)
    Edge TTS websocket  --->  miniaudio MP3 -> PCM  --->  pygame channel queue
         MP3 bytes                 PCM blocks               gapless playback

Everything stays in memory (no temp files). A session can be cancelled at
any point (new text copied, stop hotkey) and every stage winds down promptly.
"""
from __future__ import annotations

import asyncio
import queue
import threading
from typing import Callable

import edge_tts
import miniaudio

# Network limits: fail fast when offline instead of hanging the reader
CONNECT_TIMEOUT = 5    # seconds to open the websocket
RECEIVE_TIMEOUT = 10   # seconds of silence from the server before giving up
MAX_ATTEMPTS = 3

# ~0.1s of audio per decoded block: small enough to start quickly
DECODE_BLOCK_SECONDS = 0.1


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
                item = self._feed.get(timeout=0.1)
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

    def start(self) -> None:
        asyncio.run_coroutine_threadsafe(self._produce(), self._loop)
        threading.Thread(target=self._decode, daemon=True, name="tts-decoder").start()

    def cancel(self) -> None:
        self.cancelled.set()

    # -- Producer: Edge TTS -> MP3 bytes (runs on the shared asyncio loop) ------

    async def _produce(self) -> None:
        failed: list[str] = []
        try:
            for i, text in enumerate(self.chunks):
                if self.cancelled.is_set():
                    break
                error = await self._stream_chunk(i, self._resolve_text(text))
                if error:
                    failed.append(error)
                self._feeds[i].put(None)
        finally:
            # Unblock the decoder for any chunks we never reached
            for feed in self._feeds:
                feed.put(None)
        if failed and not self.cancelled.is_set() and self._on_error:
            self._on_error(failed[-1])

    async def _stream_chunk(self, index: int, text: str) -> str | None:
        """Stream one chunk into its feed. Returns an error message, or None on success."""
        last_error = "unknown error"
        for attempt in range(1, MAX_ATTEMPTS + 1):
            sent_audio = False
            try:
                communicate = edge_tts.Communicate(
                    text, self.voice, rate=self.rate,
                    connect_timeout=CONNECT_TIMEOUT, receive_timeout=RECEIVE_TIMEOUT,
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
                await asyncio.sleep(0.4 * attempt)
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
