# Architecture

ClipForge is a single-user application that runs entirely on one Windows PC. This document explains how the
parts fit together, what happens when you press **Generate Clips**, and why it was built this way.

## The parts

```
 Browser  http://localhost:3000
    │  HTTP (JSON, video streams)
    ▼
 Web server process            python -m app.serve        (FastAPI on Uvicorn)
    │  serves the built interface from frontend/dist and the API under /api
    │  starts and watches ▼
 Worker process                python -m app.jobs.worker
    │  takes one job at a time from the queue
    ├── faster-whisper (speech-to-text, in process)
    ├── OpenCV YuNet   (face detection, in process)
    └── FFmpeg / ffprobe (separate programs: decode, analyse, render)

 Both processes share:  data/clipforge.db (SQLite)   and   the data/ folder
```

- **Two processes, on purpose.** Transcription and rendering are heavy and run native code. Keeping them out
  of the web server means the interface stays responsive during a render, and a crash inside a media library
  cannot take the app down. The web server restarts the worker if it exits.
- **SQLite is the queue.** Jobs are rows in the `jobs` table. There is no Redis, no Docker and no extra
  service to install, and the queue survives a restart because it is just the database. SQLite runs in WAL
  mode with a busy timeout, so the two processes do not block each other.
- **One job at a time.** A single worker keeps the graphics card and processor from being oversubscribed.
  Manual render requests jump the queue by priority.
- **Nothing leaves the PC.** The only network activity is what you start yourself: importing a video from a
  link, installing a speech model
  from Settings, and the setup and font scripts.

## What happens when you press Generate Clips

`POST /api/videos/{id}/analyze` writes an `analyze` job and returns. The worker claims it and runs these
stages, reporting progress that the interface polls:

| Stage | What it does | Module |
|---|---|---|
| Extracting audio | FFmpeg writes 16 kHz mono audio to `data/work/<video>/audio.wav` | `services/ffmpeg.py` |
| Transcribing speech | faster-whisper in 5-minute windows cut at quiet points, word timestamps, a checkpoint after each window. Word times are then corrected against the loudness of the audio | `transcription/` |
| Measuring sound and picture | Loudness against a rolling ±60 s median, pauses, loud events; picture motion, brightness, scene cuts | `analysis/audio.py`, `analysis/visual.py` |
| Finding the best moments | See "The highlight engine" below | `analysis/engine.py` |
| Saving suggestions | Clips are stored with score, sub-scores, confidence, explanation, evidence, title, hooks, hashtags | `jobs/pipeline.py` |

If rendering straight away is on, one `render` job per clip is queued:

| Stage | What it does | Module |
|---|---|---|
| Framing the shot | Samples frames, finds faces, decides the layout and the crop | `editing/reframe.py` |
| Building captions | Words for the clip → styled ASS subtitle script, fitted to the frame; subtitles and the hook title are switched on and off separately | `editing/captions.py`, `editing/fonts.py` |
| Rendering video | One FFmpeg run: cut (without the parts left out), reframe, burn captions, level the audio, encode | `editing/render.py` |
| Checking the export | Playable, right size and length, has sound, no black or silent stretch, captions inside the frame, no word cut in half | `editing/qc.py` |
| Making the thumbnail | Scores 16 frames of the clip (sharp, lit, contrast, a clear face), frames the best one like the clip without captions, and puts the words on it where they cover no face, in a font that has every character (words in scripts that need shaping are left off). Never a frame from a part left out. A thumbnail that cannot be made never fails the render | `editing/thumbnail.py` |

Every expensive result is cached per video in `data/work/<video>/` (audio, transcript, signals). Analysing the
same video again with different options skips straight to scoring.

## Importing from a link

`POST /api/videos/inspect-url` looks at a link without downloading anything (title, creator, length, size at
the chosen quality). `POST /api/videos/import-url` then queues an `import` job, but only once the request
states why the video may be clipped.

