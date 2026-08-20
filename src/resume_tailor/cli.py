"""Resume Tailor command-line interface."""

from __future__ import annotations

import json
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import click
import typer
from rich.console import Console
from rich.table import Table

from resume_tailor import __version__
from resume_tailor.config import config_path, load_config, serializable_config
from resume_tailor.credentials import CredentialManager, environment_variable
from resume_tailor.doctor import required_checks_pass, run_diagnostics
from resume_tailor.errors import ExitCode, ResumeTailorError, UsageError, ValidationError
from resume_tailor.generation import GenerationRequest, IngestionBundle, PlanDraft
from resume_tailor.generation import generate as run_generation
from resume_tailor.llm.client import LLMClient
from resume_tailor.llm.providers import get_provider
from resume_tailor.models.review import ReviewedContentPlan, ReviewPolicy
from resume_tailor.parsers.job_description import read_job_description, read_multiline_paste
from resume_tailor.redaction import redact
from resume_tailor.review.drafts import (
    create_draft,
    export_markdown,
    load_controller,
)
from resume_tailor.review.drafts import (
    invalidate as invalidate_draft,
)
from resume_tailor.review.session import ReviewController
from resume_tailor.review.terminal import review_terminal

app = typer.Typer(
    name="resume-tailor",
    help="Create source-grounded, ATS-friendly resumes.",
    no_args_is_help=True,
)
auth_app = typer.Typer(help="Manage provider credentials without displaying their values.")
config_app = typer.Typer(help="Inspect non-secret application configuration.")
review_app = typer.Typer(help="Resume, inspect, export, or invalidate a local review draft.")
app.add_typer(auth_app, name="auth")
app.add_typer(config_app, name="config")
app.add_typer(review_app, name="review")

console = Console(stderr=False)
error_console = Console(stderr=True)


@dataclass(slots=True)
class RuntimeState:
    debug: bool = False


runtime = RuntimeState()


def _version_callback(value: bool) -> None:
    if value:
        console.print(__version__)
        raise typer.Exit()


@app.callback()
def root(
    debug: Annotated[bool, typer.Option("--debug", help="Show tracebacks for failures.")] = False,
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            callback=_version_callback,
            is_eager=True,
            help="Show the installed version and exit.",
        ),
    ] = False,
) -> None:
    """Configure process-wide CLI behavior."""

    del version
    runtime.debug = debug


@config_app.command("path")
def show_config_path() -> None:
    """Print the platform-specific configuration path."""

    console.print(str(config_path()))


@config_app.command("show")
def show_config() -> None:
    """Print effective non-secret configuration."""

    config = load_config()
    table = Table(title="Effective configuration", show_header=True)
    table.add_column("Setting")
    table.add_column("Value")
    for key, value in serializable_config(config).items():
        table.add_row(key, "" if value is None else str(value))
    console.print(table)


@auth_app.command("set")
def auth_set(
    provider: Annotated[str, typer.Argument(help="Provider name, for example deepseek.")],
) -> None:
    """Store a provider API key in the operating-system keyring."""

    secret = typer.prompt(f"API key for {provider}", hide_input=True, confirmation_prompt=True)
    manager = CredentialManager()
    manager.store(provider, secret)
    console.print(f"Credential stored for {provider.strip().lower()} in the OS keyring.")


@auth_app.command("delete")
def auth_delete(provider: Annotated[str, typer.Argument(help="Provider name.")]) -> None:
    """Delete a provider credential from the operating-system keyring."""

    deleted = CredentialManager().delete(provider)
    normalized = provider.strip().lower()
    if deleted:
        console.print(f"Credential deleted for {normalized}.")
    else:
        console.print(f"No OS-keyring credential was stored for {normalized}.")


@auth_app.command("status")
def auth_status(provider: Annotated[str, typer.Argument(help="Provider name.")]) -> None:
    """Report credential availability and source, never its value."""

    source = CredentialManager().status(provider)
    normalized = provider.strip().lower()
    if source is None:
        console.print(
            f"No credential configured for {normalized}. "
            f"Set {environment_variable(normalized)} or run 'resume-tailor auth set {normalized}'."
        )
        return
    console.print(f"Credential configured for {normalized} via {source.value}.")


def _prompt_path(label: str, default: Path | None = None) -> Path:
    value = typer.prompt(label, default=str(default) if default is not None else None)
    return Path(value).expanduser()


