"""
Windows Icon Cache Reset utility for Audify.

Implements the workflow from ``Icone Cache Reset.bat`` / ``Icone Cache Reset.exe``:
  1. Terminates Windows Explorer (``taskkill /f /im explorer.exe``)
  2. Deletes ``%LOCALAPPDATA%\\IconCache.db`` and modern Windows 10/11
     ``%LOCALAPPDATA%\\Microsoft\\Windows\\Explorer\\iconcache_*.db`` files
     while Explorer is stopped so file locks are released
  3. Restarts ``explorer.exe`` and runs ``ie4uinit.exe -show``
  4. Re-registers Audify's system tray icon once the taskbar finishes restarting

Runs silently without flashing a console window (``CREATE_NO_WINDOW``) on a
background daemon thread.
"""

from __future__ import annotations

import glob
import os
import subprocess
import threading
import time
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from audify.engine import TTSDaemon

CREATE_NO_WINDOW = 0x08000000

_reset_lock = threading.Lock()
_is_resetting = False


def is_icon_cache_resetting() -> bool:
    """Return True while an icon cache reset is currently in progress."""
    return _is_resetting


def _delete_icon_cache_files() -> int:
    """Delete legacy IconCache.db and Windows 10/11 Explorer iconcache_*.db files.

    Returns the number of cache files removed.
    """
    local_app_data = os.environ.get("LOCALAPPDATA", "")
    if not local_app_data:
        return 0

    removed = 0
    targets: list[str] = [
        os.path.join(local_app_data, "IconCache.db"),
    ]
    explorer_cache_dir = os.path.join(
        local_app_data, "Microsoft", "Windows", "Explorer"
    )
    if os.path.isdir(explorer_cache_dir):
        targets.extend(glob.glob(os.path.join(explorer_cache_dir, "iconcache*.db")))

    for path in targets:
        if not os.path.exists(path):
            continue
        try:
            # Clear hidden/system/read-only attributes if set (mirrors `del /a /f /q`)
            subprocess.run(
                ["attrib", "-h", "-s", "-r", path],
                creationflags=CREATE_NO_WINDOW,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=3,
                check=False,
            )
        except Exception:
            pass
        try:
            os.remove(path)
            removed += 1
        except Exception:
            pass

    # Also invoke cmd's `del /a /f /q` exactly as in `Icone Cache Reset.bat`
    # to catch any remaining hidden files
    try:
        subprocess.run(
            [
                "cmd.exe",
                "/c",
                r'del /a /f /q "%localappdata%\IconCache.db" >nul 2>&1 & '
                r'del /a /f /q "%localappdata%\Microsoft\Windows\Explorer\iconcache_*.db" >nul 2>&1',
            ],
            creationflags=CREATE_NO_WINDOW,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
        )
    except Exception:
        pass

    return removed


def _run_icon_cache_reset_sequence() -> tuple[bool, str]:
    """Kill explorer.exe, purge icon cache databases, and restart explorer.exe."""
    try:
        # Step 1: Kill Windows Explorer (`taskkill /f /im explorer.exe >nul 2>&1`)
        subprocess.run(
            ["taskkill", "/f", "/im", "explorer.exe"],
            creationflags=CREATE_NO_WINDOW,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
        )
        time.sleep(0.4)

        # Step 2: Delete IconCache.db and Explorer iconcache_*.db files
        _delete_icon_cache_files()

        # Step 3: Restart Windows Explorer (`start explorer.exe`)
        win_dir = os.environ.get("SystemRoot", r"C:\Windows")
        explorer_exe = os.path.join(win_dir, "explorer.exe")
        subprocess.Popen(
            [explorer_exe if os.path.exists(explorer_exe) else "explorer.exe"],
            creationflags=CREATE_NO_WINDOW,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        # Step 4: Trigger live Windows Shell icon refresh
        try:
            subprocess.Popen(
                ["ie4uinit.exe", "-show"],
                creationflags=CREATE_NO_WINDOW,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            pass

        return True, "Icon cache reset and Windows Explorer restarted."
    except Exception as e:
        # Ensure Explorer is restarted even if an intermediate step failed
        try:
            subprocess.Popen(
                ["explorer.exe"],
                creationflags=CREATE_NO_WINDOW,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            pass
        return False, f"Icon cache reset failed: {e}"


def reset_icon_cache(
    daemon: TTSDaemon | None = None,
    on_complete: Callable[[bool, str], None] | None = None,
) -> bool:
    """Run the Windows Icon Cache Reset asynchronously on a background thread.

    Returns True if started, or False if a reset is already running.
    """
    global _is_resetting
    with _reset_lock:
        if _is_resetting:
            return False
        _is_resetting = True

    def _worker() -> None:
        global _is_resetting
        ok = False
        message = "Icon cache reset failed."
        try:
            if daemon is not None:
                daemon._set_status("Resetting Icons...", speaking=False)

            ok, message = _run_icon_cache_reset_sequence()

            # Wait ~2.0s for the restarted explorer.exe taskbar notification area
            # to finish initializing, then re-register the Audify tray icon
            time.sleep(2.0)
            if daemon is not None and daemon.tray_icon is not None:
                try:
                    daemon.tray_icon._show()
                except Exception:
                    pass
                if ok:
                    daemon._notify_info(message)
                else:
                    daemon._notify_error(message)
        except Exception as e:
            ok = False
            message = f"Icon cache reset failed: {e}"
            if daemon is not None:
                daemon._notify_error(message)
        finally:
            with _reset_lock:
                _is_resetting = False
            if daemon is not None:
                daemon._set_status("Ready", speaking=False)
            if on_complete is not None:
                try:
                    on_complete(ok, message)
                except Exception:
                    pass

    threading.Thread(
        target=_worker, daemon=True, name="icon-cache-resetter"
    ).start()
    return True
