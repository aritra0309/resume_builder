from __future__ import annotations

import json
import os
from pathlib import Path

from resume_tailor.models.review import ReviewSession


def save_checkpoint(path: Path, session: ReviewSession) -> None:
    """Persist one complete, input-bound session with an atomic replacement."""
    if session.source_hash is None or session.job_hash is None:
        raise ValueError("review checkpoint requires source_hash and job_hash")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            stream.write(session.model_dump_json(indent=2))
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise ValueError(f"could not save review checkpoint: {path}") from exc


def load_checkpoint(path: Path) -> ReviewSession:
    try:
        session = ReviewSession.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError(
            f"corrupt review checkpoint: {path}; preserve it and create a new review"
        ) from exc
    if session.source_hash is None or session.job_hash is None:
        raise ValueError(f"corrupt review checkpoint: {path}; input hashes are missing")
    return session


def assert_checkpoint_matches(
    session: ReviewSession, *, source_hash: str, job_hash: str, base_plan_hash: str
) -> None:
    """Reject resume attempts against changed private inputs or generated plans."""
    expected = (source_hash, job_hash, base_plan_hash)
    actual = (session.source_hash, session.job_hash, session.base_plan_hash)
    if actual != expected:
        raise ValueError("review checkpoint does not match source, job description, or base plan")
