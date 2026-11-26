# Instructions for AI agents

## Setting ClipForge up for someone

Someone asked you to set up ClipForge on their PC, most likely with a message like
"Set up ClipForge for me: https://github.com/justMixHax/ClipForge". Follow these steps in order. Before each
step, say in a sentence what it does. **Ask before installing a program, accepting an agreement or downloading a
model**; everything else stays inside the project folder. Commands are Windows PowerShell 5.1.

Many agent terminals start a fresh shell for every command, so a variable or a `Set-Location` does not carry over
to the next command. Each command below is written to work on its own.

### 0. Check that you can

- ClipForge runs on **64-bit Windows 10 or 11 only**. If this PC runs anything else, stop and say so.
- You need to run commands on this PC. If you are a chat assistant without a terminal, say so, and tell the person
  to ask again in **Claude Code** or in **Codex** (in the ChatGPT desktop app).
- Do not run anything as administrator. Nothing here needs it.

### 1. Get the code

Use a short folder, because Windows cannot load Python libraries from very long paths:
`%USERPROFILE%\ClipForge`.

- If `$env:USERPROFILE\ClipForge\.git` exists and `git -C "$env:USERPROFILE\ClipForge" remote get-url origin`
  names justMixHax/ClipForge, update it: `git -C "$env:USERPROFILE\ClipForge" pull`.
- If the folder exists but is something else, do not touch it: ask the person where to put ClipForge instead.
- Otherwise:

```powershell
git clone https://github.com/justMixHax/ClipForge "$env:USERPROFILE\ClipForge"
```

If `git` is missing, ask, then install it (`winget install --id Git.Git -e`), or download the "Source code (zip)"
of the latest release from https://github.com/justMixHax/ClipForge/releases. It unpacks to a folder named like
`ClipForge-0.1.0`; move that folder's contents into `%USERPROFILE%\ClipForge`.

### 2. Check what it needs

| Needed | Check | Install, after asking |
|---|---|---|
| FFmpeg | `ffmpeg -version` works | `winget install --id Gyan.FFmpeg -e` |
| uv, which fetches Python 3.12 by itself (or Python 3.12) | `uv --version` works, or `py -3.12 --version` works | `winget install --id astral-sh.uv -e` |
| Node.js 22 or newer | `node --version` prints v22 or higher | `winget install --id OpenJS.NodeJS.LTS -e` |
| 3 GB of free disk space | `[math]::Round((Get-PSDrive C).Free / 1GB)` is 3 or more (gigabytes) | |

Notes:

- Setup needs uv **or** Python 3.12 exactly. If `py` exists but `py -3.12 --version` fails, install uv; do not
  rely on another Python version.
- The first time winget runs it may ask to accept its source agreements, which a non-interactive terminal cannot
  answer, so the command waits. Tell the person, and only with their OK add
  `--accept-source-agreements --accept-package-agreements` to the install command.
- A program winget installs is only on the PATH of new terminals. Refresh PATH **in the same command** that uses
  it (step 3 does this).

### 3. Run setup

Setup downloads the Python packages (from PyPI) and the interface packages (from npm), about 600 MB together.
It also needs the speech model `small.en` (about 480 MB, from Hugging Face) and the YuNet face detector
(228 KB, from the OpenCV model zoo on GitHub): it copies them if this PC already has them, and otherwise asks
before downloading. Your terminal cannot answer that question, so **ask the person once**, then run setup with
`-Yes`, keeping its output in a log file:

```powershell
$env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User"); Set-Location "$env:USERPROFILE\ClipForge"; powershell -NoProfile -ExecutionPolicy Bypass -File scripts\setup.ps1 -Yes *>&1 | Out-File -Encoding utf8 "$env:TEMP\clipforge-setup.log"; Get-Content "$env:TEMP\clipforge-setup.log" -Tail 25
```

- It takes several minutes. If your tool stops long commands, run it in the background and read the log when it
  ends.
- The last line is `READY`, or `NOT READY` with one `[MISSING]` line above it for each thing to fix. Fix those
  and run it again; it is safe to repeat.
- `-ExecutionPolicy Bypass` applies to that one command only. Do not change the execution policy.

### 4. Start it

Make the desktop shortcut the person will use from now on, then start ClipForge in its own minimised window
(it keeps running, so it must not run in your terminal):

```powershell
Set-Location "$env:USERPROFILE\ClipForge"; powershell -NoProfile -ExecutionPolicy Bypass -File scripts\shortcut.ps1; $start = Join-Path (Get-Location) "scripts\start.ps1"; Start-Process powershell -WindowStyle Minimized -ArgumentList "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$start`"", "-Shortcut"
```

Then wait until it answers and its worker (the process that transcribes and renders) is running. The first
start can take half a minute, so check for up to 60 seconds:

```powershell
$ok = $false; foreach ($i in 1..30) { try { $h = Invoke-RestMethod -TimeoutSec 2 http://127.0.0.1:3000/api/health; if ($h.status -eq "ok" -and $h.worker.running) { $ok = $true; break } } catch { }; Start-Sleep -Seconds 2 }; if ($ok) { "ClipForge is running" } else { "ClipForge did not answer" }
```

The browser opens at http://localhost:3000 by itself.

If it did not answer:

- A small window titled "ClipForge could not start" says why. The most common reason is another program using
  port 3000. Then start it on another port, add `-Port 3100` after `"-Shortcut"` in the start command, check
  `http://127.0.0.1:3100/api/health` instead, and tell the person the address is http://localhost:3100. To make
  that permanent, set `CLIPFORGE_PORT=3100` in the `.env` file in the project folder.
- Otherwise read `data\logs\api.log` in the project folder.

To stop ClipForge yourself: `powershell -NoProfile -ExecutionPolicy Bypass -File "$env:USERPROFILE\ClipForge\scripts\stop.ps1"`.

### 5. Claude and ChatGPT

ClipForge has no AI key of its own. It uses the command-line tools of plans the person already has:

- **Claude Code** picks the clips, makes edits described in words and writes post text. If `claude --version`
  works, ClipForge finds it by itself and uses the person's own sign-in; if you are Claude Code, it is already
  there. If it is not installed, say so; it is optional.
- **Codex** (the ChatGPT desktop app) gives a second opinion on Claude's picks. It is found by itself when it is
  installed and signed in.
- **With neither**, ClipForge's own rules pick the clips, and nothing is sent anywhere.

Never ask for, store or type an API key or a password. Publishing accounts and the YouTube key for Creator
Discovery are added by the person, inside the app, when they want them.

### 6. Hand over

Tell the person, in a few lines:

- ClipForge is open at http://localhost:3000 (or the port you used). From now on, double-click **ClipForge** on
  the desktop.
- To begin: **AI Clipper**, then add a video file or paste a link to a public video.
- **Settings → What works, and what does not yet** shows what this PC can do, including whether Claude was found.
- To stop it: **Shut down ClipForge** at the bottom of the menu.

Report honestly: if a step failed or was skipped, say which and why.

### If something goes wrong

The table under "Troubleshooting" in [SETUP_WINDOWS.md](SETUP_WINDOWS.md) covers the usual problems. The logs are
in `data\logs\api.log` and `data\logs\worker.log` in the project folder. SETUP_WINDOWS.md is also the full manual
for everything above.

## Working on the code

Read [CONTRIBUTING.md](CONTRIBUTING.md) first. The tests: `powershell -ExecutionPolicy Bypass -File scripts\test.ps1`
runs the fast backend suite and type-checks the interface; [TESTING.md](TESTING.md) has the rest.
