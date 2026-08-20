from __future__ import annotations

from pathlib import Path

import pytest

from resume_tailor.config import AppConfig, load_config, read_config_file, write_config
from resume_tailor.errors import ConfigError


def test_config_precedence(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        """[resume_tailor]
provider = "openai"
model = "file-model"
pages = "2"
timeout = 20
""",
        encoding="utf-8",
    )

    config = load_config(
        path=path,
        environ={"RESUME_TAILOR_MODEL": "env-model", "RESUME_TAILOR_TIMEOUT": "30"},
        cli_overrides={"model": "cli-model", "pages": "1"},
    )

    assert config.provider == "openai"
    assert config.model == "cli-model"
    assert config.pages == "1"
    assert config.timeout == 30.0


def test_default_config_when_file_is_missing(tmp_path: Path) -> None:
    config = load_config(path=tmp_path / "missing.toml", environ={})
    assert config == AppConfig()


def test_unknown_setting_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('secret_key = "must-not-be-here"\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="unknown setting"):
        read_config_file(path)


def test_invalid_timeout_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="greater than zero"):
        load_config(path=tmp_path / "missing", environ={"RESUME_TAILOR_TIMEOUT": "0"})


def test_write_round_trip_has_no_secret_fields(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "config.toml"
    config = AppConfig(model="deepseek-chat", output_dir=Path("my output"))
    write_config(config, path=path)
    content = path.read_text(encoding="utf-8")

    assert "api_key" not in content
    assert "secret" not in content
    assert load_config(path=path, environ={}) == config
