"""Tool implementations.

Handlers validate arguments, apply limits, and delegate to sources/media/analysis.
The MCP layer wraps anticipated errors as tool errors; nothing here touches the
network directly.
"""

from __future__ import annotations

import base64
import math
import threading
import uuid
from dataclasses import dataclass, field
from typing import Any

from mcp.types import CallToolResult, ImageContent, TextContent

from .analysis import AnalysisService, VideoProvider
from .cache import Cache
from .config import Settings
from .errors import ConfigError, InvalidSource, LimitExceeded
from .media import extract_frame, probe
from .models import (
    FrameRef,
    FramesResult,
    LimitsInfo,
    MediaInfo,
    SourceRef,
    UsedToday,
    VideoInfo,
)
from .sources import mime_for_upload, parse_source, upload_supported


@dataclass
class App:
    settings: Settings
    cache: Cache
    provider: VideoProvider | None = None
    _provider_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _call_slots: threading.BoundedSemaphore = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._call_slots = threading.BoundedSemaphore(
            self.settings.limits.max_concurrent_calls
        )

    @property
    def call_slots(self) -> threading.BoundedSemaphore:
        return self._call_slots

    @classmethod
    def from_settings(cls, settings: Settings) -> App:
        cache = Cache(
            settings.data_dir,
            ttl_seconds=settings.cache.ttl_seconds,
            max_mb=settings.cache.max_mb,
        )
        return cls(settings=settings, cache=cache)

    def get_provider(self) -> VideoProvider:
        with self._provider_lock:
            if self.provider is not None:
                return self.provider
            if self.settings.api_key is None:
                raise ConfigError(
                    "GEMINI_API_KEY is not set",
                    hint="set GEMINI_API_KEY (or GOOGLE_API_KEY) in the MCP client environment",
                )
            from .gemini import GeminiProvider

            self.provider = GeminiProvider(
                self.settings.api_key.get_secret_value(),
                cache=self.cache,
                store=self.settings.privacy.store_interactions,
                upload_timeout=self.settings.limits.upload_timeout_seconds,
            )
            return self.provider

    def service(self) -> AnalysisService:
        return AnalysisService(
            self.settings,
            self.cache,
            self.get_provider(),
            call_slots=self.call_slots,
        )


def video_info(app: App, source: str) -> VideoInfo:
    ref = parse_source(source, app.settings)
    media: MediaInfo | None = None
    notes: list[str] = []

    if ref.kind == "local":
        assert ref.path is not None
        media = probe(ref.path, timeout=app.settings.limits.probe_timeout_seconds)
        _check_size(media, app.settings)
        notes.append("duration and dimensions come from ffprobe")
        if not upload_supported(ref.path):
            notes.append(
                "this container can be probed and framed, but must be remuxed to mp4 for analysis"
            )
    else:
        notes.append("duration is unknown; the server does not resolve YouTube metadata")

    source_id = app.cache.record_source(ref, media)
    default_model = app.settings.models.default
    quota = app.settings.quota_for(default_model)
    used = app.cache.usage_today(default_model)

    return VideoInfo(
        source_kind=ref.kind,
        canonical_id=ref.canonical_id,
        duration_seconds=media.duration_s if media else None,
        width=media.width if media else None,
        height=media.height if media else None,
        fps=media.fps if media else None,
        has_audio=media.has_audio if media else None,
        size_bytes=media.size_bytes if media else None,
        mime_type=media.mime_type if media else None,
        cached_analyses=app.cache.cached_analyses(source_id),
        limits=LimitsInfo(
            model_default=app.settings.models.default,
            model_high=app.settings.models.high or None,
            max_video_seconds=app.settings.limits.max_video_seconds,
            max_input_tokens_per_call=app.settings.limits.max_input_tokens_per_call,
            max_file_size_mb=app.settings.limits.max_file_size_mb,
            max_frames_per_call=app.settings.limits.max_frames_per_call,
            rpm=quota.rpm if quota else None,
            rpd=quota.rpd if quota else None,
            tpm=quota.tpm if quota else None,
            used_today=UsedToday(**used),
        ),
        notes=notes,
    )


