"""End-to-end generation orchestration; all final files are published atomically."""

from __future__ import annotations

import hashlib
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from uuid import uuid4

from resume_tailor.artifacts import ArtifactRun
from resume_tailor.errors import ValidationError
from resume_tailor.evidence.ledger import build_evidence_ledger
from resume_tailor.ingestion.detector import ingest_file
from resume_tailor.latex.compiler import compile_latex
from resume_tailor.latex.generator import (
    LATEX_GENERATION_PROMPT_VERSION,
    fallback_render,
)
from resume_tailor.llm.client import LLMClient, LLMResponse
from resume_tailor.models.cv import CVDocument, EvidenceLedger, Person, StrictModel
from resume_tailor.models.generation import ContentPlan, PageRecommendation, RunManifest, TokenUsage
from resume_tailor.models.job import JobAnalysis, JobDescription
from resume_tailor.models.review import ReviewedContentPlan, ReviewPolicy
from resume_tailor.models.validation import ValidationReport
from resume_tailor.tailoring.pipeline import verify_and_repair
from resume_tailor.tailoring.prompts import (
    CONTENT_TAILORING_PROMPT_VERSION,
    JD_ANALYSIS_PROMPT_VERSION,
    PAGE_FIT_PROMPT_VERSION,
    content_tailoring_messages,
    job_analysis_messages,
    page_fit_messages,
    page_recommendation_messages,
)
from resume_tailor.validation.ats import validate_ats
from resume_tailor.validation.pdf import validate_pdf
from resume_tailor.validation.report import report_json, report_markdown

Progress = Callable[[str], None]
MAX_PAGE_FIT_REVISIONS = 2
ONE_PAGE_MIN_CONTENT_DEPTH = 0.86


@dataclass(frozen=True, slots=True)
class GenerationRequest:
    master_cv: Path
    job: JobDescription
    output_dir: Path
    provider: str
    model: str
    api_key: str | None
    api_base: str | None
    pages: str
    tex_engine: str
    timeout: float


@dataclass(frozen=True, slots=True)
class GenerationResult:
    artifact_dir: Path
    manifest: RunManifest


class IngestionBundle(StrictModel):
    """Serializable output of the source-ingestion stage."""

    schema_version: int = 1
    document: CVDocument
    ledger: EvidenceLedger
    source_hash: str
    source_format: str = "markdown"


class PlanDraft(StrictModel):
    """Serializable boundary before human review or final rendering."""

    schema_version: int = 1
    source_hash: str
    job_hash: str
    job: JobDescription
    plan: ContentPlan
    base_plan_hash: str


Reviewer = Callable[[PlanDraft, IngestionBundle], ReviewedContentPlan | None]


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ingest(request: GenerationRequest, *, progress: Progress | None = None) -> IngestionBundle:
    """Detect and safely ingest a Markdown, DOCX, or text-PDF source."""
    say = progress or (lambda _: None)
    say("Detecting master-CV format")
    result = ingest_file(request.master_cv)
    say("Building evidence ledger")
    document = result.document
    return IngestionBundle(
        document=document,
        ledger=build_evidence_ledger(document),
        source_hash=result.source_hash.removeprefix("sha256:"),
        source_format=result.source_format.value,
    )


def analyze_job(
    bundle: IngestionBundle,
    request: GenerationRequest,
    client: LLMClient,
    *,
    progress: Progress | None = None,
) -> tuple[JobDescription, LLMResponse]:
    """Analyze a job description after ingestion has completed."""
    del bundle  # Kept in the public stage signature for a uniform pipeline.
    say = progress or (lambda _: None)
    say("Analyzing job description")
    response = client.complete(
        model=request.model,
        messages=job_analysis_messages(request.job),
        api_key=request.api_key,
        api_base=request.api_base,
        response_schema=JobAnalysis,
    )
    return _analyzed_job(response, request.job), response


def create_grounded_plan(
    bundle: IngestionBundle,
    job: JobDescription,
    request: GenerationRequest,
    client: LLMClient,
    *,
    progress: Progress | None = None,
) -> tuple[PlanDraft, LLMResponse]:
    """Create the validated plan that is safe to checkpoint for review."""
    say = progress or (lambda _: None)
    say("Creating grounded content plan")
    response = client.complete(
        model=request.model,
        messages=content_tailoring_messages(job, bundle.ledger),
        api_key=request.api_key,
        api_base=request.api_base,
        response_schema=ContentPlan,
    )
    plan = verify_and_repair(ContentPlan.model_validate_json(response.text), bundle.ledger)
    plan_hash = _hash_text(plan.model_dump_json())
    return PlanDraft(
        source_hash=bundle.source_hash,
        job_hash=_hash_text(job.original_text),
        job=job,
        plan=plan,
        base_plan_hash=f"sha256:{plan_hash}",
    ), response


