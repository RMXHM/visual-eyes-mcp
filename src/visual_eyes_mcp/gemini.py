"""Gemini adapter: the only module that imports ``google.genai``.

The adapter owns client construction, Files API upload/reuse, interaction calls,
usage extraction, and error translation. It does not decide cache policy or path
policy; callers pass a source that has already been validated.
"""

from __future__ import annotations

import logging
import random
import re
import time
from pathlib import Path
from typing import Any

from google import genai
from google.genai import errors, types

from .errors import ProviderError, QuotaExhausted, TransientError
from .models import ProviderRequest, ProviderResult, Usage

log = logging.getLogger(__name__)

_MAX_MESSAGE = 300
_KEY_RE = re.compile(r"AIza[0-9A-Za-z_\-]{20,}")


class GeminiProvider:
    """Synchronous provider used from MCP worker threads."""

    def __init__(
        self,
        api_key: str,
        *,
        cache: Any,
        store: bool = False,
        upload_timeout: float = 120.0,
        poll_interval: float = 1.0,
        max_transient_retries: int = 2,
        client: Any | None = None,
    ) -> None:
        if max_transient_retries < 0:
            raise ValueError("max_transient_retries must be non-negative")
        self._cache = cache
        self._store = store
        self._upload_timeout = upload_timeout
        self._poll_interval = poll_interval
        self._max_transient_retries = max_transient_retries
        self._client = client or genai.Client(api_key=api_key)

    def analyze(self, request: ProviderRequest) -> ProviderResult:
        video_part = self._video_part(request)
        prompt_part = {"type": "text", "text": request.prompt}
        input_items = [video_part, prompt_part]

        deadline = time.monotonic() + request.timeout_seconds
        attempts = 0
        while True:
            try:
                interaction = self._client.interactions.create(
                    model=request.model,
                    input=input_items,
                    response_format={
                        "type": "text",
                        "mime_type": "application/json",
                        "schema": request.json_schema,
                    },
                    store=request.store,
                    timeout=request.timeout_seconds,
                )
                break
            except Exception as exc:  # translated below; never logged raw
                translated = _translate(exc)
                remaining = deadline - time.monotonic()
                if (
                    isinstance(translated, TransientError)
                    and attempts < self._max_transient_retries
                    and remaining > 0.5
                ):
                    attempts += 1
                    delay = min(2.0**attempts + random.random(), max(0.0, remaining - 0.1))
                    time.sleep(delay)
                    continue
                raise translated from exc

        text = interaction.output_text
        if not text:
            raise ProviderError(
                "the model returned no text output",
                hint="try a narrower question or a different detail level",
            )
        usage = _usage_from(interaction)
        log.info(
            "interaction model=%s input_tokens=%s output_tokens=%s thought_tokens=%s",
            request.model,
            usage.input_tokens,
            usage.output_tokens,
            usage.thought_tokens,
        )
        return ProviderResult(text=text, usage=usage)

    # -- video preparation ------------------------------------------------

    def _video_part(self, request: ProviderRequest) -> dict[str, Any]:
        video = request.video
        if video.kind == "youtube":
            if not video.uri:
                raise ProviderError("YouTube source has no canonical URL")
            return {"type": "video", "uri": video.uri}

        if video.path is None:
            raise ProviderError("local source has no path")
        if not video.mime_type:
            raise ProviderError("local source has no upload mime type")

        uri, mime = self._upload(
            video.path, video.mime_type, request.source_id, request.fingerprint
        )
        part: dict[str, Any] = {"type": "video", "uri": uri, "mime_type": mime}
        processing: dict[str, Any] = {"type": "static"}
        if video.start_seconds is not None:
            processing["start_offset"] = f"{max(0.0, video.start_seconds):.3f}s"
        if video.end_seconds is not None:
            processing["end_offset"] = f"{max(0.0, video.end_seconds):.3f}s"
        if video.fps is not None:
            processing["fps"] = video.fps
        if len(processing) > 1:
            part["processing"] = processing
        return part

    def _upload(
        self, path: Path, mime: str, source_id: str | None, fingerprint: str | None
    ) -> tuple[str, str]:
        if source_id and fingerprint and self._cache is not None:
            cached = self._cache.get_upload(source_id, fingerprint)
            if cached:
                return cached

        try:
            uploaded = self._client.files.upload(
                file=str(path),
                config=types.UploadFileConfig(mime_type=mime, display_name=path.name),
            )
        except Exception as exc:
            raise _translate(exc) from exc

        uploaded = self._wait_active(uploaded)
        uri = uploaded.uri
        if not uri:
            raise ProviderError("uploaded file has no URI")
        result_mime = uploaded.mime_type or mime
        if source_id and fingerprint and self._cache is not None:
            self._cache.put_upload(source_id, fingerprint, uri, result_mime)
        log.info("uploaded local video: %s (%s)", path.name, result_mime)
        return uri, result_mime

    def _wait_active(self, uploaded: Any) -> Any:
        deadline = time.monotonic() + self._upload_timeout
        while True:
            state = _file_state(uploaded)
            if state == "ACTIVE":
                return uploaded
            if state in {"FAILED", "ERROR"}:
                raise ProviderError("the provider could not process the uploaded file")
            if time.monotonic() >= deadline:
                raise TransientError(
                    f"upload did not become ACTIVE within {self._upload_timeout:g}s",
                    hint="retry later; large files can take longer to process",
                )
            time.sleep(self._poll_interval)
            name = uploaded.name
            if not name:
                raise ProviderError("uploaded file has no name to poll")
            try:
                uploaded = self._client.files.get(name=name)
            except Exception as exc:
                raise _translate(exc) from exc


def _file_state(uploaded: Any) -> str:
    state = getattr(uploaded, "state", None)
    if state is None:
        return "UNKNOWN"
    name = getattr(state, "name", None)
    return str(name or state).upper()


def _usage_from(interaction: Any) -> Usage:
    usage = getattr(interaction, "usage", None)
    if usage is None:
        return Usage()
    return Usage(
        input_tokens=getattr(usage, "total_input_tokens", None),
        output_tokens=getattr(usage, "total_output_tokens", None),
        thought_tokens=getattr(usage, "total_thought_tokens", None),
    )


def _translate(exc: Exception) -> Exception:
    """Map an SDK exception to the project taxonomy without leaking a key."""

    if isinstance(exc, errors.APIError):
        code = getattr(exc, "code", None)
        message = _safe_message(str(exc))
        if code == 429:
            lowered = message.lower()
            if any(token in lowered for token in ("per day", "daily", "rpd", "requests per day")):
                return QuotaExhausted(f"provider daily quota reached: {message}")
            return TransientError(f"provider rate limit reached: {message}")
        if isinstance(code, int) and 500 <= code < 600:
            return TransientError(f"provider server error ({code}): {message}")
        if isinstance(code, int) and 400 <= code < 500:
            return ProviderError(f"provider rejected the request ({code}): {message}")
        return ProviderError(f"provider request failed: {message}")

    if isinstance(exc, TimeoutError | ConnectionError):
        return TransientError(f"provider connection failed: {_safe_message(str(exc))}")
    if isinstance(exc, ValueError):
        return ProviderError(f"invalid provider request: {_safe_message(str(exc))}")
    return ProviderError(f"provider call failed: {_safe_message(str(exc))}")


def _safe_message(message: str) -> str:
    text = _KEY_RE.sub("***", message).replace("\n", " ").strip()
    return text if len(text) <= _MAX_MESSAGE else text[: _MAX_MESSAGE - 1] + "…"
