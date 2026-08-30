$ErrorActionPreference = 'Stop'

# knx-ets-mcp AddIn installer (ETS5 + ETS6).
# Run from the EXTRACTED release folder (never from inside the zip). Expected layout:
#   install.bat / install.ps1 / uninstall.bat       (here, at the root)
#   ets6\<AppId>\AddInManifest.xml + .dll files      (the ETS6-built AddIn)
#   ets5\<AppId>\AddInManifest.xml + .dll files      (the ETS5-built AddIn)
# For every ETS version actually installed on this machine, the matching build is
# copied into that version's AddIns folder:  %ProgramData%\KNX\ETS<n>\Apps\AddIns\<AppId>\
# The SDK is version-bound, so ETS5 and ETS6 need their own separately-built DLL.

$root = $PSScriptRoot

# Presence is detected via the per-version data folder %ProgramData%\KNX\ETS<n>.
$versions = @(
    @{ Name = 'ETS6'; Src = 'ets6' },
    @{ Name = 'ETS5'; Src = 'ets5' }
)

# The AddIn payload folders (ets6\ / ets5\) must sit next to this script. If neither is
# here, this script was almost certainly launched from INSIDE the zip (Windows extracts
# only the clicked file to a temp folder). Fail loudly with the fix instead of silently
# doing nothing.
if (-not (Test-Path (Join-Path $root 'ets6')) -and -not (Test-Path (Join-Path $root 'ets5'))) {
    Write-Host "AddIn payload not found next to this script (no 'ets6' or 'ets5' folder)." -ForegroundColor Red
    Write-Host "You likely ran this from INSIDE the zip. EXTRACT THE WHOLE ZIP to a folder" -ForegroundColor Yellow
    Write-Host "first, then run install.bat from that extracted folder." -ForegroundColor Yellow
    exit 1
}

$installedAny = $false
foreach ($v in $versions) {
    $srcDir = Join-Path $root $v.Src
    if (-not (Test-Path $srcDir)) { continue }  # this release does not include that build

    $etsData = Join-Path $env:ProgramData ("KNX\" + $v.Name)
    if (-not (Test-Path $etsData)) {
        Write-Host "$($v.Name) not detected (no $etsData) - skipping." -ForegroundColor DarkGray
        continue
    }

    # The AppId is the single subfolder that contains an AddInManifest.xml.
    $appDir = Get-ChildItem -Path $srcDir -Directory |
        Where-Object { Test-Path (Join-Path $_.FullName 'AddInManifest.xml') } |
        Select-Object -First 1
    if (-not $appDir) {
        Write-Host "No AddIn folder (AddInManifest.xml) under $srcDir - skipping $($v.Name)." -ForegroundColor Yellow
        continue
    }
    $appId = $appDir.Name

    if (Get-Process $v.Name -ErrorAction SilentlyContinue) {
        Write-Host "$($v.Name) is running. Close it, then run install.bat again." -ForegroundColor Yellow
        continue
    }

    $target = Join-Path $etsData "Apps\AddIns\$appId"
    try {
        if (Test-Path $target) { Remove-Item -Path $target -Recurse -Force }
        New-Item -ItemType Directory -Force -Path $target | Out-Null
        Copy-Item -Path (Join-Path $appDir.FullName '*') -Destination $target -Recurse -Force

        # Install the ETS directory signature NEXT TO the app folder (in AddIns\), not
        # inside it: ETS only treats the folder as signed when <AppId>.signature sits
        # beside it. It lives in the payload's ets<n>\ folder, next to <AppId>\.
        $sigSrc = Join-Path $appDir.Parent.FullName ("$appId.signature")
        $sigDest = Join-Path (Split-Path $target -Parent) "$appId.signature"
        if (Test-Path $sigSrc) {
            Copy-Item -Path $sigSrc -Destination $sigDest -Force
            Write-Host "Installed signature: $sigDest" -ForegroundColor Green
        } else {
            Write-Host "No .signature in payload - AddIn will load UNSIGNED." -ForegroundColor DarkYellow
        }

        # Clear the AddIns cache so ETS re-reads the manifest on next start.
        $cache = Join-Path $env:LOCALAPPDATA ("Knx\" + $v.Name + "\AddInsCache")
        if (Test-Path $cache) { Remove-Item -Path $cache -Recurse -Force -ErrorAction SilentlyContinue }

        Write-Host "Installed into $($v.Name): $target" -ForegroundColor Green
        $installedAny = $true
    }
    catch {
        Write-Host "$($v.Name) install failed: $($_.Exception.Message)" -ForegroundColor Red
        Write-Host "If this is an access error, right-click install.bat and 'Run as administrator'." -ForegroundColor Yellow
    }
}

if (-not $installedAny) {
    Write-Host "No ETS5/ETS6 installation detected (checked %ProgramData%\KNX\ETS5 and \ETS6)." -ForegroundColor Red
    Write-Host "Install ETS first, or copy ets6\<AppId> or ets5\<AppId> manually into %ProgramData%\KNX\ETS<n>\Apps\AddIns\." -ForegroundColor Yellow
    return
}

Write-Host "Done. Start ETS and open a project; the 'KNX-ETS MCP Bridge' AddIn should appear." -ForegroundColor Cyan

# Big, unmissable next step: start the MCP server. Only shown when the server is bundled
# in this folder (the full release zip). Keep it running while the AI assistant is used.
$mcpExe = Get-ChildItem -Path $root -Filter '*.exe' -ErrorAction SilentlyContinue | Select-Object -First 1
$mcpWhl = Get-ChildItem -Path $root -Recurse -Filter '*.whl' -ErrorAction SilentlyContinue | Select-Object -First 1
if ($mcpExe -or $mcpWhl) {
    Write-Host ""
    Write-Host "  ============================================================  " -ForegroundColor Black -BackgroundColor Green
    Write-Host "   NEXT STEP  ->  start the MCP server (leave it running)         " -ForegroundColor Black -BackgroundColor Green
    if ($mcpExe) {
        Write-Host "   Double-click:  $($mcpExe.Name)                             " -ForegroundColor Black -BackgroundColor Green
    }
    Write-Host "  ============================================================  " -ForegroundColor Black -BackgroundColor Green
    Write-Host ""
    Write-Host "It serves the AI client over HTTP on http://0.0.0.0:8765/mcp by default." -ForegroundColor Gray
    if ($mcpWhl) {
        Write-Host "Or with your own Python:  pip install `"$($mcpWhl.FullName)`"  then run:  knx-ets-mcp" -ForegroundColor Gray
    }
}
