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

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\start.ps1
  powershell -ExecutionPolicy Bypass -File scripts\start.ps1 -Port 3100 -NoBrowser
#>
param(
    [int]$Port = 0,
    [switch]$NoBrowser,
    [switch]$Dev
)
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "common.ps1")

$python = Get-VenvPython
if (-not (Test-Path $python)) {
    Write-Miss "ClipForge is not set up yet. Run:  powershell -ExecutionPolicy Bypass -File scripts\setup.ps1"
    exit 1
}
if (-not (Test-Path (Join-Path $ProjectRoot "frontend\dist\index.html"))) {
    Write-Miss "The interface has not been built. Run scripts\setup.ps1 (or 'npm run build' in the frontend folder)."
    exit 1
}
if (-not (Find-Tool "ffmpeg")) { Write-Warn "FFmpeg is not on PATH. Videos cannot be processed until it is installed (winget install Gyan.FFmpeg)." }

$env:PYTHONUTF8 = "1"
if ($Port -gt 0) { $env:CLIPFORGE_PORT = "$Port" }

# Ask the app where it will listen, so .env is read exactly as the server reads it.
$where = Get-AppValue "from app.serve import server_options as o; x = o(); print(x['host'], x['port'])"
if (-not $where) {
    Write-Fail "ClipForge could not read its settings (the reason is printed above). Check the .env file in the project folder."
    exit 1
}
$bindHost, $bindPort = "$where".Trim() -split " ", 2
$bindPort = [int]$bindPort

$listener = Get-NetTCPConnection -LocalPort $bindPort -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($listener) {
    Write-Fail "Port $bindPort is already in use (process $($listener.OwningProcess)). ClipForge may already be running: open http://localhost:$bindPort"
    Write-Host "            To stop a running copy:  powershell -ExecutionPolicy Bypass -File scripts\stop.ps1"
    Write-Host "            To use another port:     powershell -ExecutionPolicy Bypass -File scripts\start.ps1 -Port 3100"
    exit 1
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
exit $code
