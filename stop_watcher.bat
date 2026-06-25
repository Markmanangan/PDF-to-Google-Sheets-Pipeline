@echo off
echo Stopping the PDF Folder Watcher Engine...
taskkill /F /IM folder_watcher.exe >nul 2>&1
taskkill /F /FI "WINDOWTITLE eq *folder_watcher*" >nul 2>&1
echo Engine successfully stopped!
