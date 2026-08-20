"""Deterministic, deliberately lossless-ish normalization helpers."""

from __future__ import annotations

import re
import unicodedata

_WORD = re.compile(r"[A-Za-z][A-Za-z0-9+#./-]*")
_NUMBER = re.compile(r"(?<!\w)(?:\d+(?:\.\d+)?%?|\d{1,3}(?:,\d{3})+)(?!\w)")


def normalized_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value)
    value = value.replace("\u2013", "-").replace("\u2014", "-")
    return " ".join(value.lower().split())


def terms(value: str) -> list[str]:
    return sorted({word.lower() for word in _WORD.findall(value) if len(word) > 1})


def numbers(value: str) -> list[str]:
    return _NUMBER.findall(value)
