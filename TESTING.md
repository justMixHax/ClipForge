# Testing

What is tested, how to run it, what was checked by hand, and what has not been checked at all.

## Running the tests

```powershell
powershell -ExecutionPolicy Bypass -File scripts\test.ps1
```

The fast suite: 958 tests, plus a type-check of the interface. No speech model is needed;
tests that need FFmpeg are skipped if it is not installed.

```powershell
powershell -ExecutionPolicy Bypass -File scripts\test.ps1 -All
```

Everything, including the 8 slow tests that load the real speech model and process the sample video: 966
tests, about seven minutes. They need the sample video, which is not kept in Git:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\make-sample.ps1
```

To run one file: from the `backend` folder, `<environment>\Scripts\python.exe -m pytest tests\test_scoring.py`
(the environment's location is in `.venv-path`).

**Last full run:** 24 November 2026, on the PC described in [SETUP_WINDOWS.md](SETUP_WINDOWS.md): 966 passed,
0 failed, 0 skipped, in 6 minutes 45 seconds.

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
| `test_audio_features.py`, `test_visual_features.py` | 16 | Loudness against the local baseline, silences, loud events, motion, scene cuts; the faster decoding measures the same as full decoding, and a file that refuses it is read in full |
| `test_captions.py` | 29 | All six styles, emphasis, wrapping and fitting, safe area, hook titles, masking on screen only, SRT and VTT |
| `test_reframe.py` | 152 | Layout decisions and crop maths for every shape and layout, facecam detection on synthetic frames; the gameplay under the camera leaves out the stream's own facecam in every corner when that costs little, and stays centred when it would cost too much; a split screen of two chosen parts stacks them in equal halves, cropped from their middles; split screen without two faces shows the whole frame rather than a crop around a small face (a profile picture, seen for real), and still crops around one big face |
| `test_render.py` | 28 | Real renders: every layout at every shape, frame-accurate cuts, joined segments, caption placement and width for every font, silent sources, volume levelling, noise reduction, frame rate, cancellation, failure clean-up |
| `test_qc.py` | 8 | The export checks catch wrong size, wrong length, missing sound, black frames, silence |
| `test_pipeline.py` | 21 | The whole analyse and render jobs: stored results, re-use of the transcript, re-analysis keeping what you approved, cancellation, missing source, missing model, render failure, fallback from graphics card to processor, pause on shutdown; every render makes a thumbnail, a thumbnail that cannot be made never costs the clip, a title the fonts cannot draw gives a thumbnail without words and the log says so, a render still lands while the old video is open in the browser, subtitles switched off are left out while the hook title stays, and with both off nothing is burned in; parts left out are cut from the render, and its thumbnail is never taken from them |
| `test_queue.py` | 14 | Claiming, progress, heartbeat, cancel, retry, recovery of interrupted jobs, attempt limit |
| `test_api.py`, `test_api_library.py` | 68 | Upload and its rejections, linking from disk, playback with seeking, clips, captions, feedback, batch actions, manual order, variants, caption files, post text, export, projects, cleanup, storage, settings, search, cross-site and wrong-host requests, persistence across a restart, honest feature status (today's features included) |
| `test_presets.py` | 17 | Your own presets: saved, listed by name, removed; saving under the same name (whatever its capitals) updates it; options left out get the usual ones; a preset that would not work (an unknown style, layout, shape, game or length, a number out of range, an unknown option) is refused; a name is needed and short; at most fifty |
| `test_answers.py` | 20 | Reading an AI's answer, with Claude's two real answers that were lost before: a draft followed by "Correction, here is the real answer" (the correction is used), times written as a clock (read as seconds), braces in the text around the answer; a clock inside a sentence is never touched; the same reading for ChatGPT's opinion, the post text and the prompt edit |
| `test_downloader.py`, `test_import.py` | 69 | Importing from a link, with the real downloader against a web site started on this PC for the test: download, progress, cancel, size limit, missing video, page without a video, unreachable site; which links are accepted; failures explained in plain words; the import job with source and rights stored, duplicates, disk-space check, pause and resume; the API refusing to start without a stated right to clip |
| `test_timecode.py`, `test_moments.py` | 70 | Clips from your own timestamps: times typed in every common way or pasted as a link shared at a moment (a link to another video is refused); your start kept, or moved a fraction of a second when it would chop a word, never leaving less than a second of clip; the end from you, from Claude or from the local rules, always inside your length; Claude's name and score; ChatGPT's verdict without additions; the transcript reused; the render queued; the work log; Claude's thumbnail words and TikTok caption for your moment |
| `test_publishing_adapters.py` | 29 | YouTube, TikTok and Instagram against stand-ins of their APIs built from the documentation: sign-in with PKCE (checked against the standard's own example), token renewal, a resumable upload that continues after a break, a retried post that resumes instead of uploading twice, TikTok's chunk rules, a Reel never published twice, quota and refusal messages |
| `test_publishing_service.py`, `test_publishing_api.py` | 46 | The queue's safeguards (approved, rendered, right to post recorded, no second copy without asking, the daily limit, each platform's rules, a time in the future), the scheduler, the job end to end with the stand-ins, renewal before posting, reconnect, two retries then failure, a failure that is never retried by itself, TikTok drafts confirmed later, cancel, retry, remove; app secrets and tokens stored encrypted and never returned; a sign-in that works once; a sign-in not started in ClipForge refused; Google's return to the home page passed on; date ranges by when a post goes live; Claude's post text; honest feature status and dashboard numbers |
| `test_paths.py`, `test_secrets.py`, `test_logging.py`, `test_serve.py` | 50 | File-name and path safety, encrypted credentials, masking of credentials in logs, a connection the browser dropped is not logged as an error (anything else still is), where the server listens and how it stops; the Shut down button stops a real running server in order, says so when it cannot, and another website cannot press it |
| `test_claude_review.py` and the Claude tests in `test_pipeline.py` | 24 | Claude as the engine, with a stand-in for the Claude Code tool (the real one is never called by tests): the prompt holds the timed transcript and the rules, the tool runs with no tools of its own, answers are read even with text around them, picks are widened to whole sentences and de-duplicated, sign-out and plan-limit failures are explained, and the local rules take over when Claude cannot be reached; the thumbnail words, TikTok caption and hashtags Claude writes are asked for, cleaned (no emoji, one line, safe hashtags, a number sign before a number kept) and go with the clip; pictures go with a request as images in the tool's stream-json input with no tools switched on, and the answer is read from the stream |
| `test_chatgpt_review.py` | 41 | ChatGPT as the second opinion, with a stand-in for the Codex tool: verdicts and additions read even with text around them, odd answers ignored or refused cleanly, failures explained (a model the plan does not offer is named; a reached limit says when ChatGPT works again), the prompt holds the transcript and the numbered picks, the tool is read-only with tools and web search off, uses your ChatGPT sign-in and never an API key, only a real program is run, model names are checked, verdicts are recorded on each clip, at most two new moments are added and only inside your length; an addition comes with thumbnail words, a caption and hashtags too |
| `test_thumbnail.py`, `test_covers.py`, `test_post_text.py` | 39 | Thumbnails: the words come from the clip's own line in any script (filler and a repeated phrase left out, ending on a real word; a price or the last real word in colour), words you type become one short line without emoji; words no font here can draw properly are left off rather than shown as boxes, and refused when typed; one long word is made smaller until it fits; the words sit under the camera or out of the way of a face, inside the edges and above TikTok's buttons; the moment chosen is sharp and lit, and never from a part left out; the picture is framed like the clip; a picture being sent to the browser is replaced once it is free. Through the API: shown and downloaded, words, colour and moment changed, a moment outside the clip refused, kept for the render before one, copied by export. The TikTok caption is the words, the credit and the hashtags; emojis can be taken out of a caption |
| `test_tiktok_caption.py` | 9 | The TikTok caption: Claude rewrites it from what is said (a stand-in answers), with or without emojis and hashtags, and emojis or hashtags it adds anyway are taken out when not wanted; the credit stays, on a line of its own, and so does the creator's hashtag; nothing is saved until you save; your own caption is kept exactly as written and used in the post text; an empty one goes back to the automatic caption; one longer than 4,000 characters is refused; Claude signed out or switched off is explained |
| `test_prompt_edit.py` | 52 | The prompt edit, with a stand-in for Claude: the request shows Claude what you wrote, the clip as it is, its timed words (both source and clip times) and what can be changed, and a long transcript is cut down from the far ends; answers are read even with text around them, and one that cannot be read is an error, not a guess. Every change is checked: a new cut never falls inside a word (it moves to the word's nearer edge), stays inside the video and at least a second long; times are read as seconds or as a clock; parts left out stay inside the cut, are merged, can all be put back, and never leave less than a second; subtitle sizes are kept within range, unknown fonts and colours that are not #RRGGBB are refused with the reason, fonts are found whatever their capitals, a style chosen by its name starts from that style; hook text, timing and switch; layout, shape, zoom, subject, sound, pauses, title and thumbnail words and moment; what ClipForge cannot do is reported; asking for what the clip already has changes nothing; the clip given is never changed in place; subtitles edited by hand are reset by a new cut, which says so; word corrections whatever their capitals, keeping the punctuation. Through the API: the clip is changed and rendered again, the copy for undo stays out of the responses, undo puts it back and renders again, there is nothing to undo before an edit, nothing is rendered when nothing changed, a word fix reaches the subtitles, a clip being rendered is left alone, nothing is sent to Claude when it is switched off, empty and very long requests are refused, a signed-out Claude leaves the clip as it was, and parts left out are stored with the clip and checked, stay inside it whenever it is trimmed, and a trim that would leave only them is refused. With the picture: two parts go on top and below in a split screen (from a list or named numbers), a part off the picture is kept inside it, a split screen needs both parts and a tiny one is refused (nothing half done), parts are only kept for the stacked layouts, parts need the picture; the picture sent is the frame most like the rest of the clip; frames across the clip are shown together with their times; a part is moved onto the camera's own edges, also when the camera moves during the clip (inside it in every frame), but not onto a line inside the picture (a door frame, seen for real), and a side drawn at the picture's edge stays there; through the API, two JPEGs and the frame's size go with the request |
| `test_ffmpeg_service.py`, `test_hardware.py` | 17 | Probing, progress, error reporting, encoder tests, the graphics card is only recommended after passing its test; frames read a few times a minute show the picture of the time asked for |
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

## A clip from your own timestamp, 22 November 2026

On the 2-hour stream, through the interface: the start typed as `1:02:31`, no end. Claude ended it at 1:03:04,
right after the payoff, and named it "Scam Call Gets Presidential"; ChatGPT agreed with it. It rendered on the
graphics card in 5 seconds, but only 7 of 8 checks passed: the typed second fell 0.16 s before the end of the
word "destroyed.", so the clip opened on half a word. Since then a start or end inside a word moves to the
word's edge (at most 0.6 s) and the work log says so. The clip was moved and rendered again, and all 8 checks
passed.

The Publishing Studio's three pages were checked in the browser: the accounts with their setup steps, the
composer with your approved clips, and the calendar. No real post has been made: no account is connected yet.

## Framing and speed on real footage, 23 November 2026

- **Framing.** One frame from each of four rendered clips of real streams (Jynxzi's GTA RP twice, a Rainbow Six
  stream, Tyler1's desk stream): the camera panel on top was right in all four, but in three the gameplay
  panel underneath showed the stream's own facecam a second time, in its corner. After the fix, copies of
  two of those clips were rendered again: the gameplay panel was clean, the crosshair and the car still in
  view, the render note said what was done, and all 8 checks passed. The copies were then deleted.
- **Speed.** Measuring the picture of the 2-hour 1080p60 stream took 121 s with the faster decoding, against
  about 220 s before. Compared with the earlier measurement of the same stream: motion agrees at a
  correlation of 0.998, the activity scale within 1%, and 128 of 133 scene cuts within a second (the others
  were borderline). The graphics card was tried too and was slower here (it decodes every frame).

## Code reviews by ChatGPT

Since 22 November 2026, changes are reviewed by ChatGPT (GPT-6.2 Astra, through Codex) before they are
committed: `scripts\review.ps1`. The reviews are kept in `reviews/`. The first one, of the ChatGPT second opinion
and the work log, found 11 real problems (4 serious), including that a "Local rules only" setup would have
started sending transcripts to ChatGPT. All were fixed in the same change, each with a test. A second review
of the fixed version found 11 narrower points; 10 were fixed the same way and one was declined with a reason
(noted in the review file). A third review, of the publishing commit on 23 November, ran on gpt-5.6-terra
because the ChatGPT plan no longer offered Astra that day; it found 3 real problems (1 serious), all fixed with
tests. A review of the thumbnails (23 November, gpt-5.6-terra) found 2 problems: a title in another script lost
all its words and got English ones instead, and one very long typed word could run off the picture. Both were
fixed with tests. Since then this PC is signed in to a ChatGPT Go account, which does not include Astra either: code reviews
use gpt-5.6-terra (the script's default now) and ClipForge's second opinion uses gpt-6-luna.

## Thumbnails on real clips, 23 November 2026

- **Five clips the local rules picked** ("Fan Mail is Over..."): a thumbnail each in about 2 seconds. The words
  taken from their titles first read badly ("MY GOD OH MY GOD", "WOW THE HERB TREE OKAY" with OKAY in colour, a
  lone "2" in colour); the word rules were fixed, with those titles as tests ("OH MY GOD", "WOW THE HERB TREE",
  "SEASON 2 IS TRASH").
- **A face that was not there.** One thumbnail showed the streamer bent over, although the frame picker had
  scored a clear, expressive face at that moment. Measured against an exact decode: the fast seek used for the
  thumbnail was exact, but every sampled frame came half a sampling interval late (FFmpeg's `fps` filter rounds
  to the nearest slot), almost a second here. Fixed in the shared frame reader, with a test; the picture
  analysis uses the same reader and its tests, including the one comparing fast and full decoding, still pass.
- **Claude on a real video** ("How to Play Rob Run!"): Claude picked a clip and wrote the thumbnail words
  "ROBBING A STORE", a TikTok caption and four hashtags; ChatGPT added a clip. Both renders made thumbnails with
  the streamer's face clear at the top and the words just under the camera. The clip pages, the Library cards
  and the editor's Thumbnail tab were checked in the browser: the picture, the words, the word in colour, the
  moment, Best moment (it found the same moment again) and the TikTok caption with Copy.
- **A re-render while the clip was playing** failed: Windows refused to replace `clip.mp4` while the browser had
  it open. Fixed, with a test: the new video then gets a name of its own, and the old one is tidied up later.

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
| Importing a 2-hour YouTube stream | YouTube refused the download part-way (HTTP 403) although the video was fine, and the import failed at 4% | Yes: the download asks again for fresh addresses and continues the partial file, up to 4 tries (simulated with a local test site that refuses requests) |
| Making a clip from a typed timestamp on a real stream | A start typed in whole seconds opened the clip on half a word (the quality check caught it) | Yes: a start or end inside a word moves to the word's edge, at most 0.6 s, and the work log says so |
| Reading the publishing code before committing | A post whose app credentials had been removed, or whose follow-up check failed in a way nobody expected, stayed due and would have been tried again every 10 seconds | Yes: it fails once, with the reason; two tests |
| ChatGPT's review of the publishing commit (23 November) | A new YouTube upload sent no Content-Range header (the stand-in YouTube accepted that; the real one may not); the word rule could squeeze a one-second selection below a second or put a start past the end; the post list's date filter ignored when a post went live | Yes, each with a test; the stand-in YouTube now refuses an upload without Content-Range |
| Looking at real rendered clips | The gameplay panel under the facecam showed the stream's own facecam a second time | Yes: slid away from it, or cut just above it, when that costs little |
| A morning when the chosen ChatGPT model was not offered | ClipForge said only "unexpected answer" and showed Codex's start-up banner; a token-refresh warning earlier in Codex's log could even make it claim a sign-out | Yes: Codex's final error decides, the reason is named, and Settings lists the models the plan offers |
| ChatGPT's review of the thumbnails (23 November) | A title in Cyrillic, Arabic or Japanese lost every word and the thumbnail said "WATCH THIS" instead; one 40-letter word could run off the picture | Yes, with tests: every script keeps its words, a font with the characters is chosen (scripts that need shaping are left off the picture, not drawn as boxes), and a long word shrinks until it fits |
| Adding the subtitles switch (23 November) | Turning subtitles off also took the hook title out of the render, although the editor's preview still showed it; and a subtitle script from an earlier render was left in the clip's folder | Yes, with tests: each has its own switch |
| Thumbnails on real clips | The frame reader handed over each sampled frame half a sampling interval late, so a thumbnail could show a different moment than the one the picker scored | Yes, with a test |
| Pressing Render again while the clip played in the browser | Windows refused to replace the video file in use, and the render failed at the very end | Yes, with a test: the new video takes a name of its own |
| Real-model transcription test | The speech model occasionally returns a word with no duration or one stretched across a pause | Yes: word times are corrected against the audio |

## Not tested

- **Publishing to the real platforms.** No account is connected yet, so no real post has been made. The
  adapters follow each platform's documentation as it stood in November 2026 and pass against local stand-ins
  of the three APIs; the first real post on each platform is the real test. Two things the documentation left
  open: whether Google accepts the bare return address `http://127.0.0.1:3000` for your Desktop client as its
  examples show (ClipForge uses exactly that form), and whether TikTok's sandbox delivers drafts to your own
  account before TikTok has reviewed your app.
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