def analyze_video(
    app: App,
    *,
    source: str,
    question: str,
    start_seconds: float | None = None,
    end_seconds: float | None = None,
    detail: str = "normal",
    refresh: bool = False,
):
    question = question.strip()
    if not 3 <= len(question) <= 500:
        raise InvalidSource("question must be between 3 and 500 characters")
    if detail not in {"low", "normal", "high"}:
        raise InvalidSource("detail must be one of low, normal, high")

    provider = app.get_provider()
    ref, media = _prepare_source(app, source, require_uploadable=True)
    service = AnalysisService(
        app.settings,
        app.cache,
        provider,
        call_slots=app.call_slots,
    )
    return service.analyze_video(
        source=ref,
        media=media,
        question=question,
        start_seconds=start_seconds,
        end_seconds=end_seconds,
        detail=detail,
        refresh=refresh,
    )


def find_moments(
    app: App,
    *,
    source: str,
    description: str,
    max_results: int = 5,
    start_seconds: float | None = None,
    end_seconds: float | None = None,
    detail: str = "normal",
    refresh: bool = False,
):
    description = description.strip()
    if not 3 <= len(description) <= 300:
        raise InvalidSource("description must be between 3 and 300 characters")
    if not 1 <= max_results <= 10:
        raise InvalidSource("max_results must be between 1 and 10")
    if detail not in {"low", "normal", "high"}:
        raise InvalidSource("detail must be one of low, normal, high")

    provider = app.get_provider()
    ref, media = _prepare_source(app, source, require_uploadable=True)
    service = AnalysisService(
        app.settings,
        app.cache,
        provider,
        call_slots=app.call_slots,
    )
    return service.find_moments(
        source=ref,
        media=media,
        description=description,
        max_results=max_results,
        start_seconds=start_seconds,
        end_seconds=end_seconds,
        detail=detail,
        refresh=refresh,
    )


def extract_frames(
    app: App,
    *,
    source: str,
    timestamps: list[float] | None = None,
    interval_seconds: float | None = None,
    max_frames: int | None = None,
    width: int | None = None,
    start_seconds: float | None = None,
    end_seconds: float | None = None,
) -> CallToolResult:
    limits = app.settings.limits
    if (timestamps is None) == (interval_seconds is None):
        raise InvalidSource("provide exactly one of timestamps or interval_seconds")

    requested_max = max_frames if max_frames is not None else limits.default_frames
    if not 1 <= requested_max <= limits.max_frames_per_call:
        raise InvalidSource(
            f"max_frames must be between 1 and {limits.max_frames_per_call}"
        )
    frame_width = width if width is not None else 960
    if not limits.min_frame_width <= frame_width <= limits.max_frame_width:
        raise InvalidSource(
            f"width must be between {limits.min_frame_width} and {limits.max_frame_width}"
        )

    ref = parse_source(source, app.settings)
    if ref.kind != "local" or ref.path is None:
        raise InvalidSource(
            "frame extraction requires a local file",
            hint="downloading YouTube media is not supported",
        )

    media = probe(ref.path, timeout=limits.probe_timeout_seconds)
    _check_size(media, app.settings)
    source_id = app.cache.record_source(ref, media)

    points, truncated = _frame_points(
        media,
        timestamps=timestamps,
        interval_seconds=interval_seconds,
        max_frames=requested_max,
        start_seconds=start_seconds,
        end_seconds=end_seconds,
    )

    frames: list[FrameRef] = []
    content: list[Any] = []
    for seconds in points:
        cached = app.cache.get_frame(source_id, int(round(seconds * 1000)), frame_width)
        if cached is not None:
            path, actual_width, actual_height = cached
            hit = True
        else:
            temporary = app.cache.frames_dir / f".tmp-{uuid.uuid4().hex}.jpg"
            actual_width, actual_height = extract_frame(
                ref.path,
                seconds,
                frame_width,
                temporary,
                timeout=limits.frame_timeout_seconds,
            )
            path, hit = app.cache.store_frame(
                source_id=source_id,
                timestamp=seconds,
                width=frame_width,
                jpeg_path=temporary,
                frame_width=actual_width,
                frame_height=actual_height,
            )

        frames.append(
            FrameRef(
                seconds=round(seconds, 1),
                label=_label(seconds),
                path=str(path),
                width=actual_width,
                height=actual_height,
                cache_hit=hit,
            )
        )
        content.append(
            ImageContent(
                type="image",
                data=base64.b64encode(path.read_bytes()).decode("ascii"),
                mime_type="image/jpeg",
            )
        )

    metadata = FramesResult(frames=frames, truncated=truncated)
    content.append(TextContent(type="text", text=metadata.model_dump_json(indent=2)))
    return CallToolResult(content=content, structured_content=metadata.model_dump(mode="json"))


