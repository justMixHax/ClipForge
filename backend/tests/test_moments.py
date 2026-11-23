"""Clips from your own timestamps: you say where the moment starts, ClipForge does the rest (the same work as
for the moments it finds itself: transcript, end point, title, hook, score, second opinion, render)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import db
from app.analysis import chatgpt_review, claude_review
from app.analysis.claude_review import Pick
from app.jobs import pipeline
from app.models import Job, TranscriptSegment, Video
from tests.helpers import ScriptedModel
from tests.test_api import client, upload, uploaded  # noqa: F401  (fixtures)
from tests.test_pipeline import (OPTIONS, claude_on, clips_of, lines, model, run_next,  # noqa: F401  (fixtures)
                                 second_opinion_on, video)

pytestmark = pytest.mark.ffmpeg
# The story in the test video starts around 13.8 s and ends around 36 s. Clips are 8 to 30 s long.


def moment(video: Video, start: float, end: float | None = None, **overrides) -> Job:  # noqa: F811
    with db.session_scope() as s:
        pipeline.enqueue_moment(s, s.get(Video, video.id), start=start, end=end, options={**OPTIONS, **overrides})
        # renders queued by an earlier moment wait their turn here, so the next job run is this moment
        s.query(Job).filter(Job.type == "render", Job.status == "queued").update({"priority": -100})
    return run_next()


def sentence_ends(video: Video) -> list[float]:  # noqa: F811
    with db.session_scope() as s:
        return [row.end for row in s.query(TranscriptSegment).filter(TranscriptSegment.video_id == video.id)]


def render_jobs(clip_id: int) -> int:
    with db.session_scope() as s:
        return s.query(Job).filter(Job.type == "render", Job.clip_id == clip_id).count()


def claude_says(monkeypatch, end: float, asked: list[str] | None = None, start: float = 12.0) -> None:  # noqa: ANN001
    def fake(prompt, **kw):  # noqa: ANN001, ANN003, ANN202
        if asked is not None:
            asked.append(prompt)
        return [Pick(start=start, end=end, title="He lost his phone in his own hand", hook="45 minutes for a phone",
                     category="funny", score=88, reason="The payoff lands at the very end.")]

    monkeypatch.setattr(claude_review, "ask_claude", fake)


# ------------------------------------------------------------------------------------------------ the job
def test_a_moment_you_pick_becomes_a_clip_that_starts_exactly_where_you_said(video: Video, model: ScriptedModel):  # noqa: F811
    job = moment(video, 14.2)

    assert job.status == "succeeded", job.error
    (clip,) = clips_of(video)
    assert clip.origin == "manual" and clip.start == 14.2
    assert 8 <= clip.end - clip.start <= 30                               # inside your length range
    assert any(abs(clip.end - end) <= 0.5 for end in sentence_ends(video))  # and at the end of a sentence
    assert clip.title and clip.hook_text and clip.score > 0 and clip.subscores
    assert clip.explanation.startswith("You picked this moment")
    assert clip.render_status == "queued" and render_jobs(clip.id) == 1   # rendering is on its way
    assert model.calls >= 1 and sentence_ends(video)                       # the video had never been transcribed
    said = lines(job)
    assert ("forge", "Reused the transcript it had already made.") not in said
    assert any(actor == "forge" and text.startswith("Saved your clip from 0:00:14 to") for actor, text in said)


def test_a_second_moment_reuses_the_transcript(video: Video, model: ScriptedModel):  # noqa: F811
    moment(video, 14.2)
    calls = model.calls

    job = moment(video, 1.0)

    assert job.status == "succeeded", job.error
    assert model.calls == calls
    assert next(st for st in job.stages if st["key"] == "transcribe")["status"] == "skipped"
    assert len(clips_of(video)) == 2


def test_with_claude_on_it_ends_your_moment_after_the_payoff_and_names_it(video: Video, model: ScriptedModel,  # noqa: F811
                                                                         monkeypatch):  # noqa: ANN001
    asked: list[str] = []
    claude_says(monkeypatch, end=35.0, asked=asked, start=12.0)   # Claude would start earlier: it may not
    claude_on()

    job = moment(video, 14.2)

    assert job.status == "succeeded", job.error
    (clip,) = clips_of(video)
    assert clip.start == 14.2 and 35.0 <= clip.end <= 35.6      # Claude's end, kept clear of a word it would chop
    assert (clip.title, clip.hook_text, clip.score, clip.category) == (
        "He lost his phone in his own hand", "45 minutes for a phone", 88, "funny")
    assert "Claude" in clip.explanation and clip.explanation.startswith("You picked this moment")
    assert len(asked) == 1 and "14.2" in asked[0] and "Can I tell you the dumbest thing" in asked[0]
    assert any(actor == "claude" and "0:35" in text and "He lost his phone" in text for actor, text in lines(job))


def test_an_end_you_give_is_kept_even_when_claude_would_end_it_elsewhere(video: Video, model: ScriptedModel,  # noqa: F811
                                                                        monkeypatch):  # noqa: ANN001
    asked: list[str] = []
    claude_says(monkeypatch, end=35.0, asked=asked)
    claude_on()

    job = moment(video, 14.2, 30.0)

    (clip,) = clips_of(video)
    assert (clip.start, clip.end) == (14.2, 30.0)
    assert clip.title == "He lost his phone in his own hand"
    assert "30.0" in asked[0]
    assert job.status == "succeeded"


def test_an_end_from_claude_outside_your_length_is_not_used(video: Video, model: ScriptedModel, monkeypatch):  # noqa: ANN001, F811
    claude_says(monkeypatch, end=15.0)     # 0.8 s: far shorter than the 8 s minimum
    claude_on()

    job = moment(video, 14.2)

    (clip,) = clips_of(video)
    assert clip.start == 14.2 and 8 <= clip.end - clip.start <= 30
    assert clip.title == "He lost his phone in his own hand"
    assert any(actor == "forge" and "end" in text.lower() for actor, text in lines(job))


def test_when_claude_cannot_help_forge_ends_and_names_your_moment_and_says_why(video: Video, model: ScriptedModel,  # noqa: F811
                                                                              monkeypatch):  # noqa: ANN001
    def signed_out(prompt, **kw):  # noqa: ANN001, ANN003, ANN202
        raise claude_review.ClaudeUnavailable("Claude is signed out on this PC, so the local rules picked the clips "
                                              "this time.", hint=claude_review.SIGN_IN_HINT)

    monkeypatch.setattr(claude_review, "ask_claude", signed_out)
    claude_on()

    job = moment(video, 14.2)

    assert job.status == "succeeded", job.error
    (clip,) = clips_of(video)
    assert clip.start == 14.2 and clip.title
    assert any(actor == "claude" and text.startswith("Could not help: Claude is signed out") for actor, text in lines(job))
    assert "claude auth login" in (job.result or {}).get("note", "")


def test_chatgpt_gives_its_verdict_on_your_moment_but_adds_no_others(video: Video, model: ScriptedModel,  # noqa: F811
                                                                    monkeypatch):  # noqa: ANN001
    claude_says(monkeypatch, end=35.0)
    asked: list[str] = []

    def fake_chatgpt(prompt, **kw):  # noqa: ANN001, ANN003, ANN202
        asked.append(prompt)
        return chatgpt_review.Review(
            verdicts={1: ("agree", "Clear setup and a payoff.")},
            additions=[Pick(start=1.0, end=12.0, title="Settings talk", hook="Settings", category="story", score=60,
                            reason="Missed.")])

    monkeypatch.setattr(chatgpt_review, "ask_chatgpt", fake_chatgpt)
    second_opinion_on()

    job = moment(video, 14.2)

    assert job.status == "succeeded", job.error
    (clip,) = clips_of(video)                                # the addition was not taken
    assert any(e.get("dimension") == "chatgpt" and e.get("verdict") == "agree" for e in clip.evidence)
    assert len(asked) == 1 and "chosen by the user" in asked[0]
    assert ("chatgpt", "Checked your moment and agreed: Clear setup and a payoff.") in lines(job)


def test_with_the_local_engine_neither_ai_is_asked(video: Video, model: ScriptedModel, monkeypatch):  # noqa: ANN001, F811
    monkeypatch.setattr(claude_review, "ask_claude", lambda *a, **k: pytest.fail("Claude was asked"))
    monkeypatch.setattr(chatgpt_review, "ask_chatgpt", lambda *a, **k: pytest.fail("ChatGPT was asked"))
    claude_on("local")

    job = moment(video, 14.2)

    assert job.status == "succeeded", job.error
    assert {st["key"]: st["status"] for st in job.stages}["review"] == "skipped"


def test_a_moment_near_the_end_of_the_video_stops_at_the_end(video: Video, model: ScriptedModel):  # noqa: F811
    job = moment(video, 37.0)

    assert job.status == "succeeded", job.error
    (clip,) = clips_of(video)
    assert clip.start == 37.0 and clip.end <= video.duration + 0.01 and clip.end > clip.start


# -------------------------------------------------------------------------------------------------- the API
def test_you_can_ask_for_a_clip_from_a_time_you_type(client: TestClient, uploaded: dict):  # noqa: F811
    response = client.post(f"/api/videos/{uploaded['id']}/moments", json={"start": "0:01", "options": {"length": "20-40"}})

    assert response.status_code == 200, response.text
    job = response.json()
    assert job["type"] == "moment" and job["status"] == "queued" and job["video_id"] == uploaded["id"]
    with db.session_scope() as s:
        payload = s.get(Job, job["id"]).payload
    assert payload["start"] == 1.0 and payload["end"] is None and payload["options"]["length"] == "20-40"


def test_asking_twice_for_the_same_moment_queues_it_once(client: TestClient, uploaded: dict):  # noqa: F811
    first = client.post(f"/api/videos/{uploaded['id']}/moments", json={"start": "1"}).json()
    second = client.post(f"/api/videos/{uploaded['id']}/moments", json={"start": "0:01"}).json()

    assert first["id"] == second["id"]


def test_a_link_shared_at_a_moment_can_be_pasted_as_the_start(client: TestClient, uploaded: dict):  # noqa: F811
    with db.session_scope() as s:
        s.get(Video, uploaded["id"]).source_url = "https://www.youtube.com/watch?v=aBrr76ysyUo"

    ok = client.post(f"/api/videos/{uploaded['id']}/moments", json={"start": "https://youtu.be/aBrr76ysyUo?t=2"})
    other = client.post(f"/api/videos/{uploaded['id']}/moments", json={"start": "https://youtu.be/zzzzzzzzzzz?t=2"})

    assert ok.status_code == 200, ok.text
    assert other.status_code == 400 and "different video" in other.json()["error"]


@pytest.mark.parametrize("body, said", [
    ({"start": "soon"}, "1:02:15"),
    ({"start": "0:09"}, "only"),                   # the test video is 3 seconds long
    ({"start": "0:02", "end": "0:01"}, "after"),
    ({"start": "0:01", "end": "0:01.5"}, "at least"),
])
def test_times_that_do_not_make_sense_are_refused_with_a_reason(client: TestClient, uploaded: dict, body: dict,  # noqa: F811
                                                                said: str):
    response = client.post(f"/api/videos/{uploaded['id']}/moments", json=body)

    assert response.status_code == 400, response.text
    assert said in response.json()["error"]


def test_a_source_you_may_not_clip_cannot_be_clipped_by_hand_either(client: TestClient, uploaded: dict):  # noqa: F811
    client.patch(f"/api/videos/{uploaded['id']}", json={"permission_status": "denied"})

    response = client.post(f"/api/videos/{uploaded['id']}/moments", json={"start": "0:01"})

    assert response.status_code == 400 and "not permitted" in response.json()["error"]


# ------------------------------------------------------------------------------------- never cut into a word
def stored_words(video: Video):  # noqa: ANN201, F811
    with db.session_scope() as s:
        return pipeline.load_words(s, video.id)


def test_a_start_typed_in_the_middle_of_a_word_moves_to_the_end_of_that_word(video: Video, model: ScriptedModel):  # noqa: F811
    moment(video, 14.2)                                        # transcribes the video first
    word = next(w for w in stored_words(video) if w.text == "tell")
    typed = round((word.start + word.end) / 2, 2)

    job = moment(video, typed)

    clip = next(c for c in clips_of(video) if abs(c.start - typed) < 1)
    assert typed < clip.start <= word.end + 0.05                  # a fraction of a second later, not a new place
    assert pipeline.cut_into_word(clip.start, clip.end, stored_words(video)) is None
    assert any(actor == "forge" and "does not cut into “tell”" in text for actor, text in lines(job))


def test_an_end_in_the_middle_of_a_word_waits_for_the_word_to_finish(video: Video, model: ScriptedModel):  # noqa: F811
    moment(video, 14.2)
    word = next(w for w in stored_words(video) if w.text == "entire")
    typed_end = round((word.start + word.end) / 2, 2)

    moment(video, 20.0, typed_end)

    clip = max(clips_of(video), key=lambda c: c.id)
    assert typed_end < clip.end <= word.end + 0.1
    assert pipeline.cut_into_word(clip.start, clip.end, stored_words(video)) is None


def test_a_short_selection_is_never_squeezed_below_a_second_by_the_word_rule():
    from app.analysis.engine import HighlightOptions, moment_highlight
    from app.transcription.sentences import Word, build_sentences

    words = [Word("hello", 5.0, 5.5, 1.0), Word("world.", 7.0, 7.4, 1.0)]
    options = HighlightOptions(count=1, min_len=8.0, max_len=30.0)

    # the start falls inside "hello"; the end is in the pause: the shortest selection the app accepts
    highlight = moment_highlight(words, build_sentences(words), None, None, options, start=5.4, end=6.4,
                                 duration=10.0)

    assert highlight.end - highlight.start >= 1.0 - 1e-6


def test_the_word_rule_never_puts_the_start_past_the_end():
    from app.analysis.engine import HighlightOptions, moment_highlight
    from app.transcription.sentences import Word, build_sentences

    words = [Word("one", 0.0, 0.4, 1.0), Word("two.", 1.0, 1.4, 1.0), Word("loooong", 9.0, 9.9, 1.0)]
    options = HighlightOptions(count=1, min_len=8.0, max_len=30.0)

    highlight = moment_highlight(words, build_sentences(words), None, None, options, start=9.7, end=None,
                                 duration=10.0)

    assert highlight.end > highlight.start and highlight.start <= 9.7
