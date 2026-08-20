"""Bounded, deterministic page-fit selection; compilation is orchestrated by Phase 7."""

from __future__ import annotations

from resume_tailor.models.generation import ContentPlan, PlannedEntry, PlannedSection


def reduce_for_page_fit(
    plan: ContentPlan, *, iteration: int, max_iterations: int = 2
) -> ContentPlan:
    if not 0 <= iteration < max_iterations:
        raise ValueError("page-fit iteration is outside the configured bound")
    sections: list[PlannedSection] = []
    removed = 0
    for section in reversed(plan.sections):
        entries: list[PlannedEntry] = []
        for entry in reversed(section.entries):
            bullets = entry.bullets
            if removed <= iteration and len(bullets) > 1:
                bullets = bullets[:-1]
                removed += 1
            entries.append(entry.model_copy(update={"bullets": bullets}))
        sections.append(section.model_copy(update={"entries": list(reversed(entries))}))
    return plan.model_copy(update={"sections": list(reversed(sections))})
