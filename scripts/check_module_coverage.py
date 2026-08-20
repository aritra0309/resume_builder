"""Fail CI when the plan's module-specific branch-coverage gates regress."""

from __future__ import annotations

import json
import sys
from pathlib import Path

TARGETS = {
    "src/resume_tailor/ingestion/detector.py": 90,
    "src/resume_tailor/ingestion/safety.py": 90,
    "src/resume_tailor/review/session.py": 90,
    "src/resume_tailor/review/checkpoint.py": 90,
    "src/resume_tailor/tailoring/grounding.py": 90,
    "src/resume_tailor/ingestion/docx.py": 85,
    "src/resume_tailor/ingestion/pdf.py": 85,
}


def main() -> int:
    report = json.loads(Path(sys.argv[1] if len(sys.argv) > 1 else "coverage.json").read_text())
    files = report["files"]
    failures = []
    for module, minimum in TARGETS.items():
        measured = files.get(module, {}).get("summary", {}).get("percent_covered", 0)
        if measured < minimum:
            failures.append(f"{module}: {measured:.2f}% < {minimum}%")
    if failures:
        print("Module coverage gates failed:\n" + "\n".join(failures), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
