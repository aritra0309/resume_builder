"""Validate the produced PDF without sending it anywhere."""

from __future__ import annotations

from pathlib import Path

from pypdf import PdfReader

from resume_tailor.models.validation import ValidationIssue, ValidationReport

STANDARD_HEADINGS = ("summary", "experience", "education", "skills", "projects")
MIN_ONE_PAGE_CONTENT_DEPTH = 0.86


def _lowest_text_baseline(page: object) -> float | None:
    """Return the lowest text baseline in PDF coordinates, including page transforms."""
    baselines: list[float] = []

    def collect(text: str, cm: list[float], tm: list[float], *_: object) -> None:
        if text.strip():
            baselines.append(float(cm[5]) + float(tm[5]))

    page.extract_text(visitor_text=collect)
    return min(baselines) if baselines else None


def validate_pdf(
    path: Path, *, max_pages: int = 2, min_one_page_content_depth: float | None = None
) -> ValidationReport:
    issues: list[ValidationIssue] = []
    try:
        raw = path.read_bytes()
    except OSError as error:
        return ValidationReport(
            passed=False,
            issues=[ValidationIssue(code="pdf_unreadable", message=str(error), severity="error")],
        )
    if not raw.startswith(b"%PDF-"):
        return ValidationReport(
            passed=False,
            issues=[
                ValidationIssue(
                    code="pdf_signature", message="Output is not a PDF file.", severity="error"
                )
            ],
        )
    try:
        reader = PdfReader(path)
        pages = list(reader.pages)
        text_by_page = [page.extract_text(extraction_mode="layout") or "" for page in pages]
    except Exception as error:  # pypdf has several parser-specific exception classes.
        return ValidationReport(
            passed=False,
            issues=[
                ValidationIssue(
                    code="pdf_parse", message=f"PDF could not be parsed: {error}", severity="error"
                )
            ],
        )
    text = "\n".join(text_by_page).strip()
    if not text:
        issues.append(
            ValidationIssue(
                code="pdf_no_text", message="PDF has no extractable text.", severity="error"
            )
        )
    if len(pages) > max_pages:
        issues.append(
            ValidationIssue(
                code="page_count",
                message=f"PDF has {len(pages)} pages; target is at most {max_pages}.",
                severity="error",
            )
        )
    if len(pages) == 0:
        issues.append(
            ValidationIssue(code="pdf_empty", message="PDF contains no pages.", severity="error")
        )
    if min_one_page_content_depth is not None and len(pages) == 1:
        if not 0 < min_one_page_content_depth < 1:
            raise ValueError("min_one_page_content_depth must be between zero and one")
        lowest = _lowest_text_baseline(pages[0])
        height = float(pages[0].mediabox.height)
        if lowest is not None and lowest > height * (1 - min_one_page_content_depth):
            used = round((1 - lowest / height) * 100)
            issues.append(
                ValidationIssue(
                    code="page_underfilled",
                    message=(
                        f"One-page resume uses only about {used}% of the page height; "
                        "add grounded content or reduce the requested page count."
                    ),
                    severity="error",
                )
            )
    normalized = text.casefold()
    missing = [heading for heading in STANDARD_HEADINGS if heading not in normalized]
    if missing:
        issues.append(
            ValidationIssue(
                code="headings_missing",
                message=f"Standard headings absent: {', '.join(missing)}.",
                severity="warning",
            )
        )
    if any(not item.strip() for item in text_by_page):
        issues.append(
            ValidationIssue(
                code="reading_order",
                message="At least one page has no extractable reading-order text.",
                severity="warning",
            )
        )
    for page in pages:
        for annotation in page.get("/Annots", []):
            entry = annotation.get_object()
            action = entry.get("/A")
            uri = action.get("/URI") if action else None
            if uri and not str(uri).startswith(("https://", "mailto:")):
                issues.append(
                    ValidationIssue(
                        code="unsafe_link",
                        message=f"PDF contains non-web link: {uri}",
                        severity="warning",
                    )
                )
    return ValidationReport(
        passed=not any(issue.severity == "error" for issue in issues), issues=issues
    )
