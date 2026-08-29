@echo off
REM knx-ets-mcp AddIn uninstaller (ETS5 + ETS6). Self-contained: the PowerShell is
REM inline, so this works even when run directly from inside the zip (no separate
REM uninstall.ps1 or payload folder needed). AppId is the static M0FFF-A0001.
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$id='M0FFF-A0001'; $any=$false; foreach($v in 'ETS6','ETS5'){ $t=Join-Path $env:ProgramData ('KNX\'+$v+'\Apps\AddIns\'+$id); if(Test-Path $t){ if(Get-Process $v -ErrorAction SilentlyContinue){ Write-Host ($v+' is running - close it and run uninstall.bat again.') -ForegroundColor Yellow; continue }; try{ Remove-Item $t -Recurse -Force; $c=Join-Path $env:LOCALAPPDATA ('Knx\'+$v+'\AddInsCache'); if(Test-Path $c){ Remove-Item $c -Recurse -Force -ErrorAction SilentlyContinue }; Write-Host ('Removed from '+$v+': '+$t) -ForegroundColor Green; $any=$true } catch { Write-Host ($v+' remove failed: '+$_.Exception.Message) -ForegroundColor Red; Write-Host 'If access denied, right-click uninstall.bat and Run as administrator.' -ForegroundColor Yellow } } }; if(-not $any){ Write-Host 'Nothing to remove (AddIn not found under ETS5/ETS6).' -ForegroundColor DarkGray }"
echo.
pause