| Stage | What it does | Module |
|---|---|---|
| Checking the link | yt-dlp resolves the one video behind the link. Playlists, channels and streams that are still live are refused; so is anything over the size limit or bigger than the free disk space | `services/downloader.py` |
| Downloading the video | The largest picture up to the chosen height, preferring H.264 and AAC, joined into an MP4 in `data/uploads/<key>/`. Progress, cancel, and resume after a restart work as for other jobs | `services/downloader.py` |
| Adding it to your library | The same step uploads use: probe, thumbnail, duplicate check. The source link, creator and rights answer are stored with the video | `services/library.py` |

If asked to, the job queues the analysis itself when the video has arrived. yt-dlp runs inside the worker with
plugins, configuration files, cookies and sign-ins switched off; see [SECURITY.md](SECURITY.md).

## Claude picks the clips

By default the local engine below runs first and its picks are kept as the fallback. Then
`analysis/claude_review.py` sends Claude the timed transcript, the loud and busy moments, the clip count and
length, and the rules (stand-alone, start at a line, end after the payoff, never invent words). It runs the
Claude Code command-line tool (`claude -p`) from an empty folder, with no tools of its own and no MCP servers,
so it uses the user's own plan and sign-in and needs no API key. Claude answers with JSON picks; each is
widened to whole sentences, de-duplicated, given the local 12-part breakdown for reference, and saved with
Claude's title, hook, score and reason (`analysis_source = "claude"`), plus the words for its thumbnail (and
which one to colour), a TikTok caption and hashtags, all cleaned before use: at most 40 characters without
emoji on the thumbnail, a caption of one line without hashtags, and at most eight safe hashtags. If the tool is missing, signed out or
over the plan's limit, the local picks are saved and the job says why.

**Reading an AI's answer** (`analysis/answers.py`, used for Claude's picks, ChatGPT's opinion, the post text and
the prompt edit): every JSON block in the answer is found, the last one first, because a correction comes after
its draft; a block that does not read as written is tried once more with clock times (`28:35`) quoted, and times
are read as seconds or as a clock. Both were seen for real: on two long streams Claude wrote a draft, then
"Correction, here is the real answer", or times as a clock, and its picks were lost to the local rules.

**ChatGPT's second opinion** (`analysis/chatgpt_review.py`): ChatGPT then gets the same transcript and the
numbered picks, and answers "agree" or "disagree" with a reason for each, plus at most two missed moments.
Nothing is removed: a disputed clip stays, marked "ChatGPT disagrees". It runs `codex exec` read-only, with
its shell, file, browser, app and plugin tools switched off, the user's Codex settings ignored and OpenAI's
own provider forced, and only after `codex login status` reports a ChatGPT sign-in (never an API key). With
"Local rules only" chosen it is skipped, so nothing leaves the PC.

**The work log**: every job collects short lines saying who did what (`ctx.say("forge" | "claude" |
"chatgpt", text)`) and stores them in `result.log`, also when a job fails. The AI Clipper shows them per
video ("Work log"), and the Dashboard under each job.

## The highlight engine

This is the core of the product, and it is deliberately made of rules you can read and test rather than a
model nobody can inspect. It lives in `backend/app/analysis/`.

1. **Sentences** (`transcription/sentences.py`). Words become sentences using punctuation, with fallbacks for
   speech-to-text output that has none.
2. **Candidate windows** (`candidates.py`). Every run of whole sentences whose length fits the requested range
   is a candidate. A clip therefore never starts or ends in the middle of a sentence.
