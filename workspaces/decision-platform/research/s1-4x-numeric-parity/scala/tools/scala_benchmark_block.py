#!/usr/bin/env python3
"""Frozen Scala JMH family raw를 shared native evidence pipeline에 연결한다."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class BlockError(RuntimeError):
    """Scala benchmark block의 frozen 입력 또는 shared evidence가 유효하지 않음."""


SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
RAW_CASE_FILES = (
    "native.json",
    "scala-jmh-run-result.v1.json",
    "scala-jmh-native-validation.v1.json",
    "scala-effective-jvm-args-result.v1.json",
    "measurement-ready.v1.json",
    "scala-jmh-generated-java-precompile.v1.json",
    "scala-jmh-precompile.stdout",
    "scala-jmh-precompile.stderr",
    "scala-javac.stdout",
    "scala-javac.stderr",
    "fork-evidence.normalized.json",
    "jmh.stdout",
    "jmh.stderr",
    "jmh-list.txt",
)
CANDIDATE_PROVENANCE_FIELDS = frozenset(
    {
        "kind",
        "selectedProfileResultPath",
        "selectedProfileResultSha256",
        "selectedProfileSourcePath",
        "selectedProfileSourceSha256",
        "selectedProfileId",
        "sourceInputManifestPath",
        "sourceInputManifestSha256",
        "compilerProfilesPath",
        "compilerProfilesSha256",
        "toolchainLockPath",
        "toolchainLockSha256",
        "mergedToolchainProvenancePath",
        "mergedToolchainProvenanceSha256",
        "effectiveJvmArgumentsCapabilityPath",
        "effectiveJvmArgumentsCapabilitySha256",
        "scalaCliPath",
        "scalaCliBinarySha256",
        "javaExecutablePath",
        "javaExecutableSha256",
    }
)
RECORDED_ENVIRONMENT_NAMES = (
    "S1_4X_BENCHMARK_CASE_ID",
    "S1_4X_BENCHMARK_PLAN",
    "S1_4X_BENCHMARK_PROFILE",
    "S1_4X_BENCHMARK_RUN_MODE",
    "S1_4X_FIXTURE_ROOT",
    "S1_4X_EFFECTIVE_JVM_EVIDENCE_DIR",
    "S1_4X_MEASUREMENT_READY_MARKER",
    "S1_4X_SCALA_WORKSPACE",
    "COURSIER_CACHE",
    "COURSIER_CONFIG_DIR",
    "SCALA_CLI_HOME",
    "SCALA_CLI_CONFIG",
    "XDG_CONFIG_HOME",
    "JAVA_HOME",
)
FORBIDDEN_AMBIENT_JVM_VARIABLES = (
    "JAVA_TOOL_OPTIONS",
    "_JAVA_OPTIONS",
    "JDK_JAVA_OPTIONS",
)
FORBIDDEN_AMBIENT_SCALA_VARIABLES = (
    "COURSIER_CONFIG_DIR",
    "COURSIER_REPOSITORIES",
    "SCALA_CLI_CONFIG",
    "SCALA_CLI_HOME",
)
SHARED_PRODUCER_RESULT_FIELDS = {
    "boundaryId",
    "selectorId",
    "caseCount",
    "nativeContractValidationSha256",
    "nativeReportSha256",
    "nativeStatisticsSha256",
    "status",
}


def sha256_file(path: Path) -> str:
    """Regular non-symlink file의 현재 bytes를 SHA-256으로 고정한다."""

    if path.is_symlink() or not path.is_file():
        raise BlockError(f"UNSAFE_OR_MISSING_FILE:{path}")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_sha256(value: Any) -> str:
    """Evidence object를 canonical JSON bytes로 해시한다."""

    try:
        payload = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    except (TypeError, ValueError) as exc:
        raise BlockError("NON_CANONICAL_HASH_INPUT") from exc
    return hashlib.sha256(payload).hexdigest()


def _strict_json_decode(payload: str, *, label: str) -> Any:
    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise BlockError(f"DUPLICATE_JSON_KEY:{label}:{key}")
            result[key] = value
        return result

    try:
        return json.loads(
            payload,
            object_pairs_hook=unique_object,
            parse_constant=lambda token: (_ for _ in ()).throw(
                BlockError(f"NONFINITE_JSON_TOKEN:{label}:{token}")
            ),
        )
    except json.JSONDecodeError as exc:
        raise BlockError(f"INVALID_JSON:{label}") from exc


def strict_json_load(path: Path) -> Any:
    """Regular JSON file을 duplicate/non-finite 거부 모드로 읽는다."""

    sha256_file(path)
    try:
        return _strict_json_decode(
            path.read_text(encoding="utf-8"),
            label=str(path),
        )
    except (OSError, UnicodeError) as exc:
        raise BlockError(f"INVALID_JSON:{path}") from exc


def _require_absolute_regular(path: Path, *, label: str) -> Path:
    if (
        not path.is_absolute()
        or path.is_symlink()
        or not path.is_file()
        or path.resolve(strict=True) != path
    ):
        raise BlockError(f"{label}_NOT_CANONICAL_REGULAR_FILE")
    return path


def _require_absolute_directory(path: Path, *, label: str) -> Path:
    if (
        not path.is_absolute()
        or path.is_symlink()
        or not path.is_dir()
        or path.resolve(strict=True) != path
    ):
        raise BlockError(f"{label}_NOT_CANONICAL_DIRECTORY")
    return path


def _required_environment(name: str) -> str:
    value = os.environ.get(name)
    if value is None or not value:
        raise BlockError(f"REQUIRED_ENVIRONMENT_MISSING:{name}")
    return value


def _verified_environment_executable(
    path_name: str,
    sha_name: str,
    *,
    label: str,
) -> tuple[Path, str]:
    path = _require_absolute_regular(
        Path(_required_environment(path_name)),
        label=label,
    )
    expected_sha256 = _required_environment(sha_name)
    if (
        not os.access(path, os.X_OK)
        or SHA256_PATTERN.fullmatch(expected_sha256) is None
        or sha256_file(path) != expected_sha256
    ):
        raise BlockError(f"{label}_EXECUTABLE_IDENTITY_MISMATCH")
    return path, expected_sha256


class PinnedExecutable:
    """실행 pathname의 inode를 열린 FD와 expected SHA에 결속한다."""

    def __init__(
        self,
        path: Path,
        *,
        expected_sha256: str,
        label: str,
    ) -> None:
        self.path = _require_absolute_regular(path, label=label)
        self.expected_sha256 = expected_sha256
        self.label = label
        self.fd = -1
        self._identity: tuple[int, int, int, int, int, int] | None = None

    @staticmethod
    def _metadata_identity(
        metadata: os.stat_result,
    ) -> tuple[int, int, int, int, int, int]:
        return (
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_mode,
            metadata.st_size,
            metadata.st_mtime_ns,
            metadata.st_ctime_ns,
        )

    def __enter__(self) -> PinnedExecutable:
        if SHA256_PATTERN.fullmatch(self.expected_sha256) is None:
            raise BlockError(f"{self.label}_EXPECTED_SHA256_INVALID")
        self.fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW)
        metadata = os.fstat(self.fd)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_mode & 0o111 == 0
        ):
            os.close(self.fd)
            self.fd = -1
            raise BlockError(f"{self.label}_PINNED_FILE_INVALID")
        digest = hashlib.sha256()
        offset = 0
        while True:
            chunk = os.pread(self.fd, 1024 * 1024, offset)
            if not chunk:
                break
            offset += len(chunk)
            digest.update(chunk)
        if digest.hexdigest() != self.expected_sha256:
            os.close(self.fd)
            self.fd = -1
            raise BlockError(f"{self.label}_PINNED_SHA256_MISMATCH")
        self._identity = self._metadata_identity(metadata)
        os.set_inheritable(self.fd, True)
        return self

    def __exit__(self, *_: object) -> None:
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1

    @property
    def proc_path(self) -> Path:
        if self.fd < 0:
            raise BlockError(f"{self.label}_PIN_NOT_OPEN")
        return Path(f"/proc/self/fd/{self.fd}")

    @property
    def pass_fds(self) -> tuple[int, ...]:
        if self.fd < 0:
            raise BlockError(f"{self.label}_PIN_NOT_OPEN")
        return (self.fd,)

    def verify_path_identity(self) -> None:
        """실행 전후 pathname이 pinned inode/bytes로 계속 수렴하는지 확인한다."""

        if self.fd < 0 or self._identity is None:
            raise BlockError(f"{self.label}_PIN_NOT_OPEN")
        try:
            metadata = os.stat(self.path, follow_symlinks=False)
        except OSError as error:
            raise BlockError(f"{self.label}_PATH_SUBSTITUTED") from error
        if (
            self._metadata_identity(metadata) != self._identity
            or sha256_file(self.path) != self.expected_sha256
        ):
            raise BlockError(f"{self.label}_PATH_SUBSTITUTED")


def deterministic_scala_environment(
    *,
    cache_root: Path,
    block_directory: Path,
    java_home: Path,
    base_environment: Mapping[str, str],
) -> tuple[dict[str, str], dict[str, str]]:
    """Timed Scala child가 ambient config/cache를 볼 수 없는 exact environment를 만든다."""

    ambient_names = {
        *FORBIDDEN_AMBIENT_JVM_VARIABLES,
        *FORBIDDEN_AMBIENT_SCALA_VARIABLES,
        "COURSIER_CACHE",
        "S1_4X_SCALA_WORKSPACE",
        "XDG_CONFIG_HOME",
    }
    present_ambient = sorted(
        name for name in ambient_names if name in base_environment
    )
    if present_ambient:
        raise BlockError(
            f"AMBIENT_SCALA_CONFIGURATION_FORBIDDEN:{present_ambient[0]}"
        )
    for path, label in (
        (cache_root, "CACHE_ROOT"),
        (block_directory, "BLOCK_DIRECTORY"),
    ):
        _require_absolute_directory(path, label=label)
    coursier_cache = cache_root / "coursier"
    scala_cli_home = block_directory / "scala-cli-home"
    coursier_config = block_directory / "coursier-config"
    workspace_key = hashlib.sha256(
        str(block_directory).encode("utf-8")
    ).hexdigest()
    scala_workspace = cache_root / "scala-workspaces" / workspace_key
    xdg_config = block_directory / "xdg-config"
    coursier_cache.mkdir(mode=0o700, parents=True, exist_ok=True)
    _require_absolute_directory(
        coursier_cache,
        label="SCALA_COURSIER_CACHE",
    )
    for path in (
        scala_cli_home,
        coursier_config,
        scala_workspace,
        xdg_config,
    ):
        if path.exists() or path.is_symlink():
            raise BlockError("AMBIENT_SCALA_ISOLATION_PATH_FOUND")
        path.mkdir(mode=0o700, parents=True)
        _require_absolute_directory(path, label="SCALA_ISOLATION_DIRECTORY")
    environment = dict(base_environment)
    environment.update(
        {
            "PATH": f"{java_home}/bin:/usr/bin:/bin",
            "JAVA_HOME": str(java_home),
            "COURSIER_CACHE": str(coursier_cache),
            "COURSIER_CONFIG_DIR": str(coursier_config),
            "SCALA_CLI_HOME": str(scala_cli_home),
            "SCALA_CLI_CONFIG": str(scala_cli_home / "config.json"),
            "S1_4X_SCALA_WORKSPACE": str(scala_workspace),
            "XDG_CONFIG_HOME": str(xdg_config),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    closure = {
        "coursierCachePathId": "CACHE_ROOT/coursier",
        "coursierConfigPathId": "BLOCK_ROOT/coursier-config",
        "scalaCliConfigPathId": "BLOCK_ROOT/scala-cli-home/config.json",
        "scalaCliHomePathId": "BLOCK_ROOT/scala-cli-home",
        "scalaWorkspacePathId": (
            f"CACHE_ROOT/scala-workspaces/{workspace_key}"
        ),
        "xdgConfigPathId": "BLOCK_ROOT/xdg-config",
    }
    return environment, closure


def case_directory_name(index: int) -> str:
    """Plan 순서의 case를 고정 폭 direct-child 이름으로 변환한다."""

    if isinstance(index, bool) or not 1 <= index <= 999:
        raise BlockError("CASE_INDEX_INVALID")
    return f"case-{index:03d}"


def build_full_case_command(
    *,
    runner: Path,
    plan: Path,
    profile: str,
    case_id: str,
    jvm_allowlist: Path,
    output_directory: Path,
) -> list[str]:
    """Lane full wrapper의 exact argv를 구성한다."""

    if profile not in {"A", "B", "C"}:
        raise BlockError("SELECTED_PROFILE_ID_INVALID")
    return [
        str(runner),
        "--plan",
        str(plan),
        "--profile",
        profile,
        "--case-id",
        case_id,
        "--jvm-allowlist",
        str(jvm_allowlist),
        "--output-dir",
        str(output_directory),
    ]


def build_producer_command(
    *,
    python: Path,
    producer: Path,
    repo_root: Path,
    plan: Path,
    block_directory: Path,
    selector_id: str,
    scala_jmh_root: Path,
    input_ledger: Path,
    fixture_root: Path,
    selected_profile_result: Path,
    selected_profile_source: Path,
    source_input_manifest: Path,
    compiler_profiles: Path,
    toolchain_lock: Path,
    toolchain_provenance: Path,
    jvm_argument_capability: Path,
    scala_cli: Path,
    java_executable: Path,
    started_at: str,
    finished_at: str,
) -> list[str]:
    """Shared `produce-scala-native`의 frozen option 순서를 구성한다."""

    return [
        str(python),
        str(producer),
        "produce-scala-native",
        "--repo-root",
        str(repo_root),
        "--plan",
        str(plan),
        "--block-dir",
        str(block_directory),
        "--selector",
        selector_id,
        "--scala-jmh-root",
        str(scala_jmh_root),
        "--input-ledger",
        str(input_ledger),
        "--fixture-root",
        str(fixture_root),
        "--selected-profile-result",
        str(selected_profile_result),
        "--selected-profile-source",
        str(selected_profile_source),
        "--source-input-manifest",
        str(source_input_manifest),
        "--compiler-profiles",
        str(compiler_profiles),
        "--toolchain-lock",
        str(toolchain_lock),
        "--toolchain-provenance",
        str(toolchain_provenance),
        "--jvm-argument-capability",
        str(jvm_argument_capability),
        "--scala-cli",
        str(scala_cli),
        "--java-executable",
        str(java_executable),
        "--started-at",
        started_at,
        "--finished-at",
        finished_at,
    ]


def build_block_result_command(
    *,
    python: Path,
    producer: Path,
    repo_root: Path,
    plan: Path,
    block_directory: Path,
    qualification: Path,
    selector_id: str,
    family_id: str,
    rotation_id: str,
    outer_repetition: int,
    run_id: str,
    benchmark_subject_commit: str,
) -> list[str]:
    """Shared block-result validator/builder의 exact argv를 구성한다."""

    return [
        str(python),
        str(producer),
        "--repo-root",
        str(repo_root),
        "--plan",
        str(plan),
        "--block-dir",
        str(block_directory),
        "--qualification",
        str(qualification),
        "--boundary",
        "scala",
        "--selector",
        selector_id,
        "--family",
        family_id,
        "--rotation",
        rotation_id,
        "--outer-repetition",
        str(outer_repetition),
        "--run-id",
        run_id,
        "--benchmark-subject-commit",
        benchmark_subject_commit,
    ]


def build_measurement_marker_command(
    *,
    python: Path,
    marker: Path,
    qualification: Path,
) -> list[str]:
    """Official integration runner의 marker-only subcommand argv를 구성한다."""

    return [
        str(python),
        str(marker),
        "mark-measurement-entered",
        "--qualification",
        str(qualification),
    ]


def _selector_input_closure(
    plan: Mapping[str, Any],
    selector: Mapping[str, Any],
    cases: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    return {
        "boundaryId": "scala",
        "familyId": selector["familyId"],
        "selectorId": selector["selectorId"],
        "expectedCaseIds": selector["expectedCaseIds"],
        "expectedCaseCount": len(cases),
        "inputClosureSha256": canonical_sha256(
            {
                "fixtureFreezeIdentity": plan["fixtureFreezeIdentity"],
                "selector": selector,
                "cases": list(cases),
            }
        ),
    }


def _selector_and_cases(
    plan: Mapping[str, Any],
    *,
    selector_id: str,
    family_id: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    selectors = [
        item
        for item in plan.get("familySelectors", [])
        if isinstance(item, dict) and item.get("selectorId") == selector_id
    ]
    if len(selectors) != 1:
        raise BlockError("SELECTOR_IDENTITY_MISMATCH")
    selector = selectors[0]
    if (
        selector.get("boundaryId") != "scala"
        or selector.get("familyId") != family_id
        or selector.get("criterionMatchMode") != "none"
        or selector.get("criterionPrefix") is not None
        or not isinstance(selector.get("jmhIncludeRegex"), str)
        or not selector["jmhIncludeRegex"]
    ):
        raise BlockError("SELECTOR_IDENTITY_MISMATCH")
    by_id = {
        item["caseId"]: item
        for item in plan.get("cases", [])
        if isinstance(item, dict) and isinstance(item.get("caseId"), str)
    }
    try:
        cases = [by_id[case_id] for case_id in selector["expectedCaseIds"]]
    except (KeyError, TypeError) as exc:
        raise BlockError("SELECTOR_CASE_CLOSURE_INVALID") from exc
    if (
        not 2 <= len(cases) <= 45
        or [item.get("familyId") for item in cases]
        != [family_id] * len(cases)
    ):
        raise BlockError("SELECTOR_FAMILY_CLOSURE_INVALID")
    return selector, cases


def _validate_rotation(rotation_id: str, repetition_text: str) -> int:
    if (
        not repetition_text.isascii()
        or not repetition_text.isdigit()
        or repetition_text.startswith("0")
    ):
        raise BlockError("OUTER_REPETITION_INVALID")
    repetition = int(repetition_text)
    if repetition not in (1, 2, 3) or rotation_id != f"R{repetition}":
        raise BlockError("ROTATION_REPETITION_MISMATCH")
    return repetition


def _validate_qualification(
    *,
    path: Path,
    plan: Mapping[str, Any],
    plan_sha256: str,
    selector: Mapping[str, Any],
    cases: Sequence[Mapping[str, Any]],
    rotation_id: str,
    outer_repetition: int,
    run_id: str,
    benchmark_subject_commit: str,
    block_directory: Path,
) -> dict[str, Any]:
    if path.parent != block_directory or path.name != "timeout-qualification.json":
        raise BlockError("QUALIFICATION_PATH_MISMATCH")
    qualification = strict_json_load(path)
    exact_fields = {
        "schemaVersion",
        "phase",
        "measurementEntered",
        "plan",
        "subject",
        "run",
        "hostValidity",
        "selectorInputClosure",
        "command",
    }
    if (
        not isinstance(qualification, dict)
        or set(qualification) != exact_fields
        or qualification.get("schemaVersion")
        != "s1.4x-timeout-qualification-v1"
        or qualification.get("phase") != "PRE_RUN"
        or qualification.get("measurementEntered") is not False
        or qualification.get("plan")
        != {"planId": plan["planId"], "sha256": plan_sha256}
        or qualification.get("subject")
        != {
            "benchmarkSubjectCommit": benchmark_subject_commit,
            "candidateSourceCommit": benchmark_subject_commit,
        }
    ):
        raise BlockError("INVALID_PRE_RUN_QUALIFICATION_STATE")
    timeout_seconds = plan["execution"]["familyBlockTimeoutSeconds"][
        selector["selectorId"]
    ]
    if qualification.get("run") != {
        "runId": run_id,
        "rotationId": rotation_id,
        "outerRepetition": outer_repetition,
        "timeoutSeconds": timeout_seconds,
    }:
        raise BlockError("QUALIFICATION_RUN_MISMATCH")
    if qualification.get("selectorInputClosure") != _selector_input_closure(
        plan,
        selector,
        cases,
    ):
        raise BlockError("QUALIFICATION_SELECTOR_CLOSURE_MISMATCH")
    host = qualification.get("hostValidity")
    if (
        not isinstance(host, dict)
        or set(host)
        != {
            "artifactPath",
            "sha256",
            "status",
            "policySha256",
            "portableHostIdSha256",
        }
        or host.get("artifactPath") != "host-validity.json"
        or host.get("status") != "PASS"
        or any(
            SHA256_PATTERN.fullmatch(str(host.get(field))) is None
            for field in ("sha256", "policySha256", "portableHostIdSha256")
        )
        or sha256_file(block_directory / "host-validity.json")
        != host.get("sha256")
    ):
        raise BlockError("QUALIFICATION_HOST_VALIDITY_INVALID")
    command = qualification.get("command")
    if (
        not isinstance(command, dict)
        or set(command)
        != {
            "commandManifestSha256",
            "allowedExecutable",
            "renderedArgvSha256",
        }
        or SHA256_PATTERN.fullmatch(str(command.get("commandManifestSha256")))
        is None
        or SHA256_PATTERN.fullmatch(str(command.get("renderedArgvSha256")))
        is None
        or not isinstance(command.get("allowedExecutable"), dict)
        or set(command["allowedExecutable"])
        != {"path", "resolvedPath", "sha256"}
        or SHA256_PATTERN.fullmatch(
            str(command["allowedExecutable"].get("sha256"))
        )
        is None
    ):
        raise BlockError("QUALIFICATION_COMMAND_INVALID")
    return qualification


def _validate_measurement_qualification(
    path: Path,
    pre_run: Mapping[str, Any],
) -> None:
    expected = dict(pre_run)
    expected["phase"] = "MEASUREMENT"
    expected["measurementEntered"] = True
    if strict_json_load(path) != expected:
        raise BlockError("INVALID_MEASUREMENT_QUALIFICATION")


def _verify_subject_commit(
    repo_root: Path,
    expected: str,
    *,
    scala_root: Path,
) -> None:
    if COMMIT_PATTERN.fullmatch(expected) is None:
        raise BlockError("BENCHMARK_SUBJECT_COMMIT_INVALID")
    completed = subprocess.run(
        ["/usr/bin/git", "-C", str(repo_root), "rev-parse", "--verify", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if completed.returncode != 0 or completed.stdout.strip() != expected:
        raise BlockError("BENCHMARK_SUBJECT_COMMIT_MISMATCH")
    tracked_status = subprocess.run(
        [
            "/usr/bin/git",
            "-C",
            str(repo_root),
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    if tracked_status.returncode != 0 or tracked_status.stdout:
        raise BlockError("BENCHMARK_SUBJECT_WORKTREE_DIRTY")
    ignored_status = subprocess.run(
        [
            "/usr/bin/git",
            "-C",
            str(repo_root),
            "status",
            "--porcelain=v1",
            "--ignored=matching",
            "--untracked-files=all",
            "--",
            str(scala_root.relative_to(repo_root)),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    if ignored_status.returncode != 0:
        raise BlockError("BENCHMARK_SUBJECT_IGNORED_AUDIT_FAILED")
    forbidden_components = ("/.scala-build/", "/.bsp/")
    ignored_paths = [
        line[3:].rstrip("/")
        for line in ignored_status.stdout.splitlines()
        if line.startswith("!! ")
    ]
    if any(
        any(
            marker in f"/{path}/"
            for marker in forbidden_components
        )
        for path in ignored_paths
    ):
        raise BlockError("BENCHMARK_SUBJECT_REPO_LOCAL_BUILD_OUTPUT_FOUND")


def _run_json_command(
    command: Sequence[str],
    *,
    label: str,
    timeout: int = 300,
    environment: Mapping[str, str] | None = None,
    pass_fds: Sequence[int] = (),
) -> dict[str, Any]:
    completed = subprocess.run(
        list(command),
        check=False,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=None if environment is None else dict(environment),
        pass_fds=tuple(pass_fds),
    )
    if completed.returncode != 0:
        raise BlockError(f"{label}_FAILED:{completed.returncode}")
    if completed.stderr:
        raise BlockError(f"{label}_UNEXPECTED_STDERR")
    result = _strict_json_decode(completed.stdout, label=label)
    if not isinstance(result, dict):
        raise BlockError(f"{label}_OUTPUT_INVALID")
    return result


def _run_checked(
    command: Sequence[str],
    *,
    label: str,
    cwd: Path,
    environment: Mapping[str, str] | None = None,
    pass_fds: Sequence[int] = (),
) -> None:
    completed = subprocess.run(
        list(command),
        cwd=cwd,
        check=False,
        stdin=subprocess.DEVNULL,
        env=None if environment is None else dict(environment),
        pass_fds=tuple(pass_fds),
    )
    if completed.returncode != 0:
        raise BlockError(f"{label}_FAILED:{completed.returncode}")


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace(
        "+00:00",
        "Z",
    )


def _validate_selected_profile(
    *,
    selected_result_path: Path,
    selected_source_path: Path,
    source_manifest_path: Path,
    compiler_profiles_path: Path,
    toolchain_lock_path: Path,
    merged_provenance_path: Path,
    jvm_allowlist_path: Path,
    scala_cli_path: Path,
    java_executable_path: Path,
) -> tuple[dict[str, Any], str]:
    selected = strict_json_load(selected_result_path)
    required_fields = {
        "schemaVersion",
        "sourceInputManifestSha256",
        "compilerProfilesSha256",
        "toolchainLockSha256",
        "mergedToolchainProvenanceSha256",
        "scalaCliBinarySha256",
        "javaExecutableSha256",
        "jvmArgumentAllowlistSha256",
        "effectiveJvmArgumentsCapabilitySha256",
        "selectedProfileSourceSha256",
        "selectedProfileId",
        "selectionStatus",
    }
    if (
        not isinstance(selected, dict)
        or not required_fields.issubset(selected)
        or selected.get("schemaVersion")
        != "s1.4x-scala-selected-profile-result-v1"
        or selected.get("selectionStatus") != "PASS"
        or selected.get("selectedProfileId") not in {"A", "B", "C"}
    ):
        raise BlockError("SELECTED_PROFILE_RESULT_INVALID")
    expected_hashes = {
        "selectedProfileSourceSha256": sha256_file(selected_source_path),
        "sourceInputManifestSha256": sha256_file(source_manifest_path),
        "compilerProfilesSha256": sha256_file(compiler_profiles_path),
        "toolchainLockSha256": sha256_file(toolchain_lock_path),
        "mergedToolchainProvenanceSha256": sha256_file(
            merged_provenance_path
        ),
        "scalaCliBinarySha256": sha256_file(scala_cli_path),
        "javaExecutableSha256": sha256_file(java_executable_path),
        "jvmArgumentAllowlistSha256": sha256_file(jvm_allowlist_path),
        "effectiveJvmArgumentsCapabilitySha256": sha256_file(
            jvm_allowlist_path
        ),
    }
    if any(selected.get(field) != value for field, value in expected_hashes.items()):
        raise BlockError("SELECTED_PROFILE_HASH_CLOSURE_MISMATCH")
    return selected, str(selected["selectedProfileId"])


def _validate_raw_case_directory(
    directory: Path,
    *,
    expected_forks: int,
) -> None:
    _require_absolute_directory(directory, label="SCALA_JMH_CASE_ROOT")
    for name in RAW_CASE_FILES:
        _require_absolute_regular(
            directory / name,
            label=f"SCALA_JMH_RAW_{name}",
        )
    fork_root = _require_absolute_directory(
        directory / "fork-evidence",
        label="SCALA_JMH_FORK_ROOT",
    )
    forks = sorted(fork_root.glob("jvm-fork-*.json"), key=lambda path: path.name)
    if (
        len(forks) != expected_forks
        or any(path.is_symlink() or not path.is_file() for path in forks)
    ):
        raise BlockError("SCALA_JMH_FORK_CLOSURE_INVALID")


def run_block(arguments: argparse.Namespace) -> dict[str, Any]:
    """한 Scala family를 순차 JMH raw → shared producer → block-result로 연결한다."""

    repo_root = _require_absolute_directory(arguments.repo_root, label="REPO_ROOT")
    if Path.cwd().resolve(strict=True) != repo_root:
        raise BlockError("REPOSITORY_WORKING_DIRECTORY_REQUIRED")
    numeric_root = (
        repo_root
        / "workspaces/decision-platform/research/s1-4x-numeric-parity"
    )
    scala_root = _require_absolute_directory(
        numeric_root / "scala",
        label="SCALA_ROOT",
    )
    integration_root = _require_absolute_directory(
        numeric_root / "integration",
        label="INTEGRATION_ROOT",
    )
    plan_path = _require_absolute_regular(arguments.plan, label="PLAN")
    expected_plan = _require_absolute_regular(
        numeric_root / "benchmarks/benchmark-plan.v1.json",
        label="FROZEN_PLAN",
    )
    if plan_path != expected_plan:
        raise BlockError("PLAN_PATH_MISMATCH")
    block_directory = _require_absolute_directory(
        arguments.block_dir,
        label="BLOCK_DIRECTORY",
    )
    qualification_path = _require_absolute_regular(
        arguments.qualification,
        label="QUALIFICATION",
    )
    if arguments.boundary != "scala":
        raise BlockError("BOUNDARY_MISMATCH")
    if arguments.selector != f"scala/{arguments.family}":
        raise BlockError("SELECTOR_FAMILY_MISMATCH")
    if RUN_ID_PATTERN.fullmatch(arguments.run_id) is None:
        raise BlockError("RUN_ID_INVALID")
    outer_repetition = _validate_rotation(
        arguments.rotation,
        arguments.outer_repetition,
    )
    expected_tail = (
        Path(arguments.run_id)
        / arguments.rotation
        / "scala"
        / arguments.family
    )
    if (
        tuple(block_directory.parts[-len(expected_tail.parts) :])
        != expected_tail.parts
    ):
        raise BlockError("BLOCK_DIRECTORY_LAYOUT_MISMATCH")
    _verify_subject_commit(
        repo_root,
        arguments.benchmark_subject_commit,
        scala_root=scala_root,
    )

    benchmark_python, _ = _verified_environment_executable(
        "S1_4X_BENCHMARK_PYTHON_BIN",
        "S1_4X_BENCHMARK_PYTHON_SHA256",
        label="BENCHMARK_PYTHON",
    )
    if Path(sys.executable).resolve(strict=True) != benchmark_python:
        raise BlockError("HELPER_PYTHON_IDENTITY_MISMATCH")
    scala_cli, _ = _verified_environment_executable(
        "S1_4X_SCALA_CLI_BIN",
        "S1_4X_SCALA_CLI_SHA256",
        label="SCALA_CLI",
    )
    java_executable, _ = _verified_environment_executable(
        "S1_4X_SCALA_JAVA_BIN",
        "S1_4X_SCALA_JAVA_SHA256",
        label="JAVA_EXECUTABLE",
    )
    java_home = _require_absolute_directory(
        Path(_required_environment("JAVA_HOME")),
        label="JAVA_HOME",
    )
    if java_executable != java_home / "bin/java":
        raise BlockError("JAVA_HOME_EXECUTABLE_MISMATCH")
    if any(name in os.environ for name in FORBIDDEN_AMBIENT_JVM_VARIABLES):
        raise BlockError("AMBIENT_JVM_OVERRIDE_FORBIDDEN")
    benchmark_python_pin = PinnedExecutable(
        benchmark_python,
        expected_sha256=_required_environment(
            "S1_4X_BENCHMARK_PYTHON_SHA256"
        ),
        label="BENCHMARK_PYTHON",
    ).__enter__()
    scala_cli_pin = PinnedExecutable(
        scala_cli,
        expected_sha256=_required_environment("S1_4X_SCALA_CLI_SHA256"),
        label="SCALA_CLI",
    ).__enter__()
    java_pin = PinnedExecutable(
        java_executable,
        expected_sha256=_required_environment("S1_4X_SCALA_JAVA_SHA256"),
        label="JAVA_EXECUTABLE",
    ).__enter__()
    benchmark_python_exec = benchmark_python_pin.proc_path
    pinned_fds = (
        *benchmark_python_pin.pass_fds,
        *scala_cli_pin.pass_fds,
        *java_pin.pass_fds,
    )

    selected_result_path = _require_absolute_regular(
        Path(_required_environment("S1_4X_SCALA_SELECTED_PROFILE_RESULT")),
        label="SELECTED_PROFILE_RESULT",
    )
    jvm_allowlist_path = _require_absolute_regular(
        Path(_required_environment("S1_4X_SCALA_JVM_ALLOWLIST_RESULT")),
        label="JVM_ARGUMENT_CAPABILITY",
    )
    selected_source_path = _require_absolute_regular(
        scala_root / "selected-profile.scala",
        label="SELECTED_PROFILE_SOURCE",
    )
    source_manifest_path = _require_absolute_regular(
        scala_root / "source-inputs.v1.json",
        label="SOURCE_INPUT_MANIFEST",
    )
    compiler_profiles_path = _require_absolute_regular(
        scala_root / "compiler-profiles.v1.json",
        label="COMPILER_PROFILES",
    )
    toolchain_lock_path = _require_absolute_regular(
        scala_root / "toolchain-lock.v1.json",
        label="TOOLCHAIN_LOCK",
    )
    merged_provenance_path = _require_absolute_regular(
        numeric_root / "contract/toolchain-provenance.v1.json",
        label="MERGED_TOOLCHAIN_PROVENANCE",
    )
    fixture_root = _require_absolute_directory(
        numeric_root / "contract/fixtures",
        label="FIXTURE_ROOT",
    )
    _, profile = _validate_selected_profile(
        selected_result_path=selected_result_path,
        selected_source_path=selected_source_path,
        source_manifest_path=source_manifest_path,
        compiler_profiles_path=compiler_profiles_path,
        toolchain_lock_path=toolchain_lock_path,
        merged_provenance_path=merged_provenance_path,
        jvm_allowlist_path=jvm_allowlist_path,
        scala_cli_path=scala_cli,
        java_executable_path=java_executable,
    )

    for directory in (
        scala_root / "tools",
        numeric_root / "benchmarks",
        integration_root,
    ):
        if str(directory) not in sys.path:
            sys.path.insert(0, str(directory))
    try:
        import validate_benchmark_report  # type: ignore[import-not-found]
    except ImportError as exc:
        raise BlockError("BENCHMARK_VALIDATOR_IMPORT_FAILED") from exc
    plan = validate_benchmark_report.validate_plan(plan_path)
    if not isinstance(plan, dict):
        raise BlockError("PLAN_VALIDATOR_RETURNED_NON_OBJECT")
    if sorted(os.sched_getaffinity(0)) != plan["execution"]["cpuSet"]:
        raise BlockError("ACTUAL_CPU_AFFINITY_MISMATCH")
    selector, cases = _selector_and_cases(
        plan,
        selector_id=arguments.selector,
        family_id=arguments.family,
    )
    qualification = _validate_qualification(
        path=qualification_path,
        plan=plan,
        plan_sha256=sha256_file(plan_path),
        selector=selector,
        cases=cases,
        rotation_id=arguments.rotation,
        outer_repetition=outer_repetition,
        run_id=arguments.run_id,
        benchmark_subject_commit=arguments.benchmark_subject_commit,
        block_directory=block_directory,
    )

    input_ledger = block_directory / "input-ledger.json"
    scala_jmh_root = block_directory / "scala-jmh"
    reserved_outputs = (
        input_ledger,
        scala_jmh_root,
        block_directory / "receipts",
        block_directory / "native-contract-validation.json",
        block_directory / "native.json",
        block_directory / "native-statistics.json",
        block_directory / "block-result.json",
        block_directory / "scala-cli-home",
        block_directory / "coursier-config",
        block_directory / "xdg-config",
    )
    if any(path.exists() or path.is_symlink() for path in reserved_outputs):
        raise BlockError("BENCHMARK_OUTPUT_ALREADY_EXISTS")
    environment, environment_closure = deterministic_scala_environment(
        cache_root=_require_absolute_directory(
            Path(_required_environment("S1_4X_CACHE_ROOT")),
            label="CACHE_ROOT",
        ),
        block_directory=block_directory,
        java_home=java_home,
        base_environment=os.environ,
    )
    environment["S1_4X_SCALA_CLI_EXEC_PATH"] = str(
        scala_cli_pin.proc_path
    )
    environment["S1_4X_BENCHMARK_PYTHON_PINNED_FD_PATH"] = str(
        benchmark_python_exec
    )
    environment["S1_4X_SCALA_JAVA_PINNED_FD_PATH"] = str(
        java_pin.proc_path
    )
    environment["S1_4X_SCALA_ENVIRONMENT_VALUES_SHA256"] = (
        canonical_sha256(environment_closure)
    )

    assert_selected = _require_absolute_regular(
        scala_root / "tools/assert-selected-profile.sh",
        label="SELECTED_PROFILE_ASSERTION",
    )
    _run_checked(
        [str(assert_selected), "--benchmark-subject"],
        label="SELECTED_PROFILE_ASSERTION",
        cwd=scala_root,
        environment=environment,
        pass_fds=pinned_fds,
    )
    for pinned in (benchmark_python_pin, scala_cli_pin, java_pin):
        pinned.verify_path_identity()

    ledger_script = _require_absolute_regular(
        integration_root / "benchmark_input_ledger.py",
        label="BENCHMARK_INPUT_LEDGER_SCRIPT",
    )
    producer_script = _require_absolute_regular(
        integration_root / "native_benchmark_block.py",
        label="NATIVE_BENCHMARK_BLOCK_SCRIPT",
    )
    marker_script = _require_absolute_regular(
        integration_root / "run_rotated_blocks.py",
        label="MEASUREMENT_MARKER_SCRIPT",
    )
    ledger_result = _run_json_command(
        [
            str(benchmark_python_exec),
            str(ledger_script),
            "--repo-root",
            str(repo_root),
            "--plan",
            str(plan_path),
            "--boundary",
            "scala",
            "--selector",
            arguments.selector,
            "--output",
            str(input_ledger),
        ],
        label="BENCHMARK_INPUT_LEDGER",
        environment=environment,
        pass_fds=pinned_fds,
    )
    if ledger_result != {
        "boundaryId": "scala",
        "selectorId": arguments.selector,
        "status": "PASS",
    }:
        raise BlockError("BENCHMARK_INPUT_LEDGER_OUTPUT_INVALID")

    scala_jmh_root.mkdir(mode=0o700)
    runner = _require_absolute_regular(
        scala_root / "tools/run-jmh-native-full.sh",
        label="SCALA_FULL_JMH_RUNNER",
    )
    started_at = _utc_now()
    _run_checked(
        build_measurement_marker_command(
            python=benchmark_python_exec,
            marker=marker_script,
            qualification=qualification_path,
        ),
        label="MEASUREMENT_MARKER",
        cwd=repo_root,
        environment=environment,
        pass_fds=pinned_fds,
    )
    _validate_measurement_qualification(qualification_path, qualification)

    expected_forks = int(plan["execution"]["forks"]["scala"])
    for index, case_id in enumerate(selector["expectedCaseIds"], start=1):
        case_directory = scala_jmh_root / case_directory_name(index)
        _run_checked(
            build_full_case_command(
                runner=runner,
                plan=plan_path,
                profile=profile,
                case_id=case_id,
                jvm_allowlist=jvm_allowlist_path,
                output_directory=case_directory,
            ),
            label=f"SCALA_FULL_JMH_CASE_{index:03d}",
            cwd=scala_root,
            environment=environment,
            pass_fds=pinned_fds,
        )
        _validate_raw_case_directory(
            case_directory,
            expected_forks=expected_forks,
        )
    finished_at = _utc_now()

    producer_result = _run_json_command(
        build_producer_command(
            python=benchmark_python_exec,
            producer=producer_script,
            repo_root=repo_root,
            plan=plan_path,
            block_directory=block_directory,
            selector_id=arguments.selector,
            scala_jmh_root=scala_jmh_root,
            input_ledger=input_ledger,
            fixture_root=fixture_root,
            selected_profile_result=selected_result_path,
            selected_profile_source=selected_source_path,
            source_input_manifest=source_manifest_path,
            compiler_profiles=compiler_profiles_path,
            toolchain_lock=toolchain_lock_path,
            toolchain_provenance=merged_provenance_path,
            jvm_argument_capability=jvm_allowlist_path,
            scala_cli=scala_cli,
            java_executable=java_executable,
            started_at=started_at,
            finished_at=finished_at,
        ),
        label="SCALA_NATIVE_PRODUCER",
        environment=environment,
        pass_fds=pinned_fds,
    )
    if (
        set(producer_result) != SHARED_PRODUCER_RESULT_FIELDS
        or producer_result.get("boundaryId") != "scala"
        or producer_result.get("selectorId") != arguments.selector
        or producer_result.get("caseCount") != len(cases)
        or producer_result.get("status") != "PASS"
        or any(
            SHA256_PATTERN.fullmatch(str(producer_result.get(field))) is None
            for field in (
                "nativeContractValidationSha256",
                "nativeReportSha256",
                "nativeStatisticsSha256",
            )
        )
    ):
        raise BlockError("SCALA_NATIVE_PRODUCER_OUTPUT_INVALID")

    block_result = _run_json_command(
        build_block_result_command(
            python=benchmark_python_exec,
            producer=producer_script,
            repo_root=repo_root,
            plan=plan_path,
            block_directory=block_directory,
            qualification=qualification_path,
            selector_id=arguments.selector,
            family_id=arguments.family,
            rotation_id=arguments.rotation,
            outer_repetition=outer_repetition,
            run_id=arguments.run_id,
            benchmark_subject_commit=arguments.benchmark_subject_commit,
        ),
        label="NATIVE_BENCHMARK_BLOCK",
        environment=environment,
        pass_fds=pinned_fds,
    )
    if (
        set(block_result)
        != {"boundaryId", "selectorId", "blockResultSha256", "status"}
        or block_result.get("boundaryId") != "scala"
        or block_result.get("selectorId") != arguments.selector
        or block_result.get("status") != "PASS"
        or SHA256_PATTERN.fullmatch(str(block_result.get("blockResultSha256")))
        is None
    ):
        raise BlockError("NATIVE_BENCHMARK_BLOCK_OUTPUT_INVALID")
    result_path = _require_absolute_regular(
        block_directory / "block-result.json",
        label="BLOCK_RESULT",
    )
    if sha256_file(result_path) != block_result["blockResultSha256"]:
        raise BlockError("BLOCK_RESULT_SHA256_MISMATCH")
    for pinned in (benchmark_python_pin, scala_cli_pin, java_pin):
        pinned.verify_path_identity()
    return {
        "status": "PASS",
        "selectorId": arguments.selector,
        "caseCount": len(cases),
        "selectedProfileId": profile,
        "selectedProfileResultSha256": sha256_file(selected_result_path),
        "nativeReportSha256": producer_result["nativeReportSha256"],
        "blockResultSha256": block_result["blockResultSha256"],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--block-dir", type=Path, required=True)
    parser.add_argument("--qualification", type=Path, required=True)
    parser.add_argument("--boundary", required=True)
    parser.add_argument("--selector", required=True)
    parser.add_argument("--family", required=True)
    parser.add_argument("--rotation", required=True)
    parser.add_argument("--outer-repetition", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--benchmark-subject-commit", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        result = run_block(arguments)
    except (
        BlockError,
        OSError,
        KeyError,
        TypeError,
        ValueError,
        subprocess.SubprocessError,
    ) as exc:
        print(f"SCALA_BENCHMARK_BLOCK_FAIL:{exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, allow_nan=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
