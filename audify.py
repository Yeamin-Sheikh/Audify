"""
Audify -- AI-powered clipboard reader using Microsoft Neural voices.

A system-tray daemon that monitors the clipboard and reads aloud any
manually-copied text using Edge TTS.  Designed to run invisibly in the
background on Windows.

Author:  Yeamin Sheikh / Sheikh Technologies
License: MIT
"""

from __future__ import annotations

import asyncio
import ctypes
import json
import os
import queue
import re
import sys
import tempfile
import threading
import time
import tkinter as tk
from tkinter import ttk

import edge_tts
import keyboard
import pygame
import pyperclip
import pystray
from PIL import Image, ImageDraw
from pynput import mouse as pynput_mouse
from pystray import Menu
from pystray import MenuItem as item

from clean_text import markdown_to_text

__version__ = "2.0.0"

# -- Paths (relative to the script / exe location, not CWD) ---------------
if getattr(sys, "frozen", False):
    # Running as a PyInstaller --onefile bundle
    _SCRIPT_DIR: str = os.path.dirname(sys.executable)
else:
    _SCRIPT_DIR: str = os.path.dirname(os.path.abspath(__file__))

CONFIG_FILE: str = os.path.join(_SCRIPT_DIR, "config.json")
HISTORY_FILE: str = os.path.join(_SCRIPT_DIR, "history.log")

# -- DPI awareness (fixes blurry UI on 125 %+ scaling) --------------------
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    pass

# -- Pygame mixer init -----------------------------------------------------
os.environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "hide"
pygame.mixer.init()

# -- Default configuration -------------------------------------------------
DEFAULT_CONFIG: dict = {
    "voice": "en-US-JennyNeural",
    "rate": "+50%",
    "stop_hotkey": "ctrl+alt+s",
    "pronunciation_dict": {
        "SQL": "sequel",
        "API": "A P I",
        "UI": "U I",
        "UX": "U X",
        "GUI": "gooey",
        "JSON": "jason",
        "ChatGPT": "Chat G P T",
        "VS Code": "V S Code",
    },
}


# ---------------------------------------------------------------------------
# Configuration helpers
# ---------------------------------------------------------------------------

def load_config() -> dict:
    """Load user config from disk, falling back to defaults."""
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                config: dict = json.load(f)
                return {**DEFAULT_CONFIG, **config}
        except Exception as e:
            print(f"[WARN] Error loading config: {e}. Using defaults.")
    return DEFAULT_CONFIG.copy()


def save_config(config: dict) -> None:
    """Persist configuration to disk as JSON."""
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=4)
    except Exception as e:
        print(f"[WARN] Error saving config: {e}")


def is_just_url(text: str) -> bool:
    """Return True if *text* is nothing but a URL (skip reading URLs aloud)."""
    text = text.strip()
    pattern = r"^(https?:\/\/)?(www\.)?([a-zA-Z0-9-]+\.)+[a-zA-Z]{2,}(:\d+)?(\/[^\s]*)?$"
    return bool(re.match(pattern, text))


# ---------------------------------------------------------------------------
# TTS Daemon -- core engine
# ---------------------------------------------------------------------------

