"""Audify Settings -- Windows 11 WinUI 3 settings and dictionary GUI."""
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
from audify.gui.theme import (
    THEME_MODES,
    apply_window_theme,
    get_palette,
)
from audify.gui.widgets import (
    FluentSlider,
    FluentToggle,
    create_fluent_entry,
    create_modern_btn,
)

SIDEBAR_WIDTH = 304

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


def _card(
    parent: tk.Misc,
    padding: int,
    pal: dict[str, str],
    card_borders: list[tk.Frame],
    card_frames: list[tk.Frame],
) -> tuple[tk.Frame, tk.Frame]:
    """Create a 1px bordered WinUI 3 surface card and register it for live re-theming."""
    border = tk.Frame(parent, bg=pal["border"], bd=0)
    body = tk.Frame(border, bg=pal["card"], bd=0)
    body.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)
    inner = tk.Frame(body, bg=pal["card"], bd=0)
    inner.pack(fill=tk.BOTH, expand=True, padx=padding, pady=padding)
    card_borders.append(border)
    card_frames.extend((body, inner))
    return border, inner


def _configure_styles(root: tk.Tk, S, pal: dict[str, str]) -> ttk.Style:
    """Configure or update ttk styles for the active WinUI 3 Light or Dark palette."""
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(
        ".",
        background=pal["bg"],
        foreground=pal["text"],
        font=("Segoe UI", 10),
    )
    style.configure("TFrame", background=pal["bg"])
    style.configure("TLabel", background=pal["bg"], foreground=pal["text"])

    # Label variants for the WinUI 3 window background and surface cards
    style.configure(
        "Header.TLabel",
        background=pal["bg"],
        foreground=pal["text"],
        font=("Segoe UI Semibold", 17),
    )
    style.configure(
        "Sub.TLabel",
        background=pal["bg"],
        foreground=pal["text_sec"],
        font=("Segoe UI", 9),
    )
    style.configure(
        "Card.TLabel",
        background=pal["card"],
        foreground=pal["text"],
    )
    style.configure(
        "CardTitle.TLabel",
        background=pal["card"],
        foreground=pal["text"],
        font=("Segoe UI Semibold", 15),
    )
    style.configure(
        "CardSub.TLabel",
        background=pal["card"],
        foreground=pal["text_sec"],
        font=("Segoe UI", 9),
    )
    style.configure(
        "Field.TLabel",
        background=pal["card"],
        foreground=pal["text"],
        font=("Segoe UI Semibold", 9),
    )
    style.configure(
        "Caption.TLabel",
        background=pal["card"],
        foreground=pal["text_muted"],
        font=("Segoe UI Semibold", 8),
    )
    style.configure(
        "Value.TLabel",
        background=pal["card"],
        foreground=pal["text_sec"],
        font=("Segoe UI Semibold", 9),
    )
    style.configure(
        "Badge.TLabel",
        background=pal["card"],
        foreground=pal["text_sec"],
        font=("Segoe UI Semibold", 9),
        padding=(S(10), S(3)),
    )

    # Hand-drawn chevron image for WinUI 3 Combobox
    size = S(12)
    chevron = getattr(root, "_chevron_img", None)
    if chevron is None:
        chevron = tk.PhotoImage(master=root, width=size + S(14), height=size)
        root._chevron_img = chevron
    else:
        chevron.blank()

    cx, cy, half = size // 2 + S(4), size // 2 - S(2), S(4)
    for i in range(half + 1):
        for t in range(max(1, S(1.5))):
            chevron.put(pal["text_sec"], (cx - half + i, cy + i - t + 1))
            chevron.put(pal["text_sec"], (cx + half - i, cy + i - t + 1))

    if "Chevron.downarrow" not in style.element_names():
        style.element_create("Chevron.downarrow", "image", chevron, sticky="")

    style.layout(
        "WinUI.TCombobox",
        [
            (
                "Combobox.field",
                {
                    "sticky": "nswe",
                    "children": [
                        ("Chevron.downarrow", {"side": "right", "sticky": "ns"}),
                        (
                            "Combobox.padding",
                            {
                                "expand": "1",
                                "sticky": "nswe",
                                "children": [
                                    ("Combobox.textarea", {"sticky": "nswe"}),
                                ],
                            },
                        ),
                    ],
                },
            ),
        ],
    )
    style.configure(
        "WinUI.TCombobox",
        fieldbackground=pal["field"],
        background=pal["field"],
        foreground=pal["text"],
        bordercolor=pal["border"],
        lightcolor=pal["field"],
        darkcolor=pal["field"],
        arrowcolor=pal["text_sec"],
        arrowsize=S(12),
        selectbackground=pal["field"],
        selectforeground=pal["text"],
        padding=(S(10), S(5)),
    )
    style.map(
        "WinUI.TCombobox",
        fieldbackground=[("readonly", pal["field"])],
        background=[("active", pal["control_hover"]), ("readonly", pal["field"])],
        foreground=[("readonly", pal["text"])],
        selectbackground=[("readonly", pal["field"])],
        selectforeground=[("readonly", pal["text"])],
        bordercolor=[
            ("focus", pal["accent_light"]),
            ("hover", pal["border_hover"]),
        ],
        lightcolor=[("focus", pal["field"])],
        darkcolor=[("focus", pal["field"])],
        arrowcolor=[("hover", pal["text"]), ("focus", pal["text"])],
    )
    root.option_add("*TCombobox*Listbox.background", pal["card"])
    root.option_add("*TCombobox*Listbox.foreground", pal["text"])
    root.option_add("*TCombobox*Listbox.selectBackground", pal["accent"])
    root.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")
    root.option_add("*TCombobox*Listbox.font", ("Segoe UI", 10))
    root.option_add("*TCombobox*Listbox.borderWidth", 0)
    root.option_add("*TCombobox*Listbox.highlightThickness", 0)

    # Flat WinUI 3 Treeview
    style.layout("Dict.Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
    style.configure(
        "Dict.Treeview",
        background=pal["card"],
        foreground=pal["text"],
        fieldbackground=pal["card"],
        rowheight=S(32),
        borderwidth=0,
        font=("Segoe UI", 10),
    )
    style.map(
        "Dict.Treeview",
        background=[("selected", pal["accent"])],
        foreground=[("selected", "#ffffff")],
    )
    style.configure(
        "Dict.Treeview.Heading",
        background=pal["card"],
        foreground=pal["text_muted"],
        font=("Segoe UI Semibold", 9),
        borderwidth=0,
        relief="flat",
        lightcolor=pal["card"],
        darkcolor=pal["card"],
        bordercolor=pal["card"],
        padding=(S(10), S(8)),
    )
    style.map(
        "Dict.Treeview.Heading",
        background=[("active", pal["card"])],
        foreground=[("active", pal["text"])],
    )

    # Minimalist WinUI 3 scrollbar
    style.layout(
        "Vertical.TScrollbar",
        [
            (
                "Vertical.Scrollbar.trough",
                {
                    "children": [
                        (
                            "Vertical.Scrollbar.thumb",
                            {"expand": "1", "sticky": "nswe"},
                        )
                    ],
                    "sticky": "ns",
                },
            )
        ],
    )
    style.configure(
        "Vertical.TScrollbar",
        background=pal["scrollbar"],
        troughcolor=pal["card"],
        bordercolor=pal["card"],
        arrowcolor=pal["card"],
        lightcolor=pal["scrollbar"],
        darkcolor=pal["scrollbar"],
        gripcount=0,
        width=S(8),
    )
    style.map(
        "Vertical.TScrollbar",
        background=[
            ("active", pal["scrollbar_hover"]),
            ("pressed", pal["scrollbar_hover"]),
        ],
    )
    return style


def show_dictionary_ui(daemon: Any) -> None:
    """Open the Audify Settings window, or bring the existing window to the front."""
    global _instance_open
    with _instance_lock:
        if _instance_open:
            _raise_request.set()
            return
        _instance_open = True
    try:
        _run_settings_window(daemon)
    finally:
        with _instance_lock:
            _instance_open = False


def _run_settings_window(daemon: Any) -> None:
    """Build and run the Windows 11 WinUI 3 Settings & Dictionary GUI."""
    _raise_request.clear()
    _enable_dpi_awareness()

    cur_theme_mode = str(daemon.config.get("theme_mode", "system"))
    pal = get_palette(cur_theme_mode)
    active_pal_mode = [pal["mode"]]

    root = tk.Tk()
    root.title("Audify Settings")
    root.attributes("-topmost", True)
    root.configure(bg=pal["bg"])

    scale = root.winfo_fpixels("1i") / 96.0

    def S(px: float) -> int:
        return int(round(px * scale))

    root.update_idletasks()
    apply_window_theme(root, pal)
    width, height = S(1040), S(845)
    x = (root.winfo_screenwidth() // 2) - (width // 2)
    y = (root.winfo_screenheight() // 2) - (height // 2)
    root.geometry(f"{width}x{height}+{x}+{y}")
    root.resizable(True, True)
    root.minsize(S(900), S(775))

    _configure_styles(root, S, pal)

    # Collections for instant live theme switching
    bg_frames: list[tk.Misc] = [root]
    card_frames: list[tk.Misc] = []
    card_borders: list[tk.Misc] = []
    dividers: list[tk.Frame] = []
    context_menus: list[tk.Menu] = []

    tts_dict_ref: dict[str, str] = daemon.config.setdefault("pronunciation_dict", {})
    stt_vocab_ref: dict[str, str] = daemon.config.setdefault("stt_vocabulary", {})
    active_mode: list[str] = ["tts"]

    def get_active_dict() -> dict[str, str]:
        return stt_vocab_ref if active_mode[0] == "stt" else tts_dict_ref

    main_frame = tk.Frame(root, bg=pal["bg"])
    main_frame.pack(fill=tk.BOTH, expand=True, padx=S(20), pady=S(20))
    main_frame.grid_columnconfigure(1, weight=1)
    main_frame.grid_rowconfigure(0, weight=1)
    bg_frames.append(main_frame)

    # -----------------------------------------------------------------------
    # LEFT COLUMN: WinUI 3 Settings Sidebar
    # -----------------------------------------------------------------------
    sidebar_border, sidebar = _card(
        main_frame, padding=S(18), pal=pal, card_borders=card_borders, card_frames=card_frames
    )
    sidebar_border.configure(width=S(SIDEBAR_WIDTH))
    sidebar_border.pack_propagate(False)
    sidebar_border.grid(row=0, column=0, sticky="ns", padx=(0, S(20)))

    # Header with title and Appearance (System / Light / Dark) segmented pill
    title_row = tk.Frame(sidebar, bg=pal["card"])
    title_row.pack(fill=tk.X, pady=(0, S(8)))
    card_frames.append(title_row)

    title_left = tk.Frame(title_row, bg=pal["card"])
    title_left.pack(side=tk.LEFT, fill=tk.X, expand=True)
    card_frames.append(title_left)
    ttk.Label(title_left, text="Settings", style="CardTitle.TLabel").pack(anchor=tk.W)
    ttk.Label(
        title_left, text=f"Audify v{__version__}", style="CardSub.TLabel"
    ).pack(anchor=tk.W)

    def section_caption(text: str) -> None:
        ttk.Label(sidebar, text=text.upper(), style="Caption.TLabel").pack(
            anchor=tk.W, pady=(0, S(6))
        )

    def field_label(text: str) -> None:
        ttk.Label(sidebar, text=text, style="Field.TLabel").pack(
            anchor=tk.W, pady=(0, S(4))
        )

    def add_divider(parent: tk.Misc, pad_top: int, pad_bot: int) -> None:
        div = tk.Frame(parent, bg=pal["divider"], height=1)
        div.pack(fill=tk.X, pady=(pad_top, pad_bot))
        dividers.append(div)

    # Appearance: System / Light / Dark segmented WinUI 3 selector
    section_caption("Appearance")
    theme_bar = tk.Frame(
        sidebar,
        bg=pal["field"],
        highlightthickness=1,
        highlightbackground=pal["border"],
    )
    theme_bar.pack(fill=tk.X, pady=(0, S(10)))
    theme_btns: dict[str, tk.Label] = {}

    def _style_theme_buttons(cur_mode: str, p: dict[str, str]) -> None:
        theme_bar.config(bg=p["field"], highlightbackground=p["border"])
        for label_txt, mode_val in THEME_MODES.items():
            lbl_w = theme_btns[mode_val]
            selected = mode_val == cur_mode
            lbl_w.config(
                bg=p["accent"] if selected else p["field"],
                fg="#ffffff" if selected else p["text_sec"],
            )

    def _on_select_theme(new_mode: str) -> None:
        if hasattr(daemon, "set_theme_mode"):
            daemon.set_theme_mode(new_mode)
        else:
            daemon.config["theme_mode"] = new_mode
            save_config(daemon.config)
        apply_theme_to_all()

    for label_txt, mode_val in THEME_MODES.items():
        b_lbl = tk.Label(
            theme_bar,
            text=label_txt,
            font=("Segoe UI Semibold", 9),
            padx=S(8),
            pady=S(4),
            cursor="hand2",
        )
        b_lbl.pack(side=tk.LEFT, fill=tk.X, expand=True)
        b_lbl.bind("<Button-1>", lambda _e, m=mode_val: _on_select_theme(m))
        theme_btns[mode_val] = b_lbl

    _style_theme_buttons(cur_theme_mode, pal)

    add_divider(sidebar, 0, S(8))
    section_caption("Playback")

    # Live Volume Controller
    vol_header = tk.Frame(sidebar, bg=pal["card"])
    vol_header.pack(fill=tk.X)
    card_frames.append(vol_header)
    ttk.Label(vol_header, text="Volume", style="Field.TLabel").pack(side=tk.LEFT)
    vol_percent = int(daemon.config.get("volume", 1.0) * 100)
    vol_var = tk.IntVar(value=vol_percent)
    vol_label = ttk.Label(vol_header, text=f"{vol_percent}%", style="Value.TLabel")
    vol_label.pack(side=tk.RIGHT)

    vol_slider = FluentSlider(
        sidebar,
        from_=0,
        to=100,
        variable=vol_var,
        bg=pal["card"],
        active_color=pal["accent_light"],
        track_color=pal["border_hover"],
        knob_color=pal["slider_knob"],
        knob_ring=pal["slider_knob_ring"],
        width=S(200),
        height=S(24),
    )
    vol_slider.pack(fill=tk.X, pady=(S(2), S(8)))

    def on_volume_change(val: float) -> None:
        daemon.set_volume(float(val) / 100.0)
        vol_label.config(text=f"{int(float(val))}%")

    vol_slider.config(command=on_volume_change)

    # Voice Combobox
    field_label("Voice")
    voice_names = list(VOICES.keys())
    current_voice_id = daemon.config.get("voice", "en-US-JennyNeural")
    current_voice_name = next(
        (n for n, v in VOICES.items() if v == current_voice_id), voice_names[0]
    )
    voice_var = tk.StringVar(value=current_voice_name)
    voice_combo = ttk.Combobox(
        sidebar,
        textvariable=voice_var,
        values=voice_names,
        state="readonly",
        style="WinUI.TCombobox",
        font=("Segoe UI", 10),
        width=10,
    )
    voice_combo.pack(fill=tk.X, pady=(0, S(8)))

    def on_voice_select(event) -> None:
        daemon.set_voice(VOICES[voice_var.get()])
        voice_combo.selection_clear()

    voice_combo.bind("<<ComboboxSelected>>", on_voice_select)

    # Speed Rate Combobox
    field_label("Speed")
    rate_names = list(RATES.keys())
    current_rate_val = daemon.config.get("rate", "+50%")
    current_rate_name = next(
        (n for n, r in RATES.items() if r == current_rate_val), rate_names[3]
    )
    rate_var = tk.StringVar(value=current_rate_name)
    rate_combo = ttk.Combobox(
        sidebar,
        textvariable=rate_var,
        values=rate_names,
        state="readonly",
        style="WinUI.TCombobox",
        font=("Segoe UI", 10),
        width=10,
    )
    rate_combo.pack(fill=tk.X, pady=(0, S(10)))

    def on_rate_select(event) -> None:
        daemon.set_rate(RATES[rate_var.get()])
        rate_combo.selection_clear()

    rate_combo.bind("<<ComboboxSelected>>", on_rate_select)

    add_divider(sidebar, 0, S(8))
    section_caption("Reading")

    read_copy_var = tk.BooleanVar(value=daemon.config.get("read_on_copy", True))

    def on_toggle_read_copy() -> None:
        daemon.toggle_read_on_copy()
        read_copy_var.set(daemon.config.get("read_on_copy", True))

    read_copy_row = tk.Frame(sidebar, bg=pal["card"], cursor="hand2")
    read_copy_row.pack(fill=tk.X, pady=(0, S(6)))
    card_frames.append(read_copy_row)
    read_copy_chk = FluentToggle(
        read_copy_row,
        variable=read_copy_var,
        command=on_toggle_read_copy,
        bg=pal["card"],
        active_color=pal["accent_light"],
        track_color=pal["card"],
        border_color=pal["text_muted"],
        knob_on_color=pal["toggle_knob_on"],
        knob_off_color=pal["toggle_knob_off"],
        width=S(40),
        height=S(20),
    )
    read_copy_chk.pack(side=tk.RIGHT, padx=(S(10), 0))
    read_copy_text = tk.Frame(read_copy_row, bg=pal["card"], cursor="hand2")
    read_copy_text.pack(side=tk.LEFT, fill=tk.X, expand=True)
    card_frames.append(read_copy_text)
    read_copy_title = ttk.Label(
        read_copy_text, text="Read on copy", style="Field.TLabel", cursor="hand2"
    )
    read_copy_title.pack(anchor=tk.W)
    read_copy_desc = ttk.Label(
        read_copy_text,
        text="Speak copied text aloud (Ctrl+C)",
        style="CardSub.TLabel",
        cursor="hand2",
    )
    read_copy_desc.pack(anchor=tk.W)
    for w in (read_copy_row, read_copy_text, read_copy_title, read_copy_desc):
        w.bind("<Button-1>", lambda e: read_copy_chk._on_click(None))

    skip_var = tk.BooleanVar(value=daemon.config.get("skip_code_blocks", True))

    def on_toggle_skip() -> None:
        daemon.toggle_skip_code_blocks()
        skip_var.set(daemon.config.get("skip_code_blocks", True))

    skip_row = tk.Frame(sidebar, bg=pal["card"], cursor="hand2")
    skip_row.pack(fill=tk.X)
    card_frames.append(skip_row)
    skip_chk = FluentToggle(
        skip_row,
        variable=skip_var,
        command=on_toggle_skip,
        bg=pal["card"],
        active_color=pal["accent_light"],
        track_color=pal["card"],
        border_color=pal["text_muted"],
        knob_on_color=pal["toggle_knob_on"],
        knob_off_color=pal["toggle_knob_off"],
        width=S(40),
        height=S(20),
    )
    skip_chk.pack(side=tk.RIGHT, padx=(S(10), 0))
    skip_text = tk.Frame(skip_row, bg=pal["card"], cursor="hand2")
    skip_text.pack(side=tk.LEFT, fill=tk.X, expand=True)
    card_frames.append(skip_text)
    skip_title = ttk.Label(
        skip_text, text="Skip code blocks", style="Field.TLabel", cursor="hand2"
    )
    skip_title.pack(anchor=tk.W)
    skip_desc = ttk.Label(
        skip_text,
        text="Don't read fenced code aloud",
        style="CardSub.TLabel",
        cursor="hand2",
    )
    skip_desc.pack(anchor=tk.W)
    for w in (skip_row, skip_text, skip_title, skip_desc):
        w.bind("<Button-1>", lambda e: skip_chk._on_click(None))

    add_divider(sidebar, S(8), S(8))
    section_caption("Dictation & System Tools")

    stt_enabled_var = tk.BooleanVar(value=daemon.config.get("stt_enabled", True))

    def _sync_stt_controls_state() -> None:
        stt_on = bool(daemon.config.get("stt_enabled", True))
        stt_obj = getattr(daemon, "stt", None)
        if not stt_on:
            dictate_btn.config(state="disabled", text="Dictation Disabled (Light Mode)")
            delay_cb.config(state="disabled")
        else:
            delay_cb.config(state="readonly")
            if stt_obj is not None and stt_obj.is_transcribing:
                dictate_btn.config(state="disabled", text="Transcribing...")
            elif stt_obj is not None and stt_obj.is_recording:
                dictate_btn.config(state="normal", text="Stop & Transcribe")
            else:
                dictate_btn.config(state="normal", text="Start Dictation (Bark 3.0)")

    def on_toggle_stt_enabled() -> None:
        if hasattr(daemon, "toggle_stt_enabled"):
            daemon.toggle_stt_enabled()
        else:
            cur = bool(daemon.config.get("stt_enabled", True))
            daemon.config["stt_enabled"] = not cur
            save_config(daemon.config)
            if hasattr(daemon, "stt"):
                daemon.stt.set_enabled(not cur)
        now_on = bool(daemon.config.get("stt_enabled", True))
        stt_enabled_var.set(now_on)
        _sync_stt_controls_state()
        p_now = get_palette(str(daemon.config.get("theme_mode", "system")))
        tools_status_lbl.config(
            text=(
                "Speech-to-text enabled (Bark 3.0 ready)"
                if now_on
                else "Speech-to-text unloaded (light mode active)"
            ),
            foreground=p_now["success"] if now_on else p_now["text_sec"],
        )

    stt_row = tk.Frame(sidebar, bg=pal["card"], cursor="hand2")
    stt_row.pack(fill=tk.X, pady=(0, S(8)))
    card_frames.append(stt_row)
    stt_chk = FluentToggle(
        stt_row,
        variable=stt_enabled_var,
        command=on_toggle_stt_enabled,
        bg=pal["card"],
        active_color=pal["accent_light"],
        track_color=pal["card"],
        border_color=pal["text_muted"],
        knob_on_color=pal["toggle_knob_on"],
        knob_off_color=pal["toggle_knob_off"],
        width=S(40),
        height=S(20),
    )
    stt_chk.pack(side=tk.RIGHT, padx=(S(10), 0))
    stt_text = tk.Frame(stt_row, bg=pal["card"], cursor="hand2")
    stt_text.pack(side=tk.LEFT, fill=tk.X, expand=True)
    card_frames.append(stt_text)
    stt_title = ttk.Label(
        stt_text, text="Speech-to-text", style="Field.TLabel", cursor="hand2"
    )
    stt_title.pack(anchor=tk.W)
    stt_desc = ttk.Label(
        stt_text,
        text="Unload model when off (light mode)",
        style="CardSub.TLabel",
        cursor="hand2",
    )
    stt_desc.pack(anchor=tk.W)
    for w in (stt_row, stt_text, stt_title, stt_desc):
        w.bind("<Button-1>", lambda e: stt_chk._on_click(None))

    tools_status_lbl = ttk.Label(
        sidebar,
        text="Reset Bluetooth driver or Windows icon cache",
        style="CardSub.TLabel",
    )

    def _on_bt_click() -> None:
        if is_bluetooth_restarting():
            return
        p_now = get_palette(str(daemon.config.get("theme_mode", "system")))
        bt_btn.config(state="disabled", text="Restarting...")
        tools_status_lbl.config(
            text="Running elevated Bluetooth reset...", foreground=p_now["text_muted"]
        )

        def _done(ok: bool, msg: str) -> None:
            def _ui_update() -> None:
                try:
                    p_cur = get_palette(str(daemon.config.get("theme_mode", "system")))
                    bt_btn.config(state="normal", text="Restart Bluetooth")
                    tools_status_lbl.config(
                        text="Bluetooth restarted & audio reconnected" if ok else msg,
                        foreground=p_cur["success"] if ok else p_cur["danger"],
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
        p_now = get_palette(str(daemon.config.get("theme_mode", "system")))
        icon_btn.config(state="disabled", text="Resetting...")
        tools_status_lbl.config(
            text="Clearing icon cache & restarting Explorer...",
            foreground=p_now["text_muted"],
        )

        def _done(ok: bool, msg: str) -> None:
            def _ui_update() -> None:
                try:
                    p_cur = get_palette(str(daemon.config.get("theme_mode", "system")))
                    icon_btn.config(state="normal", text="Reset Icon Cache")
                    tools_status_lbl.config(
                        text="Icon cache reset & Explorer restarted" if ok else msg,
                        foreground=p_cur["success"] if ok else p_cur["danger"],
                    )
                except Exception:
                    pass

            try:
                root.after(0, _ui_update)
            except Exception:
                pass

        reset_icon_cache(daemon=daemon, on_complete=_done)

    tools_row = tk.Frame(sidebar, bg=pal["card"])
    tools_row.pack(fill=tk.X, pady=(0, S(6)))
    card_frames.append(tools_row)
    tools_row.grid_columnconfigure(0, weight=1, uniform="tools")
    tools_row.grid_columnconfigure(1, weight=1, uniform="tools")

    bt_btn = create_modern_btn(
        tools_row,
        "Restart Bluetooth",
        _on_bt_click,
        primary=False,
        pal=pal,
    )
    bt_btn.config(padx=S(6))
    bt_btn.grid(row=0, column=0, sticky="ew", padx=(0, S(4)))

    icon_btn = create_modern_btn(
        tools_row,
        "Reset Icon Cache",
        _on_icon_cache_click,
        primary=False,
        pal=pal,
    )
    icon_btn.config(padx=S(6))
    icon_btn.grid(row=0, column=1, sticky="ew", padx=(S(4), 0))

    def _on_dictate_click() -> None:
        stt = getattr(daemon, "stt", None)
        if stt is None or stt.is_transcribing:
            return
        p_now = get_palette(str(daemon.config.get("theme_mode", "system")))

        def _done(ok: bool, msg: str) -> None:
            def _ui_update() -> None:
                try:
                    _sync_stt_controls_state()
                    if not daemon.config.get("stt_enabled", True):
                        return
                    p_cur = get_palette(str(daemon.config.get("theme_mode", "system")))
                    preview = msg if len(msg) <= 42 else msg[:39] + "..."
                    tools_status_lbl.config(
                        text=f"Dictated: {preview}" if ok else msg,
                        foreground=p_cur["success"] if ok else p_cur["danger"],
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
            tools_status_lbl.config(
                text="Listening... speak now (auto-stops on pause)",
                foreground=p_now["accent_light"],
            )
        else:
            dictate_btn.config(state="disabled", text="Transcribing...")
            tools_status_lbl.config(
                text="Bark 3.0 Clean transcribing...",
                foreground=p_now["text_muted"],
            )
            stt.stop_and_transcribe(on_complete=_done)

    dictate_btn = create_modern_btn(
        sidebar,
        "Start Dictation (Bark 3.0)",
        _on_dictate_click,
        primary=True,
        accent_color=pal["accent"],
        accent_hover=pal["accent_hover"],
        pal=pal,
    )
    dictate_btn.pack(fill=tk.X, pady=(0, S(6)))

    delay_row = tk.Frame(sidebar, bg=pal["card"])
    delay_row.pack(fill=tk.X, pady=(0, S(4)))
    card_frames.append(delay_row)
    ttk.Label(delay_row, text="Auto-stop silence", style="Field.TLabel").pack(
        side=tk.LEFT
    )
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
        style="WinUI.TCombobox",
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

    _sync_stt_controls_state()
    tools_status_lbl.pack(anchor=tk.W)

    # Footer: customizable global shortcuts
    footer = tk.Frame(sidebar, bg=pal["card"])
    footer.pack(side=tk.BOTTOM, fill=tk.X)
    card_frames.append(footer)
    add_divider(footer, 0, S(8))
    ttk.Label(
        footer, text="SHORTCUTS (CLICK TO CHANGE)", style="Caption.TLabel"
    ).pack(anchor=tk.W, pady=(0, S(5)))

    def _fmt_hk(hk_str: str) -> str:
        return format_hotkey_label(hk_str)

    recording_target: list[tuple[str, tk.Label, str] | None] = [None]
    last_mod_tap: list[tuple[str, float]] = [("", 0.0)]
    hotkey_badges: dict[str, tk.Label] = {}

    def _cancel_hotkey_record() -> None:
        cur = recording_target[0]
        if cur is None:
            return
        p_now = get_palette(str(daemon.config.get("theme_mode", "system")))
        cfg_key, badge_lbl, default_hk = cur
        recording_target[0] = None
        last_mod_tap[0] = ("", 0.0)
        saved_hk = str(daemon.config.get(cfg_key, default_hk))
        badge_lbl.config(
            text=_fmt_hk(saved_hk),
            fg=p_now["text"],
            highlightbackground=p_now["border"],
        )

    def _start_hotkey_record(
        cfg_key: str, badge_lbl: tk.Label, default_hk: str
    ) -> None:
        _cancel_hotkey_record()
        p_now = get_palette(str(daemon.config.get("theme_mode", "system")))
        recording_target[0] = (cfg_key, badge_lbl, default_hk)
        last_mod_tap[0] = ("", 0.0)
        badge_lbl.config(
            text="Press keys...",
            fg=p_now["accent_light"],
            highlightbackground=p_now["accent_light"],
        )
        root.focus_force()

    def _on_hotkey_capture(event: tk.Event) -> str | None:
        cur = recording_target[0]
        if cur is None:
            return None
        p_now = get_palette(str(daemon.config.get("theme_mode", "system")))
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
                if hasattr(daemon, "set_hotkey") and daemon.set_hotkey(
                    cfg_key, combo_str
                ):
                    badge_lbl.config(
                        text=_fmt_hk(combo_str),
                        fg=p_now["text"],
                        highlightbackground=p_now["border"],
                    )
                    tools_status_lbl.config(
                        text=f"Shortcut set to {_fmt_hk(combo_str)}",
                        foreground=p_now["success"],
                    )
                return "break"
            last_mod_tap[0] = (ks, now_m)
            return "break"

        if ks in (
            "alt_r",
            "shift_r",
            "win_l",
            "win_r",
            "super_l",
            "super_r",
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
        if (u32.GetAsyncKeyState(0x5B) & 0x8000) or (
            u32.GetAsyncKeyState(0x5C) & 0x8000
        ):
            mods.append("win")

        key_map = {
            "space": "space",
            "return": "enter",
            "tab": "tab",
            "back": "backspace",
            "backspace": "backspace",
            "insert": "insert",
            "delete": "delete",
            "prior": "pageup",
            "next": "pagedown",
            "home": "home",
            "end": "end",
            "up": "up",
            "down": "down",
            "left": "left",
            "right": "right",
            "grave": "`",
            "minus": "-",
            "equal": "=",
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
                foreground=p_now["danger"],
            )
            return "break"

        combo_str = "+".join(mods + [key_tok])
        recording_target[0] = None
        if hasattr(daemon, "set_hotkey") and daemon.set_hotkey(cfg_key, combo_str):
            badge_lbl.config(
                text=_fmt_hk(combo_str),
                fg=p_now["text"],
                highlightbackground=p_now["border"],
            )
            tools_status_lbl.config(
                text=f"Shortcut set to {_fmt_hk(combo_str)}",
                foreground=p_now["success"],
            )
        else:
            _cancel_hotkey_record()
            tools_status_lbl.config(
                text="Invalid shortcut combination", foreground=p_now["danger"]
            )
        return "break"

    root.bind("<KeyPress>", _on_hotkey_capture, add="+")

    def _make_hotkey_row(
        label_text: str, cfg_key: str, default_hk: str, pad_bottom: int
    ) -> None:
        row = tk.Frame(footer, bg=pal["card"])
        row.pack(fill=tk.X, pady=(0, pad_bottom))
        card_frames.append(row)
        ttk.Label(row, text=label_text, style="CardSub.TLabel").pack(side=tk.LEFT)
        cur_hk = str(daemon.config.get(cfg_key, default_hk))
        badge = tk.Label(
            row,
            text=_fmt_hk(cur_hk),
            bg=pal["field"],
            fg=pal["text"],
            font=("Segoe UI Semibold", 8),
            padx=S(8),
            pady=S(2),
            bd=0,
            highlightthickness=1,
            highlightbackground=pal["border"],
            cursor="hand2",
        )
        badge.pack(side=tk.RIGHT)
        badge.bind(
            "<Button-1>",
            lambda e, k=cfg_key, b=badge, d=default_hk: _start_hotkey_record(k, b, d),
        )
        hotkey_badges[cfg_key] = badge

    _make_hotkey_row("Dictate (Bark 3.0)", "dictation_hotkey", "double_lctrl", S(4))
    _make_hotkey_row("Paste last dictation", "paste_last_hotkey", "ctrl+alt+v", S(4))
    _make_hotkey_row("Stop playback", "stop_hotkey", "ctrl+alt+s", S(2))

    # -----------------------------------------------------------------------
    # RIGHT COLUMN: Dictionary & Vocabulary Panel
    # -----------------------------------------------------------------------
    dict_frame = tk.Frame(main_frame, bg=pal["bg"])
    dict_frame.grid(row=0, column=1, sticky="nsew")
    bg_frames.append(dict_frame)

    header_frame = tk.Frame(dict_frame, bg=pal["bg"])
    header_frame.pack(fill=tk.X, pady=(0, S(10)))
    bg_frames.append(header_frame)
    title_box = tk.Frame(header_frame, bg=pal["bg"])
    title_box.pack(side=tk.LEFT, fill=tk.X, expand=True)
    bg_frames.append(title_box)
    header_title_lbl = ttk.Label(
        title_box, text="Pronunciation Dictionary", style="Header.TLabel"
    )
    header_title_lbl.pack(anchor=tk.W)
    header_sub_lbl = ttk.Label(
        title_box,
        text="Tell Audify how to say symbols, acronyms and technical terms.",
        style="Sub.TLabel",
    )
    header_sub_lbl.pack(anchor=tk.W, pady=(S(2), 0))

    count_border = tk.Frame(header_frame, bg=pal["border"])
    count_border.pack(side=tk.RIGHT, anchor=tk.N, pady=(S(6), 0))
    card_borders.append(count_border)
    count_label = ttk.Label(count_border, style="Badge.TLabel")
    count_label.pack(padx=1, pady=1)

    # Segmented mode switcher: TTS Pronunciation vs STT Vocabulary (Auto-Learn)
    mode_bar = tk.Frame(
        dict_frame,
        bg=pal["card"],
        highlightthickness=1,
        highlightbackground=pal["border"],
    )
    mode_bar.pack(fill=tk.X, pady=(0, S(10)))
    mode_tts_btn = tk.Label(
        mode_bar,
        text="TTS Pronunciation (Read Aloud)",
        bg=pal["accent"],
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
        bg=pal["card"],
        fg=pal["text_sec"],
        font=("Segoe UI Semibold", 9),
        padx=S(14),
        pady=S(6),
        cursor="hand2",
    )
    mode_stt_btn.pack(side=tk.LEFT, fill=tk.X, expand=True)

    def _style_mode_bar(p: dict[str, str]) -> None:
        mode_bar.config(bg=p["card"], highlightbackground=p["border"])
        if active_mode[0] == "stt":
            mode_stt_btn.config(bg=p["accent"], fg="#ffffff")
            mode_tts_btn.config(bg=p["card"], fg=p["text_sec"])
        else:
            mode_tts_btn.config(bg=p["accent"], fg="#ffffff")
            mode_stt_btn.config(bg=p["card"], fg=p["text_sec"])

    # Search bar
    search_var = tk.StringVar()
    search_entry_frame, search_entry = create_fluent_entry(
        dict_frame,
        textvariable=search_var,
        bg=pal["card"],
        border_color=pal["border"],
        active_color=pal["accent_light"],
        fg=pal["text"],
        placeholder="Search rules…   (Ctrl+F)",
        placeholder_color=pal["placeholder"],
    )
    search_entry_frame.pack(fill=tk.X, pady=(0, S(12)))

    def add_context_menu(widget: tk.Entry) -> None:
        menu = tk.Menu(
            widget,
            tearoff=0,
            bg=pal["card"],
            fg=pal["text"],
            selectcolor=pal["accent"],
            activebackground=pal["accent"],
            activeforeground="#ffffff",
            bd=1,
            relief="solid",
        )
        menu.add_command(label="Cut", command=lambda: widget.event_generate("<<Cut>>"))
        menu.add_command(
            label="Copy", command=lambda: widget.event_generate("<<Copy>>")
        )
        menu.add_command(
            label="Paste", command=lambda: widget.event_generate("<<Paste>>")
        )
        menu.add_separator()
        menu.add_command(
            label="Select All", command=lambda: widget.select_range(0, tk.END)
        )
        context_menus.append(menu)

        def show_menu(event) -> str:
            menu.tk_popup(event.x_root, event.y_root)
            return "break"

        widget.bind("<Button-3>", show_menu)

    add_context_menu(search_entry)

    action_frame = tk.Frame(dict_frame, bg=pal["bg"])
    action_frame.pack(side=tk.BOTTOM, fill=tk.X)
    bg_frames.append(action_frame)

    editor_border, editor = _card(
        dict_frame,
        padding=S(14),
        pal=pal,
        card_borders=card_borders,
        card_frames=card_frames,
    )
    editor_border.pack(side=tk.BOTTOM, fill=tk.X, pady=(0, S(14)))

    # Table card
    table_border = tk.Frame(dict_frame, bg=pal["border"], bd=0)
    table_border.pack(fill=tk.BOTH, expand=True, pady=(0, S(14)))
    card_borders.append(table_border)
    table_container = tk.Frame(table_border, bg=pal["card"], bd=0)
    table_container.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)
    card_frames.append(table_container)

    columns = ("row_num", "word", "pronunciation")
    tree = ttk.Treeview(
        table_container,
        columns=columns,
        show="headings",
        style="Dict.Treeview",
        selectmode="extended",
    )
    tree.heading("row_num", text="#", anchor=tk.CENTER)
    tree.heading("word", text="WORD", anchor=tk.W)
    tree.heading("pronunciation", text="SPOKEN AS", anchor=tk.W)
    tree.column("row_num", width=S(52), minwidth=S(44), stretch=False, anchor=tk.CENTER)
    tree.column("word", width=S(200), minwidth=S(100), stretch=False, anchor=tk.W)
    tree.column(
        "pronunciation", width=S(220), minwidth=S(120), stretch=True, anchor=tk.W
    )
    tree.tag_configure("odd", background=pal["row_alt"])
    tree.tag_configure("even", background=pal["card"])

    scrollbar = ttk.Scrollbar(
        table_container,
        orient=tk.VERTICAL,
        command=tree.yview,
        style="Vertical.TScrollbar",
    )
    tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, pady=(0, S(4)))

    def on_tree_scroll(first: str, last: str) -> None:
        if float(first) <= 0.0 and float(last) >= 1.0:
            scrollbar.pack_forget()
        elif not scrollbar.winfo_ismapped():
            scrollbar.pack(
                side=tk.RIGHT, fill=tk.Y, padx=(0, S(3)), pady=S(3), before=tree
            )
        scrollbar.set(first, last)

    tree.configure(yscrollcommand=on_tree_scroll)

    empty_label = tk.Label(
        table_container,
        bg=pal["card"],
        fg=pal["text_muted"],
        font=("Segoe UI", 10),
        justify=tk.CENTER,
    )

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
            tree.insert(
                "",
                tk.END,
                iid=w,
                values=(shown, w, s),
                tags=("odd" if shown % 2 == 0 else "even",),
            )

        total = len(cur_dict)
        count_label.config(
            text=f"{total} rule{'s' if total != 1 else ''}"
            if not query
            else f"{shown} of {total}"
        )

        if shown == 0:
            if query:
                empty_label.config(text=f"No rules match '{search_var.get().strip()}'")
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

    # Rule editor
    editor.grid_columnconfigure(0, weight=1, uniform="inputs")
    editor.grid_columnconfigure(1, weight=1, uniform="inputs")

    field_left_lbl = ttk.Label(editor, text="Word or symbol", style="Field.TLabel")
    field_left_lbl.grid(row=0, column=0, sticky=tk.W, pady=(0, S(6)))
    field_right_lbl = ttk.Label(editor, text="Spoken as", style="Field.TLabel")
    field_right_lbl.grid(row=0, column=1, sticky=tk.W, padx=(S(12), 0), pady=(0, S(6)))

    word_var = tk.StringVar()
    word_entry_frame, word_entry = create_fluent_entry(
        editor,
        textvariable=word_var,
        width=8,
        bg=pal["field"],
        border_color=pal["border"],
        active_color=pal["accent_light"],
        fg=pal["text"],
        placeholder="e.g. =>",
        placeholder_color=pal["placeholder"],
    )
    word_entry_frame.grid(row=1, column=0, sticky="ew")
    add_context_menu(word_entry)

    spoken_var = tk.StringVar()
    spoken_entry_frame, spoken_entry = create_fluent_entry(
        editor,
        textvariable=spoken_var,
        width=8,
        bg=pal["field"],
        border_color=pal["border"],
        active_color=pal["accent_light"],
        fg=pal["text"],
        placeholder="e.g. arrow",
        placeholder_color=pal["placeholder"],
    )
    spoken_entry_frame.grid(row=1, column=1, sticky="ew", padx=(S(12), 0))
    add_context_menu(spoken_entry)

    status_label = tk.Label(
        action_frame, bg=pal["bg"], fg=pal["text_muted"], font=("Segoe UI", 9)
    )
    status_job: list[str | None] = [None]

    def flash_status(text: str, color: str) -> None:
        if status_job[0]:
            root.after_cancel(status_job[0])
        status_label.config(text=text, fg=color)
        status_job[0] = root.after(2500, lambda: status_label.config(text=""))

    config_lock = getattr(daemon, "_config_lock", None) or threading.Lock()

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
            p_now = get_palette(str(daemon.config.get("theme_mode", "system")))
            cur_dict = get_active_dict()
            existed = w in cur_dict
            with config_lock:
                if active_mode[0] == "stt":
                    set_stt_vocab_rule(daemon.config, w, s)
                    try:
                        stt_obj = getattr(daemon, "stt", None)
                        if stt_obj is not None and hasattr(stt_obj, "hud"):
                            stt_obj.hud.show_learned(w, s)
                    except Exception:
                        pass
                else:
                    set_rule(daemon.config, w, s)
                    save_config(daemon.config)
            refresh_tree(select=w)
            word_var.set("")
            spoken_var.set("")
            word_entry.focus_set()
            flash_status(f"{'Updated' if existed else 'Added'} '{w}'", p_now["success"])
        return "break"

    btn_box = tk.Frame(editor, bg=pal["card"])
    btn_box.grid(row=1, column=2, sticky="e", padx=(S(12), 0))
    card_frames.append(btn_box)
    clear_btn = create_modern_btn(btn_box, "Clear", clear_editor, pal=pal)
    clear_btn.pack(side=tk.RIGHT)
    add_btn = create_modern_btn(
        btn_box,
        "Add Rule",
        add_entry,
        primary=True,
        accent_color=pal["accent"],
        accent_hover=pal["accent_hover"],
        pal=pal,
    )
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
        p_now = get_palette(str(daemon.config.get("theme_mode", "system")))
        _style_mode_bar(p_now)
        if new_mode == "stt":
            header_title_lbl.config(text="Speech-to-Text Vocabulary")
            header_sub_lbl.config(
                text="Custom words, names & places (auto-learns when you double-click and fix a dictated word)."
            )
            tree.heading("word", text="HEARD AS (WRONG)", anchor=tk.W)
            tree.heading("pronunciation", text="WRITTEN AS (CORRECT)", anchor=tk.W)
            field_left_lbl.config(text="Heard as (misheard word)")
            field_right_lbl.config(text="Written as (correct spelling)")
        else:
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
        remove_btn.config(
            state=tk.NORMAL if selected else tk.DISABLED,
            text=(
                f"Remove {len(selected)} Rules"
                if len(selected) > 1
                else "Remove Selected"
            ),
        )
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
        p_now = get_palette(str(daemon.config.get("theme_mode", "system")))
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
        flash_status(
            f"Removed '{words[0]}'" if len(words) == 1 else f"Removed {len(words)} rules",
            p_now["danger"],
        )

    tree.bind("<Delete>", remove_selected)

    remove_btn = create_modern_btn(
        action_frame, "Remove Selected", remove_selected, danger=True, pal=pal
    )
    remove_btn.config(state=tk.DISABLED)
    remove_btn.pack(side=tk.LEFT)
    status_label.pack(side=tk.LEFT, padx=(S(14), 0))

    close_btn = create_modern_btn(action_frame, "Close", root.destroy, pal=pal)
    close_btn.pack(side=tk.RIGHT)

    def apply_theme_to_all() -> None:
        """Re-theme the entire Settings window live when theme_mode or Windows theme changes."""
        mode_pref = str(daemon.config.get("theme_mode", "system"))
        new_pal = get_palette(mode_pref)
        active_pal_mode[0] = new_pal["mode"]

        apply_window_theme(root, new_pal)
        _configure_styles(root, S, new_pal)

        for f in bg_frames:
            f.configure(bg=new_pal["bg"])
        for f in card_frames:
            f.configure(bg=new_pal["card"])
        for b in card_borders:
            b.configure(bg=new_pal["border"])
        for d in dividers:
            d.configure(bg=new_pal["divider"])

        _style_theme_buttons(mode_pref, new_pal)
        _style_mode_bar(new_pal)

        vol_slider.apply_palette(new_pal)
        read_copy_chk.apply_palette(new_pal)
        skip_chk.apply_palette(new_pal)
        stt_chk.apply_palette(new_pal)

        search_entry_frame.apply_palette(new_pal, field_bg=new_pal["card"])  # type: ignore[attr-defined]
        word_entry_frame.apply_palette(new_pal, field_bg=new_pal["field"])  # type: ignore[attr-defined]
        spoken_entry_frame.apply_palette(new_pal, field_bg=new_pal["field"])  # type: ignore[attr-defined]

        for btn in (bt_btn, icon_btn, dictate_btn, clear_btn, add_btn, remove_btn, close_btn):
            btn.apply_palette(new_pal)  # type: ignore[attr-defined]

        for cfg_k, badge_lbl in hotkey_badges.items():
            if recording_target[0] and recording_target[0][0] == cfg_k:
                badge_lbl.config(
                    bg=new_pal["field"],
                    fg=new_pal["accent_light"],
                    highlightbackground=new_pal["accent_light"],
                )
            else:
                badge_lbl.config(
                    bg=new_pal["field"],
                    fg=new_pal["text"],
                    highlightbackground=new_pal["border"],
                )

        tree.tag_configure("odd", background=new_pal["row_alt"])
        tree.tag_configure("even", background=new_pal["card"])
        empty_label.config(bg=new_pal["card"], fg=new_pal["text_muted"])
        status_label.config(bg=new_pal["bg"])
        tools_status_lbl.config(foreground=new_pal["text_sec"])

        for m in context_menus:
            m.config(
                bg=new_pal["card"],
                fg=new_pal["text"],
                selectcolor=new_pal["accent"],
                activebackground=new_pal["accent"],
            )

    def focus_search(event=None) -> str:
        search_entry.focus_set()
        search_entry.select_range(0, tk.END)
        return "break"

    root.bind("<Control-f>", focus_search)
    search_entry.bind("<Escape>", lambda e: search_var.set(""))

    _last_stt_snapshot: list[tuple[tuple[str, str], ...] | None] = [
        tuple(sorted(stt_vocab_ref.items()))
    ]
    _last_theme_pref: list[str] = [cur_theme_mode]

    def sync_gui_values() -> None:
        try:
            if _raise_request.is_set():
                _raise_request.clear()
                root.deiconify()
                root.lift()
                root.focus_force()

            # Detect external theme changes (either from Tray or Windows 11 system theme toggle)
            now_pref = str(daemon.config.get("theme_mode", "system"))
            now_resolved = get_palette(now_pref)["mode"]
            if now_pref != _last_theme_pref[0] or now_resolved != active_pal_mode[0]:
                _last_theme_pref[0] = now_pref
                apply_theme_to_all()

            cur_stt_snap = tuple(sorted(stt_vocab_ref.items()))
            if cur_stt_snap != _last_stt_snapshot[0]:
                _last_stt_snapshot[0] = cur_stt_snap
                if active_mode[0] == "stt":
                    refresh_tree()
                st_str = str(getattr(daemon, "status", "") or "")
                if st_str.startswith("Learned:"):
                    p_now = get_palette(now_pref)
                    flash_status(f"Auto-learned {st_str[8:].strip()}", p_now["success"])

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

            cur_read_copy = daemon.config.get("read_on_copy", True)
            if read_copy_var.get() != cur_read_copy:
                read_copy_var.set(cur_read_copy)

            cur_skip = daemon.config.get("skip_code_blocks", True)
            if skip_var.get() != cur_skip:
                skip_var.set(cur_skip)

            cur_stt_enabled = bool(daemon.config.get("stt_enabled", True))
            if stt_enabled_var.get() != cur_stt_enabled:
                stt_enabled_var.set(cur_stt_enabled)

            cur_delay = float(daemon.config.get("stt_silence_delay", 1.0))
            d_name = next(
                (n for n, d in STT_SILENCE_DELAYS.items() if abs(d - cur_delay) < 0.05),
                None,
            )
            if d_name and delay_var.get() != d_name:
                delay_var.set(d_name)

            _sync_stt_controls_state()

            root.after(1000, sync_gui_values)
        except tk.TclError:
            pass

    sync_gui_values()
    refresh_tree()
    word_entry.focus_set()
    root.mainloop()
