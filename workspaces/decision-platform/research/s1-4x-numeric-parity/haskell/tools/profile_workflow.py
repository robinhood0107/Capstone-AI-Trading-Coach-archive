#!/bin/false
"""S1.4X Haskell correctness, qualification, selector evidence workflow."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.metadata
import importlib.util
import json
import math
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence


CORRECTNESS_SCHEMA_VERSION = "s1.4x-haskell-full-correctness-v1"
CORRECTNESS_PHASES = (
    "build",
    "test",
    "canonical-process",
    "canonical-compare",
    "semantic-process",
    "semantic-compare",
)
PROFILE_OPTIONS = {
    "baseline-o0-fasm": ("-O0", "-fasm"),
    "optimized-o2-fasm": ("-O2", "-fasm"),
}
PROFILE_MARKER_SCHEMA_VERSION = "s1.4x-haskell-profile-measurement-state-v1"
QUALIFICATION_SCHEMA_VERSION = "s1.4x-haskell-profile-qualification-v1"
QUALIFICATION_DOCKER_ROUTE_SCHEMA_VERSION = (
    "s1.4x-haskell-qualification-docker-route-v1"
)
QUALIFICATION_DOCKER_SNAPSHOT_SCHEMA_VERSION = (
    "s1.4x-haskell-qualification-docker-snapshot-v1"
)
QUALIFICATION_HOST_TOOLS_PATH_ID = "S1_4X_QUALIFICATION_HOST_TOOLS"
QUALIFICATION_DOCKER_COMMAND_PATH_ID = (
    "S1_4X_QUALIFICATION_HOST_TOOLS_DOCKER"
)
QUALIFICATION_DOCKER_CONFIG_PATH_ID = (
    "S1_4X_QUALIFICATION_DOCKER_CONFIG"
)
QUALIFICATION_DOCKER_CONTEXT = "default"
QUALIFICATION_DOCKER_CONFIG_WSLENV = (
    "DOCKER_CONFIG/p:DOCKER_CONTEXT"
)
QUALIFICATION_OWNER_DOCKER_FD_PATH_ID = (
    "S1_4X_QUALIFICATION_OWNER_DOCKER_FD"
)
FINAL_PROFILE_SCHEMA_VERSION = "s1.4x-haskell-selected-profile-v1"
RUNTIME_PROFILE_FIELDS = {
    "schemaVersion",
    "profileId",
    "ghcOptions",
    "compilerVersion",
    "compilerSha256",
    "sourceTreeSha256",
    "optionsSha256",
    "fullCorrectnessSha256",
    "qualificationPlanSha256",
    "qualificationArtifactSha256",
    "selectorConfigSha256",
    "fallbackProfile",
    "selectedBy",
}
CURRENT_COMPATIBILITY_EVIDENCE_VERSION = (
    "s1.4x-ghc-current-frozen-dependency-evidence-v1"
)
CURRENT_COMPATIBILITY_PASS_EVIDENCE_VERSION = (
    "s1.4x-ghc-current-full-replay-evidence-v1"
)
COMPATIBILITY_REPLAY_PHASES = (
    "dependency",
    "candidateCompile",
    "fullCorrectness",
    "stableErrorReplay",
    "processReplay",
    "oracleReplay",
    "crossReplay",
)
PROFILE_MARKER_FIELDS = {
    "schemaVersion",
    "state",
    "planSha256",
    "selectorConfigSha256",
    "sourceTreeSha256",
    "orderBlock",
    "profileId",
    "ghcOptions",
    "optionsSha256",
    "qualificationCaseOrder",
    "hostValiditySha256",
    "markerPythonPath",
    "markerPythonPinnedFdPath",
    "markerPythonSha256",
    "markerScriptPath",
    "markerScriptPinnedFdPath",
    "markerScriptSha256",
    "markerArgv",
    "markerArgvSha256",
    "startedAt",
    "measurementEnteredAt",
    "preRunSha256",
}
PROFILE_ORDER_BLOCKS = (
    ("baseline-o0-fasm", "optimized-o2-fasm"),
    ("optimized-o2-fasm", "baseline-o0-fasm"),
    ("optimized-o2-fasm", "baseline-o0-fasm"),
    ("baseline-o0-fasm", "optimized-o2-fasm"),
)
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}")
FORBIDDEN_STACK_ENVIRONMENT = (
    "STACK_YAML",
    "STACK_ROOT",
    "STACK_OPTS",
    "STACK_CONFIG",
)
ORACLE_COMPARE_SOURCE_PATH_ID = "S1_4X_ORACLE_COMPARE_RESULTS_SOURCE"
ORACLE_COMMON_SOURCE_PATH_ID = "S1_4X_ORACLE_COMMON_SOURCE"
PINNED_ORACLE_COMPARE_BOOTSTRAP = (
    "import pathlib,sys,types\n"
    "compare_fd,common_fd,compare_source,common_source,*compare_argv="
    "sys.argv[1:]\n"
    "common=types.ModuleType('oracle_common')\n"
    "common.__file__=common_source\n"
    "common.__package__=''\n"
    "common.__spec__=None\n"
    "sys.modules['oracle_common']=common\n"
    "exec(compile(pathlib.Path(common_fd).read_bytes(),common_source,'exec'),"
    "common.__dict__)\n"
    "sys.argv=[compare_source,*compare_argv]\n"
    "namespace={'__name__':'__main__','__file__':compare_source,"
    "'__package__':None,'__cached__':None}\n"
    "exec(compile(pathlib.Path(compare_fd).read_bytes(),compare_source,'exec'),"
    "namespace)\n"
)
OCI_PLATFORM = "linux/amd64"
OCI_BASE_IMAGE = (
    "docker.io/library/haskell@sha256:"
    "417d4bc30ac7d8d5ff04ec97937f86eb508b0c76bfd1a39b5ec225688531aa9d"
)
OCI_PROVENANCE_LABEL_KEYS = {
    "io.s1-4x.base-image-id",
    "io.s1-4x.containerfile-sha256",
    "io.s1-4x.fixture-tree-sha256",
}


class WorkflowError(RuntimeError):
    """Workflow input이나 실행 결과가 frozen contract에서 벗어났을 때 발생한다."""


@dataclass(frozen=True)
class BenchmarkPythonRuntime:
    """Inherited CPython executable FD와 provenance source identity를 보존한다."""

    source_path: Path
    fd_path: Path
    descriptor: int
    sha256: str
    mode: int
    identity: tuple[int, int, int, int, int, int]
    configuration_path: Path
    configuration_sha256: str
    configuration_identity: tuple[int, int, int, int, int, int, int]
    dependency_closure: tuple[str, str, str]


@dataclass(frozen=True)
class PinnedOracleComparator:
    """Comparator와 sibling module을 같은 retained-FD 실행 폐쇄로 보존한다."""

    compare_script: Any
    common_module: Any


@dataclass(frozen=True)
class PinnedDockerClient:
    """Caller가 승인한 Docker bytes를 retained executable FD와 path ID로 결속한다."""

    source_path: Path
    fd_path: Path
    descriptor: int
    sha256: str
    mode: int
    identity: Mapping[str, int]
    path_id: str


@dataclass(frozen=True)
class QualificationDockerRoute:
    """Qualification owner FD를 output-bound `docker` 이름으로만 노출한다."""

    output_root: Path
    host_tools_directory: Path
    docker_link: Path
    docker_link_target: str
    docker_config_directory: Path
    owner_pid: int
    owner_start_ticks: int
    owner_uid: int
    output_identity: Mapping[str, int]
    directory_identity: Mapping[str, int]
    link_identity: Mapping[str, int]
    docker_config_identity: Mapping[str, int]


def _load_pinned_runtime_helpers() -> tuple[Any, Any, type[Exception]]:
    """Workflow 전체가 쓰는 FD pin helper를 지연 import해 module import를 가볍게 둔다."""

    try:
        from haskell_benchmark_block import (
            BlockError,
            pin_regular_file,
            pinned_executable_environment,
        )
    except ModuleNotFoundError as import_error:
        if import_error.name != "haskell_benchmark_block":
            raise
        from tools.haskell_benchmark_block import (
            BlockError,
            pin_regular_file,
            pinned_executable_environment,
        )
    return pinned_executable_environment, pin_regular_file, BlockError


def _benchmark_python_full_identity(
    value: os.stat_result,
) -> tuple[int, int, int, int, int, int, int]:
    """CPython source pathname과 retained FD가 공유해야 할 full stat identity다."""

    return (
        value.st_dev,
        value.st_ino,
        value.st_size,
        value.st_mode,
        value.st_mtime_ns,
        value.st_ctime_ns,
        value.st_nlink,
    )


def _benchmark_python_dependency_closure() -> tuple[str, str, str]:
    """현재 venv의 exact metadata와 imported NumPy version을 함께 읽는다."""

    try:
        importlib.import_module("jsonschema")
        numpy = importlib.import_module("numpy")
        closure = (
            importlib.metadata.version("jsonschema"),
            importlib.metadata.version("numpy"),
            str(getattr(numpy, "__version__", "")),
        )
    except (
        ImportError,
        importlib.metadata.PackageNotFoundError,
    ) as exc:
        raise WorkflowError(
            "BENCHMARK_PYTHON_DEPENDENCY_CLOSURE_INVALID"
        ) from exc
    return closure


def _benchmark_python_shell_stat_identity(path: Path) -> str:
    """Bash pin 단계가 기록한 GNU stat full identity와 같은 표현을 만든다."""

    matched = re.fullmatch(r"/proc/self/fd/([0-9]+)", str(path))
    pass_fds = () if matched is None else (int(matched.group(1)),)
    stat_environment = {"LC_ALL": "C", "PATH": "/usr/bin:/bin"}
    if "TZ" in os.environ:
        stat_environment["TZ"] = os.environ["TZ"]
    try:
        completed = subprocess.run(
            [
                "/usr/bin/stat",
                "-Lc",
                "%d:%i:%s:%f:%y:%z:%h",
                "--",
                str(path),
            ],
            env=stat_environment,
            check=False,
            capture_output=True,
            text=True,
            pass_fds=pass_fds,
        )
    except OSError as exc:
        raise WorkflowError(
            "BENCHMARK_PYTHON_INITIAL_CLOSURE_STAT_FAILED"
        ) from exc
    if completed.returncode != 0 or completed.stderr or not completed.stdout:
        raise WorkflowError("BENCHMARK_PYTHON_INITIAL_CLOSURE_STAT_FAILED")
    return completed.stdout.rstrip("\n")


def _benchmark_python_runtime() -> BenchmarkPythonRuntime:
    """현재 process와 inherited FD를 accepted CPython 3.12.13 bytes에 결속한다."""

    source_value = os.environ.get("S1_4X_BENCHMARK_PYTHON_BIN")
    fd_value = os.environ.get("S1_4X_BENCHMARK_PYTHON_PINNED_FD_PATH")
    expected_sha256 = os.environ.get("S1_4X_BENCHMARK_PYTHON_SHA256")
    if (
        source_value is None
        or fd_value is None
        or expected_sha256 is None
    ):
        raise WorkflowError("BENCHMARK_PYTHON_ENVIRONMENT_MISSING")
    if (
        not source_value.startswith("/")
        or "\0" in source_value
        or "\n" in source_value
        or ":" in source_value
        or "|" in source_value
        or "//" in source_value
        or "/./" in source_value
        or "/../" in source_value
        or source_value.endswith(("/.", "/.."))
        or SHA256_PATTERN.fullmatch(expected_sha256) is None
    ):
        raise WorkflowError("BENCHMARK_PYTHON_SOURCE_LAYOUT_INVALID")
    matched = re.fullmatch(r"/proc/self/fd/([0-9]+)", fd_value)
    if matched is None or int(matched.group(1)) < 3:
        raise WorkflowError("BENCHMARK_PYTHON_PINNED_FD_PATH_INVALID")
    descriptor = int(matched.group(1))
    if (
        sys.implementation.name != "cpython"
        or sys.version_info[:3] != (3, 12, 13)
    ):
        raise WorkflowError("BENCHMARK_PYTHON_VERSION_INVALID")
    source_path = Path(source_value)
    venv_root = source_path.parent.parent
    venv_configuration = venv_root / "pyvenv.cfg"
    try:
        source_before = os.lstat(source_path)
        configuration_before = os.lstat(venv_configuration)
        resolved_source = source_path.resolve(strict=True)
        resolved_configuration = venv_configuration.resolve(strict=True)
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_mode & 0o111 == 0
            or before.st_size < 0
            or before.st_size > 1024 * 1024 * 1024
        ):
            raise WorkflowError("BENCHMARK_PYTHON_PINNED_FD_OBJECT_INVALID")
        digest = hashlib.sha256()
        offset = 0
        while offset < before.st_size:
            chunk = os.pread(
                descriptor,
                min(1024 * 1024, before.st_size - offset),
                offset,
            )
            if not chunk:
                raise WorkflowError("BENCHMARK_PYTHON_PINNED_FD_SHORT_READ")
            digest.update(chunk)
            offset += len(chunk)
        configuration_payload = venv_configuration.read_bytes()
        dependency_closure = _benchmark_python_dependency_closure()
        pinned = os.fstat(descriptor)
        current = os.stat("/proc/self/exe")
        source_after = os.lstat(source_path)
        configuration_after = os.lstat(venv_configuration)
    except OSError as exc:
        raise WorkflowError("BENCHMARK_PYTHON_RUNTIME_FSTAT_FAILED") from exc
    source_identity = _benchmark_python_full_identity(source_before)
    configuration_identity = _benchmark_python_full_identity(
        configuration_before
    )
    pinned_identity = (
        pinned.st_dev,
        pinned.st_ino,
        pinned.st_size,
        pinned.st_mtime_ns,
        pinned.st_ctime_ns,
        pinned.st_nlink,
    )
    if (
        source_path.parent.name != "bin"
        or resolved_source != source_path
        or resolved_configuration != venv_configuration
        or not stat.S_ISREG(source_before.st_mode)
        or source_before.st_mode & 0o111 == 0
        or not stat.S_ISREG(configuration_before.st_mode)
        or source_identity != _benchmark_python_full_identity(source_after)
        or configuration_identity
        != _benchmark_python_full_identity(configuration_after)
        or source_identity != _benchmark_python_full_identity(pinned)
        or source_identity != _benchmark_python_full_identity(current)
        or pinned_identity
        != (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
            before.st_nlink,
        )
        or not stat.S_ISREG(pinned.st_mode)
        or pinned.st_mode & 0o111 == 0
        or digest.hexdigest() != expected_sha256
        or (pinned.st_dev, pinned.st_ino, pinned.st_size)
        != (current.st_dev, current.st_ino, current.st_size)
        or sys.executable != source_value
        or Path(sys.prefix) != venv_root
        or sys.prefix == sys.base_prefix
        or dependency_closure != ("4.26.0", "2.5.1", "2.5.1")
    ):
        raise WorkflowError("BENCHMARK_PYTHON_RUNTIME_IDENTITY_INVALID")
    initial_environment = {
        "route": os.environ.get(
            "S1_4X_BENCHMARK_PYTHON_INITIAL_ROUTE_IDENTITY"
        ),
        "configurationIdentity": os.environ.get(
            "S1_4X_BENCHMARK_PYTHON_INITIAL_VENV_IDENTITY"
        ),
        "configurationSha256": os.environ.get(
            "S1_4X_BENCHMARK_PYTHON_INITIAL_VENV_SHA256"
        ),
        "dependencies": os.environ.get(
            "S1_4X_BENCHMARK_PYTHON_INITIAL_DEPENDENCIES"
        ),
    }
    present_initial_values = {
        name for name, value in initial_environment.items() if value is not None
    }
    if present_initial_values and present_initial_values != set(
        initial_environment
    ):
        raise WorkflowError("BENCHMARK_PYTHON_INITIAL_CLOSURE_INCOMPLETE")
    configuration_sha256 = hashlib.sha256(
        configuration_payload
    ).hexdigest()
    if present_initial_values and (
        initial_environment["route"]
        != _benchmark_python_shell_stat_identity(source_path)
        or initial_environment["route"]
        != _benchmark_python_shell_stat_identity(Path(fd_value))
        or initial_environment["configurationIdentity"]
        != _benchmark_python_shell_stat_identity(venv_configuration)
        or initial_environment["configurationSha256"]
        != configuration_sha256
        or initial_environment["dependencies"] != "|".join(dependency_closure)
    ):
        raise WorkflowError("BENCHMARK_PYTHON_INITIAL_CLOSURE_CHANGED")
    return BenchmarkPythonRuntime(
        source_path=source_path,
        fd_path=Path(fd_value),
        descriptor=descriptor,
        sha256=expected_sha256,
        mode=pinned.st_mode,
        identity=pinned_identity,
        configuration_path=venv_configuration,
        configuration_sha256=configuration_sha256,
        configuration_identity=configuration_identity,
        dependency_closure=dependency_closure,
    )


def _pinned_file_path_id(pinned_file: Any) -> str:
    """Sealed script bytes를 command receipt에 남길 portable path ID로 바꾼다."""

    label = re.sub(r"[^A-Z0-9]+", "_", str(pinned_file.label).upper()).strip("_")
    digest = _require_sha256(pinned_file.sha256, label=f"{label}-pinned-file")
    if not label:
        raise WorkflowError("PINNED_FILE_PATH_ID_INVALID")
    return f"S1_4X_{label}_SHA256_{digest.upper()}"


def _benchmark_python_path_id(runtime: BenchmarkPythonRuntime) -> str:
    """Accepted interpreter bytes를 host-independent command ID로 표현한다."""

    digest = _require_sha256(runtime.sha256, label="benchmark-python-runtime")
    return f"S1_4X_BENCHMARK_CPYTHON_3_12_13_SHA256_{digest.upper()}"


def _pin_python_script(path: Path, *, label: str) -> Any:
    """Nested Python source를 immutable memfd로 봉인해 pathname race를 제거한다."""

    _, pin_regular_file, pinned_runtime_error = _load_pinned_runtime_helpers()
    try:
        return pin_regular_file(
            _absolute_regular(path, label=label),
            label=label,
            max_bytes=16 * 1024 * 1024,
        )
    except pinned_runtime_error as exc:
        raise WorkflowError(f"{label}_PIN_INVALID:{exc}") from exc


def _pin_oracle_comparator(numeric_root: Path) -> PinnedOracleComparator:
    """Frozen comparator와 local import를 pathname 재개방 없이 함께 봉인한다."""

    compare_script = _pin_python_script(
        _absolute_regular(
            numeric_root / "oracle/compare_results.py",
            label="COMPARE_RESULTS",
        ),
        label="COMPARE_RESULTS_PY",
    )
    try:
        common_module = _pin_python_script(
            _absolute_regular(
                numeric_root / "oracle/oracle_common.py",
                label="ORACLE_COMMON",
            ),
            label="ORACLE_COMMON_PY",
        )
    except BaseException:
        os.close(compare_script.descriptor)
        raise
    return PinnedOracleComparator(
        compare_script=compare_script,
        common_module=common_module,
    )


def _oracle_compare_command(
    *,
    python_path: Path,
    comparator: PinnedOracleComparator,
    arguments: Sequence[str],
) -> list[str]:
    """Pinned bytes를 쓰되 frozen source의 `__file__` 경로 의미를 보존한다."""

    if any(not isinstance(argument, str) or "\0" in argument for argument in arguments):
        raise WorkflowError("ORACLE_COMPARE_ARGUMENT_INVALID")
    return [
        str(python_path),
        "-I",
        "-c",
        PINNED_ORACLE_COMPARE_BOOTSTRAP,
        str(comparator.compare_script.fd_path),
        str(comparator.common_module.fd_path),
        str(comparator.compare_script.source_path),
        str(comparator.common_module.source_path),
        *arguments,
    ]


def _oracle_compare_pass_fds(
    python_runtime: BenchmarkPythonRuntime,
    comparator: PinnedOracleComparator,
) -> tuple[int, int, int]:
    """Comparator child에 accepted CPython과 두 source snapshot FD만 상속한다."""

    return (
        python_runtime.descriptor,
        comparator.compare_script.descriptor,
        comparator.common_module.descriptor,
    )


def _oracle_compare_path_ids(
    python_runtime: BenchmarkPythonRuntime,
    comparator: PinnedOracleComparator,
) -> dict[str, str]:
    """Comparator argv의 runtime, source FD, provenance path를 portable ID로 바꾼다."""

    return {
        str(python_runtime.fd_path): _benchmark_python_path_id(python_runtime),
        str(comparator.compare_script.fd_path): _pinned_file_path_id(
            comparator.compare_script
        ),
        str(comparator.common_module.fd_path): _pinned_file_path_id(
            comparator.common_module
        ),
        str(comparator.compare_script.source_path): ORACLE_COMPARE_SOURCE_PATH_ID,
        str(comparator.common_module.source_path): ORACLE_COMMON_SOURCE_PATH_ID,
    }


def _portable_argv(
    command: Sequence[str],
    path_ids: Mapping[str, str] | None,
) -> list[str]:
    """실제 FD argv를 receipt용 stable ID로 치환한다."""

    if path_ids is None:
        return list(command)
    if any(
        not isinstance(path, str)
        or not path
        or not isinstance(path_id, str)
        or re.fullmatch(r"[A-Z0-9_]+", path_id) is None
        for path, path_id in path_ids.items()
    ):
        raise WorkflowError("COMMAND_PORTABLE_PATH_IDS_INVALID")
    portable = [path_ids.get(argument, argument) for argument in command]
    if any(argument.startswith("/proc/self/fd/") for argument in portable):
        raise WorkflowError("COMMAND_PINNED_FD_NOT_PORTABLE")
    return portable


def _stat_identity(value: os.stat_result) -> dict[str, int]:
    """FD witness가 저장하는 Linux regular-file stat identity를 정규화한다."""

    return {
        "device": value.st_dev,
        "inode": value.st_ino,
        "size": value.st_size,
        "mode": value.st_mode,
        "mtimeNs": value.st_mtime_ns,
        "ctimeNs": value.st_ctime_ns,
        "linkCount": value.st_nlink,
    }


def _directory_anchor_identity(value: os.stat_result) -> dict[str, int]:
    """내용이 늘어나는 output directory는 stable inode/owner/mode만 결속한다."""

    return {
        "device": value.st_dev,
        "inode": value.st_ino,
        "mode": value.st_mode,
        "uid": value.st_uid,
        "gid": value.st_gid,
    }


def _process_start_ticks(pid: int) -> int:
    """PID reuse와 owner 종료를 구분하는 `/proc/<pid>/stat` starttime을 읽는다."""

    if type(pid) is not int or pid <= 0:
        raise WorkflowError("QUALIFICATION_WITNESS_OWNER_PID_INVALID")
    try:
        payload = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
    except (OSError, UnicodeError) as exc:
        raise WorkflowError("QUALIFICATION_WITNESS_OWNER_NOT_LIVE") from exc
    closing = payload.rfind(")")
    if closing <= 1 or not payload.startswith(f"{pid} ("):
        raise WorkflowError("QUALIFICATION_WITNESS_OWNER_STAT_INVALID")
    fields = payload[closing + 2 :].split()
    try:
        start_ticks = int(fields[19])
    except (IndexError, ValueError) as exc:
        raise WorkflowError("QUALIFICATION_WITNESS_OWNER_STAT_INVALID") from exc
    if start_ticks <= 0:
        raise WorkflowError("QUALIFICATION_WITNESS_OWNER_STAT_INVALID")
    return start_ticks


def _process_identity_is_live(pid: int, start_ticks: int) -> bool:
    """같은 PID와 starttime의 owner가 아직 살아 있는지 확인한다."""

    try:
        return _process_start_ticks(pid) == start_ticks
    except WorkflowError as exc:
        if str(exc) == "QUALIFICATION_WITNESS_OWNER_NOT_LIVE":
            return False
        raise


def _qualification_marker_path_id(
    *,
    order_block: int,
    profile_id: str,
) -> str:
    """Output pathname 대신 block/profile 의미만 담는 portable marker ID를 만든다."""

    if type(order_block) is not int or order_block not in range(4):
        raise WorkflowError("QUALIFICATION_WITNESS_BLOCK_INVALID")
    profile_options(profile_id)
    normalized_profile = re.sub(r"[^A-Z0-9]+", "_", profile_id.upper()).strip("_")
    return (
        "S1_4X_QUALIFICATION_MEASUREMENT_"
        f"BLOCK_{order_block + 1}_{normalized_profile}"
    )


def _read_descriptor_sha256(
    descriptor: int,
    *,
    expected_identity: Mapping[str, Any],
    label: str,
) -> str:
    """Live owner FD bytes를 pread하고 전후 fstat identity를 witness에 결속한다."""

    if type(descriptor) is not int or descriptor < 3:
        raise WorkflowError(f"QUALIFICATION_WITNESS_{label}_FD_INVALID")
    try:
        before = os.fstat(descriptor)
    except OSError as exc:
        raise WorkflowError(
            f"QUALIFICATION_WITNESS_{label}_FD_NOT_LIVE"
        ) from exc
    if (
        not stat.S_ISREG(before.st_mode)
        or _stat_identity(before) != dict(expected_identity)
        or before.st_size < 0
        or before.st_size > 1024 * 1024 * 1024
    ):
        raise WorkflowError(f"QUALIFICATION_WITNESS_{label}_IDENTITY_INVALID")
    digest = hashlib.sha256()
    offset = 0
    while offset < before.st_size:
        try:
            chunk = os.pread(
                descriptor,
                min(1024 * 1024, before.st_size - offset),
                offset,
            )
        except OSError as exc:
            raise WorkflowError(
                f"QUALIFICATION_WITNESS_{label}_FD_READ_FAILED"
            ) from exc
        if not chunk:
            raise WorkflowError(
                f"QUALIFICATION_WITNESS_{label}_FD_SHORT_READ"
            )
        digest.update(chunk)
        offset += len(chunk)
    if _stat_identity(os.fstat(descriptor)) != dict(expected_identity):
        raise WorkflowError(
            f"QUALIFICATION_WITNESS_{label}_CHANGED_DURING_READ"
        )
    return digest.hexdigest()


def build_qualification_command_witness(
    *,
    owner_pid: int,
    owner_start_ticks: int,
    python_source_path: Path,
    python_source_sha256: str,
    python_descriptor: int,
    python_identity: Mapping[str, Any],
    script_source_path: Path,
    script_source_sha256: str,
    script_descriptor: int,
    script_identity: Mapping[str, Any],
    marker_argv: Sequence[str],
    marker_path_id: str,
) -> dict[str, Any]:
    """Live FD owner가 exact argv/source/stat을 portable historical witness로 봉인한다."""

    if (
        type(owner_pid) is not int
        or owner_pid <= 0
        or type(owner_start_ticks) is not int
        or owner_start_ticks <= 0
        or _process_start_ticks(owner_pid) != owner_start_ticks
        or not python_source_path.is_absolute()
        or not script_source_path.is_absolute()
        or re.fullmatch(r"[A-Z0-9_]+", marker_path_id) is None
    ):
        raise WorkflowError("QUALIFICATION_WITNESS_INPUT_INVALID")
    python_sha256 = _require_sha256(
        python_source_sha256,
        label="qualification-witness-python",
    )
    script_sha256 = _require_sha256(
        script_source_sha256,
        label="qualification-witness-script",
    )
    raw_argv = list(marker_argv)
    if (
        len(raw_argv) != 8
        or raw_argv[:3]
        != ["/usr/bin/env", "-a", str(python_source_path)]
        or raw_argv[3] != f"/proc/self/fd/{python_descriptor}"
        or raw_argv[4] != f"/proc/self/fd/{script_descriptor}"
        or raw_argv[5:7] != ["mark-measurement-entered", "--qualification"]
        or not raw_argv[7].startswith("/")
    ):
        raise WorkflowError("QUALIFICATION_WITNESS_ARGV_INVALID")
    actual_python_sha256 = _read_descriptor_sha256(
        python_descriptor,
        expected_identity=python_identity,
        label="PYTHON",
    )
    actual_script_sha256 = _read_descriptor_sha256(
        script_descriptor,
        expected_identity=script_identity,
        label="SCRIPT",
    )
    if (
        actual_python_sha256 != python_sha256
        or actual_script_sha256 != script_sha256
    ):
        raise WorkflowError("QUALIFICATION_WITNESS_SOURCE_BYTES_MISMATCH")
    objects = {
        "python": {
            "sourcePath": str(python_source_path),
            "sourcePathSha256": hashlib.sha256(
                os.fsencode(str(python_source_path))
            ).hexdigest(),
            "sourceBytesSha256": python_sha256,
            "fdNumber": python_descriptor,
            "fdIdentity": dict(python_identity),
        },
        "script": {
            "sourcePath": str(script_source_path),
            "sourcePathSha256": hashlib.sha256(
                os.fsencode(str(script_source_path))
            ).hexdigest(),
            "sourceBytesSha256": script_sha256,
            "fdNumber": script_descriptor,
            "fdIdentity": dict(script_identity),
        },
    }
    normalized_argv = [
        "/usr/bin/env",
        "-a",
        "S1_4X_BENCHMARK_CPYTHON_SOURCE_ARGV0",
        (
            "S1_4X_BENCHMARK_CPYTHON_3_12_13_SHA256_"
            f"{python_sha256.upper()}"
        ),
        f"S1_4X_PROFILE_MARKER_SCRIPT_SHA256_{script_sha256.upper()}",
        "mark-measurement-entered",
        "--qualification",
        marker_path_id,
    ]
    witness = {
        "schemaVersion": "s1.4x-portable-command-witness-v1",
        "owner": {
            "pid": owner_pid,
            "startTicks": owner_start_ticks,
        },
        "objects": objects,
        "objectsSha256": canonical_sha256(objects),
        "ownerArgvSha256": canonical_sha256(raw_argv),
        "markerPathId": marker_path_id,
        "normalizedArgv": normalized_argv,
        "normalizedArgvSha256": canonical_sha256(normalized_argv),
    }
    witness["witnessSha256"] = canonical_sha256(witness)
    return witness


def validate_qualification_command_witness(
    witness: object,
    *,
    marker_argv: Sequence[str],
    marker_path_id: str,
    python_source_path: Path,
    python_source_sha256: str,
    script_source_path: Path,
    script_source_sha256: str,
    require_owner_exit: bool,
) -> dict[str, Any]:
    """Dead owner FD를 재개방하지 않고 historical stat/argv/source closure를 검증한다."""

    expected_fields = {
        "schemaVersion",
        "owner",
        "objects",
        "objectsSha256",
        "ownerArgvSha256",
        "markerPathId",
        "normalizedArgv",
        "normalizedArgvSha256",
        "witnessSha256",
    }
    if (
        not isinstance(witness, dict)
        or set(witness) != expected_fields
        or witness.get("schemaVersion")
        != "s1.4x-portable-command-witness-v1"
        or type(require_owner_exit) is not bool
        or re.fullmatch(r"[A-Z0-9_]+", marker_path_id) is None
    ):
        raise WorkflowError("QUALIFICATION_WITNESS_OBJECT_INVALID")
    owner = witness.get("owner")
    objects = witness.get("objects")
    if (
        not isinstance(owner, dict)
        or set(owner) != {"pid", "startTicks"}
        or type(owner.get("pid")) is not int
        or owner["pid"] <= 0
        or type(owner.get("startTicks")) is not int
        or owner["startTicks"] <= 0
        or not isinstance(objects, dict)
        or set(objects) != {"python", "script"}
    ):
        raise WorkflowError("QUALIFICATION_WITNESS_OWNER_OR_OBJECT_INVALID")
    expected_sources = {
        "python": (
            python_source_path,
            _require_sha256(
                python_source_sha256,
                label="qualification-witness-python",
            ),
        ),
        "script": (
            script_source_path,
            _require_sha256(
                script_source_sha256,
                label="qualification-witness-script",
            ),
        ),
    }
    raw_argv = list(marker_argv)
    for index, name in zip((3, 4), ("python", "script"), strict=True):
        binding = objects.get(name)
        source_path, source_sha256 = expected_sources[name]
        if (
            not isinstance(binding, dict)
            or set(binding)
            != {
                "sourcePath",
                "sourcePathSha256",
                "sourceBytesSha256",
                "fdNumber",
                "fdIdentity",
            }
            or binding.get("sourcePath") != str(source_path)
            or binding.get("sourcePathSha256")
            != hashlib.sha256(os.fsencode(str(source_path))).hexdigest()
            or binding.get("sourceBytesSha256") != source_sha256
            or type(binding.get("fdNumber")) is not int
            or binding["fdNumber"] < 3
            or len(raw_argv) != 8
            or raw_argv[index]
            != f"/proc/self/fd/{binding['fdNumber']}"
        ):
            raise WorkflowError(
                f"QUALIFICATION_WITNESS_{name.upper()}_BINDING_INVALID"
            )
        identity = binding.get("fdIdentity")
        if (
            not isinstance(identity, dict)
            or set(identity)
            != {
                "device",
                "inode",
                "size",
                "mode",
                "mtimeNs",
                "ctimeNs",
                "linkCount",
            }
            or any(type(value) is not int or value < 0 for value in identity.values())
            or not stat.S_ISREG(identity["mode"])
            or identity["size"] > 1024 * 1024 * 1024
        ):
            raise WorkflowError(
                f"QUALIFICATION_WITNESS_{name.upper()}_FSTAT_INVALID"
            )
    expected_normalized = [
        "/usr/bin/env",
        "-a",
        "S1_4X_BENCHMARK_CPYTHON_SOURCE_ARGV0",
        (
            "S1_4X_BENCHMARK_CPYTHON_3_12_13_SHA256_"
            f"{expected_sources['python'][1].upper()}"
        ),
        (
            "S1_4X_PROFILE_MARKER_SCRIPT_SHA256_"
            f"{expected_sources['script'][1].upper()}"
        ),
        "mark-measurement-entered",
        "--qualification",
        marker_path_id,
    ]
    without_hash = dict(witness)
    witness_sha256 = without_hash.pop("witnessSha256", None)
    if (
        raw_argv[:3]
        != ["/usr/bin/env", "-a", str(python_source_path)]
        or raw_argv[5:7] != ["mark-measurement-entered", "--qualification"]
        or not raw_argv[7].startswith("/")
        or witness.get("objectsSha256") != canonical_sha256(objects)
        or witness.get("ownerArgvSha256") != canonical_sha256(raw_argv)
        or witness.get("markerPathId") != marker_path_id
        or witness.get("normalizedArgv") != expected_normalized
        or witness.get("normalizedArgvSha256")
        != canonical_sha256(expected_normalized)
        or witness_sha256 != canonical_sha256(without_hash)
    ):
        raise WorkflowError("QUALIFICATION_WITNESS_CANONICAL_BINDING_INVALID")
    if require_owner_exit and _process_identity_is_live(
        owner["pid"],
        owner["startTicks"],
    ):
        raise WorkflowError("QUALIFICATION_WITNESS_OWNER_STILL_LIVE")
    return witness


def canonical_json_bytes(value: Any, *, trailing_newline: bool = False) -> bytes:
    """Non-finite 값을 거부하는 sorted compact JSON bytes를 만든다."""

    payload = json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    if trailing_newline:
        payload += "\n"
    return payload.encode("utf-8")


def canonical_sha256(value: Any) -> str:
    """Canonical JSON의 SHA-256을 계산한다."""

    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def sha256_file(path: Path) -> str:
    """Regular non-symlink file의 SHA-256을 계산한다."""

    if path.is_symlink() or not path.is_file():
        raise WorkflowError(f"REGULAR_FILE_REQUIRED:{path}")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def strict_json_load(path: Path) -> Any:
    """중복 key와 non-finite number를 거부하며 JSON을 읽는다."""

    def object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise WorkflowError(f"DUPLICATE_JSON_KEY:{path}:{key}")
            result[key] = value
        return result

    def reject_constant(token: str) -> None:
        raise WorkflowError(f"NONFINITE_JSON_TOKEN:{path}:{token}")

    try:
        return json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=object_pairs,
            parse_constant=reject_constant,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise WorkflowError(f"INVALID_JSON:{path}") from exc


def atomic_write_json_exclusive(path: Path, value: Any) -> None:
    """새 path에만 canonical JSON을 원자적으로 발행한다."""

    if path.exists() or path.is_symlink():
        raise WorkflowError(f"OUTPUT_ALREADY_EXISTS:{path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(canonical_json_bytes(value, trailing_newline=True))
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError as exc:
            raise WorkflowError(f"OUTPUT_ALREADY_EXISTS:{path}") from exc
    finally:
        temporary.unlink(missing_ok=True)


def atomic_replace_json(path: Path, value: Any) -> None:
    """기존 regular file을 같은 directory의 canonical JSON으로 원자 교체한다."""

    if path.is_symlink() or not path.is_file():
        raise WorkflowError(f"REPLACE_TARGET_NOT_REGULAR:{path}")
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(canonical_json_bytes(value, trailing_newline=True))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory_descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        temporary.unlink(missing_ok=True)


def profile_options(profile_id: object) -> tuple[str, str]:
    """두 authoritative profile ID만 exact compiler option tuple로 변환한다."""

    if type(profile_id) is not str or profile_id not in PROFILE_OPTIONS:
        raise WorkflowError("PROFILE_ID_INVALID")
    return PROFILE_OPTIONS[profile_id]


def runtime_selected_profile(document: object) -> tuple[str, tuple[str, str]]:
    """통합 candidate 실행에는 qualification이 끝난 exact final profile만 허용한다."""

    if (
        not isinstance(document, dict)
        or document.get("schemaVersion") != FINAL_PROFILE_SCHEMA_VERSION
    ):
        raise WorkflowError("RUNTIME_SELECTED_PROFILE_NOT_FINAL")
    if set(document) != RUNTIME_PROFILE_FIELDS:
        raise WorkflowError("RUNTIME_SELECTED_PROFILE_INVALID")
    try:
        profile_id = document["profileId"]
        options = profile_options(profile_id)
        for field in (
            "compilerSha256",
            "sourceTreeSha256",
            "optionsSha256",
            "fullCorrectnessSha256",
            "qualificationPlanSha256",
            "qualificationArtifactSha256",
            "selectorConfigSha256",
        ):
            _require_sha256(document.get(field), label=f"runtime-profile-{field}")
    except WorkflowError as exc:
        raise WorkflowError("RUNTIME_SELECTED_PROFILE_INVALID") from exc
    if (
        document.get("ghcOptions") != list(options)
        or document.get("optionsSha256") != canonical_sha256(list(options))
        or document.get("compilerVersion") != "9.10.3"
        or document.get("fallbackProfile") != "baseline-o0-fasm"
        or document.get("selectedBy")
        not in {"frozen-criterion-selector", "proven-fallback"}
    ):
        raise WorkflowError("RUNTIME_SELECTED_PROFILE_INVALID")
    return profile_id, options


def isolated_stack_root(
    cache_root: Path,
    *,
    purpose: str,
    output_path: Path,
) -> Path:
    """Workflow 목적과 output identity마다 재사용 불가능한 Stack root를 파생한다."""

    if (
        not cache_root.is_absolute()
        or not output_path.is_absolute()
        or type(purpose) is not str
        or re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", purpose) is None
    ):
        raise WorkflowError("ISOLATED_STACK_ROOT_INPUT_INVALID")
    identity = purpose.encode("ascii") + b"\0" + os.fsencode(str(output_path))
    suffix = hashlib.sha256(identity).hexdigest()[:24]
    return cache_root / f"stack-root-{purpose}-{suffix}"


def candidate_stack_root(cache_root: Path, output_path: Path) -> Path:
    """통합 candidate의 output-bound Stack root를 호환 API로 반환한다."""

    return isolated_stack_root(
        cache_root,
        purpose="candidate",
        output_path=output_path,
    )


def isolated_stack_work_dir(stack_root: Path) -> Path:
    """Output-bound Stack root 이름으로 단일-component build 경계를 만든다."""

    if (
        not stack_root.is_absolute()
        or re.fullmatch(r"stack-root(?:-[a-z0-9]+)*", stack_root.name)
        is None
    ):
        raise WorkflowError("ISOLATED_STACK_WORK_DIR_INPUT_INVALID")
    # Stack은 이 상대 경로를 unpacked dependency에도 적용하므로 중간 부모가 없는 단일
    # component여야 fresh dependency build가 실패하지 않는다.
    return Path(f".stack-work-s1-4x-{stack_root.name}")


def build_stack_command(
    *,
    ghcup: Path,
    stack: Path,
    stack_yaml: Path,
    stack_root: Path,
    work_dir: Path | None = None,
    ghc_version: str,
    operation: Sequence[str],
) -> list[str]:
    """GHCup offline resolver와 exact Stack project command를 조립한다."""

    if ghc_version not in {"9.10.3", "9.14.1"}:
        raise WorkflowError("GHC_VERSION_INVALID")
    expected_work_dir = isolated_stack_work_dir(stack_root)
    effective_work_dir = (
        expected_work_dir if work_dir is None else work_dir
    )
    if (
        effective_work_dir.is_absolute()
        or effective_work_dir != expected_work_dir
    ):
        raise WorkflowError("STACK_WORK_DIR_INVALID")
    if not operation or any(type(argument) is not str or not argument for argument in operation):
        raise WorkflowError("STACK_OPERATION_INVALID")
    return [
        str(ghcup),
        "--offline",
        "run",
        "--quick",
        "--ghc",
        ghc_version,
        "--stack",
        "3.11.1",
        "--",
        str(stack),
        "--stack-root",
        str(stack_root),
        "--work-dir",
        str(effective_work_dir),
        "--stack-yaml",
        str(stack_yaml),
        "--no-terminal",
        "--color",
        "never",
        "--system-ghc",
        "--no-install-ghc",
        "--hpack-force",
        *operation,
    ]


def _require_sha256(value: object, *, label: str) -> str:
    if type(value) is not str or SHA256_PATTERN.fullmatch(value) is None:
        raise WorkflowError(f"SHA256_INVALID:{label}")
    return value


def _require_iso_utc(value: object, *, label: str) -> str:
    if type(value) is not str or re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z",
        value,
    ) is None:
        raise WorkflowError(f"UTC_TIMESTAMP_INVALID:{label}")
    return value


def _pinned_fd_number(value: object) -> int | None:
    """현재 process의 canonical inherited FD path만 lexical하게 해석한다."""

    if type(value) is not str:
        return None
    matched = re.fullmatch(r"/proc/self/fd/([1-9][0-9]*)", value)
    if matched is None:
        return None
    descriptor = int(matched.group(1))
    return descriptor if descriptor >= 3 else None


def _qualification_marker_command(
    *,
    python_source_path: Path,
    python_pinned_fd_path: Path,
    script_pinned_fd_path: Path,
    marker_path: Path,
) -> list[str]:
    """Qualification producer와 verifier가 공유할 exact 8-token argv를 만든다."""

    if (
        not python_source_path.is_absolute()
        or not marker_path.is_absolute()
        or _pinned_fd_number(str(python_pinned_fd_path)) is None
        or _pinned_fd_number(str(script_pinned_fd_path)) is None
    ):
        raise WorkflowError("QUALIFICATION_MARKER_COMMAND_INVALID")
    return [
        "/usr/bin/env",
        "-a",
        str(python_source_path),
        str(python_pinned_fd_path),
        str(script_pinned_fd_path),
        "mark-measurement-entered",
        "--qualification",
        str(marker_path),
    ]


def build_profile_marker(
    *,
    plan_sha256: str,
    selector_config_sha256: str,
    source_tree_sha256: str,
    order_block: int,
    profile_id: str,
    case_order: Sequence[str],
    host_validity_sha256: str,
    marker_python_path: str,
    marker_python_pinned_fd_path: str,
    marker_python_sha256: str,
    marker_script_path: str,
    marker_script_pinned_fd_path: str,
    marker_script_sha256: str,
    marker_argv: Sequence[str],
    started_at: str,
) -> dict[str, Any]:
    """한 profile sub-block의 exact PRE_RUN marker object를 만든다."""

    _require_sha256(plan_sha256, label="plan")
    _require_sha256(selector_config_sha256, label="selector-config")
    _require_sha256(source_tree_sha256, label="source-tree")
    _require_sha256(host_validity_sha256, label="host-validity")
    _require_sha256(marker_python_sha256, label="marker-python")
    _require_sha256(marker_script_sha256, label="marker-script")
    _require_iso_utc(started_at, label="marker-started")
    options = profile_options(profile_id)
    if (
        type(order_block) is not int
        or order_block not in range(4)
        or len(case_order) != 7
        or len(set(case_order)) != 7
        or any(type(case_id) is not str or not case_id for case_id in case_order)
        or not marker_python_path.startswith("/")
        or not marker_script_path.startswith("/")
        or _pinned_fd_number(marker_python_pinned_fd_path) is None
        or _pinned_fd_number(marker_script_pinned_fd_path) is None
        or not marker_argv
        or any(type(argument) is not str or not argument for argument in marker_argv)
    ):
        raise WorkflowError("PROFILE_MARKER_INPUT_INVALID")
    marker_argv_list = list(marker_argv)
    return {
        "schemaVersion": PROFILE_MARKER_SCHEMA_VERSION,
        "state": "PRE_RUN",
        "planSha256": plan_sha256,
        "selectorConfigSha256": selector_config_sha256,
        "sourceTreeSha256": source_tree_sha256,
        "orderBlock": order_block,
        "profileId": profile_id,
        "ghcOptions": list(options),
        "optionsSha256": canonical_sha256(list(options)),
        "qualificationCaseOrder": list(case_order),
        "hostValiditySha256": host_validity_sha256,
        "markerPythonPath": marker_python_path,
        "markerPythonPinnedFdPath": marker_python_pinned_fd_path,
        "markerPythonSha256": marker_python_sha256,
        "markerScriptPath": marker_script_path,
        "markerScriptPinnedFdPath": marker_script_pinned_fd_path,
        "markerScriptSha256": marker_script_sha256,
        "markerArgv": marker_argv_list,
        "markerArgvSha256": canonical_sha256(marker_argv_list),
        "startedAt": started_at,
        "measurementEnteredAt": None,
        "preRunSha256": None,
    }


def _validate_profile_marker(document: object, *, state: str) -> dict[str, Any]:
    if (
        not isinstance(document, dict)
        or set(document) != PROFILE_MARKER_FIELDS
        or document.get("schemaVersion") != PROFILE_MARKER_SCHEMA_VERSION
        or document.get("state") != state
    ):
        raise WorkflowError("PROFILE_MARKER_EXACT_OBJECT_INVALID")
    profile_id = document.get("profileId")
    options = profile_options(profile_id)
    if (
        document.get("ghcOptions") != list(options)
        or document.get("optionsSha256") != canonical_sha256(list(options))
        or type(document.get("orderBlock")) is not int
        or document["orderBlock"] not in range(4)
        or not isinstance(document.get("qualificationCaseOrder"), list)
        or len(document["qualificationCaseOrder"]) != 7
        or len(set(document["qualificationCaseOrder"])) != 7
        or any(
            type(case_id) is not str or not case_id
            for case_id in document["qualificationCaseOrder"]
        )
    ):
        raise WorkflowError("PROFILE_MARKER_CONTRACT_INVALID")
    for field in (
        "planSha256",
        "selectorConfigSha256",
        "sourceTreeSha256",
        "hostValiditySha256",
        "markerPythonSha256",
        "markerScriptSha256",
        "markerArgvSha256",
    ):
        _require_sha256(document.get(field), label=f"marker-{field}")
    marker_argv = document.get("markerArgv")
    if (
        not isinstance(marker_argv, list)
        or not marker_argv
        or any(type(argument) is not str or not argument for argument in marker_argv)
        or document["markerArgvSha256"] != canonical_sha256(marker_argv)
        or len(marker_argv) != 8
        or marker_argv[:3]
        != ["/usr/bin/env", "-a", document.get("markerPythonPath")]
        or document.get("markerPythonPinnedFdPath") != marker_argv[3]
        or marker_argv[4] != document.get("markerScriptPinnedFdPath")
        or _pinned_fd_number(marker_argv[3]) is None
        or _pinned_fd_number(marker_argv[4]) is None
        or marker_argv[5:] != [
            "mark-measurement-entered",
            "--qualification",
            marker_argv[7],
        ]
        or not marker_argv[7].startswith("/")
    ):
        raise WorkflowError("PROFILE_MARKER_ARGV_INVALID")
    _require_iso_utc(document.get("startedAt"), label="marker-started")
    if state == "PRE_RUN":
        if (
            document.get("measurementEnteredAt") is not None
            or document.get("preRunSha256") is not None
        ):
            raise WorkflowError("PROFILE_MARKER_NOT_PRE_RUN")
    elif state == "MEASUREMENT":
        _require_iso_utc(
            document.get("measurementEnteredAt"),
            label="measurement-entered",
        )
        _require_sha256(document.get("preRunSha256"), label="pre-run")
    else:
        raise WorkflowError("PROFILE_MARKER_STATE_INVALID")
    return document


def _same_fd_json_snapshot(path: Path) -> tuple[bytes, dict[str, Any], os.stat_result]:
    """O_NOFOLLOW FD 하나에서 bytes/hash/parse에 쓰는 동일 snapshot을 읽는다."""

    payload, document, snapshot = _same_fd_json_value(
        path,
        label="PROFILE_MARKER",
        max_bytes=1024 * 1024,
    )
    if not isinstance(document, dict):
        raise WorkflowError("PROFILE_MARKER_JSON_INVALID")
    return payload, document, snapshot


def _same_fd_bytes_snapshot(
    path: Path,
    *,
    label: str,
    max_bytes: int,
) -> tuple[bytes, os.stat_result]:
    """Regular file 하나의 bytes와 identity를 같은 O_NOFOLLOW FD에서 읽는다."""

    if (
        type(label) is not str
        or re.fullmatch(r"[A-Z][A-Z0-9_]*", label) is None
        or type(max_bytes) is not int
        or max_bytes <= 0
        or not path.is_absolute()
    ):
        raise WorkflowError(f"{label}_PATH_NOT_ABSOLUTE")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise WorkflowError(f"{label}_FILE_INVALID") from exc
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_size > max_bytes
            or before.st_nlink != 1
        ):
            raise WorkflowError(f"{label}_FILE_INVALID")
        chunks: list[bytes] = []
        while chunk := os.read(descriptor, 64 * 1024):
            chunks.append(chunk)
        after = os.fstat(descriptor)
        current = os.stat(path, follow_symlinks=False)
        identity_fields = (
            "st_dev",
            "st_ino",
            "st_size",
            "st_mode",
            "st_mtime_ns",
            "st_ctime_ns",
            "st_nlink",
        )
        if (
            any(
                getattr(before, field) != getattr(after, field)
                or getattr(before, field) != getattr(current, field)
                for field in identity_fields
            )
        ):
            raise WorkflowError(f"{label}_CHANGED_DURING_READ")
    finally:
        os.close(descriptor)
    return b"".join(chunks), before


def _same_fd_json_value(
    path: Path,
    *,
    label: str,
    max_bytes: int = 64 * 1024 * 1024,
) -> tuple[bytes, Any, os.stat_result]:
    payload, snapshot = _same_fd_bytes_snapshot(
        path,
        label=label,
        max_bytes=max_bytes,
    )

    def object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise WorkflowError(f"{label}_DUPLICATE_KEY:{key}")
            result[key] = value
        return result

    try:
        document = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=object_pairs,
            parse_constant=lambda token: (_ for _ in ()).throw(
                WorkflowError(f"{label}_NONFINITE:{token}")
            ),
        )
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise WorkflowError(f"{label}_JSON_INVALID") from exc
    return payload, document, snapshot


def _same_fd_logged_json_snapshot(
    path: Path,
    *,
    label: str,
    command_record: Mapping[str, Any],
) -> tuple[Any, str]:
    """한 command stdout FD의 bytes를 parse/hash 양쪽에 동일하게 결속한다."""

    payload, document, _ = _same_fd_json_value(
        path,
        label=label,
        max_bytes=64 * 1024 * 1024,
    )
    digest = hashlib.sha256(payload).hexdigest()
    if (
        command_record.get("stdoutPath") != str(path)
        or command_record.get("stdoutSha256") != digest
    ):
        raise WorkflowError(f"{label}_COMMAND_LOG_BINDING_INVALID")
    return document, digest


def read_same_fd_json_evidence(
    path: Path,
    *,
    expected_sha256: str,
    label: str,
    max_bytes: int = 64 * 1024 * 1024,
) -> Any:
    """Raw JSON을 한 FD에서 읽고 그 동일 bytes의 expected SHA를 검증한다."""

    expected = _require_sha256(expected_sha256, label=f"{label}-expected")
    payload, document, _ = _same_fd_json_value(
        path,
        label=label,
        max_bytes=max_bytes,
    )
    if hashlib.sha256(payload).hexdigest() != expected:
        raise WorkflowError(f"{label}_SHA256_DRIFT")
    return document


def profile_marker_pre_run_sha256(measurement: object) -> str:
    """MEASUREMENT marker가 전이되기 전 canonical PRE_RUN bytes hash를 복원한다."""

    document = _validate_profile_marker(measurement, state="MEASUREMENT")
    pre_run = dict(document)
    expected = _require_sha256(
        pre_run["preRunSha256"],
        label="marker-pre-run",
    )
    pre_run["state"] = "PRE_RUN"
    pre_run["measurementEnteredAt"] = None
    pre_run["preRunSha256"] = None
    _validate_profile_marker(pre_run, state="PRE_RUN")
    recomputed = hashlib.sha256(
        canonical_json_bytes(pre_run, trailing_newline=True)
    ).hexdigest()
    if recomputed != expected:
        raise WorkflowError("PROFILE_MARKER_PRE_RUN_SHA256_DRIFT")
    return recomputed


def mark_profile_measurement_entered(path: Path) -> dict[str, str]:
    """Exclusive lock 아래 exact PRE_RUN snapshot을 MEASUREMENT로 한 번만 전이한다."""

    lock_path = path.with_name(f"{path.name}.transition.lock")
    try:
        lock_descriptor = os.open(
            lock_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
    except FileExistsError as exc:
        raise WorkflowError("PROFILE_MARKER_TRANSITION_BUSY") from exc
    try:
        os.close(lock_descriptor)
        payload, raw_document, snapshot = _same_fd_json_snapshot(path)
        try:
            document = _validate_profile_marker(raw_document, state="PRE_RUN")
        except WorkflowError as exc:
            if isinstance(raw_document, dict) and raw_document.get("state") != "PRE_RUN":
                raise WorkflowError("PROFILE_MARKER_NOT_PRE_RUN") from exc
            raise
        current = os.stat(path, follow_symlinks=False)
        if (
            not stat.S_ISREG(current.st_mode)
            or current.st_dev != snapshot.st_dev
            or current.st_ino != snapshot.st_ino
            or current.st_size != snapshot.st_size
            or current.st_mtime_ns != snapshot.st_mtime_ns
        ):
            raise WorkflowError("PROFILE_MARKER_CHANGED_BEFORE_TRANSITION")
        pre_run_sha256 = hashlib.sha256(payload).hexdigest()
        transitioned = dict(document)
        transitioned["state"] = "MEASUREMENT"
        transitioned["measurementEnteredAt"] = _iso_now()
        transitioned["preRunSha256"] = pre_run_sha256
        _validate_profile_marker(transitioned, state="MEASUREMENT")
        atomic_replace_json(path, transitioned)
        return {
            "preRunSha256": pre_run_sha256,
            "measurementSha256": sha256_file(path),
            "status": "MEASUREMENT_ENTERED",
        }
    finally:
        lock_path.unlink(missing_ok=True)


def parse_criterion_qualification_reports(
    reports: object,
    *,
    expected_case_order: Sequence[str],
) -> dict[str, float]:
    """Criterion raw mean seconds를 exact 7-case order로 추출한다."""

    if (
        not isinstance(reports, list)
        or len(expected_case_order) != 7
        or len(set(expected_case_order)) != 7
    ):
        raise WorkflowError("CRITERION_QUALIFICATION_REPORT_SET_INVALID")
    parsed: dict[str, float] = {}
    for report in reports:
        if not isinstance(report, dict):
            raise WorkflowError("CRITERION_QUALIFICATION_REPORT_INVALID")
        name = report.get("reportName")
        analysis = report.get("reportAnalysis")
        mean = analysis.get("anMean") if isinstance(analysis, dict) else None
        estimate = mean.get("estPoint") if isinstance(mean, dict) else None
        if (
            type(name) is not str
            or name not in expected_case_order
            or name in parsed
            or type(estimate) is not float
            or not math.isfinite(estimate)
            or estimate <= 0.0
        ):
            raise WorkflowError("CRITERION_QUALIFICATION_REPORT_INVALID")
        parsed[name] = estimate
    if tuple(parsed) != tuple(expected_case_order):
        raise WorkflowError("CRITERION_QUALIFICATION_CASE_ORDER_INVALID")
    return parsed


def recompute_qualification_ratios(
    raw_reports_by_profile: Mapping[str, object],
    *,
    expected_case_order: Sequence[str],
) -> tuple[dict[str, float], dict[str, dict[str, float]]]:
    """두 raw Criterion report의 exact 7-case mean에서 비율을 다시 계산한다."""

    if set(raw_reports_by_profile) != set(PROFILE_OPTIONS):
        raise WorkflowError("QUALIFICATION_RAW_PROFILE_SET_INVALID")
    estimates = {
        profile_id: parse_criterion_qualification_reports(
            raw_reports_by_profile[profile_id],
            expected_case_order=expected_case_order,
        )
        for profile_id in PROFILE_OPTIONS
    }
    ratios = {
        case_id: (
            estimates["optimized-o2-fasm"][case_id]
            / estimates["baseline-o0-fasm"][case_id]
        )
        for case_id in expected_case_order
    }
    if any(not math.isfinite(value) or value <= 0.0 for value in ratios.values()):
        raise WorkflowError("QUALIFICATION_RAW_RATIO_INVALID")
    return ratios, estimates


def _geometric_mean(values: Sequence[float]) -> float:
    if not values or any(
        type(value) is not float or not math.isfinite(value) or value <= 0.0
        for value in values
    ):
        raise WorkflowError("PROFILE_RATIO_INVALID")
    return math.exp(math.fsum(math.log(value) for value in values) / len(values))


def select_profile_from_blocks(
    blocks: object,
    *,
    case_order: Sequence[str],
    profile_order_blocks: Sequence[Sequence[str]],
) -> dict[str, Any]:
    """Frozen four-block paired ratio selector를 exact order closure에서 재계산한다."""

    if (
        not isinstance(blocks, list)
        or len(blocks) != 4
        or tuple(tuple(block) for block in profile_order_blocks)
        != PROFILE_ORDER_BLOCKS
        or len(case_order) != 7
        or len(set(case_order)) != 7
    ):
        raise WorkflowError("PROFILE_QUALIFICATION_BLOCK_SET_INVALID")
    paired: list[float] = []
    per_case: dict[str, list[float]] = {case_id: [] for case_id in case_order}
    improving = 0
    for index, block in enumerate(blocks):
        if (
            not isinstance(block, dict)
            or set(block)
            != {
                "orderBlock",
                "plannedProfileOrder",
                "actualProfileOrder",
                "ratios",
            }
            or type(block["orderBlock"]) is not int
            or block["orderBlock"] != index
            or block["plannedProfileOrder"] != list(PROFILE_ORDER_BLOCKS[index])
            or block["actualProfileOrder"] != list(PROFILE_ORDER_BLOCKS[index])
            or not isinstance(block["ratios"], dict)
            or set(block["ratios"]) != set(case_order)
        ):
            raise WorkflowError("PROFILE_QUALIFICATION_ORDER_CLOSURE_INVALID")
        block_ratios: list[float] = []
        for case_id in case_order:
            ratio = block["ratios"][case_id]
            if (
                type(ratio) is not float
                or not math.isfinite(ratio)
                or ratio <= 0.0
            ):
                raise WorkflowError("PROFILE_RATIO_INVALID")
            paired.append(ratio)
            block_ratios.append(ratio)
            per_case[case_id].append(ratio)
        if _geometric_mean(block_ratios) < 1.0:
            improving += 1
    maxima = {case_id: max(values) for case_id, values in per_case.items()}
    aggregate = _geometric_mean(paired)
    optimized = (
        all(value <= 1.05 for value in maxima.values())
        and aggregate <= 0.97
        and improving >= 3
    )
    return {
        "profileId": (
            "optimized-o2-fasm" if optimized else "baseline-o0-fasm"
        ),
        "selectedBy": (
            "frozen-criterion-selector" if optimized else "proven-fallback"
        ),
        "pairedRatios": paired,
        "perCaseMaxima": maxima,
        "aggregateRatio": aggregate,
        "improvingOuterRepetitions": improving,
    }


def build_final_profile_document(
    *,
    selection: Mapping[str, Any],
    source_tree_sha256: str,
    full_correctness_sha256: str,
    qualification_plan_sha256: str,
    qualification_artifact_sha256: str,
    selector_config_sha256: str,
    compiler_sha256: str,
) -> dict[str, Any]:
    """Frozen selector와 선택된 correctness receipt를 final profile로 투영한다."""

    profile_id = selection.get("profileId")
    selected_by = selection.get("selectedBy")
    if selected_by not in {"frozen-criterion-selector", "proven-fallback"}:
        raise WorkflowError("PROFILE_SELECTION_IDENTITY_INVALID")
    options = profile_options(profile_id)
    for label, digest in (
        ("source-tree", source_tree_sha256),
        ("full-correctness", full_correctness_sha256),
        ("qualification-plan", qualification_plan_sha256),
        ("qualification-artifact", qualification_artifact_sha256),
        ("selector-config", selector_config_sha256),
        ("compiler", compiler_sha256),
    ):
        _require_sha256(digest, label=label)
    return {
        "schemaVersion": FINAL_PROFILE_SCHEMA_VERSION,
        "profileId": profile_id,
        "ghcOptions": list(options),
        "compilerVersion": "9.10.3",
        "compilerSha256": compiler_sha256,
        "sourceTreeSha256": source_tree_sha256,
        "optionsSha256": canonical_sha256(list(options)),
        "fullCorrectnessSha256": full_correctness_sha256,
        "qualificationPlanSha256": qualification_plan_sha256,
        "qualificationArtifactSha256": qualification_artifact_sha256,
        "selectorConfigSha256": selector_config_sha256,
        "fallbackProfile": "baseline-o0-fasm",
        "selectedBy": selected_by,
    }


def current_compatibility_lock_hashes(haskell_root: Path) -> dict[str, str]:
    """두 Stack lock을 각각의 regular-file bytes에서 독립적으로 계산한다."""

    return {
        "authoritativeStackLockSha256": sha256_file(
            haskell_root / "stack.yaml.lock"
        ),
        "compatibilityStackLockSha256": sha256_file(
            haskell_root / "stack-ghc-9.14.1.yaml.lock"
        ),
    }


def _compatibility_result_base(
    *,
    candidate_source_tree_sha256: str,
    command_records: Sequence[Mapping[str, Any]],
    current_plan: Mapping[str, Any],
    haskell_root: Path,
) -> dict[str, Any]:
    """현재 frozen inputs에서 typed compatibility result 공통 필드를 만든다."""

    _require_sha256(candidate_source_tree_sha256, label="compatibility-source-tree")
    plan_sha_fields = (
        "authoritativeBootSetSha256",
        "compatibilityBootSetSha256",
        "authoritativeNonBootPlanSha256",
        "compatibilityNonBootPlanSha256",
        "authoritativePackageSetSha256",
        "configurationAstSha256",
    )
    for field in plan_sha_fields:
        _require_sha256(current_plan.get(field), label=f"current-plan-{field}")
    if (
        current_plan["authoritativeNonBootPlanSha256"]
        != current_plan["compatibilityNonBootPlanSha256"]
    ):
        raise WorkflowError("CURRENT_NON_BOOT_PLAN_MISMATCH")
    commands = [dict(record) for record in command_records]
    if not commands:
        raise WorkflowError("COMPATIBILITY_COMMANDS_EMPTY")
    toolchain_lock_path = _absolute_regular(
        haskell_root / "toolchain-lock.v1.json",
        label="HASKELL_TOOLCHAIN_LOCK",
    )
    toolchain_lock = strict_json_load(toolchain_lock_path)
    projection = toolchain_lock.get("contractProjection")
    resolved = toolchain_lock.get("resolvedTools")
    if not isinstance(projection, dict) or not isinstance(resolved, dict):
        raise WorkflowError("HASKELL_TOOLCHAIN_LOCK_INVALID")
    compatibility_ghc = resolved.get("compatibilityGhc")
    stack_tool = resolved.get("stack")
    ghcup_tool = resolved.get("ghcup")
    if any(
        not isinstance(tool, dict)
        for tool in (compatibility_ghc, stack_tool, ghcup_tool)
    ):
        raise WorkflowError("HASKELL_TOOLCHAIN_RESOLUTION_INVALID")
    merged_path = _absolute_regular(
        haskell_root.parent / "contract/toolchain-provenance.v1.json",
        label="MERGED_TOOLCHAIN_PROVENANCE",
    )
    merged_sha256 = sha256_file(merged_path)
    if merged_sha256 != toolchain_lock["mergedToolchainProvenance"]["sha256"]:
        raise WorkflowError("MERGED_TOOLCHAIN_PROVENANCE_DRIFT")
    lock_hashes = current_compatibility_lock_hashes(haskell_root)
    return {
        "authoritativeBootSetSha256": current_plan[
            "authoritativeBootSetSha256"
        ],
        "authoritativeNonBootPlanSha256": current_plan[
            "authoritativeNonBootPlanSha256"
        ],
        "authoritativePackageSetSha256": current_plan[
            "authoritativePackageSetSha256"
        ],
        **lock_hashes,
        "authoritativeStackYamlSha256": sha256_file(haskell_root / "stack.yaml"),
        "candidateSourceTreeSha256": candidate_source_tree_sha256,
        "commands": commands,
        "compatibilityBootSetSha256": current_plan[
            "compatibilityBootSetSha256"
        ],
        "compatibilityNonBootPlanSha256": current_plan[
            "compatibilityNonBootPlanSha256"
        ],
        "compatibilityPolicySha256": sha256_file(
            haskell_root.parent / "contract/ghc-compatibility-policy.v1.json"
        ),
        "compatibilityStackYamlSha256": sha256_file(
            haskell_root / "stack-ghc-9.14.1.yaml"
        ),
        "compilerPathId": compatibility_ghc["pathId"],
        "compilerSha256": compatibility_ghc["sha256"],
        "compilerVersion": compatibility_ghc["version"],
        "configurationQualification": {
            "evidenceSha256": current_plan["configurationAstSha256"],
            "status": "PASS",
        },
        "expectedBootSetDifferenceOnly": True,
        "forbiddenOverrideKeysPresent": [],
        "ghcupMetadataCommit": projection["ghcupMetadataCommit"],
        "ghcupMetadataUri": projection["ghcupMetadataUri"],
        "ghcupSha256": ghcup_tool["sha256"],
        "ghcupToolId": ghcup_tool["pathId"],
        "ghcupVersion": ghcup_tool["version"],
        "laneId": "ghc-9.14.1-non-scoring",
        "nonBootPlanEquivalent": True,
        "nonScoring": True,
        "performanceInput": False,
        "schemaVersion": "s1.4x-ghc-compatibility-result-v1",
        "stackArchiveSha256": projection["stackArchiveSha256"],
        "stackArchiveUri": projection["stackArchiveUri"],
        "stackBinPathId": stack_tool["pathId"],
        "stackBinSha256": stack_tool["sha256"],
        "stackDistributionChannel": projection["stackDistributionChannel"],
        "stackInstallCommand": projection["stackInstallCommand"],
        "stackNumericVersion": stack_tool["version"],
        "stackPolicy": projection["stackPolicy"],
        "toolchainProvenanceSha256": merged_sha256,
        "toolchainQualification": {
            "evidenceSha256": canonical_sha256(resolved),
            "status": "PASS",
        },
        "upstreamStandaloneAssetRole": projection[
            "upstreamStandaloneAssetRole"
        ],
        "upstreamStandaloneAssetSha256": projection[
            "upstreamStandaloneAssetSha256"
        ],
    }


def build_current_compatibility_pass_result(
    *,
    candidate_source_tree_sha256: str,
    command_records: Sequence[Mapping[str, Any]],
    phase_evidence_sha256: Mapping[str, str],
    current_plan: Mapping[str, Any],
    haskell_root: Path,
) -> dict[str, Any]:
    """Solve 성공 뒤 여섯 downstream 단계가 모두 PASS인 typed result를 만든다."""

    expected_phases = {
        "dependency",
        "candidateCompile",
        "fullCorrectness",
        "stableErrorReplay",
        "processReplay",
        "oracleReplay",
        "crossReplay",
    }
    if set(phase_evidence_sha256) != expected_phases:
        raise WorkflowError("COMPATIBILITY_PASS_PHASE_SET_INVALID")
    for phase, digest in phase_evidence_sha256.items():
        _require_sha256(digest, label=f"compatibility-{phase}")
    result = _compatibility_result_base(
        candidate_source_tree_sha256=candidate_source_tree_sha256,
        command_records=command_records,
        current_plan=current_plan,
        haskell_root=haskell_root,
    )
    result.update(
        {
            "candidateCompile": {
                "evidenceSha256": phase_evidence_sha256["candidateCompile"],
                "status": "PASS",
            },
            "crossReplay": {
                "evidenceSha256": phase_evidence_sha256["crossReplay"],
                "mismatchCount": 0,
                "status": "PASS",
            },
            "dependencyQualification": {
                "evidenceSha256": phase_evidence_sha256["dependency"],
                "status": "PASS",
            },
            "downstreamNotRun": [],
            "failurePhase": None,
            "fullCorrectness": {
                "evidenceSha256": phase_evidence_sha256["fullCorrectness"],
                "mismatchCount": 0,
                "status": "PASS",
            },
            "minimalReproducerSha256": None,
            "oracleReplay": {
                "evidenceSha256": phase_evidence_sha256["oracleReplay"],
                "mismatchCount": 0,
                "status": "PASS",
            },
            "processReplay": {
                "evidenceSha256": phase_evidence_sha256["processReplay"],
                "mismatchCount": 0,
                "status": "PASS",
            },
            "result": "PASS",
            "stableErrorReplay": {
                "evidenceSha256": phase_evidence_sha256[
                    "stableErrorReplay"
                ],
                "mismatchCount": 0,
                "status": "PASS",
            },
        }
    )
    return result


def validate_current_compatibility_status(
    result: object,
    *,
    expected_source_tree_sha256: str,
) -> dict[str, Any]:
    """현재 replay가 허용된 non-scoring frozen dependency leaf인지 분류한다."""

    _require_sha256(expected_source_tree_sha256, label="current-source-tree")
    expected_downstream = [
        "candidateCompile",
        "fullCorrectness",
        "stableErrorReplay",
        "processReplay",
        "oracleReplay",
        "crossReplay",
    ]
    common_invalid = (
        not isinstance(result, dict)
        or result.get("schemaVersion")
        != "s1.4x-ghc-compatibility-result-v1"
        or result.get("nonScoring") is not True
        or result.get("performanceInput") is not False
        or result.get("expectedBootSetDifferenceOnly") is not True
        or result.get("nonBootPlanEquivalent") is not True
        or result.get("forbiddenOverrideKeysPresent") != []
        or result.get("candidateSourceTreeSha256")
        != expected_source_tree_sha256
    )
    if common_invalid:
        raise WorkflowError("CURRENT_COMPATIBILITY_STATUS_INVALID")
    if result.get("result") == "FAIL_FROZEN_DEPENDENCY":
        if (
            result.get("failurePhase") != "dependency"
            or result.get("downstreamNotRun") != expected_downstream
            or not isinstance(result.get("dependencyQualification"), dict)
            or result["dependencyQualification"].get("status") != "FAIL"
        ):
            raise WorkflowError("CURRENT_COMPATIBILITY_STATUS_INVALID")
        _require_sha256(
            result["dependencyQualification"].get("evidenceSha256"),
            label="current-compatibility-evidence",
        )
    elif result.get("result") == "PASS":
        if (
            result.get("failurePhase") is not None
            or result.get("downstreamNotRun") != []
            or result.get("minimalReproducerSha256") is not None
            or any(
                not isinstance(result.get(phase), dict)
                or result[phase].get("status") != "PASS"
                or (
                    phase != "candidateCompile"
                    and result[phase].get("mismatchCount") != 0
                )
                for phase in (
                    "candidateCompile",
                    "fullCorrectness",
                    "stableErrorReplay",
                    "processReplay",
                    "oracleReplay",
                    "crossReplay",
                )
            )
            or not isinstance(result.get("dependencyQualification"), dict)
            or result["dependencyQualification"].get("status") != "PASS"
        ):
            raise WorkflowError("CURRENT_COMPATIBILITY_STATUS_INVALID")
    elif result.get("result") == "FAIL_CANDIDATE_SOURCE":
        ordered = list(COMPATIBILITY_REPLAY_PHASES[1:])
        failed_phase = result.get("failurePhase")
        if (
            failed_phase not in ordered
            or result.get("downstreamNotRun")
            != ordered[ordered.index(failed_phase) + 1 :]
            or result.get("minimalReproducerSha256") is None
            or not isinstance(result.get("dependencyQualification"), dict)
            or result["dependencyQualification"].get("status") != "PASS"
        ):
            raise WorkflowError("CURRENT_COMPATIBILITY_STATUS_INVALID")
        _require_sha256(
            result["minimalReproducerSha256"],
            label="current-candidate-failure",
        )
        for index, phase in enumerate(ordered):
            phase_result = result.get(phase)
            expected_status = (
                "PASS"
                if index < ordered.index(failed_phase)
                else "FAIL"
                if phase == failed_phase
                else "NOT_RUN"
            )
            if (
                not isinstance(phase_result, dict)
                or phase_result.get("status") != expected_status
            ):
                raise WorkflowError("CURRENT_COMPATIBILITY_STATUS_INVALID")
    else:
        raise WorkflowError("CURRENT_COMPATIBILITY_STATUS_INVALID")
    return result


def build_oci_build_command(
    *,
    docker: Path,
    containerfile: Path,
    context: Path,
    iidfile: Path,
    image_tag: str,
    binary_sha256: str,
    provenance_labels: Mapping[str, str],
) -> list[str]:
    """Digest-pinned context를 network-disabled BuildKit command로 조립한다."""

    _require_sha256(binary_sha256, label="oci-binary")
    if (
        re.fullmatch(r"local/s1-4x-haskell:[a-z0-9._-]+", image_tag) is None
        or not containerfile.is_absolute()
        or not context.is_absolute()
        or not iidfile.is_absolute()
        or set(provenance_labels) != OCI_PROVENANCE_LABEL_KEYS
        or re.fullmatch(
            r"sha256:[0-9a-f]{64}",
            str(provenance_labels.get("io.s1-4x.base-image-id")),
        )
        is None
        or any(
            SHA256_PATTERN.fullmatch(str(provenance_labels.get(key))) is None
            for key in (
                "io.s1-4x.containerfile-sha256",
                "io.s1-4x.fixture-tree-sha256",
            )
        )
    ):
        raise WorkflowError("OCI_BUILD_INPUT_INVALID")
    command = [
        str(docker),
        "build",
        "--platform",
        OCI_PLATFORM,
        "--network",
        "none",
        "--pull=false",
        "--file",
        str(containerfile),
        "--build-arg",
        f"S1_4X_BINARY_SHA256={binary_sha256}",
    ]
    for key, value in sorted(provenance_labels.items()):
        command.extend(["--label", f"{key}={value}"])
    command.extend(
        [
            "--iidfile",
            str(iidfile),
            "--tag",
            image_tag,
            str(context),
        ]
    )
    return command


def build_oci_run_command(
    *,
    docker: Path,
    image_id: str,
    output_directory: Path,
    output_name: str,
    request_path: str,
    uid: int,
    gid: int,
) -> list[str]:
    """Source, user directory, credential mount 없이 offline replay command를 만든다."""

    if (
        re.fullmatch(r"sha256:[0-9a-f]{64}", image_id) is None
        or not output_directory.is_absolute()
        or re.fullmatch(r"[a-z0-9._-]+\.json", output_name) is None
        or not request_path.startswith("/opt/s1-4x/fixtures/")
        or type(uid) is not int
        or type(gid) is not int
        or uid <= 0
        or gid <= 0
    ):
        raise WorkflowError("OCI_RUN_INPUT_INVALID")
    return [
        str(docker),
        "run",
        "--platform",
        OCI_PLATFORM,
        "--rm",
        "--network",
        "none",
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--pids-limit",
        "64",
        "--memory",
        "1g",
        "--cpus",
        "1",
        "--user",
        f"{uid}:{gid}",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,nodev,size=16m",
        "--mount",
        f"type=bind,src={output_directory},dst=/out",
        image_id,
        "--request",
        request_path,
        "--fixture-root",
        "/opt/s1-4x/fixtures",
        "--output",
        f"/out/{output_name}",
    ]


def build_oci_context_show_command(docker: Path) -> list[str]:
    """`docker context show`의 활성 endpoint identity command를 고정한다."""

    return [str(docker), "context", "show"]


def validate_oci_daemon_identity(
    document: object,
    *,
    context_name: str,
) -> dict[str, str]:
    """Docker daemon이 동일한 Linux amd64 endpoint인지 portable subset으로 고정한다."""

    if (
        not isinstance(document, dict)
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", context_name)
        is None
        or document.get("OSType") != "linux"
        or document.get("Architecture") not in {"amd64", "x86_64"}
        or any(
            type(document.get(field)) is not str or not document[field]
            for field in ("ID", "ServerVersion", "OperatingSystem")
        )
    ):
        raise WorkflowError("OCI_DAEMON_IDENTITY_INVALID")
    identity = {
        "contextName": context_name,
        "daemonId": document["ID"],
        "serverVersion": document["ServerVersion"],
        "operatingSystem": document["OperatingSystem"],
        "osType": "linux",
        "architecture": "amd64",
        "platform": OCI_PLATFORM,
    }
    return identity


def validate_oci_daemon_identity_pair(
    before: object,
    after: object,
) -> str:
    """Runtime-derived daemon identity의 exact before/after 동등성과 SHA를 반환한다."""

    expected_fields = {
        "contextName",
        "daemonId",
        "serverVersion",
        "operatingSystem",
        "osType",
        "architecture",
        "platform",
    }
    if any(
        not isinstance(value, dict)
        or set(value) != expected_fields
        or any(type(field) is not str or not field for field in value.values())
        for value in (before, after)
    ):
        raise WorkflowError("OCI_DAEMON_IDENTITY_PAIR_INVALID")
    if before != after:
        raise WorkflowError("OCI_DAEMON_CHANGED_DURING_RUN")
    return canonical_sha256(before)


def validate_oci_base_image_inspection(
    document: object,
    *,
    expected_reference: str,
) -> str:
    """Network-disabled build가 사용할 local digest와 immutable image ID를 결속한다."""

    expected_digest = OCI_BASE_IMAGE.rsplit("@", 1)[1]
    allowed_names = {
        "haskell",
        "library/haskell",
        "docker.io/library/haskell",
        "index.docker.io/library/haskell",
    }
    repository_digests = (
        document.get("RepoDigests") if isinstance(document, dict) else None
    )
    digest_bound = (
        isinstance(repository_digests, list)
        and all(type(value) is str for value in repository_digests)
        and any(
            value.rpartition("@")[0] in allowed_names
            and value.rpartition("@")[2] == expected_digest
            for value in repository_digests
        )
    )
    if (
        expected_reference != OCI_BASE_IMAGE
        or not isinstance(document, dict)
        or re.fullmatch(r"sha256:[0-9a-f]{64}", str(document.get("Id")))
        is None
        or not digest_bound
        or document.get("Os") != "linux"
        or document.get("Architecture") != "amd64"
    ):
        raise WorkflowError("OCI_BASE_IMAGE_INSPECTION_INVALID")
    return document["Id"]


def validate_oci_iid_bytes(payload: bytes) -> str:
    """Docker CLI iidfile의 공백 없는 exact lowercase digest bytes를 검증한다."""

    try:
        value = payload.decode("ascii")
    except UnicodeError as exc:
        raise WorkflowError("OCI_IIDFILE_INVALID") from exc
    if re.fullmatch(r"sha256:[0-9a-f]{64}", value) is None:
        raise WorkflowError("OCI_IIDFILE_INVALID")
    return value


def validate_oci_image_inspection(
    document: object,
    *,
    image_tag: str,
    expected_image_id: str | None,
    expected_labels: Mapping[str, str] | None = None,
) -> str:
    """Tag inspection이 최초 immutable image ID를 계속 가리키는지 검증한다."""

    if (
        re.fullmatch(r"local/s1-4x-haskell:[a-z0-9._-]+", image_tag) is None
        or not isinstance(document, dict)
    ):
        raise WorkflowError("OCI_IMAGE_INSPECTION_INVALID")
    image_id = document.get("Id")
    repository_tags = document.get("RepoTags")
    labels = (
        document.get("Config", {}).get("Labels")
        if isinstance(document.get("Config"), dict)
        else None
    )
    if (
        type(image_id) is not str
        or re.fullmatch(r"sha256:[0-9a-f]{64}", image_id) is None
        or not isinstance(repository_tags, list)
        or any(type(tag) is not str for tag in repository_tags)
        or image_tag not in repository_tags
        or document.get("Os") != "linux"
        or document.get("Architecture") != "amd64"
        or (
            expected_image_id is not None
            and (
                re.fullmatch(r"sha256:[0-9a-f]{64}", expected_image_id) is None
                or image_id != expected_image_id
            )
        )
        or (
            expected_labels is not None
            and (
                set(expected_labels) != OCI_PROVENANCE_LABEL_KEYS
                or not isinstance(labels, dict)
                or any(
                    labels.get(key) != value
                    for key, value in expected_labels.items()
                )
            )
        )
    ):
        raise WorkflowError("OCI_IMAGE_TAG_BINDING_INVALID")
    return image_id


def _iso_now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _absolute_regular(path: Path, *, label: str, executable: bool = False) -> Path:
    if (
        not path.is_absolute()
        or path.is_symlink()
        or not path.is_file()
        or path.resolve(strict=True) != path
        or (executable and not os.access(path, os.X_OK))
    ):
        raise WorkflowError(f"{label}_IDENTITY_INVALID")
    return path


def _absolute_existing_directory(path: Path, *, label: str) -> Path:
    if (
        not path.is_absolute()
        or path.is_symlink()
        or not path.is_dir()
        or path.resolve(strict=True) != path
    ):
        raise WorkflowError(f"{label}_DIRECTORY_INVALID")
    return path


def _reserve_directory(path: Path) -> Path:
    if not path.is_absolute():
        raise WorkflowError("OUTPUT_DIRECTORY_NOT_ABSOLUTE")
    parent = _absolute_existing_directory(path.parent, label="OUTPUT_PARENT")
    normalized = parent / path.name
    if normalized != path or path.exists() or path.is_symlink():
        raise WorkflowError("OUTPUT_DIRECTORY_NOT_NEW")
    path.mkdir(mode=0o700)
    return path


def _required_environment_path(name: str, *, executable: bool = True) -> Path:
    value = os.environ.get(name)
    if value is None:
        raise WorkflowError(f"REQUIRED_ENVIRONMENT_MISSING:{name}")
    return _absolute_regular(Path(value), label=name, executable=executable)


def _load_haskell_evidence(haskell_root: Path):
    module_path = _absolute_regular(
        haskell_root / "tools/haskell_evidence.py",
        label="HASKELL_EVIDENCE",
    )
    specification = importlib.util.spec_from_file_location(
        "s1_4x_haskell_evidence",
        module_path,
    )
    if specification is None or specification.loader is None:
        raise WorkflowError("HASKELL_EVIDENCE_IMPORT_FAILED")
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


def _load_compatibility_evidence(haskell_root: Path):
    module_path = _absolute_regular(
        haskell_root / "tools/compatibility_evidence.py",
        label="COMPATIBILITY_EVIDENCE_HELPER",
    )
    specification = importlib.util.spec_from_file_location(
        "s1_4x_current_compatibility_evidence",
        module_path,
    )
    if specification is None or specification.loader is None:
        raise WorkflowError("COMPATIBILITY_EVIDENCE_IMPORT_FAILED")
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


def _candidate_runtime(arguments: argparse.Namespace) -> None:
    """현재 source/profile/manifest closure를 검증하고 exact runtime profile ID를 낸다."""

    haskell_root = Path(__file__).resolve(strict=True).parent.parent
    numeric_root = haskell_root.parent
    expected_profile = haskell_root / "selected-profile.v1.json"
    expected_manifest = haskell_root / "source-inputs.v1.json"
    expected_plan = numeric_root / "benchmarks/benchmark-plan.v1.json"
    profile_path = _absolute_regular(arguments.profile, label="SELECTED_PROFILE")
    manifest_path = _absolute_regular(
        arguments.source_manifest,
        label="SOURCE_MANIFEST",
    )
    plan_path = _absolute_regular(
        arguments.qualification_plan,
        label="QUALIFICATION_PLAN",
    )
    if (
        profile_path != expected_profile
        or manifest_path != expected_manifest
        or plan_path != expected_plan
    ):
        raise WorkflowError("CANDIDATE_RUNTIME_INPUT_PATH_DRIFT")

    evidence = _load_haskell_evidence(haskell_root)
    plan = strict_json_load(plan_path)
    selector = (
        plan.get("haskellProfileQualification")
        if isinstance(plan, dict)
        else None
    )
    if not isinstance(selector, dict):
        raise WorkflowError("CANDIDATE_RUNTIME_SELECTOR_MISSING")
    document = strict_json_load(profile_path)
    profile_id, _ = runtime_selected_profile(document)
    try:
        source_tree_sha256 = evidence.benchmark_source_tree_sha256(haskell_root)
        evidence.validate_selected_profile_document(
            document,
            expected_compiler_sha256=evidence.AUTHORITATIVE_GHC_SHA256,
            expected_source_tree_sha256=source_tree_sha256,
            expected_qualification_plan_sha256=sha256_file(plan_path),
            expected_selector_config_sha256=canonical_sha256(selector),
        )
        evidence.validate_source_manifest(haskell_root, manifest_path)
    except evidence.EvidenceError as exc:
        raise WorkflowError(f"CANDIDATE_RUNTIME_CLOSURE_INVALID:{exc}") from exc
    if profile_path.read_bytes() != canonical_json_bytes(
        document,
        trailing_newline=True,
    ):
        raise WorkflowError("CANDIDATE_RUNTIME_PROFILE_NOT_CANONICAL")
    print(profile_id)


def _candidate_stack_root(arguments: argparse.Namespace) -> None:
    print(candidate_stack_root(arguments.cache_root, arguments.output))


def _isolated_stack_root(arguments: argparse.Namespace) -> None:
    print(
        isolated_stack_root(
            arguments.cache_root,
            purpose=arguments.purpose,
            output_path=arguments.output,
        )
    )


def _repo_commit(repo_root: Path) -> str:
    completed = subprocess.run(
        ["/usr/bin/git", "-C", str(repo_root), "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    commit = completed.stdout.strip()
    if completed.returncode != 0 or COMMIT_PATTERN.fullmatch(commit) is None:
        raise WorkflowError("CANDIDATE_COMMIT_INVALID")
    dirty = subprocess.run(
        ["/usr/bin/git", "-C", str(repo_root), "status", "--porcelain=v1"],
        check=False,
        capture_output=True,
        text=True,
    )
    if dirty.returncode != 0 or dirty.stdout:
        raise WorkflowError("CANDIDATE_WORKTREE_NOT_CLEAN")
    return commit


def _git_output(
    repo_root: Path,
    *arguments: str,
    text: bool = True,
) -> str | bytes:
    completed = subprocess.run(
        ["/usr/bin/git", "-C", str(repo_root), *arguments],
        check=False,
        capture_output=True,
        text=text,
    )
    if completed.returncode != 0:
        raise WorkflowError("SELECTED_PROFILE_GIT_QUERY_FAILED")
    return completed.stdout


def _strict_json_bytes(payload: bytes, *, label: str) -> Any:
    def object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise WorkflowError(f"{label}_DUPLICATE_KEY")
            result[key] = value
        return result

    try:
        return json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=object_pairs,
            parse_constant=lambda token: (_ for _ in ()).throw(
                WorkflowError(f"{label}_NON_FINITE:{token}")
            ),
        )
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise WorkflowError(f"{label}_INVALID_JSON") from exc


def resolve_selected_profile_commit_fixed_point(
    repo_root: Path,
    *,
    mode: str,
    expected_subject_commit: str,
    profile_relative_path: str,
    manifest_relative_path: str,
) -> dict[str, str | None]:
    """Pending→final 두 파일 commit과 이후 재생성 evidence를 결속한다."""

    root = _absolute_existing_directory(
        repo_root,
        label="SELECTED_PROFILE_REPOSITORY",
    )
    paths = (profile_relative_path, manifest_relative_path)
    if (
        mode not in {"materialize", "check"}
        or COMMIT_PATTERN.fullmatch(expected_subject_commit) is None
        or len(set(paths)) != 2
        or any(
            not path
            or Path(path).is_absolute()
            or ".." in Path(path).parts
            or Path(path).as_posix() != path
            for path in paths
        )
    ):
        raise WorkflowError("SELECTED_PROFILE_COMMIT_INPUT_INVALID")
    current_commit = _repo_commit(root)
    _git_output(
        root,
        "cat-file",
        "-e",
        f"{expected_subject_commit}^{{commit}}",
    )
    subject_profile_bytes = _git_output(
        root,
        "show",
        f"{expected_subject_commit}:{profile_relative_path}",
        text=False,
    )
    assert isinstance(subject_profile_bytes, bytes)
    subject_profile = _strict_json_bytes(
        subject_profile_bytes,
        label="SELECTED_PROFILE_EVIDENCE_SUBJECT",
    )
    if mode == "materialize":
        if (
            current_commit != expected_subject_commit
            or not isinstance(subject_profile, dict)
            or subject_profile.get("schemaVersion")
            != "s1.4x-haskell-selected-profile-pending-v1"
        ):
            raise WorkflowError("SELECTED_PROFILE_MATERIALIZE_SUBJECT_DRIFT")
        return {
            "currentCommit": current_commit,
            "materializationCommit": None,
            "preMaterializationSubjectCommit": expected_subject_commit,
        }

    ancestor = subprocess.run(
        [
            "/usr/bin/git",
            "-C",
            str(root),
            "merge-base",
            "--is-ancestor",
            expected_subject_commit,
            current_commit,
        ],
        check=False,
        capture_output=True,
    )
    if ancestor.returncode != 0:
        raise WorkflowError("SELECTED_PROFILE_SUBJECT_NOT_ANCESTOR")
    current_blobs = {
        path: _git_output(
            root,
            "show",
            f"{current_commit}:{path}",
            text=False,
        )
        for path in paths
    }
    final_profile = _strict_json_bytes(
        current_blobs[profile_relative_path],
        label="SELECTED_PROFILE_MATERIALIZED_HEAD",
    )
    if (
        not isinstance(final_profile, dict)
        or final_profile.get("schemaVersion")
        != "s1.4x-haskell-selected-profile-v1"
    ):
        raise WorkflowError("SELECTED_PROFILE_MATERIALIZED_HEAD_INVALID")
    subject_schema = (
        subject_profile.get("schemaVersion")
        if isinstance(subject_profile, dict)
        else None
    )
    candidate_subjects: list[tuple[str, str]] = []
    expected_paths = set(paths)
    if subject_schema == "s1.4x-haskell-selected-profile-pending-v1":
        ancestry_output = _git_output(
            root,
            "rev-list",
            "--ancestry-path",
            "--parents",
            f"{expected_subject_commit}..{current_commit}",
        )
        assert isinstance(ancestry_output, str)
        candidate_subjects = [
            (fields[0], fields[1])
            for line in ancestry_output.splitlines()
            if len(fields := line.split()) == 2
            and fields[1] == expected_subject_commit
        ]
    elif subject_schema == "s1.4x-haskell-selected-profile-v1":
        subject_blobs = {
            path: _git_output(
                root,
                "show",
                f"{expected_subject_commit}:{path}",
                text=False,
            )
            for path in paths
        }
        if subject_blobs != current_blobs:
            raise WorkflowError("SELECTED_PROFILE_MATERIALIZATION_COMMIT_INVALID")
        history_output = _git_output(
            root,
            "rev-list",
            "--parents",
            expected_subject_commit,
            "--",
            profile_relative_path,
        )
        assert isinstance(history_output, str)
        for line in history_output.splitlines():
            fields = line.split()
            if len(fields) != 2:
                continue
            commit, parent = fields
            parent_profile_bytes = _git_output(
                root,
                "show",
                f"{parent}:{profile_relative_path}",
                text=False,
            )
            assert isinstance(parent_profile_bytes, bytes)
            parent_profile = _strict_json_bytes(
                parent_profile_bytes,
                label="SELECTED_PROFILE_PRE_MATERIALIZATION_SUBJECT",
            )
            if (
                isinstance(parent_profile, dict)
                and parent_profile.get("schemaVersion")
                == "s1.4x-haskell-selected-profile-pending-v1"
            ):
                candidate_subjects.append((commit, parent))
    else:
        raise WorkflowError("SELECTED_PROFILE_EVIDENCE_SUBJECT_INVALID")

    candidates: list[tuple[str, str]] = []
    for commit, parent in candidate_subjects:
        changed_output = _git_output(
            root,
            "diff-tree",
            "--no-commit-id",
            "--name-only",
            "-r",
            parent,
            commit,
        )
        assert isinstance(changed_output, str)
        changed_paths = {path for path in changed_output.splitlines() if path}
        if changed_paths == expected_paths and all(
            _git_output(
                root,
                "show",
                f"{commit}:{path}",
                text=False,
            )
            == current_blobs[path]
            for path in paths
        ):
            candidates.append((commit, parent))
    if len(candidates) != 1:
        raise WorkflowError("SELECTED_PROFILE_MATERIALIZATION_COMMIT_INVALID")
    materialization_commit, pre_materialization_commit = candidates[0]
    return {
        "currentCommit": current_commit,
        "materializationCommit": materialization_commit,
        "preMaterializationSubjectCommit": pre_materialization_commit,
    }


def _sealed_child_environment(
    *,
    ghc_bin: Path,
    stack_bin: Path,
    python_runtime: BenchmarkPythonRuntime,
) -> dict[str, str]:
    """Nested child에 exact interpreter provenance와 inherited FD만 전달한다."""

    home = os.environ.get("HOME")
    if home is None:
        raise WorkflowError("HOME_MISSING")
    home_path = _absolute_existing_directory(Path(home), label="HOME")
    ghcup_prefix = os.environ.get("GHCUP_INSTALL_BASE_PREFIX")
    if ghcup_prefix is None:
        raise WorkflowError("GHCUP_INSTALL_BASE_PREFIX_MISSING")
    ghcup_prefix_path = _absolute_existing_directory(
        Path(ghcup_prefix),
        label="GHCUP_INSTALL_BASE_PREFIX",
    )
    environment = {
        "HOME": str(home_path),
        "GHCUP_INSTALL_BASE_PREFIX": str(ghcup_prefix_path),
        "PATH": (
            f"{ghc_bin.parent}:{stack_bin.parent}:"
            "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
        ),
        "LC_ALL": "C.UTF-8",
        "TZ": "UTC",
        "OMP_NUM_THREADS": "1",
        "OPENBLAS_NUM_THREADS": "1",
        "MKL_NUM_THREADS": "1",
        "NUMEXPR_NUM_THREADS": "1",
        "VECLIB_MAXIMUM_THREADS": "1",
        "BLIS_NUM_THREADS": "1",
        "S1_4X_BENCHMARK_PYTHON_BIN": str(python_runtime.source_path),
        "S1_4X_BENCHMARK_PYTHON_SHA256": python_runtime.sha256,
        "S1_4X_BENCHMARK_PYTHON_PINNED_FD_PATH": str(
            python_runtime.fd_path
        ),
    }
    return environment


def _pass_fd_identities(
    descriptors: Sequence[int],
    *,
    phase: str,
) -> dict[int, tuple[int, int, int, int, int, int]]:
    """Parent가 child 종료까지 소유해야 할 regular FD identity를 fstat한다."""

    identities: dict[int, tuple[int, int, int, int, int, int]] = {}
    for descriptor in descriptors:
        try:
            value = os.fstat(descriptor)
        except OSError as exc:
            raise WorkflowError(f"COMMAND_PASS_FD_NOT_OWNED:{phase}") from exc
        if not stat.S_ISREG(value.st_mode):
            raise WorkflowError(f"COMMAND_PASS_FD_NOT_REGULAR:{phase}")
        identities[descriptor] = (
            value.st_dev,
            value.st_ino,
            value.st_size,
            value.st_mtime_ns,
            value.st_ctime_ns,
            value.st_nlink,
        )
    return identities


def _child_runtime_descriptors(
    environment: Mapping[str, str],
    pass_fds: Sequence[int],
    *,
    phase: str,
) -> tuple[int, ...]:
    """Sealed environment가 광고한 runtime FD를 모든 nested child에 상속한다."""

    descriptors = set(pass_fds)
    runtime_fd_path = environment.get(
        "S1_4X_BENCHMARK_PYTHON_PINNED_FD_PATH"
    )
    if runtime_fd_path is not None:
        matched = re.fullmatch(r"/proc/self/fd/([0-9]+)", runtime_fd_path)
        if matched is None or int(matched.group(1)) < 3:
            raise WorkflowError(f"COMMAND_RUNTIME_FD_INVALID:{phase}")
        descriptors.add(int(matched.group(1)))
    return tuple(sorted(descriptors))


def _benchmark_python_source_route_identity(
    source_value: str,
    fd_value: str,
    *,
    phase: str,
    expected_runtime: BenchmarkPythonRuntime | None = None,
) -> tuple[
    tuple[int, int, int, int, int, int, int],
    tuple[int, int, int, int, int, int, int],
]:
    """argv0 venv route를 최초 runtime closure와 full stat으로 결속한다."""

    matched = re.fullmatch(r"/proc/self/fd/([0-9]+)", fd_value)
    if (
        not source_value.startswith("/")
        or "\0" in source_value
        or "\n" in source_value
        or ":" in source_value
        or "|" in source_value
        or "//" in source_value
        or "/./" in source_value
        or "/../" in source_value
        or source_value.endswith(("/.", "/.."))
        or matched is None
        or int(matched.group(1)) < 3
    ):
        raise WorkflowError(f"COMMAND_RUNTIME_SOURCE_ROUTE_INVALID:{phase}")
    source_path = Path(source_value)
    venv_configuration = source_path.parent.parent / "pyvenv.cfg"
    try:
        descriptor = int(matched.group(1))
        source_before = os.lstat(source_path)
        pinned_before = os.fstat(descriptor)
        configuration_before = os.lstat(venv_configuration)
        if (
            source_path.resolve(strict=True) != source_path
            or venv_configuration.resolve(strict=True) != venv_configuration
        ):
            raise WorkflowError(
                f"COMMAND_RUNTIME_SOURCE_ROUTE_NONCANONICAL:{phase}"
            )
        digest = hashlib.sha256()
        offset = 0
        while offset < pinned_before.st_size:
            chunk = os.pread(
                descriptor,
                min(1024 * 1024, pinned_before.st_size - offset),
                offset,
            )
            if not chunk:
                raise WorkflowError(
                    f"COMMAND_RUNTIME_EXECUTABLE_SHORT_READ:{phase}"
                )
            digest.update(chunk)
            offset += len(chunk)
        configuration_payload = venv_configuration.read_bytes()
        dependency_closure = _benchmark_python_dependency_closure()
        source_after = os.lstat(source_path)
        pinned_after = os.fstat(descriptor)
        configuration_after = os.lstat(venv_configuration)
    except OSError as exc:
        raise WorkflowError(
            f"COMMAND_RUNTIME_SOURCE_ROUTE_STAT_FAILED:{phase}"
        ) from exc
    source_identity = _benchmark_python_full_identity(source_before)
    pinned_identity = _benchmark_python_full_identity(pinned_before)
    configuration_identity = _benchmark_python_full_identity(
        configuration_before
    )
    if (
        source_path.parent.name != "bin"
        or not stat.S_ISREG(source_before.st_mode)
        or source_before.st_mode & 0o111 == 0
        or not stat.S_ISREG(configuration_before.st_mode)
        or (
            expected_runtime is None
            and source_identity != pinned_identity
        )
        or source_identity
        != _benchmark_python_full_identity(source_after)
        or pinned_identity
        != _benchmark_python_full_identity(pinned_after)
        or configuration_identity
        != _benchmark_python_full_identity(configuration_after)
    ):
        raise WorkflowError(
            f"COMMAND_RUNTIME_SOURCE_ROUTE_IDENTITY_INVALID:{phase}"
        )
    if expected_runtime is not None:
        expected_source_identity = (
            expected_runtime.identity[0],
            expected_runtime.identity[1],
            expected_runtime.identity[2],
            expected_runtime.mode,
            expected_runtime.identity[3],
            expected_runtime.identity[4],
            expected_runtime.identity[5],
        )
        if (
            source_path != expected_runtime.source_path
            or Path(fd_value) != expected_runtime.fd_path
            or descriptor != expected_runtime.descriptor
            or source_identity != expected_source_identity
            or pinned_identity != expected_source_identity
            or digest.hexdigest() != expected_runtime.sha256
        ):
            raise WorkflowError(
                f"COMMAND_RUNTIME_EXECUTABLE_CLOSURE_CHANGED:{phase}"
            )
        if (
            venv_configuration != expected_runtime.configuration_path
            or configuration_identity
            != expected_runtime.configuration_identity
            or hashlib.sha256(configuration_payload).hexdigest()
            != expected_runtime.configuration_sha256
        ):
            raise WorkflowError(
                f"COMMAND_RUNTIME_VENV_CONFIGURATION_CHANGED:{phase}"
            )
        if dependency_closure != expected_runtime.dependency_closure:
            raise WorkflowError(
                f"COMMAND_RUNTIME_DEPENDENCY_CLOSURE_CHANGED:{phase}"
            )
    return (
        source_identity,
        configuration_identity,
    )


def _benchmark_python_subprocess_binding(
    command: Sequence[str],
    environment: Mapping[str, str],
    descriptors: Sequence[int],
    *,
    phase: str,
    expected_runtime: BenchmarkPythonRuntime | None = None,
) -> tuple[
    list[str],
    str | None,
    tuple[
        tuple[int, int, int, int, int, int, int],
        tuple[int, int, int, int, int, int, int],
    ]
    | None,
]:
    """Logical FD argv를 같은 FD executable과 source argv0 실행으로 변환한다."""

    logical_command = list(command)
    runtime_fd = environment.get("S1_4X_BENCHMARK_PYTHON_PINNED_FD_PATH")
    if not logical_command or runtime_fd is None:
        if expected_runtime is not None:
            raise WorkflowError(
                f"COMMAND_RUNTIME_ENVIRONMENT_MISSING:{phase}"
            )
        return logical_command, None, None
    source = environment.get("S1_4X_BENCHMARK_PYTHON_BIN")
    matched = re.fullmatch(r"/proc/self/fd/([0-9]+)", runtime_fd)
    if (
        source is None
        or matched is None
        or int(matched.group(1)) not in descriptors
        or (
            logical_command[0] == runtime_fd
            and expected_runtime is None
        )
    ):
        raise WorkflowError(f"COMMAND_RUNTIME_EXECUTION_BINDING_INVALID:{phase}")
    route_identity = _benchmark_python_source_route_identity(
        source,
        runtime_fd,
        phase=phase,
        expected_runtime=expected_runtime,
    )
    if logical_command[0] != runtime_fd:
        return logical_command, None, route_identity
    return [source, *logical_command[1:]], runtime_fd, route_identity


def _assert_benchmark_python_route_unchanged(
    *,
    environment: Mapping[str, str],
    phase: str,
    expected: tuple[
        tuple[int, int, int, int, int, int, int],
        tuple[int, int, int, int, int, int, int],
    ]
    | None,
    expected_runtime: BenchmarkPythonRuntime | None = None,
) -> None:
    """Nested child 전후 source/pyvenv.cfg route identity drift를 거부한다."""

    if expected is None:
        return
    source = environment.get("S1_4X_BENCHMARK_PYTHON_BIN")
    runtime_fd = environment.get("S1_4X_BENCHMARK_PYTHON_PINNED_FD_PATH")
    if source is None or runtime_fd is None:
        raise WorkflowError(f"COMMAND_RUNTIME_ENVIRONMENT_MISSING:{phase}")
    if (
        _benchmark_python_source_route_identity(
            source,
            runtime_fd,
            phase=phase,
            expected_runtime=expected_runtime,
        )
        != expected
    ):
        raise WorkflowError(f"COMMAND_RUNTIME_SOURCE_ROUTE_CHANGED:{phase}")


def _run_logged(
    command: Sequence[str],
    *,
    cwd: Path,
    environment: Mapping[str, str],
    phase: str,
    output_directory: Path,
    expected_exit_codes: frozenset[int] = frozenset({0}),
    pass_fds: Sequence[int] = (),
    portable_path_ids: Mapping[str, str] | None = None,
    benchmark_python_runtime: BenchmarkPythonRuntime | None = None,
) -> dict[str, Any]:
    if phase not in CORRECTNESS_PHASES and not phase.startswith(("qualification-", "oci-")):
        raise WorkflowError(f"COMMAND_PHASE_INVALID:{phase}")
    stdout_path = output_directory / f"{phase}.stdout"
    stderr_path = output_directory / f"{phase}.stderr"
    if stdout_path.exists() or stderr_path.exists():
        raise WorkflowError(f"COMMAND_LOG_ALREADY_EXISTS:{phase}")
    descriptors = _child_runtime_descriptors(
        environment,
        pass_fds,
        phase=phase,
    )
    if any(
        type(descriptor) is not int or descriptor < 3
        for descriptor in descriptors
    ):
        raise WorkflowError(f"COMMAND_PASS_FDS_INVALID:{phase}")
    before_fd_identities = _pass_fd_identities(descriptors, phase=phase)
    receipt_command = _portable_argv(command, portable_path_ids)
    execution_command, executable, source_route_identity = (
        _benchmark_python_subprocess_binding(
            command,
            environment,
            descriptors,
            phase=phase,
            expected_runtime=benchmark_python_runtime,
        )
    )
    started_at = _iso_now()
    with stdout_path.open("xb") as standard_output, stderr_path.open("xb") as standard_error:
        completed = subprocess.run(
            execution_command,
            executable=executable,
            cwd=cwd,
            env=dict(environment),
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=standard_output,
            stderr=standard_error,
            pass_fds=descriptors,
        )
    _assert_benchmark_python_route_unchanged(
        environment=environment,
        phase=phase,
        expected=source_route_identity,
        expected_runtime=benchmark_python_runtime,
    )
    if _pass_fd_identities(descriptors, phase=phase) != before_fd_identities:
        raise WorkflowError(f"COMMAND_PASS_FD_IDENTITY_CHANGED:{phase}")
    finished_at = _iso_now()
    record = {
        "phase": phase,
        "argv": receipt_command,
        "argvSha256": canonical_sha256(receipt_command),
        "cwdPath": str(cwd),
        "startedAt": started_at,
        "finishedAt": finished_at,
        "exitCode": completed.returncode,
        "stdoutPath": str(stdout_path),
        "stdoutSha256": sha256_file(stdout_path),
        "stderrPath": str(stderr_path),
        "stderrSha256": sha256_file(stderr_path),
    }
    if completed.returncode not in expected_exit_codes:
        raise WorkflowError(f"COMMAND_FAILED:{phase}:{completed.returncode}")
    return record


def validate_logged_command_record(
    record: object,
    *,
    expected_phase: str,
    expected_argv: Sequence[str],
    expected_cwd: Path,
    evidence_directory: Path,
    portable_path_ids: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Command record의 exact argv/cwd/timestamps와 raw logs를 다시 결속한다."""

    expected_fields = {
        "phase",
        "argv",
        "argvSha256",
        "cwdPath",
        "startedAt",
        "finishedAt",
        "exitCode",
        "stdoutPath",
        "stdoutSha256",
        "stderrPath",
        "stderrSha256",
    }
    expected_command = _portable_argv(expected_argv, portable_path_ids)
    if (
        not isinstance(record, dict)
        or set(record) != expected_fields
        or record.get("phase") != expected_phase
        or record.get("argv") != expected_command
        or record.get("argvSha256") != canonical_sha256(expected_command)
    ):
        raise WorkflowError("COMMAND_ARGV_DRIFT")
    cwd = _absolute_existing_directory(expected_cwd, label="COMMAND_CWD")
    output = _absolute_existing_directory(
        evidence_directory,
        label="COMMAND_EVIDENCE_DIRECTORY",
    )
    if record.get("cwdPath") != str(cwd):
        raise WorkflowError("COMMAND_CWD_DRIFT")
    _require_iso_utc(record.get("startedAt"), label=f"{expected_phase}-started")
    _require_iso_utc(record.get("finishedAt"), label=f"{expected_phase}-finished")
    if record.get("exitCode") != 0:
        raise WorkflowError("COMMAND_EXIT_CODE_DRIFT")
    for stream in ("stdout", "stderr"):
        path = Path(str(record.get(f"{stream}Path", "")))
        expected_path = output / f"{expected_phase}.{stream}"
        if path != expected_path:
            raise WorkflowError("COMMAND_LOG_PATH_DRIFT")
        digest = _require_sha256(
            record.get(f"{stream}Sha256"),
            label=f"{expected_phase}-{stream}",
        )
        payload, _ = _same_fd_bytes_snapshot(
            path,
            label=f"COMMAND_{stream.upper()}",
            max_bytes=128 * 1024 * 1024,
        )
        if hashlib.sha256(payload).hexdigest() != digest:
            raise WorkflowError("COMMAND_LOG_SHA256_DRIFT")
    return record


