@echo off
REM Dropship Agent - one-time setup. Double-click this file.
cd /d "%~dp0"
echo.
echo === Dropship Agent setup ===
echo.

python --version >nul 2>nul
if errorlevel 1 goto nopython

REM Windows limits file paths to 260 characters; deep folders break the install.
python -c "import os,sys; sys.exit(1 if len(os.getcwd()) > 70 else 0)"
if errorlevel 1 goto longpath

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

:longpath
echo This folder is too deep for Windows - some files would go over its 260-character path limit.
echo Current folder: %CD%
echo Move the dropship-agent folder somewhere short, for example C:\Users\%USERNAME%\dropship-agent
echo In File Explorer, type %%USERPROFILE%% in the address bar to get there.
echo If a .venv folder exists inside dropship-agent, delete it, then run setup.bat again.
pause
exit /b 1

:installfailed
echo.
echo Installing packages failed - see the messages above.
echo If it mentions "Long Path", move the dropship-agent folder somewhere short,
echo for example C:\Users\%USERNAME%\dropship-agent, delete its .venv folder, and run setup.bat again.
echo Otherwise check your internet connection and try again.
pause
exit /b 1
