"""
Bluetooth driver restarter integration for Audify.

Embeds the 4-step Intel(R) Wireless Bluetooth(R) reset sequence:
  1. pnputil /restart-device
  2. PnP disable -> 2s wait -> enable cycle
  3. Restart Windows Bluetooth Support Service (bthserv)
  4. pnputil /scan-devices + verify device status

Runs elevated via Win32 ShellExecuteExW ("runas") on a background thread so the
tray menu and Control Center UI stay responsive. Once the elevated reset finishes,
Audify automatically re-initializes its audio mixer so reconnected Bluetooth
headphones or speakers immediately receive TTS audio.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
import tempfile
import threading
import time
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from audify.engine import TTSDaemon

# Default Intel(R) Wireless Bluetooth(R) hardware instance ID on this machine,
# with automatic fallback detection in PowerShell if the USB port path changes.
DEFAULT_BT_INSTANCE_ID = r"USB\VID_8087&PID_0029\5&37033362&0&10"

_restart_lock = threading.Lock()
_is_restarting = False

# -- Win32 ShellExecuteExW definitions for elevated process tracking ----------

SEE_MASK_NOCLOSEPROCESS = 0x00000040
SW_SHOWNORMAL = 1
INFINITE = 0xFFFFFFFF
WAIT_OBJECT_0 = 0x00000000


class SHELLEXECUTEINFOW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("fMask", wintypes.ULONG),
        ("hwnd", wintypes.HWND),
        ("lpVerb", wintypes.LPCWSTR),
        ("lpFile", wintypes.LPCWSTR),
        ("lpParameters", wintypes.LPCWSTR),
        ("lpDirectory", wintypes.LPCWSTR),
        ("nShow", ctypes.c_int),
        ("hInstApp", wintypes.HINSTANCE),
        ("lpIDList", ctypes.c_void_p),
        ("lpClass", wintypes.LPCWSTR),
        ("hkeyClass", wintypes.HKEY),
        ("dwHotKey", wintypes.DWORD),
        ("hIconOrMonitor", wintypes.HANDLE),
        ("hProcess", wintypes.HANDLE),
    ]


_shell32 = ctypes.WinDLL("shell32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

_shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(SHELLEXECUTEINFOW)]
_shell32.ShellExecuteExW.restype = wintypes.BOOL

_kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
_kernel32.WaitForSingleObject.restype = wintypes.DWORD

_kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
_kernel32.GetExitCodeProcess.restype = wintypes.BOOL

_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
_kernel32.CloseHandle.restype = wintypes.BOOL


# -- Batch script template ----------------------------------------------------
# Mirrors the user's Restart-Bluetooth.bat workflow with automatic fallback if
# the USB port path ever changes, and auto-closes 3 seconds after completion so
# it works as a one-click tray/Control Center utility.

_BATCH_SCRIPT = r"""@echo off
setlocal EnableDelayedExpansion

title Bluetooth Driver Restarter — Audify
color 0b
echo ========================================================
echo   Restarting Intel(R) Wireless Bluetooth(R) Driver
echo ========================================================
echo.

set "BT_ID=USB\VID_8087&PID_0029\5&37033362&0&10"
for /f "usebackq delims=" %%I in (`powershell -NoProfile -ExecutionPolicy Bypass -Command "$d = Get-PnpDevice -InstanceId 'USB\VID_8087&PID_0029\5&37033362&0&10' -ErrorAction SilentlyContinue; if (-not $d) { $d = Get-PnpDevice -Class Bluetooth -ErrorAction SilentlyContinue | Where-Object { $_.InstanceId -like 'USB\VID_8087*' -or $_.FriendlyName -like '*Wireless Bluetooth*' } | Select-Object -First 1 }; if ($d) { $d.InstanceId }"`) do (
    set "BT_ID=%%I"
)

echo Target Device: !BT_ID!
echo.

echo [1/4] Restarting device via pnputil...
pnputil /restart-device "!BT_ID!"

echo.
echo [2/4] Resetting device state via PnP disable/enable cycle...
powershell -NoProfile -ExecutionPolicy Bypass -Command "Disable-PnpDevice -InstanceId '!BT_ID!' -Confirm:$false -ErrorAction SilentlyContinue; Start-Sleep -Seconds 2; Enable-PnpDevice -InstanceId '!BT_ID!' -Confirm:$false -ErrorAction SilentlyContinue"

