"""Phase D review-engine contracts."""

from __future__ import annotations

from pathlib import Path

import pytest

from resume_tailor.models.cv import EvidenceItem, EvidenceLedger, SourceLocation
from resume_tailor.models.generation import (
    ContentPlan,
    GroundedText,
    PlannedBullet,
    PlannedEntry,
    PlannedSection,
)
from resume_tailor.models.review import DecisionAction
from resume_tailor.review.checkpoint import (
    assert_checkpoint_matches,
    load_checkpoint,
    save_checkpoint,
)
from resume_tailor.review.session import ReviewController, claim_ids


@pytest.fixture
def ledger() -> EvidenceLedger:
    location = SourceLocation(file="resume.md", line=1)
    return EvidenceLedger(
        evidence=[
            EvidenceItem(
                id="project.one",
                section="Projects",
                text="Built Python API.",
                normalized_text="built python api.",
                source_location=location,
            ),
            EvidenceItem(
                id="project.two",
                section="Projects",
                text="Improved SQL queries.",
                normalized_text="improved sql queries.",
                source_location=location,
            ),
        ]
    )


def _plan_and_ledger(ledger: EvidenceLedger) -> tuple[ContentPlan, EvidenceLedger]:
    evidence = ledger.evidence[:2]
    return (
        ContentPlan(
            target_title="Engineer",
            summary=GroundedText(text=evidence[0].text, evidence_ids=[evidence[0].id]),
            sections=[
                PlannedSection(
                    name="Projects",
                    entries=[
                        PlannedEntry(
                            heading="Example",
                            bullets=[
                                PlannedBullet(text=item.text, evidence_ids=[item.id])
                                for item in evidence
                            ],
                        )
                    ],
                )
            ],
        ),
        ledger,
    )


def test_claim_ids_are_stable_and_reviewed_edits_reach_final_plan(ledger: EvidenceLedger) -> None:
    plan, value = _plan_and_ledger(ledger)
    assert claim_ids(plan) == claim_ids(plan)
    controller = ReviewController(plan, "sha256:" + "a" * 64, value)
    for claim_id in controller.claims:
        controller.decide(claim_id, DecisionAction.APPROVE)
    bullet_id = next(item for item in controller.claims if item != "summary/1")
    original, ids = controller.claims[bullet_id]
    controller.decide(bullet_id, DecisionAction.EDIT, text=original, evidence_ids=ids)
    reviewed = controller.approve()
    assert reviewed.plan.sections[0].entries[0].bullets[0].text == original
    assert reviewed.final_plan_hash.startswith("sha256:")


def test_undo_restores_previous_valid_decision_and_pending_blocks_approval(
    ledger: EvidenceLedger,
) -> None:
    plan, value = _plan_and_ledger(ledger)
    controller = ReviewController(plan, "sha256:" + "b" * 64, value)
    first = next(iter(controller.claims))
    controller.decide(first, DecisionAction.APPROVE)
    controller.undo()
    assert controller.session.decisions == []
    with pytest.raises(ValueError, match="all claims"):
        controller.approve()


def test_rejected_claim_is_a_complete_decision(ledger: EvidenceLedger) -> None:
    plan, value = _plan_and_ledger(ledger)
    controller = ReviewController(plan, "sha256:" + "f" * 64, value)
    for claim_id in controller.claims:
        controller.decide(
            claim_id,
            DecisionAction.REJECT if claim_id.endswith("/2") else DecisionAction.APPROVE,
        )
    reviewed = controller.approve()
    assert len(reviewed.plan.sections[0].entries[0].bullets) == 1
    assert reviewed.counts.rejected == 1


def test_checkpoint_is_atomic_and_bound_to_all_input_hashes(
    tmp_path: Path, ledger: EvidenceLedger
) -> None:
    plan, value = _plan_and_ledger(ledger)
    controller = ReviewController(
        plan,
        "sha256:" + "c" * 64,
        value,
        source_hash="sha256:" + "d" * 64,
        job_hash="sha256:" + "e" * 64,
    )
    path = tmp_path / "review.json"
    save_checkpoint(path, controller.session)
    loaded = load_checkpoint(path)
    assert_checkpoint_matches(
        loaded,
        source_hash="sha256:" + "d" * 64,
        job_hash="sha256:" + "e" * 64,
        base_plan_hash="sha256:" + "c" * 64,
    )
    with pytest.raises(ValueError, match="does not match"):
        assert_checkpoint_matches(
            loaded,
            source_hash="sha256:" + "0" * 64,
            job_hash="sha256:" + "e" * 64,
            base_plan_hash="sha256:" + "c" * 64,
        )
