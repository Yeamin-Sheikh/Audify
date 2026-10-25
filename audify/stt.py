"""
Speech-to-Text (STT) dictation engine for Audify powered by Underdog Bark 3.0 Clean.

Provides:
  1. ``Win32MicRecorder``: Zero-dependency 16 kHz 16-bit mono microphone recorder
     using Windows Multimedia API (``winmm.dll`` ``waveInOpen``) via ``ctypes``.
  2. ``BarkCleanController``: On-demand persistent worker subprocess running
     ``ConwayResearch/Underdog-Bark-3.0-Clean`` (``ParakeetForTDT`` 0.6B with
     built-in disfluency removal that strips "um", "uh", and false starts).
     - Pre-warms the model in the background while the user is speaking.
     - Caches the unpacked ``model.bark`` weights on disk so subsequent startups
       skip ANS entropy decoding.
     - Automatically unloads after 3 minutes of inactivity so Audify returns to
       ~45 MB RAM and 0% CPU when idle.
  3. ``set_clipboard_and_paste``: Writes the cleaned transcript to the Windows
     clipboard (marking it as an internal STT write so Audify's TTS reader does
     not read your own dictation aloud) and sends a synthetic ``Ctrl+V`` to
     paste directly into the active window.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import wave
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from audify.engine import TTSDaemon

CREATE_NO_WINDOW = 0x08000000

# -- Paths for Bark 3.0 Clean model & cached unpacked weights ----------------

BARK_CLEAN_REPO = "ConwayResearch/Underdog-Bark-3.0-Clean"
BARK_MODEL_URL = f"https://huggingface.co/{BARK_CLEAN_REPO}/resolve/main/model.bark"
BARK_LOADER_URL = f"https://huggingface.co/{BARK_CLEAN_REPO}/resolve/main/bark_loader.py"
BARK_EXPECTED_MIN_BYTES = 179_000_000  # 179,458,613 bytes (~171.15 MB)


def get_bark_dir() -> str:
    """Return `%LOCALAPPDATA%\\Audify\\bark_clean`, creating it if needed."""
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    path = os.path.join(base, "Audify", "bark_clean")
    os.makedirs(path, exist_ok=True)
    return path


# -- Win32 Microphone Recorder (winmm.dll waveIn*, 16kHz 16-bit mono) --------

WAVE_MAPPER = 0xFFFFFFFF
WAVE_FORMAT_PCM = 1
CALLBACK_NULL = 0x00000000
WHDR_DONE = 0x00000001
MMSYSERR_NOERROR = 0

SAMPLE_RATE = 16000
CHANNELS = 1
BITS_PER_SAMPLE = 16
BYTES_PER_SAMPLE = BITS_PER_SAMPLE // 8
CHUNK_SAMPLES = 1600  # 100ms per buffer at 16 kHz
CHUNK_BYTES = CHUNK_SAMPLES * BYTES_PER_SAMPLE
NUM_BUFFERS = 6

# -- Voice Activity Detection (VAD) auto-stop constants ----------------------
# Each chunk is 100ms (0.1s) of 16 kHz 16-bit mono PCM.
VAD_IGNORE_INITIAL_CHUNKS = 2       # Ignore first 200ms (hotkey release click)
VAD_PRE_ROLL_CHUNKS = 4             # Keep 400ms before speech onset
VAD_END_SILENCE_CHUNKS = 14         # Auto-stop after 1.4s of silence post-speech
VAD_TRAILING_KEEP_CHUNKS = 4        # Keep 400ms of trailing silence after speech
VAD_NO_SPEECH_TIMEOUT_CHUNKS = 80   # Cancel after 8.0s if user never speaks


def _frame_rms_and_peak(pcm_bytes: bytes) -> tuple[float, int]:
    """Return ``(rms, peak)`` of 16-bit signed mono PCM bytes."""
    n = len(pcm_bytes) // 2
    if n == 0:
        return 0.0, 0
    samples = memoryview(pcm_bytes[: n * 2]).cast("h")
    total_sq = 0
    peak = 0
    for s in samples:
        a = -s if s < 0 else s
        if a > peak:
            peak = a
        total_sq += s * s
    return (total_sq / n) ** 0.5, peak


class WAVEFORMATEX(ctypes.Structure):
    _fields_ = [
        ("wFormatTag", wintypes.WORD),
        ("nChannels", wintypes.WORD),
        ("nSamplesPerSec", wintypes.DWORD),
        ("nAvgBytesPerSec", wintypes.DWORD),
        ("nBlockAlign", wintypes.WORD),
        ("wBitsPerSample", wintypes.WORD),
        ("cbSize", wintypes.WORD),
    ]


class WAVEHDR(ctypes.Structure):
    pass


WAVEHDR._fields_ = [
    ("lpData", ctypes.c_char_p),
    ("dwBufferLength", wintypes.DWORD),
    ("dwBytesRecorded", wintypes.DWORD),
    ("dwUser", ctypes.c_size_t),
    ("dwFlags", wintypes.DWORD),
    ("dwLoops", wintypes.DWORD),
    ("lpNext", ctypes.POINTER(WAVEHDR)),
    ("reserved", ctypes.c_size_t),
]

_winmm = ctypes.WinDLL("winmm", use_last_error=True)
_winmm.waveInGetNumDevs.argtypes = []
_winmm.waveInGetNumDevs.restype = wintypes.UINT
_winmm.waveInOpen.argtypes = [
    ctypes.POINTER(wintypes.HANDLE),
    wintypes.UINT,
    ctypes.POINTER(WAVEFORMATEX),
    ctypes.c_size_t,
    ctypes.c_size_t,
    wintypes.DWORD,
]
_winmm.waveInOpen.restype = wintypes.UINT
_winmm.waveInPrepareHeader.argtypes = [
    wintypes.HANDLE,
    ctypes.POINTER(WAVEHDR),
    wintypes.UINT,
]
_winmm.waveInPrepareHeader.restype = wintypes.UINT
_winmm.waveInUnprepareHeader.argtypes = [
    wintypes.HANDLE,
    ctypes.POINTER(WAVEHDR),
    wintypes.UINT,
]
_winmm.waveInUnprepareHeader.restype = wintypes.UINT
_winmm.waveInAddBuffer.argtypes = [
    wintypes.HANDLE,
    ctypes.POINTER(WAVEHDR),
    wintypes.UINT,
]
_winmm.waveInAddBuffer.restype = wintypes.UINT
_winmm.waveInStart.argtypes = [wintypes.HANDLE]
_winmm.waveInStart.restype = wintypes.UINT
_winmm.waveInStop.argtypes = [wintypes.HANDLE]
_winmm.waveInStop.restype = wintypes.UINT
_winmm.waveInReset.argtypes = [wintypes.HANDLE]
_winmm.waveInReset.restype = wintypes.UINT
_winmm.waveInClose.argtypes = [wintypes.HANDLE]
_winmm.waveInClose.restype = wintypes.UINT


class Win32MicRecorder:
    """Records 16 kHz 16-bit mono PCM audio with automatic end-of-speech (VAD) detection."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._recording = False
        self._thread: threading.Thread | None = None
        self._frames: list[bytes] = []
        self._trim_start_idx: int = 0
        self._trim_end_idx: int | None = None
        self._open_event = threading.Event()
        self._open_error: str | None = None
        self._auto_stop_cb: Callable[[str], None] | None = None

    @property
    def is_recording(self) -> bool:
        return self._recording

    def start(
        self,
        on_auto_stop: Callable[[str], None] | None = None,
        auto_stop_on_silence: bool = True,
    ) -> None:
        """Open the default microphone and begin capturing 16 kHz mono PCM frames.

        When *auto_stop_on_silence* is True (default), automatically stops recording
        once the user speaks and then pauses for 1.4 seconds (like Google Voice Typing),
        or after 8 seconds if no speech is detected, invoking ``on_auto_stop(reason)``.
        """
        with self._lock:
            if self._recording:
                return
            if _winmm.waveInGetNumDevs() == 0:
                raise RuntimeError("No microphone input device found on this system.")
            self._frames = []
            self._trim_start_idx = 0
            self._trim_end_idx = None
            self._recording = True
            self._auto_stop_cb = on_auto_stop
            self._open_event.clear()
            self._open_error = None
            self._thread = threading.Thread(
                target=self._record_loop,
                args=(auto_stop_on_silence,),
                daemon=True,
                name="win32-mic-recorder",
            )
            self._thread.start()

        self._open_event.wait(timeout=2.0)
        if self._open_error is not None:
            raise RuntimeError(self._open_error)

    def stop_and_save(self, wav_path: str) -> float:
        """Stop recording, write captured PCM to *wav_path*, and return duration in seconds."""
        with self._lock:
            self._recording = False
            self._auto_stop_cb = None
            thread = self._thread
            self._thread = None

        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=3.0)

        with self._lock:
            frames = self._frames
            start_idx = self._trim_start_idx
            end_idx = self._trim_end_idx
            self._frames = []
            self._trim_start_idx = 0
            self._trim_end_idx = None

        if not frames:
            return 0.0

        sliced = frames[start_idx:end_idx] if end_idx is not None else frames[start_idx:]
        pcm_data = b"".join(sliced)
        duration_s = len(pcm_data) / float(SAMPLE_RATE * BYTES_PER_SAMPLE)
        if not pcm_data:
            return 0.0

        with wave.open(wav_path, "wb") as wf:
            wf.setnchannels(CHANNELS)
            wf.setsampwidth(BYTES_PER_SAMPLE)
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes(pcm_data)
        return duration_s

    def _record_loop(self, auto_stop_on_silence: bool = True) -> None:
        wfx = WAVEFORMATEX()
        wfx.wFormatTag = WAVE_FORMAT_PCM
        wfx.nChannels = CHANNELS
        wfx.nSamplesPerSec = SAMPLE_RATE
        wfx.wBitsPerSample = BITS_PER_SAMPLE
        wfx.nBlockAlign = (CHANNELS * BITS_PER_SAMPLE) // 8
        wfx.nAvgBytesPerSec = SAMPLE_RATE * wfx.nBlockAlign
        wfx.cbSize = 0

        h_wave_in = wintypes.HANDLE()
        res = _winmm.waveInOpen(
            ctypes.byref(h_wave_in),
            WAVE_MAPPER,
            ctypes.byref(wfx),
            0,
            0,
            CALLBACK_NULL,
        )
        if res != MMSYSERR_NOERROR or not h_wave_in.value:
            self._recording = False
            self._open_error = f"Could not open microphone (MMSYSERR={res}). Check Windows microphone privacy permissions."
            self._open_event.set()
            return
        self._open_event.set()

        buffers = [ctypes.create_string_buffer(CHUNK_BYTES) for _ in range(NUM_BUFFERS)]
        headers = [WAVEHDR() for _ in range(NUM_BUFFERS)]
        hdr_size = ctypes.sizeof(WAVEHDR)

        # Adaptive VAD state
        noise_floor = 160.0
        speech_started = False
        active_streak = 0
        silence_streak = 0
        chunk_count = 0
        auto_stop_reason: str | None = None

        try:
            for i in range(NUM_BUFFERS):
                headers[i].lpData = ctypes.cast(buffers[i], ctypes.c_char_p)
                headers[i].dwBufferLength = CHUNK_BYTES
                headers[i].dwFlags = 0
                _winmm.waveInPrepareHeader(h_wave_in, ctypes.byref(headers[i]), hdr_size)
                _winmm.waveInAddBuffer(h_wave_in, ctypes.byref(headers[i]), hdr_size)

            _winmm.waveInStart(h_wave_in)

            while self._recording:
                any_done = False
                for i in range(NUM_BUFFERS):
                    if headers[i].dwFlags & WHDR_DONE:
                        any_done = True
                        recorded = headers[i].dwBytesRecorded
                        if recorded > 0:
                            raw_chunk = buffers[i].raw[:recorded]
                            self._frames.append(raw_chunk)
                            chunk_count += 1

                            if auto_stop_on_silence:
                                rms, peak = _frame_rms_and_peak(raw_chunk)
                                if not speech_started:
                                    if chunk_count > VAD_IGNORE_INITIAL_CHUNKS:
                                        onset_thresh = max(
                                            380.0, min(1400.0, noise_floor * 2.8)
                                        )
                                        if rms >= onset_thresh and peak >= 950:
                                            active_streak += 1
                                            if active_streak >= 2:
                                                speech_started = True
                                                self._trim_start_idx = max(
                                                    0,
                                                    len(self._frames)
                                                    - active_streak
                                                    - VAD_PRE_ROLL_CHUNKS,
                                                )
                                                silence_streak = 0
                                        else:
                                            active_streak = 0
                                            if rms < noise_floor * 2.0:
                                                noise_floor = (
                                                    0.85 * noise_floor
                                                    + 0.15 * max(60.0, min(600.0, rms))
                                                )
                                        if (
                                            not speech_started
                                            and chunk_count >= VAD_NO_SPEECH_TIMEOUT_CHUNKS
                                        ):
                                            auto_stop_reason = "no_speech"
                                            self._recording = False
                                            break
                                else:
                                    cont_thresh = max(
                                        250.0, min(1000.0, noise_floor * 1.85)
                                    )
                                    if rms >= cont_thresh:
                                        silence_streak = 0
                                    else:
                                        silence_streak += 1
                                        if silence_streak >= VAD_END_SILENCE_CHUNKS:
                                            # Keep 400ms of trailing silence and trim the rest
                                            trim_off = (
                                                silence_streak - VAD_TRAILING_KEEP_CHUNKS
                                            )
                                            if trim_off > 0:
                                                self._trim_end_idx = max(
                                                    self._trim_start_idx + 1,
                                                    len(self._frames) - trim_off,
                                                )
                                            auto_stop_reason = "speech_ended"
                                            self._recording = False
                                            break

                        headers[i].dwBytesRecorded = 0
                        headers[i].dwFlags &= ~WHDR_DONE
                        if self._recording:
                            _winmm.waveInAddBuffer(
                                h_wave_in, ctypes.byref(headers[i]), hdr_size
                            )
                if not any_done and self._recording:
                    time.sleep(0.015)

            _winmm.waveInStop(h_wave_in)
            _winmm.waveInReset(h_wave_in)

            # Drain any partial buffer flushed by waveInReset (only if manually stopped)
            if auto_stop_reason is None:
                for i in range(NUM_BUFFERS):
                    recorded = headers[i].dwBytesRecorded
                    if recorded > 0:
                        self._frames.append(buffers[i].raw[:recorded])
                        headers[i].dwBytesRecorded = 0
        finally:
            for i in range(NUM_BUFFERS):
                try:
                    _winmm.waveInUnprepareHeader(
                        h_wave_in, ctypes.byref(headers[i]), hdr_size
                    )
                except Exception:
                    pass
            _winmm.waveInClose(h_wave_in)

        if auto_stop_reason is not None:
            with self._lock:
                cb = self._auto_stop_cb
                self._auto_stop_cb = None
                self._thread = None
            if cb is not None:
                try:
                    cb(auto_stop_reason)
                except Exception:
                    pass


