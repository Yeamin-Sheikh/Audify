# Audify v2.7.0

A sleek, standalone Windows background AI Voice Daemon that reads your clipboard aloud using ultra-realistic Microsoft Neural voices online, or natural-sounding Kokoro voices that run offline on your PC. Built to run invisibly in the System Tray.

## Features

- **Smart Auto-Reader** — Monitors your clipboard. Copy text with `Ctrl+C` or `Ctrl+X` and it reads it aloud instantly.
- **Wispr Flow Proof** — Ignores programmatic clipboard changes so it won't read your own dictations back to you.
- **Single Instance Guard** — Only one Audify can run at a time. Launching a second shows a friendly notification.
- **Dynamic System Tray** — Left-click the tray icon to toggle pause/resume. The icon is a violet "A" monogram when ready, turns blue while speaking, and grey with a pause symbol when paused.
- **Pronunciation Dictionary** — Map problem words to phonetic spellings (e.g., `GUI` -> `gooey`) via a built-in GUI editor.
- **26 Voices & 8 Speeds** — 13 online Microsoft Neural voices plus 13 offline Kokoro voices (American and British). Switch voices and speed instantly from the tray menu.
- **Offline Voices** — Kokoro runs entirely on your PC: free, private, and no internet needed. It loads only when an offline voice is used and unloads after 5 idle minutes, so Audify stays light.
- **Global Kill-Switch** — Press `Ctrl + Alt + S` anywhere to stop playback immediately.
- **Smart Code Handling** — Replaces code blocks with "[Skipped code block]" instead of reading raw syntax.
- **Zero-Gap Playback** — Seamless gapless chunk transitions using channel-queued audio (no silence between chunks).
- **Reading Progress** — Tray tooltip shows "Reading (3/7)" during long reads so you know what's happening.
- **Error Notifications** — Windows toast notifications when TTS fails (e.g., no internet).
- **Auto-Retry** — Retries TTS generation up to 3 times on network failures, and reports an error within seconds when you're offline.
- **Streaming Playback** — Audio starts playing while it is still downloading, entirely in memory (no temporary files).
- **Session History** — Logs everything read during the session; history is securely deleted on exit.

## Installation

### Installer (Recommended)

1. Download **`Setup_Audify_2.7.0.exe`** from the [Releases](../../releases) page.
2. Run the installer — it creates Start Menu and optional Desktop/Startup shortcuts.
3. Launch Audify. Look for the violet "A" icon in your System Tray.

### Portable

1. Download **`Audify.exe`** from the [Releases](../../releases) page.
2. Place it anywhere and run. No installation needed.
3. The first time you pick an offline voice, Audify downloads the voice model (~200 MB) once.

## Usage

| Action | How |
|---|---|
| Read text | Copy it with `Ctrl+C` — Audify reads it automatically |
| Pause / Resume | Left-click the tray icon |
| Stop playback | `Ctrl + Alt + S` (global hotkey) |
| Change voice | Right-click tray -> Voice |
| Change speed | Right-click tray -> Speed |
| Change volume | Right-click tray -> Volume |
| Skip code blocks | Right-click tray -> Skip Code Blocks |
| Edit pronunciations | Right-click tray -> Control Center... |
| Force-read clipboard | Right-click tray -> Read Clipboard Now |
| Re-read last text | Right-click tray -> Replay Last |
| Exit | Right-click tray -> Quit Audify |

## Developer Guide

### Prerequisites

- Python 3.11+
- Windows 10/11

### Setup

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

Offline voices need the Kokoro model files in `models\` (the installer bundles them; from source they download on first use):

```bash
gh release download model-files-v1.0 --repo thewh1teagle/kokoro-onnx -D models -p "kokoro-v1.0.fp16.onnx" -p "voices-v1.0.bin"
```

### Run from Source

```bash
python -m audify
```

### Build Executable

```bash
build_exe.bat
```

This produces the portable `Audify.exe` in the project root and a folder build in `App\Audify\` that the installer uses (it starts faster because nothing is unpacked at launch).

To check a build works (including offline voices), run `Audify.exe --selftest report.txt` and read the report.

### Build Installer

Requires [Inno Setup](https://jrsoftware.org/isinfo.php):

```bash
& "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer.iss
```

This produces `Output\Setup_Audify_2.7.0.exe`.

### Project Structure

```
audify/
  __init__.py           # Package version
  __main__.py           # Entry point (single-instance guard)
  config.py             # Config constants, load/save, pronunciation dictionary
  engine.py             # TTSDaemon class (TTS pipeline, playback, clipboard)
  playback.py           # Streaming speech sessions (Edge MP3 decode / Kokoro PCM -> gapless playback)
  kokoro_engine.py      # Offline Kokoro voices: lazy load, idle unload, cancellable synthesis
  clipboard.py          # Win32 event-driven clipboard listener
  tray.py               # System tray icon and menu wiring
  gui/
    __init__.py
    widgets.py           # FluentSlider, FluentToggle, custom Fluent controls
    control_center.py    # Settings and pronunciation dictionary GUI
