<#
.SYNOPSIS
  Has ChatGPT (through the Codex tool from the ChatGPT app) review the code changes, and saves the review.

.DESCRIPTION
  By default it reviews the changes that are not committed yet. With -Base it reviews the current branch
  against another branch instead, and with -Commit the changes one commit made. The review uses your own ChatGPT sign-in, never an API key, and is saved
  in the reviews folder with the date, so it stays next to the change in Git.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\review.ps1
  powershell -ExecutionPolicy Bypass -File scripts\review.ps1 -Base main
  powershell -ExecutionPolicy Bypass -File scripts\review.ps1 -Commit 010cba1
#>
param([string]$Base = "", [string]$Commit = "", [string]$Model = "gpt-5.6-terra")
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "common.ps1")

$codex = Find-Tool "codex"
if (-not $codex) {
    $copies = Get-ChildItem (Join-Path $env:LOCALAPPDATA "OpenAI\Codex\bin") -Filter codex.exe -Recurse -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending
    if ($copies) { $codex = $copies[0].FullName }
}
if (-not $codex) { Write-Miss "The Codex tool from the ChatGPT app was not found. Install the ChatGPT (Codex) app and sign in."; exit 1 }
if (-not (Test-Path (Join-Path $ProjectRoot ".git"))) { Write-Miss "This folder is not a Git repository, so there are no changes to review."; exit 1 }

# Codex settings that could send the review somewhere other than OpenAI through your ChatGPT sign-in
$codexHome = if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $env:USERPROFILE ".codex" }
$config = Join-Path $codexHome "config.toml"
if (Test-Path $config) {
    $redirects = Select-String -Path $config -Pattern '^\s*(openai_base_url|chatgpt_base_url)\s*=|^\s*model_provider\s*=\s*"(?!openai")|^\s*\[model_providers' -ErrorAction SilentlyContinue
    if ($redirects) {
        Write-Miss "Your Codex settings ($config) send requests to another provider or address, so nothing was sent:"
        $redirects | ForEach-Object { Write-Host "            line $($_.LineNumber): $($_.Line.Trim())" }
        exit 1
    }
}

# Your ChatGPT sign-in only: never an API key. Removed for this run only and put back afterwards.
$removed = @{}
foreach ($item in @(Get-ChildItem Env: | Where-Object { $_.Name -match '^(OPENAI_|AZURE_OPENAI|CODEX_API)' })) {
    $removed[$item.Name] = $item.Value
    Remove-Item "Env:$($item.Name)" -ErrorAction SilentlyContinue
}
$code = 1
try {
    $ErrorActionPreference = "Continue"
    $status = (& $codex login status 2>&1 | ForEach-Object { "$_" }) -join " "
    $ErrorActionPreference = "Stop"
    if ($status -notmatch 'using ChatGPT') {
        if ($status -match 'API key') { Write-Miss "Codex is signed in with an API key, not your ChatGPT plan, so nothing was sent. Run  codex logout  and sign in to the ChatGPT app." }
        else { Write-Miss "Codex is not signed in with ChatGPT, so nothing was sent. Open the ChatGPT app and sign in." }
        exit 1
    }

    $reviews = Join-Path $ProjectRoot "reviews"
    New-Item -ItemType Directory -Force -Path $reviews | Out-Null
    $file = Join-Path $reviews ("{0}-codex-review.md" -f (Get-Date -Format "yyyy-MM-dd-HHmm"))
    $diagnostics = Join-Path $env:TEMP ("clipforge-review-{0}.log" -f $PID)
    $arguments = @("review", "-c", "model_provider=`"openai`"", "-c", "web_search=`"disabled`"", "-c", "model=`"$Model`"", "-c", "model_reasoning_effort=`"high`"")
    if ($Commit) {
        if ($Commit -notmatch '^[0-9a-fA-F]{7,40}$') { Write-Miss "-Commit takes a commit id (7 to 40 hexadecimal characters)."; exit 1 }
        $arguments += @("--commit", $Commit); $what = "commit $Commit"
    } elseif ($Base) { $arguments += @("--base", $Base); $what = "this branch against $Base" } else { $arguments += "--uncommitted"; $what = "the changes not committed yet" }

    Write-Step "ChatGPT ($Model) is reviewing $what. This can take a few minutes."
    Push-Location $ProjectRoot
    try {
        # The final review comes on the output stream. Codex's working trace (commands, file excerpts) goes to
        # the error stream: it is kept aside and shown only if the review fails.
        $ErrorActionPreference = "Continue"
        $output = & $codex @arguments 2> $diagnostics | ForEach-Object { "$_" }
        $code = $LASTEXITCODE
        $ErrorActionPreference = "Stop"
    } finally {
        Pop-Location
    }
    if ($code -ne 0) {
        Write-Miss "The review did not finish (exit code $code). Codex said:"
        Get-Content $diagnostics -Tail 20 -ErrorAction SilentlyContinue | Out-Host
        exit $code
    }
    $header = "# Code review by ChatGPT ($Model) through Codex`n`nReviewed: $what, on $(Get-Date -Format 'yyyy-MM-dd HH:mm').`n`n"
    # The review names files by their full path on this PC. Saved, they are named from the project folder, so
    # a review that ends up in Git does not carry your user name and folder layout with it.
    $text = ($output | Out-String).Trim()
    foreach ($root in @($ProjectRoot, $ProjectRoot.Replace("\", "/"))) {
        foreach ($slash in @("\", "/")) { $text = $text.Replace($root + $slash, "") }
    }
    [IO.File]::WriteAllText($file, $header + $text + "`n")
    $output | Select-Object -Last 60 | Out-Host
    Write-Ok "Review saved to $file"
} finally {
    foreach ($name in $removed.Keys) { Set-Item "Env:$name" $removed[$name] }
    if ($diagnostics) { Remove-Item $diagnostics -Force -ErrorAction SilentlyContinue }
}
exit $code
