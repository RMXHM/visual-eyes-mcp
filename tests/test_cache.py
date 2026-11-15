from __future__ import annotations

import stat
from datetime import UTC, datetime
from pathlib import Path

import pytest

from visual_eyes_mcp.cache import Cache, pacific_date
from visual_eyes_mcp.config import Quota
from visual_eyes_mcp.errors import QuotaExhausted
from visual_eyes_mcp.models import Usage


def test_cache_permissions(settings) -> None:
    cache = Cache(settings.data_dir)

    assert stat.S_IMODE(cache.data_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(cache.frames_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(cache.db_path.stat().st_mode) == 0o600


def test_analysis_put_get_and_expiry(cache: Cache) -> None:
    cache.put_analysis(
        cache_key="key",
        source_id="source",
        tool="analyze_video",
        request={"question": "why"},
        result={"answer": "because"},
        model="model",
        detail="normal",
        usage=Usage(input_tokens=10, output_tokens=2),
    )

    assert cache.get_analysis("key") == {"answer": "because"}
    assert cache.cached_analyses("source") == 1

    with cache._connect() as connection:
        connection.execute("UPDATE analyses SET expires_at=0 WHERE cache_key='key'")

    assert cache.get_analysis("key") is None
    assert cache.cached_analyses("source") == 0


def test_analysis_hit_count(cache: Cache) -> None:
    cache.put_analysis(
        cache_key="key",
        source_id="source",
        tool="analyze_video",
        request={},
        result={"answer": "x"},
        model="model",
        detail="normal",
        usage=Usage(),
    )
    cache.get_analysis("key")
    cache.get_analysis("key")

    with cache._connect() as connection:
        row = connection.execute(
            "SELECT hit_count FROM analyses WHERE cache_key='key'"
        ).fetchone()
    assert row["hit_count"] == 2


def test_upload_reuse_and_invalidation(cache: Cache) -> None:
    cache.put_upload("source", "fingerprint", "https://files/1", "video/mp4")

    assert cache.get_upload("source", "fingerprint") == ("https://files/1", "video/mp4")
    assert cache.get_upload("source", "different") is None

    with cache._connect() as connection:
        connection.execute("UPDATE uploads SET expires_at=0")

    assert cache.get_upload("source", "fingerprint") is None


def test_frame_store_lookup_and_content_dedupe(cache: Cache, tmp_path: Path) -> None:
    first = tmp_path / "first.jpg"
    first.write_bytes(b"\xff\xd8fake-jpeg-one")

    path, hit = cache.store_frame(
        source_id="source",
        timestamp=1.25,
        width=320,
        jpeg_path=first,
        frame_width=320,
        frame_height=240,
    )
    assert hit is False
    assert path.exists()

    cached = cache.get_frame("source", 1250, 320)
    assert cached == (path, 320, 240)

    second = tmp_path / "second.jpg"
    second.write_bytes(b"\xff\xd8fake-jpeg-one")
    other_timestamp, hit = cache.store_frame(
        source_id="source",
        timestamp=2.0,
        width=320,
        jpeg_path=second,
        frame_width=320,
        frame_height=240,
    )
    assert hit is True
    assert other_timestamp == path
    assert not second.exists()


def test_missing_frame_file_is_evicted(cache: Cache, tmp_path: Path) -> None:
    jpeg = tmp_path / "frame.jpg"
    jpeg.write_bytes(b"\xff\xd8fake")
    path, _ = cache.store_frame(
        source_id="source",
        timestamp=1.0,
        width=320,
        jpeg_path=jpeg,
        frame_width=320,
        frame_height=240,
    )
    path.unlink()

    assert cache.get_frame("source", 1000, 320) is None


def test_frame_pruning_keeps_cache_under_limit(tmp_path: Path) -> None:
    cache = Cache(tmp_path / "small", max_mb=1)
    for index in range(2):
        jpeg = tmp_path / f"frame-{index}.jpg"
        jpeg.write_bytes(b"x" * 700_000 + bytes([index]))
        cache.store_frame(
            source_id="source",
            timestamp=float(index),
            width=320,
            jpeg_path=jpeg,
            frame_width=320,
            frame_height=240,
        )

    files = list(cache.frames_dir.glob("*.jpg"))
    assert len(files) == 1
    total = sum(path.stat().st_size for path in files)
    assert total <= 1024 * 1024


def test_usage_today_and_pacific_day(cache: Cache) -> None:
    assert pacific_date(datetime(2026, 1, 1, 7, 0, tzinfo=UTC)) == "2025-12-31"
    assert pacific_date(datetime(2026, 1, 1, 12, 0, tzinfo=UTC)) == "2026-01-01"
    assert pacific_date(datetime(2026, 7, 1, 7, 0, tzinfo=UTC)) == "2026-07-01"

    assert cache.usage_today("model") == {
        "requests": 0,
        "input_tokens": 0,
        "output_tokens": 0,
    }


def test_quota_reservation_settle_and_release(cache: Cache) -> None:
    quota = Quota(rpd=2)
    first = cache.reserve_quota("model", quota, 100)

    assert cache.usage_today("model")["requests"] == 1
    assert cache.window_usage("model") == (1, 100)

    cache.settle_quota(first, "model", Usage(input_tokens=50, output_tokens=10))

    usage = cache.usage_today("model")
    assert usage["requests"] == 1
    assert usage["input_tokens"] == 50
    assert usage["output_tokens"] == 10
    assert cache.window_usage("model") == (1, 60)

    second = cache.reserve_quota("model", quota, 100)
    with pytest.raises(QuotaExhausted, match="daily request limit"):
        cache.reserve_quota("model", quota, 100)

    cache.release_quota(second)
    assert cache.usage_today("model")["requests"] == 1
    assert cache.reserve_quota("model", quota, 100) is not None


def test_quota_rpm_window(cache: Cache) -> None:
    quota = Quota(rpm=1)
    first = cache.reserve_quota("model", quota, 10)

    with pytest.raises(QuotaExhausted, match="per-minute request limit"):
        cache.reserve_quota("model", quota, 10)

    cache.release_quota(first)
    assert cache.reserve_quota("model", quota, 10) is not None


def test_quota_tpm_window(cache: Cache) -> None:
    quota = Quota(tpm=1000)
    first = cache.reserve_quota("model", quota, 900)

    with pytest.raises(QuotaExhausted, match="per-minute token limit"):
        cache.reserve_quota("model", quota, 200)

    cache.release_quota(first)
    assert cache.reserve_quota("model", quota, 900) is not None

    with pytest.raises(QuotaExhausted, match="per-minute token limit"):
        cache.reserve_quota("model", quota, 1200)


def test_no_quota_means_no_refusal(cache: Cache) -> None:
    for _ in range(3):
        assert cache.reserve_quota("model", None, 10_000_000) is not None
