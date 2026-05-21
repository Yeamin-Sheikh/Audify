@echo off
setlocal

:: Change directory to where this batch file is located
cd /d "%~dp0"

echo [INFO] Installing PyInstaller...
venv\Scripts\pip.exe install -q pyinstaller

echo [INFO] Compiling Audify v2.0.0...
venv\Scripts\pyinstaller.exe --noconfirm --onefile --windowed --icon "logo.ico" --version-file "version_info.txt" --name "Audify" "audify.py"

echo [INFO] Moving executable to root and cleaning up...
move /y "dist\Audify.exe" "Audify.exe"
rmdir /s /q build
rmdir /s /q dist
del /q Audify.spec
if exist __pycache__ rmdir /s /q __pycache__

echo [INFO] Done! Audify.exe (v2.0.0) is ready in this folder.
