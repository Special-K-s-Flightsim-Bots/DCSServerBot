@echo off
echo Updating DCSSererBot to the latest version...
git checkout master 2>/NUL
git pull 2>/NUL
if %ERRORLEVEL% EQU 9009 (
    echo Git for Windows is not installed.
    echo Please download the latest version of DCSServerBot from
    echo https://github.com/Special-K-s-Flightsim-Bots/DCSServerBot/releases/latest
    echo and update manually.
    exit /B %ERRORLEVEL%
) else if %ERRORLEVEL% NEQ 0 (
    echo Error while updating DCSServerBot. Please check the messages above.
    exit /B %ERRORLEVEL%
)
if not exist venv (
    python -m venv venv
)
echo Installing Python Libraries ...
venv\Scripts\python.exe -m pip install --upgrade pip >NUL 2>NUL
venv\Scripts\pip -q install -r requirements.txt
if %ERRORLEVEL% NEQ 0 (
    echo Error while updating DCSServerBot. Please check the messages above.
    exit /B %ERRORLEVEL%
)
echo DCSServerBot updated.
