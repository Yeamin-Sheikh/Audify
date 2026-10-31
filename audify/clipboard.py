"""
clipboard -- Win32 event-driven clipboard listener and native hotkey handler.

Uses AddClipboardFormatListener to receive instant WM_CLIPBOARDUPDATE
notifications and RegisterHotKey to receive kernel-level WM_HOTKEY events
(such as Ctrl+Alt+S to stop playback) on the same Win32 message pump.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import threading
import time
from typing import Callable

# Private DLL handles: ctypes.windll hands every module the SAME function objects, so
# another library setting e.g. GlobalLock.restype (pyperclip does) would break ours.
user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# Win32 message constants
WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_HOTKEY = 0x0312
WM_CLIPBOARDUPDATE = 0x031D
WM_APP_REBIND_HOTKEYS = 0x8001
WM_APP_SET_ESC_GUARD = 0x8002
HWND_MESSAGE = wintypes.HWND(-3)  # Message-only window (no taskbar icon)
CF_UNICODETEXT = 13

# Win32 RegisterHotKey modifier flags
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000
HOTKEY_ID_STOP = 1
HOTKEY_ID_DICTATION = 2
HOTKEY_ID_PASTE_LAST = 3
HOTKEY_ID_ACTIVE_ESC = 4

# Win32 Virtual-Key codes for direct physical state queries via GetAsyncKeyState
VK_LBUTTON = 0x01
VK_RBUTTON = 0x02
VK_RETURN = 0x0D
VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12      # Alt key
VK_ESCAPE = 0x1B
VK_SPACE = 0x20
VK_INSERT = 0x2D
VK_DELETE = 0x2E
VK_C = 0x43
VK_X = 0x58
VK_LSHIFT = 0xA0
VK_RSHIFT = 0xA1
VK_LCONTROL = 0xA2
VK_RCONTROL = 0xA3
VK_LMENU = 0xA4
VK_RMENU = 0xA5

DOUBLE_TAP_HOTKEYS: dict[str, tuple[int, str]] = {
    "double_lctrl": (VK_LCONTROL, "Double Left Ctrl"),
    "double_rctrl": (VK_RCONTROL, "Double Right Ctrl"),
    "double_lalt": (VK_LMENU, "Double Left Alt"),
    "double_lshift": (VK_LSHIFT, "Double Left Shift"),
}

# Callback type for the window procedure
LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(
    LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
)


class WNDCLASSEXW(ctypes.Structure):
    """Win32 WNDCLASSEXW structure for registering a window class."""
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HICON),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
        ("hIconSm", wintypes.HICON),
    ]


# -- Win32 function signatures ------------------------------------------------
user32.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
user32.RegisterClassExW.restype = wintypes.ATOM
user32.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HINSTANCE]
user32.UnregisterClassW.restype = wintypes.BOOL

user32.CreateWindowExW.argtypes = [
    wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
]
user32.CreateWindowExW.restype = wintypes.HWND

user32.DestroyWindow.argtypes = [wintypes.HWND]
user32.DestroyWindow.restype = wintypes.BOOL
user32.DefWindowProcW.argtypes = [
    wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
]
user32.DefWindowProcW.restype = LRESULT

user32.AddClipboardFormatListener.argtypes = [wintypes.HWND]
user32.AddClipboardFormatListener.restype = wintypes.BOOL
user32.RemoveClipboardFormatListener.argtypes = [wintypes.HWND]
user32.RemoveClipboardFormatListener.restype = wintypes.BOOL

user32.RegisterHotKey.argtypes = [
    wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT,
]
user32.RegisterHotKey.restype = wintypes.BOOL
user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
user32.UnregisterHotKey.restype = wintypes.BOOL

user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = wintypes.SHORT

user32.GetMessageW.argtypes = [
    ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT,
]
user32.GetMessageW.restype = wintypes.BOOL
user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.TranslateMessage.restype = wintypes.BOOL
user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.DispatchMessageW.restype = LRESULT

user32.PostQuitMessage.argtypes = [ctypes.c_int]
user32.PostQuitMessage.restype = None
user32.PostMessageW.argtypes = [
    wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
]
user32.PostMessageW.restype = wintypes.BOOL

user32.OpenClipboard.argtypes = [wintypes.HWND]
user32.OpenClipboard.restype = wintypes.BOOL
user32.CloseClipboard.argtypes = []
user32.CloseClipboard.restype = wintypes.BOOL
user32.GetClipboardData.argtypes = [wintypes.UINT]
user32.GetClipboardData.restype = wintypes.HANDLE

kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE
kernel32.GlobalLock.argtypes = [wintypes.HANDLE]
kernel32.GlobalLock.restype = ctypes.c_wchar_p
kernel32.GlobalUnlock.argtypes = [wintypes.HANDLE]
kernel32.GlobalUnlock.restype = wintypes.BOOL


# -- Hardware key state helpers (immune to WH_KEYBOARD_LL stuck keys & UIPI) --

_key_state_lock = threading.Lock()


def is_vk_down(vk: int) -> bool:
    """Return True if virtual-key ``vk`` is physically held down right now."""
    return bool(user32.GetAsyncKeyState(vk) & 0x8000)


def is_ctrl_pressed() -> bool:
    """Return True if either Left or Right Ctrl is physically held down."""
    return is_vk_down(VK_CONTROL)


def check_copy_keys() -> bool:
    """Check via Win32 GetAsyncKeyState if a manual copy key or mouse click occurred.

    Polled every 15ms and also called at the moment WM_CLIPBOARDUPDATE fires:
      * Calling GetAsyncKeyState every 15ms clears any stale 0x0001 ('pressed since
        last query') bit when C/X/Insert is typed without Ctrl.
      * When Ctrl+C, Ctrl+X, Ctrl+Shift+C, or Ctrl+Insert is pressed (even in an
        Administrator window where UIPI blocks WH_KEYBOARD_LL hooks, or on a <10ms
        tap), both VK_CONTROL and the copy key report non-zero state.
    """
    with _key_state_lock:
        ctrl_state = user32.GetAsyncKeyState(VK_CONTROL)
        c_state = user32.GetAsyncKeyState(VK_C)
        x_state = user32.GetAsyncKeyState(VK_X)
        ins_state = user32.GetAsyncKeyState(VK_INSERT)
        lmb_state = user32.GetAsyncKeyState(VK_LBUTTON)
        rmb_state = user32.GetAsyncKeyState(VK_RBUTTON)

        if ctrl_state != 0 and (c_state != 0 or x_state != 0 or ins_state != 0):
            return True
        if lmb_state != 0 or rmb_state != 0:
            return True
        return False


def is_double_tap_hotkey(hotkey_str: str) -> int | None:
    """Return the Win32 VK code if *hotkey_str* is a double-tap modifier shortcut, else None."""
    entry = DOUBLE_TAP_HOTKEYS.get((hotkey_str or "").strip().lower())
    return entry[0] if entry else None


def format_hotkey_label(hotkey_str: str) -> str:
    """Return a human-readable display string for *hotkey_str*."""
    norm = (hotkey_str or "").strip().lower()
    if norm in DOUBLE_TAP_HOTKEYS:
        return DOUBLE_TAP_HOTKEYS[norm][1]
    return "+".join(part.strip().capitalize() for part in norm.split("+") if part.strip())


class DoubleTapDetector:
    """Detects a clean double-tap of a single modifier key (such as Left or Right Ctrl) via Win32 state."""

    MAX_HOLD_S = 0.40
    MAX_GAP_S = 0.45

    def __init__(self, target_vk: int | None = VK_LCONTROL) -> None:
        self.target_vk: int | None = target_vk
        self._was_down: bool = False
        self._down_time: float = 0.0
        self._tainted: bool = False
        self._first_tap_up_time: float = 0.0
        # Flush any stale 0x0001 bit from before initialization
        if target_vk is not None:
            try:
                user32.GetAsyncKeyState(target_vk)
            except Exception:
                pass

    def set_target_vk(self, target_vk: int | None) -> None:
        self.target_vk = target_vk
        if target_vk is not None:
            try:
                user32.GetAsyncKeyState(target_vk)
            except Exception:
                pass
        self.reset()

    def reset(self) -> None:
        self._was_down = False
        self._down_time = 0.0
        self._tainted = False
        self._first_tap_up_time = 0.0

    def taint(self) -> None:
        self._tainted = True
        self._first_tap_up_time = 0.0

    def taint_if_other_key(self, key_name: str) -> None:
        """Mark current tap as tainted if any non-target key is pressed."""
        if self.target_vk is None:
            return
        allowed = {
            VK_LCONTROL: ("left ctrl", "ctrl", "control_l"),
            VK_RCONTROL: ("right ctrl", "ctrl", "control_r"),
            VK_LMENU: ("left alt", "alt", "alt_l"),
            VK_LSHIFT: ("left shift", "shift", "shift_l"),
        }.get(self.target_vk, ())
        if key_name not in allowed:
            self.taint()

    def poll(self) -> bool:
        """Polled every 15ms. Returns True on the release of a clean second solo tap."""
        vk = self.target_vk
        if vk is None:
            return False

        now = time.monotonic()
        raw_state = user32.GetAsyncKeyState(vk)
        down = bool(raw_state & 0x8000)
        pressed_edge = bool(raw_state & 0x0001)

        if down or pressed_edge or self._was_down or self._first_tap_up_time > 0.0:
            for m_vk in (
                VK_LCONTROL,
                VK_RCONTROL,
                VK_LMENU,
                VK_RMENU,
                VK_LSHIFT,
                VK_RSHIFT,
                0x5B,  # VK_LWIN
                0x5C,  # VK_RWIN
                0x20,  # VK_SPACE
                0x43,  # 'C'
                0x56,  # 'V'
                0x58,  # 'X'
            ):
                if m_vk != vk and is_vk_down(m_vk):
                    self.taint()
                    break

        if down:
            if not self._was_down:
                self._was_down = True
                self._down_time = now
                self._tainted = False
                if (
                    self._first_tap_up_time
                    and (now - self._first_tap_up_time) > self.MAX_GAP_S
                ):
                    self._first_tap_up_time = 0.0
            elif pressed_edge:
                # Key was released and re-pressed within a single 15ms poll interval
                hold_dur = now - self._down_time
                if not self._tainted and hold_dur <= self.MAX_HOLD_S:
                    self._first_tap_up_time = now
                    self._down_time = now
                else:
                    self.taint()
            else:
                if (now - self._down_time) > self.MAX_HOLD_S:
                    self.taint()
            return False

        if self._was_down:
            self._was_down = False
            hold_dur = now - self._down_time
            if self._tainted or hold_dur > self.MAX_HOLD_S:
                self._tainted = False
                self._first_tap_up_time = 0.0
                return False

            if (
                self._first_tap_up_time
                and (self._down_time - self._first_tap_up_time) <= self.MAX_GAP_S
            ):
                self._first_tap_up_time = 0.0
                return True

            self._first_tap_up_time = now
            return False

        if pressed_edge:
            # Ultra-fast tap (<15ms hold) completed entirely between two polls
            if self._tainted:
                self._tainted = False
                self._first_tap_up_time = 0.0
                return False
            if (
                self._first_tap_up_time
                and (now - self._first_tap_up_time) <= self.MAX_GAP_S
            ):
                self._first_tap_up_time = 0.0
                return True
            self._first_tap_up_time = now
            return False

        if self._first_tap_up_time and (now - self._first_tap_up_time) > self.MAX_GAP_S:
            self._first_tap_up_time = 0.0
        return False


def parse_win32_hotkey(hotkey_str: str) -> tuple[int, int] | None:
    """Convert a hotkey string like 'ctrl+alt+s' into (win32_modifiers, vk_code)."""
    parts = [p.strip().lower() for p in hotkey_str.split("+") if p.strip()]
    if not parts:
        return None

    mods = 0
    key_part = ""
    for part in parts:
        if part in ("ctrl", "control", "left ctrl", "right ctrl"):
            mods |= MOD_CONTROL
        elif part in ("alt", "menu", "left alt", "right alt"):
            mods |= MOD_ALT
        elif part in ("shift", "left shift", "right shift"):
            mods |= MOD_SHIFT
        elif part in ("win", "windows", "left windows", "right windows"):
            mods |= MOD_WIN
        else:
            key_part = part

    if not key_part:
        return None

    special_vks = {
        "esc": VK_ESCAPE,
        "escape": VK_ESCAPE,
        "space": VK_SPACE,
        "enter": VK_RETURN,
        "return": VK_RETURN,
        "ins": VK_INSERT,
        "insert": VK_INSERT,
        "del": VK_DELETE,
        "delete": VK_DELETE,
        "tab": 0x09,
        "backspace": 0x08,
        "back": 0x08,
        "pageup": 0x21,
        "prior": 0x21,
        "pagedown": 0x22,
        "next": 0x22,
        "end": 0x23,
        "home": 0x24,
        "left": 0x25,
        "up": 0x26,
        "right": 0x27,
        "down": 0x28,
        "`": 0xC0,
        "grave": 0xC0,
        "-": 0xBD,
        "minus": 0xBD,
        "=": 0xBB,
        "equal": 0xBB,
        "[": 0xDB,
        "]": 0xDD,
        "\\": 0xDC,
        ";": 0xBA,
        "'": 0xDE,
        ",": 0xBC,
        ".": 0xBE,
        "/": 0xBF,
    }
    if key_part in special_vks:
        vk = special_vks[key_part]
    elif len(key_part) == 1 and key_part.isalnum():
        vk = ord(key_part.upper())
    elif key_part.startswith("f") and key_part[1:].isdigit():
        fn = int(key_part[1:])
        if 1 <= fn <= 24:
            vk = 0x70 + (fn - 1)
        else:
            return None
    else:
        return None

    return mods, vk


def _open_clipboard(attempts: int = 10, delay: float = 0.01) -> bool:
    """Open the clipboard, retrying while another app still has it open.

    Right after a copy, the source app (or a clipboard manager) often holds the
    clipboard for a few milliseconds; a single attempt would silently miss the copy.
    """
    for attempt in range(attempts):
        if user32.OpenClipboard(None):
            return True
        if attempt < attempts - 1:
            time.sleep(delay)
    return False


def get_clipboard_text() -> str:
    """Read Unicode text from the system clipboard via Win32 API.

    Returns an empty string if the clipboard is empty or doesn't contain text.
    """
    if not _open_clipboard():
        return ""
    try:
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return ""
        text = kernel32.GlobalLock(handle)
        result = str(text) if text else ""
        kernel32.GlobalUnlock(handle)
        return result
    finally:
        user32.CloseClipboard()


class ClipboardListener:
    """Listens for clipboard changes (WM_CLIPBOARDUPDATE) and global stop hotkey (WM_HOTKEY).

    Calls ``on_text_change(text)`` whenever the clipboard text content is updated,
    and ``on_stop_hotkey()`` when the registered Win32 stop hotkey is pressed.
    Runs its own message pump on a background daemon thread.
    """

    def __init__(
        self,
        on_text_change: Callable[[str], None],
        stop_hotkey: str = "ctrl+alt+s",
        on_stop_hotkey: Callable[[], None] | None = None,
        dictation_hotkey: str = "double_lctrl",
        on_dictation_hotkey: Callable[[], None] | None = None,
        paste_last_hotkey: str = "ctrl+alt+v",
        on_paste_last_hotkey: Callable[[], None] | None = None,
        on_escape_hotkey: Callable[[], None] | None = None,
        stt_enabled: bool = True,
    ) -> None:
        self.on_text_change = on_text_change
        self.stop_hotkey = stop_hotkey
        self.on_stop_hotkey = on_stop_hotkey
        self.dictation_hotkey = dictation_hotkey
        self.on_dictation_hotkey = on_dictation_hotkey
        self.paste_last_hotkey = paste_last_hotkey
        self.on_paste_last_hotkey = on_paste_last_hotkey
        self.on_escape_hotkey = on_escape_hotkey
        self.stt_enabled = bool(stt_enabled)
        self._hwnd: int | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._class_name = f"AudifyClipWatcher_{id(self)}"
        # prevent GC of the ctypes callback function pointer
        self._wnd_proc_ref: WNDPROC | None = None
        self._last_text: str = ""
        self._last_update_time: float = 0.0
        self._hotkey_registered: bool = False
        self._dictation_hotkey_registered: bool = False
        self._paste_last_hotkey_registered: bool = False
        self._esc_guard_registered: bool = False

    def _unbind_all_hotkeys(self, hwnd: int) -> None:
        if self._hotkey_registered:
            user32.UnregisterHotKey(hwnd, HOTKEY_ID_STOP)
            self._hotkey_registered = False
        if self._dictation_hotkey_registered:
            user32.UnregisterHotKey(hwnd, HOTKEY_ID_DICTATION)
            self._dictation_hotkey_registered = False
        if self._paste_last_hotkey_registered:
            user32.UnregisterHotKey(hwnd, HOTKEY_ID_PASTE_LAST)
            self._paste_last_hotkey_registered = False
        if self._esc_guard_registered:
            user32.UnregisterHotKey(hwnd, HOTKEY_ID_ACTIVE_ESC)
            self._esc_guard_registered = False

    def _bind_all_hotkeys(self, hwnd: int) -> None:
        self._unbind_all_hotkeys(hwnd)
        parsed = parse_win32_hotkey(self.stop_hotkey)
        if parsed and self.on_stop_hotkey:
            mods, vk = parsed
            if user32.RegisterHotKey(hwnd, HOTKEY_ID_STOP, mods | MOD_NOREPEAT, vk):
                self._hotkey_registered = True

        if not self.stt_enabled:
            return

        parsed_dict = parse_win32_hotkey(self.dictation_hotkey)
        if parsed_dict and self.on_dictation_hotkey:
            d_mods, d_vk = parsed_dict
            if user32.RegisterHotKey(hwnd, HOTKEY_ID_DICTATION, d_mods | MOD_NOREPEAT, d_vk):
                self._dictation_hotkey_registered = True

        parsed_paste = parse_win32_hotkey(self.paste_last_hotkey)
        if parsed_paste and self.on_paste_last_hotkey:
            p_mods, p_vk = parsed_paste
            if user32.RegisterHotKey(hwnd, HOTKEY_ID_PASTE_LAST, p_mods | MOD_NOREPEAT, p_vk):
                self._paste_last_hotkey_registered = True

    def rebind_hotkeys(
        self,
        stop_hotkey: str | None = None,
        dictation_hotkey: str | None = None,
        paste_last_hotkey: str | None = None,
        stt_enabled: bool | None = None,
    ) -> None:
        """Update hotkey strings and re-register them on the Win32 message-pump thread."""
        if stop_hotkey is not None:
            self.stop_hotkey = stop_hotkey
        if dictation_hotkey is not None:
            self.dictation_hotkey = dictation_hotkey
        if paste_last_hotkey is not None:
            self.paste_last_hotkey = paste_last_hotkey
        if stt_enabled is not None:
            self.stt_enabled = bool(stt_enabled)
        if self._hwnd:
            user32.PostMessageW(self._hwnd, WM_APP_REBIND_HOTKEYS, 0, 0)

    def set_recording_escape_guard(self, active: bool) -> None:
        """Intercept Escape via RegisterHotKey only while dictating so Escape never unfocuses the input box."""
        if self._hwnd:
            user32.PostMessageW(self._hwnd, WM_APP_SET_ESC_GUARD, 1 if active else 0, 0)

    def _wnd_proc(self, hwnd: int, msg: int, wparam: int, lparam: int) -> int:
        """Win32 window procedure. Handles clipboard updates and native global hotkeys."""
        if msg == WM_APP_REBIND_HOTKEYS:
            self._bind_all_hotkeys(hwnd)
            return 0
        if msg == WM_APP_SET_ESC_GUARD:
            if wparam:
                if not self._esc_guard_registered:
                    if user32.RegisterHotKey(hwnd, HOTKEY_ID_ACTIVE_ESC, MOD_NOREPEAT, VK_ESCAPE):
                        self._esc_guard_registered = True
            else:
                if self._esc_guard_registered:
                    user32.UnregisterHotKey(hwnd, HOTKEY_ID_ACTIVE_ESC)
                    self._esc_guard_registered = False
            return 0
        if msg == WM_HOTKEY:
            if wparam == HOTKEY_ID_STOP and self.on_stop_hotkey:
                try:
                    self.on_stop_hotkey()
                except Exception:
                    pass
            elif wparam == HOTKEY_ID_DICTATION and self.on_dictation_hotkey:
                try:
                    self.on_dictation_hotkey()
                except Exception:
                    pass
            elif wparam == HOTKEY_ID_PASTE_LAST and self.on_paste_last_hotkey:
                try:
                    self.on_paste_last_hotkey()
                except Exception:
                    pass
            elif wparam == HOTKEY_ID_ACTIVE_ESC and self.on_escape_hotkey:
                try:
                    self.on_escape_hotkey()
                except Exception:
                    pass
            return 0
        if msg == WM_CLIPBOARDUPDATE:
            try:
                now = time.monotonic()
                text = get_clipboard_text()
                # Deduplicate only rapid double-writes (<250ms) from the same copy action
                # so copying the same selection again intentionally still triggers reading.
                if text and (text != self._last_text or (now - self._last_update_time) >= 0.25):
                    self._last_text = text
                    self._last_update_time = now
                    self.on_text_change(text)
            except Exception:
                pass
            return 0
        if msg == WM_CLOSE:
            user32.DestroyWindow(hwnd)
            return 0
        if msg == WM_DESTROY:
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _run(self) -> None:
        """Background thread: register window class, create hidden window, run message pump."""
        h_inst = kernel32.GetModuleHandleW(None)
        self._wnd_proc_ref = WNDPROC(self._wnd_proc)

        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wc.lpfnWndProc = self._wnd_proc_ref
        wc.hInstance = h_inst
        wc.lpszClassName = self._class_name

        atom = user32.RegisterClassExW(ctypes.byref(wc))
        if not atom:
            self._ready.set()
            return

        self._hwnd = user32.CreateWindowExW(
            0, self._class_name, "AudifyClipWindow", 0,
            0, 0, 0, 0, HWND_MESSAGE, None, h_inst, None,
        )
        if not self._hwnd:
            user32.UnregisterClassW(self._class_name, h_inst)
            self._ready.set()
            return

        if not user32.AddClipboardFormatListener(self._hwnd):
            user32.DestroyWindow(self._hwnd)
            user32.UnregisterClassW(self._class_name, h_inst)
            self._ready.set()
            return

        # Register native Win32 global hotkeys (stop, dictation, paste-last) on this message pump
        self._bind_all_hotkeys(self._hwnd)

        # Snapshot current clipboard so we don't fire on startup
        try:
            self._last_text = get_clipboard_text()
            self._last_update_time = time.monotonic()
        except Exception:
            pass

        self._ready.set()

        # Run Win32 message pump until WM_QUIT
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

        # Cleanup
        self._unbind_all_hotkeys(self._hwnd)
        user32.RemoveClipboardFormatListener(self._hwnd)
        user32.UnregisterClassW(self._class_name, h_inst)

    def start(self) -> None:
        """Start the clipboard listener on a background daemon thread."""
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="clipboard-listener"
        )
        self._thread.start()
        self._ready.wait(timeout=5.0)

    def stop(self) -> None:
        """Shut down the listener cleanly."""
        if self._hwnd:
            user32.PostMessageW(self._hwnd, WM_CLOSE, 0, 0)
            if self._thread:
                self._thread.join(timeout=2.0)
            self._hwnd = None
