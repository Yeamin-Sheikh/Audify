"""Audify Control Center -- settings and pronunciation dictionary GUI."""
from __future__ import annotations

import ctypes
import threading
import time
import tkinter as tk
from tkinter import ttk
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from audify.engine import TTSDaemon

from audify import __version__
from audify.bluetooth import is_bluetooth_restarting, restart_bluetooth_driver
from audify.clipboard import format_hotkey_label
from audify.icon_cache import is_icon_cache_resetting, reset_icon_cache
from audify.config import (
    STT_SILENCE_DELAYS,
    VOICES,
    RATES,
    remove_rule,
    remove_stt_vocab_rule,
    save_config,
    set_rule,
    set_stt_vocab_rule,
)
from audify.gui.widgets import (
    FluentSlider,
    FluentToggle,
    create_fluent_entry,
    create_modern_btn,
)

# Styling Tokens (Windows 11 Dark Mode)
BG_COLOR = "#1c1c1e"          # Window background
CARD_COLOR = "#2c2c2e"        # Card / panel background
FIELD_COLOR = "#1c1c1e"       # Input field background inside cards
ROW_ALT_COLOR = "#303032"     # Zebra stripe for table rows
ACCENT_COLOR = "#0a84ff"      # Windows active blue accent
ACCENT_HOVER = "#2693ff"      # Blue hover accent
TEXT_COLOR = "#ffffff"        # Primary text
TEXT_MUTED = "#8e8e93"        # Secondary text
BORDER_COLOR = "#3a3a3c"      # Control / card borders
SUCCESS_COLOR = "#30d158"     # Status confirmation
DANGER_COLOR = "#ff453a"      # Status removal

SIDEBAR_WIDTH = 290

# Single-instance guard: the tray may ask to open the window while it is already open
_instance_lock = threading.Lock()
_instance_open = False
_raise_request = threading.Event()


def _enable_dpi_awareness() -> None:
    """Must run before the Tk root is created, otherwise Windows bitmap-scales the window."""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass


def _apply_dark_title_bar(root: tk.Tk) -> None:
    try:
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id()) or root.winfo_id()
        rendering = ctypes.c_int(1)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(rendering), ctypes.sizeof(rendering))
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 19, ctypes.byref(rendering), ctypes.sizeof(rendering))
    except Exception:
        pass


def _card(parent: tk.Misc, padding: int) -> tuple[tk.Frame, tk.Frame]:
    """A 1px bordered card. Returns (outer frame to place, inner content frame)."""
    border = tk.Frame(parent, bg=BORDER_COLOR, bd=0)
    body = tk.Frame(border, bg=CARD_COLOR, bd=0)
    body.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)
    inner = tk.Frame(body, bg=CARD_COLOR, bd=0)
    inner.pack(fill=tk.BOTH, expand=True, padx=padding, pady=padding)
    return border, inner


