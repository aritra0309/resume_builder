"""Validated job-description input selection and conservative normalization."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import TextIO

from resume_tailor.errors import UsageError, ValidationError
from resume_tailor.models.job import JobDescription

_MAX_BYTES = 500_000
_BOILERPLATE = re.compile(
    r"(?im)^\s*(?:privacy policy|terms of use|cookie policy|equal opportunity employer)\s*$"
)


def normalize_job_description(value: str) -> str:
    return "\n".join(
        " ".join(line.split()) for line in _BOILERPLATE.sub("", value).splitlines() if line.strip()
    )


def is_weak_job_description(value: str) -> bool:
    words = re.findall(r"\w+", value)
    has_signal = bool(
        re.search(r"(?i)responsibilit|requirement|qualification|experience|skill", value)
    )
    return len(words) < 40 or not has_signal


def read_multiline_paste(stream: TextIO, *, sentinel: str = "END") -> str:
    """Read an interactive paste until an explicit sentinel, without altering content."""

    lines: list[str] = []
    for line in stream:
        if line.rstrip("\r\n") == sentinel:
            return "".join(lines)
        lines.append(line)
    raise UsageError(f"multiline job-description paste must end with a line containing {sentinel}")


def read_job_description(
    *,
    jd: Path | None = None,
    jd_text: str | None = None,
    jd_stdin: bool = False,
    stdin: TextIO | None = None,
    allow_weak: bool = False,
) -> JobDescription:
    """Read exactly one JD source, preserving the original text for grounding."""

    selected = sum(item is not None and item is not False for item in (jd, jd_text, jd_stdin))
    if selected != 1:
        raise UsageError("provide exactly one of --jd, --jd-text, or --jd-stdin")
    if jd is not None:
        try:
            if not jd.is_file() or jd.stat().st_size > _MAX_BYTES:
                raise ValidationError(
                    "job-description file is missing, not a file, or exceeds the safety limit"
                )
            original = jd.read_text(encoding="utf-8")
        except OSError as exc:
            raise ValidationError(f"could not read job description: {jd}") from exc
    elif jd_text is not None:
        original = jd_text
    else:
        original = (stdin or sys.stdin).read(_MAX_BYTES + 1)
        if len(original.encode("utf-8")) > _MAX_BYTES:
            raise ValidationError("job description from standard input exceeds the safety limit")
    normalized = normalize_job_description(original)
    if not normalized:
        raise ValidationError("job description is empty")
    warnings: list[str] = []
    if is_weak_job_description(normalized):
        if not allow_weak:
            raise ValidationError(
                "JD is too short or lacks responsibilities; use --allow-weak-jd to continue"
            )
        warnings.append("Job description is weak; tailoring results may be unreliable.")
    return JobDescription(original_text=original, normalized_text=normalized, warnings=warnings)
