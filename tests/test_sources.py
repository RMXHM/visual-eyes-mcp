from __future__ import annotations

from pathlib import Path

import pytest

from visual_eyes_mcp.config import Settings
from visual_eyes_mcp.errors import InvalidSource
from visual_eyes_mcp.sources import (
    mime_for_upload,
    parse_source,
    upload_supported,
)


@pytest.mark.parametrize(
    ("raw", "video_id"),
    [
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://www.youtube.com/watch/?v=dQw4w9WgXcQ&t=10", "dQw4w9WgXcQ"),
        ("https://youtu.be/dQw4w9WgXcQ?t=10", "dQw4w9WgXcQ"),
        ("https://m.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://www.youtube.com/shorts/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("http://www.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("youtu.be/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("  https://www.youtube.com/watch?v=dQw4w9WgXcQ  ", "dQw4w9WgXcQ"),
    ],
)
def test_youtube_forms_are_canonicalised(settings: Settings, raw: str, video_id: str) -> None:
    ref = parse_source(raw, settings)

    assert ref.kind == "youtube"
    assert ref.canonical_id == video_id
    assert ref.url == f"https://www.youtube.com/watch?v={video_id}"
    assert ref.path is None
    assert len(ref.fingerprint) == 64


@pytest.mark.parametrize(
    "raw",
    [
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PL123",
        "https://youtu.be/dQw4w9WgXcQ?list=PL123",
        "https://www.youtube.com/live/dQw4w9WgXcQ",
        "https://www.youtube.com/embed/dQw4w9WgXcQ",
        "https://www.youtube.com/",
        "https://www.youtube.com/watch?v=too-short",
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ&v=dQw4w9WgXcQ",
        "https://www.youtube.com/watch?x=dQw4w9WgXcQ",
        "https://youtu.be/",
        "https://youtu.be/dQw4w9WgXcQ/extra",
        "https://vimeo.com/123",
        "ftp://youtube.com/watch?v=dQw4w9WgXcQ",
        "https://user@youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtube.com:443/watch?v=dQw4w9WgXcQ",
    ],
)
def test_unsupported_youtube_forms_are_rejected(settings: Settings, raw: str) -> None:
    with pytest.raises(InvalidSource):
        parse_source(raw, settings)


def test_youtube_can_be_disabled(make_settings, media_dir: Path) -> None:
    settings = make_settings(
        VISUAL_EYES_ALLOWED_DIRS=str(media_dir),
        VISUAL_EYES_YOUTUBE="false",
    )

    with pytest.raises(InvalidSource, match="disabled"):
        parse_source("https://youtu.be/dQw4w9WgXcQ", settings)


def test_disabling_youtube_does_not_break_local_files(make_settings, media_dir: Path) -> None:
    settings = make_settings(
        VISUAL_EYES_ALLOWED_DIRS=str(media_dir),
        VISUAL_EYES_YOUTUBE="false",
    )
    path = media_dir / "clip.mp4"
    path.write_bytes(b"not really a video")

    ref = parse_source(str(path), settings)

    assert ref.kind == "local"
    assert ref.path == path.resolve()


def test_empty_source_is_rejected(settings: Settings) -> None:
    with pytest.raises(InvalidSource, match="must not be empty"):
        parse_source("   ", settings)


def test_overlong_source_is_rejected(settings: Settings) -> None:
    with pytest.raises(InvalidSource, match="unreasonably long"):
        parse_source("x" * 5000, settings)


def test_upload_support_map(media_dir: Path) -> None:
    assert mime_for_upload(Path("a.mp4")) == "video/mp4"
    assert mime_for_upload(Path("a.MOV")) == "video/mov"
    assert mime_for_upload(Path("a.mkv")) is None
    assert upload_supported(Path("a.webm")) is True
    assert upload_supported(Path("a.mkv")) is False
    assert upload_supported(Path("a.ts")) is False
