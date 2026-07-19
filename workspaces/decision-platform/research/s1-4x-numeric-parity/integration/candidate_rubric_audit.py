#!/usr/bin/env python3
"""Git subject blob과 typed raw receipt로 S1.4X 후보 rubric을 독립 판정한다."""

from __future__ import annotations

import argparse
import ctypes
import errno
import hashlib
import json
import math
import os
import re
import secrets
import stat
import statistics
import subprocess
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import final_candidate_audit

CANDIDATES = ("scala", "haskell")
RUBRIC_IDS = tuple(
    sorted(
        final_candidate_audit.RUBRIC_EVIDENCE,
        key=lambda value: value.encode("utf-8"),
    )
)
SCHEMA = "s1.4x-candidate-rubric-audit-v1"
COMMIT = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
SCALA_BASE_IMAGE = (
    "docker.io/library/eclipse-temurin@sha256:"
    "5742cdb98ef117621ad75f57475ab127db04f344d9c523307cc60b9955bdd676"
)
HASKELL_BASE_IMAGE = (
    "docker.io/library/haskell@sha256:"
    "417d4bc30ac7d8d5ff04ec97937f86eb508b0c76bfd1a39b5ec225688531aa9d"
)
AUTHORITATIVE_GHC_SHA256 = (
    "d0c0dd79a1bcc5dce3c9e73613c1be51f61b78d5ef7c0970ffe9f142a90a5e2c"
)
S1 = PurePosixPath("workspaces/decision-platform/research/s1-4x-numeric-parity")
SCALA_CORE = S1 / "scala/src/main/scala/ai/trading/coach/s14x/core"
SCALA_SHELL = S1 / "scala/src/main/scala/ai/trading/coach/s14x/shell"
SCALA_TEST = S1 / "scala/src/test/scala/ai/trading/coach/s14x"
HASKELL_CORE = S1 / "haskell/src/core/S14X/Core"
HASKELL_CONTRACT = S1 / "haskell/src/contract/S14X/Contract"
HASKELL_TEST = S1 / "haskell/test/S14X"
CORRECTNESS_WORKFLOW = PurePosixPath(
    ".github/workflows/s1-4x-numeric-parity-correctness.yml"
)
BENCHMARK_WORKFLOW = PurePosixPath(
    ".github/workflows/s1-4x-numeric-parity-benchmark.yml"
)
SCALA_POLICY = S1 / "contract/scala-source-policy.v1.json"
HASKELL_POLICY = S1 / "contract/haskell-module-safety-policy.v1.json"
HASKELL_SELECTED = S1 / "haskell/selected-profile.v1.json"
HASKELL_TOOLCHAIN_LOCK = S1 / "haskell/toolchain-lock.v1.json"
SCALA_SOURCE_INPUTS = S1 / "scala/source-inputs.v1.json"
HASKELL_SOURCE_INPUTS = S1 / "haskell/source-inputs.v1.json"
SCALA_SELECTED_SOURCE = S1 / "scala/selected-profile.scala"
SCALA_COMPILER_PROFILES = S1 / "scala/compiler-profiles.v1.json"
SCALA_TOOLCHAIN_LOCK = S1 / "scala/toolchain-lock.v1.json"
SCALA_CONTAINERFILE = S1 / "scala/Containerfile"
HASKELL_CONTAINERFILE = S1 / "haskell/Containerfile"
BENCHMARK_PLAN = S1 / "benchmarks/benchmark-plan.v1.json"
PROPERTY_PLAN = S1 / "contract/property-plan.v1.json"
PROPERTY_SEEDS = S1 / "contract/fixtures/property/property-seeds.v1.json"
FUNCTION_REGISTRY = S1 / "contract/function-registry.v1.json"
ERROR_REGISTRY = S1 / "contract/error-registry.v1.json"
CANONICAL_INPUTS = S1 / "contract/fixtures/small/canonical-inputs.v1.json"
CANONICAL_RESULTS = S1 / "contract/fixtures/expected/canonical-results.v1.json"
SEMANTIC_INPUTS = S1 / "contract/fixtures/invalid/semantic-errors.v1.json"
SEMANTIC_RESULTS = S1 / "contract/fixtures/invalid/semantic-errors.expected.v1.json"
SCALA_PROPERTY_RUNNER = S1 / "scala/tools/run-property-evidence.sh"
HASKELL_PROPERTY_RUNNER = S1 / "haskell/tools/run-property-evidence.sh"
RESEARCH_BOUNDARY_TEST = S1 / "integration/tests/test_s1_4r_regression_boundary.py"

RAW_PATHS = {
    "coverage": PurePosixPath("coverage/integration-coverage.json"),
    "scala-property-execution": PurePosixPath(
        "coverage/scala/scala-property-execution-evidence.v1.json"
    ),
    "haskell-property-execution": PurePosixPath(
        "coverage/haskell/haskell-property-execution-evidence.v1.json"
    ),
    "haskell-generated-cabal-provenance": PurePosixPath(
        "coverage/haskell/haskell-generated-cabal-provenance.v1.json"
    ),
    "haskell-generated-cabal": PurePosixPath(
        "coverage/haskell/generated/s1-4x-haskell.cabal"
    ),
    "canonical": PurePosixPath("cross-language/canonical/comparison-report.json"),
    "semantic": PurePosixPath("cross-language/semantic/comparison-report.json"),
    "canonical-reference": PurePosixPath(
        "cross-language/canonical/reference-capture.json"
    ),
    "canonical-scala": PurePosixPath(
        "cross-language/canonical/scala-results.json"
    ),
    "canonical-haskell": PurePosixPath(
        "cross-language/canonical/haskell-results.json"
    ),
    "canonical-summary": PurePosixPath(
        "cross-language/canonical/correctness-summary.json"
    ),
    "semantic-reference": PurePosixPath(
        "cross-language/semantic/reference-capture.json"
    ),
    "semantic-scala": PurePosixPath(
        "cross-language/semantic/scala-results.json"
    ),
    "semantic-haskell": PurePosixPath(
        "cross-language/semantic/haskell-results.json"
    ),
    "semantic-summary": PurePosixPath(
        "cross-language/semantic/correctness-summary.json"
    ),
    "research-regression": PurePosixPath(
        "regression/research-compound-receipt.v1.json"
    ),
    "production-regression": PurePosixPath(
        "regression/production-compound-receipt.v1.json"
    ),
    "scala-policy": PurePosixPath("scala/scala-source-policy-result.v1.json"),
    "scala-dependency": PurePosixPath("scala/scala-dependency-edge-result.v1.json"),
    "scala-format": PurePosixPath(
        "scala/scalafmt/scala-scalafmt-idempotence-result.v1.json"
    ),
    "scala-lint": PurePosixPath(
        "scala/scalafix/scala-semantic-policy-receipt.v1.json"
    ),
    "scala-selected": PurePosixPath("scala/scala-selected-profile-result.v1.json"),
    "scala-oci-build": PurePosixPath("oci/scala/scala-oci-build-result.v1.json"),
    "scala-oci-runtime": PurePosixPath(
        "oci/scala/runtime/scala-oci-correctness-result.v1.json"
    ),
    "haskell-safety": PurePosixPath(
        "haskell/module-safety/haskell-module-safety-result.v1.json"
    ),
    "haskell-lint": PurePosixPath("haskell/hlint/receipt.json"),
    "haskell-format": PurePosixPath("haskell/format/receipt.json"),
    "haskell-oci": PurePosixPath("oci/haskell/oci-correctness-receipt.v1.json"),
    "oci-cross-comparison": PurePosixPath("oci/cross-language-comparison.json"),
}
PRODUCTION_PROJECT = "workspaces/decision-platform/python-services"
RESEARCH_PROJECT = "workspaces/decision-platform/research/s1-4r-jax-risk"
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
SOURCE_INPUT_SETS = {
    "tracked": "files",
    "manifest": "files",
    "format": "files",
    "compile": "files",
    "lint": "files",
    "profileRun": "files",
}
HASKELL_CANDIDATE_ROOTS = ("src", "app", "test", "benchmark")
HASKELL_SOURCE_TREE_INPUTS = (
    "package.yaml",
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
HASKELL_PROPERTY_CLOSURE_INPUTS = (
    *HASKELL_SOURCE_TREE_INPUTS,
    "selected-profile.v1.json",
    "source-inputs.v1.json",
)
EXECUTABLE_SUBJECT_PATHS = frozenset(
    {
        SCALA_PROPERTY_RUNNER,
        HASKELL_PROPERTY_RUNNER,
        *(
            S1 / "haskell" / path
            for path in (
                "tools/assert-toolchain.sh",
                "tools/check-format.sh",
                "tools/check-hlint.sh",
                "tools/hlint_inventory.py",
                "tools/profile_workflow.py",
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
        ),
    }
)
PROPERTY_IDS = (
    "production.output-finite-or-stable-error",
    "simple-returns.scale-invariant",
    "log-returns.scale-invariant",
    "cumulative-return.bankruptcy-absorbing",
    "cumulative-return.manual-product-identity",
    "volatility.translation-and-scale",
    "max-drawdown.bounds",
    "var-hf7-observation-range",
    "var-cvar.shift-and-positive-scale",
    "cvar-threshold-tail",
    "expected-shortfall.permutation-invariant",
    "realized.permutation-invariant",
    "realized.scale-laws",
    "lo.order-sensitive",
    "psr.benchmark-equality",
    "dsr.benchmark-equality",
    "dsr.provenance-count-consistency",
    "kupiec.paired-permutation-invariant",
    "backtest.strict-loss-greater-than-var",
    "christoffersen.order-sensitive",
    "backtest.positive-common-scaling",
    "likelihood.record-invariants",
    "conditional-coverage.component-identity",
    "christoffersen.unidentifiable-transition-rejected",
    "recursive-negative-zero-normalization",
)
PROPERTY_QUALIFICATION_CASES = (
    "path-transform/log_returns/n100000/b1",
    "classical-path-risk/historical_expected_shortfall/n100000/b1",
    "intraday-realized/realized_variance/n100000/b1",
    "serial-sharpe/lo_adjusted_sharpe_ratio/n100000/q5/b1",
    "probabilistic-scalar/probabilistic_sharpe_ratio/b16384",
    "coverage-batch/kupiec_pof/n100000/b32",
    "coverage-batch/christoffersen_conditional_coverage/n100000/b32",
)
CORRECTNESS_TRIGGER_PATHS = (
    "workspaces/decision-platform/research/s1-4x-numeric-parity/**",
    "workspaces/decision-platform/python-services/pyproject.toml",
    "workspaces/decision-platform/python-services/uv.lock",
    "workspaces/decision-platform/python-services/app/financial_engineering/**",
    "workspaces/decision-platform/python-services/tests/financial_engineering/**",
    "workspaces/decision-platform/research/s1-4r-jax-risk/README.md",
    "workspaces/decision-platform/research/s1-4r-jax-risk/pyproject.toml",
    "workspaces/decision-platform/research/s1-4r-jax-risk/uv.lock",
    "workspaces/decision-platform/research/s1-4r-jax-risk/src/s1_4r_risk_research/**",
    "workspaces/decision-platform/research/s1-4r-jax-risk/tests/**",
    "workspaces/decision-platform/research/s1-4r-jax-risk/benchmarks/**",
    "shared-docs/metrics_definitions.md",
    ".gitignore",
    ".github/workflows/s1-4x-*.yml",
)


class CandidateRubricAuditError(ValueError):
    """독립 rubric audit의 subject, raw evidence, 또는 source 판정이 실패했다."""


@dataclass(frozen=True)
class RawSnapshot:
    """한 raw evidence path에서 같은 descriptor로 읽은 immutable snapshot이다."""

    relative_path: PurePosixPath
    payload: bytes
    sha256: str
    identity: tuple[int, ...]


@dataclass(frozen=True)
class RepositoryBlob:
    """benchmark subject Git tree의 regular blob과 그 byte SHA-256이다."""

    path: PurePosixPath
    payload: bytes
    sha256: str


@dataclass(frozen=True)
class DirectoryAnchor:
    """Path 교체와 무관하게 열린 directory descriptor의 identity를 고정한다."""

    path: Path
    descriptor: int
    identity: tuple[int, ...]


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
        raise CandidateRubricAuditError("OUTPUT_JSON_INVALID") from exc


def _strict_json(payload: bytes, *, label: str) -> dict[str, Any]:
    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise CandidateRubricAuditError(f"{label}_DUPLICATE_JSON_KEY:{key}")
            result[key] = value
        return result

    try:
        value = json.loads(
            payload,
            object_pairs_hook=unique_object,
            parse_constant=lambda token: (_ for _ in ()).throw(
                ValueError(f"NONFINITE_JSON:{token}")
            ),
        )
    except CandidateRubricAuditError:
        raise
    except (UnicodeDecodeError, ValueError) as exc:
        raise CandidateRubricAuditError(f"{label}_JSON_INVALID") from exc
    if not isinstance(value, dict):
        raise CandidateRubricAuditError(f"{label}_JSON_INVALID")
    return value


def _git(
    repository: Path,
    *arguments: str,
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["/usr/bin/git", "-c", "core.fsmonitor=false", *arguments],
        cwd=repository,
        capture_output=True,
        check=False,
        timeout=15,
    )


def _canonical_directory(path: Path, *, label: str) -> Path:
    if not path.is_absolute() or path.is_symlink():
        raise CandidateRubricAuditError(f"{label}_INVALID")
    try:
        resolved = path.resolve(strict=True)
        metadata = path.stat(follow_symlinks=False)
    except OSError as exc:
        raise CandidateRubricAuditError(f"{label}_INVALID") from exc
    if resolved != path or not stat.S_ISDIR(metadata.st_mode):
        raise CandidateRubricAuditError(f"{label}_INVALID")
    return resolved


def _validate_repository(repository_root: Path, subject: str) -> Path:
    repository = _canonical_directory(
        repository_root,
        label="SUBJECT",
    )
    if COMMIT.fullmatch(subject) is None:
        raise CandidateRubricAuditError("SUBJECT_INVALID")
    top = _git(repository, "rev-parse", "--show-toplevel")
    head = _git(repository, "rev-parse", "--verify", "HEAD")
    exists = _git(repository, "cat-file", "-e", f"{subject}^{{commit}}")
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
        raise CandidateRubricAuditError("SUBJECT_INVALID") from exc
    if (
        top.returncode != 0
        or actual_top != repository
        or head.returncode != 0
        or actual_head != subject
        or exists.returncode != 0
        or clean.returncode != 0
        or clean.stdout
    ):
        raise CandidateRubricAuditError("SUBJECT_INVALID")
    return repository


def _preflight_paths(
    correctness_root: Path,
    output_root: Path,
) -> tuple[Path, Path]:
    correctness = _canonical_directory(
        correctness_root,
        label="CORRECTNESS_ROOT",
    )
    if (
        not output_root.is_absolute()
        or output_root.name != "rubric-audit"
        or output_root.parent != correctness
        or output_root.exists()
        or output_root.is_symlink()
        or output_root.resolve(strict=False) != output_root
    ):
        raise CandidateRubricAuditError("OUTPUT_ROOT_INVALID")
    return correctness, output_root


def _portable_relative(value: PurePosixPath | str, *, label: str) -> PurePosixPath:
    text = value.as_posix() if isinstance(value, PurePosixPath) else value
    if (
        not isinstance(text, str)
        or not text
        or "\\" in text
        or any(ord(character) < 32 or ord(character) == 127 for character in text)
    ):
        raise CandidateRubricAuditError(f"{label}_PATH_INVALID")
    path = PurePosixPath(text)
    if (
        path.is_absolute()
        or ".." in path.parts
        or path.as_posix() != text
        or not path.parts
    ):
        raise CandidateRubricAuditError(f"{label}_PATH_INVALID")
    return path


def _identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _directory_identity(metadata: os.stat_result) -> tuple[int, ...]:
    # 자식 생성은 directory size/timestamps를 바꾸므로 경로 고정에는 inode만 쓴다.
    return (metadata.st_dev, metadata.st_ino, metadata.st_mode)


def _open_directory_anchor(path: Path, *, label: str) -> DirectoryAnchor:
    canonical = _canonical_directory(path, label=label)
    no_follow = getattr(os, "O_NOFOLLOW", None)
    if no_follow is None:
        raise CandidateRubricAuditError(f"{label}_INVALID")
    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | no_follow
        | getattr(os, "O_CLOEXEC", 0)
    )
    descriptor: int | None = None
    try:
        descriptor = os.open(canonical, flags)
        opened = os.fstat(descriptor)
        current = os.stat(canonical, follow_symlinks=False)
    except OSError as exc:
        if descriptor is not None:
            os.close(descriptor)
        raise CandidateRubricAuditError(f"{label}_INVALID") from exc
    if (
        not stat.S_ISDIR(opened.st_mode)
        or _directory_identity(opened) != _directory_identity(current)
    ):
        os.close(descriptor)
        raise CandidateRubricAuditError(f"{label}_INVALID")
    return DirectoryAnchor(
        path=canonical,
        descriptor=descriptor,
        identity=_directory_identity(opened),
    )


def _verify_directory_anchor(anchor: DirectoryAnchor, *, label: str) -> None:
    try:
        opened = os.fstat(anchor.descriptor)
        current = os.stat(anchor.path, follow_symlinks=False)
    except OSError as exc:
        raise CandidateRubricAuditError(f"{label}_CHANGED") from exc
    if (
        not stat.S_ISDIR(opened.st_mode)
        or not stat.S_ISDIR(current.st_mode)
        or _directory_identity(opened) != anchor.identity
        or _directory_identity(current) != anchor.identity
    ):
        raise CandidateRubricAuditError(f"{label}_CHANGED")


def _read_raw_snapshot(
    correctness_fd: int,
    relative_path: PurePosixPath,
    *,
    label: str,
) -> RawSnapshot:
    relative = _portable_relative(relative_path, label=label)
    no_follow = getattr(os, "O_NOFOLLOW", None)
    if no_follow is None:
        raise CandidateRubricAuditError(f"{label}_RAW_EVIDENCE_INVALID")
    directory_flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | no_follow
        | getattr(os, "O_CLOEXEC", 0)
    )
    file_flags = os.O_RDONLY | no_follow | getattr(os, "O_CLOEXEC", 0)
    directory_fds: list[int] = []
    file_fd: int | None = None
    try:
        directory_fd = os.dup(correctness_fd)
        directory_fds.append(directory_fd)
        for component in relative.parts[:-1]:
            directory_fd = os.open(
                component,
                directory_flags,
                dir_fd=directory_fd,
            )
            directory_fds.append(directory_fd)
        file_fd = os.open(
            relative.name,
            file_flags,
            dir_fd=directory_fd,
        )
        before = os.fstat(file_fd)
        if not stat.S_ISREG(before.st_mode):
            raise CandidateRubricAuditError(f"{label}_RAW_EVIDENCE_INVALID")
        blocks: list[bytes] = []
        while block := os.read(file_fd, 1024 * 1024):
            blocks.append(block)
        payload = b"".join(blocks)
        after = os.fstat(file_fd)
        current = os.stat(
            relative.name,
            dir_fd=directory_fd,
            follow_symlinks=False,
        )
        if (
            _identity(before) != _identity(after)
            or _identity(after) != _identity(current)
            or len(payload) != after.st_size
        ):
            raise CandidateRubricAuditError(f"{label}_RAW_EVIDENCE_CHANGED")
    except CandidateRubricAuditError:
        raise
    except OSError as exc:
        raise CandidateRubricAuditError(f"{label}_RAW_EVIDENCE_INVALID") from exc
    finally:
        if file_fd is not None:
            os.close(file_fd)
        for descriptor in reversed(directory_fds):
            os.close(descriptor)
    return RawSnapshot(
        relative_path=relative,
        payload=payload,
        sha256=hashlib.sha256(payload).hexdigest(),
        identity=_identity(after),
    )


def _raw_json(
    correctness_fd: int,
    path: PurePosixPath,
    *,
    label: str,
    snapshots: dict[PurePosixPath, RawSnapshot],
) -> tuple[RawSnapshot, dict[str, Any]]:
    try:
        snapshot = _read_raw_snapshot(
            correctness_fd,
            path,
            label=label,
        )
    except CandidateRubricAuditError as exc:
        raise CandidateRubricAuditError(f"RAW_EVIDENCE_INVALID:{label}") from exc
    snapshots[snapshot.relative_path] = snapshot
    return snapshot, _strict_json(snapshot.payload, label=label)


def _raw_snapshot(
    correctness_fd: int,
    path: PurePosixPath,
    *,
    label: str,
    key: str,
    snapshots: dict[PurePosixPath, RawSnapshot],
    raw: dict[str, RawSnapshot],
) -> RawSnapshot:
    try:
        snapshot = _read_raw_snapshot(correctness_fd, path, label=label)
    except CandidateRubricAuditError as exc:
        raise CandidateRubricAuditError(f"RAW_EVIDENCE_INVALID:{label}") from exc
    snapshots[snapshot.relative_path] = snapshot
    raw[key] = snapshot
    return snapshot


def _verify_raw_snapshots_unchanged(
    correctness_fd: int,
    snapshots: tuple[RawSnapshot, ...],
) -> None:
    for snapshot in snapshots:
        try:
            current = _read_raw_snapshot(
                correctness_fd,
                snapshot.relative_path,
                label="RAW_EVIDENCE",
            )
        except CandidateRubricAuditError as exc:
            raise CandidateRubricAuditError("RAW_EVIDENCE_CHANGED") from exc
        if (
            current.identity != snapshot.identity
            or current.sha256 != snapshot.sha256
            or current.payload != snapshot.payload
        ):
            raise CandidateRubricAuditError("RAW_EVIDENCE_CHANGED")


def _git_blob(
    repository: Path,
    subject: str,
    path_value: PurePosixPath,
) -> RepositoryBlob:
    path = _portable_relative(path_value, label="REPOSITORY_BLOB")
    encoded_path = path.as_posix().encode("utf-8")
    entry = _git(
        repository,
        "ls-tree",
        "-z",
        subject,
        "--",
        path.as_posix(),
    )
    entries = entry.stdout.split(b"\0")
    if (
        entry.returncode != 0
        or entries[-1] != b""
        or len(entries) != 2
        or b"\t" not in entries[0]
    ):
        raise CandidateRubricAuditError(f"REPOSITORY_BLOB_INVALID:{path}")
    metadata, actual_path = entries[0].split(b"\t", 1)
    fields = metadata.split()
    expected_mode = b"100755" if path in EXECUTABLE_SUBJECT_PATHS else b"100644"
    if (
        fields[:2] != [expected_mode, b"blob"]
        or len(fields) != 3
        or actual_path != encoded_path
    ):
        raise CandidateRubricAuditError(f"REPOSITORY_BLOB_INVALID:{path}")
    content = _git(
        repository,
        "show",
        f"{subject}:{path.as_posix()}",
    )
    if content.returncode != 0:
        raise CandidateRubricAuditError(f"REPOSITORY_BLOB_INVALID:{path}")
    return RepositoryBlob(
        path=path,
        payload=content.stdout,
        sha256=hashlib.sha256(content.stdout).hexdigest(),
    )


def _git_inventory(
    repository: Path,
    subject: str,
    prefix_value: PurePosixPath,
    *,
    suffix: str,
) -> tuple[RepositoryBlob, ...]:
    prefix = _portable_relative(prefix_value, label="REPOSITORY_INVENTORY")
    result = _git(
        repository,
        "ls-tree",
        "-r",
        "-z",
        subject,
        "--",
        prefix.as_posix(),
    )
    if result.returncode != 0 or not result.stdout.endswith(b"\0"):
        raise CandidateRubricAuditError("REPOSITORY_INVENTORY_INVALID")
    paths: list[PurePosixPath] = []
    for raw_entry in result.stdout.split(b"\0")[:-1]:
        if b"\t" not in raw_entry:
            raise CandidateRubricAuditError("REPOSITORY_INVENTORY_INVALID")
        metadata, raw_path = raw_entry.split(b"\t", 1)
        fields = metadata.split()
        try:
            path_text = raw_path.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise CandidateRubricAuditError("REPOSITORY_INVENTORY_INVALID") from exc
        path = _portable_relative(path_text, label="REPOSITORY_INVENTORY")
        if not path_text.endswith(suffix):
            continue
        if len(fields) != 3 or fields[:2] != [b"100644", b"blob"]:
            raise CandidateRubricAuditError(f"REPOSITORY_BLOB_INVALID:{path}")
        paths.append(path)
    if not paths:
        raise CandidateRubricAuditError("REPOSITORY_INVENTORY_EMPTY")
    if paths != sorted(paths, key=lambda item: item.as_posix().encode("utf-8")):
        raise CandidateRubricAuditError("REPOSITORY_INVENTORY_ORDER_INVALID")
    return tuple(_git_blob(repository, subject, path) for path in paths)


