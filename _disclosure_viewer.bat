@echo off
setlocal
cd /d "%~dp0"

if exist "DART-Disclosure-Viewer.exe" (
  start "" "%~dp0DART-Disclosure-Viewer.exe"
  exit /b
)

if exist "%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" (
  start "" "%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" "%~dp0disclosure_viewer.py"
  exit /b
)

python "%~dp0disclosure_viewer.py"
