@echo off
cd /d "%~dp0"
if not exist "%~dp0output\desktop\Xiaoman-win32-x64\Xiaoman.exe" (
  echo Desktop app is not built. See README for desktop build instructions.
  pause
  exit /b 1
)
start "" "%~dp0output\desktop\Xiaoman-win32-x64\Xiaoman.exe"
