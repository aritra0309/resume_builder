from __future__ import annotations

import pytest
from typer.testing import CliRunner

from resume_tailor import cli
from resume_tailor.cli import app
from resume_tailor.credentials import CredentialManager
from resume_tailor.errors import ConfigError, ExitCode

runner = CliRunner()


def test_help() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "auth" in result.stdout
    assert "doctor" in result.stdout


def test_auth_status_never_prints_secret(monkeypatch: object) -> None:
    from pytest import MonkeyPatch

    assert isinstance(monkeypatch, MonkeyPatch)
    secret = "environment-secret-value"
    monkeypatch.setenv("DEEPSEEK_API_KEY", secret)
    result = runner.invoke(app, ["auth", "status", "deepseek"])
    assert result.exit_code == 0
    assert "via environment" in result.stdout
    assert secret not in result.stdout


def test_manager_status_value_is_only_source() -> None:
    manager = CredentialManager(keyring_backend=None, environ={})
    assert manager.status("deepseek") is None


def test_expected_error_boundary_has_no_traceback_by_default(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "app", lambda: (_ for _ in ()).throw(ConfigError("bad config")))
    cli.runtime.debug = False
    with pytest.raises(SystemExit) as caught:
        cli.main()
    captured = capsys.readouterr()
    assert caught.value.code == int(ExitCode.USAGE)
    assert "bad config" in captured.err
    assert "Traceback" not in captured.err


def test_debug_traceback_redacts_secret(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    secret = "sk-abcdefghijk"
    monkeypatch.setattr(
        cli,
        "app",
        lambda: (_ for _ in ()).throw(ConfigError(f"api_key={secret}")),
    )
    cli.runtime.debug = True
    with pytest.raises(SystemExit):
        cli.main()
    captured = capsys.readouterr()
    assert "Traceback" in captured.err
    assert secret not in captured.err
    cli.runtime.debug = False
