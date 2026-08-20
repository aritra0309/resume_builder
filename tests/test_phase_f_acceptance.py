"""Synthetic cross-format and hostile-input regressions for the release boundary."""
# ruff: noqa: E501

from __future__ import annotations

import importlib.util
import zipfile
from pathlib import Path

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from resume_tailor.errors import ValidationError
from resume_tailor.evidence.ledger import build_evidence_ledger
from resume_tailor.ingestion.detector import detect_format, ingest_file
from resume_tailor.ingestion.pdf import ingest_pdf
from resume_tailor.ingestion.safety import safe_docx_members
from resume_tailor.models.ingestion import SourceFormat

TEXT = "Built a forecasting pipeline using Python and Prophet that reduced manual reporting by 40%."


def _write_docx(path: Path, *, hostile_member: str | None = None) -> None:
    """Build the smallest valid, deliberately synthetic OOXML container."""
    content_types = """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
 <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
 <Default Extension="xml" ContentType="application/xml"/>
 <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>"""
    relationships = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
 <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>"""
    document = f"""<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>
<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Projects</w:t></w:r></w:p>
<w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr></w:pPr><w:r><w:t>{TEXT}</w:t></w:r></w:p>
<w:sectPr/></w:body></w:document>"""
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", relationships)
        archive.writestr("word/document.xml", document)
        if hostile_member:
            archive.writestr(hostile_member, "synthetic")


def _write_pdf(path: Path) -> None:
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    stream = DecodedStreamObject()
    stream.set_data(f"BT /F1 12 Tf 72 720 Td (Projects) Tj 0 -20 Td ({TEXT}) Tj ET".encode())
    font = DictionaryObject(
        {
            NameObject("/F1"): DictionaryObject(
                {
                    NameObject("/Type"): NameObject("/Font"),
                    NameObject("/Subtype"): NameObject("/Type1"),
                    NameObject("/BaseFont"): NameObject("/Helvetica"),
                }
            )
        }
    )
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): font})
    page[NameObject("/Contents")] = writer._add_object(stream)
    with path.open("wb") as output:
        writer.write(output)


@pytest.mark.skipif(not importlib.util.find_spec("docx"), reason="requires resume-tailor[docx]")
def test_synthetic_markdown_docx_and_pdf_produce_equivalent_ledgers(tmp_path: Path) -> None:
    markdown = Path(__file__).parent / "fixtures" / "canonical_resume.md"
    docx = tmp_path / "canonical_resume.docx"
    pdf = tmp_path / "canonical_resume.pdf"
    _write_docx(docx)
    _write_pdf(pdf)

    ledgers = [
        build_evidence_ledger(ingest_file(source).document) for source in (markdown, docx, pdf)
    ]
    assert [{item.id: item.text for item in ledger.evidence} for ledger in ledgers] == [
        {ledgers[0].evidence[0].id: TEXT}
    ] * 3
    assert [ledger.evidence[0].source_location.format for ledger in ledgers] == [
        "markdown",
        "docx",
        "pdf",
    ]


def test_hostile_docx_members_are_rejected_before_parser_use(tmp_path: Path) -> None:
    macro = tmp_path / "hostile.docx"
    traversal = tmp_path / "traversal.docx"
    _write_docx(macro, hostile_member="word/vbaProject.bin")
    _write_docx(traversal, hostile_member="../outside.txt")

    with pytest.raises(ValidationError, match="macros"):
        safe_docx_members(macro)
    with pytest.raises(ValidationError, match="unsafe ZIP"):
        safe_docx_members(traversal)


def test_pdf_signature_mismatch_and_image_only_pdf_are_rejected(tmp_path: Path) -> None:
    mismatch = tmp_path / "not-really.pdf"
    mismatch.write_text("# Projects\n", encoding="utf-8")
    image_only = tmp_path / "image-only.pdf"
    PdfWriter().write(image_only)

    with pytest.raises(ValidationError, match="signature"):
        detect_format(mismatch)
    with pytest.raises(ValidationError, match="no extractable text"):
        ingest_pdf(image_only)
    assert (
        detect_format(Path(__file__).parent / "fixtures" / "canonical_resume.md")
        is SourceFormat.MARKDOWN
    )
