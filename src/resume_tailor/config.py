"""Cross-platform, secret-free application configuration."""

from __future__ import annotations

import os
import tomllib
from dataclasses import asdict, dataclass, fields, replace
from pathlib import Path
from typing import Any, Literal, get_args

from platformdirs import PlatformDirs

from resume_tailor.errors import ConfigError

APP_NAME = "resume-tailor"
APP_AUTHOR = "Aritra Sarkar"
CONFIG_FILENAME = "config.toml"
ENV_PREFIX = "RESUME_TAILOR_"

PagePreference = Literal["1", "2", "auto"]
TexEngine = Literal["auto", "tectonic", "xelatex", "pdflatex"]


@dataclass(frozen=True, slots=True)
class AppConfig:
    """Validated non-secret settings."""

    provider: str = "deepseek"
    model: str | None = None
    api_base: str | None = None
    master_cv: Path = Path("Master_CV.md")
    output_dir: Path = Path("output")
    pages: PagePreference = "auto"
    tex_engine: TexEngine = "auto"
    timeout: float = 60.0


def config_path(*, dirs: PlatformDirs | None = None) -> Path:
    resolved_dirs = dirs or PlatformDirs(APP_NAME, APP_AUTHOR)
    return Path(resolved_dirs.user_config_path) / CONFIG_FILENAME


def _coerce(field_name: str, value: object) -> object:
    if field_name in {"master_cv", "output_dir"}:
        return Path(str(value)).expanduser()
    if field_name == "timeout":
        try:
            timeout = float(str(value))
        except (TypeError, ValueError) as exc:
            raise ConfigError("timeout must be a number greater than zero") from exc
        if timeout <= 0:
            raise ConfigError("timeout must be greater than zero")
        return timeout
    if field_name in {"model", "api_base"}:
        return str(value).strip() or None
    return str(value).strip()


def _validated(config: AppConfig) -> AppConfig:
    provider = config.provider.strip().lower()
    if not provider or not all(character.isalnum() or character in "-_" for character in provider):
        raise ConfigError("provider must contain only letters, numbers, hyphens, or underscores")
    if config.pages not in get_args(PagePreference):
        raise ConfigError(f"pages must be one of: {', '.join(get_args(PagePreference))}")
    if config.tex_engine not in get_args(TexEngine):
        raise ConfigError(f"tex_engine must be one of: {', '.join(get_args(TexEngine))}")
    if config.timeout <= 0:
        raise ConfigError("timeout must be greater than zero")
    return replace(config, provider=provider)


def _known_values(raw: dict[str, Any], *, source: str) -> dict[str, object]:
    names = {item.name for item in fields(AppConfig)}
    unknown = sorted(set(raw) - names)
    if unknown:
        joined = ", ".join(unknown)
        raise ConfigError(f"unknown setting(s) in {source}: {joined}")
    return {name: _coerce(name, value) for name, value in raw.items()}


def read_config_file(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    try:
        with path.open("rb") as stream:
            parsed = tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(
            f"could not read configuration at {path}",
            hint="Fix the TOML file or move it aside and try again.",
        ) from exc
    if not isinstance(parsed, dict):
        raise ConfigError(f"configuration at {path} must be a TOML table")
    app_table = parsed.get("resume_tailor", parsed)
    if not isinstance(app_table, dict):
        raise ConfigError("the [resume_tailor] configuration value must be a table")
    return _known_values(app_table, source=str(path))


def environment_values(environ: dict[str, str] | None = None) -> dict[str, object]:
    source = os.environ if environ is None else environ
    result: dict[str, object] = {}
    for item in fields(AppConfig):
        key = f"{ENV_PREFIX}{item.name.upper()}"
        if key in source:
            result[item.name] = _coerce(item.name, source[key])
    return result


def load_config(
    *,
    path: Path | None = None,
    environ: dict[str, str] | None = None,
    cli_overrides: dict[str, object] | None = None,
    dirs: PlatformDirs | None = None,
) -> AppConfig:
    """Load settings using default < TOML < environment < CLI precedence."""

    resolved_path = path or config_path(dirs=dirs)
    values: dict[str, object] = {}
    values.update(read_config_file(resolved_path))
    values.update(environment_values(environ))
    if cli_overrides:
        non_null = {key: value for key, value in cli_overrides.items() if value is not None}
        values.update(_known_values(non_null, source="CLI overrides"))
    try:
        return _validated(AppConfig(**values))  # type: ignore[arg-type]
    except TypeError as exc:
        raise ConfigError("configuration contains invalid values") from exc


def serializable_config(config: AppConfig) -> dict[str, str | float | None]:
    """Return non-secret settings suitable for display or persistence."""

    result: dict[str, str | float | None] = {}
    for key, value in asdict(config).items():
        result[key] = str(value) if isinstance(value, Path) else value
    return result


def write_config(config: AppConfig, *, path: Path | None = None) -> Path:
    """Persist non-secret settings atomically as simple TOML."""

    destination = path or config_path()
    destination.parent.mkdir(parents=True, exist_ok=True)
    lines = ["[resume_tailor]"]
    for key, value in serializable_config(config).items():
        if value is None:
            continue
        if isinstance(value, float):
            lines.append(f"{key} = {value}")
        else:
            escaped = value.replace("\\", "\\\\").replace('"', '\\"')
            lines.append(f'{key} = "{escaped}"')
    temporary = destination.with_suffix(".tmp")
    try:
        temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
        temporary.replace(destination)
    except OSError as exc:
        raise ConfigError(
            f"could not write configuration at {destination}",
            hint="Check that the configuration directory exists and is writable.",
        ) from exc
    return destination
