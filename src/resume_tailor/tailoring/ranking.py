"""Deterministic evidence relevance and page-budget selection."""

from __future__ import annotations

from collections import defaultdict

from resume_tailor.models.cv import EvidenceItem, EvidenceLedger
from resume_tailor.models.generation import ContentPlan
from resume_tailor.tailoring.requirements import TermMatch

_PAGE_BUDGETS = {"1": 12, "2": 28, "auto": 20}
_SECTION_WEIGHT = {"experience": 1.25, "projects": 1.1}


def rank_evidence(ledger: EvidenceLedger, matches: list[TermMatch]) -> list[EvidenceItem]:
    scores: dict[str, float] = defaultdict(float)
    for match in matches:
        if match.classification != "unsupported":
            for item_id in match.evidence_ids:
                scores[item_id] += match.score
    return sorted(
        ledger.evidence,
        key=lambda item: (-scores[item.id] * _SECTION_WEIGHT.get(item.section, 1), item.id),
    )


def select_content_plan(plan: ContentPlan, *, pages: str) -> ContentPlan:
    if pages not in _PAGE_BUDGETS:
        raise ValueError("pages must be 1, 2, or auto")
    budget = _PAGE_BUDGETS[pages]
    selected = 0
    sections = []
    for section in plan.sections:
        entries = []
        for entry in section.entries:
            remaining = max(budget - selected, 0)
            bullets = entry.bullets[:remaining]
            selected += len(bullets)
            if bullets:
                entries.append(entry.model_copy(update={"bullets": bullets}))
        if entries:
            sections.append(section.model_copy(update={"entries": entries}))
    return plan.model_copy(update={"sections": sections})
