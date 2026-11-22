<#
.SYNOPSIS
  Updates the downloader ClipForge uses to import videos from links (yt-dlp).

.DESCRIPTION
  Video sites change often. When one does, importing from its links can stop working until yt-dlp has
  caught up, which usually takes its maintainers a few days. This script installs the newest yt-dlp release
  from PyPI into ClipForge's own Python environment (nothing system-wide) and records the installed
  versions in backend\requirements.txt. Restart ClipForge afterwards.

  Run it when an import fails with a message that suggests an update, or every few weeks.
#>
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "common.ps1")

$python = Get-VenvPython
if (-not (Test-Path $python)) { Write-Miss "Run scripts\setup.ps1 first."; exit 1 }

$probe = "from app.services import downloader; print(downloader.version() or 'not installed')"
$before = Get-AppValue $probe
Write-Step "Updating yt-dlp (installed now: $before)"
$uv = Find-Tool "uv"
if ($uv) { $code = Invoke-Native $uv @("pip", "install", "--python", $python, "--upgrade", "yt-dlp[default]") }
else { $code = Invoke-Native $python @("-m", "pip", "install", "--disable-pip-version-check", "--upgrade", "yt-dlp[default]") }
if ($code -ne 0) { Write-Miss "The update failed, so nothing was changed. Check your internet connection and try again."; exit 1 }

$after = Get-AppValue $probe
if (-not $after -or $after -eq "not installed") { Write-Miss "yt-dlp does not load after the update. Run scripts\setup.ps1 to restore the pinned version."; exit 1 }

# keep the pinned list in step with what is installed, so setup on another PC installs the same versions.
# The optional NVIDIA libraries from enable-gpu.ps1 are left out: a PC without an NVIDIA card must not be
# made to download them.
if ($uv) { $frozen = & $uv pip freeze --python $python } else { $frozen = & $python -m pip freeze --disable-pip-version-check }
if ($LASTEXITCODE -eq 0 -and $frozen) {
    $pinned = [string[]]@($frozen | Where-Object { $_ -notmatch '^nvidia-' })
    [IO.File]::WriteAllLines((Join-Path $ProjectRoot "backend\requirements.txt"), $pinned)
}

if ($after -eq $before) { Write-Ok "yt-dlp is already the newest version ($after)." }
else { Write-Ok "yt-dlp updated from $before to $after. Restart ClipForge to use it." }
exit 0
