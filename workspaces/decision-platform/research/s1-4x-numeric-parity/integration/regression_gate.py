#!/usr/bin/env python3
"""Frozen production/S1.4R Python 회귀를 raw compound receipt로 결속한다."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

Runner = Callable[..., subprocess.CompletedProcess[bytes]]

RECEIPT_SCHEMA = "s1.4x-regression-compound-receipt-v1"
COMMAND_RECEIPT_SCHEMA = "s1.4x-regression-command-receipt-v1"
EXECUTION_MANIFEST_SCHEMA = "s1.4x-regression-execution-manifest-v1"
PRODUCTION_PROJECT = "workspaces/decision-platform/python-services"
RESEARCH_PROJECT = "workspaces/decision-platform/research/s1-4r-jax-risk"
ORACLE_PROJECT = (
    "workspaces/decision-platform/research/s1-4x-numeric-parity/oracle"
)
BOUNDARY_TEST = (
    "workspaces/decision-platform/research/s1-4x-numeric-parity/"
    "integration/tests/test_s1_4r_regression_boundary.py"
)
DESELECTED_RESEARCH_NODE = (
    "tests/test_production_isolation.py::"
    "test_branch_diff_is_confined_to_the_research_project_and_two_workflows"
)
REPLACEMENT_RESEARCH_NODES = (
    (
        f"{BOUNDARY_TEST}::"
        "test_s1_4x_branch_diff_is_confined_to_the_experiment_boundary"
    ),
    (
        f"{BOUNDARY_TEST}::"
        "test_aggregate_deselects_only_the_inapplicable_s1_4r_branch_scope"
    ),
)
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
MAX_JUNIT_BYTES = 64 * 1024 * 1024


class RegressionGateError(ValueError):
    """회귀 실행 또는 raw evidence closure 위반의 stable error code다."""


@dataclass(frozen=True, slots=True)
class _VerifiedExecutable:
    """열린 FD가 supplier pathname 교체와 무관하게 검증된 uv bytes를 고정한다."""

    descriptor: int
    sha256: str

    @property
    def process_path(self) -> str:
        return f"/proc/self/fd/{self.descriptor}"


@dataclass(frozen=True, slots=True)
class _CommandSpec:
    """한 직렬 회귀 단계의 portable identity와 process 계약이다."""

    label: str
    project: str
    role: str
    cwd: Path
    arguments: tuple[str, ...]
    junit_path: Path | None = None
    expected_passed: int | None = None
    expected_deselected: int = 0
    compound_role: bool = False


@dataclass(frozen=True, slots=True)
class _CommandEvidence:
    """Compound receipt와 execution manifest가 소비하는 검증 완료 evidence다."""

    command_receipt_path: Path
    compound_entry: dict[str, Any] | None


def _canonical_json_bytes(value: Any) -> bytes:
    try:
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
    except (TypeError, ValueError) as exc:
        raise RegressionGateError("CANONICAL_JSON_INVALID") from exc


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            while block := stream.read(1024 * 1024):
                digest.update(block)
    except OSError as exc:
        raise RegressionGateError("ARTIFACT_READ_FAILED") from exc
    return digest.hexdigest()


def _require_canonical_directory(path: Path, *, code: str) -> Path:
    if not path.is_absolute():
        raise RegressionGateError(code)
    try:
        resolved = path.resolve(strict=True)
        metadata = path.lstat()
    except OSError as exc:
        raise RegressionGateError(code) from exc
    if (
        resolved != path
        or stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
    ):
        raise RegressionGateError(code)
    return path


def _preflight_output_root(output_root: Path) -> None:
    if not output_root.is_absolute() or output_root.name != "regression":
        raise RegressionGateError("OUTPUT_ROOT_INVALID")
    if output_root.exists() or output_root.is_symlink():
        raise RegressionGateError("OUTPUT_ROOT_ALREADY_EXISTS")
    try:
        if output_root.resolve(strict=False) != output_root:
            raise RegressionGateError("OUTPUT_ROOT_INVALID")
    except OSError as exc:
        raise RegressionGateError("OUTPUT_ROOT_INVALID") from exc
    _require_canonical_directory(
        output_root.parent,
        code="OUTPUT_PARENT_INVALID",
    )


def _write_exclusive(path: Path, payload: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
    no_follow = getattr(os, "O_NOFOLLOW", None)
    if no_follow is None:
        raise RegressionGateError("OUTPUT_NOFOLLOW_UNSUPPORTED")
    descriptor: int | None = None
    try:
        descriptor = os.open(path, flags | no_follow, 0o600)
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written < 1:
                raise OSError("zero-byte artifact write")
            view = view[written:]
        os.fsync(descriptor)
    except FileExistsError as exc:
        raise RegressionGateError("OUTPUT_ARTIFACT_ALREADY_EXISTS") from exc
    except OSError as exc:
        raise RegressionGateError("OUTPUT_ARTIFACT_WRITE_FAILED") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _write_canonical_json(path: Path, value: Any) -> None:
    _write_exclusive(path, _canonical_json_bytes(value))


def _read_regular_artifact(path: Path, *, code: str) -> bytes:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise RegressionGateError(code) from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise RegressionGateError(code)
    if metadata.st_size > MAX_JUNIT_BYTES:
        raise RegressionGateError(code)
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise RegressionGateError(code) from exc
    try:
        after = path.lstat()
    except OSError as exc:
        raise RegressionGateError(code) from exc
    if (
        stat.S_ISLNK(after.st_mode)
        or not stat.S_ISREG(after.st_mode)
        or (metadata.st_dev, metadata.st_ino, metadata.st_size)
        != (after.st_dev, after.st_ino, after.st_size)
    ):
        raise RegressionGateError(code)
    return payload


def _open_verified_uv(path: Path, expected_sha256: str) -> _VerifiedExecutable:
    if SHA256_PATTERN.fullmatch(expected_sha256) is None:
        raise RegressionGateError("UV_SHA256_INVALID")
    if not path.is_absolute():
        raise RegressionGateError("UV_EXECUTABLE_INVALID")
    try:
        resolved = path.resolve(strict=True)
        before = path.lstat()
    except OSError as exc:
        raise RegressionGateError("UV_EXECUTABLE_INVALID") from exc
    if (
        resolved != path
        or stat.S_ISLNK(before.st_mode)
        or not stat.S_ISREG(before.st_mode)
        or not os.access(path, os.X_OK)
    ):
        raise RegressionGateError("UV_EXECUTABLE_INVALID")
    no_follow = getattr(os, "O_NOFOLLOW", None)
    if no_follow is None:
        raise RegressionGateError("UV_NOFOLLOW_UNSUPPORTED")
    source_descriptor: int | None = None
    sealed_descriptor: int | None = None
    try:
        source_descriptor = os.open(
            path,
            os.O_RDONLY | os.O_CLOEXEC | no_follow,
        )
    except OSError as exc:
        raise RegressionGateError("UV_EXECUTABLE_OPEN_FAILED") from exc
    try:
        opened = os.fstat(source_descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or (before.st_dev, before.st_ino)
            != (opened.st_dev, opened.st_ino)
        ):
            raise RegressionGateError("UV_EXECUTABLE_IDENTITY_CHANGED")
        digest = hashlib.sha256()
        payload = bytearray()
        while block := os.read(source_descriptor, 1024 * 1024):
            digest.update(block)
            payload.extend(block)
        actual_sha256 = digest.hexdigest()
        if actual_sha256 != expected_sha256:
            raise RegressionGateError("UV_SHA256_MISMATCH")
        after = path.lstat()
        if (
            stat.S_ISLNK(after.st_mode)
            or not stat.S_ISREG(after.st_mode)
            or (
                before.st_dev,
                before.st_ino,
                before.st_size,
                before.st_mtime_ns,
                before.st_ctime_ns,
            )
            != (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            )
        ):
            raise RegressionGateError("UV_EXECUTABLE_IDENTITY_CHANGED")
        temporary_flag = getattr(os, "O_TMPFILE", None)
        if temporary_flag is None:
            raise RegressionGateError("UV_ANONYMOUS_FILE_UNSUPPORTED")
        writable_descriptor = os.open(
            path.parent,
            temporary_flag | os.O_RDWR | os.O_CLOEXEC,
            0o500,
        )
        try:
            view = memoryview(payload)
            while view:
                written = os.write(writable_descriptor, view)
                if written < 1:
                    raise OSError("zero-byte anonymous executable write")
                view = view[written:]
            os.fsync(writable_descriptor)
            os.fchmod(writable_descriptor, 0o500)
            sealed_descriptor = os.open(
                f"/proc/self/fd/{writable_descriptor}",
                os.O_RDONLY | os.O_CLOEXEC,
            )
        finally:
            os.close(writable_descriptor)
        if _sha256_file(Path(f"/proc/self/fd/{sealed_descriptor}")) != actual_sha256:
            raise RegressionGateError("UV_ANONYMOUS_FILE_INVALID")
        return _VerifiedExecutable(
            descriptor=sealed_descriptor,
            sha256=actual_sha256,
        )
    except BaseException:
        if sealed_descriptor is not None:
            os.close(sealed_descriptor)
        raise
    finally:
        if source_descriptor is not None:
            os.close(source_descriptor)


def _normalize_stream(value: bytes | str | None, *, code: str) -> bytes:
    if value is None:
        return b""
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        try:
            return value.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise RegressionGateError(code) from exc
    raise RegressionGateError(code)


def _read_head(repo_root: Path, runner: Runner) -> str:
    try:
        completed = runner(
            [
                "/usr/bin/git",
                "-c",
                "core.fsmonitor=false",
                "rev-parse",
                "--verify",
                "HEAD^{commit}",
            ],
            cwd=repo_root,
            capture_output=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RegressionGateError("HEAD_READ_FAILED") from exc
    stdout = _normalize_stream(completed.stdout, code="HEAD_OUTPUT_INVALID")
    stderr = _normalize_stream(completed.stderr, code="HEAD_OUTPUT_INVALID")
    try:
        head = stdout.decode("ascii").strip()
    except UnicodeDecodeError as exc:
        raise RegressionGateError("HEAD_OUTPUT_INVALID") from exc
    if (
        completed.returncode != 0
        or stderr
        or COMMIT_PATTERN.fullmatch(head) is None
    ):
        raise RegressionGateError("HEAD_READ_FAILED")
    return head


def _assert_clean_repository(repo_root: Path, runner: Runner) -> None:
    try:
        completed = runner(
            [
                "/usr/bin/git",
                "-c",
                "core.fsmonitor=false",
                "status",
                "--porcelain=v1",
                "-z",
                "--untracked-files=all",
            ],
            cwd=repo_root,
            capture_output=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RegressionGateError("SUBJECT_STATUS_FAILED") from exc
    stdout = _normalize_stream(
        completed.stdout,
        code="SUBJECT_STATUS_INVALID",
    )
    stderr = _normalize_stream(
        completed.stderr,
        code="SUBJECT_STATUS_INVALID",
    )
    if completed.returncode != 0 or stderr:
        raise RegressionGateError("SUBJECT_STATUS_FAILED")
    if stdout:
        raise RegressionGateError("SUBJECT_WORKTREE_DIRTY")


def _portable_argument(
    argument: str,
    *,
    repo_root: Path,
    correctness_root: Path,
) -> str:
    for root in (repo_root, correctness_root):
        try:
            relative = Path(argument).relative_to(root)
        except (TypeError, ValueError):
            continue
        return relative.as_posix()
    return argument


def _logical_command(
    spec: _CommandSpec,
    *,
    repo_root: Path,
    correctness_root: Path,
) -> list[str]:
    return [
        "S1_4X_VERIFIED_UV_BIN",
        *(
            _portable_argument(
                argument,
                repo_root=repo_root,
                correctness_root=correctness_root,
            )
            for argument in spec.arguments
        ),
    ]


def _command_environment(
    *,
    runtime_home: Path,
    runtime_tmp: Path,
) -> dict[str, str]:
    environment = {
        "HOME": str(runtime_home),
        "JAX_ENABLE_X64": "1",
        "JAX_NUM_THREADS": "1",
        "JAX_PLATFORMS": "cpu",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "MKL_NUM_THREADS": "1",
        "NO_COLOR": "1",
        "NUMEXPR_NUM_THREADS": "1",
        "OMP_NUM_THREADS": "1",
        "OPENBLAS_NUM_THREADS": "1",
        "PATH": "/usr/bin:/bin",
        "PY_COLORS": "0",
        "PYTHONDONTWRITEBYTECODE": "1",
        "TEMP": str(runtime_tmp),
        "TMP": str(runtime_tmp),
        "TMPDIR": str(runtime_tmp),
        "TZ": "UTC",
        "UV_PYTHON": "3.12.13",
        "VECLIB_MAXIMUM_THREADS": "1",
        "XLA_FLAGS": (
            "--xla_cpu_multi_thread_eigen=false "
            "intra_op_parallelism_threads=1"
        ),
    }
    cache = os.environ.get("UV_CACHE_DIR")
    if cache:
        cache_path = Path(cache)
        _require_canonical_directory(
            cache_path,
            code="UV_CACHE_DIR_INVALID",
        )
        environment["UV_CACHE_DIR"] = str(cache_path)
    return environment


def _pytest_summary_counts(stdout: bytes, *, label: str) -> tuple[int, int]:
    try:
        text = stdout.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RegressionGateError(f"PYTEST_STDOUT_INVALID:{label}") from exc
    summary = next(
        (
            line
            for line in reversed(text.splitlines())
            if re.search(r"\b\d+ passed\b", line)
        ),
        None,
    )
    if summary is None:
        raise RegressionGateError(f"PYTEST_SUMMARY_MISSING:{label}")
    passed_matches = re.findall(r"\b(\d+) passed\b", summary)
    deselected_matches = re.findall(r"\b(\d+) deselected\b", summary)
    if len(passed_matches) != 1 or len(deselected_matches) > 1:
        raise RegressionGateError(f"PYTEST_SUMMARY_INVALID:{label}")
    return (
        int(passed_matches[0]),
        int(deselected_matches[0]) if deselected_matches else 0,
    )


def _xml_count(root: ET.Element, field: str, *, label: str) -> int:
    raw = root.attrib.get(field)
    if raw is None or re.fullmatch(r"(?:0|[1-9][0-9]*)", raw) is None:
        raise RegressionGateError(f"JUNIT_INVALID:{label}")
    return int(raw)


def _validate_junit(
    path: Path,
    *,
    stdout: bytes,
    expected_passed: int,
    expected_deselected: int,
    label: str,
) -> str:
    payload = _read_regular_artifact(path, code=f"JUNIT_INVALID:{label}")
    if b"<!DOCTYPE" in payload or b"<!ENTITY" in payload:
        raise RegressionGateError(f"JUNIT_INVALID:{label}")
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise RegressionGateError(f"JUNIT_INVALID:{label}") from exc
    if root.tag == "testsuite":
        suite = root
    elif root.tag == "testsuites":
        children = list(root)
        if len(children) != 1 or children[0].tag != "testsuite":
            raise RegressionGateError(f"JUNIT_INVALID:{label}")
        suite = children[0]
    else:
        raise RegressionGateError(f"JUNIT_INVALID:{label}")
    observed = {
        field: _xml_count(suite, field, label=label)
        for field in ("tests", "failures", "errors", "skipped")
    }
    summary_passed, summary_deselected = _pytest_summary_counts(
        stdout,
        label=label,
    )
    if (
        observed
        != {
            "tests": expected_passed,
            "failures": 0,
            "errors": 0,
            "skipped": 0,
        }
        or summary_passed != expected_passed
        or summary_deselected != expected_deselected
    ):
        raise RegressionGateError(f"PYTEST_COUNT_MISMATCH:{label}")
    return _sha256_bytes(payload)


def _portable_path(path: Path, correctness_root: Path) -> str:
    try:
        relative = path.relative_to(correctness_root)
    except ValueError as exc:
        raise RegressionGateError("ARTIFACT_PATH_NOT_PORTABLE") from exc
    if not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
        raise RegressionGateError("ARTIFACT_PATH_NOT_PORTABLE")
    return relative.as_posix()


def _command_record(
    *,
    spec: _CommandSpec,
    benchmark_subject_commit: str,
    uv_sha256: str,
    logical_command: list[str],
    exit_code: int,
    stdout_path: Path,
    stdout_sha256: str,
    stderr_path: Path,
    stderr_sha256: str,
    junit_sha256: str | None,
    correctness_root: Path,
    status: str,
) -> dict[str, Any]:
    junit_path = (
        _portable_path(spec.junit_path, correctness_root)
        if spec.junit_path is not None
        else None
    )
    return {
        "schemaVersion": COMMAND_RECEIPT_SCHEMA,
        "benchmarkSubjectCommit": benchmark_subject_commit,
        "project": spec.project,
        "role": spec.role,
        "uvExecutableSha256": uv_sha256,
        "commandArgv": logical_command,
        "commandArgvSha256": _sha256_bytes(
            _canonical_json_bytes(logical_command)
        ),
        "exitCode": exit_code,
        "stdoutPath": _portable_path(stdout_path, correctness_root),
        "stdoutSha256": stdout_sha256,
        "stderrPath": _portable_path(stderr_path, correctness_root),
        "stderrSha256": stderr_sha256,
        "junitPath": junit_path,
        "junitSha256": junit_sha256,
        "status": status,
    }


def _execute_command(
    *,
    spec: _CommandSpec,
    verified_uv: _VerifiedExecutable,
    benchmark_subject_commit: str,
    repo_root: Path,
    output_root: Path,
    runtime_home: Path,
    runtime_tmp: Path,
    timeout_seconds: int,
    runner: Runner,
) -> _CommandEvidence:
    correctness_root = output_root.parent
    stdout_path = output_root / "logs" / f"{spec.label}.stdout"
    stderr_path = output_root / "logs" / f"{spec.label}.stderr"
    command_receipt_path = (
        output_root / "commands" / f"{spec.label}.command.v1.json"
    )
    logical_command = _logical_command(
        spec,
        repo_root=repo_root,
        correctness_root=correctness_root,
    )
    actual_command = [verified_uv.process_path, *spec.arguments]
    if _sha256_file(Path(verified_uv.process_path)) != verified_uv.sha256:
        raise RegressionGateError(f"UV_SEALED_BYTES_CHANGED:{spec.label}")
    try:
        completed = runner(
            actual_command,
            cwd=spec.cwd,
            env=_command_environment(
                runtime_home=runtime_home,
                runtime_tmp=runtime_tmp,
            ),
            capture_output=True,
            check=False,
            timeout=timeout_seconds,
            pass_fds=(verified_uv.descriptor,),
        )
        stdout = _normalize_stream(
            completed.stdout,
            code=f"COMMAND_STREAM_INVALID:{spec.label}",
        )
        stderr = _normalize_stream(
            completed.stderr,
            code=f"COMMAND_STREAM_INVALID:{spec.label}",
        )
        exit_code = completed.returncode
    except subprocess.TimeoutExpired as exc:
        stdout = _normalize_stream(
            exc.stdout,
            code=f"COMMAND_STREAM_INVALID:{spec.label}",
        )
        stderr = _normalize_stream(
            exc.stderr,
            code=f"COMMAND_STREAM_INVALID:{spec.label}",
        )
        exit_code = 124
    except OSError as exc:
        raise RegressionGateError(f"COMMAND_START_FAILED:{spec.label}") from exc
    if _sha256_file(Path(verified_uv.process_path)) != verified_uv.sha256:
        raise RegressionGateError(f"UV_SEALED_BYTES_CHANGED:{spec.label}")

    _write_exclusive(stdout_path, stdout)
    _write_exclusive(stderr_path, stderr)
    stdout_sha256 = _sha256_file(stdout_path)
    stderr_sha256 = _sha256_file(stderr_path)
    if exit_code != 0:
        record = _command_record(
            spec=spec,
            benchmark_subject_commit=benchmark_subject_commit,
            uv_sha256=verified_uv.sha256,
            logical_command=logical_command,
            exit_code=exit_code,
            stdout_path=stdout_path,
            stdout_sha256=stdout_sha256,
            stderr_path=stderr_path,
            stderr_sha256=stderr_sha256,
            junit_sha256=None,
            correctness_root=correctness_root,
            status="FAIL",
        )
        _write_canonical_json(command_receipt_path, record)
        if exit_code == 124:
            raise RegressionGateError(f"COMMAND_TIMEOUT:{spec.label}")
        raise RegressionGateError(f"COMMAND_FAILED:{spec.label}")

    junit_sha256: str | None = None
    if spec.expected_passed is not None:
        if spec.junit_path is None:
            raise RegressionGateError(f"JUNIT_PATH_MISSING:{spec.label}")
        junit_sha256 = _validate_junit(
            spec.junit_path,
            stdout=stdout,
            expected_passed=spec.expected_passed,
            expected_deselected=spec.expected_deselected,
            label=spec.label,
        )
    record = _command_record(
        spec=spec,
        benchmark_subject_commit=benchmark_subject_commit,
        uv_sha256=verified_uv.sha256,
        logical_command=logical_command,
        exit_code=0,
        stdout_path=stdout_path,
        stdout_sha256=stdout_sha256,
        stderr_path=stderr_path,
        stderr_sha256=stderr_sha256,
        junit_sha256=junit_sha256,
        correctness_root=correctness_root,
        status="PASS",
    )
    _write_canonical_json(command_receipt_path, record)
    compound_entry = None
    if spec.compound_role:
        compound_entry = {
            "role": spec.role,
            "exitCode": 0,
            "stdoutPath": record["stdoutPath"],
            "stdoutSha256": stdout_sha256,
            "stderrPath": record["stderrPath"],
            "stderrSha256": stderr_sha256,
            "status": "PASS",
        }
    return _CommandEvidence(
        command_receipt_path=command_receipt_path,
        compound_entry=compound_entry,
    )


def _command_specs(
    *,
    repo_root: Path,
    output_root: Path,
) -> tuple[_CommandSpec, ...]:
    production = repo_root / PRODUCTION_PROJECT
    research = repo_root / RESEARCH_PROJECT
    oracle = repo_root / ORACLE_PROJECT
    junit = output_root / "junit"
    return (
        _CommandSpec(
            label="production-lock",
            project=PRODUCTION_PROJECT,
            role="lock",
            cwd=production,
            arguments=(
                "--no-config",
                "lock",
                "--check",
                "--project",
                str(production),
            ),
        ),
        _CommandSpec(
            label="production-sync",
            project=PRODUCTION_PROJECT,
            role="sync",
            cwd=production,
            arguments=(
                "--no-config",
                "sync",
                "--frozen",
                "--project",
                str(production),
            ),
        ),
        _CommandSpec(
            label="production-ruff",
            project=PRODUCTION_PROJECT,
            role="ruff",
            cwd=production,
            arguments=(
                "--no-config",
                "run",
                "--frozen",
                "--project",
                str(production),
                "ruff",
                "check",
                ".",
            ),
            compound_role=True,
        ),
        _CommandSpec(
            label="production-mypy",
            project=PRODUCTION_PROJECT,
            role="mypy",
            cwd=production,
            arguments=(
                "--no-config",
                "run",
                "--frozen",
                "--project",
                str(production),
                "mypy",
                "app",
            ),
            compound_role=True,
        ),
        _CommandSpec(
            label="production-pytest",
            project=PRODUCTION_PROJECT,
            role="pytest",
            cwd=production,
            arguments=(
                "--no-config",
                "run",
                "--frozen",
                "--project",
                str(production),
                "pytest",
                "-q",
                "--junitxml",
                str(junit / "production-pytest.xml"),
            ),
            junit_path=junit / "production-pytest.xml",
            expected_passed=1344,
            compound_role=True,
        ),
        _CommandSpec(
            label="research-lock",
            project=RESEARCH_PROJECT,
            role="lock",
            cwd=research,
            arguments=(
                "--no-config",
                "lock",
                "--check",
                "--project",
                str(research),
            ),
        ),
        _CommandSpec(
            label="research-sync",
            project=RESEARCH_PROJECT,
            role="sync",
            cwd=research,
            arguments=(
                "--no-config",
                "sync",
                "--frozen",
                "--all-groups",
                "--project",
                str(research),
            ),
        ),
        _CommandSpec(
            label="research-ruff",
            project=RESEARCH_PROJECT,
            role="ruff",
            cwd=research,
            arguments=(
                "--no-config",
                "run",
                "--frozen",
                "--project",
                str(research),
                "ruff",
                "check",
                ".",
            ),
            compound_role=True,
        ),
        _CommandSpec(
            label="research-mypy",
            project=RESEARCH_PROJECT,
            role="mypy",
            cwd=research,
            arguments=(
                "--no-config",
                "run",
                "--frozen",
                "--project",
                str(research),
                "mypy",
                "src",
                "benchmarks",
            ),
            compound_role=True,
        ),
        _CommandSpec(
            label="research-replacement-pytest",
            project=RESEARCH_PROJECT,
            role="replacement-pytest",
            cwd=repo_root,
            arguments=(
                "--no-config",
                "run",
                "--frozen",
                "--project",
                str(oracle),
                "pytest",
                "-q",
                "--junitxml",
                str(junit / "research-replacement-pytest.xml"),
                *REPLACEMENT_RESEARCH_NODES,
            ),
            junit_path=junit / "research-replacement-pytest.xml",
            expected_passed=2,
            compound_role=True,
        ),
        _CommandSpec(
            label="research-base-pytest",
            project=RESEARCH_PROJECT,
            role="base-pytest",
            cwd=research,
            arguments=(
                "--no-config",
                "run",
                "--frozen",
                "--project",
                str(research),
                "pytest",
                "-q",
                "--junitxml",
                str(junit / "research-base-pytest.xml"),
                f"--deselect={DESELECTED_RESEARCH_NODE}",
            ),
            junit_path=junit / "research-base-pytest.xml",
            expected_passed=262,
            expected_deselected=1,
            compound_role=True,
        ),
    )


def _compound_receipt(
    *,
    benchmark_subject_commit: str,
    project: str,
    commands: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    if project == PRODUCTION_PROJECT:
        counts = (1344, 1344, 0, 0, 1344)
        deselected: list[str] = []
        replacements: list[str] = []
    elif project == RESEARCH_PROJECT:
        counts = (263, 262, 1, 2, 264)
        deselected = [DESELECTED_RESEARCH_NODE]
        replacements = list(REPLACEMENT_RESEARCH_NODES)
    else:
        raise RegressionGateError("PROJECT_INVALID")
    (
        collected,
        base_passed,
        deselected_count,
        replacement_passed,
        total_executed,
    ) = counts
    return {
        "schemaVersion": RECEIPT_SCHEMA,
        "benchmarkSubjectCommit": benchmark_subject_commit,
        "project": project,
        "collectedCount": collected,
        "basePassedCount": base_passed,
        "deselectedCount": deselected_count,
        "replacementPassedCount": replacement_passed,
        "totalExecutedPassedCount": total_executed,
        "deselectedNodeIds": deselected,
        "replacementNodeIds": replacements,
        "commands": list(commands),
        "status": "PASS",
    }


def run_regression_gate(
    *,
    repo_root: Path,
    output_root: Path,
    uv_executable: Path,
    uv_sha256: str,
    benchmark_subject_commit: str,
    timeout_seconds: int = 7200,
    runner: Runner = subprocess.run,
) -> dict[str, dict[str, Any]]:
    """HEAD와 검증된 uv bytes에 묶어 두 Python workspace 회귀를 직렬 실행한다."""

    repository = _require_canonical_directory(
        repo_root,
        code="REPO_ROOT_INVALID",
    )
    _preflight_output_root(output_root)
    if timeout_seconds < 1:
        raise RegressionGateError("TIMEOUT_INVALID")
    if COMMIT_PATTERN.fullmatch(benchmark_subject_commit) is None:
        raise RegressionGateError("SUBJECT_COMMIT_INVALID")
    if _read_head(repository, runner) != benchmark_subject_commit:
        raise RegressionGateError("SUBJECT_HEAD_MISMATCH")
    _assert_clean_repository(repository, runner)
    verified_uv = _open_verified_uv(uv_executable, uv_sha256)
    try:
        try:
            output_root.mkdir(mode=0o700)
            (output_root / "logs").mkdir(mode=0o700)
            (output_root / "junit").mkdir(mode=0o700)
            (output_root / "commands").mkdir(mode=0o700)
            runtime_home = output_root / "runtime-home"
            runtime_tmp = output_root / "runtime-tmp"
            runtime_home.mkdir(mode=0o700)
            runtime_tmp.mkdir(mode=0o700)
        except FileExistsError as exc:
            raise RegressionGateError("OUTPUT_ROOT_ALREADY_EXISTS") from exc
        except OSError as exc:
            raise RegressionGateError("OUTPUT_ROOT_CREATE_FAILED") from exc

        production_commands: list[dict[str, Any]] = []
        research_commands: list[dict[str, Any]] = []
        command_receipts: list[dict[str, str]] = []
        for spec in _command_specs(
            repo_root=repository,
            output_root=output_root,
        ):
            evidence = _execute_command(
                spec=spec,
                verified_uv=verified_uv,
                benchmark_subject_commit=benchmark_subject_commit,
                repo_root=repository,
                output_root=output_root,
                runtime_home=runtime_home,
                runtime_tmp=runtime_tmp,
                timeout_seconds=timeout_seconds,
                runner=runner,
            )
            command_receipts.append(
                {
                    "path": _portable_path(
                        evidence.command_receipt_path,
                        output_root.parent,
                    ),
                    "sha256": _sha256_file(evidence.command_receipt_path),
                }
            )
            if evidence.compound_entry is not None:
                if spec.project == PRODUCTION_PROJECT:
                    production_commands.append(evidence.compound_entry)
                elif spec.project == RESEARCH_PROJECT:
                    research_commands.append(evidence.compound_entry)
                else:
                    raise RegressionGateError("PROJECT_INVALID")

        if [entry["role"] for entry in production_commands] != [
            "ruff",
            "mypy",
            "pytest",
        ]:
            raise RegressionGateError("PRODUCTION_COMMAND_CLOSURE_INVALID")
        if [entry["role"] for entry in research_commands] != [
            "ruff",
            "mypy",
            "replacement-pytest",
            "base-pytest",
        ]:
            raise RegressionGateError("RESEARCH_COMMAND_CLOSURE_INVALID")
        if _read_head(repository, runner) != benchmark_subject_commit:
            raise RegressionGateError("SUBJECT_HEAD_CHANGED")
        _assert_clean_repository(repository, runner)

        execution_manifest = {
            "schemaVersion": EXECUTION_MANIFEST_SCHEMA,
            "benchmarkSubjectCommit": benchmark_subject_commit,
            "uvExecutableSha256": verified_uv.sha256,
            "commandReceipts": command_receipts,
            "status": "PASS",
        }
        _write_canonical_json(
            output_root / "execution-manifest.v1.json",
            execution_manifest,
        )
        receipts = {
            "production": _compound_receipt(
                benchmark_subject_commit=benchmark_subject_commit,
                project=PRODUCTION_PROJECT,
                commands=production_commands,
            ),
            "research": _compound_receipt(
                benchmark_subject_commit=benchmark_subject_commit,
                project=RESEARCH_PROJECT,
                commands=research_commands,
            ),
        }
        _write_canonical_json(
            output_root / "production-compound-receipt.v1.json",
            receipts["production"],
        )
        _write_canonical_json(
            output_root / "research-compound-receipt.v1.json",
            receipts["research"],
        )
        return receipts
    finally:
        os.close(verified_uv.descriptor)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--uv-bin", required=True, type=Path)
    parser.add_argument("--uv-sha256", required=True)
    parser.add_argument("--benchmark-subject-commit", required=True)
    parser.add_argument("--timeout-seconds", type=int, default=7200)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        run_regression_gate(
            repo_root=arguments.repo_root,
            output_root=arguments.output_root,
            uv_executable=arguments.uv_bin,
            uv_sha256=arguments.uv_sha256,
            benchmark_subject_commit=arguments.benchmark_subject_commit,
            timeout_seconds=arguments.timeout_seconds,
        )
    except RegressionGateError as exc:
        print(f"S1_4X_REGRESSION_GATE_FAIL:{exc}", file=sys.stderr)
        return 2
    summary = {
        "productionReceipt": (
            "regression/production-compound-receipt.v1.json"
        ),
        "researchReceipt": "regression/research-compound-receipt.v1.json",
        "status": "PASS",
    }
    sys.stdout.buffer.write(_canonical_json_bytes(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