def _decode_blob(blob: RepositoryBlob) -> str:
    try:
        text = blob.payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise CandidateRubricAuditError(
            f"REPOSITORY_SOURCE_UTF8_INVALID:{blob.path}"
        ) from exc
    if "\x00" in text:
        raise CandidateRubricAuditError(f"REPOSITORY_SOURCE_UTF8_INVALID:{blob.path}")
    return text


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise CandidateRubricAuditError(code)


def _is_exact_int(value: Any, expected: int) -> bool:
    return type(value) is int and value == expected


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and SHA256.fullmatch(value) is not None


def _canonical_value_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json_bytes(value)[:-1]).hexdigest()


def _exact_object(
    value: Any,
    fields: set[str],
    *,
    code: str,
) -> Mapping[str, Any]:
    _require(isinstance(value, dict) and set(value) == fields, code)
    if not isinstance(value, dict):
        raise CandidateRubricAuditError(code)
    return value


def _source_role(language: str, path: str) -> str:
    if language == "scala":
        if path in {"project.scala", "selected-profile.scala"}:
            return "configuration"
        if path.startswith("src/main/scala/"):
            return "main"
        if path.startswith("src/test/scala/"):
            return "test"
        if path.startswith("benchmarks/"):
            return "benchmark"
    else:
        if path in {"package.yaml", "selected-profile.v1.json"}:
            return "configuration"
        if path.startswith("test/"):
            return "test"
        if path.startswith("benchmark/"):
            return "benchmark"
        if path.startswith(("src/", "app/")):
            return "main"
    raise CandidateRubricAuditError(f"{language.upper()}_SOURCE_INPUT_INVALID")


def _validate_source_manifest(
    repository: Path,
    subject: str,
    manifest_blob: RepositoryBlob,
    *,
    language: str,
) -> tuple[dict[str, RepositoryBlob], Mapping[str, Any]]:
    document = _strict_json(
        manifest_blob.payload,
        label=f"{language.upper()}_SOURCE_INPUTS",
    )
    _exact_object(
        document,
        {
            "schemaVersion",
            "language",
            "files",
            "inputSets",
            "canonicalManifestSha256",
        },
        code=f"{language.upper()}_SOURCE_INPUT_INVALID",
    )
    raw_files = document.get("files")
    _require(
        document.get("schemaVersion") == "s1.4x-source-input-manifest-v1"
        and document.get("language") == language
        and document.get("inputSets") == SOURCE_INPUT_SETS
        and isinstance(raw_files, dict)
        and bool(raw_files)
        and list(raw_files)
        == sorted(raw_files, key=lambda value: value.encode("utf-8")),
        f"{language.upper()}_SOURCE_INPUT_INVALID",
    )
    if not isinstance(raw_files, dict):
        raise CandidateRubricAuditError(f"{language.upper()}_SOURCE_INPUT_INVALID")
    blobs: dict[str, RepositoryBlob] = {}
    lines: list[bytes] = []
    for raw_path, raw_entry in raw_files.items():
        relative = _portable_relative(raw_path, label=f"{language.upper()}_SOURCE")
        entry = _exact_object(
            raw_entry,
            {"role", "sha256"},
            code=f"{language.upper()}_SOURCE_INPUT_INVALID",
        )
        _require(
            entry.get("role") == _source_role(language, raw_path)
            and _is_sha256(entry.get("sha256")),
            f"{language.upper()}_SOURCE_INPUT_INVALID",
        )
        blob = _git_blob(
            repository,
            subject,
            S1 / language / relative,
        )
        _require(
            blob.sha256 == entry["sha256"],
            f"{language.upper()}_SOURCE_INPUT_HASH_INVALID:{raw_path}",
        )
        blobs[raw_path] = blob
        lines.append(f"{blob.sha256}  {raw_path}\n".encode("utf-8"))
    _require(
        document.get("canonicalManifestSha256")
        == hashlib.sha256(b"".join(lines)).hexdigest(),
        f"{language.upper()}_SOURCE_MANIFEST_HASH_INVALID",
    )
    return blobs, document


