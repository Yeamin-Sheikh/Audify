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
from concurrent.futures import ThreadPoolExecutor, Future

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

__version__ = "2.2.3"

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

# -- Large standard technical dictionary (150+ terms) ----------------------
DEFAULT_PRONUNCIATION: dict[str, str] = {
    # Tech Terms / Abbreviations
    "API": "A P I",
    "APIs": "A P I s",
    "CLI": "C L I",
    "GUI": "gooey",
    "GUIs": "gooeys",
    "UI": "U I",
    "UX": "U X",
    "URL": "U R L",
    "URLs": "U R L s",
    "SQL": "sequel",
    "NoSQL": "no sequel",
    "MySQL": "my sequel",
    "PostgreSQL": "post-gres sequel",
    "GraphQL": "graph Q L",
    "JSON": "jason",
    "YAML": "yamel",
    "TOML": "tom-ul",
    "HTML": "H T M L",
    "CSS": "C S S",
    "HTTP": "H T M L",
    "HTTPS": "H T T P S",
    "SSH": "S S H",
    "FTP": "F T P",
    "DNS": "D N S",
    "TCP": "T C P",
    "UDP": "U D P",
    "OAuth": "oh-auth",
    "REST": "rest",
    "CRUD": "crud",
    "AJAX": "ay-jax",
    "regex": "reg-ex",
    "sudo": "sue-doo",
    "nginx": "engine X",
    "async": "ay-sink",
    "await": "ay-wait",
    "npm": "N P M",
    "npx": "N P X",
    "PyPI": "pie P I",
    "pip": "pip",
    "venv": "V env",
    "kubectl": "kube control",
    "Kubernetes": "koo-ber-net-eez",
    "DevOps": "dev ops",
    "ChatGPT": "Chat G P T",
    "GPT": "G P T",
    "LLM": "L L M",
    "LLMs": "L L M s",
    "DALL-E": "dolly",
    "FastAPI": "fast A P I",
    "Django": "jango",
    "webpack": "web pack",
    "TypeScript": "type script",
    "JavaScript": "java script",
    "GitHub": "git hub",
    "GitLab": "git lab",
    "VSCode": "V S Code",
    "VS Code": "V S Code",
    "LinkedIn": "linked in",
    "macOS": "mac O S",
    "iOS": "I O S",
    "iPadOS": "I pad O S",
    "MongoDB": "mongo D B",
    "Redis": "reddis",
    "Vercel": "ver-sell",
    "Supabase": "super base",
    "LaTeX": "lay tech",
    "localhost": "local host",
    "README": "read me",
    "changelog": "change log",
    "middleware": "middle ware",
    "frontend": "front end",
    "backend": "back end",
    "fullstack": "full stack",
    "boolean": "boo-lee-an",
    "tuple": "too-pull",
    "tuples": "too-pulls",
    "innerHTML": "inner H T M L",
    "XMLHttpRequest": "X M L H T T P request",
    "AWS": "A W S",
    "GCP": "G C P",
    "AI": "A I",
    "ML": "M L",
    "NLP": "N L P",
    "GPU": "G P U",
    "CPU": "C P U",
    "RAM": "ram",
    "SSD": "S S D",
    "HDD": "H D D",
    "USB": "U S B",
    "BIOS": "buy-oss",
    "UEFI": "U E F I",
    "PDF": "P D F",
    "IDE": "I D E",
    "SDK": "S D K",
    "CDN": "C D N",
    "VPN": "V P N",
    "JWT": "J W T",
    "JWTs": "J W T s",
    "UUID": "U U I D",
    "CORS": "cors",
    "CSRF": "C S R F",
    "XSS": "X S S",
    "TLS": "T L S",
    "SSL": "S S L",
    "SMTP": "S M T P",
    "IMAP": "I M A P",
    "IoT": "I o T",
    "IEEE": "I triple E",
    "FIFO": "fife-oh",
    "LIFO": "life-oh",
    "ASAP": "A S A P",
    "EOF": "E O F",
    "OOP": "O O P",
    "MVC": "M V C",
    "MVVM": "M V V M",
    "SaaS": "sass",
    "PaaS": "pass",
    "IaaS": "I az",
    "CICD": "C I C D",
    "PR": "P R",
    "PRs": "P R s",
    "QA": "Q A",
    "UAT": "U A T",
    "ETL": "E T L",
    "CSV": "C S V",
    "XML": "X M L",
    "WASM": "waz-em",
    "ESLint": "E S lint",
    "OAuth2": "oh-auth 2",
    # Text shortcuts / punctuation
    "e.g.": "for example",
    "i.e.": "that is",
    "etc.": "etcetera",
    "vs.": "versus",
    "approx.": "approximately",
    "dept.": "department",
    "govt.": "government",
    "et al.": "et all",
    "Fig.": "figure",
    "fig.": "figure",
    "w/": "with ",
    "w/o": "without",
    # Symbols
    "=>": " arrow ",
    "->": " arrow ",
    "!==": " not equal to ",
    "===": " triple equals ",
    "!=": " not equal ",
    ">=": " greater or equal ",
    "<=": " less or equal ",
    "&&": " and ",
    "||": " or ",
}

DEFAULT_CONFIG: dict = {
    "voice": "en-US-JennyNeural",
    "rate": "+50%",
    "volume": 1.0,
    "skip_code_blocks": True,
    "stop_hotkey": "ctrl+alt+s",
    "pronunciation_dict": {},
}

