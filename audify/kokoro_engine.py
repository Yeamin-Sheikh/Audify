"""
Offline Kokoro TTS for Audify (kokoro-onnx on the CPU).

Kept light on purpose:
  * nothing is imported or loaded until a Kokoro voice is actually used,
  * the model is dropped again after a few idle minutes (~250-400MB freed),
  * inference is limited to the physical cores so the PC stays responsive.

Model files are looked up next to the executable ("models" folder, shipped by
the installer) and otherwise downloaded once to %LOCALAPPDATA%\\Audify\\models.
"""
from __future__ import annotations

import gc
import os
import re
import sys
import threading
import urllib.request
from typing import Any, Callable

MODEL_FILE = "kokoro-v1.0.fp16.onnx"
VOICES_FILE = "voices-v1.0.bin"
DOWNLOAD_BASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
EXPECTED_SIZES = {MODEL_FILE: 177_464_787, VOICES_FILE: 28_214_398}

SAMPLE_RATE = 24000
IDLE_UNLOAD_SECONDS = 300
MIN_SPEED, MAX_SPEED = 0.5, 2.0  # limits of the Kokoro model


def _app_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _model_dirs() -> list[str]:
    dirs = [os.path.join(_app_dir(), "models")]
    local = os.environ.get("LOCALAPPDATA")
    if local:
        dirs.append(os.path.join(local, "Audify", "models"))
    return dirs


def _complete(folder: str) -> bool:
    return all(
        os.path.isfile(os.path.join(folder, name)) and os.path.getsize(os.path.join(folder, name)) == size
        for name, size in EXPECTED_SIZES.items()
    )


def find_models() -> str | None:
    return next((d for d in _model_dirs() if _complete(d)), None)


def download_models(on_progress: Callable[[str], None] | None = None) -> str:
    """Download the model files once into the per-user folder. Returns that folder."""
    target = _model_dirs()[-1]
    os.makedirs(target, exist_ok=True)
    for name, size in EXPECTED_SIZES.items():
        path = os.path.join(target, name)
        if os.path.isfile(path) and os.path.getsize(path) == size:
            continue
        if on_progress:
            on_progress(f"Downloading offline voice model ({name}, {size // 2**20} MB)...")
        part = path + ".part"
        with urllib.request.urlopen(DOWNLOAD_BASE + name, timeout=30) as resp, open(part, "wb") as f:
            while True:
                block = resp.read(1 << 20)
                if not block:
                    break
                f.write(block)
        if os.path.getsize(part) != size:
            os.remove(part)
            raise IOError(f"Download of {name} was incomplete")
        os.replace(part, path)
    return target


def _ends_with(word: str, marks: str) -> bool:
    return word.rstrip("\"')]}”’")[-1:] in tuple(marks)


def split_for_streaming(
    text: str, first_words: int = 5, growth: float = 1.35, max_words: int = 40
) -> list[str]:
    """Split text into chunks that start tiny and grow, for gap-free offline streaming.

    On a modest CPU Kokoro generates ~1.5x faster than real time, so each chunk
    may only be ~1.4x longer than the one playing before it or playback would
    catch up and stall. A tiny first chunk makes audio start quickly. Breaks
    prefer sentence ends, then clause ends, then fall back to word boundaries.
    """
    chunks: list[str] = []
    limit = first_words
    for paragraph in re.split(r"\n\s*\n", text):
        words = paragraph.split()
        while words:
            take = len(words) if len(words) <= limit else 0
            if not take:
                earliest = max(1, int(limit * 0.4))
                # Sentence ends may overshoot the limit by a few words, clause ends by one
                for marks, reach in ((".!?", 3), (",;:", 1)):
                    for j in range(min(len(words), limit + reach) - 1, earliest - 1, -1):
                        if _ends_with(words[j], marks):
                            take = j + 1
                            break
                    if take:
                        break
                take = take or limit
            chunks.append(" ".join(words[:take]))
            words = words[take:]
            limit = min(max_words, int(limit * growth) + 1)
    return chunks or [text]


def voice_language(voice: str) -> str:
    """Kokoro voice ids start with a/b for American/British English."""
    return "en-gb" if voice.startswith("b") else "en-us"


