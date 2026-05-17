import sys
import os
import time
import asyncio
import tkinter as tk
from tkinter import ttk, messagebox
import tempfile
import threading
import queue
import re
import json
import pyperclip
import keyboard
import pystray
from pystray import MenuItem as item
from pystray import Menu
from PIL import Image, ImageDraw
from pynput import mouse as pynput_mouse
from clean_text import markdown_to_text
import edge_tts
import pygame
import ctypes

# Make the app DPI-aware to fix blurry system tray menus on 125% scaling
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    pass

# Hide pygame welcome message
os.environ['PYGAME_HIDE_SUPPORT_PROMPT'] = "hide"
pygame.mixer.init()

CONFIG_FILE = "config.json"
HISTORY_FILE = "history.log"

DEFAULT_CONFIG = {
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
        "VS Code": "V S Code"
    }
}

def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                config = json.load(f)
                return {**DEFAULT_CONFIG, **config}
        except Exception as e:
            print(f"[WARN] Error loading config: {e}. Using defaults.")
    return DEFAULT_CONFIG.copy()

def save_config(config):
    try:
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=4)
    except Exception as e:
        print(f"[WARN] Error saving config: {e}")

def is_just_url(text):
    text = text.strip()
    pattern = r'^(https?:\/\/)?(www\.)?([a-zA-Z0-9-]+\.)+[a-zA-Z]{2,}(:\d+)?(\/[^\s]*)?$'
    return bool(re.match(pattern, text))

class TTSDaemon:
    def __init__(self):
        self.q = queue.Queue()
        self.last_spoken = ""
        self.config = load_config()
        self.is_paused = False
        
        # Wispr Flow Heuristic tracking
        self.last_ctrl_c_time = 0.0
        
        # Setup history file - clear it at startup
        if os.path.exists(HISTORY_FILE):
            try:
                os.remove(HISTORY_FILE)
            except:
                pass
            
        # Bind global hotkeys
        try:
            # Kill-switch
            keyboard.add_hotkey(self.config.get("stop_hotkey", "ctrl+alt+s"), self.stop_audio)
            
            # Hooks for Ctrl+C and Ctrl+X to detect manual copying (Wispr Flow fix)
            keyboard.add_hotkey('ctrl+c', self.mark_manual_copy)
            keyboard.add_hotkey('ctrl+x', self.mark_manual_copy)
            
            # Mouse hook to allow Right-Click -> Copy
            self.mouse_listener = pynput_mouse.Listener(on_click=self.on_mouse_click)
            self.mouse_listener.start()
        except Exception as e:
            print(f"[WARN] Failed to bind hotkey: {e}")

    def on_mouse_click(self, x, y, button, pressed):
        if pressed:
            self.last_ctrl_c_time = time.time()

    def mark_manual_copy(self):
        self.last_ctrl_c_time = time.time()

    def set_voice(self, voice_id):
        self.config["voice"] = voice_id
        save_config(self.config)

    def set_rate(self, rate_str):
        self.config["rate"] = rate_str
        save_config(self.config)

    def stop_audio(self):
        """Callback for the global kill-switch hotkey."""
        if pygame.mixer.music.get_busy():
            pygame.mixer.music.stop()

    def log_history(self, text):
        """Appends the spoken text to the session history log."""
        try:
            with open(HISTORY_FILE, 'a', encoding='utf-8') as f:
                f.write(text + "\n\n---\n\n")
        except:
            pass

    def worker_loop(self):
        while True:
            text = self.q.get()
            
            # Drain queue
            while not self.q.empty():
                text = self.q.get()
                
            if text is None:  # Exit signal
                break
                
            if self.is_paused:
                continue
                
            cleaned = markdown_to_text(text, self.config.get("pronunciation_dict", {}))
            if not cleaned.strip():
                continue
                
            if cleaned == self.last_spoken:
                continue
            self.last_spoken = cleaned
                
            self.stop_audio()
                
            try:
                temp_fd, temp_path = tempfile.mkstemp(suffix=".mp3")
                os.close(temp_fd)
                
                asyncio.run(self._generate_audio(cleaned, temp_path))
                
                # If a new item arrived or we were paused while generating, abort playback
                if not self.q.empty() or self.is_paused:
                    os.remove(temp_path)
                    continue
                
                # Log to history right before playing
                self.log_history(cleaned)
                    
                pygame.mixer.music.load(temp_path)
                pygame.mixer.music.play()
                
            except Exception as e:
                print(f"[ERROR] TTS Worker Exception: {e}")

    async def _generate_audio(self, text, output_file):
        voice = self.config.get("voice", "en-US-JennyNeural")
        rate = self.config.get("rate", "+50%")
        communicate = edge_tts.Communicate(text, voice, rate=rate)
        await communicate.save(output_file)

    def clipboard_loop(self):
        last_text = pyperclip.paste()
        try:
            while True:
                time.sleep(0.3)
                current_text = pyperclip.paste()
                if current_text != last_text:
                    last_text = current_text
                    
                    if self.is_paused or not current_text.strip():
                        continue
                        
                    if is_just_url(current_text):
                        continue
                        
                    # Wispr Flow Heuristic: Only read if Ctrl+C or Ctrl+X was pressed in the last 1.5 seconds.
                    time_since_copy = time.time() - self.last_ctrl_c_time
                    if time_since_copy <= 1.5:
                        self.q.put(current_text)
                    else:
                        print("[INFO] Clipboard changed but Ctrl+C wasn't pressed. Ignoring (Wispr Flow heuristic).")
        except Exception:
            pass

