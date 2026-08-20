"""Validate generated plans and retain safe original-wording fallbacks."""

from __future__ import annotations

from dataclasses import dataclass

from resume_tailor.models.cv import EvidenceLedger
from resume_tailor.models.generation import ContentPlan, GroundedText, PlannedBullet
from resume_tailor.tailoring.grounding import GroundingIssue, check_claim


@dataclass(frozen=True, slots=True)
class ClaimValidation:
    text: str
    evidence_ids: list[str]
    issues: tuple[GroundingIssue, ...]


def _claims(plan: ContentPlan) -> list[GroundedText | PlannedBullet]:
    return [plan.summary] + [
        bullet for section in plan.sections for entry in section.entries for bullet in entry.bullets
    ]


def validate_content_plan(plan: ContentPlan, ledger: EvidenceLedger) -> list[ClaimValidation]:
    valid_ids = {item.id for item in ledger.evidence}
    plan.validate_evidence(valid_ids)
    return [
        ClaimValidation(
            claim.text,
            claim.evidence_ids,
            tuple(check_claim(claim.text, claim.evidence_ids, ledger)),
        )
        for claim in _claims(plan)
    ]


def fallback_text(evidence_ids: list[str], ledger: EvidenceLedger) -> str:
    indexed = {item.id: item for item in ledger.evidence}
    return " ".join(indexed[item_id].text for item_id in evidence_ids if item_id in indexed)
