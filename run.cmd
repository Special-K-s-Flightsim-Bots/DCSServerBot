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

SETLOCAL ENABLEEXTENSIONS
SET "ARGS=%*"
SET "node_name=%computername%"
SET "restarted=false"

:loop1
if "%~1"=="-n" (
   SET node_name=%~2
)
SHIFT
if NOT "%~1"=="" goto loop1

DEL dcssb_%node_name%.pid 2>NUL

SET VENV=%USERPROFILE%\.dcssb
if not exist "%VENV%" (
    echo Creating the Python Virtual Environment ...
    python -m venv "%VENV%"
    "%VENV%\Scripts\python.exe" -m pip install --upgrade pip==25.2
    "%VENV%\Scripts\pip" install -r requirements.txt
)

SET PROGRAM=run.py
:loop
"%VENV%\Scripts\python" %PROGRAM% %ARGS%
if %ERRORLEVEL% EQU -1 (
    IF NOT %restarted% == true (
        SET restarted=true
        SET ARGS=%* --restarted
    )
    SET PROGRAM=run.py
    goto loop
) else if %ERRORLEVEL% EQU -3 (
    SET PROGRAM=update.py
    goto loop
) else if %ERRORLEVEL% EQU -2 (
    echo Please press any key to continue...
    pause > NUL
) else (
    echo Unexpected return code: %ERRORLEVEL%
    echo Please check the logs and press any key to continue...
    pause > NUL
)
