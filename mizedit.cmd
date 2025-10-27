@echo off

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
    "%VENV%\Scripts\python.exe" -m pip install --upgrade pip==25.2
    "%VENV%\Scripts\pip" install -r requirements.txt
)
"%VENV%\Scripts\python" mizedit.py %*
