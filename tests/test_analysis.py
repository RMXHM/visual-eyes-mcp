from __future__ import annotations

import json
from pathlib import Path

import pytest

from visual_eyes_mcp.analysis import AnalysisService
from visual_eyes_mcp.cache import Cache
from visual_eyes_mcp.config import load_settings
from visual_eyes_mcp.errors import ConfigError, InvalidSource, LimitExceeded, ProviderError
from visual_eyes_mcp.models import MediaInfo, SourceRef

ANSWER = {
    "answer": "The speaker configures the database.",
    "key_points": ["opens settings", "sets the URL"],
    "timestamps": [{"seconds": 2.0, "note": "settings screen"}],
    "confidence": "high",
}

MOMENTS = {
    "moments": [
        {
            "start_seconds": 1,
            "end_seconds": 2,
            "title": "low",
            "reason": "low confidence",
            "confidence": 0.4,
        },
        {
            "start_seconds": 3,
            "end_seconds": 4,
            "title": "best",
            "reason": "clearly matches",
            "confidence": 0.9,
        },
        {
            "start_seconds": 20,
            "end_seconds": 21,
            "title": "outside",
            "reason": "past the end",
            "confidence": 0.8,
        },
        {
            "start_seconds": 8,
            "end_seconds": 9,
            "title": "second",
            "reason": "reasonable match",
            "confidence": 0.7,
        },
    ]
}


def make_media(duration: float = 10.0, size: int = 10_000) -> MediaInfo:
    return MediaInfo(
        duration_s=duration,
        width=640,
        height=360,
        fps=30.0,
        has_audio=True,
        mime_type="video/mp4",
        size_bytes=size,
        container="mp4",
        video_codec="h264",
    )


def make_source(tmp_path: Path, *, kind: str = "local") -> SourceRef:
    if kind == "youtube":
        video_id = "dQw4w9WgXcQ"
        url = f"https://www.youtube.com/watch?v={video_id}"
        return SourceRef(
            kind="youtube",
            canonical_id=video_id,
            display=url,
            fingerprint="youtube-fingerprint",
            url=url,
        )
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"video-bytes")
    resolved = path.resolve()
    return SourceRef(
        kind="local",
        canonical_id=str(resolved),
        display="clip.mp4",
        fingerprint="local-fingerprint",
        path=resolved,
    )


def load_custom(tmp_path: Path, media_dir: Path, body: str = ""):
    config = tmp_path / "custom.toml"
    config.write_text(
        f'[paths]\nallowed_dirs = ["{media_dir}"]\ninclude_cwd = false\n{body}',
        encoding="utf-8",
    )
    return load_settings(
        env={
            "VISUAL_EYES_CONFIG": str(config),
            "XDG_CACHE_HOME": str(tmp_path / "custom-cache"),
        }
    )


def build(settings, make_provider, replies=None):
    cache = Cache(settings.data_dir)
    provider = make_provider(replies)
    service = AnalysisService(settings, cache, provider)
    return service, provider, cache


def analyze(service, source, media, **overrides):
    kwargs = {
        "source": source,
        "media": media,
        "question": "What happens in the video?",
        "start_seconds": None,
        "end_seconds": None,
        "detail": "normal",
        "refresh": False,
    }
    kwargs.update(overrides)
    return service.analyze_video(**kwargs)


def find(service, source, media, **overrides):
    kwargs = {
        "source": source,
        "media": media,
        "description": "where is the database configured",
        "max_results": 5,
        "start_seconds": None,
        "end_seconds": None,
        "detail": "normal",
        "refresh": False,
    }
    kwargs.update(overrides)
    return service.find_moments(**kwargs)