# -- Win32 Clipboard Write + Ctrl+V Auto-Paste -------------------------------

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002
KEYEVENTF_KEYUP = 0x0002
VK_CONTROL = 0x11
VK_MENU = 0x12
VK_SHIFT = 0x10
VK_V = 0x56

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

_user32.OpenClipboard.argtypes = [wintypes.HWND]
_user32.OpenClipboard.restype = wintypes.BOOL
_user32.EmptyClipboard.argtypes = []
_user32.EmptyClipboard.restype = wintypes.BOOL
_user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
_user32.SetClipboardData.restype = wintypes.HANDLE
_user32.CloseClipboard.argtypes = []
_user32.CloseClipboard.restype = wintypes.BOOL
_user32.keybd_event.argtypes = [
    wintypes.BYTE,
    wintypes.BYTE,
    wintypes.DWORD,
    ctypes.c_size_t,
]
_user32.keybd_event.restype = None

_kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
_kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
_kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
_kernel32.GlobalLock.restype = ctypes.c_void_p
_kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
_kernel32.GlobalUnlock.restype = wintypes.BOOL
_kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
_kernel32.GlobalFree.restype = wintypes.HGLOBAL


def set_clipboard_text(text: str) -> bool:
    """Write Unicode *text* to the Windows clipboard with retry backoff."""
    encoded = (text + "\0").encode("utf-16-le")
    for attempt in range(5):
        if _user32.OpenClipboard(None):
            try:
                _user32.EmptyClipboard()
                h_mem = _kernel32.GlobalAlloc(GMEM_MOVEABLE, len(encoded))
                if not h_mem:
                    return False
                ptr = _kernel32.GlobalLock(h_mem)
                if not ptr:
                    _kernel32.GlobalFree(h_mem)
                    return False
                ctypes.memmove(ptr, encoded, len(encoded))
                _kernel32.GlobalUnlock(h_mem)
                if not _user32.SetClipboardData(CF_UNICODETEXT, h_mem):
                    _kernel32.GlobalFree(h_mem)
                    return False
                return True
            finally:
                _user32.CloseClipboard()
        time.sleep(0.015 * (attempt + 1))
    return False


