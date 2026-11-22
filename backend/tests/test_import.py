"""Importing a video from a link, as a job: download, add to the library with its source and rights,
optionally go straight on to finding clips. The real downloader runs against a web site on this PC."""
from __future__ import annotations

import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import db
from app.jobs import pipeline, queue
from app.models import Job, Notification, Video
from app.services import downloader, storage
from app.services.downloader import LinkInfo
from tests.conftest import LocalSite
from tests.test_api import client  # noqa: F401  (fixture)

pytestmark = pytest.mark.ffmpeg
RIGHTS = {"permission_status": "licensed", "permission_note": "Creative Commons Attribution", "creator_name": "Blender"}


def start_import(url: str, **fields) -> int:
    with db.session_scope() as s:
        return pipeline.enqueue_import(s, url, **{**RIGHTS, **fields}).id


def run_next() -> Job:
    """What the worker does: claim the next job and run it."""
    with db.session_scope() as s:
        job = queue.claim_next(s, "test-worker")
        assert job is not None, "no job queued"
        job_id = job.id
    pipeline.run_job(job_id)
    with db.session_scope() as s:
        job = s.get(Job, job_id)
        s.expunge(job)
        return job


def videos() -> list[Video]:
    with db.session_scope() as s:
        rows = list(s.query(Video).order_by(Video.id))
        for row in rows:
            s.expunge(row)
        return rows


def upload_folders(data_dir: Path) -> list[Path]:
    return [p for p in (data_dir / "uploads").iterdir() if p.is_dir()]


# ------------------------------------------------------------------------------------------- the job
def test_importing_a_link_adds_the_video_with_its_source_and_rights(data_dir: Path, site: LocalSite):
    start_import(site.url("/clip.mp4"))

    job = run_next()

    assert job.status == "succeeded", job.error
    assert [stage["status"] for stage in job.stages] == ["done", "done", "done"]
    (video,) = videos()
    assert job.result["video_id"] == video.id
    assert (video.source_type, video.source_url, video.storage_mode) == ("url", site.url("/clip.mp4"), "managed")
    assert (video.permission_status, video.permission_note, video.creator_name) == (
        "licensed", "Creative Commons Attribution", "Blender")
    assert video.title == "clip" and video.duration == pytest.approx(3.0, abs=0.2) and video.has_audio
    source = storage.video_source_path(video)
    assert source.read_bytes() == site.video and source.parent.parent == data_dir / "uploads"
    assert storage.data_path(video.thumb_path).stat().st_size > 1000
    with db.session_scope() as s:
        note = s.query(Notification).one()
        assert "clip" in note.title and note.link == f"/clipper?video={video.id}"


def test_a_title_you_typed_wins_over_the_sites_title(data_dir: Path, site: LocalSite):
    start_import(site.url("/clip.mp4"), title="Friday stream, part 2")
    run_next()

    assert videos()[0].title == "Friday stream, part 2"


def test_an_import_can_go_straight_on_to_finding_clips(data_dir: Path, site: LocalSite):
    start_import(site.url("/clip.mp4"), analyze={"count": 3, "length": "10-20"})

    run_next()

    with db.session_scope() as s:
        waiting = s.query(Job).filter_by(status="queued").one()
        assert (waiting.type, waiting.video_id) == ("analyze", videos()[0].id)
        assert waiting.payload["options"]["count"] == 3


def test_a_failed_import_says_why_and_leaves_nothing_behind(data_dir: Path, site: LocalSite):
    start_import(site.url("/gone.mp4"))

    job = run_next()

    assert job.status == "failed" and "could not be found" in job.error
    assert videos() == [] and upload_folders(data_dir) == []
    with db.session_scope() as s:
        note = s.query(Notification).one()
        assert note.kind == "job_failed" and "could not be found" in note.body


def test_cancelling_an_import_removes_the_partial_download(data_dir: Path, slow_site: LocalSite):
    job_id = start_import(slow_site.url("/clip.mp4"))

    def cancel() -> None:
        with db.session_scope() as s:
            queue.request_cancel(s, job_id)

    timer = threading.Timer(0.8, cancel)
    timer.start()
    try:
        job = run_next()
    finally:
        timer.cancel()

    assert job.status == "cancelled"
    assert videos() == [] and upload_folders(data_dir) == []


def test_asking_for_the_same_link_twice_while_it_waits_queues_it_once(data_dir: Path, site: LocalSite):
    first = start_import(site.url("/clip.mp4"))
    second = start_import(site.url("/clip.mp4"))

    assert first == second
    with db.session_scope() as s:
        assert s.query(Job).count() == 1


def test_the_same_video_imported_again_is_flagged_as_a_duplicate(data_dir: Path, site: LocalSite):
    start_import(site.url("/clip.mp4"))
    first = run_next()
    start_import(site.url("/clip.mp4"))
    second = run_next()

    assert second.status == "succeeded"
    assert second.result["duplicate_of"] == first.result["video_id"]


def test_an_import_interrupted_by_closing_the_app_goes_back_in_the_queue_and_finishes_later(
        data_dir: Path, slow_site: LocalSite):
    start_import(slow_site.url("/clip.mp4"))
    timer = threading.Timer(0.8, pipeline.SHUTDOWN.set)
    timer.start()
    try:
        paused = run_next()
    finally:
        timer.cancel()
        pipeline.SHUTDOWN.clear()

    assert paused.status == "queued" and videos() == []
    assert len(upload_folders(data_dir)) == 1                          # the partial download is kept for next time

    slow_site.delay = 0.0
    resumed = run_next()

    assert resumed.status == "succeeded", resumed.error
    assert len(videos()) == 1 and len(upload_folders(data_dir)) == 1   # the same folder was continued, not a second one
    assert storage.video_source_path(videos()[0]).read_bytes() == slow_site.video


