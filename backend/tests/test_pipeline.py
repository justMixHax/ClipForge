"""The analysis and render jobs end to end, on a real (tiny) video, with a scripted stand-in for the
speech model so the tests run in seconds."""
from __future__ import annotations

from pathlib import Path

import pytest

from app import db
from app.errors import DependencyMissing
from app.jobs import pipeline, queue
from app.models import Clip, Job, Notification, TranscriptSegment, Video
from app.services import ffmpeg, storage
from tests.conftest import make_test_video
from tests.helpers import ScriptedModel
from tests.test_scoring import FILLER, STORY

pytestmark = pytest.mark.ffmpeg
SCRIPT = FILLER + STORY                      # the story starts around 13.8 s and ends around 36 s
OPTIONS = {"count": 2, "length": "10-20", "min_len": 8, "max_len": 30, "min_score": 0, "auto_render": False}


@pytest.fixture()
def model(monkeypatch) -> ScriptedModel:  # noqa: ANN001
    scripted = ScriptedModel(SCRIPT)
    monkeypatch.setattr(pipeline, "load_transcriber", lambda session: (scripted, "scripted|test", "cpu"))
    return scripted


@pytest.fixture()
def video(data_dir: Path, tmp_path: Path) -> Video:
    source = make_test_video(tmp_path / "stream.mp4", seconds=40, size="640x360")
    info = ffmpeg.probe(source)
    with db.session_scope() as s:
        row = Video(title="Test stream", original_filename="stream.mp4", storage_key=storage.new_key(),
                    storage_mode="linked", file_path=str(source), size_bytes=info.size_bytes, duration=info.duration,
                    width=info.width, height=info.height, fps=info.fps, video_codec=info.video_codec,
                    audio_codec=info.audio_codec, has_audio=info.has_audio, creator_name="Test Streamer",
                    permission_status="owned")
        s.add(row)
        s.flush()
        s.expunge(row)
        return row


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


def analyze(video: Video, **overrides) -> Job:
    with db.session_scope() as s:
        pipeline.enqueue_analysis(s, s.get(Video, video.id), {**OPTIONS, **overrides})
    return run_next()


def clips_of(video: Video) -> list[Clip]:
    with db.session_scope() as s:
        rows = s.query(Clip).filter(Clip.video_id == video.id).order_by(Clip.rank).all()
        for row in rows:
            s.expunge(row)
        return rows


def test_analysis_stores_the_transcript_and_ranked_explained_suggestions(video: Video, model: ScriptedModel):
    job = analyze(video)
    clips = clips_of(video)

    assert job.status == "succeeded", job.error
    assert job.progress == 1.0 and all(stage["status"] in ("done", "skipped") for stage in job.stages)
    assert job.result["clips"] == len(clips) >= 1
    top = clips[0]
    assert 12.0 <= top.start <= 16.0 and top.end >= 30.0                 # the story, not the filler
    assert top.score > 60 and len(top.subscores) == 12 and top.explanation and top.evidence
    assert top.title and top.hook_text and top.hashtags and top.transcript_text
    assert (top.review_status, top.render_status, top.origin) == ("pending", "none", "ai")
    assert [c.rank for c in clips] == list(range(1, len(clips) + 1))
    with db.session_scope() as s:
        stored = s.get(Video, video.id)
        assert stored.status == "analyzed" and stored.language == "en"
        assert s.query(TranscriptSegment).filter_by(video_id=video.id).count() >= 8
        assert s.query(Notification).filter_by(kind="clips_ready").count() == 1


def test_auto_render_queues_one_render_per_selected_clip(video: Video, model: ScriptedModel):
    analyze(video, auto_render=True, count=2)

    clips = clips_of(video)
    with db.session_scope() as s:
        renders = s.query(Job).filter_by(type="render", status="queued").all()
        assert len(renders) == len(clips) >= 1
        assert {j.clip_id for j in renders} == {c.id for c in clips}
    assert all(c.render_status == "queued" for c in clips)


