@echo off
REM Resolve a uv executable for this session, installing it for the current user when it is absent.
REM Exports UVEXE, and puts the install directory on PATH so child processes (update.py) find uv too.
REM This never ends the caller: a launcher that only has to RUN an existing environment can carry on.

IF DEFINED UVEXE exit /B 0

where uv >NUL 2>&1
if not errorlevel 1 (
    SET "UVEXE=uv"
    exit /B 0
)

REM uv's standalone installer puts the binary here (see uv's own installation documentation).
if exist "%USERPROFILE%\.local\bin\uv.exe" (
    SET "UVEXE=%USERPROFILE%\.local\bin\uv.exe"
    SET "PATH=%USERPROFILE%\.local\bin;%PATH%"
    exit /B 0
)

echo uv was not found - installing it for this user ...
powershell -NoProfile -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

if exist "%USERPROFILE%\.local\bin\uv.exe" (
    SET "UVEXE=%USERPROFILE%\.local\bin\uv.exe"
    SET "PATH=%USERPROFILE%\.local\bin;%PATH%"
    exit /B 0
)

echo.
echo ***  WARNING  ***
echo uv could not be installed automatically.
echo Install it from https://docs.astral.sh/uv/ and try again.
exit /B 1
