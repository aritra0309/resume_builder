"""Read-only environment diagnostics with actionable remediation."""

from __future__ import annotations

import importlib.util
import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from resume_tailor.config import config_path


@dataclass(frozen=True, slots=True)
class DiagnosticCheck:
    name: str
    ok: bool
    detail: str
    remediation: str | None = None
    required: bool = True


def _nearest_existing(path: Path) -> Path:
    current = path.expanduser().resolve(strict=False)
    while not current.exists() and current != current.parent:
        current = current.parent
    return current


def _writable_check(name: str, path: Path) -> DiagnosticCheck:
    existing = _nearest_existing(path)
    is_directory = existing.is_dir()
    writable = is_directory and os.access(existing, os.W_OK)
    detail = f"{path} (nearest existing directory: {existing})"
    remediation = None if writable else f"Create {path} or choose a writable directory."
    return DiagnosticCheck(name, writable, detail, remediation)


def _engine_version(executable: str) -> str:
    try:
        result = subprocess.run(
            [executable, "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "found, but version check failed"
    output = result.stdout or result.stderr
    first_line = output.splitlines()[0].strip() if output.splitlines() else "version unknown"
    return first_line[:160]


def run_diagnostics(
    *,
    output_dir: Path,
    configuration_path: Path | None = None,
) -> list[DiagnosticCheck]:
    """Collect diagnostics without creating files or changing the environment."""

    checks = [
        DiagnosticCheck(
            "Platform",
            True,
            f"{platform.system()} {platform.release()} ({platform.machine()})",
        ),
        DiagnosticCheck(
            "Python",
            sys.version_info >= (3, 11),
            platform.python_version(),
            None if sys.version_info >= (3, 11) else "Install Python 3.11 or newer.",
        ),
        _writable_check("Configuration directory", (configuration_path or config_path()).parent),
        _writable_check("Output directory", output_dir),
    ]

    engine_found = False
    for engine in ("tectonic", "xelatex", "pdflatex"):
        executable = shutil.which(engine)
        if executable:
            engine_found = True
            checks.append(
                DiagnosticCheck(f"TeX engine: {engine}", True, _engine_version(executable))
            )
        else:
            checks.append(
                DiagnosticCheck(
                    f"TeX engine: {engine}",
                    False,
                    "not found on PATH",
                    f"Install {engine} or choose another supported TeX engine.",
                    required=False,
                )
            )
    checks.append(
        DiagnosticCheck(
            "Usable TeX engine",
            engine_found,
            "at least one supported engine found" if engine_found else "none found",
            None
            if engine_found
            else "Install Tectonic or TeX Live, then ensure its executable is on PATH.",
        )
    )

    for module, install_hint in (
        ("keyring", "Reinstall resume-tailor to enable OS-keyring credentials."),
        ("litellm", "Reinstall resume-tailor to enable remote-provider generation."),
        ("pypdf", "Reinstall resume-tailor to enable PDF ingestion."),
        ("docx", "Install DOCX support with: pip install 'resume-tailor[docx]'."),
    ):
        available = importlib.util.find_spec(module) is not None
        checks.append(
            DiagnosticCheck(
                f"Optional Python package: {module}",
                available,
                "installed" if available else "not installed",
                None if available else install_hint,
                required=False,
            )
        )
    return checks


def required_checks_pass(checks: list[DiagnosticCheck]) -> bool:
    return all(check.ok for check in checks if check.required)
