"""
Weekly update checker for edge-tts.

Microsoft periodically updates the Sec-MS-GEC token version or headers required
by the Edge Read Aloud endpoint. This module checks PyPI once every 7 days in
a background thread, persists the last check timestamp in config.json, and
automatically upgrades edge-tts (when running from source/venv) or notifies
the user via the system tray when a newer release is available.
"""
from __future__ import annotations

import importlib.metadata
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from audify.engine import TTSDaemon

PYPI_EDGE_TTS_URL = "https://pypi.org/pypi/edge-tts/json"
WEEK_SECONDS = 7 * 24 * 60 * 60  # 604,800 seconds (7 days)


def get_installed_edge_tts_version() -> str:
    """Return the currently installed edge-tts version string."""
    try:
        return importlib.metadata.version("edge-tts")
    except Exception:
        return "0.0.0"


def fetch_latest_pypi_version(timeout: int = 8) -> str | None:
    """Query PyPI JSON API for the latest published edge-tts version."""
    try:
        req = urllib.request.Request(
            PYPI_EDGE_TTS_URL,
            headers={"Accept": "application/json", "User-Agent": "Audify-Updater/2.9"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        return str(payload.get("info", {}).get("version", "")).strip() or None
    except Exception:
        return None


def _version_tuple(version_str: str) -> tuple[int, ...]:
    """Convert a version string like '7.3.1' into a comparable integer tuple."""
    parts: list[int] = []
    for part in version_str.split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def check_edge_tts_update(daemon: "TTSDaemon", force: bool = False) -> dict[str, str | bool]:
    """Check if a newer edge-tts version exists on PyPI and update config state.

    Returns a dictionary summarizing installed version, latest version, and
    whether an update was found or applied.
    """
    now = time.time()
    with daemon._config_lock:
        try:
            last_check = float(daemon.config.get("last_edge_tts_check") or 0.0)
        except (TypeError, ValueError):
            last_check = 0.0

    installed = get_installed_edge_tts_version()
    if not force and (now - last_check) < WEEK_SECONDS:
        return {"checked": False, "installed": installed, "latest": installed, "updated": False}

    latest = fetch_latest_pypi_version()
    if not latest:
        return {"checked": False, "installed": installed, "latest": installed, "updated": False}

    with daemon._config_lock:
        daemon.config["last_edge_tts_check"] = now
        daemon.config["latest_edge_tts_version"] = latest
    daemon._debounced_save()

    if _version_tuple(latest) > _version_tuple(installed):
        # When running from Python source/venv (not frozen PyInstaller exe),
        # auto-upgrade edge-tts in the current environment.
        if not getattr(sys, "frozen", False) and os.path.isfile(sys.executable):
            try:
                subprocess.run(
                    [sys.executable, "-m", "pip", "install", "--upgrade", f"edge-tts=={latest}"],
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=60,
                )
                daemon._notify_info(
                    f"edge-tts was updated from {installed} to {latest}. Restart Audify to use the new version."
                )
                return {"checked": True, "installed": installed, "latest": latest, "updated": True}
            except Exception:
                pass

        daemon._notify_info(
            f"A newer edge-tts version ({latest}) is available on PyPI (installed: {installed})."
        )
        return {"checked": True, "installed": installed, "latest": latest, "updated": False}

    return {"checked": True, "installed": installed, "latest": latest, "updated": False}


def start_weekly_update_checker(daemon: "TTSDaemon") -> None:
    """Run a background thread that checks for edge-tts updates once a week."""

    def _loop() -> None:
        # Delay initial startup check by 15 seconds so startup and pre-warming finish first
        time.sleep(15.0)
        while True:
            try:
                check_edge_tts_update(daemon, force=False)
            except Exception as e:
                print(f"[WARN] Weekly edge-tts update check failed: {e}")
            # Sleep 6 hours between timestamp checks; actual network call only fires once per 7 days
            time.sleep(6 * 3600)

    threading.Thread(target=_loop, daemon=True, name="edge-tts-updater").start()
