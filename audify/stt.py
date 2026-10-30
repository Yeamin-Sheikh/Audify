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
import gc
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import wave
import zipfile
import difflib
import re
from typing import TYPE_CHECKING, Any, Callable

from audify.clipboard import get_clipboard_text
from audify.config import apply_stt_vocabulary, set_stt_vocab_rule
from audify.gui.stt_hud import DictationVisualizerHUD

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
# On Windows laptops with Intel Smart Sound / Realtek APO, cold mic open has a 2.4s AGC/NS
# convergence period (550-870 RMS) before locking into the quiet floor (~80 RMS).
VAD_IGNORE_INITIAL_CHUNKS = 24      # Ignore first 2.4s on cold open (hardware AGC/NS convergence)
VAD_PRE_ROLL_CHUNKS = 5             # Keep 500ms before speech onset
VAD_END_SILENCE_CHUNKS = 18         # Auto-stop after 1.8s of silence post-speech
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
        self._cancelled = False
        self._thread: threading.Thread | None = None
        self._frames: list[bytes] = []
        self._trim_start_idx: int = 0
        self._trim_end_idx: int | None = None
        self._open_event = threading.Event()
        self._open_error: str | None = None
        self._auto_stop_cb: Callable[[str], None] | None = None
        self._vad_enabled: bool = True
        self._vad_reset_requested: bool = False
        self._silence_delay_s: float = 1.0
        self._no_speech_timeout_s: float = 8.0
        self.current_level: float = 0.0
        self.level_bars: list[float] = [0.08] * 9
        self.speech_detected: bool = False
        self.speech_chunks: int = 0

    @property
    def is_recording(self) -> bool:
        return self._recording

    def start(
        self,
        on_auto_stop: Callable[[str], None] | None = None,
        auto_stop_on_silence: bool = True,
        silence_delay_s: float = 1.0,
        no_speech_timeout_s: float = 8.0,
    ) -> None:
        """Open the default microphone and begin capturing 16 kHz mono PCM frames.

        When *auto_stop_on_silence* is True (default), automatically stops recording
        once the user speaks and then pauses for *silence_delay_s* seconds,
        or after *no_speech_timeout_s* seconds if no speech is detected, invoking ``on_auto_stop(reason)``.
        """
        with self._lock:
            if self._recording:
                return
            lingering_thread = self._thread
            self._thread = None

        if lingering_thread is not None and lingering_thread is not threading.current_thread():
            lingering_thread.join(timeout=1.5)

        with self._lock:
            if self._recording:
                return
            if _winmm.waveInGetNumDevs() == 0:
                raise RuntimeError("No microphone input device found on this system.")
            self._frames = []
            self._trim_start_idx = 0
            self._trim_end_idx = None
            self._cancelled = False
            self._recording = True
            self.current_level = 0.0
            self.level_bars = [0.08] * 9
            self.speech_detected = False
            self.speech_chunks = 0
            self._auto_stop_cb = on_auto_stop
            self._vad_enabled = auto_stop_on_silence
            self._vad_reset_requested = False
            self._silence_delay_s = silence_delay_s
            self._no_speech_timeout_s = no_speech_timeout_s
            self._started_at = time.monotonic()
            self._open_event.clear()
            self._open_error = None
            self._thread = threading.Thread(
                target=self._record_loop,
                args=(auto_stop_on_silence, silence_delay_s, no_speech_timeout_s),
                daemon=True,
                name="win32-mic-recorder",
            )
            self._thread.start()

        self._open_event.wait(timeout=2.0)
        if self._open_error is not None:
            raise RuntimeError(self._open_error)

    def arm_vad(
        self,
        on_auto_stop: Callable[[str], None] | None = None,
        silence_delay_s: float = 2.0,
        no_speech_timeout_s: float = 120.0,
    ) -> None:
        """Clear pre-warmed audio frames and arm VAD immediately with zero hardware AGC warmup delay."""
        with self._lock:
            self._frames = []
            self._trim_start_idx = 0
            self._trim_end_idx = None
            self.speech_detected = False
            self.speech_chunks = 0
            self._auto_stop_cb = on_auto_stop
            self._silence_delay_s = silence_delay_s
            self._no_speech_timeout_s = no_speech_timeout_s
            self._vad_reset_requested = True
            self._vad_enabled = True

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
            if self._cancelled:
                self._frames = []
                return 0.0
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

    def cancel(self, wait: bool = False) -> None:
        """Abort microphone recording immediately and discard captured audio frames."""
        with self._lock:
            self._recording = False
            self._cancelled = True
            self._auto_stop_cb = None
            self._frames = []
            self._trim_start_idx = 0
            self._trim_end_idx = None
            thread = self._thread if wait else None
            if wait:
                self._thread = None

        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1.5)

    def _record_loop(
        self,
        auto_stop_on_silence: bool = True,
        silence_delay_s: float = 1.0,
        no_speech_timeout_s: float = 8.0,
    ) -> None:
        end_silence_chunks = max(4, min(40, int(round(float(silence_delay_s) * 10.0))))
        no_speech_chunks = max(30, int(round(float(no_speech_timeout_s) * 10.0)))
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

        # Adaptive VAD state calibrated for real-time close-talk speech vs silence
        noise_floor = 60.0
        ignore_chunks = 2
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
                if self._vad_reset_requested:
                    with self._lock:
                        self._vad_reset_requested = False
                        end_silence_chunks = max(
                            4, min(40, int(round(float(self._silence_delay_s) * 10.0)))
                        )
                        no_speech_chunks = max(
                            30, int(round(float(self._no_speech_timeout_s) * 10.0))
                        )
                        speech_started = False
                        active_streak = 0
                        silence_streak = 0
                        chunk_count = 0
                        ignore_chunks = 2
                        noise_floor = 60.0

                any_done = False
                for i in range(NUM_BUFFERS):
                    if headers[i].dwFlags & WHDR_DONE:
                        any_done = True
                        recorded = headers[i].dwBytesRecorded
                        if recorded > 0:
                            raw_chunk = buffers[i].raw[:recorded]
                            self._frames.append(raw_chunk)
                            chunk_count += 1
                            rms, peak = _frame_rms_and_peak(raw_chunk)
                            norm_lvl = min(1.0, max(0.05, (rms - noise_floor * 0.85) / 2200.0))
                            self.current_level = 0.45 * self.current_level + 0.55 * norm_lvl
                            bars = list(self.level_bars[1:]) + [self.current_level]
                            self.level_bars = bars

                            if self._vad_enabled:
                                # Ignore only the first 2 chunks (200ms) for waveInStart hardware pop.
                                # Track chunk_count (all 100ms chunks, including silent ones) so silence_streak
                                # increments immediately as soon as the user stops speaking.
                                if not speech_started:
                                    if chunk_count > ignore_chunks:
                                        onset_thresh = 135.0
                                        onset_peak = 420
                                        if rms >= onset_thresh and peak >= onset_peak:
                                            active_streak += 1
                                            if active_streak >= 2 or (rms >= 260.0 and peak >= 850):
                                                speech_started = True
                                                self.speech_detected = True
                                                self.speech_chunks = max(2, active_streak)
                                                self._trim_start_idx = max(
                                                    0,
                                                    len(self._frames)
                                                    - active_streak
                                                    - VAD_PRE_ROLL_CHUNKS,
                                                )
                                                silence_streak = 0
                                        else:
                                            active_streak = 0
                                            if rms >= 5.0 and rms < 110.0:
                                                noise_floor = (
                                                    0.85 * noise_floor
                                                    + 0.15 * max(25.0, min(105.0, rms))
                                                )
                                        if (
                                            not speech_started
                                            and chunk_count >= no_speech_chunks
                                        ):
                                            auto_stop_reason = "no_speech"
                                            self._recording = False
                                            break
                                else:
                                    # Close-talk user speech continuation is >=185 RMS and >=600 peak.
                                    # As soon as the user stops talking (<185 RMS or <600 peak), silence_streak
                                    # increments on every 100ms chunk and stops recording after silence_delay_s.
                                    cont_thresh = 185.0
                                    cont_peak = 600
                                    if rms >= cont_thresh and peak >= cont_peak:
                                        silence_streak = 0
                                        self.speech_chunks += 1
                                    else:
                                        silence_streak += 1
                                        if silence_streak >= end_silence_chunks:
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

            # Drain any partial buffer flushed by waveInReset (only if manually stopped and not cancelled)
            if auto_stop_reason is None and not self._cancelled:
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
VK_LSHIFT = 0xA0
VK_RSHIFT = 0xA1
VK_LMENU = 0xA4
VK_RMENU = 0xA5
VK_V = 0x56
VK_C = 0x43
VK_CAPITAL = 0x14

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

_user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
_user32.GetAsyncKeyState.restype = wintypes.SHORT
_user32.GetKeyState.argtypes = [ctypes.c_int]
_user32.GetKeyState.restype = wintypes.SHORT

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
    # Wait briefly (up to 350ms) if Alt or Shift is still physically held from a shortcut
    t_wait = time.monotonic()
    while (time.monotonic() - t_wait) < 0.35:
        alt_down = bool(_user32.GetAsyncKeyState(VK_MENU) & 0x8000)
        shift_down = bool(_user32.GetAsyncKeyState(VK_SHIFT) & 0x8000)
        if not alt_down and not shift_down:
            break
        time.sleep(0.015)

    for vk_mod in (VK_MENU, VK_LMENU, VK_RMENU, VK_SHIFT, VK_LSHIFT, VK_RSHIFT):
        _user32.keybd_event(vk_mod, 0, KEYEVENTF_KEYUP, 0)
    time.sleep(0.02)
    _user32.keybd_event(VK_CONTROL, 0, 0, 0)
    _user32.keybd_event(VK_V, 0, 0, 0)
    time.sleep(0.015)
    _user32.keybd_event(VK_V, 0, KEYEVENTF_KEYUP, 0)
    _user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)


