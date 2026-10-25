"""Custom Windows 11 WinUI 3 widgets for the Audify Settings GUI."""
from __future__ import annotations

import tkinter as tk
from typing import Callable


class FluentSlider(tk.Canvas):
    """WinUI 3 flat slider with rounded track and circular thumb that expands on hover."""

    def __init__(
        self,
        parent: tk.Misc,
        from_: float = 0,
        to: float = 100,
        variable: tk.Variable | None = None,
        command: Callable[[float], None] | None = None,
        bg: str = "#2b2b2b",
        active_color: str = "#60cdff",
        track_color: str = "#505050",
        knob_color: str = "#60cdff",
        knob_ring: str = "#383838",
        **kwargs,
    ) -> None:
        kwargs.setdefault("height", 26)
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
        self.knob_ring = knob_ring

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

    def apply_palette(self, pal: dict[str, str]) -> None:
        """Update slider colors when switching between Light and Dark WinUI 3 themes."""
        self.active_color = pal["accent_light"]
        self.track_color = pal["border_hover"]
        self.knob_color = pal["slider_knob"]
        self.knob_ring = pal["slider_knob_ring"]
        super().configure(bg=pal["card"])
        self._draw()

    def _on_var_write(self, *args) -> None:
        try:
            val = self.variable.get()
            if val != self.value:
                self.value = max(self.from_, min(self.to, val))
                self._draw()
        except Exception:
            pass

    def _on_enter(self, event) -> None:
        self.is_hovered = True
        self._draw()

    def _on_leave(self, event) -> None:
        self.is_hovered = False
        self._draw()

    def _on_click(self, event) -> None:
        self.is_dragging = True
        self._update_val_from_x(event.x)

    def _on_drag(self, event) -> None:
        if self.is_dragging:
            self._update_val_from_x(event.x)

    def _on_release(self, event) -> None:
        self.is_dragging = False
        self._draw()

    def _update_val_from_x(self, x: float) -> None:
        w = self.winfo_width()
        if w <= 22:
            return
        margin = 11
        usable_w = w - 2 * margin
        pct = max(0.0, min(1.0, (x - margin) / usable_w))
        new_val = self.from_ + pct * (self.to - self.from_)
        if isinstance(self.variable, tk.IntVar):
            new_val = int(round(new_val))

        self.value = new_val
        if self.variable:
            self.variable.set(new_val)
        if self.command:
            self.command(new_val)
        self._draw()

    def config(self, **kwargs) -> None:
        if "command" in kwargs:
            self.command = kwargs.pop("command")
        if "variable" in kwargs:
            self.variable = kwargs.pop("variable")
            self.value = self.variable.get()
            self.variable.trace_add("write", self._on_var_write)
        super().configure(**kwargs)
        self._draw()

    def configure(self, **kwargs) -> None:
        self.config(**kwargs)

    def _draw(self, event=None) -> None:
        self.delete("all")
        w = self.winfo_width()
        h = self.winfo_height()
        if w <= 1:
            return

        margin = 11
        usable_w = w - 2 * margin
        pct = (
            (self.value - self.from_) / (self.to - self.from_)
            if self.to != self.from_
            else 0.0
        )
        knob_x = margin + pct * usable_w
        cy = h / 2

        # WinUI 3 4px rounded track
        self.create_line(
            margin, cy, w - margin, cy, fill=self.track_color, width=4, capstyle="round"
        )
        if knob_x > margin:
            self.create_line(
                margin, cy, knob_x, cy, fill=self.active_color, width=4, capstyle="round"
            )

        # WinUI 3 outer thumb ring + inner thumb circle
        outer_r = 9 if (self.is_hovered or self.is_dragging) else 8
        inner_r = 5 if self.is_dragging else (6 if self.is_hovered else 4.5)
        self.create_oval(
            knob_x - outer_r,
            cy - outer_r,
            knob_x + outer_r,
            cy + outer_r,
            fill=self.knob_ring,
            outline=self.track_color,
            width=1,
        )
        self.create_oval(
            knob_x - inner_r,
            cy - inner_r,
            knob_x + inner_r,
            cy + inner_r,
            fill=self.knob_color,
            outline="",
        )


