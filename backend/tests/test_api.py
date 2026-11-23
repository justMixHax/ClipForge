"""The HTTP API, exercised through the real application with a temporary data directory."""
from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from app import db
from app.models import Clip, Job, TranscriptSegment, Video
from tests.conftest import make_test_video

pytestmark = pytest.mark.ffmpeg
BASE = "http://127.0.0.1:3000"


@pytest.fixture()
def client(data_dir: Path):
    from app.main import create_app

    with TestClient(create_app(), base_url=BASE) as c:
        yield c


def upload(client: TestClient, path: Path, name: str | None = None, **params) -> dict:
    response = client.post("/api/videos/upload", content=path.read_bytes(), params=params,
                           headers={"X-Filename": quote(name or path.name), "Content-Type": "application/octet-stream"})
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture()
def uploaded(client: TestClient, tiny_video: Path) -> dict:
    return upload(client, tiny_video, "My Stream.mp4")


# ---------------------------------------------------------------------------------------------- basics
def test_health(client: TestClient):
    body = client.get("/api/health").json()
    assert body["status"] == "ok" and body["version"]


def test_unknown_api_routes_return_json_errors(client: TestClient):
    response = client.get("/api/nope")
    assert response.status_code == 404 and "error" in response.json()


# ---------------------------------------------------------------------------------------------- upload
def test_an_uploaded_video_is_probed_stored_and_listed(client: TestClient, uploaded: dict, data_dir: Path):
    assert uploaded["title"] == "My Stream"
    assert (uploaded["width"], uploaded["height"], uploaded["has_audio"]) == (640, 360, True)
    assert uploaded["duration"] == pytest.approx(3.0, abs=0.2)
    assert uploaded["status"] == "ready" and uploaded["permission_status"] == "unknown"
    stored = list((data_dir / "uploads").rglob("*.mp4"))
    assert len(stored) == 1 and stored[0].stat().st_size == uploaded["size_bytes"]
    listed = client.get("/api/videos").json()
    assert [v["id"] for v in listed] == [uploaded["id"]]
    assert client.get(f"/api/videos/{uploaded['id']}/thumbnail").headers["content-type"] == "image/jpeg"


def test_upload_rejects_file_types_that_are_not_video(client: TestClient, tmp_path: Path):
    notes = tmp_path / "notes.txt"
    notes.write_text("hello")
    response = client.post("/api/videos/upload", content=notes.read_bytes(), headers={"X-Filename": "notes.txt"})

    assert response.status_code == 415
    assert "MP4" in response.json()["error"]


def test_upload_rejects_a_file_that_only_pretends_to_be_a_video(client: TestClient, data_dir: Path):
    response = client.post("/api/videos/upload", content=b"not a video at all" * 500, headers={"X-Filename": "fake.mp4"})

    assert response.status_code == 422
    assert "not a video" in response.json()["error"]
    assert list((data_dir / "uploads").rglob("*.*")) == []
    assert client.get("/api/videos").json() == []


def test_upload_larger_than_the_limit_is_refused(data_dir: Path, tiny_video: Path, monkeypatch):  # noqa: ANN001
    from app import config
    from app.main import create_app

    monkeypatch.setenv("CLIPFORGE_MAX_UPLOAD_GB", "0.000001")       # about 1 KB
    config.reset_settings_cache()
    with TestClient(create_app(), base_url=BASE) as client:
        response = client.post("/api/videos/upload", content=tiny_video.read_bytes(), headers={"X-Filename": "big.mp4"})

    assert response.status_code == 413
    assert list((data_dir / "uploads").rglob("*.*")) == []


def test_a_hostile_file_name_cannot_escape_the_upload_folder(client: TestClient, tiny_video: Path, data_dir: Path):
    video = upload(client, tiny_video, "../../../evil.mp4")

    assert video["title"] == "evil"
    stored = list(data_dir.rglob("*.mp4"))
    assert len(stored) == 1 and (data_dir / "uploads") in stored[0].parents
    assert not (data_dir.parent / "evil.mp4").exists()


def test_the_same_file_uploaded_twice_is_flagged_as_a_duplicate(client: TestClient, tiny_video: Path):
    first = upload(client, tiny_video)
    second = upload(client, tiny_video)

    assert second["duplicate_of"] == first["id"]


