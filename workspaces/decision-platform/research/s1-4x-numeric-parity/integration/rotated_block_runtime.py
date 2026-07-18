#!/usr/bin/env python3
"""Gate 2의 3회전 87개 native family block runtime을 구현한다."""

from __future__ import annotations

import argparse
import ctypes
import errno
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

from benchmark_contract import ContractError, sha256_file, strict_json_load  # noqa: E402
from executable_identity import (  # noqa: E402
    ExecutableIdentityError,
    inspect_executable_identity,
    inspect_regular_file_path,
)
from validate_benchmark_report import (  # noqa: E402
    DEFAULT_PLAN,
    validate_block_result,
    validate_plan,
)

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
RUNTIME_DEPENDENCY_ROLES_BY_BOUNDARY = {
    "hostValidator": ("uv",),
    "python-numpy-s1-4": ("uv", "benchmarkPython"),
    "python-numpy-s1-4r": ("uv", "benchmarkPython"),
    "python-jax-eager-s1-4r": ("uv", "benchmarkPython"),
    "python-jax-jit-s1-4r": ("uv", "benchmarkPython"),
    "scala": (
        "benchmarkPython",
        "scalaCli",
        "java",
        "scalafix",
        "scalafmt",
    ),
    "haskell": (
        "benchmarkPython",
        "ghcup",
        "stack",
        "authoritativeGhc",
        "compatibilityGhc",
        "hlint",
        "stylishHaskell",
    ),
}
RUNTIME_EVIDENCE_ROLES_BY_BOUNDARY = {
    "hostValidator": (),
    "python-numpy-s1-4": (),
    "python-numpy-s1-4r": (),
    "python-jax-eager-s1-4r": (),
    "python-jax-jit-s1-4r": (),
    "scala": (
        "scalafmtArchive",
        "selectedProfileResult",
        "profileQualificationResult",
        "jvmAllowlistResult",
        "correctnessA",
        "correctnessB",
        "correctnessC",
    ),
    "haskell": (
        "baselineCorrectness",
        "optimizedCorrectness",
        "profileQualification",
    ),
}
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
PR_SET_CHILD_SUBREAPER = 36
PR_GET_CHILD_SUBREAPER = 37
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


@dataclass(frozen=True, order=True)
class ProcessIdentity:
    """PID 재사용과 기존 외부 process를 구분하는 Linux process identity다."""

    pid: int
    start_time_ticks: int


