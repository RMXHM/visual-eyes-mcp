from __future__ import annotations

from pathlib import Path

import pytest

from visual_eyes_mcp.analysis import AnalysisService
from visual_eyes_mcp.cache import Cache
from visual_eyes_mcp.config import DEFAULT_MODEL, DEFAULT_MODEL_HIGH, load_settings
from visual_eyes_mcp.errors import ProviderError, QuotaExhausted
from visual_eyes_mcp.models import MediaInfo, SourceRef

ANSWER = {
    "answer": "Quota-aware answer.",
    "key_points": [],
    "timestamps": [],
    "confidence": "medium",
}


def make_source(media_dir: Path) -> SourceRef:
    path = media_dir / "clip.mp4"
    path.write_bytes(b"video-bytes")
    return SourceRef(
        kind="local",
        canonical_id=str(path.resolve()),
        display="clip.mp4",
        fingerprint="fingerprint",
        path=path.resolve(),
    )


def make_media() -> MediaInfo:
    return MediaInfo(
        duration_s=10,
        width=640,
        height=360,
        fps=30,
        has_audio=True,
        mime_type="video/mp4",
        size_bytes=10_000,
        container="mp4",
        video_codec="h264",
    )


def load_with_quota(tmp_path: Path, media_dir: Path, quota_body: str):
    config = tmp_path / "quota.toml"
    config.write_text(
        f'[paths]\nallowed_dirs = ["{media_dir}"]\ninclude_cwd = false\n{quota_body}',
        encoding="utf-8",
    )
    return load_settings(
        env={
            "VISUAL_EYES_CONFIG": str(config),
            "XDG_CACHE_HOME": str(tmp_path / "quota-cache"),
        }
    )


def call(
    service: AnalysisService,
    source: SourceRef,
    *,
    refresh: bool = False,
    detail: str = "normal",
):
    return service.analyze_video(
        source=source,
        media=make_media(),
        question="What happens?",
        start_seconds=None,
        end_seconds=None,
        detail=detail,
        refresh=refresh,
    )


def test_cached_analysis_bypasses_rpd(make_provider, tmp_path: Path, media_dir: Path) -> None:
    settings = load_with_quota(
        tmp_path, media_dir, f'[limits."{DEFAULT_MODEL}"]\nrpd = 1\n'
    )
    provider = make_provider([ANSWER])
    service = AnalysisService(settings, Cache(settings.data_dir), provider)
    source = make_source(media_dir)

    first = call(service, source)
    second = call(service, source)

    assert first.cache.hit is False
    assert second.cache.hit is True
    assert len(provider.calls) == 1


def test_refresh_respects_rpd(make_provider, tmp_path: Path, media_dir: Path) -> None:
    settings = load_with_quota(
        tmp_path, media_dir, f'[limits."{DEFAULT_MODEL}"]\nrpd = 1\n'
    )
    provider = make_provider([ANSWER])
    service = AnalysisService(settings, Cache(settings.data_dir), provider)
    source = make_source(media_dir)

    call(service, source)

    with pytest.raises(QuotaExhausted, match="daily request limit"):
        call(service, source, refresh=True)
    assert len(provider.calls) == 1


def test_failed_call_releases_the_reservation(
    make_provider, tmp_path: Path, media_dir: Path
) -> None:
    settings = load_with_quota(
        tmp_path, media_dir, f'[limits."{DEFAULT_MODEL}"]\nrpd = 1\n'
    )
    provider = make_provider([ProviderError("nope"), ANSWER])
    cache = Cache(settings.data_dir)
    service = AnalysisService(settings, cache, provider)
    source = make_source(media_dir)

    with pytest.raises(ProviderError):
        call(service, source)

    assert cache.usage_today(settings.models.default)["requests"] == 0

    result = call(service, source)
    assert result.answer == ANSWER["answer"]
    assert len(provider.calls) == 2


def test_tpm_refusal_happens_before_the_provider(
    make_provider, tmp_path: Path, media_dir: Path
) -> None:
    settings = load_with_quota(
        tmp_path, media_dir, f'[limits."{DEFAULT_MODEL}"]\ntpm = 100\n'
    )
    provider = make_provider([ANSWER])
    service = AnalysisService(settings, Cache(settings.data_dir), provider)

    with pytest.raises(QuotaExhausted, match="per-minute token limit"):
        call(service, make_source(media_dir))
    assert provider.calls == []


def test_high_model_uses_its_own_quota(make_provider, tmp_path: Path, media_dir: Path) -> None:
    settings = load_with_quota(
        tmp_path,
        media_dir,
        f'[limits."{DEFAULT_MODEL_HIGH}"]\nrpd = 1\n',
    )
    provider = make_provider([ANSWER, ANSWER])
    service = AnalysisService(settings, Cache(settings.data_dir), provider)
    source = make_source(media_dir)

    high = call(service, source, detail="high")
    assert high.model == DEFAULT_MODEL_HIGH

    with pytest.raises(QuotaExhausted, match="daily request limit"):
        call(service, source, detail="high", refresh=True)

    # The default model has no quota table, so it is not blocked by the high-model budget.
    normal = call(service, source, detail="normal")
    assert normal.model == DEFAULT_MODEL
    assert normal.answer == ANSWER["answer"]
    assert [request.model for request in provider.calls] == [DEFAULT_MODEL_HIGH, DEFAULT_MODEL]


def test_usage_counters_record_actual_tokens(
    make_provider, tmp_path: Path, media_dir: Path
) -> None:
    settings = load_with_quota(
        tmp_path, media_dir, f'[limits."{DEFAULT_MODEL}"]\nrpd = 5\n'
    )
    provider = make_provider([ANSWER])
    cache = Cache(settings.data_dir)
    service = AnalysisService(settings, cache, provider)

    call(service, make_source(media_dir))

    usage = cache.usage_today(settings.models.default)
    assert usage == {"requests": 1, "input_tokens": 1000, "output_tokens": 100}