def test_an_import_is_refused_when_the_disk_is_too_full_for_it(data_dir: Path, site: LocalSite, monkeypatch):  # noqa: ANN001
    real = downloader._describe

    def with_a_known_size(info, url):  # noqa: ANN001, ANN202  (a plain file link does not state its size)
        found = real(info, url)
        found.approx_bytes = 50 * 1024**3
        return found

    monkeypatch.setattr(downloader, "_describe", with_a_known_size)
    from collections import namedtuple
    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(pipeline.shutil, "disk_usage", lambda path: usage(500 * 1024**3, 490 * 1024**3, 10 * 1024**3))
    monkeypatch.setenv("CLIPFORGE_MAX_UPLOAD_GB", "100")
    from app import config
    config.reset_settings_cache()
    start_import(site.url("/clip.mp4"))

    job = run_next()

    assert job.status == "failed" and "free disk space" in job.error
    assert videos() == [] and upload_folders(data_dir) == []


# ------------------------------------------------------------------------------------------- the API
PUBLIC = "https://www.youtube.com/watch?v=abc123XYZ_-"


def ask(client: TestClient, **body):  # noqa: ANN201
    return client.post("/api/videos/import-url", json={"url": PUBLIC, "permission_status": "owned", **body})


def test_an_import_starts_only_after_you_say_why_you_may_clip_the_video(client: TestClient):
    for status in ("unknown", "denied"):
        refused = ask(client, permission_status=status)
        assert refused.status_code == 400 and "allowed to clip" in refused.json()["error"]
    assert client.post("/api/videos/import-url", json={"url": PUBLIC}).status_code == 422       # rights must be sent
    assert client.get("/api/jobs").json() == []

    accepted = ask(client, permission_status="permission", permission_note="DM from the creator, 3 Nov")

    assert accepted.status_code == 200
    job = accepted.json()
    assert (job["type"], job["status"], job["video_id"]) == ("import", "queued", None)
    assert [stage["key"] for stage in job["stages"]] == ["inspect", "download", "register"]
    with db.session_scope() as s:
        payload = s.get(Job, job["id"]).payload
        assert (payload["url"], payload["permission_status"], payload["permission_note"]) == (
            PUBLIC, "permission", "DM from the creator, 3 Nov")


def test_asking_the_api_twice_for_the_same_link_gives_the_same_job(client: TestClient):
    assert ask(client).json()["id"] == ask(client, url="  " + PUBLIC + "  ").json()["id"]


@pytest.mark.parametrize(("url", "reason"), [
    ("http://localhost:3000/api/videos/1/stream", "home network"),
    ("http://192.168.1.20/video.mp4", "home network"),
    ("C:\\Videos\\stream.mp4", "on this PC"),
    ("https://someone:secret@example.com/video", "sign in"),
    ("ftp://example.com/video.mp4", "web links"),
])
def test_the_api_refuses_links_that_are_not_public_web_videos(client: TestClient, url: str, reason: str):
    for endpoint, body in (("import-url", {"url": url, "permission_status": "owned"}), ("inspect-url", {"url": url})):
        refused = client.post(f"/api/videos/{endpoint}", json=body)
        assert refused.status_code == 400 and reason in refused.json()["error"], endpoint
    assert client.get("/api/jobs").json() == []


def test_an_import_cannot_be_aimed_at_a_project_that_does_not_exist(client: TestClient):
    assert ask(client, project_id=999).status_code == 404
    assert client.get("/api/jobs").json() == []


def test_checking_a_link_describes_the_video_and_warns_about_size_and_repeats(client: TestClient, monkeypatch):  # noqa: ANN001
    found = LinkInfo(url=PUBLIC, title="Agent 327", creator="Blender", creator_url="https://www.youtube.com/@blender",
                     site="Youtube", video_id="abc123XYZ_-", duration=231.0, is_live=False,
                     license="Creative Commons Attribution license (reuse allowed)", approx_bytes=64_000_000,
                     width=1920, height=1080)
    asked: list[tuple[str, int]] = []

    def fake_inspect(url: str, *, max_height: int = 1080, max_bytes=None) -> LinkInfo:  # noqa: ANN001
        asked.append((url, max_height))
        return found

    monkeypatch.setattr(downloader, "inspect_link", fake_inspect)

    body = client.post("/api/videos/inspect-url", json={"url": " " + PUBLIC, "max_height": 720}).json()

    assert asked == [(PUBLIC, 720)]
    assert (body["title"], body["creator"], body["site"], body["duration"]) == ("Agent 327", "Blender", "Youtube", 231.0)
    assert body["license"].startswith("Creative Commons") and body["approx_bytes"] == 64_000_000
    assert body["over_limit"] is False and body["existing_video_id"] is None

    found.approx_bytes = 30 * 1024**3                                  # bigger than the 20 GB limit
    with db.session_scope() as s:
        s.add(Video(title="Agent 327", storage_key=storage.new_key(), file_path="uploads/x/source.mp4",
                    source_type="url", source_url=PUBLIC))
    again = client.post("/api/videos/inspect-url", json={"url": PUBLIC}).json()
    assert again["over_limit"] is True and again["limit_gb"] == 20
    assert again["existing_video_id"] is not None
