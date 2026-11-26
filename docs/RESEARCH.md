# Research: what ClipForge takes from its references

The first three were reviewed on 2026-11-21, before anything was built. Nothing from them was installed or
executed for ClipForge; the code was read, and the techniques below were re-implemented. The code projects are
MIT licensed, so re-implementing their ideas with credit is allowed. clipping.net is a commercial product:
only workflow concepts were studied, none of its interface or branding is copied. A fourth project, OpenShorts,
was looked at on 2026-11-24 (section 4).

This page is a record of those dates. Where it says something is planned, [ROADMAP.md](../ROADMAP.md) says
what has been built since.

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

## 4. OpenShorts (github.com/mutonby/openshorts, MIT)

Looked at on 2026-11-24. Unlike the three above, a copy was set up on the author's PC, in Docker, to try it
next to ClipForge; it was removed again afterwards, together with Docker. Its source was then read for ideas,
and what was taken was written again for ClipForge. None of its code is in ClipForge or run by it.

| Useful | How ClipForge uses it |
|---|---|
| A camera that behaves like a heavy tripod: it stays put while the person is near the middle, and moves only once they have really moved | `editing/follow.py`: **Follow the person** for the cropped layout. A move must be seen three times in a row, and FFmpeg moves the crop inside the same render |
| Telling the user when a link only offers a poor picture | The link check warns before anything is downloaded |
| A short zoom push on the big moments | Noted in the roadmap, not built |
| Asking once per video whether the source already has subtitles burned in | Noted in the roadmap, not built |
| What Twitch viewers clipped, as a hint for the picker | Noted in the roadmap, not built. The same thought led to Creator Discovery's hints from YouTube comments |
| Three or four people tiled from one wide shot | Noted in the roadmap, not built |

Built differently: OpenShorts picks its moments with Google's Gemini through an API key and is set up with
Docker. ClipForge uses the Claude plan you already have, through the Claude Code tool, with its own rules as
the fallback, and installs with one script and no extra services.

## 5. "If Your TikToks Get Stuck Under 1,000 Views... Do This!" (Taro Creates, YouTube, 4 February 2025)

An 11-minute video the project owner asked ClipForge to learn from, watched on 2026-11-25
(youtube.com/watch?v=RZIkNL7qHLo). It is one creator's account of what gets short videos past a few hundred
views, not platform documentation: nothing in it is verified by TikTok, and ClipForge states no claim of its
own about how any platform ranks content. What it says, and what ClipForge does about it:

| What the video says | What ClipForge does |
|---|---|
| Watch time and engagement decide how far a short video travels | The whole ranking aims at this: twelve sub-scores, led by the opening hook and the payoff |
| The first 5 seconds are the hook, and above all a **visual** one that stops the scroll | The hook score counted words and loudness only. It now also counts whether the picture moves or cuts in the first 3 seconds, and marks down a still opening (`scoring.py`) |
| No boring stretches; keep the clips changing | Pacing penalises dead air over 1.8 s; the editor cuts parts out of the middle |
| Give people something to comment on, without faking it | Claude is now asked, where the clip holds a claim or a choice people would argue with, to end the caption with the question they would answer (`claude_review.py`, `assistant.py`) |
| Hashtags must be specific; general ones like #fyp and #viral send the clip to everyone instead of the people who would watch it | `#fyp` is no longer added to any clip. The hashtags go specific first: game, creator, category, then the preset's (`describe.py`) |
| Tag the account of the person in the clip | The credit line now uses the creator's `@handle` when the site gives one, which YouTube does (`describe.py`, `downloader.py`) |
| Post from a phone, not a PC | ClipForge already sends TikTok posts to the app's drafts, which you finish on your phone |
| Leave at least 3 hours between posts, so the platform can judge one before the next | New: two posts on one account are refused closer than the gap in Settings, 3 hours by default (`publishing/service.py`) |
| Say what region and language you are posting for | Not built. ClipForge writes in the language of the clip and makes no claim about where a post is shown |

Deliberately not taken from it:

| What the video says | Why not |
|---|---|
| Strip a downloaded clip's metadata so the platform cannot tell it was reposted | Works around a platform's own detection. ClipForge records where a clip came from and whether you may post it, and keeps that record |
| Use a proxy to post as if from another country | The same reason, and outside what a local clipping tool should do |
| "Warm up" a new account by following and liking before posting | Advice about running an account, not about making clips |

Its main point, that watch time and engagement are what matter, needs numbers ClipForge does not have yet:
real figures from your connected accounts. That is Milestone 6 in the roadmap and is not started.

## Decisions for the first PC

As decided on 2026-11-21:

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