def review_plan(
    draft: PlanDraft,
    policy: ReviewPolicy = ReviewPolicy.DISABLED,
    reviewed: ReviewedContentPlan | None = None,
) -> ContentPlan:
    """Enforce the review boundary before any LaTeX/provider rendering call."""
    if policy is ReviewPolicy.DISABLED:
        return draft.plan
    if reviewed is None:
        raise ValidationError("required review did not produce an approved plan")
    if reviewed.base_plan_hash != draft.base_plan_hash:
        raise ValidationError("approved review does not match the generated plan")
    return reviewed.plan


def _combined(*reports: ValidationReport) -> ValidationReport:
    issues = [issue for report in reports for issue in report.issues]
    mappings = {key: value for report in reports for key, value in report.evidence_mappings.items()}
    return ValidationReport(
        passed=all(report.passed for report in reports), issues=issues, evidence_mappings=mappings
    )


def _usage_total(values: list[LLMResponse], field: str, *, integer: bool) -> int | float | None:
    present: list[int | float] = []
    for value in values:
        candidate = value.usage.get(field)
        if isinstance(candidate, int | float):
            present.append(candidate)
    if not present:
        return None
    total = sum(present)
    return int(total) if integer else float(total)


def _litellm_version() -> str | None:
    try:
        return version("litellm")
    except PackageNotFoundError:
        return None


def _analyzed_job(response: LLMResponse, source: JobDescription) -> JobDescription:
    parsed = JobAnalysis.model_validate_json(response.text)
    fields = parsed.model_dump(exclude={"warnings"})
    return JobDescription(
        original_text=source.original_text,
        normalized_text=source.normalized_text,
        warnings=source.warnings,
        **fields,
    )


def _target_pages(
    plan: ContentPlan, request: GenerationRequest, client: LLMClient
) -> tuple[int, LLMResponse | None, str | None]:
    """Honor explicit page choices; consult the model only for ``auto``."""
    if request.pages in {"1", "2"}:
        return int(request.pages), None, None
    response = client.complete(
        model=request.model,
        messages=page_recommendation_messages(plan),
        api_key=request.api_key,
        api_base=request.api_base,
        response_schema=PageRecommendation,
    )
    recommendation = PageRecommendation.model_validate_json(response.text)
    return recommendation.pages, response, recommendation.reason


def _render_latex(
    plan: ContentPlan, request: GenerationRequest, client: LLMClient, person: Person
) -> tuple[str, LLMResponse | None]:
    del request, client
    return fallback_render(plan, person), None


def _validation_error_message(report: ValidationReport) -> str:
    errors = [
        f"{issue.code}: {issue.message}" for issue in report.issues if issue.severity == "error"
    ]
    return "generated PDF did not pass validation: " + "; ".join(errors)


def _page_fit_messages(report: ValidationReport) -> list[str]:
    return [
        issue.message
        for issue in report.issues
        if issue.code in {"page_count", "page_underfilled"}
    ]


