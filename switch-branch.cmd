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

:: If current branch is development, switch to master
if "%branch%" == "master" (
	echo Switching to development branch
	git checkout development
	call update.cmd
) else if "%branch%" == "development" (
	echo Switching to master branch
	git checkout master
	call update.cmd
) else (
	echo Unknown branch: %branch%
)
