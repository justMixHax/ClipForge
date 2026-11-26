"""The publishing queue: what may be posted (the safeguards), when it runs, and what a post's status says after the
platform answers. The platforms are the local fakes; the jobs run exactly as the worker runs them."""
from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone

import pytest

from app import db
from app.errors import ClipForgeError
from app.jobs import pipeline, queue
from app.models import Account, Clip, Job, Notification, Post, Video
from app.publishing import service, store
from app.publishing.base import Profile, Tokens
from app.publishing.service import PostRequest
from app.services import settings_store, storage
from tests.fake_platforms import FakePlatforms, platforms  # noqa: F401  (fixture)

NOW = datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc)


@pytest.fixture()
def world(data_dir, platforms: FakePlatforms) -> dict[str, int]:  # noqa: ANN001, F811
    """A video you may clip, an approved and rendered clip of it, the three apps set up and one account on each."""
    with db.session_scope() as s:
        video = Video(title="Scam call center", original_filename="s.mp4", storage_key=storage.new_key(),
                      storage_mode="linked", file_path="C:/nowhere/s.mp4", duration=7380, width=1920, height=1080,
                      creator_name="Tyler1", permission_status="permission")
        s.add(video)
        s.flush()
        clip = Clip(video_id=video.id, storage_key=storage.new_key(), title="The microwave badge number",
                    hook_text="Badge number?", description="The microwave badge number\n\nClip from Tyler1",
                    hashtags=["#tyler1", "#funny"], start=10.0, end=50.0, review_status="approved",
                    render_status="ready")
        s.add(clip)
        s.flush()
        folder = storage.clip_dir(clip)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "clip.mp4").write_bytes(os.urandom(2 * 1024 * 1024 + 3))
        clip.file_path = storage.rel_path(folder / "clip.mp4")
        for platform, key in (("youtube", "cid"), ("tiktok", "ckey"), ("instagram", "app-id")):
            store.save_app(s, platform, key, "csecret")
        far = time.time() + 30 * 86400
        accounts = {
            "youtube": store.upsert_account(s, "youtube", Profile("UC_test_channel", "Forge Clips"),
                                            Tokens("g-access-1", "g-refresh-1", time.time() + 3000)),
            "tiktok": store.upsert_account(s, "tiktok", Profile("tt-open-1", "Forge TT"),
                                           Tokens("t-access-1", "t-refresh-1", time.time() + 7000, far)),
            "instagram": store.upsert_account(s, "instagram", Profile("17841400000000001", "Forge"),
                                              Tokens("fb-long-1", "", far, far)),
        }
        return {"video": video.id, "clip": clip.id, **{k: a.id for k, a in accounts.items()}}


def ask(world: dict[str, int], platform: str = "youtube", **overrides) -> PostRequest:
    values = {"clip_id": world["clip"], "account_id": world[platform], "title": "The microwave badge number",
              "caption": "Clip from Tyler1", "hashtags": ["#tyler1", "#funny"],
              "privacy": "public" if platform == "youtube" else ""}
    return PostRequest(**{**values, **overrides})


def queue_one(world: dict[str, int], platform: str = "youtube", **overrides) -> Post:
    with db.session_scope() as s:
        (post,) = service.queue_posts(s, [ask(world, platform, **overrides)], now=NOW)
        s.expunge(post)
        return post


def post_of(post_id: int) -> Post:
    with db.session_scope() as s:
        post = s.get(Post, post_id)
        s.expunge(post)
        return post


def run_publish(post_id: int) -> Job:
    with db.session_scope() as s:
        job = queue.enqueue(s, "publish", {"post_id": post_id}, priority=9, stages=pipeline.stage_list("publish"),
                            target=("post", post_id))
        job_id = job.id
    pipeline.run_job(job_id)
    with db.session_scope() as s:
        job = s.get(Job, job_id)
        s.expunge(job)
        return job


def change(model, row_id: int, **values) -> None:  # noqa: ANN001
    with db.session_scope() as s:
        row = s.get(model, row_id)
        for key, value in values.items():
            setattr(row, key, value)


# ------------------------------------------------------------------------------------------- the safeguards
def test_an_approved_rendered_clip_is_queued_for_a_connected_account(world: dict[str, int]):
    post = queue_one(world)

    assert (post.status, post.platform, post.title) == ("scheduled", "youtube", "The microwave badge number")
    assert post.next_run_at == NOW and post.scheduled_at is None
    assert (post.clip_title, post.account_name) == ("The microwave badge number", "Forge Clips")


