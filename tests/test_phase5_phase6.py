from __future__ import annotations

from pathlib import Path

import pytest
from pypdf import PdfWriter

from resume_tailor.errors import LatexError
from resume_tailor.latex.compiler import discover_engines, select_engine
from resume_tailor.latex.generator import fallback_render, latex_generation_messages
from resume_tailor.latex.sanitizer import sanitize_latex, validate_latex_against_plan
from resume_tailor.models.generation import (
    ContentPlan,
    GroundedText,
    PlannedBullet,
    PlannedEntry,
    PlannedSection,
)
from resume_tailor.models.job import JobDescription, JobKeyword
from resume_tailor.validation.ats import validate_ats
from resume_tailor.validation.fitting import reduce_for_page_fit
from resume_tailor.validation.pdf import validate_pdf


@pytest.fixture
def plan() -> ContentPlan:
    return ContentPlan(
        target_title="Data Scientist",
        summary=GroundedText(text="Grounded summary.", evidence_ids=["project.example.1"]),
        sections=[
            PlannedSection(
                name="Projects",
                entries=[
                    PlannedEntry(
                        heading="Example",
                        bullets=[
                            PlannedBullet(
                                text="Built an ATS-safe renderer with 100% factual content.",
                                evidence_ids=["project.example.1"],
                            )
                        ],
                    )
                ],
            )
        ],
    )


def test_fallback_is_complete_safe_latex_and_prompt_only_contains_plan(plan: ContentPlan) -> None:
    rendered = fallback_render(plan)
    assert "\\begin{document}" in rendered
    assert "\\end{document}" in rendered
    messages = latex_generation_messages(plan)
    assert "Grounded summary." in messages[1]["content"]
    assert "evidence_ledger" not in messages[1]["content"]


@pytest.mark.parametrize("malicious", [r"\input{/etc/passwd}", r"\write18{whoami}"])
def test_sanitizer_rejects_file_and_shell_commands(malicious: str) -> None:
    document = "\\documentclass{article}\n\\begin{document}\n" + malicious + "\n\\end{document}"
    with pytest.raises(LatexError):
        sanitize_latex(document)


def test_plan_validator_rejects_invented_prose(plan: ContentPlan) -> None:
    document = sanitize_latex(
        "\\documentclass{article}\n\\begin{document}\n"
        "Grounded summary. Chief Executive Officer.\n\\end{document}"
    )
    with pytest.raises(LatexError, match="outside the validated content plan"):
        validate_latex_against_plan(document, plan)


def test_engine_selection_uses_windows_suffix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("resume_tailor.latex.compiler.os.name", "nt")
    monkeypatch.setattr(
        "resume_tailor.latex.compiler.shutil.which",
        lambda name, path=None: "C:/tex/tectonic.exe" if name == "tectonic.exe" else None,
    )
    assert discover_engines() == {"tectonic": "C:/tex/tectonic.exe"}
    assert select_engine() == ("tectonic", "C:/tex/tectonic.exe")


def test_pdf_validation_rejects_non_pdf(tmp_path: Path) -> None:
    path = tmp_path / "broken.pdf"
    path.write_text("not a PDF", encoding="utf-8")
    assert any(issue.code == "pdf_signature" for issue in validate_pdf(path).issues)


def test_pdf_validation_detects_non_extractable_pdf(tmp_path: Path) -> None:
    path = tmp_path / "blank.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    with path.open("wb") as destination:
        writer.write(destination)
    report = validate_pdf(path)
    assert not report.passed
    assert any(issue.code in {"pdf_no_text", "pdf_parse"} for issue in report.issues)


def test_ats_reports_absent_and_unsupported_requirements(plan: ContentPlan) -> None:
    job = JobDescription(
        original_text="Python SQL",
        normalized_text="python sql",
        keywords=[
            JobKeyword(term="Python", importance=1, source_quote="Python"),
            JobKeyword(term="SQL", importance=1, source_quote="SQL"),
        ],
    )
    report = validate_ats(plan.model_copy(update={"unsupported_jd_requirements": ["SQL"]}), job)
    codes = {issue.code for issue in report.issues}
    assert {"supported_keywords_absent", "unsupported_jd_requirements"} <= codes


def test_page_fit_is_bounded(plan: ContentPlan) -> None:
    with pytest.raises(ValueError):
        reduce_for_page_fit(plan, iteration=2)
