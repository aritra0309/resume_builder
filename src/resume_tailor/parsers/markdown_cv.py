"""Conservative Markdown CV parser with source-line preservation."""

from __future__ import annotations

import re
from pathlib import Path

from resume_tailor.errors import ValidationError
from resume_tailor.models.cv import CVBullet, CVDocument, CVEntry, CVSection, SourceLocation

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_BULLET = re.compile(r"^\s*[-*+]\s+(.+?)\s*$")
_CANONICAL = {
    "contact",
    "profile",
    "links",
    "summary",
    "education",
    "experience",
    "projects",
    "skills",
    "certifications",
    "achievements",
}
_MAX_BYTES = 2_000_000


def _classify(title: str, level: int) -> str:
    normalized = re.sub(r"[^a-z]+", " ", title.lower()).strip()
    if normalized in _CANONICAL:
        return normalized
    # Top-level portfolio headings are project entries even when the source has no Projects heading.
    return "projects" if level <= 2 else "unknown"


def _entry_from_lines(
    title: str, location: SourceLocation, lines: list[tuple[int, str]], source: str
) -> CVEntry:
    bullets: list[CVBullet] = []
    text: list[str] = []
    for line_no, line in lines:
        if bullet := _BULLET.match(line):
            bullets.append(
                CVBullet(
                    text=bullet.group(1).strip(),
                    source_location=SourceLocation(file=source, line=line_no),
                )
            )
        elif line.strip():
            text.append(line.strip())
    organization = None
    role = None
    date_range = None
    for value in text:
        plain = re.sub(r"[*_`]+", "", value)
        if date_range is None and re.search(
            r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{4}\b", plain
        ):
            date_range = plain
        if organization is None and ("—" in value or " - " in value):
            organization = plain.split("—", 1)[0].split(" - ", 1)[0].strip()
        if role is None and value.startswith("**"):
            role = plain
    return CVEntry(
        heading=title,
        organization=organization,
        role=role,
        date_range=date_range,
        bullets=bullets,
        text=text,
        source_location=location,
    )


def parse_markdown_cv(path: Path, *, max_bytes: int = _MAX_BYTES) -> CVDocument:
    """Parse a Markdown CV without silently dropping unknown content."""

    try:
        if not path.is_file():
            raise ValidationError(f"master CV does not exist or is not a file: {path}")
        if path.stat().st_size > max_bytes:
            raise ValidationError(f"master CV exceeds the {max_bytes}-byte safety limit")
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValidationError(f"could not read master CV: {path}") from exc
    if not raw.strip():
        raise ValidationError("master CV is empty")

    source = str(path)
    headings: list[tuple[int, int, str]] = []
    lines = raw.splitlines()
    for number, line in enumerate(lines, start=1):
        if matched := _HEADING.match(line):
            headings.append((number, len(matched.group(1)), matched.group(2)))
    if not headings:
        raise ValidationError("master CV has no Markdown headings and cannot be parsed safely")

    sections: list[CVSection] = []
    warnings: list[str] = []
    for index, (line_no, level, title) in enumerate(headings):
        end = headings[index + 1][0] - 1 if index + 1 < len(headings) else len(lines)
        content = list(enumerate(lines[line_no:end], start=line_no + 1))
        kind = _classify(title, level)
        if kind == "unknown":
            warnings.append(
                f"Unrecognized section '{title}' preserved as unknown (line {line_no})."
            )
        location = SourceLocation(file=source, line=line_no)
        entry = _entry_from_lines(title, location, content, source)
        sections.append(
            CVSection(name=kind, entries=[entry], source_location=location, level=level)
        )
    if not any(section.name in _CANONICAL or section.name == "projects" for section in sections):
        raise ValidationError("master CV has no recognizable resume sections")
    return CVDocument(source_file=source, sections=sections, warnings=warnings)
