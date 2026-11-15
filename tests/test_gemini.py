from __future__ import annotations

from types import SimpleNamespace

import pytest

from visual_eyes_mcp import gemini
from visual_eyes_mcp.errors import TransientError
from visual_eyes_mcp.gemini import GeminiProvider
from visual_eyes_mcp.models import ProviderRequest, VideoInput


class RecordingInteractions:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs: object) -> SimpleNamespace:
        self.calls.append(kwargs)
        return SimpleNamespace(
            output_text='{"answer": "ok"}',
            usage=SimpleNamespace(
                total_input_tokens=11,
                total_output_tokens=7,
                total_thought_tokens=2,
            ),
        )


class FailingInteractions:
    def __init__(self) -> None:
        self.calls = 0

    def create(self, **kwargs: object) -> None:
        self.calls += 1
        raise TimeoutError("simulated transient failure")


def make_request() -> ProviderRequest:
    return ProviderRequest(
        model="gemini-test-model",
        prompt="Describe the video.",
        json_schema={
            "type": "object",
            "properties": {"answer": {"type": "string"}},
            "required": ["answer"],
        },
        video=VideoInput(
            kind="youtube",
            uri="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        ),
        store=False,
        timeout_seconds=12.5,
    )


def test_analyze_sends_json_mime_type_with_schema() -> None:
    interactions = RecordingInteractions()
    client = SimpleNamespace(interactions=interactions)
    provider = GeminiProvider("unit-test-key-not-real", cache=None, client=client)
    request = make_request()

    result = provider.analyze(request)

    assert result.text == '{"answer": "ok"}'
    assert result.usage.input_tokens == 11
    assert result.usage.output_tokens == 7
    assert result.usage.thought_tokens == 2
    assert interactions.calls == [
        {
            "model": "gemini-test-model",
            "input": [
                {
                    "type": "video",
                    "uri": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                },
                {"type": "text", "text": "Describe the video."},
            ],
            "response_format": {
                "type": "text",
                "mime_type": "application/json",
                "schema": request.json_schema,
            },
            "store": False,
            "timeout": 12.5,
        }
    ]


def test_transient_retries_can_be_disabled() -> None:
    interactions = FailingInteractions()
    client = SimpleNamespace(interactions=interactions)
    provider = GeminiProvider(
        "unit-test-key-not-real",
        cache=None,
        max_transient_retries=0,
        client=client,
    )

    with pytest.raises(TransientError):
        provider.analyze(make_request())

    assert interactions.calls == 1


def test_transient_retries_default_to_two(monkeypatch: pytest.MonkeyPatch) -> None:
    interactions = FailingInteractions()
    client = SimpleNamespace(interactions=interactions)
    provider = GeminiProvider("unit-test-key-not-real", cache=None, client=client)
    sleeps: list[float] = []
    monkeypatch.setattr(gemini.time, "sleep", sleeps.append)
    monkeypatch.setattr(gemini.random, "random", lambda: 0.0)

    with pytest.raises(TransientError):
        provider.analyze(make_request())

    assert interactions.calls == 3
    assert len(sleeps) == 2
