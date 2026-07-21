#!/usr/bin/env python3
"""봉인된 실패 run과 검증된 ancestor evidence를 fresh correctness root로 옮긴다."""

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
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

SOURCE_MANIFEST_SCHEMA = "s1.4x-continuation-source-manifest-v1"
IMPORT_RECEIPT_SCHEMA = "s1.4x-continuation-import-v1"
CANDIDATE_SCHEMA = "s1.4x-detached-full-run-candidate-v1"
TERMINAL_SCHEMA = "s1.4x-detached-full-run-terminal-v1"
EVIDENCE_INDEX_SCHEMA = "s1.4x-detached-full-run-evidence-index-v1"
RUN_PLAN_SCHEMA = "s1.4x-detached-full-run-plan-v1"
FAILED_STAGE = "correctness-oci-regression"
SOURCE_MANIFEST_NAME = "continuation-source-manifest.v1.json"
IMPORT_RECEIPT_NAME = "continuation-import.v1.json"

SCALA_QUALIFICATION_SOURCE_COMMIT = "01bfbaa57fdceeddbaa6f6b113e95358349f0c42"
HASKELL_PROFILE_SOURCE_COMMIT = "a30bbca696614512a45bdc3635896c81dd8fcd85"
SCALA_QUALIFICATION_EXPECTED_FILE_COUNT = 4924
HASKELL_QUALIFICATION_EXPECTED_FILE_COUNT = 57
HASKELL_QUALIFICATION_SHA256 = (
    "996c99ec659b67fe9b38ca77ae59a3d696e79903b3c443d8a42ec52c7137c764"
)
HASKELL_BASELINE_CORRECTNESS_SHA256 = (
    "c250e56090103aedbee9cc77832da4127e09f4667aec9ee2ddf2bd6ba699eb9c"
)
HASKELL_SOURCE_TREE_SHA256 = (
    "cbcffa1ece42d718c3d605d51247b71cba0daf3c1e22e517a13f0fae73318152"
)

COMMIT = re.compile(r"^[0-9a-f]{40}$")
RUN_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,47}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
SHA256_SUM_LINE = re.compile(r"^([0-9a-f]{64})  ([^\r\n]+)\n$")
READ_BLOCK_BYTES = 1024 * 1024
GIT = "/usr/bin/git"
S1_ROOT = PurePosixPath(
    "workspaces/decision-platform/research/s1-4x-numeric-parity"
)

# parent..target에는 continuation orchestration과 host-validity 완화만 허용한다.
CONTINUATION_DIFF_ALLOWLIST = frozenset(
    {
        str(S1_ROOT / "integration/assemble_final_candidate_evidence.py"),
        str(S1_ROOT / "integration/coverage_execution.py"),
        str(S1_ROOT / "integration/continuation_prefix.py"),
        str(S1_ROOT / "integration/detached_full_run.py"),
        str(S1_ROOT / "integration/final_candidate_audit.py"),
        str(S1_ROOT / "integration/gate.py"),
        str(S1_ROOT / "integration/run_full_correctness.py"),
        str(S1_ROOT / "integration/tools/launch-detached-full-run.sh"),
        str(S1_ROOT / "integration/tools/run-haskell-candidate.sh"),
        str(S1_ROOT / "integration/tools/run-integration-correctness.sh"),
        str(S1_ROOT / "integration/tools/run-native-oci-regression-gates.sh"),
        str(S1_ROOT / "integration/tests/test_continuation_prefix.py"),
        str(S1_ROOT / "integration/tests/test_coverage_execution.py"),
        str(
            S1_ROOT
            / "integration/tests/test_assemble_final_candidate_evidence.py"
        ),
        str(S1_ROOT / "integration/tests/test_detached_full_run.py"),
        str(S1_ROOT / "integration/tests/test_gate.py"),
        str(S1_ROOT / "integration/tests/test_native_oci_continuation_contract.py"),
        str(S1_ROOT / "contract/contract-manifest.v1.json"),
        str(S1_ROOT / "oracle/validate_environment.py"),
        str(S1_ROOT / "oracle/tests/test_validate_environment.py"),
        str(S1_ROOT / "reports/integration-baseline.v1.json"),
        str(S1_ROOT / "README.md"),
        str(S1_ROOT / "integration/README.md"),
    }
)

FAILED_PREFIX_DIRECTORIES = (
    "large-fixtures",
    "scala/scalafmt",
    "scala/scalafix",
    "scala/hard-compiler-A",
    "scala/hard-compiler-B",
    "scala/hard-compiler-C",
    "scala/profiles",
)
FAILED_PREFIX_REQUIRED_FILES = (
    "large-fixture-check-receipt.json",
    "large-fixture-receipt.json",
    "scala/scala-dependency-edge-result.v1.json",
    "scala/scala-source-policy-result.v1.json",
)
FORBIDDEN_DESTINATIONS = frozenset(
    {
        "contract-validation.json",
        "scala/scala-selected-profile-result.v1.json",
        "haskell/selected-profile.v1.json",
    }
)


class ContinuationPrefixError(ValueError):
    """Continuation source, provenance 또는 copy closure 계약 위반이다."""


@dataclass(frozen=True, slots=True)
class ParentFailure:
    """봉인된 실패 run에서 검증한 identity와 runtime binding이다."""

    run_id: str
    subject: str
    failed_stage: str
    scala_cli_path: Path


@dataclass(frozen=True, slots=True)
class Artifact:
    """한 source의 regular file과 destination identity를 묶는다."""

    source_id: str
    source_root: Path
    source_relative_path: str
    destination_path: str
    sha256: str
    size_bytes: int

    def manifest_entry(self) -> dict[str, object]:
        return {
            "sourceId": self.source_id,
            "sourceRelativePath": self.source_relative_path,
            "destinationPath": self.destination_path,
            "sha256": self.sha256,
            "sizeBytes": self.size_bytes,
        }


@dataclass(frozen=True, slots=True)
class SourceTree:
    """한 logical source allowlist와 그 typed binding을 설명한다."""

    source_id: str
    source_root: Path
    source_relative_path: str
    destination_relative_path: str
    excluded_subtrees: tuple[dict[str, str], ...]
    bindings: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class _CopySpec:
    source_relative: str
    destination_relative: str
    excluded: tuple[str, ...] = ()


def _error(code: str, detail: str | None = None) -> ContinuationPrefixError:
    if detail is None:
        return ContinuationPrefixError(code)
    return ContinuationPrefixError(f"{code}:{detail}")


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
    except (TypeError, ValueError, UnicodeError) as exc:
        raise _error("CANONICAL_JSON_INVALID") from exc


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json_bytes(value)[:-1]).hexdigest()


def _reject_constant(token: str) -> Any:
    raise _error("NON_FINITE_JSON", token)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _error("DUPLICATE_JSON_KEY", key)
        result[key] = value
    return result


def _strict_json_bytes(payload: bytes, *, label: str) -> Any:
    try:
        text = payload.decode("utf-8", errors="strict")
        return json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except UnicodeDecodeError as exc:
        raise _error("INVALID_UTF8", label) from exc
    except json.JSONDecodeError as exc:
        raise _error("INVALID_JSON", label) from exc


