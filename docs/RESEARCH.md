# Research: what ClipForge takes from the three references

Reviewed on 2026-11-21. Nothing from these projects was installed or executed; the code was read, and the
techniques below were re-implemented for ClipForge. Both code projects are MIT licensed, so re-implementing
their ideas with credit is allowed. clipping.net is a commercial product: only workflow concepts were studied,
none of its interface or branding is copied.

## 1. Clipify (github.com/louisedesadeleer/clipify, MIT)

A Claude Code skill (4 small Python scripts plus a prompt) for talking-head video on macOS.

| Useful | How ClipForge uses it |
|---|---|
| Whisper word timestamps drive everything | Same, with faster-whisper instead of openai-whisper (several times faster, runs on CPU or CUDA) |
| ASS subtitles with one event per "active word" state | Same idea in `editing/captions.py`, extended to 6 presets, emphasis words, auto-fit and safe zones |
| Hard-cut pan built as a nested `if(lt(t,..))` crop expression | Kept as the planned technique for speaker-following pans (not built yet, see ROADMAP) |
| Motion energy inside a face box decides who is speaking | Planned for split-screen speaker switching |
| "Audio detection is not a visible event: verify the frame" | ClipForge scores visual activity separately and runs black-frame checks after render |
| Scene-cut warning before reframing | Scene cuts are detected during analysis and stored with the video |

Built differently: Clipify asks a human to eyeball face rectangles and relies on the chat model to pick
moments. ClipForge detects faces automatically and has its own scoring engine, so it works with no AI
subscription. Clipify's macOS-only `-hwaccel videotoolbox` flags are not used; NVENC is used when it is
detected and passes a real test encode.

## 2. GTA 6 Clipper (gitlab.com/nocodehack.io/gta6-clipper, MIT; guide.nocodehack.io/gta6-clipper)

The Windows edition is already installed on this PC, so the actual script (`gta6clip.py`) was read.

| Useful | How ClipForge uses it |
|---|---|
| Loudness per second compared with a rolling local median (not an absolute level) | `analysis/audio.py` uses the same relative idea at 10 Hz, so a loud stream does not flag everything |
| "Signals help, they don't decide": loud with nothing said is not a clip | Core rule of the scorer: loudness can only raise 2 of 12 sub-scores, and is capped |
| Picking rules: start on the hook, end right after the payoff, drop anything needing context, drop filler openers | Encoded as the hook, clarity and payoff sub-scores and the boundary rules |
| Snap picks to sentence edges, minimum spacing between clips | `analysis/candidates.py` and `analysis/dedupe.py` |
| Facecam finder: a small face that stays in one spot, then box edges from static straight lines | Re-implemented in `editing/reframe.py` with the same YuNet detector |
| Three layouts: facecam over gameplay, face-centred crop, blurred fill | Same three, plus aspect ratios 1:1, 4:5 and 16:9 |
| Cut with `trim`/`atrim` on one timeline, not input `-ss` alone, to avoid audio/video offset | Used in `editing/render.py` |
| `loudnorm=I=-14:TP=-1.5:LRA=11`, libx264 veryfast crf 20, `+faststart` | Same audio target; encoder chosen from tested hardware |
| Post note with title, caption, hashtags, source link and timestamp | Every clip gets a post kit `.txt` |
| Transcribe long audio in windows so 9-hour streams fit in memory | Same, with a checkpoint per window so an interrupted job resumes |
| Per-streamer "is clipping welcome" note (guide, v1.0.15) | Each source stores a permission status, its basis and an attribution line |
| Swear masking on burned-in captions | Optional toggle, on by default; transcripts and SRT stay verbatim |

Built differently: the skill needs a chat session to do the picking and downloads sections with yt-dlp
(including browser-cookie access for age-restricted streams). ClipForge is a standalone app with a database,
queue and UI, picks moments itself, and does not read browser cookies. (Importing from a link was added
later, also with yt-dlp, for public videos only and without cookies or sign-ins.) Captions are ASS text, not PNG frames,
so they stay editable and render faster.

## 3. clipping.net

A two-sided marketplace: brands fund campaigns, clippers link the accounts they post from, paste each clip's
link, and are paid per verified view.

Concepts adopted:
- **Views come from the platform, not from the clipper.** ClipForge only shows metrics that came from a
  connected account or are labelled as typed in by hand.
- **Campaigns state the licence up front.** A ClipForge source records why clipping it is allowed
  (own content, campaign licence, creator permission, unknown).
- **A clipper dashboard is organised around accounts, posted clips and their numbers.** That is the model for
  the Publishing and Analytics areas.

Not adopted: payouts, campaign marketplace, Discord sign-in. ClipForge is single-user and local.

## Decisions for this PC

| Area | Choice | Why |
|---|---|---|
| Speech to text | faster-whisper 1.2 (CTranslate2) | Already proven on this PC; `small.en` model already on disk, so no download |
| Face detection | OpenCV YuNet (ONNX, 228 KB) | Already on disk; far better than Haar cascades on small facecams |
| Video | FFmpeg 9 (gyan.dev full build) | Installed; has libass, loudnorm, NVENC |
| Highlight scoring | Own multi-signal engine. A language-model review pass is planned, not built | Works offline with no subscription; Ollama is not installed here |
| Backend | Python 3.12, FastAPI, SQLAlchemy, Alembic, SQLite | Python 3.14 is the default here but 3.12 has the widest wheel support for the AI libraries |
| Queue | SQLite-backed job table and a separate worker process | Survives restarts with no Redis or Docker (Docker is not installed) |
| Frontend | React, TypeScript, Vite, Tailwind | As requested |
| GPU | RTX 3060 detected. NVENC available in FFmpeg. CUDA libraries for Whisper are not installed | The app tests each before using it and falls back to CPU |