def test_analysis_happy_path(settings, make_provider, tmp_path: Path) -> None:
    service, provider, cache = build(settings, make_provider, [ANSWER])
    source = make_source(tmp_path)

    result = analyze(service, source, make_media())

    assert result.answer == ANSWER["answer"]
    assert result.key_points == ANSWER["key_points"]
    assert result.timestamps[0].seconds == 2.0
    assert result.timestamps[0].label == "0:02"
    assert result.timestamps[0].note == "settings screen"
    assert result.segment is not None
    assert (result.segment.start_seconds, result.segment.end_seconds) == (0, 10)
    assert result.model == settings.models.default
    assert result.detail == "normal"
    assert result.cache.hit is False
    assert result.usage.input_tokens == 1000
    assert any("approximate" in warning for warning in result.warnings)

    request = provider.calls[0]
    assert request.model == settings.models.default
    assert request.video.kind == "local"
    assert request.video.path == source.path
    assert request.video.mime_type == "video/mp4"
    assert request.video.start_seconds is None
    assert "What happens in the video?" in request.prompt
    assert "answer" in request.json_schema["properties"]
    assert cache.usage_today(settings.models.default)["requests"] == 1


def test_cache_hit_avoids_second_paid_call(settings, make_provider, tmp_path: Path) -> None:
    service, provider, _ = build(settings, make_provider, [ANSWER, ANSWER])
    source = make_source(tmp_path)

    first = analyze(service, source, make_media())
    second = analyze(service, source, make_media())

    assert first.cache.hit is False
    assert second.cache.hit is True
    assert second.answer == first.answer
    assert len(provider.calls) == 1


def test_refresh_forces_a_new_call(settings, make_provider, tmp_path: Path) -> None:
    service, provider, _ = build(settings, make_provider, [ANSWER, ANSWER])
    source = make_source(tmp_path)

    analyze(service, source, make_media())
    refreshed = analyze(service, source, make_media(), refresh=True)

    assert len(provider.calls) == 2
    assert refreshed.cache.hit is False


def test_question_whitespace_is_normalised_for_cache(
    settings, make_provider, tmp_path: Path
) -> None:
    service, provider, _ = build(settings, make_provider, [ANSWER])
    source = make_source(tmp_path)

    analyze(service, source, make_media(), question="What  is\nthis?")
    second = analyze(service, source, make_media(), question="What is this?")

    assert len(provider.calls) == 1
    assert second.cache.hit is True


def test_high_detail_uses_high_model(settings, make_provider, tmp_path: Path) -> None:
    service, provider, _ = build(settings, make_provider, [ANSWER])
    source = make_source(tmp_path)

    result = analyze(service, source, make_media(), detail="high")

    assert provider.calls[0].model == settings.models.high
    assert result.detail == "high"
    assert provider.calls[0].video.fps is None


def test_low_detail_requests_lower_fps(settings, make_provider, tmp_path: Path) -> None:
    service, provider, _ = build(settings, make_provider, [ANSWER])
    source = make_source(tmp_path)

    analyze(service, source, make_media(), detail="low")

    assert provider.calls[0].video.fps == 0.5


def test_invalid_detail_is_rejected(settings, make_provider, tmp_path: Path) -> None:
    service, provider, _ = build(settings, make_provider, [ANSWER])

    with pytest.raises(InvalidSource, match="detail"):
        analyze(service, make_source(tmp_path), make_media(), detail="maximum")
    assert provider.calls == []


def test_high_detail_without_configured_model_fails(
    make_provider, make_settings, media_dir: Path, tmp_path: Path
) -> None:
    settings = load_custom(tmp_path, media_dir, '[models]\nhigh = ""\n')
    service, provider, _ = build(settings, make_provider, [ANSWER])

    with pytest.raises(ConfigError, match="detail='high' is not configured"):
        analyze(service, make_source(tmp_path), make_media(), detail="high")
    assert provider.calls == []


def test_local_range_is_sent_and_timestamps_are_filtered(
    settings, make_provider, tmp_path: Path
) -> None:
    reply = {
        "answer": "Only one moment is in range.",
        "key_points": [],
        "timestamps": [
            {"seconds": 1, "note": "before"},
            {"seconds": 3, "note": "inside"},
            {"seconds": 6, "note": "after"},
        ],
        "confidence": "medium",
    }
    service, provider, _ = build(settings, make_provider, [reply])
    source = make_source(tmp_path)

    result = analyze(service, source, make_media(), start_seconds=2, end_seconds=5)

    request = provider.calls[0]
    assert request.video.start_seconds == 2
    assert request.video.end_seconds == 5
    assert "2.0s to 5.0s" in request.prompt
    assert [timestamp.seconds for timestamp in result.timestamps] == [3.0]
    assert any("outside the requested range" in warning for warning in result.warnings)
    assert result.segment is not None
    assert (result.segment.start_seconds, result.segment.end_seconds) == (2, 5)


