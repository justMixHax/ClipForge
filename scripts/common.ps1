# Shared helpers for the ClipForge scripts. Dot-sourced by the others; not run on its own.
# Written for Windows PowerShell 5.1 (the one that ships with Windows) and PowerShell 7.

$script:ProjectRoot = Split-Path -Parent $PSScriptRoot

function Get-VenvDir {
    # The Python environment normally lives in backend\.venv. Windows cannot load libraries from very
    # long paths, so when the project sits in a deep folder the environment is kept in a short path
    # instead, and its location is remembered in .venv-path.
    # That short path is in your user folder, not under AppData: Windows shows a packaged app (the Claude
    # desktop app, for one) its own private copy of AppData, so an environment made there from inside such an
    # app cannot be seen by a shortcut on the desktop.
    $marker = Join-Path $script:ProjectRoot ".venv-path"
    if (Test-Path $marker) {
        $saved = (Get-Content -Raw $marker).Trim()
        if ($saved) { return $saved }
    }
    if ($script:ProjectRoot.Length -gt 100) { return (Join-Path $env:USERPROFILE ".clipforge\venv") }
    return (Join-Path $script:ProjectRoot "backend\.venv")
}

function Get-VenvPython {
    return (Join-Path (Get-VenvDir) "Scripts\python.exe")
}

function Find-Tool($name) {
    $command = Get-Command $name -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($command) { return $command.Source }
    return $null
}

function Write-Ok($text)   { Write-Host "  [ok]      $text" -ForegroundColor Green }
function Write-Step($text) { Write-Host "  [doing]   $text" -ForegroundColor Cyan }
function Write-Warn($text) { Write-Host "  [note]    $text" -ForegroundColor Yellow }
function Write-Miss($text) { Write-Host "  [MISSING] $text" -ForegroundColor Red }
function Write-Fail($text) { Write-Host "  [problem] $text" -ForegroundColor Red }

function Show-Problem($text) {
    # For a start from the desktop shortcut: its window is minimised and closes when the script ends, so a
    # problem written there is never seen. This puts it in a small window instead (it closes by itself after
    # five minutes).
    try { (New-Object -ComObject WScript.Shell).Popup($text, 300, "ClipForge could not start", 48) | Out-Null } catch { }
}

function Invoke-Native($exe, [string[]]$arguments, $workingDirectory) {
    # Runs a program, shows what it prints, and returns only its exit code.
    # The output is sent to the screen explicitly (Out-Host): otherwise it would become part of this
    # function's return value and the caller could no longer tell success from failure.
    # Error text is not redirected, because PowerShell 5.1 turns redirected error text into failures.
    $previous = Get-Location
    try {
        if ($workingDirectory) { Set-Location $workingDirectory }
        & $exe @arguments | Out-Host
        return $LASTEXITCODE
    } finally {
        Set-Location $previous
    }
}

function Get-AppValue($code) {
    # Runs one line of Python inside ClipForge's environment and returns the last line it prints, or
    # $null if it failed. Used to ask the app for its own settings, so the scripts and the server always
    # read .env the same way.
    $previous = Get-Location
    try {
        Set-Location (Join-Path $script:ProjectRoot "backend")
        $output = & (Get-VenvPython) -c $code
        if ($LASTEXITCODE -ne 0) { return $null }
        return ($output | Select-Object -Last 1)
    } finally {
        Set-Location $previous
    }
}

function Get-ClipForgeProcesses {
    # The web server and worker started from this project's Python environment. The environment's
    # python.exe is a small launcher that runs the real interpreter as a child, so children are included.
    $python = Get-VenvPython
    $all = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue)
    $roots = @($all | Where-Object {
        $_.ExecutablePath -eq $python -and $_.CommandLine -match 'app\.serve|app\.main:create_app|app\.jobs\.worker'
    })
    $ids = @($roots | ForEach-Object { $_.ProcessId })
    $children = @($all | Where-Object { $ids -contains $_.ParentProcessId -and $ids -notcontains $_.ProcessId })
    return @($roots) + @($children)
}
