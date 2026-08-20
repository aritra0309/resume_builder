"""Capability policy: JSON mode is an optimization, never an assumption."""

from __future__ import annotations

from dataclasses import dataclass

from resume_tailor.llm.providers import Provider


@dataclass(frozen=True, slots=True)
class CapabilityResult:
    chat_completion: bool
    structured_output: bool
    strategy: str


def capabilities_for(
    provider: Provider, *, json_mode_confirmed: bool | None = None
) -> CapabilityResult:
    structured = provider.supports_json_mode if json_mode_confirmed is None else json_mode_confirmed
    return CapabilityResult(
        True, structured, "native_json" if structured else "prompt_enforced_json"
    )
