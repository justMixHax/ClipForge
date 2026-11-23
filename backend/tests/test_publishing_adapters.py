"""The YouTube, TikTok and Instagram adapters against fakes of their documented APIs: sign-in, upload, resume,
confirmation and the errors a person needs explained."""
from __future__ import annotations

import hashlib
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from app.publishing.base import Draft, PublishError, Tokens
from app.publishing.instagram import Instagram
from app.publishing.oauth import begin, challenge_hex, challenge_s256, finish
from app.publishing.tiktok import TikTok, plan_chunks
from app.publishing.youtube import YouTube
from tests.fake_platforms import FakePlatforms, platforms  # noqa: F401  (fixture)

MB = 1024 * 1024
REDIRECT = "http://127.0.0.1:3000/api/publishing/oauth/callback"


def clip_file(tmp_path: Path, size: int = 3 * MB + 17) -> Path:
    path = tmp_path / "clip.mp4"
    path.write_bytes(os.urandom(size))
    return path


def draft(path: Path, **overrides) -> Draft:
    values = {"file": path, "title": "He lost his <phone> in his hand", "caption": "Clip from Tyler1",
              "hashtags": ["#funny", "#tyler1"], "privacy": "public", "options": {}}
    return Draft(**{**values, **overrides})


def run(adapter, tokens: Tokens, d: Draft, account: str = "acct", state: dict | None = None):  # noqa: ANN001, ANN201
    state = {} if state is None else state
    progress: list[float] = []
    outcome = adapter.publish(tokens, d, account=account, state=state, remember=lambda s: None,
                              on_progress=progress.append, should_cancel=lambda: False)
    return outcome, state, progress


# --------------------------------------------------------------------------------------------- sign-in basics
def test_the_standard_pkce_challenge_matches_the_rfc_example():
    assert challenge_s256("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk") == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"


def test_tiktok_gets_the_hex_form_of_the_hash_its_documentation_asks_for():
    assert challenge_hex("abc") == hashlib.sha256(b"abc").hexdigest()


def test_a_sign_in_state_works_once_and_only_for_ten_minutes(monkeypatch):  # noqa: ANN001
    state, verifier = begin("youtube", REDIRECT)
    pending = finish(state)
    assert pending is not None and pending.verifier == verifier and pending.platform == "youtube"
    assert finish(state) is None
    assert finish("made-up") is None and finish(None) is None

    old, _ = begin("tiktok", REDIRECT)
    later = time.time() + 601
    monkeypatch.setattr(time, "time", lambda: later)
    assert finish(old) is None


# --------------------------------------------------------------------------------------------------- YouTube
@pytest.fixture()
def yt(platforms: FakePlatforms) -> YouTube:  # noqa: F811
    return YouTube("cid.apps.googleusercontent.com", "csecret", endpoints=platforms.endpoints()["youtube"])


def google_tokens() -> Tokens:
    return Tokens(access_token="g-access-1", refresh_token="g-refresh-1", expires_at=time.time() + 3000)


def test_youtube_sign_in_asks_for_upload_and_read_access_with_pkce(yt: YouTube):
    query = parse_qs(urlsplit(yt.authorize_url(REDIRECT, "st", "v" * 64)).query)

    assert query["code_challenge_method"] == ["S256"] and query["code_challenge"] == [challenge_s256("v" * 64)]
    assert "youtube.upload" in query["scope"][0] and "youtube.readonly" in query["scope"][0]
    assert query["access_type"] == ["offline"] and query["state"] == ["st"] and query["redirect_uri"] == [REDIRECT]


def test_youtube_code_exchange_and_channel(yt: YouTube, platforms: FakePlatforms):  # noqa: F811
    tokens = yt.exchange("code-1", REDIRECT, "the-verifier")

    assert tokens.refresh_token == "g-refresh-1" and tokens.expires_at > time.time() + 3000
    form = platforms.calls("POST", "/google/token")[0].form
    assert form["code_verifier"] == "the-verifier" and form["code"] == "code-1" and form["redirect_uri"] == REDIRECT
    profile = yt.profile(tokens)
    assert (profile.external_id, profile.name, profile.handle) == ("UC_test_channel", "Forge Clips", "@forgeclips")


def test_a_google_account_without_a_channel_is_told_so(yt: YouTube, platforms: FakePlatforms):  # noqa: F811
    platforms.channels = []
    with pytest.raises(PublishError, match="no YouTube channel"):
        yt.profile(google_tokens())


