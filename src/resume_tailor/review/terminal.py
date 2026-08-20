"""Safe, line-oriented terminal review UI."""

from __future__ import annotations

from pathlib import Path

from rich.console import Console
from rich.markup import escape

from resume_tailor.errors import CancelledError
from resume_tailor.models.review import DecisionAction, ReviewedContentPlan
from resume_tailor.review.checkpoint import save_checkpoint
from resume_tailor.review.session import ReviewController
from resume_tailor.tailoring.grounding import check_claim


def review_terminal(
    controller: ReviewController, console: Console, *, checkpoint: Path | None = None
) -> ReviewedContentPlan | None:
    """Review all claims with keyboard commands, saving after every change."""
    for index, claim in enumerate(controller.claim_list, 1):
        while True:
            warnings = check_claim(claim.proposed_text, claim.evidence_ids, controller.ledger)
            warning_text = "; ".join(item.message for item in warnings) or "none"
            console.print(
                f"[bold]Claim {index}/{len(controller.claim_list)}: "
                f"{escape(claim.claim_id)}[/bold]\n"
                f"- source: {escape(claim.source_text)}\n"
                f"+ proposed: {escape(claim.proposed_text)}\n"
                f"Diff:\n{escape(claim.diff or 'unchanged')}\n"
                f"Evidence: {escape(', '.join(claim.evidence_ids))}\n"
                f"Warnings: {escape(warning_text)}"
            )
            try:
                answer = console.input(
                    "[a]pprove [e]dit [r]eject [s]ource [c]hange evidence "
                    "[v]iew context [d]efer [u]ndo redo-[x] [q]uit: "
                ).strip()
            except (EOFError, KeyboardInterrupt):
                if checkpoint is not None:
                    save_checkpoint(checkpoint, controller.session)
                raise CancelledError(
                    "review cancelled", hint="The review draft was saved."
                ) from None
            command, _, value = answer.partition(" ")
            command = command.lower()
            if command in {"q", "quit"}:
                if checkpoint is not None:
                    save_checkpoint(checkpoint, controller.session)
                return None
            if command in {"u", "undo", "x", "redo"}:
                try:
                    (controller.undo if command in {"u", "undo"} else controller.redo)()
                except ValueError as exc:
                    console.print(f"[yellow]{escape(str(exc))}[/yellow]")
                continue
            if command in {"v", "view"}:
                console.print(f"Context: {escape(claim.source_text)}")
                continue
            evidence_ids: list[str] | None = None
            if command in {"c", "evidence"}:
                evidence_ids = [item.strip() for item in value.split(",") if item.strip()]
                if not evidence_ids:
                    console.print("[yellow]Provide comma-separated evidence IDs after c.[/yellow]")
                    continue
                command = "a"
            action = {
                "a": DecisionAction.APPROVE,
                "approve": DecisionAction.APPROVE,
                "e": DecisionAction.EDIT,
                "edit": DecisionAction.EDIT,
                "r": DecisionAction.REJECT,
                "reject": DecisionAction.REJECT,
                "s": DecisionAction.RESTORE_SOURCE,
                "source": DecisionAction.RESTORE_SOURCE,
                "d": DecisionAction.DEFER,
                "defer": DecisionAction.DEFER,
            }.get(command)
            if action is None or (action is DecisionAction.EDIT and not value):
                console.print(
                    "[yellow]Enter an action; edit requires text after the command.[/yellow]"
                )
                continue
            try:
                controller.decide(
                    claim.claim_id,
                    action,
                    text=value if action is DecisionAction.EDIT else None,
                    evidence_ids=evidence_ids,
                )
                if checkpoint is not None:
                    save_checkpoint(checkpoint, controller.session)
                break
            except ValueError as exc:
                console.print(f"[red]{escape(str(exc))}[/red]")
    return controller.approve()