def send_ctrl_c() -> None:
    """Simulate Ctrl+C keystrokes to silently inspect the currently highlighted word."""
    _user32.keybd_event(VK_CONTROL, 0, 0, 0)
    _user32.keybd_event(VK_C, 0, 0, 0)
    time.sleep(0.012)
    _user32.keybd_event(VK_C, 0, KEYEVENTF_KEYUP, 0)
    _user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)


# -- Automatic Double-Click Correction Learner -------------------------------

_IGNORED_MODIFIER_NAMES = frozenset(
    {
        "shift",
        "left shift",
        "right shift",
        "ctrl",
        "left ctrl",
        "right ctrl",
        "alt",
        "left alt",
        "right alt",
        "alt gr",
        "caps lock",
        "left windows",
        "right windows",
    }
)

_COMMIT_KEY_NAMES = frozenset(
    {
        "space",
        "enter",
        "tab",
        "up",
        "down",
        "left",
        "right",
        "home",
        "end",
        "dot",
        "comma",
        ".",
        ",",
        "!",
        "?",
        ";",
        ":",
    }
)


class DictationCorrectionLearner:
    """Watches for double-click word edits after dictation and auto-learns STT vocabulary rules.

    Workflow:
      1. After Bark 3.0 Clean pastes a transcription, ``record_dictation(text)`` opens a
         3-minute learning window and indexes the words in the dictated text.
      2. If the user double-clicks a word in any application (two left-clicks within
         550ms and 12px), ``on_mouse_click`` starts a keystroke capture session and
         runs a background clipboard probe (``Ctrl+C`` + restore) to identify the exact
         highlighted word from the recent dictation.
      3. As the user types the replacement spelling on their keyboard, ``on_key_press``
         records the characters (handling Shift, Caps Lock, Backspace, or ``Ctrl+V``).
      4. When the user finishes typing (presses Space, Enter, Tab, arrow keys, punctuation,
         clicks the mouse, or pauses typing for 1.2s), ``_commit_correction`` saves
         ``heard -> written`` into ``config["stt_vocabulary"]`` so future dictations
         automatically write the user's preferred spelling.
    """

    LEARNING_WINDOW_S = 180.0
    DBLCLICK_MAX_INTERVAL_S = 0.55
    DBLCLICK_MAX_DIST_PX = 12
    COMMIT_PAUSE_S = 1.2

    def __init__(self, daemon: TTSDaemon | None = None) -> None:
        self.daemon = daemon
        self._lock = threading.Lock()
        self._last_text: str = ""
        self._last_words: list[str] = []
        self._last_dictation_time: float = 0.0

        # Double-click state
        self._last_click_time: float = 0.0
        self._last_click_pos: tuple[int, int] = (-9999, -9999)
        self._pending_dblclick_release: bool = False

        # Active correction capture session
        self._session_id: int = 0
        self._capturing_keys: bool = False
        self._capture_start_time: float = 0.0
        self._selected_word: str = ""
        self._typed_chars: list[str] = []
        self._commit_timer: threading.Timer | None = None

        # Suppresses TTS readback while probing the double-clicked selection
        self.is_probing_clipboard: bool = False
        self.last_probe_time: float = 0.0

    def record_dictation(self, text: str) -> None:
        """Record newly dictated text and open the double-click learning window."""
        cleaned = (text or "").strip()
        if not cleaned:
            return
        words: list[str] = []
        seen_lower: set[str] = set()
        for w in re.findall(r"[A-Za-z0-9']+", cleaned):
            w_clean = w.strip("'")
            if len(w_clean) >= 2 and w_clean.lower() not in seen_lower:
                seen_lower.add(w_clean.lower())
                words.append(w_clean)

        with self._lock:
            self._cancel_capture_locked()
            self._last_text = cleaned
            self._last_words = words
            self._last_dictation_time = time.monotonic()

    def on_mouse_click(self, x: int, y: int, button: Any, pressed: bool) -> None:
        """Detect double-clicks on dictated words or commit an in-progress correction on click."""
        btn_str = str(button).lower()
        if not btn_str.endswith("left"):
            if pressed and self._capturing_keys:
                self._commit_or_cancel()
            return

        now = time.monotonic()
        if pressed:
            # If the user was typing a correction and clicked elsewhere, commit it first
            if self._capturing_keys:
                if self._typed_chars:
                    self._commit_correction()
                else:
                    self._cancel_capture()

            with self._lock:
                if (
                    not self._last_text
                    or (now - self._last_dictation_time) > self.LEARNING_WINDOW_S
                ):
                    self._last_click_time = now
                    self._last_click_pos = (x, y)
                    self._pending_dblclick_release = False
                    return

                dt = now - self._last_click_time
                dx = abs(x - self._last_click_pos[0])
                dy = abs(y - self._last_click_pos[1])
                if (
                    dt <= self.DBLCLICK_MAX_INTERVAL_S
                    and dx <= self.DBLCLICK_MAX_DIST_PX
                    and dy <= self.DBLCLICK_MAX_DIST_PX
                ):
                    self._pending_dblclick_release = True
                    self._last_click_time = 0.0
                    self._start_capture_session_locked(now)
                else:
                    self._last_click_time = now
                    self._last_click_pos = (x, y)
                    self._pending_dblclick_release = False
        else:
            with self._lock:
                if not self._pending_dblclick_release:
                    return
                self._pending_dblclick_release = False
                session_id = self._session_id

            threading.Thread(
                target=self._probe_selected_word,
                args=(session_id,),
                daemon=True,
                name="stt-dblclick-probe",
            ).start()

    def _start_capture_session_locked(self, now: float) -> None:
        self._cancel_capture_locked()
        self._session_id += 1
        self._capturing_keys = True
        self._capture_start_time = now
        self._selected_word = ""
        self._typed_chars = []

    def _probe_selected_word(self, session_id: int) -> None:
        """Silently copy the double-clicked word to identify which dictated word is being fixed."""
        time.sleep(0.025)
        with self._lock:
            if (
                not self._capturing_keys
                or self._session_id != session_id
                or self._typed_chars
            ):
                return
            last_words = list(self._last_words)
            last_text = self._last_text

        self.is_probing_clipboard = True
        self.last_probe_time = time.monotonic()
        try:
            prev_clip = get_clipboard_text()
            if self.daemon is not None:
                self.daemon.last_ctrl_c_time = 0.0

            send_ctrl_c()
            time.sleep(0.035)
            self.last_probe_time = time.monotonic()
            sel = (get_clipboard_text() or "").strip()

            if isinstance(prev_clip, str) and prev_clip and prev_clip != sel:
                set_clipboard_text(prev_clip)
                if self.daemon is not None:
                    try:
                        self.daemon._clip_listener._last_text = prev_clip
                        self.daemon._clip_listener._last_update_time = time.monotonic()
                    except Exception:
                        pass
            if self.daemon is not None:
                self.daemon.last_ctrl_c_time = 0.0
        finally:
            self.last_probe_time = time.monotonic()
            self.is_probing_clipboard = False

        if not sel or len(sel) > 48:
            return

        m = re.search(r"[A-Za-z0-9']+", sel)
        if not m:
            return
        clicked_word = m.group(0).strip("'")
        if len(clicked_word) < 2:
            return

        matched = ""
        for w in last_words:
            if w.lower() == clicked_word.lower():
                matched = w
                break
        if not matched and clicked_word.lower() in last_text.lower():
            matched = clicked_word

        with self._lock:
            if self._session_id != session_id or not self._capturing_keys:
                return
            if matched:
                self._selected_word = matched
            else:
                self._cancel_capture_locked()

    def on_key_press(self, event: Any) -> None:
        """Record replacement characters typed after double-clicking a dictated word."""
        if not self._capturing_keys:
            return

        now = time.monotonic()
        if (now - self._capture_start_time) > 20.0:
            self._cancel_capture()
            return

        name_raw = str(getattr(event, "name", "") or "")
        name_low = name_raw.lower()
        if not name_low or name_low in _IGNORED_MODIFIER_NAMES:
            return

        ctrl = bool(_user32.GetAsyncKeyState(VK_CONTROL) & 0x8000)
        alt = bool(_user32.GetAsyncKeyState(VK_MENU) & 0x8000)

        if ctrl or alt:
            if ctrl and not alt and name_low == "c":
                return
            if ctrl and not alt and name_low == "v":
                threading.Thread(
                    target=self._commit_from_paste,
                    daemon=True,
                    name="stt-paste-learn",
                ).start()
                return
            self._cancel_capture()
            return

        if name_low == "esc":
            self._cancel_capture()
            return

        if name_low == "backspace":
            with self._lock:
                if self._typed_chars:
                    self._typed_chars.pop()
                    self._reset_commit_timer_locked()
            return

        if len(name_raw) == 1 and (name_raw.isalnum() or name_raw in ("'", "-")):
            shift = bool(_user32.GetAsyncKeyState(VK_SHIFT) & 0x8000)
            caps = bool(_user32.GetKeyState(VK_CAPITAL) & 0x0001)
            if name_raw.isalpha():
                ch = name_raw.upper() if (shift ^ caps) else name_raw.lower()
            else:
                ch = name_raw
            with self._lock:
                self._typed_chars.append(ch)
                self._reset_commit_timer_locked()
            return

        if name_low in _COMMIT_KEY_NAMES:
            self._commit_or_cancel()

    def _commit_from_paste(self) -> None:
        time.sleep(0.03)
        pasted = (get_clipboard_text() or "").strip()
        if not pasted or len(pasted) > 48:
            self._cancel_capture()
            return
        with self._lock:
            self._typed_chars = list(pasted)
        self._commit_correction()

    def _reset_commit_timer_locked(self) -> None:
        if self._commit_timer is not None:
            self._commit_timer.cancel()
        self._commit_timer = threading.Timer(
            self.COMMIT_PAUSE_S, self._commit_correction
        )
        self._commit_timer.daemon = True
        self._commit_timer.start()

    def _commit_or_cancel(self) -> None:
        with self._lock:
            has_chars = bool(self._typed_chars)
        if has_chars:
            self._commit_correction()
        else:
            self._cancel_capture()

    def reset(self) -> None:
        """Cancel any active key capture and clear cached dictation words."""
        with self._lock:
            self._cancel_capture_locked()
            self._last_text = ""
            self._last_words = []
            self._last_dictation_time = 0.0
            self._last_click_time = 0.0
            self._last_click_pos = (0, 0)
            self._pending_dblclick_release = False

    def _cancel_capture(self) -> None:
        with self._lock:
            self._cancel_capture_locked()

    def _cancel_capture_locked(self) -> None:
        if self._commit_timer is not None:
            self._commit_timer.cancel()
            self._commit_timer = None
        self._capturing_keys = False
        self._selected_word = ""
        self._typed_chars = []

    def _resolve_target_word(
        self, selected_word: str, last_words: list[str], replacement: str
    ) -> str:
        """Resolve which dictated word was replaced, using clipboard probe or fuzzy match."""
        if selected_word:
            return selected_word
        if not last_words:
            return ""
        if len(last_words) == 1:
            return last_words[0]

        rep_low = replacement.lower()
        best_word = ""
        best_score = 0.0
        for candidate in last_words:
            cand_low = candidate.lower()
            if cand_low == rep_low:
                continue
            score = difflib.SequenceMatcher(None, cand_low, rep_low).ratio()
            if cand_low[:1] == rep_low[:1]:
                score += 0.1
            if score > best_score:
                best_score = score
                best_word = candidate
        return best_word if best_score >= 0.42 else ""

    def _commit_correction(self) -> None:
        """Persist the learned ``heard -> written`` rule into ``stt_vocabulary``."""
        with self._lock:
            if not self._capturing_keys:
                return
            replacement = "".join(self._typed_chars).strip("'- ")
            selected_word = self._selected_word
            last_words = list(self._last_words)
            self._cancel_capture_locked()

        if len(replacement) < 2:
            return

        heard = self._resolve_target_word(selected_word, last_words, replacement)
        if not heard or len(heard) < 2 or heard == replacement:
            return

        if self.daemon is not None:
            with self.daemon._config_lock:
                saved = set_stt_vocab_rule(self.daemon.config, heard, replacement)
        else:
            from audify.config import load_config

            cfg = load_config()
            saved = set_stt_vocab_rule(cfg, heard, replacement)

        if not saved:
            return

        pat = re.compile(rf"(?<!\w){re.escape(heard)}(?!\w)", re.IGNORECASE)
        with self._lock:
            if self._last_text:
                self._last_text = pat.sub(replacement, self._last_text)
            self._last_words = [
                replacement if w.lower() == heard.lower() else w
                for w in self._last_words
            ]

        if self.daemon is not None:
            if self.daemon.stt.last_dictated:
                self.daemon.stt.last_dictated = pat.sub(
                    replacement, self.daemon.stt.last_dictated
                )
            try:
                self.daemon.stt.hud.show_learned(heard, replacement)
            except Exception:
                pass
            print(f"[STT Auto-Learn] Learned correction: '{heard}' -> '{replacement}'")
            self.daemon._set_status(
                f"Learned: {heard} -> {replacement}", speaking=False
            )

            def _restore_ready() -> None:
                if (
                    self.daemon is not None
                    and not self.daemon.is_speaking
                    and not self.daemon.stt.is_busy
                    and self.daemon.status.startswith("Learned:")
                ):
                    self.daemon._set_status("Ready", speaking=False)

            t = threading.Timer(2.8, _restore_ready)
            t.daemon = True
            t.start()


