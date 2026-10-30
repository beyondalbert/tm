@echo off
setlocal
rem Launch the TM release script with Windows PowerShell, bypassing the
rem execution policy for this invocation. All arguments are forwarded as-is.
rem
rem   scripts\release.bat
rem   scripts\release.bat -Bump minor
rem   scripts\release.bat -Message "feat(core): add x" -Paths src\tm\x.py tests\test_x.py
rem   scripts\release.bat -SkipChecks

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0release.ps1" %*
exit /b %ERRORLEVEL%
