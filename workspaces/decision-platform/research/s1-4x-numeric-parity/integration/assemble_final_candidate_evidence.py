#!/usr/bin/env python3
"""한 correctness run의 immutable raw closure를 최종 후보 감사 evidence로 투영한다."""

from __future__ import annotations

import argparse
import ctypes
import datetime as dt
import errno
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import coverage_gate
import final_candidate_audit as final_audit
from gate import strict_json_load

CANDIDATES = final_audit.CANDIDATES
COMMIT = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
RUN_MANIFEST = Path("correctness-run-manifest.v1.json")
RUN_MANIFEST_SCHEMA = "s1.4x-correctness-run-manifest-v1"
ASSEMBLY_SCHEMA = "s1.4x-final-candidate-evidence-assembly-v1"
S1_4X_RELATIVE = Path(
    "workspaces/decision-platform/research/s1-4x-numeric-parity"
)

DESELECTED_RESEARCH_NODE = (
    "tests/test_production_isolation.py::"
    "test_branch_diff_is_confined_to_the_research_project_and_two_workflows"
)
REPLACEMENT_RESEARCH_NODES = (
    "workspaces/decision-platform/research/s1-4x-numeric-parity/"
    "integration/tests/test_s1_4r_regression_boundary.py::"
    "test_s1_4x_branch_diff_is_confined_to_the_experiment_boundary",
    "workspaces/decision-platform/research/s1-4x-numeric-parity/"
    "integration/tests/test_s1_4r_regression_boundary.py::"
    "test_aggregate_deselects_only_the_inapplicable_s1_4r_branch_scope",
)

RAW_PATHS = {
    "coverage": Path("coverage/integration-coverage.json"),
    "canonical-comparison": Path(
        "cross-language/canonical/comparison-report.json"
    ),
    "semantic-comparison": Path(
        "cross-language/semantic/comparison-report.json"
    ),
    "selected-comparison": Path("cross-language/selected-comparison.json"),
    "contract-validation": Path("contract-validation.json"),
    "large-fixture": Path("large-fixture-receipt.json"),
    "large-fixture-replay": Path("large-fixture-check-receipt.json"),
    "oci-cross-comparison": Path("oci/cross-language-comparison.json"),
    "scala-selected-profile": Path(
        "scala/scala-selected-profile-result.v1.json"
    ),
    "scala-source-policy": Path(
        "scala/scala-source-policy-result.v1.json"
    ),
    "scala-dependency-edge": Path(
        "scala/scala-dependency-edge-result.v1.json"
    ),
    "scala-format": Path(
        "scala/scalafmt/scala-scalafmt-idempotence-result.v1.json"
    ),
    "scala-lint": Path(
        "scala/scalafix/scala-semantic-policy-receipt.v1.json"
    ),
    "scala-oci-build": Path(
        "oci/scala/scala-oci-build-result.v1.json"
    ),
    "scala-oci-runtime": Path(
        "oci/scala/runtime/scala-oci-correctness-result.v1.json"
    ),
    "haskell-module-safety": Path(
        "haskell/module-safety/haskell-module-safety-result.v1.json"
    ),
    "haskell-format": Path("haskell/format/receipt.json"),
    "haskell-lint": Path("haskell/hlint/receipt.json"),
    "haskell-oci": Path("oci/haskell/oci-correctness-receipt.v1.json"),
}

REPOSITORY_PATHS = {
    "contract-manifest": S1_4X_RELATIVE
    / "contract/contract-manifest.v1.json",
    "reference-lock": S1_4X_RELATIVE / "contract/reference-lock.v1.json",
    "toolchain-provenance": S1_4X_RELATIVE
    / "contract/toolchain-provenance.v1.json",
    "canonical-inputs": S1_4X_RELATIVE
    / "contract/fixtures/small/canonical-inputs.v1.json",
    "property-seeds": S1_4X_RELATIVE
    / "contract/fixtures/property/property-seeds.v1.json",
    "canonical-results": S1_4X_RELATIVE
    / "contract/fixtures/expected/canonical-results.v1.json",
    "semantic-inputs": S1_4X_RELATIVE
    / "contract/fixtures/invalid/semantic-errors.v1.json",
    "semantic-results": S1_4X_RELATIVE
    / "contract/fixtures/invalid/semantic-errors.expected.v1.json",
    "benchmark-plan": S1_4X_RELATIVE / "benchmarks/benchmark-plan.v1.json",
    "scala-toolchain-lock": S1_4X_RELATIVE / "scala/toolchain-lock.v1.json",
    "haskell-toolchain-lock": S1_4X_RELATIVE
    / "haskell/toolchain-lock.v1.json",
    "haskell-selected-profile": S1_4X_RELATIVE
    / "haskell/selected-profile.v1.json",
    "scala-source-inputs": S1_4X_RELATIVE / "scala/source-inputs.v1.json",
    "scala-selected-source": S1_4X_RELATIVE / "scala/selected-profile.scala",
    "scala-compiler-profiles": S1_4X_RELATIVE
    / "scala/compiler-profiles.v1.json",
    "scala-containerfile": S1_4X_RELATIVE / "scala/Containerfile",
    "haskell-source-inputs": S1_4X_RELATIVE / "haskell/source-inputs.v1.json",
    "haskell-containerfile": S1_4X_RELATIVE / "haskell/Containerfile",
}

LARGE_FIXTURE_GENERATOR_SHA256 = (
    "4e19845c1d1d030dbab3f40527745c3f7803062958b38c3441937ff1674e9d00"
)
LARGE_FIXTURES = (
    (
        "large/large-coverage-forecast-var-n3200000.manifest.json",
        688,
        "f4c2eeab713a948bfd645dcd43457c0a90c38340f4e66043a8a622f452797142",
        "large/generated/large-coverage-forecast-var-n3200000.f64le",
        25_600_000,
        "e5e635b28e4025bc1fa71f7c6b92fbf3807861814d3b6010a751ea0e81168d14",
    ),
    (
        "large/large-coverage-realized-losses-n3200000.manifest.json",
        696,
        "68b5c6c8e2eb5f502e7297ffdf63b3b635cce9131e27e37dfd1fb578a5e784b8",
        "large/generated/large-coverage-realized-losses-n3200000.f64le",
        25_600_000,
        "a9bf46f0f836e4fe386723ac517f6caba2ddf31289113ef38a6b6da3fed29139",
    ),
    (
        "large/large-prices-n100000.manifest.json",
        648,
        "778abae4d621653b448a40b2b854cdf0f2e6fc63b7f439bdde96aaba9b83e7b5",
        "large/generated/large-prices-n100000.f64le",
        800_000,
        "a37153a538130dc2118e4f2c8029a5e4becabd3272d964308bc3200232049c12",
    ),
    (
        "large/large-returns-n100000.manifest.json",
        651,
        "10000aaf12ae80ba5d813ebf3012753d142088df19742e90a52467ca2c93f99a",
        "large/generated/large-returns-n100000.f64le",
        800_000,
        "f81251d60ae5c411ef8eb5df83524375c53af411566060e497c2d6cf86988554",
    ),
)

HASKELL_CANDIDATE_ROOTS = ("src", "app", "test", "benchmark")
PROPERTY_EVIDENCE_FILES = {
    "scala": (
        Path("coverage/scala/scala-property-report.v1.json"),
        Path("coverage/scala/scala-registry-report.v1.json"),
        Path("coverage/scala/scala-property-execution-evidence.v1.json"),
        Path("coverage/scala-coverage-receipt.json"),
    ),
    "haskell": (
        Path("coverage/haskell/haskell-property-report.v1.json"),
        Path("coverage/haskell/haskell-registry-report.v1.json"),
        Path(
            "coverage/haskell/"
            "haskell-property-execution-evidence.v1.json"
        ),
        Path("coverage/haskell-coverage-receipt.json"),
    ),
}
PROPERTY_RUNNERS = {
    "scala": S1_4X_RELATIVE / "scala/tools/run-property-evidence.sh",
    "haskell": S1_4X_RELATIVE / "haskell/tools/run-property-evidence.sh",
}
HASKELL_CABAL_PROVENANCE = Path(
    "coverage/haskell/haskell-generated-cabal-provenance.v1.json"
)
HASKELL_GENERATED_CABAL = Path(
    "coverage/haskell/generated/s1-4x-haskell.cabal"
)
HASKELL_GHC_OPTION_ARGPARSE_FAILURE = (
    b"haskell_evidence.py generated-cabal-provenance: error: argument "
    b"--ghc-option: expected one argument\n"
)
PROPERTY_FROZEN_INPUTS = {
    "property-plan": S1_4X_RELATIVE / "contract/property-plan.v1.json",
    "property-seeds": S1_4X_RELATIVE
    / "contract/fixtures/property/property-seeds.v1.json",
    "function-registry": S1_4X_RELATIVE
    / "contract/function-registry.v1.json",
    "error-registry": S1_4X_RELATIVE / "contract/error-registry.v1.json",
}
HASKELL_SOURCE_TREE_INPUTS = (
    "package.yaml",
    "s1-4x-haskell.cabal",
    "stack.yaml",
    "stack.yaml.lock",
    ".hlint.yaml",
    "Containerfile",
    "ghc-compatibility-solve-failure.v1.json",
    "lint-exceptions.v1.json",
    "stack-ghc-9.14.1.yaml",
    "stack-ghc-9.14.1.yaml.lock",
    "stylish-ghc2024-fallback.v1.json",
    "toolchain-lock.v1.json",
    "tools/assert-toolchain.sh",
    "tools/check-format.sh",
    "tools/check-hlint.sh",
    "tools/compatibility_evidence.py",
    "tools/fixtures/hlint-negative.v1.json",
    "tools/fixtures/hlint/aliased-from-left.hs",
    "tools/fixtures/hlint/aliased-from-right.hs",
    "tools/fixtures/hlint/core-system-io.hs",
    "tools/fixtures/hlint/debug-trace.hs",
    "tools/fixtures/hlint/forbidden-deriving.hs",
    "tools/fixtures/hlint/forbidden-extension.hs",
    "tools/fixtures/hlint/foreign-interface.hs",
    "tools/fixtures/hlint/partial-and-unsafe.hs",
    "tools/fixtures/hlint/qualified-from-just.hs",
    "tools/fixtures/hlint/qualified-throw-io.hs",
    "tools/fixtures/hlint/qualified-throw.hs",
    "tools/fixtures/hlint/unchecked-folds.hs",
    "tools/fixtures/hlint/unsafe-module.hs",
    "tools/fixtures/hlint/unsafe-modules.hs",
    "tools/fixtures/process/large/unicode-digit-path.manifest.json",
    "tools/fixtures/process/large/unicode-digit-sha.manifest.json",
    "tools/fixtures/stylish/misformatted.hs",
    "tools/haskell_benchmark_block.py",
    "tools/haskell_evidence.py",
    "tools/hlint_inventory.py",
    "tools/profile_workflow.py",
    "tools/python-runtime.sh",
    "tools/run-benchmark-block.sh",
    "tools/run-candidate.sh",
    "tools/run-correctness-profile.sh",
    "tools/run-ghc-9.14.1-compatibility.sh",
    "tools/run-oci-correctness.sh",
    "tools/run-profile-qualification.sh",
    "tools/run-property-evidence.sh",
    "tools/select-proven-profile.sh",
    "tools/stylish_fallback.py",
    "tools/validate-ghc-9.14.1-compatibility.sh",
)


class EvidenceAssemblyError(ValueError):
    """Raw closure 또는 projection이 최종 감사의 fail-closed 계약을 위반했다."""


@dataclass(frozen=True)
class Snapshot:
    """검증 시점과 출력 시점 사이에 바뀌지 않는 regular-file snapshot이다."""

    relative_path: Path
    payload: bytes
    sha256: str

    @property
    def size_bytes(self) -> int:
        return len(self.payload)


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
        raise EvidenceAssemblyError("ASSEMBLY_JSON_INVALID") from exc


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _is_exact_int(value: Any, expected: int) -> bool:
    return type(value) is int and value == expected


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and SHA256.fullmatch(value) is not None


def _require_object(
    value: Any,
    *,
    required: set[str],
    label: str,
    exact: bool = False,
) -> dict[str, Any]:
    if (
        not isinstance(value, dict)
        or not required.issubset(value)
        or (exact and set(value) != required)
    ):
        raise EvidenceAssemblyError(f"{label}_OBJECT_INVALID")
    return value


def _portable_relative_path(value: Any, *, label: str) -> Path:
    try:
        relative = final_audit._portable_relative_file(  # noqa: SLF001
            value,
            error=f"{label}_PATH_INVALID",
        )
    except final_audit.FinalAuditError as exc:
        raise EvidenceAssemblyError(f"{label}_PATH_INVALID") from exc
    return relative


def _absolute_canonical_directory(path: Path, *, label: str) -> Path:
    if not path.is_absolute() or path.is_symlink():
        raise EvidenceAssemblyError(f"{label}_INVALID")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise EvidenceAssemblyError(f"{label}_INVALID") from exc
    if resolved != path or not stat.S_ISDIR(path.stat(follow_symlinks=False).st_mode):
        raise EvidenceAssemblyError(f"{label}_INVALID")
    return resolved


def _new_absolute_directory(path: Path, *, label: str) -> Path:
    if (
        not path.is_absolute()
        or path.exists()
        or path.is_symlink()
        or path.name in {"", ".", ".."}
    ):
        raise EvidenceAssemblyError(f"{label}_INVALID")
    try:
        parent = path.parent.resolve(strict=True)
    except OSError as exc:
        raise EvidenceAssemblyError(f"{label}_INVALID") from exc
    if parent != path.parent or parent.is_symlink():
        raise EvidenceAssemblyError(f"{label}_INVALID")
    return path


def _git(
    repository: Path,
    *arguments: str,
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["/usr/bin/git", "-c", "core.fsmonitor=false", *arguments],
        cwd=repository,
        capture_output=True,
        check=False,
        timeout=10,
    )


def _validate_repository(repository: Path, subject: str) -> None:
    if COMMIT.fullmatch(subject) is None:
        raise EvidenceAssemblyError("ASSEMBLY_SUBJECT_INVALID")
    top = _git(repository, "rev-parse", "--show-toplevel")
    head = _git(repository, "rev-parse", "--verify", "HEAD")
    clean = _git(
        repository,
        "status",
        "--porcelain=v1",
        "-z",
        "--untracked-files=all",
    )
    try:
        actual_top = Path(top.stdout.rstrip(b"\n").decode("utf-8"))
        actual_head = head.stdout.strip().decode("ascii")
    except (UnicodeDecodeError, ValueError) as exc:
        raise EvidenceAssemblyError("ASSEMBLY_SUBJECT_INVALID") from exc
    if (
        top.returncode != 0
        or actual_top != repository
        or head.returncode != 0
        or actual_head != subject
        or clean.returncode != 0
        or clean.stdout
    ):
        raise EvidenceAssemblyError("ASSEMBLY_SUBJECT_INVALID")


def _directory_open_flags() -> int:
    no_follow = getattr(os, "O_NOFOLLOW", None)
    directory = getattr(os, "O_DIRECTORY", None)
    if not isinstance(no_follow, int) or not isinstance(directory, int):
        raise EvidenceAssemblyError("RAW_CLOSURE_NOFOLLOW_UNSUPPORTED")
    return os.O_RDONLY | directory | no_follow | os.O_CLOEXEC


def _walk_regular_files(root_fd: int) -> tuple[Path, ...]:
    paths: list[Path] = []

    def visit(directory_fd: int, prefix: Path) -> None:
        try:
            entries = sorted(
                os.scandir(directory_fd),
                key=lambda entry: entry.name.encode("utf-8"),
            )
        except (OSError, UnicodeError) as exc:
            raise EvidenceAssemblyError("RAW_CLOSURE_WALK_FAILED") from exc
        try:
            for entry in entries:
                relative = prefix / entry.name
                if entry.is_symlink():
                    raise EvidenceAssemblyError(
                        f"RAW_CLOSURE_SYMLINK_FORBIDDEN:{relative.as_posix()}"
                    )
                try:
                    if entry.is_dir(follow_symlinks=False):
                        child_fd = os.open(
                            entry.name,
                            _directory_open_flags(),
                            dir_fd=directory_fd,
                        )
                        try:
                            visit(child_fd, relative)
                        finally:
                            os.close(child_fd)
                    elif entry.is_file(follow_symlinks=False):
                        _portable_relative_path(
                            relative.as_posix(),
                            label="RAW_CLOSURE",
                        )
                        paths.append(relative)
                    else:
                        raise EvidenceAssemblyError(
                            f"RAW_CLOSURE_NON_REGULAR:{relative.as_posix()}"
                        )
                except OSError as exc:
                    raise EvidenceAssemblyError(
                        f"RAW_CLOSURE_WALK_FAILED:{relative.as_posix()}"
                    ) from exc
        finally:
            for entry in entries:
                del entry

    visit(root_fd, Path())
    return tuple(sorted(paths, key=lambda value: value.as_posix().encode()))


def _directory_identity(path: Path, *, label: str) -> tuple[int, int]:
    try:
        metadata = path.stat(follow_symlinks=False)
    except OSError as exc:
        raise EvidenceAssemblyError(f"{label}_INVALID") from exc
    if not stat.S_ISDIR(metadata.st_mode) or path.is_symlink():
        raise EvidenceAssemblyError(f"{label}_INVALID")
    return metadata.st_dev, metadata.st_ino


def _read_snapshot(root_fd: int, relative: Path, *, label: str) -> Snapshot:
    portable = _portable_relative_path(relative.as_posix(), label=label)
    directory_fds: list[int] = []
    descriptor: int | None = None
    try:
        directory_fd = os.dup(root_fd)
        directory_fds.append(directory_fd)
        for component in portable.parts[:-1]:
            directory_fd = os.open(
                component,
                _directory_open_flags(),
                dir_fd=directory_fd,
            )
            directory_fds.append(directory_fd)
        no_follow = getattr(os, "O_NOFOLLOW", None)
        if no_follow is None:
            raise EvidenceAssemblyError(f"{label}_INVALID")
        descriptor = os.open(
            portable.name,
            os.O_RDONLY | no_follow | os.O_CLOEXEC,
            dir_fd=directory_fd,
        )
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise EvidenceAssemblyError(f"{label}_INVALID")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(descriptor)
        identity_before = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        )
        identity_after = (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        )
        payload = b"".join(chunks)
        if identity_before != identity_after or len(payload) != after.st_size:
            raise EvidenceAssemblyError(f"{label}_INVALID")
    except (EvidenceAssemblyError, OSError) as exc:
        raise EvidenceAssemblyError(f"{label}_INVALID") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        for directory_fd in reversed(directory_fds):
            os.close(directory_fd)
    return Snapshot(
        relative_path=portable,
        payload=payload,
        sha256=_sha256_bytes(payload),
    )


class RawClosure:
    """Run manifest와 실제 regular-file tree가 정확히 같은 raw snapshot 집합이다."""

    def __init__(self, root: Path, *, subject: str) -> None:
        self.root_fd = -1
        self.parent_fd = -1
        self.root = _absolute_canonical_directory(
            root,
            label="CORRECTNESS_ROOT",
        )
        self.root_identity = _directory_identity(
            self.root,
            label="CORRECTNESS_ROOT",
        )
        try:
            self.root_fd = os.open(self.root, _directory_open_flags())
            self.parent_fd = os.open(
                self.root.parent,
                _directory_open_flags(),
            )
        except OSError as exc:
            self.close()
            raise EvidenceAssemblyError("CORRECTNESS_ROOT_INVALID") from exc
        opened = os.fstat(self.root_fd)
        parent = os.fstat(self.parent_fd)
        self.parent_identity = (
            parent.st_dev,
            parent.st_ino,
            parent.st_mtime_ns,
            parent.st_ctime_ns,
        )
        if (
            not stat.S_ISDIR(opened.st_mode)
            or (opened.st_dev, opened.st_ino) != self.root_identity
        ):
            os.close(self.root_fd)
            self.root_fd = -1
            raise EvidenceAssemblyError("CORRECTNESS_ROOT_INVALID")
        inventory = _walk_regular_files(self.root_fd)
        if RUN_MANIFEST not in inventory:
            raise EvidenceAssemblyError("RAW_RUN_MANIFEST_MISSING")
        manifest_snapshot = _read_snapshot(
            self.root_fd,
            RUN_MANIFEST,
            label="RAW_RUN_MANIFEST",
        )
        manifest = _require_object(
            strict_json_load(manifest_snapshot.payload),
            required={
                "schemaVersion",
                "benchmarkSubjectCommit",
                "artifactCount",
                "artifacts",
                "status",
            },
            label="RAW_RUN_MANIFEST",
            exact=True,
        )
        if (
            manifest["schemaVersion"] != RUN_MANIFEST_SCHEMA
            or manifest["benchmarkSubjectCommit"] != subject
            or manifest["status"] != "PASS"
            or not isinstance(manifest["artifacts"], list)
            or not _is_exact_int(
                manifest["artifactCount"],
                len(manifest["artifacts"]),
            )
            or manifest_snapshot.payload != _canonical_json_bytes(manifest)
        ):
            raise EvidenceAssemblyError("RAW_RUN_MANIFEST_INVALID")
        expected_paths = tuple(
            path for path in inventory if path != RUN_MANIFEST
        )
        snapshots: dict[Path, Snapshot] = {}
        listed_paths: list[Path] = []
        for raw_entry in manifest["artifacts"]:
            entry = _require_object(
                raw_entry,
                required={"path", "sha256", "sizeBytes"},
                label="RAW_RUN_MANIFEST_ENTRY",
                exact=True,
            )
            relative = _portable_relative_path(
                entry["path"],
                label="RAW_RUN_MANIFEST_ENTRY",
            )
            if (
                relative == RUN_MANIFEST
                or relative in snapshots
                or not _is_sha256(entry["sha256"])
                or type(entry["sizeBytes"]) is not int
                or entry["sizeBytes"] < 0
            ):
                raise EvidenceAssemblyError("RAW_RUN_MANIFEST_ENTRY_INVALID")
            snapshot = _read_snapshot(
                self.root_fd,
                relative,
                label="RAW_RUN_MANIFEST_ENTRY",
            )
            if (
                snapshot.sha256 != entry["sha256"]
                or snapshot.size_bytes != entry["sizeBytes"]
            ):
                raise EvidenceAssemblyError("RAW_RUN_MANIFEST_ENTRY_INVALID")
            snapshots[relative] = snapshot
            listed_paths.append(relative)
        if (
            tuple(listed_paths) != expected_paths
            or tuple(sorted(listed_paths, key=lambda item: item.as_posix().encode()))
            != tuple(listed_paths)
        ):
            raise EvidenceAssemblyError("RAW_RUN_MANIFEST_CLOSURE_INVALID")
        self.manifest = manifest
        self.snapshots = snapshots

    def close(self) -> None:
        """Pinned raw directory descriptor를 반복 호출에도 안전하게 닫는다."""

        if self.root_fd >= 0:
            os.close(self.root_fd)
            self.root_fd = -1
        if self.parent_fd >= 0:
            os.close(self.parent_fd)
            self.parent_fd = -1

    def __del__(self) -> None:
        self.close()

    def snapshot(self, relative: Path, *, label: str) -> Snapshot:
        portable = _portable_relative_path(
            relative.as_posix(),
            label=label,
        )
        try:
            return self.snapshots[portable]
        except KeyError as exc:
            raise EvidenceAssemblyError(f"{label}_MISSING") from exc

    def json(self, relative: Path, *, label: str) -> dict[str, Any]:
        snapshot = self.snapshot(relative, label=label)
        try:
            value = strict_json_load(snapshot.payload)
        except (ValueError, UnicodeError) as exc:
            raise EvidenceAssemblyError(f"{label}_JSON_INVALID") from exc
        return _require_object(
            value,
            required=set(),
            label=label,
        )

    def verify_unchanged(self) -> None:
        """Sealed raw root, inventory, bytes가 조립 중 바뀌지 않았는지 재확인한다."""

        if (
            self.root_fd < 0
            or self.parent_fd < 0
            or (
                os.fstat(self.root_fd).st_dev,
                os.fstat(self.root_fd).st_ino,
            )
            != self.root_identity
            or (
                os.fstat(self.parent_fd).st_dev,
                os.fstat(self.parent_fd).st_ino,
                os.fstat(self.parent_fd).st_mtime_ns,
                os.fstat(self.parent_fd).st_ctime_ns,
            )
            != self.parent_identity
            or
            _directory_identity(
                self.root,
                label="RAW_CLOSURE_RECHECK",
            )
            != self.root_identity
        ):
            raise EvidenceAssemblyError("RAW_CLOSURE_CHANGED")
        expected_inventory = tuple(
            sorted(
                (RUN_MANIFEST, *self.snapshots),
                key=lambda value: value.as_posix().encode(),
            )
        )
        if _walk_regular_files(self.root_fd) != expected_inventory:
            raise EvidenceAssemblyError("RAW_CLOSURE_CHANGED")
        manifest = _read_snapshot(
            self.root_fd,
            RUN_MANIFEST,
            label="RAW_RUN_MANIFEST_RECHECK",
        )
        if manifest.payload != _canonical_json_bytes(self.manifest):
            raise EvidenceAssemblyError("RAW_CLOSURE_CHANGED")
        for relative, expected in self.snapshots.items():
            actual = _read_snapshot(
                self.root_fd,
                relative,
                label="RAW_CLOSURE_RECHECK",
            )
            if (
                actual.sha256 != expected.sha256
                or actual.size_bytes != expected.size_bytes
            ):
                raise EvidenceAssemblyError("RAW_CLOSURE_CHANGED")


