"""ffprobe validation and ffmpeg frame extraction.

Both binaries are invoked as argv lists with timeouts. No network protocols are
enabled for local inputs, and stderr is truncated and path-scrubbed before it is
shown to a caller.
"""

from __future__ import annotations

import json
import math
import shutil
import struct
import subprocess
from pathlib import Path
from typing import Any

from .errors import InvalidSource, ProcessingError
from .models import MediaInfo
from .sources import FRAME_EXTENSIONS, mime_for_upload

_FRAME_MIMES = {
    ".mkv": "video/x-matroska",
    ".ts": "video/mp2t",
    ".m2ts": "video/mp2t",
}

# Container families ffprobe may report for each accepted extension. A mismatch
# is how extension spoofing shows up (for example MPEG-TS renamed to .mp4).
_FORMAT_FAMILIES = {
    ".mp4": {"mp4", "mov", "m4a", "3gp", "3g2", "mj2"},
    ".m4v": {"mp4", "mov", "m4a", "3gp", "3g2", "mj2"},
    ".mov": {"mp4", "mov", "m4a", "3gp", "3g2", "mj2"},
    ".3gp": {"mp4", "mov", "m4a", "3gp", "3g2", "mj2"},
    ".3gpp": {"mp4", "mov", "m4a", "3gp", "3g2", "mj2"},
    ".webm": {"matroska", "webm"},
    ".mkv": {"matroska", "webm"},
    ".avi": {"avi"},
    ".flv": {"flv"},
    ".mpeg": {"mpeg", "mpegvideo"},
    ".mpg": {"mpeg", "mpegvideo"},
    ".wmv": {"asf"},
    ".ts": {"mpegts"},
    ".m2ts": {"mpegts"},
}

_MAX_STDERR = 500


def probe(path: Path, *, timeout: float = 20.0) -> MediaInfo:
    """Validate a local file with ffprobe and return its metadata."""

    executable = shutil.which("ffprobe")
    if executable is None:
        raise ProcessingError(
            "ffprobe is not installed or not on PATH",
            hint="install ffmpeg (includes ffprobe), e.g. sudo pacman -S ffmpeg",
        )

    args = [
        executable,
        "-v",
        "error",
        "-protocol_whitelist",
        "file",
        "-show_format",
        "-show_streams",
        "-of",
        "json",
        str(path),
    ]
    try:
        completed = subprocess.run(
            args, capture_output=True, timeout=timeout, check=False, stdin=subprocess.DEVNULL
        )
    except subprocess.TimeoutExpired as exc:
        raise ProcessingError(
            f"ffprobe timed out after {timeout:g}s",
            hint="the file may be corrupt or unusually slow to read",
        ) from exc
    except OSError as exc:
        raise ProcessingError(f"could not run ffprobe: {exc.strerror or exc}") from exc

    if completed.returncode != 0:
        raise InvalidSource(
            "ffprobe could not read this file as video",
            hint=_stderr_tail(completed.stderr, path),
        )

    try:
        payload = json.loads(completed.stdout or b"{}")
    except json.JSONDecodeError as exc:
        raise InvalidSource("ffprobe returned malformed metadata") from exc

    streams = payload.get("streams") or []
    video = _first_video_stream(streams)
    if video is None:
        raise InvalidSource(
            "file has no video stream",
            hint="audio-only and image-only files are not supported",
        )

    fmt = payload.get("format") or {}
    duration = _as_float(fmt.get("duration")) or _as_float(video.get("duration"))
    if duration is None or duration <= 0:
        raise InvalidSource(
            "could not determine video duration",
            hint="the file may be truncated; try remuxing it with ffmpeg",
        )

    width = _as_int(video.get("width"))
    height = _as_int(video.get("height"))
    if not width or not height:
        raise InvalidSource("ffprobe reported no usable video dimensions")

    format_name = str(fmt.get("format_name") or "")
    _check_container_matches_extension(path, format_name)

    extension = path.suffix.lower()
    mime = mime_for_upload(path) or _FRAME_MIMES.get(extension)
    if mime is None:  # pragma: no cover - guarded by sources.FRAME_EXTENSIONS
        raise InvalidSource(f"unsupported video extension {extension!r}")

    try:
        size_bytes = path.stat().st_size
    except OSError as exc:
        raise InvalidSource(f"cannot stat file: {exc.strerror or exc}") from exc

    return MediaInfo(
        duration_s=duration,
        width=width,
        height=height,
        fps=_parse_rate(video.get("avg_frame_rate") or video.get("r_frame_rate")),
        has_audio=any(stream.get("codec_type") == "audio" for stream in streams),
        mime_type=mime,
        size_bytes=size_bytes,
        container=format_name or extension.lstrip("."),
        video_codec=str(video.get("codec_name") or "unknown"),
    )


