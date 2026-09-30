"""
clipboard -- Win32 event-driven clipboard listener.

Uses AddClipboardFormatListener to receive instant WM_CLIPBOARDUPDATE
notifications instead of polling. Runs on a background daemon thread
with its own Win32 message pump.
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
WM_CLIPBOARDUPDATE = 0x031D
HWND_MESSAGE = wintypes.HWND(-3)  # Message-only window (no taskbar icon)
CF_UNICODETEXT = 13

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
    """Listens for clipboard changes via Win32 WM_CLIPBOARDUPDATE.

    Calls `on_text_change(text)` whenever the clipboard text content changes.
    Runs its own message pump on a background daemon thread, so it doesn't
    block the calling thread.

    Usage:
        listener = ClipboardListener(my_callback)
        listener.start()   # non-blocking
        ...
        listener.stop()     # clean shutdown
    """

    def __init__(self, on_text_change: Callable[[str], None]) -> None:
        self.on_text_change = on_text_change
        self._hwnd: int | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._class_name = f"AudifyClipWatcher_{id(self)}"
        # prevent GC of the ctypes callback function pointer
        self._wnd_proc_ref: WNDPROC | None = None
        self._last_text: str = ""

    def _wnd_proc(self, hwnd: int, msg: int, wparam: int, lparam: int) -> int:
        """Win32 window procedure. Handles clipboard update messages."""
        if msg == WM_CLIPBOARDUPDATE:
            try:
                text = get_clipboard_text()
                if text and text != self._last_text:
                    self._last_text = text
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

        # Snapshot current clipboard so we don't fire on startup
        try:
            self._last_text = get_clipboard_text()
        except Exception:
            pass

        self._ready.set()

        # Run Win32 message pump until WM_QUIT
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

        # Cleanup
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