def send_ctrl_v() -> None:
    """Simulate Ctrl+V keystrokes to paste clipboard text into the focused window."""
    # Ensure Alt/Shift modifiers from the dictation hotkey are released first
    _user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
    _user32.keybd_event(VK_SHIFT, 0, KEYEVENTF_KEYUP, 0)
    time.sleep(0.02)
    _user32.keybd_event(VK_CONTROL, 0, 0, 0)
    _user32.keybd_event(VK_V, 0, 0, 0)
    time.sleep(0.015)
    _user32.keybd_event(VK_V, 0, KEYEVENTF_KEYUP, 0)
    _user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)


# -- Embedded Sidecar Worker Script for Bark 3.0 Clean -----------------------

_WORKER_SCRIPT = r'''"""Persistent JSON-lines sidecar worker for Underdog Bark 3.0 Clean."""
import importlib.util
import json
import os
import sys
import time
import urllib.request

os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"
os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
os.environ["TRANSFORMERS_VERBOSITY"] = "error"
os.environ["TOKENIZERS_PARALLELISM"] = "false"

import numpy as np
import soundfile as sf
import torch

# Use physical cores effectively without starving the UI
torch.set_num_threads(max(2, min(6, os.cpu_count() or 4)))

BARK_DIR = sys.argv[1]
MODEL_BARK = os.path.join(BARK_DIR, "model.bark")
BARK_LOADER = os.path.join(BARK_DIR, "bark_loader.py")
CACHED_DIR = os.path.join(BARK_DIR, "cached_hf")

BARK_MODEL_URL = "https://huggingface.co/ConwayResearch/Underdog-Bark-3.0-Clean/resolve/main/model.bark"
BARK_LOADER_URL = "https://huggingface.co/ConwayResearch/Underdog-Bark-3.0-Clean/resolve/main/bark_loader.py"


def emit(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def ensure_downloaded() -> None:
    os.makedirs(BARK_DIR, exist_ok=True)
    if not os.path.isfile(BARK_LOADER):
        urllib.request.urlretrieve(BARK_LOADER_URL, BARK_LOADER)
    if not os.path.isfile(MODEL_BARK) or os.path.getsize(MODEL_BARK) < 179_000_000:
        emit({"type": "status", "status": "Downloading Bark 3.0 Clean..."})
        tmp_path = MODEL_BARK + ".part"
        urllib.request.urlretrieve(BARK_MODEL_URL, tmp_path)
        os.replace(tmp_path, MODEL_BARK)


def load_bark_clean():
    import concurrent.futures
    from transformers import AutoProcessor, GenerationConfig, ParakeetForTDT, ParakeetTDTConfig

    ensure_downloaded()
    emit({"type": "status", "status": "Loading Bark 3.0 Clean..."})

    # Fast path: load already-unpacked safetensors cache if present
    safetensors_path = os.path.join(CACHED_DIR, "model.safetensors")
    if os.path.isfile(safetensors_path):
        try:
            model = ParakeetForTDT.from_pretrained(CACHED_DIR, dtype=torch.float32).eval()
            model.generation_config = GenerationConfig.from_pretrained(CACHED_DIR)
            proc = AutoProcessor.from_pretrained(CACHED_DIR)
            return model, proc
        except Exception:
            pass

    # First-run path: parallel-unpack model.bark (~13s across 8 threads) and cache to CACHED_DIR
    spec = importlib.util.spec_from_file_location("official_bark", BARK_LOADER)
    loader = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loader)

    raw = open(MODEL_BARK, "rb").read()
    n = int.from_bytes(raw[:8], "little")
    head = json.loads(raw[8 : 8 + n])
    p = 8 + n
    tasks = []
    for e in head["index"]:
        sz = e.get("lev", 0) + e.get("meta", 0) + e.get("b", 0) + e.get("s", 0)
        tasks.append((e, raw[p : p + sz]))
        p += sz

    def _decode_one(item):
        import constriction
        e, chunk = item
        shape = e["shape"]
        q_off = 0
        if e["k"] == "levels2":
            K, rows, cols = e["K"], shape[0], int(np.prod(shape[1:]))
            lev = np.frombuffer(loader._unz(chunk[q_off : q_off + e["lev"]], e.get("zl")), np.float16).reshape(rows, K)
            q_off += e["lev"]
            meta = chunk[q_off : q_off + e["meta"]]
            q_off += e["meta"]
            nb = (cols * 4 + 7) // 8
            bits = np.unpackbits(np.frombuffer(meta[:nb], np.uint8))[: cols * 4].reshape(cols, 4)
            cls = np.packbits(np.concatenate([np.zeros((cols, 4), np.uint8), bits], 1), axis=1)[:, 0]
            counts = np.frombuffer(meta[nb:], np.uint32).reshape(16, 2 * K + 1).astype(np.float64)
            words = np.frombuffer(chunk[q_off : q_off + e["b"]], np.uint32).copy()
            codes = loader._decode_codes(cls, counts, words, rows, cols)
            arr = loader._levels_to_weights(lev, codes).reshape(shape)
        elif e["k"].startswith("ansint"):
            bits = int(e["k"][6:])
            scale = np.frombuffer(loader._unz(chunk[q_off : q_off + e["s"]], e.get("zs")), np.float16).astype(np.float32)
            q_off += e["s"]
            counts = np.frombuffer(chunk[q_off : q_off + e["meta"]], np.uint32).astype(np.float64)
            q_off += e["meta"]
            words = np.frombuffer(chunk[q_off : q_off + e["b"]], np.uint32).copy()
            m = constriction.stream.model.Categorical(counts / counts.sum(), perfect=False)
            sym = constriction.stream.stack.AnsCoder(words).decode(m, int(np.prod(shape)))
            q = (sym - (2 ** (bits - 1) - 1)).astype(np.float32)
            arr = (q.reshape(len(scale), -1) * scale[:, None]).reshape(shape)
        else:
            dtype = {"torch.int64": np.int64, "torch.int32": np.int32}.get(e["dtype"], np.float16)
            arr = np.frombuffer(loader._unz(chunk[q_off : q_off + e["b"]], e.get("zr")), dtype).copy().reshape(shape)
        return e["n"], torch.from_numpy(arr).float()

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        sd = dict(ex.map(_decode_one, tasks))

    cfg = json.loads(head["config"])
    model = ParakeetForTDT(ParakeetTDTConfig.from_dict(cfg))
    model.load_state_dict(sd, strict=False)
    model.generation_config = GenerationConfig.from_pretrained("nvidia/parakeet-tdt-0.6b-v3")
    model = model.eval().to(device="cpu", dtype=torch.float32)
    proc = AutoProcessor.from_pretrained("nvidia/parakeet-tdt-0.6b-v3")

    try:
        os.makedirs(CACHED_DIR, exist_ok=True)
        model.save_pretrained(CACHED_DIR)
        proc.save_pretrained(CACHED_DIR)
        if model.generation_config is not None:
            model.generation_config.save_pretrained(CACHED_DIR)
    except Exception:
        pass

    return model, proc


def transcribe_audio(model, proc, wav_path: str) -> str:
    wave_arr, sr = sf.read(wav_path, dtype="float32", always_2d=True)
    mono = wave_arr.mean(1)
    if len(mono) == 0:
        return ""
    if sr != 16000:
        import soxr
        mono = soxr.resample(mono, sr, 16000)

    # Bark 3.0 Clean is evaluated with pad_s=0.0 (no leading silence); chunk at 25s if long
    max_samples = 25 * 16000
    pieces = [mono[i : i + max_samples] for i in range(0, len(mono), max_samples)]
    texts = []
    for piece in pieces:
        inp = proc([piece.astype(np.float32)], sampling_rate=16000, return_tensors="pt", padding=True)
        with torch.inference_mode():
            out = model.generate(**inp)
        piece_text = proc.batch_decode(out.sequences, skip_special_tokens=True)[0].strip()
        if piece_text:
            texts.append(piece_text)
    return " ".join(texts).strip()


def main() -> None:
    try:
        model, proc = load_bark_clean()
        emit({"type": "ready"})
    except Exception as e:
        emit({"type": "error", "error": f"Failed to load Bark 3.0 Clean: {e}"})
        return

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception:
            continue
        cmd = req.get("cmd")
        if cmd == "quit":
            break
        if cmd == "transcribe":
            wav_path = req.get("wav_path", "")
            t0 = time.perf_counter()
            try:
                text = transcribe_audio(model, proc, wav_path)
                elapsed_ms = (time.perf_counter() - t0) * 1000.0
                emit({"type": "result", "ok": True, "text": text, "ms": round(elapsed_ms, 1)})
            except Exception as e:
                emit({"type": "result", "ok": False, "error": str(e)})


if __name__ == "__main__":
    main()
'''


