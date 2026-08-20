"""Best-effort redaction for logs and user-visible error output."""

from __future__ import annotations

import re
from collections.abc import Iterable

_AUTHORIZATION = re.compile(r"(?i)(authorization\s*[:=]\s*(?:bearer\s+)?)[^\s,;]+")
_KEY_ASSIGNMENT = re.compile(
    r"(?i)((?:api[_-]?key|token|secret|password)\s*[:=]\s*)['\"]?[^\s,'\";]+"
)
_KNOWN_KEY = re.compile(r"\b(?:sk|key)-[A-Za-z0-9_-]{8,}\b")


def redact(text: object, *, known_secrets: Iterable[str] = ()) -> str:
    """Return text with known and common credential patterns removed."""

    value = str(text)
    for secret in known_secrets:
        if secret:
            value = value.replace(secret, "[REDACTED]")
    value = _AUTHORIZATION.sub(r"\1[REDACTED]", value)
    value = _KEY_ASSIGNMENT.sub(r"\1[REDACTED]", value)
    return _KNOWN_KEY.sub("[REDACTED]", value)
