"""Frozen Python 회귀 producer의 compound receipt와 fail-closed 경계를 검증한다."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

INTEGRATION = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[6]
sys.path.insert(0, str(INTEGRATION))

from regression_gate import (  # noqa: E402
    DESELECTED_RESEARCH_NODE,
    REPLACEMENT_RESEARCH_NODES,
    RegressionGateError,
    run_regression_gate,
)


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _canonical(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _head() -> str:
    completed = subprocess.run(
        ["/usr/bin/git", "-c", "core.fsmonitor=false", "rev-parse", "HEAD"],
        cwd=REPO_ROOT,
        capture_output=True,
        check=True,
        text=True,
        timeout=10,
    )
    return completed.stdout.strip()


def _fake_uv(tmp_path: Path) -> tuple[Path, str]:
    executable = tmp_path / "verified-uv"
    executable.write_bytes(b"#!/bin/sh\nexit 99\n")
    executable.chmod(0o700)
    return executable, _sha256(executable.read_bytes())


def _junit_payload(test_count: int) -> bytes:
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        f'<testsuites tests="{test_count}" failures="0" errors="0" '
        f'skipped="0"><testsuite tests="{test_count}" failures="0" '
        'errors="0" skipped="0"/></testsuites>'
    ).encode("utf-8")


class FakeRunner:
    """실제 uv 회귀를 실행하지 않고 동일한 process/JUnit 경계를 재현한다."""

    def __init__(
        self,
        *,
        fail_role: str | None = None,
        base_passed: int = 262,
        replace_supplier: Path | None = None,
    ) -> None:
        self.fail_role = fail_role
        self.base_passed = base_passed
        self.replace_supplier = replace_supplier
        self.calls: list[tuple[list[str], dict[str, Any]]] = []
        self.uv_calls = 0

    def __call__(
        self,
        command: list[str],
        **kwargs: Any,
    ) -> subprocess.CompletedProcess[bytes]:
        self.calls.append((list(command), kwargs))
        if command[0] == "/usr/bin/git":
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=f"{_head()}\n".encode(),
                stderr=b"",
            )

        self.uv_calls += 1
        role, passed, deselected = self._classify(command, Path(kwargs["cwd"]))
        if self.uv_calls == 1 and self.replace_supplier is not None:
            replacement = self.replace_supplier.with_name("replacement-uv")
            replacement.write_bytes(b"#!/bin/sh\nexit 88\n")
            replacement.chmod(0o700)
            os.replace(replacement, self.replace_supplier)

        junit = self._junit_path(command)
        if junit is not None:
            junit.parent.mkdir(parents=True, exist_ok=True)
            junit.write_bytes(_junit_payload(passed))

        exit_code = 7 if role == self.fail_role else 0
        if role.endswith("pytest"):
            summary = f"{passed} passed"
            if deselected:
                summary += f", {deselected} deselected"
            stdout = f"{summary} in 0.01s\n".encode()
        else:
            stdout = f"{role} PASS\n".encode()
        stderr = b"forced failure\n" if exit_code else b""
        return subprocess.CompletedProcess(
            command,
            exit_code,
            stdout=stdout,
            stderr=stderr,
        )

    def _classify(
        self,
        command: list[str],
        cwd: Path,
    ) -> tuple[str, int, int]:
        if "lock" in command:
            return "lock", 0, 0
        if "sync" in command:
            return "sync", 0, 0
        if "ruff" in command:
            return "ruff", 0, 0
        if "mypy" in command:
            return "mypy", 0, 0
        if any(node in command for node in REPLACEMENT_RESEARCH_NODES):
            return "replacement-pytest", 2, 0
        if cwd.name == "python-services":
            return "pytest", 1344, 0
        return "base-pytest", self.base_passed, 1

    @staticmethod
    def _junit_path(command: list[str]) -> Path | None:
        if "--junitxml" not in command:
            return None
        return Path(command[command.index("--junitxml") + 1])


def _run(
    tmp_path: Path,
    runner: FakeRunner,
    *,
    subject: str | None = None,
    uv_sha256: str | None = None,
) -> tuple[Path, Path, str, dict[str, dict[str, Any]]]:
    uv, actual_sha256 = _fake_uv(tmp_path)
    correctness_root = tmp_path / "correctness"
    correctness_root.mkdir()
    output_root = correctness_root / "regression"
    commit = subject or _head()
    receipts = run_regression_gate(
        repo_root=REPO_ROOT,
        output_root=output_root,
        uv_executable=uv,
        uv_sha256=uv_sha256 or actual_sha256,
        benchmark_subject_commit=commit,
        timeout_seconds=30,
        runner=runner,
    )
    return uv, output_root, commit, receipts


def test_gate_emits_exact_compound_receipts_and_bound_raw_evidence(
    tmp_path: Path,
) -> None:
    runner = FakeRunner()
    uv, output_root, commit, receipts = _run(tmp_path, runner)

    production_path = output_root / "production-compound-receipt.v1.json"
    research_path = output_root / "research-compound-receipt.v1.json"
    assert receipts == {
        "production": json.loads(production_path.read_bytes()),
        "research": json.loads(research_path.read_bytes()),
    }
    assert production_path.read_bytes() == _canonical(receipts["production"])
    assert research_path.read_bytes() == _canonical(receipts["research"])

    production = receipts["production"]
    assert production == {
        "schemaVersion": "s1.4x-regression-compound-receipt-v1",
        "benchmarkSubjectCommit": commit,
        "project": "workspaces/decision-platform/python-services",
        "collectedCount": 1344,
        "basePassedCount": 1344,
        "deselectedCount": 0,
        "replacementPassedCount": 0,
        "totalExecutedPassedCount": 1344,
        "deselectedNodeIds": [],
        "replacementNodeIds": [],
        "commands": production["commands"],
        "status": "PASS",
    }
    assert [entry["role"] for entry in production["commands"]] == [
        "ruff",
        "mypy",
        "pytest",
    ]

    research = receipts["research"]
    assert research == {
        "schemaVersion": "s1.4x-regression-compound-receipt-v1",
        "benchmarkSubjectCommit": commit,
        "project": "workspaces/decision-platform/research/s1-4r-jax-risk",
        "collectedCount": 263,
        "basePassedCount": 262,
        "deselectedCount": 1,
        "replacementPassedCount": 2,
        "totalExecutedPassedCount": 264,
        "deselectedNodeIds": [DESELECTED_RESEARCH_NODE],
        "replacementNodeIds": list(REPLACEMENT_RESEARCH_NODES),
        "commands": research["commands"],
        "status": "PASS",
    }
    assert [entry["role"] for entry in research["commands"]] == [
        "ruff",
        "mypy",
        "replacement-pytest",
        "base-pytest",
    ]

    for receipt in receipts.values():
        for command in receipt["commands"]:
            assert set(command) == {
                "role",
                "exitCode",
                "stdoutPath",
                "stdoutSha256",
                "stderrPath",
                "stderrSha256",
                "status",
            }
            assert command["exitCode"] == 0
            assert command["status"] == "PASS"
            for path_field, sha_field in (
                ("stdoutPath", "stdoutSha256"),
                ("stderrPath", "stderrSha256"),
            ):
                relative = Path(command[path_field])
                assert not relative.is_absolute()
                assert relative.parts[0] == "regression"
                artifact = output_root.parent / relative
                assert command[sha_field] == _sha256(artifact.read_bytes())

    assert {
        path.name for path in (output_root / "junit").iterdir()
    } == {
        "production-pytest.xml",
        "research-base-pytest.xml",
        "research-replacement-pytest.xml",
    }
    manifest = json.loads(
        (output_root / "execution-manifest.v1.json").read_bytes()
    )
    assert manifest["schemaVersion"] == "s1.4x-regression-execution-manifest-v1"
    assert manifest["benchmarkSubjectCommit"] == commit
    assert manifest["uvExecutableSha256"] == _sha256(uv.read_bytes())
    assert manifest["status"] == "PASS"
    assert len(manifest["commandReceipts"]) == 11
    for entry in manifest["commandReceipts"]:
        command_receipt = output_root.parent / entry["path"]
        assert entry["sha256"] == _sha256(command_receipt.read_bytes())

    uv_calls = [
        call for call in runner.calls if call[0][0] != "/usr/bin/git"
    ]
    assert len(uv_calls) == 11
    assert [next(arg for arg in command if arg in {"lock", "sync", "ruff", "mypy", "pytest"}) for command, _ in uv_calls] == [
        "lock",
        "sync",
        "ruff",
        "mypy",
        "pytest",
        "lock",
        "sync",
        "ruff",
        "mypy",
        "pytest",
        "pytest",
    ]
    sealed_paths = {command[0] for command, _ in uv_calls}
    assert len(sealed_paths) == 1
    assert next(iter(sealed_paths)).startswith("/proc/self/fd/")
    assert all(kwargs["pass_fds"] for _, kwargs in uv_calls)


def test_gate_keeps_running_the_sealed_uv_after_supplier_replacement(
    tmp_path: Path,
) -> None:
    uv = tmp_path / "verified-uv"
    runner = FakeRunner(replace_supplier=uv)
    _, output_root, _, receipts = _run(tmp_path, runner)

    assert receipts["production"]["status"] == "PASS"
    assert receipts["research"]["status"] == "PASS"
    manifest = json.loads(
        (output_root / "execution-manifest.v1.json").read_bytes()
    )
    assert manifest["uvExecutableSha256"] != _sha256(uv.read_bytes())
    uv_commands = [
        command
        for command, _ in runner.calls
        if command[0] != "/usr/bin/git"
    ]
    assert len({command[0] for command in uv_commands}) == 1


@pytest.mark.parametrize("occupied_kind", ["directory", "file", "symlink"])
def test_gate_rejects_preexisting_or_symlink_output_root(
    tmp_path: Path,
    occupied_kind: str,
) -> None:
    uv, uv_sha256 = _fake_uv(tmp_path)
    correctness_root = tmp_path / "correctness"
    correctness_root.mkdir()
    output_root = correctness_root / "regression"
    if occupied_kind == "directory":
        output_root.mkdir()
    elif occupied_kind == "file":
        output_root.write_bytes(b"occupied")
    else:
        target = tmp_path / "target"
        target.mkdir()
        output_root.symlink_to(target, target_is_directory=True)
    runner = FakeRunner()

    with pytest.raises(
        RegressionGateError,
        match="OUTPUT_ROOT_ALREADY_EXISTS",
    ):
        run_regression_gate(
            repo_root=REPO_ROOT,
            output_root=output_root,
            uv_executable=uv,
            uv_sha256=uv_sha256,
            benchmark_subject_commit=_head(),
            runner=runner,
        )
    assert runner.calls == []


def test_gate_rejects_subject_and_uv_identity_before_creating_output(
    tmp_path: Path,
) -> None:
    uv, uv_sha256 = _fake_uv(tmp_path)
    correctness_root = tmp_path / "correctness"
    correctness_root.mkdir()
    runner = FakeRunner()

    with pytest.raises(RegressionGateError, match="SUBJECT_HEAD_MISMATCH"):
        run_regression_gate(
            repo_root=REPO_ROOT,
            output_root=correctness_root / "regression",
            uv_executable=uv,
            uv_sha256=uv_sha256,
            benchmark_subject_commit="0" * 40,
            runner=runner,
        )
    assert not (correctness_root / "regression").exists()

    runner = FakeRunner()
    with pytest.raises(RegressionGateError, match="UV_SHA256_MISMATCH"):
        run_regression_gate(
            repo_root=REPO_ROOT,
            output_root=correctness_root / "regression",
            uv_executable=uv,
            uv_sha256="f" * 64,
            benchmark_subject_commit=_head(),
            runner=runner,
        )
    assert not (correctness_root / "regression").exists()


def test_gate_stops_on_first_failure_and_preserves_logs_without_receipts(
    tmp_path: Path,
) -> None:
    runner = FakeRunner(fail_role="mypy")
    uv, uv_sha256 = _fake_uv(tmp_path)
    correctness_root = tmp_path / "correctness"
    correctness_root.mkdir()
    output_root = correctness_root / "regression"

    with pytest.raises(RegressionGateError, match="COMMAND_FAILED:production-mypy"):
        run_regression_gate(
            repo_root=REPO_ROOT,
            output_root=output_root,
            uv_executable=uv,
            uv_sha256=uv_sha256,
            benchmark_subject_commit=_head(),
            runner=runner,
        )

    assert runner.uv_calls == 4
    assert (output_root / "logs/production-mypy.stdout").is_file()
    assert (output_root / "logs/production-mypy.stderr").read_bytes() == (
        b"forced failure\n"
    )
    assert not (output_root / "production-compound-receipt.v1.json").exists()
    assert not (output_root / "research-compound-receipt.v1.json").exists()
    failure_receipt = json.loads(
        (output_root / "commands/production-mypy.command.v1.json").read_bytes()
    )
    assert failure_receipt["exitCode"] == 7
    assert failure_receipt["status"] == "FAIL"


def test_gate_rejects_wrong_pytest_accounting_without_pass_receipts(
    tmp_path: Path,
) -> None:
    runner = FakeRunner(base_passed=261)
    uv, uv_sha256 = _fake_uv(tmp_path)
    correctness_root = tmp_path / "correctness"
    correctness_root.mkdir()
    output_root = correctness_root / "regression"

    with pytest.raises(
        RegressionGateError,
        match="PYTEST_COUNT_MISMATCH:research-base-pytest",
    ):
        run_regression_gate(
            repo_root=REPO_ROOT,
            output_root=output_root,
            uv_executable=uv,
            uv_sha256=uv_sha256,
            benchmark_subject_commit=_head(),
            runner=runner,
        )

    assert runner.uv_calls == 11
    assert (output_root / "junit/research-base-pytest.xml").is_file()
    assert not (output_root / "production-compound-receipt.v1.json").exists()
    assert not (output_root / "research-compound-receipt.v1.json").exists()