def test_video_playback_supports_seeking(client: TestClient, uploaded: dict):
    response = client.get(f"/api/videos/{uploaded['id']}/stream", headers={"Range": "bytes=0-99"})

    assert response.status_code == 206
    assert len(response.content) == 100
    assert response.headers["content-range"].startswith("bytes 0-99/")


# ------------------------------------------------------------------------------------- videos on disk
def test_a_video_already_on_disk_is_linked_not_copied_and_never_deleted(client: TestClient, tmp_path: Path, data_dir: Path):
    original = make_test_video(tmp_path / "recording.mkv", seconds=2)
    response = client.post("/api/videos/import-path", json={"path": str(original), "creator_name": "Me",
                                                            "permission_status": "owned"})
    assert response.status_code == 200, response.text
    video = response.json()

    assert video["storage_mode"] == "linked" and video["permission_status"] == "owned"
    assert list((data_dir / "uploads").rglob("*.*")) == []
    assert client.delete(f"/api/videos/{video['id']}").status_code == 200
    assert original.exists()


def test_linking_rejects_paths_that_are_missing_or_not_video(client: TestClient, tmp_path: Path):
    missing = client.post("/api/videos/import-path", json={"path": str(tmp_path / "nope.mp4")})
    script = tmp_path / "run.bat"
    script.write_text("echo")
    wrong = client.post("/api/videos/import-path", json={"path": str(script)})

    assert missing.status_code == 400 and "not found" in missing.json()["error"]
    assert wrong.status_code == 400 and "type" in wrong.json()["error"]


def test_deleting_an_uploaded_video_removes_its_files(client: TestClient, uploaded: dict, data_dir: Path):
    assert client.delete(f"/api/videos/{uploaded['id']}").status_code == 200

    assert list((data_dir / "uploads").rglob("*.*")) == []
    assert client.get(f"/api/videos/{uploaded['id']}").status_code == 404


def test_source_details_and_permission_can_be_recorded(client: TestClient, uploaded: dict):
    response = client.patch(f"/api/videos/{uploaded['id']}", json={
        "creator_name": "Kai", "permission_status": "licensed", "permission_note": "Campaign #12",
        "source_url": "https://www.youtube.com/watch?v=abc", "attribution": "Clip from @kai"})

    assert response.status_code == 200
    body = response.json()
    assert (body["creator_name"], body["permission_status"], body["permission_note"]) == ("Kai", "licensed", "Campaign #12")
    assert client.patch(f"/api/videos/{uploaded['id']}", json={"permission_status": "whatever"}).status_code == 422


# ------------------------------------------------------------------------------------------------ jobs
def test_asking_for_analysis_queues_one_job_even_if_asked_twice(client: TestClient, uploaded: dict):
    first = client.post(f"/api/videos/{uploaded['id']}/analyze", json={"count": 3, "length": "10-20"})
    second = client.post(f"/api/videos/{uploaded['id']}/analyze", json={"count": 3})

    assert first.status_code == 200 and first.json()["status"] == "queued"
    assert second.json()["id"] == first.json()["id"]
    jobs = client.get("/api/jobs", params={"active": True}).json()
    assert [j["id"] for j in jobs] == [first.json()["id"]]
    assert jobs[0]["stages"][0]["status"] == "pending"


def test_analysis_options_are_validated(client: TestClient, uploaded: dict):
    assert client.post(f"/api/videos/{uploaded['id']}/analyze", json={"count": 999}).status_code == 422
    assert client.post(f"/api/videos/{uploaded['id']}/analyze", json={"aspect": "7:3"}).status_code == 422
    assert client.post("/api/videos/9999/analyze", json={}).status_code == 404


def test_an_unknown_clip_length_is_refused_instead_of_quietly_becoming_the_default(client: TestClient, uploaded: dict):
    refused = client.post(f"/api/videos/{uploaded['id']}/analyze", json={"length": "10-40"})
    assert refused.status_code == 422 and "length" in refused.json()["error"]
    assert client.get("/api/jobs").json() == []
    assert client.put("/api/settings", json={"values": {"clips.length": "10-40"}}).status_code == 400

    for known in ("10-20", "20-40", "40-60", "60-90", "15-60"):
        assert client.put("/api/settings", json={"values": {"clips.length": known}}).status_code == 200
    assert client.post(f"/api/videos/{uploaded['id']}/analyze", json={"length": "20-40"}).status_code == 200
    # your own limits, without a named range, are still fine
    assert client.post(f"/api/videos/{uploaded['id']}/analyze", json={"min_len": 10, "max_len": 40}).status_code == 200


