from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from visual_eyes_mcp.errors import InvalidSource, ProcessingError
from visual_eyes_mcp.media import extract_frame, jpeg_dimensions, probe

pytestmark = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
    reason="ffmpeg/ffprobe not installed",
)


def test_probe_reports_ffprobe_facts(clip: Path) -> None:
    info = probe(clip)

    assert info.duration_s == pytest.approx(2.0, abs=0.2)
    assert (info.width, info.height) == (160, 120)
    assert info.fps == pytest.approx(10.0, abs=0.1)
    assert info.has_audio is True
    assert info.mime_type == "video/mp4"
    assert info.size_bytes == clip.stat().st_size
    assert "mp4" in info.container
    assert info.video_codec


def test_probe_accepts_mkv_for_local_use(make_video) -> None:
    path = make_video(name="clip.mkv")

    info = probe(path)

    assert info.mime_type == "video/x-matroska"
    assert info.duration_s > 0


def test_audio_only_file_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "audio.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=1",
            "-c:a",
            "aac",
            str(path),
            "-y",
        ],
        check=True,
        capture_output=True,
    )

    with pytest.raises(InvalidSource, match="no video stream"):
        probe(path)


def test_extension_spoofing_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "spoof.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=160x120:rate=10:duration=1",
            "-c:v",
            "mpeg2video",
            "-f",
            "mpegts",
            str(path),
            "-y",
        ],
        check=True,
        capture_output=True,
    )

    with pytest.raises(InvalidSource, match="does not match its extension"):
        probe(path)


def test_extract_frame_returns_real_dimensions(clip: Path, tmp_path: Path) -> None:
    destination = tmp_path / "frame.jpg"

    width, height = extract_frame(clip, 0.5, 320, destination)

    assert (width, height) == (320, 240)
    assert destination.read_bytes()[:2] == b"\xff\xd8"
    assert jpeg_dimensions(destination) == (320, 240)


def test_extract_frame_rejects_invalid_timestamp(clip: Path, tmp_path: Path) -> None:
    with pytest.raises(InvalidSource, match="non-negative"):
        extract_frame(clip, -1, 320, tmp_path / "frame.jpg")


def test_extract_frame_rejects_invalid_width(clip: Path, tmp_path: Path) -> None:
    with pytest.raises(InvalidSource, match="width"):
        extract_frame(clip, 0.5, 10, tmp_path / "frame.jpg")


def test_probe_timeout_is_a_processing_error(monkeypatch: pytest.MonkeyPatch, clip: Path) -> None:
    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0][0], kwargs.get("timeout", 1))

    monkeypatch.setattr("visual_eyes_mcp.media.subprocess.run", fake_run)

    with pytest.raises(ProcessingError, match="timed out"):
        probe(clip)


def test_extract_timeout_is_a_processing_error(
    monkeypatch: pytest.MonkeyPatch, clip: Path, tmp_path: Path
) -> None:
    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0][0], kwargs.get("timeout", 1))

    monkeypatch.setattr("visual_eyes_mcp.media.subprocess.run", fake_run)

    destination = tmp_path / "frame.jpg"
    with pytest.raises(ProcessingError, match="timed out"):
        extract_frame(clip, 0.5, 320, destination)
    assert not destination.exists()


def test_missing_ffprobe_is_a_processing_error(monkeypatch: pytest.MonkeyPatch, clip: Path) -> None:
    monkeypatch.setattr("visual_eyes_mcp.media.shutil.which", lambda _name: None)

    with pytest.raises(ProcessingError, match="ffprobe is not installed"):
        probe(clip)


def test_missing_ffmpeg_is_a_processing_error(
    monkeypatch: pytest.MonkeyPatch, clip: Path, tmp_path: Path
) -> None:
    monkeypatch.setattr("visual_eyes_mcp.media.shutil.which", lambda _name: None)

    with pytest.raises(ProcessingError, match="ffmpeg is not installed"):
        extract_frame(clip, 0.5, 320, tmp_path / "frame.jpg")


def test_jpeg_dimensions_rejects_non_jpeg(tmp_path: Path) -> None:
    path = tmp_path / "fake.jpg"
    path.write_bytes(b"not a jpeg")

    with pytest.raises(ValueError):
        jpeg_dimensions(path)
