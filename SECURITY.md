# Security and privacy

ClipForge is a single-user application for one PC. It has no login, because it only accepts connections from
the PC it runs on. This document says what that design protects against, how, where the limits are, and
which rules the planned integrations will have to follow.

## What it protects against

| Risk | Protection | Code | Test |
|---|---|---|---|
| Another device on your network opening the app | The server listens on `127.0.0.1` only. Any other address is refused at start-up unless `CLIPFORGE_ALLOW_REMOTE=true` is set deliberately | `config.check_bind`, `serve.py`, `main.create_app` | `test_serve.py` |
| A website you visit quietly calling the local API (cross-site requests) | Anything that changes data must come from the app's own origin; requests carrying another site's `Origin` get 403 | `main.py` (`guard`) | `test_requests_from_another_website_cannot_change_anything` |
| DNS-rebinding (a hostile domain made to point at your PC) | Requests must be addressed to `localhost` or `127.0.0.1`; any other `Host` header gets 400 | `main.py` (`guard`) | `test_requests_addressed_to_another_host_name_are_refused` |
| A file name that tries to escape the upload folder | File names are reduced to a safe base name; files are stored under a random key, never under the name you sent | `security/paths.py` | `test_a_hostile_file_name_cannot_escape_the_upload_folder`, `test_paths.py` |
| Path traversal through the API | Every path built from a request is resolved and checked to be inside its base folder | `security/paths.resolve_inside` | `test_resolve_inside_rejects_traversal_and_absolute_paths` |
| Linking files you did not mean to expose | "Add a video already on this PC" accepts only a full local path to an existing video file: no web addresses, network shares, device paths, folders or other file types | `security/paths.validate_import_path` | `test_import_path_*` |
| Huge or bogus uploads | Only MP4, MOV, MKV, WebM and M4V; a size limit enforced while the upload streams (20 GB by default); every file must pass an `ffprobe` check as a real video or it is deleted | `api/videos.py` | `test_upload_rejects_*`, `test_upload_larger_than_the_limit_is_refused` |
| Commands smuggled in through text fields | No shell is ever used. FFmpeg is started with an argument list; caption text reaches it through a file, not the command line; numbers in filter graphs are computed by the app and validated | `services/ffmpeg.py`, `editing/render.py`, `api/clips.merge_settings` | `test_clips_can_be_edited_*`, `test_edited_captions_are_validated_and_stored` |
| A pasted link reaching into this PC or your home network | Only `http` and `https` links to public hosts are accepted: no files, no `localhost`, no private or link-local addresses, no user names or passwords in the link | `services/downloader.validate_source_url` | `test_links_that_are_not_public_web_videos_are_refused_with_a_reason`, `test_the_api_refuses_links_that_are_not_public_web_videos` |
| The downloader being steered from outside ClipForge | yt-dlp runs with its plugins and configuration files switched off, no cookies, no browser profiles, no `.netrc`, and no code fetched at run time. File names come from ClipForge, never from the site. The JavaScript helper YouTube needs runs in a sandboxed runtime | `services/downloader._options` | `test_downloader.py` |
| Importing something you have no right to clip without noticing | An import is refused until you state your right to clip the video; the answer and the source link are stored with it | `api/videos.import_url` | `test_an_import_starts_only_after_you_say_why_you_may_clip_the_video` |
| A download filling the disk | The size limit for uploads also applies to imports, before and during the download, and free disk space is checked first | `services/downloader`, `jobs/pipeline._import` | `test_a_video_larger_than_the_limit_is_refused_before_it_fills_the_disk`, `test_an_import_is_refused_when_the_disk_is_too_full_for_it` |
| Exporting into an unexpected place | The export folder must be a full local path; relative and network paths are refused | `api/clips.export_clips` | `test_export_refuses_a_folder_that_is_not_a_full_local_path` |
| Credentials leaking from the database | Stored credentials are encrypted (Fernet: AES with an integrity check). The key is in `data/secret.key`, itself encrypted with Windows DPAPI for your Windows account | `security/secrets.py` | `test_secrets.py` |
| Credentials leaking through the interface | The API can store and delete a credential but never returns one; it only reports whether one is set | `services/settings_store.py` | `test_settings_are_validated_and_secrets_never_come_back` |
| Credentials leaking into logs | Anything shaped like a key, token, password or authorization header is masked before a log line is written, including inside error traces and web addresses | `logging_config.redact` | `test_logging.py` |
| Cleanup deleting the wrong thing | Cleanup acts only inside the data folder and never on source videos or approved clips | `api/system.cleanup` | `test_cleanup_*` |
| The page being framed or sniffed | `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`; API responses are not cached | `main.py` | |

## What it does not protect against

- **Someone using your Windows account.** There is no login. Anyone who can sit at this PC as you can open the
  app and read everything in `data`.