clean_text.py           # Markdown-to-speech text sanitization
config.json             # User settings (persisted between sessions)
```

## Changelog

### v2.7.0

- **Offline Voices (Kokoro)** — 13 natural-sounding voices that run entirely on your PC, free and without internet: Heart, Bella, Nicole, Sarah, Aoede, Kore, Michael, Fenrir, Puck (American) and Emma, Isabella, George, Fable (British). The online Microsoft voices are unchanged.
- **Fast Offline Start** — Text is fed to Kokoro in pieces that start tiny and grow, so audio begins in ~0.5–0.85s on a 4-core laptop CPU and playback never waits for the next piece, even at 2x speed.
- **Lightweight** — Kokoro loads only when an offline voice is used (warmed in the background when you pick one) and unloads after 5 idle minutes, returning Audify to ~70MB. Inference is limited to the physical CPU cores.
- **Instant Stop/Switch** — Stopping or copying new text aborts the in-progress offline synthesis immediately instead of waiting for it to finish.
- **New Tray Icon** — A bold "A" monogram (matching the logo) with a sound-wave crossbar; blue while speaking, grey with pause bars when paused.
- **Voice Menu** — Voice now has "Online voices (Microsoft)" and "Offline voices (Kokoro)" submenus, each grouped into Female / Male. Speed shows "(2x offline)" when a faster speed is capped for offline voices.
- **Faster Startup (installer)** — The installer now installs a folder build that doesn't unpack itself on every launch. The portable `Audify.exe` still works as a single file.
- **Build Self-Test** — `Audify.exe --selftest report.txt` verifies a build can decode online audio and synthesize offline voices.

### v2.6.1

- **Faster, Steadier Start** — Speech now plays while it is still downloading (streamed MP3 decoding via `miniaudio`), and the first chunk is one short sentence. First audio arrives in ~0.85–1.0s, and slow outliers of 3–10s are gone.
- **No Temp Files** — Audio is decoded and played entirely in memory.
- **Stop Really Stops** — `Ctrl+Alt+S` now stops the whole text; previously it only cut the current chunk and reading continued with the next one.
- **Auto-Reading Fix** — After using "Read Clipboard Now" once, every later copy was read aloud as a number (a clipboard library overwrote a shared Win32 setting). Audify now uses only its own clipboard reader with private Win32 bindings.
- **Clipboard Retry** — Copies are no longer missed when another app briefly holds the clipboard.
- **Fail Fast Offline** — Errors are reported within ~1s instead of up to ~90s, and a new copy is never blocked by a stuck request.
- **Crash-Safe Settings** — `config.json` is written atomically with a backup; a damaged file is restored from the backup instead of being reset to defaults.
- **Deleted Rules Stay Deleted** — Built-in pronunciation rules you remove no longer come back on restart.
- **Volume Fix** — Volume was applied twice (80% played at 64%); it is now applied once.

### v2.6.0

- **Redesigned Control Center** — Fixed-width settings sidebar with Playback / Reading sections, flat dark dropdowns with a Windows 11 chevron, a stop-hotkey hint, and a borderless striped rule table with a live rule count, search placeholder and empty states.
- **Better Rule Editor** — Labelled inputs with example placeholders, an "Add Rule" / "Update Rule" button that reflects whether the word exists, a Clear button, multi-select removal, and a short confirmation after each change.
- **Keyboard Shortcuts** — `Enter` saves a rule, `Esc` clears the editor or search, `Delete` removes selected rules, `Ctrl+F` jumps to search, double-click edits a rule.
- **Layout Fixes** — The volume slider no longer inflates the sidebar to ~570px on scaled displays, which had been clipping the dictionary table and squashing the editor inputs. DPI awareness is now set before the window is created.
- **New Tray Icon** — A violet sound-wave tile matching the logo, with distinct Ready, Speaking and Paused states that stay legible at 16px.
- **Redesigned Tray Menu** — Live status in the header, current voice / speed / volume right-aligned, voices grouped into Female / Male, a Skip Code Blocks checkbox, the stop hotkey shown next to Stop Speaking, and items greyed out when they don't apply.
- **Live Tray Sync** — Changes made in the Control Center now appear in the tray menu immediately (previously the menu only refreshed after a tray click).
- **Single-Instance Control Center** — Opening it again brings the existing window to the front instead of starting a second one.
- **Bug Fix** — Rules for numeric words (e.g. `1`) could not be edited or removed.

### v2.5.0

- **Event-Driven Clipboard** — Replaced 300ms polling loop with Win32 `AddClipboardFormatListener` for instant (<1ms) clipboard detection and zero CPU idle cost.
- **Zero-Gap Audio** — Switched from `pygame.mixer.music` (load/play per chunk) to `pygame.mixer.Sound` with `Channel.queue()` for truly gapless transitions between chunks.
- **Persistent Asyncio Loop** — Reused a single asyncio event loop running on a background thread instead of creating/destroying one per chunk. Eliminates ~10ms overhead per chunk.
- **Single-Pass Pronunciation** — Combined 150+ individual regex calls into one compiled alternation pattern. Text cleaning is now O(n) instead of O(n*d).
- **Module Split** — Broke the 1681-line monolith into 9 focused modules across `audify/` package for maintainability.
- **Thread-Safe Config** — Added `threading.Lock` around all config mutations to prevent race conditions across 4+ threads.
- **Debounced Config Saves** — Volume slider drag no longer writes `config.json` 60 times/second. Saves are debounced to 500ms.
- **Error Notifications** — Windows toast notifications via `pystray` when TTS generation fails after 3 retries.
- **Reading Progress** — Tray tooltip dynamically shows "Reading (3/7)" during multi-chunk playback.
- **HTTP Pronunciation Fix** — Fixed typo where "HTTP" was pronounced as "H T M L" instead of "H T T P" in the default dictionary.

### v2.2.4

- **Seamless Audio Playback** — Redesigned the splitting algorithm to use natural sentence-aligned boundaries (`.!?`) and paragraph-preserving joins, expanding chunk size to ~800 characters. This completely eliminates abrupt pauses in the middle of sentences (commas/semicolons) and makes file transitions feel 100% seamless and natural.

### v2.2.3

- **Dynamic Resizable Layout** — Modernized the Audify Control Center to be fully resizable with a minimum constraint (`850x520`) to prevent visual clipping.
- **Flexbox-style Input Scaling** — Refactored pronunciation editor gridding layout to dynamically stretch and fill space horizontally.
- **Native Immersive Dark Title Bar** — Applied Win32 DWM API attributes to force a dark title bar header matching the dark mode theme.
- **Pausing/Resuming Fixes** — Corrected TTS playback state management when toggling pause from the tray and dictionary interface.
- **Process Lifecycle Guard** — Guaranteed complete cleanup of daemon threads and speech player objects on exit, leaving no rogue processes behind.

### v2.0.0

- **Single-instance guard** — Prevents multiple copies from running simultaneously
- **Persistent event loop** — Faster TTS generation (no asyncio.run() overhead)
- **Auto-retry with backoff** — Retries TTS up to 3 times on network failures (30s timeout)
- **Temp file cleanup** — Automatically removes temporary .mp3 files after playback
- **Robust error handling** — Clipboard monitoring, text cleaning, and playback wrapped in error recovery
- **Type hints** — Full type annotations throughout the codebase
- **Config path fix** — Config/history files resolve relative to the script/exe location
- **Pinned dependencies** — requirements.txt uses exact versions for reproducible builds
- **Code cleanup** — Consistent docstrings, removed bare excepts, fixed Unicode in tray menu

### v1.0.0

- Initial release with Edge TTS integration, system tray, pronunciation dictionary, 13 voices, and 6 speed presets
