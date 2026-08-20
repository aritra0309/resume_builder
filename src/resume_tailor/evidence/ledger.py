"""Create stable atomic evidence from parsed CV data."""

from __future__ import annotations

import hashlib
import re

from resume_tailor.evidence.normalizer import normalized_text, numbers, terms
from resume_tailor.models.cv import CVDocument, EvidenceItem, EvidenceLedger, Person


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", ".", value.lower()).strip(".")
    return slug or "entry"


def _id(section: str, heading: str, text: str) -> str:
    digest = hashlib.sha256(normalized_text(text).encode()).hexdigest()[:10]
    return f"{_slug(section)}.{_slug(heading)}.{digest}"


def build_evidence_ledger(document: CVDocument) -> EvidenceLedger:
    """Atomize bullets and protected non-bullet entry facts in source order."""

    evidence: list[EvidenceItem] = []
    name: str | None = None
    links: list[str] = []
    email: str | None = None
    phone: str | None = None
    for section in document.sections:
        # Contact information is presentation metadata, never evidence for a claim.
        if section.name == "contact":
            values = [value for entry in section.entries for value in entry.text]
            if values:
                name = re.sub(r"[*_`]+", "", values[0]).strip()
            contact_text = " ".join(values)
            email_match = re.search(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", contact_text)
            phone_match = re.search(r"\+?[0-9][0-9 ()-]{7,}[0-9]", contact_text)
            email = email_match.group(0) if email_match else None
            phone = phone_match.group(0) if phone_match else None
            links.extend(re.findall(r"https://[^\s)>]+", contact_text))
            continue
        for entry in section.entries:
            if name is None and section.level == 1 and section.name == "projects":
                name = entry.heading
            for item in entry.bullets:
                evidence.append(
                    EvidenceItem(
                        id=_id(section.name, entry.heading, item.text),
                        section=section.name,
                        text=item.text,
                        normalized_text=normalized_text(item.text),
                        organization=entry.organization,
                        date_range=entry.date_range,
                        entities=terms(item.text),
                        numbers=numbers(item.text),
                        source_location=item.source_location,
                    )
                )
            # Retain protected facts (degree, date, certification) even when they are prose.
            for offset, value in enumerate(entry.text):
                plain = re.sub(r"[*_`]+", "", value).strip()
                if not plain or plain.lower().startswith("keywords:"):
                    continue
                if entry.source_location.line is None:
                    # Non-line-oriented adapters provide a more precise child
                    # anchor themselves; retain the entry anchor as a safe
                    # compatibility fallback until then.
                    location = entry.source_location
                else:
                    location = entry.source_location.model_validate(
                        {
                            **entry.source_location.model_dump(),
                            "line": entry.source_location.line + offset + 1,
                            "locator": None,
                            "label": None,
                        }
                    )
                evidence.append(
                    EvidenceItem(
                        id=_id(section.name, entry.heading, plain),
                        section=section.name,
                        text=plain,
                        normalized_text=normalized_text(plain),
                        organization=entry.organization,
                        date_range=entry.date_range,
                        entities=terms(plain),
                        numbers=numbers(plain),
                        source_location=location,
                    )
                )
    if not evidence:
        raise ValueError("CV produced no evidence items")
    # Stable sort makes both order and JSON serialization independent of incidental parser ordering.
    evidence.sort(
        key=lambda item: (item.source_location.file, item.source_location.line or 0, item.id)
    )
    return EvidenceLedger(
        person=Person(
            name=name,
            email=email,
            phone=phone,
            links=links,
        ),
        evidence=evidence,
    )
