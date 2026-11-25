# Contributing to ClipForge

Thanks for wanting to help. ClipForge is a local clipping studio for Windows: it runs on the user's own PC, with
their own Claude plan as the AI. This page says how to get it running for development and what a change needs
before it can go in.

## Getting it running

You need 64-bit Windows 10 or 11, [FFmpeg](https://www.gyan.dev/ffmpeg/builds/), Python 3.12 (or
[uv](https://docs.astral.sh/uv/)) and Node.js. From the project folder:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
```

```powershell
powershell -ExecutionPolicy Bypass -File scripts\start.ps1 -Dev
```

`-Dev` also starts the interface's development server on `http://localhost:5173`, which reloads as you edit.
[SETUP_WINDOWS.md](SETUP_WINDOWS.md) has the details and the troubleshooting.

## Running the tests

```powershell
powershell -ExecutionPolicy Bypass -File scripts\test.ps1
```

That runs the backend tests (about six and a half minutes with FFmpeg installed; add `-All` for the slow ones
that load the real speech model, about a minute more) and type-checks the interface. [TESTING.md](TESTING.md) says what is covered, what is
not, and how the tests are built. In short:

- Tests run the real thing where they can: real FFmpeg renders on tiny generated videos, the real migrations,
  the real HTTP application.
- Nothing in the tests calls Claude, ChatGPT, YouTube, TikTok or Instagram. Each has a stand-in: a scripted
  answer for the AI tools, and a local web server built from the platform's documentation for the APIs
  (`backend/tests/fake_platforms.py`, `backend/tests/fake_youtube_data.py`).
- A test's name says what the user gets, in plain words:
  `test_a_found_video_is_not_imported_until_you_say_why_you_may_clip_it`.

## What a change needs

1. **A test that fails without it.** Write the test first and watch it fail; then make it pass. Tests added
   after the fact have missed real bugs in this project more than once.
2. **All tests passing**, and the interface building.
3. **The documents kept true.** If a change makes a sentence in README, ROADMAP, TESTING, SECURITY or
   ARCHITECTURE wrong, change the sentence in the same commit.
4. **A database change comes with a migration** in `backend/alembic/versions`, and the model in
   `backend/app/models.py` says the same thing (a test compares them). A migration that rebuilds a table passes
   `table_kwargs={"sqlite_autoincrement": True}`, or the table forgets that ids are never reused.

## Rules the project keeps

These are not style preferences. They are what makes the app trustworthy, and a change that breaks one will not
be merged.

- **Nothing is presented as working unless it is built and tested.** The status list in
  `backend/app/services/features.py` is what the app shows; "working" means verified on a real PC, and an
  integration that has only met a stand-in says so.
- **No invented numbers.** No made-up views, retention, benchmarks or test results, anywhere. A score is
  labelled an estimate; a count is a count of something real, with where it came from.
- **Official routes only.** Platforms are reached through their official APIs and their own sign-in. No
  password or cookie scraping, no hidden browser automation, no working around access controls.
- **A public video is not a free video.** Every source carries a rights record, and nothing is imported from a
  link until the right to clip it is stated.
- **This PC only.** The server listens on `127.0.0.1`. Credentials are encrypted, never returned by the API and
  never written to a log. File names and paths from requests are validated. No shell is ever used to run a
  command built from input.
- **Paid requests are opt-in.** Anything that costs money shows an estimate first and stays off until it is
  switched on.

[SECURITY.md](SECURITY.md) lists the protections and the tests behind each.

## Style

- Comments and messages are written for a person: what the thing is for and why it is the way it is. Error
  messages say what happened and what to do next.
- Python: type hints, small functions, no new dependency without a reason. A dependency is pinned to an exact
  version in `backend/requirements.txt` after its source and licence have been looked at.
- TypeScript: the interface talks to the backend only through `frontend/src/lib/api.ts`.
- Keep a file's line endings as they are (`*.ps1` is CRLF; most other files are LF).

## Licence

ClipForge is under the [MIT licence](LICENSE). By sending a change you agree that it is offered under the same
licence, and that it is yours to offer.

## Reporting a problem

Open an issue with what you did, what you expected and what happened, and the last lines of
`data\logs\api.log` or `data\logs\worker.log` if they say anything (they never contain keys or tokens, but
read them before pasting: they do contain video titles and file paths from your PC).

For a security problem, please do not open a public issue; see [SECURITY.md](SECURITY.md).