# -- Speech option definitions (Global Scope for Unified Access) -----------
VOICES: dict[str, str] = {
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

RATES: dict[str, str] = {
    "0.5x (Slow)": "-50%",
    "1.0x (Normal)": "+0%",
    "1.25x": "+25%",
    "1.5x (Fast)": "+50%",
    "1.7x (Faster)": "+70%",
    "2.0x (Very Fast)": "+100%",
}

VOLUMES: dict[str, float] = {
    "20%": 0.2,
    "40%": 0.4,
    "60%": 0.6,
    "80%": 0.8,
    "100%": 1.0,
}


# ---------------------------------------------------------------------------
# Configuration helpers
# ---------------------------------------------------------------------------

def load_config() -> dict:
    """Load user config from disk, falling back to defaults and merging library items."""
    config = DEFAULT_CONFIG.copy()
    
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                config.update(loaded)
        except Exception as e:
            print(f"[WARN] Error loading config: {e}. Using defaults.")

    # Auto-merge missing library items into the config's dictionary
    user_dict = config.setdefault("pronunciation_dict", {})
    changed = False
    for k, v in DEFAULT_PRONUNCIATION.items():
        if k not in user_dict:
            user_dict[k] = v
            changed = True
            
    if changed:
        save_config(config)

    return config


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
        self.current_code_blocks: list[str] = []

        # Wispr Flow heuristic -- tracks when the user last pressed Ctrl+C/X
        self.last_ctrl_c_time: float = 0.0

        # Persistent event loop for TTS
        self._loop: asyncio.AbstractEventLoop = asyncio.new_event_loop()

        # Track the last temp .mp3 so we can delete it before the next one
        self._prev_temp: str | None = None

        # Clear stale history from previous session
        if os.path.exists(HISTORY_FILE):
            try:
                os.remove(HISTORY_FILE)
            except Exception:
                pass

        # Apply saved volume on startup
        pygame.mixer.music.set_volume(self.config.get("volume", 1.0))

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

    # -- Controls ----------------------------------------------------------

    def set_voice(self, voice_id: str) -> None:
        """Change the active TTS voice and persist to config."""
        self.config["voice"] = voice_id
        save_config(self.config)

    def set_rate(self, rate_str: str) -> None:
        """Change the speech rate and persist to config. Changes apply on next chunk playback."""
        self.config["rate"] = rate_str
        save_config(self.config)

    def set_volume(self, vol: float) -> None:
        """Change immediate mixer volume (0.0 to 1.0) and save to config."""
        self.config["volume"] = vol
        pygame.mixer.music.set_volume(vol)
        save_config(self.config)

    def toggle_skip_code_blocks(self) -> None:
        """Toggle skipping of fenced code blocks and save to config."""
        current = self.config.get("skip_code_blocks", True)
        self.config["skip_code_blocks"] = not current
        save_config(self.config)

    # -- Playback controls -------------------------------------------------

    def stop_audio(self) -> None:
        """Stop any currently-playing audio (global kill-switch callback)."""
        try:
            if pygame.mixer.music.get_busy():
                pygame.mixer.music.stop()
        except Exception:
            pass

    def pause_audio(self) -> None:
        """Pause any currently-playing audio."""
        try:
            pygame.mixer.music.pause()
        except Exception:
            pass

    def resume_audio(self) -> None:
        """Resume any currently-paused audio."""
        try:
            pygame.mixer.music.unpause()
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
            if pygame.mixer.music.get_busy():
                pygame.mixer.music.pause()
        except Exception:
            pass

        while self.is_paused:
            if not self.q.empty():
                # New text in queue, we must abort current playback
                try:
                    pygame.mixer.music.stop()
                except Exception:
                    pass
                return True
            time.sleep(0.05)

        # Unpaused, resume playback if it was busy
        try:
            pygame.mixer.music.unpause()
        except Exception:
            pass

        return False

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

    def _split_text(self, text: str) -> list[str]:
        """Split text into natural sentence-based chunks of ~200 characters."""
        sentences = re.split(r"(?<=[.!?])\s+", text)
        chunks: list[str] = []
        current_chunk: list[str] = []
        current_len = 0
        
        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue
            if len(sentence) > 300:
                sub_sentences = re.split(r"(?<=[,;])\s+", sentence)
                for sub in sub_sentences:
                    sub = sub.strip()
                    if current_len + len(sub) > 200:
                        if current_chunk:
                            chunks.append(" ".join(current_chunk))
                        current_chunk = [sub]
                        current_len = len(sub)
                    else:
                        current_chunk.append(sub)
                        current_len += len(sub) + 1
                    
            else:
                if current_len + len(sentence) > 200:
                    if current_chunk:
                        chunks.append(" ".join(current_chunk))
                    current_chunk = [sentence]
                    current_len = len(sentence)
                else:
                    current_chunk.append(sentence)
                    current_len += len(sentence) + 1
                    
        if current_chunk:
            chunks.append(" ".join(current_chunk))
            
        return chunks if chunks else [text]

    def _generate_chunk(self, chunk_text: str) -> str | None:
        """Generate audio for a single text chunk with retry, resolving code blocks live."""
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
                loop = asyncio.new_event_loop()
                try:
                    loop.run_until_complete(
                        asyncio.wait_for(
                            self._generate_audio(resolved_text, temp_path),
                            timeout=30,
                        )
                    )
                finally:
                    loop.close()
                return temp_path
            except asyncio.TimeoutError:
                last_error = "TTS generation timed out (30s)"
            except Exception as e:
                last_error = str(e)
            if attempt < 3:
                time.sleep(0.5 * attempt)

        print(f"[ERROR] Chunk generation failed: {last_error}")
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

            # Stop current playback and clean up previous temp file
            self.stop_audio()
            self._cleanup_temp()

            # Log to history once for the full text
            self.log_history(cleaned)

            # Split into chunks for streamed playback
            chunks = self._split_text(cleaned)
            prev_temp: str | None = None
            aborted = False

            # First chunk is generated and played synchronously for <1s latency
            first_path = self._generate_chunk(chunks[0])
            if not first_path:
                continue

            if not self.q.empty():
                self._safe_remove(first_path)
                continue

            if self.is_paused:
                if self.check_pause_and_wait():
                    self._safe_remove(first_path)
                    continue

            pygame.mixer.music.load(first_path)
            pygame.mixer.music.play()
            prev_temp = first_path

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

                # Wait for current playback to finish
                while pygame.mixer.music.get_busy() or self.is_paused:
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

                if prev_temp:
                    try:
                        pygame.mixer.music.unload()
                    except Exception:
                        pass
                    self._safe_remove(prev_temp)

                pygame.mixer.music.load(next_path)
                pygame.mixer.music.play()
                prev_temp = next_path

            self._prev_temp = prev_temp

        try:
            executor.shutdown(wait=False)
        except Exception:
            pass

    # -- Clipboard monitor thread ------------------------------------------

    def clipboard_loop(self) -> None:
        """Monitor the clipboard for new copied text and queue it for TTS."""
        last_text: str = ""
        try:
            last_text = pyperclip.paste()
        except Exception:
            pass

        while True:
            try:
                time.sleep(0.3)
                try:
                    current_text = pyperclip.paste()
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
                print(f"[WARN] Clipboard monitor error: {e}")
                time.sleep(1)


# ---------------------------------------------------------------------------
# Tray icon image generators
# ---------------------------------------------------------------------------

def create_play_icon() -> Image.Image:
    """Red play button -- shown when Audify is PAUSED."""
    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(image)
    d.ellipse((4, 4, 60, 60), fill=(220, 53, 69))
    d.polygon([(24, 18), (24, 46), (46, 32)], fill="white")
    return image


def create_pause_icon() -> Image.Image:
    """Green pause button -- shown when Audify is ACTIVE."""
    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(image)
    d.ellipse((4, 4, 60, 60), fill=(40, 167, 69))
    d.rectangle((22, 20, 28, 44), fill="white")
    d.rectangle((36, 20, 42, 44), fill="white")
    return image


# ---------------------------------------------------------------------------
# Unified Audify Control Center GUI
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Beautiful Fluent Custom Widgets (Windows 11 Dark Mode Styled Canvas Controls)
# ---------------------------------------------------------------------------

class FluentSlider(tk.Canvas):
    """
    A modern, custom-drawn flat slider to replace tk.Scale.
    Features a thin track with a circular knob that glows on hover/drag.
    """
    def __init__(self, parent, from_=0, to=100, variable=None, command=None, bg="#1c1c1e", active_color="#0a84ff", track_color="#3a3a3c", knob_color="#ffffff", **kwargs):
        kwargs.setdefault("height", 24)
        kwargs.setdefault("highlightthickness", 0)
        kwargs.setdefault("bd", 0)
        kwargs.setdefault("bg", bg)
        kwargs.setdefault("cursor", "hand2")
        super().__init__(parent, **kwargs)
        
        self.from_ = from_
        self.to = to
        self.variable = variable
        self.command = command
        self.active_color = active_color
        self.track_color = track_color
        self.knob_color = knob_color
        
        self.value = from_
        if self.variable:
            self.value = self.variable.get()
            self.variable.trace_add("write", self._on_var_write)
            
        self.is_hovered = False
        self.is_dragging = False
        
        self.bind("<Configure>", self._draw)
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_click)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<ButtonRelease-1>", self._on_release)
        
    def _on_var_write(self, *args):
        try:
            val = self.variable.get()
            if val != self.value:
                self.value = max(self.from_, min(self.to, val))
                self._draw()
        except Exception:
            pass
            
    def _on_enter(self, event):
        self.is_hovered = True
        self._draw()
        
    def _on_leave(self, event):
        self.is_hovered = False
        self._draw()
        
    def _on_click(self, event):
        self.is_dragging = True
        self._update_val_from_x(event.x)
        
    def _on_drag(self, event):
        if self.is_dragging:
            self._update_val_from_x(event.x)
            
    def _on_release(self, event):
        self.is_dragging = False
        self._draw()
        
    def _update_val_from_x(self, x):
        w = self.winfo_width()
        if w <= 20:
            return
        margin = 10
        usable_w = w - 2 * margin
        pct = (x - margin) / usable_w
        pct = max(0.0, min(1.0, pct))
        new_val = self.from_ + pct * (self.to - self.from_)
        if isinstance(self.variable, tk.IntVar):
            new_val = int(round(new_val))
        
        self.value = new_val
        if self.variable:
            self.variable.set(new_val)
        if self.command:
            self.command(new_val)
        self._draw()
        
    def config(self, **kwargs):
        if "command" in kwargs:
            self.command = kwargs.pop("command")
        if "variable" in kwargs:
            self.variable = kwargs.pop("variable")
            self.value = self.variable.get()
            self.variable.trace_add("write", self._on_var_write)
        super().configure(**kwargs)
        self._draw()
        
    def configure(self, **kwargs):
        self.config(**kwargs)
        
    def _draw(self, event=None):
        self.delete("all")
        w = self.winfo_width()
        h = self.winfo_height()
        if w <= 1:
            return
            
        margin = 10
        usable_w = w - 2 * margin
        pct = (self.value - self.from_) / (self.to - self.from_) if self.to != self.from_ else 0.0
        knob_x = margin + pct * usable_w
        cy = h / 2
        
        # Track line
        self.create_line(margin, cy, w - margin, cy, fill=self.track_color, width=4, capstyle="round")
        # Active filled line
        if knob_x > margin:
            self.create_line(margin, cy, knob_x, cy, fill=self.active_color, width=4, capstyle="round")
            
        # Hover glow/ring
        if self.is_hovered or self.is_dragging:
            self.create_oval(knob_x - 8, cy - 8, knob_x + 8, cy + 8, fill="", outline=self.active_color, width=2)
            
        # Slider knob
        self.create_oval(knob_x - 6, cy - 6, knob_x + 6, cy + 6, fill=self.knob_color, outline="#2c2c2e", width=1)


