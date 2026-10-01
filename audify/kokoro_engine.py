"""
Offline Kokoro TTS for Audify (kokoro-onnx on the CPU).

Kept light on purpose:
  * nothing is imported or loaded until a Kokoro voice is actually used,
  * the model is dropped again after a minute of idling (~300MB freed),
  * inference uses 3 threads (about a third of a 4-core CPU while speaking).
    Below-normal priority was tried and dropped: ordinary background load
    starved it, delaying first audio by seconds.

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

# numpy's math library would otherwise start one idle thread per CPU on import
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("OMP_NUM_THREADS", "1")

MODEL_FILE = "kokoro-v1.0.fp16.onnx"
VOICES_FILE = "voices-v1.0.bin"
DOWNLOAD_BASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
EXPECTED_SIZES = {MODEL_FILE: 177_464_787, VOICES_FILE: 28_214_398}

SAMPLE_RATE = 24000
IDLE_UNLOAD_SECONDS = 60
INFERENCE_THREADS = 3  # measured on a 4-core laptop: steadiest (>=1.05x real time at 2x) at ~35% CPU;
                       # 2 threads dipped below real time (gaps), 4 were no faster
# Kokoro always speaks at its natural 1x pace (its own fast speech blurs
# words); the chosen speed is then applied by a pitch-preserving time-stretch.
MIN_SPEED = 0.5
MAX_SPEED = 3.0


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
    text: str, first_words: int = 5, growth: float = 1.1, max_words: int = 40
) -> list[str]:
    """Split text into chunks that start tiny and grow, for gap-free offline streaming.

    On a modest (or busy) 4-core laptop CPU, offline speech at 1.5x is only a
    little faster to generate than to play, so each chunk may only be ~1.1x
    longer than the one playing before it or playback would
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


def time_stretch(samples: Any, factor: float, sample_rate: int = SAMPLE_RATE) -> Any:
    """Speed speech up (``factor`` > 1) or slow it down without changing pitch (WSOLA).

    Frames of 30ms are overlap-added at a fixed output hop while the input hop
    is ``factor`` times larger; each frame's exact start is nudged (+-5ms) to
    the point that best continues the previous frame, which avoids the phasey
    artefacts of naive overlap-add.
    """
    import numpy as np

    if abs(factor - 1.0) < 0.001 or len(samples) < sample_rate // 10:
        return samples
    x = np.asarray(samples, dtype=np.float32)
    frame = int(sample_rate * 0.030)
    hop_out = frame // 2
    hop_in = hop_out * factor
    tolerance = int(sample_rate * 0.005)
    window = np.hanning(frame).astype(np.float32)

    x = np.concatenate([np.zeros(tolerance, np.float32), x, np.zeros(frame + tolerance, np.float32)])
    n_frames = int((len(x) - frame - 2 * tolerance) / hop_in)
    out = np.zeros(n_frames * hop_out + frame, np.float32)
    norm = np.zeros_like(out)

    previous = tolerance  # input position of the last frame used
    for k in range(n_frames):
        ideal = int(k * hop_in) + tolerance
        if k == 0:
            start = ideal
        else:
            # Natural continuation of the previous frame, matched within +-tolerance
            target = x[previous + hop_out: previous + hop_out + frame]
            region = x[ideal - tolerance: ideal + tolerance + frame]
            if len(target) < frame or len(region) < frame:
                break
            scores = np.correlate(region, target, mode="valid")
            start = ideal - tolerance + int(np.argmax(scores))
        segment = x[start: start + frame]
        if len(segment) < frame:
            break
        position = k * hop_out
        out[position: position + frame] += segment * window
        norm[position: position + frame] += window
        previous = start
    norm[norm < 1e-3] = 1.0
    return out / norm


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
            options.intra_op_num_threads = min(INFERENCE_THREADS, os.cpu_count() or 1)
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
        """Return float32 mono samples at 24kHz for ``text``, spoken at ``speed``.

        The speech is generated at 1x and then time-stretched to ``speed``,
        which keeps words clearer than Kokoro's own fast or slow speech.

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
                    samples, _sr = kokoro.create(text, voice=voice, speed=1.0, lang=voice_language(voice))
                finally:
                    self._current_owner = None
                    session.run_options = None
            return time_stretch(samples, clamp_speed(speed))
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
