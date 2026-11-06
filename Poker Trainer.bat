@echo off
rem Double-click this file to open the practice trainer (needs Python 3.12 or newer).
cd /d "%~dp0"
for %%v in (3.14 3.13 3.12) do (
  py -%%v -c "" >nul 2>&1 && (py -%%v trainer8.py & goto :done)
)
echo This trainer needs Python 3.12 or newer. Get it free from https://www.python.org/downloads/
echo (tick "Add python.exe to PATH" when installing), then double-click this file again.
pause
exit /b
:done
if errorlevel 1 pause