# -- Embedded Sidecar Worker Script for Bark 3.0 Clean -----------------------

_WORKER_SCRIPT = r'''"""Persistent JSON-lines sidecar worker for Underdog Bark 3.0 Clean."""
import importlib.machinery
import importlib.util
import json
import os
import sys
import time
import types
import urllib.request

os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"
os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
os.environ["TRANSFORMERS_VERBOSITY"] = "error"
os.environ["TOKENIZERS_PARALLELISM"] = "false"

try:
    exe_dir = os.path.dirname(os.path.abspath(sys.executable))
    if os.path.isdir(exe_dir) and hasattr(os, "add_dll_directory"):
        os.add_dll_directory(exe_dir)
except Exception:
    pass

BARK_DIR = sys.argv[1]
MODEL_BARK = os.path.join(BARK_DIR, "model.bark")
BARK_LOADER = os.path.join(BARK_DIR, "bark_loader.py")
CACHED_DIR = os.path.join(BARK_DIR, "cached_hf")

BARK_MODEL_URL = "https://huggingface.co/ConwayResearch/Underdog-Bark-3.0-Clean/resolve/main/model.bark"
BARK_LOADER_URL = "https://huggingface.co/ConwayResearch/Underdog-Bark-3.0-Clean/resolve/main/bark_loader.py"


def emit(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _install_librosa_shim(np) -> None:
    """Provide an exact float32 Slaney mel filterbank if librosa is not installed.

    ParakeetFeatureExtractor in transformers is decorated with
    @requires(backends=('torch', 'librosa')) solely to call
    librosa.filters.mel(..., norm='slaney'). Registering this pure-NumPy shim
    satisfies both importlib.util.find_spec('librosa') and the exact filterbank
    computation without requiring 200+ MB of numba/llvmlite/scipy/scikit-learn.
    """
    if importlib.util.find_spec("librosa") is not None:
        return

    def _mel(
        *,
        sr: float = 22050,
        n_fft: int = 2048,
        n_mels: int = 128,
        fmin: float = 0.0,
        fmax: float | None = None,
        htk: bool = False,
        norm: str | None = "slaney",
        dtype=np.float32,
    ):
        if fmax is None:
            fmax = float(sr) / 2.0
        n_mels = int(n_mels)
        weights = np.zeros((n_mels, int(1 + n_fft // 2)), dtype=dtype)
        fftfreqs = np.fft.rfftfreq(n=n_fft, d=1.0 / sr)
        f_min = 0.0
        f_sp = 200.0 / 3.0
        min_log_hz = 1000.0
        min_log_mel = (min_log_hz - f_min) / f_sp
        logstep = np.log(6.4) / 27.0

        def _hz_to_mel(f):
            f_arr = np.asanyarray(f)
            mels = (f_arr - f_min) / f_sp
            if np.ndim(f_arr) > 0:
                log_region = f_arr >= min_log_hz
                mels[log_region] = (
                    min_log_mel + np.log(f_arr[log_region] / min_log_hz) / logstep
                )
            elif f_arr >= min_log_hz:
                mels = min_log_mel + np.log(f_arr / min_log_hz) / logstep
            return mels

        def _mel_to_hz(mels):
            m_arr = np.asanyarray(mels)
            freqs = f_min + f_sp * m_arr
            log_region = m_arr >= min_log_mel
            freqs[log_region] = min_log_hz * np.exp(
                logstep * (m_arr[log_region] - min_log_mel)
            )
            return freqs

        mel_f = _mel_to_hz(
            np.linspace(_hz_to_mel(fmin), _hz_to_mel(fmax), n_mels + 2)
        )
        fdiff = np.diff(mel_f)
        ramps = np.subtract.outer(mel_f, fftfreqs)
        for i in range(n_mels):
            lower = -ramps[i] / fdiff[i]
            upper = ramps[i + 2] / fdiff[i + 1]
            weights[i] = np.maximum(0, np.minimum(lower, upper))
        if norm == "slaney":
            enorm = 2.0 / (mel_f[2 : n_mels + 2] - mel_f[:n_mels])
            weights *= enorm[:, np.newaxis]
        return weights.astype(dtype)

    lib_mod = types.ModuleType("librosa")
    lib_mod.__spec__ = importlib.machinery.ModuleSpec("librosa", loader=None)
    lib_mod.__version__ = "0.10.2"
    filters_mod = types.ModuleType("librosa.filters")
    filters_mod.__spec__ = importlib.machinery.ModuleSpec("librosa.filters", loader=None)
    filters_mod.mel = _mel
    lib_mod.filters = filters_mod
    sys.modules["librosa"] = lib_mod
    sys.modules["librosa.filters"] = filters_mod


def _download_with_progress(url: str, dst_path: str, label: str = "") -> None:
    tmp_path = dst_path + ".part"
    last_err: Exception | None = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "Audify-BarkClean/2.9"}
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                total = int(resp.headers.get("Content-Length") or 0)
                downloaded = 0
                last_pct = -5
                with open(tmp_path, "wb") as out_f:
                    while True:
                        chunk = resp.read(256 * 1024)
                        if not chunk:
                            break
                        out_f.write(chunk)
                        downloaded += len(chunk)
                        if label and total > 0:
                            pct = min(100, int((downloaded * 100) / total))
                            if pct - last_pct >= 2 or pct == 100:
                                last_pct = pct
                                emit(
                                    {
                                        "type": "status",
                                        "status": f"{label} ({pct}%)...",
                                    }
                                )
            os.replace(tmp_path, dst_path)
            return
        except Exception as e:
            last_err = e
            time.sleep(1.0 * (attempt + 1))
    raise RuntimeError(f"Failed to download {url}: {last_err}")


def ensure_downloaded() -> None:
    os.makedirs(BARK_DIR, exist_ok=True)
    if not os.path.isfile(BARK_LOADER) or os.path.getsize(BARK_LOADER) < 1000:
        _download_with_progress(BARK_LOADER_URL, BARK_LOADER)
    if not os.path.isfile(MODEL_BARK) or os.path.getsize(MODEL_BARK) < 179_000_000:
        emit({"type": "status", "status": "Downloading Bark 3.0 model (0%)..."})
        _download_with_progress(
            BARK_MODEL_URL, MODEL_BARK, label="Downloading Bark 3.0 model"
        )


def load_bark_clean(np, torch):
    import concurrent.futures

    _install_librosa_shim(np)
    from transformers import (
        AutoProcessor,
        GenerationConfig,
        ParakeetForTDT,
        ParakeetTDTConfig,
    )

    # Fast path: load already-unpacked safetensors cache if present
    safetensors_path = os.path.join(CACHED_DIR, "model.safetensors")
    if os.path.isfile(safetensors_path) and os.path.getsize(safetensors_path) > 100_000_000:
        try:
            emit({"type": "status", "status": "Loading Bark 3.0 Clean..."})
            model = ParakeetForTDT.from_pretrained(CACHED_DIR, dtype=torch.float32).eval()
            model.generation_config = GenerationConfig.from_pretrained(CACHED_DIR)
            proc = AutoProcessor.from_pretrained(CACHED_DIR)
            return model, proc
        except Exception:
            pass

    ensure_downloaded()
    emit({"type": "status", "status": "Unpacking Bark 3.0 Clean weights..."})

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

    emit({"type": "status", "status": "Configuring Bark 3.0 tokenizer..."})
    cfg = json.loads(head["config"])
    model = ParakeetForTDT(ParakeetTDTConfig.from_dict(cfg))
    model.load_state_dict(sd, strict=False)
    model.generation_config = GenerationConfig.from_pretrained("nvidia/parakeet-tdt-0.6b-v3")
    model = model.eval().to(device="cpu", dtype=torch.float32)
    proc = AutoProcessor.from_pretrained("nvidia/parakeet-tdt-0.6b-v3")

    try:
        emit({"type": "status", "status": "Caching Bark 3.0 Clean model..."})
        os.makedirs(CACHED_DIR, exist_ok=True)
        model.save_pretrained(CACHED_DIR)
        proc.save_pretrained(CACHED_DIR)
        if model.generation_config is not None:
            model.generation_config.save_pretrained(CACHED_DIR)
    except Exception:
        pass

    return model, proc


def transcribe_audio(np, sf, torch, model, proc, wav_path: str) -> str:
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
        import numpy as np
        import soundfile as sf
        import torch

        torch.set_num_threads(max(2, min(6, os.cpu_count() or 4)))
        model, proc = load_bark_clean(np, torch)
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
            req_id = req.get("req_id", 0)
            t0 = time.perf_counter()
            try:
                text = transcribe_audio(np, sf, torch, model, proc, wav_path)
                elapsed_ms = (time.perf_counter() - t0) * 1000.0
                emit({"type": "result", "req_id": req_id, "ok": True, "text": text, "ms": round(elapsed_ms, 1)})
            except Exception as e:
                emit({"type": "result", "req_id": req_id, "ok": False, "error": str(e)})


if __name__ == "__main__":
    main()
'''


