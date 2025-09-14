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
SET VENV=%USERPROFILE%\.dcssb
if not exist "%VENV%" (
    echo Creating the Python Virtual Environment. This may take some time...
    python -m pip install --upgrade pip
    python -m venv "%VENV%"
    "%VENV%\Scripts\python.exe" -m pip install --upgrade pip
    "%VENV%\Scripts\python.exe" -m pip install --no-cache-dir --prefer-binary -r requirements.txt
)
"%VENV%\Scripts\python" install.py %*
echo Please press any key to continue...
pause > NUL
