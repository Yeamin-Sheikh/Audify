"""
Entry point for Audify.

Enforces single-instance via a Windows named mutex, then launches
the system-tray daemon.  Run with:  python -m audify
"""

from __future__ import annotations

import ctypes
import sys


def main() -> None:
    """Single-instance guard and tray launch."""
    # Only one Audify instance can run at a time.
    # If a second is launched, show a friendly dialog and exit.
    _ERROR_ALREADY_EXISTS = 0xB7
    _mutex = ctypes.windll.kernel32.CreateMutexW(
        None, False, "Global\\AudifyTTSSingleInstance"
    )
    if ctypes.windll.kernel32.GetLastError() == _ERROR_ALREADY_EXISTS:
        ctypes.windll.user32.MessageBoxW(
            0,
            "Audify is already running in the system tray.\n\n"
            "Look for the violet sound-wave icon near the clock.",
            "Audify — Already Running",
            0x40,  # MB_ICONINFORMATION
        )
        sys.exit(0)

    # Import here (not at top) so the mutex check runs before any
    # heavy module initialization (pygame, pystray, etc.)
    from audify.tray import setup_tray

    setup_tray()


if __name__ == "__main__":
    main()
