@echo off
REM Resolve the node name first: the environment is per node, so every launcher must agree on it.
SET "node_name=%computername%"

:parse_args
if "%~1"=="-n" SET "node_name=%~2"
SHIFT
if NOT "%~1"=="" goto parse_args

SET VENV=%USERPROFILE%\.dcssb-%node_name%
echo Cleaning up the virtual environment ...
rmdir /s /q %VENV% >NUL 2>&1
if exist %VENV% (
    echo **************************************************
    echo WARNING: Could not delete the virtual environment.
    echo Please manually delete the environment directory:
    echo Directory Path: %VENV%
    echo **************************************************
) else (
    echo Virtual environment cleaned.
    echo A new environment will be created on the next DCSServerBot launch.
)
where git >NUL 2>&1
if errorlevel 1 (
    rem Git executable not found, couldn't reset the repository
) else (
    echo Resetting the GIT repository ...
    git config --global --add safe.directory "%cd%" >NUL 2>&1
    git reset --hard >NUL 2>&1
    echo Repository reset.
)
echo.
echo Please press any key to continue...
pause > NUL
