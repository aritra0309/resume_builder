"""Models for source CVs and their traceable evidence."""

from __future__ import annotations

import re
from datetime import date

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_EVIDENCE_ID = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class SourceLocation(StrictModel):
    """A format-neutral, non-content-bearing pointer into an input document.

    ``line`` is retained for compatibility with v1 Markdown artifacts.  The
    other fields are populated for every location so reports need not assume a
    line-oriented source.
    """

    file: str = Field(min_length=1)
    format: str = Field(default="markdown", min_length=1)
    locator: str | None = Field(default=None, min_length=1)
    label: str | None = Field(default=None, min_length=1)
    line: int | None = Field(default=None, ge=1)
    page: int | None = Field(default=None, ge=1)
    paragraph: int | None = Field(default=None, ge=1)
    part: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def populate_legacy_defaults(self) -> SourceLocation:
        # Old run files contain only ``file`` and ``line``.  Preserve that
        # representation as a Markdown anchor while upgrading it in memory.
        if self.format == "markdown":
            if self.line is None:
                raise ValueError("Markdown source locations require a line number")
            if self.locator is None:
                self.locator = f"line:{self.line}"
            if self.label is None:
                self.label = f"line {self.line}"
        elif self.format == "docx":
            if self.paragraph is None and self.locator is None:
                raise ValueError("DOCX source locations require a paragraph or locator")
            if self.locator is None:
                self.locator = f"{self.part or 'body'}/p:{self.paragraph}"
            if self.label is None:
                self.label = f"paragraph {self.paragraph}" if self.paragraph else self.locator
        elif self.format == "pdf":
            if self.page is None and self.locator is None:
                raise ValueError("PDF source locations require a page or locator")
            if self.locator is None:
                self.locator = f"page:{self.page}"
            if self.label is None:
                self.label = f"page {self.page}" if self.page else self.locator
        elif self.locator is None or self.label is None:
            raise ValueError("source locations require locator and label")
        return self

    def report_label(self) -> str:
        """Return the concise human-facing form used in reports."""
        if self.line is not None:
            return f"{self.file}:{self.line}"
        return f"{self.file}:{self.label}"


class CVBullet(StrictModel):
    text: str = Field(min_length=1)
    source_location: SourceLocation


class CVEntry(StrictModel):
    heading: str = Field(min_length=1)
    organization: str | None = None
    role: str | None = None
    date_range: str | None = None
    bullets: list[CVBullet] = Field(default_factory=list)
    text: list[str] = Field(default_factory=list)
    source_location: SourceLocation


class CVSection(StrictModel):
    name: str = Field(min_length=1)
    entries: list[CVEntry] = Field(default_factory=list)
    source_location: SourceLocation
    level: int = Field(ge=1, le=6)


class CVDocument(StrictModel):
    source_file: str = Field(min_length=1)
    sections: list[CVSection] = Field(min_length=1)
    warnings: list[str] = Field(default_factory=list)


class Person(StrictModel):
    name: str | None = None
    email: str | None = None
    phone: str | None = None
    links: list[str] = Field(default_factory=list)


class EvidenceItem(StrictModel):
    id: str = Field(min_length=3)
    section: str = Field(min_length=1)
    text: str = Field(min_length=1)
    normalized_text: str = Field(min_length=1)
    organization: str | None = None
    date_range: str | None = None
    normalized_dates: tuple[date | None, date | None] | None = None
    entities: list[str] = Field(default_factory=list)
    numbers: list[str] = Field(default_factory=list)
    source_location: SourceLocation

    @field_validator("id")
    @classmethod
    def valid_id(cls, value: str) -> str:
        if not _EVIDENCE_ID.fullmatch(value):
            raise ValueError("must be a lowercase dot, hyphen, or underscore-separated identifier")
        return value


class EvidenceLedger(StrictModel):
    person: Person = Field(default_factory=Person)
    evidence: list[EvidenceItem] = Field(min_length=1)