def _configure_styles(root: tk.Tk, S) -> ttk.Style:
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(".", background=BG_COLOR, foreground=TEXT_COLOR, font=("Segoe UI", 10))
    style.configure("TFrame", background=BG_COLOR)
    style.configure("TLabel", background=BG_COLOR, foreground=TEXT_COLOR)

    # Label variants for the window background and for cards
    style.configure("Header.TLabel", font=("Segoe UI Semibold", 17))
    style.configure("Sub.TLabel", font=("Segoe UI", 9), foreground=TEXT_MUTED)
    style.configure("Card.TLabel", background=CARD_COLOR)
    style.configure("CardTitle.TLabel", background=CARD_COLOR, font=("Segoe UI Semibold", 14))
    style.configure("CardSub.TLabel", background=CARD_COLOR, foreground=TEXT_MUTED, font=("Segoe UI", 9))
    style.configure("Field.TLabel", background=CARD_COLOR, font=("Segoe UI Semibold", 9))
    style.configure("Caption.TLabel", background=CARD_COLOR, foreground=TEXT_MUTED, font=("Segoe UI Semibold", 8))
    style.configure("Value.TLabel", background=CARD_COLOR, foreground=TEXT_MUTED, font=("Segoe UI Semibold", 9))
    style.configure("Badge.TLabel", background=CARD_COLOR, foreground=TEXT_MUTED,
                    font=("Segoe UI Semibold", 9), padding=(S(10), S(3)))

    # Borderless dark combobox: clam's arrow element draws its own boxed bevel, so swap in a
    # hand-drawn chevron image (element names are process-global in Tk, hence the guard)
    if "Chevron.downarrow" not in style.element_names():
        size = S(12)
        chevron = tk.PhotoImage(master=root, width=size + S(14), height=size)
        cx, cy, half = size // 2 + S(4), size // 2 - S(2), S(4)
        for i in range(half + 1):
            for t in range(max(1, S(1.5))):
                chevron.put(TEXT_MUTED, (cx - half + i, cy + i - t + 1))
                chevron.put(TEXT_MUTED, (cx + half - i, cy + i - t + 1))
        root._chevron_img = chevron  # keep a reference so Tk doesn't discard the image
        style.element_create("Chevron.downarrow", "image", chevron, sticky="")
    style.layout("Dark.TCombobox", [
        ("Combobox.field", {"sticky": "nswe", "children": [
            ("Chevron.downarrow", {"side": "right", "sticky": "ns"}),
            ("Combobox.padding", {"expand": "1", "sticky": "nswe", "children": [
                ("Combobox.textarea", {"sticky": "nswe"}),
            ]}),
        ]}),
    ])
    style.configure(
        "Dark.TCombobox",
        fieldbackground=FIELD_COLOR,
        background=FIELD_COLOR,
        foreground=TEXT_COLOR,
        bordercolor=BORDER_COLOR,
        lightcolor=FIELD_COLOR,
        darkcolor=FIELD_COLOR,
        arrowcolor=TEXT_MUTED,
        arrowsize=S(12),
        selectbackground=FIELD_COLOR,
        selectforeground=TEXT_COLOR,
        padding=(S(10), S(6)),
    )
    style.map(
        "Dark.TCombobox",
        fieldbackground=[("readonly", FIELD_COLOR)],
        background=[("active", "#26262a"), ("readonly", FIELD_COLOR)],
        foreground=[("readonly", TEXT_COLOR)],
        selectbackground=[("readonly", FIELD_COLOR)],
        selectforeground=[("readonly", TEXT_COLOR)],
        bordercolor=[("focus", ACCENT_COLOR), ("hover", "#545458")],
        lightcolor=[("focus", FIELD_COLOR)],
        darkcolor=[("focus", FIELD_COLOR)],
        arrowcolor=[("hover", TEXT_COLOR), ("focus", TEXT_COLOR)],
    )
    root.option_add("*TCombobox*Listbox.background", CARD_COLOR)
    root.option_add("*TCombobox*Listbox.foreground", TEXT_COLOR)
    root.option_add("*TCombobox*Listbox.selectBackground", ACCENT_COLOR)
    root.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")
    root.option_add("*TCombobox*Listbox.font", ("Segoe UI", 10))
    root.option_add("*TCombobox*Listbox.borderWidth", 0)
    root.option_add("*TCombobox*Listbox.highlightThickness", 0)

    # Flat treeview: strip the outer border element entirely
    style.layout("Dict.Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
    style.configure(
        "Dict.Treeview",
        background=CARD_COLOR,
        foreground=TEXT_COLOR,
        fieldbackground=CARD_COLOR,
        rowheight=S(32),
        borderwidth=0,
        font=("Segoe UI", 10),
    )
    style.map("Dict.Treeview", background=[("selected", ACCENT_COLOR)], foreground=[("selected", "#ffffff")])
    style.configure(
        "Dict.Treeview.Heading",
        background=CARD_COLOR,
        foreground=TEXT_MUTED,
        font=("Segoe UI Semibold", 9),
        borderwidth=0,
        relief="flat",
        lightcolor=CARD_COLOR,
        darkcolor=CARD_COLOR,
        bordercolor=CARD_COLOR,
        padding=(S(10), S(8)),
    )
    style.map("Dict.Treeview.Heading", background=[("active", CARD_COLOR)], foreground=[("active", TEXT_COLOR)])

    # Minimalist dark scrollbar layout (hides standard arrow elements)
    style.layout("Vertical.TScrollbar", [
        ("Vertical.Scrollbar.trough", {
            "children": [("Vertical.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})],
            "sticky": "ns",
        })
    ])
    style.configure("Vertical.TScrollbar",
                    background="#48484a",
                    troughcolor=CARD_COLOR,
                    bordercolor=CARD_COLOR,
                    arrowcolor=CARD_COLOR,
                    lightcolor="#48484a",
                    darkcolor="#48484a",
                    gripcount=0,
                    width=S(8))
    style.map("Vertical.TScrollbar",
              background=[("active", "#5a5a5c"), ("pressed", "#6c6c6e")])
    return style


def show_dictionary_ui(daemon: Any) -> None:
    """Open the Control Center, or bring the existing window to the front if already open."""
    global _instance_open
    with _instance_lock:
        if _instance_open:
            _raise_request.set()
            return
        _instance_open = True
    try:
        _run_control_center(daemon)
    finally:
        with _instance_lock:
            _instance_open = False


def _run_control_center(daemon: Any) -> None:
    """Build and run a Windows 11-themed Tkinter window for settings & pronunciation rules."""
    _raise_request.clear()
    _enable_dpi_awareness()
    root = tk.Tk()
    root.title("Audify Control Center")
    root.attributes("-topmost", True)
    root.configure(bg=BG_COLOR)

    # Pixel sizes are authored at 96 DPI and scaled to the monitor (fonts scale automatically)
    scale = root.winfo_fpixels("1i") / 96.0

    def S(px: float) -> int:
        return int(round(px * scale))

    root.update_idletasks()
    _apply_dark_title_bar(root)
    width, height = S(1020), S(785)
    x = (root.winfo_screenwidth() // 2) - (width // 2)
    y = (root.winfo_screenheight() // 2) - (height // 2)
    root.geometry(f"{width}x{height}+{x}+{y}")
    root.resizable(True, True)
    root.minsize(S(880), S(710))

    _configure_styles(root, S)

    tts_dict_ref: dict[str, str] = daemon.config.setdefault("pronunciation_dict", {})
    stt_vocab_ref: dict[str, str] = daemon.config.setdefault("stt_vocabulary", {})
    active_mode: list[str] = ["tts"]

    def get_active_dict() -> dict[str, str]:
        return stt_vocab_ref if active_mode[0] == "stt" else tts_dict_ref

    main_frame = tk.Frame(root, bg=BG_COLOR)
    main_frame.pack(fill=tk.BOTH, expand=True, padx=S(20), pady=S(20))
    main_frame.grid_columnconfigure(1, weight=1)
    main_frame.grid_rowconfigure(0, weight=1)

    # -----------------------------------------------------------------------
    # LEFT COLUMN: Settings Panel (fixed width)
    # -----------------------------------------------------------------------
    sidebar_border, sidebar = _card(main_frame, padding=S(20))
    sidebar_border.configure(width=S(SIDEBAR_WIDTH))
    sidebar_border.pack_propagate(False)
    sidebar_border.grid(row=0, column=0, sticky="ns", padx=(0, S(20)))

    ttk.Label(sidebar, text="Audify", style="CardTitle.TLabel").pack(anchor=tk.W)
    ttk.Label(sidebar, text="Control Center", style="CardSub.TLabel").pack(anchor=tk.W, pady=(0, S(18)))

    def section_caption(text: str) -> None:
        ttk.Label(sidebar, text=text.upper(), style="Caption.TLabel").pack(anchor=tk.W, pady=(0, S(10)))

    def field_label(text: str) -> None:
        ttk.Label(sidebar, text=text, style="Field.TLabel").pack(anchor=tk.W, pady=(0, S(6)))

    section_caption("Playback")

    # Live Volume Controller (label + value on one row, slider below)
    vol_header = tk.Frame(sidebar, bg=CARD_COLOR)
    vol_header.pack(fill=tk.X)
    ttk.Label(vol_header, text="Volume", style="Field.TLabel").pack(side=tk.LEFT)
    vol_percent = int(daemon.config.get("volume", 1.0) * 100)
    vol_var = tk.IntVar(value=vol_percent)
    vol_label = ttk.Label(vol_header, text=f"{vol_percent}%", style="Value.TLabel")
    vol_label.pack(side=tk.RIGHT)

    # Explicit width: a Canvas otherwise defaults to 10cm and bloats the sidebar
    vol_slider = FluentSlider(
        sidebar,
        from_=0,
        to=100,
        variable=vol_var,
        bg=CARD_COLOR,
        active_color=ACCENT_COLOR,
        track_color=BORDER_COLOR,
        knob_color="#ffffff",
        width=S(200),
        height=S(26),
    )
    vol_slider.pack(fill=tk.X, pady=(S(2), S(16)))

    def on_volume_change(val):
        daemon.set_volume(float(val) / 100.0)
        vol_label.config(text=f"{int(float(val))}%")

    vol_slider.config(command=on_volume_change)

    # Voice Combobox
    field_label("Voice")
    voice_names = list(VOICES.keys())
    current_voice_id = daemon.config.get("voice", "en-US-JennyNeural")
    current_voice_name = next((n for n, v in VOICES.items() if v == current_voice_id), voice_names[0])
    voice_var = tk.StringVar(value=current_voice_name)
    voice_combo = ttk.Combobox(sidebar, textvariable=voice_var, values=voice_names,
                               state="readonly", style="Dark.TCombobox", font=("Segoe UI", 10), width=10)
    voice_combo.pack(fill=tk.X, pady=(0, S(16)))

    def on_voice_select(event):
        daemon.set_voice(VOICES[voice_var.get()])
        voice_combo.selection_clear()

    voice_combo.bind("<<ComboboxSelected>>", on_voice_select)

    # Speed Rate Combobox
    field_label("Speed")
    rate_names = list(RATES.keys())
    current_rate_val = daemon.config.get("rate", "+50%")
    current_rate_name = next((n for n, r in RATES.items() if r == current_rate_val), rate_names[3])
    rate_var = tk.StringVar(value=current_rate_name)
    rate_combo = ttk.Combobox(sidebar, textvariable=rate_var, values=rate_names,
                              state="readonly", style="Dark.TCombobox", font=("Segoe UI", 10), width=10)
    rate_combo.pack(fill=tk.X, pady=(0, S(20)))

    def on_rate_select(event):
        daemon.set_rate(RATES[rate_var.get()])
        rate_combo.selection_clear()

    rate_combo.bind("<<ComboboxSelected>>", on_rate_select)

    tk.Frame(sidebar, bg=BORDER_COLOR, height=1).pack(fill=tk.X, pady=(0, S(18)))
    section_caption("Reading")

    # Skip Code Blocks toggle row (text on the left, switch on the right)
    skip_var = tk.BooleanVar(value=daemon.config.get("skip_code_blocks", True))

    def on_toggle_skip():
        daemon.toggle_skip_code_blocks()
        skip_var.set(daemon.config.get("skip_code_blocks", True))

    skip_row = tk.Frame(sidebar, bg=CARD_COLOR, cursor="hand2")
    skip_row.pack(fill=tk.X)
    skip_chk = FluentToggle(
        skip_row,
        variable=skip_var,
        command=on_toggle_skip,
        bg=CARD_COLOR,
        active_color=ACCENT_COLOR,
        track_color=BORDER_COLOR,
        knob_color="#ffffff",
        width=S(40),
        height=S(20),
    )
    skip_chk.pack(side=tk.RIGHT, padx=(S(10), 0))
    skip_text = tk.Frame(skip_row, bg=CARD_COLOR, cursor="hand2")
    skip_text.pack(side=tk.LEFT, fill=tk.X, expand=True)
    skip_title = ttk.Label(skip_text, text="Skip code blocks", style="Field.TLabel", cursor="hand2")
    skip_title.pack(anchor=tk.W)
    skip_desc = ttk.Label(skip_text, text="Don't read fenced code aloud", style="CardSub.TLabel", cursor="hand2")
    skip_desc.pack(anchor=tk.W)
    for w in (skip_row, skip_text, skip_title, skip_desc):
        w.bind("<Button-1>", lambda e: skip_chk._on_click(None))

    tk.Frame(sidebar, bg=BORDER_COLOR, height=1).pack(fill=tk.X, pady=(S(18), S(16)))
    section_caption("System Tools")

    tools_status_lbl = ttk.Label(
        sidebar,
        text="Reset Bluetooth driver or Windows icon cache",
        style="CardSub.TLabel",
    )

    def _on_bt_click() -> None:
        if is_bluetooth_restarting():
            return
        bt_btn.config(state="disabled", text="Restarting...")
        tools_status_lbl.config(text="Running elevated Bluetooth reset...", foreground=TEXT_MUTED)

        def _done(ok: bool, msg: str) -> None:
            def _ui_update() -> None:
                try:
                    bt_btn.config(state="normal", text="Restart Bluetooth")
                    tools_status_lbl.config(
                        text="Bluetooth restarted & audio reconnected" if ok else msg,
                        foreground=SUCCESS_COLOR if ok else DANGER_COLOR,
                    )
                except Exception:
                    pass
            try:
                root.after(0, _ui_update)
            except Exception:
                pass

        restart_bluetooth_driver(daemon=daemon, on_complete=_done)

    def _on_icon_cache_click() -> None:
        if is_icon_cache_resetting():
            return
        icon_btn.config(state="disabled", text="Resetting...")
        tools_status_lbl.config(text="Clearing icon cache & restarting Explorer...", foreground=TEXT_MUTED)

        def _done(ok: bool, msg: str) -> None:
            def _ui_update() -> None:
                try:
                    icon_btn.config(state="normal", text="Reset Icon Cache")
                    tools_status_lbl.config(
                        text="Icon cache reset & Explorer restarted" if ok else msg,
                        foreground=SUCCESS_COLOR if ok else DANGER_COLOR,
                    )
                except Exception:
                    pass
            try:
                root.after(0, _ui_update)
            except Exception:
                pass

        reset_icon_cache(daemon=daemon, on_complete=_done)

    tools_row = tk.Frame(sidebar, bg=CARD_COLOR)
    tools_row.pack(fill=tk.X, pady=(0, S(6)))
    tools_row.grid_columnconfigure(0, weight=1, uniform="tools")
    tools_row.grid_columnconfigure(1, weight=1, uniform="tools")

    bt_btn = create_modern_btn(
        tools_row,
        "Restart Bluetooth",
        _on_bt_click,
        primary=False,
        accent_color=ACCENT_COLOR,
        accent_hover=ACCENT_HOVER,
    )
    bt_btn.config(padx=S(6))
    bt_btn.grid(row=0, column=0, sticky="ew", padx=(0, S(4)))

    icon_btn = create_modern_btn(
        tools_row,
        "Reset Icon Cache",
        _on_icon_cache_click,
        primary=False,
        accent_color=ACCENT_COLOR,
        accent_hover=ACCENT_HOVER,
    )
    icon_btn.config(padx=S(6))
    icon_btn.grid(row=0, column=1, sticky="ew", padx=(S(4), 0))

    def _on_dictate_click() -> None:
        stt = getattr(daemon, "stt", None)
        if stt is None or stt.is_transcribing:
            return

        def _done(ok: bool, msg: str) -> None:
            def _ui_update() -> None:
                try:
                    dictate_btn.config(state="normal", text="Start Dictation (Bark 3.0)")
                    preview = msg if len(msg) <= 42 else msg[:39] + "..."
                    tools_status_lbl.config(
                        text=f"Dictated: {preview}" if ok else msg,
                        foreground=SUCCESS_COLOR if ok else DANGER_COLOR,
                    )
                except Exception:
                    pass
            try:
                root.after(0, _ui_update)
            except Exception:
                pass

        if not stt.is_recording:
            stt.start_dictation(on_complete=_done)
            dictate_btn.config(text="Stop & Transcribe")
            tools_status_lbl.config(text="Listening... speak now (auto-stops on pause)", foreground=ACCENT_COLOR)
        else:
            dictate_btn.config(state="disabled", text="Transcribing...")
            tools_status_lbl.config(text="Bark 3.0 Clean transcribing...", foreground=TEXT_MUTED)
            stt.stop_and_transcribe(on_complete=_done)

    dictate_btn = create_modern_btn(
        sidebar,
        "Start Dictation (Bark 3.0)",
        _on_dictate_click,
        primary=True,
        accent_color=ACCENT_COLOR,
        accent_hover=ACCENT_HOVER,
    )
    dictate_btn.pack(fill=tk.X, pady=(0, S(8)))

    delay_row = tk.Frame(sidebar, bg=CARD_COLOR)
    delay_row.pack(fill=tk.X, pady=(0, S(6)))
    ttk.Label(delay_row, text="Auto-stop silence", style="Field.TLabel").pack(side=tk.LEFT)
    cur_delay_val = float(daemon.config.get("stt_silence_delay", 1.0))
    current_delay_name = next(
        (n for n, d in STT_SILENCE_DELAYS.items() if abs(d - cur_delay_val) < 0.05),
        "1.0s (Balanced)",
    )
    delay_var = tk.StringVar(value=current_delay_name)
    delay_cb = ttk.Combobox(
        delay_row,
        textvariable=delay_var,
        values=list(STT_SILENCE_DELAYS.keys()),
        state="readonly",
        style="Dark.TCombobox",
        font=("Segoe UI", 9),
        width=15,
    )
    delay_cb.pack(side=tk.RIGHT)

    def on_delay_change(event) -> None:
        d_val = STT_SILENCE_DELAYS.get(delay_var.get())
        if d_val is not None and hasattr(daemon, "set_stt_silence_delay"):
            daemon.set_stt_silence_delay(d_val)
        delay_cb.selection_clear()

    delay_cb.bind("<<ComboboxSelected>>", on_delay_change)

    tools_status_lbl.pack(anchor=tk.W)

    # Footer: customizable global shortcuts + version
    footer = tk.Frame(sidebar, bg=CARD_COLOR)
    footer.pack(side=tk.BOTTOM, fill=tk.X)
    tk.Frame(footer, bg=BORDER_COLOR, height=1).pack(fill=tk.X, pady=(0, S(10)))
    ttk.Label(footer, text="SHORTCUTS (CLICK TO CHANGE)", style="Caption.TLabel").pack(anchor=tk.W, pady=(0, S(6)))

    def _fmt_hk(hk_str: str) -> str:
        return format_hotkey_label(hk_str)

    recording_target: list[tuple[str, tk.Label, str] | None] = [None]
    last_mod_tap: list[tuple[str, float]] = [("", 0.0)]
    hotkey_badges: dict[str, tk.Label] = {}

    def _cancel_hotkey_record() -> None:
        cur = recording_target[0]
        if cur is None:
            return
        cfg_key, badge_lbl, default_hk = cur
        recording_target[0] = None
        last_mod_tap[0] = ("", 0.0)
        saved_hk = str(daemon.config.get(cfg_key, default_hk))
        badge_lbl.config(text=_fmt_hk(saved_hk), fg=TEXT_COLOR, highlightbackground=BORDER_COLOR)

    def _start_hotkey_record(cfg_key: str, badge_lbl: tk.Label, default_hk: str) -> None:
        _cancel_hotkey_record()
        recording_target[0] = (cfg_key, badge_lbl, default_hk)
        last_mod_tap[0] = ("", 0.0)
        badge_lbl.config(text="Press keys...", fg=ACCENT_COLOR, highlightbackground=ACCENT_COLOR)
        root.focus_force()

    def _on_hotkey_capture(event: tk.Event) -> str | None:
        cur = recording_target[0]
        if cur is None:
            return None
        cfg_key, badge_lbl, default_hk = cur
        ks = (getattr(event, "keysym", "") or "").lower()
        kc = getattr(event, "keycode", 0) or 0

        double_mod_map = {
            "control_l": "double_lctrl",
            "control_r": "double_rctrl",
            "alt_l": "double_lalt",
            "shift_l": "double_lshift",
        }
        if ks in double_mod_map:
            now_m = time.monotonic()
            prev_ks, prev_t = last_mod_tap[0]
            if prev_ks == ks and 0.05 <= (now_m - prev_t) <= 0.55:
                combo_str = double_mod_map[ks]
                recording_target[0] = None
                last_mod_tap[0] = ("", 0.0)
                if hasattr(daemon, "set_hotkey") and daemon.set_hotkey(cfg_key, combo_str):
                    badge_lbl.config(text=_fmt_hk(combo_str), fg=TEXT_COLOR, highlightbackground=BORDER_COLOR)
                    tools_status_lbl.config(
                        text=f"Shortcut set to {_fmt_hk(combo_str)}",
                        foreground=SUCCESS_COLOR,
                    )
                return "break"
            last_mod_tap[0] = (ks, now_m)
            return "break"

        if ks in (
            "alt_r", "shift_r", "win_l", "win_r", "super_l", "super_r",
        ):
            return "break"

        last_mod_tap[0] = ("", 0.0)
        if ks == "escape":
            _cancel_hotkey_record()
            return "break"

        u32 = ctypes.windll.user32
        mods: list[str] = []
        if u32.GetAsyncKeyState(0x11) & 0x8000:
            mods.append("ctrl")
        if u32.GetAsyncKeyState(0x12) & 0x8000:
            mods.append("alt")
        if u32.GetAsyncKeyState(0x10) & 0x8000:
            mods.append("shift")
        if (u32.GetAsyncKeyState(0x5B) & 0x8000) or (u32.GetAsyncKeyState(0x5C) & 0x8000):
            mods.append("win")

        key_map = {
            "space": "space", "return": "enter", "tab": "tab",
            "back": "backspace", "backspace": "backspace",
            "insert": "insert", "delete": "delete",
            "prior": "pageup", "next": "pagedown",
            "home": "home", "end": "end",
            "up": "up", "down": "down", "left": "left", "right": "right",
            "grave": "`", "minus": "-", "equal": "=",
        }
        key_tok = ""
        if 0x70 <= kc <= 0x87:
            key_tok = f"f{kc - 0x70 + 1}"
        elif 0x30 <= kc <= 0x5A:
            key_tok = chr(kc).lower()
        elif ks in key_map:
            key_tok = key_map[ks]
        elif len(ks) == 1 and ks.isalnum():
            key_tok = ks

        if not key_tok:
            return "break"
        if not mods and not key_tok.startswith("f"):
            tools_status_lbl.config(
                text="Include Ctrl, Alt, or Shift (or use F1-F12)",
                foreground=DANGER_COLOR,
            )
            return "break"

        combo_str = "+".join(mods + [key_tok])
        recording_target[0] = None
        if hasattr(daemon, "set_hotkey") and daemon.set_hotkey(cfg_key, combo_str):
            badge_lbl.config(text=_fmt_hk(combo_str), fg=TEXT_COLOR, highlightbackground=BORDER_COLOR)
            tools_status_lbl.config(
                text=f"Shortcut set to {_fmt_hk(combo_str)}",
                foreground=SUCCESS_COLOR,
            )
        else:
            _cancel_hotkey_record()
            tools_status_lbl.config(text="Invalid shortcut combination", foreground=DANGER_COLOR)
        return "break"

    root.bind("<KeyPress>", _on_hotkey_capture, add="+")

    def _make_hotkey_row(label_text: str, cfg_key: str, default_hk: str, pad_bottom: int) -> None:
        row = tk.Frame(footer, bg=CARD_COLOR)
        row.pack(fill=tk.X, pady=(0, pad_bottom))
        ttk.Label(row, text=label_text, style="CardSub.TLabel").pack(side=tk.LEFT)
        cur_hk = str(daemon.config.get(cfg_key, default_hk))
        badge = tk.Label(
            row,
            text=_fmt_hk(cur_hk),
            bg=FIELD_COLOR, fg=TEXT_COLOR, font=("Segoe UI Semibold", 8),
            padx=S(8), pady=S(2), bd=0, highlightthickness=1,
            highlightbackground=BORDER_COLOR, cursor="hand2",
        )
        badge.pack(side=tk.RIGHT)
        badge.bind("<Button-1>", lambda e, k=cfg_key, b=badge, d=default_hk: _start_hotkey_record(k, b, d))
        hotkey_badges[cfg_key] = badge

    _make_hotkey_row("Dictate (Bark 3.0)", "dictation_hotkey", "double_lctrl", S(5))
    _make_hotkey_row("Paste last dictation", "paste_last_hotkey", "ctrl+alt+v", S(5))
    _make_hotkey_row("Stop playback", "stop_hotkey", "ctrl+alt+s", S(8))
    ttk.Label(footer, text=f"Version {__version__}", style="CardSub.TLabel").pack(anchor=tk.W)

    # -----------------------------------------------------------------------
    # RIGHT COLUMN: Dictionary Panel
    # -----------------------------------------------------------------------
    dict_frame = tk.Frame(main_frame, bg=BG_COLOR)
    dict_frame.grid(row=0, column=1, sticky="nsew")

    # Header: title + subtitle on the left, rule count badge on the right
    header_frame = tk.Frame(dict_frame, bg=BG_COLOR)
    header_frame.pack(fill=tk.X, pady=(0, S(10)))
    title_box = tk.Frame(header_frame, bg=BG_COLOR)
    title_box.pack(side=tk.LEFT, fill=tk.X, expand=True)
    header_title_lbl = ttk.Label(title_box, text="Pronunciation Dictionary", style="Header.TLabel")
    header_title_lbl.pack(anchor=tk.W)
    header_sub_lbl = ttk.Label(
        title_box,
        text="Tell Audify how to say symbols, acronyms and technical terms.",
        style="Sub.TLabel",
    )
    header_sub_lbl.pack(anchor=tk.W, pady=(S(2), 0))

    count_border = tk.Frame(header_frame, bg=BORDER_COLOR)
    count_border.pack(side=tk.RIGHT, anchor=tk.N, pady=(S(6), 0))
    count_label = ttk.Label(count_border, style="Badge.TLabel")
    count_label.pack(padx=1, pady=1)

    # Segmented mode switcher: TTS Pronunciation vs STT Vocabulary (Auto-Learn)
    mode_bar = tk.Frame(dict_frame, bg=CARD_COLOR, highlightthickness=1, highlightbackground=BORDER_COLOR)
    mode_bar.pack(fill=tk.X, pady=(0, S(10)))
    mode_tts_btn = tk.Label(
        mode_bar,
        text="TTS Pronunciation (Read Aloud)",
        bg=ACCENT_COLOR,
        fg="#ffffff",
        font=("Segoe UI Semibold", 9),
        padx=S(14),
        pady=S(6),
        cursor="hand2",
    )
    mode_tts_btn.pack(side=tk.LEFT, fill=tk.X, expand=True)
    mode_stt_btn = tk.Label(
        mode_bar,
        text="STT Vocabulary (Voice Dictation Auto-Learn)",
        bg=CARD_COLOR,
        fg=TEXT_MUTED,
        font=("Segoe UI Semibold", 9),
        padx=S(14),
        pady=S(6),
        cursor="hand2",
    )
    mode_stt_btn.pack(side=tk.LEFT, fill=tk.X, expand=True)

    # Search bar
    search_var = tk.StringVar()
    search_entry_frame, search_entry = create_fluent_entry(
        dict_frame,
        textvariable=search_var,
        bg=CARD_COLOR,
        border_color=BORDER_COLOR,
        active_color=ACCENT_COLOR,
        placeholder="Search rules…   (Ctrl+F)",
        placeholder_color=TEXT_MUTED,
    )
    search_entry_frame.pack(fill=tk.X, pady=(0, S(12)))

    # Right-Click Context menu helper
    def add_context_menu(widget: tk.Entry) -> None:
        menu = tk.Menu(widget, tearoff=0, bg=CARD_COLOR, fg=TEXT_COLOR, selectcolor=ACCENT_COLOR,
                       activebackground=ACCENT_COLOR, activeforeground="#ffffff", bd=1, relief="solid")
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

    # Bottom sections are packed before the table so they keep their space when the window shrinks
    action_frame = tk.Frame(dict_frame, bg=BG_COLOR)
    action_frame.pack(side=tk.BOTTOM, fill=tk.X)

    editor_border, editor = _card(dict_frame, padding=S(14))
    editor_border.pack(side=tk.BOTTOM, fill=tk.X, pady=(0, S(14)))

    # Table card
    table_border = tk.Frame(dict_frame, bg=BORDER_COLOR, bd=0)
    table_border.pack(fill=tk.BOTH, expand=True, pady=(0, S(14)))
    table_container = tk.Frame(table_border, bg=CARD_COLOR, bd=0)
    table_container.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)

    columns = ("row_num", "word", "pronunciation")
    tree = ttk.Treeview(table_container, columns=columns, show="headings", style="Dict.Treeview", selectmode="extended")
    tree.heading("row_num", text="#", anchor=tk.CENTER)
    tree.heading("word", text="WORD", anchor=tk.W)
    tree.heading("pronunciation", text="SPOKEN AS", anchor=tk.W)
    tree.column("row_num", width=S(52), minwidth=S(44), stretch=False, anchor=tk.CENTER)
    tree.column("word", width=S(200), minwidth=S(100), stretch=False, anchor=tk.W)
    tree.column("pronunciation", width=S(220), minwidth=S(120), stretch=True, anchor=tk.W)
    tree.tag_configure("odd", background=ROW_ALT_COLOR)
    tree.tag_configure("even", background=CARD_COLOR)

    scrollbar = ttk.Scrollbar(table_container, orient=tk.VERTICAL, command=tree.yview, style="Vertical.TScrollbar")
    tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, pady=(0, S(4)))

    def on_tree_scroll(first: str, last: str) -> None:
        # Auto-hide the scrollbar when every row fits
        if float(first) <= 0.0 and float(last) >= 1.0:
            scrollbar.pack_forget()
        elif not scrollbar.winfo_ismapped():
            scrollbar.pack(side=tk.RIGHT, fill=tk.Y, padx=(0, S(3)), pady=S(3), before=tree)
        scrollbar.set(first, last)

    tree.configure(yscrollcommand=on_tree_scroll)

    empty_label = tk.Label(table_container, bg=CARD_COLOR, fg=TEXT_MUTED, font=("Segoe UI", 10), justify=tk.CENTER)

    # Rules are keyed by iid = original word, so values are never coerced (e.g. "1" -> int)
    def refresh_tree(select: str | None = None) -> None:
        cur_dict = get_active_dict()
        query = search_var.get().strip().lower()
        tree.delete(*tree.get_children())
        shown = 0
        for w in sorted(cur_dict.keys(), key=str.lower):
            s = cur_dict[w]
            if query and query not in w.lower() and query not in s.lower():
                continue
            shown += 1
            tree.insert("", tk.END, iid=w, values=(shown, w, s), tags=("odd" if shown % 2 == 0 else "even",))

        total = len(cur_dict)
        count_label.config(text=f"{total} rule{'s' if total != 1 else ''}" if not query else f"{shown} of {total}")

        if shown == 0:
            if query:
                empty_label.config(text=f"No rules match “{search_var.get().strip()}”")
            elif active_mode[0] == "stt":
                empty_label.config(
                    text="No custom STT words yet.\nAdd one below, or double-click a misheard word after dictating to auto-learn it."
                )
            else:
                empty_label.config(text="No rules yet.\nAdd one below to get started.")
            empty_label.place(relx=0.5, rely=0.5, anchor=tk.CENTER)
        else:
            empty_label.place_forget()

        if select and tree.exists(select):
            tree.selection_set(select)
            tree.see(select)

    search_var.trace_add("write", lambda *a: refresh_tree())

    # Rule editor: labels above two equal-width inputs, buttons on the right
    editor.grid_columnconfigure(0, weight=1, uniform="inputs")
    editor.grid_columnconfigure(1, weight=1, uniform="inputs")

    field_left_lbl = ttk.Label(editor, text="Word or symbol", style="Field.TLabel")
    field_left_lbl.grid(row=0, column=0, sticky=tk.W, pady=(0, S(6)))
    field_right_lbl = ttk.Label(editor, text="Spoken as", style="Field.TLabel")
    field_right_lbl.grid(row=0, column=1, sticky=tk.W, padx=(S(12), 0), pady=(0, S(6)))

    word_var = tk.StringVar()
    word_entry_frame, word_entry = create_fluent_entry(
        editor, textvariable=word_var, width=8, bg=FIELD_COLOR,
        border_color=BORDER_COLOR, active_color=ACCENT_COLOR,
        placeholder="e.g. =>", placeholder_color="#636366",
    )
    word_entry_frame.grid(row=1, column=0, sticky="ew")
    add_context_menu(word_entry)

    spoken_var = tk.StringVar()
    spoken_entry_frame, spoken_entry = create_fluent_entry(
        editor, textvariable=spoken_var, width=8, bg=FIELD_COLOR,
        border_color=BORDER_COLOR, active_color=ACCENT_COLOR,
        placeholder="e.g. arrow", placeholder_color="#636366",
    )
    spoken_entry_frame.grid(row=1, column=1, sticky="ew", padx=(S(12), 0))
    add_context_menu(spoken_entry)

    status_label = tk.Label(action_frame, bg=BG_COLOR, fg=TEXT_MUTED, font=("Segoe UI", 9))
    status_job: list[str | None] = [None]

    def flash_status(text: str, color: str) -> None:
        if status_job[0]:
            root.after_cancel(status_job[0])
        status_label.config(text=text, fg=color)
        status_job[0] = root.after(2500, lambda: status_label.config(text=""))

    # The reader thread copies the dictionary under this lock, so edits must hold it too
    config_lock = getattr(daemon, "_config_lock", None) or threading.Lock()

    def persist() -> None:
        with config_lock:
            save_config(daemon.config)

    def clear_editor() -> None:
        word_var.set("")
        spoken_var.set("")
        tree.selection_remove(*tree.selection())
        word_entry.focus_set()

    def add_entry(event=None) -> str:
        w = word_var.get().strip()
        s = spoken_var.get().strip()
        if not w:
            word_entry.focus_set()
        elif not s:
            spoken_entry.focus_set()
        else:
            cur_dict = get_active_dict()
            existed = w in cur_dict
            with config_lock:
                if active_mode[0] == "stt":
                    set_stt_vocab_rule(daemon.config, w, s)
                else:
                    set_rule(daemon.config, w, s)
                    save_config(daemon.config)
            refresh_tree(select=w)
            word_var.set("")
            spoken_var.set("")
            word_entry.focus_set()
            flash_status(f"{'Updated' if existed else 'Added'} “{w}”", SUCCESS_COLOR)
        return "break"

    btn_box = tk.Frame(editor, bg=CARD_COLOR)
    btn_box.grid(row=1, column=2, sticky="e", padx=(S(12), 0))
    clear_btn = create_modern_btn(btn_box, "Clear", clear_editor)
    clear_btn.pack(side=tk.RIGHT)
    add_btn = create_modern_btn(btn_box, "Add Rule", add_entry, primary=True,
                                accent_color=ACCENT_COLOR, accent_hover=ACCENT_HOVER)
    add_btn.config(width=11)
    add_btn.pack(side=tk.RIGHT, padx=(0, S(8)))

    def update_editor_state(*args) -> None:
        w = word_var.get().strip()
        cur_dict = get_active_dict()
        add_btn.config(text="Update Rule" if w in cur_dict else "Add Rule")

    def switch_mode(new_mode: str) -> None:
        if active_mode[0] == new_mode:
            return
        active_mode[0] = new_mode
        if new_mode == "stt":
            mode_stt_btn.config(bg=ACCENT_COLOR, fg="#ffffff")
            mode_tts_btn.config(bg=CARD_COLOR, fg=TEXT_MUTED)
            header_title_lbl.config(text="Speech-to-Text Vocabulary")
            header_sub_lbl.config(
                text="Custom words, names & places (auto-learns when you double-click and fix a dictated word)."
            )
            tree.heading("word", text="HEARD AS (WRONG)", anchor=tk.W)
            tree.heading("pronunciation", text="WRITTEN AS (CORRECT)", anchor=tk.W)
            field_left_lbl.config(text="Heard as (misheard word)")
            field_right_lbl.config(text="Written as (correct spelling)")
        else:
            mode_tts_btn.config(bg=ACCENT_COLOR, fg="#ffffff")
            mode_stt_btn.config(bg=CARD_COLOR, fg=TEXT_MUTED)
            header_title_lbl.config(text="Pronunciation Dictionary")
            header_sub_lbl.config(
                text="Tell Audify how to say symbols, acronyms and technical terms."
            )
            tree.heading("word", text="WORD", anchor=tk.W)
            tree.heading("pronunciation", text="SPOKEN AS", anchor=tk.W)
            field_left_lbl.config(text="Word or symbol")
            field_right_lbl.config(text="Spoken as")
        clear_editor()
        refresh_tree()
        on_tree_select(None)

    mode_tts_btn.bind("<Button-1>", lambda _e: switch_mode("tts"))
    mode_stt_btn.bind("<Button-1>", lambda _e: switch_mode("stt"))

    word_var.trace_add("write", update_editor_state)
    spoken_var.trace_add("write", update_editor_state)
    update_editor_state()

    for entry in (word_entry, spoken_entry):
        entry.bind("<Return>", add_entry)
        entry.bind("<Escape>", lambda e: (clear_editor(), "break")[1])

    def on_tree_select(event) -> None:
        cur_dict = get_active_dict()
        selected = tree.selection()
        remove_btn.config(state=tk.NORMAL if selected else tk.DISABLED,
                          text=f"Remove {len(selected)} Rules" if len(selected) > 1 else "Remove Selected")
        if len(selected) == 1 and selected[0] in cur_dict:
            word_var.set(selected[0])
            spoken_var.set(cur_dict[selected[0]])

    tree.bind("<<TreeviewSelect>>", on_tree_select)
    tree.bind("<Double-1>", lambda e: spoken_entry.focus_set())

    def remove_selected(event=None) -> None:
        cur_dict = get_active_dict()
        words = [w for w in tree.selection() if w in cur_dict]
        if not words:
            return
        with config_lock:
            for w in words:
                if active_mode[0] == "stt":
                    remove_stt_vocab_rule(daemon.config, w)
                else:
                    remove_rule(daemon.config, w)
            if active_mode[0] != "stt":
                save_config(daemon.config)
        refresh_tree()
        word_var.set("")
        spoken_var.set("")
        on_tree_select(None)
        flash_status(f"Removed “{words[0]}”" if len(words) == 1 else f"Removed {len(words)} rules", DANGER_COLOR)

    tree.bind("<Delete>", remove_selected)

    remove_btn = create_modern_btn(action_frame, "Remove Selected", remove_selected, danger=True)
    remove_btn.config(state=tk.DISABLED)
    remove_btn.pack(side=tk.LEFT)
    status_label.pack(side=tk.LEFT, padx=(S(14), 0))

    close_btn = create_modern_btn(action_frame, "Close", root.destroy)
    close_btn.pack(side=tk.RIGHT)

    def focus_search(event=None) -> str:
        search_entry.focus_set()
        search_entry.select_range(0, tk.END)
        return "break"

    root.bind("<Control-f>", focus_search)
    search_entry.bind("<Escape>", lambda e: search_var.set(""))

    _last_stt_snapshot: list[tuple[tuple[str, str], ...] | None] = [
        tuple(sorted(stt_vocab_ref.items()))
    ]

    # Continuous Active Value Synchronization loop (from Tray menu changes & STT auto-learn)
    def sync_gui_values():
        try:
            if _raise_request.is_set():
                _raise_request.clear()
                root.deiconify()
                root.lift()
                root.focus_force()

            cur_stt_snap = tuple(sorted(stt_vocab_ref.items()))
            if cur_stt_snap != _last_stt_snapshot[0]:
                _last_stt_snapshot[0] = cur_stt_snap
                if active_mode[0] == "stt":
                    refresh_tree()
                st_str = str(getattr(daemon, "status", "") or "")
                if st_str.startswith("Learned:"):
                    flash_status(f"Auto-learned {st_str[8:].strip()}", SUCCESS_COLOR)

            cur_vol = int(daemon.config.get("volume", 1.0) * 100)
            if vol_var.get() != cur_vol and not vol_slider.is_dragging:
                vol_var.set(cur_vol)
                vol_label.config(text=f"{cur_vol}%")

            cur_voice_id = daemon.config.get("voice", "en-US-JennyNeural")
            name = next((n for n, v in VOICES.items() if v == cur_voice_id), None)
            if name and voice_var.get() != name:
                voice_var.set(name)

            cur_rate_val = daemon.config.get("rate", "+50%")
            name = next((n for n, r in RATES.items() if r == cur_rate_val), None)
            if name and rate_var.get() != name:
                rate_var.set(name)

            cur_skip = daemon.config.get("skip_code_blocks", True)
            if skip_var.get() != cur_skip:
                skip_var.set(cur_skip)

            cur_delay = float(daemon.config.get("stt_silence_delay", 1.0))
            d_name = next(
                (n for n, d in STT_SILENCE_DELAYS.items() if abs(d - cur_delay) < 0.05),
                None,
            )
            if d_name and delay_var.get() != d_name:
                delay_var.set(d_name)

            stt = getattr(daemon, "stt", None)
            if stt is not None:
                if stt.is_transcribing:
                    dictate_btn.config(state="disabled", text="Transcribing...")
                elif stt.is_recording:
                    dictate_btn.config(state="normal", text="Stop & Transcribe")
                else:
                    dictate_btn.config(state="normal", text="Start Dictation (Bark 3.0)")

            root.after(1000, sync_gui_values)
        except tk.TclError:
            pass  # window was closed

    sync_gui_values()
    refresh_tree()
    word_entry.focus_set()
    root.mainloop()
