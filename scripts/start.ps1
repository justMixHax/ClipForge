<#
.SYNOPSIS
  Starts ClipForge: the interface, the API and the processing worker, on http://localhost:3000.

.DESCRIPTION
  One window runs everything. The web server serves the interface and the API, and starts and watches the
  worker process that does the transcription and rendering. Press Ctrl+C in this window to stop both;
  a job that was running continues the next time you start.

  The address and port come from the .env file (127.0.0.1 and 3000 unless you changed them).

.PARAMETER Port
  Use this port just for this run, instead of the one in .env.

.PARAMETER NoBrowser
  Do not open the browser.

.PARAMETER Dev
  For working on the interface: also starts the live-reloading development server in a second window
  and opens http://localhost:5173 instead. Not needed for normal use.

.PARAMETER Shortcut
  Set by the desktop shortcut. Its window is minimised and closes when this script ends, so a problem that
  stops ClipForge from starting is also shown in a small window of its own.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\start.ps1
  powershell -ExecutionPolicy Bypass -File scripts\start.ps1 -Port 3100 -NoBrowser
#>
param(
    [int]$Port = 0,
    [switch]$NoBrowser,
    [switch]$Dev,
    [switch]$Shortcut
)
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "common.ps1")

function Stop-Start($text) {
    # Says why ClipForge cannot start and ends the script. From the shortcut, also in a window you can see.
    Write-Miss $text
    if ($Shortcut) { Show-Problem $text }
    exit 1
}

$python = Get-VenvPython
if (-not (Test-Path $python)) {
    Stop-Start ("ClipForge's Python environment was not found at " + (Get-VenvDir) + ". Set it up first: open PowerShell in " + $ProjectRoot + " and run:  powershell -ExecutionPolicy Bypass -File scripts\setup.ps1")
}
if (-not (Test-Path (Join-Path $ProjectRoot "frontend\dist\index.html"))) {
    Stop-Start "The interface has not been built. Run scripts\setup.ps1 (or 'npm run build' in the frontend folder)."
}
if (-not (Find-Tool "ffmpeg")) { Write-Warn "FFmpeg is not on PATH. Videos cannot be processed until it is installed (winget install Gyan.FFmpeg)." }

$env:PYTHONUTF8 = "1"
if ($Port -gt 0) { $env:CLIPFORGE_PORT = "$Port" }

# Ask the app where it will listen, so .env is read exactly as the server reads it.
$where = Get-AppValue "from app.serve import server_options as o; x = o(); print(x['host'], x['port'])"
if (-not $where) {
    Stop-Start "ClipForge could not read its settings. Check the .env file in the project folder; running scripts\start.ps1 in a PowerShell window shows the reason."
}
$bindHost, $bindPort = "$where".Trim() -split " ", 2
$bindPort = [int]$bindPort

$listener = Get-NetTCPConnection -LocalPort $bindPort -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($listener) {
    $alreadyRunning = $false
    try { $alreadyRunning = ((Invoke-RestMethod -TimeoutSec 3 -Uri "http://127.0.0.1:$bindPort/api/health").status -eq "ok") } catch { }
    if ($alreadyRunning) {
        # Started again (the desktop shortcut, say) while it runs: just open it.
        Write-Ok "ClipForge is already running: http://localhost:$bindPort"
        if (-not $NoBrowser) { Start-Process "http://localhost:$bindPort" }
        exit 0
    }
    Write-Host "            To stop a running copy:  powershell -ExecutionPolicy Bypass -File scripts\stop.ps1"
    Write-Host "            To use another port:     powershell -ExecutionPolicy Bypass -File scripts\start.ps1 -Port 3100"
    Stop-Start "Port $bindPort is already in use by another program (process $($listener.OwningProcess)), so ClipForge cannot start there. Close that program, or set another port (CLIPFORGE_PORT) in the .env file."
}

Write-Host ""
if ($bindHost -eq "127.0.0.1" -or $bindHost -eq "localhost" -or $bindHost -eq "::1") {
    Write-Host "Only this PC can reach ClipForge. Logs are in data\logs." -ForegroundColor Green
} else {
    Write-Warn "ClipForge is set to listen on $bindHost, so other devices on your network can reach it. It has no login."
}

$openPort = $bindPort
if ($Dev) {
    if (-not (Test-Path (Join-Path $ProjectRoot "frontend\node_modules"))) { Write-Miss "Run scripts\setup.ps1 first (the interface packages are not installed)."; exit 1 }
    Write-Host "Starting the interface development server in a second window (http://localhost:5173)."
    Start-Process -FilePath "cmd.exe" -ArgumentList "/k", "title ClipForge interface (dev) && npm.cmd run dev" -WorkingDirectory (Join-Path $ProjectRoot "frontend")
    $openPort = 5173
}

if (-not $NoBrowser) {
    # open the browser once the server answers
    Start-Job -ArgumentList $bindPort, $openPort -ScriptBlock {
        param($apiPort, $pagePort)
        for ($i = 0; $i -lt 120; $i++) {
            try {
                Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 -Uri "http://127.0.0.1:$apiPort/api/health" | Out-Null
                Start-Process "http://localhost:$pagePort"
                return
            } catch { Start-Sleep -Milliseconds 500 }
        }
    } | Out-Null
}

$code = 0
Push-Location (Join-Path $ProjectRoot "backend")
try {
    & $python -m app.serve
    $code = $LASTEXITCODE
} finally {
    Pop-Location
}
if ($code -ne 0 -and $Shortcut) {
    Show-Problem "ClipForge stopped because of a problem (code $code). What went wrong is at the end of data\logs\api.log in $ProjectRoot."
}
exit $code
