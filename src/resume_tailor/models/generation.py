"""Contracts for grounded generated content and non-secret run metadata."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator

from resume_tailor.models.cv import StrictModel


class GroundedText(StrictModel):
    text: str = Field(min_length=1)
    evidence_ids: list[str] = Field(min_length=1)


class PlannedBullet(GroundedText):
    matched_keywords: list[str] = Field(default_factory=list)


class PlannedEntry(StrictModel):
    heading: str = Field(min_length=1)
    bullets: list[PlannedBullet] = Field(min_length=1)


class PlannedSection(StrictModel):
    name: str = Field(min_length=1)
    entries: list[PlannedEntry] = Field(min_length=1)


class ContentPlan(StrictModel):
    target_title: str = Field(min_length=1)
    summary: GroundedText
    sections: list[PlannedSection] = Field(min_length=1)
    omitted_evidence_ids: list[str] = Field(default_factory=list)
    unsupported_jd_requirements: list[str] = Field(default_factory=list)

    def validate_evidence(self, valid_ids: set[str]) -> None:
        cited = set(self.summary.evidence_ids)
        cited.update(
            item
            for section in self.sections
            for entry in section.entries
            for bullet in entry.bullets
            for item in bullet.evidence_ids
        )
        unknown = sorted(cited - valid_ids)
        if unknown:
            raise ValueError(f"content plan cites unknown evidence IDs: {', '.join(unknown)}")


class PageRecommendation(StrictModel):
    """A model recommendation used only when the user selected automatic length."""

    pages: Literal[1, 2]
    reason: str = Field(min_length=1, max_length=240)


class TokenUsage(StrictModel):
    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    estimated_cost: float | None = Field(default=None, ge=0)


class RunManifest(StrictModel):
    schema_version: int = Field(default=1, ge=1)
    run_id: UUID
    created_at: datetime
    input_hashes: dict[str, str] = Field(min_length=1)
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    litellm_version: str | None = None
    prompt_versions: dict[str, str] = Field(default_factory=dict)
    compiler: str | None = None
    compiler_version: str | None = None
    output_paths: list[str] = Field(default_factory=list)
    validation_passed: bool
    usage: TokenUsage = Field(default_factory=TokenUsage)
    retry_count: int = Field(default=0, ge=0)
    temperature: float = Field(ge=0, le=2)
    source_format: str = Field(default="markdown", min_length=1)
    ingestion_schema_version: int = Field(default=1, ge=1)
    review_policy: str = Field(default="disabled", min_length=1)
    review_id: UUID | None = None
    review_revision: int | None = Field(default=None, ge=0)
    final_plan_hash: str | None = None
    decision_counts: dict[str, int] = Field(default_factory=dict)
    is_draft_resume: bool = False
    target_pages: int | None = Field(default=None, ge=1, le=2)
    page_target_reason: str | None = Field(default=None, max_length=240)
    page_fit_revisions: int = Field(default=0, ge=0)

    @field_validator("input_hashes")
    @classmethod
    def hashes_are_nonempty(cls, value: dict[str, str]) -> dict[str, str]:
        if any(not path or not digest for path, digest in value.items()):
            raise ValueError("input hashes must have non-empty paths and digests")
        return value


def migrate_run_manifest(payload: dict[str, object]) -> dict[str, object]:
    """Upgrade a pre-v1.1 manifest payload without changing its meaning."""
    migrated = dict(payload)
    migrated.setdefault("schema_version", 1)
    migrated.setdefault("source_format", "markdown")
    migrated.setdefault("ingestion_schema_version", 1)
    migrated.setdefault("review_policy", "disabled")
    migrated.setdefault("decision_counts", {})
    migrated.setdefault("is_draft_resume", False)
    migrated.setdefault("target_pages", None)
    migrated.setdefault("page_target_reason", None)
    migrated.setdefault("page_fit_revisions", 0)
    return migrated
