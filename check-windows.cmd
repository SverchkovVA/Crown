@echo off
setlocal DisableDelayedExpansion
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0packaging\windows\verify.ps1"
set "pf_exit_code=%ERRORLEVEL%"
pause
exit /b %pf_exit_code%
