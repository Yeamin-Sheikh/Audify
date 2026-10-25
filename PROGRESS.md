# Project progress

> Auto-maintained by dev-tracker skill. Do not edit the log section manually.

## Project info

- **Project:** Audify
- **Started:** 2026-10-16
- **Last updated:** 2026-10-25
- **Status:** Active

---

## Progress log

### 2026-10-25, v2.9.9 Double Left Ctrl trigger, Escape focus guard, adjustable silence delay, and live audio visualizer

**Duration:** ~12m
**Status:** Done

#### What changed
- Mapped default voice dictation shortcut to **Double Left Ctrl** (`double_lctrl`) using `DoubleTapDetector` in `audify/clipboard.py` and `audify/engine.py`, with support for recording double-tap modifier shortcuts (`double_lctrl`, `double_rctrl`, `double_lalt`, `double_lshift`) in the Control Center.
- Added dynamic Win32 `Escape` interception (`set_recording_escape_guard` / `HOTKEY_ID_ACTIVE_ESC` in `audify/clipboard.py` and `on_escape_pressed` in `audify/stt.py`) while dictating so pressing `Escape` after speaking finishes and transcribes immediately without blurring/unfocusing browser input fields.
- Added configurable **Auto-stop silence** delay (`STT_SILENCE_DELAYS`: `0.6s`, `0.8s`, `1.0s`, `1.4s`, `2.0s`, default `1.0s`) in both the Control Center sidebar and the system tray menu.
- Added a real-time audio-reactive **Dictation Visualizer HUD pill** (`audify/gui/stt_hud.py`, using `WS_EX_NOACTIVATE | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_TOPMOST` so it never steals focus or blocks clicks) plus a live 10 FPS 5-bar animated visualizer in the system tray icon (`create_recording_visualizer_icon` in `audify/tray.py`).
- Bumped version to `2.9.9`.

#### Files touched
- `audify/clipboard.py`, added `DOUBLE_TAP_HOTKEYS`, `DoubleTapDetector`, `format_hotkey_label()`, `is_double_tap_hotkey()`, and `set_recording_escape_guard()`.
- `audify/config.py`, set default `dictation_hotkey` to `"double_lctrl"`, added `stt_silence_delay` (`1.0s`) and `STT_SILENCE_DELAYS`.
- `audify/gui/stt_hud.py`, created non-activating click-through floating audio visualizer pill.
- `audify/stt.py`, added real-time `current_level` and `level_bars` tracking, configurable `silence_delay_s`, `on_escape_pressed()`, and HUD lifecycle hooks.
- `audify/engine.py`, wired `DoubleTapDetector`, `set_stt_silence_delay()`, and `on_escape_hotkey`.
- `audify/tray.py`, added 10 FPS animated tray icon visualizer (`create_recording_visualizer_icon`) and `Dictation Auto-Stop Delay` submenu.
- `audify/gui/control_center.py`, added `Auto-stop silence` combobox and double-tap modifier capture in the shortcut recorder.
- `audify/__main__.py`, `audify/__init__.py`, `version_info.txt`, `build_exe.bat`, `installer.iss`, `README.md`, bumped version to `2.9.9`.

---

### 2026-10-25, v2.9.8 STT Custom Vocabulary and Double-Click Auto-Correction Learner

**Duration:** ~10m
**Status:** Done

#### What changed
- Added `stt_vocabulary` (`DEFAULT_STT_VOCABULARY`, `set_stt_vocab_rule`, `remove_stt_vocab_rule`, `apply_stt_vocabulary`) in `audify/config.py` so custom names of people, apps, and places are automatically substituted in Bark 3.0 Clean transcriptions.
- Added `DictationCorrectionLearner` in `audify/stt.py` and wired mouse/keyboard hooks in `audify/engine.py`: after dictating, if the user double-clicks a misheard word in any application and types the corrected spelling, Audify silently probes the selected word, captures the typed replacement, saves `heard -> written` into `stt_vocabulary`, and updates `last_dictated`.
- Added a segmented mode switcher (`TTS Pronunciation` | `STT Vocabulary (Voice Dictation Auto-Learn)`) in `audify/gui/control_center.py` with live table synchronization when new corrections are auto-learned.
- Bumped version to `2.9.8`.

