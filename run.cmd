@echo off
echo.
echo  ___   ___ ___ ___                      ___      _
echo ^|   \ / __/ __/ __^| ___ _ ___ _____ _ _^| _ ) ___^| ^|_
echo ^| ^|) ^| (__\__ \__ \/ -_) '_\ V / -_) '_^| _ \/ _ \  _^|
echo ^|___/ \___^|___/___/\___^|_^|  \_/\___^|_^| ^|___/\___/\__^|
echo.
python --version > NUL 2>&1
if %ERRORLEVEL% EQU 9009 (
    echo python.exe is not in your PATH.
    echo Chose "Add python to the environment" in your Python-installer.
    echo Please press any key to continue...
    pause > NUL
    exit /B 9009
)

SET ARGS=%*
SET node_name=%computername%

:loop1
if "%~1"=="-n" (
   SET node_name=%~2
)
SHIFT
if NOT "%~1"=="" goto loop1

DEL dcssb_%node_name%.pid 2>NUL

SET VENV=%USERPROFILE%\.dcssb
if not exist "%VENV%" (
    echo Creating the Python Virtual Environment. This may take some time...
    python -m pip install --upgrade pip
    python -m venv "%VENV%"
    "%VENV%\Scripts\python.exe" -m pip install --upgrade pip
    "%VENV%\Scripts\python.exe" -m pip install --no-cache-dir --prefer-binary -r requirements.txt
)

SET PROGRAM=run.py
:loop
"%VENV%\Scripts\python" %PROGRAM% %ARGS%
if %ERRORLEVEL% EQU -1 (
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
