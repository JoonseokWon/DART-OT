@echo off
setlocal
cd /d "%~dp0"

if exist "DART-OT.exe" (
  start "" "%~dp0DART-OT.exe"
  exit /b
)

if exist "%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" (
  start "" "%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" "%~dp0app.py"
  exit /b
)

python "%~dp0app.py"
