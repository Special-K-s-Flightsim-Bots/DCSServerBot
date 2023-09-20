@echo off
if not exist venv (
    python -m venv venv
    venv\Scripts\python.exe -m pip install --upgrade pip
    venv\Scripts\pip install -r requirements.txt
)
:loop
venv\Scripts\python run.py
if %ERRORLEVEL% EQ -2 (
    exit /B %ERRORLEVEL%
) else (
    goto loop
)