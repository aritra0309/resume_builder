"""Phase A compatibility and contract tests."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from resume_tailor.models.cv import SourceLocation
from resume_tailor.models.generation import migrate_run_manifest
from resume_tailor.models.review import (
    DecisionAction,
    ReviewDecision,
    ReviewSession,
    ReviewStatus,
)


def test_legacy_markdown_anchor_upgrades_without_losing_line() -> None:
    anchor = SourceLocation.model_validate({"file": "cv.md", "line": 7})

    assert anchor.model_dump() == {
        "file": "cv.md",
        "format": "markdown",
        "locator": "line:7",
        "label": "line 7",
        "line": 7,
        "page": None,
        "paragraph": None,
        "part": None,
    }
    assert anchor.report_label() == "cv.md:7"


def test_docx_anchor_uses_generalized_report_label() -> None:
    anchor = SourceLocation(file="cv.docx", format="docx", part="body", paragraph=18)

    assert anchor.locator == "body/p:18"
    assert anchor.report_label() == "cv.docx:paragraph 18"


def test_old_manifest_payload_receives_safe_defaults() -> None:
    migrated = migrate_run_manifest({"run_id": "x"})

    assert migrated["schema_version"] == 1
    assert migrated["source_format"] == "markdown"
    assert migrated["review_policy"] == "disabled"


def test_review_models_reject_extra_fields_and_invalid_approval() -> None:
    with pytest.raises(ValidationError):
        ReviewSession.model_validate(
            {
                "review_id": str(uuid4()),
                "run_id": str(uuid4()),
                "base_plan_hash": "sha256:" + "a" * 64,
                "surprise": True,
            }
        )

    deferred = ReviewDecision(
        claim_id="summary/1",
        action=DecisionAction.DEFER,
        original_text_hash="sha256:" + "a" * 64,
        evidence_ids=["summary.fact"],
        reviewed_at=datetime.now(UTC),
    )
    with pytest.raises(ValidationError):
        ReviewSession(
            review_id=uuid4(),
            run_id=uuid4(),
            base_plan_hash="sha256:" + "a" * 64,
            status=ReviewStatus.APPROVED,
            decisions=[deferred],
        )


def test_review_session_rejects_impossible_state_transition() -> None:
    session = ReviewSession(review_id=uuid4(), run_id=uuid4(), base_plan_hash="sha256:" + "a" * 64)

    with pytest.raises(ValueError, match="invalid review-state transition"):
        session.transition_to(ReviewStatus.APPROVED)