echo.
echo [3/4] Restarting Bluetooth Support Service (bthserv)...
net stop bthserv /y >nul 2>&1
net start bthserv >nul 2>&1

echo.
echo [4/4] Scanning for hardware changes...
pnputil /scan-devices

echo.
echo ========================================================
echo Device Status:
echo ========================================================
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-PnpDevice -InstanceId '!BT_ID!' -ErrorAction SilentlyContinue | Format-Table FriendlyName, Status, Problem -AutoSize"

echo.
echo Completed! Closing automatically in 3 seconds...
timeout /t 3 >nul
exit /b 0
"""


def is_bluetooth_restarting() -> bool:
    """Return True while a Bluetooth driver restart is currently in progress."""
    return _is_restarting


def _run_elevated_batch(script_path: str, timeout_ms: int = 90000) -> tuple[bool, str]:
    """Execute *script_path* elevated via ShellExecuteExW('runas') and wait for completion."""
    sei = SHELLEXECUTEINFOW()
    sei.cbSize = ctypes.sizeof(SHELLEXECUTEINFOW)
    sei.fMask = SEE_MASK_NOCLOSEPROCESS
    sei.hwnd = None
    sei.lpVerb = "runas"
    sei.lpFile = "cmd.exe"
    sei.lpParameters = f'/c ""{script_path}""'
    sei.lpDirectory = os.path.dirname(script_path)
    sei.nShow = SW_SHOWNORMAL

    if not _shell32.ShellExecuteExW(ctypes.byref(sei)):
        err = ctypes.get_last_error()
        # ERROR_CANCELLED (1223) means the user clicked "No" on the UAC prompt
        if err == 1223:
            return False, "Cancelled (UAC prompt declined)."
        return False, f"Failed to launch elevated command (Win32 error {err})."

    h_proc = sei.hProcess
    if not h_proc:
        return False, "Elevated process handle unavailable."

    try:
        wait_res = _kernel32.WaitForSingleObject(h_proc, timeout_ms)
        if wait_res != WAIT_OBJECT_0:
            return False, "Bluetooth restart timed out."
        exit_code = wintypes.DWORD(0)
        _kernel32.GetExitCodeProcess(h_proc, ctypes.byref(exit_code))
        if exit_code.value != 0:
            return False, f"Bluetooth restart exited with code {exit_code.value}."
        return True, "Bluetooth driver restarted and audio reconnected."
    finally:
        _kernel32.CloseHandle(h_proc)


def restart_bluetooth_driver(
    daemon: TTSDaemon | None = None,
    on_complete: Callable[[bool, str], None] | None = None,
) -> bool:
    """Launch the Bluetooth driver restart asynchronously on a background thread.

    Returns True if started, or False if a restart is already running.
    """
    global _is_restarting
    with _restart_lock:
        if _is_restarting:
            return False
        _is_restarting = True

    def _worker() -> None:
        global _is_restarting
        script_path: str | None = None
        ok = False
        message = "Bluetooth restart failed."
        try:
            if daemon is not None:
                daemon.stop_audio()
                daemon._set_status("Restarting Bluetooth...", speaking=False)

            fd, script_path = tempfile.mkstemp(
                prefix="audify_restart_bt_", suffix=".bat"
            )
            with os.fdopen(fd, "w", encoding="utf-8", newline="\r\n") as f:
                f.write(_BATCH_SCRIPT)

            ok, message = _run_elevated_batch(script_path)

            if ok and daemon is not None:
                # Give Bluetooth audio endpoints (A2DP/hands-free) ~1.5s to re-register
                # with Windows Audio after bthserv restarts, then rebind pygame.mixer
                time.sleep(1.5)
                daemon._ensure_mixer(retries=3, force_reinit=True)
                daemon._notify_info(message)
            elif not ok and daemon is not None and "Cancelled" not in message:
                daemon._notify_error(message)
        except Exception as e:
            ok = False
            message = f"Bluetooth restart failed: {e}"
            if daemon is not None:
                daemon._notify_error(message)
        finally:
            if script_path:
                try:
                    os.remove(script_path)
                except Exception:
                    pass
            with _restart_lock:
                _is_restarting = False
            if daemon is not None:
                daemon._set_status("Ready", speaking=False)
            if on_complete is not None:
                try:
                    on_complete(ok, message)
                except Exception:
                    pass

    threading.Thread(
        target=_worker, daemon=True, name="bluetooth-restarter"
    ).start()
    return True