def _git_blob_snapshot(
    repository: Path,
    subject: str,
    relative: Path,
    *,
    label: str,
) -> Snapshot:
    portable = _portable_relative_path(relative.as_posix(), label=label)
    entry = _git(
        repository,
        "ls-tree",
        "-z",
        subject,
        "--",
        portable.as_posix(),
    )
    suffix = b"\t" + portable.as_posix().encode("utf-8") + b"\0"
    if (
        entry.returncode != 0
        or not entry.stdout.endswith(suffix)
        or entry.stdout.count(b"\0") != 1
    ):
        raise EvidenceAssemblyError(f"{label}_INVALID")
    metadata = entry.stdout[: -len(suffix)]
    try:
        mode, object_type, raw_object = metadata.split(b" ", 2)
    except ValueError as exc:
        raise EvidenceAssemblyError(f"{label}_INVALID") from exc
    # 실행 스크립트도 정상 Git blob이므로 regular-file mode 두 가지만 허용한다.
    if mode not in {b"100644", b"100755"} or object_type != b"blob":
        raise EvidenceAssemblyError(f"{label}_INVALID")
    if re.fullmatch(rb"[0-9a-f]{40,64}", raw_object) is None:
        raise EvidenceAssemblyError(f"{label}_INVALID")
    shown = _git(
        repository,
        "show",
        f"{subject}:{portable.as_posix()}",
    )
    if shown.returncode != 0 or shown.stderr:
        raise EvidenceAssemblyError(f"{label}_INVALID")
    return Snapshot(
        relative_path=portable,
        payload=shown.stdout,
        sha256=_sha256_bytes(shown.stdout),
    )


def _repository_snapshots(
    repository: Path,
    subject: str,
) -> dict[str, Snapshot]:
    snapshots: dict[str, Snapshot] = {}
    for label, relative in REPOSITORY_PATHS.items():
        snapshots[label] = _git_blob_snapshot(
            repository,
            subject,
            relative,
            label=f"REPOSITORY_ARTIFACT:{label}",
        )
    return snapshots


def _json_snapshot(snapshot: Snapshot, *, label: str) -> dict[str, Any]:
    try:
        value = strict_json_load(snapshot.payload)
    except (ValueError, UnicodeError) as exc:
        raise EvidenceAssemblyError(f"{label}_JSON_INVALID") from exc
    return _require_object(value, required=set(), label=label)


def _load_coverage(closure: RawClosure) -> tuple[Snapshot, dict[str, Any]]:
    snapshot = closure.snapshot(RAW_PATHS["coverage"], label="COVERAGE")
    document = _json_snapshot(snapshot, label="COVERAGE")
    try:
        for candidate in CANDIDATES:
            final_audit._coverage_candidate(  # noqa: SLF001
                document,
                candidate=candidate,
                error="COVERAGE_INVALID",
            )
    except final_audit.FinalAuditError as exc:
        raise EvidenceAssemblyError("COVERAGE_INVALID") from exc
    return snapshot, document


def _validate_comparison(
    closure: RawClosure,
    path: Path,
    *,
    label: str,
    expected_request_id: str,
) -> tuple[Snapshot, dict[str, Any]]:
    snapshot = closure.snapshot(path, label=label)
    document = _json_snapshot(snapshot, label=label)
    try:
        final_audit._validate_comparison_source(  # noqa: SLF001
            document,
            error=f"{label}_INVALID",
        )
    except final_audit.FinalAuditError as exc:
        raise EvidenceAssemblyError(f"{label}_INVALID") from exc
    if document.get("requestId") != expected_request_id:
        raise EvidenceAssemblyError(f"{label}_REQUEST_ID_INVALID")
    return snapshot, document


def _validate_native_cross_language(
    closure: RawClosure,
    repository: Mapping[str, Snapshot],
    toolchains: Mapping[str, Mapping[str, Any]],
) -> tuple[Snapshot, dict[str, Any], Snapshot, dict[str, Any]]:
    """Aggregate cross-language evidence를 selected candidate bytes에 exact 결합한다."""

    validated: dict[str, tuple[Snapshot, dict[str, Any]]] = {}
    for matrix, request_id, expected_label, scala_name, haskell_name in (
        (
            "canonical",
            "s1.4x-canonical-small-v1",
            "canonical-results",
            "canonical-results.json",
            "canonical.actual.json",
        ),
        (
            "semantic",
            "s1.4x-semantic-errors-v1",
            "semantic-results",
            "semantic-errors.json",
            "semantic.actual.json",
        ),
    ):
        root = Path("cross-language") / matrix
        comparison, document = _validate_comparison(
            closure,
            root / "comparison-report.json",
            label=f"{matrix.upper()}_COMPARISON",
            expected_request_id=request_id,
        )
        reference = closure.snapshot(
            root / "reference-capture.json",
            label=f"{matrix.upper()}_REFERENCE_CAPTURE",
        )
        reference_document = _json_snapshot(
            reference,
            label=f"{matrix.upper()}_REFERENCE_CAPTURE",
        )
        scala = closure.snapshot(
            root / "scala-results.json",
            label=f"{matrix.upper()}_SCALA_RESULT",
        )
        haskell = closure.snapshot(
            root / "haskell-results.json",
            label=f"{matrix.upper()}_HASKELL_RESULT",
        )
        scala_document = _json_snapshot(
            scala,
            label=f"{matrix.upper()}_SCALA_RESULT",
        )
        haskell_document = _json_snapshot(
            haskell,
            label=f"{matrix.upper()}_HASKELL_RESULT",
        )
        summary = closure.snapshot(
            root / "correctness-summary.json",
            label=f"{matrix.upper()}_CORRECTNESS_SUMMARY",
        )
        summary_document = _json_snapshot(
            summary,
            label=f"{matrix.upper()}_CORRECTNESS_SUMMARY",
        )
        scala_selected = closure.snapshot(
            Path(
                f"scala/profiles/{toolchains['scala']['profileId']}/"
                f"{scala_name}"
            ),
            label=f"{matrix.upper()}_SCALA_SELECTED_RESULT",
        )
        haskell_selected = closure.snapshot(
            Path(
                f"haskell/profiles/{toolchains['haskell']['profileId']}/"
                f"{haskell_name}"
            ),
            label=f"{matrix.upper()}_HASKELL_SELECTED_RESULT",
        )
        expected_artifacts = {
            "reference-capture.json": reference.sha256,
            "scala-results.json": scala.sha256,
            "haskell-results.json": haskell.sha256,
            "comparison-report.json": comparison.sha256,
        }
        if (
            set(reference_document)
            != {
                "schemaVersion",
                "uvVersion",
                "processCount",
                "projects",
                "resultSha256",
                "status",
            }
            or reference_document.get("schemaVersion")
            != "s1.4x-reference-capture-report-v1"
            or reference_document.get("processCount") != 2
            or not isinstance(reference_document.get("uvVersion"), str)
            or not reference_document["uvVersion"]
            or not isinstance(reference_document.get("projects"), list)
            or len(reference_document["projects"]) != 2
            or reference_document.get("resultSha256")
            != repository[expected_label].sha256
            or reference_document.get("status") != "PASS"
            or reference.payload != _canonical_json_bytes(reference_document)
            or scala.sha256 != scala_selected.sha256
            or haskell.sha256 != haskell_selected.sha256
            or scala_document.get("requestId") != request_id
            or scala_document.get("implementation") != "scala-3.8.4-jvm25"
            or haskell_document.get("requestId") != request_id
            or haskell_document.get("implementation")
            != "haskell-ghc-9.10.3"
            or set(summary_document)
            != {
                "schemaVersion",
                "requestId",
                "oracleImplementation",
                "candidateImplementations",
                "caseCount",
                "mismatchCount",
                "artifacts",
                "referenceCaptureStatus",
                "status",
            }
            or summary_document.get("schemaVersion")
            != "s1.4x-integration-correctness-v1"
            or summary_document.get("requestId") != request_id
            or summary_document.get("oracleImplementation")
            != "python-frozen-oracle"
            or summary_document.get("candidateImplementations")
            != ["scala-3.8.4-jvm25", "haskell-ghc-9.10.3"]
            or type(summary_document.get("caseCount")) is not int
            or summary_document["caseCount"] < 1
            or summary_document.get("mismatchCount") != 0
            or summary_document.get("artifacts") != expected_artifacts
            or summary_document.get("referenceCaptureStatus") != "PASS"
            or summary_document.get("status") != "PASS"
            or summary.payload != _canonical_json_bytes(summary_document)
        ):
            raise EvidenceAssemblyError(
                f"{matrix.upper()}_AGGREGATE_CORRECTNESS_INVALID"
            )
        validated[matrix] = (comparison, document)
    selected, _ = _validate_comparison(
        closure,
        RAW_PATHS["selected-comparison"],
        label="SELECTED_COMPARISON",
        expected_request_id="s1.4x-canonical-small-v1",
    )
    if selected.payload != validated["canonical"][0].payload:
        raise EvidenceAssemblyError("SELECTED_COMPARISON_BINDING_INVALID")
    return (*validated["canonical"], *validated["semantic"])


def _validate_contract_and_fixtures(
    closure: RawClosure,
    repository: Mapping[str, Snapshot],
) -> tuple[Snapshot, Snapshot, Snapshot, str]:
    frozen = {
        "contract-manifest": final_audit.FROZEN_CONTRACT_MANIFEST_SHA256,
        "reference-lock": final_audit.FROZEN_REFERENCE_LOCK_SHA256,
        "canonical-inputs": final_audit.FROZEN_FIXTURE_SHA256[
            "canonicalInputs"
        ],
        "property-seeds": final_audit.FROZEN_FIXTURE_SHA256["propertySeeds"],
        "canonical-results": final_audit.FROZEN_FIXTURE_SHA256[
            "canonicalResults"
        ],
    }
    if any(repository[label].sha256 != digest for label, digest in frozen.items()):
        raise EvidenceAssemblyError("FIXTURE_FROZEN_HASH_INVALID")
    contract_snapshot = closure.snapshot(
        RAW_PATHS["contract-validation"],
        label="CONTRACT_VALIDATION",
    )
    contract = _json_snapshot(
        contract_snapshot,
        label="CONTRACT_VALIDATION",
    )
    required_contract = {
        "schemaVersion": "s1.4x-contract-validation-v1",
        "status": "PASS",
        "checkAll": True,
        "functionCount": 20,
        "errorCodeCount": 32,
        "propertyCount": 25,
        "binaryManifestCount": 4,
        "referenceSourceTreeCount": 4,
    }
    if any(contract.get(key) != value for key, value in required_contract.items()):
        raise EvidenceAssemblyError("CONTRACT_VALIDATION_INVALID")
    if (
        type(contract.get("referenceSourceCount")) is not int
        or contract["referenceSourceCount"] < 1
        or type(contract.get("contractManifestFileCount")) is not int
        or contract["contractManifestFileCount"] < 1
    ):
        raise EvidenceAssemblyError("CONTRACT_VALIDATION_INVALID")
    large_snapshot = closure.snapshot(
        RAW_PATHS["large-fixture"],
        label="LARGE_FIXTURE",
    )
    large = _json_snapshot(large_snapshot, label="LARGE_FIXTURE")
    replay_snapshot = closure.snapshot(
        RAW_PATHS["large-fixture-replay"],
        label="LARGE_FIXTURE_REPLAY",
    )
    replay = _json_snapshot(
        replay_snapshot,
        label="LARGE_FIXTURE_REPLAY",
    )
    manifest_entries = [
        {"path": path, "byteLength": length, "sha256": digest}
        for path, length, digest, _, _, _ in LARGE_FIXTURES
    ]
    payload_entries = [
        {
            "path": payload_path,
            "manifestPath": manifest_path,
            "byteLength": payload_length,
            "sha256": payload_digest,
        }
        for (
            manifest_path,
            _,
            _,
            payload_path,
            payload_length,
            payload_digest,
        ) in LARGE_FIXTURES
    ]
    fixture_tree = {
        "schemaVersion": "s1.4x-large-fixture-tree-v1",
        "manifestEntries": manifest_entries,
        "payloadEntries": payload_entries,
    }
    expected_large = {
        "schemaVersion": (
            "s1.4x-large-fixture-materialization-receipt-v1"
        ),
        "status": "PASS",
        "generatorSha256": LARGE_FIXTURE_GENERATOR_SHA256,
        "materializedRootPathId": "S1_4X_LARGE_FIXTURE_ROOT",
        "manifestEntries": manifest_entries,
        "payloadEntries": payload_entries,
        "fixtureTreeSha256": _sha256_bytes(
            _canonical_json_bytes(fixture_tree)[:-1]
        ),
    }
    if (
        large != expected_large
        or replay != expected_large
        or replay_snapshot.payload != large_snapshot.payload
    ):
        raise EvidenceAssemblyError("LARGE_FIXTURE_INVALID")
    for entry in (*manifest_entries, *payload_entries):
        materialized = closure.snapshot(
            Path("large-fixtures") / str(entry["path"]),
            label="LARGE_FIXTURE_MATERIALIZED",
        )
        if (
            materialized.sha256 != entry["sha256"]
            or materialized.size_bytes != entry["byteLength"]
        ):
            raise EvidenceAssemblyError("LARGE_FIXTURE_MATERIALIZED_INVALID")
    return (
        contract_snapshot,
        large_snapshot,
        replay_snapshot,
        str(expected_large["fixtureTreeSha256"]),
    )


def _validate_regression(
    closure: RawClosure,
    relative: Path,
    *,
    subject: str,
    role: str,
    command_receipts: Mapping[str, dict[str, Any]],
) -> Snapshot:
    label = f"{role.upper()}_REGRESSION"
    portable = _portable_relative_path(relative.as_posix(), label=label)
    snapshot = closure.snapshot(portable, label=label)
    document = _json_snapshot(snapshot, label=label)
    expected = {
        "production": {
            "project": "workspaces/decision-platform/python-services",
            "counts": (1344, 1344, 0, 0, 1344),
            "deselected": [],
            "replacements": [],
            "commandRoles": ("ruff", "mypy", "pytest"),
        },
        "research": {
            "project": (
                "workspaces/decision-platform/research/s1-4r-jax-risk"
            ),
            "counts": (263, 262, 1, 2, 264),
            "deselected": [DESELECTED_RESEARCH_NODE],
            "replacements": list(REPLACEMENT_RESEARCH_NODES),
            "commandRoles": (
                "ruff",
                "mypy",
                "replacement-pytest",
                "base-pytest",
            ),
        },
    }[role]
    fields = {
        "schemaVersion",
        "benchmarkSubjectCommit",
        "project",
        "collectedCount",
        "basePassedCount",
        "deselectedCount",
        "replacementPassedCount",
        "totalExecutedPassedCount",
        "deselectedNodeIds",
        "replacementNodeIds",
        "commands",
        "status",
    }
    if set(document) != fields:
        raise EvidenceAssemblyError(f"{label}_INVALID")
    counts = (
        document["collectedCount"],
        document["basePassedCount"],
        document["deselectedCount"],
        document["replacementPassedCount"],
        document["totalExecutedPassedCount"],
    )
    if (
        document["schemaVersion"]
        != "s1.4x-regression-compound-receipt-v1"
        or document["benchmarkSubjectCommit"] != subject
        or document["project"] != expected["project"]
        or counts != expected["counts"]
        or any(type(value) is not int for value in counts)
        or document["deselectedNodeIds"] != expected["deselected"]
        or document["replacementNodeIds"] != expected["replacements"]
        or document["status"] != "PASS"
        or not isinstance(document["commands"], list)
        or len(document["commands"]) != len(expected["commandRoles"])
    ):
        raise EvidenceAssemblyError(f"{label}_INVALID")
    command_roles: list[str] = []
    expected_labels = (
        ("production-ruff", "production-mypy", "production-pytest")
        if role == "production"
        else (
            "research-ruff",
            "research-mypy",
            "research-replacement-pytest",
            "research-base-pytest",
        )
    )
    for raw_command, expected_role in zip(
        document["commands"],
        expected["commandRoles"],
        strict=True,
    ):
        command = _require_object(
            raw_command,
            required={
                "role",
                "exitCode",
                "stdoutPath",
                "stdoutSha256",
                "stderrPath",
                "stderrSha256",
                "status",
            },
            label=f"{label}_COMMAND",
            exact=True,
        )
        if (
            command["role"] != expected_role
            or not _is_exact_int(command["exitCode"], 0)
            or command["status"] != "PASS"
        ):
            raise EvidenceAssemblyError(f"{label}_COMMAND_INVALID")
        command_roles.append(command["role"])
    if tuple(command_roles) != expected["commandRoles"]:
        raise EvidenceAssemblyError(f"{label}_COMMAND_INVALID")
    for compound, command_label in zip(
        document["commands"],
        expected_labels,
        strict=True,
    ):
        command = command_receipts[command_label]
        expected_compound = {
            field: command[field]
            for field in (
                "role",
                "exitCode",
                "stdoutPath",
                "stdoutSha256",
                "stderrPath",
                "stderrSha256",
                "status",
            )
        }
        if compound != expected_compound:
            raise EvidenceAssemblyError(f"{label}_COMMAND_LINK_INVALID")
    return snapshot


def _regression_specs() -> tuple[dict[str, Any], ...]:
    production = "workspaces/decision-platform/python-services"
    research = "workspaces/decision-platform/research/s1-4r-jax-risk"
    oracle = (
        "workspaces/decision-platform/research/s1-4x-numeric-parity/oracle"
    )
    junit = "regression/junit"
    return (
        {
            "label": "production-lock",
            "project": production,
            "role": "lock",
            "argv": [
                "S1_4X_VERIFIED_UV_BIN",
                "--no-config",
                "lock",
                "--check",
                "--project",
                production,
            ],
        },
        {
            "label": "production-sync",
            "project": production,
            "role": "sync",
            "argv": [
                "S1_4X_VERIFIED_UV_BIN",
                "--no-config",
                "sync",
                "--frozen",
                "--project",
                production,
            ],
        },
        {
            "label": "production-ruff",
            "project": production,
            "role": "ruff",
            "argv": [
                "S1_4X_VERIFIED_UV_BIN",
                "--no-config",
                "run",
                "--frozen",
                "--project",
                production,
                "ruff",
                "check",
                ".",
            ],
        },
        {
            "label": "production-mypy",
            "project": production,
            "role": "mypy",
            "argv": [
                "S1_4X_VERIFIED_UV_BIN",
                "--no-config",
                "run",
                "--frozen",
                "--project",
                production,
                "mypy",
                "app",
            ],
        },
        {
            "label": "production-pytest",
            "project": production,
            "role": "pytest",
            "argv": [
                "S1_4X_VERIFIED_UV_BIN",
                "--no-config",
                "run",
                "--frozen",
                "--project",
                production,
                "pytest",
                "-q",
                "--junitxml",
                f"{junit}/production-pytest.xml",
            ],
            "junit": f"{junit}/production-pytest.xml",
            "passed": 1344,
            "deselected": 0,
        },
        {
            "label": "research-lock",
            "project": research,
            "role": "lock",
            "argv": [
                "S1_4X_VERIFIED_UV_BIN",
                "--no-config",
                "lock",
                "--check",
                "--project",
                research,
            ],
        },
        {
            "label": "research-sync",
            "project": research,
            "role": "sync",
            "argv": [
                "S1_4X_VERIFIED_UV_BIN",
                "--no-config",
                "sync",
                "--frozen",
                "--all-groups",
                "--project",
                research,
            ],
        },
        {
            "label": "research-ruff",
            "project": research,
            "role": "ruff",
            "argv": [
                "S1_4X_VERIFIED_UV_BIN",
                "--no-config",
                "run",
                "--frozen",
                "--project",
                research,
                "ruff",
                "check",
                ".",
            ],
        },
        {
            "label": "research-mypy",
            "project": research,
            "role": "mypy",
            "argv": [
                "S1_4X_VERIFIED_UV_BIN",
                "--no-config",
                "run",
                "--frozen",
                "--project",
                research,
                "mypy",
                "src",
                "benchmarks",
            ],
        },
        {
            "label": "research-replacement-pytest",
            "project": research,
            "role": "replacement-pytest",
            "argv": [
                "S1_4X_VERIFIED_UV_BIN",
                "--no-config",
                "run",
                "--frozen",
                "--project",
                oracle,
                "pytest",
                "-q",
                "--junitxml",
                f"{junit}/research-replacement-pytest.xml",
                *REPLACEMENT_RESEARCH_NODES,
            ],
            "junit": f"{junit}/research-replacement-pytest.xml",
            "passed": 2,
            "deselected": 0,
        },
        {
            "label": "research-base-pytest",
            "project": research,
            "role": "base-pytest",
            "argv": [
                "S1_4X_VERIFIED_UV_BIN",
                "--no-config",
                "run",
                "--frozen",
                "--project",
                research,
                "pytest",
                "-q",
                "--junitxml",
                f"{junit}/research-base-pytest.xml",
                f"--deselect={DESELECTED_RESEARCH_NODE}",
            ],
            "junit": f"{junit}/research-base-pytest.xml",
            "passed": 262,
            "deselected": 1,
        },
    )


def _validate_junit_snapshot(
    snapshot: Snapshot,
    stdout: Snapshot,
    *,
    passed: int,
    deselected: int,
    label: str,
) -> None:
    if b"<!DOCTYPE" in snapshot.payload or b"<!ENTITY" in snapshot.payload:
        raise EvidenceAssemblyError(f"REGRESSION_JUNIT_INVALID:{label}")
    try:
        root = ET.fromstring(snapshot.payload)
        suite = (
            root
            if root.tag == "testsuite"
            else list(root)[0]
            if root.tag == "testsuites"
            and len(list(root)) == 1
            and list(root)[0].tag == "testsuite"
            else None
        )
        if suite is None:
            raise ValueError("suite shape")
        counts = tuple(
            int(suite.attrib[field])
            for field in ("tests", "failures", "errors", "skipped")
        )
        stdout_text = stdout.payload.decode("utf-8")
    except (ET.ParseError, KeyError, UnicodeError, ValueError) as exc:
        raise EvidenceAssemblyError(
            f"REGRESSION_JUNIT_INVALID:{label}"
        ) from exc
    summaries = [
        line
        for line in stdout_text.splitlines()
        if re.search(r"\b\d+ passed\b", line)
    ]
    if (
        counts != (passed, 0, 0, 0)
        or not summaries
        or len(re.findall(r"\b(\d+) passed\b", summaries[-1])) != 1
        or int(re.findall(r"\b(\d+) passed\b", summaries[-1])[0]) != passed
    ):
        raise EvidenceAssemblyError(f"REGRESSION_JUNIT_INVALID:{label}")
    deselected_values = re.findall(
        r"\b(\d+) deselected\b",
        summaries[-1],
    )
    observed_deselected = (
        int(deselected_values[0]) if deselected_values else 0
    )
    if len(deselected_values) > 1 or observed_deselected != deselected:
        raise EvidenceAssemblyError(f"REGRESSION_JUNIT_INVALID:{label}")


