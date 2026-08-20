"""Private, local persistence for resumable review sessions."""

from __future__ import annotations

import json
import os
from pathlib import Path

from resume_tailor.generation import IngestionBundle, PlanDraft
from resume_tailor.models.cv import EvidenceLedger
from resume_tailor.models.review import ReviewSession, ReviewStatus
from resume_tailor.review.checkpoint import load_checkpoint, save_checkpoint
from resume_tailor.review.session import ReviewController


def draft_path(output_dir: Path, review_id: str) -> Path:
    return output_dir / "drafts" / review_id


def _write_json(path: Path, value: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise ValueError(f"could not save review draft: {path}") from exc


def create_draft(
    output_dir: Path,
    draft: PlanDraft,
    bundle: IngestionBundle,
    session: ReviewSession,
    *,
    destination: Path | None = None,
) -> Path:
    """Create a non-overwritable draft before interaction begins."""
    path = destination or draft_path(output_dir, str(session.review_id))
    try:
        path.mkdir(parents=True, exist_ok=False)
        os.chmod(path, 0o700)
    except OSError as exc:
        raise ValueError(f"could not create review draft: {path}") from exc
    try:
        _write_json(path / "draft.json", draft.model_dump(mode="json"))
        _write_json(path / "ledger.json", bundle.ledger.model_dump(mode="json"))
        save_checkpoint(path / "review.json", session)
    except Exception:
        # The directory is intentionally left as a clearly incomplete draft,
        # never mistaken for a successful generation.
        raise
    return path


def load_controller(path: Path) -> ReviewController:
    """Load a draft entirely locally; no credentials or provider calls occur."""
    try:
        draft = PlanDraft.model_validate(json.loads((path / "draft.json").read_text("utf-8")))
        ledger = EvidenceLedger.model_validate(
            json.loads((path / "ledger.json").read_text("utf-8"))
        )
        session = load_checkpoint(path / "review.json")
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"corrupt review draft: {path}") from exc
    return ReviewController(
        draft.plan,
        draft.base_plan_hash,
        ledger,
        session,
        source_hash=f"sha256:{draft.source_hash}",
        job_hash=f"sha256:{draft.job_hash}",
    )


def save_controller(path: Path, controller: ReviewController) -> None:
    save_checkpoint(path / "review.json", controller.session)


def export_markdown(path: Path) -> str:
    controller = load_controller(path)
    lines = [f"# Review {controller.session.review_id}", ""]
    for claim in controller.claim_list:
        lines.extend(
            [
                f"## {claim.claim_id}",
                "",
                f"- Source: {claim.source_text}",
                f"- Proposed: {claim.proposed_text}",
                f"- Evidence: {', '.join(claim.evidence_ids)}",
                "",
            ]
        )
    return "\n".join(lines)


def invalidate(path: Path) -> ReviewSession:
    controller = load_controller(path)
    controller.session = controller.session.transition_to(ReviewStatus.INVALIDATED)
    save_controller(path, controller)
    return controller.session
