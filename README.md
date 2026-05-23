# Audify v2.2.4

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
- **Auto-Retry** — Retries TTS generation up to 3 times on network failures with exponential backoff.
- **Temp Cleanup** — Automatically removes temporary audio files after playback.
- **Session History** — Logs everything read during the session; history is securely deleted on exit.

## Installation

### Installer (Recommended)

1. Download **`Setup_Audify_2.2.4.exe`** from the [Releases](../../releases) page.
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
| Edit pronunciations | Right-click tray -> Pronunciation Dictionary |
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
python audify.py
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

This produces `Output\Setup_Audify_2.2.4.exe`.

## Changelog

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
