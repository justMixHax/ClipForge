<#
.SYNOPSIS
  Runs the automated tests.

.DESCRIPTION
  Default: the tests that need no speech model (about six and a half minutes, most of it real renders) plus a
  type-check of the interface.
  -All also runs the slow tests, which load the real speech model and process the sample video
  (create it first with:  powershell -ExecutionPolicy Bypass -File scripts\make-sample.ps1).
#>
param([switch]$All)
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "common.ps1")

$python = Get-VenvPython
if (-not (Test-Path $python)) { Write-Miss "Run scripts\setup.ps1 first."; exit 1 }
$arguments = @("-m", "pytest")
if (-not $All) { $arguments += @("-m", "not slow") }
$code = Invoke-Native $python $arguments (Join-Path $ProjectRoot "backend")
if ($code -ne 0) { Write-Host "Backend tests FAILED." -ForegroundColor Red; exit $code }

if (Test-Path (Join-Path $ProjectRoot "frontend\node_modules")) {
    $code = Invoke-Native "npm.cmd" @("run", "typecheck", "--silent") (Join-Path $ProjectRoot "frontend")
    if ($code -ne 0) { Write-Host "Interface type-check FAILED." -ForegroundColor Red; exit $code }
    Write-Ok "Interface type-check"
}
Write-Host "All tests passed." -ForegroundColor Green
