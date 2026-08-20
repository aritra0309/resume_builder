"""Stable word-level review diff rendering."""

from __future__ import annotations

from difflib import SequenceMatcher


def word_diff(source: str, proposed: str) -> str:
    source_words, proposed_words = source.split(), proposed.split()
    parts: list[str] = []
    for opcode, a0, a1, b0, b1 in SequenceMatcher(None, source_words, proposed_words).get_opcodes():
        if opcode in {"equal", "delete", "replace"} and source_words[a0:a1]:
            parts.append("- " + " ".join(source_words[a0:a1]))
        if opcode in {"insert", "replace"} and proposed_words[b0:b1]:
            parts.append("+ " + " ".join(proposed_words[b0:b1]))
    return "\n".join(parts)


def source_text(evidence_ids: list[str], evidence: dict[str, str]) -> str:
    """Return cited source wording in selected-ID order, never silently skipping IDs."""
    missing = [item_id for item_id in evidence_ids if item_id not in evidence]
    if missing:
        raise ValueError(f"unknown evidence IDs: {', '.join(sorted(set(missing)))}")
    return " ".join(evidence[item_id] for item_id in evidence_ids)
