from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from visual_eyes_mcp.cache import Cache
from visual_eyes_mcp.config import Settings, load_settings
from visual_eyes_mcp.models import ProviderRequest, ProviderResult, Usage
from visual_eyes_mcp.tools import App

FFMPEG = shutil.which("ffmpeg") and shutil.which("ffprobe")
requires_ffmpeg = pytest.mark.skipif(not FFMPEG, reason="ffmpeg/ffprobe not installed")


class FakeProvider:
    """Deterministic provider used instead of Gemini in tests."""

    def __init__(self, replies: list[Any] | None = None) -> None:
        self.replies = list(replies or [])
        self.calls: list[ProviderRequest] = []
        self.default_reply: dict[str, Any] = {
            "answer": "A synthetic answer.",
            "key_points": ["a point"],
            "timestamps": [],
            "confidence": "medium",
        }

    def analyze(self, request: ProviderRequest) -> ProviderResult:
        self.calls.append(request)
        reply = self.replies.pop(0) if self.replies else self.default_reply
        if isinstance(reply, Exception):
            raise reply
        text = reply if isinstance(reply, str) else json.dumps(reply)
        return ProviderResult(
            text=text,
            usage=Usage(input_tokens=1000, output_tokens=100, thought_tokens=0),
        )


@pytest.fixture
def media_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "videos"
    directory.mkdir()
    return directory


@pytest.fixture
def make_video(media_dir: Path):
    def _make(
        name: str = "clip.mp4",
        *,
        duration: float = 2.0,
        size: str = "160x120",
        rate: int = 10,
        audio: bool = True,
    ) -> Path:
        path = media_dir / name
        command = [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"testsrc=size={size}:rate={rate}:duration={duration}",
        ]
        if audio:
            command += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}"]
        command += ["-c:v", "libx264", "-pix_fmt", "yuv420p"]
        if audio:
            command += ["-c:a", "aac", "-shortest"]
        command += [str(path), "-y"]
        try:
            subprocess.run(command, check=True, capture_output=True)
        except subprocess.CalledProcessError:
            command[command.index("libx264")] = "mpeg4"
            subprocess.run(command, check=True, capture_output=True)
        return path

    return _make


@pytest.fixture
def clip(make_video) -> Path:
    if not FFMPEG:
        pytest.skip("ffmpeg/ffprobe not installed")
    return make_video()


@pytest.fixture
def make_settings(tmp_path: Path):
    def _make(**env: object) -> Settings:
        base: dict[str, str] = {
            "VISUAL_EYES_INCLUDE_CWD": "false",
            "XDG_CONFIG_HOME": str(tmp_path / "config"),
            "XDG_CACHE_HOME": str(tmp_path / "cache"),
        }
        base.update({key: str(value) for key, value in env.items()})
        return load_settings(env=base)

    return _make


@pytest.fixture
def settings(make_settings, media_dir: Path) -> Settings:
    return make_settings(VISUAL_EYES_ALLOWED_DIRS=str(media_dir))


@pytest.fixture
def cache(settings: Settings) -> Cache:
    return Cache(
        settings.data_dir,
        ttl_seconds=settings.cache.ttl_seconds,
        max_mb=settings.cache.max_mb,
    )


@pytest.fixture
def fake_provider() -> FakeProvider:
    return FakeProvider()


@pytest.fixture
def make_provider():
    return FakeProvider


@pytest.fixture
def app(settings: Settings, cache: Cache, fake_provider: FakeProvider) -> App:
    return App(settings=settings, cache=cache, provider=fake_provider)
