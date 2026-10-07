@echo off
echo.
echo  ___   ___ ___ ___                      ___      _
echo ^|   \ / __/ __/ __^| ___ _ ___ _____ _ _^| _ ) ___^| ^|_
echo ^| ^|) ^| (__\__ \__ \/ -_) '_\ V / -_) '_^| _ \/ _ \  _^|
echo ^|___/ \___^|___/___/\___^|_^|  \_/\___^|_^| ^|___/\___/\__^|
echo.

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

REM The environment is per node; every launcher resolves the same name.
SET VENV=%USERPROFILE%\.dcssb-%node_name%

REM Prefer the environment's own interpreter, so no Python has to sit on PATH. Point PYTHON at
REM another launcher (e.g. py) if that is where your Python lives.
SET "PYTHON=%VENV%\Scripts\python.exe"
if not exist "%PYTHON%" SET "PYTHON=python"

"%PYTHON%" --version >NUL 2>&1
if errorlevel 9009 (
    echo.
    echo ***  ERROR  ***
    echo No Python found - neither in this node's environment nor on your PATH.
    echo Please run the Python installer and check "Add python to the environment".
    exit /B 9009
)

"%PYTHON%" -c "import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)" >NUL 2>&1
if errorlevel 1 (
    echo.
    echo ***  ERROR  ***
    echo DCSServerBot requires Python >= 3.11.
    exit /B 1
)

REM Resolve uv for this session, installing it for this user if it is missing (see get_uv.cmd).
call "%~dp0get_uv.cmd"

REM Keep the package cache on the same drive as the environment, so uv can hardlink packages
REM into it instead of copying them. Set UV_CACHE_DIR yourself to place the cache elsewhere.
if not defined UV_CACHE_DIR SET "UV_CACHE_DIR=%USERPROFILE%\.uv-cache"

if exist "%VENV%" goto venv_ready

if not defined UVEXE (
    echo.
    echo ***  ERROR  ***
    echo uv is required to create this node's environment and is not available.
    echo Install it from https://docs.astral.sh/uv/ and try again.
    exit /B 1
)

REM requirements.local is an optional extra requirements file (see plugins/README.md).
SET "REQ=requirements.txt"
if exist requirements.local SET "REQ=requirements.txt requirements.local"

echo Creating the Python Virtual Environment ...
"%UVEXE%" venv "%VENV%"
"%UVEXE%" pip sync --python "%VENV%\Scripts\python.exe" %REQ%

:venv_ready
REM The environment exists now, so run the bot with its own interpreter.
SET "PYTHON=%VENV%\Scripts\python.exe"
SET PROGRAM=run.py
:loop
"%PYTHON%" %PROGRAM% %ARGS%
if %ERRORLEVEL% EQU -1 (
    IF NOT %restarted% == true (
        SET restarted=true
        SET ARGS=%* --restarted
    )
    SET PROGRAM=run.py
    goto loop
) else if %ERRORLEVEL% LSS -1000000000 (
    echo A Windows error occured: %ERRORLEVEL%
    IF NOT %restarted% == true (
        SET restarted=true
        SET ARGS=%* --restarted
    )
    SET PROGRAM=run.py
    goto loop
) else if %ERRORLEVEL% EQU -3 (
    SET PROGRAM=update.py
    goto loop
) else if %ERRORLEVEL% EQU -5 (
    REM The updater already started a fresh launcher (see update.py), so there is nothing to do.
    exit /B 0
) else if %ERRORLEVEL% EQU -2 (
    echo Please press any key to continue...
    pause > NUL
) else (
    echo Unexpected return code: %ERRORLEVEL%
    echo Please check the logs and press any key to continue...
    pause > NUL
)