def clamp_speed(speed: float) -> float:
    return max(MIN_SPEED, min(MAX_SPEED, speed))


class _CancellableSession:
    """Wraps the ONNX session so a running synthesis can be aborted mid-way.

    kokoro-onnx calls ``session.run(None, inputs)``; we add RunOptions whose
    ``terminate`` flag makes ONNX Runtime stop within milliseconds.
    """

    def __init__(self, session: Any) -> None:
        self._session = session
        self.run_options: Any = None

    def run(self, output_names: Any, inputs: Any, run_options: Any = None) -> Any:
        return self._session.run(output_names, inputs, run_options or self.run_options)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._session, name)


class KokoroEngine:
    """Lazily loaded, idle-unloaded Kokoro model shared by all speech sessions."""

    def __init__(self, notify: Callable[[str], None] | None = None) -> None:
        self._notify = notify
        self._kokoro: Any = None
        self._lock = threading.Lock()        # guards loading/unloading
        self._infer_lock = threading.Lock()  # one synthesis at a time
        self._idle_timer: threading.Timer | None = None
        self._session: _CancellableSession | None = None
        self._current_owner: object | None = None

    @property
    def loaded(self) -> bool:
        return self._kokoro is not None

    def preload_async(self) -> None:
        """Warm the model in the background (e.g. right after a Kokoro voice is picked)."""
        threading.Thread(target=self._safe_preload, daemon=True, name="kokoro-preload").start()

    def _safe_preload(self) -> None:
        try:
            self.ensure_loaded()
            self._schedule_unload()
        except Exception as e:
            print(f"[WARN] Kokoro preload failed: {e}")

    def ensure_loaded(self) -> Any:
        with self._lock:
            if self._kokoro is not None:
                return self._kokoro
            folder = find_models() or download_models(self._notify)

            # Heavy imports happen only here, so Edge-only use never pays for them
            import onnxruntime as rt
            from kokoro_onnx import Kokoro

            options = rt.SessionOptions()
            options.intra_op_num_threads = max(1, (os.cpu_count() or 2) // 2)  # physical cores
            options.inter_op_num_threads = 1
            session = rt.InferenceSession(
                os.path.join(folder, MODEL_FILE), options, providers=["CPUExecutionProvider"]
            )
            kokoro = Kokoro.from_session(session, os.path.join(folder, VOICES_FILE))
            self._session = _CancellableSession(session)
            kokoro.sess = self._session
            kokoro.create("Ready.", voice="af_heart", speed=1.0, lang="en-us")  # warm-up run
            self._kokoro = kokoro
            return kokoro

    def synthesize(self, text: str, voice: str, speed: float, owner: object | None = None) -> Any:
        """Return float32 mono samples at 24kHz for ``text``.

        ``owner`` identifies the caller so ``cancel(owner)`` aborts only its own run.
        """
        import onnxruntime as rt

        self._cancel_unload()
        try:
            # Loading inside the inference lock means unload() can never pull the
            # model away between loading it and using it
            with self._infer_lock:
                kokoro = self.ensure_loaded()
                session = self._session
                session.run_options = rt.RunOptions()
                self._current_owner = owner
                try:
                    samples, _sr = kokoro.create(
                        text, voice=voice, speed=clamp_speed(speed), lang=voice_language(voice)
                    )
                finally:
                    self._current_owner = None
                    session.run_options = None
            return samples
        finally:
            self._schedule_unload()

    def cancel(self, owner: object) -> None:
        """Abort the synthesis currently running for ``owner`` (if any)."""
        session = self._session
        options = session.run_options if session else None
        if options is not None and self._current_owner is owner:
            options.terminate = True

    def unload(self) -> None:
        with self._lock:
            if self._infer_lock.locked():
                self._schedule_unload()  # busy: try again later
                return
            self._kokoro = None
            self._session = None  # the wrapper holds the ONNX session too
        gc.collect()

    def _cancel_unload(self) -> None:
        if self._idle_timer:
            self._idle_timer.cancel()
            self._idle_timer = None

    def _schedule_unload(self) -> None:
        self._cancel_unload()
        self._idle_timer = threading.Timer(IDLE_UNLOAD_SECONDS, self.unload)
        self._idle_timer.daemon = True
        self._idle_timer.start()
