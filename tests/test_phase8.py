from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from pypdf import PdfWriter

from resume_tailor.errors import CompilerError, LatexError, UsageError
from resume_tailor.evidence.ledger import build_evidence_ledger
from resume_tailor.generation import GenerationRequest, generate
from resume_tailor.latex.compiler import compile_latex, select_engine
from resume_tailor.latex.sanitizer import sanitize_latex
from resume_tailor.llm.client import LLMClient
from resume_tailor.llm.providers import get_provider
from resume_tailor.models.generation import (
    ContentPlan,
    GroundedText,
    PlannedBullet,
    PlannedEntry,
    PlannedSection,
)
from resume_tailor.models.job import JobAnalysis, JobDescription, JobKeyword
from resume_tailor.models.validation import ValidationIssue, ValidationReport
from resume_tailor.parsers.markdown_cv import parse_markdown_cv

LATEX = """\\documentclass{article}
\\begin{document}
Safe resume text.
\\end{document}
"""


@pytest.mark.parametrize(
    ("document", "message"),
    [
        ("", "empty"),
        ("\\documentclass{article}", "document environment"),
        ("\\documentclass{article}\\begin{document}x", "document environment"),
        ("\\begin{document}x\\end{document}", "document class"),
        ("\\documentclass{article}\\begin{document}\x00\\end{document}", "control"),
        (
            "\\documentclass{article}\\begin{document}\\usepackage{graphicx}\\end{document}",
            "outside the allowlist",
        ),
    ],
)
def test_sanitizer_rejects_contract_boundaries(document: str, message: str) -> None:
    with pytest.raises(LatexError, match=message):
        sanitize_latex(document)


def test_engine_selection_rejects_unknown_and_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("resume_tailor.latex.compiler.discover_engines", lambda path=None: {})
    with pytest.raises(CompilerError):
        select_engine()
    with pytest.raises(UsageError):
        select_engine("unsupported")
    with pytest.raises(CompilerError):
        select_engine("pdflatex")


def test_compiler_success_and_does_not_overwrite(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "resume_tailor.latex.compiler.select_engine", lambda engine: ("pdflatex", "pdflatex")
    )

    def fake_run(arguments: list[str], **kwargs: object) -> SimpleNamespace:
        output = Path(
            next(
                str(argument).split("=", 1)[1]
                for argument in arguments
                if "output-directory=" in str(argument)
            )
        )
        output.mkdir(exist_ok=True)
        (output / "resume.pdf").write_bytes(b"%PDF- fake")
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("resume_tailor.latex.compiler.subprocess.run", fake_run)
    output = tmp_path / "resume.pdf"
    result = compile_latex(LATEX, output, engine="pdflatex")
    assert result.pdf_path == output
    with pytest.raises(CompilerError, match="overwrite"):
        compile_latex(LATEX, output, engine="pdflatex")


def test_compiler_turns_timeout_and_engine_failure_into_diagnostics(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "resume_tailor.latex.compiler.select_engine", lambda engine: ("pdflatex", "pdflatex")
    )
    monkeypatch.setattr(
        "resume_tailor.latex.compiler.subprocess.run",
        lambda *args, **kwargs: (_ for _ in ()).throw(subprocess.TimeoutExpired("tex", 1)),
    )
    with pytest.raises(CompilerError, match="timed out"):
        compile_latex(LATEX, tmp_path / "timeout.pdf", engine="pdflatex")
    monkeypatch.setattr(
        "resume_tailor.latex.compiler.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(returncode=1, stdout="! error", stderr=""),
    )
    with pytest.raises(CompilerError, match="compilation failed"):
        compile_latex(LATEX, tmp_path / "failure.pdf", engine="pdflatex")


