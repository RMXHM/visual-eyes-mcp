from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from visual_eyes_mcp.cache import Cache
from visual_eyes_mcp.config import Settings
from visual_eyes_mcp.models import MediaInfo, ProviderRequest, ProviderResult, SourceRef, Usage
from visual_eyes_mcp.tools import App, analyze_video

ANSWER = {
    "answer": "A synthetic answer.",
    "key_points": [],
    "timestamps": [],
    "confidence": "medium",
}


class TrackingSemaphore(threading.BoundedSemaphore):
    """Bounded semaphore that records when a second caller is blocked."""

    def __init__(self, value: int = 1) -> None:
        super().__init__(value)
        self._attempt_lock = threading.Lock()
        self._attempts = 0
        self.second_attempted = threading.Event()

    def acquire(self, blocking: bool = True, timeout: float | None = None) -> bool:
        with self._attempt_lock:
            self._attempts += 1
            if self._attempts >= 2:
                self.second_attempted.set()
        return super().acquire(blocking, timeout)

    def __enter__(self) -> None:
        self.acquire()


class BlockingProvider:
    """Provider that holds its first call open so overlap is observable."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.first_started = threading.Event()
        self.second_started = threading.Event()
        self.release = threading.Event()
        self.calls = 0
        self.active = 0
        self.max_active = 0

    def analyze(self, request: ProviderRequest) -> ProviderResult:
        with self.lock:
            self.calls += 1
            call_number = self.calls
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        if call_number == 1:
            self.first_started.set()
        else:
            self.second_started.set()
        self.release.wait(timeout=10)
        with self.lock:
            self.active -= 1
        return ProviderResult(
            text=json.dumps(ANSWER),
            usage=Usage(input_tokens=10, output_tokens=2),
        )


def test_paid_calls_are_serialized_by_max_concurrent_calls(
    settings: Settings, cache: Cache, monkeypatch
) -> None:
    source = SourceRef(
        kind="local",
        canonical_id="/tmp/clip.mp4",
        display="clip.mp4",
        fingerprint="concurrency-fingerprint",
        path=Path("/tmp/clip.mp4"),
    )
    media = MediaInfo(
        duration_s=10,
        width=640,
        height=360,
        fps=30,
        has_audio=True,
        mime_type="video/mp4",
        size_bytes=10_000,
        container="mp4",
        video_codec="h264",
    )
    monkeypatch.setattr(
        "visual_eyes_mcp.tools._prepare_source",
        lambda app, _source, *, require_uploadable: (source, media),
    )

    provider = BlockingProvider()
    app = App(settings=settings, cache=cache, provider=provider)
    slots = TrackingSemaphore(1)
    app._call_slots = slots

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(
            analyze_video,
            app,
            source=str(source.path),
            question="What is shown first?",
        )
        try:
            assert provider.first_started.wait(timeout=5)
            second = pool.submit(
                analyze_video,
                app,
                source=str(source.path),
                question="What is shown second?",
            )
            assert slots.second_attempted.wait(timeout=5)
            assert not provider.second_started.is_set()
        finally:
            provider.release.set()

        first_result = first.result(timeout=10)
        second_result = second.result(timeout=10)

    assert first_result.answer == ANSWER["answer"]
    assert second_result.answer == ANSWER["answer"]
    assert provider.calls == 2
    assert provider.max_active == 1