class TTSDaemon:
    """Background daemon that converts queued text to speech via Edge TTS."""

    def __init__(self) -> None:
        self.q: queue.Queue[str | None] = queue.Queue()
        self.last_spoken: str = ""
        self.config: dict = load_config()
        self.is_paused: bool = False

        # Wispr Flow heuristic -- tracks when the user last pressed Ctrl+C/X
        self.last_ctrl_c_time: float = 0.0

        # Persistent event loop for TTS -- reuses one loop instead of
        # creating / destroying with asyncio.run() on every generation call
        self._loop: asyncio.AbstractEventLoop = asyncio.new_event_loop()

        # Track the last temp .mp3 so we can delete it before the next one
        self._prev_temp: str | None = None

        # Clear stale history from previous session
        if os.path.exists(HISTORY_FILE):
            try:
                os.remove(HISTORY_FILE)
            except Exception:
                pass

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

    # -- Voice / rate setters ----------------------------------------------

    def set_voice(self, voice_id: str) -> None:
        """Change the active TTS voice and persist to config."""
        self.config["voice"] = voice_id
        save_config(self.config)

    def set_rate(self, rate_str: str) -> None:
        """Change the speech rate and persist to config."""
        self.config["rate"] = rate_str
        save_config(self.config)

    # -- Playback controls -------------------------------------------------

    def stop_audio(self) -> None:
        """Stop any currently-playing audio (global kill-switch callback)."""
        try:
            if pygame.mixer.music.get_busy():
                pygame.mixer.music.stop()
        except Exception:
            pass

    def _cleanup_temp(self) -> None:
        """Remove the previous temp audio file to avoid filling up %TEMP%."""
        if self._prev_temp and os.path.exists(self._prev_temp):
            try:
                pygame.mixer.music.unload()
            except Exception:
                pass
            self._safe_remove(self._prev_temp)
            self._prev_temp = None

    @staticmethod
    def _safe_remove(path: str | None) -> None:
        """Silently try to delete a file -- never raises."""
        if path:
            try:
                os.remove(path)
            except Exception:
                pass

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

    # -- Worker thread -----------------------------------------------------

    def worker_loop(self) -> None:
        """Main TTS worker -- processes queued text and plays audio."""
        asyncio.set_event_loop(self._loop)

        while True:
            text: str | None = self.q.get()

            # Drain queue -- only the latest clipboard text matters
            while not self.q.empty():
                text = self.q.get()

            if text is None:  # Exit signal
                break

            if self.is_paused:
                continue

            # Clean the raw text (strip markdown, apply pronunciation dict)
            try:
                cleaned: str = markdown_to_text(
                    text, self.config.get("pronunciation_dict", {})
                )
            except Exception as e:
                print(f"[WARN] Text cleaning failed: {e}")
                continue

            if not cleaned.strip():
                continue

            # Skip if we already just spoke this exact text
            if cleaned == self.last_spoken:
                continue
            self.last_spoken = cleaned

            # Stop current playback and clean up previous temp file
            self.stop_audio()
            self._cleanup_temp()

            temp_path: str | None = None
            try:
                temp_fd, temp_path = tempfile.mkstemp(suffix=".mp3")
                os.close(temp_fd)

                # Retry up to 3 times on network / TTS failures
                last_error: str | None = None
                for attempt in range(1, 4):
                    try:
                        # Reuse the persistent loop (faster than asyncio.run())
                        self._loop.run_until_complete(
                            asyncio.wait_for(
                                self._generate_audio(cleaned, temp_path),
                                timeout=30,
                            )
                        )
                        last_error = None
                        break  # Success
                    except asyncio.TimeoutError:
                        last_error = "TTS generation timed out (30s)"
                        print(f"[WARN] Attempt {attempt}/3: {last_error}")
                    except Exception as e:
                        last_error = str(e)
                        print(f"[WARN] Attempt {attempt}/3: TTS failed: {e}")
                    # Brief backoff before retry (0.5s, then 1s)
                    if attempt < 3:
                        time.sleep(0.5 * attempt)

                # All retries exhausted
                if last_error:
                    print(f"[ERROR] TTS failed after 3 attempts: {last_error}")
                    self._safe_remove(temp_path)
                    continue

                # Skip playback if a newer item arrived or paused mid-generation
                if not self.q.empty() or self.is_paused:
                    self._safe_remove(temp_path)
                    continue

                # Log to history right before playing
                self.log_history(cleaned)

                # Play the generated audio
                try:
                    pygame.mixer.music.load(temp_path)
                    pygame.mixer.music.play()
                    self._prev_temp = temp_path  # Track for cleanup later
                except Exception as e:
                    print(f"[ERROR] Playback failed: {e}")
                    self._safe_remove(temp_path)

            except Exception as e:
                print(f"[ERROR] TTS Worker Exception: {e}")
                self._safe_remove(temp_path)

        # Worker is shutting down
        try:
            self._loop.close()
        except Exception:
            pass

    # -- Clipboard monitor thread ------------------------------------------

    def clipboard_loop(self) -> None:
        """Monitor the clipboard for new copied text and queue it for TTS."""
        # Grab initial clipboard so we don't immediately read stale text
        last_text: str = ""
        try:
            last_text = pyperclip.paste()
        except Exception:
            pass

        while True:
            try:
                time.sleep(0.3)

                # pyperclip can throw if another app has the clipboard locked
                try:
                    current_text: str = pyperclip.paste()
                except Exception:
                    continue

                if current_text != last_text:
                    last_text = current_text

                    if self.is_paused or not current_text.strip():
                        continue

                    if is_just_url(current_text):
                        continue

                    # Wispr Flow Heuristic: only read if Ctrl+C/X was recent
                    time_since_copy: float = time.time() - self.last_ctrl_c_time
                    if time_since_copy <= 1.5:
                        self.q.put(current_text)
                    else:
                        print(
                            "[INFO] Clipboard changed but Ctrl+C wasn't pressed. "
                            "Ignoring (Wispr Flow heuristic)."
                        )

            except Exception as e:
                # Catch-all so the clipboard thread never dies silently
                print(f"[WARN] Clipboard monitor error: {e}")
                time.sleep(1)  # Brief backoff before retrying


