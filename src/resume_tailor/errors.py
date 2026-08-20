"""Domain errors and stable process exit codes."""

from __future__ import annotations

from enum import IntEnum


class ExitCode(IntEnum):
    """Stable command exit codes documented in the product plan."""

    SUCCESS = 0
    INTERNAL = 1
    USAGE = 2
    CREDENTIAL = 3
    PROVIDER = 4
    GROUNDING = 5
    LATEX = 6
    COMPILER = 7
    VALIDATION = 8
    CANCELLED = 9


class ResumeTailorError(Exception):
    """Base class for expected, user-actionable failures."""

    exit_code = ExitCode.INTERNAL

    def __init__(self, message: str, *, hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint


class UsageError(ResumeTailorError):
    exit_code = ExitCode.USAGE


class ConfigError(UsageError):
    """Configuration could not be parsed or validated."""


class CredentialError(ResumeTailorError):
    exit_code = ExitCode.CREDENTIAL


class ProviderError(ResumeTailorError):
    exit_code = ExitCode.PROVIDER


class GroundingError(ResumeTailorError):
    exit_code = ExitCode.GROUNDING


class LatexError(ResumeTailorError):
    exit_code = ExitCode.LATEX


class CompilerError(ResumeTailorError):
    exit_code = ExitCode.COMPILER


class ValidationError(ResumeTailorError):
    exit_code = ExitCode.VALIDATION


class CancelledError(ResumeTailorError):
    exit_code = ExitCode.CANCELLED
