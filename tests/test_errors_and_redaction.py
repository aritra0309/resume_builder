from __future__ import annotations

from resume_tailor.errors import CredentialError, ExitCode, ResumeTailorError
from resume_tailor.redaction import redact


def test_error_exit_code_contract() -> None:
    assert ResumeTailorError("boom").exit_code is ExitCode.INTERNAL
    assert CredentialError("missing").exit_code is ExitCode.CREDENTIAL


def test_redaction_removes_known_and_pattern_secrets() -> None:
    text = "api_key=sk-abcdefghijk Authorization: Bearer token-value private-value"
    cleaned = redact(text, known_secrets=["private-value"])
    assert "abcdefghijk" not in cleaned
    assert "token-value" not in cleaned
    assert "private-value" not in cleaned
    assert cleaned.count("[REDACTED]") == 3
