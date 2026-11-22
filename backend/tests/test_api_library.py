"""Library actions through the HTTP API: variants, caption files, post text, export to a folder,
manual order, cleanup, storage and the timeline. Renders are real (tiny) FFmpeg renders."""
from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import db
from app.db import utcnow
from app.jobs import pipeline, queue
from app.models import Job, TranscriptSegment
from tests.test_api import client, clip, upload, uploaded  # noqa: F401  (fixtures shared with the main API tests)

pytestmark = pytest.mark.ffmpeg


def add_transcript(video_id: int) -> None:
    with db.session_scope() as s:
        s.add(TranscriptSegment(video_id=video_id, idx=0, start=0.5, end=1.5, text="That was fucking incredible.",
                                words=[["That", 0.5, 0.7, 0.9], ["was", 0.7, 0.9, 0.9], ["fucking", 0.9, 1.2, 0.9],
                                       ["incredible.", 1.2, 1.5, 0.9]]))
        s.add(TranscriptSegment(video_id=video_id, idx=1, start=1.7, end=2.4, text="No way.",
                                words=[["No", 1.7, 1.9, 0.9], ["way.", 1.9, 2.4, 0.9]]))


def render_now(client: TestClient, clip_id: int) -> dict:
    """Ask for a render and do what the worker would do, so the test does not depend on a second process."""
    assert client.post(f"/api/clips/{clip_id}/render").status_code == 200
    with db.session_scope() as s:
        job = queue.claim_next(s, "test-worker")
        assert job is not None and job.type == "render"
        job_id = job.id
    pipeline.run_job(job_id)
    body = client.get(f"/api/clips/{clip_id}").json()
    assert body["render_status"] == "ready", body["render_error"]
    return body


# -------------------------------------------------------------------------------------------- variants
def test_a_variant_is_an_independent_copy_linked_to_the_original(client: TestClient, clip: dict):
    client.patch(f"/api/clips/{clip['id']}", json={"settings": {"captions": {"preset": "gaming"}}})

    variant = client.post(f"/api/clips/{clip['id']}/duplicate", params={"label": "Bolder hook"}).json()

    assert variant["id"] != clip["id"]
    assert (variant["origin"], variant["variant_of"], variant["variant_label"]) == ("variant", clip["id"], "Bolder hook")
    assert (variant["start"], variant["end"], variant["title"]) == (clip["start"], clip["end"], clip["title"])
    assert variant["settings"]["captions"]["preset"] == "gaming"
    assert variant["render_status"] == "none"                      # nothing is rendered until asked

    client.patch(f"/api/clips/{variant['id']}", json={"settings": {"captions": {"preset": "minimal"}}, "title": "B"})
    original = client.get(f"/api/clips/{clip['id']}").json()
    assert original["settings"]["captions"]["preset"] == "gaming" and original["title"] == clip["title"]


def test_a_variant_of_a_variant_still_points_at_the_first_clip(client: TestClient, clip: dict):
    first = client.post(f"/api/clips/{clip['id']}/duplicate").json()
    second = client.post(f"/api/clips/{first['id']}/duplicate").json()

    assert second["variant_of"] == clip["id"]


# --------------------------------------------------------------------------------------- caption files
def test_caption_files_are_word_for_word_and_timed_from_the_start_of_the_clip(client: TestClient, clip: dict):
    add_transcript(clip["video_id"])                               # the clip runs from 0.5 s to 2.5 s

    srt = client.get(f"/api/clips/{clip['id']}/subtitles.srt")
    vtt = client.get(f"/api/clips/{clip['id']}/subtitles.vtt")

    assert srt.status_code == 200 and vtt.status_code == 200
    assert srt.text.splitlines()[1].startswith("00:00:00,000 --> ")     # first word is spoken as the clip starts
    assert "fucking" in srt.text.lower()                                # masking is for the picture only
    assert vtt.text.startswith("WEBVTT") and "No way." in vtt.text
    assert 'filename="My pick.srt"' in srt.headers["content-disposition"]


def test_caption_files_download_even_when_the_title_has_unusual_characters(client: TestClient, clip: dict):
    add_transcript(clip["video_id"])
    title = "Clutch “1 HP” 😀 — wow"
    assert client.patch(f"/api/clips/{clip['id']}", json={"title": title}).status_code == 200

    response = client.get(f"/api/clips/{clip['id']}/subtitles.srt")

    assert response.status_code == 200, response.text
    disposition = response.headers["content-disposition"]
    assert disposition.startswith('attachment; filename="Clutch _1 HP_ _ _ wow.srt"')       # plain name for old clients
    assert "filename*=UTF-8''Clutch%20%E2%80%9C1%20HP%E2%80%9D%20%F0%9F%98%80%20%E2%80%94%20wow.srt" in disposition


