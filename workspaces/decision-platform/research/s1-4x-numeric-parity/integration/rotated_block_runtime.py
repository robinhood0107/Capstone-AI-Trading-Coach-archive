#!/usr/bin/env python3
"""Gate 2의 3회전 87개 native family block runtime을 구현한다."""

from __future__ import annotations

import argparse
import ctypes
import fcntl
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from string import Formatter
from typing import Any, Protocol

BENCHMARKS_DIRECTORY = Path(__file__).resolve().parents[1] / "benchmarks"
if str(BENCHMARKS_DIRECTORY) not in sys.path:
    sys.path.append(str(BENCHMARKS_DIRECTORY))

from benchmark_contract import ContractError, sha256_file, strict_json_load
from executable_identity import (
    ExecutableIdentityError,
    inspect_executable_identity,
    inspect_regular_file_path,
)
from validate_benchmark_report import DEFAULT_PLAN, validate_block_result, validate_plan

THREAD_ENVIRONMENT = {
    "OMP_NUM_THREADS": "1",
    "OPENBLAS_NUM_THREADS": "1",
    "MKL_NUM_THREADS": "1",
    "VECLIB_MAXIMUM_THREADS": "1",
    "NUMEXPR_NUM_THREADS": "1",
    "JAX_NUM_THREADS": "1",
    "XLA_FLAGS": "--xla_cpu_multi_thread_eigen=false intra_op_parallelism_threads=1",
}
RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
BOUNDARY_IDS = (
    "python-numpy-s1-4",
    "python-numpy-s1-4r",
    "python-jax-eager-s1-4r",
    "python-jax-jit-s1-4r",
    "scala",
    "haskell",
)
HOST_CHECK_IDS = {
    "disk.home-free-bytes",
    "memory.available-bytes",
    "cpu.logical-count",
    "cpu.affinity-round-trip",
    "docker.running-containers",
    "load.normalized-load1-window",
    "process.external-cpu",
}
F_ADD_SEALS = getattr(fcntl, "F_ADD_SEALS", 1033)
F_GET_SEALS = getattr(fcntl, "F_GET_SEALS", 1034)
F_SEAL_SEAL = getattr(fcntl, "F_SEAL_SEAL", 0x0001)
F_SEAL_SHRINK = getattr(fcntl, "F_SEAL_SHRINK", 0x0002)
F_SEAL_GROW = getattr(fcntl, "F_SEAL_GROW", 0x0004)
F_SEAL_WRITE = getattr(fcntl, "F_SEAL_WRITE", 0x0008)
FROZEN_BASH_IDENTITY = {
    "path": "/usr/bin/bash",
    "sha256": "3efccc187bafa75ff1e37d246270ab3e7aa559f242c7a52bf3ec2a1b5450bdbd",
}
FROZEN_GIT_IDENTITY = {
    "path": "/usr/bin/git",
    "sha256": "5516c9f362c29376ab9a499a33082f9f611941d8c75930c880e30ad109e39c9a",
}


@dataclass(frozen=True)
class ScheduledBlock:
    """한 native process가 담당하는 회전·경계·family selector를 나타낸다."""

    rotation_id: str
    outer_repetition: int
    candidate_order: tuple[str, ...]
    python_boundary_order: tuple[str, ...]
    scheduling_group: str
    boundary_id: str
    family_id: str
    selector_id: str
    timeout_seconds: int
    expected_case_count: int


@dataclass(frozen=True)
class PinnedExecutable:
    """검증한 exact bytes를 sealed memfd에 고정한 실행 객체다."""

    binding: dict[str, str]
    descriptor: int
    required_seals: int


class WaitableProcess(Protocol):
    """process-group 종료 helper가 요구하는 최소 child-process 계약이다."""

    pid: int

    def wait(self, timeout: float | None = None) -> int: ...


def _create_memfd(name: str, flags: int) -> int:
    """CPython 노출 여부와 무관하게 Linux memfd_create를 호출한다."""

    python_memfd_create = getattr(os, "memfd_create", None)
    if callable(python_memfd_create):
        return int(python_memfd_create(name, flags))
    try:
        function: Any = ctypes.CDLL(None, use_errno=True).memfd_create
    except (AttributeError, OSError) as exc:
        raise OSError("memfd_create unavailable") from exc
    function.argtypes = (ctypes.c_char_p, ctypes.c_uint)
    function.restype = ctypes.c_int
    descriptor = int(function(name.encode("ascii", errors="strict"), flags))
    if descriptor < 0:
        error_number = ctypes.get_errno()
        raise OSError(error_number, os.strerror(error_number))
    return descriptor