def generate(
    request: GenerationRequest,
    client: LLMClient,
    *,
    progress: Progress | None = None,
    review_policy: ReviewPolicy = ReviewPolicy.DISABLED,
    reviewer: Reviewer | None = None,
) -> GenerationResult:
    """Generate, validate, and publish a resume. Nothing final is written on failure."""
    say = progress or (lambda _: None)
    bundle = ingest(request, progress=say)
    job, job_response = analyze_job(bundle, request, client, progress=say)
    draft, plan_response = create_grounded_plan(bundle, job, request, client, progress=say)
    reviewed = reviewer(draft, bundle) if reviewer is not None else None
    plan = review_plan(draft, review_policy, reviewed)
    run_id = uuid4()
    target_pages, recommendation_response, page_target_reason = _target_pages(plan, request, client)
    if page_target_reason is not None:
        say(f"Auto page target: {target_pages} ({page_target_reason})")
    usage_values = [job_response, plan_response]
    if recommendation_response is not None:
        usage_values.append(recommendation_response)

    with ArtifactRun(request.output_dir, run_id) as artifacts:
        for attempt in range(MAX_PAGE_FIT_REVISIONS + 1):
            say("Rendering LaTeX" if attempt == 0 else f"Improving page fit (attempt {attempt})")
            latex, latex_response = _render_latex(plan, request, client, bundle.ledger.person)
            if latex_response is not None:
                usage_values.append(latex_response)
            say("Compiling PDF")
            candidate_pdf = artifacts.path / f"resume-attempt-{attempt}.pdf"
            compilation = compile_latex(
                latex,
                candidate_pdf,
                engine=request.tex_engine,
                timeout_seconds=int(request.timeout),
            )
            say("Validating PDF and ATS readability")
            report = _combined(
                validate_pdf(
                    candidate_pdf,
                    max_pages=target_pages,
                    min_one_page_content_depth=(
                        ONE_PAGE_MIN_CONTENT_DEPTH if target_pages == 1 else None
                    ),
                ),
                validate_ats(plan, job),
            )
            fit_feedback = _page_fit_messages(report)
            if report.passed:
                shutil.copyfile(candidate_pdf, artifacts.path / "resume.pdf")
                for stale_candidate in artifacts.path.glob("resume-attempt-*.pdf"):
                    stale_candidate.unlink()
                break
            if not fit_feedback or attempt == MAX_PAGE_FIT_REVISIONS:
                raise ValidationError(_validation_error_message(report))
            if review_policy is not ReviewPolicy.DISABLED:
                raise ValidationError(
                    _validation_error_message(report),
                    hint=(
                        "Page fitting would change an approved plan. Add or remove content, "
                        "then review the revised plan first."
                    ),
                )
            fit_response = client.complete(
                model=request.model,
                messages=page_fit_messages(
                    plan,
                    bundle.ledger,
                    target_pages=target_pages,
                    validation_issues=fit_feedback,
                ),
                api_key=request.api_key,
                api_base=request.api_base,
                response_schema=ContentPlan,
            )
            usage_values.append(fit_response)
            revised_plan = ContentPlan.model_validate_json(fit_response.text)
            plan = verify_and_repair(revised_plan, bundle.ledger)
        else:
            raise AssertionError("page-fit loop did not exit")

        artifacts.write_text("resume.tex", latex)
        prompt_tokens = _usage_total(usage_values, "prompt_tokens", integer=True)
        completion_tokens = _usage_total(usage_values, "completion_tokens", integer=True)
        total_tokens = _usage_total(usage_values, "total_tokens", integer=True)
        usage = TokenUsage(
            prompt_tokens=int(prompt_tokens) if prompt_tokens is not None else None,
            completion_tokens=int(completion_tokens) if completion_tokens is not None else None,
            total_tokens=int(total_tokens) if total_tokens is not None else None,
            estimated_cost=_usage_total(usage_values, "estimated_cost", integer=False),
        )
        retries = sum(value.retries for value in usage_values)
        manifest = RunManifest(
            run_id=run_id,
            created_at=datetime.now(UTC),
            input_hashes={
                str(request.master_cv): bundle.source_hash,
                "job_description": _hash_text(request.job.original_text),
            },
            provider=request.provider,
            model=request.model,
            litellm_version=_litellm_version(),
            compiler=compilation.engine,
            output_paths=[
                "resume.pdf",
                "resume.tex",
                "resume.json",
                "validation.json",
                "validation.md",
                "run.json",
            ]
            + (["review.json"] if reviewed is not None else []),
            validation_passed=True,
            usage=usage,
            retry_count=retries,
            temperature=client.temperature,
            source_format=bundle.source_format,
            review_policy=review_policy.value,
            review_id=reviewed.review_id if reviewed is not None else None,
            review_revision=reviewed.revision if reviewed is not None else None,
            final_plan_hash=reviewed.final_plan_hash
            if reviewed is not None
            else draft.base_plan_hash,
            decision_counts=reviewed.counts.model_dump() if reviewed is not None else {},
            target_pages=target_pages,
            page_target_reason=page_target_reason,
            page_fit_revisions=attempt,
            prompt_versions={
                "job_analysis": JD_ANALYSIS_PROMPT_VERSION,
                "content_tailoring": CONTENT_TAILORING_PROMPT_VERSION,
                "page_fit": PAGE_FIT_PROMPT_VERSION,
                "latex_generation": LATEX_GENERATION_PROMPT_VERSION,
            },
        )
        artifacts.write_json("resume.json", plan.model_dump(mode="json"))
        if reviewed is not None:
            artifacts.write_json("review.json", reviewed.model_dump(mode="json"))
        artifacts.write_json("validation.json", report_json(report, bundle.ledger))
        artifacts.write_text("validation.md", report_markdown(report, bundle.ledger))
        artifacts.write_json("run.json", manifest.model_dump(mode="json"))
        final = artifacts.publish()
    say(f"Completed: {final}")
    return GenerationResult(final, manifest)