def test_whole_video_timestamps_are_clamped(settings, make_provider, tmp_path: Path) -> None:
    reply = {
        "answer": "x",
        "key_points": [],
        "timestamps": [
            {"seconds": 0, "note": "start"},
            {"seconds": 10, "note": "end"},
            {"seconds": 10.5, "note": "past end"},
        ],
        "confidence": "low",
    }
    service, _, _ = build(settings, make_provider, [reply])

    result = analyze(service, make_source(tmp_path), make_media())

    assert [timestamp.seconds for timestamp in result.timestamps] == [0.0, 10.0]


@pytest.mark.parametrize(
    ("start", "end", "error"),
    [
        (1, None, InvalidSource),
        (None, 1, InvalidSource),
        (5, 2, InvalidSource),
        (-1, 2, InvalidSource),
        (10, 12, LimitExceeded),
    ],
)
def test_invalid_ranges_are_rejected(
    settings, make_provider, tmp_path: Path, start, end, error
) -> None:
    service, provider, _ = build(settings, make_provider, [ANSWER])

    with pytest.raises(error):
        analyze(service, make_source(tmp_path), make_media(), start_seconds=start, end_seconds=end)
    assert provider.calls == []


def test_video_duration_limit_is_enforced(settings, make_provider, tmp_path: Path) -> None:
    service, provider, _ = build(settings, make_provider, [ANSWER])

    with pytest.raises(LimitExceeded, match="processed duration"):
        analyze(service, make_source(tmp_path), make_media(duration=2000))
    assert provider.calls == []


def test_token_limit_is_enforced_before_calling(
    make_provider, make_settings, media_dir: Path, tmp_path: Path
) -> None:
    settings = make_settings(
        VISUAL_EYES_ALLOWED_DIRS=str(media_dir),
        VISUAL_EYES_MAX_INPUT_TOKENS_PER_CALL="1",
    )
    service, provider, _ = build(settings, make_provider, [ANSWER])

    with pytest.raises(LimitExceeded, match="estimated input"):
        analyze(service, make_source(tmp_path), make_media())
    assert provider.calls == []


def test_long_answer_is_truncated(
    make_provider, make_settings, media_dir: Path, tmp_path: Path
) -> None:
    settings = load_custom(tmp_path, media_dir, "[limits]\nmax_answer_chars = 100\n")
    reply = {
        "answer": "x" * 200,
        "key_points": [],
        "timestamps": [],
        "confidence": "medium",
    }
    service, _, _ = build(settings, make_provider, [reply])

    result = analyze(service, make_source(tmp_path), make_media())

    assert len(result.answer) == 101
    assert result.answer.endswith("…")
    assert any("truncated" in warning for warning in result.warnings)


def test_invalid_json_is_reasked_once(settings, make_provider, tmp_path: Path) -> None:
    service, provider, _ = build(settings, make_provider, ["this is not json", ANSWER])

    result = analyze(service, make_source(tmp_path), make_media())

    assert result.answer == ANSWER["answer"]
    assert len(provider.calls) == 2
    assert "not valid JSON" in provider.calls[1].prompt
    assert result.usage.input_tokens == 2000
    assert result.usage.output_tokens == 200


def test_invalid_json_twice_is_a_provider_error(settings, make_provider, tmp_path: Path) -> None:
    service, provider, _ = build(settings, make_provider, ["nope", "still nope"])

    with pytest.raises(ProviderError, match="invalid structured output twice"):
        analyze(service, make_source(tmp_path), make_media())
    assert len(provider.calls) == 2


def test_json_code_fences_are_accepted(settings, make_provider, tmp_path: Path) -> None:
    fenced = "```json\n" + json.dumps(ANSWER) + "\n```"
    service, provider, _ = build(settings, make_provider, [fenced])

    result = analyze(service, make_source(tmp_path), make_media())

    assert result.answer == ANSWER["answer"]
    assert len(provider.calls) == 1


