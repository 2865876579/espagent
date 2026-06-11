@echo off
setlocal

set "ROOT=%~dp0"
set "SERVER_DIR=%ROOT%server"

if /i "%~1"=="server" goto run_server
if /i "%~1"=="pc_agent" goto run_pc_agent
if /i "%~1"=="client" goto run_client

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

start "ESPAgent Server" cmd /k call "%~f0" server
timeout /t 2 /nobreak >nul

start "ESPAgent PC Agent" cmd /k call "%~f0" pc_agent
timeout /t 1 /nobreak >nul

start "ESPAgent Test Client" cmd /k call "%~f0" client

echo Started. Use the "ESPAgent Test Client" window to type messages.
echo Close the opened windows when you finish testing.
pause
exit /b 0

:run_server
cd /d "%SERVER_DIR%"
echo [ESPAgent Server] %CD%
echo Running: py main.py
echo.
py main.py
echo.
echo Server exited.
pause
exit /b %errorlevel%

:run_pc_agent
cd /d "%SERVER_DIR%"
echo [ESPAgent PC Agent] %CD%
echo Running: py pc_agent.py
echo.
py pc_agent.py
echo.
echo PC Agent exited.
pause
exit /b %errorlevel%

:run_client
cd /d "%SERVER_DIR%"
echo [ESPAgent Test Client] %CD%
echo Running: py test_client.py
echo.
py test_client.py
echo.
echo Test Client exited.
pause
exit /b %errorlevel%
