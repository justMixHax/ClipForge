# Setting up ClipForge on Windows

Every command below is run in PowerShell from the project folder. The scripts work with the Windows
PowerShell 5.1 that ships with Windows; nothing is changed system-wide (`-ExecutionPolicy Bypass` applies to
that one command only).

## 1. What you need first

| | Why | Install |
|---|---|---|
| Windows 10 or 11, 64-bit | | |
| FFmpeg, with `ffmpeg` and `ffprobe` on PATH | All video and audio work | `winget install Gyan.FFmpeg`, then open a new terminal |
| Python 3.12, or `uv` | The backend | [python.org](https://www.python.org/downloads/) or `winget install astral-sh.uv` |
| Node.js (22 or newer) | Builds the interface once. It also lets YouTube links be imported (Deno does the same job: `winget install DenoLand.Deno`) | [nodejs.org](https://nodejs.org/) |
| About 3 GB of free disk space | Python packages 450 MB, interface packages 130 MB, speech model 480 MB, plus your videos | |
| A reasonably modern processor and 8 GB of memory or more | Speech-to-text and encoding. Built and tested on a 6-core i5-11400 with 16 GB; nothing smaller has been tried | |
| NVIDIA graphics card | Optional. Faster video encoding works straight away; faster speech-to-text needs step 6 | |

Python 3.12 specifically: the speech and vision libraries publish ready-made builds for it. If you have `uv`,
setup uses it and `uv` fetches Python 3.12 itself if it is missing.

## 2. Set up

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
```

It is safe to run again at any time. What it does, in order:

1. Checks FFmpeg.
2. Creates the Python environment and installs the backend packages from `backend\requirements.txt`
   (downloaded from PyPI).
3. Creates `.env` from `.env.example` if you do not have one.
4. Installs a speech model. If one is already in a model cache on this PC it is copied; otherwise setup asks
   before downloading `small.en` (about 480 MB) from Hugging Face.
5. Installs the face detector (228 KB), copied if already on this PC, otherwise it asks before downloading.
6. Installs the interface packages (from npm) and builds the interface.

Options:

| Option | Effect |
|---|---|
| `-Yes` | Accept the model and face-detector downloads without being asked |
| `-Fonts` | Also fetch the open-licence caption fonts (step 5 below) |
| `-Model medium` | Install a different speech model: `tiny.en`, `base.en`, `small.en`, `small`, `medium`, `large-v3-turbo`, `large-v3` |
| `-SkipFrontend` | Do not build the interface |

It finishes with `READY`, or with `NOT READY` and a `[MISSING]` line for each thing to fix.

## 3. Start, stop

```powershell
powershell -ExecutionPolicy Bypass -File scripts\start.ps1
```

One window runs the interface, the API and the worker that does the processing, and your browser opens at
`http://localhost:3000`. Only this PC can reach it.

- **Stop:** press Ctrl+C in that window, or from another terminal run
  `powershell -ExecutionPolicy Bypass -File scripts\stop.ps1`. A job that was running goes back in the queue and
  continues the next time you start.
- `-Port 3100` uses another port for this run. `-NoBrowser` does not open the browser.
- `-Dev` is for changing the interface: it also opens a second window with the live-reloading development
  server and opens `http://localhost:5173`. Close that second window yourself when you are done.

**The database** needs no setup. `data\clipforge.db` is created the first time ClipForge starts, and
upgraded automatically whenever a newer version of the app needs a change to it.

**External APIs:** none are needed, and none are called. When creator discovery and publishing are built they
will need keys and sign-ins of your own; [ROADMAP.md](ROADMAP.md) lists exactly what, under "What needs you".
Keys will be entered in the app under Settings, where they are stored encrypted, never in a file.

## 4. Settings file

`.env` in the project folder. Every line is optional.

| Setting | Default | Meaning |
|---|---|---|
| `CLIPFORGE_HOST` | `127.0.0.1` | Address to listen on. Leave it so only this PC can reach the app |
| `CLIPFORGE_PORT` | `3000` | Port |
| `CLIPFORGE_ALLOW_REMOTE` | `false` | Must be `true` before any other address is accepted. There is no login, so only on a network you fully trust |
| `CLIPFORGE_DATA_DIR` | `data` in the project | Where videos, clips, models, the database and logs are kept. Point it at a bigger drive if you need to |
| `CLIPFORGE_MAX_UPLOAD_GB` | `20` | Largest file the browser may upload. Bigger recordings can be linked from disk instead |
| `CLIPFORGE_FFMPEG_PATH`, `CLIPFORGE_FFPROBE_PATH` | from PATH | Full paths, if FFmpeg is not on PATH |
| `CLIPFORGE_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING` or `ERROR` |
| `CLIPFORGE_SPAWN_WORKER` | `true` | Set to `false` to run the worker yourself |

Do not put API keys in this file. The app stores credentials encrypted, under Settings.

## Importing from links

Paste a link in **AI Clipper → Or paste a link**. It works for public videos on YouTube, Twitch, Kick and most
video sites, through [yt-dlp](https://github.com/yt-dlp/yt-dlp), which setup installs into ClipForge's own
Python environment. ClipForge never signs in to a site and never reads browser cookies, so private,
members-only and age-restricted videos cannot be imported: download those yourself and add the file.

Two things to know:

- **YouTube needs a JavaScript runtime on the PC**: Deno, or Node.js 22 or newer. Setup tells you which one it
  found. Other sites do not need one.
- **Sites change, and the downloader has to keep up.** When an import that used to work starts failing, update
  it and restart ClipForge:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\update-downloader.ps1
```

## 5. Optional: caption fonts

```powershell
powershell -ExecutionPolicy Bypass -File scripts\get-fonts.ps1
```

Downloads Noto Sans, Montserrat, Anton and Bebas Neue (SIL Open Font License, about 2 MB) from their official
GitHub repositories into `data\fonts`. Restart ClipForge and they appear in the editor's font list. Without
them the editor offers fonts that ship with Windows (Arial Black, Impact, Segoe UI and others). You can also
drop any `.ttf` or `.otf` file you have the right to use into `data\fonts`.

## 6. Optional: speech-to-text on the graphics card

```powershell
powershell -ExecutionPolicy Bypass -File scripts\enable-gpu.ps1
```

Installs NVIDIA's cuBLAS and cuDNN runtime libraries (about 1.3 GB, from PyPI) into ClipForge's own Python
environment. Then restart ClipForge, open **Settings → This PC** and press **Test again**. ClipForge switches to
the graphics card only if a speech model really loads and runs on it; otherwise it keeps using the processor
and shows the reason.

**Run on this PC on 22 November 2026** (RTX 3060, driver 617.42). It installed cuBLAS 12.9.2.10, cuDNN
9.28.1.4 and the CUDA runtime compiler 12.9.86 in two minutes, and the test passed. Measured with `small.en`
on the 174-second sample video:

| | Time | Speed |
|---|---|---|
| Graphics card (RTX 3060) | 3.8 s | 46× real time |
| Processor (6-core i5-11400) | 15.8 s | 11× real time |

At those rates a 3-hour stream would take roughly 4 minutes instead of 16, but nothing that long has been
run yet, so take that as an estimate. Without the script, speech-to-text simply runs on the processor. Video
encoding on the graphics card (NVENC) does not need this script.

## 7. Check that it works

```powershell
powershell -ExecutionPolicy Bypass -File scripts\test.ps1
```

Runs the fast tests (about a minute) and a type-check of the interface. See [TESTING.md](TESTING.md) for the
slow tests that use the real speech model.

## Where things are kept

| What | Where |
|---|---|
| Your videos, clips, database, models, logs, encryption key | `data\` (or `CLIPFORGE_DATA_DIR`) |
| Exported clips | `data\exports\<date>\`, or the folder you set in Settings |
| Logs | `data\logs\api.log` and `data\logs\worker.log` |
| Python environment | `backend\.venv`, or `%LOCALAPPDATA%\ClipForge\venv` when the project path is longer than 100 characters. `.venv-path` records which |

**Why the environment may live outside the project:** Windows cannot load program libraries from paths longer
than about 260 characters, even with long paths enabled, and Python packages nest deeply. A short location
avoids the problem.

**Back up** by copying the `data` folder. **Move** the project by moving the whole folder; the database stores
paths relative to `data`. Videos you linked from disk (rather than uploaded) stay where they were and must
stay there. **Uninstall** by deleting the project folder and `%LOCALAPPDATA%\ClipForge`.

## Tested with

Windows 11 Pro (build 26200), Windows PowerShell 5.1, Python 3.12.10, uv 0.13.9, Node.js 26.13 with npm 11.11,
FFmpeg 9.0.2 (gyan.dev full build), Deno 2.9.8, NVIDIA RTX 3060 12 GB with cuBLAS 12.9.2.10 and cuDNN 9.28.1.4.
Backend: FastAPI 0.165, Uvicorn 0.54, SQLAlchemy 2.1.9, Alembic 1.20.1, Pydantic 2.14, faster-whisper 1.2.1,
CTranslate2 4.9, OpenCV 5.1 (headless), NumPy 2.5.5, Pillow 13, cryptography 50, yt-dlp 2026.10.26, pytest 9.2.
Exact versions of
every package are pinned in `backend\requirements.txt`.
Interface: React 19.3, React Router 7.18, Vite 6.4, Tailwind CSS 4.3, TypeScript 5.9, TanStack Query 5.

It has been run on this one PC only. `setup.ps1` was verified there both on top of an existing installation
and creating a fresh Python environment from the pinned requirements; a first install on a completely clean
PC (no package caches) has not been tried.

## Troubleshooting

| What you see | What to do |
|---|---|
| `running scripts is disabled on this system` | Use the full command with `-ExecutionPolicy Bypass -File`, as shown above |
| `[MISSING] FFmpeg was not found` | `winget install Gyan.FFmpeg`, open a **new** terminal, run setup again |
| `Port 3000 is already in use` | ClipForge is probably already running: open `http://localhost:3000`. Otherwise `scripts\stop.ps1`, or start with `-Port 3100` |
| `The interface has not been built` | Run `scripts\setup.ps1` (Node.js is needed) |
| `ImportError` or `DLL load failed`, `The filename or extension is too long` | The Python environment is in too long a path. Delete it, delete `.venv-path`, and run setup again from a shorter project path, or let setup place it in `%LOCALAPPDATA%\ClipForge\venv` |
| A job fails with "No speech model is installed" | Settings → Speech models → Install, or `scripts\setup.ps1` |
| Settings shows `cublas64_12.dll is not found` | Normal until step 6 is done. Speech-to-text uses the processor meanwhile |
| A render fails on the graphics card | ClipForge retries that render on the processor by itself. Settings → Test again shows whether NVENC still passes |
| A job seems stuck | Stages and progress are on the AI Clipper page. Cancel it there and press Retry; finished steps (audio, transcript) are reused |
| The page says the app cannot be reached | The start window was closed. Run `scripts\start.ps1` again; nothing is lost |
| An import from a link fails with "could not be imported" | Run `scripts\update-downloader.ps1`, restart ClipForge, try again. If the message says the site asked for a sign-in, the video is not public: download it yourself and add the file |
| A YouTube link fails but other sites work | Install Deno (`winget install DenoLand.Deno`) or Node.js 22 or newer, then restart ClipForge |
| Something else | `data\logs\api.log` and `data\logs\worker.log` have the details. Error messages in the app include a reference you can search for |
