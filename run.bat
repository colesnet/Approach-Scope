@echo off
rem Windows: double-click for a windowed demo.  "run.bat --setup" to set up, then "run.bat --window".
cd /d "%~dp0"
if "%~1"=="" (
    py main.py --window --demo
) else (
    py main.py %*
)
if errorlevel 1 pause