#### Files touched
- `audify/config.py`, added `DEFAULT_STT_VOCABULARY`, `set_stt_vocab_rule()`, `remove_stt_vocab_rule()`, and `apply_stt_vocabulary()`.
- `audify/stt.py`, added `send_ctrl_c()`, `DictationCorrectionLearner`, and wired vocabulary replacement + learning into `BarkCleanController`.
- `audify/engine.py`, forwarded mouse and keyboard events to `self.stt.learner` and suppressed TTS readback during double-click clipboard probing.
- `audify/gui/control_center.py`, added `TTS Pronunciation` / `STT Vocabulary` mode switcher and live auto-learn table sync.
- `audify/__main__.py`, `audify/__init__.py`, `version_info.txt`, `build_exe.bat`, `installer.iss`, `README.md`, bumped version to `2.9.8`.

---

### 2026-10-25, v2.9.7 Customizable global shortcuts and Paste Last Dictation

**Duration:** ~6m
**Status:** Done

#### What changed
- Added **Paste Last Dictation** (`paste_last_dictation()` in `audify/stt.py` and `audify/engine.py`, default hotkey `Ctrl+Alt+V` in `audify/config.py`, plus **Paste Last Dictation** in `audify/tray.py`) so if the user's cursor missed the target input box when dictating, pressing `Ctrl+Alt+V` re-pastes the last dictated text without triggering TTS readback.
- Added interactive click-to-record shortcut badges in the Control Center sidebar footer (`audify/gui/control_center.py`) for `Dictate (Bark 3.0)`, `Paste last dictation`, and `Stop playback`, backed by live Win32 hotkey rebinding (`WM_APP_REBIND_HOTKEYS` in `audify/clipboard.py` and `TTSDaemon.set_hotkey()` in `audify/engine.py`).
- Bumped version to `2.9.7`.

#### Files touched
- `audify/config.py`, added `"paste_last_hotkey": "ctrl+alt+v"`.
- `audify/clipboard.py`, added `HOTKEY_ID_PASTE_LAST`, `WM_APP_REBIND_HOTKEYS`, expanded `parse_win32_hotkey`, and added `rebind_hotkeys()`.
- `audify/stt.py`, added `last_dictated` tracking, `paste_last_dictation()`, and modifier-release wait in `send_ctrl_v()`.
- `audify/engine.py`, added `_is_paste_last_combo_down()`, `paste_last_dictation()`, and `set_hotkey()`.
- `audify/tray.py`, added **Paste Last Dictation** menu item.
- `audify/gui/control_center.py`, added click-to-record shortcut badges in the sidebar footer.
- `audify/__main__.py`, `audify/__init__.py`, `version_info.txt`, `build_exe.bat`, `installer.iss`, `README.md`, bumped version to `2.9.7`.

---

### 2026-10-25, v2.9.6 Google-style automatic end-of-speech (VAD) detection for Bark 3.0 Clean

**Duration:** ~8m
**Status:** Done

#### What changed
- Added adaptive Voice Activity Detection (VAD) in `Win32MicRecorder._record_loop` (`audify/stt.py`) that tracks ambient microphone noise floor, speech onset (with 400ms pre-roll preservation), and 1.4-second post-speech silence (`VAD_END_SILENCE_CHUNKS = 14`) to automatically stop recording, trim dead air, transcribe with Bark 3.0 Clean, and auto-paste without requiring a second `Ctrl+Alt+D` keypress.
- Added an 8-second no-speech timeout (`VAD_NO_SPEECH_TIMEOUT_CHUNKS = 80`) so pressing `Ctrl+Alt+D` without speaking cancels cleanly back to `"Ready"`.
- Added silent background pre-warming of the Bark 3.0 Clean worker 3 seconds after `TTSDaemon` startup (`IDLE_UNLOAD_SECONDS = 1800.0`) so even the first dictation completes in ~2 seconds.
- Bumped version to `2.9.6`.

#### Files touched
- `audify/stt.py`, added `_frame_rms_and_peak()`, adaptive VAD state machine in `Win32MicRecorder`, `_on_vad_auto_stop()` in `BarkCleanController`, and startup pre-warming.
- `audify/gui/control_center.py`, wired `_done` callback into `start_dictation()` so Control Center UI updates on VAD auto-stop.
- `audify/__init__.py`, `version_info.txt`, `build_exe.bat`, `installer.iss`, `README.md`, bumped version to `2.9.6`.

---

### 2026-10-25, v2.9.5 Speech-to-Text dictation with Underdog Bark 3.0 Clean

**Duration:** ~20m
**Status:** Done

