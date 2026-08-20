"""Deterministic review claims, edits, and immutable approved plans."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from resume_tailor.models.cv import EvidenceLedger
from resume_tailor.models.generation import (
    ContentPlan,
    GroundedText,
    PlannedBullet,
    PlannedEntry,
    PlannedSection,
)
from resume_tailor.models.review import (
    DecisionAction,
    ReviewDecision,
    ReviewDecisionCounts,
    ReviewedContentPlan,
    ReviewSession,
    ReviewStatus,
    ValidationStatus,
)
from resume_tailor.review.diff import source_text, word_diff
from resume_tailor.tailoring.grounding import check_claim


def _hash(value: str) -> str:
    return f"sha256:{hashlib.sha256(value.encode()).hexdigest()}"


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or "entry"


@dataclass(frozen=True, slots=True)
class ReviewClaim:
    """A stable, presentation-independent comparison for one generated claim."""

    claim_id: str
    proposed_text: str
    evidence_ids: list[str]
    source_text: str
    diff: str


def claim_ids(plan: ContentPlan) -> list[tuple[str, str, list[str]]]:
    """Create stable structural IDs without relying on display order or width."""
    output = [("summary/1", plan.summary.text, plan.summary.evidence_ids)]
    section_counts: dict[str, int] = {}
    for section in plan.sections:
        section_slug = _slug(section.name)
        section_counts[section_slug] = section_counts.get(section_slug, 0) + 1
        section_id = (
            section_slug
            if section_counts[section_slug] == 1
            else f"{section_slug}-{section_counts[section_slug]}"
        )
        entry_counts: dict[str, int] = {}
        for entry in section.entries:
            entry_slug = _slug(entry.heading)
            entry_counts[entry_slug] = entry_counts.get(entry_slug, 0) + 1
            entry_id = (
                entry_slug
                if entry_counts[entry_slug] == 1
                else f"{entry_slug}-{entry_counts[entry_slug]}"
            )
            for number, bullet in enumerate(entry.bullets, 1):
                output.append(
                    (f"{section_id}/{entry_id}/{number}", bullet.text, bullet.evidence_ids)
                )
    return output


class ReviewController:
    """Local state machine; it never calls a provider or accepts unsafe edits."""

    def __init__(
        self,
        plan: ContentPlan,
        base_hash: str,
        ledger: EvidenceLedger,
        session: ReviewSession | None = None,
        *,
        source_hash: str | None = None,
        job_hash: str | None = None,
    ) -> None:
        if session is not None and session.base_plan_hash != base_hash:
            raise ValueError("review session does not match the base plan hash")
        if session is not None and source_hash is not None and session.source_hash != source_hash:
            raise ValueError("review session does not match the source hash")
        if session is not None and job_hash is not None and session.job_hash != job_hash:
            raise ValueError("review session does not match the job-description hash")
        self.plan, self.ledger = plan, ledger
        evidence = {item.id: item.text for item in ledger.evidence}
        self.claim_list = [
            ReviewClaim(
                claim_id,
                proposed_text,
                ids,
                source := source_text(ids, evidence),
                word_diff(source, proposed_text),
            )
            for claim_id, proposed_text, ids in claim_ids(plan)
        ]
        self.claims = {
            item.claim_id: (item.proposed_text, item.evidence_ids) for item in self.claim_list
        }
        self.session = session or ReviewSession(
            review_id=uuid4(),
            run_id=uuid4(),
            base_plan_hash=base_hash,
            source_hash=source_hash,
            job_hash=job_hash,
            status=ReviewStatus.IN_PROGRESS,
        )
        if self.session.status not in {ReviewStatus.IN_PROGRESS, ReviewStatus.NOT_STARTED}:
            raise ValueError(f"review session is not editable: {self.session.status}")
        self._undo: list[ReviewSession] = []
        self._redo: list[ReviewSession] = []

    def comparison(self, claim_id: str) -> ReviewClaim:
        try:
            return next(item for item in self.claim_list if item.claim_id == claim_id)
        except StopIteration as exc:
            raise ValueError(f"unknown claim ID: {claim_id}") from exc

    def _replace_session(self, decisions: list[ReviewDecision]) -> ReviewSession:
        self._undo.append(self.session)
        self._redo.clear()
        ordered = sorted(decisions, key=lambda item: list(self.claims).index(item.claim_id))
        self.session = ReviewSession.model_validate(
            {
                **self.session.model_dump(),
                "decisions": ordered,
                "revision": self.session.revision + 1,
            }
        )
        return self.session

    def decide(
        self,
        claim_id: str,
        action: DecisionAction,
        *,
        text: str | None = None,
        evidence_ids: list[str] | None = None,
    ) -> ReviewSession:
        claim = self.comparison(claim_id)
        ids = list(evidence_ids if evidence_ids is not None else claim.evidence_ids)
        if not ids:
            raise ValueError("review decisions require at least one evidence ID")
        indexed = {item.id: item.text for item in self.ledger.evidence}
        # Validating this before mutations gives concise unknown-ID errors and
        # avoids accidentally restoring a partial evidence selection.
        cited_source = source_text(ids, indexed)
        if action is DecisionAction.RESTORE_SOURCE:
            text = cited_source
        if action is DecisionAction.EDIT and (
            text is None
            or not text.strip()
            or any(ord(char) < 32 for char in text if char not in "\n\t")
        ):
            raise ValueError("edited review text must be non-empty printable text")
        if action is DecisionAction.REJECT:
            text = None
        retained = action not in {DecisionAction.REJECT, DecisionAction.DEFER}
        effective_text = text if text is not None else claim.proposed_text
        issues = check_claim(effective_text, ids, self.ledger) if retained else []
        status = (
            ValidationStatus.PENDING if action is DecisionAction.DEFER else ValidationStatus.PASSED
        )
        if issues:
            raise ValueError("invalid review edit: " + "; ".join(issue.message for issue in issues))
        decision = ReviewDecision(
            claim_id=claim_id,
            action=action,
            original_text_hash=_hash(claim.proposed_text),
            reviewed_text=text,
            evidence_ids=ids,
            reviewed_at=datetime.now(UTC),
            validation_status=status,
        )
        decisions = [item for item in self.session.decisions if item.claim_id != claim_id] + [
            decision
        ]
        return self._replace_session(decisions)

    def undo(self) -> ReviewSession:
        if not self._undo:
            raise ValueError("nothing to undo")
        self._redo.append(self.session)
        self.session = self._undo.pop()
        return self.session

    def redo(self) -> ReviewSession:
        if not self._redo:
            raise ValueError("nothing to redo")
        self._undo.append(self.session)
        self.session = self._redo.pop()
        return self.session

    def _reviewed_plan(self) -> ContentPlan:
        decisions = {item.claim_id: item for item in self.session.decisions}
        summary_decision = decisions["summary/1"]
        if summary_decision.action is DecisionAction.REJECT:
            raise ValueError("the summary cannot be rejected without a documented omission")
        summary = GroundedText(
            text=summary_decision.reviewed_text or self.plan.summary.text,
            evidence_ids=summary_decision.evidence_ids,
        )
        sections: list[PlannedSection] = []
        for section_id, section in self._section_ids():
            entries: list[PlannedEntry] = []
            for entry_id, entry in self._entry_ids(section):
                bullets: list[PlannedBullet] = []
                for number, bullet in enumerate(entry.bullets, 1):
                    decision = decisions[f"{section_id}/{entry_id}/{number}"]
                    if decision.action is DecisionAction.REJECT:
                        continue
                    bullets.append(
                        PlannedBullet(
                            text=decision.reviewed_text or bullet.text,
                            evidence_ids=decision.evidence_ids,
                            matched_keywords=bullet.matched_keywords,
                        )
                    )
                if bullets:
                    entries.append(PlannedEntry(heading=entry.heading, bullets=bullets))
            if entries:
                sections.append(PlannedSection(name=section.name, entries=entries))
        if not sections:
            raise ValueError("rejections removed every required section")
        return ContentPlan(
            target_title=self.plan.target_title,
            summary=summary,
            sections=sections,
            omitted_evidence_ids=self.plan.omitted_evidence_ids,
            unsupported_jd_requirements=self.plan.unsupported_jd_requirements,
        )

    def _section_ids(self) -> list[tuple[str, PlannedSection]]:
        counts: dict[str, int] = {}
        result: list[tuple[str, PlannedSection]] = []
        for section in self.plan.sections:
            slug = _slug(section.name)
            counts[slug] = counts.get(slug, 0) + 1
            result.append((slug if counts[slug] == 1 else f"{slug}-{counts[slug]}", section))
        return result

    def _entry_ids(self, section: PlannedSection) -> list[tuple[str, PlannedEntry]]:
        # ``section`` originates from ContentPlan; keeping this helper separate
        # mirrors claim_ids and makes duplicate labels deterministic.
        entries = section.entries
        counts: dict[str, int] = {}
        result: list[tuple[str, PlannedEntry]] = []
        for entry in entries:
            slug = _slug(entry.heading)
            counts[slug] = counts.get(slug, 0) + 1
            result.append((slug if counts[slug] == 1 else f"{slug}-{counts[slug]}", entry))
        return result

    def approve(self) -> ReviewedContentPlan:
        if set(self.claims) != {item.claim_id for item in self.session.decisions}:
            raise ValueError("all claims require a decision")
        if any(
            item.action is DecisionAction.DEFER
            or item.validation_status is not ValidationStatus.PASSED
            for item in self.session.decisions
        ):
            raise ValueError("pending or deferred decisions remain")
        reviewed = self._reviewed_plan()
        self.session = self.session.transition_to(ReviewStatus.APPROVED)
        counts = ReviewDecisionCounts(
            approved=sum(item.action is DecisionAction.APPROVE for item in self.session.decisions),
            edited=sum(item.action is DecisionAction.EDIT for item in self.session.decisions),
            rejected=sum(item.action is DecisionAction.REJECT for item in self.session.decisions),
            restored=sum(
                item.action is DecisionAction.RESTORE_SOURCE for item in self.session.decisions
            ),
            deferred=0,
        )
        return ReviewedContentPlan.from_approved_session(
            plan=reviewed,
            base_plan_hash=self.session.base_plan_hash,
            final_plan_hash=_hash(reviewed.model_dump_json()),
            session=self.session,
            counts=counts,
        )