def _run_compatibility_logged(
    command: Sequence[str],
    *,
    cwd: Path,
    environment: Mapping[str, str],
    phase: str,
    log_id: str,
    output_directory: Path,
    pass_fds: Sequence[int] = (),
    portable_path_ids: Mapping[str, str] | None = None,
    benchmark_python_runtime: BenchmarkPythonRuntime | None = None,
) -> dict[str, Any]:
    """Compatibility 단계 하나를 raw stdout/stderr와 함께 실행해 기록한다."""

    if phase not in COMPATIBILITY_REPLAY_PHASES or re.fullmatch(
        r"[a-z0-9-]+",
        log_id,
    ) is None:
        raise WorkflowError("COMPATIBILITY_COMMAND_PHASE_INVALID")
    stdout_path = output_directory / f"{log_id}.stdout"
    stderr_path = output_directory / f"{log_id}.stderr"
    if stdout_path.exists() or stderr_path.exists():
        raise WorkflowError(f"COMPATIBILITY_LOG_ALREADY_EXISTS:{log_id}")
    descriptors = _child_runtime_descriptors(
        environment,
        pass_fds,
        phase=phase,
    )
    if any(
        type(descriptor) is not int or descriptor < 3
        for descriptor in descriptors
    ):
        raise WorkflowError(f"COMPATIBILITY_PASS_FDS_INVALID:{phase}")
    before_fd_identities = _pass_fd_identities(descriptors, phase=phase)
    receipt_command = _portable_argv(command, portable_path_ids)
    execution_command, executable, source_route_identity = (
        _benchmark_python_subprocess_binding(
            command,
            environment,
            descriptors,
            phase=phase,
            expected_runtime=benchmark_python_runtime,
        )
    )
    started_at = _iso_now()
    with stdout_path.open("xb") as standard_output, stderr_path.open(
        "xb"
    ) as standard_error:
        completed = subprocess.run(
            execution_command,
            executable=executable,
            cwd=cwd,
            env=dict(environment),
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=standard_output,
            stderr=standard_error,
            pass_fds=descriptors,
        )
    _assert_benchmark_python_route_unchanged(
        environment=environment,
        phase=phase,
        expected=source_route_identity,
        expected_runtime=benchmark_python_runtime,
    )
    if _pass_fd_identities(descriptors, phase=phase) != before_fd_identities:
        raise WorkflowError(f"COMPATIBILITY_PASS_FD_IDENTITY_CHANGED:{phase}")
    finished_at = _iso_now()
    return {
        "phase": phase,
        "logId": log_id,
        "argv": receipt_command,
        "argvSha256": canonical_sha256(receipt_command),
        "startedAt": started_at,
        "endedAt": finished_at,
        "exitCode": completed.returncode,
        "stdoutPath": str(stdout_path),
        "stdoutSha256": sha256_file(stdout_path),
        "stderrPath": str(stderr_path),
        "stderrSha256": sha256_file(stderr_path),
    }