@dataclass(frozen=True)
class ProcessRecord:
    """한 번의 /proc snapshot에서 읽은 process 계보와 상태다."""

    identity: ProcessIdentity
    parent_pid: int
    state: str


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
        "allowedEvidenceByBoundary",
    }:
        raise ContractError("INVALID_COMMAND_MANIFEST_FIELDS")
    if manifest["schemaVersion"] != "s1.4x-benchmark-command-manifest-v3":
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
    allowed_evidence = manifest["allowedEvidenceByBoundary"]
    if (
        not isinstance(host_command, list)
        or not host_command
        or not all(isinstance(item, str) and item for item in host_command)
        or not isinstance(boundary_commands, dict)
        or set(boundary_commands) != set(BOUNDARY_IDS)
        or not isinstance(allowed_executables, dict)
        or set(allowed_executables)
        != {
            "hostValidator",
            "boundaries",
            "runtimeDependenciesByBoundary",
        }
        or not isinstance(allowed_executables["boundaries"], dict)
        or set(allowed_executables["boundaries"]) != set(BOUNDARY_IDS)
        or not isinstance(
            allowed_executables["runtimeDependenciesByBoundary"],
            dict,
        )
        or set(allowed_executables["runtimeDependenciesByBoundary"])
        != set(RUNTIME_DEPENDENCY_ROLES_BY_BOUNDARY)
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
    for boundary_id, expected_roles in (
        RUNTIME_DEPENDENCY_ROLES_BY_BOUNDARY.items()
    ):
        dependencies = allowed_executables[
            "runtimeDependenciesByBoundary"
        ][boundary_id]
        if (
            not isinstance(dependencies, dict)
            or tuple(dependencies) != expected_roles
        ):
            raise ContractError("INVALID_COMMAND_MANIFEST_COMMANDS")
        for role, identity in dependencies.items():
            identity_path = (
                identity.get("path") if isinstance(identity, dict) else ""
            )
            _validate_executable_identity(
                [identity_path]
                if isinstance(identity_path, str)
                else [""],
                identity,
                role=f"runtimeDependency:{boundary_id}:{role}",
            )
    if (
        not isinstance(allowed_evidence, dict)
        or set(allowed_evidence) != set(RUNTIME_EVIDENCE_ROLES_BY_BOUNDARY)
    ):
        raise ContractError("INVALID_COMMAND_MANIFEST_EVIDENCE")
    for boundary_id, expected_roles in (
        RUNTIME_EVIDENCE_ROLES_BY_BOUNDARY.items()
    ):
        evidence = allowed_evidence[boundary_id]
        if (
            not isinstance(evidence, dict)
            or tuple(evidence) != expected_roles
        ):
            raise ContractError("INVALID_COMMAND_MANIFEST_EVIDENCE")
        for role, identity in evidence.items():
            _validate_evidence_identity(
                identity,
                role=f"runtimeEvidence:{boundary_id}:{role}",
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


def _validate_evidence_identity(identity: Any, *, role: str) -> None:
    if (
        not isinstance(identity, dict)
        or set(identity) != {"path", "sha256"}
        or not isinstance(identity["path"], str)
        or not Path(identity["path"]).is_absolute()
        or SHA256_PATTERN.fullmatch(str(identity["sha256"])) is None
        or "{" in identity["path"]
        or "}" in identity["path"]
    ):
        raise ContractError(f"INVALID_ALLOWED_EVIDENCE_IDENTITY:{role}")


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


@contextmanager
def _pin_evidence(
    identity: dict[str, str],
    *,
    role: str,
) -> Iterator[PinnedExecutable]:
    """검증한 regular evidence bytes를 child가 재개방하지 않도록 sealed memfd에 둔다."""

    try:
        inspected = inspect_regular_file_path(
            Path(identity["path"]),
            role=role,
        )
    except (ExecutableIdentityError, KeyError, TypeError) as exc:
        raise ContractError(f"RUNTIME_EVIDENCE_INVALID:{role}") from exc
    if (
        inspected.path != identity["path"]
        or inspected.sha256 != identity["sha256"]
    ):
        raise ContractError(f"RUNTIME_EVIDENCE_IDENTITY_MISMATCH:{role}")
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
            f"s1-4x-evidence-{role}",
            memfd_cloexec | memfd_allow_sealing,
        )
        view = memoryview(inspected.payload)
        written = 0
        while written < len(view):
            count = os.write(descriptor, view[written:])
            if count <= 0:
                raise OSError("short write while pinning evidence")
            written += count
        os.fchmod(descriptor, 0o400)
        os.lseek(descriptor, 0, os.SEEK_SET)
        fcntl.fcntl(descriptor, F_ADD_SEALS, required_seals)
        if (
            fcntl.fcntl(descriptor, F_GET_SEALS) & required_seals
            != required_seals
        ):
            raise ContractError(f"SEALED_EVIDENCE_INCOMPLETE:{role}")
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
        raise ContractError(f"SEALED_EVIDENCE_PIN_FAILED:{role}") from exc
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


def _benchmark_environment(
    runtime_dependencies: dict[str, PinnedExecutable],
    runtime_evidence: dict[str, PinnedExecutable],
    *,
    boundary_id: str,
) -> dict[str, str]:
    """해당 boundary에 선언된 sealed runtime/evidence만 child에 전달한다."""

    home = os.environ.get("HOME")
    if not home or not Path(home).is_absolute():
        raise ContractError("BENCHMARK_HOME_INVALID")
    expected_dependencies = RUNTIME_DEPENDENCY_ROLES_BY_BOUNDARY.get(
        boundary_id
    )
    expected_evidence = RUNTIME_EVIDENCE_ROLES_BY_BOUNDARY.get(boundary_id)
    if (
        expected_dependencies is None
        or expected_evidence is None
        or tuple(runtime_dependencies) != expected_dependencies
    ):
        raise ContractError("BENCHMARK_RUNTIME_DEPENDENCY_SET_MISMATCH")
    if tuple(runtime_evidence) != expected_evidence:
        raise ContractError("BENCHMARK_RUNTIME_EVIDENCE_SET_MISMATCH")
    for role, pinned in (*runtime_dependencies.items(), *runtime_evidence.items()):
        if (
            pinned.descriptor < 0
            or SHA256_PATTERN.fullmatch(
                str(pinned.binding.get("sha256"))
            )
            is None
            or not isinstance(pinned.binding.get("path"), str)
            or not Path(pinned.binding["path"]).is_absolute()
        ):
            raise ContractError(f"BENCHMARK_RUNTIME_INPUT_INVALID:{role}")
    cache_root = Path(home) / ".cache/s1-4x"
    cache_directories = {
        "TMP": cache_root / "tmp",
        "UV_CACHE_DIR": cache_root / "uv",
        "COURSIER_CACHE": cache_root / "coursier",
    }
    for path in (cache_root, *cache_directories.values()):
        try:
            path.mkdir(mode=0o700, parents=True, exist_ok=True)
            resolved = path.resolve(strict=True)
        except OSError as exc:
            raise ContractError("BENCHMARK_CACHE_ROOT_INVALID") from exc
        if path.is_symlink() or resolved != path or not path.is_dir():
            raise ContractError("BENCHMARK_CACHE_ROOT_INVALID")
    environment = {
        "HOME": home,
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": "/usr/bin:/bin",
        "TMP": str(cache_directories["TMP"]),
        "TMPDIR": str(cache_directories["TMP"]),
        "TEMP": str(cache_directories["TMP"]),
        "UV_CACHE_DIR": str(cache_directories["UV_CACHE_DIR"]),
        "COURSIER_CACHE": str(cache_directories["COURSIER_CACHE"]),
        **THREAD_ENVIRONMENT,
        "S1_4X_THREAD_COUNT": "1",
    }
    proc_path = {
        role: f"/proc/self/fd/{pinned.descriptor}"
        for role, pinned in runtime_dependencies.items()
    }
    if "uv" in runtime_dependencies:
        environment.update(
            {
                "S1_4X_UV_BIN": proc_path["uv"],
                "S1_4X_UV_SHA256": runtime_dependencies["uv"].binding[
                    "sha256"
                ],
            }
        )
    if "benchmarkPython" in runtime_dependencies:
        pinned = runtime_dependencies["benchmarkPython"]
        environment.update(
            {
                "S1_4X_BENCHMARK_PYTHON_BIN": pinned.binding["path"],
                "S1_4X_BENCHMARK_PYTHON_SHA256": pinned.binding[
                    "sha256"
                ],
                "S1_4X_BENCHMARK_PYTHON_PINNED_FD_PATH": proc_path[
                    "benchmarkPython"
                ],
            }
        )
    if boundary_id == "scala":
        role_environment = {
            "scalaCli": "S1_4X_SCALA_CLI",
            "java": "S1_4X_SCALA_JAVA",
            "scalafix": "S1_4X_SCALAFIX",
            "scalafmt": "S1_4X_SCALAFMT",
        }
        for role, prefix in role_environment.items():
            pinned = runtime_dependencies[role]
            environment[f"{prefix}_BIN"] = pinned.binding["path"]
            environment[f"{prefix}_SHA256"] = pinned.binding["sha256"]
            environment[f"{prefix}_PINNED_FD_PATH"] = proc_path[role]
        java_path = Path(runtime_dependencies["java"].binding["path"])
        if java_path.name != "java" or java_path.parent.name != "bin":
            raise ContractError("BENCHMARK_JAVA_LAYOUT_INVALID")
        environment["JAVA_HOME"] = str(java_path.parent.parent)
        scala_evidence_environment = {
            "scalafmtArchive": "S1_4X_SCALAFMT_ARCHIVE",
            "selectedProfileResult": (
                "S1_4X_SCALA_SELECTED_PROFILE_RESULT"
            ),
            "profileQualificationResult": (
                "S1_4X_SCALA_QUALIFICATION_RESULT"
            ),
            "jvmAllowlistResult": "S1_4X_SCALA_JVM_ALLOWLIST_RESULT",
        }
        for role, name in scala_evidence_environment.items():
            pinned = runtime_evidence[role]
            environment[name] = pinned.binding["path"]
            environment[f"{name}_SHA256"] = pinned.binding["sha256"]
            environment[f"{name}_PINNED_FD_PATH"] = (
                f"/proc/self/fd/{pinned.descriptor}"
            )
        correctness_paths = {
            profile: Path(
                runtime_evidence[f"correctness{profile}"].binding["path"]
            )
            for profile in ("A", "B", "C")
        }
        roots = {
            path.parent.parent
            for path in correctness_paths.values()
        }
        if (
            len(roots) != 1
            or any(
                path
                != next(iter(roots))
                / profile
                / "scala-profile-correctness-result.v1.json"
                for profile, path in correctness_paths.items()
            )
        ):
            raise ContractError("BENCHMARK_SCALA_CORRECTNESS_LAYOUT_INVALID")
        environment["S1_4X_SCALA_CORRECTNESS_ROOT"] = str(
            next(iter(roots))
        )
        environment["S1_4X_CACHE_ROOT"] = str(cache_root)
    elif boundary_id == "haskell":
        role_environment = {
            "ghcup": "S1_4X_GHCUP",
            "stack": "S1_4X_STACK",
            "authoritativeGhc": "S1_4X_AUTHORITATIVE_GHC",
            "compatibilityGhc": "S1_4X_LATEST_GHC",
            "hlint": "S1_4X_HLINT",
            "stylishHaskell": "S1_4X_STYLISH",
        }
        for role, prefix in role_environment.items():
            pinned = runtime_dependencies[role]
            environment[f"{prefix}_BIN"] = pinned.binding["path"]
            environment[f"{prefix}_SHA256"] = pinned.binding["sha256"]
            environment[f"{prefix}_PINNED_FD_PATH"] = proc_path[role]
        authoritative = Path(
            runtime_dependencies["authoritativeGhc"].binding["path"]
        )
        ghcup_ancestors = [
            parent
            for parent in authoritative.parents
            if parent.name == ".ghcup"
        ]
        if len(ghcup_ancestors) != 1:
            raise ContractError("BENCHMARK_GHCUP_PREFIX_INVALID")
        environment["GHCUP_INSTALL_BASE_PREFIX"] = str(
            ghcup_ancestors[0].parent
        )
        haskell_evidence_environment = {
            "baselineCorrectness": (
                "S1_4X_HASKELL_BASELINE_CORRECTNESS"
            ),
            "optimizedCorrectness": (
                "S1_4X_HASKELL_OPTIMIZED_CORRECTNESS"
            ),
            "profileQualification": (
                "S1_4X_HASKELL_QUALIFICATION_ARTIFACT"
            ),
        }
        for role, name in haskell_evidence_environment.items():
            pinned = runtime_evidence[role]
            environment[name] = f"/proc/self/fd/{pinned.descriptor}"
            environment[f"{name}_SHA256"] = pinned.binding["sha256"]
            environment[f"{name}_SOURCE_PATH"] = pinned.binding["path"]
        environment["S1_4X_CACHE_ROOT"] = str(cache_root)
    return environment


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


def _read_process_record(pid: int) -> ProcessRecord | None:
    """동일 stat read에서 PPID와 starttime을 얻어 PID 재사용을 구분한다."""

    if pid <= 0:
        raise ContractError("INVALID_DESCENDANT_PROCESS_ID")
    try:
        payload = Path(f"/proc/{pid}/stat").read_bytes()
    except (FileNotFoundError, ProcessLookupError):
        return None
    except PermissionError as exc:
        raise ContractError("DESCENDANT_PROC_STAT_DENIED") from exc
    except OSError as exc:
        raise ContractError("DESCENDANT_PROC_STAT_FAILED") from exc
    if len(payload) > 1_048_576:
        raise ContractError("INVALID_DESCENDANT_PROC_STAT")
    try:
        document = payload.decode("ascii", errors="strict")
    except UnicodeError as exc:
        raise ContractError("INVALID_DESCENDANT_PROC_STAT") from exc
    closing_parenthesis = document.rfind(")")
    if (
        not document.startswith(f"{pid} (")
        or closing_parenthesis < len(str(pid)) + 2
    ):
        raise ContractError("INVALID_DESCENDANT_PROC_STAT")
    fields = document[closing_parenthesis + 2 :].split()
    if len(fields) < 20 or len(fields[0]) != 1:
        raise ContractError("INVALID_DESCENDANT_PROC_STAT")
    try:
        parent_pid = int(fields[1])
        start_time_ticks = int(fields[19])
    except ValueError as exc:
        raise ContractError("INVALID_DESCENDANT_PROC_STAT") from exc
    if parent_pid < 0 or start_time_ticks <= 0:
        raise ContractError("INVALID_DESCENDANT_PROC_STAT")
    return ProcessRecord(
        identity=ProcessIdentity(pid=pid, start_time_ticks=start_time_ticks),
        parent_pid=parent_pid,
        state=fields[0],
    )


def _read_launch_leader_record(pid: int) -> ProcessRecord | None:
    """launch 직후 leader identity read를 테스트 가능한 단일 경계로 둔다."""

    return _read_process_record(pid)


def _process_snapshot() -> dict[int, ProcessRecord]:
    """현재 PID namespace를 한 번 훑어 추적 대상의 parent chain을 복원한다."""

    snapshot: dict[int, ProcessRecord] = {}
    try:
        with os.scandir("/proc") as directory:
            entries = tuple(directory)
    except (FileNotFoundError, PermissionError, OSError) as exc:
        raise ContractError("DESCENDANT_PROC_SCAN_UNAVAILABLE") from exc
    for entry in entries:
        if not entry.name.isascii() or not entry.name.isdecimal():
            continue
        record = _read_process_record(int(entry.name))
        if record is not None:
            snapshot[record.identity.pid] = record
    return snapshot


def _child_subreaper_state() -> bool:
    state = ctypes.c_int(0)
    try:
        function: Any = ctypes.CDLL(None, use_errno=True).prctl
    except (AttributeError, OSError) as exc:
        raise ContractError("CHILD_SUBREAPER_UNAVAILABLE") from exc
    function.restype = ctypes.c_int
    result = int(
        function(
            ctypes.c_int(PR_GET_CHILD_SUBREAPER),
            ctypes.byref(state),
            ctypes.c_ulong(0),
            ctypes.c_ulong(0),
            ctypes.c_ulong(0),
        )
    )
    if result != 0:
        error_number = ctypes.get_errno()
        raise ContractError(
            f"CHILD_SUBREAPER_QUERY_FAILED:{error_number}"
        )
    return state.value == 1


def _set_child_subreaper(enabled: bool) -> None:
    try:
        function: Any = ctypes.CDLL(None, use_errno=True).prctl
    except (AttributeError, OSError) as exc:
        raise ContractError("CHILD_SUBREAPER_UNAVAILABLE") from exc
    function.restype = ctypes.c_int
    result = int(
        function(
            ctypes.c_int(PR_SET_CHILD_SUBREAPER),
            ctypes.c_ulong(1 if enabled else 0),
            ctypes.c_ulong(0),
            ctypes.c_ulong(0),
            ctypes.c_ulong(0),
        )
    )
    if result != 0:
        error_number = ctypes.get_errno()
        raise ContractError(
            f"CHILD_SUBREAPER_CONFIGURATION_FAILED:{error_number}"
        )


@contextmanager
def _child_subreaper_scope() -> Iterator[None]:
    """setsid/double-fork 자식이 PID 1로 빠지기 전에 현재 runner로 재부모화한다."""

    if sys.platform != "linux" or not Path("/proc/self/stat").is_file():
        raise ContractError("CHILD_SUBREAPER_UNAVAILABLE")
    previously_enabled = _child_subreaper_state()
    changed = False
    try:
        if not previously_enabled:
            _set_child_subreaper(True)
            changed = True
            if not _child_subreaper_state():
                raise ContractError("CHILD_SUBREAPER_CONFIGURATION_FAILED")
        yield
    finally:
        if changed:
            _set_child_subreaper(False)
            if _child_subreaper_state():
                raise ContractError("CHILD_SUBREAPER_RESTORE_FAILED")


class DescendantTracker:
    """leader 계보와 subreaper로 돌아온 새 자식을 exact PID/starttime으로 추적한다."""

    def __init__(
        self,
        *,
        root: ProcessIdentity,
        leader: ProcessIdentity | None,
        baseline_direct_children: frozenset[ProcessIdentity],
        baseline_processes: frozenset[ProcessIdentity],
        minimum_start_time_ticks: int,
    ) -> None:
        self.root = root
        self.leader = leader
        self.baseline_direct_children = baseline_direct_children
        self.baseline_processes = baseline_processes
        self.minimum_start_time_ticks = minimum_start_time_ticks
        self.tracked: set[ProcessIdentity] = (
            set() if leader is None else {leader}
        )

    def _refresh(self) -> dict[int, ProcessRecord]:
        snapshot = _process_snapshot()
        root_record = snapshot.get(self.root.pid)
        if root_record is None or root_record.identity != self.root:
            raise ContractError("DESCENDANT_TRACKER_ROOT_IDENTITY_CHANGED")

        # subreaper 전환 뒤 현재 runner의 새 direct child가 된 orphan도 원래
        # PGID/session과 무관하게 이 실행에서 만들어진 descendant로 취급한다.
        for record in snapshot.values():
            if (
                record.parent_pid == self.root.pid
                and record.identity not in self.baseline_direct_children
                and record.identity not in self.baseline_processes
                and record.identity.start_time_ticks >= self.minimum_start_time_ticks
            ):
                self.tracked.add(record.identity)

        while True:
            live_parent_pids: set[int] = set()
            for identity in self.tracked:
                current_record = snapshot.get(identity.pid)
                if (
                    current_record is not None
                    and current_record.identity == identity
                ):
                    live_parent_pids.add(identity.pid)
            discovered = {
                record.identity
                for record in snapshot.values()
                if record.parent_pid in live_parent_pids
            }
            new_identities = discovered - self.tracked
            if not new_identities:
                break
            self.tracked.update(new_identities)
        return snapshot

    def descendant_identities(self) -> tuple[ProcessIdentity, ...]:
        snapshot = self._refresh()
        descendants: list[ProcessIdentity] = []
        for identity in self.tracked:
            current_record = snapshot.get(identity.pid)
            if (
                (self.leader is None or identity != self.leader)
                and current_record is not None
                and current_record.identity == identity
            ):
                descendants.append(identity)
        return tuple(sorted(descendants))

    def reap_adopted_children(self) -> None:
        """subreaper의 direct zombie만 exact PID로 회수해 다른 child를 건드리지 않는다."""

        snapshot = self._refresh()
        for identity in sorted(self.tracked):
            if self.leader is not None and identity == self.leader:
                continue
            record = snapshot.get(identity.pid)
            if (
                record is None
                or record.identity != identity
                or record.parent_pid != self.root.pid
                or record.state != "Z"
            ):
                continue
            confirmed = _read_process_record(identity.pid)
            if (
                confirmed is None
                or confirmed.identity != identity
                or confirmed.parent_pid != self.root.pid
                or confirmed.state != "Z"
            ):
                continue
            try:
                os.waitpid(identity.pid, os.WNOHANG)
            except ChildProcessError:
                continue
            except OSError as exc:
                raise ContractError("DESCENDANT_PROCESS_REAP_FAILED") from exc


def _signal_process_identity(identity: ProcessIdentity, sent_signal: int) -> None:
    """starttime 재검증과 pidfd를 결합해 재사용된 PID에는 signal을 보내지 않는다."""

    current = _read_process_record(identity.pid)
    if current is None or current.identity != identity:
        return
    pidfd_open = getattr(os, "pidfd_open", None)
    pidfd_send_signal = getattr(signal, "pidfd_send_signal", None)
    descriptor = -1
    try:
        if callable(pidfd_open):
            descriptor = int(pidfd_open(identity.pid, 0))
        else:
            try:
                libc_pidfd_open: Any = ctypes.CDLL(
                    None,
                    use_errno=True,
                ).pidfd_open
            except (AttributeError, OSError) as exc:
                raise ContractError("DESCENDANT_PIDFD_UNAVAILABLE") from exc
            libc_pidfd_open.argtypes = (ctypes.c_int, ctypes.c_uint)
            libc_pidfd_open.restype = ctypes.c_int
            descriptor = int(libc_pidfd_open(identity.pid, 0))
            if descriptor < 0:
                error_number = ctypes.get_errno()
                if error_number == errno.ESRCH:
                    return
                raise OSError(error_number, os.strerror(error_number))
        confirmed = _read_process_record(identity.pid)
        if confirmed is None or confirmed.identity != identity:
            return
        if callable(pidfd_send_signal):
            pidfd_send_signal(descriptor, sent_signal, None, 0)
        else:
            try:
                libc_pidfd_send_signal: Any = ctypes.CDLL(
                    None,
                    use_errno=True,
                ).pidfd_send_signal
            except (AttributeError, OSError) as exc:
                raise ContractError("DESCENDANT_PIDFD_UNAVAILABLE") from exc
            libc_pidfd_send_signal.argtypes = (
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_void_p,
                ctypes.c_uint,
            )
            libc_pidfd_send_signal.restype = ctypes.c_int
            result = int(
                libc_pidfd_send_signal(
                    descriptor,
                    sent_signal,
                    None,
                    0,
                )
            )
            if result != 0:
                error_number = ctypes.get_errno()
                if error_number == errno.ESRCH:
                    return
                raise OSError(error_number, os.strerror(error_number))
    except ProcessLookupError:
        return
    except PermissionError as exc:
        raise ContractError("DESCENDANT_PROCESS_SIGNAL_DENIED") from exc
    except OSError as exc:
        raise ContractError("DESCENDANT_PROCESS_SIGNAL_FAILED") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _signal_descendants(
    tracker: DescendantTracker,
    sent_signal: int,
) -> tuple[ProcessIdentity, ...]:
    identities = tracker.descendant_identities()
    for identity in identities:
        _signal_process_identity(identity, sent_signal)
    return identities


def _wait_for_descendants_exit(
    tracker: DescendantTracker,
    *,
    deadline: float,
    sent_signal: int,
) -> bool:
    """deadline 동안 새로 fork/reparent된 대상까지 같은 signal로 반복 봉쇄한다."""

    while True:
        tracker.reap_adopted_children()
        _signal_descendants(tracker, sent_signal)
        tracker.reap_adopted_children()
        if not tracker.descendant_identities():
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(0.05, remaining))


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


