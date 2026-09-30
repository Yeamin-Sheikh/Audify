# Audify v2.5.0

A sleek, standalone Windows background AI Voice Daemon that reads your clipboard aloud using ultra-realistic Microsoft Neural voices. Built to run invisibly in the System Tray.

## Features

- **Smart Auto-Reader** — Monitors your clipboard. Copy text with `Ctrl+C` or `Ctrl+X` and it reads it aloud instantly.
- **Wispr Flow Proof** — Ignores programmatic clipboard changes so it won't read your own dictations back to you.
- **Single Instance Guard** — Only one Audify can run at a time. Launching a second shows a friendly notification.
- **Dynamic System Tray** — Left-click the tray icon to toggle pause/resume. The icon changes between green (active) and red (paused).
- **Pronunciation Dictionary** — Map problem words to phonetic spellings (e.g., `GUI` -> `gooey`) via a built-in GUI editor.
- **13 Neural Voices & 6 Speeds** — Switch voices and playback speed instantly from the tray context menu.
- **Global Kill-Switch** — Press `Ctrl + Alt + S` anywhere to stop playback immediately.
- **Smart Code Handling** — Replaces code blocks with "[Skipped code block]" instead of reading raw syntax.
- **Zero-Gap Playback** — Seamless gapless chunk transitions using channel-queued audio (no silence between chunks).
- **Reading Progress** — Tray tooltip shows "Reading (3/7)" during long reads so you know what's happening.
- **Error Notifications** — Windows toast notifications when TTS fails (e.g., no internet).
- **Auto-Retry** — Retries TTS generation up to 3 times on network failures with exponential backoff.
- **Temp Cleanup** — Automatically removes temporary audio files after playback.
- **Session History** — Logs everything read during the session; history is securely deleted on exit.

## Installation

### Installer (Recommended)

1. Download **`Setup_Audify_2.5.0.exe`** from the [Releases](../../releases) page.
2. Run the installer — it creates Start Menu and optional Desktop/Startup shortcuts.
3. Launch Audify. Look for the green circle icon in your System Tray.

### Portable

1. Download **`Audify.exe`** from the [Releases](../../releases) page.
2. Place it anywhere and run. No installation needed.

## Usage

| Action | How |
|---|---|
| Read text | Copy it with `Ctrl+C` — Audify reads it automatically |
| Pause / Resume | Left-click the tray icon |
| Stop playback | `Ctrl + Alt + S` (global hotkey) |
| Change voice | Right-click tray -> Voice |
| Change speed | Right-click tray -> Speed |
| Edit pronunciations | Right-click tray -> Audify Control Center |
| Force-read clipboard | Right-click tray -> Read Current Clipboard |
| Copy last spoken text | Right-click tray -> Copy Last Spoken |
| Exit | Right-click tray -> Exit |

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

### Run from Source

```bash
python -m audify
```

### Build Executable

```bash
build_exe.bat
```

This produces `Audify.exe` in the project root.

### Build Installer

Requires [Inno Setup](https://jrsoftware.org/isinfo.php):

```bash
& "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer.iss
```

This produces `Output\Setup_Audify_2.5.0.exe`.

### Project Structure

```
audify/
  __init__.py           # Package version
  __main__.py           # Entry point (single-instance guard)
  config.py             # Config constants, load/save, pronunciation dictionary
  engine.py             # TTSDaemon class (TTS pipeline, playback, clipboard)
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
