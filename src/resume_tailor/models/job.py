"""Validated job-description and analysis contracts."""

from __future__ import annotations

from pydantic import Field, model_validator

from resume_tailor.models.cv import StrictModel


class JobKeyword(StrictModel):
    term: str = Field(min_length=1)
    importance: float = Field(ge=0, le=1)
    source_quote: str = Field(min_length=1)


class JobAnalysis(StrictModel):
    """The provider-produced portion of a job description.

    The original and normalized JD are local inputs, so making a provider echo
    them only creates avoidable structured-output failures and privacy risk.
    """

    role_title: str | None = None
    seniority: str | None = None
    required_skills: list[str] = Field(default_factory=list)
    preferred_skills: list[str] = Field(default_factory=list)
    responsibilities: list[str] = Field(default_factory=list)
    domain_terms: list[str] = Field(default_factory=list)
    education_requirements: list[str] = Field(default_factory=list)
    keywords: list[JobKeyword] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class JobDescription(JobAnalysis):
    original_text: str = Field(min_length=1)
    normalized_text: str = Field(min_length=1)

    @model_validator(mode="after")
    def quotes_must_be_grounded(self) -> JobDescription:
        for keyword in self.keywords:
            if keyword.source_quote not in self.original_text:
                raise ValueError("keyword source_quote must occur in original_text")
        return self
