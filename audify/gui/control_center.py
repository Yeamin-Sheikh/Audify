"""Audify Control Center -- settings and pronunciation dictionary GUI."""
from __future__ import annotations

import ctypes
import threading
import tkinter as tk
from tkinter import ttk
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from audify.engine import TTSDaemon

from audify import __version__
from audify.config import VOICES, RATES, save_config
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
    width, height = S(1020), S(640)
    x = (root.winfo_screenwidth() // 2) - (width // 2)
    y = (root.winfo_screenheight() // 2) - (height // 2)
    root.geometry(f"{width}x{height}+{x}+{y}")
    root.resizable(True, True)
    root.minsize(S(880), S(560))

    _configure_styles(root, S)

    dict_ref: dict[str, str] = daemon.config.setdefault("pronunciation_dict", {})

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

    # Footer: stop hotkey hint + version
    footer = tk.Frame(sidebar, bg=CARD_COLOR)
    footer.pack(side=tk.BOTTOM, fill=tk.X)
    tk.Frame(footer, bg=BORDER_COLOR, height=1).pack(fill=tk.X, pady=(0, S(14)))
    hotkey_row = tk.Frame(footer, bg=CARD_COLOR)
    hotkey_row.pack(fill=tk.X, pady=(0, S(10)))
    ttk.Label(hotkey_row, text="Stop playback", style="CardSub.TLabel").pack(side=tk.LEFT)
    hotkey = str(daemon.config.get("stop_hotkey", "ctrl+alt+s"))
    tk.Label(
        hotkey_row,
        text="+".join(part.strip().capitalize() for part in hotkey.split("+")),
        bg=FIELD_COLOR, fg=TEXT_COLOR, font=("Segoe UI Semibold", 8),
        padx=S(8), pady=S(2), bd=0, highlightthickness=1, highlightbackground=BORDER_COLOR,
    ).pack(side=tk.RIGHT)
    ttk.Label(footer, text=f"Version {__version__}", style="CardSub.TLabel").pack(anchor=tk.W)

    # -----------------------------------------------------------------------
    # RIGHT COLUMN: Dictionary Panel
    # -----------------------------------------------------------------------
    dict_frame = tk.Frame(main_frame, bg=BG_COLOR)
    dict_frame.grid(row=0, column=1, sticky="nsew")

    # Header: title + subtitle on the left, rule count badge on the right
    header_frame = tk.Frame(dict_frame, bg=BG_COLOR)
    header_frame.pack(fill=tk.X, pady=(0, S(14)))
    title_box = tk.Frame(header_frame, bg=BG_COLOR)
    title_box.pack(side=tk.LEFT, fill=tk.X, expand=True)
    ttk.Label(title_box, text="Pronunciation Dictionary", style="Header.TLabel").pack(anchor=tk.W)
    ttk.Label(
        title_box,
        text="Tell Audify how to say symbols, acronyms and technical terms.",
        style="Sub.TLabel",
    ).pack(anchor=tk.W, pady=(S(2), 0))

    count_border = tk.Frame(header_frame, bg=BORDER_COLOR)
    count_border.pack(side=tk.RIGHT, anchor=tk.N, pady=(S(6), 0))
    count_label = ttk.Label(count_border, style="Badge.TLabel")
    count_label.pack(padx=1, pady=1)

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
        query = search_var.get().strip().lower()
        tree.delete(*tree.get_children())
        shown = 0
        for w in sorted(dict_ref.keys(), key=str.lower):
            s = dict_ref[w]
            if query and query not in w.lower() and query not in s.lower():
                continue
            shown += 1
            tree.insert("", tk.END, iid=w, values=(shown, w, s), tags=("odd" if shown % 2 == 0 else "even",))

        total = len(dict_ref)
        count_label.config(text=f"{total} rule{'s' if total != 1 else ''}" if not query else f"{shown} of {total}")

        if shown == 0:
            empty_label.config(text=f"No rules match “{search_var.get().strip()}”" if query
                               else "No rules yet.\nAdd one below to get started.")
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

    ttk.Label(editor, text="Word or symbol", style="Field.TLabel").grid(row=0, column=0, sticky=tk.W, pady=(0, S(6)))
    ttk.Label(editor, text="Spoken as", style="Field.TLabel").grid(row=0, column=1, sticky=tk.W, padx=(S(12), 0), pady=(0, S(6)))

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

    def persist() -> None:
        daemon.config["pronunciation_dict"] = dict_ref
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
            existed = w in dict_ref
            dict_ref[w] = s
            persist()
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
        add_btn.config(text="Update Rule" if w in dict_ref else "Add Rule")

    word_var.trace_add("write", update_editor_state)
    spoken_var.trace_add("write", update_editor_state)
    update_editor_state()

    for entry in (word_entry, spoken_entry):
        entry.bind("<Return>", add_entry)
        entry.bind("<Escape>", lambda e: (clear_editor(), "break")[1])

    def on_tree_select(event) -> None:
        selected = tree.selection()
        remove_btn.config(state=tk.NORMAL if selected else tk.DISABLED,
                          text=f"Remove {len(selected)} Rules" if len(selected) > 1 else "Remove Selected")
        if len(selected) == 1 and selected[0] in dict_ref:
            word_var.set(selected[0])
            spoken_var.set(dict_ref[selected[0]])

    tree.bind("<<TreeviewSelect>>", on_tree_select)
    tree.bind("<Double-1>", lambda e: spoken_entry.focus_set())

    def remove_selected(event=None) -> None:
        words = [w for w in tree.selection() if w in dict_ref]
        if not words:
            return
        for w in words:
            del dict_ref[w]
        persist()
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

    # Continuous Active Value Synchronization loop (from Tray menu changes)
    def sync_gui_values():
        try:
            if _raise_request.is_set():
                _raise_request.clear()
                root.deiconify()
                root.lift()
                root.focus_force()

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

            root.after(1000, sync_gui_values)
        except tk.TclError:
            pass  # window was closed

    sync_gui_values()
    refresh_tree()
    word_entry.focus_set()
    root.mainloop()
