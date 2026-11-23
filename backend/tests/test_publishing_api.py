"""The publishing API: setting up your developer apps, connecting accounts through the platforms' own sign-in,
queueing and managing posts, and never letting a secret out."""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from app import db
from app.models import Account, Clip, Post, Secret, Video
from app.publishing import store
from app.publishing.base import Profile, Tokens
from app.services import storage
from tests.fake_platforms import FakePlatforms, platforms  # noqa: F401  (fixture)
from tests.test_api import BASE

CALLBACK = "http://127.0.0.1:3000/api/publishing/oauth/callback"   # TikTok: registered in your TikTok app
GOOGLE_RETURN = "http://127.0.0.1:3000"                             # Google documents only the bare loopback form


@pytest.fixture()
def client(data_dir, platforms: FakePlatforms):  # noqa: ANN001, ANN201, F811
    from app.main import create_app

    with TestClient(create_app(), base_url=BASE) as c:
        yield c


def set_up(client: TestClient, platform: str, client_id: str = "cid.apps.googleusercontent.com") -> None:
    response = client.put(f"/api/publishing/apps/{platform}", json={"client_id": client_id,
                                                                    "client_secret": "GOCSPX-very-secret"})
    assert response.status_code == 200, response.text


def connect_youtube(client: TestClient) -> dict:
    set_up(client, "youtube")
    url = client.post("/api/publishing/connect/youtube").json()["url"]
    state = parse_qs(urlsplit(url).query)["state"][0]
    page = client.get("/api/publishing/oauth/callback", params={"state": state, "code": "code-1"})
    assert page.status_code == 200, page.text
    return client.get("/api/publishing").json()["accounts"][0]


@pytest.fixture()
def clip_id(data_dir) -> int:  # noqa: ANN001
    with db.session_scope() as s:
        video = Video(title="Scam call center", original_filename="s.mp4", storage_key=storage.new_key(),
                      storage_mode="linked", file_path="C:/nowhere/s.mp4", duration=7380, width=1920, height=1080,
                      creator_name="Tyler1", permission_status="permission")
        s.add(video)
        s.flush()
        clip = Clip(video_id=video.id, storage_key=storage.new_key(), title="The microwave badge number",
                    start=10.0, end=50.0, review_status="approved", render_status="ready")
        s.add(clip)
        s.flush()
        folder = storage.clip_dir(clip)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "clip.mp4").write_bytes(os.urandom(64 * 1024))
        clip.file_path = storage.rel_path(folder / "clip.mp4")
        return clip.id


def tiktok_account() -> int:
    with db.session_scope() as s:
        store.save_app(s, "tiktok", "ckey", "csecret")
        return store.upsert_account(s, "tiktok", Profile("tt-open-1", "Forge TT"),
                                    Tokens("t-access-1", "t-refresh-1", time.time() + 7000, time.time() + 86400)).id


# ------------------------------------------------------------------------------------------- apps and sign-in
def test_app_credentials_are_kept_encrypted_and_the_secret_never_comes_back(client: TestClient):
    set_up(client, "youtube")

    overview = client.get("/api/publishing").json()
    youtube = next(p for p in overview["platforms"] if p["platform"] == "youtube")
    assert youtube["configured"] and youtube["client_id"] == "cid.apps.googleusercontent.com"
    assert youtube["redirect_uri"] == GOOGLE_RETURN
    assert next(p for p in overview["platforms"] if p["platform"] == "tiktok")["redirect_uri"] == CALLBACK
    assert "GOCSPX-very-secret" not in json.dumps(overview)
    with db.session_scope() as s:
        assert b"GOCSPX" not in s.get(Secret, "publish.app.youtube").ciphertext

    assert client.delete("/api/publishing/apps/youtube").status_code == 200
    assert not next(p for p in client.get("/api/publishing").json()["platforms"] if p["platform"] == "youtube")["configured"]


