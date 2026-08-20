"""Offline-capable model discovery with a credential-free cache."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol

from platformdirs import user_cache_dir

from resume_tailor.llm.providers import Provider


class ModelLister(Protocol):
    def __call__(self, provider: Provider) -> list[str]: ...


def cache_path(provider: Provider, *, directory: Path | None = None) -> Path:
    root = directory or Path(user_cache_dir("resume-tailor", "Aritra Sarkar"))
    return root / "models" / f"{provider.name}.json"


def load_cached_models(
    provider: Provider,
    *,
    directory: Path | None = None,
    max_age: timedelta = timedelta(days=7),
    now: datetime | None = None,
) -> list[str] | None:
    path = cache_path(provider, directory=directory)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        fetched_at = datetime.fromisoformat(data["fetched_at"])
        models = data["models"]
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None
    current = now or datetime.now(UTC)
    if (
        fetched_at.tzinfo is None
        or current - fetched_at > max_age
        or not all(isinstance(item, str) and item for item in models)
    ):
        return None
    return sorted(set(models))


def cache_models(
    provider: Provider,
    models: list[str],
    *,
    directory: Path | None = None,
    now: datetime | None = None,
) -> None:
    path = cache_path(provider, directory=directory)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"fetched_at": (now or datetime.now(UTC)).isoformat(), "models": sorted(set(models))}
    path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")


def discover_models(
    provider: Provider, *, lister: ModelLister | None = None, cache_directory: Path | None = None
) -> tuple[list[str], str]:
    """Return cache, live, or curated models without making offline use fail."""
    if cached := load_cached_models(provider, directory=cache_directory):
        return cached, "cache"
    if provider.supports_live_listing and lister is not None:
        try:
            live = sorted(set(model for model in lister(provider) if model.strip()))
        except Exception:
            live = []
        if live:
            cache_models(provider, live, directory=cache_directory)
            return live, "live"
    return list(provider.default_models), "curated"
