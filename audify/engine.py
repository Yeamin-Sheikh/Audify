"""
TTS Daemon engine for Audify.
"""
from __future__ import annotations

import asyncio
import ctypes
import os
import queue
import re
import threading
import time
from collections import deque
from typing import Callable

os.environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "hide"
import pygame
import keyboard
import pynput.mouse as pynput_mouse

from audify.clipboard import ClipboardListener
from audify.kokoro_engine import KokoroEngine, find_models, download_models, split_for_streaming
from audify.playback import KOKORO_PREFIX, SpeechSession
from audify.config import (
    CONFIG_FILE,
    HISTORY_FILE,
    is_just_url,
    load_config,
    save_config,
)
from clean_text import markdown_to_text

# DPI awareness for Windows
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    pass

# Edge TTS delivers 24kHz mono audio; matching the mixer avoids any resampling
pygame.mixer.pre_init(frequency=24000, size=-16, channels=1, buffer=512)
pygame.mixer.init()

class TTSDaemon:
    """Background daemon that converts queued text to speech via Edge TTS."""

    def __init__(self) -> None:
        self.q: queue.Queue[str | None] = queue.Queue()
        self.last_spoken: str = ""
        self.config: dict = load_config()
        self.is_paused: bool = False
        self.current_code_blocks: list[str] = []

        # Thread-safe config lock
        self._config_lock = threading.Lock()
        self._save_timer: threading.Timer | None = None

        # Wispr Flow heuristic -- tracks when the user last pressed Ctrl+C/X
        self.last_ctrl_c_time: float = 0.0

        # Persistent event loop for TTS
        self._loop = asyncio.new_event_loop()
        threading.Thread(target=self._start_loop, daemon=True).start()

        # Event-driven clipboard listener
        self._clip_listener = ClipboardListener(self._on_clipboard_change)

        # Offline voices: loaded on first use (or warmed now if one is selected)
        self.kokoro = KokoroEngine(notify=self._notify_info)
        self._model_download_lock = threading.Lock()
        if self.config.get("voice", "").startswith(KOKORO_PREFIX) and find_models():
            self.kokoro.preload_async()
        
        # Tray icon (injected later)
        self.tray_icon = None

        # Playback status shown by the tray; the tray registers on_state_change to redraw
        self.status: str = "Ready"
        self.is_speaking: bool = False
        self.on_state_change: Callable[[], None] | None = None

        # Bumped by the stop hotkey; a session aborts when this no longer matches its start value
        self._stop_generation: int = 0

        # Clear stale history from previous session
        if os.path.exists(HISTORY_FILE):
            try:
                os.remove(HISTORY_FILE)
            except Exception:
                pass

        # Apply saved volume on startup
        pygame.mixer.set_num_channels(4)
        self._tts_channel = pygame.mixer.Channel(0)
        self._tts_channel.set_volume(self.config.get("volume", 1.0))

        # Bind global hotkeys
        try:
            keyboard.add_hotkey(
                self.config.get("stop_hotkey", "ctrl+alt+s"), self.stop_audio
            )
            # Hooks for Ctrl+C / Ctrl+X to detect manual copying
            keyboard.add_hotkey("ctrl+c", self.mark_manual_copy)
            keyboard.add_hotkey("ctrl+x", self.mark_manual_copy)

            # Mouse hook to detect Right-Click -> Copy
            self.mouse_listener = pynput_mouse.Listener(on_click=self.on_mouse_click)
            self.mouse_listener.start()
        except Exception as e:
            print(f"[WARN] Failed to bind hotkey: {e}")

    def _start_loop(self):
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    # -- Hotkey / mouse callbacks ------------------------------------------

    def on_mouse_click(
        self, x: int, y: int, button: pynput_mouse.Button, pressed: bool
    ) -> None:
        """Record timestamp on any mouse click (for Wispr Flow heuristic)."""
        if pressed:
            self.last_ctrl_c_time = time.time()

    def mark_manual_copy(self) -> None:
        """Record that the user manually pressed Ctrl+C / Ctrl+X."""
        self.last_ctrl_c_time = time.time()

    # -- Controls ----------------------------------------------------------
    
    def _do_save(self) -> None:
        with self._config_lock:
            save_config(self.config)

    def _debounced_save(self) -> None:
        if self._save_timer:
            self._save_timer.cancel()
        self._save_timer = threading.Timer(0.5, self._do_save)
        self._save_timer.start()

    def set_voice(self, voice_id: str) -> None:
        """Change the active TTS voice and persist to config."""
        with self._config_lock:
            self.config["voice"] = voice_id
            save_config(self.config)
        if voice_id.startswith(KOKORO_PREFIX):
            if find_models():
                self.kokoro.preload_async()  # warm it so the first read is quick
            else:
                self._download_models_async()
        self._notify_state_change()

    def set_rate(self, rate_str: str) -> None:
        """Change the speech rate and persist to config. Changes apply on next chunk playback."""
        with self._config_lock:
            self.config["rate"] = rate_str
            save_config(self.config)
        self._notify_state_change()

    def set_volume(self, vol: float) -> None:
        """Change immediate mixer volume (0.0 to 1.0) and save to config."""
        with self._config_lock:
            self.config["volume"] = vol
        self._tts_channel.set_volume(vol)
        self._debounced_save()
        self._notify_state_change()

    def toggle_skip_code_blocks(self) -> None:
        """Toggle skipping of fenced code blocks and save to config."""
        with self._config_lock:
            current = self.config.get("skip_code_blocks", True)
            self.config["skip_code_blocks"] = not current
            save_config(self.config)
        self._notify_state_change()

    # -- Playback controls -------------------------------------------------

    def stop_audio(self) -> None:
        """Stop reading entirely (global kill-switch callback).

        Bumping the generation makes the worker abandon the rest of the text;
        stopping the channel alone would only cut the current chunk.
        """
        self._stop_generation += 1
        self._halt_channel()

    def _halt_channel(self) -> None:
        try:
            self._tts_channel.stop()
        except Exception:
            pass

    def pause_audio(self) -> None:
        """Pause any currently-playing audio."""
        try:
            self._tts_channel.pause()
        except Exception:
            pass

    def resume_audio(self) -> None:
        """Resume any currently-paused audio."""
        try:
            self._tts_channel.unpause()
        except Exception:
            pass

    def check_pause_and_wait(self, stop_generation: int | None = None) -> bool:
        """
        Check if paused. If paused, sleep in a loop until unpaused or new text arrives.
        Returns True if we should abort (new text arrived, or stop was pressed).
        """
        if not self.is_paused:
            return False

        # Pause playback if it's currently busy
        try:
            if self._tts_channel.get_busy():
                self._tts_channel.pause()
        except Exception:
            pass

        while self.is_paused:
            stopped = stop_generation is not None and stop_generation != self._stop_generation
            if stopped or not self.q.empty():
                # New text in queue, we must abort current playback
                try:
                    self._tts_channel.stop()
                except Exception:
                    pass
                return True
            time.sleep(0.05)

        # Unpaused, resume playback if it was busy
        try:
            self._tts_channel.unpause()
        except Exception:
            pass

        return False

    @staticmethod
    def _safe_remove(path: str | None) -> None:
        """Silently try to delete a file -- never raises."""
        if path:
            try:
                os.remove(path)
            except Exception:
                pass

    # -- Tray Updates ------------------------------------------------------
    
    def _set_status(self, status: str, speaking: bool = False) -> None:
        """Record the playback status and let the tray redraw its icon, tooltip and menu."""
        self.status = status
        self.is_speaking = speaking
        self._notify_state_change()

    def _notify_state_change(self) -> None:
        if self.on_state_change:
            try:
                self.on_state_change()
            except Exception as e:
                print(f"[WARN] Tray refresh failed: {e}")

    # -- History -----------------------------------------------------------

    def log_history(self, text: str) -> None:
        """Append spoken text to the session history log."""
        try:
            with open(HISTORY_FILE, "a", encoding="utf-8") as f:
                f.write(text + "\n\n---\n\n")
        except Exception:
            pass

    # -- TTS generation ----------------------------------------------------

    def _split_text(self, text: str, first_chunk_max: int = 100) -> list[str]:
        """
        Split text into natural paragraph and sentence-based chunks.

        The FIRST chunk targets ~100 characters (about one sentence) so the
        first request finishes quickly and later chunks are fetched sooner.
        All subsequent chunks use the normal ~800 character limit for
        efficient batch synthesis while the first chunk plays.
        """
        chunk_limit = first_chunk_max  # starts small, switches to 800 after first chunk

        # Split by paragraphs first
        paragraphs = text.split("\n\n")
        chunks: list[str] = []
        current_chunk: list[str] = []
        current_len = 0

        for para in paragraphs:
            para = para.strip()
            if not para:
                continue
                
            # If paragraph itself fits in the remaining space of current chunk, add it whole
            if current_len + len(para) <= chunk_limit:
                current_chunk.append(para)
                current_len += len(para) + 2  # +2 for double newline
                continue
                
            # Otherwise, split paragraph into sentences
            sentences = re.split(r"(?<=[.!?])\s+", para)
            for sentence in sentences:
                sentence = sentence.strip()
                if not sentence:
                    continue
                    
                # If a single sentence is exceptionally long (>1000 chars), split by clauses (commas, semicolons)
                if len(sentence) > 1000:
                    clauses = re.split(r"(?<=[,;])\s+", sentence)
                    for clause in clauses:
                        clause = clause.strip()
                        if not clause:
                            continue
                        if current_len + len(clause) > chunk_limit:
                            if current_chunk:
                                chunks.append("\n\n".join(current_chunk) if "\n\n" in text else " ".join(current_chunk))
                                chunk_limit = 800  # switch to normal size after first chunk
                            current_chunk = [clause]
                            current_len = len(clause)
                        else:
                            current_chunk.append(clause)
                            current_len += len(clause) + 1
                else:
                    # Normal sentence: if it exceeds target size, push current chunk and start a new one
                    if current_len + len(sentence) > chunk_limit:
                        if current_chunk:
                            chunks.append("\n\n".join(current_chunk) if "\n\n" in text else " ".join(current_chunk))
                            chunk_limit = 800  # switch to normal size after first chunk
                        current_chunk = [sentence]
                        current_len = len(sentence)
                    else:
                        current_chunk.append(sentence)
                        current_len += len(sentence) + 1

        if current_chunk:
            chunks.append("\n\n".join(current_chunk) if "\n\n" in text else " ".join(current_chunk))

        return chunks if chunks else [text]

    def _resolve_code_blocks(self, chunk_text: str) -> str:
        """Swap code-block placeholders back in, honouring the live skip setting."""
        with self._config_lock:
            skip_code = self.config.get("skip_code_blocks", True)

        resolved_text = chunk_text
        for idx, code_content in enumerate(self.current_code_blocks):
            placeholder = f"[[CODE_BLOCK_{idx}]]"
            if code_content.startswith("FENCED:"):
                replacement = " [Skipped code block] " if skip_code else f" {code_content[7:]} "
            else:
                replacement = f" {code_content[7:]} "
            resolved_text = resolved_text.replace(placeholder, replacement)
        return resolved_text

    def _notify_info(self, message: str) -> None:
        print(f"[INFO] {message}")
        if self.tray_icon:
            try:
                self.tray_icon.notify(message, "Audify")
            except Exception:
                pass

    def _download_models_async(self) -> None:
        """Fetch the offline voice model once, in the background."""
        if not self._model_download_lock.acquire(blocking=False):
            return  # already downloading

        def run() -> None:
            try:
                self._notify_info("Downloading offline voices (about 200 MB). Using an online voice until it's done.")
                download_models()
                self._notify_info("Offline voices are ready.")
                self.kokoro.preload_async()
            except Exception as e:
                self._notify_error(f"Could not download offline voices: {e}")
            finally:
                self._model_download_lock.release()

        threading.Thread(target=run, daemon=True, name="kokoro-download").start()

    def _notify_error(self, message: str) -> None:
        print(f"[ERROR] {message}")
        if self.tray_icon:
            try:
                self.tray_icon.notify(message, "Audify Error")
            except Exception:
                pass

    # -- Worker thread -----------------------------------------------------

    def worker_loop(self) -> None:
        """Main TTS worker: cleans each copied text and plays it through a streaming session."""
        while True:
            text: str | None = self.q.get()

            # Drain queue -- only the latest clipboard text matters
            while not self.q.empty():
                text = self.q.get()

            if text is None:  # Exit signal
                break

            stop_generation = self._stop_generation

            if self.is_paused:
                # Wait until unpaused or a new text is queued
                if self.check_pause_and_wait(stop_generation):
                    continue

            # Clean the raw text (strip markdown, apply pronunciation dict)
            try:
                with self._config_lock:
                    dict_rules = dict(self.config.get("pronunciation_dict", {}))
                cleaned, code_blocks = markdown_to_text(
                    text,
                    pronunciation_dict=dict_rules,
                )
                self.current_code_blocks = code_blocks
            except Exception as e:
                print(f"[WARN] Text cleaning failed: {e}")
                continue

            if not cleaned.strip():
                continue

            # Skip if we already just spoke this exact text
            if cleaned == self.last_spoken:
                continue
            self.last_spoken = cleaned

            self._halt_channel()
            self.log_history(cleaned)

            try:
                self._speak(cleaned, stop_generation)
            except Exception as e:
                print(f"[ERROR] Playback failed: {e}")
                self._halt_channel()
            self._set_status("Ready")

    def _speak(self, text: str, stop_generation: int) -> None:
        """Play one text: start a streaming session and feed its audio to the channel gaplessly."""
        with self._config_lock:
            voice = self.config.get("voice", "en-US-JennyNeural")
            rate = self.config.get("rate", "+50%")
        frequency, _size, channels = pygame.mixer.get_init()

        offline = voice.startswith(KOKORO_PREFIX)
        if offline and not self.kokoro.loaded and not find_models():
            # Model not downloaded yet: fetch it in the background, read this one online
            self._download_models_async()
            voice, offline = "en-US-JennyNeural", False
        chunks = split_for_streaming(text) if offline else self._split_text(text)

        session = SpeechSession(
            chunks, voice, rate, self._loop, (frequency, channels),
            resolve_text=self._resolve_code_blocks,
            on_error=self._notify_error,
            kokoro=self.kokoro,
        )
        session.start()

        channel = self._tts_channel
        ready: deque[list] = deque()  # [chunk_index, bytearray] not yet handed to the channel
        playing: list[tuple[pygame.mixer.Sound, int]] = []  # sounds given to the channel, in order
        shown_chunk = -1
        decoded_all = False
        start_cushion = int(frequency * channels * 2 * 0.1)  # one decoded block (0.1s) of 16-bit audio

        def aborted() -> bool:
            return not self.q.empty() or stop_generation != self._stop_generation

        try:
            while True:
                if aborted():
                    return
                if self.is_paused and self.check_pause_and_wait(stop_generation):
                    return

                # Collect decoded audio; merge consecutive blocks of the same chunk
                try:
                    item = session.pcm.get(timeout=0.02)
                except queue.Empty:
                    item = False
                if item is None:
                    decoded_all = True
                elif item:
                    index, data = item
                    if ready and ready[-1][0] == index:
                        ready[-1][1] += data
                    else:
                        ready.append([index, bytearray(data)])

                # Keep the channel's single queue slot filled for gapless playback.
                # Starting from silence, wait for a small cushion so a slow network start doesn't stutter.
                idle = not channel.get_busy()
                cushioned = decoded_all or len(ready) > 1 or (ready and len(ready[0][1]) >= start_cushion)
                if ready and ((idle and cushioned) or (not idle and channel.get_queue() is None)):
                    index, data = ready.popleft()
                    sound = pygame.mixer.Sound(buffer=bytes(data))
                    if channel.get_busy():
                        channel.queue(sound)
                    else:
                        channel.play(sound)
                    playing.append((sound, index))

                # Status follows what is audible, not what has been downloaded
                current = channel.get_sound()
                for sound, index in playing:
                    if sound is current and index != shown_chunk:
                        shown_chunk = index
                        total = len(chunks)
                        self._set_status("Reading" if total == 1 else f"Reading {index + 1} of {total}", speaking=True)
                        break
                if len(playing) > 8:
                    del playing[:-4]

                if decoded_all and not ready and not channel.get_busy():
                    return
        finally:
            session.cancel()
            if aborted():
                self._halt_channel()

    # -- Clipboard monitor thread ------------------------------------------

    def start_clipboard_monitor(self) -> None:
        self._clip_listener.start()

    def _on_clipboard_change(self, text: str) -> None:
        if self.is_paused or not text.strip():
            return
        if is_just_url(text):
            return

        # Fast path: check immediately (0ms latency). The keyboard hook
        # fires within ~1-5ms of keypress, so this succeeds most of the time.
        time_since_copy = time.time() - self.last_ctrl_c_time
        if time_since_copy <= 1.5:
            self.q.put(text)
            return

        # Slow path: keyboard hook might not have fired yet (rare race).
        # Wait 30ms and retry once.
        def _retry():
            time_since = time.time() - self.last_ctrl_c_time
            if time_since <= 1.55:
                self.q.put(text)

        threading.Timer(0.03, _retry).start()

