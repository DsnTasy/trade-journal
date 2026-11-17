@echo off
REM Dropship Agent - double-click to open the dashboard. Keep this window open while you use it.
cd /d "%~dp0"
if not exist ".venv\Scripts\activate.bat" goto nosetup
call ".venv\Scripts\activate.bat"
python run.py dashboard --open
pause
exit /b 0

:nosetup
echo Please double-click setup.bat first.
pause
exit /b 1
