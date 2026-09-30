"""Audify Control Center -- settings and pronunciation dictionary GUI."""
from __future__ import annotations

import ctypes
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


def show_dictionary_ui(daemon: Any) -> None:
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

    add_btn = create_modern_btn(grid_container, "Add / Update", add_entry, primary=True, accent_color=ACCENT_COLOR, accent_hover=ACCENT_HOVER)
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

    remove_btn = create_modern_btn(action_frame, "Remove Selected", remove_selected, primary=False, accent_color=ACCENT_COLOR, accent_hover=ACCENT_HOVER)
    remove_btn.pack(side=tk.LEFT)

    close_btn = create_modern_btn(action_frame, "Close Window", root.destroy, primary=False, accent_color=ACCENT_COLOR, accent_hover=ACCENT_HOVER)
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
            
        root.after(1000, sync_gui_values)
        
    sync_gui_values()
    refresh_tree()
    root.mainloop()
