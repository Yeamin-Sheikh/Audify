"""System tray icon, menu wiring, and lifecycle management for Audify."""
from __future__ import annotations

import ctypes
import math
import os
import re
import threading

import pystray
from PIL import Image, ImageDraw
from pystray import Menu
from pystray import MenuItem as item

from audify import __version__
from audify.clipboard import get_clipboard_text
from audify.config import (
    HISTORY_FILE,
    VOICES,
    RATES,
    VOLUMES,
)
from audify.engine import TTSDaemon
from audify.playback import KOKORO_PREFIX, rate_to_speed
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
#
# A disc in the logo's indigo/violet carrying a bold "A" monogram whose
# crossbar is a small sound wave. Drawn at 4x and downsampled so edges stay
# smooth at 16-32px tray sizes. Paused swaps to a grey disc with pause bars.

_ICON_SIZE = 64
_SUPERSAMPLE = 4

# (top colour, bottom colour) of the disc gradient per state
_DISC_COLORS = {
    "ready": ((99, 102, 241), (139, 92, 246)),     # indigo -> violet
    "speaking": ((56, 189, 248), (99, 102, 241)),  # sky -> indigo, brighter while talking
    "paused": ((120, 120, 128), (72, 72, 80)),     # neutral grey
}

# Crossbar wave amplitude (fraction of icon size): livelier while speaking
_WAVE_AMPLITUDE = {"ready": 0.027, "speaking": 0.05}

_icon_cache: dict[str, Image.Image] = {}


def create_tray_icon(state: str) -> Image.Image:
    """Render the tray icon for ``state``: "ready", "speaking" or "paused"."""
    if state in _icon_cache:
        return _icon_cache[state]

    n = _ICON_SIZE * _SUPERSAMPLE
    top, bottom = _DISC_COLORS[state]

    # Vertical gradient clipped to a circle
    gradient = Image.new("RGBA", (1, n))
    for y in range(n):
        t = y / (n - 1)
        gradient.putpixel((0, y), tuple(int(a + (b - a) * t) for a, b in zip(top, bottom)) + (255,))
    gradient = gradient.resize((n, n))
    mask = Image.new("L", (n, n), 0)
    inset = n * 0.025
    ImageDraw.Draw(mask).ellipse((inset, inset, n - inset, n - inset), fill=255)
    image = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    image.paste(gradient, (0, 0), mask)

    d = ImageDraw.Draw(image)
    white = (255, 255, 255, 255)
    if state == "paused":
        bar_w, gap, bar_h = n * 0.11, n * 0.11, n * 0.44
        x0 = (n - (2 * bar_w + gap)) / 2
        y0 = (n - bar_h) / 2
        for i in range(2):
            x = x0 + i * (bar_w + gap)
            d.rounded_rectangle((x, y0, x + bar_w, y0 + bar_h), radius=bar_w / 2, fill=white)
    else:
        # Heavy geometric "A" (wide legs survive 16px), with a sound-wave crossbar
        f = n / 256
        d.polygon([(128 * f, 50 * f), (214 * f, 204 * f), (164 * f, 204 * f),
                   (128 * f, 122 * f), (92 * f, 204 * f), (42 * f, 204 * f)], fill=white)
        amp = n * _WAVE_AMPLITUDE[state]
        x_start, x_end = 76 * f, 180 * f
        steps = 52
        points = [
            (x_start + (x_end - x_start) * i / steps,
             172 * f + amp * math.sin(i / steps * 2 * math.pi))
            for i in range(steps + 1)
        ]
        d.line(points, fill=white, width=int(20 * f), joint="curve")

    icon = image.resize((_ICON_SIZE, _ICON_SIZE), Image.Resampling.LANCZOS)
    _icon_cache[state] = icon
    return icon


# -- Helpers for dynamic menu labels ------------------------------------------

def _voice_gender(label: str) -> str:
    return "Male" if re.search(r"\bMale\)", label) else "Female"


def _short_voice_label(label: str) -> str:
    """"Heart (Offline American Female)" -> "Heart (American)".

    The gender moves to a group header and "Offline" to the submenu name.
    """
    label = label.replace("(Offline ", "(")
    return re.sub(r"\s*\b(Female|Male)\)", ")", label).replace(" ()", "")


def _voice_display_name(daemon: TTSDaemon) -> str:
    """Short name ("Jenny") of the currently selected voice."""
    current = daemon.config.get("voice", "")
    for label, vid in VOICES.items():
        if vid == current:
            return label.split(" (")[0]
    return "Unknown"


def _speed_display_name(daemon: TTSDaemon) -> str:
    """Multiplier ("1.5x") of the currently selected speed."""
    current = daemon.config.get("rate", "+0%")
    for label, rate in RATES.items():
        if rate == current:
            name = label.split(" (")[0]
            # Offline voices top out at 2x; say so instead of silently capping
            if daemon.config.get("voice", "").startswith(KOKORO_PREFIX) and rate_to_speed(rate) > 2.0:
                name += " (2x offline)"
            return name
    return "Custom"


def _volume_display_name(daemon: TTSDaemon) -> str:
    return f"{int(round(daemon.config.get('volume', 1.0) * 100))}%"


def _hotkey_display_name(daemon: TTSDaemon) -> str:
    hotkey = str(daemon.config.get("stop_hotkey", "ctrl+alt+s"))
    return "+".join(part.strip().capitalize() for part in hotkey.split("+"))


