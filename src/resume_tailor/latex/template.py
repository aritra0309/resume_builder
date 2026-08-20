"""The intentionally small, ATS-safe LaTeX template contract."""

from __future__ import annotations

PACKAGE_ALLOWLIST = frozenset(
    {
        "geometry", "enumitem", "hyperref", "lmodern", "fontenc", "inputenc", "array",
        "helvet", "microtype", "titlesec", "xcolor",
    }
)
COMMAND_ALLOWLIST = frozenset(
    {
        "begin",
        "centering",
        "cdot",
        "documentclass",
        "end",
        "href",
        "hypersetup",
        "item",
        "large",
        "Large",
        "Huge",
        "maketitle",
        "noindent",
        "pagenumbering",
        "pagestyle",
        "renewcommand",
        "section",
        "small",
        "setlist",
        "textbackslash",
        "textasciicircum",
        "textasciitilde",
        "textbf",
        "textit",
        "texttt",
        "textnormal",
        "titleformat",
        "titlespacing",
        "titlerule",
        "title",
        "usepackage", "vspace", "hfill", "enspace", "hspace", "MakeUppercase", "setlength",
        "newlist", "tabular", "arraybackslash", "bfseries", "itshape", "normalfont", "normalsize",
        "familydefault", "parindent", "parskip", "sfdefault", "triangleright", "diamond",
        "itemsep", "parsep", "topsep", "partopsep", "leftmargin", "label",
    }
)
FORBIDDEN_COMMANDS = frozenset(
    {
        "catcode",
        "csname",
        "def",
        "directlua",
        "every",
        "immediate",
        "include",
        "input",
        "loop",
        "newcommand",
        "openin",
        "openout",
        "read",
        "write",
        "write18",
    }
)
TEMPLATE_CONTRACT = (
    "A deterministic renderer owns the visual template. Do not generate LaTeX."
)


def document_preamble(title: str) -> str:
    return rf"""\documentclass[10pt]{{article}}
\usepackage[margin=0.65in]{{geometry}}
\usepackage{{enumitem}}
\usepackage[hidelinks]{{hyperref}}
\usepackage{{lmodern}}
\setlist[itemize]{{leftmargin=*,nosep}}
\pagenumbering{{gobble}}
\title{{{title}}}
\begin{{document}}
\maketitle
"""