def test_a_queued_job_can_be_cancelled_and_retried(client: TestClient, uploaded: dict):
    job = client.post(f"/api/videos/{uploaded['id']}/analyze", json={}).json()

    cancelled = client.post(f"/api/jobs/{job['id']}/cancel").json()
    assert cancelled["status"] == "cancelled"
    retried = client.post(f"/api/jobs/{job['id']}/retry").json()
    assert retried["status"] == "queued"


# ----------------------------------------------------------------------------------------------- clips
@pytest.fixture()
def clip(client: TestClient, uploaded: dict) -> dict:
    response = client.post(f"/api/videos/{uploaded['id']}/clips", json={"start": 0.5, "end": 2.5, "title": "My pick"})
    assert response.status_code == 200, response.text
    return response.json()


def test_a_manual_clip_keeps_its_link_to_the_source(clip: dict, uploaded: dict):
    assert clip["video_id"] == uploaded["id"] and clip["origin"] == "manual"
    assert (clip["start"], clip["end"], clip["title"]) == (0.5, 2.5, "My pick")
    assert clip["review_status"] == "pending" and clip["render_status"] == "none"
    assert clip["settings"]["aspect"] == "9:16"


def test_clip_times_must_make_sense(client: TestClient, uploaded: dict, clip: dict):
    vid = uploaded["id"]
    assert client.post(f"/api/videos/{vid}/clips", json={"start": 2.0, "end": 1.0}).status_code == 400
    assert client.post(f"/api/videos/{vid}/clips", json={"start": 0.0, "end": 99.0}).status_code == 400
    assert client.patch(f"/api/clips/{clip['id']}", json={"start": -1}).status_code in (400, 422)
    assert client.patch(f"/api/clips/{clip['id']}", json={"end": 500}).status_code == 400


def test_clips_can_be_edited_approved_favourited_and_filtered(client: TestClient, clip: dict):
    cid = clip["id"]
    updated = client.patch(f"/api/clips/{cid}", json={"title": "Better title", "review_status": "approved",
                                                      "favorite": True, "hashtags": ["#gaming", "#fyp"],
                                                      "settings": {"layout": "fit_blur", "captions": {"preset": "gaming"}}}).json()

    assert (updated["title"], updated["review_status"], updated["favorite"]) == ("Better title", "approved", True)
    assert updated["settings"]["layout"] == "fit_blur" and updated["settings"]["captions"]["preset"] == "gaming"
    assert updated["settings"]["aspect"] == "9:16"                         # untouched settings are kept
    assert [c["id"] for c in client.get("/api/clips", params={"review_status": "approved"}).json()["items"]] == [cid]
    assert client.get("/api/clips", params={"review_status": "rejected"}).json()["items"] == []
    assert client.patch(f"/api/clips/{cid}", json={"review_status": "maybe"}).status_code == 422
    assert client.patch(f"/api/clips/{cid}", json={"settings": {"layout": "sideways"}}).status_code == 400


def test_edited_captions_are_validated_and_stored(client: TestClient, clip: dict):
    cid = clip["id"]
    words = [{"text": "Hello", "start": 0.1, "end": 0.5}, {"text": "there", "start": 0.5, "end": 0.9, "emph": True}]
    stored = client.patch(f"/api/clips/{cid}", json={"captions": words}).json()

    assert [w["text"] for w in stored["captions"]] == ["Hello", "there"] and stored["captions"][1]["emph"] is True
    bad = client.patch(f"/api/clips/{cid}", json={"captions": [{"text": "x", "start": 5, "end": 1}]})
    assert bad.status_code == 400
    assert client.patch(f"/api/clips/{cid}", json={"captions": None}).json()["captions"] is None


def test_requesting_a_render_queues_it(client: TestClient, clip: dict):
    job = client.post(f"/api/clips/{clip['id']}/render").json()

    assert job["type"] == "render" and job["status"] == "queued" and job["clip_id"] == clip["id"]
    assert client.get(f"/api/clips/{clip['id']}").json()["render_status"] == "queued"