def test_an_unknown_platform_or_a_half_filled_app_is_refused(client: TestClient):
    assert client.put("/api/publishing/apps/myspace", json={"client_id": "a", "client_secret": "b"}).status_code == 400
    assert client.put("/api/publishing/apps/youtube", json={"client_id": "a", "client_secret": " "}).status_code == 400


def test_connecting_youtube_goes_through_googles_sign_in_and_keeps_only_the_account_name(client: TestClient,
                                                                                         platforms: FakePlatforms):  # noqa: F811
    set_up(client, "youtube")
    url = client.post("/api/publishing/connect/youtube").json()["url"]
    query = parse_qs(urlsplit(url).query)
    assert url.startswith(f"{platforms.base}/google/auth") and query["redirect_uri"] == [GOOGLE_RETURN]

    landing = client.get("/", params={"state": query["state"][0], "code": "code-1", "scope": "x"},
                         follow_redirects=False)                    # where Google sends you back
    assert landing.status_code in (302, 307)
    assert landing.headers["location"].startswith("/api/publishing/oauth/callback?state=")
    page = client.get(landing.headers["location"])

    assert page.status_code == 200 and "Forge Clips" in page.text and "text/html" in page.headers["content-type"]
    overview = client.get("/api/publishing")
    assert [(a["platform"], a["name"], a["status"]) for a in overview.json()["accounts"]] == [
        ("youtube", "Forge Clips", "connected")]
    assert "g-access-1" not in overview.text and "g-refresh-1" not in overview.text
    with db.session_scope() as s:
        stored = [row for row in s.query(Secret) if row.name.startswith("publish.account.")]
        assert len(stored) == 1 and b"g-refresh-1" not in stored[0].ciphertext
    again = client.get("/api/publishing/oauth/callback", params={"state": query["state"][0], "code": "code-1"})
    assert again.status_code == 400                                      # a sign-in link works once
    assert platforms.calls("POST", "/google/token")[0].form["redirect_uri"] == GOOGLE_RETURN


def test_the_home_page_without_a_sign_in_is_just_the_home_page(client: TestClient):
    assert client.get("/", params={"video": "6"}, follow_redirects=False).status_code not in (302, 307)


def test_a_sign_in_that_did_not_start_in_clipforge_is_refused(client: TestClient):
    page = client.get("/api/publishing/oauth/callback", params={"state": "made-up", "code": "stolen"})

    assert page.status_code == 400 and "expired" in page.text
    assert client.get("/api/publishing").json()["accounts"] == []


def test_saying_no_on_the_platforms_page_is_reported_plainly(client: TestClient):
    page = client.get("/api/publishing/oauth/callback", params={"error": "access_denied", "state": "x"})

    assert page.status_code == 400 and "not connected" in page.text


def test_what_the_platform_says_is_shown_safely(client: TestClient):
    page = client.get("/api/publishing/oauth/callback",
                      params={"error": "<script>alert(1)</script>", "state": "x"})

    assert "<script>" not in page.text and "&lt;script&gt;" in page.text


def test_instagram_connects_with_a_pasted_token(client: TestClient, platforms: FakePlatforms):  # noqa: F811
    set_up(client, "instagram", "1234567890")

    response = client.post("/api/publishing/instagram/connect", json={"token": "short-lived-token"})

    assert response.status_code == 200, response.text
    assert [(a["platform"], a["handle"]) for a in response.json()] == [("instagram", "@forge.clips")]
    assert response.json()[0]["reconnect_by"] is not None
    assert "fb-long-1" not in client.get("/api/publishing").text


def test_disconnecting_revokes_and_forgets_the_sign_in_but_keeps_the_history(client: TestClient,
                                                                            platforms: FakePlatforms, clip_id: int):  # noqa: F811
    account = connect_youtube(client)
    with db.session_scope() as s:
        s.add(Post(clip_id=clip_id, account_id=account["id"], platform="youtube", status="published",
                   clip_title="The microwave badge number", account_name="Forge Clips"))

    response = client.post(f"/api/publishing/accounts/{account['id']}/disconnect")

    assert response.status_code == 200, response.text
    assert client.get("/api/publishing").json()["accounts"] == []
    assert platforms.calls("POST", "/google/revoke")
    with db.session_scope() as s:
        assert not [row for row in s.query(Secret) if row.name.startswith("publish.account.")]
        (post,) = s.query(Post).all()
        assert post.account_id is None and post.account_name == "Forge Clips"


