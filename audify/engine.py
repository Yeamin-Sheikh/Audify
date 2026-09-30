"""
TTS Daemon engine for Audify.
"""
from __future__ import annotations

import asyncio
import ctypes
import os
import queue
import re
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor

os.environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "hide"
import pygame
import keyboard
import pynput.mouse as pynput_mouse
import edge_tts

from audify.clipboard import ClipboardListener
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
        
        # Tray icon (injected later)
        self.tray_icon = None

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

    def set_rate(self, rate_str: str) -> None:
        """Change the speech rate and persist to config. Changes apply on next chunk playback."""
        with self._config_lock:
            self.config["rate"] = rate_str
            save_config(self.config)

    def set_volume(self, vol: float) -> None:
        """Change immediate mixer volume (0.0 to 1.0) and save to config."""
        with self._config_lock:
            self.config["volume"] = vol
        self._tts_channel.set_volume(vol)
        self._debounced_save()

    def toggle_skip_code_blocks(self) -> None:
        """Toggle skipping of fenced code blocks and save to config."""
        with self._config_lock:
            current = self.config.get("skip_code_blocks", True)
            self.config["skip_code_blocks"] = not current
            save_config(self.config)

    # -- Playback controls -------------------------------------------------

    def stop_audio(self) -> None:
        """Stop any currently-playing audio (global kill-switch callback)."""
        try:
            if self._tts_channel.get_busy():
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

    def check_pause_and_wait(self) -> bool:
        """
        Check if paused. If paused, sleep in a loop until unpaused or new text arrives.
        Returns True if we should abort (e.g. new text arrived in queue).
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
            if not self.q.empty():
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
    
    def _update_tray_title(self, title: str) -> None:
        """Update the tray icon hover text."""
        if self.tray_icon:
            self.tray_icon.title = title

    # -- History -----------------------------------------------------------

    def log_history(self, text: str) -> None:
        """Append spoken text to the session history log."""
        try:
            with open(HISTORY_FILE, "a", encoding="utf-8") as f:
                f.write(text + "\n\n---\n\n")
        except Exception:
            pass

    # -- TTS generation ----------------------------------------------------

    async def _generate_audio(self, text: str, output_file: str) -> None:
        """Call Edge TTS to synthesize speech and save to an mp3 file."""
        voice: str = self.config.get("voice", "en-US-JennyNeural")
        rate: str = self.config.get("rate", "+50%")
        communicate = edge_tts.Communicate(text, voice, rate=rate)
        await communicate.save(output_file)

    def _split_text(self, text: str, first_chunk_max: int = 200) -> list[str]:
        """
        Split text into natural paragraph and sentence-based chunks.

        The FIRST chunk targets ~200 characters (1-2 sentences) so Edge TTS
        can synthesize it fast and playback starts within ~1 second.
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

    def _generate_chunk(self, chunk_text: str) -> str | None:
        """Generate audio for a single text chunk with retry, resolving code blocks live."""
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

        temp_fd, temp_path = tempfile.mkstemp(suffix=".mp3")
        os.close(temp_fd)

        last_error: str | None = None
        for attempt in range(1, 4):
            try:
                future = asyncio.run_coroutine_threadsafe(
                    self._generate_audio(resolved_text, temp_path), 
                    self._loop
                )
                future.result(timeout=30)
                return temp_path
            except asyncio.TimeoutError:
                last_error = "TTS generation timed out (30s)"
            except Exception as e:
                last_error = str(e)
            if attempt < 3:
                time.sleep(0.5 * attempt)

        error_msg = f"Chunk generation failed: {last_error}"
        print(f"[ERROR] {error_msg}")
        if self.tray_icon:
            self.tray_icon.notify(error_msg, "Audify Error")
        self._safe_remove(temp_path)
        return None

    # -- Worker thread -----------------------------------------------------

    def worker_loop(self) -> None:
        """Main TTS worker -- pre-buffers remaining chunks concurrently using ThreadPoolExecutor."""
        executor = ThreadPoolExecutor(max_workers=1)

        while True:
            text: str | None = self.q.get()

            # Drain queue -- only the latest clipboard text matters
            while not self.q.empty():
                text = self.q.get()

            if text is None:  # Exit signal
                executor.shutdown(wait=False)
                break

            if self.is_paused:
                # Wait until unpaused or a new text is queued
                if self.check_pause_and_wait():
                    continue

            # Clean the raw text (strip markdown, apply pronunciation dict)
            try:
                with self._config_lock:
                    dict_rules = self.config.get("pronunciation_dict", {})
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

            # Stop current playback
            self.stop_audio()

            # Log to history once for the full text
            self.log_history(cleaned)

            # Split into chunks for streamed playback
            chunks = self._split_text(cleaned)
            aborted = False
            
            temp_files = []

            # First chunk is generated and played synchronously for <1s latency
            first_path = self._generate_chunk(chunks[0])
            if not first_path:
                continue
                
            temp_files.append(first_path)

            if not self.q.empty():
                for f in temp_files:
                    self._safe_remove(f)
                continue

            if self.is_paused:
                if self.check_pause_and_wait():
                    for f in temp_files:
                        self._safe_remove(f)
                    continue

            self._update_tray_title(f"Audify — Reading (1/{len(chunks)})")
            first_sound = pygame.mixer.Sound(first_path)
            with self._config_lock:
                vol = self.config.get("volume", 1.0)
            first_sound.set_volume(vol)
            self._tts_channel.play(first_sound)

            # Concurrently pre-buffer remaining chunks
            for i in range(1, len(chunks)):
                if not self.q.empty():
                    aborted = True
                    break
                if self.is_paused:
                    if self.check_pause_and_wait():
                        aborted = True
                        break

                # Submit next chunk generation to parallel thread
                future = executor.submit(self._generate_chunk, chunks[i])

                # Wait for current playback to finish/slot to open
                while self._tts_channel.get_queue() is not None or self.is_paused:
                    if not self.q.empty():
                        aborted = True
                        break
                    if self.is_paused:
                        if self.check_pause_and_wait():
                            aborted = True
                            break
                    time.sleep(0.05)

                if aborted:
                    future.cancel()
                    break

                # Fetch pre-buffered chunk
                try:
                    next_path = future.result(timeout=35)
                except Exception as e:
                    print(f"[ERROR] Pre-buffer failed on chunk {i+1}: {e}")
                    next_path = None

                if not next_path:
                    continue
                    
                temp_files.append(next_path)
                
                self._update_tray_title(f"Audify — Reading ({i+1}/{len(chunks)})")
                next_sound = pygame.mixer.Sound(next_path)
                with self._config_lock:
                    vol = self.config.get("volume", 1.0)
                next_sound.set_volume(vol)
                self._tts_channel.queue(next_sound)
                
            # Wait for the remaining audio to finish
            while self._tts_channel.get_busy():
                if not self.q.empty() or self.is_paused and self.check_pause_and_wait():
                    break
                time.sleep(0.05)
                
            # Cleanup temp files
            for f in temp_files:
                self._safe_remove(f)
                
            self._update_tray_title("Audify (Active — Ready)")

        try:
            executor.shutdown(wait=False)
        except Exception:
            pass

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

