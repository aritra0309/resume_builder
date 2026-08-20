"""Validate untrusted model LaTeX before it reaches a compiler."""

from __future__ import annotations

import re

from resume_tailor.errors import LatexError
from resume_tailor.latex.template import COMMAND_ALLOWLIST, FORBIDDEN_COMMANDS, PACKAGE_ALLOWLIST
from resume_tailor.models.generation import ContentPlan

MAX_LATEX_BYTES = 100_000
_COMMAND = re.compile(r"\\([A-Za-z@]+)")
_PACKAGE = re.compile(r"\\usepackage(?:\[[^\]]*\])?\{([^}]+)\}")
_FENCE = re.compile(r"^\s*```(?:latex|tex)?\s*\n?|\n?\s*```\s*$", re.IGNORECASE)
_WORDS = re.compile(r"[a-z0-9][a-z0-9+.#/-]*", re.IGNORECASE)


def escape_latex(value: str) -> str:
    """Escape factual data before interpolation into the deterministic template."""
    replacements = {
        "\\": r"\textbackslash{}",
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_",
        "{": r"\{",
        "}": r"\}",
        "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
        # Use an ASCII separator: Helvetica renders the Unicode middle dot
        # inconsistently, and a text-mode math dot can become a stray glyph.
        "·": " - ",
        "\u2013": "--",
        "\u2014": "---",
    }
    return "".join(replacements.get(character, character) for character in value)


def sanitize_latex(value: str, *, max_bytes: int = MAX_LATEX_BYTES) -> str:
    """Return a contract-compliant document or raise before any subprocess starts."""
    document = _FENCE.sub("", value).strip()
    if not document:
        raise LatexError("LaTeX output is empty.")
    if len(document.encode("utf-8")) > max_bytes:
        raise LatexError(f"LaTeX output exceeds the {max_bytes}-byte safety limit.")
    if "\x00" in document or "\r" in document:
        raise LatexError("LaTeX output contains disallowed control characters.")
    if "\\begin{document}" not in document or "\\end{document}" not in document:
        raise LatexError("LaTeX must contain exactly one complete document environment.")
    if (
        document.count("\\begin{document}") != 1
        or document.count("\\end{document}") != 1
        or document.find("\\begin{document}") > document.find("\\end{document}")
    ):
        raise LatexError("LaTeX must contain exactly one ordered document environment.")
    commands = set(_COMMAND.findall(document))
    forbidden = sorted(commands & FORBIDDEN_COMMANDS)
    if forbidden:
        raise LatexError(f"LaTeX uses forbidden command(s): {', '.join(forbidden)}.")
    unknown = sorted(commands - COMMAND_ALLOWLIST)
    if unknown:
        raise LatexError(
            f"LaTeX uses command(s) outside the template contract: {', '.join(unknown)}."
        )
    packages = {name.strip() for match in _PACKAGE.findall(document) for name in match.split(",")}
    blocked = sorted(packages - PACKAGE_ALLOWLIST)
    if blocked:
        raise LatexError(f"LaTeX uses package(s) outside the allowlist: {', '.join(blocked)}.")
    if "\\documentclass" not in document:
        raise LatexError("LaTeX must declare a document class.")
    return document + "\n"


def validate_latex_against_plan(document: str, plan: ContentPlan) -> None:
    """Reject prose words that are not present in the validated content plan.

    This deliberately permits a few structural resume labels but prevents a model from
    slipping unsupported claims into an otherwise syntactically valid document.
    """
    body = document.partition("\\begin{document}")[2].partition("\\end{document}")[0]
    body = re.sub(r"\\[A-Za-z@]+(?:\[[^]]*\])?", " ", body)
    allowed_text = " ".join(
        [
            plan.target_title,
            plan.summary.text,
            *[section.name for section in plan.sections],
            *[entry.heading for section in plan.sections for entry in section.entries],
            *[
                bullet.text
                for section in plan.sections
                for entry in section.entries
                for bullet in entry.bullets
            ],
            "profile summary experience education skills projects certifications",
        ]
    )
    allowed = {word.casefold() for word in _WORDS.findall(allowed_text)}
    actual = {word.casefold() for word in _WORDS.findall(body)}
    unsupported = sorted(actual - allowed)
    if unsupported:
        raise LatexError(
            "LaTeX contains prose outside the validated content plan: "
            + ", ".join(unsupported[:10])
            + "."
        )