#### What changed
- Added `audify/stt.py` integrating **Underdog Bark 3.0 Clean** (`ConwayResearch/Underdog-Bark-3.0-Clean`, 171 MB ANS-compressed `ParakeetForTDT` 0.6B with built-in disfluency removal that strips "um", "uh", and false starts).
- Implemented `Win32MicRecorder` using Windows Multimedia API (`winmm.dll` `waveInOpen`) via `ctypes` for zero-dependency 16 kHz 16-bit mono PCM capture.
- Implemented `BarkCleanController` with background model pre-warming while the user speaks, disk caching of unpacked `model.safetensors` in `%LOCALAPPDATA%\Audify\bark_clean\cached_hf`, automatic unloading after 3 minutes of inactivity, and clipboard auto-paste (`Ctrl+V`) that suppresses TTS self-readback.
- Added global hotkey `Ctrl+Alt+D` (`dictation_hotkey`) in `audify/config.py`, `audify/clipboard.py`, and `audify/engine.py`, an emerald-green `"recording"` tray icon state and **Start Dictation (Bark 3.0)** menu item in `audify/tray.py`, and a **Start Dictation (Bark 3.0)** button + `Ctrl+Alt+D` shortcut badge in `audify/gui/control_center.py`.
- Bumped version to `2.9.5`.

#### Files touched
- `audify/stt.py`, new Speech-to-Text module (`Win32MicRecorder`, `BarkCleanController`, `set_clipboard_text`, `send_ctrl_v`).
- `audify/config.py`, added `"dictation_hotkey": "ctrl+alt+d"`.
- `audify/clipboard.py`, added `HOTKEY_ID_DICTATION` (`WM_HOTKEY`) registration alongside `HOTKEY_ID_STOP`.
- `audify/engine.py`, wired `self.stt = BarkCleanController(daemon=self)` and rising-edge `Ctrl+Alt+D` detection.
- `audify/tray.py`, added `"recording"` icon gradient, **Start Dictation (Bark 3.0)** / **Stop & Transcribe** menu item, and worker shutdown on exit.
- `audify/gui/control_center.py`, added **Start Dictation (Bark 3.0)** button and `Ctrl+Alt+D` footer badge.
- `audify/__main__.py`, `audify/__init__.py`, `version_info.txt`, `build_exe.bat`, `installer.iss`, `README.md`, bumped version to `2.9.5`.

---

### 2026-10-21, v2.9.4 built-in Windows Icon Cache Reset utility

**Duration:** ~8m
**Status:** Done

