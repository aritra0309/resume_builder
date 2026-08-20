"""Validated, serializable contracts at the document-ingestion boundary."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from resume_tailor.models.cv import CVDocument, StrictModel


class SourceFormat(StrEnum):
    MARKDOWN = "markdown"
    DOCX = "docx"
    PDF = "pdf"


class IngestionWarning(StrictModel):
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    requires_acknowledgement: bool = False


class IngestionStatistics(StrictModel):
    pages: int | None = Field(default=None, ge=0)
    paragraphs: int | None = Field(default=None, ge=0)
    tables: int | None = Field(default=None, ge=0)
    characters: int = Field(ge=0)


class IngestionResult(StrictModel):
    schema_version: int = Field(default=1, ge=1)
    source_format: SourceFormat
    document: CVDocument
    warnings: list[IngestionWarning] = Field(default_factory=list)
    statistics: IngestionStatistics
    source_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
