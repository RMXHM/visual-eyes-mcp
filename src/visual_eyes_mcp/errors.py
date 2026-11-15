"""Error taxonomy.

Every anticipated failure maps to one of these classes so the calling agent can
decide what to do next (fix configuration, narrow a range, wait until tomorrow,
or retry). Messages are safe to show to a model: they contain no secrets and no
raw provider bodies.
"""

from __future__ import annotations

from typing import Any


class VisualEyesError(Exception):
    """Base class for anticipated failures."""

    code = "error"
    retryable = False

    def __init__(self, message: str, *, hint: str | None = None) -> None:
        self.message = message
        self.hint = hint
        text = message if not hint else f"{message} Hint: {hint}"
        super().__init__(text)

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"error": self.code, "message": self.message}
        if self.hint:
            data["hint"] = self.hint
        return data


class ConfigError(VisualEyesError):
    """Configuration is missing or invalid."""

    code = "config_error"


class AccessDenied(VisualEyesError):
    """A path is outside the configured allowed roots."""

    code = "access_denied"


class InvalidSource(VisualEyesError):
    """The source is not a supported video or URL."""

    code = "invalid_source"


class LimitExceeded(VisualEyesError):
    """A configured size, duration, token, or frame limit was exceeded."""

    code = "limit_exceeded"


class QuotaExhausted(VisualEyesError):
    """The locally configured quota or concurrency budget is exhausted."""

    code = "quota_exhausted"


class ProviderError(VisualEyesError):
    """The model provider rejected the request or returned unusable output."""

    code = "provider_error"


class TransientError(VisualEyesError):
    """A retryable provider/network failure."""

    code = "transient_error"
    retryable = True


class ProcessingError(VisualEyesError):
    """ffmpeg, ffprobe, or local media processing failed."""

    code = "processing_error"
