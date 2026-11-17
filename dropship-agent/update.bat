@echo off
REM Dropship Agent - install a newly downloaded version into your existing folder.
REM Double-click this file INSIDE THE NEWLY DOWNLOADED dropship-agent folder.
REM Your own files are never touched: config.yaml, brand.yaml, .env, your research CSVs,
REM Google Trends files, media, output, and the database.
setlocal
set "SRC=%~dp0"
set "DEST=%USERPROFILE%\dropship-agent"
cd /d "%SRC%"
if /I "%CD%"=="%DEST%" goto same
if not exist "%DEST%\run.py" goto nodest

echo Updating %DEST% ...
set "OPTS=/NFL /NDL /NJH /NJS /NP"
robocopy "%SRC%app" "%DEST%\app" /E /XD __pycache__ %OPTS%
if errorlevel 8 goto failed
robocopy "%SRC%tests" "%DEST%\tests" /E /XD __pycache__ %OPTS%
if errorlevel 8 goto failed
robocopy "%SRC%policies" "%DEST%\policies" /E %OPTS%
if errorlevel 8 goto failed
robocopy "%SRC%research" "%DEST%\research" README.md candidates.example.csv suppliers.example.csv %OPTS%
if errorlevel 8 goto failed
robocopy "%SRC%." "%DEST%" run.py requirements.txt setup.bat start.bat update.bat README.md GO-LIVE-WINDOWS.md .env.example .gitattributes .gitignore %OPTS%
if errorlevel 8 goto failed

cd /d "%DEST%"
if not exist ".venv\Scripts\activate.bat" goto nosetup
call ".venv\Scripts\activate.bat"
echo Checking packages...
pip install -q -r requirements.txt
if errorlevel 1 goto failed
python run.py init
echo.
echo Update finished. Close any old dashboard window, then double-click start.bat in:
echo %DEST%
echo You can now delete the downloaded copy you ran this from.
pause
exit /b 0

:same
echo This is already your main dropship-agent folder.
echo Run update.bat from the NEWLY DOWNLOADED copy instead (in your Downloads folder).
pause
exit /b 1

:nodest
echo Couldn't find your existing installation at %DEST%
echo If this is your first install, move this dropship-agent folder to %USERPROFILE% and run setup.bat.
pause
exit /b 1

:nosetup
echo Files copied, but setup hasn't been run in %DEST% yet. Double-click setup.bat there.
pause
exit /b 1

:failed
echo.
echo The update didn't finish - see the messages above. Your settings and data were not changed.
pause
exit /b 1