def test_only_srt_and_vtt_caption_files_are_offered(client: TestClient, clip: dict):
    assert client.get(f"/api/clips/{clip['id']}/subtitles.txt").status_code == 404


# ------------------------------------------------------------------------------------------- post text
def test_the_post_text_names_the_source_and_warns_when_rights_are_not_recorded(client: TestClient, clip: dict):
    text = client.get(f"/api/clips/{clip['id']}/post-kit").text

    assert "My pick" in text and "Video: My Stream" in text
    assert "Rights: Not recorded: check before posting" in text
    assert "heuristic estimate, not a prediction" in text

    client.patch(f"/api/videos/{clip['video_id']}", json={
        "creator_name": "Kai", "permission_status": "licensed", "permission_note": "Campaign #12",
        "source_url": "https://www.youtube.com/watch?v=abc"})
    text = client.get(f"/api/clips/{clip['id']}/post-kit").text
    assert "Creator: Kai" in text and "Rights: Licensed (campaign or agreement) (Campaign #12)" in text
    assert "https://www.youtube.com/watch?v=abc&t=0s" in text      # link opens at the moment the clip starts


# ---------------------------------------------------------------------------------------------- export
def test_export_copies_rendered_clips_with_their_post_text_and_numbers_them(client: TestClient, clip: dict,
                                                                            data_dir: Path):
    add_transcript(clip["video_id"])
    rendered = render_now(client, clip["id"])

    first = client.post("/api/clips/export", json={"ids": [clip["id"]]}).json()
    second = client.post("/api/clips/export", json={"ids": [clip["id"]]}).json()

    folder = Path(first["folder"])
    assert folder.parent == data_dir / "exports" and second["folder"] == first["folder"]
    assert first["files"] == ["01 - My pick.mp4"] and second["files"] == ["02 - My pick.mp4"]   # never overwrites
    assert (folder / "01 - My pick.mp4").stat().st_size == rendered["file_size"] > 10_000
    assert "My pick" in (folder / "01 - My pick.txt").read_text(encoding="utf-8")
    assert "-->" in (folder / "01 - My pick.srt").read_text(encoding="utf-8")
    assert first["skipped"] == []


def test_export_skips_clips_that_are_not_rendered_and_says_which(client: TestClient, clip: dict):
    result = client.post("/api/clips/export", json={"ids": [clip["id"]]}).json()

    assert result["files"] == [] and result["skipped"] == ["My pick"]


def test_export_uses_the_folder_chosen_in_settings(client: TestClient, clip: dict, tmp_path: Path):
    render_now(client, clip["id"])
    target = tmp_path / "My Clips"
    assert client.put("/api/settings", json={"values": {"export.folder": str(target)}}).status_code == 200

    result = client.post("/api/clips/export", json={"ids": [clip["id"]]}).json()

    assert Path(result["folder"]) == target and (target / "01 - My pick.mp4").is_file()


@pytest.mark.parametrize("folder", ["clips\\out", "..\\..\\somewhere", "\\\\server\\share\\clips"])
def test_export_refuses_a_folder_that_is_not_a_full_local_path(client: TestClient, clip: dict, folder: str):
    client.put("/api/settings", json={"values": {"export.folder": folder}})

    response = client.post("/api/clips/export", json={"ids": [clip["id"]]})

    assert response.status_code == 400 and "full local path" in response.json()["error"]


# ---------------------------------------------------------------------------------------- manual order
def test_resetting_your_order_goes_back_to_ranking_by_score(client: TestClient, uploaded: dict):
    ids = [client.post(f"/api/videos/{uploaded['id']}/clips", json={"start": s, "end": s + 0.8}).json()["id"]
           for s in (0.1, 1.0, 2.0)]
    with db.session_scope() as s:
        from app.models import Clip
        for clip_id, score in zip(ids, (40.0, 90.0, 65.0)):
            s.get(Clip, clip_id).score = score
    client.post("/api/clips/reorder", json={"ids": ids})
    assert [c["id"] for c in client.get("/api/clips", params={"sort": "rank"}).json()["items"]] == ids

    assert client.post("/api/clips/reset-order", params={"video_id": uploaded["id"]}).json() == {"reset": 3}

    by_score = [ids[1], ids[2], ids[0]]
    assert [c["id"] for c in client.get("/api/clips", params={"sort": "rank"}).json()["items"]] == by_score
    assert all(c["manual_rank"] is None for c in client.get("/api/clips").json()["items"])