def test_refreshing_keeps_the_refresh_token_google_does_not_send_again(yt: YouTube):
    fresh = yt.refresh(google_tokens())

    assert fresh.access_token == "g-access-2" and fresh.refresh_token == "g-refresh-1"


def test_a_sign_in_google_ended_asks_you_to_reconnect(yt: YouTube, platforms: FakePlatforms):  # noqa: F811
    platforms.google_token_status = 400
    with pytest.raises(PublishError) as caught:
        yt.refresh(google_tokens())
    assert caught.value.reconnect and "In production" in caught.value.hint


def test_youtube_upload_sends_the_whole_file_with_its_text_and_is_confirmed(yt: YouTube, platforms: FakePlatforms,  # noqa: F811
                                                                           tmp_path: Path):
    path = clip_file(tmp_path)

    outcome, state, progress = run(yt, google_tokens(), draft(path))

    session = platforms.youtube_sessions["s1"]
    assert bytes(session["data"]) == path.read_bytes()
    body = session["body"]
    assert body["snippet"]["title"] == "He lost his phone in his hand"          # < and > are not allowed
    assert body["snippet"]["tags"] == ["funny", "tyler1"]
    assert "Clip from Tyler1" in body["snippet"]["description"] and "#funny #tyler1" in body["snippet"]["description"]
    assert body["status"] == {"privacyStatus": "public", "selfDeclaredMadeForKids": False,
                              "containsSyntheticMedia": False}
    assert outcome.status == "published" and outcome.url == "https://youtube.com/shorts/vids1"
    assert progress[-1] == pytest.approx(1.0) and state["upload_session"].endswith("/upload-session/s1")
    assert session["query"]["uploadType"] == "resumable"
    first_put = platforms.calls("PUT", "/upload-session/")[0]
    size = path.stat().st_size
    assert first_put.headers["content-range"] == f"bytes 0-{size - 1}/{size}"


def test_a_scheduled_youtube_post_is_uploaded_private_with_its_publish_time(yt: YouTube, platforms: FakePlatforms,  # noqa: F811
                                                                           tmp_path: Path):
    when = datetime(2026, 12, 1, 18, 0, tzinfo=timezone.utc)

    outcome, _, _ = run(yt, google_tokens(), draft(clip_file(tmp_path), publish_at=when))

    status = platforms.youtube_sessions["s1"]["body"]["status"]
    assert status["privacyStatus"] == "private" and status["publishAt"] == "2026-12-01T18:00:00Z"
    assert outcome.status == "scheduled_on_platform" and "2026-12-01" in outcome.detail


def test_an_upload_cut_off_half_way_continues_where_it_stopped(yt: YouTube, platforms: FakePlatforms, tmp_path: Path):  # noqa: F811
    platforms.youtube_break_after = MB
    path = clip_file(tmp_path)

    outcome, _, _ = run(yt, google_tokens(), draft(path))

    assert outcome.status == "published"
    assert len(platforms.youtube_sessions) == 1                           # one upload, not two
    assert bytes(platforms.youtube_sessions["s1"]["data"]) == path.read_bytes()
    assert any(c.headers.get("content-range", "").startswith("bytes */") for c in platforms.calls("PUT", "/upload-session/"))


def test_trying_again_after_a_failed_attempt_resumes_the_same_upload(yt: YouTube, platforms: FakePlatforms,  # noqa: F811
                                                                    tmp_path: Path):
    path = clip_file(tmp_path)
    platforms.youtube_put_status = 503
    state: dict = {}
    with pytest.raises(PublishError) as caught:
        run(yt, google_tokens(), draft(path), state=state)
    assert caught.value.retry and "upload_session" in state

    platforms.youtube_put_status = None
    outcome, _, _ = run(yt, google_tokens(), draft(path), state=state)

    assert outcome.status == "published" and len(platforms.calls("POST", "/upload/youtube")) == 1


def test_youtube_rejecting_a_video_is_a_failure_with_the_reason(yt: YouTube, platforms: FakePlatforms, tmp_path: Path):  # noqa: F811
    platforms.youtube_upload_status = "rejected"
    with pytest.raises(PublishError, match="rejected.*copyright"):
        run(yt, google_tokens(), draft(clip_file(tmp_path)))


