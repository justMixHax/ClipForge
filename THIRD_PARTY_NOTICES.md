# Third-party software

ClipForge's own code is under the MIT licence ([LICENSE](LICENSE)). This page is about everything else.

ClipForge does not contain any of the software below: none of it is copied into this repository. Setup
installs it on your PC from its own official source (PyPI, npm, GitHub, Hugging Face), and each part stays
under its own licence. This page lists what is installed, from where, and under which licence, as read from
each package's own metadata on 24 November 2026.

## Programs you install yourself

| What | Used for | Licence |
|---|---|---|
| [FFmpeg](https://ffmpeg.org) | Reading, cutting and encoding video and audio. Run as a separate program, never linked | LGPL 2.1 or later, or GPL depending on the build (the gyan.dev "full" build is GPL v3) |
| [Claude Code](https://claude.com/claude-code) | Optional: picks the clips, prompt edits, post text, through your own Claude plan | Anthropic's terms |
| Codex, from the ChatGPT app | Optional: the second opinion and code reviews, through your own ChatGPT plan | OpenAI's terms |
| [Deno](https://deno.com) or Node.js 22+ | Runs the small script YouTube links need, in a sandbox | MIT |

## Models downloaded on request

| What | From | Licence |
|---|---|---|
| Whisper speech models, converted for faster-whisper | The `Systran` repositories on Hugging Face | MIT |
| YuNet face detector (`face_detection_yunet_2023mar.onnx`) | The OpenCV model zoo on GitHub | MIT |
| Optional caption fonts: Noto Sans, Montserrat, Anton, Bebas Neue | Their official GitHub repositories (`scripts\get-fonts.ps1`) | SIL Open Font License 1.1 |
| Optional NVIDIA runtime libraries (cuBLAS, cuDNN) | PyPI (`scripts\enable-gpu.ps1`) | NVIDIA's licence for those libraries |

## Python packages (`backend/requirements.txt`)

The ones ClipForge uses directly:

| Package | Licence |
|---|---|
| fastapi, starlette, uvicorn | MIT · BSD-3-Clause · BSD-3-Clause |
| pydantic, pydantic-settings | MIT |
| sqlalchemy, alembic | MIT |
| httpx | BSD |
| faster-whisper, ctranslate2 | MIT |
| numpy | BSD-3-Clause (with parts under 0BSD, MIT, Zlib and CC0) |
| opencv-python-headless | Apache 2.0 |
| pillow | MIT-CMU |
| yt-dlp, yt-dlp-ejs | Unlicense (yt-dlp-ejs also MIT and ISC) |
| psutil | BSD-3-Clause |
| cryptography | Apache 2.0 or BSD-3-Clause |
| filelock | MIT |
| python-multipart | Apache 2.0 |
| pytest (tests only) | MIT |

Installed because the ones above need them:

| Package | Licence |
|---|---|
| annotated-doc, annotated-types, anyio, brotli, charset-normalizer, h11, httptools, iniconfig, mako, pluggy, pydantic-core, pyyaml, truststore, typing-inspection, urllib3, watchfiles | MIT |
| cffi | MIT-0 |
| av, click, colorama, fsspec, httpcore, httpcore2, httpx2, idna, markupsafe, protobuf, pycparser, python-dotenv, websockets | BSD |
| pygments | BSD-2-Clause |
| flatbuffers, hf-xet, huggingface-hub, opentelemetry-api, requests, tokenizers | Apache 2.0 |
| onnxruntime | MIT |
| packaging | Apache 2.0 or BSD-2-Clause |
| pycryptodomex | BSD and public domain |
| certifi | MPL 2.0 |
| tqdm | MPL 2.0 and MIT |
| typing-extensions | PSF 2.0 |
| mutagen | GPL 2.0 or later. It comes with yt-dlp's default extras and is used by yt-dlp only; ClipForge's own code does not import it |

## Interface packages (`frontend/package.json`)

| Package | Licence |
|---|---|
| react, react-dom, react-router-dom | MIT |
| @tanstack/react-query | MIT |
| lucide-react (icons) | ISC |
| vite, @vitejs/plugin-react, tailwindcss, @tailwindcss/vite (build tools) | MIT |
| typescript (build tool) | Apache 2.0 |

The exact versions, and everything those packages bring with them, are in `frontend/package-lock.json`.

## Ideas from other projects

None of their code is in ClipForge or run by it. What ClipForge does with each idea is its own code;
[docs/RESEARCH.md](docs/RESEARCH.md) lists every one.

| Project | Licence | What was learned |
|---|---|---|
| [Clipify](https://github.com/louisedesadeleer/clipify) | MIT | Subtitles with one event per spoken word, and checking the picture as well as the sound |
| [GTA 6 Clipper](https://gitlab.com/nocodehack.io/gta6-clipper) | MIT | Loudness compared with its surroundings, the rules for where a clip starts and ends, and finding a facecam from the edges that never move |
| [OpenShorts](https://github.com/mutonby/openshorts) | MIT | The "heavy tripod" camera that follows a person (`backend/app/editing/follow.py`) |

## Services ClipForge can talk to

Only when you set them up, and only through their official routes: the YouTube Data API v3 (Creator Discovery
and Shorts uploads), TikTok's Content Posting API and Instagram's Graph API. Each is used with your own key or
developer app, under that service's own terms. Using the YouTube Data API means agreeing to the
[YouTube API Services Terms of Service](https://developers.google.com/youtube/terms/api-services-terms-of-service).