# ---------------------------------------------------------------------------------------------------- posts
def test_posts_can_be_queued_listed_cancelled_retried_and_removed(client: TestClient, clip_id: int):
    account_id = tiktok_account()
    when = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()

    queued = client.post("/api/publishing/posts", json={"items": [{
        "clip_id": clip_id, "account_id": account_id, "caption": "Clip from Tyler1", "hashtags": ["tyler1", "#funny"],
        "scheduled_at": when}]})

    assert queued.status_code == 200, queued.text
    (post,) = queued.json()
    assert post["status"] == "scheduled" and post["hashtags"] == ["#tyler1", "#funny"]
    assert "_state" not in post["options"]
    listed = client.get("/api/publishing/posts").json()
    assert [(p["id"], p["clip_title"], p["account_name"]) for p in listed] == [
        (post["id"], "The microwave badge number", "Forge TT")]
    assert listed[0]["thumb_url"] == f"/api/clips/{clip_id}/thumbnail"

    assert client.post(f"/api/publishing/posts/{post['id']}/cancel").json()["status"] == "cancelled"
    assert client.post(f"/api/publishing/posts/{post['id']}/retry").json()["status"] == "scheduled"
    assert client.post(f"/api/publishing/posts/{post['id']}/cancel").status_code == 200
    assert client.delete(f"/api/publishing/posts/{post['id']}").status_code == 200
    assert client.get("/api/publishing/posts").json() == []


def test_a_post_that_breaks_a_rule_is_refused_with_the_reason(client: TestClient, clip_id: int):
    account_id = tiktok_account()
    with db.session_scope() as s:
        s.get(Clip, clip_id).review_status = "pending"

    response = client.post("/api/publishing/posts", json={"items": [{"clip_id": clip_id, "account_id": account_id}]})

    assert response.status_code == 400 and "not approved" in response.json()["error"]


def test_posting_now_starts_at_once(client: TestClient, clip_id: int):
    account_id = tiktok_account()

    client.post("/api/publishing/posts", json={"items": [{"clip_id": clip_id, "account_id": account_id}]})

    jobs = client.get("/api/jobs?active=true").json()
    assert [job["type"] for job in jobs] == ["publish"]


# --------------------------------------------------------------------------------------------- honest status
def test_publishing_features_say_what_they_need(client: TestClient):
    features = {f["key"]: f for f in client.get("/api/system/features").json()}

    assert features["publish_youtube"]["status"] == "needs_setup" and "Google" in features["publish_youtube"]["detail"]
    assert features["publish_tiktok"]["status"] == "needs_setup" and "inbox" in features["publish_tiktok"]["detail"]
    assert features["publish_tiktok_direct"]["status"] == "blocked"
    assert features["publish_instagram"]["status"] == "needs_setup"
    assert features["scheduling"]["status"] == "needs_setup"


def test_a_platform_counts_as_working_only_after_a_confirmed_post(client: TestClient, clip_id: int):
    account = connect_youtube(client)
    features = {f["key"]: f for f in client.get("/api/system/features").json()}
    assert features["publish_youtube"]["status"] == "needs_setup" and "Forge Clips" in features["publish_youtube"]["detail"]

    with db.session_scope() as s:
        s.add(Post(clip_id=clip_id, account_id=account["id"], platform="youtube", status="published",
                   published_at=datetime.now(timezone.utc)))
    features = {f["key"]: f for f in client.get("/api/system/features").json()}
    assert features["publish_youtube"]["status"] == "working" and features["scheduling"]["status"] == "working"


