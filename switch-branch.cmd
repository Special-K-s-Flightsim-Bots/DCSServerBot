@echo off
setlocal EnableDelayedExpansion
:: Get the directory of the script file
set script_dir=%~dp0
set script_dir=%script_dir:~0,-1%

cd /d "%script_dir%"

:: Get current branch from .git/HEAD
for /f "tokens=2 delims= " %%a in ('type .git\HEAD') do set branch=%%a
:: Trim the branch name
set branch=%branch:refs/heads/=%

@REM echo Current branch: %branch%

:: Switch to the other branch
if "%branch%" == "master" (
    choice /c yn /n /m "Switch to development branch? [Y/N] "
    if ERRORLEVEL 2 (
        echo Operation aborted.
        goto :eof
    )
    git checkout development
    call update.cmd
) else if "%branch%" == "development" (
    choice /c yn /n /m "Switch to master branch? [Y/N] "
    if ERRORLEVEL 2 (
        echo Operation aborted.
        goto :eof
    )
    git checkout master
    call update.cmd
) else (
    echo Unknown branch: %branch%
)
