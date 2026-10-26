"""
Floating non-activating audio visualizer and learned-word HUD pill for Bark 3.0 Clean dictation.

Renders a borderless, click-through Windows 11 monochrome pill near the bottom
of the screen whenever microphone dictation is active or when a word correction
is saved to the STT vocabulary:
  - Uses Win32 ``WS_EX_NOACTIVATE | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_TOPMOST``
    so it NEVER steals keyboard focus from the active input field and mouse clicks
    pass straight through it.
  - Automatically adapts to the active WinUI 3 theme (Device System, Dark, or Light):
    dark mode renders white equalizer bars / text inside a borderless black capsule;
    light mode inverts to black equalizer bars / text inside a borderless white capsule.
  - Animates 9 real-time audio-reactive visualizer bars at 25 FPS while recording,
    a smooth wave animation while transcribing, and a minimal ``✓ Saved: heard → written``
    confirmation pill for 2.4s whenever Audify learns a word correction.
"""

from __future__ import annotations

import ctypes
import math
import threading
import time
import tkinter as tk
from typing import TYPE_CHECKING

from audify.gui.theme import get_palette

if TYPE_CHECKING:
    from audify.stt import BarkCleanController

GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_TOPMOST = 0x00000008
WS_EX_LAYERED = 0x00080000
WS_EX_NOACTIVATE = 0x08000000

SW_SHOWNOACTIVATE = 4
HWND_TOPMOST = -1
SWP_NOMOVE = 0x0002
SWP_NOSIZE = 0x0001
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040


def _rounded_rect_points(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    r: float,
) -> list[float]:
    return [
        x1 + r, y1,
        x2 - r, y1,
        x2, y1,
        x2, y1 + r,
        x2, y2 - r,
        x2, y2,
        x2 - r, y2,
        x1 + r, y2,
        x1, y2,
        x1, y2 - r,
        x1, y1 + r,
        x1, y1,
    ]


def _draw_rounded_rect(
    canvas: tk.Canvas,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    r: float,
    **kwargs,
) -> int:
    points = _rounded_rect_points(x1, y1, x2, y2, r)
    return canvas.create_polygon(points, smooth=True, **kwargs)


