# Audify

A sleek, standalone Windows background AI Voice Daemon that intelligently reads your clipboard aloud using ultra-realistic Microsoft Neural voices. Built to be completely invisible and instantly accessible via the System Tray.

## Core Features
- **Smart Auto-Reader**: Monitors your clipboard. If you manually copy text (`Ctrl+C` or `Ctrl+X`), it immediately reads it aloud at your preferred speed.
- **Wispr Flow Proof**: Automatically ignores programmatic clipboard pastes (like Wispr Flow dictation) so it won't read your own dictations back to you.
- **Dynamic System Tray**: Runs entirely hidden with a System Tray icon. The icon changes color/shape to indicate if it is currently Active or Paused.
- **Pronunciation Dictionary GUI**: Right-click the tray icon to open a sleek GUI where you can map problematic words to phonetic spellings (e.g., `GUI` -> `gooey`). Rules save instantly and persist forever.
- **Premium Voices & Speed**: Choose from 13 natural/expressive neural voices and speeds up to 2.0x, all changeable instantly from the tray menu.
- **Global Kill-Switch**: Press `Ctrl + Alt + S` anytime, anywhere, to instantly stop playback.
- **Smart Code Parsing**: Skips over raw code syntax during reading so you aren't forced to listen to punctuation.
- **Session History**: Logs everything read during the current session to `history.log`, which is securely deleted the moment you exit the app.

## Installation & Usage

1. Navigate to the `Output` directory and run **`Setup_Audify_1.0.0.exe`**.
2. Follow the installer steps to install Audify system-wide (it will create a Start Menu and optional Desktop shortcut).
3. Run the application. Look for the green 'Pause' icon in your System Tray!

## Developer Compilation
If you wish to modify the source code and rebuild the executable:
1. Ensure the Python `venv` is active and dependencies from `requirements.txt` are installed.
2. Run `build_exe.bat` to compile `Audify_App.exe`.
3. Open `installer.iss` with Inno Setup Compiler (`iscc`) to rebuild the final `.exe` installer.
