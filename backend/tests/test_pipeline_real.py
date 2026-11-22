"""The acceptance test: the real speech model, the real engine and real renders on the sample video.

Nothing is faked here. The sample video is synthetic (see scripts/sample/make_sample.py) and comes with
the script it was built from, so the test can check that what the pipeline found is what was planted.
Slow: about a minute. Skipped when the sample or the speech model is missing.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from app import db
from app.jobs import pipeline, queue
from app.models import Clip, Job, TranscriptSegment, Video
from app.services import ffmpeg, storage
from tests.conftest import ROOT, SAMPLE_TRUTH

pytestmark = [pytest.mark.slow, pytest.mark.ffmpeg]
MODELS = ROOT / "data" / "models"


@pytest.fixture()
def real_models(monkeypatch):  # noqa: ANN001
    """Use the speech model and face detector installed in the project, not the empty test data folder."""
    from app.editing import reframe
    from app.transcription import models

    if not (MODELS / "whisper" / "small.en" / "model.bin").exists():
        pytest.skip("speech model small.en is not installed in data/models/whisper")
    monkeypatch.setattr(models, "models_root", lambda: MODELS / "whisper")
    monkeypatch.setattr(reframe, "yunet_model_path", lambda: MODELS / "yunet" / reframe.YUNET_FILE)


def run_next() -> Job:
    with db.session_scope() as s:
        job = queue.claim_next(s, "acceptance")
        assert job is not None
        job_id = job.id
    pipeline.run_job(job_id)
    with db.session_scope() as s:
        job = s.get(Job, job_id)
        s.expunge(job)
        return job


def test_upload_to_finished_clips_on_the_sample_video(data_dir: Path, sample_video: Path, real_models):  # noqa: ANN001
    truth = json.loads(SAMPLE_TRUTH.read_text(encoding="utf-8"))
    info = ffmpeg.probe(sample_video)
    with db.session_scope() as s:
        video = Video(title="Sample stream", storage_key=storage.new_key(), storage_mode="linked",
                      file_path=str(sample_video), size_bytes=info.size_bytes, duration=info.duration, width=info.width,
                      height=info.height, fps=info.fps, has_audio=info.has_audio, creator_name="Sample Streamer",
                      permission_status="owned")
        s.add(video)
        s.flush()
        video_id = video.id
        pipeline.enqueue_analysis(s, video, {"count": 4, "min_len": 10, "max_len": 40, "min_score": 50,
                                             "content_preset": "gaming", "auto_render": True})

    # ---- analysis: transcription, signals, highlights ------------------------------------------------
    analysis = run_next()
    assert analysis.status == "succeeded", analysis.error
    with db.session_scope() as s:
        clips = s.query(Clip).filter_by(video_id=video_id).order_by(Clip.rank).all()
        spoken = " ".join(seg.text for seg in s.query(TranscriptSegment).filter_by(video_id=video_id)
                          .order_by(TranscriptSegment.idx)).lower()
        found = [(c.id, c.start, c.end, c.score, c.category, c.title) for c in clips]
    for phrase in ("one health point", "flashlight on my phone", "hot take", "fell off the map"):
        assert phrase in spoken, f"the transcript is missing: {phrase}"

    assert len(found) == 4, found
    worthy = [sec for sec in truth["sections"] if sec["worthy"]]
    filler = [sec for sec in truth["sections"] if not sec["worthy"]]

    def inside(start: float, end: float, sections: list[dict]) -> float:
        covered = sum(max(0.0, min(end, sec["end"]) - max(start, sec["start"])) for sec in sections)
        return covered / (end - start)

    for section in worthy:  # every planted moment was found...
        assert any(inside(start, end, [section]) >= 0.6 for _, start, end, *_ in found), f"missed {section['name']}: {found}"
    for _, start, end, score, _, title in found:  # ...and no suggestion is mostly filler
        assert inside(start, end, filler) < 0.35, f"mostly filler: {title}"
        assert score >= 50

    # ---- rendering -----------------------------------------------------------------------------------
    for _ in found:
        render = run_next()
        assert render.type == "render" and render.status == "succeeded", render.error
    with db.session_scope() as s:
        for clip in s.query(Clip).filter_by(video_id=video_id):
            path = storage.data_path(clip.file_path)
            out = ffmpeg.probe(path)
            assert (out.width, out.height) == (1080, 1920)
            assert out.duration == pytest.approx(clip.end - clip.start, abs=0.3)
            assert (out.video_codec, out.audio_codec) == ("h264", "aac")
            assert all(check["status"] != "fail" for check in clip.qc), clip.qc
            assert clip.render_info["captions"]["events"] > 5 and clip.render_info["captions"]["font_found"]
            srt = (path.parent / "captions.srt").read_text(encoding="utf-8")
            assert srt.count("-->") >= 3
            assert storage.data_path(clip.thumb_path).stat().st_size > 2000

    # ---- a second analysis reuses the saved transcript and signals ------------------------------------
    with db.session_scope() as s:
        pipeline.enqueue_analysis(s, s.get(Video, video_id), {"count": 4, "min_len": 10, "max_len": 40, "min_score": 50})
    started = time.monotonic()
    again = run_next()
    assert again.status == "succeeded", again.error
    assert next(stage for stage in again.stages if stage["key"] == "transcribe")["status"] == "skipped"
    assert time.monotonic() - started < 15, "re-analysis should not transcribe again"
