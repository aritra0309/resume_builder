"""Signature-based, extension-validated source format detection."""

from __future__ import annotations

from pathlib import Path

from resume_tailor.errors import ValidationError
from resume_tailor.ingestion.docx import ingest_docx
from resume_tailor.ingestion.markdown import ingest_markdown
from resume_tailor.ingestion.pdf import ingest_pdf
from resume_tailor.models.ingestion import IngestionResult, SourceFormat


def detect_format(path: Path) -> SourceFormat:
    if not path.is_file():
        raise ValidationError(f"master CV does not exist or is not a file: {path}")
    head = path.read_bytes()[:8]
    suffix = path.suffix.lower()
    if head.startswith(b"%PDF-"):
        detected = SourceFormat.PDF
    elif head.startswith(b"PK\x03\x04"):
        detected = SourceFormat.DOCX
    else:
        detected = SourceFormat.MARKDOWN
    expected = {
        ".md": SourceFormat.MARKDOWN,
        ".markdown": SourceFormat.MARKDOWN,
        ".docx": SourceFormat.DOCX,
        ".pdf": SourceFormat.PDF,
    }.get(suffix)
    if expected is None:
        raise ValidationError("master CV must have a .md, .docx, or .pdf extension")
    if expected is not detected:
        raise ValidationError(f"file signature does not match its {suffix} extension")
    return detected


def ingest_file(path: Path) -> IngestionResult:
    detected = detect_format(path)
    if detected is SourceFormat.MARKDOWN:
        return ingest_markdown(path)
    if detected is SourceFormat.DOCX:
        return ingest_docx(path)
    return ingest_pdf(path)
