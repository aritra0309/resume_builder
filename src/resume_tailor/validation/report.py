"""Serialize validation findings with source-line evidence traceability."""

from __future__ import annotations

from resume_tailor.models.cv import EvidenceLedger
from resume_tailor.models.validation import ValidationReport


def report_json(report: ValidationReport, ledger: EvidenceLedger) -> dict[str, object]:
    evidence = {item.id: item for item in ledger.evidence}
    mappings: dict[str, object] = {}
    for claim, ids in report.evidence_mappings.items():
        mappings[claim] = {
            "evidence_ids": ids,
            "sources": [
                {
                    "id": item_id,
                    "file": evidence[item_id].source_location.file,
                    "line": evidence[item_id].source_location.line,
                    "locator": evidence[item_id].source_location.locator,
                    "label": evidence[item_id].source_location.label,
                }
                for item_id in ids
                if item_id in evidence
            ],
        }
    return {
        "passed": report.passed,
        "issues": [issue.model_dump() for issue in report.issues],
        "evidence_mappings": mappings,
    }


def report_markdown(report: ValidationReport, ledger: EvidenceLedger) -> str:
    status = "passed" if report.passed else "failed"
    lines = ["# Validation report", "", f"**Status:** {status}", ""]
    lines.extend(["## Findings", ""])
    if report.issues:
        lines.extend(
            f"- **{issue.severity}** `{issue.code}` — {issue.message}" for issue in report.issues
        )
    else:
        lines.append("- No findings.")
    lines.extend(["", "## Evidence mappings", ""])
    evidence = {item.id: item for item in ledger.evidence}
    for claim, ids in report.evidence_mappings.items():
        sources = ", ".join(
            f"{evidence[item_id].source_location.report_label()} ({item_id})"
            for item_id in ids
            if item_id in evidence
        )
        lines.append(f"- `{claim}`: {sources or 'no source found'}")
    return "\n".join(lines) + "\n"