class FluentToggle(tk.Canvas):
    """
    A beautiful modern iOS/Windows-style capsule pill switch.
    Transitions color smoothly on active state change.
    """
    def __init__(self, parent, variable=None, command=None, bg="#1c1c1e", active_color="#0a84ff", track_color="#3a3a3c", knob_color="#ffffff", **kwargs):
        kwargs.setdefault("width", 38)
        kwargs.setdefault("height", 20)
        kwargs.setdefault("highlightthickness", 0)
        kwargs.setdefault("bd", 0)
        kwargs.setdefault("bg", bg)
        kwargs.setdefault("cursor", "hand2")
        super().__init__(parent, **kwargs)
        
        self.variable = variable
        self.command = command
        self.active_color = active_color
        self.track_color = track_color
        self.knob_color = knob_color
        
        self.state = False
        if self.variable:
            self.state = bool(self.variable.get())
            self.variable.trace_add("write", self._on_var_write)
            
        self.bind("<Button-1>", self._on_click)
        self.bind("<Configure>", self._draw)
        
    def _on_var_write(self, *args):
        try:
            val = bool(self.variable.get())
            if val != self.state:
                self.state = val
                self._draw()
        except Exception:
            pass
            
    def _on_click(self, event):
        self.state = not self.state
        if self.variable:
            self.variable.set(self.state)
        if self.command:
            self.command()
        self._draw()
        
    def _draw(self, event=None):
        self.delete("all")
        w = self.winfo_width()
        h = self.winfo_height()
        
        r = h / 2
        fill_color = self.active_color if self.state else self.track_color
        
        # Draw pill capsule
        self.create_oval(1, 1, h - 1, h - 1, fill=fill_color, outline=fill_color)
        self.create_oval(w - h + 1, 1, w - 1, h - 1, fill=fill_color, outline=fill_color)
        self.create_rectangle(r, 1, w - r, h - 1, fill=fill_color, outline=fill_color)
        
        # Draw knob
        knob_d = h - 6
        if self.state:
            kx1 = w - h + 3
            kx2 = w - 3
        else:
            kx1 = 3
            kx2 = h - 3
            
        ky1 = 3
        ky2 = h - 3
        self.create_oval(kx1, ky1, kx2, ky2, fill=self.knob_color, outline="", width=0)