class FluentToggle(tk.Canvas):
    """WinUI 3 ToggleSwitch capsule with distinct on/off track, border, and thumb."""

    def __init__(
        self,
        parent: tk.Misc,
        variable: tk.BooleanVar | None = None,
        command: Callable[[], None] | None = None,
        bg: str = "#2b2b2b",
        active_color: str = "#60cdff",
        track_color: str = "#2b2b2b",
        border_color: str = "#9a9a9a",
        knob_on_color: str = "#000000",
        knob_off_color: str = "#c5c5c5",
        **kwargs,
    ) -> None:
        kwargs.setdefault("width", 40)
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
        self.border_color = border_color
        self.knob_on_color = knob_on_color
        self.knob_off_color = knob_off_color

        self.state = False
        if self.variable:
            self.state = bool(self.variable.get())
            self.variable.trace_add("write", self._on_var_write)

        self.bind("<Button-1>", self._on_click)
        self.bind("<Configure>", self._draw)

    def apply_palette(self, pal: dict[str, str]) -> None:
        """Update toggle switch colors for Light/Dark WinUI 3 themes."""
        self.active_color = pal["accent_light"]
        self.track_color = pal["card"]
        self.border_color = pal["text_muted"]
        self.knob_on_color = pal["toggle_knob_on"]
        self.knob_off_color = pal["toggle_knob_off"]
        super().configure(bg=pal["card"])
        self._draw()

    def _on_var_write(self, *args) -> None:
        try:
            val = bool(self.variable.get())
            if val != self.state:
                self.state = val
                self._draw()
        except Exception:
            pass

    def _on_click(self, event) -> None:
        self.state = not self.state
        if self.variable:
            self.variable.set(self.state)
        if self.command:
            self.command()
        self._draw()

    def _draw(self, event=None) -> None:
        self.delete("all")
        w = self.winfo_width()
        h = self.winfo_height()
        if w <= 2 or h <= 2:
            return

        r = h / 2
        if self.state:
            fill_col = self.active_color
            stroke_col = self.active_color
            knob_col = self.knob_on_color
        else:
            fill_col = self.track_color
            stroke_col = self.border_color
            knob_col = self.knob_off_color

        # Outer capsule stroke
        self.create_oval(1, 1, h - 1, h - 1, fill=stroke_col, outline=stroke_col)
        self.create_oval(w - h + 1, 1, w - 1, h - 1, fill=stroke_col, outline=stroke_col)
        self.create_rectangle(r, 1, w - r, h - 1, fill=stroke_col, outline=stroke_col)

        # Inner capsule fill (1px inset when off so WinUI 3 border ring is visible)
        if not self.state:
            self.create_oval(2, 2, h - 2, h - 2, fill=fill_col, outline=fill_col)
            self.create_oval(
                w - h + 2, 2, w - 2, h - 2, fill=fill_col, outline=fill_col
            )
            self.create_rectangle(r, 2, w - r, h - 2, fill=fill_col, outline=fill_col)

        # Knob
        if self.state:
            kx1, kx2 = w - h + 4, w - 4
        else:
            kx1, kx2 = 4, h - 4
        ky1, ky2 = 4, h - 4
        self.create_oval(kx1, ky1, kx2, ky2, fill=knob_col, outline="", width=0)