def extract_frame(
    path: Path,
    timestamp: float,
    width: int,
    destination: Path,
    *,
    timeout: float = 30.0,
) -> tuple[int, int]:
    """Extract one JPEG frame and return its actual pixel dimensions."""

    if not math.isfinite(timestamp) or timestamp < 0:
        raise InvalidSource("frame timestamp must be a non-negative number")
    if not 64 <= width <= 4096:
        raise InvalidSource("frame width is outside the supported range")

    executable = shutil.which("ffmpeg")
    if executable is None:
        raise ProcessingError(
            "ffmpeg is not installed or not on PATH",
            hint="install ffmpeg, e.g. sudo pacman -S ffmpeg",
        )

    args = [
        executable,
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-protocol_whitelist",
        "file",
        "-ss",
        f"{timestamp:.3f}",
        "-i",
        str(path),
        "-frames:v",
        "1",
        "-vf",
        f"scale={int(width)}:-2",
        "-q:v",
        "3",
        "-y",
        str(destination),
    ]
    try:
        completed = subprocess.run(
            args, capture_output=True, timeout=timeout, check=False, stdin=subprocess.DEVNULL
        )
    except subprocess.TimeoutExpired as exc:
        destination.unlink(missing_ok=True)
        raise ProcessingError(
            f"frame extraction timed out after {timeout:g}s",
            hint="try a smaller width or a keyframe-aligned timestamp",
        ) from exc
    except OSError as exc:
        destination.unlink(missing_ok=True)
        raise ProcessingError(f"could not run ffmpeg: {exc.strerror or exc}") from exc

    if completed.returncode != 0 or not destination.exists() or destination.stat().st_size == 0:
        destination.unlink(missing_ok=True)
        raise ProcessingError(
            "ffmpeg could not extract a frame at this timestamp",
            hint=_stderr_tail(completed.stderr, path),
        )

    try:
        return jpeg_dimensions(destination)
    except ValueError as exc:
        destination.unlink(missing_ok=True)
        raise ProcessingError("ffmpeg produced an unreadable JPEG") from exc


def jpeg_dimensions(path: Path) -> tuple[int, int]:
    """Read width/height from a JPEG's start-of-frame marker."""

    data = path.read_bytes()
    if len(data) < 4 or data[:2] != b"\xff\xd8":
        raise ValueError("not a JPEG")

    index = 2
    sof_markers = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
    while index + 4 <= len(data):
        if data[index] != 0xFF:
            index += 1
            continue
        marker = data[index + 1]
        index += 2
        if marker in {0xD8, 0xD9} or 0xD0 <= marker <= 0xD7:
            continue
        if index + 2 > len(data):
            break
        (length,) = struct.unpack(">H", data[index : index + 2])
        if length < 2 or index + length > len(data):
            break
        if marker in sof_markers:
            if length < 7:
                break
            height, width = struct.unpack(">HH", data[index + 3 : index + 7])
            return width, height
        index += length
    raise ValueError("no JPEG start-of-frame marker")


def _first_video_stream(streams: list[dict[str, Any]]) -> dict[str, Any] | None:
    for stream in streams:
        if stream.get("codec_type") != "video":
            continue
        disposition = stream.get("disposition") or {}
        if disposition.get("attached_pic") == 1:
            continue
        return stream
    return None


def _check_container_matches_extension(path: Path, format_name: str) -> None:
    extension = path.suffix.lower()
    expected = _FORMAT_FAMILIES.get(extension)
    if expected is None or extension not in FRAME_EXTENSIONS:
        return
    reported = {part.strip() for part in format_name.split(",") if part.strip()}
    if not reported:
        raise InvalidSource("could not determine the container format")
    if not (reported & expected):
        raise InvalidSource(
            "file content does not match its extension",
            hint=(
                f"{path.suffix} expected one of {sorted(expected)}, "
                f"ffprobe reported {sorted(reported)}; refusing a mismatched upload"
            ),
        )


def _as_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _as_int(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _parse_rate(value: Any) -> float | None:
    if not isinstance(value, str) or "/" not in value:
        return None
    numerator, _, denominator = value.partition("/")
    try:
        result = float(numerator) / float(denominator)
    except (ValueError, ZeroDivisionError):
        return None
    return result if math.isfinite(result) and result > 0 else None


def _stderr_tail(stderr: bytes | None, path: Path) -> str:
    text = (stderr or b"").decode("utf-8", errors="replace").strip()
    if not text:
        return "no details from ffprobe/ffmpeg"
    text = text.replace(str(path), path.name).replace(str(path.parent), "<dir>")
    tail = text[-_MAX_STDERR:]
    return tail if len(text) <= _MAX_STDERR else "…" + tail
