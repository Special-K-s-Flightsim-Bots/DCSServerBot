@echo off
python --version > NUL 2>&1
if %ERRORLEVEL% EQU 9009 (
    echo python.exe is not in your PATH.
    echo Chose "Add python to the environment" in your Python-installer.
    exit /B %ERRORLEVEL%
)
SET VENV=%USERPROFILE%\.dcssb
if not exist "%VENV%" (
    echo Creating the Python Virtual Environment
    python -m pip install --upgrade pip
    python -m venv "%VENV%"
    "%VENV%\Scripts\python.exe" -m pip install --upgrade pip
    "%VENV%\Scripts\python.exe" -m pip install --no-cache-dir --prefer-binary -r requirements.txt
) else (
    "%VENV%\Scripts\python.exe" -m pip install --upgrade pip
)
"%VENV%\Scripts\python" update.py --no-restart %*
echo Please press any key to continue...
pause > NUL