@pytest.mark.parametrize("problem, said", [
    ({"review_status": "pending"}, "not approved"),
    ({"render_status": "none"}, "not been rendered"),
])
def test_a_clip_that_is_not_ready_is_not_queued(world: dict[str, int], problem: dict, said: str):
    change(Clip, world["clip"], **problem)
    with db.session_scope() as s, pytest.raises(ClipForgeError, match=said):
        service.queue_posts(s, [ask(world)], now=NOW)
    with db.session_scope() as s:
        assert s.query(Post).count() == 0


def test_a_rendered_file_that_has_gone_missing_is_caught_before_queueing(world: dict[str, int]):
    with db.session_scope() as s:
        storage.data_path(s.get(Clip, world["clip"]).file_path).unlink()
        with pytest.raises(ClipForgeError, match="not been rendered"):
            service.queue_posts(s, [ask(world)], now=NOW)


@pytest.mark.parametrize("rights", ["unknown", "denied"])
def test_a_clip_whose_right_to_post_is_not_recorded_is_not_queued(world: dict[str, int], rights: str):
    change(Video, world["video"], permission_status=rights)
    with db.session_scope() as s, pytest.raises(ClipForgeError, match="right"):
        service.queue_posts(s, [ask(world)], now=NOW)


def test_the_same_clip_is_not_posted_twice_to_one_account_unless_you_say_so(world: dict[str, int]):
    queue_one(world)
    with db.session_scope() as s, pytest.raises(ClipForgeError, match="already"):
        service.queue_posts(s, [ask(world)], now=NOW)
    with db.session_scope() as s:
        settings_store.set_many(s, {"publish.min_gap_hours": 0})   # the gap has a test of its own
        assert len(service.queue_posts(s, [ask(world)], post_again=True, now=NOW)) == 1
        assert len(service.queue_posts(s, [ask(world, "tiktok")], now=NOW)) == 1   # another account is fine


def test_no_account_gets_more_than_the_daily_limit(world: dict[str, int]):
    with db.session_scope() as s:
        settings_store.set_many(s, {"publish.daily_limit": 2, "publish.min_gap_hours": 0})
        service.queue_posts(s, [ask(world), ask(world)], post_again=True, now=NOW)
        with pytest.raises(ClipForgeError, match="2 posts in 24 hours"):
            service.queue_posts(s, [ask(world)], post_again=True, now=NOW)
        later = ask(world, scheduled_at=NOW + timedelta(days=2))
        assert len(service.queue_posts(s, [later], post_again=True, now=NOW)) == 1   # two days later is fine


@pytest.mark.parametrize("overrides, said", [
    ({"title": ""}, "title"),
    ({"title": "x" * 101}, "100"),
    ({"privacy": "friends"}, "public, unlisted or private"),
    ({"scheduled_at": NOW - timedelta(hours=1)}, "future"),
])
def test_youtube_posts_are_checked_before_queueing(world: dict[str, int], overrides: dict, said: str):
    with db.session_scope() as s, pytest.raises(ClipForgeError, match=said):
        service.queue_posts(s, [ask(world, **overrides)], now=NOW)


def test_a_disconnected_or_expired_account_is_not_used(world: dict[str, int]):
    change(Account, world["youtube"], status="needs_reconnect")
    with db.session_scope() as s, pytest.raises(ClipForgeError, match="Reconnect"):
        service.queue_posts(s, [ask(world)], now=NOW)


def test_scheduled_youtube_posts_upload_now_and_others_wait_for_their_time(world: dict[str, int]):
    when = NOW + timedelta(days=1)
    with db.session_scope() as s:
        youtube, tiktok = service.queue_posts(s, [ask(world, scheduled_at=when), ask(world, "tiktok", scheduled_at=when)],
                                              now=NOW)
        assert youtube.next_run_at == NOW and youtube.scheduled_at == when   # YouTube publishes it at that time
        assert tiktok.next_run_at == when


def test_only_posts_whose_time_has_come_are_started(world: dict[str, int]):
    soon = queue_one(world)
    later = queue_one(world, "tiktok", scheduled_at=NOW + timedelta(hours=3))
    with db.session_scope() as s:
        assert service.enqueue_due(s, now=NOW) == 1
        jobs = s.query(Job).filter(Job.type == "publish").all()
        assert [j.payload["post_id"] for j in jobs] == [soon.id]
        assert service.enqueue_due(s, now=NOW) == 1       # asking again does not queue it twice
        assert s.query(Job).filter(Job.type == "publish").count() == 1
        assert service.enqueue_due(s, now=NOW + timedelta(hours=4)) == 2
        assert {j.payload["post_id"] for j in s.query(Job).filter(Job.type == "publish")} == {soon.id, later.id}


