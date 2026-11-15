from __future__ import annotations

import os
from pathlib import Path

import pytest

from visual_eyes_mcp.config import Settings
from visual_eyes_mcp.errors import AccessDenied, InvalidSource
from visual_eyes_mcp.sources import parse_source


def test_file_inside_allowed_root_is_accepted(settings: Settings, media_dir: Path) -> None:
    path = media_dir / "clip.mp4"
    path.write_bytes(b"video-bytes")

    ref = parse_source(str(path), settings)

    assert ref.kind == "local"
    assert ref.path == path.resolve()
    assert ref.canonical_id == str(path.resolve())
    assert ref.display == "clip.mp4"


def test_relative_path_resolves_against_explicit_cwd(settings: Settings, media_dir: Path) -> None:
    path = media_dir / "clip.mp4"
    path.write_bytes(b"video-bytes")

    ref = parse_source("clip.mp4", settings, cwd=media_dir)

    assert ref.path == path.resolve()


def test_parent_traversal_inside_root_still_resolves(settings: Settings, media_dir: Path) -> None:
    subdir = media_dir / "sub"
    subdir.mkdir()
    path = media_dir / "clip.mp4"
    path.write_bytes(b"video-bytes")

    ref = parse_source(str(subdir / ".." / "clip.mp4"), settings)

    assert ref.path == path.resolve()


def test_file_outside_allowed_root_is_denied(settings: Settings, tmp_path: Path) -> None:
    outside = tmp_path / "outside.mp4"
    outside.write_bytes(b"video-bytes")

    with pytest.raises(AccessDenied, match="outside the configured allowed directories"):
        parse_source(str(outside), settings)


def test_symlink_escape_is_denied(settings: Settings, media_dir: Path, tmp_path: Path) -> None:
    outside = tmp_path / "private.mp4"
    outside.write_bytes(b"video-bytes")
    link = media_dir / "innocent.mp4"
    link.symlink_to(outside)

    with pytest.raises(AccessDenied, match="outside the configured allowed directories"):
        parse_source(str(link), settings)


def test_symlink_inside_root_is_accepted(settings: Settings, media_dir: Path) -> None:
    target = media_dir / "real.mp4"
    target.write_bytes(b"video-bytes")
    link = media_dir / "alias.mp4"
    link.symlink_to(target)

    ref = parse_source(str(link), settings)

    assert ref.path == target.resolve()


def test_no_allowed_roots_fails_closed(make_settings, tmp_path: Path) -> None:
    settings = make_settings()
    path = tmp_path / "outside.mp4"
    path.write_bytes(b"video-bytes")

    with pytest.raises(AccessDenied, match="no allowed directories"):
        parse_source(str(path), settings)


def test_directory_is_not_a_video(settings: Settings, media_dir: Path) -> None:
    directory = media_dir / "folder.mp4"
    directory.mkdir()

    with pytest.raises(InvalidSource, match="not a regular file"):
        parse_source(str(directory), settings)


def test_fifo_is_not_a_video(settings: Settings, media_dir: Path) -> None:
    fifo = media_dir / "pipe.mp4"
    os.mkfifo(fifo)

    with pytest.raises(InvalidSource, match="not a regular file"):
        parse_source(str(fifo), settings)


def test_unsupported_extension_is_rejected(settings: Settings, media_dir: Path) -> None:
    path = media_dir / "notes.txt"
    path.write_bytes(b"hello")

    with pytest.raises(InvalidSource, match="unsupported video extension"):
        parse_source(str(path), settings)


def test_empty_file_is_rejected(settings: Settings, media_dir: Path) -> None:
    path = media_dir / "empty.mp4"
    path.write_bytes(b"")

    with pytest.raises(InvalidSource, match="empty"):
        parse_source(str(path), settings)


def test_missing_file_is_rejected(settings: Settings, media_dir: Path) -> None:
    with pytest.raises(InvalidSource, match="file not found"):
        parse_source(str(media_dir / "nope.mp4"), settings)


def test_mkv_is_allowed_locally(settings: Settings, media_dir: Path) -> None:
    path = media_dir / "clip.mkv"
    path.write_bytes(b"matroska-bytes")

    ref = parse_source(str(path), settings)

    assert ref.kind == "local"
    assert ref.path == path.resolve()


def test_fingerprint_changes_with_mtime(settings: Settings, media_dir: Path) -> None:
    path = media_dir / "clip.mp4"
    path.write_bytes(b"video-bytes")
    first = parse_source(str(path), settings).fingerprint

    os.utime(path, (1_700_000_000, 1_700_000_000))
    second = parse_source(str(path), settings).fingerprint

    assert first != second