def build_schedule(plan: dict[str, Any]) -> list[ScheduledBlock]:
    """R1/R2/R3마다 정확히 29개 selector를 frozen order로 펼친다."""

    selectors_by_boundary: dict[str, list[dict[str, Any]]] = {}
    for selector in plan["familySelectors"]:
        selectors_by_boundary.setdefault(selector["boundaryId"], []).append(selector)
    group_boundary = {"Scala": "scala", "Haskell": "haskell"}
    timeouts = plan["execution"]["familyBlockTimeoutSeconds"]
    schedule: list[ScheduledBlock] = []
    for index, rotation in enumerate(plan["execution"]["candidateOrderBlocks"], start=1):
        for scheduling_group in rotation["schedulingGroups"]:
            boundaries = (
                rotation["pythonBoundaries"]
                if scheduling_group == "PythonBaselines"
                else [group_boundary[scheduling_group]]
            )
            for boundary_id in boundaries:
                for selector in selectors_by_boundary[boundary_id]:
                    schedule.append(
                        ScheduledBlock(
                            rotation_id=f"R{index}",
                            outer_repetition=index,
                            candidate_order=tuple(rotation["schedulingGroups"]),
                            python_boundary_order=tuple(rotation["pythonBoundaries"]),
                            scheduling_group=scheduling_group,
                            boundary_id=boundary_id,
                            family_id=selector["familyId"],
                            selector_id=selector["selectorId"],
                            timeout_seconds=timeouts[selector["selectorId"]],
                            expected_case_count=len(selector["expectedCaseIds"]),
                        )
                    )
    per_rotation = {
        rotation_id: sum(block.rotation_id == rotation_id for block in schedule)
        for rotation_id in ("R1", "R2", "R3")
    }
    if len(schedule) != 87 or set(per_rotation.values()) != {29}:
        raise ContractError(f"INVALID_ROTATED_SCHEDULE:{len(schedule)}:{per_rotation}")
    return schedule


def reserve_directory(path: Path) -> Path:
    """기존 산출물의 증거 보존을 위해 디렉터리 재사용과 덮어쓰기를 거부한다."""

    try:
        path.mkdir(parents=True, exist_ok=False)
    except FileExistsError as exc:
        raise ContractError(f"OUTPUT_ALREADY_EXISTS:{path}") from exc
    return path


def block_directory(run_directory: Path, block: ScheduledBlock) -> Path:
    """frozen output template의 repetition/boundary/family 경로를 계산한다."""

    return (
        run_directory
        / block.rotation_id
        / block.boundary_id
        / block.family_id
    )


def _strict_json_load_bytes(
    payload: bytes,
    *,
    error_leaf: str = "INVALID_COMMAND_MANIFEST_JSON",
) -> Any:
    """한 FD에서 읽은 manifest snapshot을 duplicate/non-finite 거부로 파싱한다."""

    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ContractError(f"DUPLICATE_JSON_KEY:{key}")
            result[key] = value
        return result

    def reject_constant(token: str) -> Any:
        raise ContractError(f"NON_FINITE_JSON:{token}")

    try:
        return json.loads(
            payload.decode("utf-8", errors="strict"),
            object_pairs_hook=unique,
            parse_constant=reject_constant,
        )
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ContractError(error_leaf) from exc


def _host_command_template(executable: str) -> list[str]:
    """Runner가 허용하는 host validator의 exact argv다."""

    return [
        executable,
        "--output",
        "{host_report}",
        "--allowed-process-root-pid",
        "{allowed_process_root_pid}",
    ]


def _boundary_command_template(executable: str, boundary_id: str) -> list[str]:
    """모든 native boundary가 공유하는 exact argv 순서다."""

    return [
        executable,
        "--plan",
        "{plan}",
        "--block-dir",
        "{block_dir}",
        "--qualification",
        "{qualification}",
        "--boundary",
        boundary_id,
        "--selector",
        "{selector_id}",
        "--family",
        "{family_id}",
        "--rotation",
        "{rotation_id}",
        "--outer-repetition",
        "{outer_repetition}",
        "--run-id",
        "{run_id}",
        "--benchmark-subject-commit",
        "{benchmark_subject_commit}",
    ]


def _strict_command_manifest(
    path: Path,
    *,
    expected_sha256: str,
    benchmark_subject_commit: str,
    candidate_source_commit: str,
) -> dict[str, Any]:
    """사전 동결 digest와 commit에 묶인 shell-free command allowlist만 수락한다."""

    if SHA256_PATTERN.fullmatch(expected_sha256) is None:
        raise ContractError("INVALID_EXPECTED_COMMAND_MANIFEST_SHA256")
    try:
        snapshot = inspect_regular_file_path(path, role="commandManifest")
    except ExecutableIdentityError as exc:
        raise ContractError("COMMAND_MANIFEST_NOT_REGULAR_FILE") from exc
    if snapshot.sha256 != expected_sha256:
        raise ContractError("COMMAND_MANIFEST_SHA256_MISMATCH")
    manifest = _strict_json_load_bytes(snapshot.payload)
    if not isinstance(manifest, dict) or set(manifest) != {
        "schemaVersion",
        "benchmarkSubjectCommit",
        "candidateSourceCommit",
        "hostValidatorCommand",
        "boundaryCommands",
        "allowedExecutables",
    }:
        raise ContractError("INVALID_COMMAND_MANIFEST_FIELDS")
    if manifest["schemaVersion"] != "s1.4x-benchmark-command-manifest-v2":
        raise ContractError("INVALID_COMMAND_MANIFEST_VERSION")
    if (
        COMMIT_PATTERN.fullmatch(benchmark_subject_commit) is None
        or COMMIT_PATTERN.fullmatch(candidate_source_commit) is None
        or benchmark_subject_commit != candidate_source_commit
        or manifest["benchmarkSubjectCommit"] != benchmark_subject_commit
        or manifest["candidateSourceCommit"] != candidate_source_commit
    ):
        raise ContractError("BENCHMARK_SUBJECT_SOURCE_COMMIT_MISMATCH")
    host_command = manifest["hostValidatorCommand"]
    boundary_commands = manifest["boundaryCommands"]
    allowed_executables = manifest["allowedExecutables"]
    if (
        not isinstance(host_command, list)
        or not host_command
        or not all(isinstance(item, str) and item for item in host_command)
        or not isinstance(boundary_commands, dict)
        or set(boundary_commands) != set(BOUNDARY_IDS)
        or not isinstance(allowed_executables, dict)
        or set(allowed_executables) != {"hostValidator", "boundaries"}
        or not isinstance(allowed_executables["boundaries"], dict)
        or set(allowed_executables["boundaries"]) != set(BOUNDARY_IDS)
    ):
        raise ContractError("INVALID_COMMAND_MANIFEST_COMMANDS")
    if sum(argument.count("{host_report}") for argument in host_command) != 1:
        raise ContractError("HOST_REPORT_PLACEHOLDER_MUST_APPEAR_ONCE")
    if any("{qualification}" in argument for argument in host_command):
        raise ContractError("QUALIFICATION_PLACEHOLDER_IN_HOST_COMMAND")
    _validate_executable_identity(
        host_command,
        allowed_executables["hostValidator"],
        role="hostValidator",
    )
    if host_command != _host_command_template(host_command[0]):
        raise ContractError("HOST_COMMAND_TEMPLATE_MISMATCH")
    for boundary_id, command in boundary_commands.items():
        if (
            not isinstance(command, list)
            or not command
            or not all(isinstance(item, str) and item for item in command)
        ):
            raise ContractError("INVALID_BOUNDARY_COMMAND")
        if sum(argument.count("{qualification}") for argument in command) != 1:
            raise ContractError(
                f"QUALIFICATION_PLACEHOLDER_MUST_APPEAR_ONCE:{boundary_id}"
            )
        if any("{host_report}" in argument for argument in command):
            raise ContractError(f"HOST_REPORT_PLACEHOLDER_IN_BOUNDARY:{boundary_id}")
        _validate_executable_identity(
            command,
            allowed_executables["boundaries"][boundary_id],
            role=boundary_id,
        )
        if command != _boundary_command_template(command[0], boundary_id):
            raise ContractError(
                f"BOUNDARY_COMMAND_TEMPLATE_MISMATCH:{boundary_id}"
            )
    return manifest