def test_a_used_up_daily_upload_limit_is_explained(yt: YouTube, platforms: FakePlatforms, tmp_path: Path):  # noqa: F811
    platforms.youtube_init_error = (403, "quotaExceeded")
    with pytest.raises(PublishError) as caught:
        run(yt, google_tokens(), draft(clip_file(tmp_path)))
    assert "daily" in caught.value.message and caught.value.retry


def test_a_title_youtube_cannot_take_is_refused_before_uploading(yt: YouTube, tmp_path: Path):
    with pytest.raises(PublishError, match="title"):
        run(yt, google_tokens(), draft(clip_file(tmp_path), title="<>"))


# ---------------------------------------------------------------------------------------------------- TikTok
@pytest.fixture()
def tt(platforms: FakePlatforms) -> TikTok:  # noqa: F811
    return TikTok("client-key", "client-secret", endpoints=platforms.endpoints()["tiktok"])


def tiktok_tokens() -> Tokens:
    return Tokens(access_token="t-access-1", refresh_token="t-refresh-1", expires_at=time.time() + 7000)


def test_the_chunk_plan_follows_tiktoks_rules():
    assert plan_chunks(3 * MB) == (3 * MB, 1)
    assert plan_chunks(64 * MB) == (64 * MB, 1)
    size = 150 * MB + 123
    chunk, count = plan_chunks(size)
    assert 5 * MB <= chunk <= 64 * MB and count == size // chunk and size - chunk * (count - 1) <= 128 * MB


def test_tiktok_sign_in_uses_the_hex_challenge_and_only_asks_to_upload(tt: TikTok):
    query = parse_qs(urlsplit(tt.authorize_url(REDIRECT, "st", "v" * 64)).query)

    assert query["client_key"] == ["client-key"] and query["scope"] == ["user.info.basic,video.upload"]
    assert query["code_challenge"] == [challenge_hex("v" * 64)] and query["code_challenge_method"] == ["S256"]


def test_tiktok_exchange_and_profile(tt: TikTok, platforms: FakePlatforms):  # noqa: F811
    tokens = tt.exchange("code-1", REDIRECT, "the-verifier")

    assert tokens.refresh_token == "t-refresh-1" and tokens.refresh_expires_at > time.time() + 100 * 86400
    assert platforms.calls("POST", "/v2/oauth/token/")[0].form["code_verifier"] == "the-verifier"
    profile = tt.profile(tokens)
    assert (profile.external_id, profile.name) == ("tt-open-1", "Forge TT")


def test_refreshing_a_tiktok_sign_in_replaces_the_refresh_token(tt: TikTok):
    fresh = tt.refresh(tiktok_tokens())
    assert (fresh.access_token, fresh.refresh_token) == ("t-access-2", "t-refresh-2")


def test_a_clip_sent_to_tiktok_arrives_whole_in_chunks_and_lands_in_the_inbox(tt: TikTok, platforms: FakePlatforms,  # noqa: F811
                                                                             tmp_path: Path, monkeypatch):  # noqa: ANN001
    monkeypatch.setattr(TikTok, "SINGLE_MAX", 5 * MB)
    monkeypatch.setattr(TikTok, "CHUNK", 5 * MB)
    path = clip_file(tmp_path, 12 * MB + 5)

    outcome, state, progress = run(tt, tiktok_tokens(), draft(path))

    assert bytes(platforms.tiktok_parts["p1"]) == path.read_bytes()
    puts = platforms.calls("PUT", "/tiktok-upload/")
    ranges = [c.headers["content-range"] for c in puts]
    assert all("transfer-encoding" not in c.headers and int(c.headers["content-length"]) == len(c.body) for c in puts)
    assert ranges == [f"bytes 0-{5 * MB - 1}/{12 * MB + 5}", f"bytes {5 * MB}-{12 * MB + 4}/{12 * MB + 5}"]
    assert outcome.status == "in_drafts" and outcome.external_id == "v_inbox_file~p1" and "TikTok" in outcome.detail
    assert state["publish_id"] == "v_inbox_file~p1" and progress[-1] == pytest.approx(1.0)


def test_tiktok_refusing_more_drafts_says_why(tt: TikTok, platforms: FakePlatforms, tmp_path: Path):  # noqa: F811
    platforms.tiktok_init_error = "spam_risk_too_many_pending_share"
    with pytest.raises(PublishError, match="5 drafts"):
        run(tt, tiktok_tokens(), draft(clip_file(tmp_path)))


