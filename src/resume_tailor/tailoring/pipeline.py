"""One-repair content plan workflow; unsafe claims never proceed downstream."""

from __future__ import annotations

from collections.abc import Callable

from resume_tailor.errors import GroundingError
from resume_tailor.models.cv import EvidenceLedger
from resume_tailor.models.generation import ContentPlan
from resume_tailor.tailoring.content_plan import (
    ClaimValidation,
    fallback_text,
    validate_content_plan,
)

Verifier = Callable[[str, list[str]], str]
Repair = Callable[[ContentPlan, list[ClaimValidation]], ContentPlan]


def verify_and_repair(
    plan: ContentPlan,
    ledger: EvidenceLedger,
    *,
    verifier: Verifier | None = None,
    repair: Repair | None = None,
) -> ContentPlan:
    """Use at most one repair; deterministic failures always block output."""
    current = plan
    for attempt in range(2):
        checks = validate_content_plan(current, ledger)
        deterministic_failure = any(check.issues for check in checks)
        verifier_failure = verifier is not None and any(
            verifier(check.text, check.evidence_ids) != "entailed" for check in checks
        )
        if not deterministic_failure and not verifier_failure:
            return current
        if attempt == 0 and repair is not None:
            current = repair(current, checks)
            continue
        fallback = _fallback_plan(current, checks, ledger, fallback_all=verifier_failure)
        if not any(check.issues for check in validate_content_plan(fallback, ledger)):
            return fallback
        raise GroundingError("generated content contains unsupported claims")
    raise AssertionError("unreachable")


def _fallback_plan(
    plan: ContentPlan,
    checks: list[ClaimValidation],
    ledger: EvidenceLedger,
    *,
    fallback_all: bool = False,
) -> ContentPlan:
    """Replace failed claims with their cited original wording, preserving section structure."""

    replacements = {
        (check.text, tuple(check.evidence_ids)): fallback_text(check.evidence_ids, ledger)
        for check in checks
        if check.issues or fallback_all
    }

    def replacement(text: str, evidence_ids: list[str]) -> str:
        return replacements.get((text, tuple(evidence_ids)), text)

    summary = plan.summary.model_copy(
        update={"text": replacement(plan.summary.text, plan.summary.evidence_ids)}
    )
    sections = []
    for section in plan.sections:
        entries = []
        for entry in section.entries:
            bullets = [
                bullet.model_copy(update={"text": replacement(bullet.text, bullet.evidence_ids)})
                for bullet in entry.bullets
            ]
            entries.append(entry.model_copy(update={"bullets": bullets}))
        sections.append(section.model_copy(update={"entries": entries}))
    return plan.model_copy(update={"summary": summary, "sections": sections})
