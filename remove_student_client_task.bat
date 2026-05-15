@echo off
setlocal

set "TASK_NAME=NWMonitorStudentClient"

echo Removing task "%TASK_NAME%" from Task Scheduler...
schtasks /Query /TN "%TASK_NAME%" >nul 2>&1
if errorlevel 1 (
    echo Task not found. Nothing to remove.
    exit /b 0
)

schtasks /Delete /TN "%TASK_NAME%" /F >nul 2>&1
if errorlevel 1 (
    echo Failed to remove task "%TASK_NAME%".
    exit /b 1
)

echo Task "%TASK_NAME%" removed successfully.
exit /b 0