def _find_candidate_binary(
    haskell_root: Path,
    *,
    work_dir: Path,
    ghc_version: str,
) -> Path:
    if (
        work_dir.is_absolute()
        or len(work_dir.parts) != 1
        or re.fullmatch(
            r"\.stack-work-s1-4x-[a-z0-9][a-z0-9-]{0,127}",
            work_dir.name,
        )
        is None
    ):
        raise WorkflowError("CANDIDATE_STACK_WORK_DIR_INVALID")
    work_root = _absolute_existing_directory(
        haskell_root / work_dir,
        label="CANDIDATE_STACK_WORK_DIR",
    )
    candidates = sorted(
        (
            path.resolve(strict=True)
            for path in (work_root / "dist").glob(
                f"*/ghc-{ghc_version}/build/s1-4x-haskell/s1-4x-haskell"
            )
            if path.is_file() and not path.is_symlink() and os.access(path, os.X_OK)
        ),
        key=lambda path: str(path).encode(),
    )
    if len(candidates) != 1:
        raise WorkflowError(f"CANDIDATE_BINARY_CARDINALITY:{len(candidates)}")
    return candidates[0]


def _comparison_status_document(report: object) -> dict[str, Any]:
    if (
        not isinstance(report, dict)
        or report.get("schemaVersion") != "s1.4x-comparison-report-v1"
        or report.get("status") != "PASS"
        or report.get("mismatchCount") != 0
        or report.get("mismatches") != []
    ):
        raise WorkflowError("COMPARISON_NOT_PASS")
    return report