def _identity(metadata: os.stat_result) -> tuple[int, int, int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_nlink,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _portable_relative(value: str, *, label: str) -> PurePosixPath:
    try:
        path = PurePosixPath(value)
    except (TypeError, ValueError) as exc:
        raise _error("PATH_INVALID", label) from exc
    if (
        not value
        or path.is_absolute()
        or path.as_posix() != value
        or any(
            component in {"", ".", ".."}
            or "\\" in component
            or any(ord(character) < 32 or ord(character) == 127 for character in component)
            for component in path.parts
        )
    ):
        raise _error("PATH_INVALID", label)
    return path


def _canonical_directory(path: Path, *, label: str) -> Path:
    if not path.is_absolute():
        raise _error("DIRECTORY_INVALID", label)
    try:
        resolved = path.resolve(strict=True)
        metadata = path.lstat()
    except (OSError, RuntimeError) as exc:
        raise _error("DIRECTORY_INVALID", label) from exc
    if resolved != path or stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise _error("DIRECTORY_INVALID", label)
    return path


def _stable_regular_bytes(root: Path, relative: str, *, label: str) -> bytes:
    relative_path = _portable_relative(relative, label=label)
    current = root
    try:
        for component in relative_path.parts[:-1]:
            current = current / component
            metadata = current.lstat()
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
                raise _error("SYMLINK_OR_NON_DIRECTORY", relative)
        path = root.joinpath(*relative_path.parts)
        before = path.lstat()
    except OSError as exc:
        raise _error("ARTIFACT_MISSING", relative) from exc
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise _error("NON_REGULAR_ARTIFACT", relative)
    no_follow = getattr(os, "O_NOFOLLOW", None)
    if no_follow is None:
        raise _error("NOFOLLOW_UNSUPPORTED")
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | no_follow)
    except OSError as exc:
        raise _error("ARTIFACT_OPEN_FAILED", relative) from exc
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or _identity(opened) != _identity(before):
            raise _error("ARTIFACT_CHANGED", relative)
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, READ_BLOCK_BYTES)
            if not chunk:
                break
            chunks.append(chunk)
        after_fd = os.fstat(descriptor)
        after_path = path.lstat()
        if _identity(opened) != _identity(after_fd) or _identity(after_fd) != _identity(
            after_path
        ):
            raise _error("ARTIFACT_CHANGED", relative)
        payload = b"".join(chunks)
        if len(payload) != opened.st_size:
            raise _error("ARTIFACT_SIZE_CHANGED", relative)
        return payload
    finally:
        os.close(descriptor)


def _stable_file_record(
    root: Path,
    relative: str,
    *,
    source_id: str,
    destination: str,
) -> Artifact:
    payload = _stable_regular_bytes(root, relative, label=relative)
    _portable_relative(destination, label=destination)
    return Artifact(
        source_id=source_id,
        source_root=root,
        source_relative_path=relative,
        destination_path=destination,
        sha256=hashlib.sha256(payload).hexdigest(),
        size_bytes=len(payload),
    )


