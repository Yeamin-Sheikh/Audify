"""Custom Fluent-style widgets for the Audify Control Center GUI."""
from __future__ import annotations
import tkinter as tk

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


def create_modern_btn(
    parent, text: str, command, primary: bool = False,
    accent_color: str = "#0a84ff", accent_hover: str = "#2693ff",
) -> tk.Button:
    btn_bg = accent_color if primary else "#3a3a3c"
    btn_active = accent_hover if primary else "#48484a"
    
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
