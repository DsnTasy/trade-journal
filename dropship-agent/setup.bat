@echo off
REM Dropship Agent - one-time setup. Double-click this file.
cd /d "%~dp0"
echo.
echo === Dropship Agent setup ===
echo.

python --version >nul 2>nul
if errorlevel 1 goto nopython

if not exist ".venv\Scripts\activate.bat" (
  echo Creating a private Python environment...
  python -m venv .venv
)
call ".venv\Scripts\activate.bat"

echo Installing packages - this can take a few minutes...
python -m pip install --upgrade pip >nul
pip install -r requirements.txt
if errorlevel 1 goto installfailed

if not exist ".env" copy ".env.example" ".env" >nul
if not exist "research\candidates.csv" copy "research\candidates.example.csv" "research\candidates.csv" >nul
if not exist "research\suppliers.csv" copy "research\suppliers.example.csv" "research\suppliers.csv" >nul

python run.py init
echo.
echo === Setup check ===
python run.py doctor
echo.
echo Setup finished. Double-click start.bat to open the dashboard.
pause
exit /b 0

:nopython
echo Python was not found.
echo Install it from https://www.python.org/downloads/
echo IMPORTANT: on the first installer screen, tick "Add python.exe to PATH".
echo Then double-click setup.bat again.
pause
exit /b 1

:installfailed
echo.
echo Installing packages failed - see the messages above.
echo Check your internet connection and try again.
pause
exit /b 1