def _scala_property_source_closure(
    sources: Mapping[str, RepositoryBlob],
) -> str:
    selected = [
        path
        for path in sources
        if path in {"project.scala", "selected-profile.scala"}
        or path.startswith(("src/main/scala/", "src/test/scala/"))
    ]
    digest = hashlib.sha256()
    for path in sorted(selected, key=lambda value: value.encode("utf-8")):
        digest.update(path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(sources[path].payload)
        digest.update(b"\0")
    return digest.hexdigest()


def _haskell_source_tree_sha256(
    repository: Path,
    subject: str,
    sources: Mapping[str, RepositoryBlob],
    generated_cabal: RawSnapshot,
) -> str:
    source_paths = {
        path
        for path in sources
        if path.endswith(".hs")
        and path.split("/", 1)[0] in HASKELL_CANDIDATE_ROOTS
    }
    _require(
        set(sources)
        == source_paths | {"package.yaml", "selected-profile.v1.json"},
        "HASKELL_SOURCE_INPUT_SET_INVALID",
    )
    entries = []
    for path in sorted(
        source_paths | set(HASKELL_SOURCE_TREE_INPUTS) | {"s1-4x-haskell.cabal"},
        key=lambda value: value.encode("utf-8"),
    ):
        sha256 = (
            generated_cabal.sha256
            if path == "s1-4x-haskell.cabal"
            else _git_blob(repository, subject, S1 / "haskell" / path).sha256
        )
        entries.append({"path": path, "sha256": sha256})
    return _canonical_value_sha256(entries)


def _haskell_property_source_closure(
    repository: Path,
    subject: str,
    sources: Mapping[str, RepositoryBlob],
    generated_cabal: RawSnapshot,
) -> str:
    source_paths = {
        path
        for path in sources
        if path.endswith(".hs")
        and path.split("/", 1)[0] in HASKELL_CANDIDATE_ROOTS
    }
    entries: list[bytes] = []
    for path in sorted(
        source_paths
        | set(HASKELL_PROPERTY_CLOSURE_INPUTS)
        | {"s1-4x-haskell.cabal"},
        key=lambda value: value.encode("utf-8"),
    ):
        sha256 = (
            generated_cabal.sha256
            if path == "s1-4x-haskell.cabal"
            else _git_blob(repository, subject, S1 / "haskell" / path).sha256
        )
        entries.append(
            path.encode("utf-8")
            + b"\0"
            + sha256.encode("ascii")
            + b"\n"
        )
    return hashlib.sha256(b"".join(entries)).hexdigest()


def _property_stack_root_path_id(output_directory: Path) -> str:
    output = output_directory.resolve(strict=True)
    suffix = hashlib.sha256(
        b"property\0" + os.fsencode(str(output))
    ).hexdigest()[:24]
    return f"S1_4X_CACHE_ROOT/stack-root-property-{suffix}"


def _validate_generated_cabal_provenance(
    document: Mapping[str, Any],
    *,
    snapshot: RawSnapshot,
    artifact: RawSnapshot,
    subject: str,
    output_directory: Path,
    package_yaml: RepositoryBlob,
    manifest: RepositoryBlob,
    toolchain_lock: RepositoryBlob,
    source_tree_sha256: str,
    property_closure_sha256: str,
    selected_document: Mapping[str, Any],
) -> None:
    _exact_object(
        document,
        {
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
        },
        code="HASKELL_GENERATED_CABAL_INVALID",
    )
    package = _exact_object(
        document.get("packageYaml"),
        {"path", "blobSha256"},
        code="HASKELL_GENERATED_CABAL_INVALID",
    )
    source_manifest = _exact_object(
        document.get("sourceInputManifest"),
        {"path", "blobSha256"},
        code="HASKELL_GENERATED_CABAL_INVALID",
    )
    stack = _exact_object(
        document.get("stack"),
        {"pathId", "version", "binarySha256"},
        code="HASKELL_GENERATED_CABAL_INVALID",
    )
    hpack = _exact_object(
        document.get("hpack"),
        {"version", "versionOutputSha256"},
        code="HASKELL_GENERATED_CABAL_INVALID",
    )
    build = _exact_object(
        document.get("build"),
        {
            "portableArgv",
            "portableArgvSha256",
            "runtimeArgvSha256",
            "stackRootPathId",
            "exitCode",
        },
        code="HASKELL_GENERATED_CABAL_INVALID",
    )
    generated = _exact_object(
        document.get("generatedCabal"),
        {
            "repositoryRelativePath",
            "artifactPath",
            "sha256",
            "sizeBytes",
            "preBuildSha256",
            "postBuildSha256",
        },
        code="HASKELL_GENERATED_CABAL_INVALID",
    )
    toolchain = _strict_json(
        toolchain_lock.payload,
        label="HASKELL_TOOLCHAIN_LOCK",
    )
    resolved_stack = (
        toolchain.get("resolvedTools", {}).get("stack")
        if isinstance(toolchain.get("resolvedTools"), dict)
        else None
    )
    portable_argv = [
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
        " ".join(selected_document.get("ghcOptions", [])),
    ]
    expected_stack_root = _property_stack_root_path_id(output_directory)
    _require(
        snapshot.relative_path
        == RAW_PATHS["haskell-generated-cabal-provenance"]
        and artifact.relative_path == RAW_PATHS["haskell-generated-cabal"]
        and document.get("schemaVersion")
        == "s1.4x-haskell-generated-cabal-provenance-v1"
        and document.get("benchmarkSubjectCommit") == subject
        and document.get("toolchainLockSha256") == toolchain_lock.sha256
        and package
        == {
            "path": (S1 / "haskell/package.yaml").as_posix(),
            "blobSha256": package_yaml.sha256,
        }
        and source_manifest
        == {
            "path": HASKELL_SOURCE_INPUTS.as_posix(),
            "blobSha256": manifest.sha256,
        }
        and isinstance(resolved_stack, dict)
        and stack
        == {
            "pathId": resolved_stack.get("pathId"),
            "version": resolved_stack.get("version"),
            "binarySha256": resolved_stack.get("sha256"),
        }
        and hpack
        == {
            "version": "0.39.6",
            "versionOutputSha256": hashlib.sha256(b"0.39.6\n").hexdigest(),
        }
        and build.get("portableArgv") == portable_argv
        and build.get("portableArgvSha256")
        == _canonical_value_sha256(portable_argv)
        and _is_sha256(build.get("runtimeArgvSha256"))
        and build.get("stackRootPathId") == expected_stack_root
        and _is_exact_int(build.get("exitCode"), 0)
        and generated.get("repositoryRelativePath")
        == (S1 / "haskell/s1-4x-haskell.cabal").as_posix()
        and generated.get("artifactPath")
        == RAW_PATHS["haskell-generated-cabal"].as_posix()
        and generated.get("sha256") == artifact.sha256
        and _is_exact_int(generated.get("sizeBytes"), len(artifact.payload))
        and generated.get("preBuildSha256") == artifact.sha256
        and generated.get("postBuildSha256") == artifact.sha256
        and document.get("sourceTreeSha256") == source_tree_sha256
        and document.get("propertyClosureSha256")
        == property_closure_sha256
        and document.get("status") == "PASS",
        "HASKELL_GENERATED_CABAL_INVALID",
    )


def _validate_property_execution_entries(
    value: Any,
    *,
    label: str,
    property_ids: Sequence[str],
    seeds: Sequence[int],
    minimum_successful_per_property: int,
    minimum_successful_per_seed: int,
    maximum_discarded_per_property: int,
    maximum_discard_ratio: float,
) -> None:
    _require(
        isinstance(value, list) and len(value) == len(property_ids),
        f"{label}_INVALID",
    )
    if not isinstance(value, list):
        raise CandidateRubricAuditError(f"{label}_INVALID")
    for property_index, raw_property in enumerate(value):
        entry = _exact_object(
            raw_property,
            {
                "propertyId",
                "successfulTests",
                "discardedTests",
                "attemptedTests",
                "shrinks",
                "seedCount",
                "seedExecutions",
                "status",
            },
            code=f"{label}_INVALID",
        )
        property_id = entry.get("propertyId")
        seed_executions = entry.get("seedExecutions")
        _require(
            property_id == property_ids[property_index]
            and _is_exact_int(entry.get("seedCount"), len(seeds))
            and isinstance(seed_executions, list)
            and len(seed_executions) == len(seeds)
            and entry.get("status") == "PASS",
            f"{label}_INVALID",
        )
        if not isinstance(seed_executions, list):
            raise CandidateRubricAuditError(f"{label}_INVALID")
        successful_sum = 0
        discarded_sum = 0
        attempted_sum = 0
        shrinks_sum = 0
        for seed_index, raw_seed in enumerate(seed_executions):
            seed = _exact_object(
                raw_seed,
                {
                    "seedIndex",
                    "originalSeed",
                    "successfulTests",
                    "discardedTests",
                    "attemptedTests",
                    "replayToken",
                    "shrinks",
                    "status",
                },
                code=f"{label}_INVALID",
            )
            _require(
                _is_exact_int(seed.get("seedIndex"), seed_index)
                and seed.get("originalSeed") == seeds[seed_index]
                and type(seed.get("successfulTests")) is int
                and seed["successfulTests"] >= minimum_successful_per_seed
                and type(seed.get("discardedTests")) is int
                and seed["discardedTests"] >= 0
                and type(seed.get("attemptedTests")) is int
                and seed["attemptedTests"]
                == seed["successfulTests"] + seed["discardedTests"]
                and seed["attemptedTests"] > 0
                and seed["discardedTests"] / seed["attemptedTests"]
                <= maximum_discard_ratio
                and isinstance(seed.get("replayToken"), str)
                and bool(seed["replayToken"])
                and type(seed.get("shrinks")) is int
                and seed["shrinks"] >= 0
                and seed.get("status") == "PASS",
                f"{label}_INVALID",
            )
            successful_sum += seed["successfulTests"]
            discarded_sum += seed["discardedTests"]
            attempted_sum += seed["attemptedTests"]
            shrinks_sum += seed["shrinks"]
        _require(
            _is_exact_int(entry.get("successfulTests"), successful_sum)
            and successful_sum >= minimum_successful_per_property
            and _is_exact_int(entry.get("discardedTests"), discarded_sum)
            and discarded_sum <= maximum_discarded_per_property
            and _is_exact_int(entry.get("attemptedTests"), attempted_sum)
            and attempted_sum == successful_sum + discarded_sum
            and discarded_sum / attempted_sum <= maximum_discard_ratio
            and _is_exact_int(entry.get("shrinks"), shrinks_sum),
            f"{label}_INVALID",
        )


def _validate_property_execution_reports(
    scala: Mapping[str, Any],
    haskell: Mapping[str, Any],
    *,
    property_plan: RepositoryBlob,
    property_plan_document: Mapping[str, Any],
    property_seeds: RepositoryBlob,
    scala_runner: RepositoryBlob,
    haskell_runner: RepositoryBlob,
    scala_source_closure_sha256: str,
    haskell_source_closure_sha256: str,
    scala_profile: str,
    haskell_profile: str,
    scala_toolchain_document: Mapping[str, Any],
    haskell_manifest: RepositoryBlob,
    haskell_selected: RepositoryBlob,
    haskell_selected_document: Mapping[str, Any],
    haskell_stack_root_path_id: str,
) -> None:
    raw_properties = property_plan_document.get("properties")
    seed_document = _strict_json(
        property_seeds.payload,
        label="PROPERTY_SEEDS",
    )
    raw_seeds = seed_document.get("seeds")
    _require(
        property_plan_document.get("schemaVersion")
        == "s1.4x-property-plan-v1"
        and _is_exact_int(property_plan_document.get("seedCount"), 24)
        and _is_exact_int(
            property_plan_document.get("minimumSuccessfulPerProperty"),
            1000,
        )
        and _is_exact_int(
            property_plan_document.get("maximumDiscardedPerProperty"),
            100,
        )
        and property_plan_document.get("maximumDiscardRatio") == 0.1
        and isinstance(raw_properties, list)
        and tuple(
            item.get("propertyId")
            for item in raw_properties
            if isinstance(item, dict)
        )
        == PROPERTY_IDS
        and isinstance(raw_seeds, list)
        and len(raw_seeds) == 24
        and all(type(seed) is int for seed in raw_seeds)
        and len(set(raw_seeds)) == 24,
        "PROPERTY_PLAN_INVALID",
    )
    if not isinstance(raw_seeds, list):
        raise CandidateRubricAuditError("PROPERTY_PLAN_INVALID")
    common = {
        "schemaVersion",
        "implementation",
        "propertyPlanSha256",
        "seedCorpusSha256",
        "seedCount",
        "minimumSuccessfulPerSeed",
        "framework",
        "toolchainProfile",
        "commandArgvSha256",
        "runnerSha256",
        "sourceClosureSha256",
        "startedAt",
        "finishedAt",
        "exitCode",
        "properties",
        "status",
    }
    _exact_object(
        scala,
        common | {"maximumDiscardRatio", "scalaCliBinarySha256"},
        code="SCALA_PROPERTY_EXECUTION_INVALID",
    )
    _exact_object(
        haskell,
        common
        | {
            "outerCommandArgvSha256",
            "buildArgvSha256",
            "sourceInputManifestSha256",
            "selectedProfileSha256",
            "sourceTreeSha256",
            "propertyClosureSha256",
            "profileGhcOptions",
            "profileOptionsSha256",
            "stackRootPathId",
        },
        code="HASKELL_PROPERTY_EXECUTION_INVALID",
    )
    scala_cli = scala_toolchain_document.get("scalaCli")
    _require(
        isinstance(scala_cli, dict)
        and scala.get("schemaVersion")
        == "s1.4x-candidate-property-execution-v1"
        and scala.get("implementation") == "scala-3.8.4-jvm25"
        and scala.get("propertyPlanSha256") == property_plan.sha256
        and scala.get("seedCorpusSha256") == property_seeds.sha256
        and _is_exact_int(scala.get("seedCount"), 24)
        and _is_exact_int(scala.get("minimumSuccessfulPerSeed"), 42)
        and scala.get("maximumDiscardRatio")
        == property_plan_document.get("maximumDiscardRatio")
        and scala.get("framework") == "scala-check-1.19.0"
        and scala.get("toolchainProfile") == scala_profile
        and scala.get("scalaCliBinarySha256")
        == scala_cli.get("binarySha256")
        and _is_sha256(scala.get("commandArgvSha256"))
        and scala.get("runnerSha256") == scala_runner.sha256
        and scala.get("sourceClosureSha256")
        == scala_source_closure_sha256
        and isinstance(scala.get("startedAt"), str)
        and scala["startedAt"].endswith("Z")
        and isinstance(scala.get("finishedAt"), str)
        and scala["finishedAt"].endswith("Z")
        and _is_exact_int(scala.get("exitCode"), 0)
        and scala.get("status") == "PASS",
        "SCALA_PROPERTY_EXECUTION_INVALID",
    )
    _validate_property_execution_entries(
        scala.get("properties"),
        label="SCALA_PROPERTY_EXECUTION_ENTRIES",
        property_ids=PROPERTY_IDS,
        seeds=raw_seeds,
        minimum_successful_per_property=1000,
        minimum_successful_per_seed=42,
        maximum_discarded_per_property=100,
        maximum_discard_ratio=0.1,
    )
    _require(
        haskell.get("schemaVersion")
        == "s1.4x-candidate-property-execution-v1"
        and haskell.get("implementation") == "haskell"
        and haskell.get("propertyPlanSha256") == property_plan.sha256
        and haskell.get("seedCorpusSha256") == property_seeds.sha256
        and _is_exact_int(haskell.get("seedCount"), 24)
        and _is_exact_int(haskell.get("minimumSuccessfulPerSeed"), 42)
        and haskell.get("framework") == "QuickCheck-2.15.0.1"
        and haskell.get("toolchainProfile")
        == f"haskell-ghc-9.10.3-{haskell_profile}"
        and all(
            _is_sha256(haskell.get(field))
            for field in (
                "commandArgvSha256",
                "outerCommandArgvSha256",
                "buildArgvSha256",
            )
        )
        and haskell.get("runnerSha256") == haskell_runner.sha256
        and haskell.get("sourceClosureSha256")
        == haskell_source_closure_sha256
        and haskell.get("sourceInputManifestSha256")
        == haskell_manifest.sha256
        and haskell.get("selectedProfileSha256")
        == haskell_selected.sha256
        and haskell.get("sourceTreeSha256")
        == haskell_selected_document.get("sourceTreeSha256")
        and haskell.get("propertyClosureSha256")
        == haskell_source_closure_sha256
        and haskell.get("profileGhcOptions")
        == haskell_selected_document.get("ghcOptions")
        and haskell.get("profileOptionsSha256")
        == haskell_selected_document.get("optionsSha256")
        and re.fullmatch(
            r"S1_4X_CACHE_ROOT/stack-root-property-[0-9a-f]{24}",
            str(haskell.get("stackRootPathId")),
        )
        is not None
        and haskell.get("stackRootPathId") == haskell_stack_root_path_id
        and isinstance(haskell.get("startedAt"), str)
        and haskell["startedAt"].endswith("Z")
        and isinstance(haskell.get("finishedAt"), str)
        and haskell["finishedAt"].endswith("Z")
        and _is_exact_int(haskell.get("exitCode"), 0)
        and haskell.get("status") == "PASS",
        "HASKELL_PROPERTY_EXECUTION_INVALID",
    )
    _validate_property_execution_entries(
        haskell.get("properties"),
        label="HASKELL_PROPERTY_EXECUTION_ENTRIES",
        property_ids=PROPERTY_IDS,
        seeds=raw_seeds,
        minimum_successful_per_property=1000,
        minimum_successful_per_seed=42,
        maximum_discarded_per_property=100,
        maximum_discard_ratio=0.1,
    )


def _validate_coverage(
    document: Mapping[str, Any],
    *,
    property_plan: RepositoryBlob,
    property_seeds: RepositoryBlob,
    scala_runner: RepositoryBlob,
    haskell_runner: RepositoryBlob,
    scala_source_closure_sha256: str,
    haskell_source_closure_sha256: str,
    scala_profile: str,
    haskell_profile: str,
) -> None:
    _exact_object(
        document,
        {
            "schemaVersion",
            "candidateCount",
            "candidates",
            "propertyCountPerCandidate",
            "functionCountPerCandidate",
            "errorTrackCountsPerCandidate",
            "errorVerificationModeCountsPerCandidate",
            "status",
        },
        code="COVERAGE_INVALID",
    )
    error_modes = {
        "processDynamic": 29,
        "referenceObjectModel": 1,
        "registryStatic": 2,
    }
    _require(
        document.get("schemaVersion") == "s1.4x-integration-coverage-v1"
        and document.get("status") == "PASS"
        and _is_exact_int(document.get("candidateCount"), 2)
        and _is_exact_int(document.get("propertyCountPerCandidate"), 25)
        and _is_exact_int(document.get("functionCountPerCandidate"), 20)
        and document.get("errorTrackCountsPerCandidate")
        == {"s1.4": 19, "s1.4r": 13}
        and document.get("errorVerificationModeCountsPerCandidate")
        == error_modes,
        "COVERAGE_INVALID",
    )
    raw_candidates = document.get("candidates")
    _require(
        isinstance(raw_candidates, list) and len(raw_candidates) == 2,
        "COVERAGE_INVALID",
    )
    if not isinstance(raw_candidates, list):
        raise CandidateRubricAuditError("COVERAGE_INVALID")
    indexed: dict[str, Mapping[str, Any]] = {}
    for raw_candidate in raw_candidates:
        _exact_object(
            raw_candidate,
            {
                "implementation",
                "reportedImplementation",
                "propertyPlanSha256",
                "propertyCount",
                "functionCount",
                "errorCount",
                "errorTrackCounts",
                "errorVerificationModeCounts",
                "processDynamicErrorCount",
                "staticErrorCount",
                "propertyExecution",
                "status",
            },
            code="COVERAGE_INVALID",
        )
        _require(isinstance(raw_candidate, dict), "COVERAGE_INVALID")
        candidate = raw_candidate.get("implementation")
        _require(
            isinstance(candidate, str) and candidate not in indexed,
            "COVERAGE_INVALID",
        )
        indexed[candidate] = raw_candidate
    _require(tuple(indexed) == CANDIDATES, "COVERAGE_INVALID")
    expected = {
        "scala": {
            "reported": "scala-3.8.4-jvm25",
            "framework": "scala-check-1.19.0",
            "profile": scala_profile,
            "runner": scala_runner.sha256,
            "source": scala_source_closure_sha256,
        },
        "haskell": {
            "reported": "haskell",
            "framework": "QuickCheck-2.15.0.1",
            "profile": f"haskell-ghc-9.10.3-{haskell_profile}",
            "runner": haskell_runner.sha256,
            "source": haskell_source_closure_sha256,
        },
    }
    for candidate in CANDIDATES:
        entry = indexed[candidate]
        execution = _exact_object(
            entry.get("propertyExecution"),
            {
                "framework",
                "toolchainProfile",
                "seedCorpusSha256",
                "seedCount",
                "minimumSuccessfulPerSeed",
                "runnerSha256",
                "sourceClosureSha256",
                "startedAt",
                "finishedAt",
            },
            code="COVERAGE_INVALID",
        )
        candidate_expected = expected[candidate]
        _require(
            entry.get("status") == "PASS"
            and entry.get("reportedImplementation")
            == candidate_expected["reported"]
            and entry.get("propertyPlanSha256") == property_plan.sha256
            and _is_exact_int(entry.get("functionCount"), 20)
            and _is_exact_int(entry.get("propertyCount"), 25)
            and _is_exact_int(entry.get("errorCount"), 32)
            and entry.get("errorTrackCounts") == {"s1.4": 19, "s1.4r": 13}
            and entry.get("errorVerificationModeCounts") == error_modes
            and _is_exact_int(entry.get("processDynamicErrorCount"), 29)
            and _is_exact_int(entry.get("staticErrorCount"), 3)
            and execution.get("framework") == candidate_expected["framework"]
            and execution.get("toolchainProfile") == candidate_expected["profile"]
            and execution.get("seedCorpusSha256") == property_seeds.sha256
            and _is_exact_int(execution.get("seedCount"), 24)
            and _is_exact_int(execution.get("minimumSuccessfulPerSeed"), 42)
            and execution.get("runnerSha256") == candidate_expected["runner"]
            and execution.get("sourceClosureSha256")
            == candidate_expected["source"]
            and isinstance(execution.get("startedAt"), str)
            and execution["startedAt"].endswith("Z")
            and isinstance(execution.get("finishedAt"), str)
            and execution["finishedAt"].endswith("Z"),
            "COVERAGE_INVALID",
        )


def _validate_comparison(
    document: Mapping[str, Any],
    *,
    matrix: str,
) -> None:
    request_id = {
        "canonical": "s1.4x-canonical-small-v1",
        "semantic": "s1.4x-semantic-errors-v1",
    }[matrix]
    _require(
        document.get("schemaVersion") == "s1.4x-comparison-report-v1"
        and document.get("requestId") == request_id
        and _is_exact_int(document.get("implementationCount"), 2)
        and _is_exact_int(document.get("mismatchCount"), 0)
        and document.get("mismatches") == []
        and document.get("status") == "PASS",
        f"COMPARISON_{matrix.upper()}_INVALID",
    )


def _validate_native_cross_binding(
    matrix: str,
    *,
    comparison: RawSnapshot,
    reference: RawSnapshot,
    scala: RawSnapshot,
    haskell: RawSnapshot,
    summary: RawSnapshot,
    expected: RepositoryBlob,
    selected_scala: RawSnapshot,
    selected_haskell: RawSnapshot,
) -> None:
    request_id = {
        "canonical": "s1.4x-canonical-small-v1",
        "semantic": "s1.4x-semantic-errors-v1",
    }[matrix]
    reference_document = _strict_json(
        reference.payload, label=f"{matrix.upper()}_REFERENCE_CAPTURE"
    )
    scala_document = _strict_json(
        scala.payload, label=f"{matrix.upper()}_SCALA_ACTUAL"
    )
    haskell_document = _strict_json(
        haskell.payload, label=f"{matrix.upper()}_HASKELL_ACTUAL"
    )
    summary_document = _strict_json(
        summary.payload, label=f"{matrix.upper()}_CORRECTNESS_SUMMARY"
    )
    artifacts = summary_document.get("artifacts")
    _require(
        reference_document.get("schemaVersion")
        == "s1.4x-reference-capture-report-v1"
        and _is_exact_int(reference_document.get("processCount"), 2)
        and reference_document.get("resultSha256") == expected.sha256
        and reference_document.get("status") == "PASS"
        and scala.payload == selected_scala.payload
        and haskell.payload == selected_haskell.payload
        and scala_document.get("requestId") == request_id
        and scala_document.get("implementation") == "scala-3.8.4-jvm25"
        and isinstance(scala_document.get("results"), list)
        and haskell_document.get("requestId") == request_id
        and haskell_document.get("implementation") == "haskell-ghc-9.10.3"
        and isinstance(haskell_document.get("results"), list)
        and summary_document.get("schemaVersion")
        == "s1.4x-integration-correctness-v1"
        and summary_document.get("requestId") == request_id
        and summary_document.get("oracleImplementation")
        == "python-frozen-oracle"
        and summary_document.get("candidateImplementations")
        == ["scala-3.8.4-jvm25", "haskell-ghc-9.10.3"]
        and _is_exact_int(
            summary_document.get("caseCount"),
            len(scala_document["results"]),
        )
        and len(scala_document["results"]) == len(haskell_document["results"])
        and _is_exact_int(summary_document.get("mismatchCount"), 0)
        and artifacts
        == {
            "comparison-report.json": comparison.sha256,
            "haskell-results.json": haskell.sha256,
            "reference-capture.json": reference.sha256,
            "scala-results.json": scala.sha256,
        }
        and summary_document.get("referenceCaptureStatus") == "PASS"
        and summary_document.get("status") == "PASS",
        f"CROSS_{matrix.upper()}_INVALID",
    )


def _validate_regression(
    document: Mapping[str, Any],
    *,
    subject: str,
    role: str,
    correctness_fd: int,
    snapshots: dict[PurePosixPath, RawSnapshot],
    raw: dict[str, RawSnapshot],
) -> None:
    expected = {
        "production": {
            "project": PRODUCTION_PROJECT,
            "counts": (1344, 1344, 0, 0, 1344),
            "deselected": [],
            "replacements": [],
            "roles": ("ruff", "mypy", "pytest"),
            "labels": (
                "production-ruff",
                "production-mypy",
                "production-pytest",
            ),
        },
        "research": {
            "project": RESEARCH_PROJECT,
            "counts": (263, 262, 1, 2, 264),
            "deselected": [DESELECTED_RESEARCH_NODE],
            "replacements": list(REPLACEMENT_RESEARCH_NODES),
            "roles": (
                "ruff",
                "mypy",
                "replacement-pytest",
                "base-pytest",
            ),
            "labels": (
                "research-ruff",
                "research-mypy",
                "research-replacement-pytest",
                "research-base-pytest",
            ),
        },
    }.get(role)
    _require(expected is not None, "REGRESSION_ROLE_INVALID")
    if expected is None:
        raise CandidateRubricAuditError("REGRESSION_ROLE_INVALID")
    _exact_object(
        document,
        {
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
        },
        code=f"{role.upper()}_REGRESSION_INVALID",
    )
    counts = (
        document.get("collectedCount"),
        document.get("basePassedCount"),
        document.get("deselectedCount"),
        document.get("replacementPassedCount"),
        document.get("totalExecutedPassedCount"),
    )
    commands = document.get("commands")
    _require(
        document.get("schemaVersion") == "s1.4x-regression-compound-receipt-v1"
        and document.get("benchmarkSubjectCommit") == subject
        and document.get("project") == expected["project"]
        and counts == expected["counts"]
        and all(type(value) is int for value in counts)
        and document.get("deselectedNodeIds") == expected["deselected"]
        and document.get("replacementNodeIds") == expected["replacements"]
        and document.get("status") == "PASS"
        and isinstance(commands, list)
        and len(commands) == len(expected["roles"]),
        f"{role.upper()}_REGRESSION_INVALID",
    )
    if not isinstance(commands, list):
        raise CandidateRubricAuditError(f"{role.upper()}_REGRESSION_INVALID")
    command_fields = {
        "role",
        "exitCode",
        "stdoutPath",
        "stdoutSha256",
        "stderrPath",
        "stderrSha256",
        "status",
    }
    for command, command_role, label in zip(
        commands,
        expected["roles"],
        expected["labels"],
        strict=True,
    ):
        _exact_object(
            command,
            command_fields,
            code=f"{role.upper()}_REGRESSION_INVALID",
        )
        _require(
            isinstance(command, dict)
            and command.get("role") == command_role
            and _is_exact_int(command.get("exitCode"), 0)
            and command.get("stdoutPath") == f"regression/logs/{label}.stdout"
            and command.get("stderrPath") == f"regression/logs/{label}.stderr"
            and _is_sha256(command.get("stdoutSha256"))
            and _is_sha256(command.get("stderrSha256"))
            and command.get("status") == "PASS",
            f"{role.upper()}_REGRESSION_INVALID",
        )
        for stream in ("stdout", "stderr"):
            relative = _portable_relative(
                str(command[f"{stream}Path"]),
                label=f"{role.upper()}_REGRESSION",
            )
            snapshot = _read_raw_snapshot(
                correctness_fd,
                relative,
                label=f"{role.upper()}_REGRESSION_{stream.upper()}",
            )
            _require(
                snapshot.sha256 == command[f"{stream}Sha256"],
                f"{role.upper()}_REGRESSION_LOG_INVALID",
            )
            snapshots[relative] = snapshot
            raw[f"{role}-regression-{label}-{stream}"] = snapshot


def _validate_scala_semantic_receipt(
    document: Mapping[str, Any],
    *,
    policy: RepositoryBlob,
    manifest: RepositoryBlob,
    sources: Mapping[str, RepositoryBlob],
) -> None:
    _exact_object(
        document,
        {
            "schemaVersion",
            "policySha256",
            "sourceInputManifestSha256",
            "checkedFiles",
            "sourceTreeSha256",
            "checkerMode",
            "semanticSmokeStatus",
            "semanticdb",
            "scalafix",
            "rule",
            "execution",
            "negativeMatrix",
            "status",
        },
        code="SCALA_SOURCE_POLICY_INVALID",
    )
    source_tree = [
        {"path": path, "sha256": sources[path].sha256}
        for path in sorted(sources, key=lambda value: value.encode("utf-8"))
    ]
    _require(
        document.get("schemaVersion")
        == "s1.4x-scala-semantic-policy-receipt-v1"
        and document.get("policySha256") == policy.sha256
        and document.get("sourceInputManifestSha256") == manifest.sha256
        and document.get("checkedFiles") == list(sources)
        and document.get("sourceTreeSha256")
        == _canonical_value_sha256(source_tree)
        and document.get("checkerMode") == "semanticdb"
        and document.get("semanticSmokeStatus") == "PASS"
        and isinstance(document.get("semanticdb"), dict)
        and isinstance(document.get("scalafix"), dict)
        and isinstance(document.get("rule"), dict)
        and isinstance(document.get("execution"), dict)
        and isinstance(document.get("negativeMatrix"), list)
        and bool(document["negativeMatrix"])
        and document.get("status") == "PASS",
        "SCALA_SOURCE_POLICY_INVALID",
    )


def _validate_scala_policy(
    document: Mapping[str, Any],
    *,
    snapshot: RawSnapshot,
    semantic_snapshot: RawSnapshot,
    policy: RepositoryBlob,
    manifest: RepositoryBlob,
    sources: Mapping[str, RepositoryBlob],
    correctness_fd: int,
    snapshots: dict[PurePosixPath, RawSnapshot],
    raw: dict[str, RawSnapshot],
) -> None:
    base_fields = {
        "schemaVersion",
        "policySha256",
        "sourceInputManifestSha256",
        "semanticReceiptSha256",
        "checkerMode",
        "semanticSmokeStatus",
        "checkedFiles",
        "violations",
        "usedAllowlistEntries",
        "staleAllowlistEntries",
        "sourceSetExact",
        "aggregateStatus",
    }
    _exact_object(
        document,
        base_fields | {"coreResultSha256", "process"},
        code="SCALA_SOURCE_POLICY_INVALID",
    )
    process = _exact_object(
        document.get("process"),
        {
            "portableArgv",
            "portableArgvSha256",
            "runtimeArgvSha256",
            "exitCode",
            "stdoutSha256",
            "stderrSha256",
            "status",
        },
        code="SCALA_SOURCE_POLICY_INVALID",
    )
    _require(
        document.get("schemaVersion") == "s1.4x-scala-source-policy-result-v1"
        and document.get("policySha256") == policy.sha256
        and document.get("sourceInputManifestSha256") == manifest.sha256
        and document.get("semanticReceiptSha256") == semantic_snapshot.sha256
        and document.get("checkerMode") == "semanticdb"
        and document.get("semanticSmokeStatus") == "PASS"
        and document.get("sourceSetExact") is True
        and document.get("violations") == []
        and document.get("usedAllowlistEntries") == []
        and document.get("staleAllowlistEntries") == []
        and document.get("checkedFiles") == list(sources)
        and _is_sha256(document.get("coreResultSha256"))
        and isinstance(process.get("portableArgv"), list)
        and bool(process["portableArgv"])
        and process.get("portableArgvSha256")
        == _canonical_value_sha256(process["portableArgv"])
        and _is_sha256(process.get("runtimeArgvSha256"))
        and _is_exact_int(process.get("exitCode"), 0)
        and _is_sha256(process.get("stdoutSha256"))
        and _is_sha256(process.get("stderrSha256"))
        and process.get("status") == "PASS"
        and document.get("aggregateStatus") == "PASS",
        "SCALA_SOURCE_POLICY_INVALID",
    )
    dynamic = {
        "core": PurePosixPath(f"{snapshot.relative_path.as_posix()}.core"),
        "stdout": PurePosixPath(f"{snapshot.relative_path.as_posix()}.stdout"),
        "stderr": PurePosixPath(f"{snapshot.relative_path.as_posix()}.stderr"),
    }
    loaded: dict[str, RawSnapshot] = {}
    for name, relative in dynamic.items():
        evidence = _read_raw_snapshot(
            correctness_fd,
            relative,
            label=f"SCALA_SOURCE_POLICY_{name.upper()}",
        )
        snapshots[relative] = evidence
        raw[f"scala-policy-{name}"] = evidence
        loaded[name] = evidence
    core = _strict_json(loaded["core"].payload, label="SCALA_SOURCE_POLICY_CORE")
    _require(
        core == {field: document[field] for field in base_fields}
        and document["coreResultSha256"] == loaded["core"].sha256
        and process["stdoutSha256"] == loaded["stdout"].sha256
        and process["stderrSha256"] == loaded["stderr"].sha256,
        "SCALA_SOURCE_POLICY_BINDING_INVALID",
    )


def _validate_scala_dependency(
    document: Mapping[str, Any],
    *,
    policy: RepositoryBlob,
    manifest: RepositoryBlob,
    project: RepositoryBlob,
) -> None:
    _exact_object(
        document,
        {
            "schemaVersion",
            "policySha256",
            "sourceInputManifestSha256",
            "projectSha256",
            "dependencies",
            "forbiddenSourceFindings",
            "candidateAuthoredEdgeCount",
            "candidateAddedNativeDependencyCount",
            "candidateCoreDirectNativeBindingImportCount",
            "candidateCoreDirectNativeBindingCallCount",
            "timedKernelExplicitCandidateNativeInteropCallCount",
            "unknownEdgeCount",
            "aggregateStatus",
        },
        code="SCALA_DEPENDENCY_INVALID",
    )
    zero_fields = (
        "candidateAddedNativeDependencyCount",
        "candidateAuthoredEdgeCount",
        "candidateCoreDirectNativeBindingCallCount",
        "candidateCoreDirectNativeBindingImportCount",
        "timedKernelExplicitCandidateNativeInteropCallCount",
        "unknownEdgeCount",
    )
    _require(
        document.get("schemaVersion") == "s1.4x-scala-dependency-native-edge-result-v1"
        and document.get("policySha256") == policy.sha256
        and document.get("sourceInputManifestSha256") == manifest.sha256
        and document.get("projectSha256") == project.sha256
        and isinstance(document.get("dependencies"), list)
        and bool(document["dependencies"])
        and all(
            isinstance(item, dict)
            and set(item) == {"coordinate", "coordinateSha256", "nativeInterop"}
            and isinstance(item.get("coordinate"), str)
            and item.get("coordinateSha256")
            == hashlib.sha256(item["coordinate"].encode("utf-8")).hexdigest()
            and item.get("nativeInterop") is False
            for item in document["dependencies"]
        )
        and all(document.get(field) == 0 for field in zero_fields)
        and document.get("forbiddenSourceFindings") == []
        and document.get("aggregateStatus") == "PASS",
        "SCALA_DEPENDENCY_INVALID",
    )


def _validate_scala_format(
    document: Mapping[str, Any],
    *,
    manifest: RepositoryBlob,
    sources: Mapping[str, RepositoryBlob],
    toolchain_lock: RepositoryBlob,
) -> None:
    _exact_object(
        document,
        {
            "schemaVersion",
            "scalafmtVersion",
            "scalafmtArtifact",
            "networkPolicy",
            "configPath",
            "configSha256",
            "sourceInputManifestSha256",
            "toolchainLockSha256",
            "checkedFiles",
            "sourceBeforeSha256",
            "firstPassSourceSha256",
            "secondPassSourceSha256",
            "formattedSourcePatchSha256",
            "firstApply",
            "secondApply",
            "copiedNonMutatingCheck",
            "nonMutatingCheck",
            "misformattedNegative",
            "status",
        },
        code="SCALA_FORMAT_INVALID",
    )
    copied = document.get("copiedNonMutatingCheck")
    _require(
        document.get("schemaVersion")
        == "s1.4x-scala-scalafmt-idempotence-result-v1"
        and document.get("scalafmtVersion") == "3.11.4"
        and document.get("networkPolicy") == "OFFLINE_PINNED_LAUNCHER"
        and document.get("sourceInputManifestSha256") == manifest.sha256
        and document.get("toolchainLockSha256") == toolchain_lock.sha256
        and document.get("checkedFiles") == list(sources)
        and _is_sha256(document.get("sourceBeforeSha256"))
        and document.get("sourceBeforeSha256")
        == document.get("firstPassSourceSha256")
        == document.get("secondPassSourceSha256")
        and isinstance(copied, dict)
        and _is_exact_int(copied.get("exitCode"), 0)
        and _is_exact_int(copied.get("downloadLineCount"), 0)
        and document.get("status") == "PASS",
        "SCALA_FORMAT_INVALID",
    )


def _geometric_mean(values: Sequence[float], *, code: str) -> float:
    _require(
        bool(values)
        and all(
            type(value) is float and math.isfinite(value) and value > 0.0
            for value in values
        ),
        code,
    )
    return math.exp(math.fsum(math.log(value) for value in values) / len(values))


def _scala_selector_config_sha256(
    *,
    policy: Mapping[str, Any],
    benchmark_plan_sha256: str,
    blocks: Sequence[Mapping[str, Any]],
) -> str:
    observed = []
    for block in blocks:
        profile_evidence = block.get("profileEvidence")
        measurements = block.get("measurements")
        _require(
            isinstance(profile_evidence, list)
            and isinstance(measurements, list),
            "SCALA_QUALIFICATION_INVALID",
        )
        if not isinstance(profile_evidence, list) or not isinstance(
            measurements,
            list,
        ):
            raise CandidateRubricAuditError("SCALA_QUALIFICATION_INVALID")
        observed.append(
            {
                "outerRepetition": block.get("outerRepetition"),
                "plannedProfileOrder": block.get("plannedProfileOrder"),
                "actualProfileOrder": block.get("actualProfileOrder"),
                "profileCaseOrder": [
                    {
                        "profileId": item.get("profileId"),
                        "plannedCaseOrder": item.get("plannedCaseOrder"),
                        "actualCaseOrder": item.get("actualCaseOrder"),
                        "caseCount": item.get("caseCount"),
                    }
                    for item in profile_evidence
                    if isinstance(item, dict)
                ],
                "measurementOrder": [
                    {
                        "profileId": item.get("profileId"),
                        "caseId": item.get("caseId"),
                        "rawNativeJsonSha256": item.get("rawNativeJsonSha256"),
                        "effectiveJvmArgsSha256": item.get(
                            "effectiveJvmArgsSha256"
                        ),
                        "jmhRunResultSha256": item.get("jmhRunResultSha256"),
                    }
                    for item in measurements
                    if isinstance(item, dict)
                ],
            }
        )
    return _canonical_value_sha256(
        {
            "benchmarkPlanSha256": benchmark_plan_sha256,
            "policy": policy,
            "observedLatinProfileCaseClosure": observed,
        }
    )


def _validate_scala_qualification_and_select(
    qualification: Mapping[str, Any],
    *,
    policy: Mapping[str, Any],
    benchmark_plan_sha256: str,
    profile_options: Mapping[str, Any],
    manifest_sha256: str,
    scala_cli_sha256: str,
) -> tuple[dict[str, dict[str, Any]], str]:
    case_order = policy.get("qualificationCaseOrder")
    profile_orders = policy.get("profileOrderBlocks")
    _require(
        case_order == list(PROPERTY_QUALIFICATION_CASES)
        and policy.get("qualificationCaseIds") == list(PROPERTY_QUALIFICATION_CASES)
        and profile_orders == [["A", "B", "C"], ["B", "C", "A"], ["C", "A", "B"]]
        and _is_exact_int(policy.get("outerQualificationRepetitions"), 3),
        "SCALA_QUALIFICATION_INVALID",
    )
    if not isinstance(profile_orders, list):
        raise CandidateRubricAuditError("SCALA_QUALIFICATION_INVALID")
    blocks = qualification.get("blocks")
    if not isinstance(blocks, list):
        raise CandidateRubricAuditError("SCALA_QUALIFICATION_INVALID")
    scores: dict[tuple[int, str, str], float] = {}
    all_effective: list[str] = []
    block_fields = {
        "outerRepetition",
        "plannedProfileOrder",
        "actualProfileOrder",
        "hostValiditySha256",
        "effectiveJvmArgsSha256",
        "profileEvidence",
        "measurements",
    }
    profile_fields = {
        "profileId",
        "plannedCaseOrder",
        "actualCaseOrder",
        "startedAt",
        "endedAt",
        "hostValiditySha256",
        "scalaCliBinarySha256",
        "profileOptionsSha256",
        "sourceInputManifestSha256",
        "effectiveJvmArgsSha256",
        "caseCount",
    }
    measurement_fields = {
        "profileId",
        "caseId",
        "scoreNsPerInvocation",
        "rawNativeJsonSha256",
        "effectiveJvmArgsSha256",
        "jmhRunResultSha256",
    }
    for repetition, (raw_block, order) in enumerate(
        zip(blocks, profile_orders, strict=True),
        start=1,
    ):
        block = _exact_object(
            raw_block,
            block_fields,
            code="SCALA_QUALIFICATION_INVALID",
        )
        profile_evidence = block.get("profileEvidence")
        measurements = block.get("measurements")
        _require(
            block.get("outerRepetition") == repetition
            and block.get("plannedProfileOrder") == order
            and block.get("actualProfileOrder") == order
            and isinstance(profile_evidence, list)
            and len(profile_evidence) == 3
            and isinstance(measurements, list)
            and len(measurements) == 21,
            "SCALA_QUALIFICATION_INVALID",
        )
        if not isinstance(profile_evidence, list) or not isinstance(measurements, list):
            raise CandidateRubricAuditError("SCALA_QUALIFICATION_INVALID")
        expected_pairs = [
            (profile_id, case_id)
            for profile_id in order
            for case_id in PROPERTY_QUALIFICATION_CASES
        ]
        observed_pairs: list[tuple[Any, Any]] = []
        by_profile_hashes: dict[str, list[str]] = {
            profile_id: [] for profile_id in order
        }
        for measurement in measurements:
            item = _exact_object(
                measurement,
                measurement_fields,
                code="SCALA_QUALIFICATION_INVALID",
            )
            profile_id = item.get("profileId")
            case_id = item.get("caseId")
            score = item.get("scoreNsPerInvocation")
            _require(
                profile_id in order
                and case_id in PROPERTY_QUALIFICATION_CASES
                and type(score) is float
                and math.isfinite(score)
                and score > 0.0
                and all(
                    _is_sha256(item.get(field))
                    for field in (
                        "rawNativeJsonSha256",
                        "effectiveJvmArgsSha256",
                        "jmhRunResultSha256",
                    )
                ),
                "SCALA_QUALIFICATION_INVALID",
            )
            if type(score) is not float:
                raise CandidateRubricAuditError("SCALA_QUALIFICATION_INVALID")
            observed_pairs.append((profile_id, case_id))
            scores[(repetition, str(profile_id), str(case_id))] = score
            by_profile_hashes[str(profile_id)].append(
                str(item["effectiveJvmArgsSha256"])
            )
            all_effective.append(str(item["effectiveJvmArgsSha256"]))
        _require(
            observed_pairs == expected_pairs,
            "SCALA_QUALIFICATION_INVALID",
        )
        for item, profile_id in zip(profile_evidence, order, strict=True):
            evidence = _exact_object(
                item,
                profile_fields,
                code="SCALA_QUALIFICATION_INVALID",
            )
            _require(
                evidence.get("profileId") == profile_id
                and evidence.get("plannedCaseOrder")
                == list(PROPERTY_QUALIFICATION_CASES)
                and evidence.get("actualCaseOrder")
                == list(PROPERTY_QUALIFICATION_CASES)
                and _is_exact_int(evidence.get("caseCount"), 7)
                and isinstance(evidence.get("startedAt"), str)
                and evidence["startedAt"].endswith("Z")
                and isinstance(evidence.get("endedAt"), str)
                and evidence["endedAt"].endswith("Z")
                and _is_sha256(evidence.get("hostValiditySha256"))
                and evidence.get("scalaCliBinarySha256") == scala_cli_sha256
                and evidence.get("profileOptionsSha256")
                == _canonical_value_sha256(profile_options[profile_id])
                and evidence.get("sourceInputManifestSha256") == manifest_sha256
                and evidence.get("effectiveJvmArgsSha256")
                == _canonical_value_sha256(by_profile_hashes[profile_id]),
                "SCALA_QUALIFICATION_INVALID",
            )
        _require(
            block.get("hostValiditySha256")
            == _canonical_value_sha256(
                [item["hostValiditySha256"] for item in profile_evidence]
            )
            and block.get("effectiveJvmArgsSha256")
            == _canonical_value_sha256(
                [item["effectiveJvmArgsSha256"] for item in profile_evidence]
            ),
            "SCALA_QUALIFICATION_INVALID",
        )
    _require(
        qualification.get("effectiveJvmArgsClosureSha256")
        == _canonical_value_sha256(all_effective)
        and qualification.get("profileOptionsSha256")
        == _canonical_value_sha256(profile_options)
        and qualification.get("selectorConfigSha256")
        == _scala_selector_config_sha256(
            policy=policy,
            benchmark_plan_sha256=benchmark_plan_sha256,
            blocks=blocks,
        ),
        "SCALA_QUALIFICATION_INVALID",
    )
    results: dict[str, dict[str, Any]] = {
        "A": {
            "aggregateRatioToA": 1.0,
            "maximumCaseRatio": 1.0,
            "improvingOuterRepetitions": 3,
            "caseMedianRatiosToA": {
                case_id: 1.0 for case_id in PROPERTY_QUALIFICATION_CASES
            },
            "outerAggregateRatiosToA": [1.0, 1.0, 1.0],
            "qualified": True,
        }
    }
    for profile_id in ("B", "C"):
        outer = [
            _geometric_mean(
                [
                    scores[(repetition, profile_id, case_id)]
                    / scores[(repetition, "A", case_id)]
                    for case_id in PROPERTY_QUALIFICATION_CASES
                ],
                code="SCALA_QUALIFICATION_INVALID",
            )
            for repetition in range(1, 4)
        ]
        case_ratios = {
            case_id: statistics.median(
                [
                    scores[(repetition, profile_id, case_id)]
                    for repetition in range(1, 4)
                ]
            )
            / statistics.median(
                [
                    scores[(repetition, "A", case_id)]
                    for repetition in range(1, 4)
                ]
            )
            for case_id in PROPERTY_QUALIFICATION_CASES
        }
        aggregate = _geometric_mean(
            list(case_ratios.values()),
            code="SCALA_QUALIFICATION_INVALID",
        )
        maximum = max(case_ratios.values())
        improving = sum(value < 1.0 for value in outer)
        results[profile_id] = {
            "aggregateRatioToA": aggregate,
            "maximumCaseRatio": maximum,
            "improvingOuterRepetitions": improving,
            "caseMedianRatiosToA": case_ratios,
            "outerAggregateRatiosToA": outer,
            "qualified": (
                maximum <= float(policy["perCaseMaxRegressionRatio"])
                and aggregate <= float(policy["aggregateMaxRatio"])
                and improving
                >= int(policy["minimumImprovingOuterRepetitions"])
            ),
        }
    c_over_b = (
        results["C"]["aggregateRatioToA"]
        / results["B"]["aggregateRatioToA"]
    )
    results["C"]["aggregateRatioToB"] = c_over_b
    if results["C"]["qualified"] and (
        not results["B"]["qualified"]
        or c_over_b <= 1.0 - float(policy["cOverBMinimumImprovement"])
    ):
        selected = "C"
    elif results["B"]["qualified"]:
        selected = "B"
    elif results["C"]["qualified"]:
        selected = "C"
    else:
        selected = "A"
    return results, selected


def _validate_scala_selected(
    selected: Mapping[str, Any],
    selected_snapshot: RawSnapshot,
    qualification: Mapping[str, Any],
    qualification_snapshot: RawSnapshot,
    correctness: Mapping[str, Any],
    correctness_snapshot: RawSnapshot,
    compiler: Mapping[str, Any],
    *,
    benchmark_plan: RepositoryBlob,
    benchmark_plan_document: Mapping[str, Any],
    compiler_profiles: RepositoryBlob,
    compiler_profiles_document: Mapping[str, Any],
    manifest: RepositoryBlob,
    manifest_document: Mapping[str, Any],
    selected_source: RepositoryBlob,
    toolchain_lock: RepositoryBlob,
    toolchain_document: Mapping[str, Any],
    candidate: RawSnapshot,
    matrix_artifacts: Mapping[str, RawSnapshot],
    property_plan: RepositoryBlob,
    property_seeds: RepositoryBlob,
    function_registry: RepositoryBlob,
    error_registry: RepositoryBlob,
) -> str:
    profile = selected.get("selectedProfileId")
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
    _exact_object(
        selected,
        selected_fields,
        code="SCALA_SELECTED_PROFILE_INVALID",
    )
    raw_profiles = compiler_profiles_document.get("profiles")
    _require(
        isinstance(raw_profiles, dict)
        and set(raw_profiles) == {"A", "B", "C"}
        and profile in raw_profiles
        and isinstance(raw_profiles[profile], dict),
        "SCALA_SELECTED_PROFILE_INVALID",
    )
    if not isinstance(raw_profiles, dict) or profile not in raw_profiles:
        raise CandidateRubricAuditError("SCALA_SELECTED_PROFILE_INVALID")
    options = raw_profiles[profile].get("additionalOptions")
    scala_cli = toolchain_document.get("scalaCli")
    if not isinstance(options, list) or not isinstance(scala_cli, dict):
        raise CandidateRubricAuditError("SCALA_SELECTED_PROFILE_INVALID")
    _require(
        _is_sha256(scala_cli.get("binarySha256")),
        "SCALA_SELECTED_PROFILE_INVALID",
    )
    _require(
        selected.get("schemaVersion") == "s1.4x-scala-selected-profile-result-v1"
        and selected.get("selectionStatus") == "PASS"
        and profile in {"A", "B", "C"}
        and selected.get("benchmarkPlanSha256") == benchmark_plan.sha256
        and selected.get("selectorConfigSha256")
        == qualification.get("selectorConfigSha256")
        and selected.get("qualificationSha256") == qualification_snapshot.sha256
        and selected.get("sourceInputManifestSha256") == manifest.sha256
        and selected.get("compilerProfilesSha256") == compiler_profiles.sha256
        and selected.get("toolchainLockSha256") == toolchain_lock.sha256
        and selected.get("mergedToolchainProvenanceSha256")
        == toolchain_document.get("mergedToolchainProvenanceSha256")
        and selected.get("scalaCliBinarySha256")
        == scala_cli.get("binarySha256")
        and selected.get("profileOptionsSha256")
        == qualification.get("profileOptionsSha256")
        and selected.get("selectedProfileSourceSha256")
        == selected_source.sha256
        and selected.get("selectedProfileOptions") == options
        and selected.get("selectedProfileOptionsSha256")
        == _canonical_value_sha256(options)
        and selected.get("correctnessResultSha256") == correctness_snapshot.sha256,
        "SCALA_SELECTED_PROFILE_INVALID",
    )
    _require(
        all(
            _is_sha256(selected.get(field))
            for field in (
                "javaExecutableSha256",
                "jvmArgumentAllowlistSha256",
                "effectiveJvmArgumentsCapabilitySha256",
            )
        )
        and selected.get("jvmArgumentAllowlistSha256")
        == selected.get("effectiveJvmArgumentsCapabilitySha256")
        and isinstance(selected.get("profiles"), dict)
        and set(selected["profiles"]) == {"A", "B", "C"}
        and selected.get("fallbackProfileId") == "A"
        and selected.get("fallbackExecuted") is (profile == "A"),
        "SCALA_SELECTED_PROFILE_INVALID",
    )
    qualification_fields = {
        "schemaVersion",
        "benchmarkPlanSha256",
        "selectorConfigSha256",
        "sourceInputManifestSha256",
        "profileOptionsSha256",
        "scalaCliBinarySha256",
        "jvmArgumentAllowlistSha256",
        "profileRunInputPaths",
        "effectiveJvmArgsClosureSha256",
        "blocks",
        "status",
    }
    expected_qualification_inputs = [
        path
        for path, entry in manifest_document["files"].items()
        if entry["role"] in {"configuration", "main", "benchmark"}
    ]
    _exact_object(
        qualification,
        qualification_fields,
        code="SCALA_QUALIFICATION_INVALID",
    )
    _require(
        qualification.get("schemaVersion")
        == "s1.4x-scala-profile-qualification-v1"
        and qualification.get("benchmarkPlanSha256") == benchmark_plan.sha256
        and qualification.get("sourceInputManifestSha256") == manifest.sha256
        and qualification.get("scalaCliBinarySha256")
        == scala_cli.get("binarySha256")
        and qualification.get("jvmArgumentAllowlistSha256")
        == selected.get("jvmArgumentAllowlistSha256")
        and qualification.get("profileRunInputPaths")
        == expected_qualification_inputs
        and all(
            _is_sha256(qualification.get(field))
            for field in (
                "selectorConfigSha256",
                "profileOptionsSha256",
                "effectiveJvmArgsClosureSha256",
            )
        )
        and isinstance(qualification.get("blocks"), list)
        and len(qualification["blocks"]) == 3
        and qualification.get("status") == "PASS",
        "SCALA_QUALIFICATION_INVALID",
    )
    qualification_policy = benchmark_plan_document.get(
        "scalaProfileQualification"
    )
    _require(
        isinstance(qualification_policy, dict),
        "SCALA_QUALIFICATION_INVALID",
    )
    if not isinstance(qualification_policy, dict):
        raise CandidateRubricAuditError("SCALA_QUALIFICATION_INVALID")
    computed_profiles, computed_profile = (
        _validate_scala_qualification_and_select(
            qualification,
            policy=qualification_policy,
            benchmark_plan_sha256=benchmark_plan.sha256,
            profile_options={
                candidate_profile: raw_profiles[candidate_profile][
                    "additionalOptions"
                ]
                for candidate_profile in ("A", "B", "C")
            },
            manifest_sha256=manifest.sha256,
            scala_cli_sha256=str(scala_cli["binarySha256"]),
        )
    )
    _require(
        selected.get("profiles") == computed_profiles
        and profile == computed_profile,
        "SCALA_SELECTED_PROFILE_INVALID",
    )
    correctness_fields = {
        "schemaVersion",
        "profileId",
        "compilerProfilesSha256",
        "profileOptions",
        "profileOptionsSha256",
        "sourceInputManifestSha256",
        "toolchainLockSha256",
        "scalaCliBinarySha256",
        "profileRunInputPaths",
        "candidateSha256",
        "matrix",
        "mismatchCount",
        "status",
    }
    _exact_object(
        correctness,
        correctness_fields,
        code="SCALA_SELECTED_PROFILE_INVALID",
    )
    expected_profile_inputs = [
        path
        for path, entry in manifest_document["files"].items()
        if entry["role"] != "benchmark"
    ]
    _require(
        correctness.get("schemaVersion") == "s1.4x-scala-profile-correctness-v1"
        and correctness.get("profileId") == profile
        and correctness.get("compilerProfilesSha256") == compiler_profiles.sha256
        and correctness.get("profileOptions") == options
        and correctness.get("profileOptionsSha256")
        == _canonical_value_sha256(options)
        and correctness.get("sourceInputManifestSha256") == manifest.sha256
        and correctness.get("toolchainLockSha256") == toolchain_lock.sha256
        and correctness.get("scalaCliBinarySha256")
        == scala_cli.get("binarySha256")
        and correctness.get("profileRunInputPaths") == expected_profile_inputs
        and correctness.get("candidateSha256") == candidate.sha256
        and correctness.get("mismatchCount") == 0
        and correctness.get("status") == "PASS",
        "SCALA_SELECTED_PROFILE_INVALID",
    )
    matrix = _exact_object(
        correctness.get("matrix"),
        {
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
        code="SCALA_SELECTED_PROFILE_INVALID",
    )
    dynamic_fields = {
        "candidateResultSha256": "canonical-result",
        "semanticResultSha256": "semantic-result",
        "unitTestResultSha256": "unit-result",
        "unitStdoutSha256": "unit-stdout",
        "unitStderrSha256": "unit-stderr",
        "canonicalComparisonSha256": "canonical-comparison",
        "semanticComparisonSha256": "semantic-comparison",
        "propertyReportSha256": "property-report",
        "registryReportSha256": "registry-report",
        "propertyExecutionEvidenceSha256": "property-execution",
    }
    _require(
        all(
            matrix.get(field) == matrix_artifacts[key].sha256
            for field, key in dynamic_fields.items()
        )
        and matrix.get("propertyPlanSha256") == property_plan.sha256
        and matrix.get("propertySeedCorpusSha256") == property_seeds.sha256
        and matrix.get("functionRegistrySha256") == function_registry.sha256
        and matrix.get("errorRegistrySha256") == error_registry.sha256,
        "SCALA_SELECTED_PROFILE_MATRIX_INVALID",
    )
    full_compile = compiler.get("fullCompile")
    positive = compiler.get("positiveFlags")
    negatives = compiler.get("negativeWarnings")
    _exact_object(
        compiler,
        {
            "schemaVersion",
            "profileId",
            "scalaVersion",
            "jdkRelease",
            "toolPathId",
            "resolvedBinarySha256",
            "toolchainLockSha256",
            "compilerProfilesSha256",
            "profileOptionsSha256",
            "sourceInputManifestSha256",
            "compileInputPaths",
            "positiveFlags",
            "negativeWarnings",
            "fullCompile",
            "diagnosticOnly",
            "aggregateStatus",
        },
        code="SCALA_SELECTED_HARD_COMPILER_INVALID",
    )
    _require(
        compiler.get("schemaVersion") == "s1.4x-scala-hard-compiler-result-v1"
        and compiler.get("profileId") == profile
        and compiler.get("scalaVersion") == "3.8.4"
        and compiler.get("jdkRelease") == "25"
        and compiler.get("toolPathId") == "SCALA_CLI_1_15_0"
        and compiler.get("resolvedBinarySha256")
        == scala_cli.get("binarySha256")
        and compiler.get("toolchainLockSha256") == toolchain_lock.sha256
        and compiler.get("compilerProfilesSha256") == compiler_profiles.sha256
        and compiler.get("profileOptionsSha256")
        == _canonical_value_sha256(options)
        and compiler.get("sourceInputManifestSha256") == manifest.sha256
        and compiler.get("aggregateStatus") == "PASS"
        and compiler.get("compileInputPaths") == expected_profile_inputs
        and isinstance(full_compile, dict)
        and _is_exact_int(full_compile.get("exitCode"), 0)
        and full_compile.get("status") == "PASS"
        and isinstance(positive, list)
        and any(
            isinstance(item, dict)
            and item.get("optionGroup") == ["-Werror"]
            and _is_exact_int(item.get("exitCode"), 0)
            and item.get("status") == "PASS"
            for item in positive
        )
        and isinstance(negatives, list)
        and len(negatives) >= 4
        and all(
            isinstance(item, dict)
            and _is_exact_int(item.get("exitCode"), 1)
            and item.get("status") == "PASS"
            for item in negatives
        )
        and isinstance(compiler.get("diagnosticOnly"), list),
        "SCALA_SELECTED_HARD_COMPILER_INVALID",
    )
    _require(
        selected_snapshot.relative_path
        == RAW_PATHS["scala-selected"],
        "SCALA_SELECTED_PROFILE_INVALID",
    )
    return str(profile)


def _validate_haskell_correctness(
    document: Mapping[str, Any],
    *,
    profile: str,
    subject: str,
    source_tree_sha256: str,
    artifacts: Mapping[str, RawSnapshot],
    stack_yaml: RepositoryBlob,
    canonical_inputs: RepositoryBlob,
    canonical_results: RepositoryBlob,
    semantic_inputs: RepositoryBlob,
    semantic_results: RepositoryBlob,
) -> str:
    profile_options = {
        "baseline-o0-fasm": ["-O0", "-fasm"],
        "optimized-o2-fasm": ["-O2", "-fasm"],
    }
    options = profile_options[profile]
    fields = {
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
    _exact_object(
        document,
        fields,
        code="HASKELL_CORRECTNESS_INVALID",
    )
    commands = document.get("commands")
    comparisons = document.get("comparisonArtifacts")
    _require(
        document.get("schemaVersion") == "s1.4x-haskell-full-correctness-v1"
        and document.get("status") == "PASS"
        and document.get("profileId") == profile
        and document.get("ghcOptions") == options
        and document.get("optionsSha256") == _canonical_value_sha256(options)
        and document.get("compilerVersion") == "9.10.3"
        and isinstance(document.get("compilerPath"), str)
        and bool(document["compilerPath"])
        and document.get("compilerSha256") == AUTHORITATIVE_GHC_SHA256
        and document.get("candidateSourceCommit") == subject
        and document.get("sourceTreeSha256") == source_tree_sha256
        and isinstance(document.get("candidateBinaryPath"), str)
        and bool(document["candidateBinaryPath"])
        and _is_sha256(document.get("candidateBinarySha256"))
        and isinstance(document.get("stackRootPath"), str)
        and bool(document["stackRootPath"])
        and isinstance(document.get("stackWorkDir"), str)
        and bool(document["stackWorkDir"])
        and isinstance(document.get("stackYamlPath"), str)
        and bool(document["stackYamlPath"])
        and document.get("stackYamlSha256") == stack_yaml.sha256
        and isinstance(commands, list)
        and len(commands) == 6
        and isinstance(comparisons, list)
        and len(comparisons) == 2
        and _is_exact_int(document.get("mismatchCount"), 0),
        "HASKELL_CORRECTNESS_INVALID",
    )
    if not isinstance(commands, list) or not isinstance(comparisons, list):
        raise CandidateRubricAuditError("HASKELL_CORRECTNESS_INVALID")
    phases = (
        "build",
        "test",
        "canonical-process",
        "canonical-compare",
        "semantic-process",
        "semantic-compare",
    )
    command_fields = {
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
    for command, phase in zip(commands, phases, strict=True):
        _exact_object(
            command,
            command_fields,
            code="HASKELL_CORRECTNESS_COMMAND_INVALID",
        )
        argv = command.get("argv")
        _require(
            command.get("phase") == phase
            and isinstance(argv, list)
            and bool(argv)
            and all(isinstance(item, str) and item for item in argv)
            and command.get("argvSha256") == _canonical_value_sha256(argv)
            and _is_exact_int(command.get("exitCode"), 0)
            and command.get("stdoutSha256")
            == artifacts[f"{phase}-stdout"].sha256
            and command.get("stderrSha256")
            == artifacts[f"{phase}-stderr"].sha256,
            "HASKELL_CORRECTNESS_COMMAND_INVALID",
        )
        if phase in {"build", "test"}:
            _require(
                any("--pedantic" in argument for argument in argv)
                and any(
                    argument == f"--ghc-options={' '.join(options)}"
                    for argument in argv
                ),
                "HASKELL_HARD_WARNING_INVALID",
            )
    frozen = {
        "canonical": (
            canonical_inputs.sha256,
            canonical_results.sha256,
            "s1.4x-canonical-small-v1",
        ),
        "semantic": (
            semantic_inputs.sha256,
            semantic_results.sha256,
            "s1.4x-semantic-errors-v1",
        ),
    }
    comparison_fields = {
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
    for comparison, matrix in zip(
        comparisons,
        ("canonical", "semantic"),
        strict=True,
    ):
        _exact_object(
            comparison,
            comparison_fields,
            code="HASKELL_CORRECTNESS_COMPARISON_INVALID",
        )
        request_sha, expected_sha, request_id = frozen[matrix]
        actual = _strict_json(
            artifacts[f"{matrix}-actual"].payload,
            label=f"HASKELL_{matrix.upper()}_ACTUAL",
        )
        compared = _strict_json(
            artifacts[f"{matrix}-comparison"].payload,
            label=f"HASKELL_{matrix.upper()}_COMPARISON",
        )
        _validate_comparison(compared, matrix=matrix)
        _require(
            comparison.get("matrixId") == matrix
            and comparison.get("requestSha256") == request_sha
            and comparison.get("expectedSha256") == expected_sha
            and comparison.get("actualSha256")
            == artifacts[f"{matrix}-actual"].sha256
            and comparison.get("comparisonSha256")
            == artifacts[f"{matrix}-comparison"].sha256
            and _is_exact_int(comparison.get("mismatchCount"), 0)
            and comparison.get("status") == "PASS"
            and actual.get("requestId") == request_id
            and actual.get("implementation") == "haskell-ghc-9.10.3",
            "HASKELL_CORRECTNESS_COMPARISON_INVALID",
        )
    return str(document["candidateBinarySha256"])


def _validate_haskell_selected(
    selected: Mapping[str, Any],
    *,
    correctness_documents: Mapping[str, Mapping[str, Any]],
    correctness_artifacts: Mapping[str, Mapping[str, RawSnapshot]],
    qualification: Mapping[str, Any],
    subject: str,
    repository: Path,
    selected_profile: RepositoryBlob,
    manifest: RepositoryBlob,
    manifest_sources: Mapping[str, RepositoryBlob],
    generated_cabal: RawSnapshot,
    benchmark_plan: RepositoryBlob,
    benchmark_plan_document: Mapping[str, Any],
    canonical_inputs: RepositoryBlob,
    canonical_results: RepositoryBlob,
    semantic_inputs: RepositoryBlob,
    semantic_results: RepositoryBlob,
) -> tuple[str, dict[str, str]]:
    profile = selected.get("profileId")
    profile_options = {
        "baseline-o0-fasm": ["-O0", "-fasm"],
        "optimized-o2-fasm": ["-O2", "-fasm"],
    }
    expected_fields = {
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
    options = profile_options.get(profile) if isinstance(profile, str) else None
    options_sha256 = (
        hashlib.sha256(
            json.dumps(
                options,
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        if options is not None
        else None
    )
    sha_fields = (
        "compilerSha256",
        "sourceTreeSha256",
        "optionsSha256",
        "fullCorrectnessSha256",
        "qualificationPlanSha256",
        "qualificationArtifactSha256",
        "selectorConfigSha256",
    )
    _require(
        set(selected) == expected_fields
        and selected.get("schemaVersion") == "s1.4x-haskell-selected-profile-v1"
        and options is not None
        and selected.get("ghcOptions") == options
        and selected.get("compilerVersion") == "9.10.3"
        and selected.get("compilerSha256") == AUTHORITATIVE_GHC_SHA256
        and selected.get("optionsSha256") == options_sha256
        and selected.get("sourceTreeSha256")
        == _haskell_source_tree_sha256(
            repository,
            subject,
            manifest_sources,
            generated_cabal,
        )
        and selected.get("qualificationPlanSha256") == benchmark_plan.sha256
        and all(
            isinstance(selected.get(field), str)
            and SHA256.fullmatch(selected[field]) is not None
            for field in sha_fields
        )
        and selected.get("selectedBy")
        in {"frozen-criterion-selector", "proven-fallback"}
        and selected.get("fallbackProfile") == "baseline-o0-fasm",
        "HASKELL_SELECTED_PROFILE_INVALID",
    )
    source_tree_sha256 = str(selected["sourceTreeSha256"])
    stack_yaml = _git_blob(repository, subject, S1 / "haskell/stack.yaml")
    candidate_binaries = {
        candidate_profile: _validate_haskell_correctness(
            correctness_documents[candidate_profile],
            profile=candidate_profile,
            subject=subject,
            source_tree_sha256=source_tree_sha256,
            artifacts=correctness_artifacts[candidate_profile],
            stack_yaml=stack_yaml,
            canonical_inputs=canonical_inputs,
            canonical_results=canonical_results,
            semantic_inputs=semantic_inputs,
            semantic_results=semantic_results,
        )
        for candidate_profile in profile_options
    }
    qualification_fields = {
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
    _exact_object(
        qualification,
        qualification_fields,
        code="HASKELL_QUALIFICATION_INVALID",
    )
    config = benchmark_plan_document.get("haskellProfileQualification")
    selection = _exact_object(
        qualification.get("selection"),
        {
            "profileId",
            "selectedBy",
            "pairedRatios",
            "perCaseMaxima",
            "aggregateRatio",
            "improvingOuterRepetitions",
        },
        code="HASKELL_QUALIFICATION_INVALID",
    )
    case_order = config.get("qualificationCaseOrder") if isinstance(config, dict) else None
    profile_orders = config.get("profileOrderBlocks") if isinstance(config, dict) else None
    _require(
        isinstance(config, dict)
        and bool(config)
        and case_order == list(PROPERTY_QUALIFICATION_CASES)
        and config.get("qualificationCaseIds")
        == list(PROPERTY_QUALIFICATION_CASES)
        and profile_orders
        == [
            ["baseline-o0-fasm", "optimized-o2-fasm"],
            ["optimized-o2-fasm", "baseline-o0-fasm"],
            ["optimized-o2-fasm", "baseline-o0-fasm"],
            ["baseline-o0-fasm", "optimized-o2-fasm"],
        ]
        and qualification.get("schemaVersion")
        == "s1.4x-haskell-profile-qualification-v1"
        and qualification.get("status") == "PASS"
        and qualification.get("candidateSourceCommit") == subject
        and qualification.get("planPathId") == "S1_4X_BENCHMARK_PLAN"
        and qualification.get("planSha256") == benchmark_plan.sha256
        and qualification.get("selectorConfigSha256")
        == _canonical_value_sha256(config)
        and qualification.get("selectorConfigSha256")
        == selected.get("selectorConfigSha256")
        and qualification.get("sourceTreeSha256") == source_tree_sha256
        and isinstance(qualification.get("blocks"), list)
        and len(qualification["blocks"]) == 4,
        "HASKELL_QUALIFICATION_INVALID",
    )
    blocks = qualification.get("blocks")
    if (
        not isinstance(config, dict)
        or not isinstance(profile_orders, list)
        or not isinstance(blocks, list)
    ):
        raise CandidateRubricAuditError("HASKELL_QUALIFICATION_INVALID")
    paired: list[float] = []
    per_case: dict[str, list[float]] = {
        case_id: [] for case_id in PROPERTY_QUALIFICATION_CASES
    }
    improving = 0
    profile_record_fields = {
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
    for index, (raw_block, order) in enumerate(
        zip(blocks, profile_orders, strict=True)
    ):
        block = _exact_object(
            raw_block,
            {
                "orderBlock",
                "plannedProfileOrder",
                "actualProfileOrder",
                "profiles",
                "ratios",
            },
            code="HASKELL_QUALIFICATION_INVALID",
        )
        profiles = block.get("profiles")
        ratios = block.get("ratios")
        _require(
            _is_exact_int(block.get("orderBlock"), index)
            and block.get("plannedProfileOrder") == order
            and block.get("actualProfileOrder") == order
            and isinstance(profiles, list)
            and len(profiles) == 2
            and isinstance(ratios, dict)
            and set(ratios) == set(PROPERTY_QUALIFICATION_CASES),
            "HASKELL_QUALIFICATION_INVALID",
        )
        if not isinstance(profiles, list) or not isinstance(ratios, dict):
            raise CandidateRubricAuditError("HASKELL_QUALIFICATION_INVALID")
        estimates: dict[str, Mapping[str, Any]] = {}
        for raw_profile, expected_profile in zip(profiles, order, strict=True):
            profile_record = _exact_object(
                raw_profile,
                profile_record_fields,
                code="HASKELL_QUALIFICATION_INVALID",
            )
            candidate_options = profile_options[expected_profile]
            case_estimates = profile_record.get("caseSecondsPerBatch")
            _require(
                profile_record.get("profileId") == expected_profile
                and profile_record.get("ghcOptions") == candidate_options
                and profile_record.get("optionsSha256")
                == _canonical_value_sha256(candidate_options)
                and isinstance(profile_record.get("startedAt"), str)
                and profile_record["startedAt"].endswith("Z")
                and isinstance(profile_record.get("finishedAt"), str)
                and profile_record["finishedAt"].endswith("Z")
                and all(
                    _is_sha256(profile_record.get(field))
                    for field in (
                        "hostValiditySha256",
                        "hostDockerRouteBeforeSha256",
                        "hostDockerRouteAfterSha256",
                        "rawCriterionSha256",
                    )
                )
                and isinstance(profile_record.get("hostCommand"), dict)
                and isinstance(profile_record.get("criterionCommand"), dict)
                and isinstance(profile_record.get("marker"), dict)
                and isinstance(case_estimates, dict)
                and set(case_estimates) == set(PROPERTY_QUALIFICATION_CASES)
                and all(
                    type(value) is float
                    and math.isfinite(value)
                    and value > 0.0
                    for value in case_estimates.values()
                ),
                "HASKELL_QUALIFICATION_INVALID",
            )
            if isinstance(case_estimates, dict):
                estimates[expected_profile] = case_estimates
        block_ratios: list[float] = []
        for case_id in PROPERTY_QUALIFICATION_CASES:
            expected_ratio = (
                estimates["optimized-o2-fasm"][case_id]
                / estimates["baseline-o0-fasm"][case_id]
            )
            ratio = ratios.get(case_id)
            _require(
                type(ratio) is float
                and math.isfinite(ratio)
                and ratio > 0.0
                and ratio == expected_ratio,
                "HASKELL_QUALIFICATION_INVALID",
            )
            if type(ratio) is not float:
                raise CandidateRubricAuditError("HASKELL_QUALIFICATION_INVALID")
            paired.append(ratio)
            block_ratios.append(ratio)
            per_case[case_id].append(ratio)
        if (
            _geometric_mean(
                block_ratios,
                code="HASKELL_QUALIFICATION_INVALID",
            )
            < 1.0
        ):
            improving += 1
    maxima = {case_id: max(values) for case_id, values in per_case.items()}
    aggregate = _geometric_mean(
        paired,
        code="HASKELL_QUALIFICATION_INVALID",
    )
    optimized = (
        all(
            value <= float(config["perCaseMaxRegressionRatio"])
            for value in maxima.values()
        )
        and aggregate <= float(config["aggregateMaxRatio"])
        and improving >= int(config["minimumImprovingOuterRepetitions"])
    )
    computed_selection = {
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
    _require(
        selection == computed_selection
        and selection.get("profileId") == profile
        and selection.get("selectedBy") == selected.get("selectedBy"),
        "HASKELL_QUALIFICATION_INVALID",
    )
    _require(
        manifest.path == HASKELL_SOURCE_INPUTS
        and selected_profile.path == HASKELL_SELECTED,
        "HASKELL_SELECTED_PROFILE_INVALID",
    )
    parent_result = _git(repository, "rev-parse", f"{subject}^")
    if parent_result.returncode == 0:
        parent = parent_result.stdout.decode("ascii", errors="strict").strip()
        changed_result = _git(
            repository,
            "diff-tree",
            "--no-commit-id",
            "--name-only",
            "-r",
            "-z",
            parent,
            subject,
        )
        if changed_result.returncode != 0 or not changed_result.stdout.endswith(b"\0"):
            raise CandidateRubricAuditError("HASKELL_MATERIALIZATION_INVALID")
        changed = {
            value.decode("utf-8", errors="strict")
            for value in changed_result.stdout.split(b"\0")
            if value
        }
        materialized_paths = {
            HASKELL_SELECTED.as_posix(),
            HASKELL_SOURCE_INPUTS.as_posix(),
        }
        if changed == materialized_paths:
            pending = _strict_json(
                _git_blob(repository, parent, HASKELL_SELECTED).payload,
                label="HASKELL_PENDING_PROFILE",
            )
            _require(
                pending.get("schemaVersion")
                == "s1.4x-haskell-selected-profile-pending-v1"
                and selected_profile.payload
                == _git_blob(repository, subject, HASKELL_SELECTED).payload
                and manifest.payload
                == _git_blob(repository, subject, HASKELL_SOURCE_INPUTS).payload,
                "HASKELL_MATERIALIZATION_INVALID",
            )
    return str(profile), candidate_binaries


def _validate_haskell_safety(
    document: Mapping[str, Any],
    *,
    policy: RepositoryBlob,
    manifest: RepositoryBlob,
    sources: Mapping[str, RepositoryBlob],
) -> None:
    _exact_object(
        document,
        {
            "schemaVersion",
            "policySha256",
            "sourceInputManifestSha256",
            "modules",
            "candidateDirectImports",
            "candidateHomeModuleEdges",
            "upstreamTransitiveEdges",
            "unclassifiedModuleCount",
            "candidateTrustworthyUnsafeDeclarationCount",
            "candidateDirectUnsafeIoForeignImportCount",
            "coreToShellEdgeCount",
            "unknownTransitiveEdgeCount",
            "staleAllowlistCount",
            "aggregateStatus",
        },
        code="HASKELL_MODULE_SAFETY_INVALID",
    )
    zero_fields = (
        "unclassifiedModuleCount",
        "candidateTrustworthyUnsafeDeclarationCount",
        "candidateDirectUnsafeIoForeignImportCount",
        "coreToShellEdgeCount",
        "unknownTransitiveEdgeCount",
        "staleAllowlistCount",
    )
    modules = document.get("modules")
    upstream = document.get("upstreamTransitiveEdges")
    _require(
        document.get("schemaVersion") == "s1.4x-haskell-module-safety-result-v1"
        and document.get("policySha256") == policy.sha256
        and document.get("sourceInputManifestSha256") == manifest.sha256
        and all(document.get(field) == 0 for field in zero_fields)
        and isinstance(modules, list)
        and bool(modules)
        and isinstance(document.get("candidateDirectImports"), list)
        and isinstance(document.get("candidateHomeModuleEdges"), list)
        and isinstance(upstream, list)
        and bool(upstream)
        and all(
            isinstance(edge, dict) and edge.get("allowlisted") is True
            for edge in upstream
        )
        and document.get("aggregateStatus") == "PASS",
        "HASKELL_MODULE_SAFETY_INVALID",
    )
    if not isinstance(modules, list):
        raise CandidateRubricAuditError("HASKELL_MODULE_SAFETY_INVALID")
    module_paths: set[str] = set()
    for raw_module in modules:
        module = _exact_object(
            raw_module,
            {
                "moduleName",
                "path",
                "category",
                "compileMode",
                "extensions",
                "sourceSha256",
            },
            code="HASKELL_MODULE_SAFETY_INVALID",
        )
        path = module.get("path")
        _require(
            isinstance(path, str)
            and path in sources
            and path.endswith(".hs")
            and module.get("sourceSha256") == sources[path].sha256
            and module.get("category")
            in {"safe-scalar", "audited-pure-vector", "io-shell", "test", "benchmark"}
            and isinstance(module.get("extensions"), list)
            and path not in module_paths,
            "HASKELL_MODULE_SAFETY_INVALID",
        )
        if not isinstance(path, str):
            raise CandidateRubricAuditError("HASKELL_MODULE_SAFETY_INVALID")
        module_paths.add(path)
    _require(
        module_paths == {path for path in sources if path.endswith(".hs")},
        "HASKELL_MODULE_SAFETY_CLOSURE_INVALID",
    )


def _validate_log_map(
    logs: Any,
    *,
    root: PurePosixPath,
    label: str,
    correctness_fd: int,
    snapshots: dict[PurePosixPath, RawSnapshot],
    raw: dict[str, RawSnapshot],
) -> None:
    _require(isinstance(logs, dict) and bool(logs), f"{label}_INVALID")
    if not isinstance(logs, dict):
        raise CandidateRubricAuditError(f"{label}_INVALID")
    for name in sorted(logs, key=lambda value: value.encode("utf-8")):
        _require(
            isinstance(name, str)
            and "/" not in name
            and name not in {"", ".", "..", "receipt.json"}
            and _is_sha256(logs[name]),
            f"{label}_INVALID",
        )
        snapshot = _raw_snapshot(
            correctness_fd,
            root / name,
            label=f"{label}_LOG",
            key=f"{label.lower()}-log-{name}",
            snapshots=snapshots,
            raw=raw,
        )
        _require(snapshot.sha256 == logs[name], f"{label}_LOG_INVALID")


def _validate_haskell_lint(
    document: Mapping[str, Any],
    *,
    manifest: RepositoryBlob,
    manifest_document: Mapping[str, Any],
    correctness_fd: int,
    snapshots: dict[PurePosixPath, RawSnapshot],
    raw: dict[str, RawSnapshot],
) -> None:
    fields = {
        "schemaVersion",
        "hlintPathId",
        "hlintPath",
        "hlintSha256",
        "hlintVersion",
        "configurationSha256",
        "sourceInputManifestSha256",
        "sourceInputCanonicalManifestSha256",
        "sourceInputFileCount",
        "sourceInputPathsSha256",
        "exceptionManifestSha256",
        "exceptionSchemaSha256",
        "fixtureManifestSha256",
        "positiveArgv",
        "ignoredInventoryArgv",
        "ignoredInventoryExitCode",
        "negativeFixtureCount",
        "logs",
        "status",
    }
    _exact_object(document, fields, code="HASKELL_LINT_INVALID")
    files = manifest_document["files"]
    paths_sha256 = hashlib.sha256(
        "".join(f"{path}\n" for path in files).encode("utf-8")
    ).hexdigest()
    _require(
        document.get("schemaVersion") == "s1.4x-haskell-hlint-evidence-v1"
        and document.get("hlintPathId") == "HLINT_3_10"
        and document.get("hlintVersion") == "3.10"
        and _is_sha256(document.get("hlintSha256"))
        and _is_sha256(document.get("configurationSha256"))
        and document.get("sourceInputManifestSha256") == manifest.sha256
        and document.get("sourceInputCanonicalManifestSha256")
        == manifest_document["canonicalManifestSha256"]
        and type(document.get("sourceInputFileCount")) is int
        and document["sourceInputFileCount"] == len(files)
        and document.get("sourceInputPathsSha256") == paths_sha256
        and _is_sha256(document.get("exceptionManifestSha256"))
        and _is_sha256(document.get("exceptionSchemaSha256"))
        and _is_sha256(document.get("fixtureManifestSha256"))
        and isinstance(document.get("positiveArgv"), list)
        and bool(document["positiveArgv"])
        and isinstance(document.get("ignoredInventoryArgv"), list)
        and bool(document["ignoredInventoryArgv"])
        and type(document.get("ignoredInventoryExitCode")) is int
        and type(document.get("negativeFixtureCount")) is int
        and document["negativeFixtureCount"] > 0
        and document.get("status") == "PASS",
        "HASKELL_LINT_INVALID",
    )
    _validate_log_map(
        document.get("logs"),
        root=PurePosixPath("haskell/hlint"),
        label="HASKELL_LINT",
        correctness_fd=correctness_fd,
        snapshots=snapshots,
        raw=raw,
    )


def _validate_haskell_format(
    document: Mapping[str, Any],
    *,
    manifest: RepositoryBlob,
    manifest_document: Mapping[str, Any],
    correctness_fd: int,
    snapshots: dict[PurePosixPath, RawSnapshot],
    raw: dict[str, RawSnapshot],
) -> None:
    fields = {
        "schemaVersion",
        "formatterPathId",
        "formatterPath",
        "formatterSha256",
        "formatterVersion",
        "mandatedConfigurationPath",
        "mandatedConfigurationSha256",
        "derivedConfigurationPath",
        "derivedConfigurationSha256",
        "fallbackContractPath",
        "fallbackContractSha256",
        "parserCapabilityReceiptSha256",
        "parserCapabilityStatus",
        "sourceInputManifestSha256",
        "sourceInputCanonicalManifestSha256Before",
        "sourceInputCanonicalManifestSha256After",
        "sourceInputFileCount",
        "sourceInputPathsSha256",
        "positiveArgv",
        "positiveExitCode",
        "negativeArgv",
        "negativeFixturePath",
        "negativeFixtureSha256Before",
        "negativeFixtureSha256After",
        "misformattedExitCode",
        "sourceInputNegativeTests",
        "logs",
        "fallbackLimitation",
        "status",
    }
    _exact_object(document, fields, code="HASKELL_FORMAT_INVALID")
    files = manifest_document["files"]
    paths_sha256 = hashlib.sha256(
        "".join(f"{path}\n" for path in files).encode("utf-8")
    ).hexdigest()
    canonical_manifest = manifest_document["canonicalManifestSha256"]
    _require(
        document.get("schemaVersion") == "s1.4x-haskell-format-evidence-v1"
        and document.get("formatterPathId") == "STYLISH_HASKELL_0_15_1_0"
        and document.get("formatterVersion") == "0.15.1.0"
        and _is_sha256(document.get("formatterSha256"))
        and _is_sha256(document.get("mandatedConfigurationSha256"))
        and _is_sha256(document.get("derivedConfigurationSha256"))
        and _is_sha256(document.get("fallbackContractSha256"))
        and _is_sha256(document.get("parserCapabilityReceiptSha256"))
        and document.get("parserCapabilityStatus")
        == "PINNED_PARSER_COMPATIBILITY_FALLBACK"
        and document.get("sourceInputManifestSha256") == manifest.sha256
        and document.get("sourceInputCanonicalManifestSha256Before")
        == canonical_manifest
        and document.get("sourceInputCanonicalManifestSha256After")
        == canonical_manifest
        and _is_exact_int(document.get("sourceInputFileCount"), len(files))
        and document.get("sourceInputPathsSha256") == paths_sha256
        and isinstance(document.get("positiveArgv"), list)
        and bool(document["positiveArgv"])
        and _is_exact_int(document.get("positiveExitCode"), 0)
        and isinstance(document.get("negativeArgv"), list)
        and bool(document["negativeArgv"])
        and document.get("negativeFixturePath")
        == "tools/fixtures/stylish/misformatted.hs"
        and _is_sha256(document.get("negativeFixtureSha256Before"))
        and _is_sha256(document.get("negativeFixtureSha256After"))
        and document.get("negativeFixtureSha256Before")
        == document.get("negativeFixtureSha256After")
        and _is_exact_int(document.get("misformattedExitCode"), 1)
        and document.get("sourceInputNegativeTests")
        == [
            "untracked-rogue-source",
            "stale-manifest-entry",
            "intermediate-directory-symlink",
        ]
        and isinstance(document.get("fallbackLimitation"), str)
        and bool(document["fallbackLimitation"])
        and document.get("status") == "PASS",
        "HASKELL_FORMAT_INVALID",
    )
    _validate_log_map(
        document.get("logs"),
        root=PurePosixPath("haskell/format"),
        label="HASKELL_FORMAT",
        correctness_fd=correctness_fd,
        snapshots=snapshots,
        raw=raw,
    )


def _validate_scala_oci(
    build: Mapping[str, Any],
    runtime: Mapping[str, Any],
    *,
    build_snapshot: RawSnapshot,
    candidate: RawSnapshot,
    containerfile: RepositoryBlob,
    binding_before: RawSnapshot,
    binding_after: RawSnapshot,
    binding: Mapping[str, Any],
    artifacts: Mapping[str, RawSnapshot],
) -> None:
    base = build.get("baseImageReference")
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
    _exact_object(build, build_fields, code="SCALA_OCI_INVALID")
    docker_identity = _exact_object(
        build.get("dockerIdentity"),
        {
            "dockerCliPathId",
            "dockerCliSha256",
            "contextName",
            "daemonId",
            "serverVersion",
            "operatingSystem",
            "architecture",
        },
        code="SCALA_OCI_INVALID",
    )
    expected_labels = {
        "org.opencontainers.image.s1-4x.candidate-sha256": candidate.sha256,
        "org.opencontainers.image.s1-4x.base-reference": SCALA_BASE_IMAGE,
        "org.opencontainers.image.s1-4x.base-image-id": build.get("baseImageId"),
        "org.opencontainers.image.s1-4x.containerfile-sha256": containerfile.sha256,
        "org.opencontainers.image.s1-4x.fixture-tree-sha256": build.get(
            "fixtureTreeSha256"
        ),
    }
    _require(
        build.get("schemaVersion") == "s1.4x-scala-oci-build-result-v2"
        and build.get("baseImageReferenceSource") == "caller-digest-argument"
        and build.get("buildNetwork") == "none"
        and build.get("pull") is False
        and build.get("buildUsedIidfile") is True
        and base == SCALA_BASE_IMAGE
        and re.fullmatch(r"sha256:[0-9a-f]{64}", str(build.get("baseImageId")))
        is not None
        and build.get("candidateSha256") == candidate.sha256
        and build.get("containerfileSha256") == containerfile.sha256
        and _is_sha256(build.get("fixtureTreeSha256"))
        and re.fullmatch(r"sha256:[0-9a-f]{64}", str(build.get("imageId")))
        is not None
        and isinstance(build.get("localTag"), str)
        and bool(build["localTag"])
        and _is_sha256(docker_identity.get("dockerCliSha256"))
        and build.get("inspectedLabels") == expected_labels
        and build.get("aggregateStatus") == "PASS",
        "SCALA_OCI_INVALID",
    )
    binding_fields = {
        "schemaVersion",
        "imageId",
        "buildReceiptSha256",
        "candidateSha256",
        "baseImageReference",
        "baseImageId",
        "dockerIdentity",
        "status",
    }
    _exact_object(binding, binding_fields, code="SCALA_OCI_INVALID")
    _require(
        binding_before.payload == binding_after.payload
        and binding.get("schemaVersion") == "s1.4x-scala-oci-runtime-binding-v1"
        and binding.get("imageId") == build.get("imageId")
        and binding.get("buildReceiptSha256") == build_snapshot.sha256
        and binding.get("candidateSha256") == candidate.sha256
        and binding.get("baseImageReference") == SCALA_BASE_IMAGE
        and binding.get("baseImageId") == build.get("baseImageId")
        and binding.get("dockerIdentity") == docker_identity
        and binding.get("status") == "PASS",
        "SCALA_OCI_INVALID",
    )
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
    _exact_object(runtime, runtime_fields, code="SCALA_OCI_INVALID")
    _require(
        runtime.get("schemaVersion") == "s1.4x-scala-oci-correctness-result-v2"
        and runtime.get("imageId") == build.get("imageId")
        and runtime.get("buildReceiptSha256") == build_snapshot.sha256
        and runtime.get("candidateSha256") == candidate.sha256
        and runtime.get("baseImageReference") == SCALA_BASE_IMAGE
        and runtime.get("baseImageId") == build.get("baseImageId")
        and runtime.get("dockerIdentity") == docker_identity
        and runtime.get("dockerIdentitySha256")
        == _canonical_value_sha256(docker_identity)
        and runtime.get("runtimeNetwork") == "none"
        and runtime.get("readOnlyRoot") is True
        and runtime.get("capabilitiesDropped") == "ALL"
        and runtime.get("sourceTreeMounted") is False
        and runtime.get("userHomeMounted") is False
        and runtime.get("credentialMounted") is False
        and runtime.get("canonicalResultSha256")
        == artifacts["canonical-result"].sha256
        and runtime.get("semanticResultSha256")
        == artifacts["semantic-result"].sha256
        and runtime.get("canonicalComparisonSha256")
        == artifacts["canonical-comparison"].sha256
        and runtime.get("semanticComparisonSha256")
        == artifacts["semantic-comparison"].sha256
        and runtime.get("runtimeBindingSha256") == binding_before.sha256
        and runtime.get("mismatchCount") == 0
        and runtime.get("aggregateStatus") == "PASS",
        "SCALA_OCI_INVALID",
    )
    for matrix, request_id in (
        ("canonical", "s1.4x-canonical-small-v1"),
        ("semantic", "s1.4x-semantic-errors-v1"),
    ):
        actual = _strict_json(
            artifacts[f"{matrix}-result"].payload,
            label=f"SCALA_OCI_{matrix.upper()}_RESULT",
        )
        comparison = _strict_json(
            artifacts[f"{matrix}-comparison"].payload,
            label=f"SCALA_OCI_{matrix.upper()}_COMPARISON",
        )
        _validate_comparison(comparison, matrix=matrix)
        _require(
            actual.get("requestId") == request_id
            and actual.get("implementation") == "scala-3.8.4-jvm25",
            "SCALA_OCI_INVALID",
        )


def _validate_haskell_oci(
    document: Mapping[str, Any],
    *,
    subject: str,
    selected_profile: RepositoryBlob,
    selected_document: Mapping[str, Any],
    profile: str,
    source_tree_sha256: str,
    candidate_binary_sha256: str,
    containerfile: RepositoryBlob,
    artifacts: Mapping[str, RawSnapshot],
) -> None:
    comparisons = document.get("comparisons")
    commands = document.get("commands")
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
    _exact_object(document, expected_fields, code="HASKELL_OCI_INVALID")
    context = _exact_object(
        document.get("contextSnapshot"),
        {"binarySha256", "containerfileSha256", "fixtureTreeSha256"},
        code="HASKELL_OCI_INVALID",
    )
    daemon_before = document.get("daemonIdentityBefore")
    daemon_after = document.get("daemonIdentityAfter")
    _require(
        document.get("schemaVersion") == "s1.4x-haskell-oci-correctness-v1"
        and document.get("candidateSourceCommit") == subject
        and document.get("sourceTreeSha256") == source_tree_sha256
        and document.get("selectedProfileSha256") == selected_profile.sha256
        and document.get("profileId") == profile
        and document.get("ghcOptions") == selected_document.get("ghcOptions")
        and document.get("optionsSha256") == selected_document.get("optionsSha256")
        and document.get("containerfileSha256") == containerfile.sha256
        and document.get("baseImage") == HASKELL_BASE_IMAGE
        and re.fullmatch(
            r"sha256:[0-9a-f]{64}",
            str(document.get("baseImageId")),
        )
        is not None
        and document.get("baseInspectionBeforeSha256")
        == artifacts["oci-base-before-stdout"].sha256
        and document.get("baseInspectionAfterSha256")
        == artifacts["oci-base-after-stdout"].sha256
        and context.get("binarySha256") == candidate_binary_sha256
        and context.get("containerfileSha256") == containerfile.sha256
        and _is_sha256(context.get("fixtureTreeSha256"))
        and document.get("fixtureTreeSha256")
        == context.get("fixtureTreeSha256")
        and document.get("candidateBinarySha256") == candidate_binary_sha256
        and isinstance(document.get("dockerPathId"), str)
        and bool(document["dockerPathId"])
        and _is_sha256(document.get("dockerSha256"))
        and document.get("dockerSha256")
        == document.get("expectedDockerSha256")
        and isinstance(document.get("dockerTrustBaseline"), dict)
        and isinstance(document.get("dockerTrustStageSnapshots"), list)
        and bool(document["dockerTrustStageSnapshots"])
        and isinstance(daemon_before, dict)
        and daemon_before == daemon_after
        and document.get("daemonIdentitySha256")
        == _canonical_value_sha256(daemon_before)
        and isinstance(document.get("dockerContextName"), str)
        and bool(document["dockerContextName"])
        and isinstance(document.get("imageTag"), str)
        and bool(document["imageTag"])
        and re.fullmatch(
            r"sha256:[0-9a-f]{64}",
            str(document.get("imageId")),
        )
        is not None
        and _is_sha256(document.get("iidFileSha256"))
        and document.get("provenanceLabels")
        == {
            "io.s1-4x.base-image-id": document.get("baseImageId"),
            "io.s1-4x.containerfile-sha256": containerfile.sha256,
            "io.s1-4x.fixture-tree-sha256": document.get("fixtureTreeSha256"),
        }
        and document.get("platform") == "linux/amd64"
        and document.get("runtimeImageSubject")
        == {
            "referenceType": "immutable-image-id",
            "imageId": document.get("imageId"),
        }
        and isinstance(document.get("imageTagBindingChecks"), list)
        and len(document["imageTagBindingChecks"]) == 3
        and document.get("buildNetwork") == "none"
        and document.get("runtimeNetwork") == "none"
        and document.get("runtimeMounts") == ["output-only"]
        and document.get("mismatchCount") == 0
        and isinstance(comparisons, list)
        and len(comparisons) == 2
        and isinstance(commands, list)
        and len(commands) == 16
        and document.get("status") == "PASS",
        "HASKELL_OCI_INVALID",
    )
    if not isinstance(comparisons, list) or not isinstance(commands, list):
        raise CandidateRubricAuditError("HASKELL_OCI_INVALID")
    expected_phases = (
        "oci-stack-build",
        "oci-context-before",
        "oci-daemon-before",
        "oci-base-before",
        "oci-image-build",
        "oci-image-id-inspect",
        "oci-image-inspect",
        "oci-canonical-run",
        "oci-canonical-tag-check",
        "oci-canonical-compare",
        "oci-semantic-run",
        "oci-semantic-tag-check",
        "oci-semantic-compare",
        "oci-base-after",
        "oci-context-after",
        "oci-daemon-after",
    )
    command_fields = {
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
    for command, phase in zip(commands, expected_phases, strict=True):
        _exact_object(
            command,
            command_fields,
            code="HASKELL_OCI_COMMAND_INVALID",
        )
        argv = command.get("argv")
        _require(
            command.get("phase") == phase
            and isinstance(argv, list)
            and bool(argv)
            and all(isinstance(item, str) and item for item in argv)
            and command.get("argvSha256") == _canonical_value_sha256(argv)
            and _is_exact_int(command.get("exitCode"), 0)
            and command.get("stdoutSha256")
            == artifacts[f"{phase}-stdout"].sha256
            and command.get("stderrSha256")
            == artifacts[f"{phase}-stderr"].sha256,
            "HASKELL_OCI_COMMAND_INVALID",
        )
        if not isinstance(argv, list):
            raise CandidateRubricAuditError("HASKELL_OCI_COMMAND_INVALID")
        if phase == "oci-image-build":
            _require(
                len(argv) >= 2
                and argv[0] == document.get("dockerPath")
                and argv[1] == "build"
                and any(
                    argv[index : index + 2] == ["--network", "none"]
                    for index in range(len(argv) - 1)
                )
                and "--pull=false" in argv
                and "--iidfile" in argv
                and "--tag" in argv,
                "HASKELL_OCI_COMMAND_INVALID",
            )
        if phase in {"oci-canonical-run", "oci-semantic-run"}:
            mounts = [
                argv[index + 1]
                for index, value in enumerate(argv[:-1])
                if value == "--mount"
            ]
            _require(
                len(argv) >= 2
                and argv[0] == document.get("dockerPath")
                and argv[1] == "run"
                and any(
                    argv[index : index + 2] == ["--network", "none"]
                    for index in range(len(argv) - 1)
                )
                and "--read-only" in argv
                and "--cap-drop=ALL" in argv
                and "--security-opt=no-new-privileges" in argv
                and bool(mounts)
                and len(mounts) == 1
                and mounts[0].startswith("type=bind,")
                and mounts[0].endswith(",dst=/out")
                and document.get("imageId") in argv
                and document.get("imageTag") not in argv,
                "HASKELL_OCI_COMMAND_INVALID",
            )
    expected_tag_phases = (
        "oci-image-inspect",
        "oci-canonical-tag-check",
        "oci-semantic-tag-check",
    )
    for check, phase in zip(
        document["imageTagBindingChecks"],
        expected_tag_phases,
        strict=True,
    ):
        _exact_object(
            check,
            {"phase", "imageTag", "imageId", "inspectionSha256", "status"},
            code="HASKELL_OCI_INVALID",
        )
        _require(
            check.get("phase") == phase
            and check.get("imageTag") == document.get("imageTag")
            and check.get("imageId") == document.get("imageId")
            and check.get("inspectionSha256")
            == artifacts[f"{phase}-stdout"].sha256
            and check.get("status") == "PASS",
            "HASKELL_OCI_INVALID",
        )
    for entry, matrix in zip(
        comparisons,
        ("canonical", "semantic"),
        strict=True,
    ):
        _require(
            isinstance(entry, dict)
            and set(entry)
            == {
                "matrixId",
                "actualSha256",
                "comparisonSha256",
                "mismatchCount",
                "status",
            }
            and entry.get("matrixId") == matrix
            and entry.get("actualSha256")
            == artifacts[f"{matrix}-actual"].sha256
            and entry.get("comparisonSha256")
            == artifacts[f"{matrix}-comparison"].sha256
            and entry.get("mismatchCount") == 0
            and entry.get("status") == "PASS",
            "HASKELL_OCI_INVALID",
        )
        actual = _strict_json(
            artifacts[f"{matrix}-actual"].payload,
            label=f"HASKELL_OCI_{matrix.upper()}_ACTUAL",
        )
        comparison = _strict_json(
            artifacts[f"{matrix}-comparison"].payload,
            label=f"HASKELL_OCI_{matrix.upper()}_COMPARISON",
        )
        _validate_comparison(comparison, matrix=matrix)
        _require(
            actual.get("requestId")
            == {
                "canonical": "s1.4x-canonical-small-v1",
                "semantic": "s1.4x-semantic-errors-v1",
            }[matrix]
            and actual.get("implementation") == "haskell-ghc-9.10.3",
            "HASKELL_OCI_INVALID",
        )


def _audit_scala_cohesion(blobs: Sequence[RepositoryBlob]) -> None:
    core_count = 0
    shell_count = 0
    source_root = S1 / "scala/src/main/scala"
    for blob in blobs:
        text = _decode_blob(blob)
        try:
            relative = blob.path.relative_to(source_root)
        except ValueError as exc:
            raise CandidateRubricAuditError("SCALA_MODULE_COHESION_INVALID") from exc
        expected_package = ".".join(relative.parts[:-1])
        package_match = re.search(
            r"(?m)^package\s+([A-Za-z_][A-Za-z0-9_.]*)\s*$",
            text,
        )
        _require(
            package_match is not None and package_match.group(1) == expected_package,
            f"SCALA_MODULE_COHESION_INVALID:{blob.path}",
        )
        if SCALA_CORE in blob.path.parents:
            core_count += 1
            _require(
                re.search(
                    r"\bai\.trading\.coach\.s14x\.shell\b",
                    text,
                )
                is None,
                f"SCALA_CORE_TO_SHELL_EDGE:{blob.path}",
            )
        elif SCALA_SHELL in blob.path.parents:
            shell_count += 1
        else:
            raise CandidateRubricAuditError(
                f"SCALA_MODULE_COHESION_INVALID:{blob.path}"
            )
    _require(
        core_count >= 2 and shell_count >= 1,
        "SCALA_MODULE_COHESION_INVALID",
    )


def _audit_haskell_cohesion(blobs: Sequence[RepositoryBlob]) -> None:
    core_count = 0
    contract_count = 0
    roots = (
        (HASKELL_CORE.parent.parent, "core"),
        (HASKELL_CONTRACT.parent.parent, "contract"),
    )
    for blob in blobs:
        text = _decode_blob(blob)
        matched_root: tuple[PurePosixPath, str] | None = None
        for root, kind in roots:
            if root in blob.path.parents:
                matched_root = (root, kind)
                break
        if matched_root is None:
            raise CandidateRubricAuditError(
                f"HASKELL_MODULE_COHESION_INVALID:{blob.path}"
            )
        root, kind = matched_root
        relative = blob.path.relative_to(root)
        expected_module = ".".join(relative.with_suffix("").parts)
        module_match = re.search(
            r"(?m)^module\s+([A-Z][A-Za-z0-9_.]*)\b",
            text,
        )
        _require(
            module_match is not None and module_match.group(1) == expected_module,
            f"HASKELL_MODULE_COHESION_INVALID:{blob.path}",
        )
        if kind == "core":
            core_count += 1
            _require(
                re.search(
                    r"(?m)^import(?:\s+qualified)?\s+S14X\.Contract\b",
                    text,
                )
                is None,
                f"HASKELL_CORE_TO_SHELL_EDGE:{blob.path}",
            )
        else:
            contract_count += 1
    _require(
        core_count >= 2 and contract_count >= 1,
        "HASKELL_MODULE_COHESION_INVALID",
    )


def _audit_core_side_effect_surface(
    blobs: Sequence[RepositoryBlob],
    *,
    language: str,
) -> None:
    """producer receipt와 별개로 core source의 명백한 effect escape를 막는다."""

    patterns = {
        "scala": re.compile(
            r"\b(?:print|println)\s*\("
            r"|\b(?:System\.(?:out|err)|Console)\b"
            r"|\b(?:java|scala)\.(?:io|net)\b"
            r"|\b(?:ProcessBuilder|Runtime\.getRuntime)\b"
        ),
        "haskell": re.compile(
            r"\b(?:unsafePerformIO|unsafeDupablePerformIO)\b"
            r"|^\s*(?:import\s+)?(?:System\.IO|Debug\.Trace)\b"
            r"|^\s*foreign\s+import\b"
            r"|^\s*\{-#\s*LANGUAGE\s+(?:Trustworthy|Unsafe)\s*#-\}",
            flags=re.MULTILINE,
        ),
    }
    for blob in blobs:
        text = _decode_blob(blob)
        if language == "scala":
            text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
            text = "\n".join(line.split("//", 1)[0] for line in text.splitlines())
        else:
            text = "\n".join(line.split("--", 1)[0] for line in text.splitlines())
        _require(
            patterns[language].search(text) is None,
            f"{language.upper()}_SOURCE_SIDE_EFFECT:{blob.path}",
        )


def _normalized_lines(blob: RepositoryBlob, *, language: str) -> list[str]:
    text = _decode_blob(blob)
    if language == "scala":
        text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
        lines = [line.split("//", 1)[0] for line in text.splitlines()]
        prefixes = ("package ", "import ")
        ignored = {"{", "}", "end"}
    else:
        lines = [line.split("--", 1)[0] for line in text.splitlines()]
        prefixes = ("module ", "import ")
        ignored = {"where", "{", "}"}
    normalized: list[str] = []
    for raw_line in lines:
        line = " ".join(raw_line.strip().split())
        if (
            not line
            or line in ignored
            or any(line.startswith(prefix) for prefix in prefixes)
        ):
            continue
        normalized.append(line)
    return normalized


def _audit_duplicate_blocks(
    blobs: Sequence[RepositoryBlob],
    *,
    language: str,
) -> None:
    inventory: dict[tuple[str, ...], tuple[PurePosixPath, int]] = {}
    for blob in blobs:
        raw_lines = _decode_blob(blob).splitlines()
        callables: list[tuple[int, list[str]]] = []
        if language == "scala":
            definitions = [
                index
                for index, line in enumerate(raw_lines)
                if re.match(
                    r"^\s*(?:(?:private|protected)\s+)?def\s+"
                    r"[A-Za-z_][A-Za-z0-9_]*",
                    line,
                )
            ]
            for position, start in enumerate(definitions):
                indentation = len(raw_lines[start]) - len(raw_lines[start].lstrip())
                end = (
                    definitions[position + 1]
                    if position + 1 < len(definitions)
                    else len(raw_lines)
                )
                for index in range(start + 1, end):
                    line = raw_lines[index]
                    if (
                        line.strip()
                        and len(line) - len(line.lstrip()) <= indentation
                        and re.match(
                            r"^\s*(?:object|class|trait|enum)\b",
                            line,
                        )
                    ):
                        end = index
                        break
                callables.append((start, raw_lines[start:end]))
        else:
            starts = [
                index
                for index, line in enumerate(raw_lines)
                if re.match(r"^[a-z][A-Za-z0-9_']*(?:\s+[^:]*)?=", line)
            ]
            for position, start in enumerate(starts):
                end = starts[position + 1] if position + 1 < len(starts) else len(raw_lines)
                for index in range(start + 1, end):
                    if (
                        raw_lines[index].strip()
                        and not raw_lines[index][0].isspace()
                        and re.match(
                            r"^(?:data|newtype|type|class|instance|module|import)\b",
                            raw_lines[index],
                        )
                    ):
                        end = index
                        break
                callables.append((start, raw_lines[start:end]))
        for start, callable_lines in callables:
            normalized = [
                " ".join(line.strip().split())
                for line in callable_lines
                if line.strip()
                and not line.lstrip().startswith(("//", "--"))
            ]
            if len(normalized) < 7:
                continue
            if language == "scala":
                normalized[0] = re.sub(
                    r"^((?:(?:private|protected)\s+)?def)\s+"
                    r"[A-Za-z_][A-Za-z0-9_]*",
                    r"\1 <callable>",
                    normalized[0],
                )
            else:
                normalized[0] = re.sub(
                    r"^[a-z][A-Za-z0-9_']*",
                    "<callable>",
                    normalized[0],
                )
            block = tuple(normalized)
            prior = inventory.get(block)
            if prior is not None:
                raise CandidateRubricAuditError(
                    "DUPLICATE_PRODUCTION_BLOCK:"
                    f"{prior[0]}:{prior[1] + 1}:{blob.path}:{start + 1}"
                )
            inventory[block] = (blob.path, start)


def _scala_doc_precedes(lines: Sequence[str], definition_index: int) -> bool:
    cursor = definition_index - 1
    while cursor >= 0 and not lines[cursor].strip():
        cursor -= 1
    if cursor < 0 or "*/" not in lines[cursor]:
        return False
    while cursor >= 0:
        if "/**" in lines[cursor]:
            return True
        if "/*" in lines[cursor] and "/**" not in lines[cursor]:
            return False
        cursor -= 1
    return False


def _audit_scala_public_docs(core_blobs: Sequence[RepositoryBlob]) -> None:
    count = 0
    public_def = re.compile(
        r"^  (?!(?:private|protected)\b)(?:override\s+)?def\s+"
        r"([A-Za-z_][A-Za-z0-9_]*)\b"
    )
    for blob in core_blobs:
        lines = _decode_blob(blob).splitlines()
        for index, line in enumerate(lines):
            match = public_def.match(line)
            if match is None:
                continue
            count += 1
            _require(
                _scala_doc_precedes(lines, index),
                "UNDOCUMENTED_PUBLIC_BOUNDARY:"
                f"{blob.path}:{index + 1}:{match.group(1)}",
            )
    _require(count > 0, "UNDOCUMENTED_PUBLIC_BOUNDARY:NO_SCALA_BOUNDARIES")


def _haskell_exports(text: str, *, path: PurePosixPath) -> tuple[str, ...]:
    header = re.search(
        r"\bmodule\s+[A-Z][A-Za-z0-9_.]*\s*\((.*?)\)\s*where\b",
        text,
        flags=re.DOTALL,
    )
    _require(header is not None, f"HASKELL_EXPORT_LIST_INVALID:{path}")
    if header is None:
        raise CandidateRubricAuditError(f"HASKELL_EXPORT_LIST_INVALID:{path}")
    exports: list[str] = []
    for raw_item in header.group(1).split(","):
        item = " ".join(raw_item.split())
        match = re.fullmatch(r"([a-z_][A-Za-z0-9_']*)", item)
        if match is not None:
            exports.append(match.group(1))
    return tuple(exports)


def _haskell_doc_precedes(lines: Sequence[str], signature_index: int) -> bool:
    cursor = signature_index - 1
    while cursor >= 0 and not lines[cursor].strip():
        cursor -= 1
    found_haddock = False
    while cursor >= 0 and lines[cursor].lstrip().startswith("--"):
        if lines[cursor].lstrip().startswith("-- |"):
            found_haddock = True
        cursor -= 1
    return found_haddock


def _audit_haskell_public_docs(core_blobs: Sequence[RepositoryBlob]) -> None:
    count = 0
    for blob in core_blobs:
        text = _decode_blob(blob)
        lines = text.splitlines()
        for exported in _haskell_exports(text, path=blob.path):
            signature = re.compile(rf"^{re.escape(exported)}\s*::")
            indexes = [
                index for index, line in enumerate(lines) if signature.match(line)
            ]
            _require(
                len(indexes) == 1,
                f"HASKELL_PUBLIC_SIGNATURE_INVALID:{blob.path}:{exported}",
            )
            count += 1
            _require(
                _haskell_doc_precedes(lines, indexes[0]),
                f"UNDOCUMENTED_PUBLIC_BOUNDARY:{blob.path}:{indexes[0] + 1}:{exported}",
            )
    _require(count > 0, "UNDOCUMENTED_PUBLIC_BOUNDARY:NO_HASKELL_BOUNDARIES")


def _audit_scala_tests(test_blobs: Sequence[RepositoryBlob]) -> None:
    names: list[str] = []
    kinds: set[str] = set()
    paths = {blob.path.as_posix() for blob in test_blobs}
    for blob in test_blobs:
        text = _decode_blob(blob)
        _require(
            re.search(
                r"\bassert\s*\(\s*true\s*\)"
                r"|\bassertEquals\s*\(\s*true\s*,\s*true\s*\)",
                text,
                flags=re.IGNORECASE,
            )
            is None,
            "TEST_STRUCTURE_WEAK:scala",
        )
        for match in re.finditer(
            r"\b(test|property)\s*\(\s*\"([^\"\r\n]+)\"",
            text,
        ):
            kinds.add(match.group(1))
            names.append(match.group(2))
    _require(
        len(names) >= 3
        and len(names) == len(set(names))
        and all(len(name.strip()) >= 8 for name in names)
        and kinds == {"test", "property"}
        and any("/core/" in path for path in paths)
        and any("/shell/" in path for path in paths),
        "TEST_STRUCTURE_WEAK:scala",
    )


def _audit_haskell_tests(test_blobs: Sequence[RepositoryBlob]) -> None:
    names: list[str] = []
    kinds: set[str] = set()
    paths = {blob.path.name for blob in test_blobs}
    property_registry_names: list[str] = []
    property_comprehension_present = False
    for blob in test_blobs:
        text = _decode_blob(blob)
        _require(
            re.search(
                r"\bassertBool\s+\"[^\"]*\"\s+True\b"
                r"|\btestCase\s+\"[^\"]+\"\s*(?:\\?->\s*)?"
                r"(?:\(\s*)?(?:pure|return)\s*\(\s*\)"
                r"|^\s*(?:testCase\s+\"[^\"]+\"\s+\$?\s*)?"
                r"(?:pure|return)\s*\(\s*\)\s*$",
                text,
                flags=re.MULTILINE,
            )
            is None,
            "TEST_STRUCTURE_WEAK:haskell",
        )
        for match in re.finditer(
            r"\b(testCase|testProperty)\s+\"([^\"\r\n]+)\"",
            text,
        ):
            kinds.add(match.group(1))
            names.append(match.group(2))
        if blob.path.name == "PropertyCases.hs":
            property_registry_names.extend(
                match.group(1)
                for match in re.finditer(
                    r"\bPropertyCase\s+\"([^\"\r\n]+)\"",
                    text,
                )
            )
        if blob.path.name == "PropertySpec.hs":
            property_comprehension_present = (
                re.search(
                    r"\[\s*testProperty\s+propertyId\s+invariant\s*\|\s*"
                    r"PropertyCase\s+propertyId\s+invariant\s*<-\s*"
                    r"propertyCases\s*\]",
                    text,
                    flags=re.DOTALL,
                )
                is not None
            )
    if property_registry_names or property_comprehension_present:
        _require(
            property_comprehension_present
            and len(property_registry_names) >= 1
            and len(property_registry_names) == len(set(property_registry_names))
            and all(len(name.strip()) >= 8 for name in property_registry_names),
            "TEST_STRUCTURE_WEAK:haskell",
        )
        kinds.add("testProperty")
        names.extend(property_registry_names)
    _require(
        len(names) >= 3
        and len(names) == len(set(names))
        and all(len(name.strip()) >= 8 for name in names)
        and kinds == {"testCase", "testProperty"}
        and {"CoreSpec.hs", "ContractSpec.hs", "PropertySpec.hs"}.issubset(paths),
        "TEST_STRUCTURE_WEAK:haskell",
    )


def _audit_workflows(
    correctness: RepositoryBlob,
    benchmark: RepositoryBlob,
) -> None:
    correctness_text = _decode_blob(correctness)
    benchmark_text = _decode_blob(benchmark)

    def without_comment(line: str) -> str:
        quoted: str | None = None
        escaped = False
        result: list[str] = []
        for character in line:
            if escaped:
                result.append(character)
                escaped = False
                continue
            if character == "\\" and quoted == '"':
                result.append(character)
                escaped = True
                continue
            if character in {"'", '"'}:
                if quoted is None:
                    quoted = character
                elif quoted == character:
                    quoted = None
                result.append(character)
                continue
            if character == "#" and quoted is None:
                break
            result.append(character)
        return "".join(result).rstrip()

    def trigger_blocks(text: str) -> tuple[set[str], dict[str, list[str]]]:
        lines = text.splitlines()
        on_index: int | None = None
        for index, raw_line in enumerate(lines):
            line = without_comment(raw_line)
            if re.fullmatch(r"on:\s*", line):
                on_index = index
                break
        _require(on_index is not None, "CI_TRIGGER_STRUCTURE_INVALID")
        events: set[str] = set()
        blocks: dict[str, list[str]] = {}
        current: str | None = None
        for raw_line in lines[(on_index or 0) + 1 :]:
            line = without_comment(raw_line)
            if not line.strip():
                continue
            indentation = len(line) - len(line.lstrip(" "))
            if indentation == 0:
                break
            event_match = re.fullmatch(
                r" {2}([A-Za-z_][A-Za-z0-9_-]*):\s*",
                line,
            )
            if event_match is not None:
                current = event_match.group(1)
                events.add(current)
                blocks[current] = []
            elif current is not None:
                blocks[current].append(line)
        return events, blocks

    correctness_events, correctness_blocks = trigger_blocks(correctness_text)
    benchmark_events, benchmark_blocks = trigger_blocks(benchmark_text)

    def nested_sequence(block: Sequence[str], key: str) -> list[str] | None:
        key_index: int | None = None
        key_indent = 0
        for index, line in enumerate(block):
            match = re.fullmatch(
                rf"( +){re.escape(key)}:\s*",
                without_comment(line),
            )
            if match is not None:
                if key_index is not None:
                    return None
                key_index = index
                key_indent = len(match.group(1))
        if key_index is None:
            return None
        values: list[str] = []
        for raw_line in block[key_index + 1 :]:
            line = without_comment(raw_line)
            if not line.strip():
                continue
            indentation = len(line) - len(line.lstrip(" "))
            if indentation <= key_indent:
                break
            item = re.fullmatch(r" +-\s*[\"']?([^\"']+?)[\"']?\s*", line)
            if item is None:
                return None
            values.append(item.group(1))
        return values

    pull_paths = nested_sequence(
        correctness_blocks.get("pull_request", []),
        "paths",
    )
    push_paths = nested_sequence(correctness_blocks.get("push", []), "paths")
    push_branches = nested_sequence(
        correctness_blocks.get("push", []),
        "branches",
    )
    clean_correctness = "\n".join(
        without_comment(line) for line in correctness_text.splitlines()
    )
    clean_benchmark = "\n".join(
        without_comment(line) for line in benchmark_text.splitlines()
    )
    _require(
        correctness_events == {"pull_request", "push"}
        and pull_paths == list(CORRECTNESS_TRIGGER_PATHS)
        and push_paths == list(CORRECTNESS_TRIGGER_PATHS)
        and push_branches == ["main"]
        and "contents: read" in clean_correctness
        and "cancel-in-progress: true" in clean_correctness
        and "schedule" not in correctness_events
        and "regression_gate.py" in clean_correctness,
        "CI_CORRECTNESS_STRUCTURE_INVALID",
    )
    dispatch_block = benchmark_blocks.get("workflow_dispatch", [])
    matrix_options = nested_sequence(dispatch_block, "options")
    _require(
        benchmark_events == {"workflow_dispatch"}
        and matrix_options == ["smallest", "full"]
        and "contents: read" in clean_benchmark
        and "correctness-before-timing" in clean_benchmark
        and SCALA_BASE_IMAGE in clean_benchmark
        and HASKELL_BASE_IMAGE in clean_benchmark
        and "schedule" not in benchmark_events,
        "CI_BENCHMARK_STRUCTURE_INVALID",
    )
    timeouts = [
        int(value)
        for value in re.findall(
            r"(?m)^\s+timeout-minutes:\s*([0-9]+)\s*$",
            correctness_text + "\n" + benchmark_text,
        )
    ]
    _require(
        bool(timeouts) and all(1 <= value <= 360 for value in timeouts),
        "CI_TIMEOUT_STRUCTURE_INVALID",
    )


def _audit_research_boundary(blob: RepositoryBlob) -> None:
    text = _decode_blob(blob)
    _require(
        all(node.rsplit("::", 1)[1] in text for node in REPLACEMENT_RESEARCH_NODES)
        and '["/usr/bin/git", "diff", "--name-only", "origin/main"]' in text
        and 'S1_4X_ROOT = "workspaces/decision-platform/research/s1-4x-numeric-parity/"'
        in text
        and ".github/workflows/s1-4x-numeric-parity-correctness.yml" in text
        and ".github/workflows/s1-4x-numeric-parity-benchmark.yml" in text
        and "assert unexpected == []" in text
        and "from regression_gate import" in text
        and "PRODUCER_DESELECTED_RESEARCH_NODE == S1_4R_BRANCH_SCOPE_NODE"
        in text
        and "aggregate_source.count" in text
        and "S1_4R_EXECUTION_BOUNDARY=oci" in text
        and "not in aggregate_source" in text,
        "RESEARCH_BOUNDARY_TEST_INVALID",
    )


def _repo_entries(
    blobs: Iterable[RepositoryBlob],
) -> list[dict[str, str]]:
    indexed: dict[PurePosixPath, RepositoryBlob] = {}
    for blob in blobs:
        prior = indexed.get(blob.path)
        if prior is not None and prior.sha256 != blob.sha256:
            raise CandidateRubricAuditError("REPOSITORY_BLOB_CONFLICT")
        indexed[blob.path] = blob
    return [
        {"path": path.as_posix(), "blobSha256": indexed[path].sha256}
        for path in sorted(indexed, key=lambda item: item.as_posix().encode())
    ]


def _raw_entries(
    snapshots: Iterable[RawSnapshot],
) -> list[dict[str, str]]:
    indexed: dict[PurePosixPath, RawSnapshot] = {}
    for snapshot in snapshots:
        prior = indexed.get(snapshot.relative_path)
        if prior is not None and prior.sha256 != snapshot.sha256:
            raise CandidateRubricAuditError("RAW_EVIDENCE_CONFLICT")
        indexed[snapshot.relative_path] = snapshot
    return [
        {"path": path.as_posix(), "sha256": indexed[path].sha256}
        for path in sorted(indexed, key=lambda item: item.as_posix().encode())
    ]


def _rubric_entry(
    rubric_id: str,
    objective_checks: Sequence[str],
    raw: Sequence[RawSnapshot],
    repository: Sequence[RepositoryBlob],
) -> dict[str, Any]:
    _require(
        rubric_id in RUBRIC_IDS
        and bool(objective_checks)
        and len(objective_checks) == len(set(objective_checks))
        and all(
            isinstance(check, str)
            and re.fullmatch(r"[a-z0-9][a-z0-9.-]+", check) is not None
            for check in objective_checks
        )
        and bool(raw)
        and bool(repository),
        f"RUBRIC_ASSESSMENT_INVALID:{rubric_id}",
    )
    return {
        "rubricId": rubric_id,
        "objectiveChecks": list(objective_checks),
        "reviewedArtifacts": _raw_entries(raw),
        "repositoryArtifacts": _repo_entries(repository),
        "findings": [],
        "status": "PASS",
    }


def _build_candidate_summary(
    *,
    subject: str,
    candidate: str,
    raw: Mapping[str, RawSnapshot],
    production: Sequence[RepositoryBlob],
    core: Sequence[RepositoryBlob],
    tests: Sequence[RepositoryBlob],
    policy: RepositoryBlob,
    selected: RepositoryBlob,
    workflows: Sequence[RepositoryBlob],
    boundary: RepositoryBlob,
) -> dict[str, Any]:
    def raw_group(
        *names: str,
        prefixes: tuple[str, ...] = (),
    ) -> tuple[RawSnapshot, ...]:
        selected_names = set(names)
        return tuple(
            snapshot
            for key, snapshot in raw.items()
            if key in selected_names
            or any(key.startswith(prefix) for prefix in prefixes)
        )

    common_repo = tuple(production)
    regression_raw = raw_group(
        "production-regression",
        "research-regression",
        prefixes=("production-regression-", "research-regression-"),
    )
    purity_raw: tuple[RawSnapshot, ...]
    side_effect_raw: tuple[RawSnapshot, ...]
    dependency_raw: tuple[RawSnapshot, ...]
    warning_raw: tuple[RawSnapshot, ...]
    oci_raw: tuple[RawSnapshot, ...]
    purity_checks: tuple[str, ...]
    side_effect_checks: tuple[str, ...]
    dependency_checks: tuple[str, ...]
    warning_checks: tuple[str, ...]
    if candidate == "scala":
        purity_raw = raw_group(
            "scala-policy",
            "scala-lint",
            prefixes=("scala-policy-",),
        )
        side_effect_raw = raw_group(
            "scala-policy",
            "scala-lint",
            "scala-dependency",
            prefixes=("scala-policy-",),
        )
        dependency_raw = (raw["scala-dependency"],)
        warning_raw = raw_group(
            "scala-format",
            "scala-selected",
            "scala-qualification",
            "scala-hard-compiler",
            "scala-correctness",
            prefixes=("scala-matrix-",),
        )
        oci_raw = raw_group(
            "oci-cross-comparison",
            prefixes=("scala-oci-",),
        )
        purity_checks = (
            "raw.scala.semantic-source-policy-pass",
            "subject.scala.core-has-no-shell-reference",
        )
        side_effect_checks = (
            "raw.scala.side-effect-policy-pass",
            "raw.scala.native-edge-count-zero",
        )
        dependency_checks = (
            "raw.scala.native-dependency-surface-zero",
            "subject.scala.dependency-policy-bound",
        )
        warning_checks = (
            "raw.scala.selected-profile-hard-compiler-pass",
            "raw.scala.selected-profile-correctness-pass",
        )
    else:
        purity_raw = (raw["haskell-safety"],)
        side_effect_raw = raw_group(
            "haskell-safety",
            "haskell-lint",
            prefixes=("haskell_lint-log-",),
        )
        dependency_raw = (raw["haskell-safety"],)
        warning_raw = raw_group(
            "haskell-lint",
            "haskell-format",
            "haskell-correctness",
            prefixes=(
                "haskell_lint-log-",
                "haskell_format-log-",
            ),
        )
        oci_raw = raw_group(
            "haskell-oci",
            "oci-cross-comparison",
            prefixes=("haskell-oci-",),
        )
        purity_checks = (
            "raw.haskell.module-safety-pass",
            "subject.haskell.core-has-no-contract-import",
        )
        side_effect_checks = (
            "raw.haskell.core-io-unsafe-edge-count-zero",
            "raw.haskell.hlint-policy-pass",
        )
        dependency_checks = (
            "raw.haskell.unknown-transitive-edge-count-zero",
            "subject.haskell.dependency-policy-bound",
        )
        warning_checks = (
            "raw.haskell.hlint-pass",
            "raw.haskell.selected-profile-correctness-pass",
        )
    rubric_map = {
        "purity-core-shell": _rubric_entry(
            "purity-core-shell",
            purity_checks,
            purity_raw,
            (*core, policy),
        ),
        "purity-side-effect": _rubric_entry(
            "purity-side-effect",
            side_effect_checks,
            side_effect_raw,
            (*core, policy),
        ),
        "purity-validation-transparency": _rubric_entry(
            "purity-validation-transparency",
            (
                "raw.semantic-comparison-zero-mismatch",
                "raw.coverage-full-function-property-error-closure",
            ),
            (
                raw["semantic"],
                raw["coverage"],
                raw["scala-property-execution"],
                raw["haskell-property-execution"],
            ),
            (*core, policy),
        ),
        "purity-dependency-surface": _rubric_entry(
            "purity-dependency-surface",
            dependency_checks,
            dependency_raw,
            (*core, policy),
        ),
        "maintainability-module-cohesion": _rubric_entry(
            "maintainability-module-cohesion",
            (
                f"subject.{candidate}.path-module-cohesion",
                f"subject.{candidate}.core-shell-separation",
            ),
            purity_raw,
            common_repo,
        ),
        "maintainability-duplication-inventory": _rubric_entry(
            "maintainability-duplication-inventory",
            (f"subject.{candidate}.normalized-eight-line-blocks-unique",),
            purity_raw,
            common_repo,
        ),
        "maintainability-comments-docs": _rubric_entry(
            "maintainability-comments-docs",
            (f"subject.{candidate}.public-core-callables-documented",),
            purity_raw,
            core,
        ),
        "maintainability-test-readability": _rubric_entry(
            "maintainability-test-readability",
            (
                f"subject.{candidate}.test-names-unique-and-descriptive",
                f"subject.{candidate}.unit-contract-property-test-structure",
            ),
            (
                raw["coverage"],
                raw[f"{candidate}-property-execution"],
            ),
            tests,
        ),
        "maintainability-warning-free": _rubric_entry(
            "maintainability-warning-free",
            warning_checks,
            warning_raw,
            (*core, selected),
        ),
        "integration-process-contract": _rubric_entry(
            "integration-process-contract",
            (
                "raw.canonical-comparison-zero-mismatch",
                "raw.semantic-comparison-zero-mismatch",
            ),
            (raw["canonical"], raw["semantic"]),
            common_repo,
        ),
        "integration-production-isolation": _rubric_entry(
            "integration-production-isolation",
            (
                "raw.research-regression-exact-deselect-replacement-closure",
                "subject.research-boundary-replacement-tests-present",
            ),
            regression_raw,
            (boundary,),
        ),
        "integration-ci-cost-rollback": _rubric_entry(
            "integration-ci-cost-rollback",
            (
                "subject.ci.correctness-path-scoped-and-cancellable",
                "subject.ci.benchmark-manual-only",
                f"raw.{candidate}.oci-network-none-and-pinned-base",
            ),
            (*oci_raw, *regression_raw),
            (*workflows, boundary),
        ),
    }
    _require(set(rubric_map) == set(RUBRIC_IDS), "RUBRIC_CLOSURE_INVALID")
    return {
        "schemaVersion": SCHEMA,
        "benchmarkSubjectCommit": subject,
        "candidate": candidate,
        "rubrics": [rubric_map[rubric_id] for rubric_id in RUBRIC_IDS],
        "status": "PASS",
    }


def _write_exclusive(directory_fd: int, name: str, payload: bytes) -> None:
    _portable_relative(name, label="OUTPUT")
    if "/" in name:
        raise CandidateRubricAuditError("OUTPUT_WRITE_FAILED")
    no_follow = getattr(os, "O_NOFOLLOW", None)
    if no_follow is None:
        raise CandidateRubricAuditError("OUTPUT_WRITE_FAILED")
    descriptor: int | None = None
    try:
        descriptor = os.open(
            name,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | no_follow
            | getattr(os, "O_CLOEXEC", 0),
            0o600,
            dir_fd=directory_fd,
        )
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                raise CandidateRubricAuditError("OUTPUT_WRITE_FAILED")
            offset += written
        os.fsync(descriptor)
    except CandidateRubricAuditError:
        raise
    except OSError as exc:
        raise CandidateRubricAuditError("OUTPUT_WRITE_FAILED") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _remove_output_directory(directory_fd: int, name: str) -> None:
    """같은 pinned parent dirfd 아래의 audit staging/final directory만 제거한다."""

    no_follow = getattr(os, "O_NOFOLLOW", None)
    if no_follow is None:
        raise CandidateRubricAuditError("OUTPUT_CLEANUP_FAILED")
    child_fd: int | None = None
    try:
        child_fd = os.open(
            name,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | no_follow
            | getattr(os, "O_CLOEXEC", 0),
            dir_fd=directory_fd,
        )
        for entry in os.listdir(child_fd):
            os.unlink(entry, dir_fd=child_fd)
        os.close(child_fd)
        child_fd = None
        os.rmdir(name, dir_fd=directory_fd)
    except FileNotFoundError:
        return
    except OSError as exc:
        raise CandidateRubricAuditError("OUTPUT_CLEANUP_FAILED") from exc
    finally:
        if child_fd is not None:
            os.close(child_fd)


def _rename_noreplace(
    source_fd: int,
    source_name: str,
    destination_fd: int,
    destination_name: str,
) -> None:
    """Linux renameat2(NOREPLACE)로 경쟁 중 생성된 목적지를 절대 교체하지 않는다."""

    library = ctypes.CDLL(None, use_errno=True)
    renameat2: Any = getattr(library, "renameat2", None)
    if renameat2 is None:
        raise CandidateRubricAuditError("OUTPUT_WRITE_FAILED")
    renameat2.argtypes = (
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    )
    renameat2.restype = ctypes.c_int
    result = renameat2(
        source_fd,
        os.fsencode(source_name),
        destination_fd,
        os.fsencode(destination_name),
        1,  # RENAME_NOREPLACE
    )
    if result == 0:
        return
    error_number = ctypes.get_errno()
    if error_number == errno.EEXIST:
        raise CandidateRubricAuditError("OUTPUT_WRITE_FAILED")
    raise CandidateRubricAuditError("OUTPUT_WRITE_FAILED") from OSError(
        error_number,
        os.strerror(error_number),
    )


def _publish_candidate_outputs(
    anchor: DirectoryAnchor,
    payloads: Mapping[str, bytes],
) -> None:
    """두 후보 파일을 sibling staging directory에 완성한 뒤 원자 publish한다."""

    stage_name = f".rubric-audit.stage-{secrets.token_hex(16)}"
    output_name = "rubric-audit"
    stage_fd: int | None = None
    published = False
    try:
        try:
            os.stat(
                output_name,
                dir_fd=anchor.descriptor,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            pass
        else:
            raise CandidateRubricAuditError("OUTPUT_ROOT_INVALID")
        os.mkdir(stage_name, mode=0o700, dir_fd=anchor.descriptor)
        stage_fd = os.open(
            stage_name,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
            dir_fd=anchor.descriptor,
        )
        for candidate in CANDIDATES:
            _write_exclusive(
                stage_fd,
                f"{candidate}-candidate-rubric-audit.v1.json",
                payloads[candidate],
            )
        os.fsync(stage_fd)
        _verify_directory_anchor(anchor, label="CORRECTNESS_ROOT")
        _rename_noreplace(
            anchor.descriptor,
            stage_name,
            anchor.descriptor,
            output_name,
        )
        published = True
        os.fsync(anchor.descriptor)
    except CandidateRubricAuditError:
        raise
    except OSError as exc:
        raise CandidateRubricAuditError("OUTPUT_WRITE_FAILED") from exc
    finally:
        if stage_fd is not None:
            os.close(stage_fd)
        cleanup_name = output_name if published else stage_name
        if sys.exc_info()[0] is not None:
            try:
                _remove_output_directory(anchor.descriptor, cleanup_name)
            except CandidateRubricAuditError:
                # 최초 실패 원인을 보존하면서도 best-effort cleanup을 수행한다.
                pass


def generate_candidate_rubric_audit(
    *,
    repository_root: Path,
    benchmark_subject_commit: str,
    correctness_root: Path,
    output_root: Path,
) -> dict[str, dict[str, Any]]:
    """두 후보의 12개 rubric을 raw receipt와 subject Git blob에서만 생성한다."""

    repository = _validate_repository(
        repository_root,
        benchmark_subject_commit,
    )
    correctness, output = _preflight_paths(
        correctness_root,
        output_root,
    )
    del output  # publication is anchored to correctness_fd, never to this Path.
    anchor = _open_directory_anchor(correctness, label="CORRECTNESS_ROOT")
    try:
        raw_snapshots: dict[PurePosixPath, RawSnapshot] = {}
        raw: dict[str, RawSnapshot] = {}

        def load(
            name: str,
            *,
            label: str,
        ) -> tuple[RawSnapshot, dict[str, Any]]:
            snapshot, document = _raw_json(
                anchor.descriptor,
                RAW_PATHS[name],
                label=label,
                snapshots=raw_snapshots,
            )
            raw[name] = snapshot
            return snapshot, document

        def load_path(
            path: PurePosixPath,
            *,
            label: str,
            key: str,
        ) -> tuple[RawSnapshot, dict[str, Any]]:
            snapshot, document = _raw_json(
                anchor.descriptor,
                path,
                label=label,
                snapshots=raw_snapshots,
            )
            raw[key] = snapshot
            return snapshot, document

        def snapshot_path(
            path: PurePosixPath,
            *,
            label: str,
            key: str,
        ) -> RawSnapshot:
            return _raw_snapshot(
                anchor.descriptor,
                path,
                label=label,
                key=key,
                snapshots=raw_snapshots,
                raw=raw,
            )

        # Subject-source audits run before receipt loading so source-quality
        # findings cannot be hidden by unrelated stale evidence.
        scala_production = (
            *_git_inventory(
                repository,
                benchmark_subject_commit,
                SCALA_CORE,
                suffix=".scala",
            ),
            *_git_inventory(
                repository,
                benchmark_subject_commit,
                SCALA_SHELL,
                suffix=".scala",
            ),
        )
        scala_core = tuple(
            blob for blob in scala_production if SCALA_CORE in blob.path.parents
        )
        scala_tests = _git_inventory(
            repository,
            benchmark_subject_commit,
            SCALA_TEST,
            suffix=".scala",
        )
        haskell_production = (
            *_git_inventory(
                repository,
                benchmark_subject_commit,
                HASKELL_CORE,
                suffix=".hs",
            ),
            *_git_inventory(
                repository,
                benchmark_subject_commit,
                HASKELL_CONTRACT,
                suffix=".hs",
            ),
        )
        haskell_core = tuple(
            blob for blob in haskell_production if HASKELL_CORE in blob.path.parents
        )
        haskell_tests = _git_inventory(
            repository,
            benchmark_subject_commit,
            HASKELL_TEST,
            suffix=".hs",
        )
        correctness_workflow = _git_blob(
            repository,
            benchmark_subject_commit,
            CORRECTNESS_WORKFLOW,
        )
        benchmark_workflow = _git_blob(
            repository,
            benchmark_subject_commit,
            BENCHMARK_WORKFLOW,
        )
        boundary = _git_blob(
            repository,
            benchmark_subject_commit,
            RESEARCH_BOUNDARY_TEST,
        )
        _audit_scala_cohesion(scala_production)
        _audit_haskell_cohesion(haskell_production)
        _audit_core_side_effect_surface(scala_core, language="scala")
        _audit_core_side_effect_surface(haskell_core, language="haskell")
        _audit_duplicate_blocks(scala_production, language="scala")
        _audit_duplicate_blocks(haskell_production, language="haskell")
        _audit_scala_public_docs(scala_core)
        _audit_haskell_public_docs(haskell_core)
        _audit_scala_tests(scala_tests)
        _audit_haskell_tests(haskell_tests)
        _audit_workflows(correctness_workflow, benchmark_workflow)
        _audit_research_boundary(boundary)

        repository_blobs = {
            "scala-policy": _git_blob(
                repository, benchmark_subject_commit, SCALA_POLICY
            ),
            "haskell-policy": _git_blob(
                repository, benchmark_subject_commit, HASKELL_POLICY
            ),
            "scala-manifest": _git_blob(
                repository, benchmark_subject_commit, SCALA_SOURCE_INPUTS
            ),
            "haskell-manifest": _git_blob(
                repository, benchmark_subject_commit, HASKELL_SOURCE_INPUTS
            ),
            "haskell-package": _git_blob(
                repository,
                benchmark_subject_commit,
                S1 / "haskell/package.yaml",
            ),
            "haskell-toolchain": _git_blob(
                repository, benchmark_subject_commit, HASKELL_TOOLCHAIN_LOCK
            ),
            "scala-selected-source": _git_blob(
                repository, benchmark_subject_commit, SCALA_SELECTED_SOURCE
            ),
            "scala-compiler-profiles": _git_blob(
                repository, benchmark_subject_commit, SCALA_COMPILER_PROFILES
            ),
            "scala-toolchain": _git_blob(
                repository, benchmark_subject_commit, SCALA_TOOLCHAIN_LOCK
            ),
            "scala-project": _git_blob(
                repository,
                benchmark_subject_commit,
                S1 / "scala/project.scala",
            ),
            "scala-containerfile": _git_blob(
                repository, benchmark_subject_commit, SCALA_CONTAINERFILE
            ),
            "haskell-containerfile": _git_blob(
                repository, benchmark_subject_commit, HASKELL_CONTAINERFILE
            ),
            "benchmark-plan": _git_blob(
                repository, benchmark_subject_commit, BENCHMARK_PLAN
            ),
            "property-plan": _git_blob(
                repository, benchmark_subject_commit, PROPERTY_PLAN
            ),
            "property-seeds": _git_blob(
                repository, benchmark_subject_commit, PROPERTY_SEEDS
            ),
            "function-registry": _git_blob(
                repository, benchmark_subject_commit, FUNCTION_REGISTRY
            ),
            "error-registry": _git_blob(
                repository, benchmark_subject_commit, ERROR_REGISTRY
            ),
            "canonical-inputs": _git_blob(
                repository, benchmark_subject_commit, CANONICAL_INPUTS
            ),
            "canonical-results": _git_blob(
                repository, benchmark_subject_commit, CANONICAL_RESULTS
            ),
            "semantic-inputs": _git_blob(
                repository, benchmark_subject_commit, SEMANTIC_INPUTS
            ),
            "semantic-results": _git_blob(
                repository, benchmark_subject_commit, SEMANTIC_RESULTS
            ),
            "scala-runner": _git_blob(
                repository, benchmark_subject_commit, SCALA_PROPERTY_RUNNER
            ),
            "haskell-runner": _git_blob(
                repository, benchmark_subject_commit, HASKELL_PROPERTY_RUNNER
            ),
            "haskell-selected": _git_blob(
                repository, benchmark_subject_commit, HASKELL_SELECTED
            ),
        }
        scala_sources, scala_manifest_document = _validate_source_manifest(
            repository,
            benchmark_subject_commit,
            repository_blobs["scala-manifest"],
            language="scala",
        )
        haskell_sources, haskell_manifest_document = _validate_source_manifest(
            repository,
            benchmark_subject_commit,
            repository_blobs["haskell-manifest"],
            language="haskell",
        )
        scala_subject_paths = {
            blob.path.relative_to(S1 / "scala").as_posix()
            for blob in (*scala_production, *scala_tests)
        }
        haskell_subject_paths = {
            blob.path.relative_to(S1 / "haskell").as_posix()
            for blob in (*haskell_production, *haskell_tests)
        }
        _require(
            scala_subject_paths
            == {
                path
                for path, entry in scala_manifest_document["files"].items()
                if entry["role"] in {"main", "test"}
            },
            "SCALA_SOURCE_INPUT_SET_INVALID",
        )
        _require(
            haskell_subject_paths
            == {
                path
                for path, entry in haskell_manifest_document["files"].items()
                if entry["role"] in {"main", "test"}
            },
            "HASKELL_SOURCE_INPUT_SET_INVALID",
        )
        generated_cabal_snapshot = snapshot_path(
            RAW_PATHS["haskell-generated-cabal"],
            label="HASKELL_GENERATED_CABAL",
            key="haskell-generated-cabal",
        )
        generated_cabal_provenance_snapshot, generated_cabal_provenance = load(
            "haskell-generated-cabal-provenance",
            label="HASKELL_GENERATED_CABAL_PROVENANCE",
        )
        scala_source_closure = _scala_property_source_closure(scala_sources)
        haskell_source_tree = _haskell_source_tree_sha256(
            repository,
            benchmark_subject_commit,
            haskell_sources,
            generated_cabal_snapshot,
        )
        haskell_source_closure = _haskell_property_source_closure(
            repository,
            benchmark_subject_commit,
            haskell_sources,
            generated_cabal_snapshot,
        )
        compiler_profiles_document = _strict_json(
            repository_blobs["scala-compiler-profiles"].payload,
            label="SCALA_COMPILER_PROFILES",
        )
        toolchain_document = _strict_json(
            repository_blobs["scala-toolchain"].payload,
            label="SCALA_TOOLCHAIN",
        )
        benchmark_plan_document = _strict_json(
            repository_blobs["benchmark-plan"].payload,
            label="BENCHMARK_PLAN",
        )
        property_plan_document = _strict_json(
            repository_blobs["property-plan"].payload,
            label="PROPERTY_PLAN",
        )
        haskell_selected_document = _strict_json(
            repository_blobs["haskell-selected"].payload,
            label="HASKELL_SELECTED_PROFILE",
        )
        _validate_generated_cabal_provenance(
            generated_cabal_provenance,
            snapshot=generated_cabal_provenance_snapshot,
            artifact=generated_cabal_snapshot,
            subject=benchmark_subject_commit,
            output_directory=correctness / "coverage/haskell",
            package_yaml=repository_blobs["haskell-package"],
            manifest=repository_blobs["haskell-manifest"],
            toolchain_lock=repository_blobs["haskell-toolchain"],
            source_tree_sha256=haskell_source_tree,
            property_closure_sha256=haskell_source_closure,
            selected_document=haskell_selected_document,
        )

        canonical_snapshot, canonical = load(
            "canonical", label="COMPARISON_CANONICAL"
        )
        semantic_snapshot, semantic = load(
            "semantic", label="COMPARISON_SEMANTIC"
        )
        _validate_comparison(canonical, matrix="canonical")
        _validate_comparison(semantic, matrix="semantic")
        cross_bindings: dict[str, dict[str, RawSnapshot]] = {}
        for matrix in ("canonical", "semantic"):
            cross_bindings[matrix] = {
                kind: load(
                    f"{matrix}-{kind}",
                    label=f"{matrix.upper()}_{kind.upper()}",
                )[0]
                for kind in ("reference", "scala", "haskell", "summary")
            }
        _, production_regression = load(
            "production-regression",
            label="PRODUCTION_REGRESSION",
        )
        _, research_regression = load(
            "research-regression",
            label="RESEARCH_REGRESSION",
        )
        _validate_regression(
            production_regression,
            subject=benchmark_subject_commit,
            role="production",
            correctness_fd=anchor.descriptor,
            snapshots=raw_snapshots,
            raw=raw,
        )
        _validate_regression(
            research_regression,
            subject=benchmark_subject_commit,
            role="research",
            correctness_fd=anchor.descriptor,
            snapshots=raw_snapshots,
            raw=raw,
        )

        scala_semantic_snapshot, scala_semantic_document = load(
            "scala-lint",
            label="SCALA_SEMANTIC_POLICY",
        )
        _validate_scala_semantic_receipt(
            scala_semantic_document,
            policy=repository_blobs["scala-policy"],
            manifest=repository_blobs["scala-manifest"],
            sources=scala_sources,
        )
        scala_policy_snapshot, scala_policy_document = load(
            "scala-policy",
            label="SCALA_SOURCE_POLICY",
        )
        _validate_scala_policy(
            scala_policy_document,
            snapshot=scala_policy_snapshot,
            semantic_snapshot=scala_semantic_snapshot,
            policy=repository_blobs["scala-policy"],
            manifest=repository_blobs["scala-manifest"],
            sources=scala_sources,
            correctness_fd=anchor.descriptor,
            snapshots=raw_snapshots,
            raw=raw,
        )
        _, scala_dependency_document = load(
            "scala-dependency",
            label="SCALA_DEPENDENCY",
        )
        _validate_scala_dependency(
            scala_dependency_document,
            policy=repository_blobs["scala-policy"],
            manifest=repository_blobs["scala-manifest"],
            project=repository_blobs["scala-project"],
        )
        _, scala_format_document = load(
            "scala-format",
            label="SCALA_FORMAT",
        )
        _validate_scala_format(
            scala_format_document,
            manifest=repository_blobs["scala-manifest"],
            sources=scala_sources,
            toolchain_lock=repository_blobs["scala-toolchain"],
        )

        scala_selected_snapshot, scala_selected_document = load(
            "scala-selected",
            label="SCALA_SELECTED_PROFILE",
        )
        scala_profile_value = scala_selected_document.get("selectedProfileId")
        _require(
            scala_profile_value in {"A", "B", "C"},
            "SCALA_SELECTED_PROFILE_INVALID",
        )
        scala_profile = str(scala_profile_value)
        scala_profile_root = PurePosixPath(f"scala/profiles/{scala_profile}")
        scala_qualification_snapshot, scala_qualification_document = load_path(
            PurePosixPath(
                "scala/qualification/scala-profile-qualification.v1.json"
            ),
            label="SCALA_QUALIFICATION",
            key="scala-qualification",
        )
        scala_correctness_snapshot, scala_correctness_document = load_path(
            scala_profile_root / "scala-profile-correctness-result.v1.json",
            label="SCALA_SELECTED_CORRECTNESS",
            key="scala-correctness",
        )
        _, scala_compiler_document = load_path(
            PurePosixPath(
                f"scala/hard-compiler-{scala_profile}/"
                "scala-hard-compiler-result.v1.json"
            ),
            label="SCALA_SELECTED_HARD_COMPILER",
            key="scala-hard-compiler",
        )
        scala_candidate = snapshot_path(
            scala_profile_root / "candidate.jar",
            label="SCALA_SELECTED_CANDIDATE",
            key="scala-candidate",
        )
        scala_matrix_paths = {
            "canonical-result": "canonical-results.json",
            "semantic-result": "semantic-errors.json",
            "unit-result": "scala-profile-unit-test-result.v1.json",
            "unit-stdout": "unit-test.stdout",
            "unit-stderr": "unit-test.stderr",
            "canonical-comparison": "canonical-comparison.json",
            "semantic-comparison": "semantic-comparison.json",
            "property-report": "property/scala-property-report.v1.json",
            "registry-report": "property/scala-registry-report.v1.json",
            "property-execution": (
                "property/scala-property-execution-evidence.v1.json"
            ),
        }
        scala_matrix_artifacts = {
            key: snapshot_path(
                scala_profile_root / suffix,
                label="SCALA_SELECTED_MATRIX",
                key=f"scala-matrix-{key}",
            )
            for key, suffix in scala_matrix_paths.items()
        }
        scala_profile = _validate_scala_selected(
            scala_selected_document,
            scala_selected_snapshot,
            scala_qualification_document,
            scala_qualification_snapshot,
            scala_correctness_document,
            scala_correctness_snapshot,
            scala_compiler_document,
            benchmark_plan=repository_blobs["benchmark-plan"],
            compiler_profiles=repository_blobs["scala-compiler-profiles"],
            compiler_profiles_document=compiler_profiles_document,
            manifest=repository_blobs["scala-manifest"],
            manifest_document=scala_manifest_document,
            selected_source=repository_blobs["scala-selected-source"],
            toolchain_lock=repository_blobs["scala-toolchain"],
            toolchain_document=toolchain_document,
            candidate=scala_candidate,
            matrix_artifacts=scala_matrix_artifacts,
            benchmark_plan_document=benchmark_plan_document,
            property_plan=repository_blobs["property-plan"],
            property_seeds=repository_blobs["property-seeds"],
            function_registry=repository_blobs["function-registry"],
            error_registry=repository_blobs["error-registry"],
        )

        _, haskell_safety_document = load(
            "haskell-safety",
            label="HASKELL_MODULE_SAFETY",
        )
        _validate_haskell_safety(
            haskell_safety_document,
            policy=repository_blobs["haskell-policy"],
            manifest=repository_blobs["haskell-manifest"],
            sources=haskell_sources,
        )
        _, haskell_lint_document = load(
            "haskell-lint",
            label="HASKELL_LINT",
        )
        _validate_haskell_lint(
            haskell_lint_document,
            manifest=repository_blobs["haskell-manifest"],
            manifest_document=haskell_manifest_document,
            correctness_fd=anchor.descriptor,
            snapshots=raw_snapshots,
            raw=raw,
        )
        _, haskell_format_document = load(
            "haskell-format",
            label="HASKELL_FORMAT",
        )
        _validate_haskell_format(
            haskell_format_document,
            manifest=repository_blobs["haskell-manifest"],
            manifest_document=haskell_manifest_document,
            correctness_fd=anchor.descriptor,
            snapshots=raw_snapshots,
            raw=raw,
        )
        correctness_documents: dict[str, Mapping[str, Any]] = {}
        correctness_artifacts: dict[str, Mapping[str, RawSnapshot]] = {}
        correctness_snapshots: dict[str, RawSnapshot] = {}
        haskell_phases = (
            "build",
            "test",
            "canonical-process",
            "canonical-compare",
            "semantic-process",
            "semantic-compare",
        )
        for profile in ("baseline-o0-fasm", "optimized-o2-fasm"):
            root = PurePosixPath(f"haskell/profiles/{profile}")
            receipt, document = load_path(
                root / "correctness-receipt.v1.json",
                label=f"HASKELL_CORRECTNESS_{profile}",
                key=f"haskell-correctness-{profile}",
            )
            artifacts: dict[str, RawSnapshot] = {}
            for phase in haskell_phases:
                for stream in ("stdout", "stderr"):
                    key = f"{phase}-{stream}"
                    artifacts[key] = snapshot_path(
                        root / f"{phase}.{stream}",
                        label="HASKELL_CORRECTNESS_COMMAND",
                        key=f"haskell-{profile}-{key}",
                    )
            for matrix in ("canonical", "semantic"):
                artifacts[f"{matrix}-actual"] = snapshot_path(
                    root / f"{matrix}.actual.json",
                    label="HASKELL_CORRECTNESS_ACTUAL",
                    key=f"haskell-{profile}-{matrix}-actual",
                )
                artifacts[f"{matrix}-comparison"] = snapshot_path(
                    root / f"{matrix}.comparison.json",
                    label="HASKELL_CORRECTNESS_COMPARISON",
                    key=f"haskell-{profile}-{matrix}-comparison",
                )
            correctness_documents[profile] = document
            correctness_artifacts[profile] = artifacts
            correctness_snapshots[profile] = receipt
        _, haskell_qualification = load_path(
            PurePosixPath(
                "haskell/qualification/qualification-artifact.v1.json"
            ),
            label="HASKELL_QUALIFICATION",
            key="haskell-qualification",
        )
        haskell_profile, haskell_binaries = _validate_haskell_selected(
            haskell_selected_document,
            correctness_documents=correctness_documents,
            correctness_artifacts=correctness_artifacts,
            qualification=haskell_qualification,
            subject=benchmark_subject_commit,
            repository=repository,
            selected_profile=repository_blobs["haskell-selected"],
            manifest=repository_blobs["haskell-manifest"],
            manifest_sources=haskell_sources,
            generated_cabal=generated_cabal_snapshot,
            benchmark_plan=repository_blobs["benchmark-plan"],
            benchmark_plan_document=benchmark_plan_document,
            canonical_inputs=repository_blobs["canonical-inputs"],
            canonical_results=repository_blobs["canonical-results"],
            semantic_inputs=repository_blobs["semantic-inputs"],
            semantic_results=repository_blobs["semantic-results"],
        )
        raw["haskell-correctness"] = correctness_snapshots[haskell_profile]
        for matrix, comparison_snapshot, expected in (
            (
                "canonical",
                canonical_snapshot,
                repository_blobs["canonical-results"],
            ),
            (
                "semantic",
                semantic_snapshot,
                repository_blobs["semantic-results"],
            ),
        ):
            binding = cross_bindings[matrix]
            _validate_native_cross_binding(
                matrix,
                comparison=comparison_snapshot,
                reference=binding["reference"],
                scala=binding["scala"],
                haskell=binding["haskell"],
                summary=binding["summary"],
                expected=expected,
                selected_scala=scala_matrix_artifacts[
                    f"{matrix}-result"
                ],
                selected_haskell=correctness_artifacts[haskell_profile][
                    f"{matrix}-actual"
                ],
            )

        _, scala_property_execution = load(
            "scala-property-execution",
            label="SCALA_PROPERTY_EXECUTION",
        )
        _, haskell_property_execution = load(
            "haskell-property-execution",
            label="HASKELL_PROPERTY_EXECUTION",
        )
        _validate_property_execution_reports(
            scala_property_execution,
            haskell_property_execution,
            property_plan=repository_blobs["property-plan"],
            property_plan_document=property_plan_document,
            property_seeds=repository_blobs["property-seeds"],
            scala_runner=repository_blobs["scala-runner"],
            haskell_runner=repository_blobs["haskell-runner"],
            scala_source_closure_sha256=scala_source_closure,
            haskell_source_closure_sha256=haskell_source_closure,
            scala_profile=scala_profile,
            haskell_profile=haskell_profile,
            scala_toolchain_document=toolchain_document,
            haskell_manifest=repository_blobs["haskell-manifest"],
            haskell_selected=repository_blobs["haskell-selected"],
            haskell_selected_document=haskell_selected_document,
            haskell_stack_root_path_id=str(
                generated_cabal_provenance["build"]["stackRootPathId"]
            ),
        )
        coverage_snapshot, coverage_document = load(
            "coverage",
            label="COVERAGE",
        )
        del coverage_snapshot
        _validate_coverage(
            coverage_document,
            property_plan=repository_blobs["property-plan"],
            property_seeds=repository_blobs["property-seeds"],
            scala_runner=repository_blobs["scala-runner"],
            haskell_runner=repository_blobs["haskell-runner"],
            scala_source_closure_sha256=scala_source_closure,
            haskell_source_closure_sha256=haskell_source_closure,
            scala_profile=scala_profile,
            haskell_profile=haskell_profile,
        )

        scala_build_snapshot, scala_build_document = load(
            "scala-oci-build",
            label="SCALA_OCI_BUILD",
        )
        _, scala_runtime_document = load(
            "scala-oci-runtime",
            label="SCALA_OCI_RUNTIME",
        )
        binding_before = snapshot_path(
            PurePosixPath(
                "oci/scala/runtime/oci-runtime-binding-before.v1.json"
            ),
            label="SCALA_OCI_BINDING",
            key="scala-oci-binding-before",
        )
        binding_after = snapshot_path(
            PurePosixPath(
                "oci/scala/runtime/oci-runtime-binding-after.v1.json"
            ),
            label="SCALA_OCI_BINDING",
            key="scala-oci-binding-after",
        )
        binding_document = _strict_json(
            binding_before.payload,
            label="SCALA_OCI_BINDING",
        )
        scala_oci_artifacts = {
            "canonical-result": snapshot_path(
                PurePosixPath("oci/scala/runtime/canonical-results.json"),
                label="SCALA_OCI_CANONICAL",
                key="scala-oci-canonical-result",
            ),
            "semantic-result": snapshot_path(
                PurePosixPath("oci/scala/runtime/semantic-errors.json"),
                label="SCALA_OCI_SEMANTIC",
                key="scala-oci-semantic-result",
            ),
            "canonical-comparison": snapshot_path(
                PurePosixPath(
                    "oci/scala/runtime/canonical-comparison.json"
                ),
                label="SCALA_OCI_CANONICAL_COMPARISON",
                key="scala-oci-canonical-comparison",
            ),
            "semantic-comparison": snapshot_path(
                PurePosixPath("oci/scala/runtime/semantic-comparison.json"),
                label="SCALA_OCI_SEMANTIC_COMPARISON",
                key="scala-oci-semantic-comparison",
            ),
        }
        _validate_scala_oci(
            scala_build_document,
            scala_runtime_document,
            build_snapshot=scala_build_snapshot,
            candidate=scala_candidate,
            containerfile=repository_blobs["scala-containerfile"],
            binding_before=binding_before,
            binding_after=binding_after,
            binding=binding_document,
            artifacts=scala_oci_artifacts,
        )

        _, haskell_oci_document = load(
            "haskell-oci",
            label="HASKELL_OCI",
        )
        haskell_oci_phases = (
            "oci-stack-build",
            "oci-context-before",
            "oci-daemon-before",
            "oci-base-before",
            "oci-image-build",
            "oci-image-id-inspect",
            "oci-image-inspect",
            "oci-canonical-run",
            "oci-canonical-tag-check",
            "oci-canonical-compare",
            "oci-semantic-run",
            "oci-semantic-tag-check",
            "oci-semantic-compare",
            "oci-base-after",
            "oci-context-after",
            "oci-daemon-after",
        )
        haskell_oci_artifacts: dict[str, RawSnapshot] = {}
        for phase in haskell_oci_phases:
            for stream in ("stdout", "stderr"):
                key = f"{phase}-{stream}"
                haskell_oci_artifacts[key] = snapshot_path(
                    PurePosixPath(f"oci/haskell/{phase}.{stream}"),
                    label="HASKELL_OCI_COMMAND",
                    key=f"haskell-oci-{key}",
                )
        for matrix in ("canonical", "semantic"):
            haskell_oci_artifacts[f"{matrix}-actual"] = snapshot_path(
                PurePosixPath(
                    f"oci/haskell/runtime/{matrix}.actual.json"
                ),
                label="HASKELL_OCI_ACTUAL",
                key=f"haskell-oci-{matrix}-actual",
            )
            haskell_oci_artifacts[f"{matrix}-comparison"] = snapshot_path(
                PurePosixPath(
                    f"oci/haskell/{matrix}.oci-comparison.json"
                ),
                label="HASKELL_OCI_COMPARISON",
                key=f"haskell-oci-{matrix}-comparison",
            )
        _validate_haskell_oci(
            haskell_oci_document,
            subject=benchmark_subject_commit,
            selected_profile=repository_blobs["haskell-selected"],
            selected_document=haskell_selected_document,
            profile=haskell_profile,
            source_tree_sha256=haskell_source_tree,
            candidate_binary_sha256=haskell_binaries[haskell_profile],
            containerfile=repository_blobs["haskell-containerfile"],
            artifacts=haskell_oci_artifacts,
        )
        _, oci_cross = load(
            "oci-cross-comparison",
            label="OCI_CROSS_COMPARISON",
        )
        _validate_comparison(oci_cross, matrix="canonical")

        workflows = (correctness_workflow, benchmark_workflow)
        summaries = {
            "scala": _build_candidate_summary(
                subject=benchmark_subject_commit,
                candidate="scala",
                raw=raw,
                production=scala_production,
                core=scala_core,
                tests=scala_tests,
                policy=repository_blobs["scala-policy"],
                selected=repository_blobs["scala-selected-source"],
                workflows=workflows,
                boundary=boundary,
            ),
            "haskell": _build_candidate_summary(
                subject=benchmark_subject_commit,
                candidate="haskell",
                raw=raw,
                production=haskell_production,
                core=haskell_core,
                tests=haskell_tests,
                policy=repository_blobs["haskell-policy"],
                selected=repository_blobs["haskell-selected"],
                workflows=workflows,
                boundary=boundary,
            ),
        }
        payloads = {
            candidate: _canonical_json_bytes(summaries[candidate])
            for candidate in CANDIDATES
        }
        _verify_raw_snapshots_unchanged(
            anchor.descriptor,
            tuple(
                raw_snapshots[path]
                for path in sorted(
                    raw_snapshots,
                    key=lambda value: value.as_posix().encode(),
                )
            ),
        )
        _validate_repository(repository, benchmark_subject_commit)
        _verify_directory_anchor(anchor, label="CORRECTNESS_ROOT")
        _publish_candidate_outputs(anchor, payloads)
        return summaries
    finally:
        os.close(anchor.descriptor)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate independent S1.4X candidate rubric assessments."
    )
    parser.add_argument("--repository-root", required=True, type=Path)
    parser.add_argument("--benchmark-subject-commit", required=True)
    parser.add_argument("--correctness-root", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        result = generate_candidate_rubric_audit(
            repository_root=arguments.repository_root,
            benchmark_subject_commit=arguments.benchmark_subject_commit,
            correctness_root=arguments.correctness_root,
            output_root=arguments.output_root,
        )
    except CandidateRubricAuditError as exc:
        print(f"S1_4X_CANDIDATE_RUBRIC_AUDIT_FAIL:{exc}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "schemaVersion": SCHEMA,
                "candidateCount": len(result),
                "rubricCountPerCandidate": len(RUBRIC_IDS),
                "outputRoot": "rubric-audit",
                "status": "PASS",
            },
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