3. **Scoring** (`scoring.py`). Each candidate gets twelve scores from 0 to 100:

   | Sub-score | Looks at |
   |---|---|
   | Opening hook | The first line: questions, strong claims, direct address; penalised for filler openings and replies that need the previous line |
   | Entertainment | Humour, surprise, conflict and reaction cues in the words, backed by voice energy |
   | Emotional impact | Emotional language and how far the voice rises above the surrounding minutes |
   | Clear without context | Starts at the start of a thought, no dangling "and then he...", no change of subject midway |
   | Visual interest | Picture activity and scene cuts during the window |
   | Pacing | Speaking speed, share of silence, long gaps |
   | Payoff | Whether there is a punchline or resolution, where it lands, and whether the clip ends soon after it |
   | Shareability | A quotable line, strong opinions, humour or surprise, speaking to the viewer, and a length that suits short video |
   | Audience relevance | Your niche keywords and the vocabulary of the chosen game |
   | Subtitle readability | Speaking speed, and how clearly the words were recognised |
   | Originality | Distinctive wording compared with the rest of the video |
   | Not repetitive | How similar it is to the suggestions ranked above it |

   Rules that matter:
   - **Loudness is relative.** A shout counts only against the level of the minutes around it, so a stream
     that is loud throughout does not flag everything.
   - **Loud is not enough.** Loudness with nothing said can lift only the emotion and entertainment scores,
     and both are capped at 50 when the words do not support them.
   - **A payoff needs words.** The payoff score needs strength in the text, a setup before it, and an ending
     that is not cut off.
   - **Housekeeping is filler.** "Let me fix my audio", "thanks for the sub" and similar lines count against a
     window.
4. **Weights** (`presets.py`, `feedback.py`). The twelve scores are combined with weights from the content
   preset (automatic, gaming, streamer, podcast). Your feedback shifts those weights by fixed, capped amounts
   that Settings lists in full.
5. **Choosing** (`dedupe.py`). Best first. A candidate is skipped if it overlaps a better pick by more than 10%
   or one second, if more than 55% of its distinctive words match one (the same story told twice), or if it
   matches a clip you already have.
6. **Describing** (`describe.py`). Title, hook options and hashtags are taken from the words that were spoken.
   Nothing is invented. When Claude wrote no thumbnail words, they come from the title the same way: filler at
   the start and small words at the end are left out, a phrase said over and over counts once, and the word in
   colour is a price or number when there is one, else the last real word.
7. **Explaining.** Every suggestion stores the evidence behind each sub-score, which is what the "Why this
   score" panel shows.

**Confidence** reflects how much the signals agree and how much speech there was. It is capped at 0.74 while
no language model reviews the candidates. **Where almost nothing is said** (`_action_highlights` in `engine.py`): if the spoken moments do not fill
the number of clips asked for, peaks of loudness and picture activity fill the rest. Such a clip may be at
most a quarter talk, is not looked for where people are mostly talking, keeps a short remark at its edge
whole and leaves a longer sentence out, and never overlaps a spoken pick. It is marked "signals only", with
confidence at most 0.45, and is ranked with the others by score. A video with no speech at all is handled
the same way, with nothing but these.

The cue lists are English. Other languages are transcribed (with a multilingual model) and scored on the
non-text signals, with lower confidence.

## Captions and rendering

- **Captions are ASS subtitle text**, not images. One subtitle event is written per "active word" state, which
  is how the current word is highlighted. Styles: Opus-style, karaoke, minimal, gaming, bold creator and
  word-by-word; every property can be overridden per clip. Text is measured before rendering and shrunk or
  re-wrapped to stay inside the safe area (10% to 80% of the height, clear of platform buttons).
- **Fonts** (`editing/fonts.py`). The family name, bold flag and line height are read from the font file
  itself, because the subtitle renderer matches and scales fonts by those values. A test renders every
  bundled font and compares the drawn width with the measured width.
- **Masking swear words** applies to burned-in text only. Transcripts, SRT and VTT files are always word for
  word.
- **Layouts**: fill (crop around the subject), blurred background, facecam above gameplay, split, or automatic
  from what the face detector finds. The crop is fixed for the whole clip.