def _validate_regression_execution(
    closure: RawClosure,
    *,
    subject: str,
) -> dict[str, dict[str, Any]]:
    manifest_snapshot = closure.snapshot(
        Path("regression/execution-manifest.v1.json"),
        label="REGRESSION_EXECUTION_MANIFEST",
    )
    manifest = _json_snapshot(
        manifest_snapshot,
        label="REGRESSION_EXECUTION_MANIFEST",
    )
    specs = _regression_specs()
    expected_paths = [
        f"regression/commands/{spec['label']}.command.v1.json"
        for spec in specs
    ]
    if (
        set(manifest)
        != {
            "schemaVersion",
            "benchmarkSubjectCommit",
            "uvExecutableSha256",
            "commandReceipts",
            "status",
        }
        or manifest.get("schemaVersion")
        != "s1.4x-regression-execution-manifest-v1"
        or manifest.get("benchmarkSubjectCommit") != subject
        or not _is_sha256(manifest.get("uvExecutableSha256"))
        or manifest.get("status") != "PASS"
        or not isinstance(manifest.get("commandReceipts"), list)
        or len(manifest["commandReceipts"]) != len(specs)
    ):
        raise EvidenceAssemblyError("REGRESSION_EXECUTION_MANIFEST_INVALID")
    validated: dict[str, dict[str, Any]] = {}
    for raw_entry, spec, expected_path in zip(
        manifest["commandReceipts"],
        specs,
        expected_paths,
        strict=True,
    ):
        entry = _require_object(
            raw_entry,
            required={"path", "sha256"},
            label="REGRESSION_COMMAND_MANIFEST_ENTRY",
            exact=True,
        )
        if entry["path"] != expected_path or not _is_sha256(entry["sha256"]):
            raise EvidenceAssemblyError(
                "REGRESSION_COMMAND_MANIFEST_ENTRY_INVALID"
            )
        command_snapshot = closure.snapshot(
            Path(expected_path),
            label="REGRESSION_COMMAND_RECEIPT",
        )
        command = _json_snapshot(
            command_snapshot,
            label="REGRESSION_COMMAND_RECEIPT",
        )
        label = str(spec["label"])
        stdout_path = f"regression/logs/{label}.stdout"
        stderr_path = f"regression/logs/{label}.stderr"
        expected_junit = spec.get("junit")
        if (
            command_snapshot.sha256 != entry["sha256"]
            or set(command)
            != {
                "schemaVersion",
                "benchmarkSubjectCommit",
                "project",
                "role",
                "uvExecutableSha256",
                "commandArgv",
                "commandArgvSha256",
                "exitCode",
                "stdoutPath",
                "stdoutSha256",
                "stderrPath",
                "stderrSha256",
                "junitPath",
                "junitSha256",
                "status",
            }
            or command.get("schemaVersion")
            != "s1.4x-regression-command-receipt-v1"
            or command.get("benchmarkSubjectCommit") != subject
            or command.get("project") != spec["project"]
            or command.get("role") != spec["role"]
            or command.get("uvExecutableSha256")
            != manifest["uvExecutableSha256"]
            or command.get("commandArgv") != spec["argv"]
            or command.get("commandArgvSha256")
            != _sha256_bytes(_canonical_json_bytes(spec["argv"]))
            or not _is_exact_int(command.get("exitCode"), 0)
            or command.get("stdoutPath") != stdout_path
            or command.get("stderrPath") != stderr_path
            or command.get("junitPath") != expected_junit
            or command.get("status") != "PASS"
        ):
            raise EvidenceAssemblyError(
                f"REGRESSION_COMMAND_RECEIPT_INVALID:{label}"
            )
        stdout = closure.snapshot(
            Path(stdout_path),
            label="REGRESSION_COMMAND_STDOUT",
        )
        stderr = closure.snapshot(
            Path(stderr_path),
            label="REGRESSION_COMMAND_STDERR",
        )
        if (
            stdout.sha256 != command.get("stdoutSha256")
            or stderr.sha256 != command.get("stderrSha256")
        ):
            raise EvidenceAssemblyError(
                f"REGRESSION_COMMAND_STREAM_INVALID:{label}"
            )
        if expected_junit is None:
            if command.get("junitSha256") is not None:
                raise EvidenceAssemblyError(
                    f"REGRESSION_COMMAND_JUNIT_INVALID:{label}"
                )
        else:
            junit = closure.snapshot(
                Path(str(expected_junit)),
                label="REGRESSION_COMMAND_JUNIT",
            )
            if junit.sha256 != command.get("junitSha256"):
                raise EvidenceAssemblyError(
                    f"REGRESSION_COMMAND_JUNIT_INVALID:{label}"
                )
            _validate_junit_snapshot(
                junit,
                stdout,
                passed=int(spec["passed"]),
                deselected=int(spec["deselected"]),
                label=label,
            )
        validated[label] = command
    return validated


def _validate_tracked_source_manifest(
    repository_root: Path,
    repository: Mapping[str, Snapshot],
    *,
    subject: str,
    language: str,
) -> dict[str, dict[str, str]]:
    key = f"{language}-source-inputs"
    manifest = _json_snapshot(
        repository[key],
        label=f"{language.upper()}_SOURCE_INPUTS",
    )
    files = manifest.get("files")
    expected_input_sets = {
        "tracked": "files",
        "manifest": "files",
        "format": "files",
        "compile": "files",
        "lint": "files",
        "profileRun": "files",
    }
    if (
        manifest.get("schemaVersion")
        != "s1.4x-source-input-manifest-v1"
        or manifest.get("language") != language
        or not isinstance(files, dict)
        or not files
        or list(files)
        != sorted(files, key=lambda value: value.encode("utf-8"))
        or manifest.get("inputSets") != expected_input_sets
        or not _is_sha256(manifest.get("canonicalManifestSha256"))
    ):
        raise EvidenceAssemblyError(
            f"{language.upper()}_SOURCE_INPUTS_INVALID"
        )
    validated: dict[str, dict[str, str]] = {}
    for raw_path, raw_entry in files.items():
        relative = _portable_relative_path(
            raw_path,
            label=f"{language.upper()}_SOURCE_INPUT",
        )
        entry = _require_object(
            raw_entry,
            required={"role", "sha256"},
            label=f"{language.upper()}_SOURCE_INPUT",
            exact=True,
        )
        if (
            entry["role"] not in {"main", "test", "benchmark", "configuration"}
            or not _is_sha256(entry["sha256"])
        ):
            raise EvidenceAssemblyError(
                f"{language.upper()}_SOURCE_INPUT_INVALID"
            )
        blob = _git_blob_snapshot(
            repository_root,
            subject,
            S1_4X_RELATIVE / language / relative,
            label=f"{language.upper()}_SOURCE_INPUT",
        )
        if blob.sha256 != entry["sha256"]:
            raise EvidenceAssemblyError(
                f"{language.upper()}_SOURCE_INPUT_HASH_INVALID"
            )
        validated[relative.as_posix()] = {
            "role": str(entry["role"]),
            "sha256": str(entry["sha256"]),
        }
    expected_manifest_hash = _sha256_bytes(
        b"".join(
            f"{validated[path]['sha256']}  {path}\n".encode("utf-8")
            for path in sorted(
                validated,
                key=lambda value: value.encode("utf-8"),
            )
        )
    )
    if manifest["canonicalManifestSha256"] != expected_manifest_hash:
        raise EvidenceAssemblyError(
            f"{language.upper()}_SOURCE_MANIFEST_HASH_INVALID"
        )
    return validated


def _package_component_projection(payload: bytes) -> dict[str, Any]:
    try:
        text = payload.decode("utf-8")
    except UnicodeError as exc:
        raise EvidenceAssemblyError("HASKELL_PACKAGE_YAML_INVALID") from exc
    if "\t" in text:
        raise EvidenceAssemblyError("HASKELL_PACKAGE_YAML_INVALID")
    lines = text.splitlines()
    scalar_keys = {
        "name",
        "version",
        "github",
        "license",
        "author",
        "maintainer",
        "category",
        "synopsis",
        "description",
        "language",
    }
    scalars: dict[str, str] = {}
    for line in lines:
        if not line or line.startswith((" ", "#")):
            continue
        match = re.fullmatch(r"([A-Za-z][A-Za-z-]*):\s*(\S(?:.*\S)?)\s*", line)
        if match is None:
            continue
        key, value = match.groups()
        if key not in scalar_keys or key in scalars or "#" in value:
            raise EvidenceAssemblyError("HASKELL_PACKAGE_YAML_INVALID")
        scalars[key] = value
    if set(scalars) != scalar_keys:
        raise EvidenceAssemblyError("HASKELL_PACKAGE_YAML_INVALID")

    root_sections = [
        (index, match.group(1))
        for index, line in enumerate(lines)
        if (match := re.fullmatch(r"([A-Za-z][A-Za-z-]*):\s*", line))
    ]
    section_ranges = {
        name: (
            start + 1,
            root_sections[position + 1][0]
            if position + 1 < len(root_sections)
            else len(lines),
        )
        for position, (start, name) in enumerate(root_sections)
    }
    expected_sections = {
        "default-extensions",
        "ghc-options",
        "internal-libraries",
        "library",
        "executables",
        "tests",
        "benchmarks",
    }
    if set(section_ranges) != expected_sections:
        raise EvidenceAssemblyError("HASKELL_PACKAGE_YAML_INVALID")

    def list_in_range(
        start: int,
        end: int,
        *,
        indent: int,
    ) -> list[str]:
        values: list[str] = []
        for line in lines[start:end]:
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            match = re.fullmatch(
                rf"{' ' * indent}-\s+(\S(?:.*\S)?)\s*",
                line,
            )
            if match is None or "#" in match.group(1):
                raise EvidenceAssemblyError("HASKELL_PACKAGE_YAML_INVALID")
            values.append(match.group(1))
        if not values:
            raise EvidenceAssemblyError("HASKELL_PACKAGE_YAML_INVALID")
        return values

    default_extensions = list_in_range(
        *section_ranges["default-extensions"],
        indent=2,
    )
    root_ghc_options = list_in_range(
        *section_ranges["ghc-options"],
        indent=2,
    )
    if len(default_extensions) != len(set(default_extensions)):
        raise EvidenceAssemblyError("HASKELL_PACKAGE_YAML_INVALID")

    def component(
        start: int,
        end: int,
        *,
        indent: int,
        stanza: str,
    ) -> dict[str, Any]:
        scalar_pattern = re.compile(
            rf"{' ' * indent}(source-dirs|main):\s*(\S(?:.*\S)?)\s*"
        )
        section_pattern = re.compile(
            rf"{' ' * indent}(ghc-options|dependencies):\s*"
        )
        component_scalars: dict[str, str] = {}
        component_sections: dict[str, tuple[int, int]] = {}
        headers: list[tuple[int, str]] = []
        for index in range(start, end):
            line = lines[index]
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            scalar_match = scalar_pattern.fullmatch(line)
            if scalar_match is not None:
                key, value = scalar_match.groups()
                if key in component_scalars or "#" in value:
                    raise EvidenceAssemblyError("HASKELL_PACKAGE_YAML_INVALID")
                component_scalars[key] = value
                continue
            section_match = section_pattern.fullmatch(line)
            if section_match is not None:
                headers.append((index, section_match.group(1)))
                continue
            if len(line) - len(line.lstrip(" ")) <= indent:
                raise EvidenceAssemblyError("HASKELL_PACKAGE_YAML_INVALID")
        for position, (header, name) in enumerate(headers):
            if name in component_sections:
                raise EvidenceAssemblyError("HASKELL_PACKAGE_YAML_INVALID")
            component_sections[name] = (
                header + 1,
                headers[position + 1][0]
                if position + 1 < len(headers)
                else end,
            )
        if (
            "source-dirs" not in component_scalars
            or "dependencies" not in component_sections
            or (stanza.startswith(("executable ", "test-suite ", "benchmark ")))
            != ("main" in component_scalars)
        ):
            raise EvidenceAssemblyError("HASKELL_PACKAGE_YAML_INVALID")
        return {
            "sourceDirs": component_scalars["source-dirs"],
            "mainIs": component_scalars.get("main"),
            "ghcOptions": (
                list_in_range(
                    *component_sections["ghc-options"],
                    indent=indent + 2,
                )
                if "ghc-options" in component_sections
                else []
            ),
            "dependencies": list_in_range(
                *component_sections["dependencies"],
                indent=indent + 2,
            ),
        }

    components: dict[str, dict[str, Any]] = {}
    simple_sections = {
        "library": "library",
    }
    collection_sections = {
        "internal-libraries": "library",
        "executables": "executable",
        "tests": "test-suite",
        "benchmarks": "benchmark",
    }
    for section, stanza in simple_sections.items():
        if section not in section_ranges:
            raise EvidenceAssemblyError("HASKELL_PACKAGE_YAML_INVALID")
        start, end = section_ranges[section]
        components[stanza] = component(
            start,
            end,
            indent=2,
            stanza=stanza,
        )
    for section, stanza_prefix in collection_sections.items():
        if section not in section_ranges:
            raise EvidenceAssemblyError("HASKELL_PACKAGE_YAML_INVALID")
        start, end = section_ranges[section]
        entries = [
            (index, match.group(1))
            for index in range(start, end)
            if (
                match := re.fullmatch(
                    r"  ([A-Za-z0-9][A-Za-z0-9-]*):\s*",
                    lines[index],
                )
            )
        ]
        if not entries:
            raise EvidenceAssemblyError("HASKELL_PACKAGE_YAML_INVALID")
        for position, (entry_start, name) in enumerate(entries):
            entry_end = (
                entries[position + 1][0]
                if position + 1 < len(entries)
                else end
            )
            stanza = f"{stanza_prefix} {name}"
            if stanza in components:
                raise EvidenceAssemblyError("HASKELL_PACKAGE_YAML_INVALID")
            components[stanza] = component(
                entry_start + 1,
                entry_end,
                indent=4,
                stanza=stanza,
            )
    return {
        "metadata": scalars,
        "language": scalars["language"],
        "defaultExtensions": default_extensions,
        "ghcOptions": root_ghc_options,
        "components": components,
    }


def _validate_generated_cabal_projection(
    package_yaml: Snapshot,
    generated_cabal: Snapshot,
    *,
    hpack_version: str,
    expected_modules: Mapping[str, Mapping[str, Sequence[str]]],
) -> None:
    projection = _package_component_projection(package_yaml.payload)
    try:
        text = generated_cabal.payload.decode("utf-8")
    except UnicodeError as exc:
        raise EvidenceAssemblyError("HASKELL_GENERATED_CABAL_INVALID") from exc
    header = re.search(
        r"^-- This file has been generated from package\.yaml by hpack "
        r"version ([0-9]+(?:\.[0-9]+)+)\.$",
        text,
        flags=re.MULTILINE,
    )
    if (
        header is None
        or header.group(1) != hpack_version
        or projection["language"] != "GHC2024"
    ):
        raise EvidenceAssemblyError("HASKELL_GENERATED_CABAL_INVALID")
    lines = text.splitlines()
    metadata = projection["metadata"]
    github = metadata["github"]
    expected_root_fields = {
        "cabal-version": "2.0",
        "name": metadata["name"],
        "version": metadata["version"],
        "synopsis": metadata["synopsis"],
        "description": metadata["description"],
        "category": metadata["category"],
        "homepage": f"https://github.com/{github}#readme",
        "bug-reports": f"https://github.com/{github}/issues",
        "author": metadata["author"],
        "maintainer": metadata["maintainer"],
        "license": metadata["license"],
        "build-type": "Simple",
    }
    actual_root_fields: dict[str, str] = {}
    for line in lines:
        match = re.fullmatch(
            r"([A-Za-z][A-Za-z-]*):\s*(\S(?:.*\S)?)\s*",
            line,
        )
        if match is None:
            continue
        key, value = match.groups()
        if key in actual_root_fields:
            raise EvidenceAssemblyError("HASKELL_GENERATED_CABAL_INVALID")
        actual_root_fields[key] = value
    if actual_root_fields != expected_root_fields:
        raise EvidenceAssemblyError("HASKELL_GENERATED_CABAL_INVALID")
    try:
        source_repository_index = lines.index("source-repository head")
    except ValueError as exc:
        raise EvidenceAssemblyError("HASKELL_GENERATED_CABAL_INVALID") from exc
    source_repository_block = lines[
        source_repository_index + 1 : source_repository_index + 3
    ]
    if source_repository_block != [
        "  type: git",
        f"  location: https://github.com/{github}",
    ]:
        raise EvidenceAssemblyError("HASKELL_GENERATED_CABAL_INVALID")

    stanza_pattern = re.compile(
        r"(library(?: [A-Za-z0-9][A-Za-z0-9-]*)?"
        r"|executable [A-Za-z0-9][A-Za-z0-9-]*"
        r"|test-suite [A-Za-z0-9][A-Za-z0-9-]*"
        r"|benchmark [A-Za-z0-9][A-Za-z0-9-]*)"
    )
    stanza_starts = [
        (index, match.group(1))
        for index, line in enumerate(lines)
        if (match := stanza_pattern.fullmatch(line))
    ]
    actual_stanzas = [name for _, name in stanza_starts]
    expected_components = projection["components"]
    if (
        set(actual_stanzas) != set(expected_components)
        or len(actual_stanzas) != len(set(actual_stanzas))
    ):
        raise EvidenceAssemblyError("HASKELL_GENERATED_CABAL_INVALID")
    known_top_level = {
        "source-repository head",
        *expected_components,
    }
    for line in lines:
        if (
            line
            and not line.startswith((" ", "--"))
            and ":" not in line
            and line not in known_top_level
        ):
            raise EvidenceAssemblyError("HASKELL_GENERATED_CABAL_INVALID")

    def block_scalar(block: Sequence[str], key: str) -> str | None:
        values = [
            match.group(1)
            for line in block
            if (
                match := re.fullmatch(
                    rf"  {re.escape(key)}:\s*(\S(?:.*\S)?)\s*",
                    line,
                )
            )
        ]
        if len(values) > 1:
            raise EvidenceAssemblyError("HASKELL_GENERATED_CABAL_INVALID")
        return values[0] if values else None

    def block_single_list_value(block: Sequence[str], key: str) -> str | None:
        inline = block_scalar(block, key)
        if inline is not None:
            return inline
        header = f"  {key}:"
        if block.count(header) != 1:
            return None
        index = block.index(header)
        values: list[str] = []
        for line in block[index + 1 :]:
            match = re.fullmatch(r"      (\S(?:.*\S)?)\s*", line)
            if match is None:
                break
            values.append(match.group(1))
        return values[0] if len(values) == 1 else None

    def block_list(block: Sequence[str], key: str) -> list[str] | None:
        inline = block_scalar(block, key)
        if inline is not None:
            return inline.split()
        header_line = f"  {key}:"
        if block.count(header_line) != 1:
            return None
        index = block.index(header_line)
        values: list[str] = []
        for line in block[index + 1 :]:
            if re.fullmatch(r"  [A-Za-z][A-Za-z0-9-]*:.*", line):
                break
            if not line.strip():
                continue
            value = line.strip()
            if value.startswith(","):
                value = value[1:].strip()
            if not value:
                raise EvidenceAssemblyError("HASKELL_GENERATED_CABAL_INVALID")
            values.append(value)
        return values

    def normalized_dependency(value: str) -> str:
        return re.sub(r"\s*(==|>=|<=|>|<|\^>=)\s*", r" \1 ", value).strip()

    for position, (start, stanza) in enumerate(stanza_starts):
        end = (
            stanza_starts[position + 1][0]
            if position + 1 < len(stanza_starts)
            else len(lines)
        )
        block = lines[start + 1 : end]
        allowed_fields = {
            "exposed-modules",
            "other-modules",
            "autogen-modules",
            "hs-source-dirs",
            "default-extensions",
            "ghc-options",
            "build-depends",
            "default-language",
        }
        if stanza.startswith(("executable ", "test-suite ", "benchmark ")):
            allowed_fields.add("main-is")
        if stanza.startswith(("test-suite ", "benchmark ")):
            allowed_fields.add("type")
        actual_fields = {
            match.group(1)
            for line in block
            if (
                match := re.fullmatch(
                    r"  ([A-Za-z][A-Za-z0-9-]*):(?:\s*.*)?",
                    line,
                )
            )
        }
        if (
            not actual_fields.issubset(allowed_fields)
            or "buildable" in actual_fields
            or any(
                line.lstrip().startswith(("if ", "else", "elif "))
                for line in block
            )
        ):
            raise EvidenceAssemblyError("HASKELL_GENERATED_CABAL_INVALID")
        try:
            extension_index = block.index("  default-extensions:")
        except ValueError as exc:
            raise EvidenceAssemblyError(
                "HASKELL_GENERATED_CABAL_INVALID"
            ) from exc
        extensions: list[str] = []
        for line in block[extension_index + 1 :]:
            match = re.fullmatch(r"      ([A-Za-z][A-Za-z0-9]*)", line)
            if match is None:
                break
            extensions.append(match.group(1))
        expected = expected_components[stanza]
        module_projection = expected_modules.get(stanza)
        actual_dependencies = block_list(block, "build-depends")
        expected_dependencies = [
            normalized_dependency(value) for value in expected["dependencies"]
        ]
        actual_ghc_options = block_scalar(block, "ghc-options")
        expected_ghc_options = " ".join(
            [*projection["ghcOptions"], *expected["ghcOptions"]]
        )
        if (
            block_scalar(block, "default-language")
            != projection["language"]
            or extensions != projection["defaultExtensions"]
            or block_single_list_value(block, "hs-source-dirs")
            != expected["sourceDirs"]
            or block_scalar(block, "main-is") != expected["mainIs"]
            or module_projection is None
            or block_list(block, "exposed-modules")
            != (
                list(module_projection["exposedModules"])
                if module_projection["exposedModules"]
                else None
            )
            or block_list(block, "other-modules")
            != list(module_projection["otherModules"])
            or block_list(block, "autogen-modules")
            != list(module_projection["autogenModules"])
            or actual_ghc_options != expected_ghc_options
            or actual_dependencies is None
            or len(actual_dependencies) != len(expected_dependencies)
            or len(set(expected_dependencies)) != len(expected_dependencies)
            or len(
                {
                    normalized_dependency(value)
                    for value in actual_dependencies
                }
            )
            != len(actual_dependencies)
            or sorted(
                normalized_dependency(value)
                for value in actual_dependencies
            )
            != sorted(expected_dependencies)
            or (
                stanza.startswith(("test-suite ", "benchmark "))
                and block_scalar(block, "type") != "exitcode-stdio-1.0"
            )
            or (
                not stanza.startswith(("test-suite ", "benchmark "))
                and block_scalar(block, "type") is not None
            )
        ):
            raise EvidenceAssemblyError("HASKELL_GENERATED_CABAL_INVALID")