def _comparison_status(path: Path) -> dict[str, Any]:
    _, report, _ = _same_fd_json_value(
        path,
        label="COMPARISON",
        max_bytes=64 * 1024 * 1024,
    )
    return _comparison_status_document(report)


def _correctness(arguments: argparse.Namespace) -> None:
    output = _reserve_directory(arguments.output_dir)
    python_runtime = arguments.benchmark_python_runtime
    haskell_root = Path(__file__).resolve(strict=True).parent.parent
    numeric_root = haskell_root.parent
    repo_root = numeric_root.parents[3]
    profile_id = arguments.profile
    options = profile_options(profile_id)
    ghcup = _required_environment_path("S1_4X_GHCUP_BIN")
    stack = _required_environment_path("S1_4X_STACK_BIN")
    ghc = _required_environment_path("S1_4X_AUTHORITATIVE_GHC_BIN")
    cache_root_value = os.environ.get("S1_4X_CACHE_ROOT")
    if cache_root_value is None:
        raise WorkflowError("REQUIRED_ENVIRONMENT_MISSING:S1_4X_CACHE_ROOT")
    cache_root = _absolute_existing_directory(
        Path(cache_root_value),
        label="CACHE_ROOT",
    )
    stack_root = isolated_stack_root(
        cache_root,
        purpose=f"correctness-{profile_id}",
        output_path=output,
    )
    if stack_root.exists() or stack_root.is_symlink():
        raise WorkflowError("CORRECTNESS_STACK_ROOT_ALREADY_EXISTS")
    stack_root.mkdir(mode=0o700)
    work_dir = isolated_stack_work_dir(stack_root)
    if (haskell_root / work_dir).exists() or (
        haskell_root / work_dir
    ).is_symlink():
        raise WorkflowError("CORRECTNESS_STACK_WORK_DIR_ALREADY_EXISTS")
    candidate_commit = _repo_commit(repo_root)
    evidence = _load_haskell_evidence(haskell_root)
    source_tree_sha256 = evidence.benchmark_source_tree_sha256(haskell_root)
    environment = _sealed_child_environment(
        ghc_bin=ghc,
        stack_bin=stack,
        python_runtime=python_runtime,
    )
    stack_yaml = _absolute_regular(
        haskell_root / "stack.yaml",
        label="AUTHORITATIVE_STACK_YAML",
    )
    build = build_stack_command(
        ghcup=ghcup,
        stack=stack,
        stack_yaml=stack_yaml,
        stack_root=stack_root,
        work_dir=work_dir,
        ghc_version="9.10.3",
        operation=[
            "build",
            "--test",
            "--bench",
            "--no-run-tests",
            "--no-run-benchmarks",
            "--pedantic",
            f"--ghc-options={' '.join(options)}",
        ],
    )
    test = build_stack_command(
        ghcup=ghcup,
        stack=stack,
        stack_yaml=stack_yaml,
        stack_root=stack_root,
        work_dir=work_dir,
        ghc_version="9.10.3",
        operation=["test", "--pedantic", f"--ghc-options={' '.join(options)}"],
    )
    records = [
        _run_logged(
            build,
            cwd=haskell_root,
            environment=environment,
            phase="build",
            output_directory=output,
            benchmark_python_runtime=python_runtime,
        ),
        _run_logged(
            test,
            cwd=haskell_root,
            environment=environment,
            phase="test",
            output_directory=output,
            benchmark_python_runtime=python_runtime,
        ),
    ]
    candidate_binary = _find_candidate_binary(
        haskell_root,
        work_dir=work_dir,
        ghc_version="9.10.3",
    )
    fixture_root = _absolute_existing_directory(
        numeric_root / "contract/fixtures",
        label="FIXTURE_ROOT",
    )
    comparator = _pin_oracle_comparator(numeric_root)
    python_path_ids = _oracle_compare_path_ids(python_runtime, comparator)
    requests = (
        (
            "canonical",
            fixture_root / "small/canonical-inputs.v1.json",
            fixture_root / "expected/canonical-results.v1.json",
        ),
        (
            "semantic",
            fixture_root / "invalid/semantic-errors.v1.json",
            fixture_root / "invalid/semantic-errors.expected.v1.json",
        ),
    )
    comparison_artifacts: list[dict[str, Any]] = []
    for label, request, expected in requests:
        _absolute_regular(request, label=f"{label.upper()}_REQUEST")
        _absolute_regular(expected, label=f"{label.upper()}_EXPECTED")
        actual = output / f"{label}.actual.json"
        comparison = output / f"{label}.comparison.json"
        process_phase = f"{label}-process"
        compare_phase = f"{label}-compare"
        records.append(
            _run_logged(
                [
                    str(candidate_binary),
                    "--request",
                    str(request),
                    "--fixture-root",
                    str(fixture_root),
                    "--output",
                    str(actual),
                ],
                cwd=haskell_root,
                environment=environment,
                phase=process_phase,
                output_directory=output,
                benchmark_python_runtime=python_runtime,
            )
        )
        _absolute_regular(actual, label=f"{label.upper()}_ACTUAL")
        records.append(
            _run_logged(
                _oracle_compare_command(
                    python_path=python_runtime.fd_path,
                    comparator=comparator,
                    arguments=[
                        "--expected",
                        str(expected),
                        "--actual",
                        str(actual),
                        "--request",
                        str(request),
                        "--output",
                        str(comparison),
                    ],
                ),
                cwd=repo_root,
                environment=environment,
                phase=compare_phase,
                output_directory=output,
                pass_fds=_oracle_compare_pass_fds(
                    python_runtime,
                    comparator,
                ),
                portable_path_ids=python_path_ids,
                benchmark_python_runtime=python_runtime,
            )
        )
        _comparison_status(comparison)
        comparison_artifacts.append(
            {
                "matrixId": label,
                "requestPath": str(request),
                "requestSha256": sha256_file(request),
                "expectedPath": str(expected),
                "expectedSha256": sha256_file(expected),
                "actualPath": str(actual),
                "actualSha256": sha256_file(actual),
                "comparisonPath": str(comparison),
                "comparisonSha256": sha256_file(comparison),
                "mismatchCount": 0,
                "status": "PASS",
            }
        )
    if tuple(record["phase"] for record in records) != CORRECTNESS_PHASES:
        raise WorkflowError("CORRECTNESS_PHASE_SEQUENCE_DRIFT")
    if evidence.benchmark_source_tree_sha256(haskell_root) != source_tree_sha256:
        raise WorkflowError("SOURCE_TREE_CHANGED_DURING_CORRECTNESS")
    if _repo_commit(repo_root) != candidate_commit:
        raise WorkflowError("CANDIDATE_COMMIT_CHANGED_DURING_CORRECTNESS")
    receipt = {
        "schemaVersion": CORRECTNESS_SCHEMA_VERSION,
        "status": "PASS",
        "profileId": profile_id,
        "ghcOptions": list(options),
        "optionsSha256": canonical_sha256(list(options)),
        "compilerVersion": "9.10.3",
        "compilerPath": str(ghc),
        "compilerSha256": sha256_file(ghc),
        "candidateSourceCommit": candidate_commit,
        "sourceTreeSha256": source_tree_sha256,
        "candidateBinaryPath": str(candidate_binary),
        "candidateBinarySha256": sha256_file(candidate_binary),
        "stackRootPath": str(stack_root),
        "stackWorkDir": str(work_dir),
        "stackYamlPath": str(stack_yaml),
        "stackYamlSha256": sha256_file(stack_yaml),
        "commands": records,
        "comparisonArtifacts": comparison_artifacts,
        "mismatchCount": 0,
    }
    receipt_path = output / "correctness-receipt.v1.json"
    atomic_write_json_exclusive(receipt_path, receipt)
    print(
        json.dumps(
            {
                "profileId": profile_id,
                "receiptPath": str(receipt_path),
                "receiptSha256": sha256_file(receipt_path),
                "status": "PASS",
            },
            sort_keys=True,
        )
    )


def _qualification_contract(plan: object) -> tuple[dict[str, Any], tuple[str, ...]]:
    if not isinstance(plan, dict):
        raise WorkflowError("QUALIFICATION_PLAN_INVALID")
    configuration = plan.get("haskellProfileQualification")
    if not isinstance(configuration, dict):
        raise WorkflowError("QUALIFICATION_CONFIG_MISSING")
    expected_fields = {
        "qualificationCaseIds",
        "qualificationCaseOrder",
        "profileOrderBlocks",
        "hostValidityBeforeEachProfileBlock",
        "criterionTimeLimitSeconds",
        "outerQualificationRepetitions",
        "ratioPairing",
        "perCaseCollapse",
        "aggregateFormula",
        "improvingBlockFormula",
        "perCaseMaxRegressionRatio",
        "aggregateMaxRatio",
        "minimumImprovingOuterRepetitions",
        "optimizedProfile",
        "fallbackProfile",
    }
    case_order = configuration.get("qualificationCaseOrder")
    if (
        set(configuration) != expected_fields
        or configuration.get("qualificationCaseIds") != case_order
        or not isinstance(case_order, list)
        or len(case_order) != 7
        or len(set(case_order)) != 7
        or configuration.get("profileOrderBlocks")
        != [list(block) for block in PROFILE_ORDER_BLOCKS]
        or configuration.get("hostValidityBeforeEachProfileBlock") is not True
        or configuration.get("criterionTimeLimitSeconds") != 3
        or configuration.get("outerQualificationRepetitions") != 4
        or configuration.get("ratioPairing") != "same-order-block-and-case"
        or configuration.get("perCaseCollapse") != "max-of-four-paired-ratios"
        or configuration.get("aggregateFormula")
        != "geometric-mean-of-all-28-paired-ratios"
        or configuration.get("improvingBlockFormula")
        != "geometric-mean-of-seven-case-ratios"
        or configuration.get("perCaseMaxRegressionRatio") != 1.05
        or configuration.get("aggregateMaxRatio") != 0.97
        or configuration.get("minimumImprovingOuterRepetitions") != 3
        or configuration.get("optimizedProfile") != "optimized-o2-fasm"
        or configuration.get("fallbackProfile") != "baseline-o0-fasm"
    ):
        raise WorkflowError("QUALIFICATION_CONFIG_DRIFT")
    return configuration, tuple(case_order)


def _host_validator_command(
    *,
    numeric_root: Path,
    plan: Mapping[str, Any],
    output: Path,
    root_pid: int,
    python_bin: Path,
    validator_script: Path | None = None,
) -> list[str]:
    execution = plan.get("execution")
    environment = plan.get("environmentValidity")
    if not isinstance(execution, dict) or not isinstance(environment, dict):
        raise WorkflowError("HOST_POLICY_MISSING")
    cpu_set = execution.get("cpuSet")
    if (
        not isinstance(cpu_set, list)
        or not cpu_set
        or any(type(cpu) is not int or cpu < 0 for cpu in cpu_set)
    ):
        raise WorkflowError("HOST_CPU_SET_INVALID")
    validator = (
        _absolute_regular(
            numeric_root / "oracle/validate_environment.py",
            label="HOST_VALIDATOR",
        )
        if validator_script is None
        else validator_script
    )
    return [
        str(python_bin),
        str(validator),
        "--home",
        str(_absolute_existing_directory(Path(os.environ["HOME"]), label="HOME")),
        "--cpu-set",
        ",".join(str(cpu) for cpu in cpu_set),
        "--min-home-free-bytes",
        "32212254720",
        "--min-available-memory-bytes",
        str(environment["minAvailableMemoryGiB"] * 1024**3),
        "--max-normalized-load1",
        str(environment["maxNormalizedLoad1"]),
        "--load-samples",
        str(environment["loadSampleCount"]),
        "--sample-interval-seconds",
        str(environment["loadSampleIntervalSeconds"]),
        "--max-quiet-wait-seconds",
        str(environment["maxQuietWaitSeconds"]),
        "--max-running-containers",
        str(environment["runningContainerCount"]),
        "--external-process-sample-seconds",
        "30",
        "--max-external-process-cpu-percent",
        str(environment["externalProcessCpuPercentThreshold"]),
        "--allowed-process-root-pid",
        str(root_pid),
        "--output",
        str(output),
    ]


def _validate_host_report_document(
    report: object,
    *,
    plan: Mapping[str, Any],
    root_pid: int,
) -> dict[str, Any]:
    execution = plan["execution"]
    frozen = plan["environmentValidity"]
    expected_policy = {
        "cpu_set": execution["cpuSet"],
        "min_home_free_bytes": 32_212_254_720,
        "min_available_memory_bytes": frozen["minAvailableMemoryGiB"] * 1024**3,
        "max_normalized_load1": frozen["maxNormalizedLoad1"],
        "load_samples": frozen["loadSampleCount"],
        "sample_interval_seconds": frozen["loadSampleIntervalSeconds"],
        "max_quiet_wait_seconds": frozen["maxQuietWaitSeconds"],
        "max_running_containers": frozen["runningContainerCount"],
        "external_process_sample_seconds": 30.0,
        "max_external_process_cpu_percent": frozen[
            "externalProcessCpuPercentThreshold"
        ],
        "allowed_process_root_pid": root_pid,
    }
    host_check_ids = {
        "disk.home-free-bytes",
        "memory.available-bytes",
        "cpu.logical-count",
        "cpu.affinity-round-trip",
        "docker.running-containers",
        "load.normalized-load1-window",
        "process.external-cpu",
    }
    checks = report.get("checks") if isinstance(report, dict) else None
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
        or report.get("schemaVersion") != "s1.4x-host-validity-v1"
        or report.get("status") != "PASS"
        or report.get("failureCount") != 0
        or report.get("policy") != expected_policy
        or not isinstance(report.get("metadata"), dict)
        or not isinstance(checks, list)
        or len(checks) != len(host_check_ids)
        or any(
            not isinstance(check, dict)
            or set(check)
            != {"id", "expected", "actual", "status", "evidence"}
            or check.get("status") != "PASS"
            for check in checks
        )
        or {check["id"] for check in checks} != host_check_ids
    ):
        raise WorkflowError("HOST_VALIDITY_NOT_PASS")
    _require_sha256(
        report.get("portableHostIdSha256"),
        label="host-portable-id",
    )
    return report


def _validate_host_report(path: Path, *, plan: Mapping[str, Any], root_pid: int) -> None:
    _, report, _ = _same_fd_json_value(
        path,
        label="HOST_VALIDITY",
        max_bytes=8 * 1024 * 1024,
    )
    _validate_host_report_document(report, plan=plan, root_pid=root_pid)


def _criterion_qualification_command(
    *,
    ghcup: Path,
    stack: Path,
    stack_yaml: Path,
    stack_root: Path,
    work_dir: Path,
    profile_id: str,
    time_limit_seconds: int,
    raw_report: Path,
    case_order: Sequence[str],
) -> list[str]:
    expression = "^(?:" + "|".join(re.escape(case_id) for case_id in case_order) + ")$"
    return build_stack_command(
        ghcup=ghcup,
        stack=stack,
        stack_yaml=stack_yaml,
        stack_root=stack_root,
        work_dir=work_dir,
        ghc_version="9.10.3",
        operation=[
            "bench",
            f"--ghc-options={' '.join(profile_options(profile_id))}",
            (
                "--benchmark-arguments="
                f"--time-limit {time_limit_seconds} "
                f"--json {raw_report} "
                f"--match pattern {expression} +RTS -N1 -RTS"
            ),
        ],
    )


def _qualification(arguments: argparse.Namespace) -> None:
    if (
        arguments.profiles != "baseline-o0-fasm,optimized-o2-fasm"
        or arguments.enforce_order_plan is not True
    ):
        raise WorkflowError("QUALIFICATION_CLI_CONTRACT_INVALID")
    output = _reserve_directory(arguments.output_dir)
    marker_python = arguments.benchmark_python_runtime
    plan_path = _absolute_regular(arguments.plan, label="QUALIFICATION_PLAN")
    plan = strict_json_load(plan_path)
    configuration, case_order = _qualification_contract(plan)
    haskell_root = Path(__file__).resolve(strict=True).parent.parent
    numeric_root = haskell_root.parent
    repo_root = numeric_root.parents[3]
    candidate_commit = _repo_commit(repo_root)
    evidence = _load_haskell_evidence(haskell_root)
    source_tree_sha256 = evidence.benchmark_source_tree_sha256(haskell_root)
    selector_config_sha256 = canonical_sha256(configuration)
    plan_sha256 = sha256_file(plan_path)
    ghcup = _required_environment_path("S1_4X_GHCUP_BIN")
    stack = _required_environment_path("S1_4X_STACK_BIN")
    ghc = _required_environment_path("S1_4X_AUTHORITATIVE_GHC_BIN")
    # Shared materializer root는 large timing 전용이며 correctness/OCI static fixture와 섞지 않는다.
    large_fixture_value = os.environ.get("S1_4X_LARGE_FIXTURE_ROOT")
    if large_fixture_value is None:
        raise WorkflowError(
            "REQUIRED_ENVIRONMENT_MISSING:S1_4X_LARGE_FIXTURE_ROOT"
        )
    large_fixture_root = _absolute_existing_directory(
        Path(large_fixture_value),
        label="LARGE_FIXTURE_ROOT",
    )
    _absolute_existing_directory(
        large_fixture_root / "large",
        label="LARGE_FIXTURE_DIRECTORY",
    )
    marker_script = _pin_python_script(
        Path(__file__).resolve(strict=True),
        label="PROFILE_MARKER_SCRIPT",
    )
    host_validator = _pin_python_script(
        numeric_root / "oracle/validate_environment.py",
        label="HOST_VALIDATOR_PY",
    )
    docker_client = pin_qualification_docker_client_from_environment()
    docker_route = prepare_qualification_docker_route(
        output,
        docker_client=docker_client,
    )
    docker_route_baseline = snapshot_qualification_docker_route(
        docker_route,
        docker_client=docker_client,
    )
    docker_route_receipt = build_qualification_docker_route_receipt(
        docker_route,
        docker_client=docker_client,
        baseline=docker_route_baseline,
    )
    configured_python_sha256 = marker_python.sha256
    marker_script_sha256 = marker_script.sha256
    qualification_owner_pid = os.getpid()
    qualification_owner_start_ticks = _process_start_ticks(
        qualification_owner_pid
    )
    cache_value = os.environ.get("S1_4X_CACHE_ROOT")
    if cache_value is None:
        raise WorkflowError("REQUIRED_ENVIRONMENT_MISSING:S1_4X_CACHE_ROOT")
    cache_root = _absolute_existing_directory(Path(cache_value), label="CACHE_ROOT")
    stack_root = isolated_stack_root(
        cache_root,
        purpose="qualification",
        output_path=output,
    )
    if stack_root.exists() or stack_root.is_symlink():
        raise WorkflowError("QUALIFICATION_STACK_ROOT_ALREADY_EXISTS")
    stack_root.mkdir(mode=0o700)
    work_dir = isolated_stack_work_dir(stack_root)
    if (haskell_root / work_dir).exists() or (
        haskell_root / work_dir
    ).is_symlink():
        raise WorkflowError("QUALIFICATION_STACK_WORK_DIR_ALREADY_EXISTS")
    environment = _sealed_child_environment(
        ghc_bin=ghc,
        stack_bin=stack,
        python_runtime=marker_python,
    )
    host_environment = qualification_environment_with_docker_route(
        environment,
        route=docker_route,
        docker_client=docker_client,
    )
    host_path_ids = {
        str(marker_python.fd_path): _benchmark_python_path_id(marker_python),
        str(host_validator.fd_path): _pinned_file_path_id(host_validator),
    }
    stack_yaml = _absolute_regular(
        haskell_root / "stack.yaml",
        label="AUTHORITATIVE_STACK_YAML",
    )
    cpu_set = set(plan["execution"]["cpuSet"])
    os.sched_setaffinity(0, cpu_set)
    if os.sched_getaffinity(0) != cpu_set:
        raise WorkflowError("QUALIFICATION_AFFINITY_MISMATCH")
    blocks: list[dict[str, Any]] = []
    for block_index, order in enumerate(PROFILE_ORDER_BLOCKS):
        profile_records: list[dict[str, Any]] = []
        estimates: dict[str, dict[str, float]] = {}
        for profile_id in order:
            prefix = f"block-{block_index + 1}-{profile_id}"
            host_report = output / f"{prefix}-host-validity.json"
            host_command = _host_validator_command(
                numeric_root=numeric_root,
                plan=plan,
                output=host_report,
                root_pid=qualification_owner_pid,
                python_bin=marker_python.fd_path,
                validator_script=host_validator.fd_path,
            )
            docker_route_before = snapshot_qualification_docker_route(
                docker_route,
                docker_client=docker_client,
            )
            if docker_route_before != docker_route_baseline:
                raise WorkflowError("QUALIFICATION_DOCKER_ROUTE_CHANGED")
            try:
                host_record = _run_logged(
                    host_command,
                    cwd=repo_root,
                    environment=host_environment,
                    phase=f"qualification-{prefix}-host",
                    output_directory=output,
                    pass_fds=(
                        marker_python.descriptor,
                        host_validator.descriptor,
                        docker_client.descriptor,
                    ),
                    portable_path_ids=host_path_ids,
                    benchmark_python_runtime=marker_python,
                )
            finally:
                # Host가 정책상 실패해도 route drift 검증은 생략하지 않는다.
                docker_route_after = snapshot_qualification_docker_route(
                    docker_route,
                    docker_client=docker_client,
                )
                if (
                    docker_route_after != docker_route_before
                    or docker_route_after != docker_route_baseline
                ):
                    raise WorkflowError(
                        "QUALIFICATION_DOCKER_ROUTE_CHANGED"
                    )
            _validate_host_report(
                host_report,
                plan=plan,
                root_pid=qualification_owner_pid,
            )
            marker_path = output / f"{prefix}-measurement-state.json"
            marker_argv = _qualification_marker_command(
                python_source_path=marker_python.source_path,
                python_pinned_fd_path=marker_python.fd_path,
                script_pinned_fd_path=marker_script.fd_path,
                marker_path=marker_path,
            )
            marker_path_id = _qualification_marker_path_id(
                order_block=block_index,
                profile_id=profile_id,
            )
            marker_witness = build_qualification_command_witness(
                owner_pid=qualification_owner_pid,
                owner_start_ticks=qualification_owner_start_ticks,
                python_source_path=marker_python.source_path,
                python_source_sha256=configured_python_sha256,
                python_descriptor=marker_python.descriptor,
                python_identity={
                    "device": marker_python.identity[0],
                    "inode": marker_python.identity[1],
                    "size": marker_python.identity[2],
                    "mode": marker_python.mode,
                    "mtimeNs": marker_python.identity[3],
                    "ctimeNs": marker_python.identity[4],
                    "linkCount": marker_python.identity[5],
                },
                script_source_path=marker_script.source_path,
                script_source_sha256=marker_script_sha256,
                script_descriptor=marker_script.descriptor,
                script_identity={
                    "device": marker_script.identity[0],
                    "inode": marker_script.identity[1],
                    "size": marker_script.identity[2],
                    "mode": marker_script.mode,
                    "mtimeNs": marker_script.identity[3],
                    "ctimeNs": marker_script.identity[4],
                    "linkCount": marker_script.identity[5],
                },
                marker_argv=marker_argv,
                marker_path_id=marker_path_id,
            )
            marker = build_profile_marker(
                plan_sha256=plan_sha256,
                selector_config_sha256=selector_config_sha256,
                source_tree_sha256=source_tree_sha256,
                order_block=block_index,
                profile_id=profile_id,
                case_order=case_order,
                host_validity_sha256=sha256_file(host_report),
                marker_python_path=str(marker_python.source_path),
                marker_python_pinned_fd_path=str(marker_python.fd_path),
                marker_python_sha256=configured_python_sha256,
                marker_script_path=str(marker_script.source_path),
                marker_script_pinned_fd_path=str(marker_script.fd_path),
                marker_script_sha256=marker_script_sha256,
                marker_argv=marker_argv,
                started_at=_iso_now(),
            )
            atomic_write_json_exclusive(marker_path, marker)
            pre_run_sha256 = sha256_file(marker_path)
            raw_report = output / f"{prefix}-criterion.json"
            criterion_command = _criterion_qualification_command(
                ghcup=ghcup,
                stack=stack,
                stack_yaml=stack_yaml,
                stack_root=stack_root,
                work_dir=work_dir,
                profile_id=profile_id,
                time_limit_seconds=configuration["criterionTimeLimitSeconds"],
                raw_report=raw_report,
                case_order=case_order,
            )
            profile_environment = dict(environment)
            profile_environment.update(
                {
                    "S1_4X_BENCHMARK_PLAN": str(plan_path),
                    "S1_4X_LARGE_FIXTURE_ROOT": str(large_fixture_root),
                    "S1_4X_BENCHMARK_QUALIFICATION": str(marker_path),
                    "S1_4X_BENCHMARK_MARKER_PYTHON": str(marker_python.fd_path),
                    "S1_4X_BENCHMARK_MARKER_PYTHON_SOURCE_PATH": str(
                        marker_python.source_path
                    ),
                    "S1_4X_BENCHMARK_MARKER_PYTHON_SHA256": (
                        configured_python_sha256
                    ),
                    "S1_4X_BENCHMARK_MARKER_SCRIPT": str(marker_script.fd_path),
                    "S1_4X_BENCHMARK_MARKER_SCRIPT_SHA256": (
                        marker_script_sha256
                    ),
                }
            )
            started_at = _iso_now()
            criterion_record = _run_logged(
                criterion_command,
                cwd=haskell_root,
                environment=profile_environment,
                phase=f"qualification-{prefix}-criterion",
                output_directory=output,
                pass_fds=(
                    marker_python.descriptor,
                    marker_script.descriptor,
                ),
                benchmark_python_runtime=marker_python,
            )
            finished_at = _iso_now()
            raw = strict_json_load(raw_report)
            case_estimates = parse_criterion_qualification_reports(
                raw,
                expected_case_order=case_order,
            )
            measurement_marker = _validate_profile_marker(
                strict_json_load(marker_path),
                state="MEASUREMENT",
            )
            if (
                measurement_marker["preRunSha256"] != pre_run_sha256
                or measurement_marker["markerScriptSha256"]
                != marker_script_sha256
                or measurement_marker["markerPythonSha256"]
                != configured_python_sha256
            ):
                raise WorkflowError("PROFILE_MARKER_TRANSITION_EVIDENCE_INVALID")
            estimates[profile_id] = case_estimates
            profile_records.append(
                {
                    "profileId": profile_id,
                    "ghcOptions": list(profile_options(profile_id)),
                    "optionsSha256": canonical_sha256(
                        list(profile_options(profile_id))
                    ),
                    "startedAt": started_at,
                    "finishedAt": finished_at,
                    "hostValidityPath": str(host_report),
                    "hostValiditySha256": sha256_file(host_report),
                    "hostDockerRouteBeforeSha256": (
                        docker_route_before["snapshotSha256"]
                    ),
                    "hostDockerRouteAfterSha256": (
                        docker_route_after["snapshotSha256"]
                    ),
                    "hostCommand": host_record,
                    "rawCriterionPath": str(raw_report),
                    "rawCriterionSha256": sha256_file(raw_report),
                    "criterionCommand": criterion_record,
                    "caseSecondsPerBatch": case_estimates,
                    "marker": {
                        "path": str(marker_path),
                        "preRunSha256": pre_run_sha256,
                        "measurementSha256": sha256_file(marker_path),
                        "pythonPath": str(marker_python.source_path),
                        "pythonPinnedFdPath": str(marker_python.fd_path),
                        "pythonSha256": configured_python_sha256,
                        "scriptPath": str(marker_script.source_path),
                        "scriptPinnedFdPath": str(marker_script.fd_path),
                        "scriptSha256": marker_script_sha256,
                        "argv": marker_argv,
                        "argvSha256": canonical_sha256(marker_argv),
                        "portableWitness": marker_witness,
                    },
                }
            )
        ratios = {
            case_id: (
                estimates["optimized-o2-fasm"][case_id]
                / estimates["baseline-o0-fasm"][case_id]
            )
            for case_id in case_order
        }
        blocks.append(
            {
                "orderBlock": block_index,
                "plannedProfileOrder": list(order),
                "actualProfileOrder": [
                    record["profileId"] for record in profile_records
                ],
                "profiles": profile_records,
                "ratios": ratios,
            }
        )
    selector_blocks = [
        {
            "orderBlock": block["orderBlock"],
            "plannedProfileOrder": block["plannedProfileOrder"],
            "actualProfileOrder": block["actualProfileOrder"],
            "ratios": block["ratios"],
        }
        for block in blocks
    ]
    selection = select_profile_from_blocks(
        selector_blocks,
        case_order=case_order,
        profile_order_blocks=PROFILE_ORDER_BLOCKS,
    )
    if evidence.benchmark_source_tree_sha256(haskell_root) != source_tree_sha256:
        raise WorkflowError("SOURCE_TREE_CHANGED_DURING_QUALIFICATION")
    if _repo_commit(repo_root) != candidate_commit:
        raise WorkflowError("CANDIDATE_COMMIT_CHANGED_DURING_QUALIFICATION")
    artifact = {
        "schemaVersion": QUALIFICATION_SCHEMA_VERSION,
        "status": "PASS",
        "candidateSourceCommit": candidate_commit,
        "planPathId": "S1_4X_BENCHMARK_PLAN",
        "planSha256": plan_sha256,
        "selectorConfigSha256": selector_config_sha256,
        "sourceTreeSha256": source_tree_sha256,
        "stackWorkDir": str(work_dir),
        "qualificationCaseOrder": list(case_order),
        "plannedProfileOrderBlocks": [
            list(block) for block in PROFILE_ORDER_BLOCKS
        ],
        "dockerRoute": docker_route_receipt,
        "blocks": blocks,
        "selection": selection,
    }
    artifact_path = output / "qualification-artifact.v1.json"
    atomic_write_json_exclusive(artifact_path, artifact)
    print(
        json.dumps(
            {
                "profileId": selection["profileId"],
                "qualificationArtifactPath": str(artifact_path),
                "qualificationArtifactSha256": sha256_file(artifact_path),
                "status": "PASS",
            },
            sort_keys=True,
        )
    )