- **One FFmpeg run per clip.** The input is opened two seconds early and the exact cut is made inside the
  filter graph with `trim` and `atrim` on one timeline, so picture and sound are cut on the same frame. Audio
  is levelled to −14 LUFS with `loudnorm`. Encoding uses NVENC if it passed its test, otherwise x264; if an
  NVENC render fails it is retried on x264.
- FFmpeg runs with the clip's work folder as its current directory and is given only relative file names for
  the subtitle file and font folder, which avoids Windows path-escaping problems.

## Data

```
data/
  clipforge.db            SQLite database (WAL)
  uploads/<key>/          source.<ext>, thumb.jpg          uploaded videos and videos imported from links
  work/<key>/             audio.wav, transcript.json, audio.npz, visual.npz   reusable intermediates
  clips/<key>/            clip.mp4, cover.jpg, thumbnail.jpg, captions.srt, captions.vtt, captions.ass, post.txt
  models/                 whisper/<name>/, yunet/
  fonts/                  optional caption fonts
  exports/<date>/         "Export to folder" output
  logs/                   api.log, worker.log (JSON lines, rotated)
  secret.key              encryption key for stored credentials, itself wrapped by Windows DPAPI
  hardware.json           result of the last hardware test
```

Every path stored in the database is relative to `data/`, so the folder can be moved or backed up as a unit.
Videos linked from disk keep their original absolute path and are never copied or deleted.

Tables: `projects`, `videos`, `transcript_segments` (with a full-text index, `transcript_fts`), `jobs`,
`clips`, `feedback`, `settings`, `secrets`, `presets`, `notifications`. The schema is created and changed
only through Alembic migrations in `backend/alembic/versions`, applied automatically at start under a file
lock so the two processes cannot migrate at once.

## The job queue

`jobs/queue.py`. States: `queued → running → succeeded | failed | cancelled`.

- **Claiming** is one atomic update, so a job cannot be taken twice.
- **Heartbeat.** A running job updates a timestamp every 10 seconds. A running job with no heartbeat for 45
  seconds is treated as interrupted and queued again, up to 3 attempts.
- **Cancel** sets a flag the job checks between steps and inside FFmpeg's progress loop; partial files are
  removed.
- **Shutdown.** Ctrl+C or `scripts\stop.ps1` asks the worker to stop; the running job goes back to the queue
  and resumes from its cached steps next time.
- **Duplicates.** Asking twice for the same analysis or render while one is pending returns the existing job.

## API

All under `/api`. Interactive documentation is at `http://localhost:3000/api/docs` while the app runs.

| Area | Endpoints |
|---|---|
| Videos | upload (raw stream), check a link, import from a link, link from disk, list, read, update, delete, stream with seeking, thumbnail, analyze, transcript read and correct, timeline, make a clip by hand |
| Clips | list with filters and sorting, batch actions, manual order, export to folder, read, update, delete, render, duplicate as a variant, file, still frame, thumbnail (see, download, make again with other words, another word in colour or another moment), TikTok caption rewritten by Claude (with or without emojis and hashtags; your own saved with the clip), prompt edit (your words → checked changes → rendered again) and its undo, caption words, SRT and VTT, post text, feedback |
| Jobs | list, read, cancel, retry |
| System | health, shut down (in order, as `stop.ps1` does), hardware and re-test, live load, feature status, storage and cleanup, settings, credential store (write-only), options for the interface, your own presets (list, save under a name, remove; checked by the same rules as an analysis), speech-model install, dashboard, notifications, search, projects |

Request and response bodies are typed Pydantic models (`schemas.py`). Errors are JSON with an `error` message
written for a person and, where useful, a `hint` saying what to do.

## Interface

`frontend/src`: React with TypeScript, built by Vite, styled with Tailwind. Server state goes through TanStack
Query, which polls while jobs are active. Pages: Dashboard, AI Clipper, Clip Library, Video Editor, Settings,
and plain "not built yet" pages for Creator Discovery, Publishing Studio, Analytics, Automation Center and AI
Strategist. Those pages have no controls: they say what is missing, what it will need from you, and what
works today.

