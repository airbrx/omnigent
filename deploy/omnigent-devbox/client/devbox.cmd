@echo off
REM Shim so `devbox <cmd>` works from cmd.exe as well as PowerShell.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0devbox.ps1" %*
