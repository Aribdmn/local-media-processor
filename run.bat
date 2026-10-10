@echo off
REM Local Media Processor - single-click launcher (Windows)
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" main.py
) else (
    uv run python main.py
)
pause