def _write_exclusive(path: Path, payload: bytes) -> None:
    if not path.is_absolute():
        raise _error("OUTPUT_PATH_INVALID")
    parent = _canonical_directory(path.parent, label="output-parent")
    if parent != path.parent or path.exists() or path.is_symlink():
        raise _error("OUTPUT_ALREADY_EXISTS", str(path))
    no_follow = getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | no_follow,
            0o600,
        )
    except OSError as exc:
        raise _error("OUTPUT_CREATE_FAILED", str(path)) from exc
    try:
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise _error("OUTPUT_WRITE_FAILED", str(path))
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _require_object(value: Any, *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _error("JSON_OBJECT_REQUIRED", label)
    return value


def _verified_terminal_artifacts(
    run_root: Path,
) -> tuple[dict[str, bytes], dict[str, Any]]:
    terminal_payload = _stable_regular_bytes(
        run_root, "terminal/FAIL.json", label="terminal-fail"
    )
    terminal = _require_object(
        _strict_json_bytes(terminal_payload, label="terminal-fail"),
        label="terminal-fail",
    )
    index_payload = _stable_regular_bytes(
        run_root, "terminal/evidence-index.json", label="evidence-index"
    )
    sums_payload = _stable_regular_bytes(
        run_root, "terminal/SHA256SUMS", label="sha256sums"
    )
    if (
        terminal.get("schemaVersion") != TERMINAL_SCHEMA
        or terminal.get("status") != "FAIL"
        or terminal.get("freshRunRequired") is not True
        or terminal.get("evidenceIndexSha256") != _sha256(index_payload)
        or terminal.get("sha256SumsSha256") != _sha256(sums_payload)
    ):
        raise _error("TERMINAL_FAIL_INVALID")

    index = _require_object(
        _strict_json_bytes(index_payload, label="evidence-index"),
        label="evidence-index",
    )
    artifacts = index.get("artifacts")
    if (
        index.get("schemaVersion") != EVIDENCE_INDEX_SCHEMA
        or index.get("status") != "SEALED"
        or not isinstance(artifacts, list)
        or index.get("artifactCount") != len(artifacts)
        or terminal.get("artifactCount") != len(artifacts)
    ):
        raise _error("EVIDENCE_INDEX_INVALID")

    checksum_lines = sums_payload.splitlines(keepends=True)
    if len(checksum_lines) != len(artifacts):
        raise _error("SHA256SUMS_COUNT_MISMATCH")
    verified: dict[str, bytes] = {}
    previous: bytes | None = None
    for item, raw_line in zip(artifacts, checksum_lines, strict=True):
        if not isinstance(item, dict) or set(item) != {"path", "sha256", "sizeBytes"}:
            raise _error("EVIDENCE_ENTRY_INVALID")
        path = item.get("path")
        digest = item.get("sha256")
        size = item.get("sizeBytes")
        if (
            not isinstance(path, str)
            or not isinstance(digest, str)
            or SHA256.fullmatch(digest) is None
            or not isinstance(size, int)
            or isinstance(size, bool)
            or size < 0
        ):
            raise _error("EVIDENCE_ENTRY_INVALID")
        encoded = path.encode("utf-8")
        if previous is not None and encoded <= previous:
            raise _error("EVIDENCE_ORDER_INVALID")
        previous = encoded
        try:
            line = raw_line.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise _error("SHA256SUMS_INVALID") from exc
        match = SHA256_SUM_LINE.fullmatch(line)
        if match is None or match.groups() != (digest, path):
            raise _error("SHA256SUMS_INDEX_MISMATCH", path)
        payload = _stable_regular_bytes(run_root, path, label=path)
        if len(payload) != size or _sha256(payload) != digest:
            raise _error("SEALED_ARTIFACT_DRIFT", path)
        if path in verified:
            raise _error("EVIDENCE_DUPLICATE", path)
        verified[path] = payload
    return verified, terminal


def verify_failed_parent_run(failed_run_root: Path) -> ParentFailure:
    """terminal/candidate/stage 봉인을 검증하고 재사용 가능한 parent identity만 반환한다."""

    run_root = _canonical_directory(failed_run_root, label="failed-run-root")
    for forbidden in ("candidate/PASS.json", "terminal/PASS.json"):
        path = run_root / forbidden
        if path.exists() or path.is_symlink():
            raise _error("FAIL_MARKER_XOR_INVALID", forbidden)
    verified, terminal = _verified_terminal_artifacts(run_root)
    required = {
        "candidate/FAIL.json",
        "run-plan.v1.json",
        f"stages/{FAILED_STAGE}/receipt.json",
        f"stages/{FAILED_STAGE}/stdout.log",
        f"stages/{FAILED_STAGE}/stderr.log",
    }
    if not required.issubset(verified):
        raise _error("FAILED_RUN_EVIDENCE_INCOMPLETE")
    candidate = _require_object(
        _strict_json_bytes(verified["candidate/FAIL.json"], label="candidate-fail"),
        label="candidate-fail",
    )
    plan = _require_object(
        _strict_json_bytes(verified["run-plan.v1.json"], label="run-plan"),
        label="run-plan",
    )
    receipt_path = f"stages/{FAILED_STAGE}/receipt.json"
    receipt = _require_object(
        _strict_json_bytes(verified[receipt_path], label="stage-receipt"),
        label="stage-receipt",
    )
    run_id = candidate.get("runId")
    subject = candidate.get("benchmarkSubjectCommit")
    exit_code = candidate.get("exitCode")
    if (
        candidate.get("schemaVersion") != CANDIDATE_SCHEMA
        or candidate.get("status") != "FAIL"
        or candidate.get("failedStage") != FAILED_STAGE
        or candidate.get("lastCompletedStage") is not None
        or candidate.get("failureCode") != "STAGE_COMMAND_FAILED"
        or candidate.get("freshRunRequired") is not True
        or not isinstance(run_id, str)
        or RUN_ID.fullmatch(run_id) is None
        or not isinstance(subject, str)
        or COMMIT.fullmatch(subject) is None
        or not isinstance(exit_code, int)
        or isinstance(exit_code, bool)
        or exit_code == 0
        or candidate.get("failureDetail")
        != f"STAGE_COMMAND_FAILED:{FAILED_STAGE}:exit={exit_code}"
    ):
        raise _error("CANDIDATE_FAIL_INVALID")
    if run_root.name != run_id:
        raise _error("PARENT_RUN_ID_ROOT_MISMATCH")
    if (
        terminal.get("runId") != run_id
        or terminal.get("benchmarkSubjectCommit") != subject
        or terminal.get("lastCompletedStage") is not None
    ):
        raise _error("TERMINAL_CANDIDATE_IDENTITY_MISMATCH")
    stdout_path = f"stages/{FAILED_STAGE}/stdout.log"
    stderr_path = f"stages/{FAILED_STAGE}/stderr.log"
    if (
        receipt.get("stage") != FAILED_STAGE
        or receipt.get("status") != "FAIL"
        or receipt.get("exitCode") != exit_code
        or receipt.get("stdoutRelativePath") != stdout_path
        or receipt.get("stderrRelativePath") != stderr_path
        or receipt.get("stdoutSha256") != _sha256(verified[stdout_path])
        or receipt.get("stderrSha256") != _sha256(verified[stderr_path])
    ):
        raise _error("FAILED_STAGE_RECEIPT_INVALID")
    runtime = plan.get("runtimeBindings")
    scala_binding = runtime.get("scalaCli") if isinstance(runtime, dict) else None
    scala_path_value = (
        scala_binding.get("path") if isinstance(scala_binding, dict) else None
    )
    if (
        plan.get("schemaVersion") != RUN_PLAN_SCHEMA
        or plan.get("runId") != run_id
        or plan.get("benchmarkSubjectCommit") != subject
        or plan.get("runRoot") != str(run_root)
        or plan.get("stageOrder", [None])[0] != FAILED_STAGE
        or not isinstance(scala_path_value, str)
    ):
        raise _error("FAILED_RUN_PLAN_INVALID")
    scala_cli = Path(scala_path_value)
    if not scala_cli.is_absolute():
        raise _error("SCALA_CLI_BINDING_INVALID")
    return ParentFailure(run_id, subject, FAILED_STAGE, scala_cli)


def _git(
    repo: Path,
    arguments: Sequence[str],
    *,
    allow_exit_one: bool = False,
) -> bytes:
    environment = os.environ.copy()
    environment["GIT_OPTIONAL_LOCKS"] = "0"
    completed = subprocess.run(
        [GIT, "-c", "core.fsmonitor=false", "-C", str(repo), *arguments],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        env=environment,
    )
    allowed = {0, 1} if allow_exit_one else {0}
    if completed.returncode not in allowed:
        raise _error("GIT_COMMAND_FAILED", arguments[0])
    return completed.stdout


def validate_current_diff(
    repository_root: Path,
    *,
    parent_subject: str,
    target_subject: str,
) -> tuple[str, ...]:
    """parent..target commit diff가 고정된 continuation 코드 allowlist 안인지 검증한다."""

    repo = _canonical_directory(repository_root, label="repo-root")
    if COMMIT.fullmatch(parent_subject) is None or COMMIT.fullmatch(target_subject) is None:
        raise _error("COMMIT_INVALID")
    top = _git(repo, ["rev-parse", "--show-toplevel"]).decode("utf-8").strip()
    head = _git(repo, ["rev-parse", "HEAD"]).decode("ascii").strip()
    if top != str(repo) or head != target_subject:
        raise _error("TARGET_REPOSITORY_IDENTITY_MISMATCH")
    for subject in (parent_subject, target_subject):
        resolved = (
            _git(repo, ["rev-parse", "--verify", f"{subject}^{{commit}}"])
            .decode("ascii")
            .strip()
        )
        if resolved != subject:
            raise _error("COMMIT_NOT_EXACT", subject)
    ancestor = subprocess.run(
        [
            GIT,
            "-c",
            "core.fsmonitor=false",
            "-C",
            str(repo),
            "merge-base",
            "--is-ancestor",
            parent_subject,
            target_subject,
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
        env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
    )
    if ancestor.returncode != 0:
        raise _error("PARENT_NOT_ANCESTOR")
    dirty = _git(repo, ["status", "--porcelain=v1", "-z", "--untracked-files=all"])
    if dirty:
        raise _error("REPOSITORY_NOT_CLEAN")
    payload = _git(
        repo,
        [
            "diff",
            "--name-only",
            "--no-renames",
            "--no-ext-diff",
            "--no-textconv",
            "-z",
            parent_subject,
            target_subject,
            "--",
        ],
    )
    if payload and not payload.endswith(b"\0"):
        raise _error("GIT_DIFF_OUTPUT_INVALID")
    try:
        paths = tuple(
            item.decode("utf-8", errors="strict")
            for item in payload.rstrip(b"\0").split(b"\0")
            if item
        )
    except UnicodeDecodeError as exc:
        raise _error("GIT_DIFF_PATH_INVALID") from exc
    if len(paths) != len(set(paths)):
        raise _error("GIT_DIFF_PATH_DUPLICATE")
    for path in paths:
        _portable_relative(path, label=path)
        if path not in CONTINUATION_DIFF_ALLOWLIST:
            raise _error("GIT_DIFF_PATH_NOT_ALLOWED", path)
    return tuple(sorted(paths, key=str.encode))


def _require_ancestor(repo: Path, ancestor: str, target: str, *, label: str) -> None:
    completed = subprocess.run(
        [
            GIT,
            "-c",
            "core.fsmonitor=false",
            "-C",
            str(repo),
            "merge-base",
            "--is-ancestor",
            ancestor,
            target,
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
        env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
    )
    if completed.returncode != 0:
        raise _error("EVIDENCE_COMMIT_NOT_ANCESTOR", label)


def _walk_directory(
    source_id: str,
    source_root: Path,
    source_relative: str,
    destination_relative: str,
    *,
    excluded: tuple[str, ...] = (),
) -> list[Artifact]:
    source_path = source_root / source_relative
    try:
        metadata = source_path.lstat()
    except OSError as exc:
        raise _error("SOURCE_DIRECTORY_MISSING", source_relative) from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise _error("SOURCE_DIRECTORY_INVALID", source_relative)
    excluded_set = set(excluded)
    records: list[Artifact] = []

    def visit(directory: Path, prefix: PurePosixPath) -> None:
        try:
            with os.scandir(directory) as iterator:
                entries = sorted(iterator, key=lambda item: item.name.encode())
        except OSError as exc:
            raise _error("SOURCE_DIRECTORY_READ_FAILED", str(prefix)) from exc
        for entry in entries:
            relative_inside = prefix / entry.name
            relative_text = relative_inside.as_posix()
            if relative_text in excluded_set or any(
                relative_text.startswith(f"{item}/") for item in excluded_set
            ):
                continue
            try:
                item_metadata = entry.stat(follow_symlinks=False)
            except OSError as exc:
                raise _error("SOURCE_ENTRY_CHANGED", relative_text) from exc
            if stat.S_ISLNK(item_metadata.st_mode):
                raise _error("SOURCE_SYMLINK_FORBIDDEN", relative_text)
            source_file_relative = (
                PurePosixPath(source_relative) / relative_inside
            ).as_posix()
            destination = (
                PurePosixPath(destination_relative) / relative_inside
            ).as_posix()
            if stat.S_ISDIR(item_metadata.st_mode):
                visit(Path(entry.path), relative_inside)
            elif stat.S_ISREG(item_metadata.st_mode):
                records.append(
                    _stable_file_record(
                        source_root,
                        source_file_relative,
                        source_id=source_id,
                        destination=destination,
                    )
                )
            else:
                raise _error("SOURCE_NON_REGULAR_FORBIDDEN", relative_text)

    visit(source_path, PurePosixPath())
    return records


def _inventory_specs(
    source_id: str,
    source_root: Path,
    specs: Sequence[_CopySpec],
) -> list[Artifact]:
    records: list[Artifact] = []
    for spec in specs:
        records.extend(
            _walk_directory(
                source_id,
                source_root,
                spec.source_relative,
                spec.destination_relative,
                excluded=spec.excluded,
            )
        )
    return records


def _failed_prefix_artifacts(correctness_root: Path) -> list[Artifact]:
    records = _inventory_specs(
        "failed-prefix",
        correctness_root,
        tuple(_CopySpec(item, item) for item in FAILED_PREFIX_DIRECTORIES),
    )
    with os.scandir(correctness_root) as iterator:
        top_entries = sorted(iterator, key=lambda item: item.name.encode())
    required_files = set(FAILED_PREFIX_REQUIRED_FILES)
    scala = correctness_root / "scala"
    with os.scandir(scala) as iterator:
        scala_entries = sorted(iterator, key=lambda item: item.name.encode())
    direct_files = [
        entry.name
        for entry in top_entries
        if entry.name.startswith("large-fixture") and entry.name.endswith(".json")
    ]
    direct_files.extend(
        f"scala/{entry.name}"
        for entry in scala_entries
        if entry.name.startswith(("scala-source-", "scala-dependency-"))
    )
    if not required_files.issubset(direct_files):
        raise _error("FAILED_PREFIX_REQUIRED_FILE_MISSING")
    for relative in sorted(set(direct_files), key=str.encode):
        records.append(
            _stable_file_record(
                correctness_root,
                relative,
                source_id="failed-prefix",
                destination=relative,
            )
        )
    if any(
        record.source_relative_path == "contract-validation.json"
        or record.source_relative_path.startswith("scala/jmh-smoke/")
        or record.source_relative_path.startswith("scala/qualification/")
        or record.source_relative_path.startswith(
            "scala/scala-selected-profile-result"
        )
        for record in records
    ):
        raise _error("FAILED_PREFIX_FORBIDDEN_ARTIFACT_SELECTED")
    return records


def _current_scala_bindings(
    repo: Path,
    *,
    parent: ParentFailure,
    scala_source: Path,
) -> dict[str, str]:
    numeric = repo / S1_ROOT
    scala = numeric / "scala"
    plan = numeric / "benchmarks/benchmark-plan.v1.json"
    source_inputs = scala / "source-inputs.v1.json"
    toolchain = scala / "toolchain-lock.v1.json"
    profiles_path = scala / "compiler-profiles.v1.json"
    for path in (plan, source_inputs, toolchain, profiles_path, parent.scala_cli_path):
        if path.is_symlink() or not path.is_file():
            raise _error("SCALA_CURRENT_INPUT_INVALID", str(path))
    plan_sha = _sha256(plan.read_bytes())
    source_sha = _sha256(source_inputs.read_bytes())
    toolchain_sha = _sha256(toolchain.read_bytes())
    profiles = _require_object(
        _strict_json_bytes(profiles_path.read_bytes(), label="compiler-profiles"),
        label="compiler-profiles",
    )
    profile_map = profiles.get("profiles")
    if not isinstance(profile_map, dict):
        raise _error("SCALA_COMPILER_PROFILES_INVALID")
    try:
        options = {
            profile: profile_map[profile]["additionalOptions"]
            for profile in ("A", "B", "C")
        }
    except (KeyError, TypeError) as exc:
        raise _error("SCALA_COMPILER_PROFILES_INVALID") from exc
    options_sha = _canonical_sha256(options)
    scala_cli_sha = _sha256(parent.scala_cli_path.read_bytes())
    qualification_payload = _stable_regular_bytes(
        scala_source, "qualification/scala-profile-qualification.v1.json", label="scala-qualification"
    )
    qualification = _require_object(
        _strict_json_bytes(qualification_payload, label="scala-qualification"),
        label="scala-qualification",
    )
    blocks = qualification.get("blocks")
    if (
        qualification.get("schemaVersion")
        != "s1.4x-scala-profile-qualification-v1"
        or qualification.get("status") != "PASS"
        or qualification.get("benchmarkPlanSha256") != plan_sha
        or qualification.get("sourceInputManifestSha256") != source_sha
        or qualification.get("profileOptionsSha256") != options_sha
        or qualification.get("scalaCliBinarySha256") != scala_cli_sha
        or not isinstance(blocks, list)
        or len(blocks) != 3
        or sum(
            len(block.get("measurements", []))
            for block in blocks
            if isinstance(block, dict)
        )
        != 63
        or sum(
            len(block.get("profileEvidence", []))
            for block in blocks
            if isinstance(block, dict)
        )
        != 9
    ):
        raise _error("SCALA_QUALIFICATION_BINDING_INVALID")
    for block in blocks:
        if not isinstance(block, dict):
            raise _error("SCALA_QUALIFICATION_BINDING_INVALID")
        for profile in block.get("profileEvidence", []):
            if (
                not isinstance(profile, dict)
                or profile.get("caseCount") != 7
                or profile.get("sourceInputManifestSha256") != source_sha
                or profile.get("scalaCliBinarySha256") != scala_cli_sha
            ):
                raise _error("SCALA_QUALIFICATION_PROFILE_INVALID")
    allowlist_payload = _stable_regular_bytes(
        scala_source,
        "jmh-smoke/scala-jvm-argument-allowlist.v1.json",
        label="scala-jvm-allowlist",
    )
    allowlist = _require_object(
        _strict_json_bytes(allowlist_payload, label="scala-jvm-allowlist"),
        label="scala-jvm-allowlist",
    )
    if (
        allowlist.get("schemaVersion")
        != "s1.4x-scala-jvm-argument-allowlist-v1"
        or allowlist.get("status") != "PASS"
        or allowlist.get("benchmarkPlanSha256") != plan_sha
        or allowlist.get("toolchainLockSha256") != toolchain_sha
    ):
        raise _error("SCALA_JMH_ALLOWLIST_BINDING_INVALID")
    return {
        "sourceCommit": SCALA_QUALIFICATION_SOURCE_COMMIT,
        "qualificationArtifactSha256": _sha256(qualification_payload),
        "benchmarkPlanSha256": plan_sha,
        "sourceInputManifestSha256": source_sha,
        "toolchainLockSha256": toolchain_sha,
        "compilerProfilesSha256": _sha256(profiles_path.read_bytes()),
        "profileOptionsSha256": options_sha,
        "scalaCliBinarySha256": scala_cli_sha,
        "jvmArgumentAllowlistSha256": _sha256(allowlist_payload),
    }


def _validate_failed_scala_profiles(
    correctness_root: Path,
    bindings: Mapping[str, str],
) -> None:
    compiler_document = _require_object(
        _strict_json_bytes(
            (
                Path(__file__).resolve().parent.parent
                / "scala/compiler-profiles.v1.json"
            ).read_bytes(),
            label="compiler-profiles",
        ),
        label="compiler-profiles",
    )
    profile_options = compiler_document["profiles"]
    for profile in ("A", "B", "C"):
        relative = f"scala/profiles/{profile}/scala-profile-correctness-result.v1.json"
        document = _require_object(
            _strict_json_bytes(
                _stable_regular_bytes(correctness_root, relative, label=relative),
                label=relative,
            ),
            label=relative,
        )
        expected_option = _canonical_sha256(
            profile_options[profile]["additionalOptions"]
        )
        if (
            document.get("schemaVersion")
            != "s1.4x-scala-profile-correctness-v1"
            or document.get("status") != "PASS"
            or document.get("profileId") != profile
            or document.get("toolchainLockSha256")
            != bindings["toolchainLockSha256"]
            or document.get("sourceInputManifestSha256")
            != bindings["sourceInputManifestSha256"]
            or document.get("scalaCliBinarySha256")
            != bindings["scalaCliBinarySha256"]
            or document.get("profileOptionsSha256") != expected_option
        ):
            raise _error("FAILED_SCALA_PROFILE_BINDING_INVALID", profile)


def _validate_haskell_sources(
    repo: Path,
    static_source: Path,
    profile_source: Path,
) -> dict[str, str]:
    numeric = repo / S1_ROOT
    selected_path = numeric / "haskell/selected-profile.v1.json"
    plan_path = numeric / "benchmarks/benchmark-plan.v1.json"
    selected = _require_object(
        _strict_json_bytes(selected_path.read_bytes(), label="haskell-selected-profile"),
        label="haskell-selected-profile",
    )
    qualification_payload = _stable_regular_bytes(
        profile_source,
        "qualification-final2/qualification-artifact.v1.json",
        label="haskell-qualification",
    )
    qualification = _require_object(
        _strict_json_bytes(qualification_payload, label="haskell-qualification"),
        label="haskell-qualification",
    )
    baseline_payload = _stable_regular_bytes(
        profile_source,
        "baseline/correctness-receipt.v1.json",
        label="haskell-baseline",
    )
    optimized_payload = _stable_regular_bytes(
        profile_source,
        "optimized/correctness-receipt.v1.json",
        label="haskell-optimized",
    )
    baseline = _require_object(
        _strict_json_bytes(baseline_payload, label="haskell-baseline"),
        label="haskell-baseline",
    )
    optimized = _require_object(
        _strict_json_bytes(optimized_payload, label="haskell-optimized"),
        label="haskell-optimized",
    )
    plan_sha = _sha256(plan_path.read_bytes())
    if (
        selected.get("schemaVersion") != "s1.4x-haskell-selected-profile-v1"
        or selected.get("qualificationArtifactSha256")
        != HASKELL_QUALIFICATION_SHA256
        or selected.get("fullCorrectnessSha256")
        != HASKELL_BASELINE_CORRECTNESS_SHA256
        or selected.get("sourceTreeSha256") != HASKELL_SOURCE_TREE_SHA256
        or selected.get("qualificationPlanSha256") != plan_sha
        or _sha256(qualification_payload) != HASKELL_QUALIFICATION_SHA256
        or _sha256(baseline_payload) != HASKELL_BASELINE_CORRECTNESS_SHA256
    ):
        raise _error("HASKELL_SELECTED_PROFILE_BINDING_INVALID")
    if (
        qualification.get("schemaVersion")
        != "s1.4x-haskell-profile-qualification-v1"
        or qualification.get("status") != "PASS"
        or qualification.get("candidateSourceCommit")
        != HASKELL_PROFILE_SOURCE_COMMIT
        or qualification.get("sourceTreeSha256") != HASKELL_SOURCE_TREE_SHA256
        or qualification.get("planSha256") != plan_sha
        or not isinstance(qualification.get("blocks"), list)
        or len(qualification["blocks"]) != 4
        or any(
            not isinstance(block, dict) or len(block.get("profiles", [])) != 2
            for block in qualification["blocks"]
        )
    ):
        raise _error("HASKELL_QUALIFICATION_BINDING_INVALID")
    for document, profile in (
        (baseline, "baseline-o0-fasm"),
        (optimized, "optimized-o2-fasm"),
    ):
        if (
            document.get("schemaVersion") != "s1.4x-haskell-full-correctness-v1"
            or document.get("status") != "PASS"
            or document.get("profileId") != profile
            or document.get("candidateSourceCommit")
            != HASKELL_PROFILE_SOURCE_COMMIT
            or document.get("sourceTreeSha256") != HASKELL_SOURCE_TREE_SHA256
            or document.get("compilerSha256") != selected.get("compilerSha256")
        ):
            raise _error("HASKELL_CORRECTNESS_BINDING_INVALID", profile)
    qualification_text = qualification_payload.decode("utf-8")
    if "host-tools" in qualification_text or "docker-config" in qualification_text:
        raise _error("HASKELL_QUALIFICATION_REFERENCES_EXCLUDED_RUNTIME")
    for relative, schema, status_field in (
        ("format/receipt.json", "s1.4x-haskell-format-evidence-v1", "status"),
        ("hlint/receipt.json", "s1.4x-haskell-hlint-evidence-v1", "status"),
        (
            "module-safety/haskell-module-safety-result.v1.json",
            "s1.4x-haskell-module-safety-result-v1",
            "aggregateStatus",
        ),
    ):
        document = _require_object(
            _strict_json_bytes(
                _stable_regular_bytes(static_source, relative, label=relative),
                label=relative,
            ),
            label=relative,
        )
        if document.get("schemaVersion") != schema or document.get(status_field) != "PASS":
            raise _error("HASKELL_STATIC_EVIDENCE_INVALID", relative)
    return {
        "sourceCommit": HASKELL_PROFILE_SOURCE_COMMIT,
        "sourceTreeSha256": HASKELL_SOURCE_TREE_SHA256,
        "qualificationArtifactSha256": HASKELL_QUALIFICATION_SHA256,
        "baselineCorrectnessSha256": HASKELL_BASELINE_CORRECTNESS_SHA256,
        "optimizedCorrectnessSha256": _sha256(optimized_payload),
        "benchmarkPlanSha256": plan_sha,
        "selectedProfileSha256": _sha256(selected_path.read_bytes()),
    }


def _tree_manifest(
    tree: SourceTree,
    artifacts: Sequence[Artifact],
) -> dict[str, object]:
    entries = [
        {
            "sourceRelativePath": item.source_relative_path,
            "destinationPath": item.destination_path,
            "sha256": item.sha256,
            "sizeBytes": item.size_bytes,
        }
        for item in sorted(artifacts, key=lambda item: item.destination_path.encode())
    ]
    return {
        "sourceId": tree.source_id,
        "sourceRoot": str(tree.source_root),
        "sourceRelativePath": tree.source_relative_path,
        "destinationRelativePath": tree.destination_relative_path,
        "excludedSubtrees": list(tree.excluded_subtrees),
        "bindings": dict(tree.bindings),
        "artifactCount": len(entries),
        "totalSizeBytes": sum(item.size_bytes for item in artifacts),
        "treeSha256": _canonical_sha256(entries),
    }


def snapshot_continuation_sources(
    *,
    repository_root: Path,
    failed_run_root: Path,
    scala_qualification_source: Path,
    haskell_static_source: Path,
    haskell_profile_source: Path,
    target_subject: str,
    output: Path,
) -> dict[str, Any]:
    """모든 source identity와 exact inventory를 검증해 새 manifest에 O_EXCL 봉인한다."""

    repo = _canonical_directory(repository_root, label="repo-root")
    failed_root = _canonical_directory(failed_run_root, label="failed-run-root")
    parent = verify_failed_parent_run(failed_root)
    current_diff = validate_current_diff(
        repo,
        parent_subject=parent.subject,
        target_subject=target_subject,
    )
    _require_ancestor(
        repo,
        SCALA_QUALIFICATION_SOURCE_COMMIT,
        target_subject,
        label="scala-qualification",
    )
    _require_ancestor(
        repo,
        HASKELL_PROFILE_SOURCE_COMMIT,
        target_subject,
        label="haskell-profile",
    )
    failed_correctness = _canonical_directory(
        failed_root / "correctness", label="failed-correctness"
    )
    scala_source = _canonical_directory(
        scala_qualification_source, label="scala-qualification-source"
    )
    haskell_static = _canonical_directory(
        haskell_static_source, label="haskell-static-source"
    )
    haskell_profile = _canonical_directory(
        haskell_profile_source, label="haskell-profile-source"
    )

    scala_bindings = _current_scala_bindings(
        repo, parent=parent, scala_source=scala_source
    )
    _validate_failed_scala_profiles(failed_correctness, scala_bindings)
    haskell_bindings = _validate_haskell_sources(
        repo, haskell_static, haskell_profile
    )

    failed_prefix = [
        Artifact(
            source_id=item.source_id,
            source_root=failed_root,
            source_relative_path=f"correctness/{item.source_relative_path}",
            destination_path=item.destination_path,
            sha256=item.sha256,
            size_bytes=item.size_bytes,
        )
        for item in _failed_prefix_artifacts(failed_correctness)
    ]
    artifacts_by_source: dict[str, list[Artifact]] = {
        "failed-prefix": failed_prefix,
        "scala-qualification": _inventory_specs(
            "scala-qualification",
            scala_source,
            (_CopySpec("qualification", "scala/qualification"),),
        ),
        "scala-jmh-smoke": _inventory_specs(
            "scala-jmh-smoke",
            scala_source,
            (_CopySpec("jmh-smoke", "scala/jmh-smoke"),),
        ),
        "haskell-static": _inventory_specs(
            "haskell-static",
            haskell_static,
            (
                _CopySpec("format", "haskell/format"),
                _CopySpec("hlint", "haskell/hlint"),
                _CopySpec("module-safety", "haskell/module-safety"),
            ),
        ),
        "haskell-baseline": _inventory_specs(
            "haskell-baseline",
            haskell_profile,
            (_CopySpec("baseline", "haskell/profiles/baseline-o0-fasm"),),
        ),
        "haskell-optimized": _inventory_specs(
            "haskell-optimized",
            haskell_profile,
            (_CopySpec("optimized", "haskell/profiles/optimized-o2-fasm"),),
        ),
        "haskell-qualification": _inventory_specs(
            "haskell-qualification",
            haskell_profile,
            (
                _CopySpec(
                    "qualification-final2",
                    "haskell/qualification",
                    excluded=("docker-config", "host-tools"),
                ),
            ),
        ),
    }
    if (
        len(artifacts_by_source["scala-qualification"])
        != SCALA_QUALIFICATION_EXPECTED_FILE_COUNT
    ):
        raise _error("SCALA_QUALIFICATION_FILE_COUNT_INVALID")
    if (
        len(artifacts_by_source["haskell-qualification"])
        != HASKELL_QUALIFICATION_EXPECTED_FILE_COUNT
    ):
        raise _error("HASKELL_QUALIFICATION_FILE_COUNT_INVALID")
    all_artifacts = [
        artifact
        for source_id in sorted(artifacts_by_source, key=str.encode)
        for artifact in artifacts_by_source[source_id]
    ]
    destinations = [item.destination_path for item in all_artifacts]
    if len(destinations) != len(set(destinations)):
        raise _error("DESTINATION_COLLISION")
    if any(
        path in FORBIDDEN_DESTINATIONS
        or path.startswith("contract-validation")
        or path == IMPORT_RECEIPT_NAME
        for path in destinations
    ):
        raise _error("FORBIDDEN_DESTINATION_SELECTED")

    runtime_exclusions = (
        {
            "path": "docker-config",
            "reason": "RUNTIME_SUPPORT_NOT_CONSUMED",
        },
        {
            "path": "host-tools",
            "reason": "RUNTIME_SUPPORT_NOT_CONSUMED",
        },
    )
    trees = (
        SourceTree(
            "failed-prefix",
            failed_root,
            "correctness",
            ".",
            (
                {
                    "path": "contract-validation.json",
                    "reason": "CURRENT_CONTRACT_REVALIDATION_REQUIRED",
                },
                {
                    "path": "scala/jmh-smoke",
                    "reason": "ANCESTOR_QUALIFICATION_PAIR_REQUIRED",
                },
                {
                    "path": "scala/qualification",
                    "reason": "FAILED_PARTIAL_QUALIFICATION_FORBIDDEN",
                },
            ),
            {
                "parentRunId": parent.run_id,
                "parentSubject": parent.subject,
                **scala_bindings,
            },
        ),
        SourceTree(
            "scala-qualification",
            scala_source,
            "qualification",
            "scala/qualification",
            (),
            scala_bindings,
        ),
        SourceTree(
            "scala-jmh-smoke",
            scala_source,
            "jmh-smoke",
            "scala/jmh-smoke",
            (),
            scala_bindings,
        ),
        SourceTree(
            "haskell-static",
            haskell_static,
            "{format,hlint,module-safety}",
            "haskell",
            (
                {
                    "path": "profiles",
                    "reason": "OLD_PARTIAL_PROFILE_FORBIDDEN",
                },
                {
                    "path": "qualification",
                    "reason": "OLD_PARTIAL_QUALIFICATION_FORBIDDEN",
                },
            ),
            {"sourceCommit": SCALA_QUALIFICATION_SOURCE_COMMIT},
        ),
        SourceTree(
            "haskell-baseline",
            haskell_profile,
            "baseline",
            "haskell/profiles/baseline-o0-fasm",
            (),
            haskell_bindings,
        ),
        SourceTree(
            "haskell-optimized",
            haskell_profile,
            "optimized",
            "haskell/profiles/optimized-o2-fasm",
            (),
            haskell_bindings,
        ),
        SourceTree(
            "haskell-qualification",
            haskell_profile,
            "qualification-final2",
            "haskell/qualification",
            runtime_exclusions,
            haskell_bindings,
        ),
    )
    source_tree_documents = [
        _tree_manifest(tree, artifacts_by_source[tree.source_id]) for tree in trees
    ]
    artifact_documents = [
        item.manifest_entry()
        for item in sorted(
            all_artifacts,
            key=lambda item: (item.destination_path.encode(), item.source_id.encode()),
        )
    ]
    manifest = {
        "schemaVersion": SOURCE_MANIFEST_SCHEMA,
        "parentRunId": parent.run_id,
        "parentSubject": parent.subject,
        "targetSubject": target_subject,
        "failedStage": parent.failed_stage,
        "currentDiffPaths": list(current_diff),
        "sourceCommits": {
            "scalaQualification": SCALA_QUALIFICATION_SOURCE_COMMIT,
            "haskellProfile": HASKELL_PROFILE_SOURCE_COMMIT,
        },
        "sourceTrees": source_tree_documents,
        "artifactCount": len(artifact_documents),
        "artifacts": artifact_documents,
        "status": "SEALED",
    }
    payload = _canonical_json_bytes(manifest)
    _write_exclusive(output, payload)
    if _stable_regular_bytes(output.parent, output.name, label="source-manifest") != payload:
        raise _error("SOURCE_MANIFEST_POST_WRITE_DRIFT")
    return manifest


def _validate_source_manifest(
    repo: Path,
    manifest_path: Path,
) -> tuple[dict[str, Any], bytes]:
    parent = _canonical_directory(manifest_path.parent, label="manifest-parent")
    payload = _stable_regular_bytes(parent, manifest_path.name, label="source-manifest")
    manifest = _require_object(
        _strict_json_bytes(payload, label="source-manifest"),
        label="source-manifest",
    )
    expected_fields = {
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
    if (
        set(manifest) != expected_fields
        or manifest.get("schemaVersion") != SOURCE_MANIFEST_SCHEMA
        or manifest.get("failedStage") != FAILED_STAGE
        or manifest.get("status") != "SEALED"
        or payload != _canonical_json_bytes(manifest)
    ):
        raise _error("SOURCE_MANIFEST_INVALID")
    target = manifest.get("targetSubject")
    parent_subject = manifest.get("parentSubject")
    if not isinstance(target, str) or not isinstance(parent_subject, str):
        raise _error("SOURCE_MANIFEST_IDENTITY_INVALID")
    current_diff = validate_current_diff(
        repo,
        parent_subject=parent_subject,
        target_subject=target,
    )
    if manifest.get("currentDiffPaths") != list(current_diff):
        raise _error("SOURCE_MANIFEST_DIFF_DRIFT")
    commits = manifest.get("sourceCommits")
    if commits != {
        "scalaQualification": SCALA_QUALIFICATION_SOURCE_COMMIT,
        "haskellProfile": HASKELL_PROFILE_SOURCE_COMMIT,
    }:
        raise _error("SOURCE_MANIFEST_COMMIT_DRIFT")
    trees = manifest.get("sourceTrees")
    artifacts = manifest.get("artifacts")
    if (
        not isinstance(trees, list)
        or not isinstance(artifacts, list)
        or manifest.get("artifactCount") != len(artifacts)
    ):
        raise _error("SOURCE_MANIFEST_INVENTORY_INVALID")
    tree_by_id: dict[str, dict[str, Any]] = {}
    for tree in trees:
        if not isinstance(tree, dict):
            raise _error("SOURCE_TREE_INVALID")
        source_id = tree.get("sourceId")
        if not isinstance(source_id, str) or source_id in tree_by_id:
            raise _error("SOURCE_TREE_INVALID")
        tree_by_id[source_id] = tree
    grouped: dict[str, list[dict[str, object]]] = {item: [] for item in tree_by_id}
    destinations: set[str] = set()
    for item in artifacts:
        if not isinstance(item, dict) or set(item) != {
            "sourceId",
            "sourceRelativePath",
            "destinationPath",
            "sha256",
            "sizeBytes",
        }:
            raise _error("SOURCE_ARTIFACT_INVALID")
        source_id = item.get("sourceId")
        relative = item.get("sourceRelativePath")
        destination = item.get("destinationPath")
        digest = item.get("sha256")
        size = item.get("sizeBytes")
        if (
            not isinstance(source_id, str)
            or source_id not in tree_by_id
            or not isinstance(relative, str)
            or not isinstance(destination, str)
            or not isinstance(digest, str)
            or SHA256.fullmatch(digest) is None
            or not isinstance(size, int)
            or isinstance(size, bool)
            or size < 0
        ):
            raise _error("SOURCE_ARTIFACT_INVALID")
        _portable_relative(relative, label=relative)
        _portable_relative(destination, label=destination)
        if destination in destinations or destination in FORBIDDEN_DESTINATIONS:
            raise _error("SOURCE_DESTINATION_INVALID", destination)
        destinations.add(destination)
        source_root_value = tree_by_id[source_id].get("sourceRoot")
        if not isinstance(source_root_value, str):
            raise _error("SOURCE_TREE_INVALID")
        source_root = _canonical_directory(Path(source_root_value), label=source_id)
        source_payload = _stable_regular_bytes(source_root, relative, label=relative)
        if len(source_payload) != size or _sha256(source_payload) != digest:
            raise _error("SOURCE_ARTIFACT_DRIFT", destination)
        grouped[source_id].append(
            {
                "sourceRelativePath": relative,
                "destinationPath": destination,
                "sha256": digest,
                "sizeBytes": size,
            }
        )
    for source_id, tree in tree_by_id.items():
        entries = sorted(
            grouped[source_id],
            key=lambda item: str(item["destinationPath"]).encode(),
        )
        total_size = 0
        for entry in entries:
            size = entry["sizeBytes"]
            if isinstance(size, bool) or not isinstance(size, int):
                raise _error("SOURCE_TREE_CLOSURE_DRIFT", source_id)
            total_size += size
        if (
            tree.get("artifactCount") != len(entries)
            or tree.get("totalSizeBytes") != total_size
            or tree.get("treeSha256") != _canonical_sha256(entries)
        ):
            raise _error("SOURCE_TREE_CLOSURE_DRIFT", source_id)
    return manifest, payload


def _copy_artifact(
    *,
    source_root: Path,
    source_relative: str,
    output_root: Path,
    destination: str,
    expected_sha256: str,
    expected_size: int,
) -> None:
    payload = _stable_regular_bytes(source_root, source_relative, label=source_relative)
    if len(payload) != expected_size or _sha256(payload) != expected_sha256:
        raise _error("SOURCE_ARTIFACT_DRIFT", destination)
    destination_path = output_root.joinpath(
        *_portable_relative(destination, label=destination).parts
    )
    current = output_root
    for component in PurePosixPath(destination).parts[:-1]:
        current = current / component
        if current.exists() or current.is_symlink():
            metadata = current.lstat()
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
                raise _error("DESTINATION_PARENT_INVALID", destination)
        else:
            current.mkdir(mode=0o700)
    _write_exclusive(destination_path, payload)
    copied = _stable_regular_bytes(output_root, destination, label=destination)
    if len(copied) != expected_size or _sha256(copied) != expected_sha256:
        raise _error("DESTINATION_COPY_DRIFT", destination)


def _destination_inventory(root: Path, *, exclude_receipt: bool) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []

    def visit(directory: Path, prefix: PurePosixPath) -> None:
        with os.scandir(directory) as iterator:
            entries = sorted(iterator, key=lambda item: item.name.encode())
        for entry in entries:
            relative = (prefix / entry.name).as_posix()
            metadata = entry.stat(follow_symlinks=False)
            if stat.S_ISLNK(metadata.st_mode):
                raise _error("DESTINATION_SYMLINK_FORBIDDEN", relative)
            if stat.S_ISDIR(metadata.st_mode):
                visit(Path(entry.path), prefix / entry.name)
            elif stat.S_ISREG(metadata.st_mode):
                if exclude_receipt and relative == IMPORT_RECEIPT_NAME:
                    continue
                payload = _stable_regular_bytes(root, relative, label=relative)
                records.append(
                    {
                        "destinationPath": relative,
                        "sha256": _sha256(payload),
                        "sizeBytes": len(payload),
                    }
                )
            else:
                raise _error("DESTINATION_NON_REGULAR_FORBIDDEN", relative)

    visit(root, PurePosixPath())
    return records


def import_continuation_prefix(
    *,
    repository_root: Path,
    manifest_path: Path,
    output_root: Path,
) -> dict[str, Any]:
    """sealed inventory를 재검증한 뒤 fresh root에 O_EXCL 복사하고 closure receipt를 쓴다."""

    repo = _canonical_directory(repository_root, label="repo-root")
    manifest, manifest_payload = _validate_source_manifest(repo, manifest_path)
    output_root = _canonical_directory(output_root, label="output-root")
    with os.scandir(output_root) as iterator:
        if next(iterator, None) is not None:
            raise _error("OUTPUT_ROOT_MUST_BE_EMPTY")
    tree_by_id = {
        str(item["sourceId"]): item for item in manifest["sourceTrees"]
    }
    expected_destination: list[dict[str, object]] = []
    for item in manifest["artifacts"]:
        tree = tree_by_id[str(item["sourceId"])]
        source_root = Path(str(tree["sourceRoot"]))
        _copy_artifact(
            source_root=source_root,
            source_relative=str(item["sourceRelativePath"]),
            output_root=output_root,
            destination=str(item["destinationPath"]),
            expected_sha256=str(item["sha256"]),
            expected_size=int(item["sizeBytes"]),
        )
        expected_destination.append(
            {
                "destinationPath": item["destinationPath"],
                "sha256": item["sha256"],
                "sizeBytes": item["sizeBytes"],
            }
        )
    expected_destination.sort(key=lambda item: str(item["destinationPath"]).encode())
    actual = sorted(
        _destination_inventory(output_root, exclude_receipt=False),
        key=lambda item: str(item["destinationPath"]).encode(),
    )
    if actual != expected_destination:
        raise _error("DESTINATION_CLOSURE_MISMATCH")
    receipt = {
        "schemaVersion": IMPORT_RECEIPT_SCHEMA,
        "sourceManifest": {
            "path": str(manifest_path),
            "sha256": _sha256(manifest_payload),
            "sizeBytes": len(manifest_payload),
        },
        "parentRunId": manifest["parentRunId"],
        "parentSubject": manifest["parentSubject"],
        "currentSubject": manifest["targetSubject"],
        "sourceCommits": manifest["sourceCommits"],
        "sourceTrees": manifest["sourceTrees"],
        "importedArtifactCount": len(manifest["artifacts"]),
        "importedArtifacts": manifest["artifacts"],
        "status": "PASS",
    }
    receipt_payload = _canonical_json_bytes(receipt)
    _write_exclusive(output_root / IMPORT_RECEIPT_NAME, receipt_payload)
    if _stable_regular_bytes(
        output_root, IMPORT_RECEIPT_NAME, label=IMPORT_RECEIPT_NAME
    ) != receipt_payload:
        raise _error("IMPORT_RECEIPT_POST_WRITE_DRIFT")
    final = sorted(
        _destination_inventory(output_root, exclude_receipt=True),
        key=lambda item: str(item["destinationPath"]).encode(),
    )
    if final != expected_destination:
        raise _error("DESTINATION_POST_RECEIPT_CLOSURE_MISMATCH")
    return receipt


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    snapshot = subparsers.add_parser("snapshot")
    snapshot.add_argument("--repo-root", type=Path, required=True)
    snapshot.add_argument("--failed-run-root", type=Path, required=True)
    snapshot.add_argument("--scala-qualification-source", type=Path, required=True)
    snapshot.add_argument("--haskell-static-source", type=Path, required=True)
    snapshot.add_argument("--haskell-profile-source", type=Path, required=True)
    snapshot.add_argument("--target-subject", required=True)
    snapshot.add_argument("--output", type=Path, required=True)
    import_parser = subparsers.add_parser("import")
    import_parser.add_argument("--repo-root", type=Path, required=True)
    import_parser.add_argument("--manifest", type=Path, required=True)
    import_parser.add_argument("--output-root", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI 입력만 사용해 snapshot 또는 import를 실행하고 canonical receipt를 출력한다."""

    arguments = _parser().parse_args(argv)
    try:
        if arguments.command == "snapshot":
            result = snapshot_continuation_sources(
                repository_root=arguments.repo_root,
                failed_run_root=arguments.failed_run_root,
                scala_qualification_source=arguments.scala_qualification_source,
                haskell_static_source=arguments.haskell_static_source,
                haskell_profile_source=arguments.haskell_profile_source,
                target_subject=arguments.target_subject,
                output=arguments.output,
            )
        else:
            result = import_continuation_prefix(
                repository_root=arguments.repo_root,
                manifest_path=arguments.manifest,
                output_root=arguments.output_root,
            )
    except (ContinuationPrefixError, OSError, UnicodeError, ValueError) as exc:
        print(f"S1_4X_CONTINUATION_PREFIX_FAIL:{exc}", file=sys.stderr)
        return 2
    sys.stdout.buffer.write(_canonical_json_bytes(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
