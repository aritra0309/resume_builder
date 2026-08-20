"""Select and run local TeX engines without shell execution."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from resume_tailor.errors import CompilerError, UsageError
from resume_tailor.latex.diagnostics import diagnose_compiler_output
from resume_tailor.latex.sanitizer import sanitize_latex

ENGINE_NAMES = ("tectonic", "xelatex", "pdflatex")


@dataclass(frozen=True, slots=True)
class CompilationResult:
    engine: str
    pdf_path: Path
    log: str


def discover_engines(*, path: str | None = None) -> dict[str, str]:
    suffix = ".exe" if os.name == "nt" else ""
    return {
        engine: executable
        for engine in ENGINE_NAMES
        if (executable := shutil.which(engine + suffix, path=path)) is not None
    }


def select_engine(requested: str = "auto", *, path: str | None = None) -> tuple[str, str]:
    found = discover_engines(path=path)
    if requested == "auto":
        for engine in ENGINE_NAMES:
            if engine in found:
                return engine, found[engine]
        raise CompilerError(
            "No supported TeX engine was found.", hint="Install Tectonic or TeX Live."
        )
    if requested not in ENGINE_NAMES:
        raise UsageError(f"Unsupported TeX engine: {requested}.")
    if requested not in found:
        raise CompilerError(f"Requested TeX engine '{requested}' was not found on PATH.")
    return requested, found[requested]


def _arguments(engine: str, executable: str, tex: Path, output: Path) -> list[str]:
    if engine == "tectonic":
        return [
            executable,
            "--outdir",
            str(output),
            "--keep-logs",
            "--keep-intermediates",
            str(tex),
        ]
    return [
        executable,
        "-interaction=nonstopmode",
        "-halt-on-error",
        "-no-shell-escape",
        f"-output-directory={output}",
        str(tex),
    ]


def compile_latex(
    latex: str,
    output_path: Path,
    *,
    engine: str = "auto",
    timeout_seconds: int = 45,
    max_passes: int = 1,
) -> CompilationResult:
    """Compile a sanitized document in a private directory and copy only its PDF."""
    if timeout_seconds < 1 or max_passes != 1:
        raise UsageError("Compilation timeout must be positive and max_passes must be exactly 1.")
    sanitized = sanitize_latex(latex)
    engine_name, executable = select_engine(engine)
    target = output_path.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise CompilerError(f"Refusing to overwrite existing PDF: {target}")
    with tempfile.TemporaryDirectory(prefix="resume-tailor-tex-") as temporary:
        work = Path(temporary)
        source = work / "resume.tex"
        source.write_text(sanitized, encoding="utf-8")
        result_dir = work / "out"
        result_dir.mkdir()
        try:
            completed = subprocess.run(
                _arguments(engine_name, executable, source, result_dir),
                cwd=work,
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired as error:
            raise CompilerError(
                "TeX compilation timed out.", hint="Shorten the resume or inspect the template."
            ) from error
        log = (completed.stdout + "\n" + completed.stderr)[-20_000:]
        pdf = result_dir / "resume.pdf"
        if completed.returncode != 0 or not pdf.is_file():
            raise CompilerError("TeX compilation failed.", hint=diagnose_compiler_output(log))
        shutil.copyfile(pdf, target)
        return CompilationResult(engine_name, target, log)