def test_the_dashboard_counts_accounts_and_posts_from_the_database(client: TestClient, clip_id: int):
    account_id = tiktok_account()
    with db.session_scope() as s:
        s.add(Post(clip_id=clip_id, account_id=account_id, platform="tiktok", status="scheduled"))
        s.add(Post(clip_id=clip_id, account_id=account_id, platform="tiktok", status="published",
                   published_at=datetime.now(timezone.utc)))

    publishing = client.get("/api/dashboard").json()["publishing"]

    assert (publishing["connected_accounts"], publishing["scheduled"], publishing["published"]) == (1, 1, 1)
    assert publishing["verified_views"] is None


# ------------------------------------------------------------------------------------------ the assistant
def claude_engine(client: TestClient) -> None:
    assert client.put("/api/settings", json={"values": {"ai.engine": "claude"}}).status_code == 200


def test_claude_suggests_post_text_from_what_is_said_in_the_clip(client: TestClient, clip_id: int, monkeypatch):  # noqa: ANN001
    from app.analysis import claude_review

    asked: list[str] = []

    def fake_run(prompt, **kw):  # noqa: ANN001, ANN003, ANN202
        asked.append(prompt)
        return json.dumps({"type": "result", "is_error": False, "result": json.dumps({
            "title": "He gave the microwave as his badge number",
            "caption": "Tyler1 questions a scammer. Clip from Tyler1.", "hashtags": ["#tyler1", "scam", "#funny"]})})

    monkeypatch.setattr(claude_review, "run_claude", fake_run)
    claude_engine(client)
    with db.session_scope() as s:
        s.get(Clip, clip_id).transcript_text = "What is your badge number? It is the microwave."

    response = client.post("/api/publishing/suggest", json={"clip_id": clip_id, "platform": "youtube"})

    assert response.status_code == 200, response.text
    assert response.json() == {"title": "He gave the microwave as his badge number",
                               "caption": "Tyler1 questions a scammer. Clip from Tyler1.",
                               "hashtags": ["#tyler1", "#scam", "#funny"]}
    assert "microwave" in asked[0] and "Never invent" in asked[0] and "YouTube" in asked[0]


def test_when_claude_cannot_help_the_suggestion_says_why(client: TestClient, clip_id: int, monkeypatch):  # noqa: ANN001
    from app.analysis import claude_review

    def signed_out(prompt, **kw):  # noqa: ANN001, ANN003, ANN202
        raise claude_review.ClaudeUnavailable("Claude is signed out on this PC, so the local rules picked the clips.",
                                              hint=claude_review.SIGN_IN_HINT)

    monkeypatch.setattr(claude_review, "run_claude", signed_out)
    claude_engine(client)

    response = client.post("/api/publishing/suggest", json={"clip_id": clip_id, "platform": "tiktok"})

    assert response.status_code == 503 and "signed out" in response.json()["error"]


def test_with_local_rules_only_nothing_is_sent_to_claude(client: TestClient, clip_id: int, monkeypatch):  # noqa: ANN001
    from app.analysis import claude_review

    monkeypatch.setattr(claude_review, "run_claude", lambda *a, **k: pytest.fail("Claude was asked"))

    response = client.post("/api/publishing/suggest", json={"clip_id": clip_id, "platform": "tiktok"})

    assert response.status_code == 400 and "Settings" in response.json()["error"]


def test_a_date_range_lists_posts_by_when_they_go_live(client: TestClient, clip_id: int):
    account_id = tiktok_account()
    now = datetime.now(timezone.utc)
    with db.session_scope() as s:
        early = Post(clip_id=clip_id, account_id=account_id, platform="tiktok", status="published",
                     created_at=now - timedelta(days=2), published_at=now - timedelta(minutes=5))
        later = Post(clip_id=clip_id, account_id=account_id, platform="tiktok", status="scheduled",
                     created_at=now - timedelta(minutes=5), scheduled_at=now + timedelta(days=3))
        s.add_all([early, later])
        s.flush()
        early_id, later_id = early.id, later.id
    window = {"start": (now - timedelta(hours=1)).isoformat(), "end": (now + timedelta(hours=1)).isoformat()}

    listed = [p["id"] for p in client.get("/api/publishing/posts", params=window).json()]

    assert listed == [early_id] and later_id not in listed
