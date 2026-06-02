@echo off
REM Launch the Adrenalift web server (browser front-end).
REM Requires: pip install -r requirements.txt  (installs Flask)
REM Hardware actions require Administrator privileges.

cd /d "%~dp0"

REM Prefer python, fall back to py launcher (common on Windows)
where python >nul 2>nul && set PY=python || set PY=py

echo Checking dependencies...
%PY% -c "import flask" 2>nul || (
    echo Installing requirements...
    %PY% -m pip install -r requirements.txt
)

echo.
echo Starting Adrenalift web server on http://127.0.0.1:8770
echo Open that address in your browser. Press Ctrl+C to stop.
echo.
%PY% -m src.web %*
