# Security and privacy

ClipForge is a single-user application for one PC. It has no login, because it only accepts connections from
the PC it runs on. This document says what that design protects against, how, where the limits are, what
goes over the network and when, and which rules the integrations follow. To report a security problem, see
the [last section](#reporting-a-security-problem).

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
| The YouTube API key ending up in a web address, a log or the interface | The key is stored encrypted like every credential, travels to YouTube in a request header and never in a web address, and is never returned by the API | `discovery/youtube_api.py`, `discovery/service.py` | `test_the_key_travels_in_a_header_and_never_in_the_address`, `test_the_key_is_kept_encrypted_and_never_comes_back` |
| Finding a video turning into downloading it | Creator Discovery only reads public details. An import is a separate step that needs a stated right to clip, from your record for that creator or said for that video. Nothing is imported there from a creator you marked "not allowed", whatever is said for one video: the record has to be changed first | `discovery/service.import_video` | `test_a_found_video_is_not_imported_until_you_say_why_you_may_clip_it`, `test_a_creator_you_marked_as_not_allowed_is_never_imported_from`, `test_a_right_said_for_one_video_does_not_get_around_not_allowed` |
| Words written by strangers (YouTube comments) steering Claude | Only comments that name a time are used, cut to one short line without control characters or text-direction tricks. Claude gets them inside a block that says they are hints from viewers and never instructions, runs with no tools, and its picks pass the same checks as without them | `discovery/signals.clean_excerpt`, `analysis/claude_review.viewer_hints` | `test_moments_viewers_wrote_in_the_comments_are_given_to_claude_as_hints`, `test_discovery_signals.py` |
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
  another PC they cannot be decrypted and have to be entered again. That is intended. ClipForge then says so
  instead of failing, and the first key you save puts the unreadable key file aside
  (`data\secret.key.unreadable-…`, kept in case it was this PC's after all) and starts a new one.

## What goes over the network

Nothing, unless you start it:

| When | What | Where to |
|---|---|---|
| `scripts\setup.ps1` | Python packages, interface packages; a speech model and the face detector only if they are not already on the PC, after asking | PyPI, npm, Hugging Face, GitHub |
| Settings → Speech models → Install | The model you chose | Hugging Face |
| `scripts\get-fonts.ps1` | Four open-licence font families | GitHub |
| `scripts\enable-gpu.ps1` | NVIDIA runtime libraries | PyPI |
| Checking or importing a link | The video's details, then the video itself | The site in the link you pasted, and the addresses that site serves its video from |
| Analysing a video with Claude as the engine (the default) | The video's transcript, title and creator name, never the video itself. For a video found through Creator Discovery, also the short lines from its public comments that name a moment | Anthropic, through the Claude Code tool on this PC and your own Claude sign-in |
| A prompt edit | What you wrote, the clip's settings and words, the names of the fonts on this PC, and two still pictures taken from the clip | Anthropic, the same way |
| Post text, a TikTok caption or a YouTube title and description, when you press the button for one | What is said in the clip, its title and the creator's name | Anthropic, the same way |
| ChatGPT's second opinion (on by default, skipped with "Local rules only") | The transcript, title, creator name and the picks | OpenAI, through the Codex tool from the ChatGPT app, only when it is signed in with ChatGPT (never an API key) |
| Creator Discovery: trying the key, finding, following or checking a channel | Your API key (in a header) and the ids of the channels and videos asked about. Nothing from your library | Google's YouTube Data API (`www.googleapis.com`) |
| Opening the Creator Discovery page | Your browser fetches the channel pictures and video thumbnails, as it would on youtube.com. No referrer is sent | YouTube's image servers (`i.ytimg.com`, `yt3.ggpht.com`) |
| Connecting an account in the Publishing Studio | The platform's own sign-in, in your browser; a one-time code comes back to this PC | Google, TikTok or Meta |
| A post whose time has come | The clip's video file, title, text and hashtags | The platform and account you chose for it |
| `scripts\review.ps1` (code reviews, for people changing ClipForge) | The uncommitted code changes | OpenAI, the same way as the second opinion |
| `scripts\update-downloader.ps1` | The newest yt-dlp release | PyPI |

There is no telemetry, no update check and no analytics. Your source videos are never uploaded anywhere. A
clip leaves this PC only when you publish it to an account you connected, and pictures from a clip go to
Claude only with a prompt edit. Transcripts are sent only to Claude when it picks the clips and to ChatGPT when
it checks them; set Settings → Who picks the clips to "Local rules only" and nothing is sent to either.

No paid API is ever called. Claude and ChatGPT are reached through the tools of your own plans, never with an
API key, and what they cost is whatever your plan already costs. The YouTube Data API is used inside the free
daily quota of your own Google Cloud project; ClipForge counts what it spends and shows it on the Creator
Discovery page.

## Third-party code

The reference projects were read for ideas; none of their code is in ClipForge or run by it. See
[docs/RESEARCH.md](docs/RESEARCH.md) for what was learned from each, and
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for everything setup installs and its licence.
yt-dlp, which does the link imports, was checked before it was installed: the official PyPI release
2026.10.26, pure Python with no install scripts, published by its known maintainers, Unlicense. Every
Python dependency is pinned to an exact version in `backend/requirements.txt`, and the interface's
dependencies are locked in `frontend/package-lock.json`. The speech models come from the `Systran`
faster-whisper repositories on Hugging Face and the face detector from the OpenCV model zoo.

## Publishing

Built on 22 November 2026, to these rules:

- **Official APIs and sign-in only**: YouTube Data API v3, TikTok Content Posting API, Instagram Graph API. No
  passwords typed into ClipForge, no session cookies, no hidden browser automation.
- **Sign-in comes back to this PC only.** YouTube returns you to `http://127.0.0.1:3000` and TikTok to
  `http://127.0.0.1:3000/api/publishing/oauth/callback`, with a one-time code. Every sign-in carries a random
  state that works once and for ten minutes, plus PKCE, so a code is no use to anyone else; a sign-in that did
  not start in ClipForge is refused. Instagram uses a token you make for your own app in Meta's Graph API
  Explorer; the backend exchanges it with your app secret.
- **App secrets and tokens are stored encrypted** in the same store as other credentials (a random key
  protected by Windows for your user account). They are never sent to the interface or written to logs, and
  they travel to the platforms in request bodies and headers, never in web addresses. Disconnecting revokes
  ClipForge's access where the platform allows it and deletes the stored sign-in.
- **Nothing on this PC is exposed.** Every platform receives the clip straight from this PC (YouTube and TikTok
  uploads, Instagram's resumable upload), so no public address is needed.
- **A post is "Published" only when the platform says so.** Until then it is waiting, uploading or processing;
  YouTube's own scheduling, private uploads and TikTok drafts are shown as what they are, failures with the
  platform's own words.
- **Safeguards**: only approved, rendered clips from a source whose right to post you recorded, checked again
  just before sending; the same clip goes to the same account once unless you ask; at most the daily limit in
  Settings per account in 24 hours; a passing failure is tried again twice, then reported.
- **TikTok direct posting is not offered**: TikTok's rules for it exclude tools for posting to your own
  accounts. ClipForge sends drafts to your inbox instead.

## Creator Discovery

Built on 24 November 2026, to these rules. It has been tested against a local stand-in of the YouTube Data
API; it has not yet made a request with a real key.

- **The official API only**: YouTube Data API v3, with a free key from your own Google Cloud project. No
  scraping, no sign-in, no cookies. Using it means agreeing to the
  [YouTube API Services Terms of Service](https://developers.google.com/youtube/terms/api-services-terms-of-service).
- **Public details only.** A channel's name, picture, description and public numbers; its newest uploads with
  their title, description, length, date, views, likes and comment count; and the top comments of long,
  finished videos. A hidden subscriber count stays unknown rather than guessed.
- **What is kept, and for how long.** Those details, and for each moment viewers pointed at one short line of
  the most-liked comment naming it, never who wrote it. Anything YouTube has not confirmed again for 30 days is
  deleted, as YouTube's rules for API data ask. Unfollowing a channel deletes what was found for it at once;
  videos you imported stay in your library.
- **Discovery is not a licence.** A found video is a suggestion. Importing it is a separate step that asks for
  your right to clip it first; the answer can be recorded once per creator, with a note saying where the
  permission comes from.
- **Numbers are counts, not predictions.** "8 of the top 100 comments are laughing" is a count of public
  comments. Nothing on the page claims a video will do well.
- **The quota is yours and it is counted.** Every request's cost in YouTube's units is added up per YouTube day
  and shown. A refused key or a used-up quota stops the automatic checks for six hours instead of asking again
  and again.

## Rules for the integrations that are not built yet

These are requirements, written down now so they are not negotiable later:
- **No fabricated numbers.** Analytics show only what a connected account returned or what you typed in, and
  each figure says which.
- **Public media URLs.** Where a platform must fetch the video from a public address, ClipForge will stage
  that one file at a temporary, unguessable address and remove it afterwards. Your localhost server is never
  exposed.
- **Paid requests are opt-in.** Any feature that costs money shows an estimate first and stays off until you
  switch it on.

## Clipping other people's content

A public video is not automatically yours to cut up and repost. Each source in ClipForge has a rights status
(your own, licensed or campaign, creator gave permission, not allowed, not recorded) and a note for where the
permission comes from. Both are written into the post text of every clip, together with the reminder that
credit does not replace permission. The app does not stop you rendering a clip from a source marked "not
recorded"; it makes sure you can see that it is.

## Reporting a security problem

Please do not open a public issue for something that could put other users at risk. Use **Report a
vulnerability** under the repository's **Security** tab on GitHub, which reaches the maintainer privately. Say
what you did, what happened, and what you expected; a failing test is the most useful report there is.

ClipForge is maintained by one person in their spare time, so there is no promised response time and no
bounty. Anything else (a crash, a wrong result) is an ordinary issue; [CONTRIBUTING.md](CONTRIBUTING.md) says
what to include. Useful places to look first: `data/logs/api.log`, `data/logs/worker.log`, and the tests named
in the table above, which show the intended behaviour.