class DictationVisualizerHUD:
    """Click-through, non-focus-stealing floating audio visualizer and learned-word pill."""

    def __init__(self, stt: BarkCleanController) -> None:
        self.stt = stt
        self._lock = threading.Lock()
        self._started = False
        self._visible_requested = False
        self._visible_since: float = 0.0
        self._status_text: str = ""
        self._toast_text: str = ""
        self._toast_until: float = 0.0

    def _ensure_thread_started(self) -> None:
        with self._lock:
            if not self._started:
                self._started = True
                threading.Thread(
                    target=self._run_loop,
                    daemon=True,
                    name="stt-visualizer-hud",
                ).start()

    def show(self) -> None:
        """Request the visualizer HUD to appear (spawning its thread on first use)."""
        with self._lock:
            self._visible_requested = True
            self._visible_since = time.monotonic()
        self._ensure_thread_started()

    def hide(self) -> None:
        """Request the visualizer HUD to hide and clear any setup status banner."""
        with self._lock:
            self._visible_requested = False
            self._status_text = ""

    def set_status(self, text: str) -> None:
        """Display a live setup or download progress message inside the pill."""
        clean = (text or "").strip()
        if len(clean) > 54:
            clean = clean[:52] + "…"
        with self._lock:
            self._status_text = clean
            if clean:
                self._visible_since = time.monotonic()
        if clean:
            self._ensure_thread_started()

    def clear_status(self) -> None:
        """Clear the setup/download status message and return to the equalizer bars."""
        with self._lock:
            self._status_text = ""

    def show_error(self, message: str, duration_s: float = 3.4) -> None:
        """Show a brief non-obstructive error notice in the pill and auto-hide afterward."""
        clean = (message or "").strip()
        if not clean:
            self.hide()
            return
        if len(clean) > 54:
            clean = clean[:52] + "…"
        with self._lock:
            self._visible_requested = False
            self._status_text = ""
            self._toast_text = f"⚠  {clean}"
            self._toast_until = time.monotonic() + float(duration_s)
        self._ensure_thread_started()

    def show_learned(self, heard: str, written: str, duration_s: float = 2.4) -> None:
        """Show a minimal non-obstructive confirmation in the pill when a word fix is learned."""
        h_clean = (heard or "").strip()
        w_clean = (written or "").strip()
        if not h_clean or not w_clean:
            return
        if len(h_clean) > 22:
            h_clean = h_clean[:20] + "…"
        if len(w_clean) > 22:
            w_clean = w_clean[:20] + "…"
        with self._lock:
            self._toast_text = f"✓  Saved: {h_clean} → {w_clean}"
            self._toast_until = time.monotonic() + float(duration_s)
        self._ensure_thread_started()

    def _current_palette(self) -> dict[str, str]:
        """Resolve the active WinUI 3 palette from daemon config ('system', 'light', or 'dark')."""
        daemon = getattr(self.stt, "daemon", None)
        mode = "system"
        if daemon is not None:
            try:
                mode = str(daemon.config.get("theme_mode", "system"))
            except Exception:
                mode = "system"
        return get_palette(mode)

    def _apply_noactivate_clickthrough(self, root: tk.Tk) -> int:
        """Apply Win32 extended styles so the HUD never takes focus or blocks clicks."""
        u32 = ctypes.windll.user32
        hwnd = u32.GetParent(root.winfo_id()) or root.winfo_id()
        try:
            ex_style = u32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            ex_style |= (
                WS_EX_NOACTIVATE
                | WS_EX_TOOLWINDOW
                | WS_EX_TOPMOST
                | WS_EX_TRANSPARENT
                | WS_EX_LAYERED
            )
            u32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex_style)
        except Exception:
            pass
        return hwnd

    def _run_loop(self) -> None:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass

        try:
            root = tk.Tk()
        except Exception:
            with self._lock:
                self._started = False
            return

        pal = self._current_palette()
        cur_mode = [pal["mode"]]
        key_color = pal["pill_key"]

        root.withdraw()
        root.overrideredirect(True)
        root.configure(bg=key_color)
        try:
            root.wm_attributes("-transparentcolor", key_color)
            root.wm_attributes("-topmost", True)
            root.wm_attributes("-alpha", 0.96)
        except Exception:
            pass

        scale = root.winfo_fpixels("1i") / 96.0

        def S(px: float) -> int:
            return max(1, int(round(px * scale)))

        num_bars = 9
        bar_w = S(3.5)
        bar_gap = S(3.0)
        bars_total_w = num_bars * bar_w + (num_bars - 1) * bar_gap
        dot_r = S(4.0)
        pad_x = S(13)
        dot_bar_gap = S(8)

        pill_w = pad_x + (2 * dot_r) + dot_bar_gap + bars_total_w + pad_x
        viz_w = int(round(pill_w + S(4)))
        cur_w = [viz_w]
        h = S(36)
        sw = root.winfo_screenwidth()
        sh = root.winfo_screenheight()
        x = (sw - viz_w) // 2
        y = sh - h - S(64)
        root.geometry(f"{viz_w}x{h}+{x}+{y}")
        root.update_idletasks()
        hwnd = self._apply_noactivate_clickthrough(root)

        canvas = tk.Canvas(
            root,
            width=viz_w,
            height=h,
            bg=key_color,
            highlightthickness=0,
            bd=0,
        )
        canvas.pack(fill=tk.BOTH, expand=True)

        pill_r = (h - S(4)) // 2
        dot_cy = h // 2

        # Borderless capsule (outline="" and width=0 so there is never an outline halo)
        pill_id = _draw_rounded_rect(
            canvas,
            S(2),
            S(2),
            viz_w - S(2),
            h - S(2),
            pill_r,
            fill=pal["pill_bg"],
            outline="",
            width=0,
        )

        dcx = S(2) + pad_x + dot_r
        dot_id = canvas.create_oval(
            dcx - dot_r,
            dot_cy - dot_r,
            dcx + dot_r,
            dot_cy + dot_r,
            fill=pal["pill_bar_idle"],
            outline="",
        )

        bars_start_x = int(round(dcx + dot_r + dot_bar_gap))
        bar_ids: list[int] = []
        for i in range(num_bars):
            bx = bars_start_x + i * (bar_w + bar_gap)
            bid = canvas.create_rectangle(
                bx,
                dot_cy - S(3),
                bx + bar_w,
                dot_cy + S(3),
                fill=pal["pill_bar_idle"],
                outline="",
            )
            bar_ids.append(bid)

        toast_text_id = canvas.create_text(
            viz_w // 2,
            dot_cy,
            text="",
            fill=pal["pill_bar_active"],
            font=("Segoe UI Semibold", 9),
            anchor="center",
            state="hidden",
        )

        is_shown = [False]
        in_toast_layout = [False]
        u32 = ctypes.windll.user32

        def _set_pill_width(target_w: int) -> None:
            if cur_w[0] == target_w:
                return
            cur_w[0] = target_w
            nx = (sw - target_w) // 2
            root.geometry(f"{target_w}x{h}+{nx}+{y}")
            canvas.configure(width=target_w)
            pts = _rounded_rect_points(
                S(2), S(2), target_w - S(2), h - S(2), pill_r
            )
            canvas.coords(pill_id, *pts)

        def _tick() -> None:
            rec = self.stt.is_recording
            trans = self.stt.is_transcribing
            now_mono = time.monotonic()
            with self._lock:
                if (
                    self._visible_requested
                    and not rec
                    and not trans
                    and not self._status_text
                    and (now_mono - self._visible_since) > 1.2
                ):
                    self._visible_requested = False
                vis_req = self._visible_requested
                s_text = self._status_text
                t_text = self._toast_text
                t_until = self._toast_until

            status_active = bool(s_text) and (vis_req or rec or trans)
            toast_active = (
                (not rec and not trans and not status_active)
                and bool(t_text)
                and (now_mono < t_until)
            )
            should_show = vis_req or rec or trans or status_active or toast_active

            if not should_show:
                if is_shown[0]:
                    root.withdraw()
                    is_shown[0] = False
                root.after(80, _tick)
                return

            pal_now = self._current_palette()
            if pal_now["mode"] != cur_mode[0]:
                cur_mode[0] = pal_now["mode"]
                new_key = pal_now["pill_key"]
                root.configure(bg=new_key)
                canvas.configure(bg=new_key)
                try:
                    root.wm_attributes("-transparentcolor", new_key)
                except Exception:
                    pass
            canvas.itemconfig(pill_id, fill=pal_now["pill_bg"], outline="")

            banner_text = s_text if (status_active and not rec) else (t_text if toast_active else "")
            if banner_text:
                if not in_toast_layout[0]:
                    in_toast_layout[0] = True
                    canvas.itemconfig(dot_id, state="hidden")
                    for bid in bar_ids:
                        canvas.itemconfig(bid, state="hidden")
                    canvas.itemconfig(toast_text_id, state="normal")

                canvas.itemconfig(
                    toast_text_id, text=banner_text, fill=pal_now["pill_bar_active"]
                )
                bbox = canvas.bbox(toast_text_id)
                text_px = (bbox[2] - bbox[0]) if bbox else S(140)
                target_w = max(viz_w, int(round(text_px + 2 * S(16) + S(4))))
                _set_pill_width(target_w)
                canvas.coords(toast_text_id, target_w // 2, dot_cy)
            else:
                if in_toast_layout[0]:
                    in_toast_layout[0] = False
                    canvas.itemconfig(toast_text_id, state="hidden")
                    canvas.itemconfig(dot_id, state="normal")
                    for bid in bar_ids:
                        canvas.itemconfig(bid, state="normal")
                _set_pill_width(viz_w)

            if not is_shown[0]:
                is_shown[0] = True
                try:
                    u32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
                    u32.SetWindowPos(
                        hwnd,
                        HWND_TOPMOST,
                        0,
                        0,
                        0,
                        0,
                        SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW,
                    )
                except Exception:
                    root.deiconify()

            if banner_text:
                root.after(60, _tick)
                return

            t = time.perf_counter()
            max_half_h = S(10.5)
            min_half_h = S(2.5)

            if trans:
                canvas.itemconfig(dot_id, fill=pal_now["pill_bar_transcribe"])
                for i, bid in enumerate(bar_ids):
                    bx = bars_start_x + i * (bar_w + bar_gap)
                    wave_v = 0.5 + 0.5 * math.sin(t * 7.5 - i * 0.55)
                    hh = min_half_h + wave_v * (max_half_h * 0.78 - min_half_h)
                    canvas.coords(bid, bx, dot_cy - hh, bx + bar_w, dot_cy + hh)
                    canvas.itemconfig(bid, fill=pal_now["pill_bar_transcribe"])
            else:
                levels = list(getattr(self.stt.recorder, "level_bars", ()))
                pulse = 0.5 + 0.5 * math.sin(t * 5.0)
                dot_col = (
                    pal_now["pill_dot_hi"]
                    if pulse > 0.45
                    else pal_now["pill_dot_lo"]
                )
                canvas.itemconfig(dot_id, fill=dot_col)

                for i, bid in enumerate(bar_ids):
                    bx = bars_start_x + i * (bar_w + bar_gap)
                    lvl = levels[i] if i < len(levels) else 0.0
                    center_weight = 1.0 - 0.12 * abs(i - (num_bars // 2))
                    idle_wave = 0.14 + 0.08 * math.sin(t * 4.2 + i * 0.65)
                    eff = max(idle_wave, min(1.0, lvl * center_weight * 1.25))
                    hh = min_half_h + eff * (max_half_h - min_half_h)
                    canvas.coords(bid, bx, dot_cy - hh, bx + bar_w, dot_cy + hh)
                    canvas.itemconfig(
                        bid,
                        fill=(
                            pal_now["pill_bar_active"]
                            if eff > 0.28
                            else pal_now["pill_bar_idle"]
                        ),
                    )

            root.after(40, _tick)

        root.after(20, _tick)
        root.mainloop()