def _terminate_process_group(
    process: WaitableProcess,
    tracker: DescendantTracker | None = None,
) -> None:
    """timeout 뒤 leader와 같은 session의 모든 descendant를 bounded 종료한다."""

    term_deadline = time.monotonic() + 5.0
    _signal_process_group(process.pid, signal.SIGTERM)
    if tracker is not None:
        _signal_descendants(tracker, signal.SIGTERM)
    leader_reaped = _wait_for_leader(
        process,
        timeout_seconds=max(0.0, term_deadline - time.monotonic()),
    )
    group_exited = (
        _wait_for_process_group_exit(
            process.pid,
            max(0.0, term_deadline - time.monotonic()),
        )
        if tracker is None
        else True
    )
    descendants_exited = (
        True
        if tracker is None
        else _wait_for_descendants_exit(
            tracker,
            deadline=term_deadline,
            sent_signal=signal.SIGTERM,
        )
    )
    if leader_reaped and group_exited and descendants_exited:
        return

    # leader와 process-group 대기를 직렬로 각각 5초씩 허용하지 않고 하나의 TERM
    # deadline을 공유하여 SIGKILL이 최초 signal 후 5초를 넘기지 않게 한다.
    kill_deadline = time.monotonic() + 5.0
    if tracker is None or not leader_reaped:
        # leader가 이미 reaped된 뒤에는 같은 숫자의 PID/PGID가 재사용될 수 있으므로
        # exact starttime tracker만 사용한다.
        _signal_process_group(process.pid, signal.SIGKILL)
    if tracker is not None:
        _signal_descendants(tracker, signal.SIGKILL)
    if not leader_reaped:
        leader_reaped = _wait_for_leader(
            process,
            timeout_seconds=max(0.0, kill_deadline - time.monotonic()),
        )
    group_exited = (
        _wait_for_process_group_exit(
            process.pid,
            max(0.0, kill_deadline - time.monotonic()),
        )
        if tracker is None
        else True
    )
    descendants_exited = (
        True
        if tracker is None
        else _wait_for_descendants_exit(
            tracker,
            deadline=kill_deadline,
            sent_signal=signal.SIGKILL,
        )
    )
    if not group_exited:
        raise ContractError("TIMEOUT_PROCESS_GROUP_SURVIVED_SIGKILL")
    if not descendants_exited:
        raise ContractError("TIMEOUT_DESCENDANTS_SURVIVED_SIGKILL")
    if not leader_reaped:
        raise ContractError("TIMEOUT_PROCESS_LEADER_NOT_REAPED")


