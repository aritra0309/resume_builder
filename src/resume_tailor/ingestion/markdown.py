from __future__ import annotations

import hashlib
from pathlib import Path

from resume_tailor.models.ingestion import IngestionResult, IngestionStatistics, SourceFormat
from resume_tailor.parsers.markdown_cv import parse_markdown_cv


def ingest_markdown(path: Path) -> IngestionResult:
    raw = path.read_bytes()
    document = parse_markdown_cv(path)
    return IngestionResult(
        source_format=SourceFormat.MARKDOWN,
        document=document,
        statistics=IngestionStatistics(characters=len(raw.decode("utf-8"))),
        source_hash=f"sha256:{hashlib.sha256(raw).hexdigest()}",
    )
