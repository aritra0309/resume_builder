"""Explain ATS-relevant coverage without claiming an ATS score."""

from __future__ import annotations

import re

from resume_tailor.models.generation import ContentPlan
from resume_tailor.models.job import JobDescription
from resume_tailor.models.validation import ValidationIssue, ValidationReport

_WORD = re.compile(r"[a-z0-9][a-z0-9+.#/-]*", re.IGNORECASE)
_STANDARD = {"summary", "experience", "education", "skills", "projects", "certifications"}


def _plan_text(plan: ContentPlan) -> str:
    return " ".join(
        [
            plan.target_title,
            plan.summary.text,
            *[section.name for section in plan.sections],
            *[entry.heading for section in plan.sections for entry in section.entries],
            *[
                bullet.text
                for section in plan.sections
                for entry in section.entries
                for bullet in entry.bullets
            ],
        ]
    ).casefold()


def validate_ats(plan: ContentPlan, job: JobDescription) -> ValidationReport:
    text = _plan_text(plan)
    issues: list[ValidationIssue] = []
    headings = {section.name.casefold() for section in plan.sections}
    if not headings & _STANDARD:
        issues.append(
            ValidationIssue(
                code="ats_headings",
                message="Resume does not use a standard ATS section heading.",
                severity="warning",
            )
        )
    supported_terms = []
    absent_terms = []
    for keyword in job.keywords:
        term = keyword.term.casefold()
        if term in text:
            supported_terms.append(term)
        else:
            absent_terms.append(term)
    if absent_terms:
        issues.append(
            ValidationIssue(
                code="supported_keywords_absent",
                message=f"Supported JD terms not used: {', '.join(sorted(set(absent_terms)))}.",
                severity="warning",
            )
        )
    unsupported = sorted(set(plan.unsupported_jd_requirements))
    if unsupported:
        issues.append(
            ValidationIssue(
                code="unsupported_jd_requirements",
                message=f"Not claimed because source evidence is absent: {', '.join(unsupported)}.",
                severity="info",
            )
        )
    if not supported_terms and job.keywords:
        issues.append(
            ValidationIssue(
                code="keyword_coverage",
                message="No supported JD keywords appear in the selected resume content.",
                severity="warning",
            )
        )
    mappings = {"summary": plan.summary.evidence_ids}
    mappings.update(
        {
            f"{section.name}/{entry.heading}/{index + 1}": bullet.evidence_ids
            for section in plan.sections
            for entry in section.entries
            for index, bullet in enumerate(entry.bullets)
        }
    )
    return ValidationReport(passed=True, issues=issues, evidence_mappings=mappings)