def create_fluent_entry(parent, textvariable=None, width=20, bg="#1c1c1e", border_color="#3a3a3c", active_color="#0a84ff", fg="#ffffff", **kwargs) -> tuple[tk.Frame, tk.Entry]:
    """
    Wraps standard tk.Entry in a dual-frame structure for beautiful, high-contrast flat borders with focus glow.
    """
    outer = tk.Frame(parent, bg=border_color, bd=0, highlightthickness=1, highlightbackground=border_color)
    inner = tk.Frame(outer, bg=bg, bd=0)
    inner.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)
    
    entry = tk.Entry(
        inner,
        textvariable=textvariable,
        width=width,
        bg=bg,
        fg=fg,
        insertbackground=fg,
        font=("Segoe UI", 10),
        bd=0,
        relief="flat",
        highlightthickness=0,
        **kwargs
    )
    entry.pack(fill=tk.BOTH, expand=True, padx=8, pady=6)
    
    def on_focus_in(e):
        outer.config(highlightbackground=active_color)
    def on_focus_out(e):
        outer.config(highlightbackground=border_color)
        
    entry.bind("<FocusIn>", on_focus_in)
    entry.bind("<FocusOut>", on_focus_out)
    
    return outer, entry


def show_dictionary_ui(daemon: TTSDaemon) -> None:
    """Open a sleek, modern, Windows 11-themed Tkinter window to manage both settings & pronunciation rules."""
    root = tk.Tk()
    root.title("Audify Control Center")
    
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
        
    root.attributes("-topmost", True)

    # Styling Tokens
    BG_COLOR = "#1c1c1e"          # Sleek modern dark background (Windows 11 Dark Mode)
    CARD_COLOR = "#2c2c2e"        # Slate grey container card background
    ACCENT_COLOR = "#0a84ff"      # Windows active blue accent
    ACCENT_HOVER = "#2693ff"      # Glowing blue hover accent
    TEXT_COLOR = "#ffffff"        # Clean bright white text
    TEXT_MUTED = "#8e8e93"        # Secondary secondary text
    BORDER_COLOR = "#3a3a3c"      # Dark control borders

    root.configure(bg=BG_COLOR)

    # Dark Theme Dropdown skins (TCombobox listbox tricks)
    root.option_add("*TCombobox*Listbox.background", CARD_COLOR)
    root.option_add("*TCombobox*Listbox.foreground", TEXT_COLOR)
    root.option_add("*TCombobox*Listbox.selectBackground", ACCENT_COLOR)
    root.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")

    # Precise screen centering with premium bounds & resizability
    root.update_idletasks()
    try:
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
        if not hwnd:
            hwnd = root.winfo_id()
        rendering = ctypes.c_int(1)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(rendering), ctypes.sizeof(rendering))
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 19, ctypes.byref(rendering), ctypes.sizeof(rendering))
    except Exception:
        pass
    width = 1000
    height = 600
    x = (root.winfo_screenwidth() // 2) - (width // 2)
    y = (root.winfo_screenheight() // 2) - (height // 2)
    root.geometry(f"{width}x{height}+{x}+{y}")
    root.resizable(True, True)
    root.minsize(850, 520)

    # TTK Configuration styles
    style = ttk.Style()
    style.theme_use("clam")
    style.configure(".", background=BG_COLOR, foreground=TEXT_COLOR)
    style.configure("TFrame", background=BG_COLOR)
    style.configure("TLabel", background=BG_COLOR, foreground=TEXT_COLOR, font=("Segoe UI", 10))
    style.configure("Header.TLabel", font=("Segoe UI", 16, "bold"), foreground="#ffffff")
    style.configure("Sub.TLabel", font=("Segoe UI", 9), foreground=TEXT_MUTED)
    style.configure("InputLabel.TLabel", font=("Segoe UI Semibold", 9), foreground=TEXT_COLOR, background=CARD_COLOR)

    # Sidebar frame style
    style.configure("Sidebar.TFrame", background=CARD_COLOR)

    # Custom dark combobox style
    style.configure(
        "Dark.TCombobox",
        fieldbackground=BG_COLOR,
        background=CARD_COLOR,
        foreground=TEXT_COLOR,
        bordercolor=BORDER_COLOR,
        arrowcolor=TEXT_COLOR
    )
    style.map(
        "Dark.TCombobox",
        fieldbackground=[("readonly", BG_COLOR)],
        foreground=[("readonly", TEXT_COLOR)]
    )

    style.configure(
        "Treeview",
        background=CARD_COLOR,
        foreground=TEXT_COLOR,
        fieldbackground=CARD_COLOR,
        rowheight=28,
        borderwidth=0,
        font=("Segoe UI", 10),
    )
    style.configure(
        "Treeview.Heading",
        background=BORDER_COLOR,
        foreground=TEXT_COLOR,
        font=("Segoe UI Semibold", 10),
        borderwidth=0,
    )
    style.map("Treeview.Heading", background=[('active', '#48484a')])
    style.map("Treeview", background=[('selected', ACCENT_COLOR)], foreground=[('selected', '#ffffff')])
    
    # Configure treeview frame border removal
    root.option_add("*Treeview.borderWidth", 0)
    root.option_add("*Treeview.highlightThickness", 0)

    # Minimalist dark scrollbar layout (hides standard arrow elements)
    style.layout("Vertical.TScrollbar", [
        ('Vertical.Scrollbar.trough', {
            'children': [
                ('Vertical.Scrollbar.thumb', {
                    'expand': '1',
                    'sticky': 'nswe'
                })
            ],
            'sticky': 'ns'
        })
    ])
    style.configure("Vertical.TScrollbar",
                    background="#48484a",
                    troughcolor="#1c1c1e",
                    bordercolor="#1c1c1e",
                    arrowcolor="#1c1c1e",
                    lightcolor="#48484a",
                    darkcolor="#48484a",
                    gripcount=0)
    style.map("Vertical.TScrollbar",
              background=[('active', "#5a5a5c"), ('pressed', "#6c6c6e")])

    dict_ref: dict[str, str] = daemon.config.setdefault("pronunciation_dict", {})

    # Outer master padding layout
    main_frame = ttk.Frame(root, padding="20")
    main_frame.pack(fill=tk.BOTH, expand=True)

    # -----------------------------------------------------------------------
    # LEFT COLUMN: Settings Panel (Width: 260px)
    # -----------------------------------------------------------------------
    sidebar_border = tk.Frame(main_frame, bg=BORDER_COLOR, bd=0)
    sidebar_border.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 20))
    sidebar = tk.Frame(sidebar_border, bg=CARD_COLOR, bd=0)
    sidebar.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)
    
    sidebar_inner = ttk.Frame(sidebar, padding="16", style="Sidebar.TFrame")
    sidebar_inner.pack(fill=tk.BOTH, expand=True)

    # Header inside Settings
    ttk.Label(sidebar_inner, text="Audify Settings", font=("Segoe UI", 13, "bold"), background=CARD_COLOR).pack(anchor=tk.W, pady=(0, 15))

    # Live Volume Controller
    ttk.Label(sidebar_inner, text="Volume", font=("Segoe UI Semibold", 10), foreground=TEXT_COLOR, background=CARD_COLOR).pack(anchor=tk.W, pady=(0, 4))
    
    vol_frame = ttk.Frame(sidebar_inner, style="Sidebar.TFrame")
    vol_frame.pack(fill=tk.X, pady=(0, 16))
    
    vol_percent = int(daemon.config.get("volume", 1.0) * 100)
    vol_var = tk.IntVar(value=vol_percent)
    
    # Custom Fluent Volume Slider
    vol_slider = FluentSlider(
        vol_frame,
        from_=0,
        to=100,
        variable=vol_var,
        bg=CARD_COLOR,
        active_color=ACCENT_COLOR,
        track_color=BG_COLOR,
        knob_color="#ffffff"
    )
    vol_slider.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))
    
    vol_label = ttk.Label(vol_frame, text=f"{vol_percent}%", font=("Segoe UI Semibold", 9), background=CARD_COLOR, width=5, anchor=tk.E)
    vol_label.pack(side=tk.RIGHT)
    
    def on_volume_change(val):
        vol_fraction = float(val) / 100.0
        daemon.set_volume(vol_fraction)
        vol_label.config(text=f"{int(float(val))}%")
        
    vol_slider.config(command=on_volume_change)

    # Voice Combobox
    ttk.Label(sidebar_inner, text="Speech Voice", font=("Segoe UI Semibold", 10), foreground=TEXT_COLOR, background=CARD_COLOR).pack(anchor=tk.W, pady=(0, 4))
    
    voice_names = list(VOICES.keys())
    current_voice_id = daemon.config.get("voice", "en-US-JennyNeural")
    current_voice_name = voice_names[0]
    for name, v_id in VOICES.items():
        if v_id == current_voice_id:
            current_voice_name = name
            break
            
    voice_var = tk.StringVar(value=current_voice_name)
    voice_combo = ttk.Combobox(sidebar_inner, textvariable=voice_var, values=voice_names, state="readonly", style="Dark.TCombobox")
    voice_combo.pack(fill=tk.X, pady=(0, 16))
    
    def on_voice_select(event):
        chosen_name = voice_var.get()
        chosen_id = VOICES[chosen_name]
        daemon.set_voice(chosen_id)
        
    voice_combo.bind("<<ComboboxSelected>>", on_voice_select)

    # Speed Rate Combobox
    ttk.Label(sidebar_inner, text="Speech Speed", font=("Segoe UI Semibold", 10), foreground=TEXT_COLOR, background=CARD_COLOR).pack(anchor=tk.W, pady=(0, 4))
    
    rate_names = list(RATES.keys())
    current_rate_val = daemon.config.get("rate", "+50%")
    current_rate_name = rate_names[3] # default 1.5x (Fast)
    for name, r_str in RATES.items():
        if r_str == current_rate_val:
            current_rate_name = name
            break
            
    rate_var = tk.StringVar(value=current_rate_name)
    rate_combo = ttk.Combobox(sidebar_inner, textvariable=rate_var, values=rate_names, state="readonly", style="Dark.TCombobox")
    rate_combo.pack(fill=tk.X, pady=(0, 16))
    
    def on_rate_select(event):
        chosen_name = rate_var.get()
        chosen_val = RATES[chosen_name]
        daemon.set_rate(chosen_val)
        
    rate_combo.bind("<<ComboboxSelected>>", on_rate_select)

    # Live Skip Code Blocks toggle switch
    skip_var = tk.BooleanVar(value=daemon.config.get("skip_code_blocks", True))
    
    def on_toggle_skip():
        daemon.toggle_skip_code_blocks()
        skip_var.set(daemon.config.get("skip_code_blocks", True))
        
    # Custom iOS/Windows-style Fluent Toggle Switch
    skip_frame = ttk.Frame(sidebar_inner, style="Sidebar.TFrame")
    skip_frame.pack(anchor=tk.W, fill=tk.X, pady=(5, 10))
    
    skip_chk = FluentToggle(
        skip_frame,
        variable=skip_var,
        command=on_toggle_skip,
        bg=CARD_COLOR,
        active_color=ACCENT_COLOR,
        track_color=BG_COLOR,
        knob_color="#ffffff"
    )
    skip_chk.pack(side=tk.LEFT, padx=(0, 10))
    
    skip_lbl = ttk.Label(skip_frame, text="Skip Code Blocks", font=("Segoe UI Semibold", 9), background=CARD_COLOR, foreground=TEXT_COLOR)
    skip_lbl.pack(side=tk.LEFT)
    
    def toggle_from_lbl(event):
        skip_chk._on_click(None)
    skip_lbl.bind("<Button-1>", toggle_from_lbl)

    # Version Indicator Card at Bottom Left
    version_card_border = tk.Frame(sidebar_inner, bg=BORDER_COLOR, bd=0)
    version_card_border.pack(fill=tk.X, side=tk.BOTTOM, pady=(15, 0))
    version_card = tk.Frame(version_card_border, bg=BG_COLOR, bd=0)
    version_card.pack(fill=tk.X, padx=1, pady=1)
    version_lbl = tk.Label(version_card, text=f"Audify v{__version__}", font=("Segoe UI Semibold", 8), bg=BG_COLOR, fg=TEXT_MUTED)
    version_lbl.pack(pady=6)

    # -----------------------------------------------------------------------
    # RIGHT COLUMN: Dictionary Panel
    # -----------------------------------------------------------------------
    dict_frame = ttk.Frame(main_frame)
    dict_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

    # Header Section
    header_frame = ttk.Frame(dict_frame)
    header_frame.pack(fill=tk.X, pady=(0, 12))
    
    ttk.Label(header_frame, text="Pronunciation Dictionary", style="Header.TLabel").pack(anchor=tk.W)
    ttk.Label(
        header_frame,
        text="Customize speech pronunciation rules for technical terms and shorthand.",
        style="Sub.TLabel"
    ).pack(anchor=tk.W, pady=(2, 0))

    # Search Bar Section
    search_frame = ttk.Frame(dict_frame)
    search_frame.pack(fill=tk.X, pady=(0, 10))
    
    ttk.Label(search_frame, text="Search Rules:", font=("Segoe UI Semibold", 9), foreground=TEXT_COLOR).pack(side=tk.LEFT, padx=(0, 8))
    
    search_var = tk.StringVar()
    # Custom Fluent Entry wrapper for Search Bar
    search_entry_frame, search_entry = create_fluent_entry(
        search_frame,
        textvariable=search_var,
        bg=CARD_COLOR,
        border_color=BORDER_COLOR,
        active_color=ACCENT_COLOR
    )
    search_entry_frame.pack(side=tk.LEFT, fill=tk.X, expand=True)
    
    # Right-Click Context menu helper
    def add_context_menu(widget: tk.Entry) -> None:
        menu = tk.Menu(widget, tearoff=0, bg=CARD_COLOR, fg=TEXT_COLOR, selectcolor=ACCENT_COLOR, activebackground=ACCENT_COLOR, activeforeground="#ffffff", bd=1, relief="solid")
        menu.add_command(label="Cut", command=lambda: widget.event_generate("<<Cut>>"))
        menu.add_command(label="Copy", command=lambda: widget.event_generate("<<Copy>>"))
        menu.add_command(label="Paste", command=lambda: widget.event_generate("<<Paste>>"))
        menu.add_separator()
        menu.add_command(label="Select All", command=lambda: widget.select_range(0, tk.END))
        
        def show_menu(event) -> str:
            menu.tk_popup(event.x_root, event.y_root)
            return "break"
            
        widget.bind("<Button-3>", show_menu)

    add_context_menu(search_entry)

    # Treeview Table Section (Wrapped in a 1px border container)
    table_container_border = tk.Frame(dict_frame, bg=BORDER_COLOR, bd=0)
    table_container_border.pack(fill=tk.BOTH, expand=True, pady=(0, 15))
    table_container = tk.Frame(table_container_border, bg=CARD_COLOR, bd=0)
    table_container.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)

    columns = ("row_num", "word", "pronunciation")
    tree = ttk.Treeview(table_container, columns=columns, show="headings", height=8, style="Treeview")
    
    tree.heading("row_num", text="#")
    tree.heading("word", text="Original Word")
    tree.heading("pronunciation", text="Spoken As")
    
    tree.column("row_num", width=55, minwidth=55, stretch=False, anchor=tk.CENTER)
    tree.column("word", width=180, minwidth=120)
    tree.column("pronunciation", width=250, minwidth=180)

    # Sleek dark scrollbar
    scrollbar = ttk.Scrollbar(table_container, orient=tk.VERTICAL, command=tree.yview, style="Vertical.TScrollbar")
    tree.configure(yscrollcommand=scrollbar.set)
    
    tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

    # Alphabetical dynamic filtering tree loader (1-based index based on search matches)
    def refresh_tree(query: str = "") -> None:
        for row in tree.get_children():
            tree.delete(row)
            
        sorted_keys = sorted(dict_ref.keys(), key=lambda s: s.lower())
        idx = 1
        for w in sorted_keys:
            s = dict_ref[w]
            if query:
                if query not in w.lower() and query not in s.lower():
                    continue
            tree.insert("", tk.END, values=(idx, w, s))
            idx += 1

    def on_search(*args) -> None:
        refresh_tree(search_var.get().strip().lower())

    search_var.trace_add("write", on_search)

    # Horizontal Rule Editor Card (1px border fluent container)
    input_card = tk.Frame(dict_frame, bg=BORDER_COLOR, bd=0)
    input_card.pack(fill=tk.X, pady=(0, 15))
    
    inner_card = tk.Frame(input_card, bg=CARD_COLOR, bd=0)
    inner_card.pack(fill=tk.X, padx=1, pady=1)
    
    grid_container = ttk.Frame(inner_card, padding="12", style="Card.TFrame")
    grid_container.pack(fill=tk.X)
    grid_container.grid_columnconfigure(1, weight=1)
    grid_container.grid_columnconfigure(3, weight=1)
    style.configure("Card.TFrame", background=CARD_COLOR)

    # Word Input
    ttk.Label(grid_container, text="Word:", style="InputLabel.TLabel").grid(row=0, column=0, sticky=tk.W, padx=(0, 6))
    word_var = tk.StringVar()
    word_entry_frame, word_entry = create_fluent_entry(
        grid_container,
        textvariable=word_var,
        width=14,
        bg="#1c1c1e",
        border_color=BORDER_COLOR,
        active_color=ACCENT_COLOR
    )
    word_entry_frame.grid(row=0, column=1, padx=(0, 15), sticky="ew")
    add_context_menu(word_entry)

    # Spoken Input
    ttk.Label(grid_container, text="Spoken As:", style="InputLabel.TLabel").grid(row=0, column=2, sticky=tk.W, padx=(0, 6))
    spoken_var = tk.StringVar()
    spoken_entry_frame, spoken_entry = create_fluent_entry(
        grid_container,
        textvariable=spoken_var,
        width=16,
        bg="#1c1c1e",
        border_color=BORDER_COLOR,
        active_color=ACCENT_COLOR
    )
    spoken_entry_frame.grid(row=0, column=3, padx=(0, 15), sticky="ew")
    add_context_menu(spoken_entry)

    # Binds table click to populate inputs live
    def on_tree_select(event) -> None:
        selected = tree.selection()
        if selected:
            row_item = tree.item(selected[0])
            vals = row_item["values"]
            if len(vals) >= 3:
                word_var.set(vals[1])
                spoken_var.set(vals[2])

    tree.bind("<<TreeviewSelect>>", on_tree_select)

    # Premium Flat buttons with active hover transitions
    def create_modern_btn(parent, text, command, primary=False):
        btn_bg = ACCENT_COLOR if primary else "#3a3a3c"
        btn_active = ACCENT_HOVER if primary else "#48484a"
        
        btn = tk.Button(
            parent,
            text=text,
            command=command,
            bg=btn_bg,
            fg="#ffffff",
            activebackground=btn_active,
            activeforeground="#ffffff",
            font=("Segoe UI Semibold", 9),
            bd=0,
            relief="flat",
            padx=14,
            pady=5,
            cursor="hand2"
        )
        
        def on_enter(e):
            btn.config(bg=btn_active)
        def on_leave(e):
            btn.config(bg=btn_bg)
            
        btn.bind("<Enter>", on_enter)
        btn.bind("<Leave>", on_leave)
        return btn

    def add_entry() -> None:
        w = word_var.get().strip()
        s = spoken_var.get().strip()
        if w and s:
            dict_ref[w] = s
            daemon.config["pronunciation_dict"] = dict_ref
            save_config(daemon.config)
            refresh_tree(search_var.get().strip().lower())
            word_var.set("")
            spoken_var.set("")
            word_entry.focus()

    add_btn = create_modern_btn(grid_container, "Add / Update", add_entry, primary=True)
    add_btn.grid(row=0, column=4, sticky=tk.E)

    # Bottom Actions Row
    action_frame = ttk.Frame(dict_frame)
    action_frame.pack(fill=tk.X)

    def remove_selected() -> None:
        selected = tree.selection()
        if selected:
            row_item = tree.item(selected[0])
            vals = row_item["values"]
            if len(vals) >= 3:
                w = vals[1]
                if w in dict_ref:
                    del dict_ref[w]
                    daemon.config["pronunciation_dict"] = dict_ref
                    save_config(daemon.config)
                    refresh_tree(search_var.get().strip().lower())
                    word_var.set("")
                    spoken_var.set("")

    remove_btn = create_modern_btn(action_frame, "Remove Selected", remove_selected, primary=False)
    remove_btn.pack(side=tk.LEFT)

    close_btn = create_modern_btn(action_frame, "Close Window", root.destroy, primary=False)
    close_btn.pack(side=tk.RIGHT)

    # Continuous Active Value Synchronization loop (from Tray menu changes)
    def sync_gui_values():
        if not root.winfo_exists():
            return
            
        # Synchronize Volume Slider
        cur_vol = int(daemon.config.get("volume", 1.0) * 100)
        if vol_var.get() != cur_vol:
            vol_var.set(cur_vol)
            vol_label.config(text=f"{cur_vol}%")
            
        # Synchronize Voice Combobox
        cur_voice_id = daemon.config.get("voice", "en-US-JennyNeural")
        for name, v_id in VOICES.items():
            if v_id == cur_voice_id:
                if voice_var.get() != name:
                    voice_var.set(name)
                break
                
        # Synchronize Speech Speed Combobox
        cur_rate_val = daemon.config.get("rate", "+50%")
        for name, r_str in RATES.items():
            if r_str == cur_rate_val:
                if rate_var.get() != name:
                    rate_var.set(name)
                break
                
        # Synchronize Code Block checkbox
        cur_skip = daemon.config.get("skip_code_blocks", True)
        if skip_var.get() != cur_skip:
            skip_var.set(cur_skip)
            
        root.after(800, sync_gui_values)

    refresh_tree()
    search_entry.focus()
    sync_gui_values()
    root.mainloop()