def test_offline_end_to_end_flow_publishes_traceable_artifacts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = Path("Master_CV.md")
    evidence = build_evidence_ledger(parse_markdown_cv(source)).evidence[0]
    job = JobDescription(
        original_text="Data Engineer responsibilities include Python and SQL experience.",
        normalized_text="Data Engineer responsibilities include Python and SQL experience.",
        keywords=[JobKeyword(term="Python", importance=1, source_quote="Python")],
    )
    plan = ContentPlan(
        target_title="Data Engineer",
        summary=GroundedText(text=evidence.text, evidence_ids=[evidence.id]),
        sections=[
            PlannedSection(
                name="Projects",
                entries=[
                    PlannedEntry(
                        heading="Selected work",
                        bullets=[PlannedBullet(text=evidence.text, evidence_ids=[evidence.id])],
                    )
                ],
            )
        ],
    )
    responses = [
            JobAnalysis.model_validate(
                job.model_dump(exclude={"original_text", "normalized_text"})
            ).model_dump_json(),
            plan.model_dump_json(),
            '{"pages": 1, "reason": "The concise plan fits on one page."}',
            "not valid latex; deterministic fallback must be used",
        ]

    def completion(**kwargs: object) -> SimpleNamespace:
        content = responses.pop(0)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
            usage={"total_tokens": 10},
        )

    def compile_stub(latex: str, output: Path, **kwargs: object) -> SimpleNamespace:
        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        with output.open("wb") as stream:
            writer.write(stream)
        return SimpleNamespace(engine="pdflatex", pdf_path=output, log="")

    monkeypatch.setattr("resume_tailor.generation.compile_latex", compile_stub)
    monkeypatch.setattr(
        "resume_tailor.generation.validate_pdf",
        lambda *args, **kwargs: ValidationReport(passed=True),
    )
    client = LLMClient(get_provider("ollama"), completion=completion, sleeper=lambda _: None)
    result = generate(
        GenerationRequest(
            master_cv=source,
            job=job,
            output_dir=tmp_path,
            provider="ollama",
            model="llama3.2",
            api_key=None,
            api_base=None,
            pages="auto",
            tex_engine="auto",
            timeout=10,
        ),
        client,
    )
    assert (result.artifact_dir / "resume.pdf").is_file()
    assert "Master_CV.md" in (result.artifact_dir / "validation.md").read_text()


def test_page_count_failure_triggers_one_grounded_revision(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = Path("Master_CV.md")
    evidence = build_evidence_ledger(parse_markdown_cv(source)).evidence[0]
    job = JobDescription(
        original_text="Python",
        normalized_text="python",
        keywords=[JobKeyword(term="Python", importance=1, source_quote="Python")],
    )
    plan = ContentPlan(
        target_title="Data Engineer",
        summary=GroundedText(text=evidence.text, evidence_ids=[evidence.id]),
        sections=[
            PlannedSection(
                name="Projects",
                entries=[
                    PlannedEntry(
                        heading="Selected work",
                        bullets=[PlannedBullet(text=evidence.text, evidence_ids=[evidence.id])],
                    )
                ],
            )
        ],
    )
    responses = [
        JobAnalysis.model_validate(
            job.model_dump(exclude={"original_text", "normalized_text"})
        ).model_dump_json(),
        plan.model_dump_json(),
        "not valid latex",
        plan.model_dump_json(),
        "not valid latex",
    ]
    calls: list[dict[str, object]] = []

    def completion(**kwargs: object) -> SimpleNamespace:
        calls.append(dict(kwargs))
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=responses.pop(0)))], usage={}
        )

    def compile_stub(latex: str, output: Path, **kwargs: object) -> SimpleNamespace:
        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        with output.open("wb") as stream:
            writer.write(stream)
        return SimpleNamespace(engine="pdflatex", pdf_path=output, log="")

    reports = [
        ValidationReport(
            passed=False,
            issues=[
                ValidationIssue(
                    code="page_count",
                    message="PDF has 2 pages; target is at most 1.",
                    severity="error",
                )
            ],
        ),
        ValidationReport(passed=True),
    ]
    monkeypatch.setattr("resume_tailor.generation.compile_latex", compile_stub)
    monkeypatch.setattr(
        "resume_tailor.generation.validate_pdf", lambda *args, **kwargs: reports.pop(0)
    )
    client = LLMClient(get_provider("ollama"), completion=completion, sleeper=lambda _: None)

    result = generate(
        GenerationRequest(
            master_cv=source,
            job=job,
            output_dir=tmp_path,
            provider="ollama",
            model="llama3.2",
            api_key=None,
            api_base=None,
            pages="1",
            tex_engine="auto",
            timeout=10,
        ),
        client,
    )

    assert (result.artifact_dir / "resume.pdf").is_file()
    # Document layout is deterministic; only job analysis, planning, and page-fit use the model.
    assert len(calls) == 4
    revision_prompts = [
        messages
        for call in calls
        if isinstance(messages := call["messages"], list)
        and "PDF has 2 pages" in messages[-1]["content"]
    ]
    assert revision_prompts
    assert (result.artifact_dir / "run.json").is_file()
