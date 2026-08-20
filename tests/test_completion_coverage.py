"""Synthetic regression cases for archive, adapter, and review boundaries."""

from __future__ import annotations

import os
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from pypdf import PdfWriter

from resume_tailor.errors import ValidationError
from resume_tailor.ingestion import docx as docx_module
from resume_tailor.ingestion import pdf as pdf_module
from resume_tailor.ingestion import safety
from resume_tailor.models.cv import EvidenceItem, EvidenceLedger, SourceLocation
from resume_tailor.models.generation import (
    ContentPlan,
    GroundedText,
    PlannedBullet,
    PlannedEntry,
    PlannedSection,
)
from resume_tailor.models.review import DecisionAction, ReviewSession
from resume_tailor.review.checkpoint import (
    assert_checkpoint_matches,
    load_checkpoint,
    save_checkpoint,
)
from resume_tailor.review.session import ReviewController


def _zip(path: Path, members: dict[str, bytes], *, compression: int = zipfile.ZIP_DEFLATED) -> None:
    with zipfile.ZipFile(path, "w", compression=compression) as archive:
        for name, content in members.items():
            archive.writestr(name, content)


def _safe_members(path: Path) -> dict[str, bytes]:
    return {"[Content_Types].xml": b"x", "word/document.xml": b"<document/>"}


@pytest.mark.parametrize(
    ("members", "message"),
    [
        ({"/absolute": b"x", "word/document.xml": b"x"}, "unsafe ZIP member"),
        ({"../traversal": b"x", "word/document.xml": b"x"}, "unsafe ZIP member"),
        ({"word/vbaProject.bin": b"x", "word/document.xml": b"x"}, "macros"),
        ({"word/embeddings/item.bin": b"x", "word/document.xml": b"x"}, "macros"),
        ({"word/oleObject1.bin": b"x", "word/document.xml": b"x"}, "macros"),
        ({"[Content_Types].xml": b"x"}, "missing word/document.xml"),
    ],
)
def test_docx_archive_rejects_unsafe_members(
    tmp_path: Path, members: dict[str, bytes], message: str
) -> None:
    path = tmp_path / "fixture.docx"
    _zip(path, members)
    with pytest.raises(ValidationError, match=message):
        safety.safe_docx_members(path)


def test_docx_archive_rejects_size_count_duplicate_and_compression_limits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "fixture.docx"
    _zip(path, _safe_members(path), compression=zipfile.ZIP_STORED)
    assert safety.safe_docx_members(path)
    monkeypatch.setattr(safety, "MAX_BYTES", 1)
    with pytest.raises(ValidationError, match="size limit"):
        safety.safe_docx_members(path)
    monkeypatch.setattr(safety, "MAX_BYTES", 20_000_000)
    _zip(path, {**_safe_members(path), "extra": b"x"})
    monkeypatch.setattr(safety, "MAX_ENTRIES", 2)
    with pytest.raises(ValidationError, match="too many"):
        safety.safe_docx_members(path)
    monkeypatch.setattr(safety, "MAX_ENTRIES", 2_000)
    monkeypatch.setattr(safety, "MAX_UNCOMPRESSED", 1)
    with pytest.raises(ValidationError, match=r"unsafe compressed member|extraction limit"):
        safety.safe_docx_members(path)
    monkeypatch.setattr(safety, "MAX_UNCOMPRESSED", 80_000_000)
    _zip(path, {"word/document.xml": b"x" * 10_000})
    monkeypatch.setattr(safety, "MAX_COMPRESSION_RATIO", 1)
    with pytest.raises(ValidationError, match="unsafe compressed member"):
        safety.safe_docx_members(path)


def test_docx_archive_rejects_malformed_and_duplicate_names(tmp_path: Path) -> None:
    bad = tmp_path / "bad.docx"
    bad.write_bytes(b"not a zip")
    with pytest.raises(ValidationError, match="malformed"):
        safety.safe_docx_members(bad)
    duplicate = tmp_path / "duplicate.docx"
    with (
        pytest.warns(UserWarning, match="Duplicate name"),
        zipfile.ZipFile(duplicate, "w") as archive,
    ):
        archive.writestr("word/document.xml", b"x")
        archive.writestr("word/document.xml", b"x")
    with pytest.raises(ValidationError, match="unsafe ZIP member"):
        safety.safe_docx_members(duplicate)


class _Paragraph:
    def __init__(self, text: str, style: str = "Normal", numbered: bool = False) -> None:
        self.text, self.style = text, SimpleNamespace(name=style)
        self._p = SimpleNamespace(pPr=SimpleNamespace(numPr=object()) if numbered else None)


def _doc(*paragraphs: _Paragraph, tables: list[object] | None = None) -> object:
    return SimpleNamespace(paragraphs=list(paragraphs), tables=tables or [])


