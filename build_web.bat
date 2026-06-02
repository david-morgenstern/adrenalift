@echo off
REM Build the Adrenalift WEB CONSOLE exe with PyInstaller (onefile).
REM Produces dist\Adrenalift_Web_<ver>_<build>.exe -- run it as Administrator.
REM Requires: pip install -r requirements.txt
REM Driver files (inpoutx64.dll, WinRing0x64.dll, WinRing0x64.sys) must be in
REM drivers\ before building.

cd /d "%~dp0"

REM Prefer python, fall back to py launcher (common on Windows)
where python >nul 2>nul && set PY=python || set PY=py

echo Checking dependencies...
%PY% -c "import flask, PyInstaller" 2>nul || (
    echo Installing requirements...
    %PY% -m pip install -r requirements.txt
)

echo.
echo Cleaning previous build cache...
if exist build rmdir /s /q build

echo Building web console with PyInstaller...
%PY% -m PyInstaller --noconfirm --clean build_web.spec

if %ERRORLEVEL% EQU 0 (
    echo.
    echo Build complete. Output is in dist\ as Adrenalift_Web_*.exe
    echo Run it as Administrator; it opens your browser at http://127.0.0.1:8770
    echo.
    echo Note: Add bios\vbios.rom for VBIOS, or the app falls back to defaults.
) else (
    echo Build failed.
    exit /b 1
)
