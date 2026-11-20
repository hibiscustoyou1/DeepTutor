@echo off
rem Agent CLI entry point for the bundled DeepTutor core.
rem Finds (or installs on first use) the sealed runtime the desktop app
rem uses, then runs deeptutor_cli.main with the same interpreter and the
rem same DEEPTUTOR_HOME default as the desktop shell (src/runtime.mjs).
setlocal EnableExtensions

rem deeptutor-ensure.ps1 prints the runtime root, or fails with a message.
rem Absolute powershell.exe: the caller may run with a stripped PATH (see
rem smoke-cli.ps1). The output goes through a temp file because for /f mangles
rem command lines whose executable path is quoted (e.g. under "Open DeepTutor").
set "PS=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
set "ENSURE_OUT=%TEMP%\deeptutor-ensure-%RANDOM%%RANDOM%.tmp"
"%PS%" -NoProfile -ExecutionPolicy Bypass -File "%~dp0deeptutor-ensure.ps1" > "%ENSURE_OUT%"
if errorlevel 1 (
    del /q "%ENSURE_OUT%" 2>nul
    echo deeptutor: no bundled runtime is available. 1>&2
    exit /b 1
)
set "RUNTIME="
set /p RUNTIME=<"%ENSURE_OUT%"
del /q "%ENSURE_OUT%" 2>nul
if not defined RUNTIME (
    echo deeptutor: no bundled runtime is available. 1>&2
    exit /b 1
)
if not exist "%RUNTIME%\python\python.exe" (
    echo deeptutor: the bundled runtime is incomplete. Reinstall the app. 1>&2
    exit /b 1
)

rem The desktop shell sets the same variables before starting the core.
if not defined DEEPTUTOR_HOME set "DEEPTUTOR_HOME=%LOCALAPPDATA%\Open DeepTutor\workspace"
set "PYTHONPATH=%RUNTIME%\core"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "PYTHONDONTWRITEBYTECODE=1"

rem -P keeps the caller's directory out of sys.path; only the sealed core is importable.
"%RUNTIME%\python\python.exe" -P -m deeptutor_cli.main %*
exit /b %ERRORLEVEL%
