@echo off
SET VENV=%USERPROFILE%\.dcssb
echo Cleaning up the virtual environment ...
rmdir /s /q %VENV% >NUL 2>&1
echo Virtual environment cleaned.
pause > NUL
