"""Credential storage and resolution without plaintext configuration."""

from __future__ import annotations

import getpass
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from resume_tailor.errors import CredentialError

KEYRING_SERVICE = "resume-tailor"

PROVIDER_ENV_VARS: dict[str, str] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "azure": "AZURE_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "groq": "GROQ_API_KEY",
    "openai": "OPENAI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
}


class KeyringLike(Protocol):
    def get_password(self, service_name: str, username: str) -> str | None: ...

    def set_password(self, service_name: str, username: str, password: str) -> None: ...

    def delete_password(self, service_name: str, username: str) -> None: ...


class CredentialSource(StrEnum):
    ENVIRONMENT = "environment"
    KEYRING = "keyring"
    PROMPT = "prompt"


@dataclass(frozen=True, slots=True)
class ResolvedCredential:
    value: str
    source: CredentialSource


def normalize_provider(provider: str) -> str:
    normalized = provider.strip().lower()
    if not normalized or not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", normalized):
        raise CredentialError(
            "provider must contain only letters, numbers, hyphens, or underscores"
        )
    return normalized


def environment_variable(provider: str) -> str:
    normalized = normalize_provider(provider)
    return PROVIDER_ENV_VARS.get(normalized, f"{normalized.upper().replace('-', '_')}_API_KEY")


def _default_keyring() -> KeyringLike | None:
    try:
        import keyring
    except ImportError:
        return None
    return keyring


class CredentialManager:
    """Resolve and manage provider credentials through safe sources."""

    def __init__(
        self,
        *,
        keyring_backend: KeyringLike | None = None,
        environ: dict[str, str] | None = None,
    ) -> None:
        self._keyring = keyring_backend if keyring_backend is not None else _default_keyring()
        self._environ = os.environ if environ is None else environ

    def status(self, provider: str) -> CredentialSource | None:
        normalized = normalize_provider(provider)
        variable = environment_variable(normalized)
        if self._environ.get(variable, "").strip():
            return CredentialSource.ENVIRONMENT
        value = self._keyring_get(normalized)
        return CredentialSource.KEYRING if value else None

    def resolve(
        self,
        provider: str,
        *,
        allow_prompt: bool = False,
        prompt: Callable[[str], str] = getpass.getpass,
    ) -> ResolvedCredential:
        normalized = normalize_provider(provider)
        variable = environment_variable(normalized)
        environment_value = self._environ.get(variable, "").strip()
        if environment_value:
            return ResolvedCredential(environment_value, CredentialSource.ENVIRONMENT)
        keyring_value = self._keyring_get(normalized)
        if keyring_value:
            return ResolvedCredential(keyring_value, CredentialSource.KEYRING)
        if allow_prompt:
            prompted = prompt(f"API key for {normalized}: ").strip()
            if prompted:
                return ResolvedCredential(prompted, CredentialSource.PROMPT)
        raise CredentialError(
            f"no credential is configured for {normalized}",
            hint=f"Set {variable} or run 'resume-tailor auth set {normalized}'.",
        )

    def store(self, provider: str, secret: str) -> None:
        normalized = normalize_provider(provider)
        value = secret.strip()
        if not value:
            raise CredentialError("API key cannot be empty")
        if self._keyring is None:
            raise CredentialError(
                "no OS keyring backend is available",
                hint=f"Set {environment_variable(normalized)} in your environment instead.",
            )
        try:
            self._keyring.set_password(KEYRING_SERVICE, normalized, value)
        except Exception as exc:
            raise CredentialError(
                "the OS keyring could not store this credential",
                hint=f"Set {environment_variable(normalized)} in your environment instead.",
            ) from exc

    def delete(self, provider: str) -> bool:
        normalized = normalize_provider(provider)
        if self._keyring is None:
            return False
        if not self._keyring_get(normalized):
            return False
        try:
            self._keyring.delete_password(KEYRING_SERVICE, normalized)
        except Exception as exc:
            raise CredentialError("the OS keyring could not delete this credential") from exc
        return True

    def _keyring_get(self, provider: str) -> str | None:
        if self._keyring is None:
            return None
        try:
            value = self._keyring.get_password(KEYRING_SERVICE, provider)
        except Exception:
            return None
        return value.strip() if value and value.strip() else None
