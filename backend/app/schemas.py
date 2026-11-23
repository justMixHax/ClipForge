"""Typed request and response bodies for the HTTP API."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .analysis.presets import LENGTH_RANGES

Permission = Literal["owned", "licensed", "permission", "unknown", "denied"]
Aspect = Literal["9:16", "1:1", "4:5", "16:9"]
Layout = Literal["auto", "fill", "fit_blur", "facecam_top", "split"]
ContentPreset = Literal["auto", "gaming", "streamer", "podcast"]
CaptionPreset = Literal["opus", "karaoke", "minimal", "gaming", "bold", "word"]
Quality = Literal["speed", "balanced", "quality"]
ReviewStatus = Literal["pending", "approved", "rejected"]
FeedbackLabel = Literal["good", "bad", "wrong_moment", "poor_subtitles", "bad_cropping", "not_entertaining",
                        "too_much_context"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FromRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ------------------------------------------------------------------------------------------------ jobs
class JobOut(FromRow):
    id: int
    type: str
    status: str
    priority: int
    progress: float
    stage: str
    message: str
    stages: list[dict[str, Any]] | None = None
    error: str
    video_id: int | None
    clip_id: int | None
    attempts: int
    result: dict[str, Any] | None = None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


# ---------------------------------------------------------------------------------------------- videos
class VideoOut(FromRow):
    id: int
    project_id: int | None
    title: str
    original_filename: str
    storage_mode: str
    size_bytes: int
    duration: float
    width: int
    height: int
    fps: float
    video_codec: str
    audio_codec: str
    has_audio: bool
    source_type: str
    source_url: str
    creator_name: str
    creator_handle: str
    permission_status: str
    permission_note: str
    attribution: str
    status: str
    language: str
    content_preset: str
    analysis: dict[str, Any] | None
    last_error: str
    created_at: datetime
    updated_at: datetime
    stream_url: str = ""
    thumb_url: str = ""
    clip_count: int = 0
    active_job: JobOut | None = None
    duplicate_of: int | None = None


class VideoPatch(Strict):
    title: str | None = Field(None, min_length=1, max_length=300)
    creator_name: str | None = Field(None, max_length=200)
    creator_handle: str | None = Field(None, max_length=200)
    permission_status: Permission | None = None
    permission_note: str | None = Field(None, max_length=2000)
    attribution: str | None = Field(None, max_length=300)
    source_url: str | None = Field(None, max_length=1000)
    project_id: int | None = None


class ImportPathIn(Strict):
    path: str = Field(min_length=1, max_length=1000)
    title: str | None = Field(None, max_length=300)
    creator_name: str = Field("", max_length=200)
    permission_status: Permission = "unknown"
    permission_note: str = Field("", max_length=2000)
    source_url: str = Field("", max_length=1000)
    project_id: int | None = None


MaxHeight = Literal[720, 1080, 1440, 2160]


class InspectUrlIn(Strict):
    url: str = Field(min_length=1, max_length=4000)
    max_height: MaxHeight = 1080


class LinkInfoOut(BaseModel):
    url: str
    title: str
    creator: str
    creator_url: str
    site: str
    video_id: str
    duration: float | None
    is_live: bool
    license: str
    approx_bytes: int | None
    width: int | None
    height: int | None
    over_limit: bool                 # bigger than the import size limit at the chosen quality
    limit_gb: float
    existing_video_id: int | None    # this link is already in the library


class AnalyzeIn(Strict):
    count: int = Field(5, ge=1, le=30)
    length: str = Field("15-60", max_length=10)
    min_len: float | None = Field(None, ge=3, le=300)
    max_len: float | None = Field(None, ge=4, le=600)
    min_score: float = Field(0, ge=0, le=100)
    content_preset: ContentPreset = "auto"
    game_style: str = Field("", max_length=20)
    aspect: Aspect = "9:16"
    layout: Layout = "auto"
    captions: CaptionPreset = "opus"
    quality: Quality = "balanced"
    auto_render: bool = True
    language: str = Field("auto", max_length=8)
    remove_silence: bool = False
    show_hook: bool = True

    @field_validator("length")
    @classmethod
    def _known_length(cls, value: str) -> str:
        if value not in LENGTH_RANGES:
            raise ValueError(f"unknown clip length; use one of {', '.join(LENGTH_RANGES)}, or min_len and max_len")
        return value


class MomentIn(Strict):
    """A moment you chose. Times can be typed (1:02:15, 62:15, 3735, 1h2m15s) or pasted as a link shared at
    that moment. Without an end, ClipForge finds one inside your clip length."""
    start: float | str = Field(..., description="When the moment starts")
    end: float | str | None = Field(None, description="When it ends (optional)")
    options: AnalyzeIn | None = None

    @field_validator("start", "end")
    @classmethod
    def _short_text(cls, value: float | str | None) -> float | str | None:
        if isinstance(value, str) and len(value) > 500:
            raise ValueError("too long to be a time or a link")
        return value


class ImportUrlIn(Strict):
    url: str = Field(min_length=1, max_length=4000)
    permission_status: Permission    # required: an import does not start until you say why you may clip it
    permission_note: str = Field("", max_length=2000)
    creator_name: str = Field("", max_length=200)
    title: str | None = Field(None, max_length=300)
    project_id: int | None = None
    max_height: MaxHeight = 1080
    analyze: AnalyzeIn | None = None  # set to go straight on to finding clips once the video has arrived


class SegmentOut(FromRow):
    id: int
    idx: int
    start: float
    end: float
    text: str
    speaker: str
    words: list[list[Any]]
    edited: bool


class SegmentPatch(Strict):
    text: str = Field(min_length=1, max_length=2000)


# ----------------------------------------------------------------------------------------------- clips
class ClipCreate(Strict):
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    title: str = Field("", max_length=300)


class ClipPatch(Strict):
    title: str | None = Field(None, max_length=300)
    hook_text: str | None = Field(None, max_length=300)
    description: str | None = Field(None, max_length=5000)
    hashtags: list[str] | None = Field(None, max_length=30)
    start: float | None = Field(None, ge=0)
    end: float | None = Field(None, gt=0)
    review_status: ReviewStatus | None = None
    favorite: bool | None = None
    manual_rank: int | None = Field(None, ge=0, le=100000)
    settings: dict[str, Any] | None = None
    captions: list[dict[str, Any]] | None = None
    tiktok_caption: str | None = Field(None, max_length=4000)  # "": back to the automatic caption


class ClipOut(FromRow):
    id: int
    video_id: int
    project_id: int | None
    origin: str
    title: str
    hook_text: str
    hook_options: list[str]
    description: str
    hashtags: list[str]
    category: str
    transcript_text: str
    start: float
    end: float
    score: float
    subscores: dict[str, float]
    confidence: float
    explanation: str
    evidence: list[dict[str, Any]]
    analysis_source: str
    rank: int
    manual_rank: int | None
    review_status: str
    favorite: bool
    settings: dict[str, Any]
    captions: list[dict[str, Any]] | None
    render_status: str
    render_error: str
    file_size: int
    out_width: int
    out_height: int
    out_duration: float
    render_info: dict[str, Any] | None
    qc: list[dict[str, Any]] | None
    rendered_at: datetime | None
    variant_of: int | None
    variant_label: str
    created_at: datetime
    updated_at: datetime
    file_url: str = ""
    thumb_url: str = ""
    cover_url: str = ""
    cover_text: str = ""                     # the words on the thumbnail (from the title when none were chosen)
    cover_word: str = ""
    cover_time: float | None = None          # the source time of its picture
    post_caption: str = ""                   # the caption to paste into TikTok: yours, else words, credit, hashtags
    tiktok_caption: str = ""                 # the caption you wrote or saved, if any
    last_edit: dict[str, Any] | None = None  # the last prompt edit: what you asked, what changed, what could not
    video_title: str = ""
    creator_name: str = ""
    permission_status: str = ""
    feedback: list[str] = []


class ClipList(BaseModel):
    items: list[ClipOut]
    total: int


class FeedbackIn(Strict):
    label: FeedbackLabel
    note: str = Field("", max_length=1000)


class BatchIn(Strict):
    ids: list[int] = Field(min_length=1, max_length=500)
    action: Literal["approve", "reject", "pending", "delete", "render", "favorite", "unfavorite"]


class ReorderIn(Strict):
    ids: list[int] = Field(min_length=1, max_length=500)


class CoverIn(Strict):
    text: str | None = Field(None, max_length=120)  # None: keep the words; "": back to the title's
    word: str = Field("", max_length=60)            # the word to colour
    time: float | None = None                       # the source time of the picture; None: keep it
    auto: bool = False                              # let Forge pick the best moment again


class TikTokCaptionIn(Strict):
    emojis: bool = True
    hashtags: bool = True


class PromptEditIn(Strict):
    instruction: str = Field(min_length=1, max_length=1000)  # what you want changed, in your own words


class PromptEditOut(BaseModel):
    summary: str
    done: list[str]        # what changed
    not_done: list[str]    # what was asked for but not made, and why
    rendering: bool        # rendered again (nothing is rendered when nothing changed)
    clip: ClipOut


class ExportIn(Strict):
    ids: list[int] = Field(min_length=1, max_length=200)


# ---------------------------------------------------------------------------------------------- system
class SettingsIn(Strict):
    values: dict[str, Any]


class SecretIn(Strict):
    name: str = Field(max_length=100)
    value: str = Field(max_length=500)


def _project_name(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    if not value:
        raise ValueError("a project needs a name")
    return value


class ProjectIn(Strict):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field("", max_length=2000)

    _trim = field_validator("name")(_project_name)


class ProjectPatch(Strict):
    """Only what is sent is changed, so renaming a project keeps its description."""
    name: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = Field(None, max_length=2000)

    _trim = field_validator("name")(_project_name)


class ProjectOut(FromRow):
    id: int
    name: str
    description: str
    created_at: datetime
    video_count: int = 0
    clip_count: int = 0


class CleanupIn(Strict):
    what: Literal["work", "rejected", "failed_jobs"]
