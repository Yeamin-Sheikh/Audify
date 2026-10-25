"""
WinUI 3 theme tokens, Windows 11 system theme detection, and DWM title-bar styling.

Provides:
  - ``get_windows_system_theme()``: Reads ``AppsUseLightTheme`` from the Windows
    Personalize registry key so Audify automatically matches the device theme
    when ``theme_mode`` is set to ``"system"``.
  - ``resolve_theme(theme_mode)``: Resolves ``"system"``, ``"light"``, or ``"dark"``
    into the active ``"dark"`` or ``"light"`` palette name.
  - ``get_palette(theme_mode)``: Returns the complete Windows 11 WinUI 3 color
    dictionary for the resolved theme, including surface layers, control fills,
    accent colors, and monochrome dictation HUD pill tokens.
  - ``apply_window_theme(root, palette)``: Synchronizes the native Windows 11
    DWM title bar and border colors with the active WinUI 3 palette.
  - ``apply_native_menu_theme(theme_mode)``: Updates ``uxtheme.dll`` preferred
    app mode so the system tray right-click menu matches Light/Dark/System.
"""

from __future__ import annotations

import ctypes
import tkinter as tk
import winreg

THEME_MODES: dict[str, str] = {
    "System": "system",
    "Light": "light",
    "Dark": "dark",
}

# Authentic Windows 11 WinUI 3 Dark Mode tokens (Mica Alt + Layer Fill)
WINUI_DARK: dict[str, str] = {
    "mode": "dark",
    "bg": "#202020",              # ApplicationPageBackgroundThemeBrush (Mica Alt base)
    "card": "#2b2b2b",            # CardBackgroundFillColorDefaultBrush
    "card_alt": "#323232",        # CardBackgroundFillColorSecondaryBrush
    "field": "#1f1f1f",           # ControlFillColorInputActiveBrush
    "control": "#373737",         # ControlFillColorDefaultBrush (secondary buttons)
    "control_hover": "#424242",   # ControlFillColorSecondaryBrush
    "control_fg": "#ffffff",      # Text on standard controls
    "disabled_fg": "#757575",     # TextFillColorDisabledBrush
    "row_alt": "#262626",         # Subtle table zebra row
    "border": "#3a3a3a",          # CardStrokeColorDefaultBrush
    "border_hover": "#505050",    # ControlStrokeColorSecondaryBrush
    "divider": "#353535",         # DividerStrokeColorDefaultBrush
    "text": "#ffffff",            # TextFillColorPrimaryBrush
    "text_sec": "#c5c5c5",        # TextFillColorSecondaryBrush
    "text_muted": "#9a9a9a",      # TextFillColorTertiaryBrush
    "placeholder": "#787878",     # Input placeholder text
    "accent": "#0078d4",          # Primary WinUI accent button fill
    "accent_hover": "#1a86d9",    # Primary WinUI accent hover
    "accent_light": "#60cdff",    # SystemAccentColorLight2 (sliders, toggles, focus)
    "accent_fg": "#ffffff",       # Text on primary accent controls
    "toggle_knob_on": "#000000",  # WinUI 3 dark mode uses dark knob on #60cdff track
    "toggle_knob_off": "#c5c5c5", # WinUI 3 dark mode muted knob when off
    "slider_knob": "#60cdff",     # WinUI 3 slider thumb
    "slider_knob_ring": "#383838",
    "success": "#6ccb5f",         # SystemFillColorSuccessBrush (dark)
    "danger": "#ff99a4",          # SystemFillColorCriticalBrush (dark text)
    "danger_btn": "#c42b1c",      # Critical button hover fill
    "scrollbar": "#5a5a5a",
    "scrollbar_hover": "#787878",
    # Floating Dictation HUD pill (monochrome dark: black capsule, white bars, no outline)
    "pill_key": "#010101",
    "pill_bg": "#121214",
    "pill_bar_idle": "#a1a1aa",
    "pill_bar_active": "#ffffff",
    "pill_bar_transcribe": "#d4d4d8",
    "pill_dot_hi": "#ffffff",
    "pill_dot_lo": "#52525b",
}