def create_fluent_entry(
    parent: tk.Misc,
    textvariable: tk.StringVar | None = None,
    width: int = 20,
    bg: str = "#1f1f1f",
    border_color: str = "#3a3a3a",
    active_color: str = "#60cdff",
    fg: str = "#ffffff",
    placeholder: str = "",
    placeholder_color: str = "#787878",
    **kwargs,
) -> tuple[tk.Frame, tk.Entry]:
    """WinUI 3 TextBox with bottom-accent focus highlight and placeholder support."""
    outer = tk.Frame(
        parent,
        bg=border_color,
        bd=0,
        highlightthickness=1,
        highlightbackground=border_color,
        highlightcolor=active_color,
    )
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
        **kwargs,
    )
    entry.pack(fill=tk.BOTH, expand=True, padx=10, pady=7)

    state = {
        "border": border_color,
        "active": active_color,
        "focused": False,
    }
    hint: tk.Label | None = None

    if placeholder:
        hint = tk.Label(
            inner,
            text=placeholder,
            bg=bg,
            fg=placeholder_color,
            font=("Segoe UI", 10),
            bd=0,
            cursor="xterm",
        )
        hint.bind("<Button-1>", lambda e: entry.focus_set())

        def update_hint(*args) -> None:
            text = textvariable.get() if textvariable is not None else entry.get()
            if text:
                hint.place_forget()
            else:
                hint.place(in_=entry, x=0, rely=0.5, anchor="w")

        if textvariable is not None:
            textvariable.trace_add("write", update_hint)
        entry.bind("<KeyRelease>", update_hint, add="+")
        entry.after_idle(update_hint)

    def on_focus_in(e) -> None:
        state["focused"] = True
        outer.config(highlightbackground=state["active"], bg=state["active"])

    def on_focus_out(e) -> None:
        state["focused"] = False
        outer.config(highlightbackground=state["border"], bg=state["border"])

    def apply_palette(pal: dict[str, str], field_bg: str | None = None) -> None:
        f_bg = field_bg or pal["field"]
        state["border"] = pal["border"]
        state["active"] = pal["accent_light"]
        b_col = state["active"] if state["focused"] else state["border"]
        outer.config(
            bg=b_col,
            highlightbackground=b_col,
            highlightcolor=state["active"],
        )
        inner.config(bg=f_bg)
        entry.config(bg=f_bg, fg=pal["text"], insertbackground=pal["text"])
        if hint is not None:
            hint.config(bg=f_bg, fg=pal["placeholder"])

    outer.apply_palette = apply_palette  # type: ignore[attr-defined]
    entry.bind("<FocusIn>", on_focus_in, add="+")
    entry.bind("<FocusOut>", on_focus_out, add="+")
    return outer, entry


def create_modern_btn(
    parent: tk.Misc,
    text: str,
    command: Callable[[], None],
    primary: bool = False,
    accent_color: str = "#0078d4",
    accent_hover: str = "#1a86d9",
    danger: bool = False,
    pal: dict[str, str] | None = None,
) -> tk.Button:
    """WinUI 3 button supporting primary accent, standard control, and danger hover states."""
    state = {
        "primary": primary,
        "danger": danger,
        "bg": accent_color if primary else (pal["control"] if pal else "#373737"),
        "hover": (
            accent_hover
            if primary
            else ("#c42b1c" if danger else (pal["control_hover"] if pal else "#424242"))
        ),
        "fg": "#ffffff" if primary else (pal["control_fg"] if pal else "#ffffff"),
        "disabled_fg": pal["disabled_fg"] if pal else "#757575",
    }

    btn = tk.Button(
        parent,
        text=text,
        command=command,
        bg=state["bg"],
        fg=state["fg"],
        activebackground=state["hover"],
        activeforeground="#ffffff" if (primary or danger) else state["fg"],
        disabledforeground=state["disabled_fg"],
        font=("Segoe UI Semibold", 9),
        bd=0,
        relief="flat",
        padx=14,
        pady=7,
        cursor="hand2",
    )

    def apply_palette(new_pal: dict[str, str]) -> None:
        if state["primary"]:
            state["bg"] = new_pal["accent"]
            state["hover"] = new_pal["accent_hover"]
            state["fg"] = new_pal["accent_fg"]
        else:
            state["bg"] = new_pal["control"]
            state["hover"] = (
                new_pal["danger_btn"] if state["danger"] else new_pal["control_hover"]
            )
            state["fg"] = new_pal["control_fg"]
        state["disabled_fg"] = new_pal["disabled_fg"]
        btn.config(
            bg=state["bg"],
            fg=state["fg"],
            activebackground=state["hover"],
            activeforeground=(
                "#ffffff" if (state["primary"] or state["danger"]) else state["fg"]
            ),
            disabledforeground=state["disabled_fg"],
        )

    btn.apply_palette = apply_palette  # type: ignore[attr-defined]

    def on_enter(e) -> None:
        if str(btn["state"]) != "disabled":
            btn.config(
                bg=state["hover"],
                fg="#ffffff" if (state["primary"] or state["danger"]) else state["fg"],
            )

    def on_leave(e) -> None:
        btn.config(bg=state["bg"], fg=state["fg"])

    btn.bind("<Enter>", on_enter)
    btn.bind("<Leave>", on_leave)
    return btn