## Decisions and trade-offs

| Decision | Why | Cost |
|---|---|---|
| Rule-based scoring first, language model later | Works offline with no subscription, every score is explainable and testable | It does not understand meaning; sarcasm, in-jokes and visual-only moments are missed |
| SQLite queue and one worker | Nothing extra to install, survives restarts | No parallel jobs; one long transcription delays renders behind it |
| Captions as subtitle text | Editable, fast, exact timing | Limited to what the subtitle renderer can draw: no animated emoji or per-letter effects |
| Fixed crop per clip | Predictable, no wobble | A subject who moves across the frame can leave the crop |
| Link imports ask for your right to clip first, and never sign in | A public video is not automatically yours to repost; cookies and logins would hand a downloader your accounts | Private, members-only and age-restricted videos cannot be imported from a link; you add those as files |
| Built interface served by the API | One address, one command, no CORS setup | Interface changes need a rebuild (or `start.ps1 -Dev`) |

## Clips from your own timestamps

`POST /api/videos/{id}/moments` takes a start (typed, marked in the player, or a link with `?t=`, read by
`services/timecode.py`) and an optional end, and queues a `moment` job (`jobs/pipeline.py`, `_moment`). It
reuses the analysis's transcript step (or transcribes once, if the video was never analysed), then:

- Claude (`claude_review.build_moment_prompt`) reads the transcript around the moment, keeps your start, picks
  the end right after the payoff when you gave none, and names the clip;
- the local rules (`engine.moment_highlight`) add the 12-part breakdown and evidence, and pick the end
  themselves when Claude is off or unreachable (the best-scoring sentence end inside your length);
- a start or end that would chop a word moves to the word's edge, at most 0.6 s, and the work log says so;
- ChatGPT gives a verdict only (`chatgpt_review.build_prompt(chosen_by_user=True)`), never additions;
- the clip is saved with `origin="manual"` and rendered straight away.

## Prompt edit

`editing/prompt_edit.py` and `POST /api/clips/{id}/prompt-edit`. You describe in your own words what you want
changed in a clip, and one request goes to Claude through the same Claude Code tool as the clip picker (no tools
of its own, an empty folder, your plan). The request holds what you wrote; the clip as it is (cut, parts left
out, the subtitle style with its effective values, hook title, layout, sound, thumbnail words); the fonts on this
PC; what can be changed and within which limits; what ClipForge cannot do; and the transcript from two minutes
before the clip to two minutes after it, each line in the clip with both its source and its clip times (cut down
from the far ends when long). Claude answers with JSON: the changes, what is not possible, and a summary.

Nothing Claude answers is used as it comes. `make_changes` checks every value against the editor's own limits:
sizes and zoom are brought within range, colours must be #RRGGBB, fonts must be on this PC, styles and layouts
are found by key or by name. A clip stays at least a second long, a cut never falls inside a word (a time inside
one moves to the word's nearer edge), and parts left out stay inside the cut and are merged. It lists in words
what changed, and what could not be done and why. Choosing a subtitle style starts from that style, as in the
editor; a new cut resets subtitles edited by hand, and says so. Word corrections ("Jinxy" → "Jynxzi") are made on
the subtitle words of the new cut, whatever their capitals, keeping the punctuation. The settings then pass
`merge_settings`, the same checks as the editor's saves.

When something changed, the clip as it was (`snapshot`: cut, settings, hook, title, subtitle words, thumbnail
words and moment) is kept in `clips.last_edit` with what you asked and those lines, and a render is queued ahead
of the queue. `POST .../prompt-edit/undo` puts the snapshot back and renders again. A clip that is rendering is
left alone, since its render read the settings when it began. When nothing changed, nothing is saved or rendered.

**The picture.** Two pictures go with the request, through the Claude Code tool's stream-json input (still
no tools): the frame most like the rest of the clip (`typical_frame`: a call app may show one camera large for
a while), at 1280 pixels, which Claude measures on, and a strip of eight small frames across the clip with their
times, to see whether the picture changes. With them Claude may choose what the stacked layouts show: `top` and
`bottom` rectangles in percent of the frame (`facecam_box` and `gameplay_box`). Each is kept inside the picture,
refused when tiny, and moved onto the camera's own edges (`snap_part`): in each frame the strongest straight line
near each side that has the call's dark, even background outside it, and the innermost of those, because a
camera can move during the clip and one crop serves all of it. A side drawn at the picture's own edge stays
there. A split screen half chosen is not made at all. Without pictures (they could not be taken) the parts
cannot be chosen, and the request says so.

