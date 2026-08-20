"""Phase E terminal, draft, and generation-boundary checks."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from typer.testing import CliRunner

from resume_tailor.cli import app
from resume_tailor.generation import IngestionBundle, PlanDraft, review_plan
from resume_tailor.models.cv import (
    CVBullet,
    CVDocument,
    CVEntry,
    CVSection,
    EvidenceItem,
    EvidenceLedger,
    SourceLocation,
)
from resume_tailor.models.generation import (
    ContentPlan,
    GroundedText,
    PlannedBullet,
    PlannedEntry,
    PlannedSection,
)
from resume_tailor.models.job import JobDescription
from resume_tailor.models.review import ReviewPolicy, ReviewSession, ReviewStatus
from resume_tailor.review.drafts import create_draft


def _draft() -> tuple[PlanDraft, IngestionBundle, ReviewSession]:
    location = SourceLocation(file="cv.md", line=1)
    evidence = EvidenceItem(
        id="project.one",
        section="Projects",
        text="Built Python API.",
        normalized_text="built python api.",
        source_location=location,
    )
    plan = ContentPlan(
        target_title="Engineer",
        summary=GroundedText(text=evidence.text, evidence_ids=[evidence.id]),
        sections=[
            PlannedSection(
                name="Projects",
                entries=[
                    PlannedEntry(
                        heading="Example",
                        bullets=[PlannedBullet(text=evidence.text, evidence_ids=[evidence.id])],
                    )
                ],
            )
        ],
    )
    draft = PlanDraft(
        source_hash="a" * 64,
        job_hash="b" * 64,
        job=JobDescription(original_text="Python", normalized_text="python"),
        plan=plan,
        base_plan_hash="sha256:" + "c" * 64,
    )
    document = CVDocument(
        source_file="cv.md",
        sections=[
            CVSection(
                name="Projects",
                level=1,
                source_location=location,
                entries=[
                    CVEntry(
                        heading="Example",
                        source_location=location,
                        bullets=[CVBullet(text=evidence.text, source_location=location)],
                    )
                ],
            )
        ],
    )
    bundle = IngestionBundle(
        document=document, ledger=EvidenceLedger(evidence=[evidence]), source_hash="a" * 64
    )
    session = ReviewSession(
        review_id=uuid4(),
        run_id=uuid4(),
        base_plan_hash=draft.base_plan_hash,
        source_hash="sha256:" + "a" * 64,
        job_hash="sha256:" + "b" * 64,
        status=ReviewStatus.IN_PROGRESS,
    )
    return draft, bundle, session


def test_required_review_boundary_rejects_unapproved_plan() -> None:
    draft, _, _ = _draft()
    with pytest.raises(Exception, match="required review"):
        review_plan(draft, ReviewPolicy.REQUIRED)


def test_review_status_export_and_invalidate_are_local(tmp_path: Path) -> None:
    draft, bundle, session = _draft()
    path = create_draft(tmp_path, draft, bundle, session)
    runner = CliRunner()
    status = runner.invoke(app, ["review", "status", str(path)])
    assert status.exit_code == 0
    assert "0/2 claims decided" in status.stdout
    exported = runner.invoke(app, ["review", "export", str(path), "--format", "markdown"])
    assert exported.exit_code == 0
    assert "Built Python API." in exported.stdout
    invalidated = runner.invoke(app, ["review", "invalidate", str(path)])
    assert invalidated.exit_code == 0
    assert "invalidated" in invalidated.stdout


def test_noninteractive_generation_requires_explicit_disabled_policy() -> None:
    result = CliRunner().invoke(app, ["generate", "--non-interactive"])
    assert result.exit_code != 0
    assert "requires" in str(result.exception)
