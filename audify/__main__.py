"""
Entry point for Audify.

Enforces single-instance via a Windows named mutex, then launches
the system-tray daemon.  Run with:  python -m audify
"""

from __future__ import annotations

import asyncio
import ctypes
import sys
import threading
import time


def _selftest(report_path: str) -> int:
    """Check that a (frozen) build can generate Sec-MS-GEC v2 tokens, stream Edge TTS, and decode MP3 to PCM.

    Usage: Audify.exe --selftest report.txt   (windowed builds have no console)
    """
    lines: list[str] = []
    ok = True

    # 1. Verify miniaudio decoder
    try:
        import miniaudio  # noqa: F401  -- online voice decoder
        lines.append("miniaudio: ok")
    except Exception as e:
        ok = False
        lines.append(f"miniaudio: FAILED {e!r}")

    # 2. Verify edge-tts version and Sec-MS-GEC v2 token generation
    try:
        from edge_tts.constants import SEC_MS_GEC_VERSION
        from edge_tts.drm import DRM
        from audify.updater import get_installed_edge_tts_version

        ver = get_installed_edge_tts_version()
        token = DRM.generate_sec_ms_gec()
        if not SEC_MS_GEC_VERSION.startswith("2-") or "-" not in token:
            raise RuntimeError(f"Expected Sec-MS-GEC v2 token, got version={SEC_MS_GEC_VERSION} token={token}")
        lines.append(f"edge-tts: ok (v{ver}, Sec-MS-GEC {SEC_MS_GEC_VERSION})")
    except Exception as e:
        ok = False
        lines.append(f"edge-tts: FAILED {e!r}")

    # 3. Verify live streaming + decoding pipeline
    try:
        from audify.playback import SpeechSession

        loop = asyncio.new_event_loop()
        threading.Thread(target=loop.run_forever, daemon=True).start()
        t0 = time.perf_counter()
        session = SpeechSession(
            ["Self test."],
            "en-US-JennyNeural",
            "+0%",
            loop,
            (24000, 1),
        )
        session.start()
        total_pcm = 0
        first_chunk_ms: float | None = None
        while True:
            item = session.pcm.get(timeout=15)
            if item is None:
                break
            if first_chunk_ms is None:
                first_chunk_ms = (time.perf_counter() - t0) * 1000.0
            _idx, pcm_bytes = item
            total_pcm += len(pcm_bytes)
        loop.call_soon_threadsafe(loop.stop)
        if session.failed or total_pcm == 0:
            raise RuntimeError(f"No PCM audio decoded (failed={session.failed})")
        duration_s = total_pcm / (24000 * 2)
        lines.append(
            f"streaming: ok ({duration_s:.2f}s PCM decoded, first audio in {first_chunk_ms:.0f}ms)"
        )
    except Exception as e:
        ok = False
        lines.append(f"streaming: FAILED {e!r}")

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