def find_bark_python() -> str | None:
    """Locate a Python interpreter on the machine with ParakeetForTDT support."""
    candidates: list[str] = [
        r"C:\Python314\python.exe",
        shutil.which("python") or "",
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Python\Python314\python.exe"),
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Python\Python313\python.exe"),
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Python\Python312\python.exe"),
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Python\Python311\python.exe"),
    ]
    if not getattr(sys, "frozen", False):
        candidates.append(sys.executable)

    appdata = os.environ.get("APPDATA") or os.path.expanduser(r"~\AppData\Roaming")
    seen: set[str] = set()
    for exe in candidates:
        if not exe:
            continue
        norm = os.path.normcase(os.path.abspath(exe))
        if norm in seen or not os.path.isfile(exe):
            continue
        seen.add(norm)
        # Fast 0.2ms filesystem check in both system and user (%APPDATA%) site-packages
        exe_dir = os.path.dirname(os.path.abspath(exe))
        py_tag = os.path.basename(exe_dir)  # e.g. "Python314"
        site_roots = [
            os.path.join(appdata, "Python", py_tag, "site-packages"),
            os.path.join(exe_dir, "Lib", "site-packages"),
            os.path.join(os.path.dirname(exe_dir), "Lib", "site-packages"),
        ]
        has_torch = False
        has_parakeet_tdt = False
        for sp in site_roots:
            if os.path.isdir(os.path.join(sp, "torch")):
                has_torch = True
            modeling_py = os.path.join(
                sp, "transformers", "models", "parakeet", "modeling_parakeet.py"
            )
            if os.path.isfile(modeling_py):
                try:
                    with open(modeling_py, "r", encoding="utf-8", errors="ignore") as f:
                        if "ParakeetForTDT" in f.read():
                            has_parakeet_tdt = True
                except Exception:
                    pass
        if has_torch and has_parakeet_tdt:
            return exe
    return None


