"""Importing a video from a link. The real downloader (yt-dlp) is used throughout, against a small web
server started on this PC for the test, so nothing here touches the internet."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.errors import JobCancelled
from app.services import downloader, ffmpeg
from app.services.downloader import LinkError
from tests.conftest import LocalSite

pytestmark = pytest.mark.ffmpeg


# ------------------------------------------------------------------------------------------ downloading
def test_a_video_link_is_downloaded_whole_and_is_playable(site: LocalSite, tmp_path: Path):
    seen: list[float] = []

    path, info = downloader.download_link(site.url("/clip.mp4"), tmp_path / "in", on_progress=seen.append)

    assert path == tmp_path / "in" / "source.mp4"
    assert path.read_bytes() == site.video
    assert ffmpeg.probe(path).duration == pytest.approx(3.0, abs=0.2)
    assert (info.title, info.url, info.is_live) == ("clip", site.url("/clip.mp4"), False)
    assert seen == sorted(seen) and seen[-1] == pytest.approx(1.0)
    assert [p.name for p in (tmp_path / "in").iterdir()] == ["source.mp4"]          # no partial files left


def test_looking_at_a_link_reports_what_it_is_without_saving_anything(site: LocalSite, tmp_path: Path, monkeypatch):  # noqa: ANN001
    monkeypatch.chdir(tmp_path)

    info = downloader.inspect_link(site.url("/clip.mp4"))

    assert (info.title, info.is_live, info.site) == ("clip", False, "Generic")
    assert info.url == site.url("/clip.mp4")
    assert list(tmp_path.iterdir()) == []                      # nothing written to the current folder...
    assert ("GET", "/clip.mp4") not in site.requests or len(site.requests) <= 3   # ...and the file was not fetched whole


def test_cancelling_stops_the_download_and_leaves_nothing_behind(slow_site: LocalSite, tmp_path: Path):
    progressed: list[float] = []

    with pytest.raises(JobCancelled):
        downloader.download_link(slow_site.url("/clip.mp4"), tmp_path / "in", on_progress=progressed.append,
                                 should_cancel=lambda: len(progressed) >= 2)

    assert progressed and progressed[-1] < 1.0
    assert not (tmp_path / "in").exists() or list((tmp_path / "in").iterdir()) == []


def test_a_video_larger_than_the_limit_is_refused_before_it_fills_the_disk(site: LocalSite, tmp_path: Path):
    with pytest.raises(LinkError) as refused:
        downloader.download_link(site.url("/clip.mp4"), tmp_path / "in", max_bytes=10_000)

    assert "limit" in refused.value.message
    assert not (tmp_path / "in").exists() or list((tmp_path / "in").iterdir()) == []


def test_a_link_to_a_video_that_is_gone_says_so(site: LocalSite, tmp_path: Path):
    with pytest.raises(LinkError) as failed:
        downloader.download_link(site.url("/gone.mp4"), tmp_path / "in")

    assert "could not be found" in failed.value.message
    assert not (tmp_path / "in").exists() or list((tmp_path / "in").iterdir()) == []


def test_a_page_with_no_video_on_it_is_refused(site: LocalSite):
    with pytest.raises(LinkError) as failed:
        downloader.inspect_link(site.url("/page.html"))

    assert "not a video" in failed.value.message


def test_a_site_that_cannot_be_reached_says_so(tmp_path: Path):
    with pytest.raises(LinkError) as failed:
        downloader.inspect_link("http://127.0.0.1:9/clip.mp4")          # nothing listens on port 9

    assert "could not be reached" in failed.value.message


# -------------------------------------------------------------------------------- which links are accepted
@pytest.mark.parametrize(("pasted", "accepted"), [
    ("https://www.youtube.com/watch?v=abc123XYZ_-", "https://www.youtube.com/watch?v=abc123XYZ_-"),
    ("  https://youtu.be/abc123  \n", "https://youtu.be/abc123"),
    ("youtube.com/watch?v=abc123", "https://youtube.com/watch?v=abc123"),
    ("HTTPS://WWW.Twitch.TV/videos/123456", "https://www.twitch.tv/videos/123456"),
    ("https://kick.com/someone/videos/0a1b2c", "https://kick.com/someone/videos/0a1b2c"),
])
def test_ordinary_video_links_are_accepted(pasted: str, accepted: str):
    assert downloader.validate_source_url(pasted) == accepted


@pytest.mark.parametrize(("pasted", "reason"), [
    ("", "Paste"),
    ("   ", "Paste"),
    ("just some words", "does not look like a link"),
    ("https:///watch?v=abc", "does not look like a link"),
    ("https://nodots/watch", "does not look like a link"),
    ("ftp://example.com/video.mp4", "web links"),
    ("javascript:alert(1)", "web links"),
    ("file:///C:/Videos/stream.mp4", "on this PC"),
    ("C:\\Videos\\stream.mp4", "on this PC"),
    ("\\\\server\\share\\stream.mp4", "on this PC"),
    ("https://someone:secret@example.com/video", "sign in"),
    ("http://localhost:3000/api/videos/1/stream", "home network"),
    ("http://127.0.0.1/video.mp4", "home network"),
    ("http://[::1]/video.mp4", "home network"),
    ("http://192.168.1.20/video.mp4", "home network"),
    ("http://10.0.0.5:8080/video.mp4", "home network"),
    ("http://169.254.10.10/video.mp4", "home network"),
    ("http://nas.local/video.mp4", "home network"),
    ("https://example.com/" + "a" * 2100, "too long"),
])
def test_links_that_are_not_public_web_videos_are_refused_with_a_reason(pasted: str, reason: str):
    with pytest.raises(LinkError) as refused:
        downloader.validate_source_url(pasted)

    assert reason in refused.value.message


# ------------------------------------------------------------------------------ what went wrong, in words
@pytest.mark.parametrize(("raw", "says"), [
    ("ERROR: [youtube] abc: Private video. Sign in if you've been granted access to this video", "not public"),
    ("ERROR: [youtube] abc: Join this channel to get access to members-only content like this video.", "not public"),
    ("ERROR: [youtube] abc: Sign in to confirm your age. This video may be inappropriate for some users.", "age-restricted"),
    ("ERROR: [youtube] abc: Sign in to confirm you’re not a bot. Use --cookies-from-browser or --cookies for the "
     "authentication. See https://github.com/yt-dlp/yt-dlp/wiki/FAQ for how to pass cookies", "asked for a sign-in"),
    ("ERROR: [youtube] abc: Video unavailable. The uploader has not made this video available in your country",
     "not available in your country"),
    ("ERROR: Unsupported URL: https://example.com/", "not a video"),
    ("ERROR: [youtube] abc: This live event will begin in 3 hours.", "has not started"),
    ("ERROR: [twitch:vod] 123: Video 123 does not exist", "could not be found"),
    ("ERROR: [youtube] abc: Video unavailable. This video has been removed by the uploader", "could not be found"),
    ("ERROR: [generic] Unable to download webpage: HTTP Error 404: Not Found (caused by <HTTPError 404: Not Found>)",
     "could not be found"),
    ("ERROR: [youtube] abc: Unable to download webpage: <urlopen error [Errno 11001] getaddrinfo failed>",
     "could not be reached"),
])
def test_download_failures_are_explained_in_plain_words(raw: str, says: str):
    error = downloader.explain_failure(raw)

    assert says in error.message
    assert "cookies" not in (error.message + error.hint).lower()        # never suggest handing over a login


def test_an_unrecognised_failure_keeps_the_sites_own_words_and_suggests_an_update():
    error = downloader.explain_failure("ERROR: [somesite] 42: The frobnicator returned an empty manifest")

    assert "could not be imported" in error.message
    assert "frobnicator returned an empty manifest" in error.hint
    assert "update-downloader" in error.hint


# ------------------------------------------------------------------- the helper program YouTube links need
@pytest.mark.parametrize(("name", "printed", "usable"), [
    ("deno", "deno 2.9.8 (long term support, release, x86_64-pc-windows-msvc)\nv8 14.0\ntypescript 5.9", "deno 2.9.8"),
    ("deno", "deno 1.46.3 (stable, release, x86_64-pc-windows-msvc)", ""),          # too old for the downloader
    ("node", "v26.13.0", "node 26.13.0"),
    ("node", "v22.0.0", "node 22.0.0"),
    ("node", "v20.11.1", ""),                                                       # too old
    ("node", "something unexpected", ""),
    ("deno", "", ""),
])
def test_only_a_new_enough_javascript_runtime_counts(name: str, printed: str, usable: str):
    assert downloader.usable_runtime(name, printed) == usable


def test_this_pc_reports_which_runtime_youtube_links_will_use():
    found = downloader.js_runtime()

    assert found == "" or found.split()[0] in ("deno", "node")


# ------------------------------------------------------------------ a download refused part-way is continued
@pytest.fixture()
def no_waiting(monkeypatch):  # noqa: ANN001
    monkeypatch.setattr(downloader, "RETRY_WAITS", (0.0, 0.0, 0.0, 0.0))


def test_a_download_the_site_refuses_once_is_tried_again_with_fresh_addresses(video_bytes: bytes, tmp_path: Path,
                                                                               no_waiting):  # noqa: ANN001
    """YouTube sometimes answers 403 part-way through a long download; asking again works."""
    site = LocalSite(video_bytes, refuse={3})          # the third request is the download itself
    try:
        path, info = downloader.download_link(site.url("/clip.mp4"), tmp_path / "in")
    finally:
        site.close()

    assert path.read_bytes() == video_bytes
    assert info.retries == 1
    assert site.gets > 3                                 # it looked at the link again, then downloaded


def test_a_site_that_keeps_refusing_gives_up_after_a_few_tries_and_cleans_up(video_bytes: bytes, tmp_path: Path,
                                                                             no_waiting):  # noqa: ANN001
    site = LocalSite(video_bytes, refuse=set(range(3, 100)))
    try:
        with pytest.raises(LinkError) as failed:
            downloader.download_link(site.url("/clip.mp4"), tmp_path / "in")
    finally:
        site.close()

    assert "refused" in failed.value.message.lower() and "403" in (failed.value.message + failed.value.hint)
    assert not (tmp_path / "in").exists() or list((tmp_path / "in").iterdir()) == []


def test_a_refusal_while_only_looking_at_the_link_is_not_retried(video_bytes: bytes, no_waiting):  # noqa: ANN001
    """A 403 on the page itself means the video is not available to us: asking again will not change that."""
    site = LocalSite(video_bytes, refuse=set(range(1, 100)))
    try:
        with pytest.raises(LinkError):
            downloader.inspect_link(site.url("/clip.mp4"))
    finally:
        site.close()

    assert site.gets <= 2


def test_cancelling_while_waiting_to_try_again_stops_at_once(video_bytes: bytes, tmp_path: Path, monkeypatch):  # noqa: ANN001
    import time

    monkeypatch.setattr(downloader, "RETRY_WAITS", (30.0, 30.0, 30.0, 30.0))
    site = LocalSite(video_bytes, refuse=set(range(3, 100)))
    started = time.monotonic()
    calls = {"n": 0}

    def cancel_after_the_first_refusal() -> bool:
        calls["n"] += 1
        return site.gets >= 3

    try:
        with pytest.raises(JobCancelled):
            downloader.download_link(site.url("/clip.mp4"), tmp_path / "in", should_cancel=cancel_after_the_first_refusal)
    finally:
        site.close()

    assert time.monotonic() - started < 10



def test_the_creators_handle_is_kept_when_the_site_gives_one():
    from app.services.downloader import _describe

    found = _describe({"title": "Agent 327", "channel": "Blender", "uploader_id": "@blender", "id": "x",
                       "webpage_url": "https://www.youtube.com/watch?v=x"}, "https://youtu.be/x")
    assert found.creator_handle == "@blender"

    found = _describe({"title": "A", "uploader": "Someone", "uploader_id": "UCabc123", "id": "y"}, "https://e.com/y")
    assert found.creator_handle == ""   # a channel id is not a handle