def test_a_clip_that_is_not_rendered_has_no_file_to_download(client: TestClient, clip: dict):
    assert client.get(f"/api/clips/{clip['id']}/file").status_code == 404


def test_feedback_is_stored_with_the_clip_details(client: TestClient, clip: dict):
    cid = clip["id"]
    assert client.post(f"/api/clips/{cid}/feedback", json={"label": "too_much_context", "note": "needs setup"}).status_code == 200
    assert client.post(f"/api/clips/{cid}/feedback", json={"label": "meh"}).status_code == 422
    assert client.get(f"/api/clips/{cid}").json()["feedback"] == ["too_much_context"]


def test_batch_actions_and_manual_order(client: TestClient, uploaded: dict):
    ids = [client.post(f"/api/videos/{uploaded['id']}/clips", json={"start": s, "end": s + 0.8}).json()["id"]
           for s in (0.1, 1.0, 2.0)]

    assert client.post("/api/clips/batch", json={"ids": ids[:2], "action": "approve"}).json()["changed"] == 2
    assert client.post("/api/clips/reorder", json={"ids": [ids[2], ids[0], ids[1]]}).status_code == 200
    ordered = client.get("/api/clips", params={"sort": "rank"}).json()["items"]
    assert [c["id"] for c in ordered] == [ids[2], ids[0], ids[1]]
    assert client.post("/api/clips/batch", json={"ids": ids, "action": "delete"}).json()["changed"] == 3
    assert client.get("/api/clips").json()["total"] == 0


def test_deleting_a_clip_removes_it(client: TestClient, clip: dict):
    assert client.delete(f"/api/clips/{clip['id']}").status_code == 200
    assert client.get(f"/api/clips/{clip['id']}").status_code == 404


# ------------------------------------------------------------------------------------------ transcript
def test_transcript_can_be_read_searched_and_corrected(client: TestClient, uploaded: dict):
    vid = uploaded["id"]
    with db.session_scope() as s:
        s.add(TranscriptSegment(video_id=vid, idx=0, start=0.2, end=1.4, text="I was using the flashlight.",
                                words=[["I", 0.2, 0.3, 0.9], ["was", 0.3, 0.5, 0.9], ["using", 0.5, 0.8, 0.9],
                                       ["the", 0.8, 0.9, 0.9], ["flashlight.", 0.9, 1.4, 0.9]]))
        s.add(TranscriptSegment(video_id=vid, idx=1, start=1.6, end=2.4, text="That is incredible.",
                                words=[["That", 1.6, 1.8, 0.9], ["is", 1.8, 1.9, 0.9], ["incredible.", 1.9, 2.4, 0.9]]))

    segments = client.get(f"/api/videos/{vid}/transcript").json()
    assert [s["text"] for s in segments] == ["I was using the flashlight.", "That is incredible."]

    hits = client.get("/api/search", params={"q": "find the moment with the flashlight"}).json()["hits"]
    assert hits and hits[0]["video_id"] == vid and hits[0]["start"] == pytest.approx(0.2)

    fixed = client.patch(f"/api/videos/{vid}/transcript/{segments[1]['id']}", json={"text": "That is unbelievable."}).json()
    assert fixed["text"] == "That is unbelievable." and fixed["edited"] is True
    assert [w[0] for w in fixed["words"]] == ["That", "is", "unbelievable."]
    assert fixed["words"][2][1:3] == [1.9, 2.4]                             # timing is kept when the word count matches
    assert client.get("/api/search", params={"q": "unbelievable"}).json()["hits"]


# ------------------------------------------------------------------------------------- security rules
def test_requests_from_another_website_cannot_change_anything(client: TestClient, uploaded: dict):
    hostile = client.delete(f"/api/videos/{uploaded['id']}", headers={"Origin": "https://evil.example"})
    assert hostile.status_code == 403
    assert client.get(f"/api/videos/{uploaded['id']}").status_code == 200       # still there

    same_site = client.patch(f"/api/videos/{uploaded['id']}", json={"title": "Renamed"},
                             headers={"Origin": "http://127.0.0.1:3000"})
    assert same_site.status_code == 200


def test_requests_addressed_to_another_host_name_are_refused(client: TestClient):
    assert client.get("/api/health", headers={"Host": "attacker.example"}).status_code == 400


