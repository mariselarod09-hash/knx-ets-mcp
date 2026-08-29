@echo off
REM knx-ets-mcp AddIn installer. Double-click to install the AddIn into every ETS version
REM present (ETS5 and/or ETS6). The real logic lives in the readable install.ps1 next to
REM this file. If install.ps1 is NOT here, this .bat was run straight from inside the zip
REM (Windows' built-in zip viewer unpacks only the clicked file to a temp folder), so we
REM print a loud "extract first" banner instead of a cryptic PowerShell -File error.
if not exist "%~dp0install.ps1" (
  powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "Write-Host ''; Write-Host '  ============================================================  ' -ForegroundColor Black -BackgroundColor Yellow; Write-Host '   EXTRACT THE WHOLE ZIP FIRST                                 ' -ForegroundColor Black -BackgroundColor Yellow; Write-Host '  ============================================================  ' -ForegroundColor Black -BackgroundColor Yellow; Write-Host ''; Write-Host 'You ran install.bat from inside the zip, so only this one file was unpacked.' -ForegroundColor Yellow; Write-Host 'Right-click the zip -> Extract All, then run install.bat from the extracted folder.' -ForegroundColor Yellow"
  echo.
  pause
  exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"
echo.
pause