def test_a_draft_you_later_post_from_the_app_is_confirmed(tt: TikTok, platforms: FakePlatforms):  # noqa: F811
    platforms.tiktok_statuses = ["PUBLISH_COMPLETE"]

    outcome = tt.check(tiktok_tokens(), "v_inbox_file~p1", account="tt-open-1", state={})

    assert outcome.status == "published" and "7300000000000000001" in outcome.detail


def test_a_video_tiktok_turns_down_is_explained(tt: TikTok, platforms: FakePlatforms, tmp_path: Path):  # noqa: F811
    platforms.tiktok_statuses = ["PROCESSING_UPLOAD", "FAILED"]
    with pytest.raises(PublishError, match="length"):
        run(tt, tiktok_tokens(), draft(clip_file(tmp_path)))


# ------------------------------------------------------------------------------------------------- Instagram
@pytest.fixture()
def ig(platforms: FakePlatforms) -> Instagram:  # noqa: F811
    return Instagram("app-id", "app-secret", endpoints=platforms.endpoints()["instagram"])


def meta_tokens() -> Tokens:
    return Tokens(access_token="fb-long-1", expires_at=time.time() + 50 * 86400,
                  refresh_expires_at=time.time() + 50 * 86400)


def test_a_pasted_token_becomes_a_long_lived_sign_in_and_finds_the_instagram_account(ig: Instagram,
                                                                                    platforms: FakePlatforms):  # noqa: F811
    tokens, profiles = ig.connect_with_token("short-lived-token")

    assert tokens.access_token == "fb-long-1" and tokens.refresh_expires_at > time.time() + 50 * 86400
    assert [(p.external_id, p.handle, p.extra["page"]) for p in profiles] == [
        ("17841400000000001", "@forge.clips", "Forge Page")]
    exchange = platforms.calls("POST", "/v27.0/oauth/access_token")[0]
    assert "client_secret" not in exchange.query and exchange.form["client_secret"] == "app-secret"


def test_a_bad_pasted_token_is_refused_plainly(ig: Instagram):
    with pytest.raises(PublishError, match="Meta"):
        ig.connect_with_token("expired-token")


def test_a_reel_is_uploaded_from_this_pc_and_published_once_it_is_ready(ig: Instagram, platforms: FakePlatforms,  # noqa: F811
                                                                       tmp_path: Path):
    path = clip_file(tmp_path)

    outcome, state, _ = run(ig, meta_tokens(), draft(path), account="17841400000000001")

    assert platforms.ig_uploads["c1"] == path.read_bytes() and platforms.ig_published == ["c1"]
    create = platforms.calls("POST", "/v27.0/17841400000000001/media")[0].form
    assert create["media_type"] == "REELS" and create["upload_type"] == "resumable"
    assert "Clip from Tyler1" in create["caption"] and "#funny" in create["caption"]
    assert outcome.status == "published" and outcome.url == "https://www.instagram.com/reel/media-c1/"
    assert state["media"] == "media-c1"


def test_trying_again_after_a_lost_answer_does_not_post_twice(ig: Instagram, platforms: FakePlatforms,  # noqa: F811
                                                             tmp_path: Path):
    state = {"container": "c1", "uploaded": True, "media": "media-c1"}

    outcome, _, _ = run(ig, meta_tokens(), draft(clip_file(tmp_path)), account="17841400000000001", state=state)

    assert platforms.ig_published == [] and outcome.status == "published"


def test_a_reel_instagram_cannot_process_is_reported(ig: Instagram, platforms: FakePlatforms, tmp_path: Path):  # noqa: F811
    platforms.ig_statuses = ["ERROR"]
    with pytest.raises(PublishError, match="could not process"):
        run(ig, meta_tokens(), draft(clip_file(tmp_path)), account="17841400000000001")
    assert platforms.ig_published == []


def test_a_reel_still_processing_is_checked_again_later(ig: Instagram, platforms: FakePlatforms, tmp_path: Path):  # noqa: F811
    platforms.ig_statuses = ["IN_PROGRESS"]
    outcome, state, _ = run(ig, meta_tokens(), draft(clip_file(tmp_path)), account="17841400000000001")
    assert outcome.status == "processing" and outcome.check_again_in and platforms.ig_published == []

    platforms.ig_statuses = ["FINISHED"]
    outcome = ig.check(meta_tokens(), outcome.external_id, account="17841400000000001", state=state)

    assert outcome.status == "published" and platforms.ig_published == ["c1"]