def test_docx_adapter_parses_sections_bullets_tables_and_warnings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "fixture.docx"
    _zip(path, _safe_members(path))
    table = SimpleNamespace(rows=[SimpleNamespace(cells=[SimpleNamespace(text="cell fact")])])
    fake = _doc(
        _Paragraph("preface"),
        _Paragraph("Experience"),
        _Paragraph("built Python API", "List Bullet"),
        _Paragraph("numbered SQL", numbered=True),
        _Paragraph("plain detail"),
        _Paragraph("Odd heading", "Heading 1"),
        tables=[table],
    )
    monkeypatch.setattr(docx_module, "safe_docx_members", lambda _: [])
    with patch("docx.Document", return_value=fake):
        result = docx_module.ingest_docx(path)
    assert [section.name for section in result.document.sections] == [
        "unknown",
        "experience",
        "unknown",
    ]
    assert [bullet.text for bullet in result.document.sections[1].entries[0].bullets] == [
        "built Python API",
        "numbered SQL",
    ]
    assert result.document.sections[-1].entries[0].text == ["cell fact"]
    assert result.warnings[0].code == "unknown_heading"
    assert result.statistics.paragraphs == 6 and result.statistics.tables == 1
    assert result.source_hash.startswith("sha256:")


def test_docx_adapter_errors_and_empty_document(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "fixture.docx"
    _zip(path, _safe_members(path))
    monkeypatch.setattr(docx_module, "safe_docx_members", lambda _: [])
    with (
        patch("docx.Document", side_effect=RuntimeError("bad parser")),
        pytest.raises(ValidationError, match="could not parse"),
    ):
        docx_module.ingest_docx(path)
    with (
        patch("docx.Document", return_value=_doc()),
        pytest.raises(ValidationError, match="no extractable"),
    ):
        docx_module.ingest_docx(path)


class _Page:
    def __init__(self, text: str | Exception) -> None:
        self.text = text

    def extract_text(self) -> str:
        if isinstance(self.text, Exception):
            raise self.text
        return self.text


def _reader(*pages: _Page, encrypted: bool = False) -> object:
    return SimpleNamespace(pages=list(pages), is_encrypted=encrypted)


def test_pdf_adapter_safety_and_text_mapping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "fixture.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    with path.open("wb") as stream:
        writer.write(stream)
    with pytest.raises(ValidationError, match="size limit"):
        pdf_module.ingest_pdf(path, max_bytes=1)
    monkeypatch.setattr(pdf_module, "PdfReader", lambda _: _reader(_Page("x"), _Page("y")))
    with pytest.raises(ValidationError, match="page safety"):
        pdf_module.ingest_pdf(path, max_pages=1)
    monkeypatch.setattr(
        pdf_module,
        "PdfReader",
        lambda _: _reader(
            _Page("intro\nExperience\n• build Python\n- ship SQL\n* test"), _Page("Skills\nPython")
        ),
    )
    result = pdf_module.ingest_pdf(path)
    assert [section.name for section in result.document.sections] == [
        "unknown",
        "experience",
        "skills",
    ]
    assert [bullet.text for bullet in result.document.sections[1].entries[0].bullets] == [
        "build Python",
        "ship SQL",
        "test",
    ]
    assert result.warnings[0].requires_acknowledgement
    assert result.document.sections[-1].source_location.locator == "p:2/l:1"
    assert result.statistics.pages == 2 and result.statistics.characters > 0


def test_pdf_adapter_parser_encryption_extraction_and_blank_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "fixture.pdf"
    path.write_bytes(b"%PDF-synthetic")
    monkeypatch.setattr(pdf_module, "PdfReader", lambda _: (_ for _ in ()).throw(RuntimeError()))
    with pytest.raises(ValidationError, match="corrupt"):
        pdf_module.ingest_pdf(path)
    monkeypatch.setattr(pdf_module, "PdfReader", lambda _: _reader(_Page("x"), encrypted=True))
    with pytest.raises(ValidationError, match="encrypted"):
        pdf_module.ingest_pdf(path)
    monkeypatch.setattr(pdf_module, "PdfReader", lambda _: _reader(_Page(RuntimeError())))
    with pytest.raises(ValidationError, match="extraction failed"):
        pdf_module.ingest_pdf(path)
    monkeypatch.setattr(pdf_module, "PdfReader", lambda _: _reader(_Page("")))
    with pytest.raises(ValidationError, match="no extractable text"):
        pdf_module.ingest_pdf(path)


@pytest.fixture
def review_controller() -> ReviewController:
    loc = SourceLocation(file="synthetic.md", line=1)
    ledger = EvidenceLedger(
        evidence=[
            EvidenceItem(
                id="fact.one",
                section="Projects",
                text="Built Python API in 2024.",
                normalized_text="built python api in 2024.",
                source_location=loc,
            ),
            EvidenceItem(
                id="fact.two",
                section="Projects",
                text="Improved SQL reports.",
                normalized_text="improved sql reports.",
                source_location=loc,
            ),
        ]
    )
    plan = ContentPlan(
        target_title="Engineer",
        summary=GroundedText(text="Built Python API in 2024.", evidence_ids=["fact.one"]),
        sections=[
            PlannedSection(
                name="Projects",
                entries=[
                    PlannedEntry(
                        heading="Demo",
                        bullets=[
                            PlannedBullet(
                                text="Built Python API in 2024.", evidence_ids=["fact.one"]
                            ),
                            PlannedBullet(text="Improved SQL reports.", evidence_ids=["fact.two"]),
                        ],
                    )
                ],
            )
        ],
    )
    return ReviewController(
        plan,
        "sha256:" + "a" * 64,
        ledger,
        source_hash="sha256:" + "b" * 64,
        job_hash="sha256:" + "c" * 64,
    )


def test_review_decision_validation_ordering_and_approval(
    review_controller: ReviewController,
) -> None:
    controller = review_controller
    with pytest.raises(ValueError, match="unknown claim"):
        controller.decide("missing", DecisionAction.APPROVE)
    with pytest.raises(ValueError, match="unknown evidence"):
        controller.decide("summary/1", DecisionAction.APPROVE, evidence_ids=["missing"])
    with pytest.raises(ValueError, match="non-empty"):
        controller.decide("summary/1", DecisionAction.EDIT, text="\x01")
    with pytest.raises(ValueError, match="invalid review edit"):
        controller.decide("summary/1", DecisionAction.EDIT, text="Built Java API in 2024.")
    ids = list(controller.claims)
    controller.decide(ids[2], DecisionAction.REJECT)
    controller.decide(ids[1], DecisionAction.RESTORE_SOURCE)
    controller.decide(ids[0], DecisionAction.EDIT, text="Built Python API in 2024.")
    controller.decide(ids[0], DecisionAction.APPROVE)
    assert [item.claim_id for item in controller.session.decisions] == ids
    assert controller.session.revision == 4
    reviewed = controller.approve()
    assert (
        reviewed.validation_passed
        and reviewed.counts.approved == 1
        and reviewed.counts.rejected == 1
    )


def test_review_undo_redo_and_approval_boundaries(review_controller: ReviewController) -> None:
    controller = review_controller
    with pytest.raises(ValueError, match="nothing to undo"):
        controller.undo()
    with pytest.raises(ValueError, match="nothing to redo"):
        controller.redo()
    controller.decide("summary/1", DecisionAction.APPROVE)
    controller.undo()
    controller.redo()
    controller.decide("projects/demo/1", DecisionAction.DEFER)
    controller.decide("projects/demo/2", DecisionAction.APPROVE)
    with pytest.raises(ValueError, match="pending or deferred"):
        controller.approve()


def test_checkpoint_corruption_save_failures_and_hash_mismatches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, review_controller: ReviewController
) -> None:
    session = review_controller.session
    path = tmp_path / "review.json"
    save_checkpoint(path, session)
    if os.name != "nt":
        assert os.stat(path).st_mode & 0o777 == 0o600
    assert load_checkpoint(path) == session
    for value in ("{", "[]"):
        path.write_text(value)
        with pytest.raises(ValueError, match="corrupt"):
            load_checkpoint(path)
    with pytest.raises(ValueError, match="corrupt"):
        load_checkpoint(tmp_path / "missing.json")
    for kwargs in (
        {"source_hash": "sha256:" + "0" * 64},
        {"job_hash": "sha256:" + "0" * 64},
        {"base_plan_hash": "sha256:" + "0" * 64},
    ):
        values = {
            "source_hash": session.source_hash,
            "job_hash": session.job_hash,
            "base_plan_hash": session.base_plan_hash,
        }
        values.update(kwargs)
        with pytest.raises(ValueError, match="does not match"):
            assert_checkpoint_matches(session, **values)  # type: ignore[arg-type]
    monkeypatch.setattr(Path, "open", lambda *args, **kwargs: (_ for _ in ()).throw(OSError()))
    with pytest.raises(ValueError, match="could not save"):
        save_checkpoint(tmp_path / "broken.json", session)
    no_hashes = ReviewSession(
        review_id=session.review_id, run_id=session.run_id, base_plan_hash=session.base_plan_hash
    )
    with pytest.raises(ValueError, match="requires source_hash"):
        save_checkpoint(tmp_path / "none.json", no_hashes)