def _validate_executable_identity(
    command: list[str],
    identity: Any,
    *,
    role: str,
) -> None:
    if (
        not isinstance(identity, dict)
        or set(identity) != {"path", "sha256"}
        or not isinstance(identity["path"], str)
        or not Path(identity["path"]).is_absolute()
        or SHA256_PATTERN.fullmatch(str(identity["sha256"])) is None
        or command[0] != identity["path"]
        or "{" in command[0]
        or "}" in command[0]
    ):
        raise ContractError(f"INVALID_ALLOWED_EXECUTABLE_IDENTITY:{role}")


@contextmanager
def _pin_executable(
    identity: dict[str, str],
    *,
    role: str,
) -> Iterator[PinnedExecutable]:
    """공급 경로가 바뀌어도 검증한 동일 바이트만 실행하도록 sealed memfd에 고정한다."""

    try:
        inspected = inspect_executable_identity(identity, role=role)
    except ExecutableIdentityError as exc:
        raise ContractError(str(exc)) from exc
    if inspected.payload.startswith(b"#!"):
        first_line = inspected.payload.splitlines(keepends=True)[:1]
        if first_line != [b"#!/usr/bin/bash\n"]:
            raise ContractError(f"COMMAND_SCRIPT_INTERPRETER_MISMATCH:{role}")
        try:
            inspect_executable_identity(
                FROZEN_BASH_IDENTITY,
                role=f"{role}:interpreter",
            )
        except ExecutableIdentityError as exc:
            raise ContractError(str(exc)) from exc
    # Linux uapi constants; CPython builds do not expose both names consistently.
    memfd_cloexec = getattr(os, "MFD_CLOEXEC", 0x0001)
    memfd_allow_sealing = getattr(os, "MFD_ALLOW_SEALING", 0x0002)
    required_seals = (
        F_SEAL_WRITE
        | F_SEAL_GROW
        | F_SEAL_SHRINK
        | F_SEAL_SEAL
    )
    descriptor = -1
    try:
        descriptor = _create_memfd(
            f"s1-4x-{role}",
            memfd_cloexec | memfd_allow_sealing,
        )
        view = memoryview(inspected.payload)
        written = 0
        while written < len(view):
            count = os.write(descriptor, view[written:])
            if count <= 0:
                raise OSError("short write while pinning executable")
            written += count
        os.fchmod(descriptor, 0o500)
        os.lseek(descriptor, 0, os.SEEK_SET)
        fcntl.fcntl(descriptor, F_ADD_SEALS, required_seals)
        actual_seals = fcntl.fcntl(descriptor, F_GET_SEALS)
        if actual_seals & required_seals != required_seals:
            raise ContractError(f"SEALED_EXECUTABLE_INCOMPLETE:{role}")
        yield PinnedExecutable(
            binding={
                "path": inspected.path,
                "resolvedPath": inspected.resolved_path,
                "sha256": inspected.sha256,
            },
            descriptor=descriptor,
            required_seals=required_seals,
        )
    except ContractError:
        raise
    except OSError as exc:
        raise ContractError(f"SEALED_EXECUTABLE_PIN_FAILED:{role}") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _render_command(template: list[str], values: dict[str, str]) -> list[str]:
    """shell 해석 없이 allowlisted placeholder만 argv 원소에 치환한다."""

    rendered: list[str] = []
    for argument in template:
        try:
            for _, field_name, format_spec, conversion in Formatter().parse(argument):
                if field_name is None:
                    continue
                if (
                    field_name not in values
                    or format_spec
                    or conversion is not None
                    or not field_name.isidentifier()
                ):
                    raise ContractError(
                        f"UNKNOWN_COMMAND_PLACEHOLDER:{field_name}"
                    )
            rendered.append(argument.format_map(values))
        except (KeyError, ValueError) as exc:
            raise ContractError(f"INVALID_COMMAND_TEMPLATE:{exc}") from exc
    return rendered