#### What changed
- Added `audify/icon_cache.py` implementing the silent Windows Icon Cache Reset workflow (`taskkill /f /im explorer.exe`, deleting `%LOCALAPPDATA%\IconCache.db` and `Explorer\iconcache_*.db`, restarting `explorer.exe`, running `ie4uinit.exe -show`, and re-registering Audify's tray icon once the taskbar reloads).
- Added **Reset Icon Cache** to the system tray context menu in `audify/tray.py` and placed **Restart Bluetooth** and **Reset Icon Cache** side-by-side under **System Tools** in the Control Center sidebar (`audify/gui/control_center.py`).
- Bumped version to `2.9.4`.

#### Files touched
- `audify/icon_cache.py`, new module for silent Windows icon cache reset and Explorer restart.
- `audify/tray.py`, added **Reset Icon Cache** menu item.
- `audify/gui/control_center.py`, grouped **Restart Bluetooth** and **Reset Icon Cache** in a 2-column horizontal row under **System Tools**.
- `audify/__main__.py`, `audify/__init__.py`, `version_info.txt`, `build_exe.bat`, `installer.iss`, `README.md`, bumped version to `2.9.4`.

---

### 2026-10-21, v2.9.3 built-in Intel Wireless Bluetooth driver restarter

**Duration:** ~10m
**Status:** Done

#### What changed
- Added `audify/bluetooth.py` implementing the 4-step Intel Wireless Bluetooth reset workflow (`pnputil /restart-device`, `Disable-PnpDevice`/`Enable-PnpDevice`, `bthserv` service restart, and `pnputil /scan-devices`) with `ShellExecuteExW` (`runas`) elevation tracking and automatic `pygame.mixer` rebinding once Bluetooth audio reconnects.
- Added **Restart Bluetooth** to the system tray context menu in `audify/tray.py` and a **Restart Bluetooth Driver** button under the **Hardware** section of the Control Center sidebar in `audify/gui/control_center.py`.
- Bumped version to `2.9.3`.

#### Files touched
- `audify/bluetooth.py`, new module for elevated Bluetooth driver reset and audio endpoint reconnection.
- `audify/tray.py`, added **Restart Bluetooth** menu item.
- `audify/gui/control_center.py`, added **Hardware -> Restart Bluetooth Driver** button and status label.
- `audify/__main__.py`, `audify/__init__.py`, `version_info.txt`, `build_exe.bat`, `installer.iss`, `README.md`, bumped version to `2.9.3`.

---

### 2026-10-21, v2.9.2 WASAPI audio client crash fix and multi-driver fallback

**Duration:** ~10m
**Status:** Done

#### What changed
- Removed unconditional module-level `pygame.mixer.pre_init()` and `pygame.mixer.init()` calls at lines 52-53 of `audify/engine.py` that crashed `Audify.exe` on import whenever Windows WASAPI had not finished initializing at startup or when no audio output device was active.
- Added `TTSDaemon._ensure_mixer(retries, force_reinit)` with `threading.Lock` and automatic driver fallback across default SDL, `wasapi`, `directsound`, and `winmm` at `(24000, 1, 512)` and `(48000, 2, 1024)`.
- Guarded all `self._tts_channel` accesses (`set_volume`, `_halt_channel`, `pause_audio`, `resume_audio`, `check_pause_and_wait`) when no audio device is connected.
- Added lazy mixer retry (`retries=3`) at the start of `_speak()` and mid-playback `pygame.error` recovery (`force_reinit=True`) so unplugging headphones or switching outputs mid-sentence reinitializes the mixer and resumes cleanly.
- Added an `audio-mixer` fallback test to `audify/__main__.py` (`--selftest`).

#### Files touched
- `audify/engine.py`, removed import-time `pygame.mixer.init()`, added `_ensure_mixer()` with `wasapi`/`directsound`/`winmm` fallback, and added mid-playback device recovery.
- `audify/__main__.py`, added `audio-mixer` multi-driver verification to `--selftest`.
- `audify/__init__.py`, `version_info.txt`, `build_exe.bat`, `installer.iss`, `README.md`, bumped version to `2.9.2`.

#### Issues found
- `audify/engine.py` line 53 called `pygame.mixer.init()` unconditionally at import time, raising `pygame.error: WASAPI can't initialize audio client:` when launched via Windows Startup before the default WASAPI endpoint was ready or when audio output devices were temporarily disconnected.

#### Next steps
- Rebuild `Audify.exe` and `Setup_Audify_2.9.2.exe`, run `--selftest`, install to `%LOCALAPPDATA%\Programs\Audify\`, and publish GitHub release `v2.9.2`.

---

### 2026-10-18, v2.9.1 native Win32 hotkey and copy shortcut fix

**Duration:** ~15m
**Status:** Done

#### What changed
- Replaced `keyboard.add_hotkey` with a 3-layer native Win32 hotkey and copy-detection system in `audify/clipboard.py` and `audify/engine.py`.
- Added kernel-level `user32.RegisterHotKey` (`WM_HOTKEY`) on `ClipboardListener`'s Win32 message pump for `stop_hotkey` (`ctrl+alt+s`), making it immune to `WH_KEYBOARD_LL` unhooking, `keyboard._pressed_events` stuck keys, and Administrator window UIPI blocking.
- Added `check_copy_keys()` using `user32.GetAsyncKeyState` (`VK_CONTROL`, `VK_C`, `VK_X`, `VK_INSERT`, `VK_LBUTTON`, `VK_RBUTTON`) polled every 15ms and checked on `WM_CLIPBOARDUPDATE`, plus a stateless `keyboard.on_press` handler that clears `keyboard._pressed_events`.
- Updated `ClipboardListener._wnd_proc` to only deduplicate identical `WM_CLIPBOARDUPDATE` bursts within 250ms, and cleared `self.last_spoken` in `TTSDaemon.stop_audio()` so re-copying the same text after stopping reads it again.

#### Files touched
- `audify/clipboard.py`, added `RegisterHotKey`/`WM_HOTKEY`, `parse_win32_hotkey()`, `check_copy_keys()`, and 250ms burst deduplication.
- `audify/engine.py`, replaced `keyboard.add_hotkey` with `_poll_input_state_loop()`, stateless `_on_key_press()`, and `last_spoken` reset on `stop_audio()`.
- `audify/__main__.py`, added Win32 hotkey registration check to `--selftest`.
- `audify/__init__.py`, `version_info.txt`, `build_exe.bat`, `installer.iss`, `README.md`, bumped version to `2.9.1`.

#### Issues found
- `keyboard.add_hotkey` in `keyboard==0.13.5` matches `tuple(sorted(_pressed_events))` by exact equality; any missed `KEY_UP` across `Alt+Tab` into an elevated window or UAC prompt leaves a stuck scan code in `_pressed_events` and permanently breaks all `add_hotkey` bindings.
- `keyboard.add_hotkey` also had a thread race on fast `<15ms` key taps where `_pressed_events` was emptied by `KEY_UP` before `processing_thread` ran `pre_process_event`.

#### Next steps
- Rebuild `Audify.exe` and `Setup_Audify_2.9.1.exe` and update installed application.

---

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