def setup_tray() -> None:
    """Create the TTSDaemon, wire up the system-tray menu, and run."""
    daemon = TTSDaemon()

    # Launch worker loop threads
    threading.Thread(target=daemon.worker_loop, daemon=True).start()
    threading.Thread(target=daemon.clipboard_loop, daemon=True).start()

    def on_toggle_pause(icon: pystray.Icon, item_action: item) -> None:
        daemon.is_paused = not daemon.is_paused
        if daemon.is_paused:
            daemon.pause_audio()
            icon.icon = create_play_icon()
            icon.title = "Audify (PAUSED - Click to Resume)"
        else:
            daemon.resume_audio()
            icon.icon = create_pause_icon()
            icon.title = "Audify (Active - Click to Pause)"

    def on_read_clipboard(icon: pystray.Icon, item_action: item) -> None:
        daemon.last_spoken = ""  # Force re-read even if duplicate clipboard copy
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
        daemon.q.put(None)
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

    def make_volume_setter(vol: float):
        return lambda icon, item_action: daemon.set_volume(vol)

    def on_toggle_code_blocks(icon: pystray.Icon, item_action: item) -> None:
        daemon.toggle_skip_code_blocks()

    # -- Tray options generation from global speech specs --------------------
    voice_menu_items = [
        item(
            label,
            make_voice_setter(v_id),
            checked=lambda i, vid=v_id: daemon.config.get("voice") == vid,
            radio=True,
        )
        for label, v_id in VOICES.items()
    ]

    rate_menu_items = [
        item(
            label,
            make_rate_setter(r_str),
            checked=lambda i, r=r_str: daemon.config.get("rate") == r,
            radio=True,
        )
        for label, r_str in RATES.items()
    ]

    volume_menu_items = [
        item(
            label,
            make_volume_setter(v),
            checked=lambda i, vol=v: abs(daemon.config.get("volume", 1.0) - vol) < 0.05,
            radio=True,
        )
        for label, v in VOLUMES.items()
    ]

    # -- Build the tray icon -----------------------------------------------
    icon_image = create_pause_icon()
    tray = pystray.Icon(
        "Audify",
        icon_image,
        "Audify (Active - Click to Pause)",
        menu=Menu(
            item(
                lambda text: "▶ Resume Listening"
                if daemon.is_paused
                else "⏸ Pause Listening",
                on_toggle_pause,
                default=True,
            ),
            Menu.SEPARATOR,
            item("Read Current Clipboard", on_read_clipboard),
            item("Copy Cleaned Text of Last Read", on_copy_last),
            Menu.SEPARATOR,
            item("Audify Control Center...", on_open_dict),
            Menu.SEPARATOR,
            item("Voice", Menu(*voice_menu_items)),
            item("Speed", Menu(*rate_menu_items)),
            item("Volume", Menu(*volume_menu_items)),
            Menu.SEPARATOR,
            item(
                lambda text: "☑ Skip Code Blocks"
                if daemon.config.get("skip_code_blocks", True)
                else "☐ Skip Code Blocks",
                on_toggle_code_blocks,
            ),
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
    _ERROR_ALREADY_EXISTS = 0xB7
    _mutex = ctypes.windll.kernel32.CreateMutexW(
        None, False, "Global\\AudifyTTSSingleInstance"
    )
    if ctypes.windll.kernel32.GetLastError() == _ERROR_ALREADY_EXISTS:
        ctypes.windll.user32.MessageBoxW(
            0,
            "Audify is already running in the system tray.\n\n"
            "Look for the green/red circle icon near the clock.",
            "Audify — Already Running",
            0x40,  # MB_ICONINFORMATION
        )
        sys.exit(0)

    setup_tray()
