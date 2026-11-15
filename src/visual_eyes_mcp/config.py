"""Configuration loading.

Precedence is environment > TOML > built-in defaults. The project-root ``.env``
is loaded into the environment first, without replacing values that are already
set. The TOML file is optional; when absent, defaults apply and no quota is
configured. Secrets are read from the environment only, and the loader rejects a
TOML file that appears to contain key material.
"""

from __future__ import annotations

import logging
import os
import re
import tomllib
from collections.abc import Mapping, MutableMapping
from pathlib import Path
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    ValidationError,
    field_validator,
    model_validator,
)

from .errors import ConfigError

log = logging.getLogger(__name__)

APP_NAME = "visual-eyes-mcp"
DEFAULT_MODEL = "gemini-3.5-flash-lite"
DEFAULT_MODEL_HIGH = "gemini-3.8-flash"
DEFAULT_MODEL_EXPERIMENTAL = "gemini-4-hydrogen"

_SECRET_KEYS = {
    "api_key",
    "apikey",
    "api_token",
    "access_token",
    "gemini_api_key",
    "google_api_key",
    "secret",
    "token",
}
_GOOGLE_KEY_RE = re.compile(r"AIza[0-9A-Za-z_\-]{30,}")
_ENV_PREFIX = "VISUAL_EYES_"
_ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_PROJECT_ENV_PATH = _PROJECT_ROOT / ".env"

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


class ModelsConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    default: str = DEFAULT_MODEL
    high: str = DEFAULT_MODEL_HIGH
    experimental: str = DEFAULT_MODEL_EXPERIMENTAL

    @field_validator("default")
    @classmethod
    def _default_required(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be empty")
        return value

    @field_validator("high", "experimental")
    @classmethod
    def _optional_model(cls, value: str) -> str:
        return value.strip()


class PathsConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    allowed_dirs: tuple[Path, ...] = ()
    include_cwd: bool = True

    @field_validator("allowed_dirs", mode="before")
    @classmethod
    def _absolute_existing_dirs(cls, value: Any) -> tuple[Path, ...]:
        if value is None:
            return ()
        if not isinstance(value, list | tuple):
            raise ValueError("must be a list of directory paths")

        resolved: list[Path] = []
        for item in value:
            if not isinstance(item, str | Path):
                raise ValueError("entries must be strings")
            path = Path(item).expanduser()
            if not path.is_absolute():
                raise ValueError(f"{str(path)!r} must be an absolute path")
            path = path.resolve()
            if not path.exists():
                raise ValueError(f"{str(path)!r} does not exist")
            if not path.is_dir():
                raise ValueError(f"{str(path)!r} is not a directory")
            if path not in resolved:
                resolved.append(path)
        return tuple(resolved)


class LimitsConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    max_video_seconds: float = Field(default=1800.0, gt=0)
    max_input_tokens_per_call: int = Field(default=200_000, gt=0)
    max_file_size_mb: int = Field(default=1024, gt=0)
    max_frames_per_call: int = Field(default=10, ge=1, le=10)
    default_frames: int = Field(default=4, ge=1)
    max_frame_width: int = Field(default=1600, ge=320, le=4096)
    min_frame_width: int = Field(default=320, ge=64)
    max_answer_chars: int = Field(default=4000, ge=100)
    analysis_timeout_seconds: float = Field(default=120.0, gt=0)
    probe_timeout_seconds: float = Field(default=20.0, gt=0)
    frame_timeout_seconds: float = Field(default=30.0, gt=0)
    upload_timeout_seconds: float = Field(default=120.0, gt=0)
    max_concurrent_calls: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def _consistent(self) -> LimitsConfig:
        if self.default_frames > self.max_frames_per_call:
            raise ValueError("default_frames must not exceed max_frames_per_call")
        if self.min_frame_width > self.max_frame_width:
            raise ValueError("min_frame_width must not exceed max_frame_width")
        return self


class Quota(BaseModel):
    """Per-model budget. Only present when the user configured it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    rpm: int | None = Field(default=None, gt=0)
    rpd: int | None = Field(default=None, gt=0)
    tpm: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _at_least_one(self) -> Quota:
        if self.rpm is None and self.rpd is None and self.tpm is None:
            raise ValueError("a quota table needs at least one of rpm, rpd, tpm")
        return self


class PrivacyConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    store_interactions: bool = False


class FeaturesConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    youtube: bool = True


class CacheConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    data_dir: Path | None = None
    ttl_seconds: int = Field(default=7 * 24 * 3600, gt=0)
    max_mb: int = Field(default=512, gt=0)

    @field_validator("data_dir", mode="before")
    @classmethod
    def _absolute_data_dir(cls, value: Any) -> Path | None:
        if value is None:
            return None
        path = Path(value).expanduser()
        if not path.is_absolute():
            raise ValueError("cache.data_dir must be an absolute path")
        return path


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True)

    models: ModelsConfig = Field(default_factory=ModelsConfig)
    paths: PathsConfig = Field(default_factory=PathsConfig)
    limits: LimitsConfig = Field(default_factory=LimitsConfig)
    quotas: dict[str, Quota] = Field(default_factory=dict)
    privacy: PrivacyConfig = Field(default_factory=PrivacyConfig)
    features: FeaturesConfig = Field(default_factory=FeaturesConfig)
    cache: CacheConfig = Field(default_factory=CacheConfig)
    data_dir: Path
    config_path: Path | None = None
    api_key: SecretStr | None = None

    def allowed_roots(self, *, cwd: Path | None = None) -> tuple[Path, ...]:
        """All effective roots, including CWD when enabled."""

        roots = list(self.paths.allowed_dirs)
        if self.paths.include_cwd:
            roots.append((cwd or Path.cwd()).resolve())
        seen: set[Path] = set()
        unique: list[Path] = []
        for root in roots:
            if root not in seen:
                seen.add(root)
                unique.append(root)
        return tuple(unique)

    def quota_for(self, model: str) -> Quota | None:
        return self.quotas.get(model)

    def startup_summary(self) -> str:
        """A one-line summary safe to log (no values, no secrets)."""

        return (
            f"config={self.config_path or 'defaults'} "
            f"model={self.models.default} "
            f"api_key_present={self.api_key is not None} "
            f"youtube={self.features.youtube} "
            f"allowed_roots={len(self.allowed_roots())} "
            f"quotas_configured={len(self.quotas)} "
            f"data_dir={self.data_dir}"
        )


def _parse_bool(raw: str, *, name: str) -> bool:
    value = raw.strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    raise ConfigError(f"{name} must be one of true/false/1/0/yes/no/on/off", hint=f"got {raw!r}")


def _parse_int(raw: str, *, name: str) -> int:
    try:
        return int(raw.strip())
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer", hint=f"got {raw!r}") from exc


def _parse_float(raw: str, *, name: str) -> float:
    try:
        return float(raw.strip())
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number", hint=f"got {raw!r}") from exc


def _apply_env(sections: dict[str, dict[str, Any]], env: Mapping[str, str]) -> None:
    """Apply environment overrides to the raw section dictionaries."""

    models = sections["models"]
    if value := env.get(f"{_ENV_PREFIX}MODEL"):
        models["default"] = value
    if value := env.get(f"{_ENV_PREFIX}MODEL_HIGH"):
        models["high"] = value
    if value := env.get(f"{_ENV_PREFIX}MODEL_EXPERIMENTAL"):
        models["experimental"] = value

    paths = sections["paths"]
    if value := env.get(f"{_ENV_PREFIX}ALLOWED_DIRS"):
        separators = os.pathsep
        paths["allowed_dirs"] = [part for part in value.split(separators) if part.strip()]
    if value := env.get(f"{_ENV_PREFIX}INCLUDE_CWD"):
        paths["include_cwd"] = _parse_bool(value, name=f"{_ENV_PREFIX}INCLUDE_CWD")

    features = sections["features"]
    if value := env.get(f"{_ENV_PREFIX}YOUTUBE"):
        features["youtube"] = _parse_bool(value, name=f"{_ENV_PREFIX}YOUTUBE")

    privacy = sections["privacy"]
    if value := env.get(f"{_ENV_PREFIX}STORE_INTERACTIONS"):
        privacy["store_interactions"] = _parse_bool(
            value, name=f"{_ENV_PREFIX}STORE_INTERACTIONS"
        )

    limits = sections["limits"]
    int_limits = {
        f"{_ENV_PREFIX}MAX_INPUT_TOKENS_PER_CALL": "max_input_tokens_per_call",
        f"{_ENV_PREFIX}MAX_FILE_SIZE_MB": "max_file_size_mb",
        f"{_ENV_PREFIX}MAX_CONCURRENT_CALLS": "max_concurrent_calls",
        f"{_ENV_PREFIX}MAX_FRAMES_PER_CALL": "max_frames_per_call",
        f"{_ENV_PREFIX}MAX_FRAME_WIDTH": "max_frame_width",
    }
    float_limits = {
        f"{_ENV_PREFIX}MAX_VIDEO_SECONDS": "max_video_seconds",
        f"{_ENV_PREFIX}ANALYSIS_TIMEOUT_SECONDS": "analysis_timeout_seconds",
    }
    for env_name, field in int_limits.items():
        if value := env.get(env_name):
            limits[field] = _parse_int(value, name=env_name)
    for env_name, field in float_limits.items():
        if value := env.get(env_name):
            limits[field] = _parse_float(value, name=env_name)

    cache = sections["cache"]
    if value := env.get(f"{_ENV_PREFIX}DATA_DIR"):
        cache["data_dir"] = value
    if value := env.get(f"{_ENV_PREFIX}CACHE_TTL_SECONDS"):
        cache["ttl_seconds"] = _parse_int(value, name=f"{_ENV_PREFIX}CACHE_TTL_SECONDS")
    if value := env.get(f"{_ENV_PREFIX}MAX_CACHE_MB"):
        cache["max_mb"] = _parse_int(value, name=f"{_ENV_PREFIX}MAX_CACHE_MB")


def _reject_secrets(node: Any, *, path: str = "") -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            key_path = f"{path}.{key}" if path else str(key)
            if str(key).strip().lower() in _SECRET_KEYS:
                raise ConfigError(
                    f"secret-looking key {key_path!r} found in TOML",
                    hint="API keys belong in GEMINI_API_KEY/GOOGLE_API_KEY, not the config file",
                )
            _reject_secrets(value, path=key_path)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            _reject_secrets(value, path=f"{path}[{index}]")
    elif isinstance(node, str) and _GOOGLE_KEY_RE.search(node):
        raise ConfigError(
            f"secret-looking value found at {path or '<root>'}",
            hint="remove the key from the config file and set GEMINI_API_KEY instead",
        )


def _resolve_config_path(env: Mapping[str, str]) -> Path | None:
    explicit = env.get(f"{_ENV_PREFIX}CONFIG")
    if explicit:
        path = Path(explicit).expanduser()
        if not path.is_absolute():
            raise ConfigError(f"{_ENV_PREFIX}CONFIG must be an absolute path")
        if not path.exists():
            raise ConfigError(
                f"{_ENV_PREFIX}CONFIG points at a missing file: {path}",
                hint="create the file or unset the variable",
            )
        return path

    xdg = env.get("XDG_CONFIG_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".config"
    candidate = base / APP_NAME / "config.toml"
    return candidate if candidate.exists() else None


def _default_data_dir(env: Mapping[str, str]) -> Path:
    xdg = env.get("XDG_CACHE_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".cache"
    return (base / APP_NAME).resolve()


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"invalid TOML in {path}: {exc}") from exc
    except OSError as exc:
        raise ConfigError(f"cannot read {path}: {exc.strerror or exc}") from exc


def _env_value(raw: str) -> str:
    """Parse one .env value without evaluating shell syntax."""

    value = raw.strip()
    if value[:1] in {"'", '"'}:
        quote = value[0]
        end = value.find(quote, 1)
        if end != -1:
            return value[1:end]
    comment = re.search(r"\s+#", value)
    if comment:
        value = value[: comment.start()].rstrip()
    return value


def _read_env_file(path: Path) -> dict[str, str]:
    """Read a small, conventional KEY=VALUE .env file.

    Values are never included in errors or logs. Both ``KEY=value`` and
    ``export KEY=value`` are accepted, as are blank lines, comments, and
    single- or double-quoted values.
    """

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ConfigError(f"cannot read {path}: {exc.strerror or exc}") from exc

    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, separator, raw_value = line.partition("=")
        key = key.strip()
        if not separator or not _ENV_KEY_RE.fullmatch(key):
            raise ConfigError(
                f"invalid .env entry on line {line_number} of {path}",
                hint="expected KEY=VALUE",
            )
        values[key] = _env_value(raw_value)
    return values


def load_project_env(
    path: Path | None = None,
    env: MutableMapping[str, str] | None = None,
) -> bool:
    """Load the project-root .env file without overriding existing values.

    Returns ``True`` when a file was loaded. The function is intentionally
    silent so credential values cannot reach logs or error output.
    """

    env_path = _PROJECT_ENV_PATH if path is None else path
    if not env_path.is_file():
        return False

    target = os.environ if env is None else env
    for key, value in _read_env_file(env_path).items():
        target.setdefault(key, value)
    return True


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    """Load, merge, and validate configuration."""

    if env is None:
        load_project_env()
        env = os.environ
    config_path = _resolve_config_path(env)

    raw: dict[str, Any] = _read_toml(config_path) if config_path else {}
    if not isinstance(raw, dict):  # pragma: no cover - tomllib always returns a dict
        raise ConfigError(f"{config_path} did not contain a TOML table")

    _reject_secrets(raw)

    section_names = ("models", "paths", "limits", "privacy", "features", "cache")
    sections: dict[str, dict[str, Any]] = {}
    for name in section_names:
        value = raw.get(name, {})
        if not isinstance(value, dict):
            raise ConfigError(
                f"[{name}] must be a TOML table", hint=f"in {config_path or 'config'}"
            )
        sections[name] = dict(value)

    for key in raw:
        if key not in section_names:
            log.warning("unknown config section %r ignored", key)

    _apply_env(sections, env)

    limits_section = sections["limits"]
    quotas_raw = {
        model: value for model, value in limits_section.items() if isinstance(value, dict)
    }
    limits_scalars = {
        key: value for key, value in limits_section.items() if not isinstance(value, dict)
    }
    limits_values = _filter_known(limits_scalars, LimitsConfig)

    allowed_limit_keys = set(LimitsConfig.model_fields)
    for key in limits_scalars:
        if key not in allowed_limit_keys:
            log.warning("unknown config key 'limits.%s' ignored", key)
    for model in quotas_raw:
        if not isinstance(model, str) or not model:
            raise ConfigError("quota model IDs must be non-empty strings")

    privacy_section = sections["privacy"]
    features_section = sections["features"]
    cache_section = sections["cache"]
    models_section = sections["models"]
    paths_section = sections["paths"]
    for section_name, section, model_cls in (
        ("models", models_section, ModelsConfig),
        ("paths", paths_section, PathsConfig),
        ("privacy", privacy_section, PrivacyConfig),
        ("features", features_section, FeaturesConfig),
        ("cache", cache_section, CacheConfig),
    ):
        for key in section:
            if key not in model_cls.model_fields:
                log.warning("unknown config key '%s.%s' ignored", section_name, key)

    data_dir = (
        Path(cache_section["data_dir"]).expanduser()
        if cache_section.get("data_dir")
        else _default_data_dir(env)
    )
    if not data_dir.is_absolute():
        raise ConfigError(
            "cache.data_dir must be an absolute path", hint=f"got {str(data_dir)!r}"
        )
    cache_section = {
        key: value for key, value in cache_section.items() if key in CacheConfig.model_fields
    }

    api_key_raw = env.get("GEMINI_API_KEY") or env.get("GOOGLE_API_KEY")
    api_key = SecretStr(api_key_raw) if api_key_raw and api_key_raw.strip() else None

    try:
        return Settings(
            models=ModelsConfig(**_filter_known(models_section, ModelsConfig)),
            paths=PathsConfig(**_filter_known(paths_section, PathsConfig)),
            limits=LimitsConfig(**limits_values),
            quotas={model: Quota(**value) for model, value in quotas_raw.items()},
            privacy=PrivacyConfig(**_filter_known(privacy_section, PrivacyConfig)),
            features=FeaturesConfig(**_filter_known(features_section, FeaturesConfig)),
            cache=CacheConfig(**cache_section),
            data_dir=data_dir.resolve(),
            config_path=config_path,
            api_key=api_key,
        )
    except ValidationError as exc:
        details = []
        for error in exc.errors():
            location = ".".join(str(part) for part in error["loc"]) or "<root>"
            details.append(f"{location}: {error['msg']}")
        raise ConfigError(
            "invalid configuration: " + "; ".join(details),
            hint=f"check {config_path}" if config_path else "check environment variables",
        ) from exc


def _filter_known(section: dict[str, Any], model_cls: type[BaseModel]) -> dict[str, Any]:
    return {key: value for key, value in section.items() if key in model_cls.model_fields}
