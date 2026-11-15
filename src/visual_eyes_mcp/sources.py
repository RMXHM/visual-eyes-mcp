"""Source parsing and the local file-access policy.

This module is the only place that decides whether a local path may be opened.
The rule is containment in an explicitly configured root after full resolution,
followed by regular-file and extension checks. No path is trusted because it is
under HOME.
"""

from __future__ import annotations

import hashlib
import re
import stat
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .config import Settings
from .errors import AccessDenied, InvalidSource
from .models import SourceRef

YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}

_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")
_BARE_YOUTUBE_RE = re.compile(r"^(?:www\.|m\.)?youtube\.com(?:/|$)|^youtu\.be(?:/|$)")

# Extensions ffmpeg can decode for local frame extraction. Upload support is a
# narrower subset: Gemini does not accept Matroska or MPEG-TS.
FRAME_EXTENSIONS = frozenset(
    {
        ".mp4", ".m4v", ".mov", ".avi", ".webm", ".mkv", ".mpeg", ".mpg",
        ".wmv", ".flv", ".3gp", ".3gpp", ".ts", ".m2ts",
    }
)

UPLOAD_MIME_BY_EXT = {
    ".mp4": "video/mp4",
    ".m4v": "video/mp4",
    ".mpeg": "video/mpeg",
    ".mpg": "video/mpg",
    ".mov": "video/mov",
    ".avi": "video/avi",
    ".webm": "video/webm",
    ".wmv": "video/wmv",
    ".flv": "video/x-flv",
    ".3gp": "video/3gpp",
    ".3gpp": "video/3gpp",
}

UPLOAD_MIMES = frozenset(UPLOAD_MIME_BY_EXT.values())


def parse_source(raw: str, settings: Settings, *, cwd: Path | None = None) -> SourceRef:
    """Parse a user-supplied source into a canonical reference, enforcing policy."""

    text = raw.strip()
    if not text:
        raise InvalidSource("source must not be empty")
    if len(text) > 4096:
        raise InvalidSource("source is unreasonably long")

    if _SCHEME_RE.match(text):
        scheme = text.split(":", 1)[0].lower()
        if scheme not in {"http", "https"}:
            raise InvalidSource(
                f"unsupported URL scheme {scheme!r}",
                hint="use a local file path or a public YouTube URL",
            )
        return _parse_youtube(text, settings)

    if _BARE_YOUTUBE_RE.match(text):
        return _parse_youtube(f"https://{text}", settings)

    return _parse_local(text, settings, cwd=cwd)


def _parse_youtube(raw: str, settings: Settings) -> SourceRef:
    if not settings.features.youtube:
        raise InvalidSource(
            "YouTube support is disabled in this configuration",
            hint="set features.youtube = true (or unset VISUAL_EYES_YOUTUBE) to enable it",
        )

    try:
        parts = urlsplit(raw)
    except ValueError as exc:
        raise InvalidSource("malformed YouTube URL") from exc

    host = (parts.hostname or "").lower()
    if host not in YOUTUBE_HOSTS:
        raise InvalidSource(
            "only public YouTube URLs and local files are supported",
            hint="supported hosts: youtube.com, www.youtube.com, m.youtube.com, youtu.be",
        )
    if parts.username or parts.password:
        raise InvalidSource("YouTube URLs must not contain user information")
    try:
        port = parts.port
    except ValueError as exc:  # pragma: no cover - urlsplit raises on bad ports
        raise InvalidSource("malformed port in YouTube URL") from exc
    if port is not None:
        raise InvalidSource("YouTube URLs must not contain a port")

    query = parse_qs(parts.query, keep_blank_values=True)
    if "list" in query:
        raise InvalidSource(
            "YouTube playlists are not supported",
            hint="pass a single video URL, not a playlist",
        )

    path = parts.path or "/"
    video_id: str | None
    if host == "youtu.be":
        segments = [segment for segment in path.split("/") if segment]
        if len(segments) != 1:
            raise InvalidSource("unsupported youtu.be URL form")
        video_id = segments[0]
    elif path.rstrip("/") == "/watch":
        values = query.get("v", [])
        if len(values) != 1 or not values[0]:
            raise InvalidSource("YouTube watch URL is missing a single 'v' parameter")
        video_id = values[0]
    elif path.startswith("/shorts/"):
        video_id = path[len("/shorts/") :].strip("/")
        if "/" in video_id:
            raise InvalidSource("unsupported YouTube Shorts URL form")
    elif path.startswith("/live/"):
        raise InvalidSource(
            "YouTube live streams are not supported",
            hint="use a recorded video instead",
        )
    else:
        raise InvalidSource(
            "unsupported YouTube URL form",
            hint="use https://www.youtube.com/watch?v=<id> or https://youtu.be/<id>",
        )

    if not video_id or not _VIDEO_ID_RE.fullmatch(video_id):
        raise InvalidSource("not a valid 11-character YouTube video ID")

    return SourceRef(
        kind="youtube",
        canonical_id=video_id,
        display=f"https://www.youtube.com/watch?v={video_id}",
        fingerprint=hashlib.sha256(f"youtube:{video_id}".encode()).hexdigest(),
        url=f"https://www.youtube.com/watch?v={video_id}",
    )


def _parse_local(raw: str, settings: Settings, *, cwd: Path | None = None) -> SourceRef:
    base = cwd or Path.cwd()
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = base / candidate

    try:
        resolved = candidate.resolve(strict=True)
    except FileNotFoundError as exc:
        raise InvalidSource("file not found", hint=str(candidate)) from exc
    except OSError as exc:
        raise InvalidSource(f"cannot resolve path: {exc.strerror or exc}") from exc

    roots = settings.allowed_roots(cwd=base)
    if not roots:
        raise AccessDenied(
            "no allowed directories are configured",
            hint=(
                'add paths.allowed_dirs = ["/path/to/videos"] to config.toml, '
                "or set VISUAL_EYES_ALLOWED_DIRS"
            ),
        )

    if not any(is_within(resolved, root) for root in roots):
        raise AccessDenied(
            f"{resolved} is outside the configured allowed directories",
            hint="add its parent to paths.allowed_dirs if this access is intentional",
        )

    try:
        info = resolved.stat()
    except OSError as exc:
        raise InvalidSource(f"cannot stat file: {exc.strerror or exc}") from exc

    if not stat.S_ISREG(info.st_mode):
        raise InvalidSource("source is not a regular file")
    if info.st_size == 0:
        raise InvalidSource("source file is empty")

    extension = resolved.suffix.lower()
    if extension not in FRAME_EXTENSIONS:
        raise InvalidSource(
            f"unsupported video extension {extension or '<none>'!r}",
            hint="supported: " + ", ".join(sorted(FRAME_EXTENSIONS)),
        )

    return SourceRef(
        kind="local",
        canonical_id=str(resolved),
        display=resolved.name,
        fingerprint=hashlib.sha256(
            f"{resolved}\0{info.st_size}\0{info.st_mtime_ns}".encode()
        ).hexdigest(),
        path=resolved,
    )


def is_within(path: Path, root: Path) -> bool:
    """True when ``path`` is ``root`` or below it (both already resolved)."""

    return path == root or root in path.parents


def mime_for_upload(path: Path) -> str | None:
    return UPLOAD_MIME_BY_EXT.get(path.suffix.lower())


def upload_supported(path: Path) -> bool:
    return mime_for_upload(path) is not None
