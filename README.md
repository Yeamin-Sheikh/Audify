<div align="center">

<img src="docs/images/logo.png" alt="Audify logo" width="128" height="128">

# Audify

**Copy text. Hear it instantly.**

A lightweight Windows tray app that reads your clipboard aloud with natural neural voices,
online through Microsoft Edge or fully offline on your own PC.

[![Latest release](https://img.shields.io/github/v/release/Yeamin-Sheikh/Audify?label=release&color=7c3aed)](https://github.com/Yeamin-Sheikh/Audify/releases/latest)
[![Downloads](https://img.shields.io/github/downloads/Yeamin-Sheikh/Audify/total?color=6366f1)](https://github.com/Yeamin-Sheikh/Audify/releases)
![Windows 10 | 11](https://img.shields.io/badge/Windows-10%20%7C%2011-0078D4?logo=windows&logoColor=white)
![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)

[**Download**](https://github.com/Yeamin-Sheikh/Audify/releases/latest) ·
[Features](#features) ·
[Voices](#voices) ·
[Usage](#usage) ·
[Build from source](#build-from-source) ·
[Changelog](#changelog)

</div>

---

## Why Audify?

Reading long answers, docs and emails gets tiring. With Audify you **just press `Ctrl+C`** and it starts
talking in about a second, without opening an app or clicking a button. It sits quietly in the system
tray, understands Markdown and code, and knows how to say developer jargon properly
(`__init__.py`, `GPT-4o`, `C#`, `k8s`, `TL;DR`...).

## Features

<table>
<tr>
<td width="50%" valign="top">

### 🎧 Hands-free reading
- **Copy to listen**: `Ctrl+C` / `Ctrl+X` text and it's read aloud right away.
- **Streams as it speaks**: audio starts in **~1 second**, before the whole text is generated.
- **Gapless playback**: long texts flow without pauses between sentences.
- **Wispr Flow proof**: ignores clipboard changes you didn't make yourself.

</td>
<td width="50%" valign="top">

### 🗣️ Voices that sound human
- **26 voices**: 13 online Microsoft Neural voices plus 13 offline Kokoro voices.
- **Works offline**: Kokoro runs on your PC with no internet and no account.
- **8 speeds, changeable mid-sentence**: Audify picks up again from the sentence you're on.
- **Busy-PC safety net**: if offline speech can't keep up, Audify hands over to an online voice instead of stuttering.

</td>
</tr>
<tr>
<td width="50%" valign="top">

### 🧠 Speaks developer
- **~580 built-in pronunciations** for code, AI, cloud and Windows terms.
- **Your own dictionary**: add, search and edit rules in the Control Center.
- **Code aware**: skips fenced code blocks (optional) and reads inline code properly.
- **Smart matching**: `IT` the acronym never touches the word "it".

</td>
<td width="50%" valign="top">

### 🪶 Light and dependable
- **~70 MB** of memory with online voices; the offline model unloads after a minute idle.
- **Global stop hotkey** (`Ctrl+Alt+S`) stops everything instantly.
- **Fails fast offline** and never misses a copy while another app holds the clipboard.
- **Crash-safe settings** with automatic backup.

</td>
</tr>
</table>

## Voices

| | Online (Microsoft Edge) | Offline (Kokoro) |
|---|---|---|
| **Voices** | Jenny, Aria, Michelle, Ava, Jane, Ana, Christopher, Guy, Steffan, Brian, Andrew, Eric, Roger | Heart, Bella, Nicole, Sarah, Aoede, Kore, Emma 🇬🇧, Isabella 🇬🇧, Michael, Fenrir, Puck, George 🇬🇧, Fable 🇬🇧 |
| **Needs internet** | Yes | No |
| **Runs on** | Microsoft's servers | Your CPU (~25-35% while speaking) |
| **Speeds** | 0.5x to 3.0x | 0.5x to 1.5x (faster speeds use the matching online voice) |
| **Best for** | Fastest start, very high speeds | Privacy, offline use, natural tone |

> [!TIP]
> Offline voices stay clear up to 1.5x thanks to a hybrid speed-up: Kokoro speaks at up to 1.3x and a
> pitch-preserving time-stretch handles the rest. In a speech-recognition test this cut misheard words
> at 1.5x from 6.4% to 2.0% (Kokoro's own 2x speed: 14.9%).

## Installation

### Installer (recommended)

1. Download **`Setup_Audify_2.8.0.exe`** from the [latest release](https://github.com/Yeamin-Sheikh/Audify/releases/latest).
2. Run it. You can optionally add Desktop and "start with Windows" shortcuts.
3. Look for the Audify icon in your system tray.

The installer includes the offline voice model, so every voice works right away.

### Portable

Download **`Audify.exe`** and run it from anywhere. The first time you choose an offline voice, Audify
downloads the voice model (~200 MB) once.

> [!NOTE]
> Windows SmartScreen may warn about an unrecognised app on first launch. Choose **More info → Run anyway**.

## Usage

<table align="center">
  <tr>
    <td align="center" width="140"><img src="docs/images/tray-ready.png" width="56" alt="Ready icon"><br><b>Ready</b></td>
    <td align="center" width="140"><img src="docs/images/tray-speaking.png" width="56" alt="Speaking icon"><br><b>Speaking</b></td>
    <td align="center" width="140"><img src="docs/images/tray-paused.png" width="56" alt="Paused icon"><br><b>Paused</b></td>
  </tr>
</table>

| To... | Do this |
|---|---|
| Read text aloud | Copy it with `Ctrl+C` |
| Pause / resume | Left-click the tray icon |
| Stop reading | `Ctrl+Alt+S` from anywhere |
| Change voice | Tray → **Voice** → Online voices / Offline voices |
| Change speed (works mid-sentence) | Tray → **Speed** |
| Change volume | Tray → **Volume** |
| Skip code blocks | Tray → **Skip Code Blocks** |
| Edit pronunciations, voice, speed | Tray → **Control Center...** |
| Read the clipboard again | Tray → **Read Clipboard Now** / **Replay Last** |
| Quit | Tray → **Quit Audify** |

### Control Center shortcuts

| Key | Action |
|---|---|
| `Enter` | Save the rule being edited |
| `Esc` | Clear the editor or search |
| `Delete` | Remove the selected rules |
| `Ctrl+F` | Search rules |
| Double-click | Edit a rule |

## Build from source

<details>
<summary><b>Developer guide</b></summary>

### Prerequisites

- Windows 10/11, Python 3.11+
- [Inno Setup 6](https://jrsoftware.org/isinfo.php) (only for the installer)

### Setup

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

Offline voices need the Kokoro model files in `models\` (from source they also download on first use):

```bash
gh release download model-files-v1.0 --repo thewh1teagle/kokoro-onnx -D models -p "kokoro-v1.0.fp16.onnx" -p "voices-v1.0.bin"
```

### Run

```bash
python -m audify
```

### Build

```bash
build_exe.bat
"C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer.iss
```

`build_exe.bat` produces the portable `Audify.exe` and a folder build in `App\Audify\` that the
installer uses (it starts faster because nothing is unpacked at launch). The installer is written to
`Output\Setup_Audify_2.8.0.exe`.

Check that a build really works, including offline voices:

```bash
Audify.exe --selftest report.txt
```

### Project structure

```
audify/
├── __main__.py              Entry point, single-instance guard, --selftest
├── engine.py                Reader: clipboard queue, playback, speed/voice changes, fallback
├── playback.py              Streaming sessions: Edge MP3 decoding and Kokoro PCM, gapless
├── kokoro_engine.py         Offline voices: lazy load, idle unload, hybrid speed, cancelling
├── clipboard.py             Event-driven Win32 clipboard listener
├── config.py                Settings, voices, speeds, crash-safe saving
├── pronunciation_library.py Built-in developer pronunciation library
├── tray.py                  Tray icon and menu
└── gui/
    ├── control_center.py    Settings and pronunciation dictionary window
    └── widgets.py           Fluent-style slider, toggle, entry and buttons
clean_text.py                Markdown to speech text, pronunciation matching
```

</details>

## Changelog

### v2.8.0

- **Clearer offline voices**: a hybrid speed-up (Kokoro up to 1.3x plus a pitch-preserving time-stretch) keeps offline speech clear up to 1.5x. Faster speeds automatically use the matching online voice (American or British, female or male).
- **Busy-PC safety net**: if offline speech starts falling behind because the PC is busy, the rest of the text continues with the matching online voice instead of stuttering. If the internet is also down, it stays offline.
- **Change speed or voice mid-sentence**: the new setting is heard within about a second, resuming from the sentence you were on.
- **~430 new pronunciations** (577 built in) covering Python, Windows, git/GitHub, AI/LLM, web, cloud, file extensions, units and everyday chat shorthand.
- **Smarter dictionary matching**: ALL-CAPS entries only match capitals ("IT" no longer changes "it"), all entries respect word boundaries (".ini" no longer hits ".initialize"), and longer entries win ("!==" before "!=").
- **Code reads properly**: inline code now uses the dictionary too, and Python names like `__init__.py` are no longer mangled by Markdown.
- **Lighter offline CPU use**: offline voices use 3 threads (about a third of a 4-core CPU while speaking), and the model unloads after 1 idle minute instead of 5.
- **New README** with badges, a voice comparison and a feature overview.

<details>
<summary><b>Older releases</b></summary>

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

</details>