# -- Main setup ---------------------------------------------------------------

def setup_tray() -> None:
    """Create the TTSDaemon, wire up the system-tray menu, and run."""

    # Enable dark mode before creating any windows/menus
    _enable_dark_mode()

    daemon = TTSDaemon()

    # Launch worker loop and clipboard monitor
    threading.Thread(target=daemon.worker_loop, daemon=True).start()
    daemon.start_clipboard_monitor()

    # -- State -> icon, tooltip and menu ---------------------------------------

    def current_state() -> str:
        if daemon.is_paused:
            return "paused"
        return "speaking" if daemon.is_speaking else "ready"

    def status_text() -> str:
        return "Paused" if daemon.is_paused else daemon.status

    def refresh_tray() -> None:
        """Called by the daemon whenever playback status or a setting changes."""
        tray.icon = create_tray_icon(current_state())
        tray.title = f"Audify — {status_text()}"
        # pystray only rebuilds the native menu after its own clicks, so changes made in
        # the Control Center or by the worker thread need an explicit rebuild
        tray.update_menu()

    # -- Callbacks ------------------------------------------------------------

    def on_toggle_pause(icon: pystray.Icon, item_action: item) -> None:
        daemon.is_paused = not daemon.is_paused
        if daemon.is_paused:
            daemon.pause_audio()
        else:
            daemon.resume_audio()
        refresh_tray()

    def on_stop_speaking(icon: pystray.Icon, item_action: item) -> None:
        """Stop current playback immediately (same as the stop hotkey)."""
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
        text = get_clipboard_text()
        if text.strip():
            daemon.q.put(text)

    def on_toggle_skip_code(icon: pystray.Icon, item_action: item) -> None:
        daemon.toggle_skip_code_blocks()

    def on_open_settings(icon: pystray.Icon, item_action: item) -> None:
        threading.Thread(
            target=show_dictionary_ui, args=(daemon,), daemon=True
        ).start()

    def on_exit(icon: pystray.Icon, item_action: item) -> None:
        daemon.on_state_change = None
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

    def voice_group_items(voices: list[tuple[str, str]]) -> list:
        """Voices under disabled "Female" / "Male" headers."""
        items: list = []
        for gender in ("Female", "Male"):
            group = [(label, v_id) for label, v_id in voices if _voice_gender(label) == gender]
            if not group:
                continue
            if items:
                items.append(Menu.SEPARATOR)
            items.append(item(gender, None, enabled=False))
            items.extend(
                item(
                    _short_voice_label(label),
                    make_voice_setter(v_id),
                    checked=lambda i, vid=v_id: daemon.config.get("voice") == vid,
                    radio=True,
                )
                for label, v_id in group
            )
        return items

    def is_offline(v_id: str) -> bool:
        return v_id.startswith(KOKORO_PREFIX)

    online_voices = [(l, v) for l, v in VOICES.items() if not is_offline(v)]
    offline_voices = [(l, v) for l, v in VOICES.items() if is_offline(v)]

    # Voice > Online voices / Offline voices, each marked when it holds the current voice
    voice_menu_items = [
        item(
            "Online voices (Microsoft)",
            Menu(*voice_group_items(online_voices)),
            checked=lambda i: not is_offline(daemon.config.get("voice", "")),
            radio=True,
        ),
        item(
            "Offline voices (Kokoro)",
            Menu(*voice_group_items(offline_voices)),
            checked=lambda i: is_offline(daemon.config.get("voice", "")),
            radio=True,
        ),
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
    # Native Win32 menu: a tab in a label right-aligns the text after it (used for values/hints)

    tray = pystray.Icon(
        "Audify",
        create_tray_icon("ready"),
        "Audify — Ready",
        menu=Menu(
            # Status header (informational only)
            item(
                lambda text: f"Audify {__version__}\t{status_text()}",
                None,
                enabled=False,
            ),
            Menu.SEPARATOR,
            # Playback (left-clicking the icon toggles pause)
            item(
                lambda text: "Resume Reading" if daemon.is_paused else "Pause Reading",
                on_toggle_pause,
                default=True,
            ),
            item(
                lambda text: f"Stop Speaking\t{_hotkey_display_name(daemon)}",
                on_stop_speaking,
                enabled=lambda i: daemon.is_speaking,
            ),
            item(
                "Replay Last",
                on_replay_last,
                enabled=lambda i: bool(daemon.last_spoken),
            ),
            item("Read Clipboard Now", on_read_clipboard),
            Menu.SEPARATOR,
            # Voice settings with the current value right-aligned
            item(
                lambda text: f"Voice\t{_voice_display_name(daemon)}",
                Menu(*voice_menu_items),
            ),
            item(
                lambda text: f"Speed\t{_speed_display_name(daemon)}",
                Menu(*rate_menu_items),
            ),
            item(
                lambda text: f"Volume\t{_volume_display_name(daemon)}",
                Menu(*volume_menu_items),
            ),
            item(
                "Skip Code Blocks",
                on_toggle_skip_code,
                checked=lambda i: daemon.config.get("skip_code_blocks", True),
            ),
            Menu.SEPARATOR,
            item("Control Center...", on_open_settings),
            item("Quit Audify", on_exit),
        ),
    )
    daemon.tray_icon = tray
    daemon.on_state_change = refresh_tray
    tray.run()