PYTHON_EMBED_URL = (
    "https://www.python.org/ftp/python/3.12.8/python-3.12.8-embed-amd64.zip"
)
GET_PIP_URL = "https://bootstrap.pypa.io/get-pip.py"
_bootstrap_lock = threading.Lock()


def get_bark_runtime_python() -> str:
    """Return path to Audify's self-contained Python runtime in `%LOCALAPPDATA%\\Audify\\bark_clean\\runtime`."""
    return os.path.join(get_bark_dir(), "runtime", "python.exe")


def _download_file(
    url: str,
    dst_path: str,
    status_cb: Callable[[str], None] | None = None,
    label: str = "",
) -> None:
    """Download *url* to *dst_path* with a 30s socket timeout, retry backoff, and optional progress callback."""
    tmp_path = dst_path + ".part"
    last_err: Exception | None = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "Audify-Installer/2.9"}
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                total = int(resp.headers.get("Content-Length") or 0)
                downloaded = 0
                last_pct = -10
                with open(tmp_path, "wb") as out_f:
                    while True:
                        chunk = resp.read(128 * 1024)
                        if not chunk:
                            break
                        out_f.write(chunk)
                        downloaded += len(chunk)
                        if status_cb and label and total > 0:
                            pct = min(100, int((downloaded * 100) / total))
                            if pct - last_pct >= 10 or pct == 100:
                                last_pct = pct
                                status_cb(f"{label} ({pct}%)...")
            os.replace(tmp_path, dst_path)
            return
        except Exception as e:
            last_err = e
            time.sleep(0.8 * (attempt + 1))
    raise RuntimeError(f"Download failed ({url}): {last_err}")