def _prepare_source(
    app: App, source: str, *, require_uploadable: bool
) -> tuple[SourceRef, MediaInfo | None]:
    ref = parse_source(source, app.settings)
    media: MediaInfo | None = None
    if ref.kind == "local":
        assert ref.path is not None
        media = probe(ref.path, timeout=app.settings.limits.probe_timeout_seconds)
        _check_size(media, app.settings)
        if require_uploadable and not upload_supported(ref.path):
            raise InvalidSource(
                "this container cannot be uploaded for analysis",
                hint=(
                    f"{ref.path.suffix} is not accepted by the provider; "
                    "remux to mp4: ffmpeg -i input -c copy output.mp4"
                ),
            )
        if require_uploadable and mime_for_upload(ref.path) is None:  # pragma: no cover
            raise InvalidSource("no upload mime type for this file")
        app.cache.record_source(ref, media)
    return ref, media


def _check_size(media: MediaInfo, settings: Settings) -> None:
    limit = settings.limits.max_file_size_mb * 1024 * 1024
    if media.size_bytes > limit:
        raise LimitExceeded(
            f"file is {media.size_bytes / (1024 * 1024):.1f} MB, over the "
            f"{settings.limits.max_file_size_mb} MB limit",
            hint="raise limits.max_file_size_mb or use a smaller file",
        )


def _frame_points(
    media: MediaInfo,
    *,
    timestamps: list[float] | None,
    interval_seconds: float | None,
    max_frames: int,
    start_seconds: float | None,
    end_seconds: float | None,
) -> tuple[list[float], bool]:
    duration = media.duration_s
    if timestamps is not None:
        if start_seconds is not None or end_seconds is not None:
            raise InvalidSource("start/end apply only to interval mode")
        if not 1 <= len(timestamps) <= max_frames:
            raise InvalidSource(f"timestamps must contain between 1 and {max_frames} values")
        points: list[float] = []
        for value in timestamps:
            if not math.isfinite(value) or value < 0 or value >= duration:
                raise InvalidSource(
                    f"timestamp {value!r} is outside the video (0 to {duration:g}s)"
                )
            if value not in points:
                points.append(float(value))
        return points, False

    assert interval_seconds is not None
    if not math.isfinite(interval_seconds) or interval_seconds < 5:
        raise InvalidSource("interval_seconds must be at least 5")
    low = 0.0 if start_seconds is None else start_seconds
    high = duration if end_seconds is None else end_seconds
    if start_seconds is None and end_seconds is not None:
        raise InvalidSource("provide both start_seconds and end_seconds, or neither")
    if start_seconds is not None and end_seconds is None:
        raise InvalidSource("provide both start_seconds and end_seconds, or neither")
    if not math.isfinite(low) or not math.isfinite(high) or low < 0 or high <= low:
        raise InvalidSource("invalid interval window")
    high = min(high, duration)

    points = []
    truncated = False
    cursor = low
    while cursor < high:
        if len(points) >= max_frames:
            truncated = True
            break
        points.append(round(cursor, 3))
        cursor += interval_seconds
    if not points:
        raise InvalidSource("the interval window produced no frames")
    return points, truncated


def _label(seconds: float) -> str:
    from .models import format_timestamp

    return format_timestamp(seconds)
