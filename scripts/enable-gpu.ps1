<#
.SYNOPSIS
  Lets speech-to-text run on an NVIDIA graphics card (optional; much faster on long videos).

.DESCRIPTION
  Installs NVIDIA's cuBLAS and cuDNN runtime libraries for CUDA 12 into ClipForge's own Python
  environment (from PyPI, about 1.3 GB). Nothing is installed system-wide.

  Afterwards open Settings in the app and press "Test again". ClipForge only switches to the graphics card
  if a speech model really loads and runs on it; otherwise it keeps using the processor and says why.

  Video encoding on the graphics card (NVENC) does not need this script.
#>
param([switch]$Yes)
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "common.ps1")

$python = Get-VenvPython
if (-not (Test-Path $python)) { Write-Miss "Run scripts\setup.ps1 first."; exit 1 }
if (-not (Find-Tool "nvidia-smi")) { Write-Miss "No NVIDIA graphics card (or driver) was found on this PC."; exit 1 }
if (-not $Yes) {
    $answer = Read-Host "  Download about 1.3 GB of NVIDIA runtime libraries into ClipForge's environment? [y/N]"
    if ($answer -notmatch '^(y|yes)$') { Write-Host "  Nothing was installed."; exit 0 }
}
Write-Step "Installing nvidia-cublas-cu12 and nvidia-cudnn-cu12"
$uv = Find-Tool "uv"
if ($uv) { $code = Invoke-Native $uv @("pip", "install", "--python", $python, "nvidia-cublas-cu12", "nvidia-cudnn-cu12") }
else { $code = Invoke-Native $python @("-m", "pip", "install", "--disable-pip-version-check", "nvidia-cublas-cu12", "nvidia-cudnn-cu12") }
if ($code -ne 0) { Write-Miss "The installation failed."; exit 1 }
Remove-Item (Join-Path $ProjectRoot "data\hardware.json") -Force -ErrorAction SilentlyContinue
Write-Ok "Installed. Restart ClipForge, then open Settings and press 'Test again' to confirm the graphics card is used."
