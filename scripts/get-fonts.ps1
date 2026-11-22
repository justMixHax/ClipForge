<#
.SYNOPSIS
  Downloads open-licence caption fonts into data\fonts (about 2 MB in total).

.DESCRIPTION
  All are under the SIL Open Font License, fetched from their official GitHub repositories:
    Noto Sans (Bold, Black)   notofonts/notofonts.github.io
    Montserrat (ExtraBold)    JulietaUla/Montserrat
    Anton, Bebas Neue         google/fonts
  ClipForge works without them, using fonts that ship with Windows (Arial Black, Impact, Segoe UI).
#>
$ErrorActionPreference = "Continue"
$ProgressPreference = "SilentlyContinue"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
. (Join-Path $PSScriptRoot "common.ps1")

$target = Join-Path $ProjectRoot "data\fonts"
New-Item -ItemType Directory -Force -Path $target | Out-Null
$fonts = @(
    @{ File = "NotoSans-Bold.ttf";        Url = "https://github.com/notofonts/notofonts.github.io/raw/main/fonts/NotoSans/hinted/ttf/NotoSans-Bold.ttf" },
    @{ File = "NotoSans-Black.ttf";       Url = "https://github.com/notofonts/notofonts.github.io/raw/main/fonts/NotoSans/hinted/ttf/NotoSans-Black.ttf" },
    @{ File = "Montserrat-ExtraBold.ttf"; Url = "https://github.com/JulietaUla/Montserrat/raw/master/fonts/ttf/Montserrat-ExtraBold.ttf" },
    @{ File = "Anton-Regular.ttf";        Url = "https://github.com/google/fonts/raw/main/ofl/anton/Anton-Regular.ttf" },
    @{ File = "BebasNeue-Regular.ttf";    Url = "https://github.com/google/fonts/raw/main/ofl/bebasneue/BebasNeue-Regular.ttf" }
)
$got = 0
foreach ($font in $fonts) {
    $path = Join-Path $target $font.File
    if ((Test-Path $path) -and (Get-Item $path).Length -gt 20000) { Write-Ok "$($font.File) (already here)"; $got++; continue }
    try {
        Invoke-WebRequest -UseBasicParsing -Uri $font.Url -OutFile $path -TimeoutSec 60
        # a real font file starts with one of these signatures; anything else (an HTML error page) is discarded
        $head = [IO.File]::ReadAllBytes($path)[0..3]
        $signature = [BitConverter]::ToString($head)
        if ($signature -eq "00-01-00-00" -or $signature -eq "4F-54-54-4F" -or $signature -eq "74-72-75-65") { Write-Ok $font.File; $got++ }
        else { Remove-Item $path -Force; Write-Warn "$($font.File): the download was not a font file; skipped." }
    } catch {
        Remove-Item $path -Force -ErrorAction SilentlyContinue
        Write-Warn "$($font.File): could not be downloaded ($($_.Exception.Message))."
    }
}
Write-Host "  $got of $($fonts.Count) fonts are in $target. They appear in the editor's font list after the app is restarted."
