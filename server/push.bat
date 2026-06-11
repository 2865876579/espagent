@echo off
cd /d D:\espagent
git add .
git commit -m "auto backup %date% %time%"
git push
echo.
echo Done! Press any key to close...
pause > nul
