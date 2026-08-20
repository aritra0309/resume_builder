"""Bounded, read-only checks for OOXML containers."""

from __future__ import annotations

import posixpath
import zipfile
from pathlib import Path

from resume_tailor.errors import ValidationError

MAX_BYTES = 20_000_000
MAX_ENTRIES = 2_000
MAX_UNCOMPRESSED = 80_000_000
MAX_COMPRESSION_RATIO = 100


def safe_docx_members(path: Path) -> list[zipfile.ZipInfo]:
    if path.stat().st_size > MAX_BYTES:
        raise ValidationError("DOCX exceeds safety size limit")
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_ENTRIES:
                raise ValidationError("DOCX has too many ZIP entries")
            names: set[str] = set()
            for info in infos:
                name = posixpath.normpath(info.filename)
                if name.startswith("/") or name.startswith("../") or name in names:
                    raise ValidationError("DOCX contains unsafe ZIP member paths")
                names.add(name)
                if info.file_size > MAX_UNCOMPRESSED:
                    raise ValidationError("DOCX contains an unsafe compressed member")
                if (
                    info.compress_size > 0
                    and info.file_size / info.compress_size > MAX_COMPRESSION_RATIO
                ):
                    raise ValidationError("DOCX contains an unsafe compressed member")
            if sum(info.file_size for info in infos) > MAX_UNCOMPRESSED:
                raise ValidationError("DOCX exceeds extraction limit")
            if "word/document.xml" not in names:
                raise ValidationError("DOCX is missing word/document.xml")
            forbidden = ("vbaProject", "embeddings/", "oleObject")
            if any(any(token in name for token in forbidden) for name in names):
                raise ValidationError("DOCX macros or embedded objects are unsupported")
            return infos
    except zipfile.BadZipFile as exc:
        raise ValidationError("DOCX container is malformed") from exc
