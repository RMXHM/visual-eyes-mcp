from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

from visual_eyes_mcp import config
from visual_eyes_mcp.config import load_project_env, load_settings


def base_env(tmp_path: Path) -> dict[str, str]:
    return {
        "XDG_CONFIG_HOME": str(tmp_path / "xdg-config"),
        "XDG_CACHE_HOME": str(tmp_path / "xdg-cache"),
        "VISUAL_EYES_INCLUDE_CWD": "false",
    }


def test_loads_env_file_without_overwriting_existing_values(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text(
        "# local development values\n"
        "\n"
        "export GEMINI_API_KEY='from-file'\n"
        "GOOGLE_API_KEY=from-file-google  # fallback\n"
        "EMPTY=\n",
        encoding="utf-8",
    )
    env = {"GEMINI_API_KEY": "from-environment"}

    loaded = load_project_env(path, env)

    assert loaded is True
    assert env == {
        "GEMINI_API_KEY": "from-environment",
        "GOOGLE_API_KEY": "from-file-google",
        "EMPTY": "",
    }


def test_missing_env_file_is_a_noop(tmp_path: Path) -> None:
    env = {"GEMINI_API_KEY": "from-environment"}

    loaded = load_project_env(tmp_path / "missing.env", env)

    assert loaded is False
    assert env == {"GEMINI_API_KEY": "from-environment"}


def test_load_settings_reads_project_env(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / ".env"
    path.write_text("GEMINI_API_KEY=from-file\n", encoding="utf-8")
    monkeypatch.setattr(config, "_PROJECT_ENV_PATH", path)

    with patch.dict(os.environ, base_env(tmp_path), clear=True):
        settings = load_settings()

    assert settings.api_key is not None
    assert settings.api_key.get_secret_value() == "from-file"


def test_existing_environment_wins_over_project_env(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / ".env"
    path.write_text("GEMINI_API_KEY=from-file\n", encoding="utf-8")
    monkeypatch.setattr(config, "_PROJECT_ENV_PATH", path)
    env = base_env(tmp_path) | {"GEMINI_API_KEY": "from-environment"}

    with patch.dict(os.environ, env, clear=True):
        settings = load_settings()

    assert settings.api_key is not None
    assert settings.api_key.get_secret_value() == "from-environment"


def test_blank_env_file_key_is_treated_as_missing(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / ".env"
    path.write_text("GEMINI_API_KEY=\n", encoding="utf-8")
    monkeypatch.setattr(config, "_PROJECT_ENV_PATH", path)

    with patch.dict(os.environ, base_env(tmp_path), clear=True):
        settings = load_settings()

    assert settings.api_key is None


def test_blank_gemini_key_falls_back_to_google_key(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / ".env"
    path.write_text(
        "GEMINI_API_KEY=\nGOOGLE_API_KEY=google-from-file\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "_PROJECT_ENV_PATH", path)

    with patch.dict(os.environ, base_env(tmp_path), clear=True):
        settings = load_settings()

    assert settings.api_key is not None
    assert settings.api_key.get_secret_value() == "google-from-file"


def test_blank_existing_key_is_not_overwritten(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / ".env"
    path.write_text("GEMINI_API_KEY=from-file\n", encoding="utf-8")
    monkeypatch.setattr(config, "_PROJECT_ENV_PATH", path)
    env = base_env(tmp_path) | {"GEMINI_API_KEY": "   "}

    with patch.dict(os.environ, env, clear=True):
        settings = load_settings()

    assert settings.api_key is None