# --------------------------------------------------------------------------------------------- cleanup
def test_cleanup_of_rejected_clips_deletes_their_files_and_nothing_else(client: TestClient, uploaded: dict,
                                                                         data_dir: Path):
    keep, drop = (client.post(f"/api/videos/{uploaded['id']}/clips", json={"start": s, "end": s + 1.0}).json()["id"]
                  for s in (0.2, 1.5))
    render_now(client, keep)
    render_now(client, drop)
    client.patch(f"/api/clips/{drop}", json={"review_status": "rejected"})
    client.patch(f"/api/clips/{keep}", json={"review_status": "approved"})

    freed = client.post("/api/system/cleanup", json={"what": "rejected"}).json()["freed_bytes"]

    assert freed > 10_000
    rejected, approved = client.get(f"/api/clips/{drop}").json(), client.get(f"/api/clips/{keep}").json()
    assert rejected["render_status"] == "none" and not rejected["file_url"]         # the clip itself is kept
    assert client.get(f"/api/clips/{drop}/file").status_code == 404
    assert approved["render_status"] == "ready" and client.get(f"/api/clips/{keep}/file").status_code == 200
    assert len(list((data_dir / "uploads").rglob("*.mp4"))) == 1                     # the source is untouched


def test_cleanup_of_working_files_keeps_the_source_and_the_saved_transcript(client: TestClient, uploaded: dict,
                                                                             data_dir: Path):
    work = next((data_dir / "uploads").iterdir()).name                              # folders are named by storage key
    folder = data_dir / "work" / work
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "audio.wav").write_bytes(b"0" * 5000)
    (folder / "audio.npz").write_bytes(b"0" * 300)
    (folder / "transcript.json").write_text("{}", encoding="utf-8")

    freed = client.post("/api/system/cleanup", json={"what": "work"}).json()["freed_bytes"]

    assert freed == 5300
    assert not (folder / "audio.wav").exists() and (folder / "transcript.json").exists()
    assert len(list((data_dir / "uploads").rglob("*.mp4"))) == 1


def test_working_files_of_a_video_that_is_being_processed_are_left_alone(client: TestClient, uploaded: dict,
                                                                          data_dir: Path):
    folder = data_dir / "work" / next((data_dir / "uploads").iterdir()).name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "audio.wav").write_bytes(b"0" * 5000)
    assert client.post(f"/api/videos/{uploaded['id']}/analyze", json={"count": 3}).status_code == 200   # queued

    assert client.post("/api/system/cleanup", json={"what": "work"}).json()["freed_bytes"] == 0
    assert (folder / "audio.wav").exists()


def test_old_job_history_can_be_trimmed_without_touching_recent_jobs(client: TestClient, uploaded: dict):
    with db.session_scope() as s:
        s.add(Job(type="analyze", status="succeeded", video_id=uploaded["id"], finished_at=utcnow() - timedelta(days=10)))
        s.add(Job(type="analyze", status="failed", video_id=uploaded["id"], finished_at=utcnow() - timedelta(days=1)))

    assert client.post("/api/system/cleanup", json={"what": "failed_jobs"}).status_code == 200

    left = client.get("/api/jobs").json()
    assert [(j["status"]) for j in left] == ["failed"]


def test_cleanup_only_accepts_the_three_known_actions(client: TestClient):
    assert client.post("/api/system/cleanup", json={"what": "everything"}).status_code == 422


# ----------------------------------------------------------------------------------- storage, timeline
def test_storage_use_is_reported_for_each_kind_of_file(client: TestClient, uploaded: dict, data_dir: Path):
    usage = client.get("/api/system/storage").json()

    video_and_cover = uploaded["size_bytes"]                       # the upload folder also holds a small cover image
    assert video_and_cover <= usage["areas"]["uploads"] < video_and_cover + 200_000
    assert usage["areas"]["clips"] == 0 and usage["areas"]["exports"] == 0
    assert usage["total"] >= usage["areas"]["uploads"] and usage["disk_free"] > 0
    assert Path(usage["data_dir"]) == data_dir


def test_the_timeline_shows_existing_clips_and_says_when_a_video_is_not_analysed_yet(client: TestClient, clip: dict):
    timeline = client.get(f"/api/videos/{clip['video_id']}/timeline").json()

    assert timeline["analysed"] is False and timeline["envelope"] == []
    assert [(c["id"], c["start"], c["end"]) for c in timeline["clips"]] == [(clip["id"], 0.5, 2.5)]
    assert timeline["duration"] == pytest.approx(3.0, abs=0.2)


