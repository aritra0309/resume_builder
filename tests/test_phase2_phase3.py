from __future__ import annotations

import logging
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from resume_tailor.evidence.ledger import build_evidence_ledger
from resume_tailor.llm.client import LLMClient
from resume_tailor.llm.models import discover_models
from resume_tailor.llm.providers import get_provider, validate_registry
from resume_tailor.models.cv import EvidenceItem, SourceLocation
from resume_tailor.models.job import JobAnalysis, JobDescription, JobKeyword
from resume_tailor.parsers.job_description import read_job_description, read_multiline_paste
from resume_tailor.parsers.markdown_cv import parse_markdown_cv


def test_master_cv_parses_to_deterministic_evidence() -> None:
    source = Path("tests/fixtures/test_master_cv.md")
    first = build_evidence_ledger(parse_markdown_cv(source)).model_dump_json()
    second = build_evidence_ledger(parse_markdown_cv(source)).model_dump_json()
    assert first == second
    assert "Credential Exposure Management" in first


def test_evidence_contract_rejects_invalid_id() -> None:
    with pytest.raises(ValidationError):
        EvidenceItem(
            id="Invalid ID",
            section="experience",
            text="Built a thing.",
            normalized_text="built a thing.",
            source_location=SourceLocation(file="cv.md", line=1),
        )


def test_jd_input_requires_one_source_and_allows_explicit_weak_override() -> None:
    with pytest.raises(Exception, match="exactly one"):
        read_job_description(jd_text="x", jd_stdin=True)
    result = read_job_description(jd_stdin=True, stdin=StringIO("Need Python."), allow_weak=True)
    assert result.warnings
    assert read_multiline_paste(StringIO("line one\nEND\nignored")) == "line one\n"


def test_job_keyword_quote_must_be_in_original_jd() -> None:
    with pytest.raises(ValidationError, match="source_quote"):
        JobDescription(
            original_text="Build data products.",
            normalized_text="build data products.",
            keywords=[JobKeyword(term="Python", importance=1, source_quote="Python")],
        )


def test_provider_registry_and_offline_discovery() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        validate_registry((get_provider("deepseek"), get_provider("deepseek")))
    models, source = discover_models(get_provider("deepseek"))
    assert source == "curated"
    assert "deepseek-chat" in models


def test_llm_client_retries_timeout_and_captures_usage() -> None:
    calls = 0

    def completion(**_: object) -> object:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise TimeoutError("timed out")
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok": true}'))],
            usage={"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
        )

    client = LLMClient(get_provider("deepseek"), completion=completion, sleeper=lambda _: None)
    result = client.complete(model="deepseek-chat", messages=[{"role": "user", "content": "hi"}])
    assert result.retries == 1
    assert result.usage["total_tokens"] == 5


def test_llm_client_corrects_one_invalid_schema_response_without_exposing_content() -> None:
    calls: list[dict[str, object]] = []

    def completion(**kwargs: object) -> object:
        calls.append(dict(kwargs))
        content = "not JSON" if len(calls) == 1 else '{"required_skills": ["Python"]}'
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))], usage={}
        )

    client = LLMClient(get_provider("deepseek"), completion=completion)
    result = client.complete(
        model="deepseek-v4-flash",
        messages=[{"role": "user", "content": "synthetic JD"}],
        response_schema=JobAnalysis,
    )
    assert result.text == '{"required_skills": ["Python"]}'
    assert result.retries == 1 and len(calls) == 2
    repair = calls[1]["messages"]
    assert isinstance(repair, list) and "conforms exactly" in repair[-1]["content"]


def test_llm_client_corrects_one_empty_structured_response(
    caplog: pytest.LogCaptureFixture,
) -> None:
    responses = ["", '{"required_skills": ["Python"]}']
    calls: list[dict[str, object]] = []

    def completion(**kwargs: object) -> object:
        calls.append(dict(kwargs))
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=responses.pop(0)), finish_reason="length"
                )
            ],
            usage={},
        )

    client = LLMClient(get_provider("deepseek"), completion=completion)
    caplog.set_level(logging.INFO, logger="resume_tailor.llm.client")
    result = client.complete(
        model="deepseek-v4-flash",
        messages=[{"role": "user", "content": "synthetic JD"}],
        response_schema=JobAnalysis,
    )
    assert result.retries == 1
    assert calls[0]["thinking"] == {"type": "disabled"}
    assert calls[1]["messages"] == calls[0]["messages"]
    assert client.telemetry["empty_response_retries"] == 1
    assert client.telemetry["schema_correction_retries"] == 0
    assert "finish_reason=length" in caplog.text
    assert "synthetic JD" not in caplog.text


def test_llm_client_falls_back_to_v4_pro_after_empty_retries() -> None:
    calls: list[dict[str, object]] = []

    def completion(**kwargs: object) -> object:
        calls.append(dict(kwargs))
        content = '{"required_skills": ["Python"]}' if len(calls) == 3 else ""
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))], usage={}
        )

    client = LLMClient(
        get_provider("deepseek"), completion=completion, max_retries=1, max_empty_retries=1
    )
    result = client.complete(
        model="deepseek-v4-flash",
        messages=[{"role": "user", "content": "synthetic JD"}],
        response_schema=JobAnalysis,
    )

    assert result.model == "deepseek/deepseek-v4-pro"
    assert result.fallback_used is True
    assert [call["model"] for call in calls] == [
        "deepseek/deepseek-v4-flash",
        "deepseek/deepseek-v4-flash",
        "deepseek/deepseek-v4-pro",
    ]
    assert client.telemetry["fallbacks"] == 1
    assert client.telemetry["fallback_rate"] == 1.0


def test_llm_client_retries_an_empty_unstructured_response() -> None:
    responses = ["", "\\documentclass{article}"]

    def completion(**_: object) -> object:
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=responses.pop(0)))], usage={}
        )

    result = LLMClient(get_provider("deepseek"), completion=completion).complete(
        model="deepseek-v4-flash", messages=[{"role": "user", "content": "render latex"}]
    )

    assert result.text == "\\documentclass{article}"
    assert result.retries == 1
