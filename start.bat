@echo off
setlocal
set "APP_DIR=%~dp0"
set "PYTHON="

rem A release bundle contains runtime\python. The .venv fallback is useful
rem when this launcher is used from a development checkout.
if exist "%APP_DIR%runtime\python\python.exe" set "PYTHON=%APP_DIR%runtime\python\python.exe"
if not defined PYTHON if exist "%APP_DIR%runtime\python\python3.exe" set "PYTHON=%APP_DIR%runtime\python\python3.exe"
if not defined PYTHON if exist "%APP_DIR%runtime\python\Scripts\python.exe" set "PYTHON=%APP_DIR%runtime\python\Scripts\python.exe"
if not defined PYTHON if exist "%APP_DIR%runtime\python.exe" set "PYTHON=%APP_DIR%runtime\python.exe"
if not defined PYTHON if exist "%APP_DIR%.venv\Scripts\python.exe" set "PYTHON=%APP_DIR%.venv\Scripts\python.exe"

if not defined PYTHON (
  echo JSON Forge bundled Python runtime was not found.
  echo Please use a complete release package, or create .venv and install requirements.txt.
  exit /b 1
)

pushd "%APP_DIR%"
"%PYTHON%" "%APP_DIR%app.py" %*
set "EXIT_CODE=%ERRORLEVEL%"
popd
exit /b %EXIT_CODE%
