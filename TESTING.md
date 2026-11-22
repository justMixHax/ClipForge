# Testing

What is tested, how to run it, what was checked by hand, and what has not been checked at all.

## Running the tests

```powershell
powershell -ExecutionPolicy Bypass -File scripts\test.ps1
```

The fast suite: 582 tests, about a minute and a half, plus a type-check of the interface. No speech model is needed;
tests that need FFmpeg are skipped if it is not installed.

```powershell
powershell -ExecutionPolicy Bypass -File scripts\test.ps1 -All
```

Everything, including the 8 slow tests that load the real speech model and process the sample video: 590
tests, a little over three minutes. They need the sample video, which is not kept in Git:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\make-sample.ps1
```

To run one file: from the `backend` folder, `<environment>\Scripts\python.exe -m pytest tests\test_scoring.py`
(the environment's location is in `.venv-path`).

**Last full run:** 22 November 2026, on the PC described in [SETUP_WINDOWS.md](SETUP_WINDOWS.md): 590 passed,
0 failed, 0 skipped, in 3 minutes 32 seconds.

## How the tests are built

- **Real things, not stand-ins.** Tests that involve video make a small real video with FFmpeg, render it for
  real and inspect the result: its size, its length, the colour of the first and last frame, where the caption
  pixels are, how loud the sound is. The API tests run the real application against a temporary database
  created by the real migrations.
- **One stand-in, on purpose.** In the fast suite the speech model is replaced by a scripted one that returns
  a known transcript, so the tests take seconds and the expected result is exact. The slow suite uses the real
  model.
- **Every test gets its own data folder.** Nothing touches your library, and your `.env` is not read.
- **Written first where it mattered.** The analysis, caption, queue, security and rendering logic was written
  test-first. The tests added afterwards for parts that already existed found real defects; they are listed
  below because that is the honest record.

## What is covered

| File | Tests | What it proves |
|---|---|---|
| `test_scoring.py`, `test_engine.py`, `test_engine_action.py`, `test_candidates.py`, `test_dedupe.py`, `test_lexicon.py` | 78 | The highlight engine: a hook beats a filler opening, loudness alone does not win, a payoff needs a setup, clips start and end on sentences, the same story is not suggested twice, titles and hooks use only spoken words, videos without speech still get suggestions; where almost nothing is said, loud and busy moments fill the free places without cutting a sentence, and loud talk is never brought in that way |
| `test_sentences.py`, `test_transcribe.py`, `test_align.py` | 40 | Sentence building, windowed transcription with resume after a failure or cancellation, correction of word timings |
| `test_audio_features.py`, `test_visual_features.py` | 14 | Loudness against the local baseline, silences, loud events, motion, scene cuts |
| `test_captions.py` | 29 | All six styles, emphasis, wrapping and fitting, safe area, hook titles, masking on screen only, SRT and VTT |
| `test_reframe.py` | 142 | Layout decisions and crop maths for every shape and layout, facecam detection on synthetic frames |
| `test_render.py` | 28 | Real renders: every layout at every shape, frame-accurate cuts, joined segments, caption placement and width for every font, silent sources, volume levelling, noise reduction, frame rate, cancellation, failure clean-up |
| `test_qc.py` | 8 | The export checks catch wrong size, wrong length, missing sound, black frames, silence |
| `test_pipeline.py` | 14 | The whole analyse and render jobs: stored results, re-use of the transcript, re-analysis keeping what you approved, cancellation, missing source, missing model, render failure, fallback from graphics card to processor, pause on shutdown |
| `test_queue.py` | 14 | Claiming, progress, heartbeat, cancel, retry, recovery of interrupted jobs, attempt limit |
| `test_api.py`, `test_api_library.py` | 67 | Upload and its rejections, linking from disk, playback with seeking, clips, captions, feedback, batch actions, manual order, variants, caption files, post text, export, projects, cleanup, storage, settings, search, cross-site and wrong-host requests, persistence across a restart, honest feature status |
| `test_downloader.py`, `test_import.py` | 69 | Importing from a link, with the real downloader against a web site started on this PC for the test: download, progress, cancel, size limit, missing video, page without a video, unreachable site; which links are accepted; failures explained in plain words; the import job with source and rights stored, duplicates, disk-space check, pause and resume; the API refusing to start without a stated right to clip |
| `test_paths.py`, `test_secrets.py`, `test_logging.py`, `test_serve.py` | 45 | File-name and path safety, encrypted credentials, masking of credentials in logs, where the server listens and how it stops |
| `test_claude_review.py` and the Claude tests in `test_pipeline.py` | 16 | Claude as the engine, with a stand-in for the Claude Code tool (the real one is never called by tests): the prompt holds the timed transcript and the rules, the tool runs with no tools of its own, answers are read even with text around them, picks are widened to whole sentences and de-duplicated, sign-out and plan-limit failures are explained, and the local rules take over when Claude cannot be reached |
| `test_ffmpeg_service.py`, `test_hardware.py` | 16 | Probing, progress, error reporting, encoder tests, the graphics card is only recommended after passing its test |
| `test_transcribe_real.py` (slow) | 7 | The real speech model on the sample: key phrases heard, at least 90% of the scripted words, word times within half a second, faster than real time. One test repeats it on the graphics card (same words, at least 10× real time) and is skipped where no usable graphics card is found |
| `test_pipeline_real.py` (slow) | 1 | The acceptance test below |

## The acceptance test

`test_pipeline_real.py` runs the real pipeline on `samples\clipforge_sample.mp4`, a 2 min 54 s synthetic video
with dull filler and four planted moments (a clutch, a funny story, a hot take and a fail). It was generated
on this PC from Windows' built-in voices and FFmpeg test patterns, so there are no rights questions, and the
script it was built from is stored next to it. The test asserts that:

- the transcript contains the key line of every planted moment;
- exactly four suggestions come back, each planted moment is covered by one, and none is mostly filler;
- each renders to a 1080×1920 H.264/AAC MP4 of the right length that passes every quality check, with
  captions, an SRT file and a cover image;
- analysing again re-uses the saved transcript instead of transcribing again.

This sample is easy on purpose: the good moments are clearly marked by what is said. Passing it shows the
pipeline works end to end. It does not show how good the choices are on real streams.

## The fourteen acceptance criteria

| # | Criterion | Evidence |
|---|---|---|
| 1 | Starts locally on Windows | `scripts\start.ps1` run for real: server healthy, worker running, bound to 127.0.0.1 |
| 2 | Upload through the browser | Done in the browser with a scripted drop of a real file onto the page (the path a real drag and drop takes); `test_an_uploaded_video_is_probed_stored_and_listed` |
| 3 | The file appears in the library | Same test; seen in the interface |
| 4 | The backend transcribes the dialogue | `test_transcribe_real.py` |
| 5 | Several candidate highlights | `test_pipeline_real.py`; `test_analysis_stores_the_transcript_and_ranked_explained_suggestions` |
| 6 | Real timestamps and explanations | Same; boundaries within about a second of the scripted lines; evidence shown on each card |
| 7 | Choose a segment | Trim in the editor by hand; `test_a_manual_clip_keeps_its_link_to_the_source`, `test_clips_can_be_edited_approved_favourited_and_filtered` |
| 8 | A real playable MP4 | `test_render_makes_a_playable_vertical_mp4_of_the_requested_length`; played in the browser |
| 9 | Correct dimensions | `test_every_layout_renders_at_every_shape` |
| 10 | Subtitles in sync | `test_word_timestamps_match_when_the_lines_were_actually_spoken`; by hand against the timecode burned into the sample |
| 11 | Preview and download | By hand; `test_api.py` file endpoints |
| 12 | Database survives a restart | `test_everything_is_still_there_after_a_restart`; server restarted by hand several times |
| 13 | Useful error messages | `test_a_missing_source_file_fails_with_a_message_that_says_so`, `test_a_missing_speech_model_fails_with_instructions`, `test_a_render_failure_is_recorded_on_both_the_clip_and_the_job`, the upload rejections |
| 14 | Results survive a refresh | All state is on the server; pages reloaded by hand |

Also asked for: **cancellation** (`test_cancelling_during_analysis_*`, `test_cancelling_a_render_leaves_no_file_behind`,
`test_a_queued_job_can_be_cancelled_and_retried`), **invalid files** (`test_upload_rejects_*`),
**FFmpeg errors** (`test_a_failed_render_raises_and_leaves_no_file_behind`), **AI failures**
(`test_a_missing_speech_model_fails_with_instructions`, the resume-after-failure tests in `test_transcribe.py`).

## Checked by hand, in the browser, on 21 November 2026

Uploading a real file by a scripted drop onto the page (a real mouse drag from Explorer cannot be automated,
so that last step is yours to try); watching the analysis stages; reviewing the four suggestions with their scores
and evidence; approving and rejecting; editing a caption word, trimming on the timeline, changing the caption
style and font, undo and redo; saving and re-rendering; playing the result in the page; creating a project,
putting a video in it and filtering the library by it; the Dashboard, Clip Library, Settings and the "not
built yet" pages; a phone-width layout; restarting the server and finding everything still there. The
download link was fetched directly rather than clicked, and the file it returned was checked frame by frame.

Importing from a link, also on 21 November 2026: pasted the YouTube link of Blender's film Sintel, pressed
Check link (it showed the title, creator, length and size, and the size changed when the quality was
changed), chose the licence as the right to clip, pressed Import video, watched the three stages, and saw
the page switch to the imported video with its link, creator and rights filled in. The analysis that
followed, the two clips made from it and a frame of one rendered clip were looked at as well. After the
engine learned to use stretches with almost no speech, the film was analysed again in the app: five
suggestions, three of them the film's action scenes, each marked "Low confidence" with the note that it
rests on sound and picture only.

Scripts run for real: `setup.ps1` (over an existing install, and creating a fresh Python environment),
`start.ps1` (normal, `-Dev`, and with the port already in use), `stop.ps1`, `test.ps1` (with and without
`-All`), `get-fonts.ps1`, `make-sample.ps1`, `update-downloader.ps1`, and on 22 November `enable-gpu.ps1`. After the project was moved to its
own folder, `setup.ps1`, `start.ps1`, `stop.ps1` and `test.ps1 -All` were run again from there.

## Claude on a real stream, 22 November 2026

The user's 37-minute stream "I lost my cool on this fool" (real bail-bond footage, 3,929 spoken words).
With the local rules, its 4 clips missed every funny moment. With Claude (Sonnet) as the engine the
analysis took 23 seconds, 18 of them Claude's review, and gave 6 clips, all of them funny bits with a
correct reason each: the "Girl Scouts" fake-name exchange (86), "Welcome to the Krusty Krab" (82), the
leash hop (74), "Do you like sausage?" (72), "You lack the pigmentation" (68) and "Do not fart on code
brown" (64). Two shorter jokes (6 and 13 seconds) were not picked; both are shorter than the 15-second
minimum asked for. All six rendered on the graphics card and passed every check.

The first real run also found a defect: started from inside another Claude program, ClipForge passed that
program's connection settings on to the tool, which then ignored the user's own sign-in. Fixed and tested
(`test_the_tool_gets_a_clean_environment_so_it_uses_your_own_sign_in`).

## Defects the tests found

Kept here because a test suite that never finds anything proves little.

| Found by | Defect | Fixed |
|---|---|---|
| Rendering every downloaded font and measuring the result | Captions in downloaded fonts were drawn at the wrong width, or in a different font, because the subtitle renderer matches fonts by a different name and scales them by line height | Yes |
| Caption-file download with an unusual title | Downloading SRT or VTT crashed when the clip title contained curly quotes, a dash or an emoji | Yes |
| Volume test with levelling switched off | A mono recording came out 3 dB quieter than its source | Yes |
| Log-masking tests | A token after `Authorization: Bearer`, Basic credentials and Google keys in web addresses would have been written to the log unmasked | Yes |
| Project tests | Renaming a project erased its description; a blank name was accepted; moving a video left its clips behind; a project that does not exist caused a server error | Yes |
| Start-up tests | The address the server listened on and the address the safety checks assumed came from two different places and could disagree | Yes |
| Restarting the app with a browser tab open | `stop.ps1` had to use force: an open connection kept the server from ever finishing its orderly shutdown | Yes: requests in flight get 4 seconds, then the server stops; covered by a test that holds a request open |
| Trying the graphics card through the API | A clip length the app does not know (`10-40`) was silently replaced by the default range, so one clip went missing | Yes: an unknown length is now refused with a message |
| Real-model transcription test | The speech model occasionally returns a word with no duration or one stretched across a pause | Yes: word times are corrected against the audio |

## Not tested

- **Real streams.** One real video has been through it: Blender's film Sintel (Creative Commons), imported
  from its YouTube link on 21 November 2026 through the interface. It downloaded in about 8 seconds at 720p,
  was transcribed correctly, and gave two sensible clips that rendered and passed every check, but they
  scored 44 and 41, under the default minimum of 55. After the engine learned to use the stretches with
  almost no speech, the same film also gave its three action scenes (55 to 56, low confidence). No real stream, podcast or gameplay recording has been
  tried, so the quality of the suggestions and of the automatic framing on that kind of content is unknown.
- **Links other than YouTube.** Twitch and Kick links have not been tried.
- **Long videos.** The longest video processed was 15 minutes. Hour-long recordings exercise the same code
  (transcription works in 5-minute windows) but have not been run.
- **Other graphics cards and larger speech models.** Speech-to-text on the graphics card was tested with
  `small.en` on an RTX 3060 only.
- **Languages other than English**, and the larger speech models.
- **The interface has no automated tests.** It is type-checked and was exercised by hand.
- **The native file picker, a real mouse drag from Explorer,** and **the browser opening by itself** on start
  (`start.ps1` was run with `-NoBrowser`; the upload itself was tested by a scripted drop and through the API).
- **Other PCs.** One machine, Windows 11, Windows PowerShell 5.1. Not tried on Windows 10 or without an NVIDIA
  card (the tests do run with a "no graphics card" hardware report).
- **Uploads of many gigabytes.** The size limit is tested with a lowered limit, not with a 20 GB file.
- **Publishing, discovery, analytics.** Not built, so there is nothing to test.
