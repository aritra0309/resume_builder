"""Deterministic extraction and evidence matching before any model call."""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from resume_tailor.evidence.normalizer import normalized_text
from resume_tailor.models.cv import EvidenceLedger
from resume_tailor.models.job import JobDescription

_WORD = re.compile(r"[A-Za-z][A-Za-z0-9+#./-]*")
_PHRASE = re.compile(r"\b(?:[A-Za-z][A-Za-z0-9+#./-]*\s+){1,3}[A-Za-z][A-Za-z0-9+#./-]*\b")
_ACRONYM = re.compile(r"\b[A-Z][A-Z0-9]{1,9}\b")
_MEASURABLE = re.compile(r"\b\d+(?:\.\d+)?(?:%|\+)?\b")
_STOP = {"and", "the", "with", "for", "you", "our", "will", "that", "this", "are", "from"}


@dataclass(frozen=True, slots=True)
class TermMatch:
    term: str
    classification: str
    evidence_ids: tuple[str, ...]
    score: float
    kind: str


def _terms(text: str) -> set[str]:
    tokens = [word.lower() for word in _WORD.findall(text)]
    words = {word for word in tokens if len(word) > 1 and word not in _STOP}
    phrases = {
        " ".join(tokens[index : index + width])
        for width in (2, 3)
        for index in range(len(tokens) - width + 1)
        if all(part not in _STOP for part in tokens[index : index + width])
    }
    return words | phrases | {value.lower() for value in _ACRONYM.findall(text)}


def _similarity(term: str, evidence: str) -> float:
    candidate_words = normalized_text(term).split()
    evidence_words = normalized_text(evidence).split()
    if not candidate_words or not evidence_words:
        return 0.0
    return max(
        SequenceMatcher(None, word, other).ratio()
        for word in candidate_words
        for other in evidence_words
    )


def analyze_terms(job: JobDescription, ledger: EvidenceLedger) -> list[TermMatch]:
    """Extract ranked exact, acronym, fuzzy, and unsupported JD terms."""

    requested = _terms(job.normalized_text)
    result: list[TermMatch] = []
    for term in requested:
        exact = tuple(
            item.id for item in ledger.evidence if normalized_text(term) in item.normalized_text
        )
        if exact:
            result.append(TermMatch(term, "supported", exact, 1.0, "exact"))
            continue
        scored = sorted(
            ((_similarity(term, item.text), item.id) for item in ledger.evidence), reverse=True
        )
        score = scored[0][0] if scored else 0.0
        close = tuple(item_id for value, item_id in scored if value >= 0.82)
        classification = "possibly_supported" if close else "unsupported"
        result.append(TermMatch(term, classification, close, score, "fuzzy"))
    result.sort(key=lambda match: (match.classification != "supported", -match.score, match.term))
    return result


def measurable_requirements(job: JobDescription) -> list[str]:
    return sorted(set(_MEASURABLE.findall(job.normalized_text)))