def _validate_correctness_receipt(
    path: Path,
    *,
    expected_profile_id: str,
    expected_source_tree_sha256: str,
    expected_commit: str,
) -> dict[str, Any]:
    receipt_path = _absolute_regular(path, label="CORRECTNESS_RECEIPT")
    receipt_payload, receipt, _ = _same_fd_json_value(
        receipt_path,
        label="CORRECTNESS_RECEIPT",
        max_bytes=4 * 1024 * 1024,
    )
    options = profile_options(expected_profile_id)
    expected_fields = {
        "schemaVersion",
        "status",
        "profileId",
        "ghcOptions",
        "optionsSha256",
        "compilerVersion",
        "compilerPath",
        "compilerSha256",
        "candidateSourceCommit",
        "sourceTreeSha256",
        "candidateBinaryPath",
        "candidateBinarySha256",
        "stackRootPath",
        "stackWorkDir",
        "stackYamlPath",
        "stackYamlSha256",
        "commands",
        "comparisonArtifacts",
        "mismatchCount",
    }
    if (
        not isinstance(receipt, dict)
        or set(receipt) != expected_fields
        or receipt_payload
        != canonical_json_bytes(receipt, trailing_newline=True)
        or receipt.get("schemaVersion") != CORRECTNESS_SCHEMA_VERSION
        or receipt.get("status") != "PASS"
        or receipt.get("profileId") != expected_profile_id
        or receipt.get("ghcOptions") != list(options)
        or receipt.get("optionsSha256") != canonical_sha256(list(options))
        or receipt.get("sourceTreeSha256") != expected_source_tree_sha256
        or receipt.get("candidateSourceCommit") != expected_commit
        or receipt.get("compilerVersion") != "9.10.3"
        or receipt.get("mismatchCount") != 0
        or not isinstance(receipt.get("commands"), list)
        or not isinstance(receipt.get("comparisonArtifacts"), list)
        or len(receipt["comparisonArtifacts"]) != 2
    ):
        raise WorkflowError(f"CORRECTNESS_RECEIPT_INVALID:{expected_profile_id}")
    output = receipt_path.parent
    haskell_root = Path(__file__).resolve(strict=True).parent.parent
    numeric_root = haskell_root.parent
    repo_root = numeric_root.parents[3]
    ghcup = _required_environment_path("S1_4X_GHCUP_BIN")
    stack = _required_environment_path("S1_4X_STACK_BIN")
    compiler = _required_environment_path("S1_4X_AUTHORITATIVE_GHC_BIN")
    cache_value = os.environ.get("S1_4X_CACHE_ROOT")
    if cache_value is None:
        raise WorkflowError("REQUIRED_ENVIRONMENT_MISSING:S1_4X_CACHE_ROOT")
    cache_root = _absolute_existing_directory(
        Path(cache_value),
        label="CACHE_ROOT",
    )
    stack_root = _absolute_existing_directory(
        Path(str(receipt.get("stackRootPath", ""))),
        label="CORRECTNESS_STACK_ROOT",
    )
    if stack_root != isolated_stack_root(
        cache_root,
        purpose=f"correctness-{expected_profile_id}",
        output_path=output,
    ):
        raise WorkflowError("CORRECTNESS_STACK_ROOT_DRIFT")
    work_dir = Path(str(receipt.get("stackWorkDir", "")))
    if work_dir != isolated_stack_work_dir(stack_root):
        raise WorkflowError("CORRECTNESS_STACK_WORK_DIR_DRIFT")
    stack_yaml = _absolute_regular(
        Path(str(receipt.get("stackYamlPath", ""))),
        label="CORRECTNESS_STACK_YAML",
    )
    if (
        stack_yaml != haskell_root / "stack.yaml"
        or receipt["stackYamlSha256"] != sha256_file(stack_yaml)
    ):
        raise WorkflowError("CORRECTNESS_STACK_YAML_DRIFT")
    if (
        receipt.get("compilerPath") != str(compiler)
        or receipt.get("compilerSha256") != sha256_file(compiler)
    ):
        raise WorkflowError("CORRECTNESS_COMPILER_DRIFT")
    candidate_binary = _absolute_regular(
        Path(str(receipt.get("candidateBinaryPath", ""))),
        label="CORRECTNESS_CANDIDATE_BINARY",
        executable=True,
    )
    if (
        candidate_binary
        != _find_candidate_binary(
            haskell_root,
            work_dir=work_dir,
            ghc_version="9.10.3",
        )
        or receipt.get("candidateBinarySha256")
        != sha256_file(candidate_binary)
    ):
        raise WorkflowError("CORRECTNESS_CANDIDATE_BINARY_DRIFT")
    build_command = build_stack_command(
        ghcup=ghcup,
        stack=stack,
        stack_yaml=stack_yaml,
        stack_root=stack_root,
        work_dir=work_dir,
        ghc_version="9.10.3",
        operation=[
            "build",
            "--test",
            "--bench",
            "--no-run-tests",
            "--no-run-benchmarks",
            "--pedantic",
            f"--ghc-options={' '.join(options)}",
        ],
    )
    test_command = build_stack_command(
        ghcup=ghcup,
        stack=stack,
        stack_yaml=stack_yaml,
        stack_root=stack_root,
        work_dir=work_dir,
        ghc_version="9.10.3",
        operation=[
            "test",
            "--pedantic",
            f"--ghc-options={' '.join(options)}",
        ],
    )
    fixture_root = _absolute_existing_directory(
        numeric_root / "contract/fixtures",
        label="FIXTURE_ROOT",
    )
    python_runtime = _benchmark_python_runtime()
    comparator = _pin_oracle_comparator(numeric_root)
    python_path_ids = _oracle_compare_path_ids(python_runtime, comparator)
    matrices = (
        (
            "canonical",
            fixture_root / "small/canonical-inputs.v1.json",
            fixture_root / "expected/canonical-results.v1.json",
        ),
        (
            "semantic",
            fixture_root / "invalid/semantic-errors.v1.json",
            fixture_root / "invalid/semantic-errors.expected.v1.json",
        ),
    )
    expected_commands: list[
        tuple[str, list[str], Path, Mapping[str, str] | None]
    ] = [
        ("build", build_command, haskell_root, None),
        ("test", test_command, haskell_root, None),
    ]
    expected_artifact_fields = {
        "matrixId",
        "requestPath",
        "requestSha256",
        "expectedPath",
        "expectedSha256",
        "actualPath",
        "actualSha256",
        "comparisonPath",
        "comparisonSha256",
        "mismatchCount",
        "status",
    }
    environment = _sealed_child_environment(
        ghc_bin=compiler,
        stack_bin=stack,
        python_runtime=python_runtime,
    )
    for artifact, (label, request, expected) in zip(
        receipt["comparisonArtifacts"],
        matrices,
        strict=True,
    ):
        actual = output / f"{label}.actual.json"
        comparison = output / f"{label}.comparison.json"
        if (
            not isinstance(artifact, dict)
            or set(artifact) != expected_artifact_fields
            or artifact.get("matrixId") != label
            or artifact.get("requestPath") != str(request)
            or artifact.get("expectedPath") != str(expected)
            or artifact.get("actualPath") != str(actual)
            or artifact.get("comparisonPath") != str(comparison)
            or artifact.get("mismatchCount") != 0
            or artifact.get("status") != "PASS"
        ):
            raise WorkflowError("CORRECTNESS_COMPARISON_ARTIFACT_INVALID")
        for evidence_path, digest_field, evidence_label in (
            (request, "requestSha256", f"CORRECTNESS_{label.upper()}_REQUEST"),
            (expected, "expectedSha256", f"CORRECTNESS_{label.upper()}_EXPECTED"),
            (actual, "actualSha256", f"CORRECTNESS_{label.upper()}_ACTUAL"),
            (
                comparison,
                "comparisonSha256",
                f"CORRECTNESS_{label.upper()}_COMPARISON",
            ),
        ):
            read_same_fd_json_evidence(
                evidence_path,
                expected_sha256=artifact[digest_field],
                label=evidence_label,
            )
        comparison_payload, comparison_document, _ = _same_fd_json_value(
            comparison,
            label=f"CORRECTNESS_{label.upper()}_COMPARISON",
        )
        if (
            hashlib.sha256(comparison_payload).hexdigest()
            != artifact["comparisonSha256"]
        ):
            raise WorkflowError("CORRECTNESS_COMPARISON_SHA256_DRIFT")
        _comparison_status_document(comparison_document)
        with tempfile.TemporaryDirectory(
            dir=output,
            prefix=f".validate-{label}-",
        ) as temporary:
            recomputed = Path(temporary) / "comparison.json"
            replay_descriptors = _oracle_compare_pass_fds(
                python_runtime,
                comparator,
            )
            replay_identities = _pass_fd_identities(
                replay_descriptors,
                phase=f"{label}-compare-replay",
            )
            replay_command = _oracle_compare_command(
                python_path=python_runtime.fd_path,
                comparator=comparator,
                arguments=[
                    "--expected",
                    str(expected),
                    "--actual",
                    str(actual),
                    "--request",
                    str(request),
                    "--output",
                    str(recomputed),
                ],
            )
            (
                replay_execution_command,
                replay_executable,
                replay_source_route_identity,
            ) = _benchmark_python_subprocess_binding(
                replay_command,
                environment,
                replay_descriptors,
                phase=f"{label}-compare-replay",
                expected_runtime=python_runtime,
            )
            completed = subprocess.run(
                replay_execution_command,
                executable=replay_executable,
                cwd=repo_root,
                env=environment,
                check=False,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                pass_fds=replay_descriptors,
            )
            _assert_benchmark_python_route_unchanged(
                environment=environment,
                phase=f"{label}-compare-replay",
                expected=replay_source_route_identity,
                expected_runtime=python_runtime,
            )
            if (
                completed.returncode != 0
                or recomputed.read_bytes() != comparison_payload
                or _pass_fd_identities(
                    replay_descriptors,
                    phase=f"{label}-compare-replay",
                )
                != replay_identities
            ):
                raise WorkflowError("CORRECTNESS_COMPARISON_REPLAY_DRIFT")
        expected_commands.extend(
            [
                (
                    f"{label}-process",
                    [
                        str(candidate_binary),
                        "--request",
                        str(request),
                        "--fixture-root",
                        str(fixture_root),
                        "--output",
                        str(actual),
                    ],
                    haskell_root,
                    None,
                ),
                (
                    f"{label}-compare",
                    _oracle_compare_command(
                        python_path=python_runtime.fd_path,
                        comparator=comparator,
                        arguments=[
                            "--expected",
                            str(expected),
                            "--actual",
                            str(actual),
                            "--request",
                            str(request),
                            "--output",
                            str(comparison),
                        ],
                    ),
                    repo_root,
                    python_path_ids,
                ),
            ]
        )
    if (
        tuple(phase for phase, _, _, _ in expected_commands)
        != CORRECTNESS_PHASES
    ):
        raise WorkflowError("CORRECTNESS_PHASE_SEQUENCE_DRIFT")
    if len(receipt["commands"]) != len(expected_commands):
        raise WorkflowError("CORRECTNESS_COMMAND_COUNT_DRIFT")
    for record, (phase, argv, cwd, portable_path_ids) in zip(
        receipt["commands"],
        expected_commands,
        strict=True,
    ):
        validate_logged_command_record(
            record,
            expected_phase=phase,
            expected_argv=argv,
            expected_cwd=cwd,
            evidence_directory=output,
            portable_path_ids=portable_path_ids,
        )
    return receipt


