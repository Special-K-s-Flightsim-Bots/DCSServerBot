@echo off
SET VENV=%TEMP%\DCSServerBot
if not exist %VENV% (
    echo Creating the Python Virtual Environment
    python -m venv %VENV%
    %VENV%\Scripts\python.exe -m pip install --upgrade pip
    %VENV%\Scripts\pip install -r requirements.txt
)
:loop
%VENV%\Scripts\python run.py
if %ERRORLEVEL% EQU -1 (
    goto loop
)