def _haskell_component_modules(
    repository_root: Path,
    *,
    subject: str,
    projection: Mapping[str, Any],
    manifest_files: Mapping[str, Mapping[str, str]],
) -> dict[str, dict[str, list[str]]]:
    """Tracked Haskell modules를 package component별 Cabal projection으로 만든다."""

    paths_module = "Paths_s1_4x_haskell"
    projected: dict[str, dict[str, list[str]]] = {}
    components = projection.get("components")
    if not isinstance(components, dict):
        raise EvidenceAssemblyError("HASKELL_PACKAGE_YAML_INVALID")
    for stanza, raw_component in components.items():
        if not isinstance(stanza, str) or not isinstance(raw_component, dict):
            raise EvidenceAssemblyError("HASKELL_PACKAGE_YAML_INVALID")
        source_dir = raw_component.get("sourceDirs")
        main_is = raw_component.get("mainIs")
        if not isinstance(source_dir, str):
            raise EvidenceAssemblyError("HASKELL_PACKAGE_YAML_INVALID")
        main_path = (
            f"{source_dir}/{main_is}"
            if isinstance(main_is, str)
            else None
        )
        modules: list[str] = []
        for relative in sorted(manifest_files, key=lambda value: value.encode()):
            if (
                not relative.endswith(".hs")
                or not relative.startswith(f"{source_dir}/")
                or relative == main_path
            ):
                continue
            snapshot = _git_blob_snapshot(
                repository_root,
                subject,
                S1_4X_RELATIVE / "haskell" / relative,
                label="HASKELL_CABAL_MODULE",
            )
            try:
                source = snapshot.payload.decode("utf-8")
            except UnicodeError as exc:
                raise EvidenceAssemblyError(
                    "HASKELL_CABAL_MODULE_INVALID"
                ) from exc
            match = re.search(
                r"(?m)^module\s+([A-Z][A-Za-z0-9_.']*)\b",
                source,
            )
            if match is None or match.group(1) in modules:
                raise EvidenceAssemblyError("HASKELL_CABAL_MODULE_INVALID")
            modules.append(match.group(1))
        modules.sort(key=lambda value: value.encode())
        projected[stanza] = {
            "exposedModules": modules if stanza.startswith("library") else [],
            "otherModules": (
                [paths_module]
                if stanza.startswith("library")
                else [*modules, paths_module]
            ),
            "autogenModules": [paths_module],
        }
    return projected


def _validate_haskell_generated_cabal_provenance(
    closure: RawClosure,
    repository_root: Path,
    repository: Mapping[str, Snapshot],
    *,
    subject: str,
    manifest_files: Mapping[str, Mapping[str, str]],
) -> dict[str, Any]:
    receipt_snapshot = closure.snapshot(
        HASKELL_CABAL_PROVENANCE,
        label="HASKELL_CABAL_PROVENANCE",
    )
    generated_snapshot = closure.snapshot(
        HASKELL_GENERATED_CABAL,
        label="HASKELL_GENERATED_CABAL",
    )
    receipt = _json_snapshot(
        receipt_snapshot,
        label="HASKELL_CABAL_PROVENANCE",
    )
    package_path = S1_4X_RELATIVE / "haskell/package.yaml"
    source_manifest_path = S1_4X_RELATIVE / "haskell/source-inputs.v1.json"
    package_snapshot = _git_blob_snapshot(
        repository_root,
        subject,
        package_path,
        label="HASKELL_CABAL_PACKAGE_YAML",
    )
    package = _require_object(
        receipt.get("packageYaml"),
        required={"path", "blobSha256"},
        label="HASKELL_CABAL_PACKAGE_YAML",
        exact=True,
    )
    source_manifest = _require_object(
        receipt.get("sourceInputManifest"),
        required={"path", "blobSha256"},
        label="HASKELL_CABAL_SOURCE_MANIFEST",
        exact=True,
    )
    stack = _require_object(
        receipt.get("stack"),
        required={"pathId", "version", "binarySha256"},
        label="HASKELL_CABAL_STACK",
        exact=True,
    )
    hpack = _require_object(
        receipt.get("hpack"),
        required={"version", "versionOutputSha256"},
        label="HASKELL_CABAL_HPACK",
        exact=True,
    )
    build = _require_object(
        receipt.get("build"),
        required={
            "portableArgv",
            "portableArgvSha256",
            "runtimeArgvSha256",
            "stackRootPathId",
            "exitCode",
        },
        label="HASKELL_CABAL_BUILD",
        exact=True,
    )
    generated = _require_object(
        receipt.get("generatedCabal"),
        required={
            "repositoryRelativePath",
            "artifactPath",
            "sha256",
            "sizeBytes",
            "preBuildSha256",
            "postBuildSha256",
        },
        label="HASKELL_CABAL_GENERATED",
        exact=True,
    )
    haskell_lock = _json_snapshot(
        repository["haskell-toolchain-lock"],
        label="HASKELL_TOOLCHAIN_LOCK",
    )
    try:
        stack_lock = haskell_lock["resolvedTools"]["stack"]
    except (KeyError, TypeError) as exc:
        raise EvidenceAssemblyError("HASKELL_CABAL_STACK_INVALID") from exc
    expected_stack = {
        "pathId": stack_lock.get("pathId"),
        "version": stack_lock.get("version"),
        "binarySha256": stack_lock.get("sha256"),
    }
    portable_argv = build.get("portableArgv")
    selected_profile_document = _json_snapshot(
        repository["haskell-selected-profile"],
        label="HASKELL_SELECTED_PROFILE",
    )
    profile_options = {
        "baseline-o0-fasm": ["-O0", "-fasm"],
        "optimized-o2-fasm": ["-O2", "-fasm"],
    }.get(str(selected_profile_document.get("profileId")))
    if profile_options is None:
        raise EvidenceAssemblyError("HASKELL_CABAL_PROVENANCE_INVALID")
    expected_portable_argv = [
        "stack",
        "--stack-root",
        "<isolated-stack-root>",
        "--work-dir",
        "<isolated-stack-work-dir>",
        "--system-ghc",
        "--no-install-ghc",
        "--stack-yaml",
        "haskell/stack.yaml",
        "--hpack-force",
        "build",
        "--test",
        "--no-run-tests",
        "--no-terminal",
        "--ghc-options",
        " ".join(profile_options),
    ]
    if (
        set(receipt)
        != {
            "schemaVersion",
            "benchmarkSubjectCommit",
            "toolchainLockSha256",
            "packageYaml",
            "sourceInputManifest",
            "stack",
            "hpack",
            "build",
            "generatedCabal",
            "sourceTreeSha256",
            "propertyClosureSha256",
            "status",
        }
        or receipt.get("schemaVersion")
        != "s1.4x-haskell-generated-cabal-provenance-v1"
        or receipt.get("benchmarkSubjectCommit") != subject
        or receipt.get("toolchainLockSha256")
        != repository["haskell-toolchain-lock"].sha256
        or package
        != {"path": package_path.as_posix(), "blobSha256": package_snapshot.sha256}
        or source_manifest
        != {
            "path": source_manifest_path.as_posix(),
            "blobSha256": repository["haskell-source-inputs"].sha256,
        }
        or stack != expected_stack
        or hpack.get("version") != "0.39.6"
        or hpack.get("versionOutputSha256")
        != _sha256_bytes(b"0.39.6\n")
        or portable_argv != expected_portable_argv
        or build.get("portableArgvSha256")
        != _sha256_bytes(_canonical_json_bytes(portable_argv)[:-1])
        or not _is_sha256(build.get("runtimeArgvSha256"))
        or re.fullmatch(
            r"S1_4X_CACHE_ROOT/stack-root-property-[0-9a-f]{24}",
            str(build.get("stackRootPathId")),
        )
        is None
        or build.get("exitCode") != 0
        or generated.get("repositoryRelativePath")
        != (S1_4X_RELATIVE / "haskell/s1-4x-haskell.cabal").as_posix()
        or generated.get("artifactPath") != HASKELL_GENERATED_CABAL.as_posix()
        or generated.get("sha256") != generated_snapshot.sha256
        or generated.get("sizeBytes") != generated_snapshot.size_bytes
        or generated.get("preBuildSha256") != generated_snapshot.sha256
        or generated.get("postBuildSha256") != generated_snapshot.sha256
        or not _is_sha256(receipt.get("sourceTreeSha256"))
        or not _is_sha256(receipt.get("propertyClosureSha256"))
        or receipt.get("status") != "PASS"
        or receipt_snapshot.payload != _canonical_json_bytes(receipt)
    ):
        raise EvidenceAssemblyError("HASKELL_CABAL_PROVENANCE_INVALID")
    package_projection = _package_component_projection(package_snapshot.payload)
    _validate_generated_cabal_projection(
        package_snapshot,
        generated_snapshot,
        hpack_version=str(hpack["version"]),
        expected_modules=_haskell_component_modules(
            repository_root,
            subject=subject,
            projection=package_projection,
            manifest_files=manifest_files,
        ),
    )
    return {
        "receipt": receipt_snapshot,
        "generated": generated_snapshot,
        "document": receipt,
        "sha256": generated_snapshot.sha256,
    }


def _haskell_source_tree_sha256(
    repository_root: Path,
    *,
    subject: str,
    manifest_files: Mapping[str, Mapping[str, str]],
    generated_cabal_sha256: str,
) -> str:
    source_paths = {
        path
        for path in manifest_files
        if path.endswith(".hs")
        and path.split("/", 1)[0] in HASKELL_CANDIDATE_ROOTS
    }
    expected_manifest_paths = source_paths | {
        "package.yaml",
        "selected-profile.v1.json",
    }
    if set(manifest_files) != expected_manifest_paths:
        raise EvidenceAssemblyError("HASKELL_SOURCE_INPUT_SET_INVALID")
    entries: list[dict[str, str]] = []
    for relative in sorted(
        source_paths | set(HASKELL_SOURCE_TREE_INPUTS),
        key=lambda value: value.encode("utf-8"),
    ):
        sha256 = generated_cabal_sha256
        if relative != "s1-4x-haskell.cabal":
            sha256 = _git_blob_snapshot(
                repository_root,
                subject,
                S1_4X_RELATIVE / "haskell" / relative,
                label="HASKELL_SOURCE_TREE_INPUT",
            ).sha256
        entries.append({"path": relative, "sha256": sha256})
    return _sha256_bytes(_canonical_json_bytes(entries)[:-1])


def _scala_property_source_closure_sha256(
    repository_root: Path,
    *,
    subject: str,
    manifest_files: Mapping[str, Mapping[str, str]],
) -> str:
    selected_paths = {
        path
        for path in manifest_files
        if path in {"project.scala", "selected-profile.scala"}
        or path.startswith("src/main/scala/")
        or path.startswith("src/test/scala/")
    }
    expected_paths = {
        path
        for path, entry in manifest_files.items()
        if entry["role"] in {"configuration", "main", "test"}
    }
    if selected_paths != expected_paths:
        raise EvidenceAssemblyError("SCALA_PROPERTY_SOURCE_SET_INVALID")
    digest = hashlib.sha256()
    for relative in sorted(selected_paths, key=lambda value: value.encode("utf-8")):
        blob = _git_blob_snapshot(
            repository_root,
            subject,
            S1_4X_RELATIVE / "scala" / relative,
            label="SCALA_PROPERTY_SOURCE",
        )
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(blob.payload)
        digest.update(b"\0")
    return digest.hexdigest()