def test_live_stats_report_the_queue_and_this_pcs_load(client: TestClient, uploaded: dict):
    client.post(f"/api/videos/{uploaded['id']}/analyze", json={"count": 3})

    stats = client.get("/api/system/stats").json()

    assert stats["queue_length"] == 1 and stats["running"] is None
    assert 0 <= stats["cpu_percent"] <= 100 and stats["memory"]["total_gb"] > 0


def test_installing_an_unknown_speech_model_is_refused(client: TestClient):
    assert client.post("/api/models/not-a-model/install").status_code == 404


# -------------------------------------------------------------------------------------------- projects
def test_projects_can_be_created_listed_renamed_and_deleted(client: TestClient):
    created = client.post("/api/projects", json={"name": "  GTA nights  ", "description": "Friday streams"}).json()
    assert (created["name"], created["video_count"], created["clip_count"]) == ("GTA nights", 0, 0)

    renamed = client.patch(f"/api/projects/{created['id']}", json={"name": "GTA Fridays"}).json()
    assert (renamed["name"], renamed["description"]) == ("GTA Fridays", "Friday streams")   # the note is kept
    assert [p["name"] for p in client.get("/api/projects").json()] == ["GTA Fridays"]

    assert client.delete(f"/api/projects/{created['id']}").json() == {"deleted": True}
    assert client.get("/api/projects").json() == []
    assert client.patch(f"/api/projects/{created['id']}", json={"name": "Gone"}).status_code == 404


@pytest.mark.parametrize("name", ["", "   ", "x" * 201])
def test_a_project_needs_a_real_name(client: TestClient, name: str):
    assert client.post("/api/projects", json={"name": name}).status_code == 422
    assert client.get("/api/projects").json() == []


def test_moving_a_video_into_a_project_takes_its_clips_along(client: TestClient, clip: dict):
    project = client.post("/api/projects", json={"name": "Shorts"}).json()

    moved = client.patch(f"/api/videos/{clip['video_id']}", json={"project_id": project["id"]})

    assert moved.status_code == 200 and moved.json()["project_id"] == project["id"]
    assert client.get(f"/api/clips/{clip['id']}").json()["project_id"] == project["id"]
    in_project = client.get("/api/clips", params={"project_id": project["id"]}).json()["items"]
    assert [c["id"] for c in in_project] == [clip["id"]]
    listed = client.get("/api/projects").json()[0]
    assert (listed["video_count"], listed["clip_count"]) == (1, 1)

    later = client.post(f"/api/videos/{clip['video_id']}/clips", json={"start": 0.1, "end": 1.0}).json()
    assert later["project_id"] == project["id"]                       # new clips of that video land there too

    assert client.patch(f"/api/videos/{clip['video_id']}", json={"project_id": None}).json()["project_id"] is None
    assert client.get(f"/api/clips/{clip['id']}").json()["project_id"] is None


def test_deleting_a_project_keeps_its_videos_and_clips(client: TestClient, clip: dict):
    project = client.post("/api/projects", json={"name": "Shorts"}).json()
    client.patch(f"/api/videos/{clip['video_id']}", json={"project_id": project["id"]})

    assert client.delete(f"/api/projects/{project['id']}").status_code == 200

    assert client.get(f"/api/videos/{clip['video_id']}").json()["project_id"] is None
    assert client.get(f"/api/clips/{clip['id']}").json()["project_id"] is None


def test_a_video_cannot_be_put_in_a_project_that_does_not_exist(client: TestClient, uploaded: dict, tiny_video: Path):
    response = client.patch(f"/api/videos/{uploaded['id']}", json={"project_id": 999})
    assert response.status_code == 404 and "project" in response.json()["error"].lower()

    refused = client.post("/api/videos/upload", content=tiny_video.read_bytes(), params={"project_id": 999},
                          headers={"X-Filename": "second.mp4"})
    assert refused.status_code == 404
    assert len(client.get("/api/videos").json()) == 1                 # nothing was kept from the refused upload


def test_an_upload_can_go_straight_into_a_project(client: TestClient, tiny_video: Path):
    project = client.post("/api/projects", json={"name": "Shorts"}).json()

    video = upload(client, tiny_video, "a.mp4", project_id=project["id"])

    assert video["project_id"] == project["id"]
    assert [v["id"] for v in client.get("/api/videos", params={"project_id": project["id"]}).json()] == [video["id"]]
