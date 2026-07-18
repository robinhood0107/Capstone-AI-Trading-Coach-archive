"""성능 timeout이 setup/harness 실패를 family 0으로 오분류하지 않는지 검증한다."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest
import run_rotated_blocks as runner
from benchmark_contract import ContractError, sha256_file, strict_json_load
from validate_benchmark_report import DEFAULT_PLAN, validate_plan

COMMIT = "a" * 40


@pytest.fixture(scope="module")
def plan() -> dict[str, Any]:
    return validate_plan(DEFAULT_PLAN, verify_files=False)


def _host_report(plan: dict[str, Any], *, status: str = "PASS") -> dict[str, Any]:
    failed = status != "PASS"
    checks: list[dict[str, Any]] = [
        {
            "id": check_id,
            "expected": {},
            "actual": {},
            "status": "FAIL" if failed and index == 0 else "PASS",
            "evidence": {},
        }
        for index, check_id in enumerate(sorted(runner.HOST_CHECK_IDS))
    ]
    return {
        "schemaVersion": "s1.4x-host-validity-v1",
        "policy": runner._expected_host_policy(plan, root_pid=os.getpid()),
        "portableHostIdSha256": "b" * 64,
        "metadata": {
            "cpuGovernor": "UNAVAILABLE_WSL",
            "temperature": "UNAVAILABLE_WSL",
        },
        "checks": checks,
        "failureCount": 1 if failed else 0,
        "status": status,
    }


def _command_manifest() -> dict[str, Any]:
    executable = str(Path(sys.executable).absolute())
    identity = {"path": executable, "sha256": sha256_file(Path(executable))}
    return {
        "schemaVersion": "s1.4x-benchmark-command-manifest-v2",
        "benchmarkSubjectCommit": COMMIT,
        "candidateSourceCommit": COMMIT,
        "hostValidatorCommand": [
            executable,
            "-c",
            "host-validator",
            "{host_report}",
        ],
        "boundaryCommands": {
            boundary_id: [
                executable,
                "-c",
                "native-wrapper",
                "{qualification}",
            ]
            for boundary_id in runner.BOUNDARY_IDS
        },
        "allowedExecutables": {
            "hostValidator": identity,
            "boundaries": {
                boundary_id: identity for boundary_id in runner.BOUNDARY_IDS
            },
        },
    }


def _write_manifest(path: Path, manifest: dict[str, Any] | None = None) -> str:
    path.write_text(
        json.dumps(manifest or _command_manifest(), allow_nan=False),
        encoding="utf-8",
    )
    return sha256_file(path)


def test_command_manifest_requires_precommitted_digest_and_executable_identity(
    tmp_path: Path,
) -> None:
    path = tmp_path / "commands.json"
    digest = _write_manifest(path)

    manifest = runner._strict_command_manifest(
        path,
        expected_sha256=digest,
        benchmark_subject_commit=COMMIT,
        candidate_source_commit=COMMIT,
    )
    assert manifest["schemaVersion"] == "s1.4x-benchmark-command-manifest-v2"

    with pytest.raises(ContractError, match="COMMAND_MANIFEST_SHA256_MISMATCH"):
        runner._strict_command_manifest(
            path,
            expected_sha256="0" * 64,
            benchmark_subject_commit=COMMIT,
            candidate_source_commit=COMMIT,
        )

    arbitrary = _command_manifest()
    arbitrary["boundaryCommands"]["scala"][0] = "/bin/echo"
    arbitrary_path = tmp_path / "arbitrary.json"
    arbitrary_digest = _write_manifest(arbitrary_path, arbitrary)
    with pytest.raises(ContractError, match="INVALID_ALLOWED_EXECUTABLE_IDENTITY"):
        runner._strict_command_manifest(
            arbitrary_path,
            expected_sha256=arbitrary_digest,
            benchmark_subject_commit=COMMIT,
            candidate_source_commit=COMMIT,
        )


def test_command_renderer_rejects_unbound_or_formatted_placeholders() -> None:
    with pytest.raises(ContractError, match="UNKNOWN_COMMAND_PLACEHOLDER"):
        runner._render_command(["tool", "{unbound}"], {"bound": "value"})
    with pytest.raises(ContractError, match="UNKNOWN_COMMAND_PLACEHOLDER"):
        runner._render_command(["tool", "{bound!r}"], {"bound": "value"})


def test_host_validity_verifies_bytes_pass_state_and_frozen_policy(
    tmp_path: Path,
    plan: dict[str, Any],
) -> None:
    report_path = tmp_path / "host-validity.json"
    report_path.write_text(
        json.dumps(_host_report(plan), allow_nan=False),
        encoding="utf-8",
    )

    binding = runner._verify_host_validity_report(
        report_path,
        plan=plan,
        root_pid=os.getpid(),
    )
    assert binding["status"] == "PASS"
    assert binding["sha256"] == sha256_file(report_path)

    failed = _host_report(plan, status="FAIL")
    report_path.write_text(json.dumps(failed, allow_nan=False), encoding="utf-8")
    with pytest.raises(ContractError, match="INVALID_HOST_VALIDITY_ARTIFACT"):
        runner._verify_host_validity_report(
            report_path,
            plan=plan,
            root_pid=os.getpid(),
        )

    wrong_policy = _host_report(plan)
    wrong_policy["policy"]["max_normalized_load1"] = 0.2
    report_path.write_text(
        json.dumps(wrong_policy, allow_nan=False),
        encoding="utf-8",
    )
    with pytest.raises(ContractError, match="INVALID_HOST_VALIDITY_ARTIFACT"):
        runner._verify_host_validity_report(
            report_path,
            plan=plan,
            root_pid=os.getpid(),
        )


def test_measurement_transition_is_exact_and_tamper_evident(
    tmp_path: Path,
    plan: dict[str, Any],
) -> None:
    block = runner.build_schedule(plan)[0]
    executable_path = Path(sys.executable).absolute()
    expected = runner._qualification_document(
        plan=plan,
        plan_sha256="1" * 64,
        command_manifest_sha256="2" * 64,
        benchmark_subject_commit=COMMIT,
        candidate_source_commit=COMMIT,
        run_id="run-001",
        block=block,
        host_validity={
            "artifactPath": "host-validity.json",
            "sha256": "3" * 64,
            "status": "PASS",
            "policySha256": "4" * 64,
            "portableHostIdSha256": "5" * 64,
        },
        executable={
            "path": str(executable_path),
            "resolvedPath": str(executable_path.resolve()),
            "sha256": sha256_file(executable_path),
        },
        command=[str(executable_path), "--selector", block.selector_id],
        measurement_entered=False,
    )
    path = tmp_path / "timeout-qualification.json"
    runner._write_json_exclusive(path, expected)

    with pytest.raises(ContractError, match="INVALID_MEASUREMENT_QUALIFICATION"):
        runner._verify_measurement_qualification(path, expected=expected)

    runner.mark_measurement_entered(path)
    assert runner._verify_measurement_qualification(path, expected=expected) == (
        sha256_file(path)
    )

    tampered = strict_json_load(path)
    tampered["selectorInputClosure"]["expectedCaseIds"] = []
    path.write_text(json.dumps(tampered, allow_nan=False), encoding="utf-8")
    with pytest.raises(ContractError, match="INVALID_MEASUREMENT_QUALIFICATION"):
        runner._verify_measurement_qualification(path, expected=expected)


def _install_execute_fakes(
    monkeypatch: pytest.MonkeyPatch,
    plan: dict[str, Any],
    *,
    native_marks_measurement: bool,
    host_status: str = "PASS",
    host_times_out: bool = False,
) -> list[runner.ScheduledBlock]:
    schedule = runner.build_schedule(plan)[:2]

    monkeypatch.setattr(runner, "validate_plan", lambda _path: plan)
    monkeypatch.setattr(runner, "build_schedule", lambda _plan: schedule)
    monkeypatch.setattr(runner, "_pin_current_process", lambda _cpu_set: None)
    monkeypatch.setattr(
        runner,
        "_verify_source_commit_binding",
        lambda *_args, **_kwargs: None,
    )

    def fake_process(
        command: list[str],
        *,
        cwd: Path,
        timeout_seconds: int,
        stdout_path: Path,
        stderr_path: Path,
        environment: dict[str, str],
    ) -> None:
        del cwd, timeout_seconds, stdout_path, stderr_path, environment
        if command[2] == "host-validator":
            if host_times_out:
                raise ContractError("PERFORMANCE_DEADLINE_EXCEEDED")
            Path(command[-1]).write_text(
                json.dumps(_host_report(plan, status=host_status), allow_nan=False),
                encoding="utf-8",
            )
            return
        if native_marks_measurement:
            runner.mark_measurement_entered(Path(command[-1]))
        raise ContractError("PERFORMANCE_DEADLINE_EXCEEDED")

    monkeypatch.setattr(runner, "_run_process", fake_process)
    return schedule


def _execute(
    tmp_path: Path,
    manifest_path: Path,
    manifest_sha256: str,
) -> dict[str, Any]:
    return runner.execute_schedule(
        plan_path=DEFAULT_PLAN,
        command_manifest_path=manifest_path,
        expected_command_manifest_sha256=manifest_sha256,
        benchmark_subject_commit=COMMIT,
        candidate_source_commit=COMMIT,
        output_root=tmp_path / "outputs",
        run_id="run-timeout",
        repo_root=tmp_path,
    )


def test_setup_timeout_cannot_be_recorded_as_performance_zero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    plan: dict[str, Any],
) -> None:
    schedule = _install_execute_fakes(
        monkeypatch,
        plan,
        native_marks_measurement=False,
    )
    manifest_path = tmp_path / "commands.json"
    manifest_sha256 = _write_manifest(manifest_path)

    with pytest.raises(ContractError, match="INVALID_MEASUREMENT_QUALIFICATION"):
        _execute(tmp_path, manifest_path, manifest_sha256)

    first_block = runner.block_directory(
        tmp_path / "outputs" / "run-timeout",
        schedule[0],
    )
    assert not (first_block / "valid-performance-timeout.json").exists()


def test_valid_measurement_timeout_binds_qualification_and_continues(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    plan: dict[str, Any],
) -> None:
    schedule = _install_execute_fakes(
        monkeypatch,
        plan,
        native_marks_measurement=True,
    )
    manifest_path = tmp_path / "commands.json"
    manifest_sha256 = _write_manifest(manifest_path)

    summary = _execute(tmp_path, manifest_path, manifest_sha256)

    assert summary["status"] == "PASS_WITH_VALID_PERFORMANCE_TIMEOUTS"
    assert summary["validPerformanceTimeoutCount"] == 2
    for block in schedule:
        block_path = runner.block_directory(
            tmp_path / "outputs" / "run-timeout",
            block,
        )
        evidence = strict_json_load(
            block_path / "valid-performance-timeout.json"
        )
        qualification = block_path / "timeout-qualification.json"
        assert evidence["measurementEntered"] is True
        assert evidence["timeoutQualificationSha256"] == sha256_file(qualification)
        assert strict_json_load(qualification)["measurementEntered"] is True


@pytest.mark.parametrize(
    ("host_status", "host_times_out", "error"),
    [
        ("FAIL", False, "INVALID_HOST_VALIDITY_ARTIFACT"),
        ("PASS", True, "HOST_PREFLIGHT_DEADLINE_EXCEEDED"),
    ],
)
def test_failed_or_hung_host_preflight_is_a_hard_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    plan: dict[str, Any],
    host_status: str,
    host_times_out: bool,
    error: str,
) -> None:
    _install_execute_fakes(
        monkeypatch,
        plan,
        native_marks_measurement=True,
        host_status=host_status,
        host_times_out=host_times_out,
    )
    manifest_path = tmp_path / "commands.json"
    manifest_sha256 = _write_manifest(manifest_path)

    with pytest.raises(ContractError, match=error):
        _execute(tmp_path, manifest_path, manifest_sha256)
