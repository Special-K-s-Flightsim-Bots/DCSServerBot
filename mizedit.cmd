@echo off
python --version > NUL 2>&1
if %ERRORLEVEL% EQU 9009 (
    echo python.exe is not in your PATH.
    echo Chose "Add python to the environment" in your Python-installer.
    echo Please press any key to continue...
    pause > NUL
    exit /B 9009
)
SET VENV=%USERPROFILE%\.dcssb
if not exist "%VENV%" (
    echo Creating the Python Virtual Environment
    python -m venv "%VENV%"
    "%VENV%\Scripts\python.exe" -m pip install --upgrade pip
    "%VENV%\Scripts\pip" install pip-tools
    "%VENV%\Scripts\pip-sync" requirements.txt
)
"%VENV%\Scripts\python" mizedit.py %*
