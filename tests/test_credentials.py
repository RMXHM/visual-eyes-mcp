from __future__ import annotations

import logging

import pytest

from visual_eyes_mcp.__main__ import RedactingFormatter
from visual_eyes_mcp.config import load_settings
from visual_eyes_mcp.errors import ConfigError
from visual_eyes_mcp.gemini import GeminiProvider, _safe_message
from visual_eyes_mcp.tools import App

_FAKE_KEY = "AIza" + "B" * 35


def test_gemini_key_takes_precedence_over_google_key(tmp_path) -> None:
    settings = load_settings(
        env={
            "GEMINI_API_KEY": "gemini-key",
            "GOOGLE_API_KEY": "google-key",
            "XDG_CONFIG_HOME": str(tmp_path / "config"),
            "XDG_CACHE_HOME": str(tmp_path / "cache"),
        }
    )

    assert settings.api_key is not None
    assert settings.api_key.get_secret_value() == "gemini-key"


@pytest.mark.parametrize("blank", ["", "   ", "\n\t"])
def test_blank_keys_are_ignored(tmp_path, blank: str) -> None:
    settings = load_settings(
        env={
            "GEMINI_API_KEY": blank,
            "XDG_CONFIG_HOME": str(tmp_path / "config"),
            "XDG_CACHE_HOME": str(tmp_path / "cache"),
        }
    )

    assert settings.api_key is None


def test_provider_requires_an_environment_key(make_settings, media_dir) -> None:
    settings = make_settings(VISUAL_EYES_ALLOWED_DIRS=str(media_dir))
    app = App.from_settings(settings)

    with pytest.raises(ConfigError, match="GEMINI_API_KEY"):
        app.get_provider()


def test_provider_construction_makes_no_network_call(make_settings, media_dir) -> None:
    settings = make_settings(
        VISUAL_EYES_ALLOWED_DIRS=str(media_dir),
        GEMINI_API_KEY="unit-test-key-not-real",
    )
    app = App.from_settings(settings)

    provider = app.get_provider()

    assert isinstance(provider, GeminiProvider)
    assert app.get_provider() is provider


def test_redacting_formatter_hides_known_secret() -> None:
    formatter = RedactingFormatter("%(message)s", secret="super-secret-token")
    record = logging.LogRecord(
        "visual_eyes_mcp",
        logging.INFO,
        __file__,
        1,
        "auth with super-secret-token",
        None,
        None,
    )

    rendered = formatter.format(record)

    assert "super-secret-token" not in rendered
    assert "***" in rendered


def test_redacting_formatter_hides_key_shaped_value() -> None:
    formatter = RedactingFormatter("%(message)s")
    record = logging.LogRecord(
        "visual_eyes_mcp",
        logging.INFO,
        __file__,
        1,
        f"request used {_FAKE_KEY}",
        None,
        None,
    )

    rendered = formatter.format(record)

    assert _FAKE_KEY not in rendered
    assert "AIza" not in rendered


def test_provider_error_messages_scrub_keys() -> None:
    message = _safe_message(f"rejected request with key={_FAKE_KEY}")

    assert _FAKE_KEY not in message
    assert "AIza" not in message
