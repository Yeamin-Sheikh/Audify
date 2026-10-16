# Project progress

> Auto-maintained by dev-tracker skill. Do not edit the log section manually.

## Project info

- **Project:** Audify
- **Started:** 2026-10-16
- **Last updated:** 2026-10-16
- **Status:** Active

---

## Progress log

### 2026-10-16, v2.9.0 edge-tts 7.3.1 upgrade and Kokoro removal

**Duration:** ~30m
**Status:** Done

#### What changed
- Upgraded `edge-tts` from `7.2.8` to `7.3.1` in `requirements.txt` and `venv` to support Microsoft's Sec-MS-GEC v2 WebSocket authentication tokens and fix `HTTP 403 Forbidden` failures.
- Removed the local offline Kokoro ONNX engine (`audify/kokoro_engine.py`) and uninstalled `kokoro-onnx`, `onnxruntime`, `espeakng-loader`, `phonemizer`, `numpy`, `flatbuffers`, `protobuf`, `joblib`, `dlinfo`, and `cloudpickle`.
- Deleted all local Kokoro model files (`e:\Scripts\Audify\models\`, `%LOCALAPPDATA%\Programs\Audify\models\`, and stale PyInstaller `_MEI*` temp folders), reclaiming over 1.3 GB of disk space.
- Optimized `audify/playback.py` and `audify/engine.py` for low-latency streaming with a persistent `_ReusableTCPConnector` (`ttl_dns_cache=300`), startup Sec-MS-GEC v2 token and DNS pre-warming, 2-chunk concurrent prefetching (`asyncio.Semaphore(2)`), 50ms decode blocks (`DECODE_BLOCK_SECONDS = 0.05`), and an 80-character first-chunk target.
- Added `audify/updater.py` for automated weekly checks against PyPI (`https://pypi.org/pypi/edge-tts/json`).
- Updated `VOICES` in `audify/config.py` to remove retired `en-US-JaneNeural` and offline Kokoro entries, adding `en-US-EmmaMultilingualNeural` plus British Neural voices (`Sonia`, `Libby`, `Ryan`, `Thomas`).
- Added terminal copy hotkeys (`ctrl+shift+c`, `ctrl+insert`) in `audify/engine.py`.

#### Files touched
- `requirements.txt`, updated `edge-tts==7.3.1` and removed `kokoro-onnx` and `onnxruntime`.
- `audify/__init__.py`, bumped version to `2.9.0`.
- `audify/__main__.py`, updated `--selftest` to validate `miniaudio`, `edge-tts` Sec-MS-GEC v2 tokens, and live streaming latency.
- `audify/playback.py`, removed Kokoro producer and added persistent DNS/SSL connector, startup pre-warming, and concurrent chunk pipelining.
- `audify/engine.py`, removed offline fallback state, tuned first-chunk latency, added terminal copy hotkeys, and wired weekly update checker.
- `audify/updater.py`, added weekly PyPI version checker and source auto-updater for `edge-tts`.
- `audify/config.py`, updated `VOICES` list and added migration for legacy voice IDs and the `"HTTP"` pronunciation entry.
- `audify/tray.py`, removed offline voice submenu nesting so `Female` and `Male` groups appear directly under `Voice`.
- `audify/pronunciation_library.py`, removed Kokoro reference from module docstring.
- `build_exe.bat`, removed Kokoro/ONNX PyInstaller flags and updated version to `2.9.0`.
- `installer.iss`, removed bundled Kokoro model files and updated version to `2.9.0`.
- `.gitignore`, removed `models/` entry.
- `README.md`, updated documentation, voice table, architecture diagram, and v2.9.0 changelog.

#### Issues found
- `edge-tts==7.2.8` failed with `WSServerHandshakeError: 403` because Microsoft switched to Sec-MS-GEC v2 tokens on October 13, 2026.
- `en-US-JaneNeural` was removed by Microsoft from the live Edge Read Aloud voice list.
- Offline Kokoro synthesis on CPU caused high CPU load, 600 MB+ disk footprint, and premature 0.6s stall fallbacks.

#### Next steps
- Monitor weekly `edge-tts` PyPI checks via the scheduled automation and `audify/updater.py`.

---