# ---------------------------------------------------------------------------
# Tray icon image generators
# ---------------------------------------------------------------------------

def create_play_icon() -> Image.Image:
    """Red play button -- shown when Audify is PAUSED."""
    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(image)
    d.ellipse((4, 4, 60, 60), fill=(220, 53, 69))       # Red circle
    d.polygon([(24, 18), (24, 46), (46, 32)], fill="white")  # Play triangle
    return image


def create_pause_icon() -> Image.Image:
    """Green pause button -- shown when Audify is ACTIVE."""
    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(image)
    d.ellipse((4, 4, 60, 60), fill=(40, 167, 69))  # Green circle
    d.rectangle((22, 20, 28, 44), fill="white")     # Left bar
    d.rectangle((36, 20, 42, 44), fill="white")     # Right bar
    return image


# ---------------------------------------------------------------------------
# Pronunciation Dictionary GUI
# ---------------------------------------------------------------------------

def show_dictionary_ui(daemon: TTSDaemon) -> None:
    """Open a Tkinter window to manage the pronunciation dictionary."""
    root = tk.Tk()
    root.title("Pronunciation Dictionary")
    root.geometry("450x350")
    root.attributes("-topmost", True)

    dict_ref: dict[str, str] = daemon.config.get("pronunciation_dict", {})

    frame = ttk.Frame(root, padding="10")
    frame.pack(fill=tk.BOTH, expand=True)

    # Treeview
    columns = ("word", "pronunciation")
    tree = ttk.Treeview(frame, columns=columns, show="headings", height=8)
    tree.heading("word", text="Original Word")
    tree.heading("pronunciation", text="Spoken As")
    tree.column("word", width=150)
    tree.column("pronunciation", width=250)
    tree.pack(fill=tk.BOTH, expand=True)

    def refresh_tree() -> None:
        for row in tree.get_children():
            tree.delete(row)
        for k, v in dict_ref.items():
            tree.insert("", tk.END, values=(k, v))

    refresh_tree()

    # Input row
    input_frame = ttk.Frame(frame, padding="5 10 0 0")
    input_frame.pack(fill=tk.X)

    ttk.Label(input_frame, text="Word:").grid(row=0, column=0, padx=5, pady=5)
    word_var = tk.StringVar()
    ttk.Entry(input_frame, textvariable=word_var, width=15).grid(
        row=0, column=1, padx=5, pady=5
    )

    ttk.Label(input_frame, text="Spoken:").grid(row=0, column=2, padx=5, pady=5)
    spoken_var = tk.StringVar()
    ttk.Entry(input_frame, textvariable=spoken_var, width=20).grid(
        row=0, column=3, padx=5, pady=5
    )

    # Buttons
    btn_frame = ttk.Frame(frame, padding="0 5 0 0")
    btn_frame.pack(fill=tk.X)

    def add_entry() -> None:
        w, s = word_var.get().strip(), spoken_var.get().strip()
        if w and s:
            dict_ref[w] = s
            daemon.config["pronunciation_dict"] = dict_ref
            save_config(daemon.config)
            refresh_tree()
            word_var.set("")
            spoken_var.set("")

    def remove_entry() -> None:
        selected = tree.selection()
        if selected:
            row_item = tree.item(selected[0])
            w = row_item["values"][0]
            if w in dict_ref:
                del dict_ref[w]
                daemon.config["pronunciation_dict"] = dict_ref
                save_config(daemon.config)
                refresh_tree()

    ttk.Button(input_frame, text="Add/Update", command=add_entry).grid(
        row=0, column=4, padx=5, pady=5
    )
    ttk.Button(btn_frame, text="Remove Selected", command=remove_entry).pack(
        side=tk.LEFT, padx=5
    )
    ttk.Button(btn_frame, text="Close", command=root.destroy).pack(
        side=tk.RIGHT, padx=5
    )

    root.mainloop()


# ---------------------------------------------------------------------------
# System tray setup
# ---------------------------------------------------------------------------

