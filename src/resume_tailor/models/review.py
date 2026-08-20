"""Strict, secret-free contracts for local human-review checkpoints."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID

from pydantic import ConfigDict, Field, ValidationInfo, model_validator

from resume_tailor.models.cv import StrictModel
from resume_tailor.models.generation import ContentPlan


class ReviewPolicy(StrEnum):
    REQUIRED = "required"
    OPTIONAL = "optional"
    DISABLED = "disabled"


class ReviewStatus(StrEnum):
    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    APPROVED = "approved"
    CANCELLED = "cancelled"
    INVALIDATED = "invalidated"


class DecisionAction(StrEnum):
    APPROVE = "approve"
    EDIT = "edit"
    REJECT = "reject"
    RESTORE_SOURCE = "restore_source"
    DEFER = "defer"


class ValidationStatus(StrEnum):
    PENDING = "pending"
    PASSED = "passed"
    FAILED = "failed"


class ReviewDecision(StrictModel):
    claim_id: str = Field(min_length=1)
    action: DecisionAction
    original_text_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    reviewed_text: str | None = Field(default=None, min_length=1)
    evidence_ids: list[str] = Field(min_length=1)
    reviewed_at: datetime
    validation_status: ValidationStatus = ValidationStatus.PENDING

    @model_validator(mode="after")
    def enforce_action_shape(self) -> ReviewDecision:
        if self.action is DecisionAction.EDIT and self.reviewed_text is None:
            raise ValueError("edit decisions require reviewed_text")
        if self.action is DecisionAction.RESTORE_SOURCE and self.reviewed_text is None:
            raise ValueError("restore-source decisions require reviewed_text")
        if self.action is DecisionAction.REJECT and self.reviewed_text is not None:
            raise ValueError("reject decisions cannot contain reviewed_text")
        if (
            self.action in {DecisionAction.APPROVE, DecisionAction.DEFER}
            and self.reviewed_text is not None
        ):
            raise ValueError("approve and defer decisions cannot contain reviewed_text")
        if (
            self.action is DecisionAction.DEFER
            and self.validation_status is not ValidationStatus.PENDING
        ):
            raise ValueError("deferred decisions must have pending validation")
        return self


class ReviewSession(StrictModel):
    schema_version: int = Field(default=1, ge=1)
    review_id: UUID
    run_id: UUID
    base_plan_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    # Older in-memory sessions did not carry input hashes.  Keep those
    # constructible for compatibility, but checkpoint persistence requires
    # both values (see review.checkpoint).
    source_hash: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    job_hash: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    revision: int = Field(default=0, ge=0)
    status: ReviewStatus = ReviewStatus.NOT_STARTED
    decisions: list[ReviewDecision] = Field(default_factory=list)

    @model_validator(mode="after")
    def enforce_status(self) -> ReviewSession:
        if self.status is ReviewStatus.APPROVED:
            if any(decision.action is DecisionAction.DEFER for decision in self.decisions):
                raise ValueError("approved sessions cannot contain deferred decisions")
            if any(
                decision.validation_status is not ValidationStatus.PASSED
                for decision in self.decisions
            ):
                raise ValueError("approved sessions require passing decisions")
        return self

    def transition_to(self, status: ReviewStatus) -> ReviewSession:
        """Return the next immutable session state, rejecting impossible moves."""
        allowed: dict[ReviewStatus, set[ReviewStatus]] = {
            ReviewStatus.NOT_STARTED: {
                ReviewStatus.IN_PROGRESS,
                ReviewStatus.CANCELLED,
                ReviewStatus.INVALIDATED,
            },
            ReviewStatus.IN_PROGRESS: {
                ReviewStatus.APPROVED,
                ReviewStatus.CANCELLED,
                ReviewStatus.INVALIDATED,
            },
            ReviewStatus.CANCELLED: {ReviewStatus.IN_PROGRESS, ReviewStatus.INVALIDATED},
            ReviewStatus.APPROVED: {ReviewStatus.INVALIDATED},
            ReviewStatus.INVALIDATED: set(),
        }
        if status not in allowed[self.status]:
            raise ValueError(f"invalid review-state transition: {self.status} -> {status}")
        return self.model_validate(
            {**self.model_dump(), "status": status, "revision": self.revision + 1}
        )


class ReviewDecisionCounts(StrictModel):
    approved: int = Field(default=0, ge=0)
    edited: int = Field(default=0, ge=0)
    rejected: int = Field(default=0, ge=0)
    restored: int = Field(default=0, ge=0)
    deferred: int = Field(default=0, ge=0)


class ReviewedContentPlan(StrictModel):
    """An immutable plan created only through :meth:`from_approved_session`."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, frozen=True)

    plan: ContentPlan
    base_plan_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    final_plan_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    review_id: UUID
    revision: int = Field(ge=0)
    counts: ReviewDecisionCounts
    approved_at: datetime
    validation_passed: bool

    @model_validator(mode="after")
    def require_validating_factory(self, info: ValidationInfo) -> ReviewedContentPlan:
        if not info.context or info.context.get("reviewed_plan_factory") is not True:
            raise ValueError("ReviewedContentPlan must be created by from_approved_session")
        if not self.validation_passed:
            raise ValueError("reviewed plans require passing deterministic validation")
        return self

    @classmethod
    def from_approved_session(
        cls,
        *,
        plan: ContentPlan,
        base_plan_hash: str,
        final_plan_hash: str,
        session: ReviewSession,
        counts: ReviewDecisionCounts,
        approved_at: datetime | None = None,
    ) -> ReviewedContentPlan:
        if session.status is not ReviewStatus.APPROVED:
            raise ValueError("review session must be approved")
        if session.base_plan_hash != base_plan_hash:
            raise ValueError("review session does not match the base plan hash")
        if counts.deferred:
            raise ValueError("reviewed plans cannot contain deferred claims")
        return cls.model_validate(
            {
                "plan": plan,
                "base_plan_hash": base_plan_hash,
                "final_plan_hash": final_plan_hash,
                "review_id": session.review_id,
                "revision": session.revision,
                "counts": counts,
                "approved_at": approved_at or datetime.now(UTC),
                "validation_passed": True,
            },
            context={"reviewed_plan_factory": True},
        )
