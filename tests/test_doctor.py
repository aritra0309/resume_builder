from __future__ import annotations

from pathlib import Path

from resume_tailor.doctor import required_checks_pass, run_diagnostics


def test_doctor_reports_remediation_for_missing_engines(
    monkeypatch: object, tmp_path: Path
) -> None:
    from pytest import MonkeyPatch

    assert isinstance(monkeypatch, MonkeyPatch)
    monkeypatch.setattr("resume_tailor.doctor.shutil.which", lambda _: None)
    checks = run_diagnostics(
        output_dir=tmp_path / "output",
        configuration_path=tmp_path / "config" / "config.toml",
    )

    usable = next(check for check in checks if check.name == "Usable TeX engine")
    assert not usable.ok
    assert usable.required
    assert usable.remediation
    assert not required_checks_pass(checks)


def test_doctor_accepts_one_engine(monkeypatch: object, tmp_path: Path) -> None:
    from pytest import MonkeyPatch

    assert isinstance(monkeypatch, MonkeyPatch)
    monkeypatch.setattr(
        "resume_tailor.doctor.shutil.which",
        lambda name: "/usr/bin/tectonic" if name == "tectonic" else None,
    )
    monkeypatch.setattr("resume_tailor.doctor._engine_version", lambda _: "Tectonic 1.0")
    checks = run_diagnostics(
        output_dir=tmp_path,
        configuration_path=tmp_path / "config.toml",
    )
    assert required_checks_pass(checks)