def test_settings_are_validated_and_secrets_never_come_back(client: TestClient):
    assert client.get("/api/settings").json()["values"]["processing.priority"] == "balanced"
    assert client.put("/api/settings", json={"values": {"processing.priority": "quality"}}).json()["values"][
        "processing.priority"] == "quality"
    assert client.put("/api/settings", json={"values": {"no.such.key": 1}}).status_code == 400
    assert client.put("/api/settings", json={"values": {"processing.priority": "warp"}}).status_code == 400

    secret = "sk-ant-super-secret-value-12345"
    assert client.put("/api/settings/secret", json={"name": "llm.anthropic_api_key", "value": secret}).status_code == 200
    body = client.get("/api/settings")
    assert body.json()["secrets"]["llm.anthropic_api_key"] is True
    assert secret not in body.text
    assert client.put("/api/settings/secret", json={"name": "llm.anthropic_api_key", "value": ""}).status_code == 200
    assert client.get("/api/settings").json()["secrets"]["llm.anthropic_api_key"] is False


# ------------------------------------------------------------------------------------- overview pages
def test_dashboard_counts_come_from_the_database(client: TestClient, uploaded: dict, clip: dict):
    client.patch(f"/api/clips/{clip['id']}", json={"review_status": "approved"})
    stats = client.get("/api/dashboard").json()

    assert stats["videos"]["total"] == 1 and stats["clips"]["total"] == 1
    assert stats["clips"]["approved"] == 1 and stats["clips"]["awaiting_review"] == 0
    assert stats["publishing"]["connected_accounts"] == 0 and stats["publishing"]["verified_views"] is None


def test_options_describe_the_presets_the_ui_offers(client: TestClient):
    options = client.get("/api/options").json()

    assert {"gaming", "podcast"} <= {p["key"] for p in options["content_presets"]}
    assert {"opus", "karaoke", "minimal", "gaming", "bold", "word"} == {p["key"] for p in options["caption_presets"]}
    assert "9:16" in {a["key"] for a in options["aspects"]} and options["fonts"]
    assert {"fast_gaming", "manual"} <= {p["key"] for p in options["workflow_presets"]}


def test_features_are_reported_honestly(client: TestClient):
    features = client.get("/api/system/features").json()
    by_name = {f["key"]: f for f in features}

    assert by_name["upload"]["status"] == "working"
    assert by_name["publish_tiktok_direct"]["status"] == "blocked"
    assert by_name["transcription"]["status"] == "needs_setup"          # no speech model in this test data folder
    assert all(f["status"] in ("working", "needs_setup", "unavailable", "blocked", "planned") and f["detail"]
               for f in features)


def test_nothing_unbuilt_is_presented_as_merely_needing_setup(client: TestClient):
    def statuses() -> dict[str, str]:
        return {f["key"]: f["status"] for f in client.get("/api/system/features").json()}

    before = statuses()
    assert before["youtube_url"] == "working"                         # link import is built (yt-dlp is installed)
    for key in ("llm_pass", "diarization", "discovery", "automation", "analytics", "strategist"):
        assert before[key] == "planned", key
    assert before["publish_tiktok_direct"] == "blocked"

    # storing a key or choosing a provider does not bring an unbuilt feature any closer to working
    assert client.put("/api/settings/secret", json={"name": "youtube.api_key", "value": "not-a-real-key"}).status_code == 200
    assert client.put("/api/settings", json={"values": {"llm.provider": "ollama"}}).status_code == 200
    after = statuses()
    assert after["discovery"] == "planned" and after["llm_pass"] == "planned"


def test_a_pc_without_a_graphics_card_is_not_told_a_third_party_is_blocking_it(client: TestClient):
    """The canned hardware report used by the tests has no NVIDIA card."""
    features = {f["key"]: f for f in client.get("/api/system/features").json()}

    for key in ("gpu_transcription", "gpu_encode"):
        assert features[key]["status"] == "unavailable", key
        assert "processor" in features[key]["detail"].lower()


def test_notifications_can_be_listed_and_marked_read(client: TestClient):
    from app.jobs.pipeline import notify

    with db.session_scope() as s:
        notify(s, "clips_ready", "3 moments found", "body", "/clipper")
    listed = client.get("/api/notifications").json()
    assert listed["unread"] == 1 and listed["items"][0]["title"] == "3 moments found"
    client.post("/api/notifications/read")
    assert client.get("/api/notifications").json()["unread"] == 0