@app.command("generate")
def generate_command(
    master_cv: Annotated[Path | None, typer.Option("--master-cv", exists=False)] = None,
    jd: Annotated[Path | None, typer.Option("--jd", exists=False)] = None,
    jd_text: Annotated[str | None, typer.Option("--jd-text")] = None,
    jd_stdin: Annotated[bool, typer.Option("--jd-stdin")] = False,
    provider: Annotated[str | None, typer.Option("--provider")] = None,
    model: Annotated[str | None, typer.Option("--model")] = None,
    output_dir: Annotated[Path | None, typer.Option("--output-dir")] = None,
    pages: Annotated[str | None, typer.Option("--pages")] = None,
    tex_engine: Annotated[str | None, typer.Option("--tex-engine")] = None,
    api_base: Annotated[str | None, typer.Option("--api-base")] = None,
    allow_weak_jd: Annotated[bool, typer.Option("--allow-weak-jd")] = False,
    non_interactive: Annotated[bool, typer.Option("--non-interactive")] = False,
    review: Annotated[ReviewPolicy | None, typer.Option("--review")] = None,
    review_file: Annotated[Path | None, typer.Option("--review-file", exists=False)] = None,
    save_draft: Annotated[Path | None, typer.Option("--save-draft", exists=False)] = None,
    accept_ingestion_warnings: Annotated[bool, typer.Option("--accept-ingestion-warnings")] = False,
) -> None:
    """Generate a resume interactively, or entirely from automation-friendly flags."""
    supplied_jd = sum(value is not None and value is not False for value in (jd, jd_text, jd_stdin))
    if supplied_jd > 1:
        raise UsageError("provide exactly one of --jd, --jd-text, or --jd-stdin")
    required = {
        "--master-cv": master_cv,
        "a JD option": supplied_jd or None,
        "--provider": provider,
        "--model": model,
        "--output-dir": output_dir,
        "--pages": pages,
    }
    if non_interactive:
        missing = [name for name, value in required.items() if value is None]
        if missing:
            raise UsageError(
                "non-interactive generate requires " + ", ".join(missing),
                hint="Pass every required flag; this mode never prompts.",
            )
        if review is None:
            raise UsageError("non-interactive generate requires explicit --review disabled")
        if review is not ReviewPolicy.DISABLED and review_file is None:
            raise UsageError("non-interactive review requires an approved --review-file")
    review = review or (ReviewPolicy.DISABLED if non_interactive else ReviewPolicy.REQUIRED)
    if review_file is not None and review is ReviewPolicy.DISABLED:
        raise UsageError(
            "--review-file requires --review required or optional",
        )
    del accept_ingestion_warnings
    config = load_config(
        cli_overrides={
            "master_cv": master_cv,
            "output_dir": output_dir,
            "provider": provider,
            "model": model,
            "pages": pages,
            "tex_engine": tex_engine,
            "api_base": api_base,
        }
    )
    if not non_interactive:
        master_cv = master_cv or _prompt_path("Master CV", config.master_cv)
        if not supplied_jd:
            source = (
                typer.prompt("Job description source (file/paste/stdin)", default="paste")
                .strip()
                .lower()
            )
            if source == "file":
                jd = _prompt_path("Job description file")
            elif source == "stdin":
                jd_stdin = True
            elif source == "paste":
                console.print("Paste the job description, then enter END on its own line.")
                jd_text = read_multiline_paste(sys.stdin)
            else:
                raise UsageError("job description source must be file, paste, or stdin")
        provider = provider or typer.prompt("Provider", default=config.provider)
        selected_provider = get_provider(provider)
        model = model or typer.prompt("Model", default=selected_provider.default_models[0])
        pages = pages or typer.prompt("Target length (1/2/auto)", default=config.pages)
        output_dir = output_dir or _prompt_path("Output directory", config.output_dir)
        tex_engine = tex_engine or typer.prompt("TeX engine", default=config.tex_engine)
        if pages not in {"1", "2", "auto"}:
            raise UsageError("pages must be one of: 1, 2, auto")
        if not typer.confirm("Generate resume?", default=True):
            raise typer.Abort()
    assert master_cv is not None and provider is not None and model is not None
    assert output_dir is not None and pages is not None
    selected_provider = get_provider(provider)
    job = read_job_description(jd=jd, jd_text=jd_text, jd_stdin=jd_stdin, allow_weak=allow_weak_jd)
    credential = None
    if selected_provider.key_variable is not None:
        credential = CredentialManager().resolve(
            selected_provider.name, allow_prompt=not non_interactive
        )
    client = LLMClient(selected_provider, timeout=config.timeout)

    def progress(stage: str) -> None:
        console.print(f"[cyan]{stage}[/cyan]")

    def reviewer(draft: PlanDraft, bundle: IngestionBundle) -> ReviewedContentPlan | None:
        if review_file is not None:
            try:
                payload = json.loads(review_file.read_text(encoding="utf-8"))
                loaded = ReviewedContentPlan.model_validate(
                    payload, context={"reviewed_plan_factory": True}
                )
            except (OSError, ValueError) as exc:
                raise ValidationError("could not load approved review file") from exc
            return loaded
        controller = ReviewController(
            draft.plan,
            draft.base_plan_hash,
            bundle.ledger,
            source_hash=f"sha256:{draft.source_hash}",
            job_hash=f"sha256:{draft.job_hash}",
        )
        try:
            draft_dir = create_draft(
                output_dir,
                draft,
                bundle,
                controller.session,
                destination=save_draft,
            )
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        checkpoint = draft_dir / "review.json"
        console.print(f"Review draft: {draft_dir}")
        return review_terminal(controller, console, checkpoint=checkpoint)

    result = run_generation(
        GenerationRequest(
            master_cv=master_cv,
            job=job,
            output_dir=output_dir,
            provider=selected_provider.name,
            model=model,
            api_key=credential.value if credential else None,
            api_base=config.api_base,
            pages=pages,
            tex_engine=tex_engine or "auto",
            timeout=config.timeout,
        ),
        client,
        progress=progress,
        review_policy=review,
        reviewer=reviewer if review is not ReviewPolicy.DISABLED else None,
    )
    usage = result.manifest.usage
    console.print(f"Artifacts: {result.artifact_dir}")
    if usage.total_tokens is not None:
        console.print(f"Tokens: {usage.total_tokens}")
    if usage.estimated_cost is not None:
        console.print(f"Estimated cost: {usage.estimated_cost:.6g}")
    console.print(f"Retries: {result.manifest.retry_count}")


