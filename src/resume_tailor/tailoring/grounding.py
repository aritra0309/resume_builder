"""Deterministic claim checks that cannot be overruled by an LLM verifier."""

from __future__ import annotations

import re
from dataclasses import dataclass

from resume_tailor.evidence.normalizer import numbers
from resume_tailor.models.cv import EvidenceLedger

_CAPITALIZED = re.compile(r"\b(?:[A-Z][A-Za-z0-9+#.-]*)(?:\s+[A-Z][A-Za-z0-9+#.-]*)*\b")
_DATE = re.compile(r"\b(?:19|20)\d{2}\b")
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9+#./-]*")
# Keep this deliberately finite: it prevents a manual edit from swapping a
# concrete technology while avoiding guesses based on ordinary lower-case words.
_TECHNOLOGIES = {
    "aws",
    "azure",
    "c",
    "c++",
    "c#",
    "docker",
    "fastapi",
    "flask",
    "gcp",
    "git",
    "go",
    "java",
    "javascript",
    "kafka",
    "kubernetes",
    "matlab",
    "mongodb",
    "mysql",
    "numpy",
    "pandas",
    "postgresql",
    "pytorch",
    "python",
    "r",
    "react",
    "ruby",
    "scala",
    "spark",
    "sql",
    "tableau",
    "tensorflow",
    "typescript",
}


@dataclass(frozen=True, slots=True)
class GroundingIssue:
    code: str
    message: str


def check_claim(text: str, evidence_ids: list[str], ledger: EvidenceLedger) -> list[GroundingIssue]:
    indexed = {item.id: item for item in ledger.evidence}
    missing = sorted(set(evidence_ids) - set(indexed))
    if missing:
        return [GroundingIssue("unknown_evidence", f"unknown evidence IDs: {', '.join(missing)}")]
    source = " ".join(indexed[item_id].text for item_id in evidence_ids)
    source_normalized = source.lower()
    issues: list[GroundingIssue] = []
    for value in numbers(text) + _DATE.findall(text):
        if value not in source:
            issues.append(
                GroundingIssue("protected_number_or_date", f"unsupported number or date: {value}")
            )
    for entity in _CAPITALIZED.findall(text):
        if len(entity) > 2 and entity.lower() not in source_normalized:
            issues.append(GroundingIssue("protected_entity", f"unsupported named entity: {entity}"))
    cited_technologies = {word.lower() for word in _WORD.findall(source)} & _TECHNOLOGIES
    claim_technologies = {word.lower() for word in _WORD.findall(text)} & _TECHNOLOGIES
    unsupported_technologies = sorted(claim_technologies - cited_technologies)
    if unsupported_technologies:
        issues.append(
            GroundingIssue(
                "protected_technology",
                "unsupported technology: " + ", ".join(unsupported_technologies),
            )
        )
    claim_terms = {word.lower() for word in _WORD.findall(text) if len(word) > 2}
    source_terms = {word.lower() for word in _WORD.findall(source) if len(word) > 2}
    if claim_terms and not claim_terms.intersection(source_terms):
        issues.append(
            GroundingIssue("low_lexical_support", "claim has no lexical support in cited evidence")
        )
    return issues