- **The database is not encrypted.** Transcripts, titles and settings are plain SQLite. Only credentials are
  encrypted. Use BitLocker if the disk itself needs protecting.
- **`CLIPFORGE_ALLOW_REMOTE=true` removes the main protection.** The app then has no access control at all and
  no HTTPS. Do not use it on a network you do not fully trust, and never expose it to the internet.
- **The link importer reads web pages you point it at.** yt-dlp is a large third-party program that parses
  whatever a site returns. It is pinned to a reviewed release and kept inside ClipForge's own environment,
  but it runs as you. Only paste links from sites you would open in your browser. A host name that secretly
  points at an address inside your network is not detected; only addresses written in the link are checked.
- **FFmpeg is not sandboxed.** Media files are parsed by FFmpeg and ffprobe running as you. They run as
  separate programs, so a crash fails the job rather than the app, but a malicious file that exploits an
  FFmpeg bug would have your user's permissions. Keep FFmpeg updated and be careful with files from strangers.
- **Stored credentials are tied to this Windows account on this PC.** After reinstalling Windows or moving to
  another PC they cannot be decrypted and have to be entered again. That is intended.

## What goes over the network

Nothing, unless you start it:

| When | What | Where to |
|---|---|---|
| `scripts\setup.ps1` | Python packages, interface packages; a speech model and the face detector only if they are not already on the PC, after asking | PyPI, npm, Hugging Face, GitHub |
| Settings → Speech models → Install | The model you chose | Hugging Face |
| `scripts\get-fonts.ps1` | Four open-licence font families | GitHub |
| `scripts\enable-gpu.ps1` | NVIDIA runtime libraries | PyPI |
| Checking or importing a link | The video's details, then the video itself | The site in the link you pasted, and the addresses that site serves its video from |
| Analysing a video with Claude as the engine (the default) | The video's transcript, title and creator name, never the video itself | Anthropic, through the Claude Code tool on this PC and your own Claude sign-in |
| ChatGPT's second opinion (on by default, skipped with "Local rules only") | The transcript, title, creator name and the picks | OpenAI, through the Codex tool from the ChatGPT app, only when it is signed in with ChatGPT (never an API key) |
| `scripts\review.ps1` (code reviews) | The uncommitted code changes | OpenAI, the same way |
| `scripts\update-downloader.ps1` | The newest yt-dlp release | PyPI |

There is no telemetry, no update check and no analytics. Your videos and clips are never uploaded.
Transcripts are sent only to Claude when it picks the clips and to ChatGPT when it checks them; set Settings → Who picks the clips to
"Local rules only" to keep everything on this PC. No paid API is ever called: the language-model review that would use one is not built, and the
setting that will allow paid requests is off by default.

## Third-party code

The three reference projects were read, not installed or run; see [docs/RESEARCH.md](docs/RESEARCH.md).
yt-dlp, which does the link imports, was checked before it was installed: the official PyPI release
2026.10.26, pure Python with no install scripts, published by its known maintainers, Unlicense. Every
Python dependency is pinned to an exact version in `backend/requirements.txt`, and the interface's
dependencies are locked in `frontend/package-lock.json`. The speech models come from the `Systran`
faster-whisper repositories on Hugging Face and the face detector from the OpenCV model zoo.

## Rules for the integrations that are not built yet

These are requirements, written down now so they are not negotiable later:

- **Official APIs and OAuth only.** No passwords typed into ClipForge, no session cookies, no hidden browser
  automation to get around a platform's posting rules.
- **Tokens are stored encrypted**, never sent to the interface, never written to logs, and every connected
  account has a visible disconnect control that deletes them.
- **A post is "published" only when the platform says so.** Until then it is "sending" or "failed", with the
  platform's own error message.
- **No fabricated numbers.** Analytics show only what a connected account returned or what you typed in, and
  each figure says which.
- **Public media URLs.** Where a platform must fetch the video from a public address, ClipForge will stage
  that one file at a temporary, unguessable address and remove it afterwards. Your localhost server is never
  exposed.
- **Paid requests are opt-in.** Any feature that costs money shows an estimate first and stays off until you
  switch it on.
- **Discovery is not a licence.** A video found through an API is a suggestion with a rights status of "not
  recorded". Importing it is a separate step that asks for your right to clip it first.

## Clipping other people's content

A public video is not automatically yours to cut up and repost. Each source in ClipForge has a rights status
(your own, licensed or campaign, creator gave permission, not allowed, not recorded) and a note for where the
permission comes from. Both are written into the post text of every clip, together with the reminder that
credit does not replace permission. The app does not stop you rendering a clip from a source marked "not
recorded"; it makes sure you can see that it is.

## If you find a problem

This is your own private installation, so there is nobody to report to but yourself. Useful places to look:
`data/logs/api.log`, `data/logs/worker.log`, and the tests named in the table above, which show the intended
behaviour.