def test_rendering_produces_the_video_cover_caption_files_and_quality_report(video: Video, model: ScriptedModel):
    analyze(video, auto_render=True, count=1)
    job = run_next()
    clip = clips_of(video)[0]

    assert job.status == "succeeded", job.error
    assert clip.render_status == "ready" and clip.rendered_at is not None
    folder = storage.data_path(clip.file_path).parent
    info = ffmpeg.probe(storage.data_path(clip.file_path))
    assert (info.width, info.height) == (1080, 1920) == (clip.out_width, clip.out_height)
    assert info.duration == pytest.approx(clip.end - clip.start, abs=0.3)
    assert storage.data_path(clip.thumb_path).stat().st_size > 1000
    assert "-->" in (folder / "captions.srt").read_text(encoding="utf-8")
    assert (folder / "captions.vtt").read_text(encoding="utf-8").startswith("WEBVTT")
    kit = (folder / "post.txt").read_text(encoding="utf-8")
    assert clip.title in kit and "Test Streamer" in kit and "#" in kit
    assert clip.qc and all(c["status"] != "fail" for c in clip.qc), clip.qc
    assert clip.render_info["layout"] in ("fit_blur", "fill", "facecam_top", "split")
    assert clip.file_size == storage.data_path(clip.file_path).stat().st_size


def test_a_second_analysis_reuses_the_transcript_instead_of_transcribing_again(video: Video, model: ScriptedModel):
    analyze(video)
    calls_after_first = model.calls
    job = analyze(video)

    assert job.status == "succeeded"
    assert model.calls == calls_after_first
    assert next(s for s in job.stages if s["key"] == "transcribe")["status"] == "skipped"


def test_reanalysis_replaces_untouched_suggestions_but_keeps_what_the_user_approved(video: Video, model: ScriptedModel):
    analyze(video, count=3)
    first = clips_of(video)
    kept = first[0]
    with db.session_scope() as s:
        s.get(Clip, kept.id).review_status = "approved"

    analyze(video, count=3)
    second = clips_of(video)

    assert kept.id in {c.id for c in second}
    assert not ({c.id for c in first} - {kept.id}) & {c.id for c in second}       # the rest were replaced
    for clip in second:
        if clip.id != kept.id:
            overlap = min(clip.end, kept.end) - max(clip.start, kept.start)
            assert overlap <= 1.0, "a new suggestion duplicates the approved clip"


def test_cancelling_during_analysis_stops_the_job_and_leaves_the_video_usable(video: Video, monkeypatch):  # noqa: ANN001
    def cancel() -> None:
        with db.session_scope() as s:
            running = s.query(Job).filter_by(status="running").one()
            queue.request_cancel(s, running.id)

    scripted = ScriptedModel(SCRIPT, on_call=cancel)
    monkeypatch.setattr(pipeline, "load_transcriber", lambda session: (scripted, "scripted|test", "cpu"))
    job = analyze(video)

    assert job.status == "cancelled"
    assert clips_of(video) == []
    with db.session_scope() as s:
        assert s.get(Video, video.id).status == "ready"


def test_a_missing_source_file_fails_with_a_message_that_says_so(video: Video, model: ScriptedModel):
    Path(video.file_path).unlink()
    job = analyze(video)

    assert job.status == "failed"
    assert "not found" in job.error
    with db.session_scope() as s:
        stored = s.get(Video, video.id)
        assert stored.status == "failed" and "not found" in stored.last_error


def test_a_missing_speech_model_fails_with_instructions(video: Video, monkeypatch):  # noqa: ANN001
    def no_model(session):  # noqa: ANN001
        raise DependencyMissing("No speech model is installed", hint="Install one in Settings.")

    monkeypatch.setattr(pipeline, "load_transcriber", no_model)
    job = analyze(video)

    assert job.status == "failed"
    assert "No speech model is installed" in job.error and "Settings" in job.error