def _validate_qualification_artifact(
    path: Path,
    *,
    plan: Mapping[str, Any],
    expected_source_tree_sha256: str,
    expected_commit: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    artifact_path = _absolute_regular(path, label="QUALIFICATION_ARTIFACT")
    artifact_payload, artifact, _ = _same_fd_json_value(
        artifact_path,
        label="QUALIFICATION_ARTIFACT",
        max_bytes=64 * 1024 * 1024,
    )
    configuration, case_order = _qualification_contract(plan)
    expected_fields = {
        "schemaVersion",
        "status",
        "candidateSourceCommit",
        "planPathId",
        "planSha256",
        "selectorConfigSha256",
        "sourceTreeSha256",
        "stackWorkDir",
        "qualificationCaseOrder",
        "plannedProfileOrderBlocks",
        "dockerRoute",
        "blocks",
        "selection",
    }
    numeric_root = Path(__file__).resolve(strict=True).parent.parent.parent
    haskell_root = numeric_root / "haskell"
    repo_root = numeric_root.parents[3]
    plan_path = _absolute_regular(
        numeric_root / "benchmarks/benchmark-plan.v1.json",
        label="QUALIFICATION_PLAN",
    )
    if (
        not isinstance(artifact, dict)
        or set(artifact) != expected_fields
        or artifact_payload
        != canonical_json_bytes(artifact, trailing_newline=True)
        or artifact.get("schemaVersion") != QUALIFICATION_SCHEMA_VERSION
        or artifact.get("status") != "PASS"
        or artifact.get("candidateSourceCommit") != expected_commit
        or artifact.get("planPathId") != "S1_4X_BENCHMARK_PLAN"
        or artifact.get("planSha256") != sha256_file(plan_path)
        or artifact.get("sourceTreeSha256") != expected_source_tree_sha256
        or artifact.get("qualificationCaseOrder") != list(case_order)
        or artifact.get("plannedProfileOrderBlocks")
        != [list(block) for block in PROFILE_ORDER_BLOCKS]
        or artifact.get("selectorConfigSha256")
        != canonical_sha256(configuration)
        or not isinstance(artifact.get("blocks"), list)
        or len(artifact["blocks"]) != len(PROFILE_ORDER_BLOCKS)
    ):
        raise WorkflowError("QUALIFICATION_ARTIFACT_INVALID")
    docker_route_receipt = validate_qualification_docker_route_receipt(
        artifact["dockerRoute"],
        require_owner_exit=True,
    )
    output = artifact_path.parent
    ghcup = _required_environment_path("S1_4X_GHCUP_BIN")
    stack = _required_environment_path("S1_4X_STACK_BIN")
    _required_environment_path("S1_4X_AUTHORITATIVE_GHC_BIN")
    marker_python = _benchmark_python_runtime()
    marker_python_sha256 = marker_python.sha256
    marker_script_source = _absolute_regular(
        Path(__file__).resolve(strict=True),
        label="PROFILE_MARKER_SCRIPT",
    )
    marker_script = _pin_python_script(
        marker_script_source,
        label="PROFILE_MARKER_SCRIPT",
    )
    marker_script_sha256 = marker_script.sha256
    host_validator = _pin_python_script(
        numeric_root / "oracle/validate_environment.py",
        label="HOST_VALIDATOR_PY",
    )
    host_path_ids = {
        str(marker_python.fd_path): _benchmark_python_path_id(marker_python),
        str(host_validator.fd_path): _pinned_file_path_id(host_validator),
    }
    cache_value = os.environ.get("S1_4X_CACHE_ROOT")
    if cache_value is None:
        raise WorkflowError("REQUIRED_ENVIRONMENT_MISSING:S1_4X_CACHE_ROOT")
    cache_root = _absolute_existing_directory(
        Path(cache_value),
        label="CACHE_ROOT",
    )
    stack_root = _absolute_existing_directory(
        isolated_stack_root(
            cache_root,
            purpose="qualification",
            output_path=output,
        ),
        label="QUALIFICATION_STACK_ROOT",
    )
    work_dir = Path(str(artifact.get("stackWorkDir", "")))
    if work_dir != isolated_stack_work_dir(stack_root):
        raise WorkflowError("QUALIFICATION_STACK_WORK_DIR_DRIFT")
    stack_yaml = _absolute_regular(
        haskell_root / "stack.yaml",
        label="AUTHORITATIVE_STACK_YAML",
    )
    expected_profile_fields = {
        "profileId",
        "ghcOptions",
        "optionsSha256",
        "startedAt",
        "finishedAt",
        "hostValidityPath",
        "hostValiditySha256",
        "hostDockerRouteBeforeSha256",
        "hostDockerRouteAfterSha256",
        "hostCommand",
        "rawCriterionPath",
        "rawCriterionSha256",
        "criterionCommand",
        "caseSecondsPerBatch",
        "marker",
    }
    expected_marker_fields = {
        "path",
        "preRunSha256",
        "measurementSha256",
        "pythonPath",
        "pythonPinnedFdPath",
        "pythonSha256",
        "scriptPath",
        "scriptPinnedFdPath",
        "scriptSha256",
        "argv",
        "argvSha256",
        "portableWitness",
    }
    selector_blocks: list[dict[str, Any]] = []
    for index, block in enumerate(artifact["blocks"]):
        planned_order = PROFILE_ORDER_BLOCKS[index]
        if (
            not isinstance(block, dict)
            or set(block)
            != {
                "orderBlock",
                "plannedProfileOrder",
                "actualProfileOrder",
                "profiles",
                "ratios",
            }
            or not isinstance(block.get("profiles"), list)
            or len(block["profiles"]) != 2
            or block.get("orderBlock") != index
            or block.get("plannedProfileOrder") != list(planned_order)
            or block.get("actualProfileOrder") != list(planned_order)
        ):
            raise WorkflowError("QUALIFICATION_ARTIFACT_BLOCK_INVALID")
        raw_reports: dict[str, object] = {}
        for profile_index, profile in enumerate(block["profiles"]):
            expected_profile_id = planned_order[profile_index]
            marker = profile.get("marker") if isinstance(profile, dict) else None
            if (
                not isinstance(profile, dict)
                or set(profile) != expected_profile_fields
                or profile.get("profileId") != expected_profile_id
                or profile.get("ghcOptions")
                != list(profile_options(expected_profile_id))
                or profile.get("optionsSha256")
                != canonical_sha256(
                    list(profile_options(expected_profile_id))
                )
                or not isinstance(marker, dict)
                or set(marker) != expected_marker_fields
                or marker.get("argvSha256")
                != canonical_sha256(marker.get("argv"))
            ):
                raise WorkflowError("QUALIFICATION_PROFILE_EVIDENCE_INVALID")
            _require_iso_utc(
                profile.get("startedAt"),
                label=f"qualification-{index}-{expected_profile_id}-started",
            )
            _require_iso_utc(
                profile.get("finishedAt"),
                label=f"qualification-{index}-{expected_profile_id}-finished",
            )
            prefix = f"block-{index + 1}-{expected_profile_id}"
            host_report_path = Path(str(profile.get("hostValidityPath", "")))
            raw_report_path = Path(str(profile.get("rawCriterionPath", "")))
            marker_path = Path(str(marker.get("path", "")))
            if (
                host_report_path
                != output / f"{prefix}-host-validity.json"
                or raw_report_path
                != output / f"{prefix}-criterion.json"
                or marker_path
                != output / f"{prefix}-measurement-state.json"
            ):
                raise WorkflowError("QUALIFICATION_RAW_PATH_DRIFT")
            host_sha256 = _require_sha256(
                profile.get("hostValiditySha256"),
                label=f"qualification-{prefix}-host",
            )
            host_report = read_same_fd_json_evidence(
                host_report_path,
                expected_sha256=host_sha256,
                label="QUALIFICATION_HOST",
                max_bytes=8 * 1024 * 1024,
            )
            host_record = profile.get("hostCommand")
            if not isinstance(host_record, dict):
                raise WorkflowError("QUALIFICATION_HOST_COMMAND_INVALID")
            host_argv = host_record.get("argv")
            try:
                root_pid_index = host_argv.index("--allowed-process-root-pid")
                root_pid = int(host_argv[root_pid_index + 1])
            except (AttributeError, ValueError, IndexError, TypeError) as exc:
                raise WorkflowError("QUALIFICATION_HOST_ROOT_PID_INVALID") from exc
            if root_pid <= 0:
                raise WorkflowError("QUALIFICATION_HOST_ROOT_PID_INVALID")
            if (
                root_pid != docker_route_receipt["owner"]["pid"]
                or profile.get("hostDockerRouteBeforeSha256")
                != docker_route_receipt["snapshotSha256"]
                or profile.get("hostDockerRouteAfterSha256")
                != docker_route_receipt["snapshotSha256"]
            ):
                raise WorkflowError(
                    "QUALIFICATION_DOCKER_ROUTE_EVIDENCE_INVALID"
                )
            _validate_host_report_document(
                host_report,
                plan=plan,
                root_pid=root_pid,
            )
            host_command = _host_validator_command(
                numeric_root=numeric_root,
                plan=plan,
                output=host_report_path,
                root_pid=root_pid,
                python_bin=marker_python.fd_path,
                validator_script=host_validator.fd_path,
            )
            validate_logged_command_record(
                host_record,
                expected_phase=f"qualification-{prefix}-host",
                expected_argv=host_command,
                expected_cwd=repo_root,
                evidence_directory=output,
                portable_path_ids=host_path_ids,
            )
            marker_sha256 = _require_sha256(
                marker.get("measurementSha256"),
                label=f"qualification-{prefix}-marker",
            )
            measurement = read_same_fd_json_evidence(
                marker_path,
                expected_sha256=marker_sha256,
                label="QUALIFICATION_MARKER",
                max_bytes=1024 * 1024,
            )
            measurement = _validate_profile_marker(
                measurement,
                state="MEASUREMENT",
            )
            marker_path_id = _qualification_marker_path_id(
                order_block=index,
                profile_id=expected_profile_id,
            )
            validate_qualification_command_witness(
                marker.get("portableWitness"),
                marker_argv=marker.get("argv", []),
                marker_path_id=marker_path_id,
                python_source_path=marker_python.source_path,
                python_source_sha256=marker_python_sha256,
                script_source_path=marker_script_source,
                script_source_sha256=marker_script_sha256,
                require_owner_exit=True,
            )
            _validate_qualification_docker_owner_binding(
                docker_route_receipt,
                marker["portableWitness"],
            )
            expected_marker_argv = _qualification_marker_command(
                python_source_path=marker_python.source_path,
                python_pinned_fd_path=Path(
                    str(marker.get("pythonPinnedFdPath", ""))
                ),
                script_pinned_fd_path=Path(
                    str(marker.get("scriptPinnedFdPath", ""))
                ),
                marker_path=marker_path,
            )
            if (
                measurement.get("planSha256") != artifact["planSha256"]
                or measurement.get("selectorConfigSha256")
                != artifact["selectorConfigSha256"]
                or measurement.get("sourceTreeSha256")
                != expected_source_tree_sha256
                or measurement.get("orderBlock") != index
                or measurement.get("profileId") != expected_profile_id
                or measurement.get("qualificationCaseOrder")
                != list(case_order)
                or measurement.get("hostValiditySha256") != host_sha256
                or measurement.get("markerPythonPath")
                != str(marker_python.source_path)
                or measurement.get("markerPythonPinnedFdPath")
                != marker.get("pythonPinnedFdPath")
                or measurement.get("markerPythonSha256")
                != marker_python_sha256
                or measurement.get("markerScriptPath")
                != str(marker_script_source)
                or measurement.get("markerScriptPinnedFdPath")
                != marker.get("scriptPinnedFdPath")
                or measurement.get("markerScriptSha256")
                != marker_script_sha256
                or marker.get("preRunSha256")
                != profile_marker_pre_run_sha256(measurement)
                or marker.get("preRunSha256")
                != measurement.get("preRunSha256")
                or marker.get("pythonPath") != str(marker_python.source_path)
                or marker.get("pythonSha256") != marker_python_sha256
                or marker.get("scriptPath") != str(marker_script_source)
                or marker.get("scriptSha256") != marker_script_sha256
                or marker.get("argv") != measurement.get("markerArgv")
                or marker.get("argvSha256")
                != measurement.get("markerArgvSha256")
                or measurement.get("markerArgv")
                != expected_marker_argv
            ):
                raise WorkflowError("QUALIFICATION_MARKER_EVIDENCE_INVALID")
            raw_sha256 = _require_sha256(
                profile.get("rawCriterionSha256"),
                label=f"qualification-{prefix}-criterion",
            )
            raw_reports[expected_profile_id] = read_same_fd_json_evidence(
                raw_report_path,
                expected_sha256=raw_sha256,
                label="QUALIFICATION_CRITERION",
            )
            criterion_command = _criterion_qualification_command(
                ghcup=ghcup,
                stack=stack,
                stack_yaml=stack_yaml,
                stack_root=stack_root,
                work_dir=work_dir,
                profile_id=expected_profile_id,
                time_limit_seconds=configuration[
                    "criterionTimeLimitSeconds"
                ],
                raw_report=raw_report_path,
                case_order=case_order,
            )
            validate_logged_command_record(
                profile.get("criterionCommand"),
                expected_phase=f"qualification-{prefix}-criterion",
                expected_argv=criterion_command,
                expected_cwd=haskell_root,
                evidence_directory=output,
            )
            for field in (
                "preRunSha256",
                "measurementSha256",
                "pythonSha256",
                "scriptSha256",
                "argvSha256",
            ):
                _require_sha256(marker.get(field), label=f"qualification-marker-{field}")
        ratios, estimates = recompute_qualification_ratios(
            raw_reports,
            expected_case_order=case_order,
        )
        for profile in block["profiles"]:
            if (
                profile.get("caseSecondsPerBatch")
                != estimates[profile["profileId"]]
            ):
                raise WorkflowError("QUALIFICATION_CASE_ESTIMATE_DRIFT")
        if block.get("ratios") != ratios:
            raise WorkflowError("QUALIFICATION_RATIO_DRIFT")
        selector_blocks.append(
            {
                "orderBlock": block["orderBlock"],
                "plannedProfileOrder": block["plannedProfileOrder"],
                "actualProfileOrder": block["actualProfileOrder"],
                "ratios": ratios,
            }
        )
    selection = select_profile_from_blocks(
        selector_blocks,
        case_order=case_order,
        profile_order_blocks=PROFILE_ORDER_BLOCKS,
    )
    if artifact.get("selection") != selection:
        raise WorkflowError("QUALIFICATION_SELECTION_DRIFT")
    return artifact, selection


def _select_profile(arguments: argparse.Namespace) -> None:
    haskell_root = Path(__file__).resolve(strict=True).parent.parent
    numeric_root = haskell_root.parent
    repo_root = numeric_root.parents[3]
    evidence = _load_haskell_evidence(haskell_root)
    source_tree_sha256 = evidence.benchmark_source_tree_sha256(haskell_root)
    plan_path = _absolute_regular(
        numeric_root / "benchmarks/benchmark-plan.v1.json",
        label="QUALIFICATION_PLAN",
    )
    plan = strict_json_load(plan_path)
    configuration, _ = _qualification_contract(plan)
    environment_paths = {}
    for name in (
        "S1_4X_HASKELL_BASELINE_CORRECTNESS",
        "S1_4X_HASKELL_OPTIMIZED_CORRECTNESS",
        "S1_4X_HASKELL_QUALIFICATION_ARTIFACT",
    ):
        value = os.environ.get(name)
        if value is None:
            raise WorkflowError(f"REQUIRED_ENVIRONMENT_MISSING:{name}")
        environment_paths[name] = Path(value)
    subject_documents = (
        strict_json_load(
                _absolute_regular(
                    environment_paths[
                        "S1_4X_HASKELL_BASELINE_CORRECTNESS"
                    ],
                    label="BASELINE_CORRECTNESS_RECEIPT",
                )
            ),
        strict_json_load(
                _absolute_regular(
                    environment_paths[
                        "S1_4X_HASKELL_OPTIMIZED_CORRECTNESS"
                    ],
                    label="OPTIMIZED_CORRECTNESS_RECEIPT",
                )
            ),
        strict_json_load(
                _absolute_regular(
                    environment_paths[
                        "S1_4X_HASKELL_QUALIFICATION_ARTIFACT"
                    ],
                    label="QUALIFICATION_ARTIFACT",
                )
            ),
    )
    subject_commits = [
        document.get("candidateSourceCommit")
        if isinstance(document, dict)
        else None
        for document in subject_documents
    ]
    subject_commit = subject_commits[0]
    if (
        any(value != subject_commit for value in subject_commits[1:])
        or not isinstance(subject_commit, str)
        or COMMIT_PATTERN.fullmatch(subject_commit) is None
    ):
        raise WorkflowError("SELECTED_PROFILE_SUBJECT_COMMIT_INVALID")
    candidate_commit = subject_commit
    profile_path = haskell_root / "selected-profile.v1.json"
    manifest_path = haskell_root / "source-inputs.v1.json"
    fixed_point = resolve_selected_profile_commit_fixed_point(
        repo_root,
        mode=arguments.mode,
        expected_subject_commit=candidate_commit,
        profile_relative_path=profile_path.relative_to(repo_root).as_posix(),
        manifest_relative_path=manifest_path.relative_to(repo_root).as_posix(),
    )
    baseline = _validate_correctness_receipt(
        environment_paths["S1_4X_HASKELL_BASELINE_CORRECTNESS"],
        expected_profile_id="baseline-o0-fasm",
        expected_source_tree_sha256=source_tree_sha256,
        expected_commit=candidate_commit,
    )
    optimized = _validate_correctness_receipt(
        environment_paths["S1_4X_HASKELL_OPTIMIZED_CORRECTNESS"],
        expected_profile_id="optimized-o2-fasm",
        expected_source_tree_sha256=source_tree_sha256,
        expected_commit=candidate_commit,
    )
    qualification_path = environment_paths[
        "S1_4X_HASKELL_QUALIFICATION_ARTIFACT"
    ]
    qualification, selection = _validate_qualification_artifact(
        qualification_path,
        plan=plan,
        expected_source_tree_sha256=source_tree_sha256,
        expected_commit=candidate_commit,
    )
    if qualification.get("planSha256") != sha256_file(plan_path):
        raise WorkflowError("QUALIFICATION_PLAN_SHA256_DRIFT")
    selected_correctness_path = (
        environment_paths["S1_4X_HASKELL_OPTIMIZED_CORRECTNESS"]
        if selection["profileId"] == "optimized-o2-fasm"
        else environment_paths["S1_4X_HASKELL_BASELINE_CORRECTNESS"]
    )
    selected_receipt = optimized if selection["profileId"] == "optimized-o2-fasm" else baseline
    profile_document = build_final_profile_document(
        selection=selection,
        source_tree_sha256=source_tree_sha256,
        full_correctness_sha256=sha256_file(selected_correctness_path),
        qualification_plan_sha256=sha256_file(plan_path),
        qualification_artifact_sha256=sha256_file(qualification_path),
        selector_config_sha256=canonical_sha256(configuration),
        compiler_sha256=selected_receipt["compilerSha256"],
    )
    if arguments.mode == "materialize":
        pending = strict_json_load(profile_path)
        if pending.get("schemaVersion") != "s1.4x-haskell-selected-profile-pending-v1":
            raise WorkflowError("SELECTED_PROFILE_NOT_PENDING")
        evidence.validate_selected_profile_document(
            pending,
            expected_compiler_sha256=evidence.AUTHORITATIVE_GHC_SHA256,
            expected_source_tree_sha256=source_tree_sha256,
            expected_qualification_plan_sha256=sha256_file(plan_path),
            expected_selector_config_sha256=canonical_sha256(configuration),
        )
        evidence.validate_source_manifest(haskell_root, manifest_path)
        lock_path = profile_path.with_name(f"{profile_path.name}.materialize.lock")
        try:
            descriptor = os.open(
                lock_path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
        except FileExistsError as exc:
            raise WorkflowError("SELECTED_PROFILE_MATERIALIZATION_BUSY") from exc
        try:
            os.close(descriptor)
            atomic_replace_json(profile_path, profile_document)
            manifest = evidence.build_source_manifest(haskell_root)
            atomic_replace_json(manifest_path, manifest)
        finally:
            lock_path.unlink(missing_ok=True)
    else:
        actual = strict_json_load(profile_path)
        if (
            actual != profile_document
            or profile_path.read_bytes()
            != canonical_json_bytes(profile_document, trailing_newline=True)
        ):
            raise WorkflowError("SELECTED_PROFILE_CHECK_FAILED")
        evidence.validate_source_manifest(haskell_root, manifest_path)
    print(
        json.dumps(
            {
                "mode": arguments.mode,
                "materializationCommit": fixed_point["materializationCommit"],
                "preMaterializationSubjectCommit": fixed_point[
                    "preMaterializationSubjectCommit"
                ],
                "profileId": profile_document["profileId"],
                "profileSha256": sha256_file(profile_path),
                "status": "PASS",
            },
            sort_keys=True,
        )
    )


def _mark_measurement(arguments: argparse.Namespace) -> None:
    result = mark_profile_measurement_entered(arguments.qualification)
    if result["status"] != "MEASUREMENT_ENTERED":
        raise WorkflowError("PROFILE_MARKER_TRANSITION_FAILED")
    print(json.dumps({"status": "MEASUREMENT_ENTERED"}))


def _portable_compatibility_command_record(
    *,
    command: Sequence[str],
    ghcup: Path,
    stack: Path,
    stack_yaml: Path,
    stack_root: Path,
    started_at: str,
    ended_at: str,
    exit_code: int,
    stdout_sha256: str,
    stderr_sha256: str,
    phase: str,
) -> dict[str, Any]:
    """Local absolute argv를 typed result용 portable path ID로 투영한다."""

    replacements = {
        str(ghcup): "GHCUP_0_2_6_2_LINUX_X86_64",
        str(stack): "GHCUP_STACK_3_11_1",
        str(stack_yaml): "HASKELL_GHC_914_STACK_YAML",
        str(stack_root): "CACHE_ROOT_COMPATIBILITY_STACK_ROOT",
        str(isolated_stack_work_dir(stack_root)): (
            "HASKELL_COMPAT_STACK_WORK_DIR"
        ),
    }
    portable_argv = [replacements.get(argument, argument) for argument in command]
    if any(argument.startswith("/") for argument in portable_argv):
        raise WorkflowError("COMPATIBILITY_RESULT_ARGV_NOT_PORTABLE")
    return {
        "argv": portable_argv,
        "cwdId": "HASKELL_COMPAT_ROOT",
        "endedAt": ended_at,
        "exitCode": exit_code,
        "phase": phase,
        "startedAt": started_at,
        "stderrSha256": stderr_sha256,
        "stdoutSha256": stdout_sha256,
    }


def _build_current_compatibility_failure_result(
    *,
    candidate_source_tree_sha256: str,
    command_record: Mapping[str, Any],
    current_plan: Mapping[str, Any],
    evidence_sha256: str,
    haskell_root: Path,
) -> dict[str, Any]:
    result = _compatibility_result_base(
        candidate_source_tree_sha256=candidate_source_tree_sha256,
        command_records=[command_record],
        current_plan=current_plan,
        haskell_root=haskell_root,
    )
    result.update(
        {
            "candidateCompile": {
                "evidenceSha256": None,
                "status": "NOT_RUN",
            },
            "crossReplay": {
                "evidenceSha256": None,
                "mismatchCount": None,
                "status": "NOT_RUN",
            },
            "dependencyQualification": {
                "evidenceSha256": evidence_sha256,
                "status": "FAIL",
            },
            "downstreamNotRun": [
                "candidateCompile",
                "fullCorrectness",
                "stableErrorReplay",
                "processReplay",
                "oracleReplay",
                "crossReplay",
            ],
            "failurePhase": "dependency",
            "fullCorrectness": {
                "evidenceSha256": None,
                "mismatchCount": None,
                "status": "NOT_RUN",
            },
            "minimalReproducerSha256": evidence_sha256,
            "oracleReplay": {
                "evidenceSha256": None,
                "mismatchCount": None,
                "status": "NOT_RUN",
            },
            "processReplay": {
                "evidenceSha256": None,
                "mismatchCount": None,
                "status": "NOT_RUN",
            },
            "result": "FAIL_FROZEN_DEPENDENCY",
            "stableErrorReplay": {
                "evidenceSha256": None,
                "mismatchCount": None,
                "status": "NOT_RUN",
            },
        }
    )
    return result


def _portable_compatibility_replay_records(
    records: Sequence[Mapping[str, Any]],
    *,
    haskell_root: Path,
    numeric_root: Path,
    output: Path,
    stack_root: Path,
    ghcup: Path,
    stack: Path,
) -> list[dict[str, Any]]:
    """Replay command records의 local path를 안정적인 portable ID로 바꾼다."""

    exact = {
        str(ghcup): "GHCUP_0_2_6_2_LINUX_X86_64",
        str(stack): "GHCUP_STACK_3_11_1",
    }
    prefixes = (
        (str(output) + "/", "OUTPUT_ROOT/"),
        (str(stack_root) + "/", "CACHE_ROOT_COMPATIBILITY_STACK_ROOT/"),
        (str(haskell_root) + "/", "HASKELL_COMPAT_ROOT/"),
        (str(numeric_root) + "/", "NUMERIC_ROOT/"),
    )

    def portable(argument: str) -> str:
        if argument in exact:
            return exact[argument]
        if argument == str(stack_root):
            return "CACHE_ROOT_COMPATIBILITY_STACK_ROOT"
        if argument == str(haskell_root):
            return "HASKELL_COMPAT_ROOT"
        if argument == str(numeric_root):
            return "NUMERIC_ROOT"
        for prefix, replacement in prefixes:
            if argument.startswith(prefix):
                return replacement + argument[len(prefix) :]
        if argument.startswith("/"):
            raise WorkflowError(f"COMPATIBILITY_REPLAY_PATH_NOT_PORTABLE:{argument}")
        return argument

    portable_records: list[dict[str, Any]] = []
    for record in records:
        portable_records.append(
            {
                "argv": [portable(str(argument)) for argument in record["argv"]],
                "cwdId": "HASKELL_COMPAT_ROOT",
                "endedAt": record["endedAt"],
                "exitCode": record["exitCode"],
                "phase": record["phase"],
                "startedAt": record["startedAt"],
                "stderrSha256": record["stderrSha256"],
                "stdoutSha256": record["stdoutSha256"],
            }
        )
    return portable_records


def _build_current_compatibility_phase_failure_result(
    *,
    candidate_source_tree_sha256: str,
    command_records: Sequence[Mapping[str, Any]],
    current_plan: Mapping[str, Any],
    phase_evidence_sha256: Mapping[str, str],
    failed_phase: str,
    failed_evidence_sha256: str,
    haskell_root: Path,
) -> dict[str, Any]:
    """Solve 뒤 candidate/replay 실패를 ordered typed closure로 발행한다."""

    downstream = list(COMPATIBILITY_REPLAY_PHASES[1:])
    if failed_phase not in downstream:
        raise WorkflowError("COMPATIBILITY_FAILURE_PHASE_INVALID")
    failed_index = downstream.index(failed_phase)
    completed = downstream[:failed_index]
    not_run = downstream[failed_index + 1 :]
    for phase in completed:
        _require_sha256(
            phase_evidence_sha256.get(phase),
            label=f"completed-compatibility-{phase}",
        )
    _require_sha256(failed_evidence_sha256, label="failed-compatibility-phase")
    dependency_sha256 = _require_sha256(
        phase_evidence_sha256.get("dependency"),
        label="compatibility-dependency",
    )
    result = _compatibility_result_base(
        candidate_source_tree_sha256=candidate_source_tree_sha256,
        command_records=command_records,
        current_plan=current_plan,
        haskell_root=haskell_root,
    )
    phase_results: dict[str, dict[str, Any]] = {}
    for phase in downstream:
        if phase == failed_phase:
            phase_results[phase] = (
                {
                    "evidenceSha256": failed_evidence_sha256,
                    "status": "FAIL",
                }
                if phase == "candidateCompile"
                else {
                    "evidenceSha256": failed_evidence_sha256,
                    "mismatchCount": 1,
                    "status": "FAIL",
                }
            )
        elif phase in completed:
            phase_results[phase] = (
                {
                    "evidenceSha256": phase_evidence_sha256[phase],
                    "status": "PASS",
                }
                if phase == "candidateCompile"
                else {
                    "evidenceSha256": phase_evidence_sha256[phase],
                    "mismatchCount": 0,
                    "status": "PASS",
                }
            )
        else:
            phase_results[phase] = (
                {"evidenceSha256": None, "status": "NOT_RUN"}
                if phase == "candidateCompile"
                else {
                    "evidenceSha256": None,
                    "mismatchCount": None,
                    "status": "NOT_RUN",
                }
            )
    result.update(
        {
            **phase_results,
            "dependencyQualification": {
                "evidenceSha256": dependency_sha256,
                "status": "PASS",
            },
            "downstreamNotRun": not_run,
            "failurePhase": failed_phase,
            "minimalReproducerSha256": failed_evidence_sha256,
            "result": "FAIL_CANDIDATE_SOURCE",
        }
    )
    return result


def _current_compatibility_evidence(
    *,
    haskell_root: Path,
    stack_yaml: Path,
    stack_root: Path,
    stdout_path: Path,
    stderr_path: Path,
    authoritative_boot_dump: Path,
    compatibility_boot_dump: Path,
    pantry_db: Path,
    started_at: str,
    ended_at: str,
    exit_code: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    standard_output = _absolute_regular(
        stdout_path,
        label="CURRENT_COMPATIBILITY_STDOUT",
    )
    standard_error = _absolute_regular(
        stderr_path,
        label="CURRENT_COMPATIBILITY_STDERR",
    )
    if exit_code != 1:
        raise WorkflowError("CURRENT_COMPATIBILITY_EXIT_NOT_FROZEN_FAILURE")
    authoritative_boot = _absolute_regular(
        authoritative_boot_dump,
        label="CURRENT_AUTHORITATIVE_BOOT_DUMP",
    )
    compatibility_boot = _absolute_regular(
        compatibility_boot_dump,
        label="CURRENT_COMPATIBILITY_BOOT_DUMP",
    )
    current_pantry_db = _absolute_regular(
        pantry_db,
        label="CURRENT_COMPATIBILITY_PANTRY_DB",
    )
    _require_iso_utc(started_at, label="compatibility-started")
    _require_iso_utc(ended_at, label="compatibility-ended")
    numeric_root = haskell_root.parent
    repo_root = numeric_root.parents[3]
    evidence_helper = _load_haskell_evidence(haskell_root)
    source_tree_sha256 = evidence_helper.benchmark_source_tree_sha256(haskell_root)
    candidate_commit = _repo_commit(repo_root)
    ghcup = _required_environment_path("S1_4X_GHCUP_BIN")
    stack = _required_environment_path("S1_4X_STACK_BIN")
    command = build_stack_command(
        ghcup=ghcup,
        stack=stack,
        stack_yaml=stack_yaml,
        stack_root=stack_root,
        work_dir=isolated_stack_work_dir(stack_root),
        ghc_version="9.14.1",
        operation=[
            "build",
            "--dry-run",
            "--test",
            "--bench",
            "--no-run-tests",
            "--no-run-benchmarks",
        ],
    )
    compatibility_helper = _load_compatibility_evidence(haskell_root)
    try:
        current_plan = compatibility_helper.build_current_failure_proof(
            haskell_root=haskell_root,
            stack_yaml=stack_yaml,
            authoritative_boot_dump=authoritative_boot,
            compatibility_boot_dump=compatibility_boot,
            pantry_db=current_pantry_db,
            stderr_path=standard_error,
        )
    except (
        compatibility_helper.CompatibilityEvidenceError,
        compatibility_helper.sqlite3.DatabaseError,
    ) as exc:
        raise WorkflowError(f"CURRENT_COMPATIBILITY_PROOF_INVALID:{exc}") from exc
    lock_hashes = current_compatibility_lock_hashes(haskell_root)
    current_evidence = {
        "schemaVersion": CURRENT_COMPATIBILITY_EVIDENCE_VERSION,
        "status": "PASS",
        "classification": "FAIL_FROZEN_DEPENDENCY",
        "nonScoring": True,
        "performanceInput": False,
        "candidateSourceCommit": candidate_commit,
        "candidateSourceTreeSha256": source_tree_sha256,
        "stackYamlSha256": sha256_file(stack_yaml),
        **lock_hashes,
        "compatibilityPolicySha256": sha256_file(
            numeric_root / "contract/ghc-compatibility-policy.v1.json"
        ),
        "command": {
            "argv": command,
            "argvSha256": canonical_sha256(command),
            "startedAt": started_at,
            "endedAt": ended_at,
            "exitCode": exit_code,
            "stdoutSha256": sha256_file(standard_output),
            "stderrSha256": sha256_file(standard_error),
        },
        "currentPlan": current_plan,
        "failureLeaf": current_plan["failureLeaf"],
        "rawEvidence": {
            "authoritativeBootDumpPath": str(authoritative_boot),
            "authoritativeBootDumpSha256": sha256_file(authoritative_boot),
            "compatibilityBootDumpPath": str(compatibility_boot),
            "compatibilityBootDumpSha256": sha256_file(compatibility_boot),
            "pantryDbPath": str(current_pantry_db),
            "pantryDbSha256": sha256_file(current_pantry_db),
            "stdoutPath": str(standard_output),
            "stdoutSha256": sha256_file(standard_output),
            "stdoutSize": standard_output.stat().st_size,
            "stderrPath": str(standard_error),
            "stderrSha256": sha256_file(standard_error),
            "stderrSize": standard_error.stat().st_size,
        },
    }
    current_evidence_sha256 = hashlib.sha256(
        canonical_json_bytes(current_evidence, trailing_newline=True)
    ).hexdigest()
    command_record = _portable_compatibility_command_record(
        command=command,
        ghcup=ghcup,
        stack=stack,
        stack_yaml=stack_yaml,
        stack_root=stack_root,
        started_at=started_at,
        ended_at=ended_at,
        exit_code=exit_code,
        stdout_sha256=sha256_file(standard_output),
        stderr_sha256=sha256_file(standard_error),
        phase="dependency",
    )
    result = _build_current_compatibility_failure_result(
        candidate_source_tree_sha256=source_tree_sha256,
        command_record=command_record,
        current_plan=current_plan,
        evidence_sha256=current_evidence_sha256,
        haskell_root=haskell_root,
    )
    validate_current_compatibility_status(
        result,
        expected_source_tree_sha256=source_tree_sha256,
    )
    return current_evidence, result


def _write_compatibility_phase_evidence(
    *,
    output: Path,
    phase: str,
    status: str,
    records: Sequence[Mapping[str, Any]],
    artifacts: Mapping[str, Any],
    mismatch_count: int,
) -> tuple[Path, str]:
    if (
        phase not in COMPATIBILITY_REPLAY_PHASES
        or status not in {"PASS", "FAIL"}
        or type(mismatch_count) is not int
        or mismatch_count < 0
    ):
        raise WorkflowError("COMPATIBILITY_PHASE_EVIDENCE_INPUT_INVALID")
    document = {
        "schemaVersion": "s1.4x-ghc-current-phase-evidence-v1",
        "phase": phase,
        "status": status,
        "mismatchCount": mismatch_count,
        "commands": [dict(record) for record in records],
        "artifacts": dict(artifacts),
    }
    path = output / f"phase-{phase}.v1.json"
    atomic_write_json_exclusive(path, document)
    return path, sha256_file(path)


def _portable_solve_record(
    *,
    command: Sequence[str],
    ghcup: Path,
    stack: Path,
    stack_yaml: Path,
    stack_root: Path,
    started_at: str,
    ended_at: str,
    stdout_path: Path,
    stderr_path: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    actual = {
        "phase": "dependency",
        "logId": "dependency",
        "argv": list(command),
        "argvSha256": canonical_sha256(list(command)),
        "startedAt": started_at,
        "endedAt": ended_at,
        "exitCode": 0,
        "stdoutPath": str(stdout_path),
        "stdoutSha256": sha256_file(stdout_path),
        "stderrPath": str(stderr_path),
        "stderrSha256": sha256_file(stderr_path),
    }
    portable = _portable_compatibility_command_record(
        command=command,
        ghcup=ghcup,
        stack=stack,
        stack_yaml=stack_yaml,
        stack_root=stack_root,
        started_at=started_at,
        ended_at=ended_at,
        exit_code=0,
        stdout_sha256=actual["stdoutSha256"],
        stderr_sha256=actual["stderrSha256"],
        phase="dependency",
    )
    return actual, portable


def _publish_current_compatibility_replay(
    *,
    output: Path,
    result: Mapping[str, Any],
    current_plan: Mapping[str, Any],
    source_tree_sha256: str,
    candidate_commit: str,
    phase_evidence: Mapping[str, Mapping[str, Any]],
    raw_inputs: Mapping[str, str],
) -> None:
    classification = str(result["result"])
    companion = {
        "schemaVersion": CURRENT_COMPATIBILITY_PASS_EVIDENCE_VERSION,
        "status": "PASS" if classification == "PASS" else "FAIL",
        "classification": classification,
        "nonScoring": True,
        "performanceInput": False,
        "candidateSourceCommit": candidate_commit,
        "candidateSourceTreeSha256": source_tree_sha256,
        "currentPlan": dict(current_plan),
        "rawInputs": dict(raw_inputs),
        "phaseEvidence": {
            phase: dict(evidence)
            for phase, evidence in phase_evidence.items()
        },
    }
    companion_path = output / (
        "compatibility-pass.v1.json"
        if classification == "PASS"
        else "compatibility-candidate-failure.v1.json"
    )
    result_path = output / "ghc-9.14.1-compatibility.v1.json"
    atomic_write_json_exclusive(companion_path, companion)
    atomic_write_json_exclusive(result_path, dict(result))
    print(
        json.dumps(
            {
                "classification": classification,
                "evidencePath": str(companion_path),
                "evidenceSha256": sha256_file(companion_path),
                "resultSha256": sha256_file(result_path),
                "status": "PASS",
            },
            sort_keys=True,
        )
    )


def _verify_cross_replay(arguments: argparse.Namespace) -> None:
    canonical = _comparison_status(
        _absolute_regular(
            arguments.canonical_comparison,
            label="CROSS_CANONICAL_COMPARISON",
        )
    )
    semantic = _comparison_status(
        _absolute_regular(
            arguments.semantic_comparison,
            label="CROSS_SEMANTIC_COMPARISON",
        )
    )
    document = {
        "schemaVersion": "s1.4x-ghc-current-cross-replay-v1",
        "canonicalComparisonSha256": sha256_file(
            arguments.canonical_comparison
        ),
        "semanticComparisonSha256": sha256_file(
            arguments.semantic_comparison
        ),
        "mismatchCount": canonical["mismatchCount"]
        + semantic["mismatchCount"],
        "status": "PASS",
    }
    if document["mismatchCount"] != 0:
        raise WorkflowError("COMPATIBILITY_CROSS_REPLAY_MISMATCH")
    atomic_write_json_exclusive(arguments.output, document)
    print(json.dumps({"status": "PASS"}, sort_keys=True))


def _replay_compatibility_success(arguments: argparse.Namespace) -> None:
    python_runtime = arguments.benchmark_python_runtime
    output = _absolute_existing_directory(
        arguments.output_dir,
        label="COMPATIBILITY_OUTPUT",
    )
    initial_paths = {
        arguments.stdout.resolve(strict=True),
        arguments.stderr.resolve(strict=True),
        arguments.authoritative_boot_dump.resolve(strict=True),
        arguments.compatibility_boot_dump.resolve(strict=True),
    }
    if {path.resolve(strict=True) for path in output.iterdir()} != initial_paths:
        raise WorkflowError("COMPATIBILITY_SUCCESS_OUTPUT_NOT_PRISTINE")
    haskell_root = Path(__file__).resolve(strict=True).parent.parent
    numeric_root = haskell_root.parent
    repo_root = numeric_root.parents[3]
    stack_yaml = _absolute_regular(
        arguments.stack_yaml,
        label="COMPATIBILITY_STACK_YAML",
    )
    expected_yaml = (haskell_root / "stack-ghc-9.14.1.yaml").resolve(strict=True)
    if stack_yaml != expected_yaml or arguments.exit_code != 0:
        raise WorkflowError("COMPATIBILITY_SUCCESS_SOLVE_INVALID")
    stack_root = _absolute_existing_directory(
        arguments.stack_root,
        label="COMPATIBILITY_STACK_ROOT",
    )
    work_dir = isolated_stack_work_dir(stack_root)
    standard_output = _absolute_regular(
        arguments.stdout,
        label="COMPATIBILITY_SOLVE_STDOUT",
    )
    standard_error = _absolute_regular(
        arguments.stderr,
        label="COMPATIBILITY_SOLVE_STDERR",
    )
    authoritative_boot_dump = _absolute_regular(
        arguments.authoritative_boot_dump,
        label="CURRENT_AUTHORITATIVE_BOOT_DUMP",
    )
    compatibility_boot_dump = _absolute_regular(
        arguments.compatibility_boot_dump,
        label="CURRENT_COMPATIBILITY_BOOT_DUMP",
    )
    pantry_db = _absolute_regular(
        arguments.pantry_db,
        label="CURRENT_COMPATIBILITY_PANTRY_DB",
    )
    _require_iso_utc(arguments.started_at, label="compatibility-started")
    _require_iso_utc(arguments.ended_at, label="compatibility-ended")
    candidate_commit = _repo_commit(repo_root)
    haskell_evidence = _load_haskell_evidence(haskell_root)
    source_tree_sha256 = haskell_evidence.benchmark_source_tree_sha256(
        haskell_root
    )
    compatibility_helper = _load_compatibility_evidence(haskell_root)
    try:
        current_plan = compatibility_helper.build_current_plan_proof(
            haskell_root=haskell_root,
            stack_yaml=stack_yaml,
            authoritative_boot_dump=authoritative_boot_dump,
            compatibility_boot_dump=compatibility_boot_dump,
            pantry_db=pantry_db,
        )
    except (
        compatibility_helper.CompatibilityEvidenceError,
        compatibility_helper.sqlite3.DatabaseError,
    ) as exc:
        raise WorkflowError(f"CURRENT_COMPATIBILITY_PLAN_INVALID:{exc}") from exc
    ghcup = _required_environment_path("S1_4X_GHCUP_BIN")
    stack = _required_environment_path("S1_4X_STACK_BIN")
    compatibility_ghc = _required_environment_path("S1_4X_LATEST_GHC_BIN")
    environment = _sealed_child_environment(
        ghc_bin=compatibility_ghc,
        stack_bin=stack,
        python_runtime=python_runtime,
    )
    solve_command = build_stack_command(
        ghcup=ghcup,
        stack=stack,
        stack_yaml=stack_yaml,
        stack_root=stack_root,
        work_dir=work_dir,
        ghc_version="9.14.1",
        operation=[
            "build",
            "--dry-run",
            "--test",
            "--bench",
            "--no-run-tests",
            "--no-run-benchmarks",
        ],
    )
    solve_actual, solve_portable = _portable_solve_record(
        command=solve_command,
        ghcup=ghcup,
        stack=stack,
        stack_yaml=stack_yaml,
        stack_root=stack_root,
        started_at=arguments.started_at,
        ended_at=arguments.ended_at,
        stdout_path=standard_output,
        stderr_path=standard_error,
    )
    _, dependency_sha256 = _write_compatibility_phase_evidence(
        output=output,
        phase="dependency",
        status="PASS",
        records=[solve_actual],
        artifacts={
            "currentPlanSha256": canonical_sha256(current_plan),
            "pantryDbSha256": sha256_file(pantry_db),
        },
        mismatch_count=0,
    )
    phase_evidence: dict[str, dict[str, Any]] = {
        "dependency": {
            "path": str(output / "phase-dependency.v1.json"),
            "sha256": dependency_sha256,
            "status": "PASS",
            "mismatchCount": 0,
        }
    }
    actual_records: list[dict[str, Any]] = [solve_actual]
    portable_records: list[dict[str, Any]] = [solve_portable]
    phase_sha256: dict[str, str] = {"dependency": dependency_sha256}
    raw_inputs = {
        "stackYamlPath": str(stack_yaml),
        "stackRootPath": str(stack_root),
        "stackWorkDir": str(work_dir),
        "authoritativeBootDumpPath": str(authoritative_boot_dump),
        "compatibilityBootDumpPath": str(compatibility_boot_dump),
        "pantryDbPath": str(pantry_db),
        "solveStdoutPath": str(standard_output),
        "solveStderrPath": str(standard_error),
    }

    def publish_failed(
        phase: str,
        records: Sequence[Mapping[str, Any]],
        artifacts: Mapping[str, Any],
    ) -> None:
        path, digest = _write_compatibility_phase_evidence(
            output=output,
            phase=phase,
            status="FAIL",
            records=records,
            artifacts=artifacts,
            mismatch_count=1,
        )
        phase_evidence[phase] = {
            "path": str(path),
            "sha256": digest,
            "status": "FAIL",
            "mismatchCount": 1,
        }
        result = _build_current_compatibility_phase_failure_result(
            candidate_source_tree_sha256=source_tree_sha256,
            command_records=portable_records,
            current_plan=current_plan,
            phase_evidence_sha256=phase_sha256,
            failed_phase=phase,
            failed_evidence_sha256=digest,
            haskell_root=haskell_root,
        )
        _publish_current_compatibility_replay(
            output=output,
            result=result,
            current_plan=current_plan,
            source_tree_sha256=source_tree_sha256,
            candidate_commit=candidate_commit,
            phase_evidence=phase_evidence,
            raw_inputs=raw_inputs,
        )

    def run_phase_command(
        *,
        phase: str,
        log_id: str,
        command: Sequence[str],
        cwd: Path,
        pass_fds: Sequence[int] = (),
        portable_path_ids: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        record = _run_compatibility_logged(
            command,
            cwd=cwd,
            environment=environment,
            phase=phase,
            log_id=log_id,
            output_directory=output,
            pass_fds=pass_fds,
            portable_path_ids=portable_path_ids,
            benchmark_python_runtime=python_runtime,
        )
        actual_records.append(record)
        portable_records.extend(
            _portable_compatibility_replay_records(
                [record],
                haskell_root=haskell_root,
                numeric_root=numeric_root,
                output=output,
                stack_root=stack_root,
                ghcup=ghcup,
                stack=stack,
            )
        )
        return record

    compile_command = build_stack_command(
        ghcup=ghcup,
        stack=stack,
        stack_yaml=stack_yaml,
        stack_root=stack_root,
        work_dir=work_dir,
        ghc_version="9.14.1",
        operation=[
            "build",
            "--test",
            "--bench",
            "--no-run-tests",
            "--no-run-benchmarks",
            "--pedantic",
        ],
    )
    compile_record = run_phase_command(
        phase="candidateCompile",
        log_id="candidate-compile",
        command=compile_command,
        cwd=haskell_root,
    )
    if compile_record["exitCode"] != 0:
        publish_failed("candidateCompile", [compile_record], {})
        return
    candidate_binary = _find_candidate_binary(
        haskell_root,
        work_dir=work_dir,
        ghc_version="9.14.1",
    )
    compile_path, compile_sha256 = _write_compatibility_phase_evidence(
        output=output,
        phase="candidateCompile",
        status="PASS",
        records=[compile_record],
        artifacts={
            "candidateBinaryPath": str(candidate_binary),
            "candidateBinarySha256": sha256_file(candidate_binary),
        },
        mismatch_count=0,
    )
    phase_sha256["candidateCompile"] = compile_sha256
    phase_evidence["candidateCompile"] = {
        "path": str(compile_path),
        "sha256": compile_sha256,
        "status": "PASS",
        "mismatchCount": 0,
    }

    correctness_command = build_stack_command(
        ghcup=ghcup,
        stack=stack,
        stack_yaml=stack_yaml,
        stack_root=stack_root,
        work_dir=work_dir,
        ghc_version="9.14.1",
        operation=["test", "--pedantic"],
    )
    correctness_record = run_phase_command(
        phase="fullCorrectness",
        log_id="full-correctness",
        command=correctness_command,
        cwd=haskell_root,
    )
    if correctness_record["exitCode"] != 0:
        publish_failed("fullCorrectness", [correctness_record], {})
        return
    correctness_path, correctness_sha256 = _write_compatibility_phase_evidence(
        output=output,
        phase="fullCorrectness",
        status="PASS",
        records=[correctness_record],
        artifacts={"candidateBinarySha256": sha256_file(candidate_binary)},
        mismatch_count=0,
    )
    phase_sha256["fullCorrectness"] = correctness_sha256
    phase_evidence["fullCorrectness"] = {
        "path": str(correctness_path),
        "sha256": correctness_sha256,
        "status": "PASS",
        "mismatchCount": 0,
    }
    fixture_root = _absolute_existing_directory(
        numeric_root / "contract/fixtures",
        label="FIXTURE_ROOT",
    )
    comparator = _pin_oracle_comparator(numeric_root)
    profile_script = _pin_python_script(
        Path(__file__).resolve(strict=True),
        label="PROFILE_WORKFLOW_PY",
    )
    compare_path_ids = _oracle_compare_path_ids(python_runtime, comparator)
    profile_path_ids = {
        str(python_runtime.fd_path): _benchmark_python_path_id(python_runtime),
        str(profile_script.fd_path): _pinned_file_path_id(profile_script),
    }

    semantic_request = _absolute_regular(
        fixture_root / "invalid/semantic-errors.v1.json",
        label="SEMANTIC_REQUEST",
    )
    semantic_expected = _absolute_regular(
        fixture_root / "invalid/semantic-errors.expected.v1.json",
        label="SEMANTIC_EXPECTED",
    )
    semantic_actual = output / "semantic.actual.json"
    semantic_comparison = output / "semantic.comparison.json"
    semantic_process_command = [
        str(candidate_binary),
        "--request",
        str(semantic_request),
        "--fixture-root",
        str(fixture_root),
        "--output",
        str(semantic_actual),
    ]
    semantic_process = run_phase_command(
        phase="stableErrorReplay",
        log_id="stable-error-process",
        command=semantic_process_command,
        cwd=haskell_root,
    )
    if semantic_process["exitCode"] != 0:
        publish_failed("stableErrorReplay", [semantic_process], {})
        return
    semantic_compare_command = _oracle_compare_command(
        python_path=python_runtime.fd_path,
        comparator=comparator,
        arguments=[
            "--expected",
            str(semantic_expected),
            "--actual",
            str(semantic_actual),
            "--request",
            str(semantic_request),
            "--output",
            str(semantic_comparison),
        ],
    )
    semantic_compare = run_phase_command(
        phase="stableErrorReplay",
        log_id="stable-error-compare",
        command=semantic_compare_command,
        cwd=repo_root,
        pass_fds=_oracle_compare_pass_fds(
            python_runtime,
            comparator,
        ),
        portable_path_ids=compare_path_ids,
    )
    if semantic_compare["exitCode"] != 0:
        publish_failed(
            "stableErrorReplay",
            [semantic_process, semantic_compare],
            {"actualSha256": sha256_file(semantic_actual)},
        )
        return
    _comparison_status(semantic_comparison)
    stable_path, stable_sha256 = _write_compatibility_phase_evidence(
        output=output,
        phase="stableErrorReplay",
        status="PASS",
        records=[semantic_process, semantic_compare],
        artifacts={
            "actualSha256": sha256_file(semantic_actual),
            "comparisonSha256": sha256_file(semantic_comparison),
        },
        mismatch_count=0,
    )
    phase_sha256["stableErrorReplay"] = stable_sha256
    phase_evidence["stableErrorReplay"] = {
        "path": str(stable_path),
        "sha256": stable_sha256,
        "status": "PASS",
        "mismatchCount": 0,
    }

    canonical_request = _absolute_regular(
        fixture_root / "small/canonical-inputs.v1.json",
        label="CANONICAL_REQUEST",
    )
    canonical_expected = _absolute_regular(
        fixture_root / "expected/canonical-results.v1.json",
        label="CANONICAL_EXPECTED",
    )
    canonical_actual = output / "canonical.actual.json"
    canonical_comparison = output / "canonical.comparison.json"
    process_command = [
        str(candidate_binary),
        "--request",
        str(canonical_request),
        "--fixture-root",
        str(fixture_root),
        "--output",
        str(canonical_actual),
    ]
    process_record = run_phase_command(
        phase="processReplay",
        log_id="process-replay",
        command=process_command,
        cwd=haskell_root,
    )
    if process_record["exitCode"] != 0:
        publish_failed("processReplay", [process_record], {})
        return
    process_path, process_sha256 = _write_compatibility_phase_evidence(
        output=output,
        phase="processReplay",
        status="PASS",
        records=[process_record],
        artifacts={"actualSha256": sha256_file(canonical_actual)},
        mismatch_count=0,
    )
    phase_sha256["processReplay"] = process_sha256
    phase_evidence["processReplay"] = {
        "path": str(process_path),
        "sha256": process_sha256,
        "status": "PASS",
        "mismatchCount": 0,
    }
    oracle_command = _oracle_compare_command(
        python_path=python_runtime.fd_path,
        comparator=comparator,
        arguments=[
            "--expected",
            str(canonical_expected),
            "--actual",
            str(canonical_actual),
            "--request",
            str(canonical_request),
            "--output",
            str(canonical_comparison),
        ],
    )
    oracle_record = run_phase_command(
        phase="oracleReplay",
        log_id="oracle-replay",
        command=oracle_command,
        cwd=repo_root,
        pass_fds=_oracle_compare_pass_fds(
            python_runtime,
            comparator,
        ),
        portable_path_ids=compare_path_ids,
    )
    if oracle_record["exitCode"] != 0:
        publish_failed("oracleReplay", [oracle_record], {})
        return
    _comparison_status(canonical_comparison)
    oracle_path, oracle_sha256 = _write_compatibility_phase_evidence(
        output=output,
        phase="oracleReplay",
        status="PASS",
        records=[oracle_record],
        artifacts={"comparisonSha256": sha256_file(canonical_comparison)},
        mismatch_count=0,
    )
    phase_sha256["oracleReplay"] = oracle_sha256
    phase_evidence["oracleReplay"] = {
        "path": str(oracle_path),
        "sha256": oracle_sha256,
        "status": "PASS",
        "mismatchCount": 0,
    }
    cross_artifact = output / "cross-replay.v1.json"
    cross_command = [
        str(python_runtime.fd_path),
        str(profile_script.fd_path),
        "verify-cross-replay",
        "--canonical-comparison",
        str(canonical_comparison),
        "--semantic-comparison",
        str(semantic_comparison),
        "--output",
        str(cross_artifact),
    ]
    cross_record = run_phase_command(
        phase="crossReplay",
        log_id="cross-replay",
        command=cross_command,
        cwd=repo_root,
        pass_fds=(
            python_runtime.descriptor,
            profile_script.descriptor,
        ),
        portable_path_ids=profile_path_ids,
    )
    if cross_record["exitCode"] != 0:
        publish_failed("crossReplay", [cross_record], {})
        return
    cross_document = strict_json_load(
        _absolute_regular(cross_artifact, label="CROSS_REPLAY_ARTIFACT")
    )
    if (
        not isinstance(cross_document, dict)
        or cross_document.get("status") != "PASS"
        or cross_document.get("mismatchCount") != 0
    ):
        raise WorkflowError("COMPATIBILITY_CROSS_REPLAY_INVALID")
    cross_path, cross_sha256 = _write_compatibility_phase_evidence(
        output=output,
        phase="crossReplay",
        status="PASS",
        records=[cross_record],
        artifacts={"crossReplaySha256": sha256_file(cross_artifact)},
        mismatch_count=0,
    )
    phase_sha256["crossReplay"] = cross_sha256
    phase_evidence["crossReplay"] = {
        "path": str(cross_path),
        "sha256": cross_sha256,
        "status": "PASS",
        "mismatchCount": 0,
    }
    if (
        haskell_evidence.benchmark_source_tree_sha256(haskell_root)
        != source_tree_sha256
        or _repo_commit(repo_root) != candidate_commit
    ):
        raise WorkflowError("COMPATIBILITY_SOURCE_CHANGED_DURING_REPLAY")
    result = build_current_compatibility_pass_result(
        candidate_source_tree_sha256=source_tree_sha256,
        command_records=portable_records,
        phase_evidence_sha256=phase_sha256,
        current_plan=current_plan,
        haskell_root=haskell_root,
    )
    validate_current_compatibility_status(
        result,
        expected_source_tree_sha256=source_tree_sha256,
    )
    _publish_current_compatibility_replay(
        output=output,
        result=result,
        current_plan=current_plan,
        source_tree_sha256=source_tree_sha256,
        candidate_commit=candidate_commit,
        phase_evidence=phase_evidence,
        raw_inputs=raw_inputs,
    )


def _capture_compatibility_failure(arguments: argparse.Namespace) -> None:
    output = _absolute_existing_directory(
        arguments.output_dir,
        label="COMPATIBILITY_OUTPUT",
    )
    if any(output.iterdir()):
        allowed = {
            arguments.stdout.resolve(strict=True),
            arguments.stderr.resolve(strict=True),
            arguments.authoritative_boot_dump.resolve(strict=True),
            arguments.compatibility_boot_dump.resolve(strict=True),
        }
        actual = {path.resolve(strict=True) for path in output.iterdir()}
        if actual != allowed:
            raise WorkflowError("COMPATIBILITY_OUTPUT_CONTAINS_UNKNOWN_FILES")
    haskell_root = Path(__file__).resolve(strict=True).parent.parent
    stack_yaml = _absolute_regular(
        arguments.stack_yaml,
        label="COMPATIBILITY_STACK_YAML",
    )
    expected_yaml = (haskell_root / "stack-ghc-9.14.1.yaml").resolve(strict=True)
    if stack_yaml != expected_yaml:
        raise WorkflowError("COMPATIBILITY_STACK_YAML_NOT_FROZEN")
    stack_root = _absolute_existing_directory(
        arguments.stack_root,
        label="COMPATIBILITY_STACK_ROOT",
    )
    evidence, result = _current_compatibility_evidence(
        haskell_root=haskell_root,
        stack_yaml=stack_yaml,
        stack_root=stack_root,
        stdout_path=arguments.stdout,
        stderr_path=arguments.stderr,
        authoritative_boot_dump=arguments.authoritative_boot_dump,
        compatibility_boot_dump=arguments.compatibility_boot_dump,
        pantry_db=arguments.pantry_db,
        started_at=arguments.started_at,
        ended_at=arguments.ended_at,
        exit_code=arguments.exit_code,
    )
    evidence_path = output / "compatibility-failure.v1.json"
    result_path = output / "ghc-9.14.1-compatibility.v1.json"
    atomic_write_json_exclusive(evidence_path, evidence)
    atomic_write_json_exclusive(result_path, result)
    print(
        json.dumps(
            {
                "classification": "FAIL_FROZEN_DEPENDENCY",
                "evidenceSha256": sha256_file(evidence_path),
                "resultSha256": sha256_file(result_path),
                "status": "PASS",
            },
            sort_keys=True,
        )
    )


def _validate_compatibility(arguments: argparse.Namespace) -> None:
    result_path = _absolute_regular(
        arguments.result,
        label="CURRENT_COMPATIBILITY_RESULT",
    )
    result = strict_json_load(result_path)
    if not isinstance(result, dict):
        raise WorkflowError("CURRENT_COMPATIBILITY_RESULT_INVALID")
    classification = result.get("result")
    evidence_name = {
        "FAIL_FROZEN_DEPENDENCY": "compatibility-failure.v1.json",
        "PASS": "compatibility-pass.v1.json",
        "FAIL_CANDIDATE_SOURCE": "compatibility-candidate-failure.v1.json",
    }.get(classification)
    if evidence_name is None:
        raise WorkflowError("CURRENT_COMPATIBILITY_CLASSIFICATION_INVALID")
    evidence_path = result_path.with_name(evidence_name)
    evidence = strict_json_load(
        _absolute_regular(
            evidence_path,
            label="CURRENT_COMPATIBILITY_EVIDENCE",
        )
    )
    haskell_root = Path(__file__).resolve(strict=True).parent.parent
    numeric_root = haskell_root.parent
    repo_root = numeric_root.parents[3]
    stack_yaml = _absolute_regular(
        haskell_root / "stack-ghc-9.14.1.yaml",
        label="COMPATIBILITY_STACK_YAML",
    )
    if classification != "FAIL_FROZEN_DEPENDENCY":
        expected_fields = {
            "schemaVersion",
            "status",
            "classification",
            "nonScoring",
            "performanceInput",
            "candidateSourceCommit",
            "candidateSourceTreeSha256",
            "currentPlan",
            "rawInputs",
            "phaseEvidence",
        }
        if (
            not isinstance(evidence, dict)
            or set(evidence) != expected_fields
            or evidence.get("schemaVersion")
            != CURRENT_COMPATIBILITY_PASS_EVIDENCE_VERSION
            or evidence.get("classification") != classification
            or evidence.get("status")
            != ("PASS" if classification == "PASS" else "FAIL")
            or evidence.get("nonScoring") is not True
            or evidence.get("performanceInput") is not False
        ):
            raise WorkflowError("CURRENT_COMPATIBILITY_REPLAY_EVIDENCE_INVALID")
        raw_inputs = evidence.get("rawInputs")
        phase_evidence = evidence.get("phaseEvidence")
        if not isinstance(raw_inputs, dict) or not isinstance(
            phase_evidence,
            dict,
        ):
            raise WorkflowError("CURRENT_COMPATIBILITY_REPLAY_INPUT_INVALID")
        stack_root = _absolute_existing_directory(
            Path(raw_inputs.get("stackRootPath", "")),
            label="COMPATIBILITY_STACK_ROOT",
        )
        if _absolute_regular(
            Path(raw_inputs.get("stackYamlPath", "")),
            label="COMPATIBILITY_STACK_YAML",
        ) != stack_yaml:
            raise WorkflowError("COMPATIBILITY_STACK_YAML_NOT_FROZEN")
        authoritative_boot_dump = _absolute_regular(
            Path(raw_inputs.get("authoritativeBootDumpPath", "")),
            label="CURRENT_AUTHORITATIVE_BOOT_DUMP",
        )
        compatibility_boot_dump = _absolute_regular(
            Path(raw_inputs.get("compatibilityBootDumpPath", "")),
            label="CURRENT_COMPATIBILITY_BOOT_DUMP",
        )
        pantry_db = _absolute_regular(
            Path(raw_inputs.get("pantryDbPath", "")),
            label="CURRENT_COMPATIBILITY_PANTRY_DB",
        )
        compatibility_helper = _load_compatibility_evidence(haskell_root)
        try:
            current_plan = compatibility_helper.build_current_plan_proof(
                haskell_root=haskell_root,
                stack_yaml=stack_yaml,
                authoritative_boot_dump=authoritative_boot_dump,
                compatibility_boot_dump=compatibility_boot_dump,
                pantry_db=pantry_db,
            )
        except (
            compatibility_helper.CompatibilityEvidenceError,
            compatibility_helper.sqlite3.DatabaseError,
        ) as exc:
            raise WorkflowError(
                f"CURRENT_COMPATIBILITY_PLAN_INVALID:{exc}"
            ) from exc
        if current_plan != evidence.get("currentPlan"):
            raise WorkflowError("CURRENT_COMPATIBILITY_PLAN_DRIFT")
        expected_phase_order = list(COMPATIBILITY_REPLAY_PHASES)
        if classification == "FAIL_CANDIDATE_SOURCE":
            failed_phase = result.get("failurePhase")
            if failed_phase not in expected_phase_order[1:]:
                raise WorkflowError("CURRENT_COMPATIBILITY_FAILURE_PHASE_INVALID")
            expected_phase_order = expected_phase_order[
                : expected_phase_order.index(failed_phase) + 1
            ]
        if set(phase_evidence) != set(expected_phase_order):
            raise WorkflowError("CURRENT_COMPATIBILITY_PHASE_SET_DRIFT")
        actual_records: list[dict[str, Any]] = []
        phase_sha256: dict[str, str] = {}
        for phase in expected_phase_order:
            binding = phase_evidence[phase]
            if (
                not isinstance(binding, dict)
                or set(binding)
                != {"path", "sha256", "status", "mismatchCount"}
            ):
                raise WorkflowError("CURRENT_COMPATIBILITY_PHASE_BINDING_INVALID")
            phase_path = _absolute_regular(
                Path(binding["path"]),
                label=f"COMPATIBILITY_PHASE_{phase}",
            )
            if phase_path.parent != result_path.parent:
                raise WorkflowError("COMPATIBILITY_PHASE_PATH_ESCAPE")
            if sha256_file(phase_path) != binding["sha256"]:
                raise WorkflowError("COMPATIBILITY_PHASE_SHA256_DRIFT")
            phase_document = strict_json_load(phase_path)
            if (
                not isinstance(phase_document, dict)
                or set(phase_document)
                != {
                    "schemaVersion",
                    "phase",
                    "status",
                    "mismatchCount",
                    "commands",
                    "artifacts",
                }
                or phase_document.get("schemaVersion")
                != "s1.4x-ghc-current-phase-evidence-v1"
                or phase_document.get("phase") != phase
                or phase_document.get("status") != binding["status"]
                or phase_document.get("mismatchCount")
                != binding["mismatchCount"]
                or not isinstance(phase_document.get("commands"), list)
                or not phase_document["commands"]
            ):
                raise WorkflowError("CURRENT_COMPATIBILITY_PHASE_EVIDENCE_INVALID")
            for record in phase_document["commands"]:
                if (
                    not isinstance(record, dict)
                    or set(record)
                    != {
                        "phase",
                        "logId",
                        "argv",
                        "argvSha256",
                        "startedAt",
                        "endedAt",
                        "exitCode",
                        "stdoutPath",
                        "stdoutSha256",
                        "stderrPath",
                        "stderrSha256",
                    }
                    or record.get("phase") != phase
                    or record.get("argvSha256")
                    != canonical_sha256(record.get("argv"))
                ):
                    raise WorkflowError(
                        "CURRENT_COMPATIBILITY_COMMAND_RECORD_INVALID"
                    )
                _require_iso_utc(
                    record.get("startedAt"),
                    label=f"{phase}-started",
                )
                _require_iso_utc(
                    record.get("endedAt"),
                    label=f"{phase}-ended",
                )
                for stream in ("stdout", "stderr"):
                    stream_path = _absolute_regular(
                        Path(record[f"{stream}Path"]),
                        label=f"{phase}-{stream}",
                    )
                    if (
                        stream_path.parent != result_path.parent
                        or sha256_file(stream_path)
                        != record[f"{stream}Sha256"]
                    ):
                        raise WorkflowError(
                            "CURRENT_COMPATIBILITY_COMMAND_LOG_DRIFT"
                        )
                actual_records.append(record)
            if binding["status"] == "PASS" and any(
                record["exitCode"] != 0
                for record in phase_document["commands"]
            ):
                raise WorkflowError("CURRENT_COMPATIBILITY_PASS_EXIT_DRIFT")
            if binding["status"] == "FAIL" and all(
                record["exitCode"] == 0
                for record in phase_document["commands"]
            ):
                raise WorkflowError("CURRENT_COMPATIBILITY_FAIL_EXIT_DRIFT")
            phase_sha256[phase] = binding["sha256"]
        ghcup = _required_environment_path("S1_4X_GHCUP_BIN")
        stack = _required_environment_path("S1_4X_STACK_BIN")
        dependency_record = actual_records[0]
        dependency_portable = _portable_compatibility_command_record(
            command=dependency_record["argv"],
            ghcup=ghcup,
            stack=stack,
            stack_yaml=stack_yaml,
            stack_root=stack_root,
            started_at=dependency_record["startedAt"],
            ended_at=dependency_record["endedAt"],
            exit_code=dependency_record["exitCode"],
            stdout_sha256=dependency_record["stdoutSha256"],
            stderr_sha256=dependency_record["stderrSha256"],
            phase="dependency",
        )
        portable_records = [dependency_portable]
        portable_records.extend(
            _portable_compatibility_replay_records(
                actual_records[1:],
                haskell_root=haskell_root,
                numeric_root=numeric_root,
                output=result_path.parent,
                stack_root=stack_root,
                ghcup=ghcup,
                stack=stack,
            )
        )
        source_tree_sha256 = _load_haskell_evidence(
            haskell_root
        ).benchmark_source_tree_sha256(haskell_root)
        if (
            source_tree_sha256 != evidence["candidateSourceTreeSha256"]
            or _repo_commit(repo_root) != evidence["candidateSourceCommit"]
        ):
            raise WorkflowError("CURRENT_COMPATIBILITY_SUBJECT_DRIFT")
        if classification == "PASS":
            rebuilt_result = build_current_compatibility_pass_result(
                candidate_source_tree_sha256=source_tree_sha256,
                command_records=portable_records,
                phase_evidence_sha256=phase_sha256,
                current_plan=current_plan,
                haskell_root=haskell_root,
            )
        else:
            failed_phase = result["failurePhase"]
            rebuilt_result = _build_current_compatibility_phase_failure_result(
                candidate_source_tree_sha256=source_tree_sha256,
                command_records=portable_records,
                current_plan=current_plan,
                phase_evidence_sha256=phase_sha256,
                failed_phase=failed_phase,
                failed_evidence_sha256=phase_sha256[failed_phase],
                haskell_root=haskell_root,
            )
        validate_current_compatibility_status(
            rebuilt_result,
            expected_source_tree_sha256=source_tree_sha256,
        )
        if (
            result != rebuilt_result
            or evidence_path.read_bytes()
            != canonical_json_bytes(evidence, trailing_newline=True)
            or result_path.read_bytes()
            != canonical_json_bytes(result, trailing_newline=True)
        ):
            raise WorkflowError("CURRENT_COMPATIBILITY_PACKET_DRIFT")
        print(
            json.dumps(
                {
                    "classification": classification,
                    "evidenceSha256": sha256_file(evidence_path),
                    "resultSha256": sha256_file(result_path),
                    "status": "PASS",
                },
                sort_keys=True,
            )
        )
        return
    if (
        not isinstance(evidence, dict)
        or evidence.get("schemaVersion")
        != CURRENT_COMPATIBILITY_EVIDENCE_VERSION
        or evidence.get("status") != "PASS"
        or evidence.get("classification") != "FAIL_FROZEN_DEPENDENCY"
        or evidence.get("nonScoring") is not True
        or evidence.get("performanceInput") is not False
    ):
        raise WorkflowError("CURRENT_COMPATIBILITY_EVIDENCE_INVALID")
    command = evidence.get("command")
    raw = evidence.get("rawEvidence")
    if not isinstance(command, dict) or not isinstance(raw, dict):
        raise WorkflowError("CURRENT_COMPATIBILITY_COMMAND_INVALID")
    stdout_path = _absolute_regular(
        Path(raw.get("stdoutPath", "")),
        label="CURRENT_COMPATIBILITY_STDOUT",
    )
    stderr_path = _absolute_regular(
        Path(raw.get("stderrPath", "")),
        label="CURRENT_COMPATIBILITY_STDERR",
    )
    authoritative_boot_dump = _absolute_regular(
        Path(raw.get("authoritativeBootDumpPath", "")),
        label="CURRENT_AUTHORITATIVE_BOOT_DUMP",
    )
    compatibility_boot_dump = _absolute_regular(
        Path(raw.get("compatibilityBootDumpPath", "")),
        label="CURRENT_COMPATIBILITY_BOOT_DUMP",
    )
    pantry_db = _absolute_regular(
        Path(raw.get("pantryDbPath", "")),
        label="CURRENT_COMPATIBILITY_PANTRY_DB",
    )
    rebuilt_evidence, rebuilt_result = _current_compatibility_evidence(
        haskell_root=haskell_root,
        stack_yaml=stack_yaml,
        stack_root=_absolute_existing_directory(
            Path(command["argv"][11]),
            label="COMPATIBILITY_STACK_ROOT",
        ),
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        authoritative_boot_dump=authoritative_boot_dump,
        compatibility_boot_dump=compatibility_boot_dump,
        pantry_db=pantry_db,
        started_at=command["startedAt"],
        ended_at=command["endedAt"],
        exit_code=command["exitCode"],
    )
    if (
        evidence != rebuilt_evidence
        or result != rebuilt_result
        or evidence_path.read_bytes()
        != canonical_json_bytes(evidence, trailing_newline=True)
        or result_path.read_bytes()
        != canonical_json_bytes(result, trailing_newline=True)
    ):
        raise WorkflowError("CURRENT_COMPATIBILITY_PACKET_DRIFT")
    print(
        json.dumps(
            {
                "classification": result["result"],
                "evidenceSha256": sha256_file(evidence_path),
                "resultSha256": sha256_file(result_path),
                "status": "PASS",
            },
            sort_keys=True,
        )
    )


def _copy_oci_context(
    *,
    context: Path,
    containerfile: Path,
    binary: Path,
    fixture_root: Path,
) -> dict[str, str]:
    if context.exists() or context.is_symlink():
        raise WorkflowError("OCI_CONTEXT_ALREADY_EXISTS")
    _absolute_regular(containerfile, label="OCI_CONTAINERFILE_SOURCE")
    _absolute_regular(binary, label="OCI_BINARY_SOURCE", executable=True)
    _absolute_existing_directory(fixture_root, label="OCI_FIXTURE_SOURCE")
    for path in fixture_root.rglob("*"):
        if path.is_symlink():
            raise WorkflowError(f"OCI_FIXTURE_SYMLINK_FORBIDDEN:{path}")
    containerfile_payload, _ = _same_fd_bytes_snapshot(
        containerfile,
        label="OCI_CONTAINERFILE_SOURCE",
        max_bytes=1024 * 1024,
    )
    binary_payload, _ = _same_fd_bytes_snapshot(
        binary,
        label="OCI_BINARY_SOURCE",
        max_bytes=512 * 1024 * 1024,
    )
    source_fixture_sha256 = _regular_tree_sha256(
        fixture_root,
        label="OCI_FIXTURE_SOURCE",
    )
    context.mkdir(mode=0o700)
    staged_containerfile = context / "Containerfile"
    with staged_containerfile.open("xb") as stream:
        stream.write(containerfile_payload)
    staged_binary = context / "s1-4x-haskell"
    with staged_binary.open("xb") as stream:
        stream.write(binary_payload)
    staged_binary.chmod(0o555)
    shutil.copytree(
        fixture_root,
        context / "fixtures",
        copy_function=shutil.copy2,
    )
    snapshot = {
        "binarySha256": hashlib.sha256(binary_payload).hexdigest(),
        "containerfileSha256": hashlib.sha256(
            containerfile_payload
        ).hexdigest(),
        "fixtureTreeSha256": source_fixture_sha256,
    }
    _validate_oci_context_snapshot(context, expected=snapshot)
    return snapshot


def _validate_oci_context_snapshot(
    context: Path,
    *,
    expected: Mapping[str, str],
) -> dict[str, str]:
    """Docker build context의 exact staged bytes와 tree를 다시 검증한다."""

    if set(expected) != {
        "binarySha256",
        "containerfileSha256",
        "fixtureTreeSha256",
    }:
        raise WorkflowError("OCI_CONTEXT_SNAPSHOT_INPUT_INVALID")
    for label, value in expected.items():
        _require_sha256(value, label=f"oci-context-{label}")
    root = _absolute_existing_directory(context, label="OCI_CONTEXT")
    if {path.name for path in root.iterdir()} != {
        "Containerfile",
        "fixtures",
        "s1-4x-haskell",
    }:
        raise WorkflowError("OCI_CONTEXT_ENTRY_SET_INVALID")
    containerfile_payload, _ = _same_fd_bytes_snapshot(
        root / "Containerfile",
        label="OCI_CONTEXT_CONTAINERFILE",
        max_bytes=1024 * 1024,
    )
    binary_payload, _ = _same_fd_bytes_snapshot(
        root / "s1-4x-haskell",
        label="OCI_CONTEXT_BINARY",
        max_bytes=512 * 1024 * 1024,
    )
    actual = {
        "binarySha256": hashlib.sha256(binary_payload).hexdigest(),
        "containerfileSha256": hashlib.sha256(
            containerfile_payload
        ).hexdigest(),
        "fixtureTreeSha256": _regular_tree_sha256(
            root / "fixtures",
            label="OCI_FIXTURE_CONTEXT",
        ),
    }
    if actual != dict(expected):
        raise WorkflowError("OCI_CONTEXT_SNAPSHOT_DRIFT")
    return actual


def _regular_tree_sha256(root: Path, *, label: str) -> str:
    """Symlink 없는 regular tree의 relative path와 raw bytes를 canonical hash한다."""

    directory = _absolute_existing_directory(root, label=label)
    entries: list[bytes] = []
    for path in sorted(
        directory.rglob("*"),
        key=lambda item: item.relative_to(directory).as_posix().encode(),
    ):
        relative = path.relative_to(directory).as_posix()
        if path.is_symlink():
            raise WorkflowError(f"{label}_TREE_SYMLINK:{relative}")
        if path.is_dir():
            continue
        if not path.is_file():
            raise WorkflowError(f"{label}_TREE_ENTRY_INVALID:{relative}")
        entries.append(
            relative.encode("utf-8")
            + b"\0"
            + sha256_file(path).encode("ascii")
            + b"\n"
        )
    if not entries:
        raise WorkflowError(f"{label}_TREE_EMPTY")
    return hashlib.sha256(b"".join(entries)).hexdigest()


def _snapshot_pinned_oci_docker_client(
    client: PinnedDockerClient,
) -> tuple[bytes, os.stat_result]:
    """Retained Docker FD의 exact bytes/stat을 pathname 재개방 없이 검증한다."""

    expected_path_id = (
        f"S1_4X_DOCKER_CLIENT_SHA256_{client.sha256.upper()}"
        if isinstance(client, PinnedDockerClient)
        and SHA256_PATTERN.fullmatch(client.sha256) is not None
        else None
    )
    if (
        not isinstance(client, PinnedDockerClient)
        or client.descriptor < 3
        or client.fd_path != Path(f"/proc/self/fd/{client.descriptor}")
        or client.path_id != expected_path_id
    ):
        raise WorkflowError("OCI_DOCKER_CLIENT_PIN_INVALID")
    try:
        before = os.fstat(client.descriptor)
    except OSError as exc:
        raise WorkflowError("OCI_DOCKER_CLIENT_FD_NOT_LIVE") from exc
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_mode & 0o111 == 0
        or before.st_size < 0
        or before.st_size > 64 * 1024 * 1024
        or before.st_nlink != 1
        or _stat_identity(before) != dict(client.identity)
        or before.st_mode != client.mode
    ):
        raise WorkflowError("OCI_DOCKER_CLIENT_FD_IDENTITY_INVALID")
    digest = hashlib.sha256()
    chunks: list[bytes] = []
    offset = 0
    while offset < before.st_size:
        try:
            chunk = os.pread(
                client.descriptor,
                min(1024 * 1024, before.st_size - offset),
                offset,
            )
        except OSError as exc:
            raise WorkflowError("OCI_DOCKER_CLIENT_FD_READ_FAILED") from exc
        if not chunk:
            raise WorkflowError("OCI_DOCKER_CLIENT_FD_SHORT_READ")
        digest.update(chunk)
        chunks.append(chunk)
        offset += len(chunk)
    try:
        after = os.fstat(client.descriptor)
    except OSError as exc:
        raise WorkflowError("OCI_DOCKER_CLIENT_FD_NOT_LIVE") from exc
    if (
        _stat_identity(after) != dict(client.identity)
        or digest.hexdigest() != client.sha256
    ):
        raise WorkflowError("OCI_DOCKER_CLIENT_FD_CHANGED")
    return b"".join(chunks), before


def pin_oci_docker_client(
    path: Path,
    *,
    expected_sha256: str,
) -> PinnedDockerClient:
    """Caller SHA와 일치하는 executable inode를 OCI 종료까지 retained FD로 연다."""

    expected = _require_sha256(expected_sha256, label="docker")
    source = _absolute_regular(
        path,
        label="S1_4X_DOCKER_BIN",
        executable=True,
    )
    try:
        descriptor = os.open(
            source,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
    except OSError as exc:
        raise WorkflowError("OCI_DOCKER_CLIENT_PIN_OPEN_FAILED") from exc
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_mode & 0o111 == 0
            or before.st_size < 0
            or before.st_size > 64 * 1024 * 1024
            or before.st_nlink != 1
        ):
            raise WorkflowError("OCI_DOCKER_CLIENT_PIN_INVALID")
        digest = hashlib.sha256()
        offset = 0
        while offset < before.st_size:
            chunk = os.pread(
                descriptor,
                min(1024 * 1024, before.st_size - offset),
                offset,
            )
            if not chunk:
                raise WorkflowError("OCI_DOCKER_CLIENT_PIN_SHORT_READ")
            digest.update(chunk)
            offset += len(chunk)
        after = os.fstat(descriptor)
        current = os.stat(source, follow_symlinks=False)
        identity = _stat_identity(before)
        if (
            _stat_identity(after) != identity
            or _stat_identity(current) != identity
        ):
            raise WorkflowError("OCI_DOCKER_CLIENT_CHANGED_DURING_PIN")
        actual_sha256 = digest.hexdigest()
        if actual_sha256 != expected:
            raise WorkflowError("DOCKER_SHA256_MISMATCH")
        return PinnedDockerClient(
            source_path=source,
            fd_path=Path(f"/proc/self/fd/{descriptor}"),
            descriptor=descriptor,
            sha256=actual_sha256,
            mode=before.st_mode,
            identity=identity,
            path_id=(
                f"S1_4X_DOCKER_CLIENT_SHA256_{actual_sha256.upper()}"
            ),
        )
    except BaseException:
        os.close(descriptor)
        raise


def pin_qualification_docker_client_from_environment() -> PinnedDockerClient:
    """Qualification도 OCI와 같은 caller-approved Docker path/SHA 계약을 쓴다."""

    source = _required_environment_path("S1_4X_DOCKER_BIN")
    expected_sha256 = _require_sha256(
        os.environ.get("S1_4X_DOCKER_SHA256"),
        label="docker",
    )
    return pin_oci_docker_client(
        source,
        expected_sha256=expected_sha256,
    )


def prepare_qualification_docker_route(
    output_root: Path,
    *,
    docker_client: PinnedDockerClient,
) -> QualificationDockerRoute:
    """Retained owner FD를 fresh output의 유일한 `docker` route로 만든다."""

    output = _absolute_existing_directory(
        output_root,
        label="QUALIFICATION_DOCKER_OUTPUT",
    )
    _snapshot_pinned_oci_docker_client(docker_client)
    owner_pid = os.getpid()
    owner_start_ticks = _process_start_ticks(owner_pid)
    owner_uid = os.getuid()
    try:
        proc_owner = os.stat(f"/proc/{owner_pid}", follow_symlinks=False)
        output_stat = os.lstat(output)
    except OSError as exc:
        raise WorkflowError("QUALIFICATION_DOCKER_OWNER_NOT_LIVE") from exc
    if (
        proc_owner.st_uid != owner_uid
        or output_stat.st_uid != owner_uid
        or not stat.S_ISDIR(output_stat.st_mode)
        or stat.S_IMODE(output_stat.st_mode) != 0o700
    ):
        raise WorkflowError("QUALIFICATION_DOCKER_OWNER_IDENTITY_DRIFT")
    host_tools = output / "host-tools"
    docker_link = host_tools / "docker"
    docker_config = output / "docker-config"
    if (
        host_tools.exists()
        or host_tools.is_symlink()
        or docker_link.exists()
        or docker_link.is_symlink()
        or docker_config.exists()
        or docker_config.is_symlink()
    ):
        raise WorkflowError("QUALIFICATION_DOCKER_ROUTE_ALREADY_EXISTS")
    try:
        host_tools.mkdir(mode=0o700)
        docker_config.mkdir(mode=0o700)
        docker_link_target = (
            f"/proc/{owner_pid}/fd/{docker_client.descriptor}"
        )
        docker_link.symlink_to(docker_link_target)
        directory_stat = os.lstat(host_tools)
        link_stat = os.lstat(docker_link)
        target_stat = os.stat(docker_link)
        docker_config_stat = os.lstat(docker_config)
    except OSError as exc:
        raise WorkflowError("QUALIFICATION_DOCKER_ROUTE_CREATE_FAILED") from exc
    if (
        not stat.S_ISDIR(directory_stat.st_mode)
        or stat.S_IMODE(directory_stat.st_mode) != 0o700
        or directory_stat.st_uid != owner_uid
        or not stat.S_ISLNK(link_stat.st_mode)
        or link_stat.st_uid != owner_uid
        or _stat_identity(target_stat) != dict(docker_client.identity)
        or not stat.S_ISDIR(docker_config_stat.st_mode)
        or stat.S_IMODE(docker_config_stat.st_mode) != 0o700
        or docker_config_stat.st_uid != owner_uid
    ):
        raise WorkflowError("QUALIFICATION_DOCKER_ROUTE_IDENTITY_INVALID")
    return QualificationDockerRoute(
        output_root=output,
        host_tools_directory=host_tools,
        docker_link=docker_link,
        docker_link_target=docker_link_target,
        docker_config_directory=docker_config,
        owner_pid=owner_pid,
        owner_start_ticks=owner_start_ticks,
        owner_uid=owner_uid,
        output_identity=_directory_anchor_identity(output_stat),
        directory_identity=_stat_identity(directory_stat),
        link_identity=_stat_identity(link_stat),
        docker_config_identity=_stat_identity(docker_config_stat),
    )


def _qualification_docker_snapshot_document(
    route: QualificationDockerRoute,
    *,
    docker_client: PinnedDockerClient,
    docker_payload: bytes,
    output_stat: os.stat_result,
    directory_stat: os.stat_result,
    link_stat: os.stat_result,
    docker_config_stat: os.stat_result,
) -> dict[str, Any]:
    """Local inode 값은 hash 안에만 넣고 receipt에는 portable path ID를 남긴다."""

    owner = {
        "pid": route.owner_pid,
        "startTicks": route.owner_start_ticks,
        "uid": route.owner_uid,
    }
    document = {
        "schemaVersion": QUALIFICATION_DOCKER_SNAPSHOT_SCHEMA_VERSION,
        "dockerClientPathId": docker_client.path_id,
        "dockerClientSha256": docker_client.sha256,
        "dockerClientByteLength": len(docker_payload),
        "dockerClientIdentitySha256": canonical_sha256(
            dict(docker_client.identity)
        ),
        "outputIdentitySha256": canonical_sha256(
            _directory_anchor_identity(output_stat)
        ),
        "hostToolsPathId": QUALIFICATION_HOST_TOOLS_PATH_ID,
        "hostToolsIdentitySha256": canonical_sha256(
            _stat_identity(directory_stat)
        ),
        "dockerCommandPathId": QUALIFICATION_DOCKER_COMMAND_PATH_ID,
        "dockerLinkIdentitySha256": canonical_sha256(
            _stat_identity(link_stat)
        ),
        "dockerConfigPathId": QUALIFICATION_DOCKER_CONFIG_PATH_ID,
        "dockerConfigIdentitySha256": canonical_sha256(
            _stat_identity(docker_config_stat)
        ),
        "dockerConfigEntryCount": 0,
        "dockerConfigTreeSha256": canonical_sha256([]),
        "ownerFdPathId": QUALIFICATION_OWNER_DOCKER_FD_PATH_ID,
        "owner": owner,
    }
    return {
        **document,
        "snapshotSha256": canonical_sha256(document),
    }


def _validate_qualification_docker_snapshot(
    snapshot: object,
) -> dict[str, Any]:
    """Runtime snapshot의 exact portable schema와 self-hash를 검증한다."""

    expected_fields = {
        "schemaVersion",
        "dockerClientPathId",
        "dockerClientSha256",
        "dockerClientByteLength",
        "dockerClientIdentitySha256",
        "outputIdentitySha256",
        "hostToolsPathId",
        "hostToolsIdentitySha256",
        "dockerCommandPathId",
        "dockerLinkIdentitySha256",
        "dockerConfigPathId",
        "dockerConfigIdentitySha256",
        "dockerConfigEntryCount",
        "dockerConfigTreeSha256",
        "ownerFdPathId",
        "owner",
        "snapshotSha256",
    }
    if not isinstance(snapshot, dict) or set(snapshot) != expected_fields:
        raise WorkflowError("QUALIFICATION_DOCKER_SNAPSHOT_INVALID")
    without_hash = dict(snapshot)
    snapshot_sha256 = without_hash.pop("snapshotSha256")
    owner = snapshot.get("owner")
    docker_sha256 = snapshot.get("dockerClientSha256")
    if (
        snapshot.get("schemaVersion")
        != QUALIFICATION_DOCKER_SNAPSHOT_SCHEMA_VERSION
        or type(docker_sha256) is not str
        or SHA256_PATTERN.fullmatch(docker_sha256) is None
        or snapshot.get("dockerClientPathId")
        != f"S1_4X_DOCKER_CLIENT_SHA256_{str(docker_sha256).upper()}"
        or type(snapshot.get("dockerClientByteLength")) is not int
        or snapshot["dockerClientByteLength"] < 0
        or snapshot.get("hostToolsPathId")
        != QUALIFICATION_HOST_TOOLS_PATH_ID
        or snapshot.get("dockerCommandPathId")
        != QUALIFICATION_DOCKER_COMMAND_PATH_ID
        or snapshot.get("dockerConfigPathId")
        != QUALIFICATION_DOCKER_CONFIG_PATH_ID
        or type(snapshot.get("dockerConfigEntryCount")) is not int
        or snapshot.get("dockerConfigEntryCount") != 0
        or snapshot.get("dockerConfigTreeSha256") != canonical_sha256([])
        or snapshot.get("ownerFdPathId")
        != QUALIFICATION_OWNER_DOCKER_FD_PATH_ID
        or not isinstance(owner, dict)
        or set(owner) != {"pid", "startTicks", "uid"}
        or type(owner.get("pid")) is not int
        or owner["pid"] <= 0
        or type(owner.get("startTicks")) is not int
        or owner["startTicks"] <= 0
        or type(owner.get("uid")) is not int
        or owner["uid"] < 0
        or any(
            type(snapshot.get(field)) is not str
            or SHA256_PATTERN.fullmatch(snapshot[field]) is None
            for field in (
                "dockerClientIdentitySha256",
                "outputIdentitySha256",
                "hostToolsIdentitySha256",
                "dockerLinkIdentitySha256",
                "dockerConfigIdentitySha256",
            )
        )
        or snapshot_sha256 != canonical_sha256(without_hash)
    ):
        raise WorkflowError("QUALIFICATION_DOCKER_SNAPSHOT_INVALID")
    return snapshot


def snapshot_qualification_docker_route(
    route: QualificationDockerRoute,
    *,
    docker_client: PinnedDockerClient,
) -> dict[str, Any]:
    """Host validator 전후 owner/FD/directory/symlink identity를 다시 고정한다."""

    if not isinstance(route, QualificationDockerRoute):
        raise WorkflowError("QUALIFICATION_DOCKER_ROUTE_INVALID")
    try:
        current_start_ticks = _process_start_ticks(route.owner_pid)
        proc_owner = os.stat(
            f"/proc/{route.owner_pid}",
            follow_symlinks=False,
        )
    except (OSError, WorkflowError) as exc:
        raise WorkflowError("QUALIFICATION_DOCKER_OWNER_NOT_LIVE") from exc
    if (
        route.owner_pid != os.getpid()
        or current_start_ticks != route.owner_start_ticks
        or route.owner_uid != os.getuid()
        or proc_owner.st_uid != route.owner_uid
    ):
        raise WorkflowError("QUALIFICATION_DOCKER_OWNER_IDENTITY_DRIFT")
    docker_payload, docker_stat = _snapshot_pinned_oci_docker_client(
        docker_client
    )
    expected_target = (
        f"/proc/{route.owner_pid}/fd/{docker_client.descriptor}"
    )
    if route.docker_link_target != expected_target:
        raise WorkflowError("QUALIFICATION_DOCKER_LINK_TARGET_DRIFT")
    try:
        output_stat = os.lstat(route.output_root)
        directory_stat = os.lstat(route.host_tools_directory)
        entries = {path.name for path in route.host_tools_directory.iterdir()}
        link_stat = os.lstat(route.docker_link)
        link_target = os.readlink(route.docker_link)
        target_stat = os.stat(route.docker_link)
    except OSError as exc:
        raise WorkflowError("QUALIFICATION_DOCKER_LINK_DRIFT") from exc
    try:
        docker_config_stat = os.lstat(route.docker_config_directory)
        docker_config_entries = tuple(
            route.docker_config_directory.iterdir()
        )
    except OSError as exc:
        raise WorkflowError("QUALIFICATION_DOCKER_CONFIG_DRIFT") from exc
    if (
        not stat.S_ISDIR(output_stat.st_mode)
        or output_stat.st_uid != route.owner_uid
        or stat.S_IMODE(output_stat.st_mode) != 0o700
        or _directory_anchor_identity(output_stat)
        != dict(route.output_identity)
        or route.host_tools_directory != route.output_root / "host-tools"
        or not stat.S_ISDIR(directory_stat.st_mode)
        or stat.S_IMODE(directory_stat.st_mode) != 0o700
        or directory_stat.st_uid != route.owner_uid
        or entries != {"docker"}
    ):
        raise WorkflowError("QUALIFICATION_DOCKER_DIRECTORY_DRIFT")
    if (
        route.docker_link != route.host_tools_directory / "docker"
        or not stat.S_ISLNK(link_stat.st_mode)
        or _stat_identity(link_stat) != dict(route.link_identity)
    ):
        raise WorkflowError("QUALIFICATION_DOCKER_LINK_IDENTITY_DRIFT")
    if (
        link_target != expected_target
        or _stat_identity(target_stat) != dict(docker_client.identity)
        or _stat_identity(target_stat) != _stat_identity(docker_stat)
    ):
        raise WorkflowError("QUALIFICATION_DOCKER_LINK_TARGET_DRIFT")
    if _stat_identity(directory_stat) != dict(route.directory_identity):
        raise WorkflowError("QUALIFICATION_DOCKER_DIRECTORY_DRIFT")
    if (
        route.docker_config_directory
        != route.output_root / "docker-config"
        or not stat.S_ISDIR(docker_config_stat.st_mode)
        or stat.S_IMODE(docker_config_stat.st_mode) != 0o700
        or docker_config_stat.st_uid != route.owner_uid
        or _stat_identity(docker_config_stat)
        != dict(route.docker_config_identity)
        or docker_config_entries
    ):
        raise WorkflowError("QUALIFICATION_DOCKER_CONFIG_DRIFT")
    snapshot = _qualification_docker_snapshot_document(
        route,
        docker_client=docker_client,
        docker_payload=docker_payload,
        output_stat=output_stat,
        directory_stat=directory_stat,
        link_stat=link_stat,
        docker_config_stat=docker_config_stat,
    )
    return _validate_qualification_docker_snapshot(snapshot)


def qualification_environment_with_docker_route(
    environment: Mapping[str, str],
    *,
    route: QualificationDockerRoute,
    docker_client: PinnedDockerClient,
) -> dict[str, str]:
    """Host validator PATH를 검증된 output-bound route 하나로만 고정한다."""

    snapshot_qualification_docker_route(
        route,
        docker_client=docker_client,
    )
    current_path = environment.get("PATH")
    route_path = str(route.host_tools_directory)
    if (
        type(current_path) is not str
        or not current_path
        or any(
            token in route_path
            for token in ("\0", "\n", ":")
        )
    ):
        raise WorkflowError("QUALIFICATION_DOCKER_PATH_INVALID")
    routed = {
        name: value
        for name, value in environment.items()
        if not name.startswith("DOCKER_")
    }
    # Route가 사라져도 ambient `/usr/bin/docker`로 계속 탐색하지 못하게 한다.
    routed["PATH"] = route_path
    routed["DOCKER_CONFIG"] = str(route.docker_config_directory)
    routed["DOCKER_CONTEXT"] = QUALIFICATION_DOCKER_CONTEXT
    # Windows docker.exe에는 WSLENV path translation으로 같은 directory를 전달한다.
    routed["WSLENV"] = QUALIFICATION_DOCKER_CONFIG_WSLENV
    return routed


def build_qualification_docker_route_receipt(
    route: QualificationDockerRoute,
    *,
    docker_client: PinnedDockerClient,
    baseline: object,
) -> dict[str, Any]:
    """Qualification artifact에 local pathname 없는 Docker trust receipt를 넣는다."""

    accepted = _validate_qualification_docker_snapshot(baseline)
    current = snapshot_qualification_docker_route(
        route,
        docker_client=docker_client,
    )
    if current != accepted:
        raise WorkflowError("QUALIFICATION_DOCKER_ROUTE_CHANGED")
    return {
        "schemaVersion": QUALIFICATION_DOCKER_ROUTE_SCHEMA_VERSION,
        "dockerClientPathId": docker_client.path_id,
        "dockerClientSha256": docker_client.sha256,
        "hostToolsPathId": QUALIFICATION_HOST_TOOLS_PATH_ID,
        "dockerCommandPathId": QUALIFICATION_DOCKER_COMMAND_PATH_ID,
        "dockerConfigPathId": QUALIFICATION_DOCKER_CONFIG_PATH_ID,
        "dockerConfigTreeSha256": canonical_sha256([]),
        "dockerConfigWslEnv": QUALIFICATION_DOCKER_CONFIG_WSLENV,
        "dockerContext": QUALIFICATION_DOCKER_CONTEXT,
        "ownerFdPathId": QUALIFICATION_OWNER_DOCKER_FD_PATH_ID,
        "owner": {
            "pid": route.owner_pid,
            "startTicks": route.owner_start_ticks,
            "uid": route.owner_uid,
        },
        "snapshotSha256": accepted["snapshotSha256"],
        "snapshot": {
            **accepted,
            "owner": dict(accepted["owner"]),
        },
    }


def validate_qualification_docker_route_receipt(
    receipt: object,
    *,
    require_owner_exit: bool,
) -> dict[str, Any]:
    """Portable Docker route receipt의 schema와 종료된 owner identity를 검증한다."""

    expected_fields = {
        "schemaVersion",
        "dockerClientPathId",
        "dockerClientSha256",
        "hostToolsPathId",
        "dockerCommandPathId",
        "dockerConfigPathId",
        "dockerConfigTreeSha256",
        "dockerConfigWslEnv",
        "dockerContext",
        "ownerFdPathId",
        "owner",
        "snapshotSha256",
        "snapshot",
    }
    owner = receipt.get("owner") if isinstance(receipt, dict) else None
    docker_sha256 = (
        receipt.get("dockerClientSha256")
        if isinstance(receipt, dict)
        else None
    )
    if (
        not isinstance(receipt, dict)
        or set(receipt) != expected_fields
        or type(require_owner_exit) is not bool
        or receipt.get("schemaVersion")
        != QUALIFICATION_DOCKER_ROUTE_SCHEMA_VERSION
        or type(docker_sha256) is not str
        or SHA256_PATTERN.fullmatch(docker_sha256) is None
        or receipt.get("dockerClientPathId")
        != f"S1_4X_DOCKER_CLIENT_SHA256_{str(docker_sha256).upper()}"
        or receipt.get("hostToolsPathId")
        != QUALIFICATION_HOST_TOOLS_PATH_ID
        or receipt.get("dockerCommandPathId")
        != QUALIFICATION_DOCKER_COMMAND_PATH_ID
        or receipt.get("dockerConfigPathId")
        != QUALIFICATION_DOCKER_CONFIG_PATH_ID
        or receipt.get("dockerConfigTreeSha256")
        != canonical_sha256([])
        or receipt.get("dockerConfigWslEnv")
        != QUALIFICATION_DOCKER_CONFIG_WSLENV
        or receipt.get("dockerContext") != QUALIFICATION_DOCKER_CONTEXT
        or receipt.get("ownerFdPathId")
        != QUALIFICATION_OWNER_DOCKER_FD_PATH_ID
        or not isinstance(owner, dict)
        or set(owner) != {"pid", "startTicks", "uid"}
        or type(owner.get("pid")) is not int
        or owner["pid"] <= 0
        or type(owner.get("startTicks")) is not int
        or owner["startTicks"] <= 0
        or type(owner.get("uid")) is not int
        or owner["uid"] < 0
        or type(receipt.get("snapshotSha256")) is not str
        or SHA256_PATTERN.fullmatch(receipt["snapshotSha256"]) is None
    ):
        raise WorkflowError("QUALIFICATION_DOCKER_RECEIPT_INVALID")
    snapshot = _validate_qualification_docker_snapshot(
        receipt["snapshot"]
    )
    if (
        receipt["snapshotSha256"] != snapshot["snapshotSha256"]
        or receipt["dockerClientPathId"]
        != snapshot["dockerClientPathId"]
        or receipt["dockerClientSha256"]
        != snapshot["dockerClientSha256"]
        or receipt["hostToolsPathId"] != snapshot["hostToolsPathId"]
        or receipt["dockerCommandPathId"]
        != snapshot["dockerCommandPathId"]
        or receipt["dockerConfigPathId"]
        != snapshot["dockerConfigPathId"]
        or receipt["dockerConfigTreeSha256"]
        != snapshot["dockerConfigTreeSha256"]
        or receipt["ownerFdPathId"] != snapshot["ownerFdPathId"]
        or receipt["owner"] != snapshot["owner"]
    ):
        raise WorkflowError(
            "QUALIFICATION_DOCKER_RECEIPT_SNAPSHOT_BINDING_INVALID"
        )
    if require_owner_exit and _process_identity_is_live(
        owner["pid"],
        owner["startTicks"],
    ):
        raise WorkflowError("QUALIFICATION_DOCKER_OWNER_STILL_LIVE")
    return receipt


def _validate_qualification_docker_owner_binding(
    docker_route_receipt: Mapping[str, Any],
    portable_witness: object,
) -> None:
    """Docker route와 marker FD가 같은 PID incarnation에서 생성됐는지 묶는다."""

    route_owner = docker_route_receipt.get("owner")
    witness_owner = (
        portable_witness.get("owner")
        if isinstance(portable_witness, dict)
        else None
    )
    if (
        not isinstance(route_owner, dict)
        or witness_owner
        != {
            "pid": route_owner.get("pid"),
            "startTicks": route_owner.get("startTicks"),
        }
    ):
        raise WorkflowError("QUALIFICATION_DOCKER_OWNER_BINDING_INVALID")


def _run_pinned_oci_docker_logged(
    client: PinnedDockerClient,
    command: Sequence[str],
    *,
    cwd: Path,
    environment: Mapping[str, str],
    phase: str,
    output_directory: Path,
    benchmark_python_runtime: BenchmarkPythonRuntime,
) -> dict[str, Any]:
    """Docker command를 retained FD로만 실행하고 receipt에는 portable ID를 남긴다."""

    _snapshot_pinned_oci_docker_client(client)
    if not command or command[0] != str(client.fd_path):
        raise WorkflowError(f"OCI_DOCKER_STAGE_COMMAND_INVALID:{phase}")
    return _run_logged(
        command,
        cwd=cwd,
        environment=environment,
        phase=phase,
        output_directory=output_directory,
        pass_fds=(client.descriptor,),
        portable_path_ids={str(client.fd_path): client.path_id},
        benchmark_python_runtime=benchmark_python_runtime,
    )


def _docker_config_tree_snapshot(
    docker_config: Path,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Output-bound Docker config의 directory/file bytes와 stat identity를 고정한다."""

    root_stat = os.lstat(docker_config)
    if not stat.S_ISDIR(root_stat.st_mode):
        raise WorkflowError("OCI_DOCKER_CONFIG_MODE_INVALID")
    records: list[dict[str, Any]] = []
    for path in sorted(
        docker_config.rglob("*"),
        key=lambda item: os.fsencode(item.relative_to(docker_config).as_posix()),
    ):
        relative = path.relative_to(docker_config).as_posix()
        value = os.lstat(path)
        if stat.S_ISLNK(value.st_mode):
            raise WorkflowError(f"OCI_DOCKER_CONFIG_SYMLINK:{relative}")
        if stat.S_ISDIR(value.st_mode):
            records.append(
                {
                    "path": relative,
                    "type": "directory",
                    "identity": _stat_identity(value),
                }
            )
            continue
        if not stat.S_ISREG(value.st_mode) or value.st_nlink != 1:
            raise WorkflowError(
                f"OCI_DOCKER_CONFIG_ENTRY_INVALID:{relative}"
            )
        payload, snapshot = _same_fd_bytes_snapshot(
            path,
            label="OCI_DOCKER_CONFIG_FILE",
            max_bytes=16 * 1024 * 1024,
        )
        records.append(
            {
                "path": relative,
                "type": "regular",
                "identity": _stat_identity(snapshot),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        )
    current_root = os.lstat(docker_config)
    if _stat_identity(current_root) != _stat_identity(root_stat):
        raise WorkflowError("OCI_DOCKER_CONFIG_CHANGED_DURING_SNAPSHOT")
    return records, _stat_identity(root_stat)


def snapshot_oci_docker_stage(
    *,
    stage: str,
    docker_client: PinnedDockerClient,
    docker_config: Path,
    output_root: Path,
) -> dict[str, Any]:
    """각 Docker command 직전/직후 client bytes와 output-bound config를 snapshot한다."""

    if (
        type(stage) is not str
        or re.fullmatch(r"[a-z0-9][a-z0-9-]{0,127}", stage) is None
        or not output_root.is_absolute()
        or output_root.is_symlink()
        or not output_root.is_dir()
        or output_root.resolve(strict=True) != output_root
        or docker_config != output_root / "docker-config"
        or docker_config.is_symlink()
        or not docker_config.is_dir()
        or docker_config.resolve(strict=True) != docker_config
    ):
        raise WorkflowError("OCI_DOCKER_TRUST_STAGE_INPUT_INVALID")
    docker_payload, docker_stat = _snapshot_pinned_oci_docker_client(
        docker_client
    )
    docker_sha256 = hashlib.sha256(docker_payload).hexdigest()
    if (
        docker_sha256 != docker_client.sha256
        or docker_stat.st_mode & 0o111 == 0
        or docker_stat.st_nlink != 1
    ):
        raise WorkflowError("OCI_DOCKER_CLIENT_STAGE_INVALID")
    config_records, config_identity = _docker_config_tree_snapshot(
        docker_config
    )
    trust_identity = {
        "dockerClientPath": str(docker_client.source_path),
        "dockerClientPathId": docker_client.path_id,
        "dockerClientSha256": docker_sha256,
        "dockerClientByteLength": len(docker_payload),
        "dockerClientIdentity": _stat_identity(docker_stat),
        "dockerConfigPath": str(docker_config),
        "dockerConfigIdentity": config_identity,
        "dockerConfigEntryCount": len(config_records),
        "dockerConfigTreeSha256": canonical_sha256(config_records),
    }
    snapshot = {
        "schemaVersion": "s1.4x-oci-docker-trust-stage-v1",
        "stage": stage,
        **trust_identity,
        "dockerTrustIdentitySha256": canonical_sha256(trust_identity),
    }
    snapshot["snapshotSha256"] = canonical_sha256(snapshot)
    return snapshot


def validate_oci_docker_stage_pair(
    before: object,
    after: object,
) -> None:
    """한 Docker command의 전후 client/config byte closure가 동일함을 강제한다."""

    expected_fields = {
        "schemaVersion",
        "stage",
        "dockerClientPath",
        "dockerClientPathId",
        "dockerClientSha256",
        "dockerClientByteLength",
        "dockerClientIdentity",
        "dockerConfigPath",
        "dockerConfigIdentity",
        "dockerConfigEntryCount",
        "dockerConfigTreeSha256",
        "dockerTrustIdentitySha256",
        "snapshotSha256",
    }
    for value in (before, after):
        if not isinstance(value, dict) or set(value) != expected_fields:
            raise WorkflowError("OCI_DOCKER_TRUST_STAGE_OBJECT_INVALID")
        without_hash = dict(value)
        snapshot_sha256 = without_hash.pop("snapshotSha256")
        if snapshot_sha256 != canonical_sha256(without_hash):
            raise WorkflowError("OCI_DOCKER_TRUST_STAGE_HASH_INVALID")
    if before != after:
        raise WorkflowError("OCI_DOCKER_TRUST_STAGE_CHANGED")


def _oci_context_name(
    path: Path,
    *,
    command_record: Mapping[str, Any],
) -> str:
    payload, _ = _same_fd_bytes_snapshot(
        path,
        label="OCI_CONTEXT_NAME",
        max_bytes=1024,
    )
    if (
        command_record.get("stdoutPath") != str(path)
        or command_record.get("stdoutSha256")
        != hashlib.sha256(payload).hexdigest()
    ):
        raise WorkflowError("OCI_CONTEXT_NAME_COMMAND_LOG_BINDING_INVALID")
    try:
        value = payload.decode("utf-8")
    except UnicodeError as exc:
        raise WorkflowError("OCI_CONTEXT_NAME_INVALID") from exc
    if (
        not value.endswith("\n")
        or value.count("\n") != 1
        or re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}",
            value[:-1],
        )
        is None
    ):
        raise WorkflowError("OCI_CONTEXT_NAME_INVALID")
    return value[:-1]


def _oci_correctness(arguments: argparse.Namespace) -> None:
    output = _reserve_directory(arguments.output_dir)
    python_runtime = arguments.benchmark_python_runtime
    haskell_root = Path(__file__).resolve(strict=True).parent.parent
    numeric_root = haskell_root.parent
    repo_root = numeric_root.parents[3]
    candidate_commit = _repo_commit(repo_root)
    evidence = _load_haskell_evidence(haskell_root)
    source_tree_sha256 = evidence.benchmark_source_tree_sha256(haskell_root)
    plan_path = _absolute_regular(
        numeric_root / "benchmarks/benchmark-plan.v1.json",
        label="QUALIFICATION_PLAN",
    )
    plan = strict_json_load(plan_path)
    configuration, _ = _qualification_contract(plan)
    profile_path = _absolute_regular(
        haskell_root / "selected-profile.v1.json",
        label="SELECTED_PROFILE",
    )
    profile = strict_json_load(profile_path)
    evidence.validate_selected_profile_document(
        profile,
        expected_compiler_sha256=evidence.AUTHORITATIVE_GHC_SHA256,
        expected_source_tree_sha256=source_tree_sha256,
        expected_qualification_plan_sha256=sha256_file(plan_path),
        expected_selector_config_sha256=canonical_sha256(configuration),
    )
    if profile.get("schemaVersion") != FINAL_PROFILE_SCHEMA_VERSION:
        raise WorkflowError("OCI_SELECTED_PROFILE_NOT_FINAL")
    evidence.validate_source_manifest(
        haskell_root,
        haskell_root / "source-inputs.v1.json",
    )
    profile_id = profile["profileId"]
    options = profile_options(profile_id)
    docker_source = _required_environment_path("S1_4X_DOCKER_BIN")
    expected_docker_sha256 = _require_sha256(
        os.environ.get("S1_4X_DOCKER_SHA256"),
        label="docker",
    )
    docker_client = pin_oci_docker_client(
        docker_source,
        expected_sha256=expected_docker_sha256,
    )
    docker = docker_client.fd_path
    docker_sha256 = docker_client.sha256
    ghcup = _required_environment_path("S1_4X_GHCUP_BIN")
    stack = _required_environment_path("S1_4X_STACK_BIN")
    ghc = _required_environment_path("S1_4X_AUTHORITATIVE_GHC_BIN")
    cache_value = os.environ.get("S1_4X_CACHE_ROOT")
    if cache_value is None:
        raise WorkflowError("REQUIRED_ENVIRONMENT_MISSING:S1_4X_CACHE_ROOT")
    cache_root = _absolute_existing_directory(Path(cache_value), label="CACHE_ROOT")
    output_identity = hashlib.sha256(os.fsencode(str(output))).hexdigest()[:12]
    suffix = f"{candidate_commit[:12]}-{profile_id}-{output_identity}"
    stack_root = isolated_stack_root(
        cache_root,
        purpose="oci",
        output_path=output,
    )
    context = cache_root / f"oci-context-{suffix}"
    docker_config = output / "docker-config"
    for path in (stack_root, context):
        if path.exists() or path.is_symlink():
            raise WorkflowError(f"OCI_CACHE_PATH_ALREADY_EXISTS:{path.name}")
    stack_root.mkdir(mode=0o700)
    work_dir = isolated_stack_work_dir(stack_root)
    if (haskell_root / work_dir).exists() or (
        haskell_root / work_dir
    ).is_symlink():
        raise WorkflowError("OCI_STACK_WORK_DIR_ALREADY_EXISTS")
    docker_config.mkdir(mode=0o700)
    environment = _sealed_child_environment(
        ghc_bin=ghc,
        stack_bin=stack,
        python_runtime=python_runtime,
    )
    environment["DOCKER_CONFIG"] = str(docker_config)
    docker_trust_baseline = snapshot_oci_docker_stage(
        stage="baseline",
        docker_client=docker_client,
        docker_config=docker_config,
        output_root=output,
    )
    if docker_trust_baseline["dockerConfigEntryCount"] != 0:
        raise WorkflowError("OCI_DOCKER_CONFIG_NOT_FRESH_EMPTY")
    docker_trust_stage_snapshots: list[dict[str, Any]] = []

    def run_docker_stage(
        command: Sequence[str],
        *,
        phase: str,
    ) -> dict[str, Any]:
        """Docker command마다 exact client/config bytes를 전후 동일 snapshot으로 묶는다."""

        if not command or command[0] != str(docker):
            raise WorkflowError(f"OCI_DOCKER_STAGE_COMMAND_INVALID:{phase}")
        before = snapshot_oci_docker_stage(
            stage=phase,
            docker_client=docker_client,
            docker_config=docker_config,
            output_root=output,
        )
        if (
            before["dockerTrustIdentitySha256"]
            != docker_trust_baseline["dockerTrustIdentitySha256"]
        ):
            raise WorkflowError("OCI_DOCKER_TRUST_BASELINE_DRIFT")
        record = _run_pinned_oci_docker_logged(
            docker_client,
            command,
            cwd=cache_root,
            environment=environment,
            phase=phase,
            output_directory=output,
            benchmark_python_runtime=python_runtime,
        )
        after = snapshot_oci_docker_stage(
            stage=phase,
            docker_client=docker_client,
            docker_config=docker_config,
            output_root=output,
        )
        validate_oci_docker_stage_pair(before, after)
        if (
            after["dockerTrustIdentitySha256"]
            != docker_trust_baseline["dockerTrustIdentitySha256"]
        ):
            raise WorkflowError("OCI_DOCKER_TRUST_BASELINE_DRIFT")
        docker_trust_stage_snapshots.append(
            {
                "phase": phase,
                "before": before,
                "after": after,
            }
        )
        return record

    stack_yaml = _absolute_regular(
        haskell_root / "stack.yaml",
        label="AUTHORITATIVE_STACK_YAML",
    )
    build_candidate = build_stack_command(
        ghcup=ghcup,
        stack=stack,
        stack_yaml=stack_yaml,
        stack_root=stack_root,
        work_dir=work_dir,
        ghc_version="9.10.3",
        operation=[
            "build",
            "--no-run-tests",
            "--pedantic",
            f"--ghc-options={' '.join(options)}",
        ],
    )
    commands = [
        _run_logged(
            build_candidate,
            cwd=haskell_root,
            environment=environment,
            phase="oci-stack-build",
            output_directory=output,
            benchmark_python_runtime=python_runtime,
        )
    ]
    context_show_command = build_oci_context_show_command(docker)
    context_before_record = run_docker_stage(
        context_show_command,
        phase="oci-context-before",
    )
    commands.append(context_before_record)
    context_name = _oci_context_name(
        output / "oci-context-before.stdout",
        command_record=context_before_record,
    )
    daemon_info_command = [
        str(docker),
        "info",
        "--format",
        "{{json .}}",
    ]
    daemon_before_record = run_docker_stage(
        daemon_info_command,
        phase="oci-daemon-before",
    )
    commands.append(daemon_before_record)
    daemon_before_document, _ = _same_fd_logged_json_snapshot(
        output / "oci-daemon-before.stdout",
        label="OCI_DAEMON_BEFORE",
        command_record=daemon_before_record,
    )
    daemon_identity_before = validate_oci_daemon_identity(
        daemon_before_document,
        context_name=context_name,
    )
    base_inspect_command = [
        str(docker),
        "image",
        "inspect",
        "--format",
        "{{json .}}",
        OCI_BASE_IMAGE,
    ]
    base_before_record = run_docker_stage(
        base_inspect_command,
        phase="oci-base-before",
    )
    commands.append(base_before_record)
    base_before_document, base_inspection_before_sha256 = (
        _same_fd_logged_json_snapshot(
            output / "oci-base-before.stdout",
            label="OCI_BASE_BEFORE",
            command_record=base_before_record,
        )
    )
    base_image_id = validate_oci_base_image_inspection(
        base_before_document,
        expected_reference=OCI_BASE_IMAGE,
    )
    binary = _find_candidate_binary(
        haskell_root,
        work_dir=work_dir,
        ghc_version="9.10.3",
    )
    fixture_root = _absolute_existing_directory(
        numeric_root / "contract/fixtures",
        label="FIXTURE_ROOT",
    )
    containerfile = _absolute_regular(
        haskell_root / "Containerfile",
        label="CONTAINERFILE",
    )
    context_snapshot = _copy_oci_context(
        context=context,
        containerfile=containerfile,
        binary=binary,
        fixture_root=fixture_root,
    )
    _validate_oci_context_snapshot(context, expected=context_snapshot)
    binary_sha256 = context_snapshot["binarySha256"]
    fixture_tree_sha256 = context_snapshot["fixtureTreeSha256"]
    containerfile_sha256 = context_snapshot["containerfileSha256"]
    provenance_labels = {
        "io.s1-4x.base-image-id": base_image_id,
        "io.s1-4x.containerfile-sha256": containerfile_sha256,
        "io.s1-4x.fixture-tree-sha256": fixture_tree_sha256,
    }
    iidfile = output / "image.iid"
    image_tag = f"local/s1-4x-haskell:{suffix}"
    image_build = build_oci_build_command(
        docker=docker,
        containerfile=context / "Containerfile",
        context=context,
        iidfile=iidfile,
        image_tag=image_tag,
        binary_sha256=binary_sha256,
        provenance_labels=provenance_labels,
    )
    _validate_oci_context_snapshot(context, expected=context_snapshot)
    commands.append(
        run_docker_stage(
            image_build,
            phase="oci-image-build",
        )
    )
    _validate_oci_context_snapshot(context, expected=context_snapshot)
    iid_payload, _ = _same_fd_bytes_snapshot(
        iidfile,
        label="OCI_IIDFILE",
        max_bytes=128,
    )
    image_id = validate_oci_iid_bytes(iid_payload)
    iidfile_sha256 = hashlib.sha256(iid_payload).hexdigest()
    immutable_inspect_command = [
        str(docker),
        "image",
        "inspect",
        "--format",
        "{{json .}}",
        image_id,
    ]
    immutable_inspect_record = run_docker_stage(
        immutable_inspect_command,
        phase="oci-image-id-inspect",
    )
    commands.append(immutable_inspect_record)
    immutable_inspection, _ = _same_fd_logged_json_snapshot(
        output / "oci-image-id-inspect.stdout",
        label="OCI_IMAGE_ID_INSPECTION",
        command_record=immutable_inspect_record,
    )
    validate_oci_image_inspection(
        immutable_inspection,
        image_tag=image_tag,
        expected_image_id=image_id,
        expected_labels=provenance_labels,
    )
    inspect_command = [
        str(docker),
        "image",
        "inspect",
        "--format",
        "{{json .}}",
        image_tag,
    ]
    image_tag_binding_checks: list[dict[str, Any]] = []

    def inspect_tag_binding(
        phase: str,
        *,
        expected_image_id: str | None,
    ) -> str:
        inspect_record = run_docker_stage(
            inspect_command,
            phase=phase,
        )
        commands.append(inspect_record)
        inspect_path = output / f"{phase}.stdout"
        inspection, inspection_sha256 = _same_fd_logged_json_snapshot(
            inspect_path,
            label="OCI_IMAGE_TAG_INSPECTION",
            command_record=inspect_record,
        )
        inspected_image_id = validate_oci_image_inspection(
            inspection,
            image_tag=image_tag,
            expected_image_id=expected_image_id,
            expected_labels=provenance_labels,
        )
        image_tag_binding_checks.append(
            {
                "phase": phase,
                "imageTag": image_tag,
                "imageId": inspected_image_id,
                "inspectionSha256": inspection_sha256,
                "status": "PASS",
            }
        )
        return inspected_image_id

    inspect_tag_binding(
        "oci-image-inspect",
        expected_image_id=image_id,
    )
    runtime_output = output / "runtime"
    runtime_output.mkdir(mode=0o700)
    comparator = _pin_oracle_comparator(numeric_root)
    compare_path_ids = _oracle_compare_path_ids(python_runtime, comparator)
    matrices = (
        (
            "canonical",
            "/opt/s1-4x/fixtures/small/canonical-inputs.v1.json",
            fixture_root / "small/canonical-inputs.v1.json",
            fixture_root / "expected/canonical-results.v1.json",
        ),
        (
            "semantic",
            "/opt/s1-4x/fixtures/invalid/semantic-errors.v1.json",
            fixture_root / "invalid/semantic-errors.v1.json",
            fixture_root / "invalid/semantic-errors.expected.v1.json",
        ),
    )
    comparisons: list[dict[str, Any]] = []
    for label, container_request, host_request, expected in matrices:
        actual = runtime_output / f"{label}.actual.json"
        comparison = output / f"{label}.oci-comparison.json"
        run_command = build_oci_run_command(
            docker=docker,
            image_id=image_id,
            output_directory=runtime_output,
            output_name=actual.name,
            request_path=container_request,
            uid=os.getuid(),
            gid=os.getgid(),
        )
        commands.append(
            run_docker_stage(
                run_command,
                phase=f"oci-{label}-run",
            )
        )
        inspect_tag_binding(
            f"oci-{label}-tag-check",
            expected_image_id=image_id,
        )
        _absolute_regular(actual, label=f"OCI_{label.upper()}_ACTUAL")
        compare_command = _oracle_compare_command(
            python_path=python_runtime.fd_path,
            comparator=comparator,
            arguments=[
                "--expected",
                str(expected),
                "--actual",
                str(actual),
                "--request",
                str(host_request),
                "--output",
                str(comparison),
            ],
        )
        commands.append(
            _run_logged(
                compare_command,
                cwd=repo_root,
                environment=environment,
                phase=f"oci-{label}-compare",
                output_directory=output,
                pass_fds=_oracle_compare_pass_fds(
                    python_runtime,
                    comparator,
                ),
                portable_path_ids=compare_path_ids,
                benchmark_python_runtime=python_runtime,
            )
        )
        _comparison_status(comparison)
        comparisons.append(
            {
                "matrixId": label,
                "actualSha256": sha256_file(actual),
                "comparisonSha256": sha256_file(comparison),
                "mismatchCount": 0,
                "status": "PASS",
            }
        )
    base_after_record = run_docker_stage(
        base_inspect_command,
        phase="oci-base-after",
    )
    commands.append(base_after_record)
    base_after_document, base_inspection_after_sha256 = (
        _same_fd_logged_json_snapshot(
            output / "oci-base-after.stdout",
            label="OCI_BASE_AFTER",
            command_record=base_after_record,
        )
    )
    if (
        validate_oci_base_image_inspection(
            base_after_document,
            expected_reference=OCI_BASE_IMAGE,
        )
        != base_image_id
    ):
        raise WorkflowError("OCI_BASE_IMAGE_CHANGED_DURING_RUN")
    context_after_record = run_docker_stage(
        context_show_command,
        phase="oci-context-after",
    )
    commands.append(context_after_record)
    context_name_after = _oci_context_name(
        output / "oci-context-after.stdout",
        command_record=context_after_record,
    )
    daemon_after_record = run_docker_stage(
        daemon_info_command,
        phase="oci-daemon-after",
    )
    commands.append(daemon_after_record)
    daemon_after_document, _ = _same_fd_logged_json_snapshot(
        output / "oci-daemon-after.stdout",
        label="OCI_DAEMON_AFTER",
        command_record=daemon_after_record,
    )
    daemon_identity_after = validate_oci_daemon_identity(
        daemon_after_document,
        context_name=context_name_after,
    )
    daemon_identity_sha256 = validate_oci_daemon_identity_pair(
        daemon_identity_before,
        daemon_identity_after,
    )
    _snapshot_pinned_oci_docker_client(docker_client)
    if evidence.benchmark_source_tree_sha256(haskell_root) != source_tree_sha256:
        raise WorkflowError("SOURCE_TREE_CHANGED_DURING_OCI")
    if _repo_commit(repo_root) != candidate_commit:
        raise WorkflowError("CANDIDATE_COMMIT_CHANGED_DURING_OCI")
    receipt = {
        "schemaVersion": "s1.4x-haskell-oci-correctness-v1",
        "status": "PASS",
        "candidateSourceCommit": candidate_commit,
        "sourceTreeSha256": source_tree_sha256,
        "selectedProfileSha256": sha256_file(profile_path),
        "profileId": profile_id,
        "ghcOptions": list(options),
        "optionsSha256": canonical_sha256(list(options)),
        "containerfileSha256": containerfile_sha256,
        "baseImage": OCI_BASE_IMAGE,
        "baseImageId": base_image_id,
        "baseInspectionBeforeSha256": base_inspection_before_sha256,
        "baseInspectionAfterSha256": base_inspection_after_sha256,
        "stackRootPath": str(stack_root),
        "stackWorkDir": str(work_dir),
        "contextSnapshot": context_snapshot,
        "fixtureTreeSha256": fixture_tree_sha256,
        "candidateBinarySha256": binary_sha256,
        "dockerPath": str(docker_source),
        "dockerPathId": docker_client.path_id,
        "dockerSha256": docker_sha256,
        "expectedDockerSha256": expected_docker_sha256,
        "dockerConfigPath": str(docker_config),
        "dockerTrustBaseline": docker_trust_baseline,
        "dockerTrustStageSnapshots": docker_trust_stage_snapshots,
        "daemonIdentitySha256": daemon_identity_sha256,
        "dockerContextName": context_name,
        "daemonIdentityBefore": daemon_identity_before,
        "daemonIdentityAfter": daemon_identity_after,
        "imageTag": image_tag,
        "imageId": image_id,
        "iidFileSha256": iidfile_sha256,
        "provenanceLabels": provenance_labels,
        "platform": OCI_PLATFORM,
        "runtimeImageSubject": {
            "referenceType": "immutable-image-id",
            "imageId": image_id,
        },
        "imageTagBindingChecks": image_tag_binding_checks,
        "buildNetwork": "none",
        "runtimeNetwork": "none",
        "runtimeMounts": ["output-only"],
        "commands": commands,
        "comparisons": comparisons,
        "mismatchCount": 0,
    }
    receipt_path = output / "oci-correctness-receipt.v1.json"
    atomic_write_json_exclusive(receipt_path, receipt)
    print(
        json.dumps(
            {
                "imageId": image_id,
                "receiptPath": str(receipt_path),
                "receiptSha256": sha256_file(receipt_path),
                "status": "PASS",
            },
            sort_keys=True,
        )
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    correctness = commands.add_parser("correctness")
    correctness.add_argument("--profile", required=True)
    correctness.add_argument("--output-dir", type=Path, required=True)
    correctness.set_defaults(handler=_correctness)
    qualification = commands.add_parser("qualification")
    qualification.add_argument("--plan", type=Path, required=True)
    qualification.add_argument("--profiles", required=True)
    qualification.add_argument("--enforce-order-plan", action="store_true")
    qualification.add_argument("--output-dir", type=Path, required=True)
    qualification.set_defaults(handler=_qualification)
    marker = commands.add_parser("mark-measurement-entered")
    marker.add_argument("--qualification", type=Path, required=True)
    marker.set_defaults(handler=_mark_measurement)
    selector = commands.add_parser("select-profile")
    selector.add_argument(
        "--mode",
        required=True,
        choices=("materialize", "check"),
    )
    selector.set_defaults(handler=_select_profile)
    capture_compatibility = commands.add_parser("capture-compatibility-failure")
    capture_compatibility.add_argument("--stack-yaml", type=Path, required=True)
    capture_compatibility.add_argument("--stack-root", type=Path, required=True)
    capture_compatibility.add_argument("--stdout", type=Path, required=True)
    capture_compatibility.add_argument("--stderr", type=Path, required=True)
    capture_compatibility.add_argument(
        "--authoritative-boot-dump",
        type=Path,
        required=True,
    )
    capture_compatibility.add_argument(
        "--compatibility-boot-dump",
        type=Path,
        required=True,
    )
    capture_compatibility.add_argument("--pantry-db", type=Path, required=True)
    capture_compatibility.add_argument("--started-at", required=True)
    capture_compatibility.add_argument("--ended-at", required=True)
    capture_compatibility.add_argument("--exit-code", type=int, required=True)
    capture_compatibility.add_argument("--output-dir", type=Path, required=True)
    capture_compatibility.set_defaults(handler=_capture_compatibility_failure)
    replay_compatibility = commands.add_parser(
        "replay-compatibility-success"
    )
    replay_compatibility.add_argument("--stack-yaml", type=Path, required=True)
    replay_compatibility.add_argument("--stack-root", type=Path, required=True)
    replay_compatibility.add_argument("--stdout", type=Path, required=True)
    replay_compatibility.add_argument("--stderr", type=Path, required=True)
    replay_compatibility.add_argument(
        "--authoritative-boot-dump",
        type=Path,
        required=True,
    )
    replay_compatibility.add_argument(
        "--compatibility-boot-dump",
        type=Path,
        required=True,
    )
    replay_compatibility.add_argument("--pantry-db", type=Path, required=True)
    replay_compatibility.add_argument("--started-at", required=True)
    replay_compatibility.add_argument("--ended-at", required=True)
    replay_compatibility.add_argument("--exit-code", type=int, required=True)
    replay_compatibility.add_argument("--output-dir", type=Path, required=True)
    replay_compatibility.set_defaults(handler=_replay_compatibility_success)
    cross_replay = commands.add_parser("verify-cross-replay")
    cross_replay.add_argument(
        "--canonical-comparison",
        type=Path,
        required=True,
    )
    cross_replay.add_argument(
        "--semantic-comparison",
        type=Path,
        required=True,
    )
    cross_replay.add_argument("--output", type=Path, required=True)
    cross_replay.set_defaults(handler=_verify_cross_replay)
    validate_compatibility = commands.add_parser("validate-compatibility")
    validate_compatibility.add_argument("--result", type=Path, required=True)
    validate_compatibility.set_defaults(handler=_validate_compatibility)
    oci_correctness = commands.add_parser("oci-correctness")
    oci_correctness.add_argument("--output-dir", type=Path, required=True)
    oci_correctness.set_defaults(handler=_oci_correctness)
    candidate_runtime = commands.add_parser("candidate-runtime")
    candidate_runtime.add_argument("--profile", type=Path, required=True)
    candidate_runtime.add_argument(
        "--source-manifest",
        type=Path,
        required=True,
    )
    candidate_runtime.add_argument(
        "--qualification-plan",
        type=Path,
        required=True,
    )
    candidate_runtime.set_defaults(handler=_candidate_runtime)
    candidate_root = commands.add_parser("candidate-stack-root")
    candidate_root.add_argument("--cache-root", type=Path, required=True)
    candidate_root.add_argument("--output", type=Path, required=True)
    candidate_root.set_defaults(handler=_candidate_stack_root)
    isolated_root = commands.add_parser("isolated-stack-root")
    isolated_root.add_argument("--cache-root", type=Path, required=True)
    isolated_root.add_argument("--purpose", required=True)
    isolated_root.add_argument("--output", type=Path, required=True)
    isolated_root.set_defaults(handler=_isolated_stack_root)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        arguments.benchmark_python_runtime = _benchmark_python_runtime()
        arguments.handler(arguments)
    except (
        WorkflowError,
        OSError,
        UnicodeError,
        ValueError,
        subprocess.SubprocessError,
    ) as exc:
        print(f"HASKELL_PROFILE_WORKFLOW_FAIL:{exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
