@echo off
setlocal

set "ROOT=%~dp0"
set "SERVER_DIR=%ROOT%server"

if not exist "%SERVER_DIR%\main.py" (
  echo server\main.py not found.
  echo Current folder: %ROOT%
  pause
  exit /b 1
)

where py >nul 2>nul
if errorlevel 1 (
  echo Python launcher "py" was not found.
  echo Please install Python or fix PATH, then run this file again.
  pause
  exit /b 1
)

echo Starting ESPAgent local test...
echo.
echo 1. Server window:      py main.py
echo 2. PC Agent window:    py pc_agent.py
echo 3. Test Client window: py test_client.py
echo.

start "ESPAgent Server" cmd /k "cd /d ^"%SERVER_DIR%^" && py main.py"
timeout /t 2 /nobreak >nul

start "ESPAgent PC Agent" cmd /k "cd /d ^"%SERVER_DIR%^" && py pc_agent.py"
timeout /t 1 /nobreak >nul

start "ESPAgent Test Client" cmd /k "cd /d ^"%SERVER_DIR%^" && py test_client.py"

echo Started. Use the "ESPAgent Test Client" window to type messages.
echo Close the opened windows when you finish testing.
pause