def test_a_render_failure_is_recorded_on_both_the_clip_and_the_job(video: Video, model: ScriptedModel, monkeypatch):  # noqa: ANN001
    analyze(video, auto_render=True, count=1)

    def explode(*args, **kwargs):  # noqa: ANN002, ANN003
        raise ffmpeg.FFmpegError("FFmpeg failed", 1, "Invalid data found when processing input")

    monkeypatch.setattr(pipeline, "render_clip", explode)
    job = run_next()
    clip = clips_of(video)[0]

    assert job.status == "failed" and "Invalid data" in job.error
    assert clip.render_status == "failed" and "Invalid data" in clip.render_error


def test_a_failed_graphics_card_encode_is_retried_on_the_processor(video: Video, model: ScriptedModel, monkeypatch):  # noqa: ANN001
    analyze(video, auto_render=True, count=1)
    monkeypatch.setattr(pipeline, "resolve_encoder", lambda session: "h264_nvenc")   # as if the GPU test had passed
    real_render, attempts = pipeline.render_clip, []

    def gpu_is_broken(request, **kwargs):  # noqa: ANN001, ANN003
        attempts.append(request.encoder)
        if request.encoder == "h264_nvenc":
            raise ffmpeg.FFmpegError("FFmpeg failed", 1, "Cannot load nvcuda.dll")
        return real_render(request, **kwargs)

    monkeypatch.setattr(pipeline, "render_clip", gpu_is_broken)
    job = run_next()
    clip = clips_of(video)[0]

    assert attempts == ["h264_nvenc", "libx264"]
    assert job.status == "succeeded", job.error
    assert clip.render_status == "ready" and clip.render_info["encoder"] == "libx264"


def test_a_video_without_sound_is_analysed_from_the_picture_alone(data_dir: Path, tmp_path: Path, model: ScriptedModel):
    source = make_test_video(tmp_path / "silent.mp4", seconds=20, with_audio=False)
    info = ffmpeg.probe(source)
    with db.session_scope() as s:
        row = Video(title="Silent", storage_key=storage.new_key(), storage_mode="linked", file_path=str(source),
                    duration=info.duration, width=info.width, height=info.height, fps=info.fps, has_audio=False)
        s.add(row)
        s.flush()
        s.expunge(row)
    job = analyze(row)

    assert job.status == "succeeded", job.error
    assert model.calls == 0
    assert "no audio" in job.message.lower() or "no audio" in str(job.result).lower()


def test_edited_captions_are_what_gets_rendered(video: Video, model: ScriptedModel):
    analyze(video, count=1)
    clip = clips_of(video)[0]
    with db.session_scope() as s:
        row = s.get(Clip, clip.id)
        row.captions = [{"text": "Edited", "start": 0.2, "end": 0.8}, {"text": "caption", "start": 0.8, "end": 1.5}]
        pipeline.enqueue_render(s, row)
    job = run_next()

    assert job.status == "succeeded", job.error
    clip = clips_of(video)[0]
    srt = (storage.data_path(clip.file_path).parent / "captions.srt").read_text(encoding="utf-8")
    assert "Edited caption" in srt


def test_silence_removal_shortens_the_clip_by_cutting_long_pauses(video: Video, model: ScriptedModel):
    analyze(video, count=1)
    clip = clips_of(video)[0]
    with db.session_scope() as s:
        row = s.get(Clip, clip.id)
        row.settings = {**row.settings, "remove_silence": True, "silence_threshold": 0.3}
        pipeline.enqueue_render(s, row)
    job = run_next()
    clip = clips_of(video)[0]

    assert job.status == "succeeded", job.error
    assert clip.out_duration < (clip.end - clip.start) - 0.4
    assert len(clip.render_info["segments"]) >= 2