def test_everything_is_still_there_after_a_restart(data_dir: Path, tiny_video: Path):
    from app.main import create_app

    with TestClient(create_app(), base_url=BASE) as first:
        video = upload(first, tiny_video)
        clip = first.post(f"/api/videos/{video['id']}/clips", json={"start": 0.5, "end": 2.0, "title": "Keep me"}).json()
    db.get_engine().dispose()

    with TestClient(create_app(), base_url=BASE) as second:        # a fresh start on the same data folder
        assert [v["id"] for v in second.get("/api/videos").json()] == [video["id"]]
        assert second.get(f"/api/clips/{clip['id']}").json()["title"] == "Keep me"
        assert second.get(f"/api/videos/{video['id']}/stream", headers={"Range": "bytes=0-9"}).status_code == 206
    with db.session_scope() as s:
        assert s.query(Video).count() == 1 and s.query(Clip).count() == 1 and s.query(Job).count() == 0


def test_testing_the_ai_connections_reports_each_one_separately(client: TestClient, monkeypatch):  # noqa: ANN001
    from app.analysis import chatgpt_review, claude_review

    monkeypatch.setattr(claude_review, "ping", lambda model="haiku", timeout=120: "OK")

    def signed_out(model="gpt-6.2-astra", timeout=120):  # noqa: ANN001, ANN202
        raise chatgpt_review.ChatGPTUnavailable("ChatGPT is signed out on this PC.", hint="Sign in.")

    monkeypatch.setattr(chatgpt_review, "ping", signed_out)

    body = client.post("/api/system/ai/test").json()

    assert body["claude"]["ok"] is True and "OK" in body["claude"]["detail"] and body["claude"]["model"] == "haiku"
    assert body["chatgpt"]["ok"] is False and "signed out" in body["chatgpt"]["detail"]
    assert body["chatgpt"]["model"] == "gpt-6.2-astra"


def test_jobs_can_be_listed_newest_finished_first(client: TestClient, uploaded: dict):
    """The work log needs finishing order; the default order is the queue's (priority first)."""
    from datetime import timedelta

    from app.db import utcnow

    now = utcnow()
    with db.session_scope() as s:
        old_render = Job(type="render", status="succeeded", priority=8, video_id=uploaded["id"],
                         finished_at=now - timedelta(hours=2))
        new_analysis = Job(type="analyze", status="succeeded", priority=0, video_id=uploaded["id"], finished_at=now)
        s.add_all([old_render, new_analysis])
        s.flush()
        expected = [new_analysis.id, old_render.id]

    listed = client.get("/api/jobs", params={"video_id": uploaded["id"], "sort": "recent"}).json()

    assert [j["id"] for j in listed] == expected

def test_the_finished_list_leaves_out_jobs_that_are_still_waiting(client: TestClient, uploaded: dict):
    from app.db import utcnow

    with db.session_scope() as s:
        s.add(Job(type="analyze", status="succeeded", video_id=uploaded["id"], finished_at=utcnow()))
        for _ in range(3):
            s.add(Job(type="render", status="queued", video_id=uploaded["id"]))

    listed = client.get("/api/jobs", params={"video_id": uploaded["id"], "sort": "recent", "limit": 2}).json()

    assert [j["status"] for j in listed] == ["succeeded"]


def test_the_chatgpt_model_setting_refuses_odd_characters(client: TestClient):
    assert client.put("/api/settings", json={"values": {"ai.chatgpt_model": "gpt & calc"}}).status_code == 400
    assert client.put("/api/settings", json={"values": {"ai.chatgpt_model": "gpt-6.2-astra"}}).status_code == 200


def test_settings_can_list_the_chatgpt_models_your_plan_offers(client: TestClient, tmp_path: Path, monkeypatch):  # noqa: ANN001
    import json

    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    assert client.get("/api/system/ai/chatgpt-models").json() == {"offered": []}   # not known yet

    (tmp_path / "models_cache.json").write_text(json.dumps({"models": [
        {"slug": "gpt-6-luna", "visibility": "list"}, {"slug": "gpt-reserve", "visibility": "hide"},
        {"slug": "gpt-5.6-terra", "visibility": "list"}]}), encoding="utf-8")

    assert client.get("/api/system/ai/chatgpt-models").json() == {"offered": ["gpt-6-luna", "gpt-5.6-terra"]}
