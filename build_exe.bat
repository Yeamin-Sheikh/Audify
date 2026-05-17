@echo off
setlocal

:: Change directory to where this batch file is located
cd /d "%~dp0"

echo [INFO] Installing PyInstaller...
venv\Scripts\pip.exe install -q pyinstaller

echo [INFO] Compiling Audify...
venv\Scripts\pyinstaller.exe --noconfirm --onefile --windowed --icon "logo.ico" --version-file "version_info.txt" --name "Audify" "audify.py"

echo [INFO] Moving executable to root and cleaning up...
move /y "dist\Audify.exe" "Audify.exe"
rmdir /s /q build
rmdir /s /q dist
del /q Audify.spec

echo [INFO] Done! The compiled app is now Audify.exe in this folder.