def find_bark_python() -> str | None:
    """Locate a Python interpreter on the machine with complete Bark 3.0 Clean package support."""
    runtime_py = get_bark_runtime_python()
    candidates: list[str] = [
        runtime_py,
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
    runtime_norm = os.path.normcase(os.path.abspath(runtime_py))
    seen: set[str] = set()
    required_pkgs = (
        "torch",
        "constriction",
        "soxr",
        "safetensors",
        "huggingface_hub",
        "numpy",
    )

    for exe in candidates:
        if not exe:
            continue
        norm = os.path.normcase(os.path.abspath(exe))
        if norm in seen or not os.path.isfile(exe):
            continue
        seen.add(norm)
        exe_dir = os.path.dirname(os.path.abspath(exe))

        # For the embedded Python runtime, ensure VC++ runtime DLLs from msvc-runtime are present
        if norm == runtime_norm:
            if not os.path.isfile(os.path.join(exe_dir, "msvcp140.dll")):
                continue

        py_tag = os.path.basename(exe_dir)  # e.g. "Python314"
        site_roots = [
            os.path.join(appdata, "Python", py_tag, "site-packages"),
            os.path.join(exe_dir, "Lib", "site-packages"),
            os.path.join(os.path.dirname(exe_dir), "Lib", "site-packages"),
        ]
        found_pkgs: set[str] = set()
        has_soundfile = False
        has_parakeet_tdt = False
        for sp in site_roots:
            if not os.path.isdir(sp):
                continue
            for pkg in required_pkgs:
                if os.path.isdir(os.path.join(sp, pkg)):
                    found_pkgs.add(pkg)
            if (
                os.path.isfile(os.path.join(sp, "soundfile.py"))
                or os.path.isdir(os.path.join(sp, "soundfile"))
                or os.path.isdir(os.path.join(sp, "_soundfile_data"))
            ):
                has_soundfile = True
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
        if (
            has_parakeet_tdt
            and has_soundfile
            and all(p in found_pkgs for p in required_pkgs)
        ):
            return exe
    return None


def _ensure_pth_configured(rt_dir: str) -> None:
    """Ensure python312._pth enables `import site`, `.`, and `Lib\\site-packages`."""
    pth_file = os.path.join(rt_dir, "python312._pth")
    if not os.path.isfile(pth_file):
        return
    try:
        with open(pth_file, "r", encoding="utf-8") as f:
            pth_txt = f.read()
        updated = pth_txt.replace("#import site", "import site")
        lines = [ln.strip() for ln in updated.splitlines() if ln.strip()]
        if "." not in lines:
            lines.insert(1 if lines else 0, ".")
        if "Lib\\site-packages" not in lines:
            lines.append("Lib\\site-packages")
        if "import site" not in lines:
            lines.append("import site")
        with open(pth_file, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except Exception:
        pass


def bootstrap_bark_python(
    status_cb: Callable[[str], None] | None = None,
) -> str | None:
    """Automatically download and configure a self-contained Python + Bark 3.0 Clean runtime.

    Runs only on machines that do not already have Python with PyTorch and
    ``transformers`` (``ParakeetForTDT``) installed, keeping ``Audify.exe`` lightweight
    while making Speech-to-Text work out of the box on a clean Windows PC.
    """
    with _bootstrap_lock:
        existing = find_bark_python()
        if existing:
            return existing

        rt_dir = os.path.join(get_bark_dir(), "runtime")
        os.makedirs(rt_dir, exist_ok=True)
        py_exe = os.path.join(rt_dir, "python.exe")

        try:
            if not os.path.isfile(py_exe):
                if status_cb:
                    status_cb("Downloading Python runtime (0%)...")
                zip_path = os.path.join(rt_dir, "python_embed.zip")
                _download_file(
                    PYTHON_EMBED_URL,
                    zip_path,
                    status_cb=status_cb,
                    label="Downloading Python runtime",
                )
                with zipfile.ZipFile(zip_path, "r") as zf:
                    zf.extractall(rt_dir)
                try:
                    os.remove(zip_path)
                except Exception:
                    pass

            _ensure_pth_configured(rt_dir)

            pip_exe = os.path.join(rt_dir, "Scripts", "pip.exe")
            if not os.path.isfile(pip_exe):
                if status_cb:
                    status_cb("Configuring Python package manager...")
                get_pip_py = os.path.join(rt_dir, "get-pip.py")
                _download_file(GET_PIP_URL, get_pip_py)
                subprocess.run(
                    [py_exe, get_pip_py, "--no-warn-script-location"],
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    text=True,
                    creationflags=CREATE_NO_WINDOW,
                )

            if status_cb:
                status_cb("Installing Bark 3.0 CPU engine (step 1/2)...")
            subprocess.run(
                [
                    py_exe,
                    "-m",
                    "pip",
                    "install",
                    "--no-warn-script-location",
                    "msvc-runtime",
                    "torch",
                    "--index-url",
                    "https://download.pytorch.org/whl/cpu",
                    "--extra-index-url",
                    "https://pypi.org/simple",
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                creationflags=CREATE_NO_WINDOW,
            )
            if status_cb:
                status_cb("Installing Bark 3.0 audio libraries (step 2/2)...")
            subprocess.run(
                [
                    py_exe,
                    "-m",
                    "pip",
                    "install",
                    "--no-warn-script-location",
                    "msvc-runtime",
                    "transformers>=5.5.0",
                    "constriction",
                    "soundfile",
                    "soxr",
                    "numpy",
                    "huggingface_hub",
                    "safetensors",
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                creationflags=CREATE_NO_WINDOW,
            )
        except subprocess.CalledProcessError as e:
            err_tail = (e.stderr or str(e)).strip().splitlines()[-1:]
            detail = err_tail[0] if err_tail else str(e)
            print(f"[WARN] Bark 3.0 Clean runtime bootstrap failed: {detail}")
            return None
        except Exception as e:
            print(f"[WARN] Bark 3.0 Clean runtime bootstrap failed: {e}")
            return None

        return find_bark_python()


# -- Tier 1 (Wispr Flow Bridge) & Tier 2 (Groq Whisper Large v3 Cloud STT) ---

VK_LCONTROL = 0xA2
VK_LWIN = 0x5B
VK_SPACE = 0x20
VK_ESCAPE = 0x1B

_WISPR_ERROR_KEYS: tuple[str, ...] = (
    "TranscriptionError",
    "NoInternet",
    "WeeklyWordsLimitReached",
    "HelperLoadingError",
    "HelperNotReady",
    "HelperPersistentFailure",
    "MicDisconnected",
    "MicNotFound",
    "MicDeviceUnavailable",
    "MicNotReadable",
    "MicAccessError",
    "NoAudio",
    "NoAudioDuringDictation",
    "NoAudioDuringDictationEscalated",
    "MicAccessTimeout",
    "TranscriptIncomplete",
)


def get_wispr_config_path() -> str:
    """Return the path to Wispr Flow's config.json in %APPDATA%."""
    appdata = os.environ.get("APPDATA") or os.path.expanduser(r"~\AppData\Roaming")
    return os.path.join(appdata, "Wispr Flow", "config.json")


def get_wispr_db_path() -> str:
    """Return the path to Wispr Flow's flow.sqlite database in %APPDATA%."""
    appdata = os.environ.get("APPDATA") or os.path.expanduser(r"~\AppData\Roaming")
    return os.path.join(appdata, "Wispr Flow", "flow.sqlite")


def get_wispr_flow_pids() -> set[int]:
    """Return all running ``Wispr Flow.exe`` and ``Wispr Flow Helper.exe`` process IDs."""
    pids: set[int] = set()
    try:
        TH32CS_SNAPPROCESS = 0x00000002

        class PROCESSENTRY32W(ctypes.Structure):
            _fields_ = [
                ("dwSize", wintypes.DWORD),
                ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t),
                ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", wintypes.LONG),
                ("dwFlags", wintypes.DWORD),
                ("szExeFile", wintypes.WCHAR * 260),
            ]

        h_snap = _kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if h_snap == wintypes.HANDLE(-1).value or not h_snap:
            return pids
        try:
            pe = PROCESSENTRY32W()
            pe.dwSize = ctypes.sizeof(PROCESSENTRY32W)
            if _kernel32.Process32FirstW(h_snap, ctypes.byref(pe)):
                while True:
                    exe_low = pe.szExeFile.lower()
                    if exe_low in ("wispr flow.exe", "wispr flow helper.exe"):
                        pids.add(int(pe.th32ProcessID))
                    if not _kernel32.Process32NextW(h_snap, ctypes.byref(pe)):
                        break
        finally:
            _kernel32.CloseHandle(h_snap)
    except Exception:
        pass
    return pids


def is_wispr_flow_running() -> bool:
    """Return True if ``Wispr Flow.exe`` is currently running on Windows."""
    return len(get_wispr_flow_pids()) > 0


def get_wispr_capture_state_and_peak(
    wispr_pids: set[int] | None = None,
) -> tuple[bool, float]:
    """Query Windows CoreAudio eCapture sessions for Wispr Flow.

    Returns ``(is_recording_active, mic_peak_0_to_1)`` so we know with 100% accuracy
    when Wispr Flow is actively capturing the microphone and when the user's voice/pitch drops.
    """
    pids = wispr_pids if wispr_pids is not None else get_wispr_flow_pids()
    if not pids:
        return False, 0.0

    from audify.engine import (
        _CLSID_MMDeviceEnumerator,
        _GUID,
        _IID_IAudioSessionControl2,
        _IID_IAudioSessionManager2,
        _IID_IMMDeviceEnumerator,
        _com_release,
        _com_vcall,
        _ole32,
    )

    iid_meter = _GUID("{C02216F6-8C67-4B5B-9D00-D008E73E0064}")
    hr_init = _ole32.CoInitializeEx(None, 0)
    enum_ptr = ctypes.c_void_p()
    dev_ptr = ctypes.c_void_p()
    mgr_ptr = ctypes.c_void_p()
    senum_ptr = ctypes.c_void_p()
    dev_meter_ptr = ctypes.c_void_p()
    try:
        if (
            _ole32.CoCreateInstance(
                ctypes.byref(_CLSID_MMDeviceEnumerator),
                None,
                7,
                ctypes.byref(_IID_IMMDeviceEnumerator),
                ctypes.byref(enum_ptr),
            )
            != 0
            or not enum_ptr.value
        ):
            return False, 0.0
        # eCapture = 1, eConsole = 0
        if (
            _com_vcall(
                enum_ptr,
                4,
                ctypes.c_long,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.POINTER(ctypes.c_void_p),
            )(1, 0, ctypes.byref(dev_ptr))
            != 0
            or not dev_ptr.value
        ):
            return False, 0.0
        if (
            _com_vcall(
                dev_ptr,
                3,
                ctypes.c_long,
                ctypes.POINTER(_GUID),
                wintypes.DWORD,
                ctypes.c_void_p,
                ctypes.POINTER(ctypes.c_void_p),
            )(ctypes.byref(_IID_IAudioSessionManager2), 7, None, ctypes.byref(mgr_ptr))
            != 0
            or not mgr_ptr.value
        ):
            return False, 0.0
        if (
            _com_vcall(mgr_ptr, 5, ctypes.c_long, ctypes.POINTER(ctypes.c_void_p))(
                ctypes.byref(senum_ptr)
            )
            != 0
            or not senum_ptr.value
        ):
            return False, 0.0

        scnt = ctypes.c_int()
        if (
            _com_vcall(senum_ptr, 3, ctypes.c_long, ctypes.POINTER(ctypes.c_int))(
                ctypes.byref(scnt)
            )
            != 0
        ):
            return False, 0.0

        is_active = False
        max_peak = 0.0
        for i in range(scnt.value):
            s_ptr = ctypes.c_void_p()
            s2_ptr = ctypes.c_void_p()
            m_ptr = ctypes.c_void_p()
            try:
                if (
                    _com_vcall(
                        senum_ptr,
                        4,
                        ctypes.c_long,
                        ctypes.c_int,
                        ctypes.POINTER(ctypes.c_void_p),
                    )(i, ctypes.byref(s_ptr))
                    != 0
                    or not s_ptr.value
                ):
                    continue
                if (
                    _com_vcall(
                        s_ptr,
                        0,
                        ctypes.c_long,
                        ctypes.POINTER(_GUID),
                        ctypes.POINTER(ctypes.c_void_p),
                    )(ctypes.byref(_IID_IAudioSessionControl2), ctypes.byref(s2_ptr))
                    != 0
                    or not s2_ptr.value
                ):
                    continue
                pid = wintypes.DWORD()
                _com_vcall(s2_ptr, 14, ctypes.c_long, ctypes.POINTER(wintypes.DWORD))(
                    ctypes.byref(pid)
                )
                if int(pid.value) not in pids:
                    continue
                st = ctypes.c_int()
                _com_vcall(s2_ptr, 3, ctypes.c_long, ctypes.POINTER(ctypes.c_int))(
                    ctypes.byref(st)
                )
                if st.value == 1:
                    is_active = True
                    if (
                        _com_vcall(
                            s_ptr,
                            0,
                            ctypes.c_long,
                            ctypes.POINTER(_GUID),
                            ctypes.POINTER(ctypes.c_void_p),
                        )(ctypes.byref(iid_meter), ctypes.byref(m_ptr))
                        == 0
                        and m_ptr.value
                    ):
                        pk = ctypes.c_float()
                        _com_vcall(
                            m_ptr, 3, ctypes.c_long, ctypes.POINTER(ctypes.c_float)
                        )(ctypes.byref(pk))
                        if pk.value > max_peak:
                            max_peak = float(pk.value)
            finally:
                _com_release(m_ptr)
                _com_release(s2_ptr)
                _com_release(s_ptr)

        if is_active and max_peak == 0.0:
            # Also check endpoint-level capture meter as fallback
            if (
                _com_vcall(
                    dev_ptr,
                    3,
                    ctypes.c_long,
                    ctypes.POINTER(_GUID),
                    wintypes.DWORD,
                    ctypes.c_void_p,
                    ctypes.POINTER(ctypes.c_void_p),
                )(ctypes.byref(iid_meter), 7, None, ctypes.byref(dev_meter_ptr))
                == 0
                and dev_meter_ptr.value
            ):
                pk = ctypes.c_float()
                _com_vcall(
                    dev_meter_ptr, 3, ctypes.c_long, ctypes.POINTER(ctypes.c_float)
                )(ctypes.byref(pk))
                if pk.value > max_peak:
                    max_peak = float(pk.value)

        return is_active, max_peak
    except Exception:
        return False, 0.0
    finally:
        _com_release(dev_meter_ptr)
        _com_release(senum_ptr)
        _com_release(mgr_ptr)
        _com_release(dev_ptr)
        _com_release(enum_ptr)
        if hr_init in (0, 1):
            try:
                _ole32.CoUninitialize()
            except Exception:
                pass


def stop_wispr_flow_if_active() -> bool:
    """Stop Wispr Flow hands-free recording if its Windows capture session is currently active."""
    active, _ = get_wispr_capture_state_and_peak()
    if not active:
        return False
    return trigger_wispr_flow_handsfree()


def dismiss_wispr_flow_if_active() -> bool:
    """Dismiss/cancel Wispr Flow recording via Escape (shortcut '27': 'dismiss') without transcribing silence."""
    active, _ = get_wispr_capture_state_and_peak()
    if not active:
        return False
    try:
        _user32.keybd_event(VK_ESCAPE, 0, 0, 0)
        time.sleep(0.03)
        _user32.keybd_event(VK_ESCAPE, 0, KEYEVENTF_KEYUP, 0)
        time.sleep(0.08)
        still_active, _ = get_wispr_capture_state_and_peak()
        if still_active:
            trigger_wispr_flow_handsfree()
        return True
    except Exception:
        return False


def get_wispr_latest_history_row() -> tuple[str, str, str]:
    """Read the newest completed non-empty row from Wispr Flow's ``flow.sqlite`` ``History`` table.

    Filters out stuck NULL/empty rows so in-progress or aborted rows never freeze ``ts_before``.
    Returns ``(timestamp_str, transcript_text, status_str)``.
    """
    db_path = get_wispr_db_path()
    if not os.path.exists(db_path):
        return "", "", ""
    try:
        import sqlite3

        con = sqlite3.connect(db_path, timeout=2.0)
        try:
            cur = con.cursor()
            cur.execute(
                "SELECT timestamp, formattedText, asrText, status FROM History "
                "WHERE coalesce(formattedText, asrText, '') != '' "
                "ORDER BY timestamp DESC LIMIT 1"
            )
            row = cur.fetchone()
            if not row:
                return "", "", ""
            ts = str(row[0] or "")
            text = str(row[1] or row[2] or "").strip()
            status = str(row[3] or "")
            return ts, text, status
        finally:
            con.close()
    except Exception:
        return "", "", ""


def get_wispr_latest_error() -> tuple[int, str]:
    """Inspect Wispr Flow's config.json notifications and return ``(latest_timestamp_ms, error_name)``."""
    cfg_path = get_wispr_config_path()
    if not os.path.exists(cfg_path):
        return 0, ""
    try:
        with open(cfg_path, "r", encoding="utf-8", errors="ignore") as f:
            data = json.load(f)
        notifs = data.get("notifications") or {}
        best_ts = 0
        best_key = ""
        for k in _WISPR_ERROR_KEYS:
            entry = notifs.get(k)
            if isinstance(entry, dict):
                ts = int(entry.get("lastShownTime") or 0)
                if ts > best_ts:
                    best_ts = ts
                    best_key = k
        return best_ts, best_key
    except Exception:
        return 0, ""


def trigger_wispr_flow_handsfree() -> bool:
    """Trigger Wispr Flow's hands-free toggle shortcut (``Left Ctrl + Left Win + Space``).

    Returns True if ``Wispr Flow.exe`` is running and the shortcut was sent, or False
    if Wispr Flow is not running so the caller can immediately fall back to Tier 2 (Groq).
    """
    if not is_wispr_flow_running():
        return False
    try:
        _user32.keybd_event(VK_LCONTROL, 0, 0, 0)
        _user32.keybd_event(VK_LWIN, 0, 0, 0)
        _user32.keybd_event(VK_SPACE, 0, 0, 0)
        time.sleep(0.035)
        _user32.keybd_event(VK_SPACE, 0, KEYEVENTF_KEYUP, 0)
        _user32.keybd_event(VK_LWIN, 0, KEYEVENTF_KEYUP, 0)
        _user32.keybd_event(VK_LCONTROL, 0, KEYEVENTF_KEYUP, 0)
        return True
    except Exception:
        return False


def resolve_groq_api_key(daemon: TTSDaemon | None = None) -> str:
    """Resolve the user's Groq API key from config, process env, or HKCU\\Environment."""
    if daemon is not None:
        try:
            with daemon._config_lock:
                k = str(daemon.config.get("groq_api_key", "")).strip()
            if k:
                return k
        except Exception:
            pass
    env_k = os.environ.get("GROQ_API_KEY", "").strip()
    if env_k:
        return env_k
    if sys.platform == "win32":
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment") as reg_key:
                val, _ = winreg.QueryValueEx(reg_key, "GROQ_API_KEY")
                if isinstance(val, str) and val.strip():
                    return val.strip()
        except Exception:
            pass
    return ""


def transcribe_with_groq(
    wav_path: str,
    api_key: str,
    model: str = "whisper-large-v3",
    vocab: dict[str, str] | None = None,
) -> tuple[bool, str]:
    """Transcribe *wav_path* via Groq's highest-accuracy ``whisper-large-v3`` model.

    Uses zero local model RAM. Passes custom STT vocabulary targets in the Whisper
    ``prompt`` parameter so names and technical terms are recognized cleanly.
    """
    key = (api_key or "").strip()
    if not key:
        return False, "No Groq API key configured."
    if not os.path.exists(wav_path):
        return False, "Audio file missing."

    try:
        with open(wav_path, "rb") as f:
            wav_bytes = f.read()
    except Exception as e:
        return False, f"Could not read recorded WAV: {e}"

    vocab_terms: list[str] = []
    if vocab:
        seen: set[str] = set()
        for target in vocab.values():
            t = str(target).strip()
            if t and t.lower() not in seen:
                seen.add(t.lower())
                vocab_terms.append(t)
    prompt_str = (
        "Transcribe English, Bengali (বাংলা), and Hindi (हिन्दी) clearly with accurate punctuation. "
        "Keep English words in Latin script, Bengali in বাংলা script, and Hindi in हिन्दी script. "
        + (f"Vocabulary: {', '.join(vocab_terms[:35])}." if vocab_terms else "")
    ).strip()

    models_to_try = [model] if model == "whisper-large-v3-turbo" else [model, "whisper-large-v3-turbo"]
    last_err = ""

    for cur_model in models_to_try:
        boundary = f"----AudifyGroqBoundary{int(time.time() * 1000)}"
        parts: list[bytes] = []

        def _add_field(name: str, value: str) -> None:
            parts.append(
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
                f"{value}\r\n".encode("utf-8")
            )

        _add_field("model", cur_model)
        _add_field("temperature", "0")
        _add_field("response_format", "json")
        if prompt_str:
            _add_field("prompt", prompt_str)

        parts.append(
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="dictation.wav"\r\n'
            f"Content-Type: audio/wav\r\n\r\n".encode("utf-8")
        )
        parts.append(wav_bytes)
        parts.append(f"\r\n--{boundary}--\r\n".encode("utf-8"))
        body = b"".join(parts)

        req = urllib.request.Request(
            "https://api.groq.com/openai/v1/audio/transcriptions",
            data=body,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "User-Agent": "Audify/2.9.19",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=15.0) as resp:
                payload = json.loads(resp.read().decode("utf-8", errors="replace"))
            text = str(payload.get("text", "")).strip()
            low_t = text.lower().strip(" .!,?")
            if low_t in {
                "subtitles by the amara.org community",
                "amara.org",
                "thanks for watching",
                "thank you for watching",
                "thank you",
                "you",
            } or "amara.org" in low_t or "subtitles by" in low_t:
                return False, "Filtered Whisper silence hallucination."
            return True, text
        except urllib.error.HTTPError as e:
            err_body = ""
            try:
                err_body = e.read().decode("utf-8", errors="replace")[:200]
            except Exception:
                pass
            last_err = f"Groq HTTP {e.code}: {err_body or e.reason}"
            if e.code == 429 and cur_model != models_to_try[-1]:
                continue
            break
        except Exception as e:
            last_err = f"Groq request error: {e}"
            break

    return False, last_err or "Groq transcription failed."


class BarkCleanController:
    """Manages 3-tier STT hierarchy: Wispr Flow -> Groq whisper-large-v3 -> Bark 3.0 Clean (last resort)."""

    IDLE_UNLOAD_SECONDS = 60.0

    def __init__(self, daemon: TTSDaemon | None = None) -> None:
        self.daemon = daemon
        self.recorder = Win32MicRecorder()
        self.learner = DictationCorrectionLearner(daemon=daemon)
        self.hud = DictationVisualizerHUD(self)
        self.last_dictated: str = ""
        self._lock = threading.Lock()
        self._spawn_lock = threading.Lock()
        self._transcribing = False
        self._cancel_seq: int = 0
        self._active_req_id: int = 0
        self._last_toggle_time = 0.0
        self._last_paste_time = 0.0
        self._on_complete_cb: Callable[[bool, str], None] | None = None

        # Sidecar subprocess state (Bark 3.0 Clean is NEVER pre-warmed at startup or idle;
        # it is loaded strictly on demand if Tier 2 Groq fails, and unloaded immediately after)
        self._proc: subprocess.Popen | None = None
        self._proc_ready = threading.Event()
        self._proc_error: str | None = None
        self._result_event = threading.Event()
        self._last_result: dict | None = None
        self._idle_timer: threading.Timer | None = None
        self._startup_timer: threading.Timer | None = None

        # Watch Wispr Flow (Tier 1) for failures so Audify can automatically take over with Tier 2 (Groq)
        if self.daemon is not None:
            threading.Thread(
                target=self._wispr_watchdog_loop,
                daemon=True,
                name="wispr-flow-watchdog",
            ).start()

    def _wispr_watchdog_loop(self) -> None:
        """Monitor Wispr Flow strictly for error notifications when Audify is in standalone mode.

        Manual Ctrl+Win or Ctrl+Win+Space sessions belong to regular Wispr Flow dictation
        and are never intercepted or auto-stopped here.
        """
        cfg_path = get_wispr_config_path()
        last_mtime = 0.0
        last_err_ts, _ = get_wispr_latest_error()
        if os.path.exists(cfg_path):
            try:
                last_mtime = os.path.getmtime(cfg_path)
            except Exception:
                pass
        lock_file = os.path.join(tempfile.gettempdir(), "audify_voiceloop.lock")

        while True:
            time.sleep(0.5)
            if os.path.exists(lock_file):
                continue
            if not self.is_enabled or self.is_busy:
                continue
            try:
                if not os.path.exists(cfg_path):
                    continue
                mtime = os.path.getmtime(cfg_path)
                if mtime <= last_mtime:
                    continue
                last_mtime = mtime
                err_ts, err_key = get_wispr_latest_error()
                now_ms = int(time.time() * 1000)
                if err_ts > last_err_ts and abs(now_ms - err_ts) <= 8000:
                    last_err_ts = err_ts
                    print(
                        f"[STT] Detected Wispr Flow failure ({err_key}); switching to Groq whisper-large-v3 fallback..."
                    )
                    self.start_dictation()
            except Exception:
                pass

    @property
    def is_enabled(self) -> bool:
        """Return True if Speech-to-Text dictation is enabled in config."""
        if self.daemon is None:
            return True
        with self.daemon._config_lock:
            return bool(self.daemon.config.get("stt_enabled", True))

    def set_enabled(self, enabled: bool) -> None:
        """Enable or disable Speech-to-Text, immediately unloading any fallback model when disabled."""
        if not enabled:
            if self._startup_timer is not None:
                self._startup_timer.cancel()
                self._startup_timer = None
            self.learner.reset()
            if self.recorder.is_recording or self._transcribing:
                self.cancel_dictation()
            else:
                with self._lock:
                    self._cancel_seq += 1
                    self._active_req_id += 1
                    self._result_event.set()
                self.hud.hide()
            self.shutdown()

    def _background_startup_init(self) -> None:
        """No-op: Bark 3.0 Clean is never pre-loaded into RAM unless Groq fails."""
        return

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

        Returns the new state: ``"recording"``, ``"transcribing"``, ``"disabled"``, or ``"busy"``.
        """
        lock_file = os.path.join(tempfile.gettempdir(), "audify_voiceloop.lock")
        if os.path.exists(lock_file):
            return "agent_handled"
        if not self.is_enabled:
            return "disabled"

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

    def on_escape_pressed(self) -> None:
        """Called when Escape is pressed during active dictation or transcription to cancel it."""
        if self.recorder.is_recording or self._transcribing:
            self.cancel_dictation()

    def cancel_dictation(self) -> bool:
        """Immediately cancel an in-progress microphone recording or transcription without pasting."""
        was_recording = self.recorder.is_recording
        if not was_recording and not self._transcribing:
            self.hud.hide()
            return False

        if was_recording:
            self.recorder.cancel(wait=False)

        with self._lock:
            self._cancel_seq += 1
            self._active_req_id += 1
            self._transcribing = False
            self._last_result = {"ok": False, "cancelled": True, "error": "Dictation cancelled."}
            self._result_event.set()
            cb = self._on_complete_cb
            self._on_complete_cb = None

        self.hud.hide()
        if self.daemon is not None:
            try:
                self.daemon._clip_listener.set_recording_escape_guard(False)
            except Exception:
                pass
            self.daemon._set_status("Ready", speaking=False)

        if cb is not None:
            try:
                cb(False, "Dictation cancelled.")
            except Exception:
                pass
        self._resume_media_if_standalone()
        print("[STT] Dictation/transcription cancelled via Escape.")
        return True

    def _resume_media_if_standalone(self) -> None:
        """Resume any external video/audio paused by standalone dictation once dictation ends."""
        try:
            lock_file = os.path.join(tempfile.gettempdir(), "audify_voiceloop.lock")
            if os.path.exists(lock_file):
                return
            from audify.voice_loop import (
                _find_audify_pids,
                _resume_external_system_audio_if_paused_by_us,
            )

            self_pids = {os.getpid()} | _find_audify_pids() | get_wispr_flow_pids()
            _resume_external_system_audio_if_paused_by_us(
                self_pids, delay_before_s=0.5
            )
        except Exception:
            pass

    def start_dictation(
        self, on_complete: Callable[[bool, str], None] | None = None
    ) -> bool:
        """Start capturing microphone audio with automatic end-of-speech detection."""
        if not self.is_enabled or self.recorder.is_recording or self._transcribing:
            return False
        try:
            self._on_complete_cb = on_complete
            silence_delay_s = 1.0
            if self.daemon is not None:
                self.daemon.stop_audio()
                with self.daemon._config_lock:
                    silence_delay_s = float(
                        self.daemon.config.get("stt_silence_delay", 1.0)
                    )
                try:
                    self.daemon._clip_listener.set_recording_escape_guard(True)
                except Exception:
                    pass
            try:
                from audify.voice_loop import (
                    _find_audify_pids,
                    _pause_external_system_audio_if_playing,
                )

                self_pids = {os.getpid()} | _find_audify_pids() | get_wispr_flow_pids()
                _pause_external_system_audio_if_playing(self_pids, wait_after_s=0.15)
            except Exception:
                pass
            self.recorder.start(
                on_auto_stop=self._on_vad_auto_stop,
                auto_stop_on_silence=True,
                silence_delay_s=silence_delay_s,
            )
            self.hud.clear_status()
            self.hud.show()
            if self.daemon is not None:
                self.daemon._set_status("Listening...", speaking=False)
            return True
        except Exception as e:
            self.hud.hide()
            if self.daemon is not None:
                try:
                    self.daemon._clip_listener.set_recording_escape_guard(False)
                except Exception:
                    pass
                self.daemon._notify_error(f"Microphone error: {e}")
                self.daemon._set_status("Ready", speaking=False)
            return False

    def _on_vad_auto_stop(self, reason: str) -> None:
        """Called by Win32MicRecorder when VAD detects end of speech or silence timeout."""
        if reason == "speech_ended":
            self.stop_and_transcribe(_from_vad=True)
        else:
            self.hud.hide()
            with self.recorder._lock:
                self.recorder._frames = []
            if self.daemon is not None:
                try:
                    self.daemon._clip_listener.set_recording_escape_guard(False)
                except Exception:
                    pass
                self.daemon._set_status("Ready", speaking=False)
            cb = self._on_complete_cb
            self._on_complete_cb = None
            if cb is not None:
                try:
                    cb(False, "No speech detected.")
                except Exception:
                    pass
            self._resume_media_if_standalone()

    def stop_and_transcribe(
        self,
        on_complete: Callable[[bool, str], None] | None = None,
        _from_vad: bool = False,
    ) -> None:
        """Stop microphone recording, run Bark 3.0 Clean, and paste the cleaned text."""
        if (not self.recorder.is_recording and not _from_vad) or self._transcribing:
            return
        with self._lock:
            my_seq = self._cancel_seq
            self._transcribing = True
        if on_complete is not None:
            self._on_complete_cb = on_complete
        if self.daemon is not None:
            self.daemon._set_status("Transcribing...", speaking=False)

        def _worker() -> None:
            wav_path: str | None = None
            ok = False
            out_msg = ""
            show_hud_err = False
            try:
                fd, wav_path = tempfile.mkstemp(
                    prefix="audify_dictation_", suffix=".wav"
                )
                os.close(fd)
                duration_s = self.recorder.stop_and_save(wav_path)
                if self._cancel_seq != my_seq:
                    return
                if duration_s < 0.25:
                    out_msg = "Recording too short."
                    return

                ok, out_msg = self.transcribe_file(wav_path, cancel_seq=my_seq)
                if self._cancel_seq != my_seq:
                    return
                if ok and out_msg:
                    self._copy_and_paste(out_msg)
                elif ok and not out_msg:
                    out_msg = "No speech detected."
                    ok = False
                elif not ok and out_msg and out_msg != "Dictation cancelled.":
                    show_hud_err = True
            except Exception as e:
                if self._cancel_seq == my_seq:
                    ok = False
                    out_msg = f"Transcription failed: {e}"
                    show_hud_err = True
                    if self.daemon is not None:
                        self.daemon._notify_error(out_msg)
            finally:
                if wav_path:
                    try:
                        os.remove(wav_path)
                    except Exception:
                        pass
                if self._cancel_seq == my_seq:
                    self._transcribing = False
                    if show_hud_err and out_msg:
                        self.hud.show_error(out_msg, duration_s=3.4)
                    else:
                        self.hud.hide()
                    if self.daemon is not None:
                        try:
                            self.daemon._clip_listener.set_recording_escape_guard(False)
                        except Exception:
                            pass
                        self.daemon._set_status("Ready", speaking=False)
                    cb = self._on_complete_cb
                    self._on_complete_cb = None
                    if cb is not None:
                        try:
                            cb(ok, out_msg)
                        except Exception:
                            pass
                    self._resume_media_if_standalone()

        threading.Thread(
            target=_worker, daemon=True, name="bark-transcribe"
        ).start()

    def transcribe_file(
        self, wav_path: str, cancel_seq: int | None = None
    ) -> tuple[bool, str]:
        """Transcribe *wav_path* using Tier 2 (Groq whisper-large-v3) first, falling back to Tier 3 (Bark 3.0 Clean) only as a last resort."""
        if cancel_seq is not None and self._cancel_seq != cancel_seq:
            return False, "Dictation cancelled."

        vocab: dict[str, str] = {}
        groq_model = "whisper-large-v3"
        if self.daemon is not None:
            with self.daemon._config_lock:
                vocab = dict(self.daemon.config.get("stt_vocabulary", {}))
                groq_model = (
                    str(self.daemon.config.get("groq_model", "whisper-large-v3")).strip()
                    or "whisper-large-v3"
                )

        # Tier 2: Groq whisper-large-v3 cloud STT (0 MB local model RAM, highest accuracy)
        groq_key = resolve_groq_api_key(self.daemon)
        if groq_key:
            self.hud.clear_status()
            if self.daemon is not None and self._transcribing:
                self.daemon._set_status("Transcribing (Groq v3)...", speaking=False)
            ok_groq, groq_res = transcribe_with_groq(
                wav_path, groq_key, model=groq_model, vocab=vocab
            )
            if cancel_seq is not None and self._cancel_seq != cancel_seq:
                return False, "Dictation cancelled."
            if ok_groq:
                raw_text = groq_res.strip()
                if raw_text and vocab:
                    raw_text = apply_stt_vocabulary(raw_text, vocab)
                return True, raw_text
            print(
                f"[WARN] Groq whisper-large-v3 failed ({groq_res}); falling back to Bark 3.0 Clean last resort..."
            )
            self._report_setup_status("Groq unavailable — loading Bark 3.0 fallback...")

        # Tier 3 (Last Resort): Load Bark 3.0 Clean on demand, transcribe, and unload immediately after
        try:
            if not self._ensure_worker_started(
                allow_bootstrap=True, cancel_seq=cancel_seq
            ):
                if cancel_seq is not None and self._cancel_seq != cancel_seq:
                    return False, "Dictation cancelled."
                err = self._proc_error or "Could not start Bark 3.0 Clean Python runtime."
                if self.daemon is not None:
                    self.daemon._notify_error(err)
                return False, err

            if cancel_seq is not None and self._cancel_seq != cancel_seq:
                return False, "Dictation cancelled."

            self.hud.clear_status()
            if self.daemon is not None and self._transcribing:
                self.daemon._set_status("Transcribing (Bark 3.0)...", speaking=False)

            with self._lock:
                if cancel_seq is not None and self._cancel_seq != cancel_seq:
                    return False, "Dictation cancelled."
                proc = self._proc
                if proc is None or proc.poll() is not None or proc.stdin is None:
                    return False, "Bark 3.0 Clean worker terminated unexpectedly."

                self._active_req_id += 1
                req_id = self._active_req_id
                self._result_event.clear()
                self._last_result = None
                payload = (
                    json.dumps(
                        {"cmd": "transcribe", "wav_path": wav_path, "req_id": req_id}
                    )
                    + "\n"
                )
                proc.stdin.write(payload)
                proc.stdin.flush()

            if not self._result_event.wait(timeout=120.0):
                return False, "Bark 3.0 Clean transcription timed out."

            if cancel_seq is not None and self._cancel_seq != cancel_seq:
                return False, "Dictation cancelled."

            res = self._last_result or {}
            if res.get("cancelled"):
                return False, "Dictation cancelled."
            if res.get("ok"):
                raw_text = str(res.get("text", "")).strip()
                if raw_text and vocab:
                    raw_text = apply_stt_vocabulary(raw_text, vocab)
                return True, raw_text
            err_msg = str(res.get("error", "Unknown transcription error"))
            if self.daemon is not None:
                self.daemon._notify_error(f"Bark 3.0 Clean error: {err_msg}")
            return False, err_msg
        finally:
            # Always unload Bark 3.0 Clean immediately after last-resort fallback so ~500 MB RAM never stays resident
            self.shutdown()

    def _copy_and_paste(self, text: str) -> None:
        """Copy *text* to clipboard without triggering TTS and paste via Ctrl+V."""
        self.last_dictated = text
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
            self.learner.record_dictation(text)
            if self.daemon is not None:
                self.daemon._notify_state_change()
                preview = text if len(text) <= 70 else text[:67] + "..."
                print(f"[STT] Dictated ({len(text)} chars): {preview}")

    def paste_last_dictation(self) -> bool:
        """Re-paste the most recently dictated text into the focused input field."""
        if not self.is_enabled:
            return False
        now = time.monotonic()
        if (now - self._last_paste_time) < 0.25:
            return False
        self._last_paste_time = now

        text = self.last_dictated
        if not text:
            return False

        threading.Thread(
            target=self._copy_and_paste,
            args=(text,),
            daemon=True,
            name="bark-paste-last",
        ).start()
        return True

    def _report_setup_status(self, msg: str) -> None:
        """Update both the HUD pill status text (when transcribing) and daemon status."""
        if not msg:
            return
        if self._transcribing or self.recorder.is_recording:
            self.hud.set_status(msg)
        if self.daemon is not None and not self.daemon.is_speaking:
            self.daemon._set_status(msg, speaking=False)

    def _ensure_worker_started(
        self, allow_bootstrap: bool = True, cancel_seq: int | None = None
    ) -> bool:
        """Spawn the Bark 3.0 Clean sidecar subprocess if not already running.

        Uses ``self._spawn_lock`` rather than ``self._lock`` so first-time runtime
        bootstrap or model downloads never block ``stop_and_transcribe`` or ``cancel_dictation``.
        """
        if not self.is_enabled:
            self._proc_error = "Speech-to-text is disabled."
            return False

        with self._spawn_lock:
            if not self.is_enabled or (
                cancel_seq is not None and self._cancel_seq != cancel_seq
            ):
                return False

            if self._proc is None or self._proc.poll() is not None:
                py_exe = find_bark_python()
                if not py_exe and allow_bootstrap:
                    py_exe = bootstrap_bark_python(status_cb=self._report_setup_status)
                if not py_exe:
                    self._proc_error = (
                        "Could not initialize Bark 3.0 Clean Python runtime."
                    )
                    return False

                if not self.is_enabled or (
                    cancel_seq is not None and self._cancel_seq != cancel_seq
                ):
                    return False

                bark_dir = get_bark_dir()
                worker_script = os.path.join(bark_dir, "bark_worker.py")
                with open(worker_script, "w", encoding="utf-8") as f:
                    f.write(_WORKER_SCRIPT)

                self._proc_ready.clear()
                self._proc_error = None
                proc = subprocess.Popen(
                    [py_exe, "-u", worker_script, bark_dir],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    creationflags=CREATE_NO_WINDOW,
                )
                with self._lock:
                    if not self.is_enabled or (
                        cancel_seq is not None and self._cancel_seq != cancel_seq
                    ):
                        try:
                            proc.kill()
                        except Exception:
                            pass
                        return False
                    self._proc = proc
                threading.Thread(
                    target=self._stdout_reader,
                    args=(proc,),
                    daemon=True,
                    name="bark-worker-stdout",
                ).start()
                threading.Thread(
                    target=self._stderr_reader,
                    args=(proc,),
                    daemon=True,
                    name="bark-worker-stderr",
                ).start()

        # Poll in 0.15s intervals so pressing Escape cancels immediately even while downloading/loading
        deadline = time.monotonic() + 600.0
        while not self._proc_ready.wait(timeout=0.15):
            if not self.is_enabled:
                self.shutdown()
                return False
            if cancel_seq is not None and self._cancel_seq != cancel_seq:
                return False
            if time.monotonic() >= deadline:
                self._proc_error = "Timed out initializing Bark 3.0 Clean."
                self.shutdown()
                return False

        if not self.is_enabled:
            self.shutdown()
            return False

        self.hud.clear_status()
        self._reset_idle_timer()
        return self._proc_error is None

    def _stderr_reader(self, proc: subprocess.Popen) -> None:
        """Capture stderr from the sidecar worker so crashes report a clear error message."""
        if proc.stderr is None:
            return
        lines: list[str] = []
        try:
            for line in proc.stderr:
                s = line.strip()
                if s:
                    lines.append(s)
                    if len(lines) > 15:
                        lines.pop(0)
        except Exception:
            pass
        if self._proc is proc and not self._proc_ready.is_set() and lines:
            self._proc_error = f"Bark 3.0 worker error: {lines[-1]}"
            self._proc_ready.set()

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
                if status_str:
                    self._report_setup_status(status_str)
            elif mtype == "ready":
                self.hud.clear_status()
                self._proc_ready.set()
            elif mtype == "error":
                self.hud.clear_status()
                self._proc_error = str(msg.get("error", "Worker error"))
                self._proc_ready.set()
            elif mtype == "result":
                with self._lock:
                    msg_req_id = msg.get("req_id")
                    if msg_req_id is None or msg_req_id == self._active_req_id:
                        self._last_result = msg
                        self._result_event.set()

        if self._proc is not proc:
            return
        self.hud.clear_status()
        # If process exited before signaling ready, unblock waiters
        if not self._proc_ready.is_set():
            if not self._proc_error:
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
        """Stop the Bark 3.0 Clean worker process and release its RAM immediately."""
        if self._startup_timer is not None:
            self._startup_timer.cancel()
            self._startup_timer = None
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
                proc.wait(timeout=0.8)
            except Exception:
                try:
                    proc.kill()
                    proc.wait(timeout=1.0)
                except Exception:
                    pass
        try:
            gc.collect()
            k32 = ctypes.windll.kernel32
            k32.SetProcessWorkingSetSize.argtypes = [
                wintypes.HANDLE,
                ctypes.c_size_t,
                ctypes.c_size_t,
            ]
            k32.SetProcessWorkingSetSize(
                k32.GetCurrentProcess(),
                ctypes.c_size_t(-1),
                ctypes.c_size_t(-1),
            )
        except Exception:
            pass