def _reject_surviving_process_group(process: WaitableProcess) -> None:
    """leader가 끝난 뒤 남은 descendant를 정리하고 해당 block을 무효화한다."""

    if not _process_group_exists(process.pid):
        return
    _terminate_process_group(process)
    raise ContractError("NATIVE_PROCESS_GROUP_SURVIVED_EXIT")


def _terminate_descendants(tracker: DescendantTracker) -> None:
    """정상 leader 종료 뒤 PGID/session을 이탈한 descendant만 bounded 종료한다."""

    term_deadline = time.monotonic() + 5.0
    _signal_descendants(tracker, signal.SIGTERM)
    if _wait_for_descendants_exit(
        tracker,
        deadline=term_deadline,
        sent_signal=signal.SIGTERM,
    ):
        return
    kill_deadline = time.monotonic() + 5.0
    _signal_descendants(tracker, signal.SIGKILL)
    if not _wait_for_descendants_exit(
        tracker,
        deadline=kill_deadline,
        sent_signal=signal.SIGKILL,
    ):
        raise ContractError("NATIVE_DESCENDANTS_SURVIVED_SIGKILL")


def _reject_surviving_processes(
    process: WaitableProcess,
    tracker: DescendantTracker,
) -> None:
    """정상 exit 뒤 하나라도 남은 tracked descendant를 청소하고 block을 거부한다."""

    del process
    if not tracker.descendant_identities():
        return
    _terminate_descendants(tracker)
    raise ContractError("NATIVE_DESCENDANTS_SURVIVED_EXIT")


