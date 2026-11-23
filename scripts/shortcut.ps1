<#
.SYNOPSIS
  Puts a ClipForge shortcut on your desktop: double-click it to start ClipForge and open it in the browser.

.DESCRIPTION
  The shortcut runs scripts\start.ps1 in a minimised window. That window is ClipForge running: the Shut down
  button in the app (bottom of the menu) stops it and closes the window. If ClipForge is already running, the
  shortcut just opens it in the browser. Running this script again replaces the shortcut.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\shortcut.ps1
#>
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "common.ps1")

$desktop = [Environment]::GetFolderPath("Desktop")
$link = Join-Path $desktop "ClipForge.lnk"
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($link)
$shortcut.TargetPath = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
$shortcut.Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$(Join-Path $ProjectRoot 'scripts\start.ps1')`""
$shortcut.WorkingDirectory = $ProjectRoot
$shortcut.Description = "Start ClipForge and open it in the browser"
$shortcut.IconLocation = (Join-Path $ProjectRoot "scripts\clipforge.ico") + ",0"
$shortcut.WindowStyle = 7  # minimised: the window is ClipForge running
$shortcut.Save()
Write-Ok "Shortcut made on your desktop: $link"
