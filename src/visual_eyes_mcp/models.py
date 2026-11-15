"""Provider-neutral models shared by tools, cache, and the Gemini adapter."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

DetailLevel = Literal["low", "normal", "high"]
Confidence = Literal["low", "medium", "high"]


class WireModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class SourceRef(WireModel):
    kind: Literal["local", "youtube"]
    canonical_id: str
    display: str
    fingerprint: str
    path: Path | None = None
    url: str | None = None


class MediaInfo(WireModel):
    duration_s: float = Field(gt=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    fps: float | None = Field(default=None, gt=0)
    has_audio: bool
    mime_type: str
    size_bytes: int = Field(ge=0)
    container: str
    video_codec: str


class Usage(WireModel):
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    thought_tokens: int | None = Field(default=None, ge=0)

    @property
    def total(self) -> int:
        return (self.input_tokens or 0) + (self.output_tokens or 0) + (self.thought_tokens or 0)


class CacheInfo(WireModel):
    hit: bool = False


class Segment(WireModel):
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(gt=0)


class TimestampPayload(WireModel):
    seconds: float = Field(ge=0, description="Seconds from the start of the video.")
    note: str = Field(default="", description="What is visible or audible at this time.")


class AnswerPayload(WireModel):
    answer: str = Field(description="A direct answer in two to six sentences.")
    key_points: list[str] = Field(
        default_factory=list, description="Short supporting points, at most five."
    )
    timestamps: list[TimestampPayload] = Field(
        default_factory=list, description="Up to five evidence timestamps."
    )
    confidence: Confidence = Field(description="Confidence in the answer.")


class MomentPayload(WireModel):
    start_seconds: float = Field(ge=0, description="Start of the moment in seconds.")
    end_seconds: float = Field(gt=0, description="End of the moment in seconds.")
    title: str = Field(description="Short label for the moment.")
    reason: str = Field(description="Why this moment matches the description.")
    confidence: float = Field(ge=0, le=1, description="Relevance from 0 to 1.")


class MomentsPayload(WireModel):
    moments: list[MomentPayload] = Field(default_factory=list)


class TimestampRef(WireModel):
    seconds: float = Field(ge=0)
    label: str
    note: str = ""


class VideoAnswer(WireModel):
    answer: str
    key_points: list[str] = Field(default_factory=list)
    timestamps: list[TimestampRef] = Field(default_factory=list)
    segment: Segment | None = None
    confidence: Confidence = "medium"
    model: str
    detail: DetailLevel
    cache: CacheInfo = Field(default_factory=CacheInfo)
    usage: Usage = Field(default_factory=Usage)
    warnings: list[str] = Field(default_factory=list)


class Moment(WireModel):
    start_seconds: float
    end_seconds: float
    title: str
    reason: str
    confidence: float


class FindMomentsResult(WireModel):
    moments: list[Moment] = Field(default_factory=list)
    searched_range: Segment | None = None
    model: str
    detail: DetailLevel
    cache: CacheInfo = Field(default_factory=CacheInfo)
    usage: Usage = Field(default_factory=Usage)
    warnings: list[str] = Field(default_factory=list)


class UsedToday(WireModel):
    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0


class LimitsInfo(WireModel):
    model_default: str
    model_high: str | None
    max_video_seconds: float
    max_input_tokens_per_call: int
    max_file_size_mb: int
    max_frames_per_call: int
    rpm: int | None = None
    rpd: int | None = None
    tpm: int | None = None
    used_today: UsedToday = Field(default_factory=UsedToday)


class VideoInfo(WireModel):
    source_kind: Literal["local", "youtube"]
    canonical_id: str
    duration_seconds: float | None = None
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    has_audio: bool | None = None
    size_bytes: int | None = None
    mime_type: str | None = None
    cached_analyses: int = 0
    limits: LimitsInfo
    notes: list[str] = Field(default_factory=list)


class FrameRef(WireModel):
    seconds: float
    label: str
    path: str
    width: int
    height: int
    cache_hit: bool = False


class FramesResult(WireModel):
    frames: list[FrameRef] = Field(default_factory=list)
    source_kind: Literal["local"] = "local"
    truncated: bool = False


class VideoInput(WireModel):
    """How the provider should address the video."""

    kind: Literal["local", "youtube"]
    uri: str | None = None
    path: Path | None = None
    mime_type: str | None = None
    start_seconds: float | None = None
    end_seconds: float | None = None
    fps: float | None = None


class ProviderRequest(WireModel):
    model: str
    prompt: str
    json_schema: dict[str, Any]
    video: VideoInput
    source_id: str | None = None
    fingerprint: str | None = None
    store: bool = False
    timeout_seconds: float = 120.0


class ProviderResult(WireModel):
    text: str
    usage: Usage = Field(default_factory=Usage)


def format_timestamp(seconds: float) -> str:
    """Format seconds as m:ss or h:mm:ss (approximate by design)."""

    total = max(0, int(seconds + 0.5))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"