# ----------------------------------------------------------------------------------------------- the job
def test_a_youtube_post_is_uploaded_confirmed_and_logged(world: dict[str, int], platforms: FakePlatforms):  # noqa: F811
    post = queue_one(world)

    job = run_publish(post.id)

    assert job.status == "succeeded", job.error
    done = post_of(post.id)
    assert (done.status, done.url, done.external_id) == ("published", "https://youtube.com/shorts/vids1", "vids1")
    assert done.published_at is not None and done.attempts == 1 and done.next_run_at is None
    assert any(line["actor"] == "forge" and "Posted “The microwave badge number”" in line["text"]
               for line in job.result["log"])
    with db.session_scope() as s:
        assert s.query(Notification).filter(Notification.title.like("%YouTube%")).count() == 1
    assert platforms.youtube_sessions["s1"]["body"]["snippet"]["title"] == "The microwave badge number"


def test_an_expiring_sign_in_is_renewed_before_posting_and_kept(world: dict[str, int], platforms: FakePlatforms):  # noqa: F811
    with db.session_scope() as s:
        store.save_tokens(s, world["tiktok"], Tokens("t-access-1", "t-refresh-1", time.time() + 10,
                                                     time.time() + 86400))
    post = queue_one(world, "tiktok")

    job = run_publish(post.id)

    assert job.status == "succeeded", job.error
    with db.session_scope() as s:
        kept = store.load_tokens(s, world["tiktok"])
    assert (kept.access_token, kept.refresh_token) == ("t-access-2", "t-refresh-2")
    assert post_of(post.id).status == "in_drafts"


def test_a_sign_in_the_platform_no_longer_accepts_asks_you_to_reconnect(world: dict[str, int],
                                                                        platforms: FakePlatforms):  # noqa: F811
    with db.session_scope() as s:
        store.save_tokens(s, world["youtube"], Tokens("revoked", "g-refresh-1", time.time() + 3000))
    post = queue_one(world)

    job = run_publish(post.id)

    assert job.status == "failed"
    assert post_of(post.id).status == "failed" and "sign-in" in post_of(post.id).error
    with db.session_scope() as s:
        assert s.get(Account, world["youtube"]).status == "needs_reconnect"


def test_a_passing_problem_is_tried_again_later_then_given_up(world: dict[str, int], platforms: FakePlatforms):  # noqa: F811
    platforms.youtube_put_status = 503
    post = queue_one(world)

    run_publish(post.id)
    again = post_of(post.id)
    assert again.status == "scheduled" and again.attempts == 1 and "again" in again.detail
    assert again.next_run_at is not None and again.next_run_at > datetime.now(timezone.utc)

    for _ in range(service.MAX_ATTEMPTS - 1):
        run_publish(post.id)
    final = post_of(post.id)
    assert final.status == "failed" and final.attempts == service.MAX_ATTEMPTS
    assert len(platforms.calls("POST", "/upload/youtube")) == 1   # every attempt resumed the same upload


def test_a_tiktok_draft_is_checked_later_and_confirmed_once_you_post_it(world: dict[str, int],
                                                                       platforms: FakePlatforms):  # noqa: F811
    post = queue_one(world, "tiktok")
    run_publish(post.id)
    waiting = post_of(post.id)
    assert waiting.status == "in_drafts" and waiting.next_run_at is not None and "TikTok" in waiting.detail

    platforms.tiktok_statuses = ["PUBLISH_COMPLETE"]
    run_publish(post.id)

    done = post_of(post.id)
    assert done.status == "published" and done.published_at is not None and done.next_run_at is None
    assert len(platforms.calls("POST", "/v2/post/publish/inbox/video/init/")) == 1   # not uploaded again


def test_an_instagram_reel_still_processing_is_finished_by_a_later_check(world: dict[str, int],
                                                                        platforms: FakePlatforms):  # noqa: F811
    platforms.ig_statuses = ["IN_PROGRESS"]
    post = queue_one(world, "instagram")
    run_publish(post.id)
    assert post_of(post.id).status == "processing"

    platforms.ig_statuses = ["FINISHED"]
    run_publish(post.id)

    done = post_of(post.id)
    assert done.status == "published" and done.url.startswith("https://www.instagram.com/reel/")
    assert platforms.ig_published == ["c1"]


def test_a_right_to_post_withdrawn_after_queueing_stops_the_post(world: dict[str, int], platforms: FakePlatforms):  # noqa: F811
    post = queue_one(world)
    change(Video, world["video"], permission_status="denied")

    job = run_publish(post.id)

    assert job.status == "failed" and post_of(post.id).status == "failed"
    assert platforms.calls("POST", "/upload/youtube") == []


