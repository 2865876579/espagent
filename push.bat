@echo off
setlocal

if /i "%~1" neq "--run" (
  start "ESPAgent Auto Push" cmd /k ""%~f0" --run"
  exit /b
)

cd /d "%~dp0"

echo ==============================
echo ESPAgent auto push
echo Folder: %CD%
echo ==============================
echo.

for /f "usebackq delims=" %%i in (`powershell -NoProfile -Command "Get-Date -Format 'yyyy-MM-dd HH:mm:ss'"`) do set "NOW=%%i"

git add -A

rem Keep local secrets and generated files out of automatic commits.
git restore --staged -- server/.env server/__pycache__/ server/last_response.json server/reply.mp3 2>nul

git diff --cached --quiet
if %errorlevel% equ 0 (
  echo No staged changes to commit.
  git status --short
  pause
  exit /b 0
)

git commit -m "auto backup %NOW%"
if errorlevel 1 (
  echo Commit failed.
  pause
  exit /b 1
)

git push
if errorlevel 1 (
  echo Push failed.
  pause
  exit /b 1
)

echo Done.
pause
