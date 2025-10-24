@echo off
echo.
echo  ___   ___ ___ ___                      ___      _
echo ^|   \ / __/ __/ __^| ___ _ ___ _____ _ _^| _ ) ___^| ^|_
echo ^| ^|) ^| (__\__ \__ \/ -_) '_\ V / -_) '_^| _ \/ _ \  _^|
echo ^|___/ \___^|___/___/\___^|_^|  \_/\___^|_^| ^|___/\___/\__^|
echo.

python --version >NUL 2>&1
if errorlevel 9009 (
    echo.
    echo ***  ERROR  ***
    echo python.exe was not found in your PATH.
    echo Please run the Python installer and check "Add python to the environment".
    exit /B 9009
)

python -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" >NUL 2>&1
if errorlevel 1 (
    echo.
    echo ***  ERROR  ***
    echo DCSServerBot requires Python >= 3.10 and < 3.14.
    exit /B 1
)

SET VENV=%USERPROFILE%\.dcssb
if not exist "%VENV%" (
    echo Creating the Python Virtual Environment ...
    python -m venv "%VENV%"
    "%VENV%\Scripts\python.exe" -m pip install --upgrade pip
    "%VENV%\Scripts\pip" install -r requirements.txt
)
"%VENV%\Scripts\python" install.py %*
echo Please press any key to continue...
pause > NUL
