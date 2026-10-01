@echo off
setlocal

:: Change directory to where this batch file is located
cd /d "%~dp0"

echo [INFO] Installing PyInstaller...
venv\Scripts\pip.exe install -q pyinstaller

set PYI_OPTS=--noconfirm --windowed --icon "logo.ico" --version-file "version_info.txt" --name "Audify" ^
 --hidden-import _cffi_backend --collect-all miniaudio --collect-all kokoro_onnx --collect-all espeakng_loader --collect-all phonemizer ^
 --exclude-module onnxruntime.transformers --exclude-module onnxruntime.tools --exclude-module onnxruntime.quantization

:: Portable build: one self-contained exe (unpacks to a temp folder on each launch)
echo [INFO] Compiling portable Audify.exe v2.8.1...
venv\Scripts\python.exe -m PyInstaller --onefile %PYI_OPTS% --workpath build\onefile --distpath dist "audify\__main__.py" || exit /b 1
move /y "dist\Audify.exe" "Audify.exe"

:: Installer build: a plain folder, which starts faster because nothing is unpacked
echo [INFO] Compiling folder build for the installer...
if exist App rmdir /s /q App
venv\Scripts\python.exe -m PyInstaller --onedir %PYI_OPTS% --workpath build\onedir --distpath App "audify\__main__.py" || exit /b 1

echo [INFO] Cleaning up...
rmdir /s /q build
if exist dist rmdir /s /q dist
del /q Audify.spec
if exist __pycache__ rmdir /s /q __pycache__

echo [INFO] Done! Audify.exe (portable) and App\Audify\ (for the installer) are ready (v2.8.1).