def _haskell_property_source_closure_sha256(
    repository_root: Path,
    *,
    subject: str,
    manifest_files: Mapping[str, Mapping[str, str]],
    generated_cabal_sha256: str,
) -> str:
    candidate_paths = {
        path
        for path in manifest_files
        if path.endswith(".hs")
        and path.split("/", 1)[0] in HASKELL_CANDIDATE_ROOTS
    }
    configuration_paths = set(HASKELL_SOURCE_TREE_INPUTS) | {
        "selected-profile.v1.json",
        "source-inputs.v1.json",
    }
    digest = hashlib.sha256()
    for relative in sorted(
        candidate_paths | configuration_paths,
        key=lambda value: value.encode("utf-8"),
    ):
        sha256 = generated_cabal_sha256
        if relative != "s1-4x-haskell.cabal":
            sha256 = _git_blob_snapshot(
                repository_root,
                subject,
                S1_4X_RELATIVE / "haskell" / relative,
                label="HASKELL_PROPERTY_SOURCE",
            ).sha256
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(sha256.encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _utc_timestamp(value: Any, *, label: str) -> dt.datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise EvidenceAssemblyError(f"{label}_INVALID")
    try:
        parsed = dt.datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise EvidenceAssemblyError(f"{label}_INVALID") from exc
    if parsed.utcoffset() != dt.timedelta(0):
        raise EvidenceAssemblyError(f"{label}_INVALID")
    return parsed


def _coverage_stream_snapshot(
    closure: RawClosure,
    *,
    receipt_path: Path,
    reference: Any,
    expected_name: str,
    label: str,
) -> Snapshot:
    stream = _require_object(
        reference,
        required={"path", "sha256", "sizeBytes"},
        label=label,
        exact=True,
    )
    snapshot = closure.snapshot(receipt_path.parent / expected_name, label=label)
    if (
        stream.get("path") != expected_name
        or stream.get("sha256") != snapshot.sha256
        or stream.get("sizeBytes") != snapshot.size_bytes
    ):
        raise EvidenceAssemblyError(f"{label}_INVALID")
    return snapshot


def _haskell_completion_portable_argv(
    *,
    subject: str,
    cabal_sha256: str,
    profile_options: list[str],
    stack_root_path_id: str,
    build_argv_sha256: str,
) -> list[str]:
    return [
        "bash",
        "--noprofile",
        "--norc",
        "-c",
        'source "$1"; shift; s1_4x_run_benchmark_python "$@"',
        "s1-4x-generated-cabal-completion",
        "haskell/tools/python-runtime.sh",
        "haskell/tools/haskell_evidence.py",
        "generated-cabal-provenance",
        "--haskell-root",
        "haskell",
        "--output-directory",
        "coverage/haskell",
        "--benchmark-subject-commit",
        subject,
        "--stack-bin",
        "<pinned-stack-bin>",
        "--pre-build-sha256",
        cabal_sha256,
        *[f"--ghc-option={option}" for option in profile_options],
        "--stack-root-path-id",
        stack_root_path_id,
        "--runtime-build-argv-sha256",
        build_argv_sha256,
    ]


def _validate_coverage(
    closure: RawClosure,
    *,
    repository_root: Path,
    subject: str,
    toolchains: Mapping[str, Mapping[str, Any]],
) -> tuple[Snapshot, dict[str, Any], tuple[Snapshot, ...]]:
    coverage_snapshot, coverage_document = _load_coverage(closure)
    if (
        set(coverage_document)
        != {
            "schemaVersion",
            "candidateCount",
            "candidates",
            "propertyCountPerCandidate",
            "functionCountPerCandidate",
            "errorTrackCountsPerCandidate",
            "errorVerificationModeCountsPerCandidate",
            "status",
        }
        or coverage_document.get("schemaVersion")
        != "s1.4x-integration-coverage-v1"
        or coverage_document.get("candidateCount") != 2
        or coverage_document.get("propertyCountPerCandidate") != 25
        or coverage_document.get("functionCountPerCandidate") != 20
        or coverage_document.get("errorTrackCountsPerCandidate")
        != {"s1.4": 19, "s1.4r": 13}
        or coverage_document.get("errorVerificationModeCountsPerCandidate")
        != {
            "processDynamic": 29,
            "referenceObjectModel": 1,
            "registryStatic": 2,
        }
        or coverage_document.get("status") != "PASS"
        or coverage_snapshot.payload != _canonical_json_bytes(coverage_document)
        or not isinstance(coverage_document.get("candidates"), list)
        or len(coverage_document["candidates"]) != 2
    ):
        raise EvidenceAssemblyError("COVERAGE_INVALID")

    frozen_snapshots = {
        label: _git_blob_snapshot(
            repository_root,
            subject,
            relative,
            label=f"COVERAGE_{label.upper()}",
        )
        for label, relative in PROPERTY_FROZEN_INPUTS.items()
    }
    frozen_snapshots.update(
        {
            "scala-toolchain-lock": toolchains["scala"]["lock"],
            "haskell-selected-profile": toolchains["haskell"]["selected"],
            "haskell-source-inputs": _git_blob_snapshot(
                repository_root,
                subject,
                REPOSITORY_PATHS["haskell-source-inputs"],
                label="COVERAGE_HASKELL_SOURCE_INPUTS",
            ),
        }
    )
    frozen_directory = tempfile.TemporaryDirectory(
        prefix="s1-4x-coverage-subject-"
    )
    frozen_root = Path(frozen_directory.name)
    frozen_paths = {
        "property-plan": frozen_root / "contract/property-plan.v1.json",
        "property-seeds": (
            frozen_root
            / "contract/fixtures/property/property-seeds.v1.json"
        ),
        "function-registry": (
            frozen_root / "contract/function-registry.v1.json"
        ),
        "error-registry": frozen_root / "contract/error-registry.v1.json",
        "scala-toolchain-lock": frozen_root / "scala/toolchain-lock.v1.json",
        "haskell-selected-profile": (
            frozen_root / "haskell/selected-profile.v1.json"
        ),
        "haskell-source-inputs": (
            frozen_root / "haskell/source-inputs.v1.json"
        ),
    }
    for label, path in frozen_paths.items():
        _write_exclusive(path, frozen_snapshots[label].payload)

    all_snapshots: list[Snapshot] = [coverage_snapshot]
    for index, candidate in enumerate(CANDIDATES):
        property_path, registry_path, execution_path, receipt_path = (
            PROPERTY_EVIDENCE_FILES[candidate]
        )
        property_snapshot = closure.snapshot(
            property_path,
            label=f"{candidate.upper()}_COVERAGE_PROPERTY",
        )
        registry_snapshot = closure.snapshot(
            registry_path,
            label=f"{candidate.upper()}_COVERAGE_REGISTRY",
        )
        execution_snapshot = closure.snapshot(
            execution_path,
            label=f"{candidate.upper()}_COVERAGE_EXECUTION",
        )
        receipt_snapshot = closure.snapshot(
            receipt_path,
            label=f"{candidate.upper()}_COVERAGE_RECEIPT",
        )
        property_document = _json_snapshot(
            property_snapshot,
            label=f"{candidate.upper()}_COVERAGE_PROPERTY",
        )
        registry_document = _json_snapshot(
            registry_snapshot,
            label=f"{candidate.upper()}_COVERAGE_REGISTRY",
        )
        execution_document = _json_snapshot(
            execution_snapshot,
            label=f"{candidate.upper()}_COVERAGE_EXECUTION",
        )
        receipt = _json_snapshot(
            receipt_snapshot,
            label=f"{candidate.upper()}_COVERAGE_RECEIPT",
        )
        if any(
            snapshot.payload != _canonical_json_bytes(document)
            for snapshot, document in (
                (property_snapshot, property_document),
                (registry_snapshot, registry_document),
                (execution_snapshot, execution_document),
                (receipt_snapshot, receipt),
            )
        ):
            raise EvidenceAssemblyError(
                f"{candidate.upper()}_COVERAGE_JSON_NOT_CANONICAL"
            )
        try:
            derived = coverage_gate.validate_candidate_coverage(
                implementation_label=candidate,
                property_plan_path=frozen_paths["property-plan"],
                function_registry_path=frozen_paths["function-registry"],
                error_registry_path=frozen_paths["error-registry"],
                property_report=property_document,
                registry_report=registry_document,
                execution_report=execution_document,
            )
        except (coverage_gate.CoverageError, OSError, ValueError) as exc:
            raise EvidenceAssemblyError(
                f"{candidate.upper()}_COVERAGE_PRODUCER_GRAPH_INVALID"
            ) from exc
        runner = _git_blob_snapshot(
            repository_root,
            subject,
            PROPERTY_RUNNERS[candidate],
            label=f"{candidate.upper()}_COVERAGE_RUNNER",
        )
        expected_source_closure = (
            _scala_property_source_closure_sha256(
                repository_root,
                subject=subject,
                manifest_files=toolchains["scala"]["sources"],
            )
            if candidate == "scala"
            else _haskell_property_source_closure_sha256(
                repository_root,
                subject=subject,
                manifest_files=toolchains["haskell"]["sources"],
                generated_cabal_sha256=str(
                    toolchains["haskell"]["generatedCabal"]["sha256"]
                ),
            )
        )
        selected_profile = str(toolchains[candidate]["profileId"])
        expected_toolchain_profile = (
            selected_profile
            if candidate == "scala"
            else f"haskell-ghc-9.10.3-{selected_profile}"
        )
        expected_implementation = (
            "scala-3.8.4-jvm25" if candidate == "scala" else "haskell"
        )
        receipt_runner = _require_object(
            receipt.get("runner"),
            required={"sha256", "commandArgvSha256"},
            label=f"{candidate.upper()}_COVERAGE_RECEIPT_RUNNER",
            exact=True,
        )
        artifacts = receipt.get("artifacts")
        expected_artifacts = [
            {
                "path": snapshot.relative_path.name,
                "sha256": snapshot.sha256,
                "sizeBytes": snapshot.size_bytes,
            }
            for snapshot in (
                property_snapshot,
                registry_snapshot,
                execution_snapshot,
            )
        ]
        execution_started = _utc_timestamp(
            execution_document.get("startedAt"),
            label=f"{candidate.upper()}_COVERAGE_EXECUTION_TIMESTAMP",
        )
        execution_finished = _utc_timestamp(
            execution_document.get("finishedAt"),
            label=f"{candidate.upper()}_COVERAGE_EXECUTION_TIMESTAMP",
        )
        receipt_schema = receipt.get("schemaVersion")
        extra_receipt_snapshots: list[Snapshot] = []
        if receipt_schema == "s1.4x-property-execution-receipt-v1":
            process = _require_object(
                receipt.get("process"),
                required={
                    "startedAt",
                    "finishedAt",
                    "exitCode",
                    "stdoutSha256",
                    "stderrSha256",
                },
                label=f"{candidate.upper()}_COVERAGE_RECEIPT_PROCESS",
                exact=True,
            )
            receipt_started = _utc_timestamp(
                process.get("startedAt"),
                label=f"{candidate.upper()}_COVERAGE_RECEIPT_TIMESTAMP",
            )
            receipt_finished = _utc_timestamp(
                process.get("finishedAt"),
                label=f"{candidate.upper()}_COVERAGE_RECEIPT_TIMESTAMP",
            )
            expected_receipt_fields = {
                "schemaVersion",
                "candidate",
                "runner",
                "process",
                "artifacts",
                "coverage",
                "status",
            }
            receipt_process_valid = (
                process.get("exitCode") == 0
                and _is_sha256(process.get("stdoutSha256"))
                and _is_sha256(process.get("stderrSha256"))
            )
            receipt_timeline_valid = (
                receipt_started
                <= execution_started
                <= execution_finished
                <= receipt_finished
            )
        elif (
            candidate == "haskell"
            and receipt_schema == "s1.4x-property-execution-receipt-v2"
        ):
            process = _require_object(
                receipt.get("process"),
                required={
                    "startedAt",
                    "finishedAt",
                    "exitCode",
                    "stdout",
                    "stderr",
                },
                label="HASKELL_COVERAGE_RECEIPT_PROCESS",
                exact=True,
            )
            completion = _require_object(
                receipt.get("completion"),
                required={"reason", "process", "artifact", "status"},
                label="HASKELL_COVERAGE_COMPLETION",
                exact=True,
            )
            completion_process = _require_object(
                completion.get("process"),
                required={
                    "commandArgvSha256",
                    "portableArgv",
                    "portableArgvSha256",
                    "startedAt",
                    "finishedAt",
                    "exitCode",
                    "stdout",
                    "stderr",
                },
                label="HASKELL_COVERAGE_COMPLETION_PROCESS",
                exact=True,
            )
            completion_artifact = _require_object(
                completion.get("artifact"),
                required={"path", "sha256", "sizeBytes"},
                label="HASKELL_COVERAGE_COMPLETION_ARTIFACT",
                exact=True,
            )
            process_stdout = _coverage_stream_snapshot(
                closure,
                receipt_path=receipt_path,
                reference=process.get("stdout"),
                expected_name="haskell-coverage-receipt.process.stdout",
                label="HASKELL_COVERAGE_PROCESS_STDOUT",
            )
            process_stderr = _coverage_stream_snapshot(
                closure,
                receipt_path=receipt_path,
                reference=process.get("stderr"),
                expected_name="haskell-coverage-receipt.process.stderr",
                label="HASKELL_COVERAGE_PROCESS_STDERR",
            )
            completion_stdout = _coverage_stream_snapshot(
                closure,
                receipt_path=receipt_path,
                reference=completion_process.get("stdout"),
                expected_name=(
                    "haskell-coverage-receipt."
                    "generated-cabal-completion.stdout"
                ),
                label="HASKELL_COVERAGE_COMPLETION_STDOUT",
            )
            completion_stderr = _coverage_stream_snapshot(
                closure,
                receipt_path=receipt_path,
                reference=completion_process.get("stderr"),
                expected_name=(
                    "haskell-coverage-receipt."
                    "generated-cabal-completion.stderr"
                ),
                label="HASKELL_COVERAGE_COMPLETION_STDERR",
            )
            extra_receipt_snapshots.extend(
                (
                    process_stdout,
                    process_stderr,
                    completion_stdout,
                    completion_stderr,
                )
            )
            receipt_started = _utc_timestamp(
                process.get("startedAt"),
                label="HASKELL_COVERAGE_RECEIPT_TIMESTAMP",
            )
            receipt_finished = _utc_timestamp(
                process.get("finishedAt"),
                label="HASKELL_COVERAGE_RECEIPT_TIMESTAMP",
            )
            completion_started = _utc_timestamp(
                completion_process.get("startedAt"),
                label="HASKELL_COVERAGE_COMPLETION_TIMESTAMP",
            )
            completion_finished = _utc_timestamp(
                completion_process.get("finishedAt"),
                label="HASKELL_COVERAGE_COMPLETION_TIMESTAMP",
            )
            generated_cabal = toolchains["haskell"]["generatedCabal"]
            generated_receipt = generated_cabal["receipt"]
            generated_document = generated_cabal["document"]
            profile_options = execution_document.get("profileGhcOptions")
            stack_root_path_id = execution_document.get("stackRootPathId")
            build_argv_sha256 = execution_document.get("buildArgvSha256")
            if (
                profile_options not in (["-O0", "-fasm"], ["-O2", "-fasm"])
                or not isinstance(stack_root_path_id, str)
                or not isinstance(build_argv_sha256, str)
            ):
                raise EvidenceAssemblyError(
                    "HASKELL_COVERAGE_RECEIPT_INVALID"
                )
            expected_portable_argv = _haskell_completion_portable_argv(
                subject=subject,
                cabal_sha256=str(generated_cabal["sha256"]),
                profile_options=profile_options,
                stack_root_path_id=stack_root_path_id,
                build_argv_sha256=build_argv_sha256,
            )
            try:
                completion_stdout_document = strict_json_load(
                    completion_stdout.payload
                )
            except (UnicodeError, ValueError) as exc:
                raise EvidenceAssemblyError(
                    "HASKELL_COVERAGE_COMPLETION_STDOUT_INVALID"
                ) from exc
            expected_receipt_fields = {
                "schemaVersion",
                "candidate",
                "runner",
                "process",
                "completion",
                "artifacts",
                "coverage",
                "status",
            }
            receipt_process_valid = (
                process.get("exitCode") == 2
                and process_stdout.payload == b""
                and process_stderr.payload.endswith(
                    HASKELL_GHC_OPTION_ARGPARSE_FAILURE
                )
                and completion.get("reason")
                == "ARGPARSE_DASH_PREFIXED_GHC_OPTION"
                and completion.get("status") == "PASS"
                and completion_process.get("exitCode") == 0
                and _is_sha256(completion_process.get("commandArgvSha256"))
                and completion_process.get("portableArgv")
                == expected_portable_argv
                and completion_process.get("portableArgvSha256")
                == _sha256_bytes(
                    _canonical_json_bytes(expected_portable_argv)[:-1]
                )
                and completion_stdout_document == generated_document
                and completion_stdout.payload
                == (
                    json.dumps(
                        generated_document,
                        allow_nan=False,
                        sort_keys=True,
                    )
                    + "\n"
                ).encode("utf-8")
                and completion_stderr.payload == b""
                and completion_artifact
                == {
                    "path": generated_receipt.relative_path.name,
                    "sha256": generated_receipt.sha256,
                    "sizeBytes": generated_receipt.size_bytes,
                }
            )
            receipt_timeline_valid = (
                receipt_started
                <= execution_started
                <= execution_finished
                <= receipt_finished
                <= completion_started
                <= completion_finished
            )
        else:
            raise EvidenceAssemblyError(
                f"{candidate.upper()}_COVERAGE_RECEIPT_INVALID"
            )
        if (
            set(receipt) != expected_receipt_fields
            or receipt.get("candidate") != candidate
            or receipt.get("status") != "PASS"
            or receipt_runner["sha256"] != runner.sha256
            or receipt_runner["commandArgvSha256"]
            != execution_document.get(
                "commandArgvSha256"
                if candidate == "scala"
                else "outerCommandArgvSha256"
            )
            or not _is_sha256(receipt_runner["commandArgvSha256"])
            or not receipt_process_valid
            or not receipt_timeline_valid
            or artifacts != expected_artifacts
            or receipt.get("coverage") != derived
            or coverage_document["candidates"][index] != derived
            or property_document.get("implementation")
            != expected_implementation
            or execution_document.get("implementation")
            != expected_implementation
            or execution_document.get("runnerSha256") != runner.sha256
            or execution_document.get("sourceClosureSha256")
            != expected_source_closure
            or (
                candidate == "haskell"
                and toolchains["haskell"]["generatedCabal"]["document"].get(
                    "propertyClosureSha256"
                )
                != expected_source_closure
            )
            or (
                candidate == "haskell"
                and (
                    execution_document.get("stackRootPathId")
                    != toolchains["haskell"]["generatedCabal"]["document"]
                    .get("build", {})
                    .get("stackRootPathId")
                    or execution_document.get("buildArgvSha256")
                    != toolchains["haskell"]["generatedCabal"]["document"]
                    .get("build", {})
                    .get("runtimeArgvSha256")
                )
            )
            or execution_document.get("toolchainProfile")
            != expected_toolchain_profile
        ):
            raise EvidenceAssemblyError(
                f"{candidate.upper()}_COVERAGE_RECEIPT_INVALID"
            )
        all_snapshots.extend(
            (
                property_snapshot,
                registry_snapshot,
                execution_snapshot,
                receipt_snapshot,
                runner,
                *extra_receipt_snapshots,
                *frozen_snapshots.values(),
            )
        )
    frozen_directory.cleanup()
    return coverage_snapshot, coverage_document, tuple(all_snapshots)


def _validate_toolchains(
    closure: RawClosure,
    repository: Mapping[str, Snapshot],
    *,
    repository_root: Path,
    subject: str,
) -> dict[str, dict[str, Any]]:
    scala_sources = _validate_tracked_source_manifest(
        repository_root,
        repository,
        subject=subject,
        language="scala",
    )
    haskell_sources = _validate_tracked_source_manifest(
        repository_root,
        repository,
        subject=subject,
        language="haskell",
    )
    generated_cabal = _validate_haskell_generated_cabal_provenance(
        closure,
        repository_root,
        repository,
        subject=subject,
        manifest_files=haskell_sources,
    )
    provenance = _json_snapshot(
        repository["toolchain-provenance"],
        label="TOOLCHAIN_PROVENANCE",
    )
    if (
        repository["toolchain-provenance"].sha256
        != final_audit.FROZEN_MERGED_TOOLCHAIN_PROVENANCE_SHA256
        or provenance.get("schemaVersion")
        != "s1.4x-toolchain-provenance-v1"
    ):
        raise EvidenceAssemblyError("TOOLCHAIN_PROVENANCE_INVALID")

    scala_lock = _json_snapshot(
        repository["scala-toolchain-lock"],
        label="SCALA_TOOLCHAIN_LOCK",
    )
    try:
        scala_cli_sha = scala_lock["scalaCli"]["binarySha256"]
        scalafix_sha = scala_lock["scalafix"]["binarySha256"]
        java_sha = scala_lock["jdk"]["javaExecutableSha256"]
    except (KeyError, TypeError) as exc:
        raise EvidenceAssemblyError("SCALA_TOOLCHAIN_LOCK_INVALID") from exc
    if (
        scala_lock.get("schemaVersion") != "s1.4x-scala-toolchain-lock-v1"
        or scala_lock.get("mergedToolchainProvenanceSha256")
        != final_audit.FROZEN_MERGED_TOOLCHAIN_PROVENANCE_SHA256
        or scala_cli_sha != final_audit.FROZEN_SCALA_CLI_SHA256
        or scalafix_sha != final_audit.FROZEN_SCALAFIX_SHA256
        or not _is_sha256(java_sha)
    ):
        raise EvidenceAssemblyError("SCALA_TOOLCHAIN_LOCK_INVALID")
    scala_selected_snapshot = closure.snapshot(
        RAW_PATHS["scala-selected-profile"],
        label="SCALA_SELECTED_PROFILE",
    )
    scala_selected = _json_snapshot(
        scala_selected_snapshot,
        label="SCALA_SELECTED_PROFILE",
    )
    scala_profile_id = scala_selected.get("selectedProfileId")
    selected_fields = {
        "schemaVersion",
        "benchmarkPlanSha256",
        "selectorConfigSha256",
        "qualificationSha256",
        "sourceInputManifestSha256",
        "compilerProfilesSha256",
        "toolchainLockSha256",
        "mergedToolchainProvenanceSha256",
        "scalaCliBinarySha256",
        "javaExecutableSha256",
        "jvmArgumentAllowlistSha256",
        "effectiveJvmArgumentsCapabilitySha256",
        "profileOptionsSha256",
        "selectedProfileSourceSha256",
        "selectedProfileOptions",
        "selectedProfileOptionsSha256",
        "correctnessResultSha256",
        "profiles",
        "selectedProfileId",
        "fallbackProfileId",
        "fallbackExecuted",
        "selectionStatus",
    }
    compiler_profiles = _json_snapshot(
        repository["scala-compiler-profiles"],
        label="SCALA_COMPILER_PROFILES",
    )
    try:
        selected_options = compiler_profiles["profiles"][scala_profile_id][
            "additionalOptions"
        ]
    except (KeyError, TypeError) as exc:
        raise EvidenceAssemblyError(
            "SCALA_COMPILER_PROFILES_INVALID"
        ) from exc
    if (
        set(scala_selected) != selected_fields
        or
        scala_selected.get("schemaVersion")
        != "s1.4x-scala-selected-profile-result-v1"
        or scala_selected.get("selectionStatus") != "PASS"
        or scala_profile_id not in {"A", "B", "C"}
        or scala_selected.get("toolchainLockSha256")
        != repository["scala-toolchain-lock"].sha256
        or scala_selected.get("mergedToolchainProvenanceSha256")
        != final_audit.FROZEN_MERGED_TOOLCHAIN_PROVENANCE_SHA256
        or scala_selected.get("scalaCliBinarySha256")
        != final_audit.FROZEN_SCALA_CLI_SHA256
        or scala_selected.get("javaExecutableSha256") != java_sha
        or scala_selected.get("benchmarkPlanSha256")
        != repository["benchmark-plan"].sha256
        or scala_selected.get("sourceInputManifestSha256")
        != repository["scala-source-inputs"].sha256
        or scala_selected.get("compilerProfilesSha256")
        != repository["scala-compiler-profiles"].sha256
        or scala_selected.get("selectedProfileSourceSha256")
        != repository["scala-selected-source"].sha256
        or scala_selected.get("selectedProfileOptions") != selected_options
        or scala_selected.get("selectedProfileOptionsSha256")
        != _sha256_bytes(_canonical_json_bytes(selected_options)[:-1])
        or scala_selected.get("fallbackProfileId") != "A"
        or scala_selected.get("fallbackExecuted")
        is not (scala_profile_id == "A")
        or any(
            not _is_sha256(scala_selected.get(field))
            for field in (
                "selectorConfigSha256",
                "qualificationSha256",
                "jvmArgumentAllowlistSha256",
                "effectiveJvmArgumentsCapabilitySha256",
                "profileOptionsSha256",
            )
        )
        or not _is_sha256(scala_selected.get("correctnessResultSha256"))
    ):
        raise EvidenceAssemblyError("SCALA_SELECTED_PROFILE_INVALID")
    scala_correctness = closure.snapshot(
        Path(
            f"scala/profiles/{scala_profile_id}/"
            "scala-profile-correctness-result.v1.json"
        ),
        label="SCALA_SELECTED_CORRECTNESS",
    )
    scala_correctness_document = _json_snapshot(
        scala_correctness,
        label="SCALA_SELECTED_CORRECTNESS",
    )
    profile_inputs = [
        path
        for path, entry in scala_sources.items()
        if entry["role"] != "benchmark"
    ]
    scala_candidate = closure.snapshot(
        Path(f"scala/profiles/{scala_profile_id}/candidate.jar"),
        label="SCALA_SELECTED_CANDIDATE",
    )
    if (
        scala_correctness.sha256
        != scala_selected["correctnessResultSha256"]
        or scala_correctness_document.get("schemaVersion")
        != "s1.4x-scala-profile-correctness-v1"
        or scala_correctness_document.get("profileId") != scala_profile_id
        or scala_correctness_document.get("toolchainLockSha256")
        != repository["scala-toolchain-lock"].sha256
        or scala_correctness_document.get("scalaCliBinarySha256")
        != final_audit.FROZEN_SCALA_CLI_SHA256
        or scala_correctness_document.get("sourceInputManifestSha256")
        != repository["scala-source-inputs"].sha256
        or scala_correctness_document.get("compilerProfilesSha256")
        != repository["scala-compiler-profiles"].sha256
        or scala_correctness_document.get("profileOptions") != selected_options
        or scala_correctness_document.get("profileRunInputPaths")
        != profile_inputs
        or scala_correctness_document.get("candidateSha256")
        != scala_candidate.sha256
        or scala_correctness_document.get("mismatchCount") != 0
        or scala_correctness_document.get("status") != "PASS"
    ):
        raise EvidenceAssemblyError("SCALA_SELECTED_CORRECTNESS_INVALID")
    scala_matrix = _require_object(
        scala_correctness_document.get("matrix"),
        required={
            "candidateResultSha256",
            "semanticResultSha256",
            "unitTestResultSha256",
            "unitStdoutSha256",
            "unitStderrSha256",
            "canonicalComparisonSha256",
            "semanticComparisonSha256",
            "propertyReportSha256",
            "registryReportSha256",
            "propertyExecutionEvidenceSha256",
            "propertyPlanSha256",
            "propertySeedCorpusSha256",
            "functionRegistrySha256",
            "errorRegistrySha256",
        },
        label="SCALA_SELECTED_CORRECTNESS_MATRIX",
        exact=True,
    )
    matrix_paths = {
        "candidateResultSha256": "canonical-results.json",
        "semanticResultSha256": "semantic-errors.json",
        "unitTestResultSha256": "scala-profile-unit-test-result.v1.json",
        "unitStdoutSha256": "unit-test.stdout",
        "unitStderrSha256": "unit-test.stderr",
        "canonicalComparisonSha256": "canonical-comparison.json",
        "semanticComparisonSha256": "semantic-comparison.json",
        "propertyReportSha256": "property/scala-property-report.v1.json",
        "registryReportSha256": "property/scala-registry-report.v1.json",
        "propertyExecutionEvidenceSha256": (
            "property/scala-property-execution-evidence.v1.json"
        ),
    }
    for field, suffix in matrix_paths.items():
        artifact = closure.snapshot(
            Path(f"scala/profiles/{scala_profile_id}") / suffix,
            label="SCALA_SELECTED_CORRECTNESS_ARTIFACT",
        )
        if scala_matrix[field] != artifact.sha256:
            raise EvidenceAssemblyError(
                "SCALA_SELECTED_CORRECTNESS_ARTIFACT_INVALID"
            )
    for matrix, request_id, actual_name, comparison_name in (
        (
            "canonical",
            "s1.4x-canonical-small-v1",
            "canonical-results.json",
            "canonical-comparison.json",
        ),
        (
            "semantic",
            "s1.4x-semantic-errors-v1",
            "semantic-errors.json",
            "semantic-comparison.json",
        ),
    ):
        actual = closure.snapshot(
            Path(f"scala/profiles/{scala_profile_id}/{actual_name}"),
            label=f"SCALA_SELECTED_{matrix.upper()}_ACTUAL",
        )
        actual_document = _json_snapshot(
            actual,
            label=f"SCALA_SELECTED_{matrix.upper()}_ACTUAL",
        )
        _validate_comparison(
            closure,
            Path(f"scala/profiles/{scala_profile_id}/{comparison_name}"),
            label=f"SCALA_SELECTED_{matrix.upper()}_COMPARISON",
            expected_request_id=request_id,
        )
        if (
            actual_document.get("requestId") != request_id
            or actual_document.get("implementation") != "scala-3.8.4-jvm25"
        ):
            raise EvidenceAssemblyError(
                f"SCALA_SELECTED_{matrix.upper()}_ACTUAL_INVALID"
            )
    frozen_matrix_hashes = {
        "propertyPlanSha256": _git_blob_snapshot(
            repository_root,
            subject,
            S1_4X_RELATIVE / "contract/property-plan.v1.json",
            label="SCALA_PROPERTY_PLAN",
        ).sha256,
        "propertySeedCorpusSha256": repository["property-seeds"].sha256,
        "functionRegistrySha256": _git_blob_snapshot(
            repository_root,
            subject,
            S1_4X_RELATIVE / "contract/function-registry.v1.json",
            label="SCALA_FUNCTION_REGISTRY",
        ).sha256,
        "errorRegistrySha256": _git_blob_snapshot(
            repository_root,
            subject,
            S1_4X_RELATIVE / "contract/error-registry.v1.json",
            label="SCALA_ERROR_REGISTRY",
        ).sha256,
    }
    if any(
        scala_matrix[field] != digest
        for field, digest in frozen_matrix_hashes.items()
    ):
        raise EvidenceAssemblyError(
            "SCALA_SELECTED_CORRECTNESS_FROZEN_INPUT_INVALID"
        )

    haskell_lock = _json_snapshot(
        repository["haskell-toolchain-lock"],
        label="HASKELL_TOOLCHAIN_LOCK",
    )
    try:
        resolved_tools = haskell_lock["resolvedTools"]
        ghcup_sha = resolved_tools["ghcup"]["sha256"]
        stack_sha = resolved_tools["stack"]["sha256"]
        authoritative_ghc_sha = resolved_tools["authoritativeGhc"]["sha256"]
        provenance_sha = haskell_lock["mergedToolchainProvenance"]["sha256"]
    except (KeyError, TypeError) as exc:
        raise EvidenceAssemblyError("HASKELL_TOOLCHAIN_LOCK_INVALID") from exc
    if (
        haskell_lock.get("schemaVersion")
        != "s1.4x-haskell-toolchain-lock-v1"
        or ghcup_sha != final_audit.FROZEN_GHCUP_SHA256
        or stack_sha != final_audit.FROZEN_STACK_SHA256
        or not _is_sha256(authoritative_ghc_sha)
        or provenance_sha
        != final_audit.FROZEN_MERGED_TOOLCHAIN_PROVENANCE_SHA256
    ):
        raise EvidenceAssemblyError("HASKELL_TOOLCHAIN_LOCK_INVALID")
    haskell_selected = _json_snapshot(
        repository["haskell-selected-profile"],
        label="HASKELL_SELECTED_PROFILE",
    )
    haskell_profile_id = haskell_selected.get("profileId")
    haskell_source_tree_sha256 = _haskell_source_tree_sha256(
        repository_root,
        subject=subject,
        manifest_files=haskell_sources,
        generated_cabal_sha256=str(generated_cabal["sha256"]),
    )
    haskell_options = (
        ["-O0", "-fasm"]
        if haskell_profile_id == "baseline-o0-fasm"
        else ["-O2", "-fasm"]
    )
    if (
        set(haskell_selected)
        != {
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
        or
        haskell_selected.get("schemaVersion")
        != "s1.4x-haskell-selected-profile-v1"
        or haskell_profile_id
        not in {"baseline-o0-fasm", "optimized-o2-fasm"}
        or haskell_selected.get("compilerVersion") != "9.10.3"
        or haskell_selected.get("compilerSha256") != authoritative_ghc_sha
        or haskell_selected.get("fallbackProfile") != "baseline-o0-fasm"
        or haskell_selected.get("selectedBy")
        not in {"frozen-criterion-selector", "proven-fallback"}
        or haskell_selected.get("ghcOptions") != haskell_options
        or haskell_selected.get("optionsSha256")
        != _sha256_bytes(_canonical_json_bytes(haskell_options)[:-1])
        or haskell_selected.get("sourceTreeSha256")
        != haskell_source_tree_sha256
        or generated_cabal["document"].get("sourceTreeSha256")
        != haskell_source_tree_sha256
        or haskell_selected.get("qualificationPlanSha256")
        != repository["benchmark-plan"].sha256
        or any(
            not _is_sha256(haskell_selected.get(field))
            for field in (
                "fullCorrectnessSha256",
                "qualificationArtifactSha256",
                "selectorConfigSha256",
            )
        )
    ):
        raise EvidenceAssemblyError("HASKELL_SELECTED_PROFILE_INVALID")
    haskell_correctness = closure.snapshot(
        Path(
            f"haskell/profiles/{haskell_profile_id}/"
            "correctness-receipt.v1.json"
        ),
        label="HASKELL_SELECTED_CORRECTNESS",
    )
    haskell_correctness_document = _json_snapshot(
        haskell_correctness,
        label="HASKELL_SELECTED_CORRECTNESS",
    )
    comparison_artifacts = haskell_correctness_document.get(
        "comparisonArtifacts"
    )
    commands = haskell_correctness_document.get("commands")
    if (
        haskell_correctness_document.get("schemaVersion")
        != "s1.4x-haskell-full-correctness-v1"
        or haskell_correctness_document.get("candidateSourceCommit") != subject
        or haskell_correctness_document.get("profileId") != haskell_profile_id
        or haskell_correctness_document.get("ghcOptions") != haskell_options
        or haskell_correctness_document.get("optionsSha256")
        != haskell_selected["optionsSha256"]
        or haskell_correctness_document.get("compilerVersion") != "9.10.3"
        or haskell_correctness_document.get("compilerSha256")
        != haskell_selected["compilerSha256"]
        or haskell_correctness_document.get("sourceTreeSha256")
        != haskell_source_tree_sha256
        or not _is_sha256(
            haskell_correctness_document.get("candidateBinarySha256")
        )
        or not isinstance(commands, list)
        or len(commands) != 6
        or not isinstance(comparison_artifacts, list)
        or len(comparison_artifacts) != 2
        or haskell_correctness_document.get("mismatchCount") != 0
        or haskell_correctness_document.get("status") != "PASS"
    ):
        raise EvidenceAssemblyError("HASKELL_SELECTED_CORRECTNESS_INVALID")
    expected_phases = (
        "build",
        "test",
        "canonical-process",
        "canonical-compare",
        "semantic-process",
        "semantic-compare",
    )
    for raw_command, phase in zip(commands, expected_phases, strict=True):
        command = _require_object(
            raw_command,
            required={
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
            },
            label="HASKELL_CORRECTNESS_COMMAND",
            exact=True,
        )
        stdout = closure.snapshot(
            Path(
                f"haskell/profiles/{haskell_profile_id}/{phase}.stdout"
            ),
            label="HASKELL_CORRECTNESS_COMMAND_STDOUT",
        )
        stderr = closure.snapshot(
            Path(
                f"haskell/profiles/{haskell_profile_id}/{phase}.stderr"
            ),
            label="HASKELL_CORRECTNESS_COMMAND_STDERR",
        )
        if (
            command["phase"] != phase
            or command["exitCode"] != 0
            or command["stdoutSha256"] != stdout.sha256
            or command["stderrSha256"] != stderr.sha256
            or not isinstance(command["argv"], list)
            or command["argvSha256"]
            != _sha256_bytes(_canonical_json_bytes(command["argv"])[:-1])
        ):
            raise EvidenceAssemblyError(
                "HASKELL_SELECTED_CORRECTNESS_COMMAND_INVALID"
            )
    expected_comparison_inputs = {
        "canonical": (
            repository["canonical-inputs"].sha256,
            repository["canonical-results"].sha256,
            "s1.4x-canonical-small-v1",
        ),
        "semantic": (
            repository["semantic-inputs"].sha256,
            repository["semantic-results"].sha256,
            "s1.4x-semantic-errors-v1",
        ),
    }
    for raw_comparison, matrix in zip(
        comparison_artifacts,
        ("canonical", "semantic"),
        strict=True,
    ):
        comparison = _require_object(
            raw_comparison,
            required={
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
            },
            label="HASKELL_CORRECTNESS_COMPARISON",
            exact=True,
        )
        request_sha, expected_sha, request_id = expected_comparison_inputs[
            matrix
        ]
        actual = closure.snapshot(
            Path(
                f"haskell/profiles/{haskell_profile_id}/"
                f"{matrix}.actual.json"
            ),
            label="HASKELL_CORRECTNESS_ACTUAL",
        )
        comparison_snapshot, _ = _validate_comparison(
            closure,
            Path(
                f"haskell/profiles/{haskell_profile_id}/"
                f"{matrix}.comparison.json"
            ),
            label="HASKELL_CORRECTNESS_COMPARISON",
            expected_request_id=request_id,
        )
        actual_document = _json_snapshot(
            actual,
            label="HASKELL_CORRECTNESS_ACTUAL",
        )
        if (
            comparison["matrixId"] != matrix
            or comparison["requestSha256"] != request_sha
            or comparison["expectedSha256"] != expected_sha
            or comparison["actualSha256"] != actual.sha256
            or comparison["comparisonSha256"] != comparison_snapshot.sha256
            or comparison["mismatchCount"] != 0
            or comparison["status"] != "PASS"
            or actual_document.get("requestId") != request_id
            or actual_document.get("implementation")
            != "haskell-ghc-9.10.3"
        ):
            raise EvidenceAssemblyError(
                "HASKELL_SELECTED_CORRECTNESS_COMPARISON_INVALID"
            )
    return {
        "scala": {
            "lock": repository["scala-toolchain-lock"],
            "selected": scala_selected_snapshot,
            "selectedDocument": scala_selected,
            "correctness": scala_correctness,
            "candidate": scala_candidate,
            "profileId": scala_profile_id,
            "sources": scala_sources,
            "binaries": {
                "scalaCli": scala_cli_sha,
                "scalafix": scalafix_sha,
            },
        },
        "haskell": {
            "lock": repository["haskell-toolchain-lock"],
            "selected": repository["haskell-selected-profile"],
            "selectedDocument": haskell_selected,
            "correctness": haskell_correctness,
            "profileId": haskell_profile_id,
            "sources": haskell_sources,
            "generatedCabal": generated_cabal,
            "binaries": {
                "ghcup": ghcup_sha,
                "stack": stack_sha,
            },
        },
    }


def _validate_scala_oci(
    closure: RawClosure,
    *,
    selected_candidate: Snapshot,
    containerfile: Snapshot,
    fixture_tree_sha256: str,
) -> tuple[Snapshot, Snapshot]:
    build = closure.snapshot(RAW_PATHS["scala-oci-build"], label="SCALA_OCI_BUILD")
    build_document = _json_snapshot(build, label="SCALA_OCI_BUILD")
    build_fields = {
        "schemaVersion",
        "baseImageReference",
        "baseImageReferenceSource",
        "baseImageId",
        "candidateSha256",
        "containerfileSha256",
        "fixtureTreeSha256",
        "imageId",
        "localTag",
        "dockerIdentity",
        "inspectedLabels",
        "buildNetwork",
        "pull",
        "buildUsedIidfile",
        "aggregateStatus",
    }
    docker_identity_fields = {
        "dockerCliPathId",
        "dockerCliSha256",
        "contextName",
        "daemonId",
        "serverVersion",
        "operatingSystem",
        "architecture",
    }
    docker_identity = build_document.get("dockerIdentity")
    expected_labels = {
        "org.opencontainers.image.s1-4x.candidate-sha256": (
            build_document.get("candidateSha256")
        ),
        "org.opencontainers.image.s1-4x.base-reference": (
            build_document.get("baseImageReference")
        ),
        "org.opencontainers.image.s1-4x.base-image-id": (
            build_document.get("baseImageId")
        ),
        "org.opencontainers.image.s1-4x.containerfile-sha256": (
            build_document.get("containerfileSha256")
        ),
        "org.opencontainers.image.s1-4x.fixture-tree-sha256": (
            build_document.get("fixtureTreeSha256")
        ),
    }
    if (
        set(build_document) != build_fields
        or
        build_document.get("schemaVersion")
        != "s1.4x-scala-oci-build-result-v2"
        or build_document.get("baseImageReferenceSource")
        != "caller-digest-argument"
        or re.fullmatch(
            r"[^@\s]+@sha256:[0-9a-f]{64}",
            str(build_document.get("baseImageReference")),
        )
        is None
        or re.fullmatch(
            r"sha256:[0-9a-f]{64}",
            str(build_document.get("baseImageId")),
        )
        is None
        or re.fullmatch(
            r"sha256:[0-9a-f]{64}",
            str(build_document.get("imageId")),
        )
        is None
        or build_document.get("candidateSha256")
        != selected_candidate.sha256
        or build_document.get("containerfileSha256") != containerfile.sha256
        or build_document.get("fixtureTreeSha256") != fixture_tree_sha256
        or not isinstance(docker_identity, dict)
        or set(docker_identity) != docker_identity_fields
        or not _is_sha256(docker_identity.get("dockerCliSha256"))
        or build_document.get("inspectedLabels") != expected_labels
        or build_document.get("buildNetwork") != "none"
        or build_document.get("pull") is not False
        or build_document.get("buildUsedIidfile") is not True
        or build_document.get("aggregateStatus") != "PASS"
    ):
        raise EvidenceAssemblyError("SCALA_OCI_BUILD_INVALID")
    binding_before = closure.snapshot(
        Path("oci/scala/runtime/oci-runtime-binding-before.v1.json"),
        label="SCALA_OCI_RUNTIME_BINDING",
    )
    binding_after = closure.snapshot(
        Path("oci/scala/runtime/oci-runtime-binding-after.v1.json"),
        label="SCALA_OCI_RUNTIME_BINDING",
    )
    binding = _json_snapshot(
        binding_before,
        label="SCALA_OCI_RUNTIME_BINDING",
    )
    if (
        binding_before.payload != binding_after.payload
        or set(binding)
        != {
            "schemaVersion",
            "imageId",
            "buildReceiptSha256",
            "candidateSha256",
            "baseImageReference",
            "baseImageId",
            "dockerIdentity",
            "status",
        }
        or binding.get("schemaVersion")
        != "s1.4x-scala-oci-runtime-binding-v1"
        or binding.get("imageId") != build_document["imageId"]
        or binding.get("buildReceiptSha256") != build.sha256
        or binding.get("candidateSha256") != selected_candidate.sha256
        or binding.get("baseImageReference")
        != build_document["baseImageReference"]
        or binding.get("baseImageId") != build_document["baseImageId"]
        or binding.get("dockerIdentity") != docker_identity
        or binding.get("status") != "PASS"
    ):
        raise EvidenceAssemblyError("SCALA_OCI_RUNTIME_BINDING_INVALID")
    runtime = closure.snapshot(
        RAW_PATHS["scala-oci-runtime"],
        label="SCALA_OCI_RUNTIME",
    )
    runtime_document = _json_snapshot(runtime, label="SCALA_OCI_RUNTIME")
    runtime_fields = {
        "schemaVersion",
        "imageId",
        "buildReceiptSha256",
        "candidateSha256",
        "baseImageReference",
        "baseImageId",
        "dockerIdentity",
        "dockerIdentitySha256",
        "runtimeNetwork",
        "readOnlyRoot",
        "capabilitiesDropped",
        "sourceTreeMounted",
        "userHomeMounted",
        "credentialMounted",
        "canonicalResultSha256",
        "semanticResultSha256",
        "canonicalComparisonSha256",
        "semanticComparisonSha256",
        "runtimeBindingSha256",
        "mismatchCount",
        "aggregateStatus",
    }
    if (
        set(runtime_document) != runtime_fields
        or
        runtime_document.get("schemaVersion")
        != "s1.4x-scala-oci-correctness-result-v2"
        or runtime_document.get("imageId") != build_document["imageId"]
        or runtime_document.get("buildReceiptSha256") != build.sha256
        or runtime_document.get("candidateSha256") != selected_candidate.sha256
        or runtime_document.get("baseImageReference")
        != build_document["baseImageReference"]
        or runtime_document.get("baseImageId") != build_document["baseImageId"]
        or runtime_document.get("dockerIdentity") != docker_identity
        or runtime_document.get("dockerIdentitySha256")
        != _sha256_bytes(_canonical_json_bytes(docker_identity)[:-1])
        or runtime_document.get("runtimeNetwork") != "none"
        or runtime_document.get("readOnlyRoot") is not True
        or runtime_document.get("capabilitiesDropped") != "ALL"
        or runtime_document.get("sourceTreeMounted") is not False
        or runtime_document.get("userHomeMounted") is not False
        or runtime_document.get("credentialMounted") is not False
        or runtime_document.get("runtimeBindingSha256")
        != binding_before.sha256
        or runtime_document.get("mismatchCount") != 0
        or runtime_document.get("aggregateStatus") != "PASS"
    ):
        raise EvidenceAssemblyError("SCALA_OCI_RUNTIME_INVALID")
    for matrix, request_id, result_field, comparison_field, result_name in (
        (
            "canonical",
            "s1.4x-canonical-small-v1",
            "canonicalResultSha256",
            "canonicalComparisonSha256",
            "canonical-results.json",
        ),
        (
            "semantic",
            "s1.4x-semantic-errors-v1",
            "semanticResultSha256",
            "semanticComparisonSha256",
            "semantic-errors.json",
        ),
    ):
        actual = closure.snapshot(
            Path("oci/scala/runtime") / result_name,
            label=f"SCALA_OCI_{matrix.upper()}_RESULT",
        )
        actual_document = _json_snapshot(
            actual,
            label=f"SCALA_OCI_{matrix.upper()}_RESULT",
        )
        comparison, _ = _validate_comparison(
            closure,
            Path(f"oci/scala/runtime/{matrix}-comparison.json"),
            label=f"SCALA_OCI_{matrix.upper()}_COMPARISON",
            expected_request_id=request_id,
        )
        if (
            runtime_document[result_field] != actual.sha256
            or runtime_document[comparison_field] != comparison.sha256
            or actual_document.get("requestId") != request_id
            or actual_document.get("implementation") != "scala-3.8.4-jvm25"
        ):
            raise EvidenceAssemblyError("SCALA_OCI_RUNTIME_INVALID")
    return build, runtime


def _validate_haskell_oci(
    closure: RawClosure,
    *,
    subject: str,
    selected_profile: Snapshot,
    profile_id: str,
    selected_profile_document: Mapping[str, Any],
    correctness_document: Mapping[str, Any],
    containerfile: Snapshot,
    fixture_tree_sha256: str,
) -> Snapshot:
    receipt = closure.snapshot(RAW_PATHS["haskell-oci"], label="HASKELL_OCI")
    document = _json_snapshot(receipt, label="HASKELL_OCI")
    expected_fields = {
        "schemaVersion",
        "status",
        "candidateSourceCommit",
        "sourceTreeSha256",
        "selectedProfileSha256",
        "profileId",
        "ghcOptions",
        "optionsSha256",
        "containerfileSha256",
        "baseImage",
        "baseImageId",
        "baseInspectionBeforeSha256",
        "baseInspectionAfterSha256",
        "stackRootPath",
        "stackWorkDir",
        "contextSnapshot",
        "fixtureTreeSha256",
        "candidateBinarySha256",
        "dockerPath",
        "dockerPathId",
        "dockerSha256",
        "expectedDockerSha256",
        "dockerConfigPath",
        "dockerTrustBaseline",
        "dockerTrustStageSnapshots",
        "daemonIdentitySha256",
        "dockerContextName",
        "daemonIdentityBefore",
        "daemonIdentityAfter",
        "imageTag",
        "imageId",
        "iidFileSha256",
        "provenanceLabels",
        "platform",
        "runtimeImageSubject",
        "imageTagBindingChecks",
        "buildNetwork",
        "runtimeNetwork",
        "runtimeMounts",
        "commands",
        "comparisons",
        "mismatchCount",
    }
    if (
        set(document) != expected_fields
        or
        document.get("schemaVersion") != "s1.4x-haskell-oci-correctness-v1"
        or document.get("candidateSourceCommit") != subject
        or document.get("sourceTreeSha256")
        != selected_profile_document.get("sourceTreeSha256")
        or document.get("selectedProfileSha256") != selected_profile.sha256
        or document.get("profileId") != profile_id
        or document.get("ghcOptions")
        != selected_profile_document.get("ghcOptions")
        or document.get("optionsSha256")
        != selected_profile_document.get("optionsSha256")
        or document.get("containerfileSha256") != containerfile.sha256
        or document.get("fixtureTreeSha256") != fixture_tree_sha256
        or document.get("candidateBinarySha256")
        != correctness_document.get("candidateBinarySha256")
        or re.fullmatch(
            r"[^@\s]+@sha256:[0-9a-f]{64}",
            str(document.get("baseImage")),
        )
        is None
        or re.fullmatch(
            r"sha256:[0-9a-f]{64}",
            str(document.get("baseImageId")),
        )
        is None
        or re.fullmatch(
            r"sha256:[0-9a-f]{64}",
            str(document.get("imageId")),
        )
        is None
        or document.get("dockerSha256")
        != document.get("expectedDockerSha256")
        or not _is_sha256(document.get("dockerSha256"))
        or document.get("platform") != "linux/amd64"
        or document.get("runtimeImageSubject")
        != {
            "referenceType": "immutable-image-id",
            "imageId": document.get("imageId"),
        }
        or document.get("buildNetwork") != "none"
        or document.get("runtimeNetwork") != "none"
        or document.get("runtimeMounts") != ["output-only"]
        or document.get("mismatchCount") != 0
        or document.get("status") != "PASS"
        or not isinstance(document.get("comparisons"), list)
        or len(document["comparisons"]) != 2
        or not isinstance(document.get("commands"), list)
        or not document["commands"]
    ):
        raise EvidenceAssemblyError("HASKELL_OCI_INVALID")
    for command in document["commands"]:
        if (
            not isinstance(command, dict)
            or command.get("exitCode") != 0
            or not _is_sha256(command.get("stdoutSha256"))
            or not _is_sha256(command.get("stderrSha256"))
        ):
            raise EvidenceAssemblyError("HASKELL_OCI_COMMAND_INVALID")
        phase = command.get("phase")
        if not isinstance(phase, str) or not phase.startswith("oci-"):
            raise EvidenceAssemblyError("HASKELL_OCI_COMMAND_INVALID")
        for suffix, field in (
            ("stdout", "stdoutSha256"),
            ("stderr", "stderrSha256"),
        ):
            artifact = closure.snapshot(
                Path(f"oci/haskell/{phase}.{suffix}"),
                label="HASKELL_OCI_COMMAND_STREAM",
            )
            if artifact.sha256 != command[field]:
                raise EvidenceAssemblyError("HASKELL_OCI_COMMAND_INVALID")
    for entry, matrix in zip(
        document["comparisons"],
        ("canonical", "semantic"),
        strict=True,
    ):
        comparison = _require_object(
            entry,
            required={
                "matrixId",
                "actualSha256",
                "comparisonSha256",
                "mismatchCount",
                "status",
            },
            label="HASKELL_OCI_COMPARISON",
            exact=True,
        )
        actual = closure.snapshot(
            Path(f"oci/haskell/runtime/{matrix}.actual.json"),
            label=f"HASKELL_OCI_{matrix.upper()}_ACTUAL",
        )
        actual_document = _json_snapshot(
            actual,
            label=f"HASKELL_OCI_{matrix.upper()}_ACTUAL",
        )
        request_id = (
            "s1.4x-canonical-small-v1"
            if matrix == "canonical"
            else "s1.4x-semantic-errors-v1"
        )
        raw, _ = _validate_comparison(
            closure,
            Path(f"oci/haskell/{matrix}.oci-comparison.json"),
            label=f"HASKELL_OCI_{matrix.upper()}_COMPARISON",
            expected_request_id=request_id,
        )
        if (
            comparison["matrixId"] != matrix
            or comparison["actualSha256"] != actual.sha256
            or comparison["comparisonSha256"] != raw.sha256
            or comparison["mismatchCount"] != 0
            or comparison["status"] != "PASS"
            or actual_document.get("requestId") != request_id
            or actual_document.get("implementation")
            != "haskell-ghc-9.10.3"
        ):
            raise EvidenceAssemblyError("HASKELL_OCI_COMPARISON_INVALID")
    return receipt


def _validate_scala_rubric_inputs(
    closure: RawClosure,
    *,
    profile_id: str,
    repository: Mapping[str, Snapshot],
) -> dict[str, Snapshot]:
    paths = {
        key: closure.snapshot(RAW_PATHS[key], label=key.upper())
        for key in (
            "scala-source-policy",
            "scala-dependency-edge",
            "scala-format",
            "scala-lint",
        )
    }
    paths["scala-compiler"] = closure.snapshot(
        Path(
            f"scala/hard-compiler-{profile_id}/"
            "scala-hard-compiler-result.v1.json"
        ),
        label="SCALA_COMPILER",
    )
    documents = {
        key: _json_snapshot(snapshot, label=key.upper())
        for key, snapshot in paths.items()
    }
    policy = documents["scala-source-policy"]
    if (
        policy.get("schemaVersion")
        != "s1.4x-scala-source-policy-result-v1"
        or policy.get("checkerMode") != "semanticdb"
        or policy.get("semanticSmokeStatus") != "PASS"
        or not isinstance(policy.get("checkedFiles"), list)
        or not policy["checkedFiles"]
        or policy.get("violations") != []
        or policy.get("staleAllowlistEntries") != []
        or policy.get("sourceSetExact") is not True
        or policy.get("aggregateStatus") != "PASS"
    ):
        raise EvidenceAssemblyError("SCALA_SOURCE_POLICY_INVALID")
    dependencies = documents["scala-dependency-edge"]
    zero_fields = (
        "candidateAddedNativeDependencyCount",
        "candidateAuthoredEdgeCount",
        "candidateCoreDirectNativeBindingCallCount",
        "candidateCoreDirectNativeBindingImportCount",
        "timedKernelExplicitCandidateNativeInteropCallCount",
        "unknownEdgeCount",
    )
    if (
        dependencies.get("schemaVersion")
        != "s1.4x-scala-dependency-native-edge-result-v1"
        or any(dependencies.get(field) != 0 for field in zero_fields)
        or dependencies.get("forbiddenSourceFindings") != []
        or dependencies.get("aggregateStatus") != "PASS"
    ):
        raise EvidenceAssemblyError("SCALA_DEPENDENCY_EDGE_INVALID")
    formatting = documents["scala-format"]
    scala_lock = _json_snapshot(
        repository["scala-toolchain-lock"],
        label="SCALA_TOOLCHAIN_LOCK",
    )
    scalafmt_lock = _require_object(
        scala_lock.get("scalafmt"),
        required={
            "version",
            "configPath",
            "configSha256",
            "runnerPathId",
            "archiveUri",
            "archivePathId",
            "archiveSha256",
            "executablePathId",
            "executableSha256",
            "resolvedVersionOutput",
            "resolutionLogUri",
            "resolutionLogSha256",
            "networkPolicy",
        },
        label="SCALA_SCALAFMT_LOCK",
    )
    scalafmt_artifact = _require_object(
        formatting.get("scalafmtArtifact"),
        required={
            "archiveUri",
            "archivePathId",
            "archiveSha256",
            "executablePathId",
            "executableSha256",
            "resolvedVersionOutput",
            "resolutionLogUri",
            "resolutionLogSha256",
            "networkPolicy",
            "versionOutputSha256",
        },
        label="SCALA_FORMAT_ARTIFACT",
        exact=True,
    )
    copied_check = _require_object(
        formatting.get("copiedNonMutatingCheck"),
        required={
            "downloadLineCount",
            "evidenceSha256",
            "exitCode",
            "portableArgv",
            "portableArgvSha256",
        },
        label="SCALA_FORMAT_COPIED_CHECK",
    )
    if (
        formatting.get("schemaVersion")
        != "s1.4x-scala-scalafmt-idempotence-result-v1"
        or formatting.get("scalafmtVersion") != scalafmt_lock["version"]
        or any(
            scalafmt_artifact[field] != scalafmt_lock[field]
            for field in (
                "archiveUri",
                "archivePathId",
                "archiveSha256",
                "executablePathId",
                "executableSha256",
                "resolvedVersionOutput",
                "resolutionLogUri",
                "resolutionLogSha256",
                "networkPolicy",
            )
        )
        or scalafmt_artifact["versionOutputSha256"]
        != _sha256_bytes(
            str(scalafmt_lock["resolvedVersionOutput"]).encode("utf-8")
        )
        or formatting.get("networkPolicy") != scalafmt_lock["networkPolicy"]
        or formatting.get("configSha256") != scalafmt_lock["configSha256"]
        or formatting.get("sourceInputManifestSha256")
        != repository["scala-source-inputs"].sha256
        or formatting.get("toolchainLockSha256")
        != repository["scala-toolchain-lock"].sha256
        or not _is_exact_int(copied_check.get("exitCode"), 0)
        or not _is_exact_int(copied_check.get("downloadLineCount"), 0)
        or not isinstance(copied_check.get("portableArgv"), list)
        or not copied_check["portableArgv"]
        or any(
            not isinstance(argument, str) or not argument
            for argument in copied_check["portableArgv"]
        )
        or not _is_sha256(copied_check.get("portableArgvSha256"))
        or not _is_sha256(copied_check.get("evidenceSha256"))
        or not isinstance(formatting.get("checkedFiles"), list)
        or not formatting["checkedFiles"]
        or formatting.get("status") != "PASS"
    ):
        raise EvidenceAssemblyError("SCALA_FORMAT_INVALID")
    lint = documents["scala-lint"]
    scalafix = _require_object(
        lint.get("scalafix"),
        required={"binarySha256", "version"},
        label="SCALA_LINT_SCALAFIX",
    )
    if (
        lint.get("schemaVersion")
        != "s1.4x-scala-semantic-policy-receipt-v1"
        or lint.get("sourceInputManifestSha256")
        != repository["scala-source-inputs"].sha256
        or scalafix.get("binarySha256")
        != scala_lock.get("scalafix", {}).get("binarySha256")
        or scalafix.get("version")
        != scala_lock.get("scalafix", {}).get("version")
        or lint.get("checkerMode") != "semanticdb"
        or lint.get("semanticSmokeStatus") != "PASS"
        or not isinstance(lint.get("checkedFiles"), list)
        or not lint["checkedFiles"]
        or lint.get("status") != "PASS"
    ):
        raise EvidenceAssemblyError("SCALA_LINT_INVALID")
    compiler = documents["scala-compiler"]
    if (
        compiler.get("schemaVersion")
        != "s1.4x-scala-hard-compiler-result-v1"
        or compiler.get("profileId") != profile_id
        or not isinstance(compiler.get("compileInputPaths"), list)
        or not compiler["compileInputPaths"]
        or compiler.get("aggregateStatus") != "PASS"
    ):
        raise EvidenceAssemblyError("SCALA_COMPILER_INVALID")
    return paths


def _validate_haskell_rubric_inputs(
    closure: RawClosure,
    *,
    repository: Mapping[str, Snapshot],
) -> dict[str, Snapshot]:
    paths = {
        key: closure.snapshot(RAW_PATHS[key], label=key.upper())
        for key in (
            "haskell-module-safety",
            "haskell-format",
            "haskell-lint",
        )
    }
    documents = {
        key: _json_snapshot(snapshot, label=key.upper())
        for key, snapshot in paths.items()
    }
    safety = documents["haskell-module-safety"]
    zero_fields = (
        "unclassifiedModuleCount",
        "candidateTrustworthyUnsafeDeclarationCount",
        "candidateDirectUnsafeIoForeignImportCount",
        "coreToShellEdgeCount",
        "unknownTransitiveEdgeCount",
        "staleAllowlistCount",
    )
    if (
        safety.get("schemaVersion")
        != "s1.4x-haskell-module-safety-result-v1"
        or not isinstance(safety.get("modules"), list)
        or not safety["modules"]
        or not isinstance(safety.get("upstreamTransitiveEdges"), list)
        or not safety["upstreamTransitiveEdges"]
        or any(safety.get(field) != 0 for field in zero_fields)
        or safety.get("aggregateStatus") != "PASS"
    ):
        raise EvidenceAssemblyError("HASKELL_MODULE_SAFETY_INVALID")
    for module in safety["modules"]:
        item = _require_object(
            module,
            required={"moduleName", "category"},
            label="HASKELL_MODULE_SAFETY",
        )
        if (
            not isinstance(item["moduleName"], str)
            or item["category"]
            not in {
                "safe-scalar",
                "audited-pure-vector",
                "io-shell",
                "test",
                "benchmark",
            }
        ):
            raise EvidenceAssemblyError("HASKELL_MODULE_SAFETY_INVALID")
    if any(
        not isinstance(edge, dict) or edge.get("allowlisted") is not True
        for edge in safety["upstreamTransitiveEdges"]
    ):
        raise EvidenceAssemblyError("HASKELL_MODULE_SAFETY_INVALID")
    formatting = documents["haskell-format"]
    haskell_lock = _json_snapshot(
        repository["haskell-toolchain-lock"],
        label="HASKELL_TOOLCHAIN_LOCK",
    )
    try:
        stylish_lock = haskell_lock["resolvedTools"]["stylishHaskell"]
        hlint_lock = haskell_lock["resolvedTools"]["hlint"]
    except (KeyError, TypeError) as exc:
        raise EvidenceAssemblyError("HASKELL_TOOLCHAIN_LOCK_INVALID") from exc
    if (
        formatting.get("schemaVersion")
        != "s1.4x-haskell-format-evidence-v1"
        or formatting.get("formatterPathId") != stylish_lock.get("pathId")
        or formatting.get("formatterSha256") != stylish_lock.get("sha256")
        or formatting.get("formatterVersion") != stylish_lock.get("version")
        or formatting.get("sourceInputManifestSha256")
        != repository["haskell-source-inputs"].sha256
        or type(formatting.get("sourceInputFileCount")) is not int
        or formatting["sourceInputFileCount"] < 1
        or not _is_exact_int(formatting.get("positiveExitCode"), 0)
        or not _is_exact_int(formatting.get("misformattedExitCode"), 1)
        or formatting.get("parserCapabilityStatus")
        != "PINNED_PARSER_COMPATIBILITY_FALLBACK"
        or formatting.get("status") != "PASS"
    ):
        raise EvidenceAssemblyError("HASKELL_FORMAT_INVALID")
    lint = documents["haskell-lint"]
    if (
        lint.get("schemaVersion") != "s1.4x-haskell-hlint-evidence-v1"
        or lint.get("hlintPathId") != hlint_lock.get("pathId")
        or lint.get("hlintSha256") != hlint_lock.get("sha256")
        or lint.get("hlintVersion") != hlint_lock.get("version")
        or lint.get("sourceInputManifestSha256")
        != repository["haskell-source-inputs"].sha256
        or type(lint.get("sourceInputFileCount")) is not int
        or lint["sourceInputFileCount"] < 1
        or type(lint.get("negativeFixtureCount")) is not int
        or lint["negativeFixtureCount"] < 1
        or lint.get("status") != "PASS"
    ):
        raise EvidenceAssemblyError("HASKELL_LINT_INVALID")
    return paths


def _git_inventory_snapshots(
    repository_root: Path,
    subject: str,
    root: Path,
    *,
    suffix: str,
) -> tuple[Snapshot, ...]:
    listed = _git(
        repository_root,
        "ls-tree",
        "-r",
        "--name-only",
        "-z",
        subject,
        "--",
        root.as_posix(),
    )
    if listed.returncode != 0 or listed.stderr:
        raise EvidenceAssemblyError("CANDIDATE_RUBRIC_PRODUCER_INVALID")
    try:
        paths = [
            Path(raw.decode("utf-8"))
            for raw in listed.stdout.split(b"\0")
            if raw
        ]
    except UnicodeError as exc:
        raise EvidenceAssemblyError(
            "CANDIDATE_RUBRIC_PRODUCER_INVALID"
        ) from exc
    selected = tuple(
        path
        for path in paths
        if path.suffix == suffix and (path == root or root in path.parents)
    )
    return tuple(
        _git_blob_snapshot(
            repository_root,
            subject,
            path,
            label="CANDIDATE_RUBRIC_PRODUCER_ARTIFACT",
        )
        for path in selected
    )


def _expected_rubric_contracts(
    closure: RawClosure,
    repository_root: Path,
    *,
    subject: str,
) -> dict[str, dict[str, dict[str, Any]]]:
    """candidate_rubric_audit producer의 rubric별 objective/artifact spec이다."""

    def existing(*paths: Path) -> tuple[Path, ...]:
        return tuple(path for path in paths if path in closure.snapshots)

    def raw_paths(
        *paths: Path,
        prefixes: tuple[Path, ...] = (),
    ) -> tuple[Path, ...]:
        selected = {
            path
            for path in paths
            if path in closure.snapshots
        }
        selected.update(
            path
            for path in closure.snapshots
            if any(
                path.as_posix().startswith(prefix.as_posix())
                for prefix in prefixes
            )
        )
        return tuple(
            sorted(selected, key=lambda value: value.as_posix().encode())
        )

    def raw_entries(paths: Sequence[Path]) -> list[dict[str, str]]:
        return [
            {
                "path": path.as_posix(),
                "sha256": closure.snapshot(
                    path,
                    label="CANDIDATE_RUBRIC_PRODUCER_ARTIFACT",
                ).sha256,
            }
            for path in sorted(
                set(paths),
                key=lambda value: value.as_posix().encode(),
            )
        ]

    def repository_entries(
        snapshots: Sequence[Snapshot],
    ) -> list[dict[str, str]]:
        indexed = {
            snapshot.relative_path: snapshot for snapshot in snapshots
        }
        return [
            {
                "path": path.as_posix(),
                "blobSha256": indexed[path].sha256,
            }
            for path in sorted(
                indexed,
                key=lambda value: value.as_posix().encode(),
            )
        ]

    regression = raw_paths(
        Path("regression/production-compound-receipt.v1.json"),
        Path("regression/research-compound-receipt.v1.json"),
        *(
            Path(f"regression/logs/{spec['label']}.{stream}")
            for spec in _regression_specs()
            for stream in ("stdout", "stderr")
        ),
    )
    coverage = Path("coverage/integration-coverage.json")
    semantic = RAW_PATHS["semantic-comparison"]
    canonical = RAW_PATHS["canonical-comparison"]
    executions = {
        candidate: PROPERTY_EVIDENCE_FILES[candidate][2]
        for candidate in CANDIDATES
    }
    workflow_paths = (
        Path(".github/workflows/s1-4x-numeric-parity-correctness.yml"),
        Path(".github/workflows/s1-4x-numeric-parity-benchmark.yml"),
    )
    boundary_path = (
        S1_4X_RELATIVE
        / "integration/tests/test_s1_4r_regression_boundary.py"
    )
    workflows = tuple(
        _git_blob_snapshot(
            repository_root,
            subject,
            path,
            label="CANDIDATE_RUBRIC_WORKFLOW",
        )
        for path in workflow_paths
    )
    boundary = _git_blob_snapshot(
        repository_root,
        subject,
        boundary_path,
        label="CANDIDATE_RUBRIC_BOUNDARY",
    )
    candidate_specs: dict[str, dict[str, Any]] = {}
    for candidate in CANDIDATES:
        objective: dict[str, tuple[str, ...]]
        if candidate == "scala":
            core = _git_inventory_snapshots(
                repository_root,
                subject,
                S1_4X_RELATIVE
                / "scala/src/main/scala/ai/trading/coach/s14x/core",
                suffix=".scala",
            )
            shell = _git_inventory_snapshots(
                repository_root,
                subject,
                S1_4X_RELATIVE
                / "scala/src/main/scala/ai/trading/coach/s14x/shell",
                suffix=".scala",
            )
            tests = _git_inventory_snapshots(
                repository_root,
                subject,
                S1_4X_RELATIVE
                / "scala/src/test/scala/ai/trading/coach/s14x",
                suffix=".scala",
            )
            policy = _git_blob_snapshot(
                repository_root,
                subject,
                S1_4X_RELATIVE / "contract/scala-source-policy.v1.json",
                label="CANDIDATE_RUBRIC_POLICY",
            )
            selected = _git_blob_snapshot(
                repository_root,
                subject,
                REPOSITORY_PATHS["scala-selected-source"],
                label="CANDIDATE_RUBRIC_SELECTED",
            )
            selected_profile = _json_snapshot(
                closure.snapshot(
                    RAW_PATHS["scala-selected-profile"],
                    label="CANDIDATE_RUBRIC_SCALA_SELECTED",
                ),
                label="CANDIDATE_RUBRIC_SCALA_SELECTED",
            ).get("selectedProfileId")
            profile_root = Path(f"scala/profiles/{selected_profile}")
            scala_matrix = (
                "canonical-results.json",
                "semantic-errors.json",
                "scala-profile-unit-test-result.v1.json",
                "unit-test.stdout",
                "unit-test.stderr",
                "canonical-comparison.json",
                "semantic-comparison.json",
                "property/scala-property-report.v1.json",
                "property/scala-registry-report.v1.json",
                "property/scala-property-execution-evidence.v1.json",
            )
            purity = raw_paths(
                RAW_PATHS["scala-source-policy"],
                RAW_PATHS["scala-lint"],
                *existing(
                    Path(f"{RAW_PATHS['scala-source-policy'].as_posix()}.core"),
                    Path(f"{RAW_PATHS['scala-source-policy'].as_posix()}.stdout"),
                    Path(f"{RAW_PATHS['scala-source-policy'].as_posix()}.stderr"),
                ),
            )
            side_effect = raw_paths(
                *purity,
                RAW_PATHS["scala-dependency-edge"],
            )
            dependency = raw_paths(RAW_PATHS["scala-dependency-edge"])
            warning = raw_paths(
                RAW_PATHS["scala-format"],
                RAW_PATHS["scala-selected-profile"],
                Path(
                    "scala/qualification/"
                    "scala-profile-qualification.v1.json"
                ),
                Path(
                    f"scala/hard-compiler-{selected_profile}/"
                    "scala-hard-compiler-result.v1.json"
                ),
                profile_root / "scala-profile-correctness-result.v1.json",
                *(profile_root / name for name in scala_matrix),
            )
            oci = raw_paths(
                RAW_PATHS["scala-oci-build"],
                RAW_PATHS["scala-oci-runtime"],
                RAW_PATHS["oci-cross-comparison"],
                Path(
                    "oci/scala/runtime/"
                    "oci-runtime-binding-before.v1.json"
                ),
                Path(
                    "oci/scala/runtime/"
                    "oci-runtime-binding-after.v1.json"
                ),
                Path("oci/scala/runtime/canonical-results.json"),
                Path("oci/scala/runtime/semantic-errors.json"),
                Path("oci/scala/runtime/canonical-comparison.json"),
                Path("oci/scala/runtime/semantic-comparison.json"),
            )
            production = (*core, *shell)
            objective = {
                "purity-core-shell": (
                    "raw.scala.semantic-source-policy-pass",
                    "subject.scala.core-has-no-shell-reference",
                ),
                "purity-side-effect": (
                    "raw.scala.side-effect-policy-pass",
                    "raw.scala.native-edge-count-zero",
                ),
                "purity-dependency-surface": (
                    "raw.scala.native-dependency-surface-zero",
                    "subject.scala.dependency-policy-bound",
                ),
                "maintainability-warning-free": (
                    "raw.scala.selected-profile-hard-compiler-pass",
                    "raw.scala.selected-profile-correctness-pass",
                ),
            }
        else:
            core = _git_inventory_snapshots(
                repository_root,
                subject,
                S1_4X_RELATIVE / "haskell/src/core/S14X/Core",
                suffix=".hs",
            )
            contract = _git_inventory_snapshots(
                repository_root,
                subject,
                S1_4X_RELATIVE / "haskell/src/contract/S14X/Contract",
                suffix=".hs",
            )
            tests = _git_inventory_snapshots(
                repository_root,
                subject,
                S1_4X_RELATIVE / "haskell/test/S14X",
                suffix=".hs",
            )
            policy = _git_blob_snapshot(
                repository_root,
                subject,
                S1_4X_RELATIVE
                / "contract/haskell-module-safety-policy.v1.json",
                label="CANDIDATE_RUBRIC_POLICY",
            )
            selected = _git_blob_snapshot(
                repository_root,
                subject,
                REPOSITORY_PATHS["haskell-selected-profile"],
                label="CANDIDATE_RUBRIC_SELECTED",
            )
            selected_document = _json_snapshot(
                selected,
                label="CANDIDATE_RUBRIC_HASKELL_SELECTED",
            )
            profile = selected_document.get("profileId")
            correctness = Path(
                f"haskell/profiles/{profile}/correctness-receipt.v1.json"
            )
            lint_document = closure.json(
                RAW_PATHS["haskell-lint"],
                label="CANDIDATE_RUBRIC_HASKELL_LINT",
            )
            format_document = closure.json(
                RAW_PATHS["haskell-format"],
                label="CANDIDATE_RUBRIC_HASKELL_FORMAT",
            )
            raw_lint_logs = lint_document.get("logs")
            lint_log_names: Mapping[str, Any] = (
                raw_lint_logs if isinstance(raw_lint_logs, dict) else {}
            )
            lint_logs = tuple(
                Path("haskell/hlint") / name
                for name in sorted(
                    lint_log_names,
                    key=lambda value: value.encode(),
                )
            )
            raw_format_logs = format_document.get("logs")
            format_log_names: Mapping[str, Any] = (
                raw_format_logs if isinstance(raw_format_logs, dict) else {}
            )
            format_logs = tuple(
                Path("haskell/format") / name
                for name in sorted(
                    format_log_names,
                    key=lambda value: value.encode(),
                )
            )
            purity = raw_paths(RAW_PATHS["haskell-module-safety"])
            side_effect = raw_paths(
                RAW_PATHS["haskell-module-safety"],
                RAW_PATHS["haskell-lint"],
                *lint_logs,
            )
            dependency = purity
            warning = raw_paths(
                RAW_PATHS["haskell-lint"],
                RAW_PATHS["haskell-format"],
                correctness,
                *lint_logs,
                *format_logs,
            )
            haskell_oci_document = closure.json(
                RAW_PATHS["haskell-oci"],
                label="CANDIDATE_RUBRIC_HASKELL_OCI",
            )
            command_streams = tuple(
                Path(f"oci/haskell/{command.get('phase')}.{stream}")
                for command in haskell_oci_document.get("commands", [])
                if isinstance(command, dict)
                for stream in ("stdout", "stderr")
            )
            oci = raw_paths(
                RAW_PATHS["haskell-oci"],
                RAW_PATHS["oci-cross-comparison"],
                *command_streams,
                Path("oci/haskell/runtime/canonical.actual.json"),
                Path("oci/haskell/runtime/semantic.actual.json"),
                Path("oci/haskell/canonical.oci-comparison.json"),
                Path("oci/haskell/semantic.oci-comparison.json"),
            )
            production = (*core, *contract)
            objective = {
                "purity-core-shell": (
                    "raw.haskell.module-safety-pass",
                    "subject.haskell.core-has-no-contract-import",
                ),
                "purity-side-effect": (
                    "raw.haskell.core-io-unsafe-edge-count-zero",
                    "raw.haskell.hlint-policy-pass",
                ),
                "purity-dependency-surface": (
                    "raw.haskell.unknown-transitive-edge-count-zero",
                    "subject.haskell.dependency-policy-bound",
                ),
                "maintainability-warning-free": (
                    "raw.haskell.hlint-pass",
                    "raw.haskell.selected-profile-correctness-pass",
                ),
            }
        objective.update(
            {
                "purity-validation-transparency": (
                    "raw.semantic-comparison-zero-mismatch",
                    "raw.coverage-full-function-property-error-closure",
                ),
                "maintainability-module-cohesion": (
                    f"subject.{candidate}.path-module-cohesion",
                    f"subject.{candidate}.core-shell-separation",
                ),
                "maintainability-duplication-inventory": (
                    f"subject.{candidate}.normalized-eight-line-blocks-unique",
                ),
                "maintainability-comments-docs": (
                    f"subject.{candidate}.public-core-callables-documented",
                ),
                "maintainability-test-readability": (
                    f"subject.{candidate}.test-names-unique-and-descriptive",
                    f"subject.{candidate}.unit-contract-property-test-structure",
                ),
                "integration-process-contract": (
                    "raw.canonical-comparison-zero-mismatch",
                    "raw.semantic-comparison-zero-mismatch",
                ),
                "integration-production-isolation": (
                    "raw.research-regression-exact-deselect-replacement-closure",
                    "subject.research-boundary-replacement-tests-present",
                ),
                "integration-ci-cost-rollback": (
                    "subject.ci.correctness-path-scoped-and-cancellable",
                    "subject.ci.benchmark-manual-only",
                    f"raw.{candidate}.oci-network-none-and-pinned-base",
                ),
            }
        )
        raw_by_rubric = {
            "purity-core-shell": purity,
            "purity-side-effect": side_effect,
            "purity-validation-transparency": raw_paths(
                semantic,
                coverage,
                executions["scala"],
                executions["haskell"],
            ),
            "purity-dependency-surface": dependency,
            "maintainability-module-cohesion": purity,
            "maintainability-duplication-inventory": purity,
            "maintainability-comments-docs": purity,
            "maintainability-test-readability": raw_paths(
                coverage,
                executions[candidate],
            ),
            "maintainability-warning-free": warning,
            "integration-process-contract": raw_paths(canonical, semantic),
            "integration-production-isolation": regression,
            "integration-ci-cost-rollback": raw_paths(*oci, *regression),
        }
        repository_by_rubric = {
            "purity-core-shell": (*core, policy),
            "purity-side-effect": (*core, policy),
            "purity-validation-transparency": (*core, policy),
            "purity-dependency-surface": (*core, policy),
            "maintainability-module-cohesion": production,
            "maintainability-duplication-inventory": production,
            "maintainability-comments-docs": core,
            "maintainability-test-readability": tests,
            "maintainability-warning-free": (*core, selected),
            "integration-process-contract": production,
            "integration-production-isolation": (boundary,),
            "integration-ci-cost-rollback": (*workflows, boundary),
        }
        candidate_specs[candidate] = {
            rubric_id: {
                "objectiveChecks": list(objective[rubric_id]),
                "reviewedArtifacts": raw_entries(raw_by_rubric[rubric_id]),
                "repositoryArtifacts": repository_entries(
                    repository_by_rubric[rubric_id]
                ),
            }
            for rubric_id in sorted(
                final_audit.RUBRIC_EVIDENCE,
                key=lambda value: value.encode(),
            )
        }
    return candidate_specs


def _validate_candidate_rubric_audit(
    closure: RawClosure,
    repository_root: Path,
    *,
    subject: str,
    relative_root: Path,
) -> dict[str, dict[str, Any]]:
    portable_root = _portable_relative_path(
        relative_root.as_posix(),
        label="CANDIDATE_RUBRIC_AUDIT",
    )
    if portable_root != Path("rubric-audit"):
        raise EvidenceAssemblyError("CANDIDATE_RUBRIC_AUDIT_ROOT_INVALID")
    expected_rubrics = tuple(
        sorted(
            final_audit.RUBRIC_EVIDENCE,
            key=lambda value: value.encode("utf-8"),
        )
    )
    expected_contracts = _expected_rubric_contracts(
        closure,
        repository_root,
        subject=subject,
    )
    validated: dict[str, dict[str, Any]] = {}
    for candidate in CANDIDATES:
        relative = portable_root / (
            f"{candidate}-candidate-rubric-audit.v1.json"
        )
        snapshot = closure.snapshot(
            relative,
            label=f"{candidate.upper()}_CANDIDATE_RUBRIC_AUDIT",
        )
        document = _json_snapshot(
            snapshot,
            label=f"{candidate.upper()}_CANDIDATE_RUBRIC_AUDIT",
        )
        if (
            set(document)
            != {
                "schemaVersion",
                "benchmarkSubjectCommit",
                "candidate",
                "rubrics",
                "status",
            }
            or document.get("schemaVersion")
            != "s1.4x-candidate-rubric-audit-v1"
            or document.get("benchmarkSubjectCommit") != subject
            or document.get("candidate") != candidate
            or document.get("status") != "PASS"
            or snapshot.payload != _canonical_json_bytes(document)
            or not isinstance(document.get("rubrics"), list)
            or len(document["rubrics"]) != len(expected_rubrics)
        ):
            raise EvidenceAssemblyError(
                f"{candidate.upper()}_CANDIDATE_RUBRIC_AUDIT_INVALID"
            )
        entries: dict[str, dict[str, Any]] = {}
        for raw_entry, rubric_id in zip(
            document["rubrics"],
            expected_rubrics,
            strict=True,
        ):
            entry = _require_object(
                raw_entry,
                required={
                    "rubricId",
                    "objectiveChecks",
                    "reviewedArtifacts",
                    "repositoryArtifacts",
                    "findings",
                    "status",
                },
                label="CANDIDATE_RUBRIC_ENTRY",
                exact=True,
            )
            objective_checks = entry["objectiveChecks"]
            reviewed_artifacts = entry["reviewedArtifacts"]
            repository_artifacts = entry["repositoryArtifacts"]
            expected_contract = expected_contracts[candidate][rubric_id]
            if (
                entry["rubricId"] != rubric_id
                or objective_checks != expected_contract["objectiveChecks"]
                or reviewed_artifacts
                != expected_contract["reviewedArtifacts"]
                or repository_artifacts
                != expected_contract["repositoryArtifacts"]
                or entry["findings"] != []
                or entry["status"] != "PASS"
            ):
                raise EvidenceAssemblyError(
                    f"CANDIDATE_RUBRIC_ENTRY_INVALID:{candidate}:{rubric_id}"
                )
            raw_snapshots: list[Snapshot] = []
            raw_paths: list[str] = []
            for raw_artifact in reviewed_artifacts:
                artifact = _require_object(
                    raw_artifact,
                    required={"path", "sha256"},
                    label="CANDIDATE_RUBRIC_RAW_ARTIFACT",
                    exact=True,
                )
                artifact_path = _portable_relative_path(
                    artifact["path"],
                    label="CANDIDATE_RUBRIC_RAW_ARTIFACT",
                )
                raw_snapshot = closure.snapshot(
                    artifact_path,
                    label="CANDIDATE_RUBRIC_RAW_ARTIFACT",
                )
                if raw_snapshot.sha256 != artifact["sha256"]:
                    raise EvidenceAssemblyError(
                        "CANDIDATE_RUBRIC_RAW_ARTIFACT_INVALID"
                    )
                raw_paths.append(artifact_path.as_posix())
                raw_snapshots.append(raw_snapshot)
            if (
                raw_paths
                != sorted(raw_paths, key=lambda value: value.encode("utf-8"))
                or len(raw_paths) != len(set(raw_paths))
            ):
                raise EvidenceAssemblyError(
                    "CANDIDATE_RUBRIC_RAW_ARTIFACT_ORDER_INVALID"
                )
            repository_snapshots: list[Snapshot] = []
            repository_paths: list[str] = []
            for raw_artifact in repository_artifacts:
                artifact = _require_object(
                    raw_artifact,
                    required={"path", "blobSha256"},
                    label="CANDIDATE_RUBRIC_REPOSITORY_ARTIFACT",
                    exact=True,
                )
                artifact_path = _portable_relative_path(
                    artifact["path"],
                    label="CANDIDATE_RUBRIC_REPOSITORY_ARTIFACT",
                )
                repository_snapshot = _git_blob_snapshot(
                    repository_root,
                    subject,
                    artifact_path,
                    label="CANDIDATE_RUBRIC_REPOSITORY_ARTIFACT",
                )
                if repository_snapshot.sha256 != artifact["blobSha256"]:
                    raise EvidenceAssemblyError(
                        "CANDIDATE_RUBRIC_REPOSITORY_ARTIFACT_INVALID"
                    )
                repository_paths.append(artifact_path.as_posix())
                repository_snapshots.append(repository_snapshot)
            if (
                repository_paths
                != sorted(
                    repository_paths,
                    key=lambda value: value.encode("utf-8"),
                )
                or len(repository_paths) != len(set(repository_paths))
            ):
                raise EvidenceAssemblyError(
                    "CANDIDATE_RUBRIC_REPOSITORY_ARTIFACT_ORDER_INVALID"
                )
            entries[rubric_id] = {
                "entry": entry,
                "snapshots": (
                    snapshot,
                    *raw_snapshots,
                    *repository_snapshots,
                ),
            }
        validated[candidate] = {
            "snapshot": snapshot,
            "document": document,
            "rubrics": entries,
        }
    return validated


def _write_exclusive(path: Path, payload: bytes) -> None:
    no_follow = getattr(os, "O_NOFOLLOW", None)
    directory_flag = getattr(os, "O_DIRECTORY", None)
    if no_follow is None or directory_flag is None or not path.is_absolute():
        raise EvidenceAssemblyError("ASSEMBLY_OUTPUT_NOFOLLOW_UNSUPPORTED")
    directory_fds: list[int] = []
    descriptor: int | None = None
    try:
        directory_fd = os.open(
            "/",
            os.O_RDONLY | directory_flag | no_follow | os.O_CLOEXEC,
        )
        directory_fds.append(directory_fd)
        for component in path.parts[1:-1]:
            try:
                os.mkdir(component, mode=0o700, dir_fd=directory_fd)
            except FileExistsError:
                pass
            directory_fd = os.open(
                component,
                os.O_RDONLY | directory_flag | no_follow | os.O_CLOEXEC,
                dir_fd=directory_fd,
            )
            directory_fds.append(directory_fd)
        descriptor = os.open(
            path.name,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | os.O_CLOEXEC
            | no_follow,
            0o600,
            dir_fd=directory_fd,
        )
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written < 1:
                raise OSError("zero-byte write")
            view = view[written:]
        os.fsync(descriptor)
        os.fsync(directory_fd)
    except FileExistsError as exc:
        raise EvidenceAssemblyError("ASSEMBLY_OUTPUT_COLLISION") from exc
    except OSError as exc:
        raise EvidenceAssemblyError("ASSEMBLY_OUTPUT_WRITE_FAILED") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        for directory_fd in reversed(directory_fds):
            os.close(directory_fd)


def _rename_no_replace(
    source: Path,
    destination: Path,
    *,
    expected_source_identity: tuple[int, int],
) -> None:
    """Linux renameat2로 완성된 staging tree를 기존 경로 위에 덮지 않고 공개한다."""

    if (
        not source.is_absolute()
        or not destination.is_absolute()
        or source.parent != destination.parent
    ):
        raise EvidenceAssemblyError("ASSEMBLY_ATOMIC_PUBLISH_SOURCE_INVALID")
    parent_fd: int | None = None
    source_fd: int | None = None
    try:
        parent_fd = os.open(source.parent, _directory_open_flags())
        source_fd = os.open(
            source.name,
            _directory_open_flags(),
            dir_fd=parent_fd,
        )
        opened = os.fstat(source_fd)
        linked = os.stat(
            source.name,
            dir_fd=parent_fd,
            follow_symlinks=False,
        )
    except OSError as exc:
        if source_fd is not None:
            os.close(source_fd)
        if parent_fd is not None:
            os.close(parent_fd)
        raise EvidenceAssemblyError(
            "ASSEMBLY_ATOMIC_PUBLISH_SOURCE_INVALID"
        ) from exc
    actual_opened = (opened.st_dev, opened.st_ino)
    actual_linked = (linked.st_dev, linked.st_ino)
    if (
        not stat.S_ISDIR(opened.st_mode)
        or not stat.S_ISDIR(linked.st_mode)
        or actual_opened != expected_source_identity
        or actual_linked != expected_source_identity
    ):
        os.close(source_fd)
        os.close(parent_fd)
        raise EvidenceAssemblyError(
            "ASSEMBLY_ATOMIC_PUBLISH_SOURCE_INVALID"
        )
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        os.close(source_fd)
        os.close(parent_fd)
        raise EvidenceAssemblyError("ASSEMBLY_ATOMIC_PUBLISH_UNSUPPORTED")
    renameat2.argtypes = (
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    )
    renameat2.restype = ctypes.c_int
    result = renameat2(
        parent_fd,
        os.fsencode(source.name),
        parent_fd,
        os.fsencode(destination.name),
        1,
    )
    if result == 0:
        try:
            published = os.stat(
                destination.name,
                dir_fd=parent_fd,
                follow_symlinks=False,
            )
            if (
                not stat.S_ISDIR(published.st_mode)
                or (published.st_dev, published.st_ino)
                != expected_source_identity
            ):
                raise EvidenceAssemblyError(
                    "ASSEMBLY_ATOMIC_PUBLISH_SOURCE_INVALID"
                )
            os.fsync(parent_fd)
        finally:
            os.close(source_fd)
            os.close(parent_fd)
        return
    os.close(source_fd)
    os.close(parent_fd)
    error_number = ctypes.get_errno()
    if error_number in {errno.EEXIST, errno.ENOTEMPTY}:
        raise EvidenceAssemblyError("ASSEMBLY_OUTPUT_COLLISION")
    if error_number in {errno.ENOSYS, errno.EINVAL, errno.ENOTSUP}:
        raise EvidenceAssemblyError("ASSEMBLY_ATOMIC_PUBLISH_UNSUPPORTED")
    raise EvidenceAssemblyError(
        f"ASSEMBLY_ATOMIC_PUBLISH_FAILED:{error_number}"
    )


def _write_json(path: Path, value: Any) -> Snapshot:
    payload = _canonical_json_bytes(value)
    _write_exclusive(path, payload)
    return Snapshot(
        relative_path=path,
        payload=payload,
        sha256=_sha256_bytes(payload),
    )


def _source_entry(
    *,
    output_root: Path,
    source: Snapshot,
    role: str,
    schema: str,
) -> dict[str, Any]:
    return {
        "role": role,
        "path": source.relative_path.relative_to(output_root).as_posix(),
        "sha256": source.sha256,
        "schemaVersion": schema,
        "status": "PASS",
    }


def _copy_reviewed(
    output_root: Path,
    snapshots: Sequence[Snapshot],
) -> dict[Path, dict[str, str]]:
    unique: dict[Path, Snapshot] = {}
    for snapshot in snapshots:
        existing = unique.get(snapshot.relative_path)
        if existing is not None and existing.sha256 != snapshot.sha256:
            raise EvidenceAssemblyError("REVIEWED_ARTIFACT_COLLISION")
        unique[snapshot.relative_path] = snapshot
    copied: dict[Path, dict[str, str]] = {}
    for relative, snapshot in sorted(
        unique.items(),
        key=lambda item: item[0].as_posix().encode(),
    ):
        destination = output_root / "reviewed" / relative
        _write_exclusive(destination, snapshot.payload)
        copied[relative] = {
            "path": destination.relative_to(output_root).as_posix(),
            "sha256": snapshot.sha256,
        }
    return copied


def _assemble_final_candidate_evidence_to_staging(
    *,
    repository_root: Path,
    benchmark_subject_commit: str,
    correctness_root: Path,
    production_regression_receipt: Path,
    research_regression_receipt: Path,
    candidate_rubric_audit: Path,
    output_root: Path,
) -> dict[str, Any]:
    """검증된 raw closure만 사용해 후보별 20개 envelope를 결정적으로 생성한다."""

    repository = _absolute_canonical_directory(
        repository_root,
        label="REPOSITORY_ROOT",
    )
    output = _absolute_canonical_directory(
        output_root,
        label="ASSEMBLY_STAGING_ROOT",
    )
    try:
        if next(os.scandir(output), None) is not None:
            raise EvidenceAssemblyError("ASSEMBLY_STAGING_ROOT_NOT_EMPTY")
    except OSError as exc:
        raise EvidenceAssemblyError("ASSEMBLY_STAGING_ROOT_INVALID") from exc
    _validate_repository(repository, benchmark_subject_commit)
    closure = RawClosure(
        correctness_root,
        subject=benchmark_subject_commit,
    )
    try:
        output.relative_to(closure.root)
    except ValueError:
        pass
    else:
        raise EvidenceAssemblyError("OUTPUT_ROOT_INSIDE_CORRECTNESS_ROOT")
    repository_artifacts = _repository_snapshots(
        repository,
        benchmark_subject_commit,
    )
    (
        _,
        large_fixture_snapshot,
        large_fixture_replay_snapshot,
        fixture_tree_sha256,
    ) = (
        _validate_contract_and_fixtures(closure, repository_artifacts)
    )
    regression_commands = _validate_regression_execution(
        closure,
        subject=benchmark_subject_commit,
    )
    production_regression = _validate_regression(
        closure,
        production_regression_receipt,
        subject=benchmark_subject_commit,
        role="production",
        command_receipts=regression_commands,
    )
    research_regression = _validate_regression(
        closure,
        research_regression_receipt,
        subject=benchmark_subject_commit,
        role="research",
        command_receipts=regression_commands,
    )
    toolchains = _validate_toolchains(
        closure,
        repository_artifacts,
        repository_root=repository,
        subject=benchmark_subject_commit,
    )
    (
        canonical_snapshot,
        canonical_document,
        semantic_snapshot,
        semantic_document,
    ) = _validate_native_cross_language(
        closure,
        repository_artifacts,
        toolchains,
    )
    coverage_snapshot, coverage_document, _ = _validate_coverage(
        closure,
        repository_root=repository,
        subject=benchmark_subject_commit,
        toolchains=toolchains,
    )
    scala_oci_build, scala_oci = _validate_scala_oci(
        closure,
        selected_candidate=toolchains["scala"]["candidate"],
        containerfile=repository_artifacts["scala-containerfile"],
        fixture_tree_sha256=fixture_tree_sha256,
    )
    haskell_oci = _validate_haskell_oci(
        closure,
        subject=benchmark_subject_commit,
        selected_profile=toolchains["haskell"]["selected"],
        profile_id=toolchains["haskell"]["profileId"],
        selected_profile_document=toolchains["haskell"]["selectedDocument"],
        correctness_document=_json_snapshot(
            toolchains["haskell"]["correctness"],
            label="HASKELL_SELECTED_CORRECTNESS",
        ),
        containerfile=repository_artifacts["haskell-containerfile"],
        fixture_tree_sha256=fixture_tree_sha256,
    )
    oci_cross_snapshot, _ = _validate_comparison(
        closure,
        RAW_PATHS["oci-cross-comparison"],
        label="OCI_CROSS_COMPARISON",
        expected_request_id="s1.4x-canonical-small-v1",
    )
    if oci_cross_snapshot.payload != canonical_snapshot.payload:
        raise EvidenceAssemblyError("OCI_CROSS_COMPARISON_BINDING_INVALID")
    scala_rubric = _validate_scala_rubric_inputs(
        closure,
        profile_id=str(toolchains["scala"]["profileId"]),
        repository=repository_artifacts,
    )
    haskell_rubric = _validate_haskell_rubric_inputs(
        closure,
        repository=repository_artifacts,
    )
    if not scala_rubric or not haskell_rubric:
        raise EvidenceAssemblyError("RUBRIC_TYPED_INPUTS_INVALID")
    rubric_audits = _validate_candidate_rubric_audit(
        closure,
        repository,
        subject=benchmark_subject_commit,
        relative_root=candidate_rubric_audit,
    )
    closure.verify_unchanged()
    _validate_repository(repository, benchmark_subject_commit)
    reviewed = _copy_reviewed(
        output,
        [
            snapshot
            for audit in rubric_audits.values()
            for rubric in audit["rubrics"].values()
            for snapshot in rubric["snapshots"]
        ],
    )

    source_entries: dict[str, dict[str, list[dict[str, Any]]]] = {
        candidate: {} for candidate in CANDIDATES
    }
    shared_specs = (
        (
            "integration-coverage",
            "s1.4x-integration-coverage-v1",
            "integration-coverage.json",
            coverage_document,
        ),
        (
            "canonical-comparison",
            "s1.4x-comparison-report-v1",
            "canonical-comparison.json",
            canonical_document,
        ),
        (
            "semantic-comparison",
            "s1.4x-comparison-report-v1",
            "semantic-comparison.json",
            semantic_document,
        ),
    )
    shared_sources: dict[str, dict[str, Any]] = {}
    for role, schema, file_name, document in shared_specs:
        source = _write_json(output / "sources/shared" / file_name, document)
        shared_sources[role] = _source_entry(
            output_root=output,
            source=source,
            role=role,
            schema=schema,
        )
    for candidate in CANDIDATES:
        source_entries[candidate]["correctness-contract"] = [
            shared_sources["integration-coverage"]
        ]
        source_entries[candidate]["property-coverage"] = [
            shared_sources["integration-coverage"]
        ]
        source_entries[candidate]["cross-language-parity"] = [
            shared_sources["canonical-comparison"],
            shared_sources["semantic-comparison"],
        ]

        regression_entries = []
        for role, raw, project, test_count in (
            (
                "production-regression",
                production_regression,
                "workspaces/decision-platform/python-services",
                1344,
            ),
            (
                "research-regression",
                research_regression,
                "workspaces/decision-platform/research/s1-4r-jax-risk",
                263,
            ),
        ):
            document = {
                "schemaVersion": "s1.4x-regression-gate-v1",
                "candidate": candidate,
                "benchmarkSubjectCommit": benchmark_subject_commit,
                "project": project,
                "testCount": test_count,
                "exitCode": 0,
                "reportSha256": raw.sha256,
                "status": "PASS",
            }
            source = _write_json(
                output
                / "sources"
                / candidate
                / f"regressions-{role}.json",
                document,
            )
            regression_entries.append(
                _source_entry(
                    output_root=output,
                    source=source,
                    role=role,
                    schema="s1.4x-regression-gate-v1",
                )
            )
        source_entries[candidate]["regressions"] = regression_entries

        candidate_oci = scala_oci if candidate == "scala" else haskell_oci
        oci_document = {
            "schemaVersion": "s1.4x-oci-correctness-receipt-v1",
            "candidate": candidate,
            "benchmarkSubjectCommit": benchmark_subject_commit,
            "networkMode": "none",
            "containerExitCode": 0,
            "comparisonMismatchCount": 0,
            "resultSha256": candidate_oci.sha256,
            "comparisonSha256": oci_cross_snapshot.sha256,
            "status": "PASS",
        }
        oci_source = _write_json(
            output / "sources" / candidate / "oci-correctness.json",
            oci_document,
        )
        source_entries[candidate]["oci-correctness"] = [
            _source_entry(
                output_root=output,
                source=oci_source,
                role="oci-correctness",
                schema="s1.4x-oci-correctness-receipt-v1",
            )
        ]

        toolchain = toolchains[candidate]
        toolchain_document = {
            "schemaVersion": "s1.4x-toolchain-reproducibility-v1",
            "candidate": candidate,
            "benchmarkSubjectCommit": benchmark_subject_commit,
            "toolchainLockSha256": toolchain["lock"].sha256,
            "selectedProfileId": toolchain["profileId"],
            "selectedProfileSha256": toolchain["selected"].sha256,
            "mergedToolchainProvenanceSha256": (
                final_audit.FROZEN_MERGED_TOOLCHAIN_PROVENANCE_SHA256
            ),
            "binarySha256": toolchain["binaries"],
            "status": "PASS",
        }
        toolchain_source = _write_json(
            output
            / "sources"
            / candidate
            / "toolchain-reproducibility.json",
            toolchain_document,
        )
        source_entries[candidate]["toolchain-reproducibility"] = [
            _source_entry(
                output_root=output,
                source=toolchain_source,
                role="toolchain-reproducibility",
                schema="s1.4x-toolchain-reproducibility-v1",
            )
        ]

        fixture_document = {
            "schemaVersion": "s1.4x-fixture-reproducibility-v1",
            "candidate": candidate,
            "benchmarkSubjectCommit": benchmark_subject_commit,
            "contractManifestSha256": (
                final_audit.FROZEN_CONTRACT_MANIFEST_SHA256
            ),
            "referenceLockSha256": final_audit.FROZEN_REFERENCE_LOCK_SHA256,
            "fixtureSha256": final_audit.FROZEN_FIXTURE_SHA256,
            "deterministicReplayCount": 2,
            "mismatchCount": 0,
            "status": "PASS",
        }
        fixture_source = _write_json(
            output
            / "sources"
            / candidate
            / "fixture-reproducibility.json",
            fixture_document,
        )
        source_entries[candidate]["fixture-reproducibility"] = [
            _source_entry(
                output_root=output,
                source=fixture_source,
                role="fixture-reproducibility",
                schema="s1.4x-fixture-reproducibility-v1",
            )
        ]

        offline_document = {
            "schemaVersion": "s1.4x-offline-runtime-reproducibility-v1",
            "candidate": candidate,
            "benchmarkSubjectCommit": benchmark_subject_commit,
            "networkMode": "none",
            "dependencyResolveMode": "offline",
            "containerExitCode": 0,
            "resultSha256": candidate_oci.sha256,
            "toolchainLockSha256": toolchain["lock"].sha256,
            "status": "PASS",
        }
        offline_source = _write_json(
            output
            / "sources"
            / candidate
            / "offline-runtime-reproducibility.json",
            offline_document,
        )
        source_entries[candidate]["offline-runtime-reproducibility"] = [
            _source_entry(
                output_root=output,
                source=offline_source,
                role="offline-runtime-reproducibility",
                schema="s1.4x-offline-runtime-reproducibility-v1",
            )
        ]

        for rubric_id in (
            evidence_id
            for evidence_id in final_audit.EXPECTED_EVIDENCE_CLAIMS
            if evidence_id in final_audit.RUBRIC_EVIDENCE
        ):
            audit_rubric = rubric_audits[candidate]["rubrics"][rubric_id]
            reviewed_artifacts = [
                reviewed[snapshot.relative_path]
                for snapshot in audit_rubric["snapshots"]
            ]
            audit_entry = audit_rubric["entry"]
            rubric_document = {
                "schemaVersion": "s1.4x-candidate-rubric-assessment-v1",
                "candidate": candidate,
                "benchmarkSubjectCommit": benchmark_subject_commit,
                "rubricId": rubric_id,
                "reviewedArtifacts": reviewed_artifacts,
                "findings": audit_entry["findings"],
                "status": audit_entry["status"],
            }
            rubric_source = _write_json(
                output
                / "sources"
                / candidate
                / f"{rubric_id}.json",
                rubric_document,
            )
            source_entries[candidate][rubric_id] = [
                _source_entry(
                    output_root=output,
                    source=rubric_source,
                    role="rubric-assessment",
                    schema="s1.4x-candidate-rubric-assessment-v1",
                )
            ]

    for candidate in CANDIDATES:
        if list(source_entries[candidate]) != list(
            final_audit.EXPECTED_EVIDENCE_CLAIMS
        ):
            raise EvidenceAssemblyError("SOURCE_PROJECTION_ORDER_INVALID")
        for evidence_id, claims in final_audit.EXPECTED_EVIDENCE_CLAIMS.items():
            sources = source_entries[candidate][evidence_id]
            expected_contract = final_audit.EXPECTED_SOURCE_CONTRACTS[
                evidence_id
            ]
            actual_contract = tuple(
                (entry["role"], entry["schemaVersion"]) for entry in sources
            )
            if actual_contract != expected_contract:
                raise EvidenceAssemblyError(
                    f"SOURCE_PROJECTION_INVALID:{candidate}:{evidence_id}"
                )
            envelope_document = {
                "schemaVersion": final_audit.EVIDENCE_SCHEMA,
                "candidate": candidate,
                "benchmarkSubjectCommit": benchmark_subject_commit,
                "evidenceId": evidence_id,
                "claims": claims,
                "sourceArtifacts": sources,
                "status": "PASS",
            }
            _write_json(
                output / "evidence" / candidate / f"{evidence_id}.json",
                envelope_document,
            )

    summary = {
        "schemaVersion": ASSEMBLY_SCHEMA,
        "benchmarkSubjectCommit": benchmark_subject_commit,
        "candidateCount": 2,
        "evidenceEnvelopeCountPerCandidate": 20,
        "sourceReferenceCountPerCandidate": 22,
        "sourceArtifactFileCount": 39,
        "status": "PASS",
    }
    actual_sources = len(
        [path for path in (output / "sources").rglob("*.json") if path.is_file()]
    )
    if actual_sources != summary["sourceArtifactFileCount"]:
        raise EvidenceAssemblyError("SOURCE_ARTIFACT_COUNT_INVALID")
    _write_json(output / "assembly-summary.json", summary)
    closure.verify_unchanged()
    _validate_repository(repository, benchmark_subject_commit)
    closure.close()
    return summary


def assemble_final_candidate_evidence(
    *,
    repository_root: Path,
    benchmark_subject_commit: str,
    correctness_root: Path,
    production_regression_receipt: Path,
    research_regression_receipt: Path,
    candidate_rubric_audit: Path,
    output_root: Path,
) -> dict[str, Any]:
    """새 staging tree에서 전부 검증한 뒤 최종 경로에 원자적으로 공개한다."""

    output = _new_absolute_directory(output_root, label="OUTPUT_ROOT")
    staging = Path(
        tempfile.mkdtemp(
            prefix=f".{output.name}.assembly-",
            dir=output.parent,
        )
    )
    os.chmod(staging, 0o700)
    staging_identity = (
        staging.stat(follow_symlinks=False).st_dev,
        staging.stat(follow_symlinks=False).st_ino,
    )
    try:
        summary = _assemble_final_candidate_evidence_to_staging(
            repository_root=repository_root,
            benchmark_subject_commit=benchmark_subject_commit,
            correctness_root=correctness_root,
            production_regression_receipt=production_regression_receipt,
            research_regression_receipt=research_regression_receipt,
            candidate_rubric_audit=candidate_rubric_audit,
            output_root=staging,
        )
        current_staging = staging.stat(follow_symlinks=False)
        if (
            staging.is_symlink()
            or not stat.S_ISDIR(current_staging.st_mode)
            or (current_staging.st_dev, current_staging.st_ino)
            != staging_identity
        ):
            raise EvidenceAssemblyError(
                "ASSEMBLY_ATOMIC_PUBLISH_SOURCE_INVALID"
            )
        _rename_no_replace(
            staging,
            output,
            expected_source_identity=staging_identity,
        )
        return summary
    except BaseException:
        try:
            current = staging.stat(follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            if (
                stat.S_ISDIR(current.st_mode)
                and not staging.is_symlink()
                and (current.st_dev, current.st_ino) == staging_identity
            ):
                shutil.rmtree(staging)
        raise


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--benchmark-subject-commit", required=True)
    parser.add_argument("--correctness-root", type=Path, required=True)
    parser.add_argument(
        "--production-regression-receipt",
        type=Path,
        required=True,
    )
    parser.add_argument(
        "--research-regression-receipt",
        type=Path,
        required=True,
    )
    parser.add_argument(
        "--candidate-rubric-audit",
        type=Path,
        required=True,
    )
    parser.add_argument("--output-root", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        summary = assemble_final_candidate_evidence(
            repository_root=arguments.repository_root,
            benchmark_subject_commit=arguments.benchmark_subject_commit,
            correctness_root=arguments.correctness_root,
            production_regression_receipt=(
                arguments.production_regression_receipt
            ),
            research_regression_receipt=arguments.research_regression_receipt,
            candidate_rubric_audit=arguments.candidate_rubric_audit,
            output_root=arguments.output_root,
        )
        print(
            json.dumps(
                summary,
                allow_nan=False,
                ensure_ascii=False,
                sort_keys=True,
            )
        )
    except (
        EvidenceAssemblyError,
        OSError,
        subprocess.SubprocessError,
        ValueError,
    ) as exc:
        print(f"FINAL_EVIDENCE_ASSEMBLY_FAIL:{exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