def test_closing_the_app_pauses_the_running_job_so_it_continues_next_time(video: Video, monkeypatch):  # noqa: ANN001
    """Shutdown is not cancellation: the job goes back to the queue and resumes from its saved work."""
    scripted = ScriptedModel(SCRIPT, on_call=pipeline.SHUTDOWN.set)
    monkeypatch.setattr(pipeline, "load_transcriber", lambda session: (scripted, "scripted|test", "cpu"))
    try:
        job = analyze(video)
    finally:
        pipeline.SHUTDOWN.clear()

    assert job.status == "queued" and "continue" in job.message
    # the app starts again: same job, and this time nothing interrupts it
    monkeypatch.setattr(pipeline, "load_transcriber", lambda session: (ScriptedModel(SCRIPT), "scripted|test", "cpu"))
    resumed = run_next()
    assert resumed.id == job.id and resumed.status == "succeeded", resumed.error
    assert next(s for s in resumed.stages if s["key"] == "audio")["status"] == "skipped"   # audio was kept
    assert clips_of(video)


# ------------------------------------------------------------------------------------ Claude picks the clips
def claude_on(session_value: str = "claude") -> None:
    from app.services import settings_store
    with db.session_scope() as s:
        settings_store.set_many(s, {"ai.engine": session_value})


def test_with_claude_as_the_engine_its_picks_become_the_clips(video: Video, model: ScriptedModel, monkeypatch):  # noqa: ANN001
    from app.analysis import claude_review
    from app.analysis.claude_review import Pick

    asked: list[str] = []

    def fake_claude(prompt, *, model="sonnet", cli_path="", should_cancel=None, timeout=0):  # noqa: ANN001, ANN202
        asked.append(prompt)
        return [Pick(start=14.0, end=35.0, title="He lost his phone in his own hand", hook="45 minutes for a phone",
                     category="funny", score=91, reason="A full story with a punchline.")]

    monkeypatch.setattr(claude_review, "ask_claude", fake_claude)
    claude_on()

    job = analyze(video)

    assert job.status == "succeeded", job.error
    assert len(asked) == 1 and "Can I tell you the dumbest thing" in asked[0]
    review = next(st for st in job.stages if st["key"] == "review")
    assert review["status"] == "done" and "Claude" in review["note"]
    (clip,) = clips_of(video)
    assert (clip.analysis_source, clip.title, clip.score, clip.category) == (
        "claude", "He lost his phone in his own hand", 91, "funny")
    assert "Claude" in clip.explanation


def test_when_claude_cannot_be_reached_the_local_rules_pick_and_the_job_says_why(video: Video, model: ScriptedModel,
                                                                                 monkeypatch):  # noqa: ANN001
    from app.analysis import claude_review

    def signed_out(prompt, **kw):  # noqa: ANN001, ANN003, ANN202
        raise claude_review.ClaudeUnavailable("Claude is signed out on this PC, so the local rules picked the clips "
                                              "this time.", hint=claude_review.SIGN_IN_HINT)

    monkeypatch.setattr(claude_review, "ask_claude", signed_out)
    claude_on()

    job = analyze(video)

    assert job.status == "succeeded"
    assert "signed out" in job.result["note"] and "claude auth login" in job.result["note"]
    assert clips_of(video) and all(c.analysis_source == "heuristic" for c in clips_of(video))


def test_with_the_local_engine_claude_is_never_asked(video: Video, model: ScriptedModel, monkeypatch):  # noqa: ANN001
    from app.analysis import claude_review

    monkeypatch.setattr(claude_review, "ask_claude", lambda *a, **k: pytest.fail("Claude was called"))
    claude_on("local")

    job = analyze(video)

    assert next(st for st in job.stages if st["key"] == "review")["status"] == "skipped"
    assert clips_of(video)


# ---------------------------------------------------------------------- ChatGPT's second opinion, the work log
def second_opinion_on() -> None:
    from app.services import settings_store
    with db.session_scope() as s:
        settings_store.set_many(s, {"ai.engine": "claude", "ai.second_opinion": "chatgpt"})


def lines(job: Job) -> list[tuple[str, str]]:
    return [(entry["actor"], entry["text"]) for entry in (job.result or {}).get("log", [])]


def fake_claude_story(monkeypatch) -> None:  # noqa: ANN001
    from app.analysis import claude_review
    from app.analysis.claude_review import Pick

    monkeypatch.setattr(claude_review, "ask_claude", lambda prompt, **kw: [
        Pick(start=14.0, end=35.0, title="Phone in his hand", hook="45 minutes", category="funny", score=91,
             reason="A full story with a punchline.")])