class BarkCleanController:
    """Manages microphone recording, Bark 3.0 Clean sidecar lifecycle, and auto-paste."""

    IDLE_UNLOAD_SECONDS = 1800.0

    def __init__(self, daemon: TTSDaemon | None = None) -> None:
        self.daemon = daemon
        self.recorder = Win32MicRecorder()
        self._lock = threading.Lock()
        self._transcribing = False
        self._last_toggle_time = 0.0
        self._on_complete_cb: Callable[[bool, str], None] | None = None

        # Sidecar subprocess state
        self._proc: subprocess.Popen | None = None
        self._proc_ready = threading.Event()
        self._proc_error: str | None = None
        self._result_event = threading.Event()
        self._last_result: dict | None = None
        self._idle_timer: threading.Timer | None = None

        # Silently pre-warm the Bark 3.0 Clean worker 3s after daemon startup
        # so the very first dictation transcribes in ~2s as soon as speech ends.
        if self.daemon is not None:
            prewarm = threading.Timer(3.0, self._ensure_worker_started)
            prewarm.daemon = True
            prewarm.start()

    @property
    def is_recording(self) -> bool:
        return self.recorder.is_recording

    @property
    def is_transcribing(self) -> bool:
        return self._transcribing

    @property
    def is_busy(self) -> bool:
        return self.recorder.is_recording or self._transcribing

    def toggle_dictation(
        self, on_complete: Callable[[bool, str], None] | None = None
    ) -> str:
        """Toggle between starting microphone recording and stopping + transcribing.

        Returns the new state: ``"recording"``, ``"transcribing"``, or ``"busy"``.
        """
        now = time.monotonic()
        if (now - self._last_toggle_time) < 0.25:
            return "busy"
        self._last_toggle_time = now

        if self._transcribing:
            return "busy"

        if not self.recorder.is_recording:
            self.start_dictation(on_complete=on_complete)
            return "recording"
        else:
            self.stop_and_transcribe(on_complete=on_complete)
            return "transcribing"

    def start_dictation(
        self, on_complete: Callable[[bool, str], None] | None = None
    ) -> bool:
        """Start capturing microphone audio with automatic end-of-speech detection."""
        if self.recorder.is_recording or self._transcribing:
            return False
        try:
            self._on_complete_cb = on_complete
            if self.daemon is not None:
                self.daemon.stop_audio()
            self.recorder.start(
                on_auto_stop=self._on_vad_auto_stop,
                auto_stop_on_silence=True,
            )
            if self.daemon is not None:
                self.daemon._set_status("Listening...", speaking=False)
            # Ensure the Bark 3.0 Clean worker is warm while the user speaks
            threading.Thread(
                target=self._ensure_worker_started,
                daemon=True,
                name="bark-prewarm",
            ).start()
            return True
        except Exception as e:
            if self.daemon is not None:
                self.daemon._notify_error(f"Microphone error: {e}")
                self.daemon._set_status("Ready", speaking=False)
            return False

    def _on_vad_auto_stop(self, reason: str) -> None:
        """Called by Win32MicRecorder when VAD detects end of speech or silence timeout."""
        if reason == "speech_ended":
            self.stop_and_transcribe(_from_vad=True)
        else:
            with self.recorder._lock:
                self.recorder._frames = []
            if self.daemon is not None:
                self.daemon._set_status("Ready", speaking=False)
            cb = self._on_complete_cb
            self._on_complete_cb = None
            if cb is not None:
                try:
                    cb(False, "No speech detected.")
                except Exception:
                    pass

    def stop_and_transcribe(
        self,
        on_complete: Callable[[bool, str], None] | None = None,
        _from_vad: bool = False,
    ) -> None:
        """Stop microphone recording, run Bark 3.0 Clean, and paste the cleaned text."""
        if (not self.recorder.is_recording and not _from_vad) or self._transcribing:
            return
        self._transcribing = True
        if on_complete is not None:
            self._on_complete_cb = on_complete
        if self.daemon is not None:
            self.daemon._set_status("Transcribing...", speaking=False)

        def _worker() -> None:
            wav_path: str | None = None
            ok = False
            out_msg = ""
            try:
                fd, wav_path = tempfile.mkstemp(
                    prefix="audify_dictation_", suffix=".wav"
                )
                os.close(fd)
                duration_s = self.recorder.stop_and_save(wav_path)
                if duration_s < 0.25:
                    out_msg = "Recording too short."
                    return

                ok, out_msg = self.transcribe_file(wav_path)
                if ok and out_msg:
                    self._copy_and_paste(out_msg)
                elif ok and not out_msg:
                    out_msg = "No speech detected."
                    ok = False
            except Exception as e:
                ok = False
                out_msg = f"Transcription failed: {e}"
                if self.daemon is not None:
                    self.daemon._notify_error(out_msg)
            finally:
                if wav_path:
                    try:
                        os.remove(wav_path)
                    except Exception:
                        pass
                self._transcribing = False
                if self.daemon is not None:
                    self.daemon._set_status("Ready", speaking=False)
                cb = self._on_complete_cb
                self._on_complete_cb = None
                if cb is not None:
                    try:
                        cb(ok, out_msg)
                    except Exception:
                        pass

        threading.Thread(
            target=_worker, daemon=True, name="bark-transcribe"
        ).start()

    def transcribe_file(self, wav_path: str) -> tuple[bool, str]:
        """Send *wav_path* to the Bark 3.0 Clean worker and return ``(ok, text_or_error)``."""
        if not self._ensure_worker_started():
            err = self._proc_error or "Could not start Bark 3.0 Clean Python runtime."
            if self.daemon is not None:
                self.daemon._notify_error(err)
            return False, err

        with self._lock:
            proc = self._proc
            if proc is None or proc.poll() is not None or proc.stdin is None:
                return False, "Bark 3.0 Clean worker terminated unexpectedly."

            self._result_event.clear()
            self._last_result = None
            payload = json.dumps({"cmd": "transcribe", "wav_path": wav_path}) + "\n"
            proc.stdin.write(payload)
            proc.stdin.flush()

        if not self._result_event.wait(timeout=120.0):
            return False, "Bark 3.0 Clean transcription timed out."

        self._reset_idle_timer()
        res = self._last_result or {}
        if res.get("ok"):
            return True, str(res.get("text", "")).strip()
        err_msg = str(res.get("error", "Unknown transcription error"))
        if self.daemon is not None:
            self.daemon._notify_error(f"Bark 3.0 Clean error: {err_msg}")
        return False, err_msg

    def _copy_and_paste(self, text: str) -> None:
        """Copy *text* to clipboard without triggering TTS and paste via Ctrl+V."""
        if self.daemon is not None:
            # Mark clipboard listener's last text and clear last_ctrl_c_time so
            # Audify's TTS reader never reads your own dictated text aloud
            self.daemon.last_ctrl_c_time = 0.0
            try:
                self.daemon._clip_listener._last_text = text
                self.daemon._clip_listener._last_update_time = time.monotonic()
            except Exception:
                pass

        if set_clipboard_text(text):
            send_ctrl_v()
            if self.daemon is not None:
                preview = text if len(text) <= 70 else text[:67] + "..."
                print(f"[STT] Dictated ({len(text)} chars): {preview}")

    def _ensure_worker_started(self) -> bool:
        """Spawn the Bark 3.0 Clean sidecar subprocess if not already running."""
        with self._lock:
            if self._proc is None or self._proc.poll() is not None:
                py_exe = find_bark_python()
                if not py_exe:
                    self._proc_error = (
                        "Python with transformers >= 5.16 (ParakeetForTDT) not found."
                    )
                    return False

                bark_dir = get_bark_dir()
                worker_script = os.path.join(bark_dir, "bark_worker.py")
                with open(worker_script, "w", encoding="utf-8") as f:
                    f.write(_WORKER_SCRIPT)

                self._proc_ready.clear()
                self._proc_error = None
                self._proc = subprocess.Popen(
                    [py_exe, "-u", worker_script, bark_dir],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    creationflags=CREATE_NO_WINDOW,
                )
                threading.Thread(
                    target=self._stdout_reader,
                    args=(self._proc,),
                    daemon=True,
                    name="bark-worker-stdout",
                ).start()

        # Wait up to 360s on first run (in case model.bark is downloading/unpacking)
        if not self._proc_ready.wait(timeout=360.0):
            self._proc_error = "Timed out initializing Bark 3.0 Clean."
            self.shutdown()
            return False

        self._reset_idle_timer()
        return self._proc_error is None

    def _stdout_reader(self, proc: subprocess.Popen) -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            if self._proc is not proc:
                return
            line = line.strip()
            if not line or not line.startswith("{"):
                continue
            try:
                msg = json.loads(line)
            except Exception:
                continue
            mtype = msg.get("type")
            if mtype == "status":
                status_str = str(msg.get("status", ""))
                if (
                    status_str
                    and self.daemon is not None
                    and self._transcribing
                ):
                    self.daemon._set_status(status_str, speaking=False)
            elif mtype == "ready":
                self._proc_ready.set()
            elif mtype == "error":
                self._proc_error = str(msg.get("error", "Worker error"))
                self._proc_ready.set()
            elif mtype == "result":
                self._last_result = msg
                self._result_event.set()

        if self._proc is not proc:
            return
        # If process exited before signaling ready, unblock waiters
        if not self._proc_ready.is_set():
            self._proc_error = "Worker exited unexpectedly."
            self._proc_ready.set()
        if not self._result_event.is_set():
            self._last_result = {"ok": False, "error": "Worker exited during transcription."}
            self._result_event.set()

    def _reset_idle_timer(self) -> None:
        if self._idle_timer is not None:
            self._idle_timer.cancel()
        self._idle_timer = threading.Timer(self.IDLE_UNLOAD_SECONDS, self.shutdown)
        self._idle_timer.daemon = True
        self._idle_timer.start()

    def shutdown(self) -> None:
        """Stop the Bark 3.0 Clean worker process and release its RAM."""
        if self._idle_timer is not None:
            self._idle_timer.cancel()
            self._idle_timer = None
        with self._lock:
            proc = self._proc
            self._proc = None
            self._proc_ready.clear()
        if proc is not None and proc.poll() is None:
            try:
                if proc.stdin:
                    proc.stdin.write('{"cmd": "quit"}\n')
                    proc.stdin.flush()
                proc.wait(timeout=2.0)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
