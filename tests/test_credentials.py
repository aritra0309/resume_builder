from __future__ import annotations

import pytest

from resume_tailor.credentials import (
    KEYRING_SERVICE,
    CredentialManager,
    CredentialSource,
    environment_variable,
)
from resume_tailor.errors import CredentialError


class FakeKeyring:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def get_password(self, service_name: str, username: str) -> str | None:
        return self.values.get((service_name, username))

    def set_password(self, service_name: str, username: str, password: str) -> None:
        self.values[(service_name, username)] = password

    def delete_password(self, service_name: str, username: str) -> None:
        del self.values[(service_name, username)]


def test_environment_takes_precedence_over_keyring() -> None:
    backend = FakeKeyring()
    backend.set_password(KEYRING_SERVICE, "deepseek", "keyring-secret")
    manager = CredentialManager(
        keyring_backend=backend,
        environ={"DEEPSEEK_API_KEY": "environment-secret"},
    )

    result = manager.resolve("deepseek")
    assert result.value == "environment-secret"
    assert result.source is CredentialSource.ENVIRONMENT


def test_keyring_round_trip_and_status_do_not_return_secret() -> None:
    backend = FakeKeyring()
    manager = CredentialManager(keyring_backend=backend, environ={})
    manager.store("DeepSeek", "super-secret-value")

    assert manager.status("deepseek") is CredentialSource.KEYRING
    assert manager.resolve("deepseek").value == "super-secret-value"
    assert manager.delete("deepseek") is True
    assert manager.status("deepseek") is None


def test_hidden_prompt_fallback() -> None:
    manager = CredentialManager(keyring_backend=FakeKeyring(), environ={})
    result = manager.resolve("custom", allow_prompt=True, prompt=lambda _: "prompt-secret")
    assert result.source is CredentialSource.PROMPT
    assert result.value == "prompt-secret"


def test_missing_credential_error_contains_instruction_not_secret() -> None:
    manager = CredentialManager(keyring_backend=FakeKeyring(), environ={})
    with pytest.raises(CredentialError) as caught:
        manager.resolve("deepseek")
    assert "DEEPSEEK_API_KEY" in str(caught.value.hint)


def test_custom_provider_environment_variable() -> None:
    assert environment_variable("my-provider") == "MY_PROVIDER_API_KEY"
