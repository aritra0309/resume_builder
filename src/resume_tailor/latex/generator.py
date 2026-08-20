"""Prompt construction and no-cost fallback rendering for validated plans."""

from __future__ import annotations

import re

from resume_tailor.latex.sanitizer import escape_latex, sanitize_latex
from resume_tailor.latex.template import TEMPLATE_CONTRACT
from resume_tailor.models.cv import Person
from resume_tailor.models.generation import ContentPlan

LATEX_GENERATION_PROMPT_VERSION = "1"
_BOLD_MARKUP = re.compile(r"\*\*(.+?)\*\*")
_METRIC = re.compile(r"\b\d[\d,.]*(?:\s*(?:%|\+|x|tests?|years?|states?|datasets?))?", re.I)


def _resume_text(value: str, highlights: list[str] | None = None) -> str:
    """Render limited, source-provided emphasis without allowing arbitrary LaTex."""
    marked: list[str] = []

    def hold(match: re.Match[str]) -> str:
        marked.append(rf"\textbf{{{escape_latex(match.group(1))}}}")
        return f"@@BOLD{len(marked) - 1}@@"

    rendered = escape_latex(_BOLD_MARKUP.sub(hold, value))
    for index, token in enumerate(marked):
        rendered = rendered.replace(escape_latex(f"@@BOLD{index}@@"), token)
    terms = sorted(
        {term.strip() for term in (highlights or []) if term.strip()}, key=len, reverse=True
    )
    for term in terms + _METRIC.findall(value):
        escaped = escape_latex(term)
        if escaped:
            rendered = re.sub(
                re.escape(escaped),
                lambda _match: rf"\textbf{{{escaped}}}",
                rendered,
                flags=re.I,
            )
    return rendered


def _summary_text(value: str) -> str:
    """Keep summaries scannable even if a provider returns an overlong response."""
    text = _BOLD_MARKUP.sub(r"\1", value).strip()
    if "·" in text:
        first_sentence = re.search(
            r"\b(?:Built|Developed|Designed|Contributed|Created|Data)\b", text
        )
        if first_sentence:
            text = text[first_sentence.start() :]
    sentences = re.split(r"(?<=[.!?])\s+", text)
    return " ".join(sentences[:2])[:420].rstrip()


def latex_generation_messages(plan: ContentPlan) -> list[dict[str, str]]:
    """Give the model only validated resume data, never CV or JD source text."""
    return [
        {
            "role": "system",
            "content": "Return a complete LaTeX document only. " + TEMPLATE_CONTRACT,
        },
        {"role": "user", "content": plan.model_dump_json()},
    ]


def fallback_render(plan: ContentPlan, person: Person | None = None) -> str:
    """Render validated content in the fixed, ATS-readable house style."""
    person = person or Person()
    name = escape_latex(person.name or plan.target_title)
    contact = [value for value in (person.email, person.phone) if value]
    for link in person.links:
        if link.startswith("https://"):
            label = link.removeprefix("https://").removeprefix("www.").rstrip("/")
            contact.append(rf"\href{{{link}}}{{{escape_latex(label)}}}")
    rendered_contact = r"\enspace $\diamond$ \enspace ".join(
        escape_latex(item) if not item.startswith(r"\href") else item for item in contact
    )
    lines = [r"""\documentclass[10pt]{article}
\usepackage[T1]{fontenc}
\usepackage[utf8]{inputenc}
\usepackage[left=0.45in,top=0.30in,right=0.45in,bottom=0.34in]{geometry}
\usepackage{enumitem}
\usepackage{titlesec}
\usepackage[hidelinks]{hyperref}
\usepackage{microtype}
\usepackage{helvet}
\renewcommand{\familydefault}{\sfdefault}
\pagestyle{empty}
\setlength{\parindent}{0pt}
\setlength{\parskip}{0pt}
\titleformat{\section}{\normalfont\bfseries\normalsize}{}{0em}{\MakeUppercase}[\vspace{0.6pt}{\titlerule[0.5pt]}\vspace{1.8pt}]
\titlespacing*{\section}{0pt}{5pt}{2pt}
\newlist{cvitems}{itemize}{1}
\setlist[cvitems]{label={\small$\triangleright$},leftmargin=1.2em,itemsep=0.5pt,parsep=0pt,topsep=1pt,partopsep=0pt}
\begin{document}
\begin{center}
{\Huge\bfseries\MakeUppercase{""" + name + r"""}}\\[2pt]
{\small """ + rendered_contact + r"""}
\end{center}
\vspace{-3pt}
\section{Profile Summary}
""" + _resume_text(_summary_text(plan.summary.text)) + "\n"]
    for section in plan.sections:
        lines.append(rf"\section{{{escape_latex(section.name)}}}")
        for entry in section.entries:
            lines.append(rf"\textbf{{{escape_latex(entry.heading)}}}\\[-1pt]")
            lines.append(r"\begin{cvitems}")
            lines.extend(
                rf"\item {_resume_text(bullet.text, bullet.matched_keywords)}"
                for bullet in entry.bullets
            )
            lines.append(r"\end{cvitems}")
    lines.append(r"\end{document}")
    return sanitize_latex("\n".join(lines))
