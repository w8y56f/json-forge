@echo off
setlocal
set "APP_DIR=%~dp0"
set "PYTHONW="

rem Use pythonw.exe so double-clicking this file does not leave a console
rem window open while the GUI application is running.
if exist "%APP_DIR%runtime\python\pythonw.exe" set "PYTHONW=%APP_DIR%runtime\python\pythonw.exe"
if not defined PYTHONW if exist "%APP_DIR%runtime\python\Scripts\pythonw.exe" set "PYTHONW=%APP_DIR%runtime\python\Scripts\pythonw.exe"
if not defined PYTHONW if exist "%APP_DIR%runtime\pythonw.exe" set "PYTHONW=%APP_DIR%runtime\pythonw.exe"
if not defined PYTHONW if exist "%APP_DIR%.venv\Scripts\pythonw.exe" set "PYTHONW=%APP_DIR%.venv\Scripts\pythonw.exe"

if not defined PYTHONW (
  echo JSON Forge bundled Python GUI runtime was not found.
  echo Please use a complete release package, or create .venv and install requirements.txt.
  pause
  exit /b 1
)

pushd "%APP_DIR%"
start "" /b "%PYTHONW%" "%APP_DIR%app.py" %*
if errorlevel 1 (
  echo JSON Forge could not be started.
  popd
  pause
  exit /b 1
)
popd
exit /b 0
