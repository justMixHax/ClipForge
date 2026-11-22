<#
.SYNOPSIS
  Builds the synthetic sample video used by the slow tests (samples\clipforge_sample.mp4, about 80 MB).

.DESCRIPTION
  Everything is generated on this PC: the speech comes from the voices built into Windows and the pictures
  from FFmpeg test patterns, so nothing is downloaded and there are no rights questions. The video mixes
  dull filler with four deliberately clip-worthy moments, and the script it was built from is saved next
  to it so the tests can check what ClipForge found. Takes a minute or two.

.PARAMETER OutputDir
  Where to write the two files. Default: the samples folder in the project.
#>
param([string]$OutputDir = "")
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "common.ps1")

$python = Get-VenvPython
if (-not (Test-Path $python)) { Write-Miss "Run scripts\setup.ps1 first."; exit 1 }
if (-not (Find-Tool "ffmpeg")) { Write-Miss "FFmpeg is not on PATH (winget install Gyan.FFmpeg)."; exit 1 }

$arguments = @((Join-Path $PSScriptRoot "sample\make_sample.py"))
if ($OutputDir) { $arguments += $OutputDir }
Write-Step "Building the sample video"
$code = Invoke-Native $python $arguments
if ($code -ne 0) { Write-Miss "The sample video could not be built."; exit $code }
Write-Ok "Sample video ready. Run the slow tests with:  powershell -ExecutionPolicy Bypass -File scripts\test.ps1 -All"