def setup_tray() -> None:
    """Create the TTSDaemon, wire up the system-tray menu, and run."""
    daemon = TTSDaemon()

    # Start background threads
    threading.Thread(target=daemon.worker_loop, daemon=True).start()
    threading.Thread(target=daemon.clipboard_loop, daemon=True).start()

    def on_toggle_pause(icon: pystray.Icon, item_action: item) -> None:
        daemon.is_paused = not daemon.is_paused
        daemon.stop_audio()
        if daemon.is_paused:
            icon.icon = create_play_icon()
            icon.title = "Audify (PAUSED - Click to Resume)"
        else:
            icon.icon = create_pause_icon()
            icon.title = "Audify (Active - Click to Pause)"

    def on_read_clipboard(icon: pystray.Icon, item_action: item) -> None:
        text: str = pyperclip.paste()
        if text.strip():
            daemon.q.put(text)

    def on_copy_last(icon: pystray.Icon, item_action: item) -> None:
        if daemon.last_spoken:
            pyperclip.copy(daemon.last_spoken)

    def on_open_dict(icon: pystray.Icon, item_action: item) -> None:
        threading.Thread(
            target=show_dictionary_ui, args=(daemon,), daemon=True
        ).start()

    def on_exit(icon: pystray.Icon, item_action: item) -> None:
        daemon.stop_audio()
        daemon._cleanup_temp()
        daemon.q.put(None)  # Signal worker to exit
        # Delete session history on exit
        if os.path.exists(HISTORY_FILE):
            try:
                os.remove(HISTORY_FILE)
            except Exception:
                pass
        icon.stop()

    def make_voice_setter(voice_id: str):
        return lambda icon, item_action: daemon.set_voice(voice_id)

    def make_rate_setter(rate_str: str):
        return lambda icon, item_action: daemon.set_rate(rate_str)

    # -- Voice & speed options ---------------------------------------------
    voices: dict[str, str] = {
        "Jenny (Natural Female)": "en-US-JennyNeural",
        "Aria (Standard Female)": "en-US-AriaNeural",
        "Michelle (Expressive Female)": "en-US-MichelleNeural",
        "Ava (Multilingual Female)": "en-US-AvaMultilingualNeural",
        "Jane (Expressive Female)": "en-US-JaneNeural",
        "Ana (Child Female)": "en-US-AnaNeural",
        "Christopher (Natural Male)": "en-US-ChristopherNeural",
        "Guy (Standard Male)": "en-US-GuyNeural",
        "Steffan (Expressive Male)": "en-US-SteffanNeural",
        "Brian (Multilingual Male)": "en-US-BrianMultilingualNeural",
        "Andrew (Multilingual Male)": "en-US-AndrewMultilingualNeural",
        "Eric (Natural Male)": "en-US-EricNeural",
        "Roger (Natural Male)": "en-US-RogerNeural",
    }

    rates: dict[str, str] = {
        "0.5x (Slow)": "-50%",
        "1.0x (Normal)": "+0%",
        "1.25x": "+25%",
        "1.5x (Fast)": "+50%",
        "1.7x (Faster)": "+70%",
        "2.0x (Very Fast)": "+100%",
    }

    voice_menu_items = [
        item(
            label,
            make_voice_setter(v_id),
            checked=lambda i, vid=v_id: daemon.config.get("voice") == vid,
            radio=True,
        )
        for label, v_id in voices.items()
    ]

    rate_menu_items = [
        item(
            label,
            make_rate_setter(r_str),
            checked=lambda i, r=r_str: daemon.config.get("rate") == r,
            radio=True,
        )
        for label, r_str in rates.items()
    ]

    # -- Build the tray icon -----------------------------------------------
    icon_image = create_pause_icon()
    tray = pystray.Icon(
        "Audify",
        icon_image,
        "Audify (Active - Click to Pause)",
        menu=Menu(
            item(
                lambda text: "\u25b6 Resume Listening"
                if daemon.is_paused
                else "\u23f8 Pause Listening",
                on_toggle_pause,
                default=True,
            ),
            Menu.SEPARATOR,
            item("Read Current Clipboard", on_read_clipboard),
            item("Copy Last Spoken", on_copy_last),
            Menu.SEPARATOR,
            item("Pronunciation Dictionary...", on_open_dict),
            Menu.SEPARATOR,
            item("Voice", Menu(*voice_menu_items)),
            item("Speed", Menu(*rate_menu_items)),
            Menu.SEPARATOR,
            item("Exit", on_exit),
        ),
    )
    tray.run()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Single-instance enforcement using a Windows named mutex.
    # A named mutex is system-wide; if another process already holds it,
    # CreateMutexW returns a handle but GetLastError() == ERROR_ALREADY_EXISTS.
    _ERROR_ALREADY_EXISTS = 0xB7
    _mutex = ctypes.windll.kernel32.CreateMutexW(
        None, False, "Global\\AudifyTTSSingleInstance"
    )
    if ctypes.windll.kernel32.GetLastError() == _ERROR_ALREADY_EXISTS:
        ctypes.windll.user32.MessageBoxW(
            0,
            "Audify is already running in the system tray.\n\n"
            "Look for the green/red circle icon near the clock.",
            "Audify \u2014 Already Running",
            0x40,  # MB_ICONINFORMATION
        )
        sys.exit(0)

    setup_tray()
