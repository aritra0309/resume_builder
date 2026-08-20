"""Atomic, secret-free publication of a completed generation run."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from uuid import UUID

from resume_tailor.errors import ValidationError
from resume_tailor.redaction import redact


class ArtifactRun:
    """Stage artifacts privately, then publish one unique completed-run directory."""

    def __init__(self, output_root: Path, run_id: UUID) -> None:
        self.output_root = output_root.expanduser()
        self.run_id = run_id
        self.final_path = self.output_root / f"run-{run_id}"
        self._staging: Path | None = None

    def __enter__(self) -> ArtifactRun:
        try:
            self.output_root.mkdir(parents=True, exist_ok=True)
            if self.final_path.exists():
                raise ValidationError(f"refusing to overwrite existing run: {self.final_path}")
            self._staging = Path(
                tempfile.mkdtemp(prefix=".resume-tailor-", dir=str(self.output_root))
            )
        except OSError as exc:
            raise ValidationError(f"could not create output directory: {self.output_root}") from exc
        return self

    @property
    def path(self) -> Path:
        if self._staging is None:
            raise RuntimeError("artifact run has not started")
        return self._staging

    def write_json(self, name: str, value: object) -> Path:
        target = self.path / name
        target.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return target

    def write_text(self, name: str, value: str) -> Path:
        target = self.path / name
        target.write_text(redact(value), encoding="utf-8")
        return target

    def publish(self) -> Path:
        if self._staging is None:
            raise RuntimeError("artifact run has not started")
        try:
            os.replace(self._staging, self.final_path)
        except OSError as exc:
            raise ValidationError("could not publish generated artifacts") from exc
        self._staging = None
        return self.final_path

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if self._staging is not None:
            # A failed run intentionally leaves no directory that resembles a completed run.
            for child in self._staging.iterdir():
                if child.is_file():
                    child.unlink()
            self._staging.rmdir()
            self._staging = None