def create_play_icon():
    """Generates a Red Play button icon (indicating it is PAUSED and waiting for you to click Play)."""
    image = Image.new('RGBA', (64, 64), (0, 0, 0, 0)) # Transparent
    d = ImageDraw.Draw(image)
    # Circle background
    d.ellipse((4, 4, 60, 60), fill=(220, 53, 69)) # Red circle
    # White triangle (Play symbol)
    d.polygon([(24, 18), (24, 46), (46, 32)], fill='white')
    return image

def create_pause_icon():
    """Generates a Green Pause button icon (indicating it is ACTIVE and you click to pause it)."""
    image = Image.new('RGBA', (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(image)
    # Circle background
    d.ellipse((4, 4, 60, 60), fill=(40, 167, 69)) # Green circle
    # Two white vertical bars (Pause symbol)
    d.rectangle((22, 20, 28, 44), fill='white')
    d.rectangle((36, 20, 42, 44), fill='white')
    return image

def show_dictionary_ui(daemon):
    root = tk.Tk()
    root.title("Pronunciation Dictionary")
    root.geometry("450x350")
    
    # Force window to top and set tool window style
    root.attributes("-topmost", True)
    
    dict_ref = daemon.config.get("pronunciation_dict", {})
    
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
    
    def refresh_tree():
        for item in tree.get_children():
            tree.delete(item)
        for k, v in dict_ref.items():
            tree.insert("", tk.END, values=(k, v))
            
    refresh_tree()
    
    # Inputs
    input_frame = ttk.Frame(frame, padding="5 10 0 0")
    input_frame.pack(fill=tk.X)
    
    ttk.Label(input_frame, text="Word:").grid(row=0, column=0, padx=5, pady=5)
    word_var = tk.StringVar()
    ttk.Entry(input_frame, textvariable=word_var, width=15).grid(row=0, column=1, padx=5, pady=5)
    
    ttk.Label(input_frame, text="Spoken:").grid(row=0, column=2, padx=5, pady=5)
    spoken_var = tk.StringVar()
    ttk.Entry(input_frame, textvariable=spoken_var, width=20).grid(row=0, column=3, padx=5, pady=5)
    
    # Buttons
    btn_frame = ttk.Frame(frame, padding="0 5 0 0")
    btn_frame.pack(fill=tk.X)
    
    def add_entry():
        w, s = word_var.get().strip(), spoken_var.get().strip()
        if w and s:
            dict_ref[w] = s
            daemon.config["pronunciation_dict"] = dict_ref
            save_config(daemon.config)
            refresh_tree()
            word_var.set("")
            spoken_var.set("")
            
    def remove_entry():
        selected = tree.selection()
        if selected:
            item = tree.item(selected[0])
            w = item['values'][0]
            if w in dict_ref:
                del dict_ref[w]
                daemon.config["pronunciation_dict"] = dict_ref
                save_config(daemon.config)
                refresh_tree()

    ttk.Button(input_frame, text="Add/Update", command=add_entry).grid(row=0, column=4, padx=5, pady=5)
    ttk.Button(btn_frame, text="Remove Selected", command=remove_entry).pack(side=tk.LEFT, padx=5)
    ttk.Button(btn_frame, text="Close", command=root.destroy).pack(side=tk.RIGHT, padx=5)
    
    root.mainloop()

def setup_tray():
    daemon = TTSDaemon()
    
    # Start background threads
    threading.Thread(target=daemon.worker_loop, daemon=True).start()
    threading.Thread(target=daemon.clipboard_loop, daemon=True).start()

    def on_toggle_pause(icon, item_action):
        daemon.is_paused = not daemon.is_paused
        daemon.stop_audio()
        # Change icon shape and color based on state
        if daemon.is_paused:
            icon.icon = create_play_icon()
            icon.title = "Audify (PAUSED - Click to Resume)"
        else:
            icon.icon = create_pause_icon()
            icon.title = "Audify (Active - Click to Pause)"

    def on_read_clipboard(icon, item_action):
        text = pyperclip.paste()
        if text.strip():
            # Removed the anti-repeat bypass! Now it will check if it already said it and refuse to repeat.
            daemon.q.put(text)

    def on_copy_last(icon, item_action):
        if daemon.last_spoken:
            pyperclip.copy(daemon.last_spoken)

    def on_open_dict(icon, item_action):
        # Run tkinter in a new thread so we don't block the pystray icon loop completely
        threading.Thread(target=show_dictionary_ui, args=(daemon,), daemon=True).start()

    def on_exit(icon, item_action):
        daemon.stop_audio()
        # signal worker to exit
        daemon.q.put(None)
        
        # Delete history.log as requested so it disappears from history
        if os.path.exists(HISTORY_FILE):
            try:
                os.remove(HISTORY_FILE)
            except:
                pass
                
        icon.stop()

    def make_voice_setter(voice_id):
        return lambda icon, item_action: daemon.set_voice(voice_id)

    def make_rate_setter(rate_str):
        return lambda icon, item_action: daemon.set_rate(rate_str)

    voices = {
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
        "Roger (Natural Male)": "en-US-RogerNeural"
    }

    rates = {
        "0.5x (Slow)": "-50%",
        "1.0x (Normal)": "+0%",
        "1.25x": "+25%",
        "1.5x (Fast)": "+50%",
        "1.7x (Faster)": "+70%",
        "2.0x (Very Fast)": "+100%"
    }

    voice_menu_items = []
    for label, v_id in voices.items():
        voice_menu_items.append(item(label, make_voice_setter(v_id), checked=lambda i, vid=v_id: daemon.config.get("voice") == vid, radio=True))

    rate_menu_items = []
    for label, r_str in rates.items():
        rate_menu_items.append(item(label, make_rate_setter(r_str), checked=lambda i, r=r_str: daemon.config.get("rate") == r, radio=True))

    # Initial Icon (Active / Pause symbol)
    icon_image = create_pause_icon()
    icon = pystray.Icon("Audify", icon_image, "Audify (Active - Click to Pause)", menu=Menu(
        # Setting default=True makes this action trigger on left-click of the tray icon!
        item(lambda text: '▶ Resume Listening' if daemon.is_paused else '⏸ Pause Listening', on_toggle_pause, default=True),
        Menu.SEPARATOR,
        item('Read Current Clipboard', on_read_clipboard),
        item('Copy Last Spoken', on_copy_last),
        Menu.SEPARATOR,
        item('Pronunciation Dictionary...', on_open_dict),
        Menu.SEPARATOR,
        item('Voice', Menu(*voice_menu_items)),
        item('Speed', Menu(*rate_menu_items)),
        Menu.SEPARATOR,
        item('Exit', on_exit)
    ))
    
    # Run the system tray icon loop
    icon.run()

if __name__ == "__main__":
    setup_tray()