def test_provider_errors_release_the_quota_reservation(
    settings, make_provider, tmp_path: Path
) -> None:
    service, _, cache = build(settings, make_provider, [ProviderError("boom")])

    with pytest.raises(ProviderError, match="boom"):
        analyze(service, make_source(tmp_path), make_media())

    assert cache.usage_today(settings.models.default)["requests"] == 0


def test_moments_are_sorted_and_limited(settings, make_provider, tmp_path: Path) -> None:
    service, provider, _ = build(settings, make_provider, [MOMENTS])

    result = find(service, make_source(tmp_path), make_media(), max_results=2)

    assert [moment.title for moment in result.moments] == ["best", "second"]
    assert [moment.confidence for moment in result.moments] == [0.9, 0.7]
    assert result.searched_range is not None
    assert (result.searched_range.start_seconds, result.searched_range.end_seconds) == (0, 10)
    assert provider.calls[0].video.kind == "local"


def test_moments_are_clamped_to_a_range(settings, make_provider, tmp_path: Path) -> None:
    reply = {
        "moments": [
            {
                "start_seconds": 2,
                "end_seconds": 3,
                "title": "before",
                "reason": "outside",
                "confidence": 0.9,
            },
            {
                "start_seconds": 6,
                "end_seconds": 12,
                "title": "overlap",
                "reason": "overlaps the end",
                "confidence": 0.8,
            },
        ]
    }
    service, _, _ = build(settings, make_provider, [reply])

    result = find(
        service,
        make_source(tmp_path),
        make_media(),
        start_seconds=5,
        end_seconds=8,
        max_results=5,
    )

    assert [moment.title for moment in result.moments] == ["overlap"]
    assert result.moments[0].start_seconds == 6
    assert result.moments[0].end_seconds == 8
    assert any("out-of-range" in warning for warning in result.warnings)


def test_empty_moments_warns(settings, make_provider, tmp_path: Path) -> None:
    service, _, _ = build(settings, make_provider, [{"moments": []}])

    result = find(service, make_source(tmp_path), make_media())

    assert result.moments == []
    assert any("no matching moments" in warning for warning in result.warnings)


def test_find_moments_cache_hit(settings, make_provider, tmp_path: Path) -> None:
    service, provider, _ = build(settings, make_provider, [MOMENTS])

    first = find(service, make_source(tmp_path), make_media())
    second = find(service, make_source(tmp_path), make_media())

    assert first.cache.hit is False
    assert second.cache.hit is True
    assert len(provider.calls) == 1


def test_youtube_unknown_duration_is_warned(settings, make_provider, tmp_path: Path) -> None:
    service, provider, _ = build(settings, make_provider, [ANSWER])

    result = analyze(service, make_source(tmp_path, kind="youtube"), None)

    assert any("duration is unknown" in warning for warning in result.warnings)
    assert provider.calls[0].video.kind == "youtube"
    assert provider.calls[0].video.uri is not None
    assert provider.calls[0].video.start_seconds is None


def test_youtube_range_is_not_sent_but_is_warned(settings, make_provider, tmp_path: Path) -> None:
    service, provider, _ = build(settings, make_provider, [ANSWER])

    result = analyze(
        service,
        make_source(tmp_path, kind="youtube"),
        None,
        start_seconds=2,
        end_seconds=5,
    )

    request = provider.calls[0]
    assert request.video.start_seconds is None
    assert request.video.end_seconds is None
    assert "2.0s to 5.0s" in request.prompt
    assert any(
        "full YouTube video may have been processed" in warning
        for warning in result.warnings
    )
    assert result.segment is not None
    assert (result.segment.start_seconds, result.segment.end_seconds) == (2, 5)


def test_cache_key_includes_detail(settings, make_provider, tmp_path: Path) -> None:
    service, provider, _ = build(settings, make_provider, [ANSWER, ANSWER])
    source = make_source(tmp_path)

    analyze(service, source, make_media(), detail="normal")
    analyze(service, source, make_media(), detail="low")

    assert len(provider.calls) == 2