def _canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _verify_source_commit_binding(
    repo_root: Path,
    *,
    benchmark_subject_commit: str,
    candidate_source_commit: str,
) -> None:
    try:
        git = inspect_executable_identity(
            FROZEN_GIT_IDENTITY,
            role="sourceBindingGit",
        ).path
    except ExecutableIdentityError as exc:
        raise ContractError(str(exc)) from exc
    git_environment = {
        "HOME": os.environ.get("HOME", ""),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": "/usr/bin:/bin",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_TERMINAL_PROMPT": "0",
    }
    try:
        resolved_root = repo_root.resolve(strict=True)
        completed = subprocess.run(
            [
                git,
                "-c",
                "core.fsmonitor=false",
                "rev-parse",
                "--show-toplevel",
                "--verify",
                "HEAD",
            ],
            cwd=resolved_root,
            check=False,
            capture_output=True,
            text=True,
            env=git_environment,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ContractError("CURRENT_SOURCE_COMMIT_MISMATCH") from exc
    lines = completed.stdout.splitlines()
    if (
        completed.returncode != 0
        or len(lines) != 2
        or Path(lines[0]).resolve(strict=True) != resolved_root
        or COMMIT_PATTERN.fullmatch(lines[1]) is None
        or lines[1] != benchmark_subject_commit
        or candidate_source_commit != benchmark_subject_commit
    ):
        raise ContractError("CURRENT_SOURCE_COMMIT_MISMATCH")
    try:
        status = subprocess.run(
            [
                git,
                "-c",
                "core.fsmonitor=false",
                "-c",
                "core.quotepath=false",
                "status",
                "--porcelain=v1",
                "-z",
                "--untracked-files=all",
            ],
            cwd=resolved_root,
            check=False,
            capture_output=True,
            env=git_environment,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ContractError("CURRENT_SOURCE_WORKTREE_STATUS_FAILED") from exc
    if status.returncode != 0:
        raise ContractError("CURRENT_SOURCE_WORKTREE_STATUS_FAILED")
    if status.stdout:
        raise ContractError("CURRENT_SOURCE_WORKTREE_DIRTY")


def _benchmark_environment() -> dict[str, str]:
    """Benchmark child에 필요한 값만 전달해 ambient code/tool 주입을 제거한다."""

    home = os.environ.get("HOME")
    if not home or not Path(home).is_absolute():
        raise ContractError("BENCHMARK_HOME_INVALID")
    return {
        "HOME": home,
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": "/usr/bin:/bin",
        "TMP": "/tmp",
        "TMPDIR": "/tmp",
        "TEMP": "/tmp",
        **THREAD_ENVIRONMENT,
        "S1_4X_THREAD_COUNT": "1",
    }


def _expected_host_policy(plan: dict[str, Any], *, root_pid: int) -> dict[str, Any]:
    frozen = plan["environmentValidity"]
    return {
        "cpu_set": plan["execution"]["cpuSet"],
        "min_home_free_bytes": 32_212_254_720,
        "min_available_memory_bytes": frozen["minAvailableMemoryGiB"] * 1024**3,
        "max_normalized_load1": frozen["maxNormalizedLoad1"],
        "load_samples": frozen["loadSampleCount"],
        "sample_interval_seconds": frozen["loadSampleIntervalSeconds"],
        "max_quiet_wait_seconds": frozen["maxQuietWaitSeconds"],
        "max_running_containers": frozen["runningContainerCount"],
        "external_process_sample_seconds": 30,
        "max_external_process_cpu_percent": frozen[
            "externalProcessCpuPercentThreshold"
        ],
        "allowed_process_root_pid": root_pid,
    }


def _verify_host_validity_report(
    path: Path,
    *,
    plan: dict[str, Any],
    root_pid: int,
) -> dict[str, Any]:
    """exit code와 별개로 report bytes, frozen policy, 모든 PASS check를 검증한다."""

    try:
        snapshot = inspect_regular_file_path(path, role="hostValidity")
    except ExecutableIdentityError as exc:
        raise ContractError("HOST_VALIDITY_ARTIFACT_MISSING") from exc
    report = _strict_json_load_bytes(
        snapshot.payload,
        error_leaf="INVALID_HOST_VALIDITY_ARTIFACT",
    )
    if (
        not isinstance(report, dict)
        or set(report)
        != {
            "schemaVersion",
            "policy",
            "portableHostIdSha256",
            "metadata",
            "checks",
            "failureCount",
            "status",
        }
        or report["schemaVersion"] != "s1.4x-host-validity-v1"
        or report["status"] != "PASS"
        or report["failureCount"] != 0
        or report["policy"] != _expected_host_policy(plan, root_pid=root_pid)
        or SHA256_PATTERN.fullmatch(str(report["portableHostIdSha256"])) is None
        or not isinstance(report["metadata"], dict)
        or not {"cpuGovernor", "temperature"}.issubset(report["metadata"])
        or not isinstance(report["checks"], list)
    ):
        raise ContractError("INVALID_HOST_VALIDITY_ARTIFACT")
    check_ids: list[str] = []
    for check in report["checks"]:
        if (
            not isinstance(check, dict)
            or set(check) != {"id", "expected", "actual", "status", "evidence"}
            or not isinstance(check["id"], str)
            or check["status"] != "PASS"
        ):
            raise ContractError("INVALID_HOST_VALIDITY_CHECK")
        check_ids.append(check["id"])
    if len(check_ids) != len(set(check_ids)) or set(check_ids) != HOST_CHECK_IDS:
        raise ContractError("HOST_VALIDITY_CHECK_SET_MISMATCH")
    return {
        "artifactPath": path.name,
        "sha256": snapshot.sha256,
        "status": "PASS",
        "policySha256": _canonical_sha256(report["policy"]),
        "portableHostIdSha256": report["portableHostIdSha256"],
    }


def _selector_input_closure(
    plan: dict[str, Any],
    block: ScheduledBlock,
) -> dict[str, Any]:
    selector = next(
        (
            item
            for item in plan["familySelectors"]
            if item["selectorId"] == block.selector_id
        ),
        None,
    )
    if (
        selector is None
        or selector["boundaryId"] != block.boundary_id
        or selector["familyId"] != block.family_id
        or len(selector["expectedCaseIds"]) != block.expected_case_count
    ):
        raise ContractError("SCHEDULE_SELECTOR_CLOSURE_MISMATCH")
    case_by_id = {case["caseId"]: case for case in plan["cases"]}
    try:
        frozen_cases = [case_by_id[case_id] for case_id in selector["expectedCaseIds"]]
    except KeyError as exc:
        raise ContractError(f"SELECTOR_CASE_NOT_FROZEN:{exc}") from exc
    closure_payload = {
        "fixtureFreezeIdentity": plan["fixtureFreezeIdentity"],
        "selector": selector,
        "cases": frozen_cases,
    }
    return {
        "boundaryId": block.boundary_id,
        "familyId": block.family_id,
        "selectorId": block.selector_id,
        "expectedCaseIds": selector["expectedCaseIds"],
        "expectedCaseCount": len(frozen_cases),
        "inputClosureSha256": _canonical_sha256(closure_payload),
    }


def _qualification_document(
    *,
    plan: dict[str, Any],
    plan_sha256: str,
    command_manifest_sha256: str,
    benchmark_subject_commit: str,
    candidate_source_commit: str,
    run_id: str,
    block: ScheduledBlock,
    host_validity: dict[str, Any],
    executable: dict[str, str],
    command: list[str],
    measurement_entered: bool,
) -> dict[str, Any]:
    return {
        "schemaVersion": "s1.4x-timeout-qualification-v1",
        "phase": "MEASUREMENT" if measurement_entered else "PRE_RUN",
        "measurementEntered": measurement_entered,
        "plan": {"planId": plan["planId"], "sha256": plan_sha256},
        "subject": {
            "benchmarkSubjectCommit": benchmark_subject_commit,
            "candidateSourceCommit": candidate_source_commit,
        },
        "run": {
            "runId": run_id,
            "rotationId": block.rotation_id,
            "outerRepetition": block.outer_repetition,
            "timeoutSeconds": block.timeout_seconds,
        },
        "hostValidity": host_validity,
        "selectorInputClosure": _selector_input_closure(plan, block),
        "command": {
            "commandManifestSha256": command_manifest_sha256,
            "allowedExecutable": executable,
            "renderedArgvSha256": _canonical_sha256(command),
        },
    }


def _write_json_exclusive(path: Path, value: Any) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, allow_nan=False, indent=2, sort_keys=True)
        stream.write("\n")


def mark_measurement_entered(path: Path) -> None:
    """native wrapper가 setup 완료 후 첫 timing 직전에 호출하는 단일 전이이다."""

    if path.is_symlink() or not path.is_file():
        raise ContractError("TIMEOUT_QUALIFICATION_ARTIFACT_MISSING")
    document = strict_json_load(path)
    if (
        not isinstance(document, dict)
        or document.get("schemaVersion") != "s1.4x-timeout-qualification-v1"
        or document.get("phase") != "PRE_RUN"
        or document.get("measurementEntered") is not False
    ):
        raise ContractError("INVALID_PRE_RUN_QUALIFICATION_STATE")
    entered = dict(document)
    entered["phase"] = "MEASUREMENT"
    entered["measurementEntered"] = True
    temporary = path.with_name(f".{path.name}.{os.getpid()}.measurement.tmp")
    _write_json_exclusive(temporary, entered)
    os.replace(temporary, path)


def _verify_measurement_qualification(
    path: Path,
    *,
    expected: dict[str, Any],
) -> str:
    if path.is_symlink() or not path.is_file():
        raise ContractError("TIMEOUT_QUALIFICATION_ARTIFACT_MISSING")
    actual = strict_json_load(path)
    entered = dict(expected)
    entered["phase"] = "MEASUREMENT"
    entered["measurementEntered"] = True
    if actual != entered:
        raise ContractError("INVALID_MEASUREMENT_QUALIFICATION")
    return sha256_file(path)


def _process_group_exists(process_group_id: int) -> bool:
    """signal 0 probe에서 ESRCH만 정상 종료로 해석한다."""

    if process_group_id <= 0:
        raise ContractError("INVALID_TIMEOUT_PROCESS_GROUP")
    try:
        os.killpg(process_group_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError as exc:
        raise ContractError("TIMEOUT_PROCESS_GROUP_PROBE_DENIED") from exc
    except OSError as exc:
        raise ContractError("TIMEOUT_PROCESS_GROUP_PROBE_FAILED") from exc
    return True


def _signal_process_group(process_group_id: int, sent_signal: int) -> None:
    """이미 사라진 group은 멱등 성공으로 두고 다른 signal 오류는 숨기지 않는다."""

    try:
        os.killpg(process_group_id, sent_signal)
    except ProcessLookupError:
        return
    except PermissionError as exc:
        raise ContractError("TIMEOUT_PROCESS_GROUP_SIGNAL_DENIED") from exc
    except OSError as exc:
        raise ContractError("TIMEOUT_PROCESS_GROUP_SIGNAL_FAILED") from exc


def _wait_for_process_group_exit(
    process_group_id: int,
    timeout_seconds: float,
) -> bool:
    """leader 종료와 별개로 descendant가 모두 사라질 때까지 짧게 확인한다."""

    deadline = time.monotonic() + timeout_seconds
    while _process_group_exists(process_group_id):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(0.05, remaining))
    return True


def _wait_for_leader(
    process: WaitableProcess,
    *,
    timeout_seconds: float,
) -> bool:
    try:
        process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        return False
    except OSError as exc:
        raise ContractError("TIMEOUT_PROCESS_LEADER_WAIT_FAILED") from exc
    return True


def _terminate_process_group(process: WaitableProcess) -> None:
    """timeout 뒤 leader와 같은 session의 모든 descendant를 bounded 종료한다."""

    term_deadline = time.monotonic() + 5.0
    _signal_process_group(process.pid, signal.SIGTERM)
    leader_reaped = _wait_for_leader(
        process,
        timeout_seconds=max(0.0, term_deadline - time.monotonic()),
    )
    group_exited = _wait_for_process_group_exit(
        process.pid,
        max(0.0, term_deadline - time.monotonic()),
    )
    if leader_reaped and group_exited:
        return

    # leader와 process-group 대기를 직렬로 각각 5초씩 허용하지 않고 하나의 TERM
    # deadline을 공유하여 SIGKILL이 최초 signal 후 5초를 넘기지 않게 한다.
    kill_deadline = time.monotonic() + 5.0
    _signal_process_group(process.pid, signal.SIGKILL)
    if not leader_reaped:
        leader_reaped = _wait_for_leader(
            process,
            timeout_seconds=max(0.0, kill_deadline - time.monotonic()),
        )
    group_exited = _wait_for_process_group_exit(
        process.pid,
        max(0.0, kill_deadline - time.monotonic()),
    )
    if not group_exited:
        raise ContractError("TIMEOUT_PROCESS_GROUP_SURVIVED_SIGKILL")
    if not leader_reaped:
        raise ContractError("TIMEOUT_PROCESS_LEADER_NOT_REAPED")


def _reject_surviving_process_group(process: WaitableProcess) -> None:
    """leader가 끝난 뒤 남은 descendant를 정리하고 해당 block을 무효화한다."""

    if not _process_group_exists(process.pid):
        return
    _terminate_process_group(process)
    raise ContractError("NATIVE_PROCESS_GROUP_SURVIVED_EXIT")


def _run_process(
    command: list[str],
    *,
    executable: PinnedExecutable,
    cwd: Path,
    timeout_seconds: int,
    stdout_path: Path,
    stderr_path: Path,
    environment: dict[str, str],
) -> None:
    if command[0] != executable.binding["path"]:
        raise ContractError("PINNED_EXECUTABLE_BINDING_MISMATCH")
    try:
        actual_seals = fcntl.fcntl(executable.descriptor, F_GET_SEALS)
    except OSError as exc:
        raise ContractError("PINNED_EXECUTABLE_UNAVAILABLE") from exc
    if actual_seals & executable.required_seals != executable.required_seals:
        raise ContractError("PINNED_EXECUTABLE_SEALS_CHANGED")
    with stdout_path.open("xb") as stdout, stderr_path.open("xb") as stderr:
        try:
            process = subprocess.Popen(
                command,
                executable=f"/proc/self/fd/{executable.descriptor}",
                cwd=cwd,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                start_new_session=True,
                pass_fds=(executable.descriptor,),
            )
        except OSError as exc:
            raise ContractError("PINNED_EXECUTABLE_LAUNCH_FAILED") from exc
        deadline = time.monotonic() + timeout_seconds
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _terminate_process_group(process)
                raise ContractError("PERFORMANCE_DEADLINE_EXCEEDED")
            try:
                return_code = process.wait(timeout=min(60.0, remaining))
                break
            except subprocess.TimeoutExpired:
                # 장기 block을 중복 실행하지 않고 60초마다 생존 상태만 외부에 알린다.
                print(
                    json.dumps(
                        {
                            "event": "PROCESS_STILL_RUNNING",
                            "pid": process.pid,
                            "remainingSeconds": max(0, int(remaining)),
                        },
                        sort_keys=True,
                    ),
                    file=sys.stderr,
                    flush=True,
                )
    _reject_surviving_process_group(process)
    if return_code != 0:
        raise ContractError(f"NATIVE_PROCESS_FAILED:{return_code}")


def _pin_current_process(cpu_set: set[int]) -> None:
    """부모와 모든 자식이 동일한 단일 CPU affinity를 상속하도록 고정한다."""

    if not hasattr(os, "sched_setaffinity") or not hasattr(os, "sched_getaffinity"):
        raise ContractError("CPU_AFFINITY_UNSUPPORTED")
    os.sched_setaffinity(0, cpu_set)
    actual = os.sched_getaffinity(0)
    if actual != cpu_set:
        raise ContractError(f"CPU_AFFINITY_MISMATCH:{sorted(actual)}")


def _record_performance_timeout(
    output_directory: Path,
    *,
    plan: dict[str, Any],
    run_id: str,
    block: ScheduledBlock,
    qualification_sha256: str,
) -> None:
    """partial native JSON을 채점하지 않고 hash만 남겨 valid timeout 증거를 보존한다."""

    artifacts = []
    for path in sorted(output_directory.iterdir(), key=lambda item: item.name.encode()):
        if path.is_file() and not path.is_symlink():
            artifacts.append(
                {
                    "path": path.name,
                    "sha256": sha256_file(path),
                    "sizeBytes": path.stat().st_size,
                }
            )
    evidence = {
        "schemaVersion": "s1.4x-valid-performance-timeout-v1",
        "planId": plan["planId"],
        "runId": run_id,
        "rotationId": block.rotation_id,
        "outerRepetition": block.outer_repetition,
        "boundaryId": block.boundary_id,
        "familyId": block.family_id,
        "selectorId": block.selector_id,
        "timeoutSeconds": block.timeout_seconds,
        "measurementEntered": True,
        "timeoutQualificationSha256": qualification_sha256,
        "terminationSequence": ["SIGTERM", "bounded-grace-5s", "SIGKILL-if-needed"],
        "partialArtifactsUsedForScoring": False,
        "scoreDisposition": "candidate-family-ratio-zero",
        "continueRemainingPredeclaredMatrix": True,
        "artifacts": artifacts,
    }
    evidence_path = output_directory / "valid-performance-timeout.json"
    with evidence_path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(evidence, stream, allow_nan=False, indent=2, sort_keys=True)
        stream.write("\n")


def execute_schedule(
    *,
    plan_path: Path,
    command_manifest_path: Path,
    expected_command_manifest_sha256: str,
    benchmark_subject_commit: str,
    candidate_source_commit: str,
    output_root: Path,
    run_id: str,
    repo_root: Path,
) -> dict[str, Any]:
    """host preflight와 native block 검증을 모든 87개 block에 적용한다."""

    if RUN_ID_PATTERN.fullmatch(run_id) is None or run_id in {".", ".."}:
        raise ContractError("INVALID_RUN_ID")
    plan = validate_plan(plan_path)
    schedule = build_schedule(plan)
    manifest = _strict_command_manifest(
        command_manifest_path,
        expected_sha256=expected_command_manifest_sha256,
        benchmark_subject_commit=benchmark_subject_commit,
        candidate_source_commit=candidate_source_commit,
    )
    _verify_source_commit_binding(
        repo_root,
        benchmark_subject_commit=benchmark_subject_commit,
        candidate_source_commit=candidate_source_commit,
    )
    plan_sha256 = sha256_file(plan_path)
    # Manifest hash와 semantic parse가 같은 FD snapshot을 사용했으므로 그 digest를 재사용한다.
    command_manifest_sha256 = expected_command_manifest_sha256
    executable_stack = ExitStack()
    try:
        host_executable = executable_stack.enter_context(
            _pin_executable(
                manifest["allowedExecutables"]["hostValidator"],
                role="hostValidator",
            )
        )
        boundary_executables = {
            boundary_id: executable_stack.enter_context(
                _pin_executable(identity, role=boundary_id)
            )
            for boundary_id, identity in manifest["allowedExecutables"][
                "boundaries"
            ].items()
        }
        run_directory = reserve_directory(output_root / run_id)
        _pin_current_process(set(plan["execution"]["cpuSet"]))
        total_deadline = time.monotonic() + plan["execution"]["totalRunTimeoutSeconds"]
        valid_performance_timeouts = 0
        environment = _benchmark_environment()
        for block in schedule:
            remaining_total = total_deadline - time.monotonic()
            if remaining_total <= 0:
                raise ContractError("TOTAL_RUN_DEADLINE_EXCEEDED")
            output_directory = reserve_directory(block_directory(run_directory, block))
            host_report_path = output_directory / "host-validity.json"
            qualification_path = output_directory / "timeout-qualification.json"
            values = {
                "plan": str(plan_path.resolve()),
                "run_dir": str(run_directory.resolve()),
                "block_dir": str(output_directory.resolve()),
                "host_report": str(host_report_path.resolve()),
                "qualification": str(qualification_path.resolve()),
                "allowed_process_root_pid": str(os.getpid()),
                "benchmark_subject_commit": benchmark_subject_commit,
                "candidate_source_commit": candidate_source_commit,
                "boundary_id": block.boundary_id,
                "selector_id": block.selector_id,
                "family_id": block.family_id,
                "rotation_id": block.rotation_id,
                "outer_repetition": str(block.outer_repetition),
                "run_id": run_id,
            }
            host_command = _render_command(manifest["hostValidatorCommand"], values)
            if host_command[0] != host_executable.binding["path"]:
                raise ContractError("HOST_EXECUTABLE_BINDING_MISMATCH")
            try:
                _run_process(
                    host_command,
                    executable=host_executable,
                    cwd=repo_root,
                    timeout_seconds=min(
                        plan["environmentValidity"]["maxQuietWaitSeconds"],
                        max(1, int(remaining_total)),
                    ),
                    stdout_path=output_directory / "host-validator.stdout",
                    stderr_path=output_directory / "host-validator.stderr",
                    environment=environment,
                )
            except ContractError as exc:
                if str(exc) == "PERFORMANCE_DEADLINE_EXCEEDED":
                    raise ContractError("HOST_PREFLIGHT_DEADLINE_EXCEEDED") from exc
                raise
            host_validity = _verify_host_validity_report(
                host_report_path,
                plan=plan,
                root_pid=os.getpid(),
            )
            remaining_total = total_deadline - time.monotonic()
            if remaining_total < block.timeout_seconds:
                raise ContractError("TOTAL_RUN_DEADLINE_INSUFFICIENT_FOR_FROZEN_BLOCK")
            _verify_source_commit_binding(
                repo_root,
                benchmark_subject_commit=benchmark_subject_commit,
                candidate_source_commit=candidate_source_commit,
            )
            native_command = _render_command(
                manifest["boundaryCommands"][block.boundary_id],
                values,
            )
            native_executable = boundary_executables[block.boundary_id]
            if native_command[0] != native_executable.binding["path"]:
                raise ContractError("NATIVE_EXECUTABLE_BINDING_MISMATCH")
            qualification = _qualification_document(
                plan=plan,
                plan_sha256=plan_sha256,
                command_manifest_sha256=command_manifest_sha256,
                benchmark_subject_commit=benchmark_subject_commit,
                candidate_source_commit=candidate_source_commit,
                run_id=run_id,
                block=block,
                host_validity=host_validity,
                executable=native_executable.binding,
                command=native_command,
                measurement_entered=False,
            )
            _write_json_exclusive(qualification_path, qualification)
            try:
                _run_process(
                    native_command,
                    executable=native_executable,
                    cwd=repo_root,
                    timeout_seconds=block.timeout_seconds,
                    stdout_path=output_directory / "native-wrapper.stdout",
                    stderr_path=output_directory / "native-wrapper.stderr",
                    environment=environment,
                )
            except ContractError as exc:
                _verify_source_commit_binding(
                    repo_root,
                    benchmark_subject_commit=benchmark_subject_commit,
                    candidate_source_commit=candidate_source_commit,
                )
                if str(exc) != "PERFORMANCE_DEADLINE_EXCEEDED":
                    raise
                qualification_sha256 = _verify_measurement_qualification(
                    qualification_path,
                    expected=qualification,
                )
                _record_performance_timeout(
                    output_directory,
                    plan=plan,
                    run_id=run_id,
                    block=block,
                    qualification_sha256=qualification_sha256,
                )
                valid_performance_timeouts += 1
                continue
            _verify_source_commit_binding(
                repo_root,
                benchmark_subject_commit=benchmark_subject_commit,
                candidate_source_commit=candidate_source_commit,
            )
            _verify_measurement_qualification(
                qualification_path,
                expected=qualification,
            )
            native_path = output_directory / "native.json"
            result_path = output_directory / "block-result.json"
            if not native_path.is_file() or not result_path.is_file():
                raise ContractError(f"NATIVE_OUTPUT_MISSING:{block.selector_id}")
            validate_block_result(
                result_path,
                plan_path=plan_path,
                native_report_path=native_path,
                expected_boundary_id=block.boundary_id,
                expected_selector_id=block.selector_id,
            )
        return {
            "schemaVersion": "s1.4x-rotated-run-summary-v1",
            "status": (
                "PASS"
                if valid_performance_timeouts == 0
                else "PASS_WITH_VALID_PERFORMANCE_TIMEOUTS"
            ),
            "scheduledBlockCount": len(schedule),
            "validPerformanceTimeoutCount": valid_performance_timeouts,
        }
    finally:
        executable_stack.close()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate_parser = subparsers.add_parser("validate-plan")
    validate_parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    schedule_parser = subparsers.add_parser("print-schedule")
    schedule_parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    schedule_parser.add_argument("--skip-file-digests", action="store_true")
    mark_parser = subparsers.add_parser("mark-measurement-entered")
    mark_parser.add_argument("--qualification", type=Path, required=True)
    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    run_parser.add_argument("--commands", type=Path, required=True)
    run_parser.add_argument("--commands-sha256", required=True)
    run_parser.add_argument("--benchmark-subject-commit", required=True)
    run_parser.add_argument("--candidate-source-commit", required=True)
    run_parser.add_argument("--output-root", type=Path, required=True)
    run_parser.add_argument("--run-id", required=True)
    run_parser.add_argument("--repo-root", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "validate-plan":
            plan = validate_plan(args.plan)
            print(
                json.dumps(
                    {"status": "PASS", "caseCount": len(plan["cases"]), "blockCount": 87},
                    sort_keys=True,
                )
            )
        elif args.command == "print-schedule":
            plan = validate_plan(args.plan, verify_files=not args.skip_file_digests)
            print(
                json.dumps(
                    [asdict(block) for block in build_schedule(plan)],
                    indent=2,
                    sort_keys=True,
                )
            )
        elif args.command == "mark-measurement-entered":
            mark_measurement_entered(args.qualification)
            print(json.dumps({"status": "MEASUREMENT_ENTERED"}, sort_keys=True))
        else:
            summary = execute_schedule(
                plan_path=args.plan,
                command_manifest_path=args.commands,
                expected_command_manifest_sha256=args.commands_sha256,
                benchmark_subject_commit=args.benchmark_subject_commit,
                candidate_source_commit=args.candidate_source_commit,
                output_root=args.output_root,
                run_id=args.run_id,
                repo_root=args.repo_root,
            )
            print(json.dumps(summary, sort_keys=True))
    except (ContractError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
