@echo off

REM Resolve the node name first: the environment is per node, so every launcher must agree on it.
SET "ARGS=%*"
SET "node_name=%computername%"

:parse_args
if "%~1"=="-n" SET "node_name=%~2"
SHIFT
if NOT "%~1"=="" goto parse_args

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
REM The environment exists now, so run with its own interpreter.
SET "PYTHON=%VENV%\Scripts\python.exe"
"%PYTHON%" mizedit.py %ARGS%