# Authentic Windows 11 WinUI 3 Light Mode tokens (Mica + Layer Fill)
WINUI_LIGHT: dict[str, str] = {
    "mode": "light",
    "bg": "#f3f3f3",              # ApplicationPageBackgroundThemeBrush (Mica base)
    "card": "#fbfbfb",            # CardBackgroundFillColorDefaultBrush
    "card_alt": "#f0f0f0",        # SubtleFillColorSecondaryBrush
    "field": "#ffffff",           # ControlFillColorDefaultBrush (crisp white input)
    "control": "#f0f0f0",         # Standard button fill
    "control_hover": "#e4e4e4",   # Standard button hover
    "control_fg": "#1a1a1a",      # Text on standard controls
    "disabled_fg": "#a0a0a0",     # TextFillColorDisabledBrush
    "row_alt": "#f4f4f4",         # Subtle table zebra row
    "border": "#e3e3e3",          # CardStrokeColorDefaultBrush
    "border_hover": "#c2c2c2",    # ControlStrokeColorSecondaryBrush
    "divider": "#e6e6e6",         # DividerStrokeColorDefaultBrush
    "text": "#1a1a1a",            # TextFillColorPrimaryBrush
    "text_sec": "#5c5c5c",        # TextFillColorSecondaryBrush
    "text_muted": "#6e6e6e",      # TextFillColorTertiaryBrush
    "placeholder": "#8a8a8a",     # Input placeholder text
    "accent": "#0067c0",          # Primary WinUI accent button fill (light)
    "accent_hover": "#1975c5",    # Primary WinUI accent hover (light)
    "accent_light": "#0067c0",    # SystemAccentColorDark1 (sliders, toggles, focus)
    "accent_fg": "#ffffff",       # Text on primary accent controls
    "toggle_knob_on": "#ffffff",  # WinUI 3 light mode uses white knob on #0067c0 track
    "toggle_knob_off": "#5c5c5c", # WinUI 3 light mode muted knob when off
    "slider_knob": "#0067c0",     # WinUI 3 slider thumb
    "slider_knob_ring": "#ffffff",
    "success": "#0f7b0f",         # SystemFillColorSuccessBrush (light)
    "danger": "#c42b1c",          # SystemFillColorCriticalBrush (light text)
    "danger_btn": "#c42b1c",      # Critical button hover fill
    "scrollbar": "#b8b8b8",
    "scrollbar_hover": "#909090",
    # Floating Dictation HUD pill (monochrome light: white capsule, black bars, no outline)
    "pill_key": "#fefefe",
    "pill_bg": "#f4f4f6",
    "pill_bar_idle": "#71717a",
    "pill_bar_active": "#121214",
    "pill_bar_transcribe": "#3f3f46",
    "pill_dot_hi": "#121214",
    "pill_dot_lo": "#a1a1aa",
}


def get_windows_system_theme() -> str:
    """Read Windows 11 app theme preference from registry ('dark' or 'light')."""
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        ) as key:
            val, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            return "light" if int(val) == 1 else "dark"
    except Exception:
        return "dark"


def resolve_theme(theme_mode: str | None = "system") -> str:
    """Resolve 'system', 'light', or 'dark' into 'dark' or 'light'."""
    mode = (theme_mode or "system").strip().lower()
    if mode == "light":
        return "light"
    if mode == "dark":
        return "dark"
    return get_windows_system_theme()


def get_palette(theme_mode: str | None = "system") -> dict[str, str]:
    """Return the WinUI 3 color token dictionary for *theme_mode*."""
    return WINUI_LIGHT if resolve_theme(theme_mode) == "light" else WINUI_DARK


def _hex_to_colorref(hex_color: str) -> int:
    """Convert '#rrggbb' to a Win32 COLORREF (0x00bbggrr)."""
    h = hex_color.lstrip("#")
    r = int(h[0:2], 16)
    g = int(h[2:4], 16)
    b = int(h[4:6], 16)
    return (b << 16) | (g << 8) | r


def apply_window_theme(root: tk.Tk, palette: dict[str, str]) -> None:
    """Synchronize the Windows 11 DWM title bar and caption colors with *palette*."""
    try:
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id()) or root.winfo_id()
        is_dark = 1 if palette.get("mode") == "dark" else 0
        dark_val = ctypes.c_int(is_dark)
        # DWMWA_USE_IMMERSIVE_DARK_MODE (20, and 19 on early Win10/11 builds)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, 20, ctypes.byref(dark_val), ctypes.sizeof(dark_val)
        )
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, 19, ctypes.byref(dark_val), ctypes.sizeof(dark_val)
        )
        # DWMWA_CAPTION_COLOR (35) and DWMWA_TEXT_COLOR (36) on Windows 11 Build 22000+
        cap_color = ctypes.c_uint32(_hex_to_colorref(palette["bg"]))
        txt_color = ctypes.c_uint32(_hex_to_colorref(palette["text"]))
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, 35, ctypes.byref(cap_color), ctypes.sizeof(cap_color)
        )
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, 36, ctypes.byref(txt_color), ctypes.sizeof(txt_color)
        )
    except Exception:
        pass


def apply_native_menu_theme(theme_mode: str | None = "system") -> None:
    """Update uxtheme.dll preferred app mode so Win32 tray menus match the theme."""
    try:
        kernel32 = ctypes.windll.kernel32
        kernel32.GetModuleHandleW.restype = ctypes.c_void_p
        kernel32.GetProcAddress.restype = ctypes.c_void_p
        kernel32.GetProcAddress.argtypes = [ctypes.c_void_p, ctypes.c_void_p]

        ctypes.WinDLL("uxtheme.dll")
        h_ux = kernel32.GetModuleHandleW("uxtheme.dll")
        if not h_ux:
            return

        # Ordinal 135: SetPreferredAppMode(mode)
        # 1 = AllowDark (System), 2 = ForceDark, 3 = ForceLight
        mode_str = (theme_mode or "system").strip().lower()
        app_mode = 2 if mode_str == "dark" else (3 if mode_str == "light" else 1)
        addr = kernel32.GetProcAddress(h_ux, 135)
        if addr:
            ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_int)(addr)(app_mode)

        # Ordinal 136: FlushMenuThemes()
        addr = kernel32.GetProcAddress(h_ux, 136)
        if addr:
            ctypes.CFUNCTYPE(None)(addr)()
    except Exception:
        pass
