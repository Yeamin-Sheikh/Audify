"""System tray icon, menu wiring, and lifecycle management for Audify."""
from __future__ import annotations

import ctypes
import os
import threading

import pyperclip
import pystray
from PIL import Image, ImageDraw
from pystray import Menu
from pystray import MenuItem as item

from audify import __version__
from audify.config import (
    HISTORY_FILE,
    VOICES,
    RATES,
    VOLUMES,
)
from audify.engine import TTSDaemon
from audify.gui.control_center import show_dictionary_ui


# -- Dark mode for native Win32 menus -----------------------------------------

def _enable_dark_mode() -> None:
    """Opt the app into Windows dark/light theme for native context menus.

    Uses undocumented but stable uxtheme.dll ordinal exports:
      - Ordinal 135: SetPreferredAppMode(mode)
        mode 1 = AllowDark (menus follow system dark/light setting)
      - Ordinal 136: FlushMenuThemes()
        Refreshes cached menu theme to apply the change immediately.

    Available since Windows 10 1903. Fails silently on older versions.
    """
    try:
        kernel32 = ctypes.windll.kernel32
        kernel32.GetModuleHandleW.restype = ctypes.c_void_p
        kernel32.GetProcAddress.restype = ctypes.c_void_p
        kernel32.GetProcAddress.argtypes = [ctypes.c_void_p, ctypes.c_void_p]

        # Ensure uxtheme.dll is loaded, then get its module handle
        ctypes.WinDLL("uxtheme.dll")
        h_ux = kernel32.GetModuleHandleW("uxtheme.dll")
        if not h_ux:
            return

        # SetPreferredAppMode(1) = AllowDark
        addr = kernel32.GetProcAddress(h_ux, 135)
        if addr:
            ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_int)(addr)(1)

        # FlushMenuThemes() — apply immediately
        addr = kernel32.GetProcAddress(h_ux, 136)
        if addr:
            ctypes.CFUNCTYPE(None)(addr)()
    except Exception:
        pass


# -- Tray icon images --------------------------------------------------------

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
    d.rectangle((22, 18, 28, 46), fill="white")
    d.rectangle((36, 18, 42, 46), fill="white")
    return image


# -- Helpers for dynamic menu labels ------------------------------------------

def _voice_display_name(daemon: TTSDaemon) -> str:
    """Get the short display name for the currently selected voice."""
    current = daemon.config.get("voice", "")
    for label, vid in VOICES.items():
        if vid == current:
            return label
    return "Unknown"


def _speed_display_name(daemon: TTSDaemon) -> str:
    """Get the display name for the currently selected speed."""
    current = daemon.config.get("rate", "+0%")
    for label, rate in RATES.items():
        if rate == current:
            return label
    return "Normal"


# -- Main setup ---------------------------------------------------------------

def setup_tray() -> None:
    """Create the TTSDaemon, wire up the system-tray menu, and run."""

    # Enable dark mode before creating any windows/menus
    _enable_dark_mode()

    daemon = TTSDaemon()

    # Launch worker loop and clipboard monitor
    threading.Thread(target=daemon.worker_loop, daemon=True).start()
    daemon.start_clipboard_monitor()

    # -- Callbacks ------------------------------------------------------------

    def on_toggle_pause(icon: pystray.Icon, item_action: item) -> None:
        daemon.is_paused = not daemon.is_paused
        if daemon.is_paused:
            daemon.pause_audio()
            icon.icon = create_play_icon()
            icon.title = "Audify (PAUSED — Click to Resume)"
        else:
            daemon.resume_audio()
            icon.icon = create_pause_icon()
            icon.title = "Audify (Active — Click to Pause)"

    def on_stop_speaking(icon: pystray.Icon, item_action: item) -> None:
        """Stop current playback immediately (same as Ctrl+Alt+S)."""
        daemon.stop_audio()

    def on_replay_last(icon: pystray.Icon, item_action: item) -> None:
        """Re-read the last spoken text."""
        text = daemon.last_spoken
        if text:
            daemon.last_spoken = ""  # Clear to bypass duplicate check
            daemon.q.put(text)

    def on_read_clipboard(icon: pystray.Icon, item_action: item) -> None:
        """Force-read whatever is currently on the clipboard."""
        daemon.last_spoken = ""  # Force re-read even if duplicate
        text: str = pyperclip.paste()
        if text.strip():
            daemon.q.put(text)

    def on_open_settings(icon: pystray.Icon, item_action: item) -> None:
        threading.Thread(
            target=show_dictionary_ui, args=(daemon,), daemon=True
        ).start()

    def on_exit(icon: pystray.Icon, item_action: item) -> None:
        daemon.stop_audio()
        daemon.q.put(None)
        if os.path.exists(HISTORY_FILE):
            try:
                os.remove(HISTORY_FILE)
            except Exception:
                pass
        daemon._clip_listener.stop()
        icon.stop()

    # -- Submenu factories ----------------------------------------------------

    def make_voice_setter(voice_id: str):
        return lambda icon, item_action: daemon.set_voice(voice_id)

    def make_rate_setter(rate_str: str):
        return lambda icon, item_action: daemon.set_rate(rate_str)

    def make_volume_setter(vol: float):
        return lambda icon, item_action: daemon.set_volume(vol)

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

    # -- Build the tray icon and menu -----------------------------------------

    icon_image = create_pause_icon()
    tray = pystray.Icon(
        "Audify",
        icon_image,
        "Audify (Active — Click to Pause)",
        menu=Menu(
            # Version label (grayed out, informational only)
            item(
                f"Audify v{__version__}",
                lambda icon, i: None,
                enabled=False,
            ),
            Menu.SEPARATOR,
            # Primary actions
            item(
                lambda text: "\u25b6 Resume Listening"
                if daemon.is_paused
                else "\u23f8 Pause Listening",
                on_toggle_pause,
                default=True,
            ),
            item("\u23f9 Stop Speaking", on_stop_speaking),
            item("\U0001f501 Replay Last", on_replay_last),
            Menu.SEPARATOR,
            # Clipboard
            item("\U0001f4cb Read Clipboard", on_read_clipboard),
            Menu.SEPARATOR,
            # Voice, speed, and volume with current selection shown in the label
            item(
                lambda text: f"Voice: {_voice_display_name(daemon)}",
                Menu(*voice_menu_items),
            ),
            item(
                lambda text: f"Speed: {_speed_display_name(daemon)}",
                Menu(*rate_menu_items),
            ),
            item("Volume", Menu(*volume_menu_items)),
            Menu.SEPARATOR,
            # Settings and exit
            item("\u2699 Settings...", on_open_settings),
            item("Exit", on_exit),
        ),
    )
    daemon.tray_icon = tray
    tray.run()
