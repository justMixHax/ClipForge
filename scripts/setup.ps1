<#
.SYNOPSIS
  Sets up ClipForge on this PC. Safe to run again at any time.

.DESCRIPTION
  Checks FFmpeg, Python 3.12 and Node.js, creates the Python environment, installs the backend packages,
  installs a speech model and the face detector, and builds the interface.

  What it downloads: the Python packages listed in backend\requirements.txt (from PyPI) and the interface
  packages (from npm). These are what ClipForge is built from, so they are installed without asking.
  The speech model (about 480 MB) and the face detector are copied if they are already on this PC;
  otherwise setup asks before downloading them (or pass -Yes to accept).

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
  powershell -ExecutionPolicy Bypass -File scripts\setup.ps1 -Yes -Fonts
#>
param(
    [switch]$Yes,          # accept downloads without asking
    [switch]$Fonts,        # also fetch the open-licence caption fonts (Noto Sans and others, about 2 MB)
    [switch]$SkipFrontend, # do not build the interface
    [string]$Model = "small.en"
)
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
. (Join-Path $PSScriptRoot "common.ps1")
$failed = $false

function Confirm-Download($what) {
    if ($Yes) { return $true }
    $answer = Read-Host "  Download $what? [y/N]"
    return ($answer -match '^(y|yes)$')
}

Write-Host ""
Write-Host "ClipForge setup" -ForegroundColor White
Write-Host "Project: $ProjectRoot"
if (-not [Environment]::Is64BitOperatingSystem) { Write-Miss "64-bit Windows 10 or 11 is required."; exit 1 }

# 1. FFmpeg ------------------------------------------------------------------------------------------
$ffmpeg = Find-Tool "ffmpeg"; $ffprobe = Find-Tool "ffprobe"
if ($ffmpeg -and $ffprobe) { Write-Ok "FFmpeg ($ffmpeg)" }
else {
    Write-Miss "FFmpeg was not found. Install it with:  winget install Gyan.FFmpeg   then open a new terminal and run setup again."
    $failed = $true
}

# 2. Python environment ----------------------------------------------------------------------------
$venvDir = Get-VenvDir
$python = Get-VenvPython
$uv = Find-Tool "uv"
if (-not (Test-Path $python)) {
    Write-Step "Creating the Python 3.12 environment in $venvDir"
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $venvDir) | Out-Null
    if ($uv) {
        $code = Invoke-Native $uv @("venv", "--python", "3.12", $venvDir)
    } elseif (Find-Tool "py") {
        $code = Invoke-Native "py" @("-3.12", "-m", "venv", $venvDir)
    } else {
        Write-Miss "Python 3.12 was not found. Install it from python.org (or install uv: https://docs.astral.sh/uv/), then run setup again."
        $code = 1
    }
    if ($code -ne 0 -or -not (Test-Path $python)) { Write-Miss "The Python environment could not be created."; exit 1 }
}
$defaultVenv = Join-Path $ProjectRoot "backend\.venv"
if ($venvDir -ne $defaultVenv) {
    [IO.File]::WriteAllText((Join-Path $ProjectRoot ".venv-path"), $venvDir)
    if ($ProjectRoot.Length -gt 100) { Write-Warn "The project path is long, so the Python environment is kept in $venvDir (Windows cannot load libraries from very long paths)." }
    else { Write-Warn "The Python environment is kept outside the project, in $venvDir (the .venv-path file records this)." }
}
Write-Ok "Python environment ($python)"

Write-Step "Installing backend packages"
$requirements = Join-Path $ProjectRoot "backend\requirements.txt"
if ($uv) { $code = Invoke-Native $uv @("pip", "install", "--python", $python, "-q", "-r", $requirements) }
else { $code = Invoke-Native $python @("-m", "pip", "install", "-q", "--disable-pip-version-check", "-r", $requirements) }
if ($code -eq 0) { Write-Ok "Backend packages" } else { Write-Miss "Backend packages failed to install."; $failed = $true }

$runtime = Get-AppValue "from app.services import downloader; print(downloader.js_runtime() or 'none')"
if ($runtime -and $runtime -ne "none") { Write-Ok "Importing from links (YouTube links will use $runtime)" }
elseif ($runtime -eq "none") { Write-Warn "Importing from YouTube links needs Deno or Node.js 22 or newer. Install one with:  winget install DenoLand.Deno   (other sites work without it)." }

