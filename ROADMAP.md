# Roadmap

Status as of 21 November 2026. "Done" means built and tested (see [TESTING.md](TESTING.md)); nothing here is
marked done because the code merely exists.

| Mark | Meaning |
|---|---|
| ✅ | Built and tested |
| 🟡 | Built, with a stated gap |
| 🔒 | Not built, and blocked by a third party |
| ⬜ | Not built |

## Milestone 1 · Working local foundation — done

| Deliverable | | Notes |
|---|---|---|
| Running frontend and backend | ✅ | One command, one address: `http://localhost:3000` |
| Database | ✅ | SQLite with migrations, applied automatically at start |
| File upload | ✅ | Drag and drop or file picker, plus linking a file already on disk |
| Project creation | ✅ | Create, rename and delete projects; a video's clips follow it; filter the library by project |
| Job queue | ✅ | Persistent, with progress stages, cancel, retry and resume after a restart |
| Video preview | ✅ | Source playback with seeking, and "play the source from this moment" on every clip |
| Local storage | ✅ | Everything under `data/`, relative paths, storage report and cleanup |
| Working navigation | ✅ | Ten areas; the five that are not built say so instead of pretending |

## Milestone 2 · Functional AI clipping — done

| Deliverable | | Notes |
|---|---|---|
| Local video transcription | ✅ | faster-whisper with word timestamps, on the processor |
| Highlight candidate generation | ✅ | Every sentence-aligned window in the chosen length range |
| Candidate ranking | ✅ | Twelve explainable sub-scores, confidence, evidence |
| Source timestamp selection | ✅ | Sentence boundaries; never mid-word |
| FFmpeg cutting | ✅ | Frame-accurate, picture and sound cut together |
| Vertical video export | ✅ | 9:16, plus 1:1, 4:5 and 16:9 |
| Automatic subtitles | ✅ | Six styles, burned in; SRT and VTT alongside |
| Finished clip preview and download | ✅ | |
| Speech-to-text on the graphics card | ✅ | Tested on an RTX 3060: about 4× faster than the processor with the small English model, and it finds the same moments. Another PC runs `scripts\enable-gpu.ps1` once |
| Language-model review of candidates | ⬜ | Ranking is rule-based only |

## Milestone 3 · Advanced editing — mostly done

| Deliverable | | Notes |
|---|---|---|
| Timeline editor | 🟡 | Trim both ends on a waveform with snapping to word gaps, undo and redo. Cutting a section out of the middle by hand is not built (automatic pause tightening is) |
| Caption editor | ✅ | Every word: text, emphasis, hide; style, font, colours, size, position |
| Cropping and reframing | 🟡 | Four layouts, subject position, zoom, facecam box, automatic face finding. The crop is fixed for the clip and has only been checked on synthetic footage |
| Editing presets | 🟡 | Six built-in workflows and four content presets. Saving your own is not built |
| Batch processing | ✅ | Batch approve, reject, render, export, delete; several videos queue up |
| Quality checks | ✅ | Eight checks after every render |
| Who-is-speaking detection | ⬜ | |
| Crops that follow a moving subject | ⬜ | |

## Milestone 4 · YouTube discovery and automation — started

| Deliverable | | Notes |
|---|---|---|
| Permission-aware source management | ✅ | Rights status and note on every source, carried into every clip's post text |
| Creator search, watchlists, upload monitoring | ⬜ | Will use the YouTube Data API with your own key |
| Import from a link | ✅ | Public videos from YouTube, Twitch, Kick and most video sites, through yt-dlp. No sign-in, no cookies. Your right to clip is asked before anything is downloaded, and an import can go straight on to finding clips |
| Automated processing workflows | ⬜ | The queue and "render straight away" exist; rules that start jobs by themselves do not |

## Milestone 5 · Social publishing — not started

| Deliverable | | Notes |
|---|---|---|
| Manual export with post text | ✅ | The working alternative today |
| YouTube Shorts upload | ⬜ | Needs your own Google Cloud project and OAuth client. Until Google has audited that project, uploads made through the API stay private |
| TikTok posting | 🔒 | Direct posting needs a developer app that has passed TikTok's audit; unaudited apps can only post privately |
| Instagram Reels posting | 🔒 | Needs a Meta app that has passed review, a professional account, and a public address for the video file |
| Publishing queue, scheduler, calendar | ⬜ | |
| Approval before posting | 🟡 | Approve and reject exist for review; nothing is ever posted, so there is nothing yet to gate |
| Accurate publishing status | ⬜ | Rule already written down: "published" only when the platform confirms |

## Milestone 6 · Analytics and optimization — not started