def test_chatgpt_checks_claudes_picks_adds_a_missed_one_and_the_log_says_who_did_what(video: Video, model: ScriptedModel,
                                                                                    monkeypatch):  # noqa: ANN001
    from app.analysis import chatgpt_review
    from app.analysis.chatgpt_review import Review
    from app.analysis.claude_review import Pick

    fake_claude_story(monkeypatch)
    asked: list[str] = []

    def fake_chatgpt(prompt, **kw):  # noqa: ANN001, ANN003, ANN202
        asked.append(prompt)
        return Review(verdicts={1: ("agree", "Clear punchline.")},
                      additions=[Pick(start=1.0, end=11.5, title="Settings menu", hook="", category="highlight",
                                      score=52, reason="Calm intro.")])

    monkeypatch.setattr(chatgpt_review, "ask_chatgpt", fake_chatgpt)
    second_opinion_on()

    job = analyze(video)

    assert job.status == "succeeded", job.error
    assert len(asked) == 1 and "Phone in his hand" in asked[0]
    second = next(st for st in job.stages if st["key"] == "second")
    assert second["status"] == "done" and "added 1" in second["note"]
    by_source = {c.analysis_source: c for c in clips_of(video)}
    assert set(by_source) == {"claude", "chatgpt"}
    verdict = next(e for e in by_source["claude"].evidence if e["dimension"] == "chatgpt")
    assert verdict["verdict"] == "agree"
    assert by_source["chatgpt"].explanation.startswith("Added by ChatGPT")

    log = lines(job)
    assert [actor for actor, _ in log] == ["forge", "forge", "claude", "chatgpt", "forge"]
    assert "Transcribed" in log[0][1] and "words" in log[0][1]
    assert log[2][1] == "Picked 1 clip: “Phone in his hand”."
    assert log[3][1] == "Checked 1 pick: agreed with 1, added 1 (“Settings menu”)."
    assert log[4][1].startswith("Saved 2 clips")
    assert all(len(text) < 200 for _, text in log)                      # short lines, as asked


def test_when_chatgpt_cannot_be_reached_claudes_picks_stay_and_the_log_says_so(video: Video, model: ScriptedModel,
                                                                               monkeypatch):  # noqa: ANN001
    from app.analysis import chatgpt_review

    fake_claude_story(monkeypatch)

    def signed_out(prompt, **kw):  # noqa: ANN001, ANN003, ANN202
        raise chatgpt_review.ChatGPTUnavailable("ChatGPT is signed out on this PC, so there is no second opinion "
                                                "this time.", hint=chatgpt_review.SIGN_IN_HINT)

    monkeypatch.setattr(chatgpt_review, "ask_chatgpt", signed_out)
    second_opinion_on()

    job = analyze(video)

    assert job.status == "succeeded"
    assert [c.analysis_source for c in clips_of(video)] == ["claude"]
    assert ("chatgpt", "Could not check the picks: ChatGPT is signed out on this PC.") in lines(job)
    assert "signed out" in job.result["note"]


def test_a_render_logs_what_forge_did(video: Video, model: ScriptedModel):
    analyze(video, auto_render=True, count=1)

    render = run_next()

    (actor, text), = lines(render)
    assert actor == "forge" and text.startswith("Rendered “") and "checks passed" in text and "1080×1920" in text


def test_a_failed_job_logs_where_it_stopped(video: Video, model: ScriptedModel, monkeypatch):  # noqa: ANN001
    analyze(video, auto_render=True, count=1)

    def explode(*args, **kwargs):  # noqa: ANN002, ANN003
        raise ffmpeg.FFmpegError("FFmpeg failed", 1, "Invalid data found when processing input")

    monkeypatch.setattr(pipeline, "render_clip", explode)
    job = run_next()

    assert job.status == "failed"
    assert lines(job)[-1][0] == "forge" and lines(job)[-1][1].startswith("Stopped: FFmpeg failed")


