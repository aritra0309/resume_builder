"""Turn common TeX output into concise fixes."""

from __future__ import annotations


def diagnose_compiler_output(output: str) -> str:
    lowered = output.lower()
    if "file `" in lowered and "not found" in lowered:
        return "Install the missing TeX package or use the fixed template packages."
    if "font" in lowered and ("not found" in lowered or "cannot" in lowered):
        return "Install the requested font or use pdfLaTeX with the built-in Latin Modern font."
    if "overfull" in lowered:
        return "Shorten the affected bullet or reduce low-relevance content."
    if "emergency stop" in lowered or "fatal error" in lowered or "! " in output:
        return (
            "Check the generated LaTeX near the reported line for unbalanced braces "
            "or unsupported syntax."
        )
    return "Review the TeX log and confirm the selected engine is installed."
