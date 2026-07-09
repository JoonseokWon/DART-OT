@echo off
setlocal
cd /d "%~dp0"

if exist "%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" (
  start "" "%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" "%~dp0depreciation_app.py"
  exit /b
)

python "%~dp0depreciation_app.py"
