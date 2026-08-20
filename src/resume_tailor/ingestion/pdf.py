"""Conservative text-PDF ingestion; never executes PDF actions."""

from __future__ import annotations

import hashlib
from pathlib import Path

from pypdf import PdfReader

from resume_tailor.errors import ValidationError
from resume_tailor.models.cv import CVBullet, CVDocument, CVEntry, CVSection, SourceLocation
from resume_tailor.models.ingestion import (
    IngestionResult,
    IngestionStatistics,
    IngestionWarning,
    SourceFormat,
)

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


def ingest_pdf(path: Path, *, max_pages: int = 10, max_bytes: int = 20_000_000) -> IngestionResult:
    raw = path.read_bytes()
    if len(raw) > max_bytes:
        raise ValidationError("PDF exceeds safety size limit")
    try:
        reader = PdfReader(path)
    except Exception as exc:
        raise ValidationError("PDF is corrupt or unsupported") from exc
    if reader.is_encrypted:
        raise ValidationError("encrypted PDFs are unsupported; export DOCX or Markdown instead")
    if len(reader.pages) > max_pages:
        raise ValidationError("PDF exceeds page safety limit")
    source = str(path)
    sections = []
    warnings = []
    current = None
    characters = 0
    for page_i, page in enumerate(reader.pages, 1):
        try:
            text = page.extract_text() or ""
        except Exception as exc:
            raise ValidationError("PDF text extraction failed") from exc
        characters += len(text)
        for line_i, raw_line in enumerate(text.splitlines(), 1):
            line = raw_line.strip()
            if not line:
                continue
            loc = SourceLocation(
                file=source,
                format="pdf",
                page=page_i,
                locator=f"p:{page_i}/l:{line_i}",
                label=f"page {page_i}, line {line_i}",
            )
            name = " ".join(line.lower().rstrip(":").split())
            if name in _CANONICAL:
                current = CVSection(
                    name=name,
                    entries=[CVEntry(heading=line, source_location=loc)],
                    source_location=loc,
                    level=1,
                )
                sections.append(current)
                continue
            if current is None:
                current = CVSection(
                    name="unknown",
                    entries=[CVEntry(heading="Uncategorized", source_location=loc)],
                    source_location=loc,
                    level=1,
                )
                sections.append(current)
            if line.startswith(("• ", "- ", "* ")):
                current.entries[0].bullets.append(
                    CVBullet(text=line[2:].strip(), source_location=loc)
                )
            else:
                current.entries[0].text.append(line)
    if not characters or not sections:
        raise ValidationError(
            "PDF contains no extractable text; export DOCX/Markdown or use OCR "
            "outside Resume Tailor"
        )
    if any(section.name == "unknown" for section in sections):
        warnings.append(
            IngestionWarning(
                code="ambiguous_layout",
                message="PDF content without recognizable headings requires review.",
                requires_acknowledgement=True,
            )
        )
    document = CVDocument(
        source_file=source, sections=sections, warnings=[w.message for w in warnings]
    )
    return IngestionResult(
        source_format=SourceFormat.PDF,
        document=document,
        warnings=warnings,
        statistics=IngestionStatistics(pages=len(reader.pages), characters=characters),
        source_hash=f"sha256:{hashlib.sha256(raw).hexdigest()}",
    )
