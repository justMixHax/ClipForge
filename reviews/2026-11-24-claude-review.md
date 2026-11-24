# Code review by Claude (code-review, medium) instead of ChatGPT

Reviewed: the changes not committed yet, on 2026-11-24 (prompt edits that see the video, split screen of two
chosen parts, the parts picker).

ChatGPT's Codex limit on the ChatGPT Go plan is reached until 30 November 2026, 9:02 PM, so Claude's code review
read the changes. After the reset: `scripts\review.ps1 -Commit <this commit>`.

Review comments:

- [nit] Parts were kept even with a layout that does not stack — backend\app\editing\prompt_edit.py
  **Fixed: parts of the picture are only kept for split screen and facecam over gameplay; with another layout
  they are left out and the edit says so. One test.**

- [pre-existing] A box typed by hand past the edge of the video is not refused — backend\app\api\clips.py
  Not changed in this round: the picker and the prompt edit keep boxes inside the picture, and only a request
  made by hand can save one outside it (the render then fails with FFmpeg's crop error).
