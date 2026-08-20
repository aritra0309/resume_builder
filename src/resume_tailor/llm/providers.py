"""Validated registry of provider-specific, non-secret behavior."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from resume_tailor.errors import ConfigError

_NAME = re.compile(r"^[a-z][a-z0-9_-]*$")
_PREFIX = re.compile(r"^[a-z][a-z0-9_-]*/$")


@dataclass(frozen=True, slots=True)
class Provider:
    name: str
    display_name: str
    prefix: str
    key_variable: str | None
    default_models: tuple[str, ...]
    supports_live_listing: bool
    supports_json_mode: bool = False
    default_api_base: str | None = None
    unsupported_parameters: tuple[str, ...] = ()

    def model_name(self, model: str) -> str:
        model = model.strip()
        if not model:
            raise ConfigError("model name cannot be empty")
        if self.name == "custom" or model.startswith(self.prefix):
            return model
        return f"{self.prefix}{model}"


def validate_registry(providers: Iterable[Provider]) -> dict[str, Provider]:
    registry: dict[str, Provider] = {}
    for provider in providers:
        if not _NAME.fullmatch(provider.name):
            raise ValueError(f"invalid provider name: {provider.name!r}")
        if provider.name in registry:
            raise ValueError(f"duplicate provider name: {provider.name}")
        if provider.name != "custom" and not _PREFIX.fullmatch(provider.prefix):
            raise ValueError(f"invalid LiteLLM prefix for {provider.name}: {provider.prefix!r}")
        if provider.key_variable is not None and not re.fullmatch(
            r"[A-Z][A-Z0-9_]*", provider.key_variable
        ):
            raise ValueError(f"invalid API-key variable for {provider.name}")
        if not provider.default_models:
            raise ValueError(f"provider {provider.name} needs at least one curated model")
        registry[provider.name] = provider
    return registry


PROVIDERS = validate_registry(
    (
        Provider(
            "deepseek",
            "DeepSeek",
            "deepseek/",
            "DEEPSEEK_API_KEY",
            ("deepseek-v4-flash", "deepseek-v4-pro", "deepseek-chat", "deepseek-reasoner"),
            True,
            True,
        ),
        Provider("openai", "OpenAI", "openai/", "OPENAI_API_KEY", ("gpt-4o-mini",), True, True),
        Provider(
            "anthropic",
            "Anthropic",
            "anthropic/",
            "ANTHROPIC_API_KEY",
            ("claude-3-5-haiku-latest",),
            False,
        ),
        Provider(
            "gemini",
            "Google Gemini",
            "gemini/",
            "GEMINI_API_KEY",
            ("gemini-2.0-flash",),
            True,
            True,
        ),
        Provider(
            "openrouter",
            "OpenRouter",
            "openrouter/",
            "OPENROUTER_API_KEY",
            ("openai/gpt-4o-mini",),
            True,
            True,
        ),
        Provider("groq", "Groq", "groq/", "GROQ_API_KEY", ("llama-3.3-70b-versatile",), True, True),
        Provider(
            "ollama",
            "Ollama",
            "ollama/",
            None,
            ("llama3.2",),
            True,
            False,
            "http://localhost:11434",
        ),
        Provider("custom", "Custom OpenAI-compatible", "", None, ("manual-model",), False),
    )
)


def get_provider(name: str) -> Provider:
    try:
        return PROVIDERS[name.strip().lower()]
    except KeyError as exc:
        raise ConfigError(
            f"unknown provider: {name}", hint=f"Choose one of: {', '.join(PROVIDERS)}"
        ) from exc
