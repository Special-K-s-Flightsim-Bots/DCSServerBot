@echo off
echo ********************************************
echo This cmd is deprecated, use run.cmd instead!
echo ********************************************
if not exist venv (
    python -m venv venv
    venv\Scripts\python.exe -m pip install --upgrade pip
    venv\Scripts\pip install -r requirements.txt
)
:loop
venv\Scripts\python run.py
goto loop
