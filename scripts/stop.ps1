<#
.SYNOPSIS
  Stops a running ClipForge (web server and worker) that was started from this project.

.DESCRIPTION
  The normal way to stop is Ctrl+C in the window where start.ps1 is running. Use this script when that
  window is gone, or from another terminal.

  It asks the server to shut down in order (it stops taking requests, and the worker puts the job it is
  running back in the queue so it continues next time). Only if that has not happened after 20 seconds
  is anything ended by force.
#>
$ErrorActionPreference = "Continue"
. (Join-Path $PSScriptRoot "common.ps1")

if (-not (Test-Path (Get-VenvPython))) { Write-Host "ClipForge is not set up, so nothing is running."; exit 0 }
if (-not (Get-ClipForgeProcesses)) { Write-Host "ClipForge is not running."; exit 0 }

$dataDir = Get-AppValue "from app.config import get_settings; print(get_settings().data_dir)"
if (-not $dataDir) { $dataDir = Join-Path $ProjectRoot "data" }
$serverFlag = Join-Path $dataDir "server.stop"
$workerFlag = Join-Path $dataDir "worker.stop"

Write-Host "Asking ClipForge to stop..."
try { [IO.File]::WriteAllText($serverFlag, "stop") } catch { }
$deadline = (Get-Date).AddSeconds(20)
while ((Get-Date) -lt $deadline -and (Get-ClipForgeProcesses)) { Start-Sleep -Milliseconds 500 }

$forced = $false
if (Get-ClipForgeProcesses) {
    # It did not stop on its own. Ask the worker directly to hand its job back, then end what is left.
    try { [IO.File]::WriteAllText($workerFlag, "stop") } catch { }
    Start-Sleep -Seconds 4
    foreach ($process in (Get-ClipForgeProcesses)) {
        Stop-Process -Id $process.ProcessId -Force -Confirm:$false -ErrorAction SilentlyContinue
        $forced = $true
    }
    Remove-Item $workerFlag -Force -ErrorAction SilentlyContinue
}
Remove-Item $serverFlag -Force -ErrorAction SilentlyContinue

if (Get-ClipForgeProcesses) { Write-Host "Some ClipForge processes could not be stopped. Close them in Task Manager (python.exe)." -ForegroundColor Red; exit 1 }
if ($forced) { Write-Host "ClipForge was stopped by force. A job that was running will start again from its last saved step." }
else { Write-Host "ClipForge stopped. Unfinished jobs will continue the next time it starts." }
exit 0
