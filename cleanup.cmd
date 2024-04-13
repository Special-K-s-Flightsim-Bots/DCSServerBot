@echo off
SET VENV=%USERPROFILE%\.dcssb
echo Cleaning up the virtual environment ...
rmdir /s /q %VENV% >NUL 2>&1
IF EXIST %VENV% (
    echo **************************************************
    echo WARNING: Could not delete the virtual environment.
    echo Please manually delete the .dcssb directory.
    echo Directory Path: %VENV%
    echo **************************************************
) ELSE (
    echo Virtual environment cleaned.
    echo A new environment will be created on the next DCSServerBot launch.
)
echo.
echo Please press any key to continue...
pause > NUL
