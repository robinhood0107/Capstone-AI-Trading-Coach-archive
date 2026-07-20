#!/usr/bin/env python3
"""S1.4X full correctness와 87-block benchmark를 무인 직렬 실행하고 봉인한다."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import math
import os
import re
import signal
import stat
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PLAN_SCHEMA = "s1.4x-detached-full-run-plan-v1"
CANDIDATE_SCHEMA = "s1.4x-detached-full-run-candidate-v1"
TERMINAL_SCHEMA = "s1.4x-detached-full-run-terminal-v1"
EVIDENCE_INDEX_SCHEMA = "s1.4x-detached-full-run-evidence-index-v1"
CONTINUATION_MANIFEST_SCHEMA = "s1.4x-continuation-source-manifest-v1"
CONTINUATION_MANIFEST_NAME = "continuation-source-manifest.v1.json"
FRESH_RETRY_POLICY = "NONE_FRESH_RUN_REQUIRED"
REUSE_RETRY_POLICY = "SEALED_PREFIX_REUSE_NEW_RUN_ONLY"
REUSE_MODE = "SEALED_PREFIX_REUSE_V1"
COMMIT = re.compile(r"^[0-9a-f]{40}$")
RUN_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,47}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
ALLOWED_SUMMARY_STATUS = {
    "PASS",
    "PASS_WITH_VALID_PERFORMANCE_TIMEOUTS",
}
BOUNDARY_IDS = {
    "python-numpy-s1-4",
    "python-numpy-s1-4r",
    "python-jax-eager-s1-4r",
    "python-jax-jit-s1-4r",
    "scala",
    "haskell",
}
SCORE_CATEGORY_MAX = {
    "correctness": 35.0,
    "purityAuditability": 20.0,
    "reproducibility": 15.0,
    "performance": 15.0,
    "maintainability": 10.0,
    "integrationFit": 5.0,
}
FINAL_REPORT_NAMES = (
    "benchmark-summary.v1.json",
    "benchmark-host-ledger.v1.json",
    "benchmark-raw-hash-manifest.v1.json",
    "scorecard.v1.json",
)
STAGE_ORDER = (
    "correctness-oci-regression",
    "command-sealing",
    "frozen-timing",
    "typed-finalization",
)
SCALA_BASE_IMAGE = (
    "docker.io/library/eclipse-temurin@sha256:"
    "5742cdb98ef117621ad75f57475ab127db04f344d9c523307cc60b9955bdd676"
)
HASKELL_BASE_IMAGE = (
    "docker.io/library/haskell@sha256:"
    "417d4bc30ac7d8d5ff04ec97937f86eb508b0c76bfd1a39b5ec225688531aa9d"
)
VECTOR_ARCHIVE_SHA256 = (
    "28f203c786cbf8ac6dc3fea3378ec36f34173d505fb4a1dd60fc8418ad91c423"
)
SCALAFMT_ARCHIVE_SHA256 = (
    "e7d43a5621074a63a46d5b287d0b0bb0650033deeb836af2b27515b2127476f2"
)
BENCHMARK_PYTHON_SHA256 = (
    "9544d2a29138833e6177d45dbc57468d37710b5080c901fbb579d53f251cdd6f"
)
RUNTIME_ENV_BY_ROLE = {
    "uv": "S1_4X_UV_BIN",
    "docker": "S1_4X_DOCKER_BIN",
    "benchmarkPython": "S1_4X_BENCHMARK_PYTHON_BIN",
    "scalaCli": "S1_4X_SCALA_CLI_BIN",
    "scalafix": "S1_4X_SCALAFIX_BIN",
    "scalafmt": "S1_4X_SCALAFMT_BIN",
    "ghcup": "S1_4X_GHCUP_BIN",
    "stack": "S1_4X_STACK_BIN",
    "authoritativeGhc": "S1_4X_AUTHORITATIVE_GHC_BIN",
    "compatibilityGhc": "S1_4X_LATEST_GHC_BIN",
    "hlint": "S1_4X_HLINT_BIN",
    "stylishHaskell": "S1_4X_STYLISH_BIN",
    "scalafmtArchive": "S1_4X_SCALAFMT_ARCHIVE",
    "vectorSourceArchive": "S1_4X_VECTOR_SOURCE_ARCHIVE",
}
NON_EXECUTABLE_ROLES = {"scalafmtArchive", "vectorSourceArchive"}
_ACTIVE_PROCESS: subprocess.Popen[bytes] | None = None
_INTERRUPTED_SIGNAL: int | None = None


class FullRunError(RuntimeError):
    """Supervisor 계약 위반 또는 evidence 불완전을 나타낸다."""


class StageFailure(FullRunError):
    """단일 stage가 실패했으며 같은 run에서 재시도하면 안 됨을 나타낸다."""

    def __init__(
        self,
        stage: str,
        exit_code: int,
        failure_code: str = "STAGE_COMMAND_FAILED",
    ) -> None:
        super().__init__(f"{failure_code}:{stage}:exit={exit_code}")
        self.stage = stage
        self.exit_code = exit_code
        self.failure_code = failure_code


@dataclass(frozen=True)
class StageCommand:
    """고정된 한 stage의 shell-free argv다."""

    name: str
    argv: tuple[str, ...]


@dataclass(frozen=True)
class StageReceipt:
    """단계별 process 결과와 append-only log identity다."""

    stage: str
    argv: tuple[str, ...]
    startedAt: str
    completedAt: str
    durationSeconds: float
    exitCode: int
    stdoutRelativePath: str
    stderrRelativePath: str
    stdoutSha256: str
    stderrSha256: str
    status: str

    @classmethod
    def for_test(cls, stage: str) -> StageReceipt:
        """무거운 subprocess 없이 순서 제어만 시험하는 receipt를 만든다."""

        return cls(
            stage=stage,
            argv=(f"/test/{stage}",),
            startedAt="2026-01-01T00:00:00Z",
            completedAt="2026-01-01T00:00:01Z",
            durationSeconds=1.0,
            exitCode=0,
            stdoutRelativePath=f"stages/{stage}/stdout.log",
            stderrRelativePath=f"stages/{stage}/stderr.log",
            stdoutSha256="0" * 64,
            stderrSha256="0" * 64,
            status="PASS",
        )


@dataclass(frozen=True)
class RunPaths:
    """한 run의 immutable evidence와 terminal control plane 경로다."""

    root: Path
    plan: Path
    plan_sidecar: Path
    events: Path
    stages: Path
    checkpoints: Path
    candidate: Path
    terminal: Path
    control: Path
    logs: Path
    correctness: Path
    correctness_audit: Path
    benchmark: Path
    final_reports: Path

    @classmethod
    def from_run_root(cls, root: Path) -> RunPaths:
        return cls(
            root=root,
            plan=root / "run-plan.v1.json",
            plan_sidecar=root / "run-plan.v1.sha256",
            events=root / "events.jsonl",
            stages=root / "stages",
            checkpoints=root / "checkpoints",
            candidate=root / "candidate",
            terminal=root / "terminal",
            control=root / "control",
            logs=root / "logs",
            correctness=root / "correctness",
            correctness_audit=root / "correctness-final-audit",
            benchmark=root / "benchmark",
            final_reports=root / "final-reports",
        )


StageRunner = Callable[
    [StageCommand, RunPaths, dict[str, str], float],
    StageReceipt,
]
OutputValidator = Callable[[StageCommand, RunPaths], None]
AuditRevalidator = Callable[
    [RunPaths, Mapping[str, Any]],
    dict[str, Any],
]


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _canonical_bytes(value: object) -> bytes:
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


def _strict_json_load(path: Path) -> Any:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise FullRunError(f"DUPLICATE_JSON_KEY:{path}:{key}")
            result[key] = value
        return result

    def reject_constant(token: str) -> Any:
        raise FullRunError(f"NON_FINITE_JSON:{path}:{token}")

    try:
        return json.loads(
            path.read_text(encoding="utf-8", errors="strict"),
            object_pairs_hook=unique,
            parse_constant=reject_constant,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise FullRunError(f"INVALID_JSON:{path}") from exc


def _exclusive_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        stream = os.fdopen(descriptor, "wb")
        descriptor = -1
        with stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        # 같은 filesystem의 hard-link publish는 target이 이미 있으면 실패하고,
        # consumer에게는 완성된 bytes만 한 번에 보인다.
        os.link(temporary, path, follow_symlinks=False)
        directory_descriptor = os.open(
            path.parent,
            os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC,
        )
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def _exclusive_json(path: Path, value: object) -> None:
    _exclusive_bytes(path, _canonical_bytes(value))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _append_event(path: Path, event: Mapping[str, object]) -> None:
    payload = _canonical_bytes({"at": _utc_now(), **dict(event)})
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_APPEND,
        0o600,
    )
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        os.write(descriptor, payload)
        os.fsync(descriptor)
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _git_environment(home: Path) -> dict[str, str]:
    return {
        "HOME": str(home),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": "/usr/bin:/bin",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_TERMINAL_PROMPT": "0",
    }


def _git(repo: Path, home: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["/usr/bin/git", "-c", "core.fsmonitor=false", *arguments],
        cwd=repo,
        env=_git_environment(home),
        capture_output=True,
        check=False,
        text=True,
        timeout=30,
    )
    if completed.returncode != 0:
        raise FullRunError(f"GIT_COMMAND_FAILED:{arguments[0]}:{completed.returncode}")
    return completed.stdout.strip()


def _validate_absolute_directory(path: Path, *, label: str) -> Path:
    if not path.is_absolute() or path.is_symlink():
        raise FullRunError(f"{label}_PATH_UNSAFE")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise FullRunError(f"{label}_PATH_UNAVAILABLE") from exc
    if resolved != path or not path.is_dir():
        raise FullRunError(f"{label}_PATH_UNSAFE")
    return path


def _snapshot_file(
    path: Path,
    *,
    label: str,
    executable: bool,
) -> dict[str, object]:
    if not path.is_absolute() or path.is_symlink():
        raise FullRunError(f"RUNTIME_BINDING_UNSAFE:{label}")
    try:
        metadata = path.stat(follow_symlinks=False)
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise FullRunError(f"RUNTIME_BINDING_UNAVAILABLE:{label}") from exc
    if (
        resolved != path
        or not stat.S_ISREG(metadata.st_mode)
        or (executable and metadata.st_mode & 0o111 == 0)
    ):
        raise FullRunError(f"RUNTIME_BINDING_UNSAFE:{label}")
    return {
        "path": str(path),
        "sha256": _sha256(path),
        "sizeBytes": metadata.st_size,
    }


def _required_environment_path(name: str) -> Path:
    value = os.environ.get(name)
    if not value:
        raise FullRunError(f"REQUIRED_ENVIRONMENT_MISSING:{name}")
    return Path(value)


def _capture_runtime_bindings() -> dict[str, dict[str, object]]:
    paths = {
        role: _required_environment_path(environment_name)
        for role, environment_name in RUNTIME_ENV_BY_ROLE.items()
    }
    java_home = _required_environment_path("JAVA_HOME")
    paths["java"] = java_home / "bin/java"
    bindings = {
        role: _snapshot_file(
            path,
            label=role,
            executable=role not in NON_EXECUTABLE_ROLES,
        )
        for role, path in paths.items()
    }
    expected = {
        "benchmarkPython": BENCHMARK_PYTHON_SHA256,
        "scalafmtArchive": SCALAFMT_ARCHIVE_SHA256,
        "vectorSourceArchive": VECTOR_ARCHIVE_SHA256,
    }
    docker_expected = os.environ.get("S1_4X_DOCKER_SHA256")
    if docker_expected:
        expected["docker"] = docker_expected
    for role, expected_sha256 in expected.items():
        if (
            SHA256.fullmatch(expected_sha256) is None
            or bindings[role]["sha256"] != expected_sha256
        ):
            raise FullRunError(f"RUNTIME_BINDING_SHA256_MISMATCH:{role}")
    return bindings


def _execution_path(
    bindings: Mapping[str, Mapping[str, object]],
) -> str:
    directories = [
        str(Path(str(bindings[role]["path"])).parent)
        for role in (
            "java",
            "authoritativeGhc",
            "stack",
            "uv",
            "scalaCli",
            "ghcup",
            "hlint",
            "stylishHaskell",
        )
    ]
    directories.extend(
        [
            "/usr/local/sbin",
            "/usr/local/bin",
            "/usr/sbin",
            "/usr/bin",
            "/sbin",
            "/bin",
            "/usr/lib/wsl/lib",
        ]
    )
    return ":".join(dict.fromkeys(directories))


def _ghcup_install_base_prefix(
    bindings: Mapping[str, Mapping[str, object]],
) -> Path:
    """GHC와 Stack의 frozen layout이 공유하는 GHCup 설치 prefix를 검증한다."""

    authoritative_ghc = Path(str(bindings["authoritativeGhc"]["path"]))
    stack = Path(str(bindings["stack"]["path"]))
    try:
        prefix = authoritative_ghc.parents[4]
    except IndexError as exc:
        raise FullRunError("GHCUP_INSTALL_BASE_PREFIX_INVALID") from exc
    if (
        not prefix.is_absolute()
        or not prefix.is_dir()
        or prefix.is_symlink()
        or prefix.resolve(strict=True) != prefix
        or authoritative_ghc != prefix / ".ghcup/ghc/9.10.3/bin/ghc-9.10.3"
        or stack != prefix / ".ghcup/stack/3.11.1/stack"
    ):
        raise FullRunError("GHCUP_INSTALL_BASE_PREFIX_INVALID")
    return prefix


def _verify_repository_state(
    repo: Path,
    home: Path,
    subject: str,
    *,
    require_remote_match: bool,
) -> str:
    if COMMIT.fullmatch(subject) is None:
        raise FullRunError("BENCHMARK_SUBJECT_INVALID")
    actual = _git(repo, home, "rev-parse", "--verify", "HEAD")
    if actual != subject:
        raise FullRunError("BENCHMARK_SUBJECT_DRIFT")
    branch = _git(repo, home, "symbolic-ref", "--short", "HEAD")
    if not branch.startswith("experiment/s1-4x-numeric-parity"):
        raise FullRunError("BENCHMARK_BRANCH_INVALID")
    if _git(
        repo,
        home,
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
    ):
        raise FullRunError("BENCHMARK_WORKTREE_DIRTY")
    if require_remote_match:
        remote = _git(
            repo,
            home,
            "rev-parse",
            "--verify",
            f"refs/remotes/origin/{branch}",
        )
        if remote != subject:
            raise FullRunError("BENCHMARK_REMOTE_SUBJECT_MISMATCH")
    return branch


def reserve_run_root(run_root: Path) -> RunPaths:
    """검증된 새 output root를 한 번만 예약한다."""

    if not run_root.is_absolute() or run_root.is_symlink():
        raise FullRunError("RUN_ROOT_PATH_UNSAFE")
    parent = _validate_absolute_directory(
        run_root.parent,
        label="RUN_PARENT",
    )
    if run_root.exists() or run_root.is_symlink():
        raise FullRunError("RUN_ROOT_ALREADY_EXISTS")
    os.mkdir(run_root, 0o700)
    paths = RunPaths.from_run_root(run_root)
    for directory in (
        paths.logs,
        paths.stages,
        paths.checkpoints,
        paths.candidate,
        paths.terminal,
        paths.control,
    ):
        os.mkdir(directory, 0o700)
    if run_root.parent != parent:
        raise FullRunError("RUN_PARENT_IDENTITY_DRIFT")
    return paths


def _continuation_source_paths(
    *,
    failed_run_root: Path | None,
    scala_qualification_source: Path | None,
    haskell_static_source: Path | None,
    haskell_profile_source: Path | None,
) -> tuple[Path, Path, Path, Path] | None:
    """재사용 source 네 개가 모두 명시됐을 때만 canonical directory로 수용한다."""

    values = (
        failed_run_root,
        scala_qualification_source,
        haskell_static_source,
        haskell_profile_source,
    )
    if not any(value is not None for value in values):
        return None
    if not all(value is not None for value in values):
        raise FullRunError("CONTINUATION_SOURCE_SET_INCOMPLETE")
    labels = (
        "FAILED_RUN_ROOT",
        "SCALA_QUALIFICATION_SOURCE",
        "HASKELL_STATIC_SOURCE",
        "HASKELL_PROFILE_SOURCE",
    )
    return tuple(
        _validate_absolute_directory(value, label=label)
        for label, value in zip(labels, values, strict=True)
        if value is not None
    )  # type: ignore[return-value]


def _validate_continuation_manifest(
    path: Path,
    *,
    target_subject: str,
    expected_parent_run_id: str | None = None,
    expected_source_roots: Sequence[Path] | None = None,
) -> dict[str, Any]:
    """helper가 봉인한 source manifest의 subject와 최소 typed closure를 검증한다."""

    manifest = _strict_json_load(path)
    expected_keys = {
        "schemaVersion",
        "parentRunId",
        "parentSubject",
        "targetSubject",
        "failedStage",
        "currentDiffPaths",
        "sourceCommits",
        "sourceTrees",
        "artifactCount",
        "artifacts",
        "status",
    }
    source_commits = manifest.get("sourceCommits") if isinstance(manifest, dict) else None
    source_trees = manifest.get("sourceTrees") if isinstance(manifest, dict) else None
    artifacts = manifest.get("artifacts") if isinstance(manifest, dict) else None
    if (
        not isinstance(manifest, dict)
        or set(manifest) != expected_keys
        or manifest.get("schemaVersion") != CONTINUATION_MANIFEST_SCHEMA
        or RUN_ID.fullmatch(str(manifest.get("parentRunId"))) is None
        or COMMIT.fullmatch(str(manifest.get("parentSubject"))) is None
        or manifest.get("targetSubject") != target_subject
        or manifest.get("failedStage") != "correctness-oci-regression"
        or not isinstance(manifest.get("currentDiffPaths"), list)
        or any(
            not isinstance(item, str) or not item
            for item in manifest.get("currentDiffPaths", [])
        )
        or not isinstance(source_commits, dict)
        or set(source_commits) != {"scalaQualification", "haskellProfile"}
        or any(COMMIT.fullmatch(str(value)) is None for value in source_commits.values())
        or not isinstance(source_trees, list)
        or not source_trees
        or not isinstance(artifacts, list)
        or not artifacts
        or isinstance(manifest.get("artifactCount"), bool)
        or not isinstance(manifest.get("artifactCount"), int)
        or manifest.get("artifactCount") != len(artifacts)
        or manifest.get("status") != "SEALED"
    ):
        raise FullRunError("CONTINUATION_SOURCE_MANIFEST_INVALID")
    if (
        expected_parent_run_id is not None
        and manifest["parentRunId"] != expected_parent_run_id
    ):
        raise FullRunError("CONTINUATION_PARENT_RUN_MISMATCH")
    source_roots: set[str] = set()
    for source_tree in source_trees:
        if (
            not isinstance(source_tree, dict)
            or not isinstance(source_tree.get("sourceId"), str)
            or not source_tree["sourceId"]
            or not isinstance(source_tree.get("sourceRoot"), str)
            or not source_tree["sourceRoot"]
            or SHA256.fullmatch(str(source_tree.get("treeSha256"))) is None
            or isinstance(source_tree.get("artifactCount"), bool)
            or not isinstance(source_tree.get("artifactCount"), int)
            or source_tree["artifactCount"] < 0
            or isinstance(source_tree.get("totalSizeBytes"), bool)
            or not isinstance(source_tree.get("totalSizeBytes"), int)
            or source_tree["totalSizeBytes"] < 0
        ):
            raise FullRunError("CONTINUATION_SOURCE_TREE_INVALID")
        source_roots.add(source_tree["sourceRoot"])
    for artifact in artifacts:
        if (
            not isinstance(artifact, dict)
            or not isinstance(artifact.get("sourceId"), str)
            or not artifact["sourceId"]
            or not isinstance(artifact.get("destinationPath"), str)
            or not artifact["destinationPath"]
            or SHA256.fullmatch(str(artifact.get("sha256"))) is None
            or isinstance(artifact.get("sizeBytes"), bool)
            or not isinstance(artifact.get("sizeBytes"), int)
            or artifact["sizeBytes"] < 0
        ):
            raise FullRunError("CONTINUATION_SOURCE_ARTIFACT_INVALID")
    if expected_source_roots is not None and source_roots != {
        str(path) for path in expected_source_roots
    }:
        raise FullRunError("CONTINUATION_SOURCE_ROOT_SET_MISMATCH")
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise FullRunError("CONTINUATION_SOURCE_MANIFEST_UNAVAILABLE") from exc
    if raw != _canonical_bytes(manifest):
        raise FullRunError("CONTINUATION_SOURCE_MANIFEST_NOT_CANONICAL")
    return manifest


def _snapshot_continuation_manifest(
    *,
    repo: Path,
    home: Path,
    paths: RunPaths,
    subject: str,
    sources: tuple[Path, Path, Path, Path],
) -> tuple[dict[str, Any], dict[str, object]]:
    """tracked helper로 source tree를 새 run control plane에 한 번만 봉인한다."""

    helper = (
        repo
        / "workspaces/decision-platform/research/s1-4x-numeric-parity"
        / "integration/continuation_prefix.py"
    )
    _snapshot_file(
        helper,
        label="continuationPrefixHelper",
        executable=False,
    )
    output = paths.control / CONTINUATION_MANIFEST_NAME
    failed_run, scala_qualification, haskell_static, haskell_profile = sources
    command = [
        "/usr/bin/python3",
        str(helper),
        "snapshot",
        "--repo-root",
        str(repo),
        "--failed-run-root",
        str(failed_run),
        "--scala-qualification-source",
        str(scala_qualification),
        "--haskell-static-source",
        str(haskell_static),
        "--haskell-profile-source",
        str(haskell_profile),
        "--target-subject",
        subject,
        "--output",
        str(output),
    ]
    completed = subprocess.run(
        command,
        cwd=repo,
        env={**_git_environment(home), "PYTHONUNBUFFERED": "1"},
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=False,
        timeout=300,
    )
    if completed.returncode != 0:
        raise FullRunError(
            f"CONTINUATION_PREFIX_SNAPSHOT_FAILED:{completed.returncode}"
        )
    if completed.stderr:
        raise FullRunError("CONTINUATION_PREFIX_SNAPSHOT_STDERR_NOT_EMPTY")
    snapshot = _snapshot_file(
        output,
        label="continuationSourceManifest",
        executable=False,
    )
    if completed.stdout != output.read_bytes():
        raise FullRunError("CONTINUATION_PREFIX_SNAPSHOT_RECEIPT_MISMATCH")
    manifest = _validate_continuation_manifest(
        output,
        target_subject=subject,
        expected_parent_run_id=failed_run.name,
        expected_source_roots=sources,
    )
    return manifest, snapshot


def prepare_run(
    *,
    repo_root: Path,
    run_root: Path,
    run_id: str,
    subject: str,
    overall_timeout_seconds: int,
    failed_run_root: Path | None = None,
    scala_qualification_source: Path | None = None,
    haskell_static_source: Path | None = None,
    haskell_profile_source: Path | None = None,
) -> dict[str, object]:
    """clean remote-backed HEAD와 runtime identity를 local run plan에 봉인한다."""

    if RUN_ID.fullmatch(run_id) is None:
        raise FullRunError("RUN_ID_INVALID")
    if not 36_000 <= overall_timeout_seconds <= 61_200:
        raise FullRunError("OVERALL_TIMEOUT_INVALID")
    continuation_sources = _continuation_source_paths(
        failed_run_root=failed_run_root,
        scala_qualification_source=scala_qualification_source,
        haskell_static_source=haskell_static_source,
        haskell_profile_source=haskell_profile_source,
    )
    repo = _validate_absolute_directory(repo_root, label="REPOSITORY")
    home = _validate_absolute_directory(Path.home(), label="HOME")
    if run_root == repo or run_root.is_relative_to(repo):
        raise FullRunError("RUN_ROOT_MUST_BE_OUTSIDE_REPOSITORY")
    branch = _verify_repository_state(
        repo,
        home,
        subject,
        require_remote_match=True,
    )
    bindings = _capture_runtime_bindings()
    cache_root = home / ".cache/s1-4x"
    _validate_absolute_directory(cache_root, label="CACHE_ROOT")
    paths = reserve_run_root(run_root)
    source_supervisor = (
        repo
        / "workspaces/decision-platform/research/s1-4x-numeric-parity"
        / "integration/detached_full_run.py"
    )
    source_snapshot = _snapshot_file(
        source_supervisor,
        label="detachedSupervisor",
        executable=False,
    )
    if Path(__file__).resolve(strict=True) != source_supervisor:
        raise FullRunError("DETACHED_SUPERVISOR_SOURCE_IDENTITY_INVALID")
    control_supervisor = paths.control / "detached_full_run.py"
    _exclusive_bytes(
        control_supervisor,
        source_supervisor.read_bytes(),
    )
    control_snapshot = _snapshot_file(
        control_supervisor,
        label="controlSupervisor",
        executable=False,
    )
    if (
        control_snapshot["sha256"] != source_snapshot["sha256"]
        or control_snapshot["sizeBytes"] != source_snapshot["sizeBytes"]
    ):
        raise FullRunError("CONTROL_SUPERVISOR_COPY_INVALID")
    evidence_reuse: dict[str, object] | None = None
    if continuation_sources is not None:
        manifest, manifest_snapshot = _snapshot_continuation_manifest(
            repo=repo,
            home=home,
            paths=paths,
            subject=subject,
            sources=continuation_sources,
        )
        evidence_reuse = {
            "mode": REUSE_MODE,
            "parentRunId": manifest["parentRunId"],
            "parentSubject": manifest["parentSubject"],
            "targetSubject": subject,
            "sourceCommits": manifest["sourceCommits"],
            "controlManifest": {
                "relativePath": f"control/{CONTINUATION_MANIFEST_NAME}",
                "sha256": manifest_snapshot["sha256"],
                "sizeBytes": manifest_snapshot["sizeBytes"],
            },
        }
    unit_name = f"s1-4x-full-{run_id}.service"
    plan: dict[str, object] = {
        "schemaVersion": PLAN_SCHEMA,
        "preparedAt": _utc_now(),
        "runId": run_id,
        "unitName": unit_name,
        "repositoryRoot": str(repo),
        "runRoot": str(run_root),
        "benchmarkSubjectCommit": subject,
        "branch": branch,
        "overallTimeoutSeconds": overall_timeout_seconds,
        "home": str(home),
        "cacheRoot": str(cache_root),
        "lockPath": str(cache_root / "detached-full-run.lock"),
        "executionPath": _execution_path(bindings),
        "ghcupInstallBasePrefix": str(_ghcup_install_base_prefix(bindings)),
        "controlSupervisor": {
            "relativePath": "control/detached_full_run.py",
            "sha256": control_snapshot["sha256"],
            "sizeBytes": control_snapshot["sizeBytes"],
        },
        "runtimeBindings": bindings,
        "scalaBaseImage": SCALA_BASE_IMAGE,
        "haskellBaseImage": HASKELL_BASE_IMAGE,
        "stageOrder": list(STAGE_ORDER),
        "retryPolicy": (
            REUSE_RETRY_POLICY if evidence_reuse is not None else FRESH_RETRY_POLICY
        ),
    }
    if evidence_reuse is not None:
        plan["evidenceReuse"] = evidence_reuse
    payload = _canonical_bytes(plan)
    digest = hashlib.sha256(payload).hexdigest()
    _exclusive_bytes(paths.plan, payload)
    _exclusive_bytes(
        paths.plan_sidecar,
        f"{digest}  {paths.plan.name}\n".encode("ascii"),
    )
    result: dict[str, object] = {
        "schemaVersion": "s1.4x-detached-full-run-preparation-v1",
        "runId": run_id,
        "unitName": unit_name,
        "runRoot": str(run_root),
        "benchmarkSubjectCommit": subject,
        "planSha256": digest,
        "status": "DETACHED_RUNNER_PREPARED",
    }
    if evidence_reuse is not None:
        result["evidenceReuseMode"] = REUSE_MODE
        result["parentRunId"] = evidence_reuse["parentRunId"]
    return result


def _validate_evidence_reuse_plan(
    plan: Mapping[str, Any],
) -> dict[str, Any] | None:
    """plan의 fresh/reuse 실행 정책과 manifest binding을 exact shape로 검증한다."""

    if "evidenceReuse" not in plan:
        if plan.get("retryPolicy") != FRESH_RETRY_POLICY:
            raise FullRunError("RUN_PLAN_EXECUTION_POLICY_INVALID")
        return None
    value = plan.get("evidenceReuse")
    expected = {
        "mode",
        "parentRunId",
        "parentSubject",
        "targetSubject",
        "sourceCommits",
        "controlManifest",
    }
    source_commits = value.get("sourceCommits") if isinstance(value, dict) else None
    control_manifest = value.get("controlManifest") if isinstance(value, dict) else None
    if (
        not isinstance(value, dict)
        or set(value) != expected
        or value.get("mode") != REUSE_MODE
        or RUN_ID.fullmatch(str(value.get("parentRunId"))) is None
        or COMMIT.fullmatch(str(value.get("parentSubject"))) is None
        or value.get("targetSubject") != plan.get("benchmarkSubjectCommit")
        or not isinstance(source_commits, dict)
        or set(source_commits) != {"scalaQualification", "haskellProfile"}
        or any(COMMIT.fullmatch(str(commit)) is None for commit in source_commits.values())
        or not isinstance(control_manifest, dict)
        or set(control_manifest) != {"relativePath", "sha256", "sizeBytes"}
        or control_manifest.get("relativePath")
        != f"control/{CONTINUATION_MANIFEST_NAME}"
        or SHA256.fullmatch(str(control_manifest.get("sha256"))) is None
        or isinstance(control_manifest.get("sizeBytes"), bool)
        or not isinstance(control_manifest.get("sizeBytes"), int)
        or control_manifest["sizeBytes"] <= 0
        or plan.get("retryPolicy") != REUSE_RETRY_POLICY
    ):
        raise FullRunError("RUN_PLAN_EVIDENCE_REUSE_INVALID")
    return value


def _load_run_plan(path: Path, *, strict: bool) -> dict[str, Any]:
    value = _strict_json_load(path)
    if (
        not isinstance(value, dict)
        or value.get("schemaVersion") != PLAN_SCHEMA
        or COMMIT.fullmatch(str(value.get("benchmarkSubjectCommit"))) is None
        or not isinstance(value.get("runRoot"), str)
        or not Path(value["runRoot"]).is_absolute()
    ):
        raise FullRunError("RUN_PLAN_INVALID")
    if strict:
        expected = {
            "schemaVersion",
            "preparedAt",
            "runId",
            "unitName",
            "repositoryRoot",
            "runRoot",
            "benchmarkSubjectCommit",
            "branch",
            "overallTimeoutSeconds",
            "home",
            "cacheRoot",
            "lockPath",
            "executionPath",
            "ghcupInstallBasePrefix",
            "controlSupervisor",
            "runtimeBindings",
            "scalaBaseImage",
            "haskellBaseImage",
            "stageOrder",
            "retryPolicy",
        }
        if "evidenceReuse" in value:
            expected.add("evidenceReuse")
        if set(value) != expected:
            raise FullRunError("RUN_PLAN_FIELDS_MISSING")
        if (
            RUN_ID.fullmatch(str(value["runId"])) is None
            or value["unitName"] != f"s1-4x-full-{value['runId']}.service"
            or not isinstance(value["overallTimeoutSeconds"], int)
            or isinstance(value["overallTimeoutSeconds"], bool)
            or not 36_000 <= value["overallTimeoutSeconds"] <= 61_200
            or not isinstance(value["branch"], str)
            or not value["branch"].startswith("experiment/s1-4x-numeric-parity")
            or value["scalaBaseImage"] != SCALA_BASE_IMAGE
            or value["haskellBaseImage"] != HASKELL_BASE_IMAGE
            or value["stageOrder"] != list(STAGE_ORDER)
        ):
            raise FullRunError("RUN_PLAN_EXECUTION_POLICY_INVALID")
        _validate_evidence_reuse_plan(value)
    return value


def _validate_control_supervisor(
    paths: RunPaths,
    plan: Mapping[str, Any],
) -> None:
    value = plan.get("controlSupervisor")
    if (
        not isinstance(value, dict)
        or set(value) != {"relativePath", "sha256", "sizeBytes"}
        or value.get("relativePath") != "control/detached_full_run.py"
        or SHA256.fullmatch(str(value.get("sha256"))) is None
        or isinstance(value.get("sizeBytes"), bool)
        or not isinstance(value.get("sizeBytes"), int)
        or value["sizeBytes"] <= 0
    ):
        raise FullRunError("CONTROL_SUPERVISOR_PLAN_INVALID")
    current = _snapshot_file(
        paths.control / "detached_full_run.py",
        label="controlSupervisor",
        executable=False,
    )
    if (
        current["sha256"] != value["sha256"]
        or current["sizeBytes"] != value["sizeBytes"]
    ):
        raise FullRunError("CONTROL_SUPERVISOR_DRIFT")


def _validate_evidence_reuse(
    paths: RunPaths,
    plan: Mapping[str, Any],
) -> Path | None:
    """plan에 봉인된 continuation manifest가 실행 직전에도 동일한지 재검증한다."""

    value = _validate_evidence_reuse_plan(plan)
    manifest_path = paths.control / CONTINUATION_MANIFEST_NAME
    if value is None:
        if manifest_path.exists() or manifest_path.is_symlink():
            raise FullRunError("UNDECLARED_CONTINUATION_MANIFEST")
        return None
    control = value["controlManifest"]
    current = _snapshot_file(
        manifest_path,
        label="continuationSourceManifest",
        executable=False,
    )
    if (
        current["sha256"] != control["sha256"]
        or current["sizeBytes"] != control["sizeBytes"]
    ):
        raise FullRunError("CONTINUATION_CONTROL_MANIFEST_DRIFT")
    manifest = _validate_continuation_manifest(
        manifest_path,
        target_subject=str(plan["benchmarkSubjectCommit"]),
        expected_parent_run_id=str(value["parentRunId"]),
    )
    if (
        manifest["parentSubject"] != value["parentSubject"]
        or manifest["targetSubject"] != value["targetSubject"]
        or manifest["sourceCommits"] != value["sourceCommits"]
    ):
        raise FullRunError("CONTINUATION_CONTROL_MANIFEST_PLAN_MISMATCH")
    return manifest_path


def _validate_plan_sidecar(paths: RunPaths) -> None:
    try:
        sidecar = paths.plan_sidecar.read_text(encoding="ascii").strip().split()
    except (OSError, UnicodeError) as exc:
        raise FullRunError("RUN_PLAN_SIDECAR_INVALID") from exc
    if (
        len(sidecar) != 2
        or SHA256.fullmatch(sidecar[0]) is None
        or sidecar[1] != paths.plan.name
        or sidecar[0] != _sha256(paths.plan)
    ):
        raise FullRunError("RUN_PLAN_SIDECAR_INVALID")


def _binding_path(
    plan: Mapping[str, Any],
    role: str,
) -> str:
    bindings = plan.get("runtimeBindings")
    if not isinstance(bindings, dict):
        raise FullRunError("RUN_PLAN_RUNTIME_BINDINGS_INVALID")
    binding = bindings.get(role)
    if not isinstance(binding, dict) or not isinstance(binding.get("path"), str):
        raise FullRunError(f"RUN_PLAN_RUNTIME_BINDING_INVALID:{role}")
    path = binding["path"]
    if not isinstance(path, str):
        raise FullRunError(f"RUN_PLAN_RUNTIME_BINDING_INVALID:{role}")
    return path


def _validate_runtime_bindings(plan: Mapping[str, Any]) -> None:
    bindings = plan.get("runtimeBindings")
    if not isinstance(bindings, dict):
        raise FullRunError("RUN_PLAN_RUNTIME_BINDINGS_INVALID")
    expected_roles = set(RUNTIME_ENV_BY_ROLE) | {"java"}
    if set(bindings) != expected_roles:
        raise FullRunError("RUN_PLAN_RUNTIME_ROLE_SET_INVALID")
    for role, value in bindings.items():
        if (
            not isinstance(value, dict)
            or not isinstance(value.get("path"), str)
            or SHA256.fullmatch(str(value.get("sha256"))) is None
        ):
            raise FullRunError(f"RUN_PLAN_RUNTIME_BINDING_INVALID:{role}")
        current = _snapshot_file(
            Path(value["path"]),
            label=role,
            executable=role not in NON_EXECUTABLE_ROLES,
        )
        if current["sha256"] != value["sha256"] or current["sizeBytes"] != value.get(
            "sizeBytes"
        ):
            raise FullRunError(f"RUNTIME_BINDING_DRIFT:{role}")


def _execution_environment(plan: Mapping[str, Any]) -> dict[str, str]:
    home = str(plan["home"])
    cache = Path(str(plan["cacheRoot"]))
    correctness = Path(str(plan["runRoot"])) / "correctness"
    environment = {
        "HOME": home,
        "PATH": str(plan["executionPath"]),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "TZ": "UTC",
        "PYTHONUNBUFFERED": "1",
        "JAVA_HOME": str(Path(_binding_path(plan, "java")).parent.parent),
        "GHCUP_INSTALL_BASE_PREFIX": str(plan["ghcupInstallBasePrefix"]),
        "TMPDIR": str(cache / "tmp"),
        "TEMP": str(cache / "tmp"),
        "TMP": str(cache / "tmp"),
        "UV_CACHE_DIR": str(cache / "uv"),
        "COURSIER_CACHE": str(cache / "coursier"),
        "UV_PYTHON": "3.12.13",
        "JAX_PLATFORMS": "cpu",
        "JAX_ENABLE_X64": "1",
        "OMP_NUM_THREADS": "1",
        "OPENBLAS_NUM_THREADS": "1",
        "MKL_NUM_THREADS": "1",
        "NUMEXPR_NUM_THREADS": "1",
        "VECLIB_MAXIMUM_THREADS": "1",
        "JAX_NUM_THREADS": "1",
        "XLA_FLAGS": (
            "--xla_cpu_multi_thread_eigen=false intra_op_parallelism_threads=1"
        ),
        "S1_4X_CACHE_ROOT": str(cache),
        "S1_4X_UV_BIN": _binding_path(plan, "uv"),
        "S1_4X_SCALA_CLI_BIN": _binding_path(plan, "scalaCli"),
        "S1_4X_SCALAFIX_BIN": _binding_path(plan, "scalafix"),
        "S1_4X_SCALAFMT_ARCHIVE": _binding_path(plan, "scalafmtArchive"),
        "S1_4X_SCALAFMT_BIN": _binding_path(plan, "scalafmt"),
        "S1_4X_GHCUP_BIN": _binding_path(plan, "ghcup"),
        "S1_4X_AUTHORITATIVE_GHC_BIN": _binding_path(plan, "authoritativeGhc"),
        "S1_4X_LATEST_GHC_BIN": _binding_path(plan, "compatibilityGhc"),
        "S1_4X_STACK_BIN": _binding_path(plan, "stack"),
        "S1_4X_HLINT_BIN": _binding_path(plan, "hlint"),
        "S1_4X_STYLISH_BIN": _binding_path(plan, "stylishHaskell"),
        "S1_4X_BENCHMARK_PYTHON_BIN": _binding_path(plan, "benchmarkPython"),
        "S1_4X_BENCHMARK_PYTHON_SHA256": str(
            plan["runtimeBindings"]["benchmarkPython"]["sha256"]
        ),
        "S1_4X_DOCKER_BIN": _binding_path(plan, "docker"),
        "S1_4X_DOCKER_SHA256": str(plan["runtimeBindings"]["docker"]["sha256"]),
        "S1_4X_SCALA_BASE_IMAGE_REF": str(plan["scalaBaseImage"]),
        "S1_4X_VECTOR_SOURCE_ARCHIVE": _binding_path(plan, "vectorSourceArchive"),
        "S1_4X_LARGE_FIXTURE_ROOT": str(correctness / "large-fixtures"),
    }
    for directory in (
        cache / "tmp",
        cache / "uv",
        cache / "coursier",
    ):
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if _validate_evidence_reuse_plan(plan) is not None:
        # 사용자가 이번 sealed continuation에 한해 ambient container 수와
        # 외부 Codex CPU 비율을 timing eligibility에서 제외하도록 승인했다.
        environment["S1_4X_IGNORE_AMBIENT_HOST_ACTIVITY"] = "1"
    return environment


def build_stage_commands(
    plan: Mapping[str, Any],
) -> list[StageCommand]:
    """GitHub full workflow와 같은 v3 runtime/evidence argv를 직렬화한다."""

    repo = Path(str(plan["repositoryRoot"]))
    run_root = Path(str(plan["runRoot"]))
    run_id = str(plan["runId"])
    subject = str(plan["benchmarkSubjectCommit"])
    s1_4x = repo / "workspaces/decision-platform/research/s1-4x-numeric-parity"
    integration = s1_4x / "integration"
    oracle = s1_4x / "oracle"
    plan_path = s1_4x / "benchmarks/benchmark-plan.v1.json"
    correctness = run_root / "correctness"
    audit = run_root / "correctness-final-audit"
    benchmark = run_root / "benchmark"
    commands = benchmark / "commands.json"
    sidecar = benchmark / "commands.sha256"
    large_root = correctness / "large-fixtures"
    large_receipt = correctness / "large-fixture-receipt.json"
    uv = _binding_path(plan, "uv")
    sealing: list[str] = [
        uv,
        "run",
        "--frozen",
        "--no-config",
        "--project",
        str(oracle),
        "python",
        str(integration / "prepare_benchmark_commands.py"),
        "--repo-root",
        str(repo),
        "--benchmark-subject-commit",
        subject,
        "--host-wrapper",
        str(integration / "tools/run-host-validator.sh"),
        "--python-wrapper",
        str(integration / "tools/run-python-benchmark-block.sh"),
        "--scala-wrapper",
        str(s1_4x / "scala/tools/run-benchmark-block.sh"),
        "--haskell-wrapper",
        str(s1_4x / "haskell/tools/run-benchmark-block.sh"),
    ]
    for role in (
        "uv",
        "docker",
        "benchmarkPython",
        "scalaCli",
        "java",
        "scalafix",
        "scalafmt",
        "ghcup",
        "stack",
        "authoritativeGhc",
        "compatibilityGhc",
        "hlint",
        "stylishHaskell",
    ):
        sealing.extend(["--runtime-executable", f"{role}={_binding_path(plan, role)}"])
    evidence = {
        "scalafmtArchive": _binding_path(plan, "scalafmtArchive"),
        "selectedProfileResult": str(
            correctness / "scala/scala-selected-profile-result.v1.json"
        ),
        "profileQualificationResult": str(
            correctness / "scala/qualification/scala-profile-qualification.v1.json"
        ),
        "jvmAllowlistResult": str(
            correctness / "scala/jmh-smoke/scala-jvm-argument-allowlist.v1.json"
        ),
        "correctnessA": str(
            correctness / "scala/profiles/A/scala-profile-correctness-result.v1.json"
        ),
        "correctnessB": str(
            correctness / "scala/profiles/B/scala-profile-correctness-result.v1.json"
        ),
        "correctnessC": str(
            correctness / "scala/profiles/C/scala-profile-correctness-result.v1.json"
        ),
        "baselineCorrectness": str(
            correctness
            / "haskell/profiles/baseline-o0-fasm/correctness-receipt.v1.json"
        ),
        "optimizedCorrectness": str(
            correctness
            / "haskell/profiles/optimized-o2-fasm/correctness-receipt.v1.json"
        ),
        "profileQualification": str(
            correctness / "haskell/qualification/qualification-artifact.v1.json"
        ),
    }
    for role, path in evidence.items():
        sealing.extend(["--runtime-evidence", f"{role}={path}"])
    sealing.extend(["--output", str(commands), "--sidecar", str(sidecar)])
    correctness_argv = [
        str(integration / "tools/run-native-oci-regression-gates.sh"),
        str(correctness),
    ]
    evidence_reuse = _validate_evidence_reuse_plan(plan)
    if evidence_reuse is not None:
        correctness_argv.extend(
            [
                "--sealed-continuation-manifest",
                str(
                    run_root
                    / str(evidence_reuse["controlManifest"]["relativePath"])
                ),
            ]
        )
    return [
        StageCommand(
            "correctness-oci-regression",
            tuple(correctness_argv),
        ),
        StageCommand("command-sealing", tuple(sealing)),
        StageCommand(
            "frozen-timing",
            (
                uv,
                "run",
                "--frozen",
                "--no-config",
                "--project",
                str(oracle),
                "python",
                str(integration / "run_rotated_blocks.py"),
                "run",
                "--plan",
                str(plan_path),
                "--commands",
                str(commands),
                "--commands-sha256",
                "{COMMAND_MANIFEST_SHA256}",
                "--benchmark-subject-commit",
                subject,
                "--candidate-source-commit",
                subject,
                "--output-root",
                str(benchmark / "run"),
                "--run-id",
                run_id,
                "--repo-root",
                str(repo),
                "--large-fixture-root",
                str(large_root),
                "--large-fixture-receipt",
                str(large_receipt),
            ),
        ),
        StageCommand(
            "typed-finalization",
            (
                uv,
                "run",
                "--frozen",
                "--no-config",
                "--project",
                str(oracle),
                "python",
                str(integration / "finalize_benchmark_run.py"),
                "--plan",
                str(plan_path),
                "--run-directory",
                str(benchmark / "run" / run_id),
                "--output-directory",
                str(run_root / "final-reports"),
                "--benchmark-subject-commit",
                subject,
                "--audit-ledger",
                str(audit / "final-candidate-audit.json"),
                "--large-fixture-root",
                str(large_root),
            ),
        ),
    ]


def _materialize_command(
    command: StageCommand,
    paths: RunPaths,
) -> StageCommand:
    if "{COMMAND_MANIFEST_SHA256}" not in command.argv:
        return command
    sidecar = paths.benchmark / "commands.sha256"
    try:
        fields = sidecar.read_text(encoding="ascii").strip().split()
    except (OSError, UnicodeError) as exc:
        raise FullRunError("COMMAND_MANIFEST_SIDECAR_INVALID") from exc
    commands = paths.benchmark / "commands.json"
    if (
        len(fields) != 2
        or SHA256.fullmatch(fields[0]) is None
        or fields[1] != commands.name
        or fields[0] != _sha256(commands)
    ):
        raise FullRunError("COMMAND_MANIFEST_SIDECAR_INVALID")
    return StageCommand(
        command.name,
        tuple(
            fields[0] if item == "{COMMAND_MANIFEST_SHA256}" else item
            for item in command.argv
        ),
    )


def _terminate_process(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=20)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    process.wait(timeout=20)


def _signal_handler(signum: int, _frame: object) -> None:
    global _INTERRUPTED_SIGNAL
    _INTERRUPTED_SIGNAL = signum
    process = _ACTIVE_PROCESS
    if process is not None and process.poll() is None:
        # signal handler 안에서 Popen.wait를 재진입하지 않는다. Child group에는
        # TERM만 전달하고 정상 wait 흐름 또는 systemd cgroup stop이 회수한다.
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGTERM)
        return
    raise StageFailure("supervisor", 128 + signum, "INTERRUPTED")


def _raise_if_interrupted(stage: str) -> None:
    if _INTERRUPTED_SIGNAL is not None:
        raise StageFailure(
            stage,
            128 + _INTERRUPTED_SIGNAL,
            "INTERRUPTED",
        )


def run_stage(
    command: StageCommand,
    paths: RunPaths,
    environment: dict[str, str],
    deadline: float,
) -> StageReceipt:
    """한 stage를 새 process group에서 한 번만 실행하고 log identity를 남긴다."""

    global _ACTIVE_PROCESS
    stage_directory = paths.stages / command.name
    stage_directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    stdout_path = stage_directory / "stdout.log"
    stderr_path = stage_directory / "stderr.log"
    started_at = _utc_now()
    started_monotonic = time.monotonic()
    _append_event(
        paths.events,
        {
            "event": "STAGE_STARTED",
            "stage": command.name,
            "argv": list(command.argv),
        },
    )
    exit_code = 127
    timed_out = False
    with stdout_path.open("xb") as stdout, stderr_path.open("xb") as stderr:
        try:
            process = subprocess.Popen(
                list(command.argv),
                cwd=str(_strict_json_load(paths.plan)["repositoryRoot"]),
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                start_new_session=True,
            )
            _ACTIVE_PROCESS = process
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                _terminate_process(process)
                exit_code = 124
            else:
                try:
                    exit_code = process.wait(timeout=remaining)
                except subprocess.TimeoutExpired:
                    timed_out = True
                    _terminate_process(process)
                    exit_code = 124
            if _INTERRUPTED_SIGNAL is not None and exit_code == 0:
                exit_code = 128 + _INTERRUPTED_SIGNAL
        except OSError as exc:
            stderr.write(
                f"supervisor process start failed: {type(exc).__name__}\n".encode()
            )
            exit_code = 127
        finally:
            _ACTIVE_PROCESS = None
    completed_at = _utc_now()
    receipt = StageReceipt(
        stage=command.name,
        argv=command.argv,
        startedAt=started_at,
        completedAt=completed_at,
        durationSeconds=round(time.monotonic() - started_monotonic, 6),
        exitCode=exit_code,
        stdoutRelativePath=str(stdout_path.relative_to(paths.root)),
        stderrRelativePath=str(stderr_path.relative_to(paths.root)),
        stdoutSha256=_sha256(stdout_path),
        stderrSha256=_sha256(stderr_path),
        status="PASS" if exit_code == 0 and not timed_out else "FAIL",
    )
    _exclusive_json(stage_directory / "receipt.json", asdict(receipt))
    _append_event(
        paths.events,
        {
            "event": "STAGE_COMPLETED",
            "stage": command.name,
            "exitCode": exit_code,
            "status": receipt.status,
        },
    )
    if timed_out:
        raise StageFailure(command.name, 124, "OVERALL_TIMEOUT")
    if exit_code != 0:
        failure_code = (
            "INTERRUPTED" if _INTERRUPTED_SIGNAL is not None else "STAGE_COMMAND_FAILED"
        )
        raise StageFailure(command.name, exit_code, failure_code)
    return receipt


def _noop_output_validator(
    _command: StageCommand,
    _paths: RunPaths,
) -> None:
    return


def execute_stage_sequence(
    commands: Sequence[StageCommand],
    *,
    paths: RunPaths,
    environment: dict[str, str],
    deadline: float,
    stage_runner: StageRunner = run_stage,
    output_validator: OutputValidator = _noop_output_validator,
) -> list[StageReceipt]:
    """첫 실패에서 멈추며 retry나 partial resume 없이 checkpoint만 누적한다."""

    receipts: list[StageReceipt] = []
    for raw_command in commands:
        _raise_if_interrupted(raw_command.name)
        command = _materialize_command(raw_command, paths)
        receipt = stage_runner(command, paths, environment, deadline)
        _raise_if_interrupted(command.name)
        output_validator(command, paths)
        _raise_if_interrupted(command.name)
        checkpoint = {
            "schemaVersion": "s1.4x-detached-full-run-checkpoint-v1",
            "stage": command.name,
            "stageReceiptSha256": (
                _sha256(paths.stages / command.name / "receipt.json")
                if (paths.stages / command.name / "receipt.json").is_file()
                else receipt.stdoutSha256
            ),
            "completedAt": receipt.completedAt,
            "status": "PASS",
        }
        _exclusive_json(
            paths.checkpoints / f"{len(receipts) + 1:02d}-{command.name}.json",
            checkpoint,
        )
        receipts.append(receipt)
    return receipts


def _validate_stage_output(command: StageCommand, paths: RunPaths) -> None:
    required: tuple[Path, ...]
    if command.name == "correctness-oci-regression":
        required = (
            paths.correctness / "correctness-run-manifest.v1.json",
            paths.correctness / "large-fixture-receipt.json",
            paths.correctness_audit / "final-candidate-audit.json",
        )
    elif command.name == "command-sealing":
        required = (
            paths.benchmark / "commands.json",
            paths.benchmark / "commands.sha256",
        )
    elif command.name == "frozen-timing":
        plan = _load_run_plan(paths.plan, strict=False)
        required = (paths.benchmark / "run" / str(plan["runId"]),)
    elif command.name == "typed-finalization":
        plan = _load_run_plan(paths.plan, strict=False)
        _validate_finalizer_receipt(
            paths,
            str(plan["benchmarkSubjectCommit"]),
        )
        validate_final_reports(
            paths.final_reports,
            str(plan["benchmarkSubjectCommit"]),
        )
        return
    else:
        raise FullRunError(f"UNKNOWN_STAGE:{command.name}")
    if any(not path.exists() or path.is_symlink() for path in required):
        raise StageFailure(command.name, 2, "STAGE_OUTPUT_INCOMPLETE")


def _validate_finalizer_receipt(
    paths: RunPaths,
    subject: str,
) -> dict[str, Any]:
    """Finalizer stdout의 문서 hash/size 봉인을 실제 portable report와 대조한다."""

    receipt = _strict_json_load(paths.stages / "typed-finalization/stdout.log")
    documents = receipt.get("documents") if isinstance(receipt, dict) else None
    if (
        not isinstance(receipt, dict)
        or receipt.get("schemaVersion") != "s1.4x-benchmark-finalization-v1"
        or receipt.get("benchmarkSubjectCommit") != subject
        or receipt.get("completedBlockCount") != 87
        or receipt.get("partialBlockCount") != 0
        or receipt.get("notMeasuredCount") != 0
        or receipt.get("status") not in ALLOWED_SUMMARY_STATUS
        or not isinstance(documents, dict)
        or set(documents) != set(FINAL_REPORT_NAMES)
    ):
        raise FullRunError("FINALIZER_RECEIPT_INVALID")
    for name in FINAL_REPORT_NAMES:
        identity = documents[name]
        path = paths.final_reports / name
        if (
            not isinstance(identity, dict)
            or set(identity) != {"sha256", "sizeBytes"}
            or SHA256.fullmatch(str(identity.get("sha256"))) is None
            or isinstance(identity.get("sizeBytes"), bool)
            or not isinstance(identity.get("sizeBytes"), int)
            or identity["sizeBytes"] <= 0
            or not path.is_file()
            or path.is_symlink()
            or _sha256(path) != identity["sha256"]
            or path.stat().st_size != identity["sizeBytes"]
        ):
            raise FullRunError("FINALIZER_DOCUMENT_IDENTITY_INVALID")
    return receipt


def validate_final_reports(
    directory: Path,
    subject: str,
) -> dict[str, Any]:
    """portable 네 문서의 full completeness와 candidate eligibility를 재검증한다."""

    documents = {
        name: _strict_json_load(directory / name) for name in FINAL_REPORT_NAMES
    }
    summary = documents["benchmark-summary.v1.json"]
    if (
        not isinstance(summary, dict)
        or summary.get("schemaVersion") != "s1.4x-full-benchmark-summary-v1"
        or summary.get("benchmarkSubjectCommit") != subject
        or summary.get("scheduledBlockCount") != 87
        or summary.get("completedBlockCount") != 87
        or summary.get("partialBlockCount") != 0
        or summary.get("notMeasuredCount") != 0
        or summary.get("familyCount") != 6
        or summary.get("candidateCaseCountPerRepetition") != 89
        or summary.get("outerRepetitions") != 3
        or summary.get("status") not in ALLOWED_SUMMARY_STATUS
        or not isinstance(summary.get("boundarySummaries"), dict)
        or set(summary["boundarySummaries"]) != BOUNDARY_IDS
    ):
        raise FullRunError("FINAL_REPORT_COMPLETENESS_INVALID")
    for value in summary["boundarySummaries"].values():
        if (
            not isinstance(value, dict)
            or isinstance(value.get("caseCount"), bool)
            or not isinstance(value.get("caseCount"), int)
            or value["caseCount"] <= 0
            or not isinstance(value.get("cases"), dict)
        ):
            raise FullRunError("FINAL_REPORT_COMPLETENESS_INVALID")
    host = documents["benchmark-host-ledger.v1.json"]
    if (
        not isinstance(host, dict)
        or host.get("schemaVersion") != "s1.4x-full-benchmark-host-ledger-v1"
        or host.get("benchmarkSubjectCommit") != subject
        or host.get("blockCount") != 87
        or not isinstance(host.get("blocks"), list)
        or len(host["blocks"]) != 87
        or host.get("status") != "PASS"
    ):
        raise FullRunError("FINAL_HOST_LEDGER_INVALID")
    raw = documents["benchmark-raw-hash-manifest.v1.json"]
    if (
        not isinstance(raw, dict)
        or raw.get("schemaVersion") != "s1.4x-full-benchmark-raw-hash-manifest-v1"
        or raw.get("benchmarkSubjectCommit") != subject
        or raw.get("status") != "PASS"
        or not isinstance(raw.get("artifacts"), list)
        or raw.get("artifactCount") != len(raw["artifacts"])
        or raw.get("artifactCount", 0) <= 0
    ):
        raise FullRunError("FINAL_RAW_HASH_MANIFEST_INVALID")
    for artifact in raw["artifacts"]:
        if (
            not isinstance(artifact, dict)
            or set(artifact) != {"path", "sha256", "sizeBytes"}
            or not isinstance(artifact.get("path"), str)
            or not artifact["path"]
            or artifact["path"].startswith("/")
            or "\\" in artifact["path"]
            or any(part in {"", ".", ".."} for part in artifact["path"].split("/"))
            or SHA256.fullmatch(str(artifact.get("sha256"))) is None
            or isinstance(artifact.get("sizeBytes"), bool)
            or not isinstance(artifact.get("sizeBytes"), int)
            or artifact["sizeBytes"] < 0
        ):
            raise FullRunError("FINAL_RAW_HASH_MANIFEST_INVALID")
    scorecard = documents["scorecard.v1.json"]
    candidates = scorecard.get("candidates") if isinstance(scorecard, dict) else None
    if (
        not isinstance(scorecard, dict)
        or scorecard.get("schemaVersion") != "s1.4x-scorecard-v1"
        or scorecard.get("benchmarkSubjectCommit") != subject
        or scorecard.get("status") != "PASS"
        or not isinstance(candidates, dict)
        or set(candidates) != {"scala", "haskell"}
    ):
        raise FullRunError("FINAL_SCORECARD_INVALID")
    totals: dict[str, float] = {}
    for candidate, value in candidates.items():
        categories = value.get("categories") if isinstance(value, dict) else None
        if (
            not isinstance(value, dict)
            or value.get("eligibility") != "QUALIFIED"
            or value.get("status") != "PASS"
            or not isinstance(categories, dict)
            or set(categories) != set(SCORE_CATEGORY_MAX)
            or isinstance(value.get("totalPoints"), bool)
            or not isinstance(value.get("totalPoints"), (int, float))
        ):
            raise FullRunError("FINAL_SCORECARD_INVALID")
        category_points = []
        for category, maximum in SCORE_CATEGORY_MAX.items():
            item = categories[category]
            if (
                not isinstance(item, dict)
                or item.get("maxPoints") != maximum
                or isinstance(item.get("points"), bool)
                or not isinstance(item.get("points"), (int, float))
                or not math.isfinite(float(item["points"]))
                or not 0.0 <= float(item["points"]) <= maximum
            ):
                raise FullRunError("FINAL_SCORECARD_INVALID")
            category_points.append(float(item["points"]))
        total = float(value["totalPoints"])
        if (
            not math.isfinite(total)
            or not 0.0 <= total <= 100.0
            or not math.isclose(
                total,
                math.fsum(category_points),
                rel_tol=0.0,
                abs_tol=1e-9,
            )
        ):
            raise FullRunError("FINAL_SCORECARD_INVALID")
        totals[candidate] = total
    return {
        "status": summary["status"],
        "completedBlockCount": 87,
        "partialBlockCount": 0,
        "notMeasuredCount": 0,
        "candidateTotalPoints": totals,
        "documentSha256": {name: _sha256(directory / name) for name in documents},
    }


def _validate_benchmark_raw_closure(
    paths: RunPaths,
    plan: Mapping[str, Any],
) -> None:
    """Final report의 raw manifest를 실제 timing run exact file set에 재대조한다."""

    raw = _strict_json_load(paths.final_reports / "benchmark-raw-hash-manifest.v1.json")
    if not isinstance(raw, dict) or not isinstance(
        raw.get("artifacts"),
        list,
    ):
        raise FullRunError("FINAL_RAW_HASH_MANIFEST_INVALID")
    expected = {
        item["path"]: (item["sha256"], item["sizeBytes"])
        for item in raw["artifacts"]
        if isinstance(item, dict)
        and isinstance(item.get("path"), str)
        and isinstance(item.get("sha256"), str)
        and isinstance(item.get("sizeBytes"), int)
    }
    if len(expected) != len(raw["artifacts"]):
        raise FullRunError("FINAL_RAW_HASH_MANIFEST_INVALID")
    run_directory = paths.benchmark / "run" / str(plan["runId"])
    if not run_directory.is_dir() or run_directory.is_symlink():
        raise FullRunError("BENCHMARK_RAW_DIRECTORY_INVALID")
    actual: dict[str, tuple[str, int]] = {}
    for path in sorted(
        run_directory.rglob("*"),
        key=lambda item: item.relative_to(run_directory).as_posix().encode("utf-8"),
    ):
        if path.is_symlink():
            raise FullRunError("BENCHMARK_RAW_SYMLINK_FORBIDDEN")
        if path.is_file():
            actual[path.relative_to(run_directory).as_posix()] = (
                _sha256(path),
                path.stat().st_size,
            )
    if not actual or actual != expected:
        raise FullRunError("BENCHMARK_RAW_HASH_CLOSURE_INVALID")


def _validate_correctness_raw_closure(
    paths: RunPaths,
    plan: Mapping[str, Any],
) -> None:
    """Correctness manifest를 manifest 자신을 제외한 exact raw tree에 재대조한다."""

    manifest_path = paths.correctness / "correctness-run-manifest.v1.json"
    manifest = _strict_json_load(manifest_path)
    artifacts = manifest.get("artifacts") if isinstance(manifest, dict) else None
    if (
        not isinstance(manifest, dict)
        or manifest.get("schemaVersion") != "s1.4x-correctness-run-manifest-v1"
        or manifest.get("benchmarkSubjectCommit") != plan["benchmarkSubjectCommit"]
        or manifest.get("status") != "PASS"
        or not isinstance(artifacts, list)
        or manifest.get("artifactCount") != len(artifacts)
        or not artifacts
    ):
        raise FullRunError("CORRECTNESS_RAW_MANIFEST_INVALID")
    expected: dict[str, tuple[str, int]] = {}
    for item in artifacts:
        if (
            not isinstance(item, dict)
            or set(item) != {"path", "sha256", "sizeBytes"}
            or not isinstance(item.get("path"), str)
            or not item["path"]
            or item["path"].startswith("/")
            or "\\" in item["path"]
            or any(part in {"", ".", ".."} for part in item["path"].split("/"))
            or SHA256.fullmatch(str(item.get("sha256"))) is None
            or isinstance(item.get("sizeBytes"), bool)
            or not isinstance(item.get("sizeBytes"), int)
            or item["sizeBytes"] < 0
            or item["path"] in expected
        ):
            raise FullRunError("CORRECTNESS_RAW_MANIFEST_INVALID")
        expected[item["path"]] = (
            item["sha256"],
            item["sizeBytes"],
        )
    actual: dict[str, tuple[str, int]] = {}
    for path in sorted(
        paths.correctness.rglob("*"),
        key=lambda item: item.relative_to(paths.correctness).as_posix().encode("utf-8"),
    ):
        if path.is_symlink():
            raise FullRunError("CORRECTNESS_RAW_SYMLINK_FORBIDDEN")
        if path.is_file() and path != manifest_path:
            actual[path.relative_to(paths.correctness).as_posix()] = (
                _sha256(path),
                path.stat().st_size,
            )
    if actual != expected:
        raise FullRunError("CORRECTNESS_RAW_HASH_CLOSURE_INVALID")


def _validate_audit_scorecard_binding(paths: RunPaths) -> str:
    """Scorecard가 현재 typed audit ledger bytes를 가리키는지 검증한다."""

    ledger = paths.correctness_audit / "final-candidate-audit.json"
    scorecard = _strict_json_load(paths.final_reports / "scorecard.v1.json")
    ledger_sha256 = _sha256(ledger)
    if (
        not isinstance(scorecard, dict)
        or scorecard.get("auditLedgerSha256") != ledger_sha256
    ):
        raise FullRunError("FINAL_AUDIT_SCORECARD_BINDING_INVALID")
    return ledger_sha256


def _revalidate_final_candidate_audit(
    paths: RunPaths,
    plan: Mapping[str, Any],
) -> dict[str, Any]:
    """기존 typed validator로 final-audit evidence와 scorecard hash를 재검증한다."""

    _validate_runtime_bindings(plan)
    ledger = paths.correctness_audit / "final-candidate-audit.json"
    ledger_sha256 = _validate_audit_scorecard_binding(paths)
    repo = Path(str(plan["repositoryRoot"]))
    integration = (
        repo
        / "workspaces/decision-platform/research/s1-4x-numeric-parity"
        / "integration"
    )
    oracle = integration.parent / "oracle"
    command = [
        _binding_path(plan, "uv"),
        "run",
        "--frozen",
        "--no-config",
        "--project",
        str(oracle),
        "python",
        str(integration / "final_candidate_audit.py"),
        "validate",
        "--repository-root",
        str(repo),
        "--benchmark-subject-commit",
        str(plan["benchmarkSubjectCommit"]),
        "--ledger",
        str(ledger),
    ]
    completed = subprocess.run(
        command,
        cwd=repo,
        env=_execution_environment(plan),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=False,
        timeout=300,
    )
    stdout_path = paths.terminal / "final-audit-revalidation.stdout.log"
    stderr_path = paths.terminal / "final-audit-revalidation.stderr.log"
    _exclusive_bytes(stdout_path, completed.stdout)
    _exclusive_bytes(stderr_path, completed.stderr)
    if completed.returncode != 0:
        raise FullRunError("FINAL_AUDIT_REVALIDATION_FAILED")
    result = _strict_json_load(stdout_path)
    if (
        not isinstance(result, dict)
        or result.get("candidateCount") != 2
        or result.get("sha256") != ledger_sha256
        or result.get("status") != "PASS"
    ):
        raise FullRunError("FINAL_AUDIT_REVALIDATION_INVALID")
    return {
        "ledgerSha256": ledger_sha256,
        "validatorStdoutSha256": _sha256(stdout_path),
        "validatorStderrSha256": _sha256(stderr_path),
        "status": "PASS",
    }


def _last_completed_stage(paths: RunPaths) -> str | None:
    completed = [
        stage for stage in STAGE_ORDER if any(paths.checkpoints.glob(f"*-{stage}.json"))
    ]
    return completed[-1] if completed else None


def _candidate_failure(
    paths: RunPaths,
    plan: Mapping[str, Any],
    error: BaseException,
) -> None:
    if (paths.candidate / "PASS.json").exists():
        return
    stage = error.stage if isinstance(error, StageFailure) else None
    exit_code = error.exit_code if isinstance(error, StageFailure) else None
    failure_code = (
        error.failure_code if isinstance(error, StageFailure) else type(error).__name__
    )
    _exclusive_json(
        paths.candidate / "FAIL.json",
        {
            "schemaVersion": CANDIDATE_SCHEMA,
            "benchmarkSubjectCommit": plan["benchmarkSubjectCommit"],
            "runId": plan["runId"],
            "failedAt": _utc_now(),
            "failedStage": stage,
            "lastCompletedStage": _last_completed_stage(paths),
            "exitCode": exit_code,
            "failureCode": failure_code,
            "failureDetail": str(error),
            "freshRunRequired": True,
            "status": "FAIL",
        },
    )


def _acquire_run_lock(plan: Mapping[str, Any]) -> int:
    lock_path = Path(str(plan["lockPath"]))
    if not lock_path.is_absolute() or lock_path.is_symlink():
        raise FullRunError("RUN_LOCK_PATH_UNSAFE")
    descriptor = os.open(
        lock_path,
        os.O_RDWR | os.O_CREAT | os.O_CLOEXEC,
        0o600,
    )
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        os.close(descriptor)
        raise FullRunError("ANOTHER_DETACHED_FULL_RUN_IS_ACTIVE") from exc
    return descriptor


def execute_run(config: Path) -> int:
    """봉인된 plan을 correctness부터 finalizer까지 foreground에서 한 번 실행한다."""

    paths = RunPaths.from_run_root(config.parent)
    plan: dict[str, Any] = {}
    lock_descriptor: int | None = None
    try:
        if config != paths.plan:
            raise FullRunError("RUN_CONFIG_PATH_INVALID")
        _validate_plan_sidecar(paths)
        plan = _load_run_plan(config, strict=True)
        if Path(plan["runRoot"]) != paths.root:
            raise FullRunError("RUN_PLAN_ROOT_MISMATCH")
        _validate_control_supervisor(paths, plan)
        _validate_evidence_reuse(paths, plan)
        lock_descriptor = _acquire_run_lock(plan)
        home = _validate_absolute_directory(
            Path(plan["home"]),
            label="HOME",
        )
        repo = _validate_absolute_directory(
            Path(plan["repositoryRoot"]),
            label="REPOSITORY",
        )
        _verify_repository_state(
            repo,
            home,
            str(plan["benchmarkSubjectCommit"]),
            require_remote_match=True,
        )
        _validate_runtime_bindings(plan)
        environment = _execution_environment(plan)
        commands = build_stage_commands(plan)
        deadline = time.monotonic() + int(plan["overallTimeoutSeconds"])
        _append_event(
            paths.events,
            {
                "event": "RUN_STARTED",
                "runId": plan["runId"],
                "benchmarkSubjectCommit": plan["benchmarkSubjectCommit"],
            },
        )

        def validate_output(
            command: StageCommand,
            run_paths: RunPaths,
        ) -> None:
            _verify_repository_state(
                repo,
                home,
                str(plan["benchmarkSubjectCommit"]),
                require_remote_match=True,
            )
            _validate_runtime_bindings(plan)
            _validate_stage_output(command, run_paths)

        execute_stage_sequence(
            commands,
            paths=paths,
            environment=environment,
            deadline=deadline,
            output_validator=validate_output,
        )
        _raise_if_interrupted("typed-finalization")
        final = validate_final_reports(
            paths.final_reports,
            str(plan["benchmarkSubjectCommit"]),
        )
        _verify_repository_state(
            repo,
            home,
            str(plan["benchmarkSubjectCommit"]),
            require_remote_match=True,
        )
        _raise_if_interrupted("terminal-candidate-seal")
        _exclusive_json(
            paths.candidate / "PASS.json",
            {
                "schemaVersion": CANDIDATE_SCHEMA,
                "benchmarkSubjectCommit": plan["benchmarkSubjectCommit"],
                "runId": plan["runId"],
                "completedAt": _utc_now(),
                "finalReportValidation": final,
                "freshRunRequiredOnAnyDrift": True,
                "status": "BENCHMARK_EVIDENCE_READY_FOR_REVIEW",
            },
        )
        _append_event(
            paths.events,
            {
                "event": "RUN_CANDIDATE_COMPLETED",
                "status": "BENCHMARK_EVIDENCE_READY_FOR_REVIEW",
            },
        )
        return 0
    except (
        FullRunError,
        OSError,
        subprocess.SubprocessError,
        ValueError,
    ) as exc:
        if plan:
            try:
                _append_event(
                    paths.events,
                    {
                        "event": "RUN_FAILED",
                        "failureCode": (
                            exc.failure_code
                            if isinstance(exc, StageFailure)
                            else type(exc).__name__
                        ),
                        "failureDetail": str(exc),
                    },
                )
                _candidate_failure(paths, plan, exc)
            except (FullRunError, OSError, ValueError):
                pass
        print(f"S1_4X_DETACHED_FULL_RUN_FAIL:{exc}", file=sys.stderr)
        return 2
    finally:
        if lock_descriptor is not None:
            fcntl.flock(lock_descriptor, fcntl.LOCK_UN)
            os.close(lock_descriptor)


def _selected_evidence_files(paths: RunPaths) -> list[Path]:
    selected = [
        paths.plan,
        paths.plan_sidecar,
        paths.events,
        paths.control / CONTINUATION_MANIFEST_NAME,
        paths.correctness / "correctness-run-manifest.v1.json",
        paths.correctness / "large-fixture-receipt.json",
        paths.correctness_audit / "final-candidate-audit.json",
        paths.benchmark / "commands.json",
        paths.benchmark / "commands.sha256",
        paths.terminal / "final-audit-revalidation.stdout.log",
        paths.terminal / "final-audit-revalidation.stderr.log",
    ]
    for directory in (
        paths.stages,
        paths.checkpoints,
        paths.candidate,
        paths.final_reports,
    ):
        if not directory.exists():
            continue
        for root, directories, files in os.walk(directory, followlinks=False):
            root_path = Path(root)
            for name in directories:
                if (root_path / name).is_symlink():
                    raise FullRunError("EVIDENCE_SYMLINK_FORBIDDEN")
            selected.extend(root_path / name for name in files)
    return sorted(
        {path for path in selected if path.is_file() and not path.is_symlink()},
        key=lambda item: str(item.relative_to(paths.root)),
    )


def _write_evidence_index(paths: RunPaths, subject: str) -> dict[str, Any]:
    artifacts = [
        {
            "path": str(path.relative_to(paths.root)),
            "sha256": _sha256(path),
            "sizeBytes": path.stat().st_size,
        }
        for path in _selected_evidence_files(paths)
    ]
    index = {
        "schemaVersion": EVIDENCE_INDEX_SCHEMA,
        "benchmarkSubjectCommit": subject,
        "artifactCount": len(artifacts),
        "artifacts": artifacts,
        "status": "SEALED",
    }
    index_path = paths.terminal / "evidence-index.json"
    _exclusive_json(index_path, index)
    checksum_lines = "".join(
        f"{item['sha256']}  {item['path']}\n" for item in artifacts
    )
    _exclusive_bytes(
        paths.terminal / "SHA256SUMS",
        checksum_lines.encode("utf-8"),
    )
    return {
        "evidenceIndexSha256": _sha256(index_path),
        "sha256SumsSha256": _sha256(paths.terminal / "SHA256SUMS"),
        "artifactCount": len(artifacts),
    }


def service_finalize(
    run_root: Path,
    *,
    service_result: str,
    exit_code: str,
    exit_status: str,
    audit_revalidator: AuditRevalidator = (_revalidate_final_candidate_audit),
) -> dict[str, Any]:
    """ExecStopPost에서 service 결과와 candidate marker를 terminal marker로 승격한다."""

    paths = RunPaths.from_run_root(run_root)
    terminal_pass = paths.terminal / "PASS.json"
    terminal_fail = paths.terminal / "FAIL.json"
    if terminal_pass.exists() and terminal_fail.exists():
        raise FullRunError("TERMINAL_MARKER_XOR_INVALID")
    if terminal_pass.exists() or terminal_fail.exists():
        value = _strict_json_load(
            terminal_pass if terminal_pass.exists() else terminal_fail
        )
        if not isinstance(value, dict):
            raise FullRunError("TERMINAL_MARKER_INVALID")
        return value
    _validate_plan_sidecar(paths)
    plan = _load_run_plan(paths.plan, strict=True)
    if Path(plan["runRoot"]) != paths.root:
        raise FullRunError("RUN_PLAN_ROOT_MISMATCH")
    _validate_control_supervisor(paths, plan)
    _validate_evidence_reuse(paths, plan)
    candidate_pass = paths.candidate / "PASS.json"
    candidate_fail = paths.candidate / "FAIL.json"
    candidate_xor = candidate_pass.exists() ^ candidate_fail.exists()
    service_success = (
        service_result == "success" and exit_code == "exited" and exit_status == "0"
    )
    failure_code: str | None = None
    final_validation: dict[str, Any] | None = None
    audit_validation: dict[str, Any] | None = None
    if not candidate_xor:
        failure_code = "CANDIDATE_MARKER_XOR_INVALID"
    elif not service_success:
        failure_code = "SYSTEMD_SERVICE_FAILED"
    elif not candidate_pass.exists():
        failure_code = "SUPERVISOR_CANDIDATE_FAILED"
    else:
        candidate = _strict_json_load(candidate_pass)
        if (
            not isinstance(candidate, dict)
            or candidate.get("schemaVersion") != CANDIDATE_SCHEMA
            or candidate.get("benchmarkSubjectCommit") != plan["benchmarkSubjectCommit"]
            or candidate.get("status") != "BENCHMARK_EVIDENCE_READY_FOR_REVIEW"
        ):
            failure_code = "CANDIDATE_PASS_INVALID"
        else:
            try:
                _validate_finalizer_receipt(
                    paths,
                    str(plan["benchmarkSubjectCommit"]),
                )
                final_validation = validate_final_reports(
                    paths.final_reports,
                    str(plan["benchmarkSubjectCommit"]),
                )
                _validate_correctness_raw_closure(paths, plan)
                _validate_benchmark_raw_closure(paths, plan)
                _validate_audit_scorecard_binding(paths)
                audit_validation = audit_revalidator(paths, plan)
            except (FullRunError, OSError, ValueError):
                failure_code = "FINAL_REPORT_REVALIDATION_FAILED"
    evidence = _write_evidence_index(
        paths,
        str(plan["benchmarkSubjectCommit"]),
    )
    if failure_code is None:
        result: dict[str, Any] = {
            "schemaVersion": TERMINAL_SCHEMA,
            "benchmarkSubjectCommit": plan["benchmarkSubjectCommit"],
            "runId": plan["runId"],
            "unitName": plan.get("unitName"),
            "completedAt": _utc_now(),
            "serviceResult": service_result,
            "serviceExitCode": exit_code,
            "serviceExitStatus": exit_status,
            "finalReportValidation": final_validation,
            "finalAuditRevalidation": audit_validation,
            **evidence,
            "nextAction": "AGENT_REVIEW_AND_RANKING",
            "status": "PASS",
        }
        _exclusive_json(terminal_pass, result)
        return result
    result = {
        "schemaVersion": TERMINAL_SCHEMA,
        "benchmarkSubjectCommit": plan["benchmarkSubjectCommit"],
        "runId": plan.get("runId"),
        "unitName": plan.get("unitName"),
        "completedAt": _utc_now(),
        "serviceResult": service_result,
        "serviceExitCode": exit_code,
        "serviceExitStatus": exit_status,
        "failureCode": failure_code,
        "lastCompletedStage": _last_completed_stage(paths),
        "freshRunRequired": True,
        **evidence,
        "nextAction": "REPORT_FAILURE_AND_REQUEST_APPROVAL",
        "status": "FAIL",
    }
    _exclusive_json(terminal_fail, result)
    return result


def inspect_status(run_root: Path) -> dict[str, Any]:
    """marker만 읽어 RUNNING, FINALIZING, PASS, FAIL을 구분한다."""

    paths = RunPaths.from_run_root(run_root)
    terminal_pass = paths.terminal / "PASS.json"
    terminal_fail = paths.terminal / "FAIL.json"
    if terminal_pass.exists() and terminal_fail.exists():
        return {"status": "INVALID", "failureCode": "TERMINAL_MARKER_XOR_INVALID"}
    if terminal_pass.exists() or terminal_fail.exists():
        value = _strict_json_load(
            terminal_pass if terminal_pass.exists() else terminal_fail
        )
        if not isinstance(value, dict):
            return {"status": "INVALID", "failureCode": "TERMINAL_MARKER_INVALID"}
        return value
    candidate_pass = (paths.candidate / "PASS.json").exists()
    candidate_fail = (paths.candidate / "FAIL.json").exists()
    if candidate_pass and candidate_fail:
        return {"status": "INVALID", "failureCode": "CANDIDATE_MARKER_XOR_INVALID"}
    if candidate_pass or candidate_fail:
        return {
            "status": "FINALIZING",
            "candidateStatus": "PASS" if candidate_pass else "FAIL",
        }
    return {
        "status": "RUNNING",
        "lastCompletedStage": _last_completed_stage(paths),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--repo-root", type=Path, required=True)
    prepare.add_argument("--run-root", type=Path, required=True)
    prepare.add_argument("--run-id", required=True)
    prepare.add_argument("--benchmark-subject-commit", required=True)
    prepare.add_argument("--failed-run-root", type=Path)
    prepare.add_argument("--scala-qualification-source", type=Path)
    prepare.add_argument("--haskell-static-source", type=Path)
    prepare.add_argument("--haskell-profile-source", type=Path)
    prepare.add_argument(
        "--overall-timeout-seconds",
        type=int,
        default=61_200,
    )
    run = commands.add_parser("run")
    run.add_argument("--config", type=Path, required=True)
    finalize = commands.add_parser("service-finalize")
    finalize.add_argument("--run-root", type=Path, required=True)
    finalize.add_argument("--service-result")
    finalize.add_argument("--exit-code")
    finalize.add_argument("--exit-status")
    status = commands.add_parser("status")
    status.add_argument("--run-root", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        if arguments.command == "prepare":
            result = prepare_run(
                repo_root=arguments.repo_root,
                run_root=arguments.run_root,
                run_id=arguments.run_id,
                subject=arguments.benchmark_subject_commit,
                overall_timeout_seconds=arguments.overall_timeout_seconds,
                failed_run_root=arguments.failed_run_root,
                scala_qualification_source=arguments.scala_qualification_source,
                haskell_static_source=arguments.haskell_static_source,
                haskell_profile_source=arguments.haskell_profile_source,
            )
        elif arguments.command == "run":
            return execute_run(arguments.config)
        elif arguments.command == "service-finalize":
            result = service_finalize(
                arguments.run_root,
                service_result=(
                    arguments.service_result
                    or os.environ.get("SERVICE_RESULT", "unknown")
                ),
                exit_code=(
                    arguments.exit_code or os.environ.get("EXIT_CODE", "unknown")
                ),
                exit_status=(
                    arguments.exit_status or os.environ.get("EXIT_STATUS", "unknown")
                ),
            )
        elif arguments.command == "status":
            result = inspect_status(arguments.run_root)
        else:
            raise FullRunError("COMMAND_INVALID")
        print(
            json.dumps(
                result,
                allow_nan=False,
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        # ExecStopPost는 원래 service result를 보존해야 하므로, FAIL marker를
        # 정상적으로 봉인했다면 후처리 자체는 성공으로 종료한다.
        if arguments.command == "service-finalize":
            return 0
        return 2 if result.get("status") in {"FAIL", "INVALID"} else 0
    except (FullRunError, OSError, ValueError) as exc:
        print(f"S1_4X_DETACHED_FULL_RUN_CONTROL_FAIL:{exc}", file=sys.stderr)
        return 2


for handled_signal in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
    signal.signal(handled_signal, _signal_handler)


if __name__ == "__main__":
    raise SystemExit(main())
