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

from audify.clipboard import (
    ClipboardListener,
    DoubleTapDetector,
    MOD_ALT,
    MOD_CONTROL,
    MOD_SHIFT,
    VK_CONTROL,
    VK_MENU,
    VK_SHIFT,
    check_copy_keys,
    is_ctrl_pressed,
    is_double_tap_hotkey,
    is_vk_down,
    parse_win32_hotkey,
)
from audify.playback import SpeechSession, prewarm_edge_tts, rate_to_speed
from audify.stt import BarkCleanController
from audify.updater import start_weekly_update_checker
from audify.config import (
    CONFIG_FILE,
    HISTORY_FILE,
    is_just_url,
    load_config,
    save_config,
)
from clean_text import apply_pronunciations, markdown_to_text

# DPI awareness for Windows
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    pass


class TTSDaemon:
    """Background daemon that converts queued text to speech via Edge TTS."""

    def __init__(self) -> None:
        self.q: queue.Queue[str | None] = queue.Queue()
        self.last_spoken: str = ""
        self.config: dict = load_config()
        self.is_paused: bool = False
        self.current_code_blocks: list[str] = []

        # Thread-safe locks
        self._config_lock = threading.Lock()
        self._mixer_lock = threading.Lock()
        self._save_timer: threading.Timer | None = None
        self._tts_channel: pygame.mixer.Channel | None = None

        # Wispr Flow heuristic -- tracks when the user last triggered a manual copy or click
        self.last_ctrl_c_time: float = 0.0
        self._last_stop_time: float = 0.0

        # Parse stop hotkey (default ctrl+alt+s) into Win32 modifiers + VK code or double-tap
        stop_hotkey_str = str(self.config.get("stop_hotkey", "ctrl+alt+s"))
        self._stop_double_tap = DoubleTapDetector(is_double_tap_hotkey(stop_hotkey_str))
        self._stop_hotkey_parsed = (
            None
            if self._stop_double_tap.target_vk is not None
            else (
                parse_win32_hotkey(stop_hotkey_str)
                or (MOD_CONTROL | MOD_ALT, ord("S"))
            )
        )

        # Parse dictation hotkey (default double_lctrl) into double-tap or Win32 modifiers + VK code
        dictation_hotkey_str = str(self.config.get("dictation_hotkey", "double_lctrl"))
        self._dictation_double_tap = DoubleTapDetector(
            is_double_tap_hotkey(dictation_hotkey_str)
        )
        self._dictation_hotkey_parsed = (
            None
            if self._dictation_double_tap.target_vk is not None
            else (
                parse_win32_hotkey(dictation_hotkey_str)
                or (MOD_CONTROL | MOD_ALT, ord("D"))
            )
        )
        self._dictation_combo_was_down: bool = False

        # Parse paste-last-dictation hotkey (default ctrl+alt+v) into Win32 modifiers + VK code
        paste_last_hotkey_str = str(self.config.get("paste_last_hotkey", "ctrl+alt+v"))
        self._paste_last_double_tap = DoubleTapDetector(
            is_double_tap_hotkey(paste_last_hotkey_str)
        )
        self._paste_last_hotkey_parsed = (
            None
            if self._paste_last_double_tap.target_vk is not None
            else (
                parse_win32_hotkey(paste_last_hotkey_str)
                or (MOD_CONTROL | MOD_ALT, ord("V"))
            )
        )
        self._paste_last_combo_was_down: bool = False

        # Speech-to-Text controller powered by Underdog Bark 3.0 Clean
        self.stt = BarkCleanController(daemon=self)

        # Persistent event loop for TTS
        self._loop = asyncio.new_event_loop()
        threading.Thread(target=self._start_loop, daemon=True, name="tts-event-loop").start()

        # Pre-warm Sec-MS-GEC v2 token, shared TCPConnector, and DNS cache
        prewarm_edge_tts(self._loop)

        # Event-driven clipboard listener + native Win32 RegisterHotKey for stop, dictation & paste-last
        self._clip_listener = ClipboardListener(
            self._on_clipboard_change,
            stop_hotkey=stop_hotkey_str,
            on_stop_hotkey=self.stop_audio,
            dictation_hotkey=dictation_hotkey_str,
            on_dictation_hotkey=self.toggle_dictation,
            paste_last_hotkey=paste_last_hotkey_str,
            on_paste_last_hotkey=self.paste_last_dictation,
            on_escape_hotkey=self.stt.on_escape_pressed,
        )

        # Tray icon (injected later)
        self.tray_icon = None

        # Playback status shown by the tray; the tray registers on_state_change to redraw
        self.status: str = "Ready"
        self.is_speaking: bool = False
        self.on_state_change: Callable[[], None] | None = None

        # Bumped by the stop hotkey; a session aborts when this no longer matches its start value
        self._stop_generation: int = 0
        # Bumped when voice or speed changes; a session then resumes the text with the new setting
        self._restart_generation: int = 0

        # Clear stale history from previous session
        if os.path.exists(HISTORY_FILE):
            try:
                os.remove(HISTORY_FILE)
            except Exception:
                pass

        # Initialize audio mixer non-fatally (if Windows Audio / WASAPI is still
        # starting up at login, _ensure_mixer will retry lazily when speech begins)
        self._ensure_mixer(retries=1)

        # Layer 1: Win32 hardware key/mouse state poller (works in Administrator windows
        # where UIPI blocks WH_KEYBOARD_LL, and never suffers from stuck keys or sleep unhooking)
        threading.Thread(
            target=self._poll_input_state_loop,
            daemon=True,
            name="win32-input-poller",
        ).start()

        # Layer 2: Stateless keyboard.on_press and mouse hooks for instant 0ms event capture
        # (avoids keyboard.add_hotkey's _pressed_events dict, which permanently breaks if
        # any KEY_UP is missed during Alt+Tab or UAC)
        try:
            keyboard.on_press(self._on_key_press)
            self.mouse_listener = pynput_mouse.Listener(on_click=self.on_mouse_click)
            self.mouse_listener.start()
        except Exception as e:
            print(f"[WARN] Failed to bind input hook: {e}")

        # Start periodic weekly check for edge-tts updates on PyPI
        start_weekly_update_checker(self)

    def _start_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    # -- Audio mixer management (WASAPI / DirectSound / WinMM fallback) ----

    def _ensure_mixer(
        self,
        retries: int = 1,
        force_reinit: bool = False,
    ) -> pygame.mixer.Channel | None:
        """Initialize or recover pygame.mixer with multi-driver fallback.

        Windows WASAPI can fail with ``WASAPI can't initialize audio client:`` when:
        - Audify starts at Windows login before the Windows Audio endpoint is ready
        - Headphones/Bluetooth/HDMI audio outputs are unplugged or switching
        - Another application holds exclusive mode on the default endpoint

        Tries default SDL driver first, then explicitly falls back across
        ``wasapi``, ``directsound``, and ``winmm`` with both 24kHz mono and
        48kHz stereo configurations. Never raises an unhandled exception.
        """
        with self._mixer_lock:
            if not force_reinit and pygame.mixer.get_init() is not None and self._tts_channel is not None:
                return self._tts_channel

            drivers: tuple[str | None, ...] = (None, "wasapi", "directsound", "winmm")
            specs: tuple[tuple[int, int, int], ...] = (
                (24000, 1, 512),
                (48000, 2, 1024),
            )
            last_err: Exception | None = None

            for attempt in range(max(1, retries)):
                for driver in drivers:
                    for freq, chans, buf in specs:
                        try:
                            if driver is None:
                                os.environ.pop("SDL_AUDIODRIVER", None)
                            else:
                                os.environ["SDL_AUDIODRIVER"] = driver
                            try:
                                pygame.mixer.quit()
                            except Exception:
                                pass
                            pygame.mixer.init(
                                frequency=freq,
                                size=-16,
                                channels=chans,
                                buffer=buf,
                            )
                            if pygame.mixer.get_init() is not None:
                                self._tts_channel = pygame.mixer.Channel(0)
                                with self._config_lock:
                                    raw_vol = float(self.config.get("volume", 1.0))
                                vol = raw_vol / 100.0 if raw_vol > 1.0 else raw_vol
                                self._tts_channel.set_volume(max(0.0, min(1.0, vol)))
                                return self._tts_channel
                        except Exception as e:
                            last_err = e
                if attempt + 1 < retries:
                    time.sleep(0.35)

            self._tts_channel = None
            print(f"[WARN] Audio mixer initialization deferred (no active audio endpoint): {last_err}")
            return None

    # -- Hotkey / mouse callbacks ------------------------------------------

    def _is_stop_combo_down(self) -> bool:
        """Return True if the configured stop hotkey (e.g. Ctrl+Alt+S) is physically held down."""
        if self._stop_hotkey_parsed is None:
            return False
        mods, vk = self._stop_hotkey_parsed
        if not is_vk_down(vk):
            return False
        if (mods & MOD_CONTROL) and not is_vk_down(VK_CONTROL):
            return False
        if (mods & MOD_ALT) and not is_vk_down(VK_MENU):
            return False
        if (mods & MOD_SHIFT) and not is_vk_down(VK_SHIFT):
            return False
        return True

    def _is_dictation_combo_down(self) -> bool:
        """Return True if the configured dictation hotkey (e.g. Ctrl+Alt+D) is physically held down."""
        if self._dictation_hotkey_parsed is None:
            return False
        mods, vk = self._dictation_hotkey_parsed
        if not is_vk_down(vk):
            return False
        if (mods & MOD_CONTROL) and not is_vk_down(VK_CONTROL):
            return False
        if (mods & MOD_ALT) and not is_vk_down(VK_MENU):
            return False
        if (mods & MOD_SHIFT) and not is_vk_down(VK_SHIFT):
            return False
        return True

    def _is_paste_last_combo_down(self) -> bool:
        """Return True if the configured paste-last hotkey (e.g. Ctrl+Alt+V) is physically held down."""
        if self._paste_last_hotkey_parsed is None:
            return False
        mods, vk = self._paste_last_hotkey_parsed
        if not is_vk_down(vk):
            return False
        if (mods & MOD_CONTROL) and not is_vk_down(VK_CONTROL):
            return False
        if (mods & MOD_ALT) and not is_vk_down(VK_MENU):
            return False
        if (mods & MOD_SHIFT) and not is_vk_down(VK_SHIFT):
            return False
        return True

    def toggle_dictation(self) -> None:
        """Toggle Bark 3.0 Clean microphone dictation on/off."""
        self.stt.toggle_dictation()

    def paste_last_dictation(self) -> None:
        """Re-paste the most recently dictated speech into the active input field."""
        self.stt.paste_last_dictation()

    def set_stt_silence_delay(self, delay_s: float) -> None:
        """Update the auto-stop silence delay (in seconds) for Bark 3.0 Clean dictation."""
        val = max(0.4, min(3.0, round(float(delay_s), 2)))
        with self._config_lock:
            self.config["stt_silence_delay"] = val
            save_config(self.config)
        self._notify_state_change()

    def set_theme_mode(self, mode: str) -> None:
        """Update the UI theme preference ('system', 'light', or 'dark') and persist config."""
        norm = (mode or "system").strip().lower()
        if norm not in ("system", "light", "dark"):
            norm = "system"
        with self._config_lock:
            self.config["theme_mode"] = norm
            save_config(self.config)
        try:
            from audify.gui.theme import apply_native_menu_theme
            apply_native_menu_theme(norm)
        except Exception:
            pass
        self._notify_state_change()

    def set_hotkey(self, key_name: str, hotkey_str: str) -> bool:
        """Update a global shortcut key ('dictation_hotkey', 'paste_last_hotkey', or 'stop_hotkey')."""
        norm = hotkey_str.strip().lower()
        dt_vk = is_double_tap_hotkey(norm)
        parsed = None if dt_vk is not None else parse_win32_hotkey(norm)
        if dt_vk is None and not parsed:
            return False

        with self._config_lock:
            self.config[key_name] = norm
            save_config(self.config)

        if key_name == "stop_hotkey":
            self._stop_double_tap.set_target_vk(dt_vk)
            self._stop_hotkey_parsed = parsed
            self._clip_listener.rebind_hotkeys(stop_hotkey=norm)
        elif key_name == "dictation_hotkey":
            self._dictation_double_tap.set_target_vk(dt_vk)
            self._dictation_hotkey_parsed = parsed
            self._dictation_combo_was_down = False
            self._clip_listener.rebind_hotkeys(dictation_hotkey=norm)
        elif key_name == "paste_last_hotkey":
            self._paste_last_double_tap.set_target_vk(dt_vk)
            self._paste_last_hotkey_parsed = parsed
            self._paste_last_combo_was_down = False
            self._clip_listener.rebind_hotkeys(paste_last_hotkey=norm)
        else:
            return False

        self._notify_state_change()
        return True

    def _poll_input_state_loop(self) -> None:
        """Poll Win32 GetAsyncKeyState every 15ms so copy/stop/dictation keys work even in Admin windows."""
        # Flush any pre-startup 0x0001 bits from key/mouse presses before Audify launched
        try:
            check_copy_keys()
        except Exception:
            pass
        while True:
            try:
                if check_copy_keys() and not self.stt.learner.is_probing_clipboard and (time.monotonic() - self.stt.learner.last_probe_time) >= 0.25:
                    self.last_ctrl_c_time = time.time()
                    self._dictation_double_tap.taint()
                    self._stop_double_tap.taint()
                    self._paste_last_double_tap.taint()

                if self.stt.is_busy and is_vk_down(0x1B):
                    self.stt.cancel_dictation()

                if self._stop_double_tap.poll() or self._is_stop_combo_down():
                    self.stop_audio()

                if self._dictation_double_tap.poll():
                    self.toggle_dictation()
                else:
                    dict_down = self._is_dictation_combo_down()
                    if dict_down and not self._dictation_combo_was_down:
                        self._dictation_combo_was_down = True
                        self.toggle_dictation()
                    elif not dict_down:
                        self._dictation_combo_was_down = False

                if self._paste_last_double_tap.poll():
                    self.paste_last_dictation()
                else:
                    paste_down = self._is_paste_last_combo_down()
                    if paste_down and not self._paste_last_combo_was_down:
                        self._paste_last_combo_was_down = True
                        self.paste_last_dictation()
                    elif not paste_down:
                        self._paste_last_combo_was_down = False
            except Exception:
                pass
            time.sleep(0.015)

    def _on_key_press(self, event: keyboard.KeyboardEvent) -> None:
        """Stateless key-down callback that queries physical modifier state via Win32."""
        try:
            # Prevent keyboard library's internal _pressed_events from accumulating stuck keys
            keyboard._pressed_events.clear()
        except Exception:
            pass
        try:
            self.stt.learner.on_key_press(event)
        except Exception:
            pass
        name = (getattr(event, "name", "") or "").lower()
        if name in ("esc", "escape") and self.stt.is_busy:
            self.stt.cancel_dictation()
            return
        self._dictation_double_tap.taint_if_other_key(name)
        self._stop_double_tap.taint_if_other_key(name)
        self._paste_last_double_tap.taint_if_other_key(name)
        if name in ("c", "x", "insert", "ins") and is_ctrl_pressed() and not self.stt.learner.is_probing_clipboard:
            self.last_ctrl_c_time = time.time()
        elif self._is_stop_combo_down():
            self.stop_audio()
        elif self._is_dictation_combo_down() and not self._dictation_combo_was_down:
            self._dictation_combo_was_down = True
            self.toggle_dictation()
        elif self._is_paste_last_combo_down() and not self._paste_last_combo_was_down:
            self._paste_last_combo_was_down = True
            self.paste_last_dictation()

    def on_mouse_click(
        self, x: int, y: int, button: pynput_mouse.Button, pressed: bool
    ) -> None:
        """Record timestamp on any mouse click (for Wispr Flow heuristic) and notify STT learner."""
        try:
            self.stt.learner.on_mouse_click(x, y, button, pressed)
        except Exception:
            pass
        if pressed:
            self.last_ctrl_c_time = time.time()
            self._dictation_double_tap.taint()
            self._stop_double_tap.taint()
            self._paste_last_double_tap.taint()

    def mark_manual_copy(self) -> None:
        """Record that the user manually triggered a copy shortcut."""
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
        """Change the active TTS voice and persist to config (applies mid-speech too)."""
        with self._config_lock:
            changed = self.config.get("voice") != voice_id
            self.config["voice"] = voice_id
            save_config(self.config)
        if changed:
            self._restart_generation += 1
        self._notify_state_change()

    def set_rate(self, rate_str: str) -> None:
        """Change the speech rate and persist to config.

        Speed is baked into the generated audio, so while speaking the current
        text is resumed from the sentence being heard at the new rate.
        """
        with self._config_lock:
            changed = self.config.get("rate") != rate_str
            self.config["rate"] = rate_str
            save_config(self.config)
        if changed:
            self._restart_generation += 1
        self._notify_state_change()

    def set_volume(self, vol: float) -> None:
        """Change immediate mixer volume (0.0 to 1.0) and save to config."""
        with self._config_lock:
            self.config["volume"] = vol
        ch = self._tts_channel or self._ensure_mixer(retries=1)
        if ch is not None:
            try:
                ch.set_volume(vol)
            except Exception:
                pass
        self._debounced_save()
        self._notify_state_change()

    def toggle_skip_code_blocks(self) -> None:
        """Toggle skipping of fenced code blocks and save to config."""
        with self._config_lock:
            current = self.config.get("skip_code_blocks", True)
            self.config["skip_code_blocks"] = not current
            save_config(self.config)
        self._notify_state_change()

    def toggle_read_on_copy(self) -> None:
        """Toggle automatic Text-to-Speech when copying text (`read_on_copy`) and save to config."""
        with self._config_lock:
            current = bool(self.config.get("read_on_copy", True))
            new_val = not current
            self.config["read_on_copy"] = new_val
            save_config(self.config)
        if not new_val:
            self.stop_audio()
        self._notify_state_change()

    # -- Playback controls -------------------------------------------------

    def stop_audio(self) -> None:
        """Stop reading entirely (global kill-switch callback).

        Bumping the generation makes the worker abandon the rest of the text;
        stopping the channel alone would only cut the current chunk.
        """
        now = time.monotonic()
        if (now - self._last_stop_time) < 0.15:
            return
        self._last_stop_time = now
        self._stop_generation += 1
        self._halt_channel()

    def _halt_channel(self) -> None:
        ch = self._tts_channel
        if ch is not None:
            try:
                ch.stop()
            except Exception:
                pass

    def pause_audio(self) -> None:
        """Pause any currently-playing audio."""
        ch = self._tts_channel
        if ch is not None:
            try:
                ch.pause()
            except Exception:
                pass

    def resume_audio(self) -> None:
        """Resume any currently-paused audio."""
        ch = self._tts_channel
        if ch is not None:
            try:
                ch.unpause()
            except Exception:
                pass

    def check_pause_and_wait(self, stop_generation: int | None = None) -> bool:
        """
        Check if paused. If paused, sleep in a loop until unpaused or new text arrives.
        Returns True if we should abort (new text arrived, or stop was pressed).
        """
        if not self.is_paused:
            return False

        ch = self._tts_channel
        # Pause playback if it's currently busy
        if ch is not None:
            try:
                if ch.get_busy():
                    ch.pause()
            except Exception:
                pass

        while self.is_paused:
            stopped = stop_generation is not None and stop_generation != self._stop_generation
            if stopped or not self.q.empty():
                # New text in queue, we must abort current playback
                if ch is not None:
                    try:
                        ch.stop()
                    except Exception:
                        pass
                return True
            time.sleep(0.05)

        # Unpaused, resume playback if it was busy
        if ch is not None:
            try:
                ch.unpause()
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

    def _split_text(self, text: str, first_chunk_max: int = 80) -> list[str]:
        """
        Split text into natural paragraph and sentence-based chunks.

        The FIRST chunk targets ~80 characters (about one short sentence) so the
        first Edge TTS request finishes with minimal latency and audio playback
        starts in well under a second. All subsequent chunks use an ~800 character
        limit and are prefetched concurrently while the first chunk plays.
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
            rules = dict(self.config.get("pronunciation_dict", {}))

        resolved_text = chunk_text
        for idx, code_content in enumerate(self.current_code_blocks):
            placeholder = f"[[CODE_BLOCK_{idx}]]"
            if placeholder not in resolved_text:
                continue
            # Code is read with the dictionary too (`requirements.txt`, `__init__.py`, ...)
            if code_content.startswith("FENCED:"):
                replacement = " [Skipped code block] " if skip_code else f" {apply_pronunciations(code_content[7:], rules)} "
            else:
                replacement = f" {apply_pronunciations(code_content[7:], rules)} "
            resolved_text = resolved_text.replace(placeholder, replacement)
        return resolved_text

    def _notify_info(self, message: str) -> None:
        print(f"[INFO] {message}")
        if self.tray_icon:
            try:
                self.tray_icon.notify(message, "Audify")
            except Exception:
                pass

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

            # Record for "Replay Last" in the tray menu (250ms burst deduplication
            # is handled in ClipboardListener._wnd_proc so intentional re-copies speak)
            self.last_spoken = cleaned

            self._halt_channel()
            self.log_history(cleaned)

            try:
                # A mid-speech voice or speed change hands back the unread rest
                # of the text to continue from the current sentence
                remaining: str | None = cleaned
                while remaining:
                    remaining = self._speak(remaining, stop_generation)
            except Exception as e:
                print(f"[ERROR] Playback failed: {e}")
                self._halt_channel()
            self._set_status("Ready")

    def _speak(self, text: str, stop_generation: int) -> str | None:
        """Play one text: start a streaming session and feed its audio to the channel gaplessly.

        Returns ``unread_text`` when reading must continue with new settings
        (resuming from the start of the sentence being heard after a voice/speed
        change), or ``None`` when playback finishes or is stopped.
        """
        restart_generation = self._restart_generation
        with self._config_lock:
            voice = self.config.get("voice", "en-US-JennyNeural")
            rate = self.config.get("rate", "+50%")

        channel = self._ensure_mixer(retries=3)
        init_fmt = pygame.mixer.get_init()
        if channel is None or init_fmt is None:
            self._notify_error(
                "Audio output device unavailable. Connect speakers or headphones and try again."
            )
            return None
        frequency, _size, channels = init_fmt

        chunks = self._split_text(text)

        session = SpeechSession(
            chunks, voice, rate, self._loop, (frequency, channels),
            resolve_text=self._resolve_code_blocks,
            on_error=self._notify_error,
        )
        session.start()

        ready: deque[list] = deque()  # [chunk_index, bytearray] not yet handed to the channel
        playing: list[tuple[pygame.mixer.Sound, int]] = []  # sounds given to the channel, in order
        shown_chunk = -1
        decoded_all = False
        bytes_per_second = frequency * channels * 2
        start_cushion = int(bytes_per_second * 0.05)  # one 50ms decoded block of 16-bit audio

        # Playback position tracking, used to resume after a voice/speed change
        chunk_bytes: dict[int, int] = {}
        chunk_started: dict[int, tuple[float, float]] = {}  # index -> (start time, paused total then)
        highest_chunk = -1
        paused_total = 0.0
        restarting = False

        def aborted() -> bool:
            return not self.q.empty() or stop_generation != self._stop_generation

        def unread_text() -> str:
            """Text from the start of the sentence being heard to the end."""
            if shown_chunk < 0:
                return "\n\n".join(chunks)
            current_text = chunks[shown_chunk]
            started, paused_then = chunk_started[shown_chunk]
            heard = time.perf_counter() - started - (paused_total - paused_then)
            if decoded_all or highest_chunk > shown_chunk:
                duration = chunk_bytes.get(shown_chunk, 0) / bytes_per_second
            else:  # still arriving: estimate from length (~15 characters/second at 1x)
                duration = len(current_text) / (15.0 * rate_to_speed(rate))
            fraction = min(1.0, max(0.0, heard / duration)) if duration > 0 else 0.0
            position = int(fraction * len(current_text))
            sentence_start = max(current_text.rfind(mark, 0, position) for mark in (". ", "! ", "? ", "\n"))
            rest = [current_text[sentence_start + 1:].strip() if sentence_start >= 0 else current_text]
            rest += chunks[shown_chunk + 1:]
            return "\n\n".join(part for part in rest if part)

        try:
            while True:
                if aborted():
                    return None
                if restart_generation != self._restart_generation:
                    restarting = True
                    return unread_text()
                if self.is_paused:
                    pause_began = time.perf_counter()
                    if self.check_pause_and_wait(stop_generation):
                        return None
                    paused_total += time.perf_counter() - pause_began
                    continue  # re-check for a voice/speed change made while paused

                # Collect decoded audio; merge consecutive blocks of the same chunk
                try:
                    item = session.pcm.get(timeout=0.015)
                except queue.Empty:
                    item = False
                if item is None:
                    decoded_all = True
                elif item:
                    index, data = item
                    chunk_bytes[index] = chunk_bytes.get(index, 0) + len(data)
                    highest_chunk = max(highest_chunk, index)
                    if ready and ready[-1][0] == index:
                        ready[-1][1] += data
                    else:
                        ready.append([index, bytearray(data)])

                # Keep the channel's single queue slot filled for gapless playback.
                # Starting from silence, wait for a 50ms cushion so playback never stutters.
                try:
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
                    current = channel.get_sound()
                    still_busy = channel.get_busy()
                except pygame.error:
                    # Audio output endpoint changed or dropped mid-session; re-init mixer
                    channel = self._ensure_mixer(retries=2, force_reinit=True)
                    new_fmt = pygame.mixer.get_init()
                    if channel is None or new_fmt is None:
                        self._notify_error("Audio output device disconnected during playback.")
                        return None
                    if (new_fmt[0], new_fmt[2]) != (frequency, channels):
                        restarting = True
                        return unread_text()
                    continue

                # Status follows what is audible, not what has been downloaded
                for sound, index in playing:
                    if sound is current and index != shown_chunk:
                        shown_chunk = index
                        chunk_started[index] = (time.perf_counter(), paused_total)
                        total = len(chunks)
                        self._set_status("Reading" if total == 1 else f"Reading {index + 1} of {total}", speaking=True)
                        break
                if len(playing) > 8:
                    del playing[:-4]

                if decoded_all and not ready and not still_busy:
                    return None
        finally:
            session.cancel()
            if restarting or aborted():
                self._halt_channel()

    # -- Clipboard monitor thread ------------------------------------------

    def start_clipboard_monitor(self) -> None:
        self._clip_listener.start()

    def _on_clipboard_change(self, text: str) -> None:
        if (
            self.is_paused
            or not self.config.get("read_on_copy", True)
            or not text.strip()
        ):
            return
        if self.stt.learner.is_probing_clipboard or (time.monotonic() - self.stt.learner.last_probe_time) < 0.35:
            return
        if is_just_url(text):
            return

        # Direct Win32 hardware key check at the millisecond WM_CLIPBOARDUPDATE arrives
        if check_copy_keys():
            self.last_ctrl_c_time = time.time()
            self.q.put(text)
            return

        # Fast path: check immediately (0ms latency). The keyboard hook or 15ms poller
        # fires within ~1-15ms of keypress, so this succeeds immediately.
        time_since_copy = time.time() - self.last_ctrl_c_time
        if time_since_copy <= 1.5:
            self.q.put(text)
            return

        # Slow path: keyboard hook or poller might be mid-tick (rare race).
        # Wait 35ms and retry once.
        def _retry():
            if check_copy_keys() or (time.time() - self.last_ctrl_c_time) <= 1.55:
                self.last_ctrl_c_time = time.time()
                self.q.put(text)

        threading.Timer(0.035, _retry).start()