| Deliverable | | Notes |
|---|---|---|
| Ranking calibration from your feedback | ✅ | Fixed, capped, visible weight changes |
| Social metrics, dashboards, comparisons | ⬜ | Only from connected accounts or numbers you type in, each labelled |
| Ranking calibration from real results | ⬜ | Needs analytics first |
| AI content strategist | ⬜ | Needs analytics and a language model |

The Dashboard today shows only real counts from your library, this PC's load and storage. Fields that would
need publishing or analytics show a dash.

## Milestone 7 · Polish and hardening — done for what exists

| Deliverable | | Notes |
|---|---|---|
| Performance | ✅ | Graphics-card encoding and speech-to-text, cached intermediates, one decode per render |
| Error handling | ✅ | Every failure path tested shows a message that says what to do |
| Responsive interface | ✅ | Desktop and phone width |
| Security review | ✅ | Self-reviewed and tested, see [SECURITY.md](SECURITY.md). Not reviewed by anyone else |
| Testing | ✅ | 639 automated tests |
| Windows installation scripts | ✅ | setup, start, stop, test, fonts, GPU libraries, sample builder |
| Documentation | ✅ | |

## What I would do next, in order

1. **Run it on your real recordings.** This is the largest unknown. The scoring rules were tuned on a
   synthetic sample; real streams will show where they are wrong. Use the feedback buttons, then adjust the
   cue lists in `backend/app/analysis/lexicon.py` and the weights in `presets.py` against what you see.
   The one real video tried so far (a 15-minute animated film with only 65 spoken words, imported from a
   link) was transcribed correctly. Its two spoken moments scored 44 and 41, below the default minimum of 55.
   That showed the engine ignored everything between the lines, which led to the next point.
2. ~~Suggest moments from stretches with no speech.~~ **Done on 21 November 2026.** Where almost nothing is
   said, loud and busy moments now fill the places the spoken moments left free. On that film it found the
   three action scenes (scores 55 to 56, low confidence). It still needs trying on real gameplay.
3. **Check the framing on real footage**, especially facecam detection, and fix what it gets wrong.
4. ~~Verify speech-to-text on the graphics card.~~ **Done on 22 November 2026.** Next there: try a larger speech
   model, which the graphics card makes practical (`large-v3-turbo`, a 1.6 GB download from Settings).
5. ~~Add an AI review of the candidates.~~ **Done on 22 November 2026, with Claude as the main engine**
   (your Claude plan, through the Claude Code tool on this PC). Prompted by your first real stream, where the
   local rules missed every funny moment. On that same stream it then found six genuinely funny moments where the rules had found none.
6. **Saved presets and cutting out a middle section** in the editor.
7. **YouTube Shorts upload**, then the publishing queue and calendar.
8. **Analytics** from connected accounts, and with them the strategist.
9. **Subject tracking and who-is-speaking detection.**

## Done on 22 November 2026 (after the list above)

- **ChatGPT as the second opinion**: checks Claude's picks, marks the ones it disagrees with and adds up to
  two missed moments, through your ChatGPT plan. On your stream it agreed with 4 of 6 and added 2.
- **Work log**: after every job, short lines saying what Forge, Claude and ChatGPT each did.
- **Code reviews by ChatGPT** before each commit (`scripts\review.ps1`).

## What needs you

Nothing was needed to build the first milestones. These are needed before the later ones can start:

| For | From you |
|---|---|
| Creator discovery | A YouTube Data API key from your own Google Cloud project (the free quota is enough for personal use) |
| YouTube Shorts upload | An OAuth client in that project, and your consent on Google's own screen. For public uploads, Google's API audit of the project |
| TikTok | A TikTok developer app and their audit |
| Instagram | A Meta developer app, their review, and a professional Instagram account |
| Language-model review | Either Ollama installed with a model pulled, or an API key and your explicit switch for paid requests |
| Importing from a link | Nothing more. You decided on 21 November 2026 to build it for public videos, without sign-ins |

## Known limitations

- Suggestions are heuristic estimates. They are not predictions, and they have not been validated on real
  content.
- The cue lists are English. Other languages lean on the non-text signals and get lower confidence.
- Moments with almost nothing said are found by loudness and picture activity alone, with low confidence.
  They are titled "Action at 4:11", not described, because nothing looks at what is actually in the picture.
  A quiet but funny visual moment is still missed.
- Importing from a link depends on yt-dlp keeping up with each site. It will break from time to time until
  `scripts\update-downloader.ps1` is run. Only YouTube has been tried; Twitch and Kick links have not.
- One job runs at a time.
- The crop does not move during a clip.
- Tested on one PC. See "Not tested" in [TESTING.md](TESTING.md).