**Split screen of two parts** (`reframe.plan_layout`): with both boxes, each fills half the height, cropped from
its middle to the half's shape, a little inside its edges. Without them, split screen still looks for two faces;
when it finds fewer, it shows the whole frame (a single small face may be a profile picture in a call app's
member list), and crops around one face only when that face fills much of the frame. The editor's Layout tab
draws the parts on a large frame of the clip (**Choose the two parts…**) and marks them on the live preview.

Parts left out are a clip setting, `cuts`: `[from, to]` pairs in seconds of the source video.
`pipeline.clip_segments` takes them out of the segments last (after pause tightening), so the render, the
subtitles, the SRT and VTT files and the export checks all follow them, and the thumbnail is never taken from
them. Every save of a clip keeps them inside it: parts outside are dropped, parts across an end are trimmed to it,
and a trim that would leave nothing to play is refused. The editor lists them under the timeline and can put
each one back; its live preview still plays them.

## Publishing

The `publishing/` package:

- `base.py`: shared types (`Tokens`, `Draft`, `Outcome`, and `PublishError` marked retry or reconnect) and an
  HTTP client that turns every failure into a message and keeps tokens out of addresses and logs.
- `oauth.py`: PKCE (base64url for Google, hex for TikTok's desktop sign-in) and single-use, ten-minute states.
- `youtube.py`, `tiktok.py`, `instagram.py`: one adapter each for sign-in, renewal, upload and confirmation.
  YouTube uses the resumable protocol, so a break continues where it stopped and a retried post resumes the
  same upload. TikTok uploads to the inbox in chunks by its rules. Instagram uses the resumable upload of
  Facebook Login for Business, so no public address is needed, and records the published id at once so a
  retry never posts twice.
- `store.py`: your developer-app credentials and the accounts' tokens in the encrypted store, renewed before use.
- `service.py`: the queue (all or nothing, with the safeguards), the `publish` job, retries (two, after 2 and
  10 minutes), follow-up checks (processing, TikTok drafts), cancel, retry, remove, connect and disconnect.
- `assistant.py`: Claude's title, caption and hashtags for a clip, from what is said in it.

Two tables: `accounts` (one per connected account, no secrets) and `posts` (one per clip and account: status,
times, text, the platform's id and link, and the upload state that lets a retry resume). The worker looks for
posts whose time has come every 10 seconds (`enqueue_due`). A post goes scheduled → publishing → processing →
published, or ends as scheduled_on_platform (YouTube publishes it itself), private, in_drafts (TikTok inbox),
failed or cancelled.

## Where the planned parts will plug in

- **Language-model review** (`analysis/`): a pass after scoring that re-ranks the top candidates and writes
  titles, behind a provider interface (Ollama locally, or a paid API with a cost estimate and explicit
  opt-in). The settings and encrypted key store for it exist.
- **Discovery** (new `sources/` package): YouTube Data API with your own key; a discovered video is a
  suggestion with a rights status, which you can then import with the link importer.
- **Analytics**: metrics tables fed only by connected accounts or values you type in, each labelled with its
  origin.
- **Speaker and subject tracking** (`editing/reframe.py`): the crop already comes from a plan object, so a
  time-varying plan can replace the fixed one.
