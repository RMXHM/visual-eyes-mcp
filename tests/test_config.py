from __future__ import annotations

import logging
from pathlib import Path

import pytest

from visual_eyes_mcp.config import (
    DEFAULT_MODEL,
    DEFAULT_MODEL_HIGH,
    load_settings,
)
from visual_eyes_mcp.errors import ConfigError


def write_config(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(content, encoding="utf-8")
    return path


def base_env(tmp_path: Path) -> dict[str, str]:
    return {
        "XDG_CONFIG_HOME": str(tmp_path / "xdg-config"),
        "XDG_CACHE_HOME": str(tmp_path / "xdg-cache"),
        "VISUAL_EYES_INCLUDE_CWD": "false",
    }


def test_defaults_without_config(tmp_path: Path) -> None:
    settings = load_settings(env=base_env(tmp_path))

    assert settings.models.default == DEFAULT_MODEL
    assert settings.models.high == DEFAULT_MODEL_HIGH
    assert settings.paths.allowed_dirs == ()
    assert settings.paths.include_cwd is False
    assert settings.quotas == {}
    assert settings.features.youtube is True
    assert settings.privacy.store_interactions is False
    assert settings.config_path is None
    assert settings.api_key is None
    assert settings.data_dir == (tmp_path / "xdg-cache" / "visual-eyes-mcp").resolve()


def test_toml_values_and_environment_precedence(tmp_path: Path, media_dir: Path) -> None:
    path = write_config(
        tmp_path,
        f"""
[models]
default = "toml-default"
high = "toml-high"
experimental = "toml-experimental"

[paths]
allowed_dirs = ["{media_dir}"]
include_cwd = false

[limits]
max_video_seconds = 42

[limits."toml-default"]
rpm = 5
rpd = 20
tpm = 10000

[features]
youtube = false

[privacy]
store_interactions = true

[cache]
ttl_seconds = 100
max_mb = 64
""",
    )
    env = base_env(tmp_path)
    env["VISUAL_EYES_CONFIG"] = str(path)
    env["VISUAL_EYES_MODEL"] = "env-default"

    settings = load_settings(env=env)

    assert settings.models.default == "env-default"
    assert settings.models.high == "toml-high"
    assert settings.models.experimental == "toml-experimental"
    assert settings.limits.max_video_seconds == 42
    assert settings.paths.allowed_dirs == (media_dir.resolve(),)
    assert settings.quotas["toml-default"].rpm == 5
    assert settings.quotas["toml-default"].rpd == 20
    assert settings.quotas["toml-default"].tpm == 10000
    assert settings.features.youtube is False
    assert settings.privacy.store_interactions is True
    assert settings.cache.ttl_seconds == 100
    assert settings.cache.max_mb == 64
    assert settings.config_path == path


def test_unknown_keys_warn_and_are_ignored(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = write_config(
        tmp_path,
        """
[models]
weird = "ignored"

[limits]
nope = 12

[extra]
thing = true
""",
    )
    env = base_env(tmp_path)
    env["VISUAL_EYES_CONFIG"] = str(path)

    with caplog.at_level(logging.WARNING, logger="visual_eyes_mcp.config"):
        settings = load_settings(env=env)

    assert settings.models.default == DEFAULT_MODEL
    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert "unknown config key 'models.weird' ignored" in messages
    assert "unknown config key 'limits.nope' ignored" in messages
    assert "unknown config section 'extra' ignored" in messages


def test_malformed_toml_is_a_config_error(tmp_path: Path) -> None:
    path = write_config(tmp_path, "[models\n")
    env = base_env(tmp_path)
    env["VISUAL_EYES_CONFIG"] = str(path)

    with pytest.raises(ConfigError, match="invalid TOML"):
        load_settings(env=env)


def test_missing_explicit_config_is_a_config_error(tmp_path: Path) -> None:
    env = base_env(tmp_path)
    env["VISUAL_EYES_CONFIG"] = str(tmp_path / "missing.toml")

    with pytest.raises(ConfigError, match="missing file"):
        load_settings(env=env)


def test_relative_explicit_config_is_a_config_error(tmp_path: Path) -> None:
    env = base_env(tmp_path)
    env["VISUAL_EYES_CONFIG"] = "relative.toml"

    with pytest.raises(ConfigError, match="must be an absolute path"):
        load_settings(env=env)


def test_secret_key_in_toml_is_rejected(tmp_path: Path) -> None:
    path = write_config(tmp_path, '[privacy]\napi_key = "looks-secret"\n')
    env = base_env(tmp_path)
    env["VISUAL_EYES_CONFIG"] = str(path)

    with pytest.raises(ConfigError, match="secret-looking key"):
        load_settings(env=env)


def test_key_shaped_value_in_toml_is_rejected(tmp_path: Path) -> None:
    fake_key = "AIza" + "A" * 35
    path = write_config(tmp_path, f'[models]\ndefault = "{fake_key}"\n')
    env = base_env(tmp_path)
    env["VISUAL_EYES_CONFIG"] = str(path)

    with pytest.raises(ConfigError, match="secret-looking value"):
        load_settings(env=env)


def test_relative_allowed_dir_is_rejected(tmp_path: Path) -> None:
    path = write_config(tmp_path, '[paths]\nallowed_dirs = ["relative/dir"]\n')
    env = base_env(tmp_path)
    env["VISUAL_EYES_CONFIG"] = str(path)

    with pytest.raises(ConfigError, match="must be an absolute path"):
        load_settings(env=env)


def test_missing_allowed_dir_is_rejected(tmp_path: Path) -> None:
    path = write_config(
        tmp_path,
        f'[paths]\nallowed_dirs = ["{tmp_path / "does-not-exist"}"]\n',
    )
    env = base_env(tmp_path)
    env["VISUAL_EYES_CONFIG"] = str(path)

    with pytest.raises(ConfigError, match="does not exist"):
        load_settings(env=env)


def test_invalid_environment_boolean(tmp_path: Path) -> None:
    env = base_env(tmp_path)
    env["VISUAL_EYES_YOUTUBE"] = "maybe"

    with pytest.raises(ConfigError, match="VISUAL_EYES_YOUTUBE"):
        load_settings(env=env)


def test_invalid_environment_integer(tmp_path: Path) -> None:
    env = base_env(tmp_path)
    env["VISUAL_EYES_MAX_FRAMES_PER_CALL"] = "many"

    with pytest.raises(ConfigError, match="must be an integer"):
        load_settings(env=env)


def test_out_of_range_limit_is_a_config_error(tmp_path: Path) -> None:
    env = base_env(tmp_path)
    env["VISUAL_EYES_MAX_FRAMES_PER_CALL"] = "11"

    with pytest.raises(ConfigError, match="max_frames_per_call"):
        load_settings(env=env)


def test_empty_quota_table_is_rejected(tmp_path: Path) -> None:
    path = write_config(tmp_path, '[limits."gemini-3.5-flash-lite"]\n')
    env = base_env(tmp_path)
    env["VISUAL_EYES_CONFIG"] = str(path)

    with pytest.raises(ConfigError, match="at least one of rpm, rpd, tpm"):
        load_settings(env=env)


def test_secret_is_masked_and_summary_is_safe(tmp_path: Path) -> None:
    env = base_env(tmp_path)
    env["GEMINI_API_KEY"] = "super-secret-value"

    settings = load_settings(env=env)

    assert settings.api_key is not None
    assert str(settings.api_key) == "**********"
    assert "super-secret-value" not in repr(settings)
    assert settings.api_key.get_secret_value() == "super-secret-value"
    summary = settings.startup_summary()
    assert "super-secret-value" not in summary
    assert "api_key_present=True" in summary


def test_google_api_key_is_used_when_gemini_key_absent(tmp_path: Path) -> None:
    env = base_env(tmp_path)
    env["GOOGLE_API_KEY"] = "google-key"

    settings = load_settings(env=env)

    assert settings.api_key is not None
    assert settings.api_key.get_secret_value() == "google-key"


def test_allowed_roots_include_cwd_only_when_enabled(tmp_path: Path, media_dir: Path) -> None:
    env = base_env(tmp_path)
    settings = load_settings(env=env)
    assert settings.allowed_roots(cwd=media_dir) == ()

    env["VISUAL_EYES_INCLUDE_CWD"] = "true"
    settings = load_settings(env=env)
    assert settings.allowed_roots(cwd=media_dir) == (media_dir.resolve(),)


def test_allowed_dirs_environment_uses_pathsep(tmp_path: Path, media_dir: Path) -> None:
    other = tmp_path / "other"
    other.mkdir()
    env = base_env(tmp_path)
    env["VISUAL_EYES_ALLOWED_DIRS"] = f"{media_dir}:{other}"

    settings = load_settings(env=env)

    assert settings.paths.allowed_dirs == (media_dir.resolve(), other.resolve())


def test_section_must_be_a_table(tmp_path: Path) -> None:
    path = write_config(tmp_path, 'models = "not-a-table"\n')
    env = base_env(tmp_path)
    env["VISUAL_EYES_CONFIG"] = str(path)

    with pytest.raises(ConfigError, match=r"\[models\] must be a TOML table"):
        load_settings(env=env)


def test_empty_default_model_is_rejected(tmp_path: Path) -> None:
    path = write_config(tmp_path, '[models]\ndefault = "  "\n')
    env = base_env(tmp_path)
    env["VISUAL_EYES_CONFIG"] = str(path)

    with pytest.raises(ConfigError, match="must not be empty"):
        load_settings(env=env)


def test_relative_cache_data_dir_is_rejected(tmp_path: Path) -> None:
    path = write_config(tmp_path, '[cache]\ndata_dir = "relative/cache"\n')
    env = base_env(tmp_path)
    env["VISUAL_EYES_CONFIG"] = str(path)

    with pytest.raises(ConfigError, match="cache.data_dir must be an absolute path"):
        load_settings(env=env)


def test_cache_environment_overrides(tmp_path: Path) -> None:
    env = base_env(tmp_path)
    env["VISUAL_EYES_CACHE_TTL_SECONDS"] = "123"
    env["VISUAL_EYES_MAX_CACHE_MB"] = "7"

    settings = load_settings(env=env)

    assert settings.cache.ttl_seconds == 123
    assert settings.cache.max_mb == 7