@review_app.command("resume")
def review_resume(path: Annotated[Path, typer.Argument(exists=True)]) -> None:
    """Resume a saved review locally, without credentials or provider calls."""
    try:
        controller = load_controller(path)
        reviewed = review_terminal(controller, console, checkpoint=path / "review.json")
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    if reviewed is None:
        console.print(f"Review saved: {path}")
    else:
        console.print(f"Review approved: {path}")


@review_app.command("status")
def review_status(path: Annotated[Path, typer.Argument(exists=True)]) -> None:
    """Show local review progress without loading a provider credential."""
    try:
        controller = load_controller(path)
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    total = len(controller.claims)
    decided = len(controller.session.decisions)
    console.print(f"Review: {controller.session.status}; {decided}/{total} claims decided")


@review_app.command("export")
def review_export(
    path: Annotated[Path, typer.Argument(exists=True)],
    format: Annotated[str, typer.Option("--format")] = "markdown",
) -> None:
    """Export a local review record in a portable, non-executable format."""
    if format != "markdown":
        raise UsageError("review export supports only --format markdown")
    try:
        console.print(export_markdown(path), markup=False)
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc


@review_app.command("invalidate")
def review_invalidate(path: Annotated[Path, typer.Argument(exists=True)]) -> None:
    """Mark a draft unusable without deleting its recoverable local files."""
    try:
        invalidate_draft(path)
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    console.print(f"Review invalidated: {path}")


@app.command("doctor")
def doctor(
    output_dir: Annotated[
        Path | None,
        typer.Option(
            "--output-dir",
            file_okay=False,
            help="Output directory to check; defaults to effective configuration.",
        ),
    ] = None,
) -> None:
    """Check Python, paths, TeX engines, and optional dependencies."""

    config = load_config(cli_overrides={"output_dir": output_dir})
    checks = run_diagnostics(output_dir=config.output_dir)
    table = Table(title="Resume Tailor doctor")
    table.add_column("Status", justify="center")
    table.add_column("Check")
    table.add_column("Detail")
    table.add_column("Remediation")
    for check in checks:
        if check.ok:
            status = "[green]PASS[/green]"
        elif check.required:
            status = "[red]FAIL[/red]"
        else:
            status = "[yellow]INFO[/yellow]"
        table.add_row(status, check.name, check.detail, check.remediation or "—")
    console.print(table)
    if not required_checks_pass(checks):
        raise typer.Exit(code=int(ExitCode.VALIDATION))


def _render_expected_error(exc: ResumeTailorError) -> None:
    error_console.print(f"[red]Error:[/red] {redact(exc.message)}")
    if exc.hint:
        error_console.print(f"[yellow]Hint:[/yellow] {redact(exc.hint)}")


def _render_debug_traceback(exc: BaseException) -> None:
    formatted = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    error_console.print(redact(formatted), markup=False)


def main() -> None:
    """Run the CLI through a single expected/unexpected error boundary."""

    try:
        app()
    except ResumeTailorError as exc:
        _render_expected_error(exc)
        if runtime.debug:
            _render_debug_traceback(exc)
        raise SystemExit(int(exc.exit_code)) from None
    except (KeyboardInterrupt, click.Abort):
        error_console.print("[yellow]Cancelled.[/yellow]")
        raise SystemExit(int(ExitCode.CANCELLED)) from None
    except Exception as exc:
        error_console.print(f"[red]Unexpected error:[/red] {redact(exc)}")
        if runtime.debug:
            _render_debug_traceback(exc)
        else:
            error_console.print("Run again with --debug for a traceback.")
        raise SystemExit(int(ExitCode.INTERNAL)) from None


if __name__ == "__main__":
    main()
