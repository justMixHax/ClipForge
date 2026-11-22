# ClipForge AI

A personal clipping studio that runs on your own Windows PC. You give it a long video; it transcribes it,
finds the moments most likely to work as short clips, explains why it picked each one, and renders them as
vertical videos with burned-in captions. You review, edit, approve and download them in the browser at
`http://localhost:3000`.

Everything essential runs locally with open-source software: no subscription, no account, and nothing is
uploaded anywhere. The foundation, the AI clipping and most of the editing (milestones 1, 2 and most of 3 in
[ROADMAP.md](ROADMAP.md)) are built and tested. Publishing, discovery and analytics are not built yet, and the
app says so wherever they would appear.

## Quick start

You need 64-bit Windows 10 or 11, [FFmpeg](https://www.gyan.dev/ffmpeg/builds/) (`winget install Gyan.FFmpeg`),
Python 3.12 (or [uv](https://docs.astral.sh/uv/)) and Node.js. Then, from the project folder:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
```

```powershell
powershell -ExecutionPolicy Bypass -File scripts\start.ps1
```

The second command opens `http://localhost:3000`. Press Ctrl+C in that window to stop, or run
`scripts\stop.ps1` from anywhere. Details, options and troubleshooting are in
[SETUP_WINDOWS.md](SETUP_WINDOWS.md).

## Using it

1. **AI Clipper → 1 · Source video.** Three ways to bring a video in:
   - drop a file (MP4, MOV, MKV, WebM, M4V) or choose one;
   - **paste a link** to a public video (YouTube, Twitch and Kick recordings, and most video sites), press
     **Check link**, say why you may clip it, and press **Import video**;
   - paste the path of a recording that is already on this PC (it is linked, not copied).

   Record who made it and whether you are allowed to clip it, and put it in a project if you want to group
   your work.
2. **2 · How to clip it.** Pick a preset (Fast Gaming Clips, Funny Streamer Moments, Podcast Highlights, ...)
   or set the kind of content, game, number of clips, clip length, minimum score, shape, layout and caption
   style yourself. Press **Generate Clips**.
3. **Watch the stages.** Extracting audio → transcribing → measuring sound and picture → finding the best
   moments → rendering. You can cancel; if the app is closed mid-job it continues next time.
4. **Review.** Each suggestion shows a 0–100 score, a confidence level, the reasons, the evidence
   (what was said, how the voice changed, where the payoff lands) and a breakdown of twelve sub-scores.
   Approve, reject, favourite, reorder, or tell it what was wrong (the feedback shifts future ranking).
5. **Edit.** Trim on a waveform timeline, fix any caption word, change the caption style, font, colours,
   layout, shape and hook title, with undo and redo. Save and render again.
6. **Download or export.** Download the MP4, SRT or VTT, or select clips in the Clip Library and export them
   to a folder with a text file holding the title, description, hashtags, source and rights note.
7. **Clip a moment yourself.** Found a good moment while watching? Under **3 · Clip a moment yourself**, type
   where it starts (`1:02:15`, `62:15`, or paste a YouTube link copied at that moment), or press **Watch and
   mark** and use **Start here** and **End here** in the player. Leave the end empty and Claude ends it right
   after the payoff. The same work as for the AI's picks follows: title, hook, score, ChatGPT's verdict,
   captions, render and checks. Your start is kept; only a start in the middle of a word moves to its edge.
8. **Publish.** In the **Publishing Studio**, set up each platform once under **Accounts** (your own free
   developer app, then the platform's own sign-in; the page walks you through it). Then **New post**: pick
   approved clips, accounts, the text (Claude can suggest it) and now or a time. The **Calendar** shows what
   goes out when. A post reads Published only once the platform has confirmed it.

## What works, and what does not

Checked on this PC on 22 November 2026. The same list, live, is in the app under **Settings → What works,
and what does not yet**.

| | |
|---|---|
| **Working and tested** | Upload and link-from-disk · import from a link (public videos) · projects · **clips from your own timestamps** · **Claude picks the clips** (through the Claude Code tool and your own Claude plan, no API key; local rules as fallback) · **ChatGPT checks the picks** and may add missed ones (through the Codex tool and your own ChatGPT plan) · a **work log** after every job saying what Forge, Claude and ChatGPT each did · local speech-to-text with word timings · highlight discovery with 12-part explainable scoring, plus loud and busy moments from stretches where almost nothing is said · content presets and game vocabularies (GTA, DayZ, EA Sports FC, Roblox) · review, ranking, feedback · clip editor · renders in 9:16, 1:1, 4:5 and 16:9 with four layouts · six caption styles plus your own, SRT and VTT · volume levelling, noise reduction, pause tightening · export quality checks · search inside transcripts · duplicate detection · A/B variants · export with post text · job queue with cancel, retry and resume · hardware detection · GPU video encoding (NVENC) · speech-to-text on the graphics card (about 4× faster than the processor here) |
| **Working, with a caveat** | Face-aware reframing and facecam detection: checked only on synthetic test footage so far, and the crop stays fixed for the whole clip. Look at the framing on real footage before relying on it. |
| **Needs setup** | Nothing on this PC. On another PC with an NVIDIA card, speech-to-text on the graphics card needs `scripts\enable-gpu.ps1` run once (about 1.3 GB); until then it runs on the processor. |
| **Waiting for your accounts** | **Publishing**: YouTube Shorts (upload and schedule), TikTok (to your drafts), Instagram Reels, with a queue, calendar, safeguards and Claude's post text. Built and tested against stand-ins of each platform's API made from its documentation; each needs your own developer app and sign-in before the first real post (Publishing Studio → Accounts). |
| **Blocked by a third party** | TikTok direct posting: TikTok's rules for it exclude tools for posting to your own accounts. Drafts work instead. |
| **Planned, not built** | Creator discovery and watchlists · analytics · AI strategist · automation rules · who-is-speaking detection · crops that follow a moving subject · saving your own presets |

The full plan is in [ROADMAP.md](ROADMAP.md).

## What the score means

The score is an estimate of engagement potential made by rules you can read, not a prediction of views.
Twelve things are scored from 0 to 100 and combined with weights that depend on the content preset: opening
hook, entertainment, emotional impact, clarity without context, visual interest, pacing, payoff,
shareability, audience relevance, subtitle readability, originality and repetition.

The signals are the transcript (what is said, how sentences begin and end), loudness compared with the
surrounding minutes, pauses, speaking speed, picture activity and scene cuts. A loud moment on its own does
not rank highly: loudness can lift only two of the twelve parts, and only so far. Suggestions start and end
on sentence boundaries, and titles and hook lines only use words that were actually said.

Where almost nothing is said (gameplay between bits of commentary, action scenes), the loud and busy moments
are offered as well. They only fill places the spoken moments left free, never cut a sentence in half, and
are marked "Low confidence" with the note that they rest on sound and picture only.

Confidence is capped at 74% because no language model reviews the candidates yet, and the rules have been
tuned on a synthetic test video and unit tests, not on a large set of real streams. Expect to disagree with
it sometimes; that is what the feedback buttons and manual ranking are for.

## Where things are

```
backend/     Python API, worker, analysis and rendering (FastAPI, SQLAlchemy, faster-whisper, FFmpeg)
frontend/    Browser interface (React, TypeScript, Vite, Tailwind)
scripts/     setup, start, stop, test, optional fonts, optional GPU libraries, sample video builder
data/        Everything the app creates: your videos, clips, the database, models, logs (not in Git)
samples/     Synthetic sample video and the script it was built from, used by the slow tests
docs/        Research notes on the three reference projects
```

Moving the project folder moves everything, because the database stores paths relative to `data/`. The one
exception is the Python environment, which lives in `%LOCALAPPDATA%\ClipForge\venv` when the project path is
long (Windows cannot load libraries from very long paths); `.venv-path` records where it is.

## Documentation

- [SETUP_WINDOWS.md](SETUP_WINDOWS.md): installing, starting, stopping, options, troubleshooting
- [ARCHITECTURE.md](ARCHITECTURE.md): how the parts fit together and why
- [TESTING.md](TESTING.md): what is tested, how to run it, and what is not covered
- [SECURITY.md](SECURITY.md): what protects your PC, your files and your future credentials
- [ROADMAP.md](ROADMAP.md): milestones, what is next, known limitations
- [docs/RESEARCH.md](docs/RESEARCH.md): what was learned from clipping.net, GTA 6 Clipper and Clipify

## Clipping other people's videos

A video being public does not make it yours to repost. ClipForge records a rights status for every source
(your own, licensed or campaign, creator gave permission, not allowed, not recorded) and writes it into the
post text of every clip. Credit does not replace permission.

Importing from a link works for public videos only. ClipForge never signs in to a site, never reads your
browser's cookies, and does not work around age, member or region restrictions. An import does not start
until you have said why you may clip that video, and your answer is stored with it.

## Third-party software

ClipForge is built on other people's work, used under their licences:
faster-whisper and CTranslate2 (MIT) with OpenAI's Whisper models (MIT) · OpenCV (Apache 2.0) and the YuNet
face detector (MIT) · FFmpeg (the gyan.dev full build is GPL v3; it is run as a separate program, not linked) ·
FastAPI, SQLAlchemy, Alembic, Pydantic (MIT), Starlette and Uvicorn (BSD) · NumPy and psutil (BSD), Pillow
(MIT-CMU), cryptography (Apache 2.0 / BSD) · React, React Router, Vite, Tailwind CSS, TanStack Query (MIT),
Lucide icons (ISC) · yt-dlp for importing from links (Unlicense) · optional fonts Noto Sans, Montserrat, Anton
and Bebas Neue (SIL Open Font License).

Ideas were adapted from two MIT-licensed projects, [Clipify](https://github.com/louisedesadeleer/clipify)
and [GTA 6 Clipper](https://gitlab.com/nocodehack.io/gta6-clipper); their code was read and re-implemented,
not copied or executed.
