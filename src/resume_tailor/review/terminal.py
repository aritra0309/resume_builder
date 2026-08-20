"""Safe, line-oriented terminal review UI."""

from __future__ import annotations

from pathlib import Path

import questionary
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
    """Review all claims with arrow-key actions, saving after every change."""
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
                answer = questionary.select(
                    "Choose an action",
                    choices=[
                        questionary.Choice("Approve", value="approve"),
                        questionary.Choice("Edit text", value="edit"),
                        questionary.Choice("Reject", value="reject"),
                        questionary.Choice("Restore source wording", value="source"),
                        questionary.Choice("Change evidence IDs", value="evidence"),
                        questionary.Choice("View source context", value="view"),
                        questionary.Choice("Defer", value="defer"),
                        questionary.Choice("Undo", value="undo"),
                        questionary.Choice("Redo", value="redo"),
                        questionary.Choice("Save and quit", value="quit"),
                    ],
                    default="approve",
                ).ask()
            except (EOFError, KeyboardInterrupt):
                if checkpoint is not None:
                    save_checkpoint(checkpoint, controller.session)
                raise CancelledError(
                    "review cancelled", hint="The review draft was saved."
                ) from None
            if answer is None or answer == "quit":
                if checkpoint is not None:
                    save_checkpoint(checkpoint, controller.session)
                return None
            command = str(answer)
            if command in {"undo", "redo"}:
                try:
                    (controller.undo if command == "undo" else controller.redo)()
                except ValueError as exc:
                    console.print(f"[yellow]{escape(str(exc))}[/yellow]")
                continue
            if command == "view":
                console.print(f"Context: {escape(claim.source_text)}")
                continue
            evidence_ids: list[str] | None = None
            if command == "evidence":
                value = console.input("Comma-separated evidence IDs: ").strip()
                evidence_ids = [item.strip() for item in value.split(",") if item.strip()]
                if not evidence_ids:
                    console.print("[yellow]Provide at least one evidence ID.[/yellow]")
                    continue
                command = "approve"
            value = ""
            if command == "edit":
                value = console.input("Edited text: ").strip()
            action = {
                "approve": DecisionAction.APPROVE,
                "edit": DecisionAction.EDIT,
                "reject": DecisionAction.REJECT,
                "source": DecisionAction.RESTORE_SOURCE,
                "defer": DecisionAction.DEFER,
            }.get(command)
            if action is None or (action is DecisionAction.EDIT and not value):
                console.print("[yellow]Edited text cannot be empty.[/yellow]")
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
