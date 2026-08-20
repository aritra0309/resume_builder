"""Machine-readable local validation result contract."""

from __future__ import annotations

from pydantic import Field

from resume_tailor.models.cv import StrictModel


class ValidationIssue(StrictModel):
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    severity: str = Field(pattern=r"^(info|warning|error)$")


class ValidationReport(StrictModel):
    passed: bool
    issues: list[ValidationIssue] = Field(default_factory=list)
    evidence_mappings: dict[str, list[str]] = Field(default_factory=dict)
