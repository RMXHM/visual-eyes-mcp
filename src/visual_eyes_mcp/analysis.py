"""Analysis orchestration: prompts, model tiers, cache, quota, validation."""

from __future__ import annotations

import hashlib
import json
import math
import threading
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from .cache import Cache
from .config import Settings
from .errors import ConfigError, InvalidSource, LimitExceeded, ProviderError
from .models import (
    AnswerPayload,
    CacheInfo,
    FindMomentsResult,
    MediaInfo,
    Moment,
    MomentsPayload,
    ProviderRequest,
    ProviderResult,
    Segment,
    SourceRef,
    TimestampRef,
    Usage,
    VideoAnswer,
    VideoInput,
    format_timestamp,
)
from .prompts import PROMPT_VERSION, SCHEMA_VERSION, build_analysis_prompt, build_moments_prompt

PayloadT = TypeVar("PayloadT", bound=BaseModel)

_TOKENS_PER_SECOND = 100
_ESTIMATE_OVERHEAD = 1000
_MAX_TIMESTAMPS = 5
_MAX_KEY_POINTS = 5
_MAX_NOTE_CHARS = 300


class VideoProvider(Protocol):
    def analyze(self, request: ProviderRequest) -> ProviderResult: ...


class AnalysisService:
    def __init__(
        self,
        settings: Settings,
        cache: Cache,
        provider: VideoProvider,
        *,
        call_slots: threading.BoundedSemaphore | None = None,
    ) -> None:
        self.settings = settings
        self.cache = cache
        self.provider = provider
        self.call_slots = (
            call_slots
            if call_slots is not None
            else threading.BoundedSemaphore(settings.limits.max_concurrent_calls)
        )

    # -- public operations ----------------------------------------------

    def analyze_video(
        self,
        *,
        source: SourceRef,
        media: MediaInfo | None,
        question: str,
        start_seconds: float | None,
        end_seconds: float | None,
        detail: str = "normal",
        refresh: bool = False,
    ) -> VideoAnswer:
        model = self._resolve_model(detail)
        start, end, segment, warnings = self._normalise_range(
            start_seconds, end_seconds, media.duration_s if media else None
        )
        processed = self._processed_seconds(start, end, media)
        estimated = self._enforce_limits(processed, media)
        warnings.extend(self._source_warnings(source, media, start, end))

        params = {
            "question": " ".join(question.split()),
            "start_seconds": start,
            "end_seconds": end,
        }
        cache_key = self._cache_key("analyze_video", source, model, detail, params)
        if not refresh:
            cached = self.cache.get_analysis(cache_key)
            if cached is not None:
                answer = VideoAnswer.model_validate(cached)
                return answer.model_copy(update={"cache": CacheInfo(hit=True)})

        request = ProviderRequest(
            model=model,
            prompt=build_analysis_prompt(question, start, end),
            json_schema=AnswerPayload.model_json_schema(),
            video=self._video_input(source, media, start, end, detail),
            source_id=self.cache.source_id(source),
            fingerprint=source.fingerprint,
            store=self.settings.privacy.store_interactions,
            timeout_seconds=self.settings.limits.analysis_timeout_seconds,
        )
        payload, usage = self._paid_json(request, AnswerPayload, estimated)

        timestamps, dropped = self._clamp_timestamps(payload.timestamps, start, end, media)
        if dropped:
            warnings.append(f"{dropped} timestamp(s) outside the requested range were discarded")

        answer = payload.answer.strip()
        if len(answer) > self.settings.limits.max_answer_chars:
            answer = answer[: self.settings.limits.max_answer_chars].rstrip() + "…"
            warnings.append(
                f"answer was truncated to {self.settings.limits.max_answer_chars} characters"
            )

        key_points = [
            point.strip()[: _MAX_NOTE_CHARS]
            for point in payload.key_points[:_MAX_KEY_POINTS]
            if point.strip()
        ]
        result = VideoAnswer(
            answer=answer,
            key_points=key_points,
            timestamps=timestamps,
            segment=segment,
            confidence=payload.confidence,
            model=model,
            detail=detail,  # type: ignore[arg-type]
            cache=CacheInfo(hit=False),
            usage=usage,
            warnings=_dedupe(warnings),
        )
        self._store(cache_key, source, "analyze_video", params, result, usage)
        return result

    def find_moments(
        self,
        *,
        source: SourceRef,
        media: MediaInfo | None,
        description: str,
        max_results: int,
        start_seconds: float | None,
        end_seconds: float | None,
        detail: str = "normal",
        refresh: bool = False,
    ) -> FindMomentsResult:
        model = self._resolve_model(detail)
        start, end, segment, warnings = self._normalise_range(
            start_seconds, end_seconds, media.duration_s if media else None
        )
        processed = self._processed_seconds(start, end, media)
        estimated = self._enforce_limits(processed, media)
        warnings.extend(self._source_warnings(source, media, start, end))
        warnings.append("moment bounds are model-estimated, not frame-exact")

        params = {
            "description": " ".join(description.split()),
            "max_results": max_results,
            "start_seconds": start,
            "end_seconds": end,
        }
        cache_key = self._cache_key("find_moments", source, model, detail, params)
        if not refresh:
            cached = self.cache.get_analysis(cache_key)
            if cached is not None:
                result = FindMomentsResult.model_validate(cached)
                return result.model_copy(update={"cache": CacheInfo(hit=True)})

        request = ProviderRequest(
            model=model,
            prompt=build_moments_prompt(description, start, end, max_results),
            json_schema=MomentsPayload.model_json_schema(),
            video=self._video_input(source, media, start, end, detail),
            source_id=self.cache.source_id(source),
            fingerprint=source.fingerprint,
            store=self.settings.privacy.store_interactions,
            timeout_seconds=self.settings.limits.analysis_timeout_seconds,
        )
        payload, usage = self._paid_json(request, MomentsPayload, estimated)

        moments, dropped = self._clamp_moments(payload.moments, start, end, media, max_results)
        if dropped:
            warnings.append(f"{dropped} out-of-range moment(s) were discarded")
        if not moments:
            warnings.append("no matching moments were found in the searched range")

        result = FindMomentsResult(
            moments=moments,
            searched_range=segment,
            model=model,
            detail=detail,  # type: ignore[arg-type]
            cache=CacheInfo(hit=False),
            usage=usage,
            warnings=_dedupe(warnings),
        )
        self._store(cache_key, source, "find_moments", params, result, usage)
        return result

    # -- model and limits ------------------------------------------------

    def _resolve_model(self, detail: str) -> str:
        if detail not in {"low", "normal", "high"}:
            raise InvalidSource("detail must be one of low, normal, high")
        if detail == "high":
            model = self.settings.models.high
            if not model:
                raise ConfigError(
                    "detail='high' is not configured",
                    hint="set models.high in config.toml or use detail='normal'",
                )
            return model
        return self.settings.models.default

    def _normalise_range(
        self, start: float | None, end: float | None, duration: float | None
    ) -> tuple[float | None, float | None, Segment | None, list[str]]:
        warnings: list[str] = []
        if (start is None) != (end is None):
            raise InvalidSource("provide both start_seconds and end_seconds, or neither")

        if start is None or end is None:
            eff_start: float | None = None
            eff_end: float | None = None
            segment = Segment(start_seconds=0, end_seconds=duration) if duration else None
            return eff_start, eff_end, segment, warnings

        if not math.isfinite(start) or not math.isfinite(end):
            raise InvalidSource("time range must be finite")
        if start < 0:
            raise InvalidSource("start_seconds must be zero or greater")
        if end <= start:
            raise InvalidSource("end_seconds must be greater than start_seconds")

        if duration is not None:
            if start >= duration:
                raise LimitExceeded(
                    f"start_seconds ({start:g}) is beyond the video duration ({duration:g}s)"
                )
            end = min(end, duration)

        return start, end, Segment(start_seconds=start, end_seconds=end), warnings

    def _processed_seconds(
        self, start: float | None, end: float | None, media: MediaInfo | None
    ) -> float | None:
        if start is not None and end is not None:
            return end - start
        return media.duration_s if media else None

    def _enforce_limits(self, processed: float | None, media: MediaInfo | None) -> int:
        limits = self.settings.limits
        if media is not None and media.size_bytes > limits.max_file_size_mb * 1024 * 1024:
            raise LimitExceeded(
                f"file is larger than the {limits.max_file_size_mb} MB limit",
                hint="narrow the work by extracting frames, or raise limits.max_file_size_mb",
            )
        if processed is not None and processed > limits.max_video_seconds:
            raise LimitExceeded(
                f"processed duration ({processed:.0f}s) exceeds the "
                f"{limits.max_video_seconds:.0f}s limit",
                hint="request a smaller time range or raise limits.max_video_seconds",
            )

        if processed is None:
            estimated = limits.max_input_tokens_per_call
        else:
            estimated = math.ceil(processed * _TOKENS_PER_SECOND) + _ESTIMATE_OVERHEAD
        if estimated > limits.max_input_tokens_per_call:
            raise LimitExceeded(
                f"estimated input ({estimated} tokens) exceeds the per-call limit "
                f"({limits.max_input_tokens_per_call})",
                hint="narrow the time range or raise limits.max_input_tokens_per_call",
            )
        return estimated

    def _source_warnings(
        self,
        source: SourceRef,
        media: MediaInfo | None,
        start: float | None,
        end: float | None,
    ) -> list[str]:
        warnings = ["timestamps are approximate (±1–2 s) at the default sampling rate"]
        if source.kind == "youtube":
            if media is None and start is None:
                warnings.append(
                    "YouTube duration is unknown; preflight uses the configured token cap"
                )
            if start is not None and end is not None:
                warnings.append(
                    "the full YouTube video may have been processed; "
                    "timestamps outside the requested range were discarded"
                )
        return warnings

    # -- provider calls --------------------------------------------------

    def _paid_json(
        self, request: ProviderRequest, payload_model: type[PayloadT], estimated: int
    ) -> tuple[PayloadT, Usage]:
        with self.call_slots:
            first = self._call_provider(request, estimated)
            try:
                return payload_model.model_validate(_parse_json(first.text)), first.usage
            except (ValueError, ValidationError) as exc:
                summary = _validation_summary(exc)
                retry_request = request.model_copy(
                    update={
                        "prompt": request.prompt
                        + "\n\nYour previous response was not valid JSON matching the schema ("
                        + summary
                        + "). Return only valid JSON."
                    }
                )
                second = self._call_provider(retry_request, estimated)
                try:
                    payload = payload_model.model_validate(_parse_json(second.text))
                except (ValueError, ValidationError) as retry_exc:
                    raise ProviderError(
                        "the model returned invalid structured output twice",
                        hint=_validation_summary(retry_exc),
                    ) from retry_exc
                return payload, _add_usage(first.usage, second.usage)

    def _call_provider(self, request: ProviderRequest, estimated: int) -> ProviderResult:
        quota = self.settings.quota_for(request.model)
        reservation = self.cache.reserve_quota(request.model, quota, estimated)
        try:
            result = self.provider.analyze(request)
        except Exception:
            self.cache.release_quota(reservation)
            raise
        self.cache.settle_quota(reservation, request.model, result.usage)
        return result

    def _video_input(
        self,
        source: SourceRef,
        media: MediaInfo | None,
        start: float | None,
        end: float | None,
        detail: str,
    ) -> VideoInput:
        if source.kind == "youtube":
            # Offsets for YouTube are unverified (U1), so they are not sent.
            return VideoInput(kind="youtube", uri=source.url)
        if media is None:  # pragma: no cover - tools always probe local files
            raise InvalidSource("local video was not probed")
        return VideoInput(
            kind="local",
            path=source.path,
            mime_type=media.mime_type,
            start_seconds=start,
            end_seconds=end,
            fps=0.5 if detail == "low" else None,
        )

    # -- cache ------------------------------------------------------------

    def _cache_key(
        self, tool: str, source: SourceRef, model: str, detail: str, params: dict[str, Any]
    ) -> str:
        material = {
            "fingerprint": source.fingerprint,
            "tool": tool,
            "model": model,
            "detail": detail,
            "params": params,
            "prompt_version": PROMPT_VERSION,
            "schema_version": SCHEMA_VERSION,
        }
        encoded = json.dumps(material, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

    def _store(
        self,
        cache_key: str,
        source: SourceRef,
        tool: str,
        params: dict[str, Any],
        result: BaseModel,
        usage: Usage,
    ) -> None:
        self.cache.put_analysis(
            cache_key=cache_key,
            source_id=self.cache.source_id(source),
            tool=tool,
            request=params,
            result=result.model_dump(mode="json"),
            model=result.model_dump()["model"],
            detail=result.model_dump()["detail"],
            usage=usage,
        )

    # -- output validation ------------------------------------------------

    def _clamp_timestamps(
        self,
        timestamps: list[Any],
        start: float | None,
        end: float | None,
        media: MediaInfo | None,
    ) -> tuple[list[TimestampRef], int]:
        low = start if start is not None else 0.0
        high = end if end is not None else (media.duration_s if media else None)
        refs: list[TimestampRef] = []
        dropped = 0
        for item in timestamps[:_MAX_TIMESTAMPS]:
            seconds = float(item.seconds)
            if high is not None and seconds > high:
                dropped += 1
                continue
            if seconds < low:
                dropped += 1
                continue
            if high is not None:
                seconds = min(seconds, high)
            seconds = max(seconds, low, 0.0)
            refs.append(
                TimestampRef(
                    seconds=round(seconds, 1),
                    label=format_timestamp(seconds),
                    note=item.note.strip()[:_MAX_NOTE_CHARS],
                )
            )
        return refs, dropped

    def _clamp_moments(
        self,
        moments: list[Any],
        start: float | None,
        end: float | None,
        media: MediaInfo | None,
        max_results: int,
    ) -> tuple[list[Moment], int]:
        low = start if start is not None else 0.0
        high = end if end is not None else (media.duration_s if media else None)
        kept: list[Moment] = []
        dropped = 0
        for item in moments:
            moment_start = float(item.start_seconds)
            moment_end = float(item.end_seconds)
            if high is not None and moment_start > high:
                dropped += 1
                continue
            if moment_end < low:
                dropped += 1
                continue
            moment_start = max(moment_start, low, 0.0)
            moment_end = max(moment_end, moment_start + 0.1)
            if high is not None:
                moment_start = min(moment_start, high)
                moment_end = min(moment_end, high)
            if moment_end <= moment_start:
                dropped += 1
                continue
            kept.append(
                Moment(
                    start_seconds=round(moment_start, 1),
                    end_seconds=round(moment_end, 1),
                    title=item.title.strip()[:200],
                    reason=item.reason.strip()[:500],
                    confidence=max(0.0, min(1.0, float(item.confidence))),
                )
            )
        kept.sort(key=lambda moment: moment.confidence, reverse=True)
        if len(kept) > max_results:
            dropped += len(kept) - max_results
            kept = kept[:max_results]
        return kept, dropped


def _parse_json(text: str) -> Any:
    candidate = text.strip()
    if candidate.startswith("```"):
        lines = candidate.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        candidate = "\n".join(lines).strip()
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start == -1 or end <= start:
            raise
        return json.loads(candidate[start : end + 1])


def _validation_summary(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        parts = []
        for error in exc.errors()[:3]:
            location = ".".join(str(part) for part in error["loc"]) or "<root>"
            parts.append(f"{location}: {error['msg']}")
        return "; ".join(parts)[:300]
    return str(exc)[:300]


def _add_usage(first: Usage, second: Usage) -> Usage:
    return Usage(
        input_tokens=(first.input_tokens or 0) + (second.input_tokens or 0),
        output_tokens=(first.output_tokens or 0) + (second.output_tokens or 0),
        thought_tokens=(first.thought_tokens or 0) + (second.thought_tokens or 0),
    )


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return out
