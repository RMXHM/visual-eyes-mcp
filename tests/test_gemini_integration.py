"""Opt-in Gemini integration test.

This test makes **real, billable API calls** and is skipped unless both gates pass:

1. A non-blank ``GEMINI_API_KEY`` in the process environment or the project-root
   ``.env`` file (copy ``.env.example`` to ``.env``; existing environment values
   win).
2. ``VISUAL_EYES_RUN_INTEGRATION=1`` in the process environment. This flag is
   read before ``.env`` is loaded, so a local ``.env`` cannot turn an ordinary
   test run into a paid call.

The key is never printed, logged, or included in assertions. The project-root
``.env`` is ignored by Git. Expected spend: at most two generation interactions
against a one-second local test video, using ``detail="low"``. The initial call
may be followed by one structured-output re-ask, and provider-level transient
retries are disabled for this test. Token usage is printed without any
credential material; no dollar estimate is made.

Run it yourself with:

    VISUAL_EYES_RUN_INTEGRATION=1 uv run pytest \
        tests/test_gemini_integration.py -m integration -q -s

Optional: ``VISUAL_EYES_INTEGRATION_MODEL`` overrides the model. Choose a model
your project/key can actually access; the default tier in this repository is not
guaranteed to exist for every account.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from visual_eyes_mcp.analysis import AnalysisService
from visual_eyes_mcp.cache import Cache
from visual_eyes_mcp.config import load_project_env, load_settings
from visual_eyes_mcp.gemini import GeminiProvider
from visual_eyes_mcp.media import probe
from visual_eyes_mcp.sources import parse_source

_RUN = os.environ.get("VISUAL_EYES_RUN_INTEGRATION") == "1"


def _has_key() -> bool:
    """Load .env only for an opted-in run, then check the key gate."""

    if not _RUN:
        return False
    load_project_env()
    return bool(os.environ.get("GEMINI_API_KEY", "").strip())


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not _RUN,
        reason="set VISUAL_EYES_RUN_INTEGRATION=1 to allow a real API call",
    ),
    pytest.mark.skipif(
        not _has_key(),
        reason="GEMINI_API_KEY is not set in the environment or project .env",
    ),
    pytest.mark.skipif(
        not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
        reason="ffmpeg/ffprobe not installed",
    ),
]


def _make_video(directory: Path) -> Path:
    path = directory / "integration.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=160x120:rate=5:duration=1",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(path),
            "-y",
        ],
        check=True,
        capture_output=True,
    )
    return path


def test_real_gemini_analysis(tmp_path: Path) -> None:
    videos = tmp_path / "videos"
    videos.mkdir()
    video = _make_video(videos)

    env = {
        "GEMINI_API_KEY": os.environ["GEMINI_API_KEY"],
        "VISUAL_EYES_ALLOWED_DIRS": str(videos),
        "VISUAL_EYES_INCLUDE_CWD": "false",
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
        "XDG_CACHE_HOME": str(tmp_path / "cache"),
    }
    model = os.environ.get("VISUAL_EYES_INTEGRATION_MODEL")
    if model:
        env["VISUAL_EYES_MODEL"] = model

    settings = load_settings(env=env)
    cache = Cache(settings.data_dir)
    provider = GeminiProvider(
        settings.api_key.get_secret_value(),  # type: ignore[union-attr]
        cache=cache,
        store=False,
        upload_timeout=120.0,
        poll_interval=1.0,
        max_transient_retries=0,
    )
    source = parse_source(str(video), settings)
    media = probe(video)

    service = AnalysisService(settings, cache, provider)
    result = service.analyze_video(
        source=source,
        media=media,
        question="What colours appear in the test pattern? Answer in one short sentence.",
        start_seconds=None,
        end_seconds=None,
        detail="low",
        refresh=True,
    )

    print(
        "integration usage: "
        f"model={result.model} "
        f"input_tokens={result.usage.input_tokens} "
        f"output_tokens={result.usage.output_tokens} "
        f"thought_tokens={result.usage.thought_tokens}",
        flush=True,
    )
    assert result.answer.strip()
    assert result.model == settings.models.default
    assert result.usage.input_tokens is not None and result.usage.input_tokens > 0
    assert result.cache.hit is False