def _run_process(
    command: list[str],
    *,
    executable: PinnedExecutable,
    inherited_executables: tuple[PinnedExecutable, ...] = (),
    cwd: Path,
    timeout_seconds: int,
    stdout_path: Path,
    stderr_path: Path,
    environment: dict[str, str],
) -> None:
    if command[0] != executable.binding["path"]:
        raise ContractError("PINNED_EXECUTABLE_BINDING_MISMATCH")
    pinned_executables = (executable, *inherited_executables)
    descriptors = tuple(item.descriptor for item in pinned_executables)
    if (
        any(descriptor < 0 for descriptor in descriptors)
        or len(descriptors) != len(set(descriptors))
    ):
        raise ContractError("PINNED_EXECUTABLE_DESCRIPTOR_SET_INVALID")
    for pinned in pinned_executables:
        try:
            actual_seals = fcntl.fcntl(pinned.descriptor, F_GET_SEALS)
        except OSError as exc:
            raise ContractError("PINNED_EXECUTABLE_UNAVAILABLE") from exc
        if actual_seals & pinned.required_seals != pinned.required_seals:
            raise ContractError("PINNED_EXECUTABLE_SEALS_CHANGED")

    with _child_subreaper_scope():
        launch_snapshot = _process_snapshot()
        root_record = launch_snapshot.get(os.getpid())
        if root_record is None:
            raise ContractError("DESCENDANT_TRACKER_ROOT_IDENTITY_MISSING")
        baseline_direct_children = frozenset(
            record.identity
            for record in launch_snapshot.values()
            if record.parent_pid == root_record.identity.pid
        )
        baseline_processes = frozenset(
            record.identity for record in launch_snapshot.values()
        )
        minimum_start_time_ticks = max(
            identity.start_time_ticks for identity in baseline_processes
        )
        with stdout_path.open("xb") as stdout, stderr_path.open("xb") as stderr:
            try:
                with ExitStack() as launch_stack:
                    launch_command = command
                    launch_executable = (
                        f"/proc/self/fd/{executable.descriptor}"
                    )
                    launch_descriptors = descriptors
                    if os.pread(executable.descriptor, 2, 0) == b"#!":
                        interpreter = launch_stack.enter_context(
                            _pin_executable(
                                FROZEN_BASH_IDENTITY,
                                role="processInterpreter",
                            )
                        )
                        actual_seals = fcntl.fcntl(
                            interpreter.descriptor,
                            F_GET_SEALS,
                        )
                        if (
                            actual_seals & interpreter.required_seals
                            != interpreter.required_seals
                        ):
                            raise ContractError(
                                "PINNED_INTERPRETER_SEALS_CHANGED"
                            )
                        launch_command = [
                            interpreter.binding["path"],
                            f"/proc/self/fd/{executable.descriptor}",
                            *command[1:],
                        ]
                        launch_executable = (
                            f"/proc/self/fd/{interpreter.descriptor}"
                        )
                        launch_descriptors = (
                            *descriptors,
                            interpreter.descriptor,
                        )
                    process = subprocess.Popen(
                        launch_command,
                        executable=launch_executable,
                        cwd=cwd,
                        env=environment,
                        stdin=subprocess.DEVNULL,
                        stdout=stdout,
                        stderr=stderr,
                        start_new_session=True,
                        pass_fds=launch_descriptors,
                    )
            except OSError as exc:
                raise ContractError("PINNED_EXECUTABLE_LAUNCH_FAILED") from exc
            leader_record = _read_launch_leader_record(process.pid)
            leader_identity: ProcessIdentity | None = None
            if (
                leader_record is not None
                and leader_record.parent_pid == root_record.identity.pid
                and leader_record.identity not in baseline_processes
                and leader_record.identity.start_time_ticks
                >= minimum_start_time_ticks
            ):
                leader_identity = leader_record.identity
            tracker = DescendantTracker(
                root=root_record.identity,
                leader=leader_identity,
                baseline_direct_children=baseline_direct_children,
                baseline_processes=baseline_processes,
                minimum_start_time_ticks=minimum_start_time_ticks,
            )
            if leader_identity is None:
                try:
                    process.wait(timeout=0)
                except subprocess.TimeoutExpired:
                    _terminate_process_group(process, tracker)
                if tracker.descendant_identities():
                    _terminate_descendants(tracker)
                raise ContractError("NATIVE_PROCESS_LEADER_IDENTITY_INVALID")
            deadline = time.monotonic() + timeout_seconds
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    _terminate_process_group(process, tracker)
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
        _reject_surviving_processes(process, tracker)
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
        runtime_executables = {
            boundary_id: {
                dependency_id: executable_stack.enter_context(
                    _pin_executable(
                        identity,
                        role=(
                            "runtimeDependency:"
                            f"{boundary_id}:{dependency_id}"
                        ),
                    )
                )
                for dependency_id, identity in dependencies.items()
            }
            for boundary_id, dependencies in manifest[
                "allowedExecutables"
            ]["runtimeDependenciesByBoundary"].items()
        }
        runtime_evidence = {
            boundary_id: {
                evidence_id: executable_stack.enter_context(
                    _pin_evidence(
                        identity,
                        role=(
                            f"runtimeEvidence:{boundary_id}:{evidence_id}"
                        ),
                    )
                )
                for evidence_id, identity in evidence.items()
            }
            for boundary_id, evidence in manifest[
                "allowedEvidenceByBoundary"
            ].items()
        }
        environments = {
            boundary_id: _benchmark_environment(
                runtime_executables[boundary_id],
                runtime_evidence[boundary_id],
                boundary_id=boundary_id,
            )
            for boundary_id in RUNTIME_DEPENDENCY_ROLES_BY_BOUNDARY
        }
        run_directory = reserve_directory(output_root / run_id)
        _pin_current_process(set(plan["execution"]["cpuSet"]))
        total_deadline = time.monotonic() + plan["execution"]["totalRunTimeoutSeconds"]
        valid_performance_timeouts = 0
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
                host_inherited = tuple(
                    runtime_executables["hostValidator"].values()
                )
                _run_process(
                    host_command,
                    executable=host_executable,
                    inherited_executables=host_inherited,
                    cwd=repo_root,
                    timeout_seconds=min(
                        plan["environmentValidity"]["maxQuietWaitSeconds"],
                        max(1, int(remaining_total)),
                    ),
                    stdout_path=output_directory / "host-validator.stdout",
                    stderr_path=output_directory / "host-validator.stderr",
                    environment=environments["hostValidator"],
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
                native_inherited = (
                    *runtime_executables[block.boundary_id].values(),
                    *runtime_evidence[block.boundary_id].values(),
                )
                _run_process(
                    native_command,
                    executable=native_executable,
                    inherited_executables=native_inherited,
                    cwd=repo_root,
                    timeout_seconds=block.timeout_seconds,
                    stdout_path=output_directory / "native-wrapper.stdout",
                    stderr_path=output_directory / "native-wrapper.stderr",
                    environment=environments[block.boundary_id],
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