def test_with_the_second_opinion_off_chatgpt_is_never_asked(video: Video, model: ScriptedModel, monkeypatch):  # noqa: ANN001
    from app.analysis import chatgpt_review

    fake_claude_story(monkeypatch)
    monkeypatch.setattr(chatgpt_review, "ask_chatgpt", lambda *a, **k: pytest.fail("ChatGPT was asked"))
    claude_on()

    job = analyze(video)

    assert next(st for st in job.stages if st["key"] == "second")["status"] == "skipped"
    assert "chatgpt" not in [actor for actor, _ in lines(job)]


def test_the_chatgpt_line_stays_short_even_when_it_disagrees_a_lot(video: Video, model: ScriptedModel, monkeypatch):  # noqa: ANN001
    from app.analysis import chatgpt_review
    from app.analysis.chatgpt_review import Review

    fake_claude_story(monkeypatch)
    long_reason = "The joke depends on a setup that happens well before the clip starts, so a viewer would be lost " * 3
    monkeypatch.setattr(chatgpt_review, "ask_chatgpt",
                        lambda prompt, **kw: Review(verdicts={1: ("disagree", long_reason)}))
    second_opinion_on()

    job = analyze(video)

    (text,) = [text for actor, text in lines(job) if actor == "chatgpt"]
    assert text == "Checked 1 pick: agreed with 0, disagreed with 1 (“Phone in his hand”)."
    clip = clips_of(video)[0]
    assert "setup that happens well before" in next(e["text"] for e in clip.evidence if e["dimension"] == "chatgpt")


def test_local_rules_only_means_nothing_goes_to_chatgpt_either(video: Video, model: ScriptedModel, monkeypatch):  # noqa: ANN001
    """Someone who chose "Local rules only" before the second opinion existed must not start sending transcripts."""
    from app.analysis import chatgpt_review
    from app.services import settings_store

    monkeypatch.setattr(chatgpt_review, "ask_chatgpt", lambda *a, **k: pytest.fail("ChatGPT was asked"))
    with db.session_scope() as s:
        settings_store.set_many(s, {"ai.engine": "local", "ai.second_opinion": "chatgpt"})

    job = analyze(video)

    second = next(st for st in job.stages if st["key"] == "second")
    assert second["status"] == "skipped" and "nothing leaves this PC" in second["note"]


def test_an_unexpected_error_in_the_second_opinion_keeps_the_picks(video: Video, model: ScriptedModel, monkeypatch):  # noqa: ANN001
    from app.analysis import chatgpt_review

    fake_claude_story(monkeypatch)

    def broken(prompt, **kw):  # noqa: ANN001, ANN003, ANN202
        raise TypeError("'int' object is not iterable")

    monkeypatch.setattr(chatgpt_review, "ask_chatgpt", broken)
    second_opinion_on()

    job = analyze(video)

    assert job.status == "succeeded", job.error
    assert [c.analysis_source for c in clips_of(video)] == ["claude"]
    assert any(actor == "chatgpt" and text.startswith("Could not check the picks") for actor, text in lines(job))

def test_a_cancelled_job_keeps_the_lines_of_what_was_already_done(video: Video, monkeypatch):  # noqa: ANN001
    from app.jobs.queue import request_cancel

    scripted = ScriptedModel(SCRIPT)
    monkeypatch.setattr(pipeline, "load_transcriber", lambda session: (scripted, "scripted|test", "cpu"))
    real_find = pipeline.find_highlights

    def cancel_after_transcribing(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        with db.session_scope() as s:
            job = s.query(Job).filter_by(type="analyze").one()
            request_cancel(s, job.id)
        return real_find(*args, **kwargs)

    monkeypatch.setattr(pipeline, "find_highlights", cancel_after_transcribing)

    job = analyze(video)

    assert job.status == "cancelled"
    log = lines(job)
    assert log[0][0] == "forge" and log[0][1].startswith("Transcribed")
    assert log[-1] == ("forge", "Cancelled.")
