"""Small, injectable LiteLLM boundary with bounded, safe retry behavior."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError

from resume_tailor.errors import ProviderError
from resume_tailor.llm.capabilities import capabilities_for
from resume_tailor.llm.providers import Provider

Completion = Callable[..., Any]
logger = logging.getLogger(__name__)

_EMPTY_RESPONSE = "provider returned an empty completion response"
_INVALID_SCHEMA = "provider returned invalid schema JSON"


@dataclass(frozen=True, slots=True)
class LLMResponse:
    text: str
    usage: dict[str, int | float | None]
    retries: int
    used_native_json: bool
    model: str
    fallback_used: bool


def _default_completion(**kwargs: Any) -> Any:
    try:
        import litellm
    except ImportError as exc:
        raise ProviderError(
            "LiteLLM is not installed", hint="Install resume-tailor with its runtime dependencies."
        ) from exc
    litellm.suppress_debug_info = True
    logging.getLogger("LiteLLM").setLevel(logging.WARNING)
    return litellm.completion(**kwargs)


def _error_kind(exc: Exception) -> str:
    text = f"{type(exc).__name__} {exc}".lower()
    if "auth" in text or "401" in text or "permission" in text:
        return "authentication"
    if "model" in text and ("not found" in text or "invalid" in text):
        return "model"
    if "credit" in text or "insufficient" in text:
        return "credit"
    if "rate" in text or "429" in text:
        return "rate_limit"
    if "timeout" in text or "timed out" in text:
        return "timeout"
    if "server" in text or "502" in text or "503" in text:
        return "transient"
    return "provider"


class LLMClient:
    def __init__(
        self,
        provider: Provider,
        *,
        completion: Completion | None = None,
        timeout: float = 60.0,
        max_retries: int = 2,
        max_empty_retries: int = 1,
        temperature: float = 0.1,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if (
            timeout <= 0
            or max_retries < 0
            or max_empty_retries < 0
            or not 0 <= temperature <= 2
        ):
            raise ValueError("invalid LLM request policy")
        self.provider, self.completion, self.timeout = (
            provider,
            completion or _default_completion,
            timeout,
        )
        self.max_retries = max_retries
        self.max_empty_retries, self.temperature, self.sleeper = (
            max_empty_retries,
            temperature,
            sleeper,
        )
        self._telemetry = {
            "requests": 0,
            "empty_response_retries": 0,
            "schema_correction_retries": 0,
            "fallbacks": 0,
        }

    @property
    def telemetry(self) -> dict[str, int | float]:
        """Aggregate request metadata; deliberately excludes prompts and completions."""
        requests = self._telemetry["requests"]
        return {
            **self._telemetry,
            "empty_response_retry_rate": self._telemetry["empty_response_retries"] / requests
            if requests
            else 0.0,
            "schema_correction_retry_rate": self._telemetry["schema_correction_retries"]
            / requests
            if requests
            else 0.0,
            "fallback_rate": self._telemetry["fallbacks"] / requests if requests else 0.0,
        }

    def complete(
        self,
        *,
        model: str,
        messages: Sequence[Mapping[str, str]],
        api_key: str | None = None,
        api_base: str | None = None,
        response_schema: type[BaseModel] | None = None,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        self._telemetry["requests"] += 1
        models = (model, *self._fallback_models(model, response_schema=response_schema))
        last_error: ProviderError | None = None
        for model_index, candidate_model in enumerate(models):
            try:
                return self._complete_with_model(
                    model=candidate_model,
                    messages=messages,
                    api_key=api_key,
                    api_base=api_base,
                    response_schema=response_schema,
                    max_tokens=max_tokens,
                    fallback_used=model_index > 0,
                )
            except ProviderError as exc:
                last_error = exc
                if model_index == len(models) - 1:
                    raise
                self._telemetry["fallbacks"] += 1
                logger.warning(
                    "llm structured fallback model=%s next_model=%s reason=%s",
                    self.provider.model_name(candidate_model),
                    self.provider.model_name(models[model_index + 1]),
                    self._error_status(exc),
                )
        raise last_error or AssertionError("completion exhausted without a provider error")

    def _complete_with_model(
        self,
        *,
        model: str,
        messages: Sequence[Mapping[str, str]],
        api_key: str | None,
        api_base: str | None,
        response_schema: type[BaseModel] | None,
        max_tokens: int,
        fallback_used: bool,
    ) -> LLMResponse:
        capability = capabilities_for(self.provider)
        request_messages = [dict(message) for message in messages]
        kwargs: dict[str, Any] = {
            "model": self.provider.model_name(model),
            "messages": request_messages,
            "timeout": self.timeout,
            "temperature": self.temperature,
            "max_tokens": max_tokens,
        }
        if api_key:
            kwargs["api_key"] = api_key
        if api_base or self.provider.default_api_base:
            kwargs["api_base"] = api_base or self.provider.default_api_base
        if response_schema and capability.structured_output:
            kwargs["response_format"] = {"type": "json_object"}
        if response_schema and self._is_deepseek_v4(model):
            # V4 enables reasoning by default. It can consume max_tokens before it
            # emits the JSON object required by extraction.
            kwargs["thinking"] = {"type": "disabled"}

        attempt = 0
        empty_attempts = 0
        repaired_schema = False
        while attempt <= self.max_retries:
            raw: Any = None
            try:
                raw = self.completion(**kwargs)
                text = self._text(raw)
                if response_schema:
                    self._validate_schema(text, response_schema)
                self._log_response(raw, model=kwargs["model"], attempt=attempt, status="success")
                return LLMResponse(
                    text=text,
                    usage=self._usage(raw),
                    retries=attempt,
                    used_native_json="response_format" in kwargs,
                    model=kwargs["model"],
                    fallback_used=fallback_used,
                )
            except ProviderError as exc:
                if (
                    str(exc) == _EMPTY_RESPONSE
                    and empty_attempts < self.max_empty_retries
                ):
                    empty_attempts += 1
                    self._telemetry["empty_response_retries"] += 1
                    self._log_response(raw, model=kwargs["model"], attempt=attempt, status="empty")
                    attempt += 1
                    continue
                if str(exc) == _INVALID_SCHEMA and response_schema and not repaired_schema:
                    # Schema correction is intentionally separate from empty-response
                    # recovery: it adds a corrective prompt only when content exists.
                    repaired_schema = True
                    self._telemetry["schema_correction_retries"] += 1
                    attempt += 1
                    kwargs["messages"] = [
                        *request_messages,
                        {
                            "role": "user",
                            "content": (
                                "Your previous response did not validate. Return only one JSON "
                                "object that conforms exactly to this schema, with no Markdown: "
                                + json.dumps(response_schema.model_json_schema(), sort_keys=True)
                            ),
                        },
                    ]
                    continue
                self._log_response(
                    raw,
                    model=kwargs["model"],
                    attempt=attempt,
                    status=self._error_status(exc),
                )
                raise
            except Exception as exc:
                kind = _error_kind(exc)
                self._log_response(raw, model=kwargs["model"], attempt=attempt, status=kind)
                if (
                    kind not in {"rate_limit", "timeout", "transient"}
                    or attempt == self.max_retries
                ):
                    raise ProviderError(
                        f"provider {kind.replace('_', ' ')} error",
                        hint="Check credentials, model name, network, and provider status.",
                    ) from exc
                self.sleeper(min(2**attempt, 4))
            attempt += 1
        raise AssertionError("retry loop exhausted")

    def _fallback_models(
        self, model: str, *, response_schema: type[BaseModel] | None
    ) -> tuple[str, ...]:
        """Use the more capable V4 model only after structured extraction exhausts recovery."""
        normalized_model = model.strip().removeprefix("deepseek/")
        if (
            response_schema
            and self._is_deepseek_v4(model)
            and normalized_model != "deepseek-v4-pro"
        ):
            return ("deepseek-v4-pro",)
        return ()

    def _is_deepseek_v4(self, model: str) -> bool:
        normalized_model = model.strip().removeprefix("deepseek/")
        return self.provider.name == "deepseek" and normalized_model.startswith("deepseek-v4")

    @staticmethod
    def _error_status(exc: ProviderError) -> str:
        if str(exc) == _EMPTY_RESPONSE:
            return "empty"
        if str(exc) == _INVALID_SCHEMA:
            return "invalid_schema"
        return "provider_error"

    @staticmethod
    def _log_response(raw: Any, *, model: str, attempt: int, status: str) -> None:
        """Log response metadata only; CV/JD prompts and model text stay private."""
        choice = None
        with suppress(AttributeError, IndexError, KeyError, TypeError):
            choice = raw.choices[0]
        finish_reason = getattr(choice, "finish_reason", None)
        response_status = getattr(raw, "status_code", None) or getattr(raw, "status", None)
        logger.info(
            "llm response model=%s status=%s finish_reason=%s attempt=%d",
            model,
            response_status or status,
            finish_reason or "unknown",
            attempt + 1,
        )

    @staticmethod
    def _text(raw: Any) -> str:
        try:
            content = raw.choices[0].message.content
        except (AttributeError, IndexError, KeyError, TypeError) as exc:
            raise ProviderError("provider returned a malformed completion response") from exc
        if not isinstance(content, str) or not content.strip():
            raise ProviderError(_EMPTY_RESPONSE)
        return content

    @staticmethod
    def _usage(raw: Any) -> dict[str, int | float | None]:
        usage = getattr(raw, "usage", None) or {}

        def get_value(key: str) -> int | float | None:
            value = usage.get(key) if isinstance(usage, dict) else getattr(usage, key, None)
            return value if isinstance(value, (int, float)) else None

        return {
            "prompt_tokens": get_value("prompt_tokens"),
            "completion_tokens": get_value("completion_tokens"),
            "total_tokens": get_value("total_tokens"),
            "estimated_cost": getattr(raw, "_hidden_params", {}).get("response_cost")
            if hasattr(raw, "_hidden_params")
            else None,
        }

    @staticmethod
    def _validate_schema(text: str, schema: type[BaseModel]) -> None:
        try:
            schema.model_validate(json.loads(text))
        except (json.JSONDecodeError, PydanticValidationError) as exc:
            raise ProviderError(_INVALID_SCHEMA) from exc
