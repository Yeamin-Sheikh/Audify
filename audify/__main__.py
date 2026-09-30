"""
Entry point for Audify.

Enforces single-instance via a Windows named mutex, then launches
the system-tray daemon.  Run with:  python -m audify
"""

from __future__ import annotations

import ctypes
import sys


def _selftest(report_path: str) -> int:
    """Check that a (frozen) build can decode online audio and synthesize offline voices.

    Usage: Audify.exe --selftest report.txt   (windowed builds have no console)
    """
    lines: list[str] = []
    ok = True
    try:
        import miniaudio  # noqa: F401  -- online voice decoder
        lines.append("miniaudio: ok")
    except Exception as e:
        ok = False
        lines.append(f"miniaudio: FAILED {e!r}")
    try:
        from audify.kokoro_engine import KokoroEngine, find_models
        if find_models():
            samples = KokoroEngine().synthesize("Self test.", "af_heart", 1.0)
            lines.append(f"kokoro: ok ({len(samples) / 24000:.1f}s audio)")
        else:
            lines.append("kokoro: model files not installed (would download on first use)")
    except Exception as e:
        ok = False
        lines.append(f"kokoro: FAILED {e!r}")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + f"\nresult: {'PASS' if ok else 'FAIL'}\n")
    return 0 if ok else 1


def main() -> None:
    """Single-instance guard and tray launch."""
    if len(sys.argv) >= 3 and sys.argv[1] == "--selftest":
        sys.exit(_selftest(sys.argv[2]))

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