# 3. Local data, configuration ----------------------------------------------------------------------
$envFile = Join-Path $ProjectRoot ".env"
if (-not (Test-Path $envFile)) { Copy-Item (Join-Path $ProjectRoot ".env.example") $envFile; Write-Ok "Created .env from .env.example" }
$backend = Join-Path $ProjectRoot "backend"

# 4. Speech model -------------------------------------------------------------------------------------
if ($Model -notmatch '^[A-Za-z0-9._-]+$') { Write-Miss "'$Model' is not a valid speech model name."; exit 1 }
$state = Get-AppValue "from app.transcription import models as m; print('installed' if m.installed_models() else ('cached' if m.find_in_local_caches('$Model') else 'none'))"
if ($null -eq $state) { Write-Miss "The speech model could not be checked because the backend did not start (see the message above)."; $failed = $true }
elseif ($state -eq "installed") { Write-Ok "Speech model" }
elseif ($state -eq "cached") {
    Write-Step "Copying the '$Model' speech model from a cache already on this PC"
    $code = Invoke-Native $python @("-c", "from app.transcription import models as m; raise SystemExit(0 if m.install_from_cache('$Model') else 1)") $backend
    if ($code -eq 0) { Write-Ok "Speech model ($Model)" } else { Write-Miss "The speech model could not be copied."; $failed = $true }
}
elseif (Confirm-Download "the '$Model' speech model from Hugging Face (about 480 MB for small.en)") {
    Write-Step "Downloading the '$Model' speech model"
    $code = Invoke-Native $python @("-c", "from app.transcription import models as m; m.download('$Model'); raise SystemExit(0 if m.is_installed('$Model') else 1)") $backend
    if ($code -eq 0) { Write-Ok "Speech model ($Model)" } else { Write-Miss "The speech model download failed."; $failed = $true }
}
else { Write-Warn "No speech model installed. Install one later in Settings, inside the app." }

# 5. Face detector (228 KB) -------------------------------------------------------------------------
$yunetDir = Join-Path $ProjectRoot "data\models\yunet"
$yunet = Join-Path $yunetDir "face_detection_yunet_2023mar.onnx"
if (Test-Path $yunet) { Write-Ok "Face detector" }
else {
    New-Item -ItemType Directory -Force -Path $yunetDir | Out-Null
    $local = Join-Path $env:USERPROFILE ".claude\skills\gta6-clipper\models\face_detection_yunet_2023mar.onnx"
    if (Test-Path $local) { Copy-Item $local $yunet; Write-Ok "Face detector (copied from this PC)" }
    elseif (Confirm-Download "the YuNet face detector from the OpenCV model zoo (228 KB)") {
        try {
            Invoke-WebRequest -UseBasicParsing -Uri "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx" -OutFile $yunet
            Write-Ok "Face detector"
        } catch { Write-Warn "The face detector could not be downloaded. Clips will use the whole-frame layout until it is installed." }
    }
    else { Write-Warn "No face detector. Clips will use the whole-frame layout until it is installed." }
}

# 6. Optional caption fonts -------------------------------------------------------------------------
if ($Fonts) { & (Join-Path $PSScriptRoot "get-fonts.ps1") }

# 7. Interface ----------------------------------------------------------------------------------------
if (-not $SkipFrontend) {
    $npm = Find-Tool "npm"
    if (-not $npm) { Write-Miss "Node.js was not found. Install the LTS version from nodejs.org, then run setup again."; $failed = $true }
    else {
        $frontend = Join-Path $ProjectRoot "frontend"
        Write-Step "Installing interface packages"
        $code = Invoke-Native "npm.cmd" @("install", "--no-audit", "--no-fund", "--loglevel=error") $frontend
        if ($code -eq 0) {
            Write-Step "Building the interface"
            $code = Invoke-Native "npm.cmd" @("run", "build", "--silent") $frontend
        }
        if ($code -eq 0 -and (Test-Path (Join-Path $frontend "dist\index.html"))) { Write-Ok "Interface built" }
        else { Write-Miss "The interface failed to build."; $failed = $true }
    }
}

Write-Host ""
if ($failed) { Write-Host "NOT READY: fix the [MISSING] items above and run setup again." -ForegroundColor Red; exit 1 }
Write-Host "READY. Start ClipForge with:  powershell -ExecutionPolicy Bypass -File scripts\start.ps1" -ForegroundColor Green
exit 0