def test_a_deleted_clip_is_not_posted(world: dict[str, int], platforms: FakePlatforms):  # noqa: F811
    post = queue_one(world)
    with db.session_scope() as s:
        s.delete(s.get(Clip, world["clip"]))

    run_publish(post.id)

    assert post_of(post.id).status == "failed" and "no longer exists" in post_of(post.id).error
    assert platforms.calls("POST", "/upload/youtube") == []


# ------------------------------------------------------------------------------------- cancel, retry, remove
def test_a_scheduled_post_can_be_cancelled_and_a_failed_one_retried(world: dict[str, int]):
    post = queue_one(world, "tiktok", scheduled_at=NOW + timedelta(days=1))
    with db.session_scope() as s:
        assert service.cancel_post(s, post.id).status == "cancelled"
        with pytest.raises(ClipForgeError):
            service.cancel_post(s, post.id)
        s.get(Post, post.id).status = "failed"
        s.commit()
        retried = service.retry_post(s, post.id, now=NOW)
        assert retried.status == "scheduled" and retried.next_run_at == NOW and retried.error == ""


def test_a_post_on_its_way_cannot_be_removed_from_the_list(world: dict[str, int]):
    post = queue_one(world)
    change(Post, post.id, status="publishing")
    with db.session_scope() as s, pytest.raises(ClipForgeError, match="uploading"):
        service.delete_post(s, post.id)
    change(Post, post.id, status="failed")
    with db.session_scope() as s:
        service.delete_post(s, post.id)
        assert s.get(Post, post.id) is None


# ------------------------------------------------------------------------- failures never repeat by themselves
def test_a_post_whose_app_was_removed_fails_once_instead_of_being_retried_for_ever(world: dict[str, int]):
    post = queue_one(world)
    with db.session_scope() as s:
        store.remove_app(s, "youtube")

    job = run_publish(post.id)

    assert job.status == "failed"
    failed = post_of(post.id)
    assert failed.status == "failed" and "app" in failed.error and failed.next_run_at is None
    with db.session_scope() as s:
        assert service.enqueue_due(s) == 0


def test_an_unexpected_error_while_checking_stops_the_checks(world: dict[str, int], platforms: FakePlatforms,  # noqa: F811
                                                            monkeypatch):  # noqa: ANN001
    from app.publishing.tiktok import TikTok

    post = queue_one(world, "tiktok")
    run_publish(post.id)
    change(Post, post.id, next_run_at=datetime.now(timezone.utc) - timedelta(minutes=1))

    def broken(self, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003, ANN202
        raise KeyError("status")

    monkeypatch.setattr(TikTok, "check", broken)

    job = run_publish(post.id)

    assert job.status == "failed"
    stopped = post_of(post.id)
    assert stopped.status == "failed" and "unexpected" in stopped.error and stopped.next_run_at is None
    with db.session_scope() as s:
        assert service.enqueue_due(s) == 0


def test_posts_on_one_account_are_kept_hours_apart_so_the_platform_can_judge_each(world: dict[str, int]):
    # The default gap is 3 hours; a platform needs that long to see how the first post does before the next.
    with db.session_scope() as s:
        service.queue_posts(s, [ask(world)], now=NOW)
        with pytest.raises(ClipForgeError, match="3 hours"):
            service.queue_posts(s, [ask(world, scheduled_at=NOW + timedelta(hours=1))], post_again=True, now=NOW)
        with pytest.raises(ClipForgeError, match="3 hours"):   # two in one request, too close to each other
            service.queue_posts(s, [ask(world, scheduled_at=NOW + timedelta(hours=5)),
                                    ask(world, scheduled_at=NOW + timedelta(hours=6))], post_again=True, now=NOW)
        assert len(service.queue_posts(s, [ask(world, scheduled_at=NOW + timedelta(hours=3, minutes=1))],
                                       post_again=True, now=NOW)) == 1
        assert len(service.queue_posts(s, [ask(world, "tiktok", scheduled_at=NOW + timedelta(hours=1))],
                                       now=NOW)) == 1   # another account is not affected


def test_the_gap_between_posts_can_be_changed_or_switched_off(world: dict[str, int]):
    with db.session_scope() as s:
        settings_store.set_many(s, {"publish.min_gap_hours": 0})
        service.queue_posts(s, [ask(world), ask(world, scheduled_at=NOW + timedelta(minutes=10))], post_again=True,
                            now=NOW)
        settings_store.set_many(s, {"publish.min_gap_hours": 12})
        with pytest.raises(ClipForgeError, match="12 hours"):
            service.queue_posts(s, [ask(world, scheduled_at=NOW + timedelta(hours=8))], post_again=True, now=NOW)
