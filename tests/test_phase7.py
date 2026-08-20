from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from typer.testing import CliRunner

from resume_tailor.artifacts import ArtifactRun
from resume_tailor.cli import app
from resume_tailor.models.cv import EvidenceItem, EvidenceLedger, SourceLocation
from resume_tailor.models.validation import ValidationReport
from resume_tailor.validation.report import report_json, report_markdown


def test_artifacts_publish_unique_run_and_remove_failed_staging(tmp_path: Path) -> None:
    run_id = uuid4()
    with ArtifactRun(tmp_path, run_id) as artifacts:
        artifacts.write_text("resume.tex", "safe")
        final = artifacts.publish()
    assert (final / "resume.tex").read_text(encoding="utf-8") == "safe"
    try:
        with ArtifactRun(tmp_path, uuid4()) as artifacts:
            artifacts.write_text("partial.txt", "not published")
            raise RuntimeError("failed")
    except RuntimeError:
        pass
    assert not list(tmp_path.glob(".resume-tailor-*"))


def test_reports_include_evidence_source_lines() -> None:
    ledger = EvidenceLedger(
        evidence=[
            EvidenceItem(
                id="projects.demo.123",
                section="projects",
                text="Built a demo",
                normalized_text="built a demo",
                source_location=SourceLocation(file="cv.md", line=12),
            )
        ]
    )
    report = ValidationReport(
        passed=True, evidence_mappings={"Projects/Demo/1": ["projects.demo.123"]}
    )
    encoded = report_json(report, ledger)
    assert (
        json.loads(json.dumps(encoded))["evidence_mappings"]["Projects/Demo/1"]["sources"][0][
            "line"
        ]
        == 12
    )
    assert "cv.md:12" in report_markdown(report, ledger)


def test_non_interactive_missing_values_never_prompt(monkeypatch: object) -> None:
    from pytest import MonkeyPatch

    assert isinstance(monkeypatch, MonkeyPatch)
    monkeypatch.setattr(
        "resume_tailor.cli.typer.prompt",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError()),
    )
    result = CliRunner().invoke(app, ["generate", "--non-interactive"])
    assert result.exit_code != 0
    assert "non-interactive generate requires" in str(result.exception)
