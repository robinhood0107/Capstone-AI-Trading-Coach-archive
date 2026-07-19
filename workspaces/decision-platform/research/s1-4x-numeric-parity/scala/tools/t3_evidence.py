#!/usr/bin/env python3
"""Scala T3 evidence validation, profile selection, and typed result assembly."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import os
import re
import stat
import statistics
import sys
from pathlib import Path
from typing import Any

import precompile_jmh_generated_java as jmh_precompile


class T3EvidenceError(ValueError):
    """Frozen Scala T3 evidence가 불완전하거나 서로 일치하지 않음을 나타낸다."""


SHA256 = re.compile(r"^[0-9a-f]{64}$")
SCALA_PROFILES = ("A", "B", "C")
PROFILE_OPTIONS = {
    "A": [],
    "B": ["-opt"],
    "C": ["-opt", "-opt-inline:ai.trading.coach.s14x.**"],
}
PROFILE_CLI_ARGUMENTS = {
    profile: [f"--scalac-option={option}" for option in options]
    for profile, options in PROFILE_OPTIONS.items()
}


def isolated_scala_workspace(output_directory: Path) -> Path:
    """Standalone JMH wrapper와 같은 external `.scala-build` 위치를 계산한다."""

    cache_root = Path(
        os.environ.get(
            "S1_4X_CACHE_ROOT",
            str(Path.home() / ".cache/s1-4x"),
        )
    )
    isolation_key = hashlib.sha256(
        str(output_directory).encode("utf-8")
    ).hexdigest()
    return (
        cache_root
        / "scala-isolation"
        / isolation_key
        / "scala-workspace"
    )
JMH_BENCHMARKS = {
    "path-transform": (
        "s1_4x.benchmarks.path_transform."
        "PathTransformBenchmark.benchmark"
    ),
    "classical-path-risk": (
        "s1_4x.benchmarks.classical_path_risk."
        "ClassicalPathRiskBenchmark.benchmark"
    ),
    "intraday-realized": (
        "s1_4x.benchmarks.intraday_realized."
        "IntradayRealizedBenchmark.benchmark"
    ),
    "serial-sharpe": (
        "s1_4x.benchmarks.serial_sharpe."
        "SerialSharpeBenchmark.benchmark"
    ),
    "probabilistic-scalar": (
        "s1_4x.benchmarks.probabilistic_scalar."
        "ProbabilisticScalarBenchmark.benchmark"
    ),
    "coverage-batch": (
        "s1_4x.benchmarks.coverage_batch."
        "CoverageBatchBenchmark.benchmark"
    ),
}
EXTRA_SEMANTIC_SYMBOLS = {
    "scala.collection.mutable",
    "java.lang.Math.fma",
    "scala.Float",
    "scala.Conversion",
    "scala.annotation.internal.RuntimeChecked",
    "scala.language.experimental",
    "ExplicitResultTypes",
    "UNRESOLVED",
    "DisableSyntax.null",
    "DisableSyntax.return",
    "DisableSyntax.asInstanceOf",
    "DisableSyntax.isInstanceOf",
    "DisableSyntax.throw",
}


def sha256_bytes(payload: bytes) -> str:
    """이미 고정된 immutable bytes의 SHA-256을 계산한다."""

    return hashlib.sha256(payload).hexdigest()


class SealedArtifact:
    """한 번 연 regular inode에서 읽은 immutable evidence bytes와 identity다."""

    def __init__(
        self,
        *,
        path: Path,
        root: Path,
        payload: bytes,
        identity: tuple[int, int, int, int, int, int],
        label: str,
        identity_only_postcheck: bool = False,
    ) -> None:
        self.path = path
        self.root = root
        self.payload = payload
        self.identity = identity
        self.label = label
        self.sha256 = sha256_bytes(payload)
        self.identity_only_postcheck = identity_only_postcheck

    def json_value(self) -> Any:
        """Path를 재개방하지 않고 capture 당시 bytes만 strict JSON으로 해석한다."""

        try:
            text = self.payload.decode("utf-8")
        except UnicodeError as error:
            raise T3EvidenceError(
                f"SEALED_EVIDENCE_UTF8_INVALID:{self.label}"
            ) from error
        try:
            return json.loads(
                text,
                parse_constant=lambda token: (_ for _ in ()).throw(
                    T3EvidenceError(
                        f"NONFINITE_JSON:{self.label}:{token}"
                    )
                ),
                object_pairs_hook=reject_duplicate_keys,
            )
        except json.JSONDecodeError as error:
            raise T3EvidenceError(
                f"SEALED_EVIDENCE_JSON_INVALID:{self.label}"
            ) from error


class SealedEvidenceSnapshot:
    """Selector 한 번의 모든 hash/parse를 같은 captured bytes에 결속한다."""

    def __init__(self) -> None:
        self._artifacts: dict[Path, SealedArtifact] = {}
        self._roots: dict[Path, tuple[int, int, int, int, int]] = {}
        self._regular_files: dict[
            Path,
            tuple[jmh_precompile.RegularFileSnapshot, str],
        ] = {}
        self._regular_files_content_verified: set[Path] = set()

    @staticmethod
    def _root_identity(value: os.stat_result) -> tuple[int, int, int, int, int]:
        return (
            value.st_dev,
            value.st_ino,
            value.st_mode,
            value.st_uid,
            value.st_gid,
        )

    def _canonical_root(self, root: Path) -> Path:
        existing = self._roots.get(root)
        if existing is not None:
            try:
                current = os.stat(root, follow_symlinks=False)
            except OSError as error:
                raise T3EvidenceError(
                    "SEALED_EVIDENCE_ROOT_INVALID"
                ) from error
            if (
                not stat.S_ISDIR(current.st_mode)
                or self._root_identity(current) != existing
            ):
                raise T3EvidenceError("SEALED_EVIDENCE_ROOT_SUBSTITUTED")
            return root
        if not root.is_absolute() or root.is_symlink() or not root.is_dir():
            raise T3EvidenceError("SEALED_EVIDENCE_ROOT_INVALID")
        resolved = root.resolve(strict=True)
        if resolved != root:
            raise T3EvidenceError("SEALED_EVIDENCE_ROOT_NOT_CANONICAL")
        metadata = os.stat(root, follow_symlinks=False)
        self._roots[root] = self._root_identity(metadata)
        return root

    @staticmethod
    def _identity(value: os.stat_result) -> tuple[int, int, int, int, int, int]:
        return (
            value.st_dev,
            value.st_ino,
            value.st_mode,
            value.st_size,
            value.st_mtime_ns,
            value.st_ctime_ns,
        )

    def _pathname_identity(
        self,
        path: Path,
        *,
        label: str,
    ) -> tuple[int, int, int, int, int, int]:
        try:
            metadata = os.stat(path, follow_symlinks=False)
        except OSError as error:
            raise T3EvidenceError(
                f"SEALED_EVIDENCE_PATH_STAT_FAILED:{label}"
            ) from error
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
        ):
            raise T3EvidenceError(
                f"SEALED_EVIDENCE_PATH_NOT_SINGLE_REGULAR:{label}"
            )
        return self._identity(metadata)

    def _open_lexical(
        self,
        path: Path,
        *,
        root: Path,
        label: str,
    ) -> tuple[int, tuple[int, int, int, int, int, int]]:
        canonical_root = self._canonical_root(root)
        if not path.is_absolute():
            raise T3EvidenceError(f"SEALED_EVIDENCE_PATH_NOT_ABSOLUTE:{label}")
        try:
            relative = path.relative_to(canonical_root)
        except ValueError as error:
            raise T3EvidenceError(
                f"SEALED_EVIDENCE_OUTSIDE_ROOT:{label}"
            ) from error
        if not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
            raise T3EvidenceError(f"SEALED_EVIDENCE_PATH_INVALID:{label}")

        directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        file_flags = os.O_RDONLY | os.O_NOFOLLOW
        current = os.open(canonical_root, directory_flags)
        try:
            if (
                self._root_identity(os.fstat(current))
                != self._roots[canonical_root]
            ):
                raise T3EvidenceError(
                    f"SEALED_EVIDENCE_ROOT_SUBSTITUTED:{label}"
                )
            for component in relative.parts[:-1]:
                next_directory = os.open(
                    component,
                    directory_flags,
                    dir_fd=current,
                )
                os.close(current)
                current = next_directory
            descriptor = os.open(
                relative.parts[-1],
                file_flags,
                dir_fd=current,
            )
        except OSError as error:
            raise T3EvidenceError(
                f"SEALED_EVIDENCE_OPEN_FAILED:{label}"
            ) from error
        finally:
            os.close(current)
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_size < 0
        ):
            os.close(descriptor)
            raise T3EvidenceError(
                f"SEALED_EVIDENCE_NOT_SINGLE_REGULAR:{label}"
            )
        return descriptor, self._identity(metadata)

    def capture(
        self,
        path: Path,
        *,
        root: Path,
        label: str,
    ) -> SealedArtifact:
        """O_NOFOLLOW/openat으로 연 inode를 한 번 읽고 process-local bytes로 고정한다."""

        key = path
        existing = self._artifacts.get(key)
        if existing is not None:
            canonical_root = self._canonical_root(root)
            try:
                relative = path.relative_to(canonical_root)
            except ValueError as error:
                raise T3EvidenceError(
                    f"SEALED_EVIDENCE_OUTSIDE_ROOT:{label}"
                ) from error
            if (
                existing.root != canonical_root
                or not relative.parts
                or any(part in {"", ".", ".."} for part in relative.parts)
            ):
                raise T3EvidenceError(
                    f"SEALED_EVIDENCE_ROOT_MISMATCH:{label}"
                )
            return existing
        canonical_root = self._canonical_root(root)
        descriptor, before = self._open_lexical(
            path,
            root=canonical_root,
            label=label,
        )
        try:
            chunks: list[bytes] = []
            while True:
                chunk = os.read(descriptor, 1024 * 1024)
                if not chunk:
                    break
                chunks.append(chunk)
            after = self._identity(os.fstat(descriptor))
        finally:
            os.close(descriptor)
        path_after = self._pathname_identity(path, label=label)
        payload = b"".join(chunks)
        if (
            before != after
            or after != path_after
            or len(payload) != before[3]
        ):
            raise T3EvidenceError(
                f"SEALED_EVIDENCE_CHANGED_DURING_CAPTURE:{label}"
            )
        artifact = SealedArtifact(
            path=path,
            root=canonical_root,
            payload=payload,
            identity=before,
            label=label,
        )
        self._artifacts[key] = artifact
        return artifact

    def sha256(self, path: Path, *, root: Path, label: str) -> str:
        return self.capture(path, root=root, label=label).sha256

    def capture_regular_file(
        self,
        path: Path,
        *,
        label: str,
    ) -> jmh_precompile.RegularFileSnapshot:
        """대형 immutable dependency를 gate 전체에서 한 번만 hash해 공유한다."""

        existing = self._regular_files.get(path)
        if existing is not None:
            return existing[0]
        try:
            captured = jmh_precompile._snapshot_regular_file(
                path,
                label=label,
                retain_payload=False,
            )
        except jmh_precompile.PrecompileError as error:
            raise T3EvidenceError(
                f"SEALED_REGULAR_FILE_CAPTURE_INVALID:{label}"
            ) from error
        self._regular_files[path] = (captured, label)
        return captured

    def adopt_prevalidated_file(
        self,
        path: Path,
        *,
        root: Path,
        label: str,
        payload: bytes,
        sha256: str,
        file_identity: tuple[int, int, int, int, int, int, int, int, int],
    ) -> SealedArtifact:
        """open/fstat+hash closure가 이미 검증한 동일 bytes를 중복 재개방 없이 봉인한다."""

        canonical_root = self._canonical_root(root)
        try:
            relative = path.relative_to(canonical_root)
        except ValueError as error:
            raise T3EvidenceError(
                f"SEALED_EVIDENCE_OUTSIDE_ROOT:{label}"
            ) from error
        if (
            not relative.parts
            or any(part in {"", ".", ".."} for part in relative.parts)
            or path.is_symlink()
            or file_identity[3] != 1
            or len(payload) != file_identity[6]
            or sha256_bytes(payload) != sha256
        ):
            raise T3EvidenceError(
                f"SEALED_EVIDENCE_PREVALIDATED_INVALID:{label}"
            )
        identity = (
            file_identity[0],
            file_identity[1],
            file_identity[2],
            file_identity[6],
            file_identity[7],
            file_identity[8],
        )
        existing = self._artifacts.get(path)
        if existing is not None:
            if (
                existing.root != canonical_root
                or not existing.identity_only_postcheck
                or existing.identity != identity
                or existing.payload != payload
                or existing.sha256 != sha256
            ):
                raise T3EvidenceError(
                    f"SEALED_EVIDENCE_PREVALIDATED_DRIFT:{label}"
                )
            return existing
        artifact = SealedArtifact(
            path=path,
            root=canonical_root,
            payload=payload,
            identity=identity,
            label=label,
            identity_only_postcheck=True,
        )
        self._artifacts[path] = artifact
        return artifact

    def json_value(self, path: Path, *, root: Path, label: str) -> Any:
        return self.capture(path, root=root, label=label).json_value()

    def json_object(self, path: Path, *, root: Path, label: str) -> dict[str, Any]:
        value = self.json_value(path, root=root, label=label)
        if not isinstance(value, dict):
            raise T3EvidenceError(f"JSON_OBJECT_REQUIRED:{label}")
        return value

    def verify_unchanged(self) -> None:
        """선택 종료 시 pathname이 capture inode/bytes에서 벗어나지 않았는지 확인한다."""

        for path, artifact in self._artifacts.items():
            descriptor, identity = self._open_lexical(
                path,
                root=self._root_for(path),
                label=artifact.label,
            )
            if artifact.identity_only_postcheck:
                os.close(descriptor)
                post_descriptor, post_identity = self._open_lexical(
                    path,
                    root=self._root_for(path),
                    label=artifact.label,
                )
                os.close(post_descriptor)
                pathname_identity = self._pathname_identity(
                    path,
                    label=artifact.label,
                )
                if (
                    identity != artifact.identity
                    or post_identity != artifact.identity
                    or pathname_identity != artifact.identity
                ):
                    raise T3EvidenceError(
                        f"SEALED_EVIDENCE_PATH_SUBSTITUTED:{artifact.label}"
                    )
                continue
            try:
                digest = hashlib.sha256()
                size = 0
                while True:
                    chunk = os.read(descriptor, 1024 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    digest.update(chunk)
            finally:
                os.close(descriptor)
            post_descriptor, post_identity = self._open_lexical(
                path,
                root=self._root_for(path),
                label=artifact.label,
            )
            os.close(post_descriptor)
            pathname_identity = self._pathname_identity(
                path,
                label=artifact.label,
            )
            if (
                identity != artifact.identity
                or post_identity != artifact.identity
                or pathname_identity != artifact.identity
                or size != len(artifact.payload)
                or digest.hexdigest() != artifact.sha256
            ):
                raise T3EvidenceError(
                    f"SEALED_EVIDENCE_PATH_SUBSTITUTED:{artifact.label}"
                )
        for path, (captured, label) in self._regular_files.items():
            try:
                if path in self._regular_files_content_verified:
                    jmh_precompile._verify_regular_file_identity(
                        captured,
                        label=label,
                    )
                else:
                    jmh_precompile._verify_regular_file_snapshot(
                        captured,
                        label=label,
                    )
                    self._regular_files_content_verified.add(path)
            except jmh_precompile.PrecompileError as error:
                raise T3EvidenceError(
                    f"SEALED_REGULAR_FILE_CHANGED:{label}"
                ) from error

    def _root_for(self, path: Path) -> Path:
        artifact = self._artifacts[path]
        return artifact.root


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise T3EvidenceError(f"DUPLICATE_JSON_KEY:{key}")
        result[key] = value
    return result


def strict_json_value(path: Path) -> Any:
    with path.open(encoding="utf-8") as stream:
        return json.load(
            stream,
            parse_constant=lambda token: (_ for _ in ()).throw(
                T3EvidenceError(f"NONFINITE_JSON:{token}")
            ),
            object_pairs_hook=reject_duplicate_keys,
        )


def strict_json(path: Path) -> dict[str, Any]:
    value = strict_json_value(path)
    if not isinstance(value, dict):
        raise T3EvidenceError(f"JSON_OBJECT_REQUIRED:{path}")
    return value


def write_exclusive_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    )
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(payload)


def require_sha(value: Any, field: str) -> str:
    if not isinstance(value, str) or SHA256.fullmatch(value) is None:
        raise T3EvidenceError(f"SHA256_REQUIRED:{field}")
    return value


def require_process_evidence(value: Any, field: str, *, expected_exit: int) -> None:
    if not isinstance(value, dict) or value.get("exitCode") != expected_exit:
        raise T3EvidenceError(f"PROCESS_EXIT_MISMATCH:{field}")
    for key in (
        "commandArgvSha256",
        "stdoutSha256",
        "stderrSha256",
        "evidenceSha256",
    ):
        require_sha(value.get(key), f"{field}.{key}")


def validate_semantic_receipt(
    receipt: dict[str, Any],
    *,
    policy: dict[str, Any],
    matrix: dict[str, Any],
    policy_sha256: str,
    manifest_sha256: str,
    source_tree_sha256: str,
    checked_files: list[str],
    scalafix_binary_sha256: str,
    rule_source_sha256: str,
) -> None:
    """Semantic receipt가 frozen policy와 negative matrix 전체에 exact 대응하는지 확인한다."""

    if (
        receipt.get("schemaVersion")
        != "s1.4x-scala-semantic-policy-receipt-v1"
        or receipt.get("status") != "PASS"
        or receipt.get("checkerMode") != "semanticdb"
        or receipt.get("semanticSmokeStatus") != "PASS"
        or receipt.get("policySha256") != policy_sha256
        or receipt.get("sourceInputManifestSha256") != manifest_sha256
        or receipt.get("sourceTreeSha256") != source_tree_sha256
        or receipt.get("checkedFiles") != checked_files
    ):
        raise T3EvidenceError("SEMANTIC_RECEIPT_IDENTITY_MISMATCH")
    if len(checked_files) != len(set(checked_files)) or not checked_files:
        raise T3EvidenceError("SEMANTIC_CHECKED_FILE_SET_INVALID")

    semanticdb = receipt.get("semanticdb")
    if (
        not isinstance(semanticdb, dict)
        or semanticdb.get("fileCount") != len(checked_files)
    ):
        raise T3EvidenceError("SEMANTICDB_FILE_COUNT_MISMATCH")
    for key in ("rootSha256", "classpathSha256", "compileCommandArgvSha256"):
        require_sha(semanticdb.get(key), f"semanticdb.{key}")

    scalafix = receipt.get("scalafix")
    rule = receipt.get("rule")
    execution = receipt.get("execution")
    if (
        not isinstance(scalafix, dict)
        or scalafix.get("version") != "0.14.7"
        or scalafix.get("binarySha256") != scalafix_binary_sha256
        or not isinstance(rule, dict)
        or rule.get("sourceSha256") != rule_source_sha256
        or not isinstance(execution, dict)
    ):
        raise T3EvidenceError("SEMANTIC_TOOL_IDENTITY_MISMATCH")
    for key in ("commandArgvSha256",):
        require_sha(scalafix.get(key), f"scalafix.{key}")
    command_links = {
        "cleanSyntactic": "syntacticCommandArgvSha256",
        "cleanExplicitResultTypes": "explicitResultTypesCommandArgvSha256",
        "cleanCustomSemanticRule": "customRuleCommandArgvSha256",
    }
    for execution_key, scalafix_key in command_links.items():
        process = execution.get(execution_key)
        require_process_evidence(process, execution_key, expected_exit=0)
        if process["commandArgvSha256"] != scalafix.get(scalafix_key):
            raise T3EvidenceError(f"SEMANTIC_COMMAND_LINK_MISMATCH:{execution_key}")
    require_sha(rule.get("classpathSha256"), "rule.classpathSha256")

    fixtures = matrix.get("fixtures")
    actual_negative = receipt.get("negativeMatrix")
    if (
        matrix.get("schemaVersion")
        != "s1.4x-scala-source-policy-negative-matrix-v1"
        or not isinstance(fixtures, list)
        or not isinstance(actual_negative, list)
        or len(fixtures) != len(actual_negative)
    ):
        raise T3EvidenceError("SEMANTIC_NEGATIVE_MATRIX_SIZE_MISMATCH")
    forbidden_symbols = set(policy.get("forbiddenFullyQualifiedSymbols", []))
    allowed_expected = forbidden_symbols | EXTRA_SEMANTIC_SYMBOLS
    if len({item.get("fixtureId") for item in fixtures}) != len(fixtures):
        raise T3EvidenceError("SEMANTIC_NEGATIVE_FIXTURE_DUPLICATE")

    for expected, actual in zip(fixtures, actual_negative, strict=True):
        expected_symbol = expected.get("expectedSymbol")
        disposition = expected.get("expectedDisposition")
        if expected_symbol not in allowed_expected:
            raise T3EvidenceError(f"SEMANTIC_EXPECTED_SYMBOL_OUTSIDE_POLICY:{expected_symbol}")
        if (
            actual.get("fixtureId") != expected.get("fixtureId")
            or actual.get("expectedPolicySymbol") != expected_symbol
            or actual.get("expectedDisposition") != disposition
            or actual.get("status") != "PASS"
            or type(actual.get("exitCode")) is not int
            or actual["exitCode"] == 0
        ):
            raise T3EvidenceError(
                f"SEMANTIC_NEGATIVE_IDENTITY_MISMATCH:{expected.get('fixtureId')}"
            )
        for key in (
            "commandArgvSha256",
            "stdoutSha256",
            "stderrSha256",
            "evidenceSha256",
        ):
            require_sha(actual.get(key), f"{expected.get('fixtureId')}.{key}")
        detected = actual.get("detectedResolvedSymbols")
        if not isinstance(detected, list):
            raise T3EvidenceError(
                f"SEMANTIC_RESOLVED_SYMBOL_LIST_MISSING:{expected.get('fixtureId')}"
            )
        if disposition == "SEMANTIC_REJECT" and expected_symbol != "ExplicitResultTypes":
            if not any(
                isinstance(item, dict)
                and item.get("policySymbol") == expected_symbol
                and isinstance(item.get("resolvedSymbol"), str)
                and item["resolvedSymbol"]
                for item in detected
            ):
                raise T3EvidenceError(
                    f"SEMANTIC_RESOLVED_SYMBOL_MISSING:{expected.get('fixtureId')}"
                )


def geometric_mean(values: list[float]) -> float:
    if not values or any(not math.isfinite(value) or value <= 0.0 for value in values):
        raise T3EvidenceError("POSITIVE_FINITE_RATIO_REQUIRED")
    return math.exp(sum(math.log(value) for value in values) / len(values))


def require_utc_timestamp(value: Any, field: str) -> dt.datetime:
    if (
        not isinstance(value, str)
        or re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z",
            value,
        )
        is None
    ):
        raise T3EvidenceError(f"UTC_TIMESTAMP_REQUIRED:{field}")
    try:
        parsed = dt.datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except ValueError as error:
        raise T3EvidenceError(f"UTC_TIMESTAMP_INVALID:{field}") from error
    return parsed


def require_portable_argv(value: Any, field: str) -> list[str]:
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, str) or not item for item in value)
        or value[0] in {"SCALA_CAPABILITY_EVIDENCE", "portable-command"}
        or any(
            re.search(r"(^/home/[^/]+|^/tmp(?:/|$)|^[A-Za-z]:\\\\)", item)
            for item in value
        )
    ):
        raise T3EvidenceError(f"PORTABLE_ARGV_REQUIRED:{field}")
    return value


def validate_correctness(correctness: dict[str, dict[str, Any]]) -> None:
    if tuple(correctness) != SCALA_PROFILES:
        raise T3EvidenceError("PROFILE_CORRECTNESS_ORDER_MISMATCH")
    manifest_hashes = set()
    compiler_hashes = set()
    profile_input_sets = set()
    expected_keys = {
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
    matrix_keys = {
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
    }
    for profile in SCALA_PROFILES:
        result = correctness[profile]
        profile_inputs = (
            result.get("profileRunInputPaths")
            if isinstance(result, dict)
            else None
        )
        if (
            not isinstance(result, dict)
            or set(result) != expected_keys
            or result.get("schemaVersion")
            != "s1.4x-scala-profile-correctness-v1"
            or result.get("profileId") != profile
            or result.get("status") != "PASS"
            or result.get("mismatchCount") != 0
            or not isinstance(result.get("matrix"), dict)
            or set(result["matrix"]) != matrix_keys
            or not isinstance(profile_inputs, list)
            or not profile_inputs
            or profile_inputs
            != sorted(profile_inputs, key=lambda value: value.encode("utf-8"))
            or len(profile_inputs) != len(set(profile_inputs))
        ):
            raise T3EvidenceError(f"PROFILE_CORRECTNESS_FAILED:{profile}")
        for key in (
            "compilerProfilesSha256",
            "profileOptionsSha256",
            "sourceInputManifestSha256",
            "toolchainLockSha256",
            "scalaCliBinarySha256",
            "candidateSha256",
        ):
            require_sha(result.get(key), f"{profile}.{key}")
        for key, value in result["matrix"].items():
            require_sha(value, f"{profile}.matrix.{key}")
        manifest_hashes.add(result["sourceInputManifestSha256"])
        compiler_hashes.add(result["compilerProfilesSha256"])
        profile_input_sets.add(tuple(profile_inputs))
    if len(manifest_hashes) != 1:
        raise T3EvidenceError("PROFILE_CORRECTNESS_SOURCE_DRIFT")
    if len(compiler_hashes) != 1 or len(profile_input_sets) != 1:
        raise T3EvidenceError("PROFILE_CORRECTNESS_TOOL_INPUT_DRIFT")


CORRECTNESS_LOCAL_ARTIFACTS = {
    "candidateResultSha256": ("canonical-results.json", True),
    "semanticResultSha256": ("semantic-errors.json", True),
    "unitTestResultSha256": (
        "scala-profile-unit-test-result.v1.json",
        True,
    ),
    "unitStdoutSha256": ("unit-test.stdout", False),
    "unitStderrSha256": ("unit-test.stderr", False),
    "canonicalComparisonSha256": ("canonical-comparison.json", True),
    "semanticComparisonSha256": ("semantic-comparison.json", True),
    "propertyReportSha256": (
        "property/scala-property-report.v1.json",
        True,
    ),
    "registryReportSha256": (
        "property/scala-registry-report.v1.json",
        True,
    ),
    "propertyExecutionEvidenceSha256": (
        "property/scala-property-execution-evidence.v1.json",
        True,
    ),
}
CORRECTNESS_FROZEN_ARTIFACTS = {
    "propertyPlanSha256": "contract/property-plan.v1.json",
    "propertySeedCorpusSha256": (
        "contract/fixtures/property/property-seeds.v1.json"
    ),
    "functionRegistrySha256": "contract/function-registry.v1.json",
    "errorRegistrySha256": "contract/error-registry.v1.json",
}


def validate_correctness_artifact_closure(
    *,
    correctness: dict[str, dict[str, Any]],
    correctness_sha256: dict[str, str],
    correctness_root: Path,
    scala_root: Path,
    snapshot: SealedEvidenceSnapshot,
) -> None:
    """A/B/C aggregate가 가리키는 candidate/raw/log/registry bytes를 전부 다시 연다."""

    if (
        not correctness_root.is_absolute()
        or correctness_root.is_symlink()
        or not correctness_root.is_dir()
    ):
        raise T3EvidenceError("PROFILE_CORRECTNESS_ROOT_INVALID")
    s1_root = scala_root.parent
    for profile in SCALA_PROFILES:
        profile_root = correctness_root / profile
        aggregate_path = (
            profile_root / "scala-profile-correctness-result.v1.json"
        )
        aggregate = snapshot.json_object(
            aggregate_path,
            root=correctness_root,
            label=f"correctness.{profile}.aggregate",
        )
        if (
            aggregate != correctness[profile]
            or snapshot.sha256(
                aggregate_path,
                root=correctness_root,
                label=f"correctness.{profile}.aggregate",
            )
            != correctness_sha256[profile]
        ):
            raise T3EvidenceError(
                f"PROFILE_CORRECTNESS_AGGREGATE_BYTE_DRIFT:{profile}"
            )
        matrix = aggregate["matrix"]
        for field, (relative, parse_json) in CORRECTNESS_LOCAL_ARTIFACTS.items():
            path = profile_root / relative
            artifact = snapshot.capture(
                path,
                root=correctness_root,
                label=f"correctness.{profile}.{field}",
            )
            if artifact.sha256 != matrix[field]:
                raise T3EvidenceError(
                    f"PROFILE_CORRECTNESS_RAW_HASH_DRIFT:{profile}:{field}"
                )
            if parse_json:
                value = artifact.json_value()
                if not isinstance(value, dict):
                    raise T3EvidenceError(
                        f"PROFILE_CORRECTNESS_RAW_OBJECT_REQUIRED:{profile}:{field}"
                    )

        candidate = snapshot.capture(
            profile_root / "candidate.jar",
            root=correctness_root,
            label=f"correctness.{profile}.candidate",
        )
        if candidate.sha256 != aggregate["candidateSha256"]:
            raise T3EvidenceError(
                f"PROFILE_CORRECTNESS_CANDIDATE_DRIFT:{profile}"
            )

        unit = snapshot.json_object(
            profile_root / "scala-profile-unit-test-result.v1.json",
            root=correctness_root,
            label=f"correctness.{profile}.unit",
        )
        canonical_result = snapshot.json_object(
            profile_root / "canonical-results.json",
            root=correctness_root,
            label=f"correctness.{profile}.canonicalResult",
        )
        semantic_result = snapshot.json_object(
            profile_root / "semantic-errors.json",
            root=correctness_root,
            label=f"correctness.{profile}.semanticResult",
        )
        canonical_comparison = snapshot.json_object(
            profile_root / "canonical-comparison.json",
            root=correctness_root,
            label=f"correctness.{profile}.canonicalComparison",
        )
        semantic_comparison = snapshot.json_object(
            profile_root / "semantic-comparison.json",
            root=correctness_root,
            label=f"correctness.{profile}.semanticComparison",
        )
        property_report = snapshot.json_object(
            profile_root / "property/scala-property-report.v1.json",
            root=correctness_root,
            label=f"correctness.{profile}.propertyReport",
        )
        registry_report = snapshot.json_object(
            profile_root / "property/scala-registry-report.v1.json",
            root=correctness_root,
            label=f"correctness.{profile}.registryReport",
        )
        property_execution = snapshot.json_object(
            profile_root
            / "property/scala-property-execution-evidence.v1.json",
            root=correctness_root,
            label=f"correctness.{profile}.propertyExecution",
        )
        if (
            unit.get("profileId") != profile
            or unit.get("status") != "PASS"
            or unit.get("exitCode") != 0
            or unit.get("stdoutSha256") != matrix["unitStdoutSha256"]
            or unit.get("stderrSha256") != matrix["unitStderrSha256"]
            or canonical_result.get("implementation")
            != "scala-3.8.4-jvm25"
            or canonical_result.get("requestId")
            != "s1.4x-canonical-small-v1"
            or not isinstance(canonical_result.get("results"), list)
            or semantic_result.get("implementation")
            != "scala-3.8.4-jvm25"
            or semantic_result.get("requestId")
            != "s1.4x-semantic-errors-v1"
            or not isinstance(semantic_result.get("results"), list)
            or canonical_comparison.get("status") != "PASS"
            or canonical_comparison.get("mismatchCount") != 0
            or semantic_comparison.get("status") != "PASS"
            or semantic_comparison.get("mismatchCount") != 0
            or property_report.get("status") != "PASS"
            or registry_report.get("status") != "PASS"
            or property_execution.get("status") != "PASS"
            or property_execution.get("toolchainProfile") != profile
        ):
            raise T3EvidenceError(
                f"PROFILE_CORRECTNESS_RAW_RECEIPT_INVALID:{profile}"
            )

        for field, relative in CORRECTNESS_FROZEN_ARTIFACTS.items():
            path = s1_root / relative
            if (
                snapshot.sha256(
                    path,
                    root=s1_root,
                    label=f"correctness.frozen.{field}",
                )
                != matrix[field]
            ):
                raise T3EvidenceError(
                    f"PROFILE_CORRECTNESS_FROZEN_HASH_DRIFT:{field}"
                )


JMH_RUN_RESULT_KEYS = {
    "schemaVersion",
    "profileId",
    "caseId",
    "logicalOperationsPerInvocation",
    "rawScoreNsPerInvocation",
    "normalizedScoreNsPerLogicalOperation",
    "runMode",
    "benchmarkPlanSha256",
    "sourceInputManifestSha256",
    "scalaCliBinarySha256",
    "scalaCliExecutionPathId",
    "compilerProfilesSha256",
    "profileOptionsSha256",
    "inputPaths",
    "portableArgv",
    "portableArgvSha256",
    "runtimeArgvSha256",
    "liveRuntimeArgvWitness",
    "liveRuntimeArgvWitnessSha256",
    "runtimeExecutionPathIdentities",
    "runtimeExecutionPathIdentitiesSha256",
    "liveExecutionPathIdentity",
    "liveExecutionPathIdentitySha256",
    "commandToolClosure",
    "commandToolClosureSha256",
    "environmentValuesSha256",
    "scalaWorkspacePathId",
    "rawNativeJsonSha256",
    "effectiveJvmArgsSha256",
    "jvmArgumentAllowlistSha256",
    "nativeValidationSha256",
    "measurementReadyMarkerSha256",
    "generatedJavaPrecompileReceiptSha256",
    "stdoutSha256",
    "stderrSha256",
    "exitCode",
    "status",
    "aggregateStatus",
}
SCALA_BENCHMARK_ENVIRONMENT_VALUES = {
    "COURSIER_CACHE": "CACHE_ROOT/coursier",
    "COURSIER_CONFIG_DIR": "SCALA_ISOLATION/coursier-config",
    "SCALA_CLI_CONFIG": "SCALA_ISOLATION/scala-cli-home/config.json",
    "SCALA_CLI_HOME": "SCALA_ISOLATION/scala-cli-home",
    "S1_4X_SCALA_WORKSPACE": "SCALA_WORKSPACE",
    "XDG_CONFIG_HOME": "SCALA_ISOLATION/xdg-config",
}


def command_tool_closure(
    *,
    scala_root: Path,
    scala_cli: Path,
    java_executable: Path,
    run_mode: str,
    snapshot: SealedEvidenceSnapshot,
) -> list[dict[str, str]]:
    """JMH shell이 실행하는 candidate-owned helper와 pinned JVM tool 순서를 고정한다."""

    paths: list[tuple[str, Path, Path]] = [
        ("SCALA_CLI_1_15_0", scala_cli, scala_cli.parent),
        (
            "TEMURIN_25_0_3_9_LTS/bin/java",
            java_executable,
            java_executable.parent.parent,
        ),
        (
            "TEMURIN_25_0_3_9_LTS/bin/javac",
            java_executable.parent / "javac",
            java_executable.parent.parent,
        ),
    ]
    if run_mode == "full":
        paths.append(
            (
                "SCALA_ROOT/tools/run-jmh-native-full.sh",
                scala_root / "tools/run-jmh-native-full.sh",
                scala_root,
            )
        )
    elif run_mode not in {"smoke", "qualification"}:
        raise T3EvidenceError("JMH_RUN_MODE_INVALID")
    paths.extend(
        [
            (
                "SCALA_ROOT/tools/run-jmh-native-smoke.sh",
                scala_root / "tools/run-jmh-native-smoke.sh",
                scala_root,
            ),
            (
                "SCALA_ROOT/tools/compile-benchmarks.sh",
                scala_root / "tools/compile-benchmarks.sh",
                scala_root,
            ),
            (
                "SCALA_ROOT/tools/assert-toolchain.sh",
                scala_root / "tools/assert-toolchain.sh",
                scala_root,
            ),
            (
                "SCALA_ROOT/tools/assert-compiler-profiles.sh",
                scala_root / "tools/assert-compiler-profiles.sh",
                scala_root,
            ),
            (
                "SCALA_ROOT/tools/check-jmh-plan-integrity.sh",
                scala_root / "tools/check-jmh-plan-integrity.sh",
                scala_root,
            ),
            (
                "SCALA_ROOT/tools/source_input_manifest.py",
                scala_root / "tools/source_input_manifest.py",
                scala_root,
            ),
            (
                "SCALA_ROOT/tools/precompile_jmh_generated_java.py",
                scala_root / "tools/precompile_jmh_generated_java.py",
                scala_root,
            ),
            (
                "SCALA_ROOT/tools/t3_evidence.py",
                scala_root / "tools/t3_evidence.py",
                scala_root,
            ),
        ]
    )
    return [
        {
            "pathId": path_id,
            "sha256": snapshot.sha256(
                path,
                root=root,
                label=f"qualification.tool.{path_id}",
            ),
        }
        for path_id, path, root in paths
    ]


def runtime_execution_path_identities(
    *,
    scala_cli: Path,
    java_executable: Path,
    scala_cli_execution_path_id: str,
    snapshot: SealedEvidenceSnapshot,
) -> list[dict[str, str]]:
    """죽은 proc pathname 대신 실행 tool의 portable path ID와 bytes를 고정한다."""

    if scala_cli_execution_path_id not in {
        "SCALA_CLI_1_15_0",
        "PINNED_SCALA_CLI_1_15_0_FD",
    }:
        raise T3EvidenceError("SCALA_CLI_EXECUTION_PATH_ID_INVALID")
    values = [
        (
            scala_cli_execution_path_id,
            "SCALA_CLI_1_15_0",
            scala_cli,
            scala_cli.parent,
        ),
        (
            "PINNED_JAVA_FD",
            "TEMURIN_25_0_3_9_LTS/bin/java",
            java_executable,
            java_executable.parent.parent,
        ),
        (
            "PINNED_JAVAC_FD",
            "TEMURIN_25_0_3_9_LTS/bin/javac",
            java_executable.parent / "javac",
            java_executable.parent.parent,
        ),
    ]
    return [
        {
            "executionPathId": execution_path_id,
            "binaryPathId": binary_path_id,
            "binarySha256": snapshot.sha256(
                path,
                root=root,
                label=f"qualification.runtimeTool.{binary_path_id}",
            ),
        }
        for execution_path_id, binary_path_id, path, root in values
    ]


def _executable_file_identity(path: Path) -> dict[str, int]:
    """현재 canonical executable의 stat identity를 portable field로 만든다."""

    try:
        metadata = os.stat(path, follow_symlinks=False)
    except OSError as error:
        raise T3EvidenceError("LIVE_RUNTIME_BINARY_STAT_FAILED") from error
    if (
        path.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or metadata.st_mode & 0o111 == 0
    ):
        raise T3EvidenceError("LIVE_RUNTIME_BINARY_IDENTITY_INVALID")
    return {
        "device": metadata.st_dev,
        "inode": metadata.st_ino,
        "mode": metadata.st_mode,
        "linkCount": metadata.st_nlink,
        "uid": metadata.st_uid,
        "gid": metadata.st_gid,
        "size": metadata.st_size,
        "mtimeNs": metadata.st_mtime_ns,
        "ctimeNs": metadata.st_ctime_ns,
    }


def validate_live_runtime_execution_witness(
    *,
    run: dict[str, Any],
    expected_normalized_argv: list[str],
    expected_execution_path_identities: list[dict[str, str]],
    scala_cli: Path,
    java_executable: Path,
    snapshot: SealedEvidenceSnapshot,
) -> None:
    """종료된 proc FD를 재개방하지 않고 당시 실행 identity 증언을 검증한다."""

    stable_identity_keys = {
        "executionPathId",
        "binaryPathId",
        "binarySha256",
    }
    live_identity_keys = stable_identity_keys | {
        "procOwnerPid",
        "procOwnerStartTimeTicks",
        "procFd",
        "runtimePathSha256",
        "fileIdentity",
    }
    file_identity_keys = {
        "device",
        "inode",
        "mode",
        "linkCount",
        "uid",
        "gid",
        "size",
        "mtimeNs",
        "ctimeNs",
    }
    witness_keys = {
        "schemaVersion",
        "normalizedArgv",
        "normalizedArgvSha256",
        "physicalArgvSha256",
        "physicalExecutionPaths",
        "status",
    }
    physical_path_keys = {
        "argvIndex",
        "executionPathId",
        "pathSha256",
    }
    if (
        len(expected_execution_path_identities) != 3
        or any(
            not isinstance(identity, dict)
            or set(identity) != stable_identity_keys
            for identity in expected_execution_path_identities
        )
    ):
        raise T3EvidenceError("LIVE_RUNTIME_EXPECTED_IDENTITY_INVALID")
    scala_cli_execution_path_id = expected_execution_path_identities[0].get(
        "executionPathId"
    )
    independently_expected = runtime_execution_path_identities(
        scala_cli=scala_cli,
        java_executable=java_executable,
        scala_cli_execution_path_id=str(scala_cli_execution_path_id),
        snapshot=snapshot,
    )
    stable_identities = run.get("runtimeExecutionPathIdentities")
    if (
        expected_execution_path_identities != independently_expected
        or stable_identities != independently_expected
        or run.get("runtimeExecutionPathIdentitiesSha256")
        != canonical_sha256(independently_expected)
    ):
        raise T3EvidenceError("LIVE_RUNTIME_STABLE_IDENTITY_DRIFT")

    live_identities = run.get("liveExecutionPathIdentity")
    binary_paths = [
        scala_cli,
        java_executable,
        java_executable.parent / "javac",
    ]
    if (
        not isinstance(live_identities, list)
        or len(live_identities) != len(independently_expected)
    ):
        raise T3EvidenceError("LIVE_RUNTIME_IDENTITY_WITNESS_INVALID")
    proc_owners: set[tuple[int, int]] = set()
    proc_fds: set[int] = set()
    for live, stable, binary_path in zip(
        live_identities,
        independently_expected,
        binary_paths,
        strict=True,
    ):
        if (
            not isinstance(live, dict)
            or set(live) != live_identity_keys
            or any(live.get(key) != value for key, value in stable.items())
            or type(live.get("procOwnerPid")) is not int
            or live["procOwnerPid"] <= 0
            or type(live.get("procOwnerStartTimeTicks")) is not int
            or live["procOwnerStartTimeTicks"] <= 0
            or type(live.get("procFd")) is not int
            or live["procFd"] < 0
            or SHA256.fullmatch(str(live.get("runtimePathSha256")))
            is None
            or not isinstance(live.get("fileIdentity"), dict)
            or set(live["fileIdentity"]) != file_identity_keys
            or any(
                type(value) is not int
                for value in live["fileIdentity"].values()
            )
            or live["fileIdentity"]
            != _executable_file_identity(binary_path)
        ):
            raise T3EvidenceError("LIVE_RUNTIME_IDENTITY_WITNESS_DRIFT")
        proc_owners.add(
            (
                live["procOwnerPid"],
                live["procOwnerStartTimeTicks"],
            )
        )
        proc_fds.add(live["procFd"])
    if len(proc_owners) != 1 or len(proc_fds) != len(live_identities):
        raise T3EvidenceError("LIVE_RUNTIME_PROC_FD_CLOSURE_DRIFT")
    if run.get("liveExecutionPathIdentitySha256") != canonical_sha256(
        live_identities
    ):
        raise T3EvidenceError("LIVE_RUNTIME_IDENTITY_HASH_DRIFT")

    witness = run.get("liveRuntimeArgvWitness")
    if not isinstance(witness, dict) or set(witness) != witness_keys:
        raise T3EvidenceError("LIVE_RUNTIME_ARGV_WITNESS_INVALID")
    normalized_argv = witness.get("normalizedArgv")
    physical_paths = witness.get("physicalExecutionPaths")
    try:
        java_index = expected_normalized_argv.index("PINNED_JAVA_FD")
    except ValueError as error:
        raise T3EvidenceError(
            "LIVE_RUNTIME_NORMALIZED_JAVA_PATH_MISSING"
        ) from error
    expected_physical_paths = [
        {
            "argvIndex": 0,
            "executionPathId": independently_expected[0][
                "executionPathId"
            ],
            "pathSha256": live_identities[0]["runtimePathSha256"],
        },
        {
            "argvIndex": java_index,
            "executionPathId": "PINNED_JAVA_FD",
            "pathSha256": live_identities[1]["runtimePathSha256"],
        },
    ]
    if (
        witness.get("schemaVersion")
        != "s1.4x-scala-live-runtime-argv-witness-v1"
        or witness.get("status") != "PASS"
        or not isinstance(normalized_argv, list)
        or any(type(item) is not str for item in normalized_argv)
        or normalized_argv != expected_normalized_argv
        or witness.get("normalizedArgvSha256")
        != canonical_sha256(expected_normalized_argv)
        or run.get("runtimeArgvSha256")
        != canonical_sha256(expected_normalized_argv)
        or SHA256.fullmatch(str(witness.get("physicalArgvSha256")))
        is None
        or not isinstance(physical_paths, list)
        or any(
            not isinstance(item, dict)
            or set(item) != physical_path_keys
            for item in physical_paths
        )
        or physical_paths != expected_physical_paths
    ):
        raise T3EvidenceError("LIVE_RUNTIME_ARGV_WITNESS_DRIFT")
    if run.get("liveRuntimeArgvWitnessSha256") != canonical_sha256(witness):
        raise T3EvidenceError("LIVE_RUNTIME_ARGV_WITNESS_HASH_DRIFT")


HOST_VALIDITY_CHECK_IDS = {
    "disk.home-free-bytes",
    "memory.available-bytes",
    "cpu.logical-count",
    "cpu.affinity-round-trip",
    "docker.running-containers",
    "load.normalized-load1-window",
    "process.external-cpu",
}


def safe_artifact(root: Path, relative: Path) -> Path:
    if not root.is_absolute() or not root.is_dir() or root.is_symlink():
        raise T3EvidenceError("QUALIFICATION_ARTIFACT_ROOT_INVALID")
    if (
        relative.is_absolute()
        or not relative.parts
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise T3EvidenceError(f"QUALIFICATION_ARTIFACT_MISSING:{relative}")
    candidate = root / relative
    if (
        candidate.is_symlink()
        or not candidate.is_file()
    ):
        raise T3EvidenceError(f"QUALIFICATION_ARTIFACT_MISSING:{relative}")
    return candidate


def validate_measurement_ready_marker(
    path: Path,
    *,
    expected_benchmark_plan_sha256: str,
    expected_case_id: str,
    expected_profile: str,
    expected_run_mode: str,
    snapshot: SealedEvidenceSnapshot | None = None,
    artifact_root: Path | None = None,
) -> str:
    require_sha(
        expected_benchmark_plan_sha256,
        "measurementReady.benchmarkPlanSha256",
    )
    if snapshot is None:
        marker = strict_json(path)
        marker_sha256 = sha256_file(path)
    else:
        if artifact_root is None:
            raise T3EvidenceError("MEASUREMENT_READY_ROOT_REQUIRED")
        marker = snapshot.json_object(
            path,
            root=artifact_root,
            label=(
                "qualification.measurementReady."
                f"{expected_profile}.{expected_case_id}"
            ),
        )
        marker_sha256 = snapshot.sha256(
            path,
            root=artifact_root,
            label=(
                "qualification.measurementReady."
                f"{expected_profile}.{expected_case_id}"
            ),
        )
    if (
        path.is_symlink()
        or set(marker)
        != {
            "schemaVersion",
            "benchmarkPlanSha256",
            "caseId",
            "profileId",
            "runMode",
            "setupStatus",
            "markerCardinality",
        }
        or marker.get("schemaVersion")
        != "s1.4x-scala-measurement-ready-v1"
        or marker.get("benchmarkPlanSha256")
        != expected_benchmark_plan_sha256
        or marker.get("caseId") != expected_case_id
        or marker.get("profileId") != expected_profile
        or marker.get("runMode") != expected_run_mode
        or marker.get("setupStatus") != "PASS"
        or type(marker.get("markerCardinality")) is not int
        or marker.get("markerCardinality") != 1
    ):
        raise T3EvidenceError("MEASUREMENT_READY_MARKER_INVALID")
    return marker_sha256


def _classpath_log_binding(
    raw: str,
    *,
    receipt: dict[str, Any],
    physical_case_root: Path,
) -> tuple[str, str, str]:
    """Generator/classpath stdout 3행을 receipt portable path 순서로 해석한다."""

    if "\x00" in raw or "\r" in raw:
        raise T3EvidenceError("JMH_PRECOMPILE_STDOUT_BINDING_INVALID")
    lines = raw.splitlines()
    if len(lines) != 3 or not raw.endswith("\n"):
        raise T3EvidenceError("JMH_PRECOMPILE_STDOUT_BINDING_INVALID")
    processing = re.fullmatch(
        r'Processing ([1-9][0-9]*) classes from (.+) '
        r'with "reflection" generator',
        lines[0],
    )
    generated = re.fullmatch(
        r"Writing out Java source to (.+) and resources to (.+)",
        lines[1],
    )
    generator = receipt.get("jmhGenerator")
    entries = receipt.get("classpathEntries")
    if (
        processing is None
        or generated is None
        or not isinstance(generator, dict)
        or not isinstance(entries, list)
        or int(processing.group(1)) != generator.get("processedClassCount")
    ):
        raise T3EvidenceError("JMH_PRECOMPILE_STDOUT_BINDING_INVALID")

    class_input = Path(processing.group(2))
    source_root = Path(generated.group(1))
    resource_root = Path(generated.group(2))
    class_input_id = str(generator.get("classInputPathId", ""))
    if not class_input_id.startswith("SCALA_WORKSPACE/"):
        raise T3EvidenceError("JMH_PRECOMPILE_STDOUT_BINDING_INVALID")
    class_input_relative = Path(
        class_input_id.removeprefix("SCALA_WORKSPACE/")
    )
    if (
        not class_input.is_absolute()
        or len(class_input_relative.parts) < 2
        or tuple(class_input.parts[-len(class_input_relative.parts) :])
        != class_input_relative.parts
    ):
        raise T3EvidenceError("JMH_PRECOMPILE_STDOUT_BINDING_INVALID")
    workspace = Path(
        *class_input.parts[: -len(class_input_relative.parts)]
    )
    if not workspace.is_absolute():
        raise T3EvidenceError("JMH_PRECOMPILE_STDOUT_BINDING_INVALID")

    def expected_path(path_id: str, actual: Path) -> tuple[Path, Path | None]:
        if path_id.startswith("SCALA_WORKSPACE/"):
            return (
                workspace / path_id.removeprefix("SCALA_WORKSPACE/"),
                None,
            )
        if path_id.startswith("EVIDENCE_ROOT/"):
            return (
                physical_case_root
                / path_id.removeprefix("EVIDENCE_ROOT/"),
                None,
            )
        if path_id.startswith("COURSIER_CACHE/"):
            relative = Path(path_id.removeprefix("COURSIER_CACHE/"))
            if (
                not actual.is_absolute()
                or len(actual.parts) <= len(relative.parts)
                or tuple(actual.parts[-len(relative.parts) :])
                != relative.parts
            ):
                raise T3EvidenceError(
                    "JMH_PRECOMPILE_STDOUT_BINDING_INVALID"
                )
            root = Path(*actual.parts[: -len(relative.parts)])
            return root / relative, root
        raise T3EvidenceError("JMH_PRECOMPILE_STDOUT_BINDING_INVALID")

    for actual, field in (
        (class_input, "classInputPathId"),
        (source_root, "generatedSourceRootPathId"),
        (resource_root, "generatedResourceRootPathId"),
    ):
        expected, _ = expected_path(str(generator.get(field, "")), actual)
        if actual != expected:
            raise T3EvidenceError("JMH_PRECOMPILE_STDOUT_BINDING_INVALID")

    raw_paths = lines[2].split(os.pathsep)
    if len(raw_paths) != len(entries):
        raise T3EvidenceError("JMH_PRECOMPILE_STDOUT_BINDING_INVALID")
    coursier_root: Path | None = None
    for raw_path, item in zip(raw_paths, entries, strict=True):
        if not isinstance(item, dict):
            raise T3EvidenceError("JMH_PRECOMPILE_STDOUT_BINDING_INVALID")
        actual = Path(raw_path)
        expected, observed_coursier = expected_path(
            str(item.get("pathId", "")),
            actual,
        )
        if actual != expected:
            raise T3EvidenceError("JMH_PRECOMPILE_STDOUT_BINDING_INVALID")
        if observed_coursier is not None:
            if coursier_root is None:
                coursier_root = observed_coursier
            elif coursier_root != observed_coursier:
                raise T3EvidenceError(
                    "JMH_PRECOMPILE_STDOUT_BINDING_INVALID"
                )
    return lines[0], lines[1], lines[2]


def validate_jmh_stdout_precompile_binding(
    *,
    compile_stdout: Path,
    jmh_stdout: Path,
    receipt: dict[str, Any],
    fork_evidence: Any,
    artifact_root: Path,
    case_root: Path,
    snapshot: SealedEvidenceSnapshot,
) -> None:
    """Actual JMH build-role과 fork classpath를 finalized receipt에 결속한다."""

    compile_artifact = snapshot.capture(
        compile_stdout,
        root=artifact_root,
        label=f"qualification.precompile.stdoutBinding.{case_root}",
    )
    jmh_artifact = snapshot.capture(
        jmh_stdout,
        root=artifact_root,
        label=f"qualification.jmh.stdoutBinding.{case_root}",
    )
    try:
        compile_raw = compile_artifact.payload.decode("utf-8")
        jmh_raw = jmh_artifact.payload.decode("utf-8")
    except UnicodeError as error:
        raise T3EvidenceError("JMH_RUN_STDOUT_BINDING_INVALID") from error
    prefix = _classpath_log_binding(
        compile_raw,
        receipt=receipt,
        physical_case_root=artifact_root / case_root,
    )
    runtime_closure = receipt.get("jmhRuntimeClosure")
    if (
        receipt.get("jmhRuntimeClosureSha256")
        != canonical_sha256(runtime_closure)
        or receipt.get("precompileRuntimeClasspathSha256")
        != hashlib.sha256(prefix[2].encode("utf-8")).hexdigest()
    ):
        raise T3EvidenceError("JMH_RUN_STDOUT_BINDING_INVALID")
    try:
        jmh_precompile.validate_jmh_runtime_closure_evidence(
            runtime_closure,
            classpath_entries=receipt.get("classpathEntries"),
            classpath_post_run=receipt.get("classpathPostRun"),
            scala_class_output_path_id=receipt.get(
                "scalaClassOutputPathId"
            ),
            jmh_generator=receipt.get("jmhGenerator"),
            generated_sources_sha256=receipt.get(
                "generatedSourcesSha256"
            ),
        )
    except jmh_precompile.PrecompileError as error:
        raise T3EvidenceError(
            "JMH_RUN_STDOUT_BINDING_INVALID"
        ) from error

    if "\x00" in jmh_raw or "\r" in jmh_raw:
        raise T3EvidenceError("JMH_RUN_STDOUT_BINDING_INVALID")
    jmh_lines = jmh_raw.splitlines()
    if (
        len(jmh_lines) < 3
        or jmh_lines[2] != "# JMH version: 1.37"
    ):
        raise T3EvidenceError("JMH_RUN_STDOUT_BINDING_INVALID")
    processing = re.fullmatch(
        r'Processing ([1-9][0-9]*) classes from (.+) '
        r'with "reflection" generator',
        jmh_lines[0],
    )
    generated = re.fullmatch(
        r"Writing out Java source to (.+) and resources to (.+)",
        jmh_lines[1],
    )
    runtime_generator = (
        runtime_closure.get("generator")
        if isinstance(runtime_closure, dict)
        else None
    )
    runtime_entries = (
        runtime_closure.get("runtimeClasspathEntries")
        if isinstance(runtime_closure, dict)
        else None
    )
    if (
        processing is None
        or generated is None
        or not isinstance(runtime_generator, dict)
        or not isinstance(runtime_entries, list)
        or int(processing.group(1))
        != runtime_generator.get("processedClassCount")
    ):
        raise T3EvidenceError("JMH_RUN_STDOUT_BINDING_INVALID")

    class_input = Path(processing.group(2))
    source_root = Path(generated.group(1))
    resource_root = Path(generated.group(2))
    class_input_id = str(
        runtime_generator.get("classInputPathId", "")
    )
    if not class_input_id.startswith("SCALA_WORKSPACE/"):
        raise T3EvidenceError("JMH_RUN_STDOUT_BINDING_INVALID")
    class_input_relative = Path(
        class_input_id.removeprefix("SCALA_WORKSPACE/")
    )
    if (
        not class_input.is_absolute()
        or len(class_input_relative.parts) < 2
        or tuple(class_input.parts[-len(class_input_relative.parts) :])
        != class_input_relative.parts
    ):
        raise T3EvidenceError("JMH_RUN_STDOUT_BINDING_INVALID")
    runtime_workspace = Path(
        *class_input.parts[: -len(class_input_relative.parts)]
    )
    if not runtime_workspace.is_absolute():
        raise T3EvidenceError("JMH_RUN_STDOUT_BINDING_INVALID")

    def runtime_workspace_path(path_id: Any) -> Path:
        if (
            not isinstance(path_id, str)
            or not path_id.startswith("SCALA_WORKSPACE/")
        ):
            raise T3EvidenceError("JMH_RUN_STDOUT_BINDING_INVALID")
        raw_relative = path_id.removeprefix("SCALA_WORKSPACE/")
        relative = Path(raw_relative)
        if (
            not raw_relative
            or relative.is_absolute()
            or ".." in relative.parts
            or relative.as_posix() != raw_relative
        ):
            raise T3EvidenceError("JMH_RUN_STDOUT_BINDING_INVALID")
        return runtime_workspace / relative

    if (
        class_input != runtime_workspace_path(class_input_id)
        or source_root
        != runtime_workspace_path(
            runtime_generator.get("generatedSourceRootPathId")
        )
        or resource_root
        != runtime_workspace_path(
            runtime_generator.get("generatedResourceRootPathId")
        )
    ):
        raise T3EvidenceError("JMH_RUN_STDOUT_BINDING_INVALID")

    precompile_entries = receipt.get("classpathEntries")
    precompile_paths = prefix[2].split(os.pathsep)
    if (
        not isinstance(precompile_entries, list)
        or len(precompile_paths) != len(precompile_entries)
        or len(runtime_entries) != len(precompile_entries)
    ):
        raise T3EvidenceError("JMH_RUN_STDOUT_BINDING_INVALID")
    runtime_paths: list[str] = []
    for precompile_path, precompile_item, runtime_item in zip(
        precompile_paths,
        precompile_entries,
        runtime_entries,
        strict=True,
    ):
        if (
            not isinstance(precompile_item, dict)
            or not isinstance(runtime_item, dict)
        ):
            raise T3EvidenceError("JMH_RUN_STDOUT_BINDING_INVALID")
        precompile_path_id = precompile_item.get("pathId")
        runtime_path_id = runtime_item.get("pathId")
        if runtime_path_id == precompile_path_id:
            runtime_paths.append(precompile_path)
        else:
            runtime_paths.append(
                str(runtime_workspace_path(runtime_path_id))
            )
    runtime_classpath_sha256 = hashlib.sha256(
        os.pathsep.join(runtime_paths).encode("utf-8")
    ).hexdigest()
    if (
        runtime_closure.get("runtimeClasspathSha256")
        != runtime_classpath_sha256
        or receipt.get("runtimeClasspathSha256")
        != runtime_classpath_sha256
        or not isinstance(fork_evidence, list)
        or not fork_evidence
        or any(
            not isinstance(fork, dict)
            or fork.get("runtimeClasspathSha256")
            != runtime_classpath_sha256
            for fork in fork_evidence
        )
    ):
        raise T3EvidenceError("JMH_RUN_STDOUT_BINDING_INVALID")


def validate_generated_java_precompile(
    *,
    receipt_path: Path,
    artifact_root: Path,
    case_root: Path,
    profile: str,
    scala_root: Path,
    scala_cli: Path,
    source_input_paths: list[str],
    source_manifest_sha256: str,
    compiler_profiles_sha256: str,
    jdk_modules_snapshot: jmh_precompile.RegularFileSnapshot,
    snapshot: SealedEvidenceSnapshot,
) -> str:
    """Generated Java typed receipt와 sealed `.class` byte closure를 재검증한다."""

    receipt = snapshot.json_object(
        receipt_path,
        root=artifact_root,
        label=f"qualification.precompile.{profile}.{case_root.name}",
    )
    exact_fields = {
        "schemaVersion",
        "profileId",
        "sourceInputManifestSha256",
        "compilerProfilesSha256",
        "toolchainLockSha256",
        "scalaCli",
        "javac",
        "scalaCompile",
        "jmhGenerator",
        "generatedSourceRootPathId",
        "generatedSources",
        "generatedSourcesSha256",
        "generatedSourcesIdentitySha256",
        "classpathEntries",
        "classpathEntriesSha256",
        "classpathPostRun",
        "classpathPostRunSha256",
        "jmhRuntimeClosure",
        "jmhRuntimeClosureSha256",
        "precompileRuntimeClasspathSha256",
        "runtimeClasspathSha256",
        "scalaClassOutputPathId",
        "generatedClassOutputPathId",
        "generatedClasses",
        "generatedClassesSha256",
        "generatedClassesIdentitySha256",
        "javacProcess",
        "status",
        "aggregateStatus",
    }
    toolchain_path = scala_root / "toolchain-lock.v1.json"
    toolchain = snapshot.json_object(
        toolchain_path,
        root=scala_root,
        label="qualification.precompile.toolchain",
    )
    scala_cli_sha256 = snapshot.sha256(
        scala_cli,
        root=scala_cli.parent,
        label="qualification.precompile.scalaCli",
    )
    javac_sha256 = toolchain.get("jdk", {}).get("javacExecutableSha256")
    jdk_modules_path_id = toolchain.get("jdk", {}).get("jdkModulesPathId")
    jdk_modules_sha256 = toolchain.get("jdk", {}).get("jdkModulesSha256")
    if (
        set(receipt) != exact_fields
        or receipt.get("schemaVersion")
        != "s1.4x-scala-jmh-generated-java-precompile-v1"
        or receipt.get("profileId") != profile
        or receipt.get("sourceInputManifestSha256")
        != source_manifest_sha256
        or receipt.get("compilerProfilesSha256")
        != compiler_profiles_sha256
        or receipt.get("toolchainLockSha256")
        != snapshot.sha256(
            toolchain_path,
            root=scala_root,
            label="qualification.precompile.toolchain",
        )
        or receipt.get("status") != "PASS"
        or receipt.get("aggregateStatus") != "PASS"
        or jdk_modules_path_id != "TEMURIN_25_0_3_9_LTS/lib/modules"
        or SHA256.fullmatch(str(javac_sha256)) is None
        or SHA256.fullmatch(str(jdk_modules_sha256)) is None
        or receipt.get("scalaCli")
        != {
            "pathId": "SCALA_CLI_1_15_0",
            "binarySha256": scala_cli_sha256,
            "executionPathId": "PINNED_SCALA_CLI_1_15_0_FD",
        }
        or receipt.get("javac")
        != {
            "pathId": "TEMURIN_25_0_3_9_LTS/bin/javac",
            "binarySha256": javac_sha256,
            "executionPathId": "PINNED_JAVAC_FD",
            "jdkModulesPathId": jdk_modules_path_id,
            "jdkModulesSha256": jdk_modules_sha256,
            "jdkModulesFileIdentity": (
                jmh_precompile._file_identity_value(
                    jdk_modules_snapshot.file_identity
                )
            ),
        }
        or jdk_modules_snapshot.sha256 != jdk_modules_sha256
    ):
        raise T3EvidenceError("JMH_PRECOMPILE_RECEIPT_IDENTITY_INVALID")

    expected_compile = [
        "SCALA_CLI_1_15_0",
        "--power",
        "compile",
        *[f"SCALA_ROOT/{path}" for path in source_input_paths],
        "--workspace",
        "SCALA_WORKSPACE",
        "--server=false",
        "--classpath",
        f"EVIDENCE_ROOT/{jmh_precompile.GENERATED_CLASSES_NAME}",
        "--jvm",
        "system",
        "--coursier-validate-checksums",
        *PROFILE_CLI_ARGUMENTS[profile],
        "--jmh",
        "--jmh-version",
        "1.37",
        "--print-classpath",
    ]
    expected_compile_runtime = [
        "PINNED_SCALA_CLI_1_15_0_FD",
        *expected_compile[1:],
    ]
    compile_process = receipt.get("scalaCompile")
    compile_stdout = safe_artifact(
        artifact_root,
        case_root / jmh_precompile.SCALA_COMPILE_STDOUT,
    )
    compile_stderr = safe_artifact(
        artifact_root,
        case_root / jmh_precompile.SCALA_COMPILE_STDERR,
    )
    if (
        not isinstance(compile_process, dict)
        or set(compile_process)
        != {
            "portableArgv",
            "portableArgvSha256",
            "runtimeArgvSha256",
            "stdoutSha256",
            "stderrSha256",
            "exitCode",
            "status",
        }
        or compile_process.get("portableArgv") != expected_compile
        or compile_process.get("portableArgvSha256")
        != canonical_sha256(expected_compile)
        or compile_process.get("runtimeArgvSha256")
        != canonical_sha256(expected_compile_runtime)
        or compile_process.get("stdoutSha256")
        != snapshot.sha256(
            compile_stdout,
            root=artifact_root,
            label="qualification.precompile.scala.stdout",
        )
        or compile_process.get("stderrSha256")
        != snapshot.sha256(
            compile_stderr,
            root=artifact_root,
            label="qualification.precompile.scala.stderr",
        )
        or compile_process.get("exitCode") != 0
        or compile_process.get("status") != "PASS"
    ):
        raise T3EvidenceError("JMH_PRECOMPILE_SCALA_PROCESS_INVALID")
    try:
        compile_raw = snapshot.capture(
            compile_stdout,
            root=artifact_root,
            label="qualification.precompile.scala.stdout",
        ).payload.decode("utf-8")
    except UnicodeError as error:
        raise T3EvidenceError(
            "JMH_PRECOMPILE_STDOUT_BINDING_INVALID"
        ) from error
    compile_prefix = _classpath_log_binding(
        compile_raw,
        receipt=receipt,
        physical_case_root=artifact_root / case_root,
    )
    if receipt.get(
        "precompileRuntimeClasspathSha256"
    ) != hashlib.sha256(compile_prefix[2].encode("utf-8")).hexdigest():
        raise T3EvidenceError("JMH_PRECOMPILE_STDOUT_BINDING_INVALID")

    generated_sources = receipt.get("generatedSources")
    expected_source_paths = jmh_precompile.expected_generated_source_paths()
    generator = receipt.get("jmhGenerator")
    generated_source_root_id = receipt.get("generatedSourceRootPathId")
    generator_class_input = (
        str(generator.get("classInputPathId"))
        if isinstance(generator, dict)
        else ""
    )
    generator_build_id = generator_class_input.removesuffix("/classes/main")
    if (
        not isinstance(generator, dict)
        or set(generator)
        != {
            "generatorId",
            "processedClassCount",
            "classInputPathId",
            "generatedSourceRootPathId",
            "generatedResourceRootPathId",
            "classInputClosureSha256",
            "generatedResourceClosureSha256",
        }
        or generator.get("generatorId") != "reflection"
        or generator.get("processedClassCount")
        != jmh_precompile.EXPECTED_JMH_PROCESSED_CLASS_COUNT
        or SHA256.fullmatch(
            str(generator.get("classInputClosureSha256"))
        )
        is None
        or SHA256.fullmatch(
            str(generator.get("generatedResourceClosureSha256"))
        )
        is None
        or re.fullmatch(
            r"SCALA_WORKSPACE/\.scala-build/"
            r"(?P<build>[A-Za-z0-9._-]+)/classes/main",
            str(generator.get("classInputPathId")),
        )
        is None
        or generator.get("generatedSourceRootPathId")
        != f"{generator_build_id}_jmh/sources"
        or generated_source_root_id
        != f"EVIDENCE_ROOT/{jmh_precompile.GENERATED_SOURCES_NAME}"
        or generator.get("generatedResourceRootPathId")
        != f"{generator_build_id}_jmh/resources"
        or re.fullmatch(
            r"SCALA_WORKSPACE/\.scala-build/"
            r"[A-Za-z0-9._-]+_jmh/resources",
            str(generator.get("generatedResourceRootPathId")),
        )
        is None
        or not isinstance(generated_sources, list)
        or len(generated_sources) != len(expected_source_paths)
        or [item.get("path") for item in generated_sources]
        != list(expected_source_paths)
        or any(
            not isinstance(item, dict)
            or set(item) != {"path", "sha256"}
            or SHA256.fullmatch(str(item.get("sha256"))) is None
            for item in generated_sources
        )
        or receipt.get("generatedSourcesSha256")
        != canonical_sha256(generated_sources)
        or SHA256.fullmatch(
            str(receipt.get("generatedSourcesIdentitySha256"))
        )
        is None
    ):
        raise T3EvidenceError("JMH_PRECOMPILE_GENERATED_SOURCE_INVALID")

    generated_source_directory = (
        artifact_root / case_root / jmh_precompile.GENERATED_SOURCES_NAME
    )
    try:
        actual_generated_sources = (
            jmh_precompile.generated_source_closure_at(
                generated_source_directory,
                evidence_dir=artifact_root / case_root,
            )
        )
    except jmh_precompile.PrecompileError as error:
        raise T3EvidenceError(
            "JMH_PRECOMPILE_GENERATED_SOURCE_INVALID"
        ) from error
    actual_source_values = jmh_precompile._file_digest_values(
        actual_generated_sources.files
    )
    if (
        generated_sources != actual_source_values
        or receipt.get("generatedSourcesSha256")
        != canonical_sha256(actual_source_values)
        or receipt.get("generatedSourcesIdentitySha256")
        != jmh_precompile._file_identity_sha256(
            actual_generated_sources.files
        )
    ):
        raise T3EvidenceError("JMH_PRECOMPILE_GENERATED_SOURCE_INVALID")
    for item in actual_generated_sources.files:
        adopted = snapshot.adopt_prevalidated_file(
            generated_source_directory / item.relative_path,
            root=artifact_root,
            label=(
                "qualification.precompile.source."
                f"{item.relative_path}"
            ),
            payload=item.payload,
            sha256=item.sha256,
            file_identity=item.file_identity,
        )
        if adopted.sha256 != item.sha256:
            raise T3EvidenceError(
                "JMH_PRECOMPILE_GENERATED_SOURCE_INVALID"
            )

    classpath_entries = receipt.get("classpathEntries")
    class_output = receipt.get("scalaClassOutputPathId")
    generated_resource_id = (
        generator.get("generatedResourceRootPathId")
        if isinstance(generator, dict)
        else None
    )
    generated_class_output_id = (
        f"EVIDENCE_ROOT/{jmh_precompile.GENERATED_CLASSES_NAME}"
    )
    if (
        not isinstance(classpath_entries, list)
        or not classpath_entries
        or any(
            not isinstance(item, dict)
            or set(item)
            != {"pathId", "kind", "sha256", "identitySha256"}
            or item.get("kind") not in {"file", "directory"}
            or SHA256.fullmatch(str(item.get("sha256"))) is None
            or SHA256.fullmatch(
                str(item.get("identitySha256"))
            )
            is None
            or not str(item.get("pathId", "")).startswith(
                (
                    "SCALA_WORKSPACE/",
                    "COURSIER_CACHE/",
                    "EVIDENCE_ROOT/",
                )
            )
            for item in classpath_entries
        )
        or len({item["pathId"] for item in classpath_entries})
        != len(classpath_entries)
        or receipt.get("classpathEntriesSha256")
        != canonical_sha256(classpath_entries)
        or class_output
        not in {item["pathId"] for item in classpath_entries}
        or sum(
            item["pathId"] == generated_resource_id
            for item in classpath_entries
        )
        != 1
        or sum(
            item["pathId"] == generated_class_output_id
            for item in classpath_entries
        )
        != 1
        or any(
            item["kind"] != "directory"
            for item in classpath_entries
            if item["pathId"]
            in {
                class_output,
                generated_resource_id,
                generated_class_output_id,
            }
        )
        or any(
            item["pathId"].startswith("EVIDENCE_ROOT/")
            and item["pathId"] != generated_class_output_id
            for item in classpath_entries
        )
        or re.fullmatch(
            r"SCALA_WORKSPACE/\.scala-build/"
            r"[A-Za-z0-9._-]+_jmh_[0-9a-f]{10}/classes/main",
            str(class_output),
        )
        is None
    ):
        raise T3EvidenceError("JMH_PRECOMPILE_CLASSPATH_INVALID")
    classpath_post_run = receipt.get("classpathPostRun")
    if (
        receipt.get("classpathPostRunSha256")
        != canonical_sha256(classpath_post_run)
    ):
        raise T3EvidenceError(
            "JMH_PRECOMPILE_CLASSPATH_POST_RUN_INVALID"
        )
    try:
        jmh_precompile.validate_classpath_post_run_evidence(
            classpath_post_run,
            classpath_entries=classpath_entries,
            scala_class_output_path_id=class_output,
            generated_resource_path_id=generated_resource_id,
        )
    except jmh_precompile.PrecompileError as error:
        raise T3EvidenceError(
            "JMH_PRECOMPILE_CLASSPATH_POST_RUN_INVALID"
        ) from error
    runtime_closure = receipt.get("jmhRuntimeClosure")
    if (
        receipt.get("jmhRuntimeClosureSha256")
        != canonical_sha256(runtime_closure)
    ):
        raise T3EvidenceError(
            "JMH_PRECOMPILE_RUNTIME_CLOSURE_INVALID"
        )
    try:
        jmh_precompile.validate_jmh_runtime_closure_evidence(
            runtime_closure,
            classpath_entries=classpath_entries,
            classpath_post_run=classpath_post_run,
            scala_class_output_path_id=class_output,
            jmh_generator=generator,
            generated_sources_sha256=receipt.get(
                "generatedSourcesSha256"
            ),
        )
    except jmh_precompile.PrecompileError as error:
        raise T3EvidenceError(
            "JMH_PRECOMPILE_RUNTIME_CLOSURE_INVALID"
        ) from error
    if (
        not isinstance(runtime_closure, dict)
        or receipt.get("runtimeClasspathSha256")
        != runtime_closure.get("runtimeClasspathSha256")
    ):
        raise T3EvidenceError(
            "JMH_PRECOMPILE_RUNTIME_CLOSURE_INVALID"
        )

    generated_classes = receipt.get("generatedClasses")
    class_directory = artifact_root / case_root / jmh_precompile.GENERATED_CLASSES_NAME
    resolved_root = artifact_root.resolve(strict=True)
    if (
        class_directory.is_symlink()
        or not class_directory.is_dir()
        or not class_directory.resolve(strict=True).is_relative_to(
            resolved_root
        )
        or receipt.get("generatedClassOutputPathId")
        != generated_class_output_id
        or not isinstance(generated_classes, list)
        or not generated_classes
    ):
        raise T3EvidenceError("JMH_PRECOMPILE_CLASS_OUTPUT_INVALID")
    try:
        actual_class_closure = jmh_precompile._generated_class_closure(
            class_directory
        )
    except jmh_precompile.PrecompileError as error:
        raise T3EvidenceError(
            "JMH_PRECOMPILE_CLASS_BYTES_INVALID"
        ) from error
    actual_class_values = jmh_precompile._file_digest_values(
        actual_class_closure
    )
    for item in actual_class_closure:
        adopted = snapshot.adopt_prevalidated_file(
            class_directory / item.relative_path,
            root=artifact_root,
            label=f"qualification.precompile.class.{item.relative_path}",
            payload=item.payload,
            sha256=item.sha256,
            file_identity=item.file_identity,
        )
        if adopted.sha256 != item.sha256:
            raise T3EvidenceError("JMH_PRECOMPILE_CLASS_BYTES_INVALID")
    actual_class_paths = [
        item["path"] for item in actual_class_values
    ]
    if (
        generated_classes != actual_class_values
        or any(
            not isinstance(item, dict)
            or set(item) != {"path", "sha256"}
            or not item["path"].endswith(".class")
            for item in generated_classes
        )
        or receipt.get("generatedClassesSha256")
        != canonical_sha256(actual_class_values)
        or receipt.get("generatedClassesIdentitySha256")
        != jmh_precompile._file_identity_sha256(
            actual_class_closure
        )
        or next(
            item
            for item in classpath_entries
            if item["pathId"] == generated_class_output_id
        ).get("sha256")
        != receipt.get("generatedClassesSha256")
        or next(
            item
            for item in classpath_entries
            if item["pathId"] == generated_class_output_id
        ).get("identitySha256")
        != receipt.get("generatedClassesIdentitySha256")
        or any(
            f"{path.removesuffix('.java')}.class"
            not in actual_class_paths
            for path in expected_source_paths
        )
    ):
        raise T3EvidenceError("JMH_PRECOMPILE_CLASS_BYTES_INVALID")

    expected_javac = [
        "TEMURIN_25_0_3_9_LTS/bin/javac",
        "-encoding",
        "UTF-8",
        "-proc:none",
        "-classpath",
        "SCALA_COMPILE_CLASSPATH",
        "-d",
        f"EVIDENCE_ROOT/{jmh_precompile.GENERATED_CLASSES_NAME}",
        *[
            f"EVIDENCE_ROOT_GENERATED/{path}"
            for path in expected_source_paths
        ],
    ]
    expected_javac_runtime = ["PINNED_JAVAC_FD", *expected_javac[1:]]
    javac_process = receipt.get("javacProcess")
    javac_stdout = safe_artifact(
        artifact_root,
        case_root / jmh_precompile.JAVAC_STDOUT,
    )
    javac_stderr = safe_artifact(
        artifact_root,
        case_root / jmh_precompile.JAVAC_STDERR,
    )
    if (
        not isinstance(javac_process, dict)
        or set(javac_process)
        != {
            "portableArgv",
            "portableArgvSha256",
            "runtimeArgvSha256",
            "stdoutSha256",
            "stderrSha256",
            "exitCode",
            "status",
        }
        or javac_process.get("portableArgv") != expected_javac
        or javac_process.get("portableArgvSha256")
        != canonical_sha256(expected_javac)
        or javac_process.get("runtimeArgvSha256")
        != canonical_sha256(expected_javac_runtime)
        or javac_process.get("stdoutSha256")
        != snapshot.sha256(
            javac_stdout,
            root=artifact_root,
            label="qualification.precompile.javac.stdout",
        )
        or javac_process.get("stderrSha256")
        != snapshot.sha256(
            javac_stderr,
            root=artifact_root,
            label="qualification.precompile.javac.stderr",
        )
        or javac_process.get("exitCode") != 0
        or javac_process.get("status") != "PASS"
    ):
        raise T3EvidenceError("JMH_PRECOMPILE_JAVAC_PROCESS_INVALID")
    return snapshot.sha256(
        receipt_path,
        root=artifact_root,
        label=f"qualification.precompile.receipt.{profile}.{case_root.name}",
    )


def benchmark_case_contract(
    plan: dict[str, Any],
    case_id: str,
) -> tuple[str, int, str]:
    cases = [
        item
        for item in plan.get("cases", [])
        if isinstance(item, dict) and item.get("caseId") == case_id
    ]
    if len(cases) != 1:
        raise T3EvidenceError(f"BENCHMARK_CASE_IDENTITY_MISMATCH:{case_id}")
    family = cases[0].get("familyId")
    logical_operations = cases[0].get("logicalOperationsPerInvocation")
    selectors = [
        item
        for item in plan.get("familySelectors", [])
        if (
            isinstance(item, dict)
            and item.get("boundaryId") == "scala"
            and item.get("familyId") == family
        )
    ]
    if (
        family not in JMH_BENCHMARKS
        or type(logical_operations) is not int
        or logical_operations < 1
        or len(selectors) != 1
        or not isinstance(selectors[0].get("jmhIncludeRegex"), str)
    ):
        raise T3EvidenceError(f"BENCHMARK_CASE_CONTRACT_INVALID:{case_id}")
    return (
        JMH_BENCHMARKS[family],
        logical_operations,
        selectors[0]["jmhIncludeRegex"],
    )


def validate_host_validity_artifact(
    *,
    artifact_root: Path,
    repetition: int,
    profile: str,
    expected_sha256: str,
    plan: dict[str, Any],
    snapshot: SealedEvidenceSnapshot,
) -> None:
    path = safe_artifact(
        artifact_root,
        Path(f"r{repetition}") / profile / "host-validity.json",
    )
    report = snapshot.json_object(
        path,
        root=artifact_root,
        label=f"qualification.host.r{repetition}.{profile}",
    )
    policy = report.get("policy")
    checks = report.get("checks")
    frozen = plan.get("environmentValidity", {})
    expected_policy = {
        "cpu_set": plan.get("execution", {}).get("cpuSet"),
        "min_home_free_bytes": 32_212_254_720,
        "min_available_memory_bytes": (
            frozen.get("minAvailableMemoryGiB", -1) * 1024**3
        ),
        "max_normalized_load1": frozen.get("maxNormalizedLoad1"),
        "load_samples": frozen.get("loadSampleCount"),
        "sample_interval_seconds": frozen.get("loadSampleIntervalSeconds"),
        "max_quiet_wait_seconds": frozen.get("maxQuietWaitSeconds"),
        "max_running_containers": frozen.get("runningContainerCount"),
        "external_process_sample_seconds": 30,
        "max_external_process_cpu_percent": frozen.get(
            "externalProcessCpuPercentThreshold"
        ),
    }
    if (
        snapshot.sha256(
            path,
            root=artifact_root,
            label=f"qualification.host.r{repetition}.{profile}",
        )
        != expected_sha256
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
        or not isinstance(policy, dict)
        or {
            key: policy.get(key) for key in expected_policy
        }
        != expected_policy
        or type(policy.get("allowed_process_root_pid")) is not int
        or policy["allowed_process_root_pid"] <= 0
        or set(policy) != {*expected_policy, "allowed_process_root_pid"}
        or not isinstance(report.get("metadata"), dict)
        or not {"cpuGovernor", "temperature"}.issubset(report["metadata"])
        or not isinstance(checks, list)
        or len(checks) != len(HOST_VALIDITY_CHECK_IDS)
        or any(
            not isinstance(item, dict)
            or set(item)
            != {"id", "expected", "actual", "status", "evidence"}
            or item.get("status") != "PASS"
            for item in checks
        )
        or {item["id"] for item in checks} != HOST_VALIDITY_CHECK_IDS
    ):
        raise T3EvidenceError(
            f"PROFILE_HOST_VALIDITY_ARTIFACT_DRIFT:{repetition}:{profile}"
        )
    require_sha(report.get("portableHostIdSha256"), "portableHostIdSha256")


def validate_qualification_case_artifacts(
    *,
    plan: dict[str, Any],
    policy: dict[str, Any],
    artifact_root: Path,
    repetition: int,
    profile: str,
    case_index: int,
    case_id: str,
    measurement: dict[str, Any],
    scala_root: Path,
    scala_cli: Path,
    source_input_paths: list[str],
    source_manifest_sha256: str,
    compiler_profiles_sha256: str,
    benchmark_plan_sha256: str,
    jvm_allowlist: dict[str, Any],
    jvm_allowlist_sha256: str,
    jdk_modules_snapshot: jmh_precompile.RegularFileSnapshot,
    snapshot: SealedEvidenceSnapshot,
) -> float:
    """선택기가 각 raw JMH/fork/log/process byte를 다시 열어 score를 재구성한다."""

    case_root = (
        Path(f"r{repetition}") / profile / f"case-{case_index:02d}"
    )
    native_path = safe_artifact(artifact_root, case_root / "native.json")
    fork_path = safe_artifact(
        artifact_root,
        case_root / "fork-evidence.normalized.json",
    )
    effective_path = safe_artifact(
        artifact_root,
        case_root / "scala-effective-jvm-args-result.v1.json",
    )
    validation_path = safe_artifact(
        artifact_root,
        case_root / "scala-jmh-native-validation.v1.json",
    )
    run_path = safe_artifact(
        artifact_root,
        case_root / "scala-jmh-run-result.v1.json",
    )
    marker_path = safe_artifact(
        artifact_root,
        case_root / "measurement-ready.v1.json",
    )
    precompile_path = safe_artifact(
        artifact_root,
        case_root / jmh_precompile.RECEIPT_NAME,
    )
    stdout_path = safe_artifact(artifact_root, case_root / "jmh.stdout")
    stderr_path = safe_artifact(artifact_root, case_root / "jmh.stderr")

    effective = snapshot.json_object(
        effective_path,
        root=artifact_root,
        label=f"qualification.effective.r{repetition}.{profile}.{case_id}",
    )
    fork_evidence = snapshot.json_value(
        fork_path,
        root=artifact_root,
        label=f"qualification.fork.r{repetition}.{profile}.{case_id}",
    )
    recomputed_effective = validate_effective_jvm_evidence(
        fork_evidence,
        expected_forks=policy["forks"],
        allowlist=jvm_allowlist,
        allowlist_sha256=jvm_allowlist_sha256,
    )
    if effective != recomputed_effective:
        raise T3EvidenceError(
            f"QUALIFICATION_EFFECTIVE_JVM_DRIFT:{repetition}:{profile}:{case_id}"
        )

    benchmark, logical_operations, include_regex = benchmark_case_contract(
        plan,
        case_id,
    )
    recomputed_validation = validate_jmh_native_json(
        snapshot.json_value(
            native_path,
            root=artifact_root,
            label=f"qualification.native.r{repetition}.{profile}.{case_id}",
        ),
        expected_benchmark=benchmark,
        expected_forks=policy["forks"],
        effective_jvm_arguments=effective["effectiveJvmArguments"],
        expected_warmup_iterations=policy["warmupIterations"],
        expected_warmup_time=policy["warmupTime"],
        expected_measurement_iterations=policy["measurementIterations"],
        expected_measurement_time=policy["measurementTime"],
        logical_operations_per_invocation=logical_operations,
    )
    if (
        snapshot.json_object(
            validation_path,
            root=artifact_root,
            label=f"qualification.validation.r{repetition}.{profile}.{case_id}",
        )
        != recomputed_validation
    ):
        raise T3EvidenceError(
            f"QUALIFICATION_NATIVE_VALIDATION_DRIFT:{repetition}:{profile}:{case_id}"
        )

    portable_sources = [
        f"SCALA_ROOT/{path}" for path in source_input_paths
    ]
    common_tail = [
        "--workspace",
        "SCALA_WORKSPACE",
        "--server=false",
        "--classpath",
        f"EVIDENCE_ROOT/{jmh_precompile.GENERATED_CLASSES_NAME}",
        "--jvm",
        "system",
        "--coursier-validate-checksums",
        *PROFILE_CLI_ARGUMENTS[profile],
        "--java-prop",
        "java.io.tmpdir=EVIDENCE_ROOT/jmh-tmp",
        "--jmh",
        "--jmh-version",
        "1.37",
        "--",
        "-bm",
        "avgt",
        "-tu",
        "ns",
        "-t",
        "1",
        "-jvm",
        "PINNED_JAVA_FD",
        "-f",
        str(policy["forks"]),
        "-wi",
        str(policy["warmupIterations"]),
        "-i",
        str(policy["measurementIterations"]),
        "-w",
        policy["warmupTime"],
        "-r",
        policy["measurementTime"],
        "-rf",
        "json",
    ]
    expected_portable_argv = [
        "SCALA_CLI_1_15_0",
        "--power",
        "run",
        *portable_sources,
        *common_tail,
        "-rff",
        "EVIDENCE_ROOT/native.json",
        include_regex,
    ]
    scala_cli_execution_path_id = "PINNED_SCALA_CLI_1_15_0_FD"
    expected_runtime_identity_argv = [
        scala_cli_execution_path_id,
        *expected_portable_argv[1:],
    ]
    java_home_value = os.environ.get("JAVA_HOME")
    if not java_home_value:
        raise T3EvidenceError("JAVA_HOME_REQUIRED")
    expected_tool_closure = command_tool_closure(
        scala_root=scala_root,
        scala_cli=scala_cli,
        java_executable=Path(java_home_value) / "bin/java",
        run_mode="qualification",
        snapshot=snapshot,
    )
    expected_runtime_execution_identities = (
        runtime_execution_path_identities(
            scala_cli=scala_cli,
            java_executable=Path(java_home_value) / "bin/java",
            scala_cli_execution_path_id=scala_cli_execution_path_id,
            snapshot=snapshot,
        )
    )
    run = snapshot.json_object(
        run_path,
        root=artifact_root,
        label=f"qualification.run.r{repetition}.{profile}.{case_id}",
    )
    expected_score = recomputed_validation["rawScoreNsPerInvocation"]
    expected_normalized = recomputed_validation[
        "normalizedScoreNsPerLogicalOperation"
    ]
    marker_sha256 = validate_measurement_ready_marker(
        marker_path,
        expected_benchmark_plan_sha256=benchmark_plan_sha256,
        expected_case_id=case_id,
        expected_profile=profile,
        expected_run_mode="qualification",
        snapshot=snapshot,
        artifact_root=artifact_root,
    )
    precompile_sha256 = validate_generated_java_precompile(
        receipt_path=precompile_path,
        artifact_root=artifact_root,
        case_root=case_root,
        profile=profile,
        scala_root=scala_root,
        scala_cli=scala_cli,
        source_input_paths=source_input_paths,
        source_manifest_sha256=source_manifest_sha256,
        compiler_profiles_sha256=compiler_profiles_sha256,
        jdk_modules_snapshot=jdk_modules_snapshot,
        snapshot=snapshot,
    )
    validated_precompile_receipt = snapshot.json_object(
        precompile_path,
        root=artifact_root,
        label=(
            f"qualification.precompile.r{repetition}.{profile}.{case_id}"
        ),
    )
    validate_jmh_stdout_precompile_binding(
        compile_stdout=safe_artifact(
            artifact_root,
            case_root / jmh_precompile.SCALA_COMPILE_STDOUT,
        ),
        jmh_stdout=stdout_path,
        receipt=validated_precompile_receipt,
        fork_evidence=fork_evidence,
        artifact_root=artifact_root,
        case_root=case_root,
        snapshot=snapshot,
    )
    if (
        set(run) != JMH_RUN_RESULT_KEYS
        or run.get("schemaVersion") != "s1.4x-scala-jmh-run-result-v1"
        or run.get("profileId") != profile
        or run.get("caseId") != case_id
        or run.get("logicalOperationsPerInvocation") != logical_operations
        or run.get("rawScoreNsPerInvocation") != expected_score
        or run.get("normalizedScoreNsPerLogicalOperation")
        != expected_normalized
        or run.get("runMode") != "qualification"
        or run.get("benchmarkPlanSha256") != benchmark_plan_sha256
        or run.get("sourceInputManifestSha256")
        != source_manifest_sha256
        or run.get("scalaCliBinarySha256")
        != snapshot.sha256(
            scala_cli,
            root=scala_cli.parent,
            label="qualification.scalaCli",
        )
        or run.get("scalaCliExecutionPathId")
        != scala_cli_execution_path_id
        or run.get("compilerProfilesSha256")
        != compiler_profiles_sha256
        or run.get("profileOptionsSha256")
        != canonical_sha256(PROFILE_OPTIONS[profile])
        or run.get("inputPaths") != source_input_paths
        or run.get("portableArgv") != expected_portable_argv
        or run.get("portableArgvSha256")
        != canonical_sha256(expected_portable_argv)
        or run.get("runtimeArgvSha256")
        != canonical_sha256(expected_runtime_identity_argv)
        or run.get("commandToolClosure") != expected_tool_closure
        or run.get("commandToolClosureSha256")
        != canonical_sha256(expected_tool_closure)
        or run.get("environmentValuesSha256")
        != canonical_sha256(SCALA_BENCHMARK_ENVIRONMENT_VALUES)
        or run.get("scalaWorkspacePathId") != "SCALA_WORKSPACE"
        or run.get("rawNativeJsonSha256")
        != snapshot.sha256(
            native_path,
            root=artifact_root,
            label=f"qualification.native.r{repetition}.{profile}.{case_id}",
        )
        or run.get("effectiveJvmArgsSha256")
        != snapshot.sha256(
            effective_path,
            root=artifact_root,
            label=f"qualification.effective.r{repetition}.{profile}.{case_id}",
        )
        or run.get("jvmArgumentAllowlistSha256")
        != jvm_allowlist_sha256
        or run.get("nativeValidationSha256")
        != snapshot.sha256(
            validation_path,
            root=artifact_root,
            label=f"qualification.validation.r{repetition}.{profile}.{case_id}",
        )
        or run.get("measurementReadyMarkerSha256") != marker_sha256
        or run.get("generatedJavaPrecompileReceiptSha256")
        != precompile_sha256
        or run.get("stdoutSha256")
        != snapshot.sha256(
            stdout_path,
            root=artifact_root,
            label=f"qualification.stdout.r{repetition}.{profile}.{case_id}",
        )
        or run.get("stderrSha256")
        != snapshot.sha256(
            stderr_path,
            root=artifact_root,
            label=f"qualification.stderr.r{repetition}.{profile}.{case_id}",
        )
        or run.get("exitCode") != 0
        or run.get("status") != "PASS"
        or run.get("aggregateStatus") != "PASS"
    ):
        raise T3EvidenceError(
            f"QUALIFICATION_RUN_RECEIPT_DRIFT:{repetition}:{profile}:{case_id}"
        )
    validate_live_runtime_execution_witness(
        run=run,
        expected_normalized_argv=expected_runtime_identity_argv,
        expected_execution_path_identities=(
            expected_runtime_execution_identities
        ),
        scala_cli=scala_cli,
        java_executable=Path(java_home_value) / "bin/java",
        snapshot=snapshot,
    )
    require_portable_argv(
        run["portableArgv"],
        f"qualification.r{repetition}.{profile}.{case_id}",
    )
    if (
        measurement.get("scoreNsPerInvocation") != expected_score
        or measurement.get("rawNativeJsonSha256")
        != snapshot.sha256(
            native_path,
            root=artifact_root,
            label=f"qualification.native.r{repetition}.{profile}.{case_id}",
        )
        or measurement.get("effectiveJvmArgsSha256")
        != snapshot.sha256(
            effective_path,
            root=artifact_root,
            label=f"qualification.effective.r{repetition}.{profile}.{case_id}",
        )
        or measurement.get("jmhRunResultSha256")
        != snapshot.sha256(
            run_path,
            root=artifact_root,
            label=f"qualification.run.r{repetition}.{profile}.{case_id}",
        )
    ):
        raise T3EvidenceError(
            f"QUALIFICATION_MEASUREMENT_BYTE_DRIFT:{repetition}:{profile}:{case_id}"
        )
    return expected_score


def select_profile_from_scores(
    *,
    policy: dict[str, Any],
    block_count: int,
    case_order: list[str],
    scores: dict[tuple[int, str, str], float],
) -> tuple[dict[str, dict[str, Any]], str]:
    """Byte 검증 뒤의 frozen median/geometric-mean selector만 순수 계산한다."""

    profile_results: dict[str, dict[str, Any]] = {
        "A": {
            "aggregateRatioToA": 1.0,
            "maximumCaseRatio": 1.0,
            "improvingOuterRepetitions": block_count,
            "caseMedianRatiosToA": {
                case_id: 1.0 for case_id in case_order
            },
            "outerAggregateRatiosToA": [1.0 for _ in range(block_count)],
            "qualified": True,
        }
    }
    for profile in ("B", "C"):
        block_geometric_means = [
            geometric_mean(
                [
                    (
                        scores[(repetition, profile, case_id)]
                        / scores[(repetition, "A", case_id)]
                    )
                    for case_id in case_order
                ]
            )
            for repetition in range(1, block_count + 1)
        ]
        median_ratios = [
            statistics.median(
                [
                    scores[(repetition, profile, case_id)]
                    for repetition in range(1, block_count + 1)
                ]
            )
            / statistics.median(
                [
                    scores[(repetition, "A", case_id)]
                    for repetition in range(1, block_count + 1)
                ]
            )
            for case_id in case_order
        ]
        maximum_case_ratio = max(median_ratios)
        aggregate_ratio = geometric_mean(median_ratios)
        improving = sum(value < 1.0 for value in block_geometric_means)
        profile_results[profile] = {
            "aggregateRatioToA": aggregate_ratio,
            "maximumCaseRatio": maximum_case_ratio,
            "improvingOuterRepetitions": improving,
            "caseMedianRatiosToA": dict(
                zip(case_order, median_ratios, strict=True)
            ),
            "outerAggregateRatiosToA": block_geometric_means,
            "qualified": (
                maximum_case_ratio
                <= float(policy["perCaseMaxRegressionRatio"])
                and aggregate_ratio <= float(policy["aggregateMaxRatio"])
                and improving
                >= int(policy["minimumImprovingOuterRepetitions"])
            ),
        }

    b_qualified = profile_results["B"]["qualified"]
    c_qualified = profile_results["C"]["qualified"]
    c_over_b = (
        profile_results["C"]["aggregateRatioToA"]
        / profile_results["B"]["aggregateRatioToA"]
    )
    required_c_over_b = 1.0 - float(policy["cOverBMinimumImprovement"])
    if c_qualified and (not b_qualified or c_over_b <= required_c_over_b):
        selected = "C"
    elif b_qualified:
        selected = "B"
    elif c_qualified:
        selected = "C"
    else:
        selected = "A"
    profile_results["C"]["aggregateRatioToB"] = c_over_b
    return profile_results, selected


def selector_config_sha256(
    *,
    policy: dict[str, Any],
    benchmark_plan_sha256: str,
    blocks: Any,
) -> str:
    """Frozen selector policy와 실제 Latin/profile/case/raw artifact 순서를 함께 동결한다."""

    require_sha(benchmark_plan_sha256, "selector.benchmarkPlanSha256")
    if not isinstance(blocks, list):
        raise T3EvidenceError("SELECTOR_BLOCK_CLOSURE_INVALID")
    observed = []
    for block in blocks:
        if not isinstance(block, dict):
            raise T3EvidenceError("SELECTOR_BLOCK_CLOSURE_INVALID")
        profile_evidence = block.get("profileEvidence")
        measurements = block.get("measurements")
        if not isinstance(profile_evidence, list) or not isinstance(
            measurements,
            list,
        ):
            raise T3EvidenceError("SELECTOR_BLOCK_CLOSURE_INVALID")
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
                        "rawNativeJsonSha256": item.get(
                            "rawNativeJsonSha256"
                        ),
                        "effectiveJvmArgsSha256": item.get(
                            "effectiveJvmArgsSha256"
                        ),
                        "jmhRunResultSha256": item.get(
                            "jmhRunResultSha256"
                        ),
                    }
                    for item in measurements
                    if isinstance(item, dict)
                ],
            }
        )
    return canonical_sha256(
        {
            "benchmarkPlanSha256": benchmark_plan_sha256,
            "policy": policy,
            "observedLatinProfileCaseClosure": observed,
        }
    )


def select_scala_profile(
    *,
    plan: dict[str, Any],
    benchmark_plan_sha256: str,
    compiler_profiles: dict[str, Any],
    compiler_profiles_sha256: str,
    selected_profile_source_sha256: str,
    correctness: dict[str, dict[str, Any]],
    correctness_sha256: dict[str, str],
    correctness_artifact_root: Path,
    qualification: dict[str, Any],
    qualification_sha256: str,
    qualification_artifact_root: Path,
    source_manifest: dict[str, Any],
    source_manifest_sha256: str,
    scala_root: Path,
    scala_cli: Path,
    pinned_scala_cli_sha256: str,
    toolchain_lock_sha256: str,
    merged_toolchain_provenance_sha256: str,
    pinned_java_executable_sha256: str,
    capability_smoke_plan_sha256: str,
    jvm_allowlist: dict[str, Any],
    jvm_allowlist_sha256: str,
    jvm_allowlist_path: Path,
    evidence_snapshot: SealedEvidenceSnapshot | None = None,
) -> dict[str, Any]:
    """Frozen Latin JMH 결과만으로 B/C를 선택하고 실패 시 proven A로 닫는다."""

    snapshot = evidence_snapshot or SealedEvidenceSnapshot()
    benchmark_plan_path = (
        scala_root.parent / "benchmarks/benchmark-plan.v1.json"
    )
    compiler_profiles_path = scala_root / "compiler-profiles.v1.json"
    selected_source_path = scala_root / "selected-profile.scala"
    source_manifest_path = scala_root / "source-inputs.v1.json"
    toolchain_lock_path = scala_root / "toolchain-lock.v1.json"
    provenance_path = scala_root.parent / "contract/toolchain-provenance.v1.json"
    capability_plan_path = (
        scala_root.parent / "contract/capability-smoke-plan.v1.json"
    )
    qualification_path = (
        qualification_artifact_root
        / "scala-profile-qualification.v1.json"
    )
    if (
        snapshot.json_object(
            benchmark_plan_path,
            root=scala_root.parent,
            label="selector.benchmarkPlan",
        )
        != plan
        or snapshot.sha256(
            benchmark_plan_path,
            root=scala_root.parent,
            label="selector.benchmarkPlan",
        )
        != benchmark_plan_sha256
        or snapshot.json_object(
            compiler_profiles_path,
            root=scala_root,
            label="selector.compilerProfiles",
        )
        != compiler_profiles
        or snapshot.sha256(
            compiler_profiles_path,
            root=scala_root,
            label="selector.compilerProfiles",
        )
        != compiler_profiles_sha256
        or snapshot.sha256(
            selected_source_path,
            root=scala_root,
            label="selector.selectedProfileSource",
        )
        != selected_profile_source_sha256
        or snapshot.json_object(
            source_manifest_path,
            root=scala_root,
            label="selector.sourceManifest",
        )
        != source_manifest
        or snapshot.sha256(
            source_manifest_path,
            root=scala_root,
            label="selector.sourceManifest",
        )
        != source_manifest_sha256
        or snapshot.sha256(
            toolchain_lock_path,
            root=scala_root,
            label="selector.toolchainLock",
        )
        != toolchain_lock_sha256
        or snapshot.sha256(
            provenance_path,
            root=scala_root.parent,
            label="selector.toolchainProvenance",
        )
        != merged_toolchain_provenance_sha256
        or snapshot.sha256(
            capability_plan_path,
            root=scala_root.parent,
            label="selector.capabilityPlan",
        )
        != capability_smoke_plan_sha256
        or snapshot.json_object(
            qualification_path,
            root=qualification_artifact_root,
            label="selector.qualification",
        )
        != qualification
        or snapshot.sha256(
            qualification_path,
            root=qualification_artifact_root,
            label="selector.qualification",
        )
        != qualification_sha256
        or snapshot.json_object(
            jvm_allowlist_path,
            root=jvm_allowlist_path.parent,
            label="selector.jvmAllowlist",
        )
        != jvm_allowlist
        or snapshot.sha256(
            jvm_allowlist_path,
            root=jvm_allowlist_path.parent,
            label="selector.jvmAllowlist",
        )
        != jvm_allowlist_sha256
    ):
        raise T3EvidenceError("PROFILE_SELECTOR_TOP_LEVEL_BYTE_DRIFT")
    validate_correctness(correctness)
    validate_correctness_artifact_closure(
        correctness=correctness,
        correctness_sha256=correctness_sha256,
        correctness_root=correctness_artifact_root,
        scala_root=scala_root,
        snapshot=snapshot,
    )
    for field, value in (
        ("benchmarkPlanSha256", benchmark_plan_sha256),
        ("compilerProfilesSha256", compiler_profiles_sha256),
        ("selectedProfileSourceSha256", selected_profile_source_sha256),
        ("qualificationSha256", qualification_sha256),
        ("sourceInputManifestSha256", source_manifest_sha256),
        ("pinnedScalaCliSha256", pinned_scala_cli_sha256),
        ("toolchainLockSha256", toolchain_lock_sha256),
        (
            "mergedToolchainProvenanceSha256",
            merged_toolchain_provenance_sha256,
        ),
        ("pinnedJavaExecutableSha256", pinned_java_executable_sha256),
        ("capabilitySmokePlanSha256", capability_smoke_plan_sha256),
        ("jvmArgumentAllowlistSha256", jvm_allowlist_sha256),
    ):
        require_sha(value, field)
    if (
        set(correctness_sha256) != set(SCALA_PROFILES)
        or any(
            require_sha(correctness_sha256[profile], f"correctness.{profile}")
            is None
            for profile in SCALA_PROFILES
        )
        or not scala_root.is_absolute()
        or not scala_root.is_dir()
        or scala_root.is_symlink()
        or not scala_cli.is_absolute()
        or not scala_cli.is_file()
        or scala_cli.is_symlink()
        or snapshot.sha256(
            scala_cli,
            root=scala_cli.parent,
            label="selector.scalaCli",
        )
        != pinned_scala_cli_sha256
    ):
        raise T3EvidenceError("PROFILE_SELECTOR_LOCAL_INPUT_INVALID")
    java_home_value = os.environ.get("JAVA_HOME")
    if not java_home_value:
        raise T3EvidenceError("JAVA_HOME_REQUIRED")
    jdk_modules_path = Path(java_home_value) / "lib/modules"
    jdk_modules_snapshot: jmh_precompile.RegularFileSnapshot | None = None
    if (
        set(jvm_allowlist) != JVM_ALLOWLIST_KEYS
        or jvm_allowlist.get("schemaVersion")
        != "s1.4x-scala-jvm-argument-allowlist-v1"
        or jvm_allowlist.get("status") != "PASS"
        or jvm_allowlist.get("benchmarkPlanSha256")
        != benchmark_plan_sha256
        or jvm_allowlist.get("capabilitySmokePlanSha256")
        != capability_smoke_plan_sha256
        or jvm_allowlist.get("toolchainLockSha256")
        != toolchain_lock_sha256
        or jvm_allowlist.get("javaExecutableSha256")
        != pinned_java_executable_sha256
    ):
        raise T3EvidenceError("PROFILE_SELECTOR_JVM_ALLOWLIST_IDENTITY_MISMATCH")
    profile_contract = compiler_profiles.get("profiles")
    if (
        compiler_profiles.get("schemaVersion")
        != "s1.4x-scala-compiler-profiles-v1"
        or not isinstance(profile_contract, dict)
        or tuple(profile_contract) != SCALA_PROFILES
        or compiler_profiles_sha256
        != snapshot.sha256(
            scala_root / "compiler-profiles.v1.json",
            root=scala_root,
            label="selector.compilerProfiles",
        )
    ):
        raise T3EvidenceError("COMPILER_PROFILE_CONTRACT_INVALID")
    profile_options = {
        profile: profile_contract[profile].get("additionalOptions")
        for profile in SCALA_PROFILES
    }
    profile_cli_arguments = {
        profile: profile_contract[profile].get("scalaCliArguments")
        for profile in SCALA_PROFILES
    }
    if (
        profile_options != PROFILE_OPTIONS
        or profile_cli_arguments != PROFILE_CLI_ARGUMENTS
        or any(
            correctness[profile].get("profileOptions")
            != profile_options[profile]
            or correctness[profile].get("profileOptionsSha256")
            != canonical_sha256(profile_options[profile])
            for profile in SCALA_PROFILES
        )
    ):
        raise T3EvidenceError("PROFILE_OPTIONS_IDENTITY_MISMATCH")
    policy = plan.get("scalaProfileQualification")
    blocks = qualification.get("blocks")
    qualification_keys = {
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
    if (
        not isinstance(policy, dict)
        or set(qualification) != qualification_keys
        or qualification.get("schemaVersion")
        != "s1.4x-scala-profile-qualification-v1"
        or qualification.get("status") != "PASS"
        or not isinstance(blocks, list)
        or len(blocks) != policy.get("outerQualificationRepetitions")
        or len(blocks) != len(policy.get("profileOrderBlocks", []))
        or policy.get("profileOrderBlocks")
        != [["A", "B", "C"], ["B", "C", "A"], ["C", "A", "B"]]
        or policy.get("hostValidityBeforeEachProfileBlock") is not True
        or policy.get("outerQualificationRepetitions") != 3
        or policy.get("tieBreakOrder") != ["B", "C", "A"]
        or policy.get("fallbackProfile") != "A"
        or qualification.get("benchmarkPlanSha256")
        != benchmark_plan_sha256
        or qualification.get("sourceInputManifestSha256")
        != source_manifest_sha256
        or qualification.get("scalaCliBinarySha256")
        != pinned_scala_cli_sha256
        or qualification.get("jvmArgumentAllowlistSha256")
        != jvm_allowlist_sha256
    ):
        raise T3EvidenceError("PROFILE_QUALIFICATION_IDENTITY_MISMATCH")
    for key in (
        "benchmarkPlanSha256",
        "selectorConfigSha256",
        "sourceInputManifestSha256",
        "profileOptionsSha256",
        "scalaCliBinarySha256",
        "jvmArgumentAllowlistSha256",
        "effectiveJvmArgsClosureSha256",
    ):
        require_sha(qualification.get(key), key)
    if qualification["selectorConfigSha256"] != selector_config_sha256(
        policy=policy,
        benchmark_plan_sha256=benchmark_plan_sha256,
        blocks=blocks,
    ):
        raise T3EvidenceError("PROFILE_SELECTOR_CONFIG_DRIFT")
    if qualification["sourceInputManifestSha256"] != correctness["A"][
        "sourceInputManifestSha256"
    ]:
        raise T3EvidenceError("PROFILE_QUALIFICATION_SOURCE_DRIFT")
    if any(
        correctness[profile]["compilerProfilesSha256"]
        != compiler_profiles_sha256
        for profile in SCALA_PROFILES
    ):
        raise T3EvidenceError("PROFILE_CORRECTNESS_COMPILER_DRIFT")
    if any(
        correctness[profile]["toolchainLockSha256"]
        != toolchain_lock_sha256
        or correctness[profile]["scalaCliBinarySha256"]
        != pinned_scala_cli_sha256
        for profile in SCALA_PROFILES
    ):
        raise T3EvidenceError("PROFILE_CORRECTNESS_TOOLCHAIN_DRIFT")
    if qualification["profileOptionsSha256"] != canonical_sha256(profile_options):
        raise T3EvidenceError("PROFILE_QUALIFICATION_OPTIONS_DRIFT")
    profile_run_inputs = qualification.get("profileRunInputPaths")
    manifest_files = source_manifest.get("files")
    expected_jmh_inputs = (
        [
            path
            for path, metadata in manifest_files.items()
            if metadata.get("role") in {"configuration", "main", "benchmark"}
        ]
        if isinstance(manifest_files, dict)
        else []
    )
    if (
        not isinstance(profile_run_inputs, list)
        or not profile_run_inputs
        or profile_run_inputs
        != sorted(profile_run_inputs, key=lambda value: value.encode("utf-8"))
        or len(profile_run_inputs) != len(set(profile_run_inputs))
        or any(
            not isinstance(path, str)
            or re.fullmatch(
                r"^(?!/)(?!.*(?:^|/)\.\.(?:/|$))[A-Za-z0-9._/-]+$",
                path,
            )
            is None
            for path in profile_run_inputs
        )
        or profile_run_inputs != expected_jmh_inputs
    ):
        raise T3EvidenceError("PROFILE_RUN_INPUT_PATHS_INVALID")

    case_order = policy.get("qualificationCaseOrder")
    if (
        not isinstance(case_order, list)
        or case_order != policy.get("qualificationCaseIds")
        or len(case_order) != 7
        or len(case_order) != len(set(case_order))
    ):
        raise T3EvidenceError("PROFILE_QUALIFICATION_CASE_ORDER_INVALID")

    scores: dict[tuple[int, str, str], float] = {}
    all_effective_hashes: list[str] = []
    block_keys = {
        "outerRepetition",
        "plannedProfileOrder",
        "actualProfileOrder",
        "hostValiditySha256",
        "effectiveJvmArgsSha256",
        "profileEvidence",
        "measurements",
    }
    profile_evidence_keys = {
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
    measurement_keys = {
        "profileId",
        "caseId",
        "scoreNsPerInvocation",
        "rawNativeJsonSha256",
        "effectiveJvmArgsSha256",
        "jmhRunResultSha256",
    }
    for repetition, (block, expected_order) in enumerate(
        zip(blocks, policy["profileOrderBlocks"], strict=True),
        start=1,
    ):
        if (
            not isinstance(block, dict)
            or set(block) != block_keys
            or block.get("outerRepetition") != repetition
            or block.get("plannedProfileOrder") != expected_order
            or block.get("actualProfileOrder") != expected_order
        ):
            raise T3EvidenceError(f"PROFILE_LATIN_ORDER_MISMATCH:{repetition}")
        profile_evidence = block.get("profileEvidence")
        if (
            not isinstance(profile_evidence, list)
            or any(not isinstance(item, dict) for item in profile_evidence)
            or [item.get("profileId") for item in profile_evidence]
            != expected_order
        ):
            raise T3EvidenceError(
                f"PROFILE_HOST_JVM_CLOSURE_MISMATCH:{repetition}"
            )
        measurements = block.get("measurements")
        if (
            not isinstance(measurements, list)
            or any(
                not isinstance(item, dict) or set(item) != measurement_keys
                for item in measurements
            )
        ):
            raise T3EvidenceError(f"PROFILE_MEASUREMENTS_MISSING:{repetition}")
        expected_pairs = [
            (profile, case_id) for profile in expected_order for case_id in case_order
        ]
        actual_pairs = [
            (item.get("profileId"), item.get("caseId"))
            for item in measurements
            if isinstance(item, dict)
        ]
        if actual_pairs != expected_pairs:
            raise T3EvidenceError(f"PROFILE_MEASUREMENT_CLOSURE_MISMATCH:{repetition}")
        for item in measurements:
            declared_score = item.get("scoreNsPerInvocation")
            if (
                type(declared_score) not in (int, float)
                or not math.isfinite(declared_score)
                or declared_score <= 0
            ):
                raise T3EvidenceError("PROFILE_SCORE_INVALID")
            for key in (
                "rawNativeJsonSha256",
                "effectiveJvmArgsSha256",
                "jmhRunResultSha256",
            ):
                require_sha(
                    item.get(key),
                    f"block{repetition}.{item['profileId']}.{item['caseId']}.{key}",
                )
            all_effective_hashes.append(item["effectiveJvmArgsSha256"])

        for item in profile_evidence:
            profile = item["profileId"]
            profile_measurements = [
                measurement
                for measurement in measurements
                if measurement["profileId"] == profile
            ]
            started_at = require_utc_timestamp(
                item.get("startedAt"),
                f"block{repetition}.{profile}.startedAt",
            )
            ended_at = require_utc_timestamp(
                item.get("endedAt"),
                f"block{repetition}.{profile}.endedAt",
            )
            expected_effective = canonical_sha256(
                [
                    measurement["effectiveJvmArgsSha256"]
                    for measurement in profile_measurements
                ]
            )
            if (
                set(item) != profile_evidence_keys
                or item.get("plannedCaseOrder") != case_order
                or item.get("actualCaseOrder") != case_order
                or item.get("caseCount") != len(case_order)
                or ended_at <= started_at
                or item.get("scalaCliBinarySha256")
                != qualification["scalaCliBinarySha256"]
                or item.get("profileOptionsSha256")
                != canonical_sha256(profile_options[profile])
                or item.get("sourceInputManifestSha256")
                != qualification["sourceInputManifestSha256"]
                or item.get("effectiveJvmArgsSha256") != expected_effective
            ):
                raise T3EvidenceError(
                    f"PROFILE_HOST_JVM_CASE_CLOSURE_MISMATCH:{repetition}:{profile}"
                )
            require_sha(
                item.get("hostValiditySha256"),
                f"block{repetition}.{profile}.host",
            )
            validate_host_validity_artifact(
                artifact_root=qualification_artifact_root,
                repetition=repetition,
                profile=profile,
                expected_sha256=item["hostValiditySha256"],
                plan=plan,
                snapshot=snapshot,
            )

        expected_block_host = canonical_sha256(
            [item["hostValiditySha256"] for item in profile_evidence]
        )
        expected_block_effective = canonical_sha256(
            [item["effectiveJvmArgsSha256"] for item in profile_evidence]
        )
        if (
            block.get("hostValiditySha256") != expected_block_host
            or block.get("effectiveJvmArgsSha256") != expected_block_effective
        ):
            raise T3EvidenceError(f"PROFILE_BLOCK_HASH_CLOSURE_MISMATCH:{repetition}")
        for item in measurements:
            profile = item["profileId"]
            case_id = item["caseId"]
            if jdk_modules_snapshot is None:
                jdk_modules_snapshot = snapshot.capture_regular_file(
                    jdk_modules_path,
                    label="SELECTOR_JDK_MODULES",
                )
            scores[(repetition, profile, case_id)] = (
                validate_qualification_case_artifacts(
                    plan=plan,
                    policy=policy,
                    artifact_root=qualification_artifact_root,
                    repetition=repetition,
                    profile=profile,
                    case_index=case_order.index(case_id) + 1,
                    case_id=case_id,
                    measurement=item,
                    scala_root=scala_root,
                    scala_cli=scala_cli,
                    source_input_paths=profile_run_inputs,
                    source_manifest_sha256=source_manifest_sha256,
                    compiler_profiles_sha256=compiler_profiles_sha256,
                    benchmark_plan_sha256=benchmark_plan_sha256,
                    jvm_allowlist=jvm_allowlist,
                    jvm_allowlist_sha256=jvm_allowlist_sha256,
                    jdk_modules_snapshot=jdk_modules_snapshot,
                    snapshot=snapshot,
                )
            )
    if (
        qualification["effectiveJvmArgsClosureSha256"]
        != canonical_sha256(all_effective_hashes)
    ):
        raise T3EvidenceError("PROFILE_TOP_LEVEL_JVM_CLOSURE_MISMATCH")

    profile_results, selected = select_profile_from_scores(
        policy=policy,
        block_count=len(blocks),
        case_order=case_order,
        scores=scores,
    )
    result = {
        "schemaVersion": "s1.4x-scala-selected-profile-result-v1",
        "benchmarkPlanSha256": qualification["benchmarkPlanSha256"],
        "selectorConfigSha256": qualification["selectorConfigSha256"],
        "qualificationSha256": qualification_sha256,
        "sourceInputManifestSha256": qualification["sourceInputManifestSha256"],
        "compilerProfilesSha256": compiler_profiles_sha256,
        "toolchainLockSha256": toolchain_lock_sha256,
        "mergedToolchainProvenanceSha256": (
            merged_toolchain_provenance_sha256
        ),
        "scalaCliBinarySha256": pinned_scala_cli_sha256,
        "javaExecutableSha256": pinned_java_executable_sha256,
        "jvmArgumentAllowlistSha256": jvm_allowlist_sha256,
        "effectiveJvmArgumentsCapabilitySha256": jvm_allowlist_sha256,
        "profileOptionsSha256": qualification["profileOptionsSha256"],
        "selectedProfileSourceSha256": selected_profile_source_sha256,
        "selectedProfileOptions": profile_options[selected],
        "selectedProfileOptionsSha256": canonical_sha256(
            profile_options[selected]
        ),
        "correctnessResultSha256": correctness_sha256,
        "profiles": profile_results,
        "selectedProfileId": selected,
        "fallbackProfileId": policy["fallbackProfile"],
        "fallbackExecuted": selected == policy["fallbackProfile"],
        "selectionStatus": "PASS",
    }
    snapshot.verify_unchanged()
    return result


def assemble_capability_result(
    *,
    plan: dict[str, Any],
    plan_sha256: str,
    toolchain_identity_sha256: str,
    evidence: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    require_sha(plan_sha256, "planSha256")
    require_sha(toolchain_identity_sha256, "toolchainIdentitySha256")
    scala = plan.get("languages", {}).get("scala", {})
    smokes = scala.get("smokes")
    if (
        plan.get("schemaVersion") != "s1.4x-capability-smoke-plan-v1"
        or not isinstance(smokes, list)
    ):
        raise T3EvidenceError("CAPABILITY_PLAN_INVALID")
    expected_ids = [item.get("smokeId") for item in smokes]
    if set(evidence) != set(expected_ids) or len(expected_ids) != 8:
        raise T3EvidenceError("CAPABILITY_EVIDENCE_CLOSURE_MISMATCH")
    results = []
    evidence_keys = {
        "compilerStatus",
        "argv",
        "exitCode",
        "stdoutSha256",
        "stderrSha256",
        "artifactSha256",
        "status",
        "disposition",
        "provenFallback",
        "fallbackExecuted",
    }
    for smoke in smokes:
        smoke_id = smoke["smokeId"]
        item = evidence[smoke_id]
        if (
            not isinstance(item, dict)
            or set(item) != evidence_keys
            or item.get("compilerStatus") != "stable"
            or type(item.get("exitCode")) is not int
            or item["exitCode"] < 0
            or item["exitCode"] > 255
            or item.get("status") not in {"PASS", "FAIL"}
            or item.get("provenFallback") != smoke.get("provenFallback")
            or not isinstance(item.get("fallbackExecuted"), bool)
        ):
            raise T3EvidenceError(f"CAPABILITY_STATUS_INVALID:{smoke_id}")
        require_portable_argv(item.get("argv"), f"{smoke_id}.argv")
        if item["status"] == "PASS":
            if (
                item["exitCode"] != 0
                or item.get("disposition") != "ADOPT"
                or item["fallbackExecuted"]
            ):
                raise T3EvidenceError(f"CAPABILITY_PASS_CONTRADICTION:{smoke_id}")
        elif item.get("disposition") not in {
            "FALLBACK",
            "BLOCKED_TOOLCHAIN",
            "BLOCKED_CONTRACT",
        }:
            raise T3EvidenceError(f"CAPABILITY_FAIL_CONTRADICTION:{smoke_id}")
        for key in ("stdoutSha256", "stderrSha256", "artifactSha256"):
            require_sha(item.get(key), f"{smoke_id}.{key}")
        results.append({"smokeId": smoke_id, **item})
    aggregate = "PASS" if all(item["status"] == "PASS" for item in results) else "FAIL"
    return {
        "schemaVersion": "s1.4x-capability-smoke-result-v1",
        "planSha256": plan_sha256,
        "language": "scala",
        "toolchainIdentitySha256": toolchain_identity_sha256,
        "results": results,
        "aggregateStatus": aggregate,
    }


def assemble_input_set_result(
    *,
    manifest: dict[str, Any],
    manifest_sha256: str,
    compiler_profile_sha256: str,
    input_sets: dict[str, list[str]],
) -> dict[str, Any]:
    """tracked/manifest/format/compile/lint/profileRun이 한 byte-locked 집합인지 증명한다."""

    require_sha(manifest_sha256, "manifestSha256")
    require_sha(compiler_profile_sha256, "compilerProfileSha256")
    expected_keys = (
        "tracked",
        "manifest",
        "format",
        "compile",
        "lint",
        "profileRun",
    )
    files = manifest.get("files")
    if (
        manifest.get("schemaVersion") != "s1.4x-source-input-manifest-v1"
        or manifest.get("language") != "scala"
        or manifest.get("inputSets")
        != {key: "files" for key in expected_keys}
        or not isinstance(files, dict)
        or tuple(input_sets) != expected_keys
    ):
        raise T3EvidenceError("INPUT_SET_MANIFEST_IDENTITY_MISMATCH")
    expected = list(files)
    if not expected or len(expected) != len(set(expected)):
        raise T3EvidenceError("INPUT_SET_EXPECTED_FILES_INVALID")

    results: dict[str, dict[str, Any]] = {}
    for name in expected_keys:
        actual = input_sets[name]
        if (
            not isinstance(actual, list)
            or any(not isinstance(path, str) for path in actual)
            or actual != expected
        ):
            raise T3EvidenceError(f"INPUT_SET_MISMATCH:{name}")
        results[name] = {
            "fileCount": len(actual),
            "pathSetSha256": canonical_sha256(actual),
            "exact": True,
        }
    return {
        "schemaVersion": "s1.4x-scala-input-set-equality-result-v1",
        "sourceInputManifestSha256": manifest_sha256,
        "canonicalManifestSha256": require_sha(
            manifest.get("canonicalManifestSha256"),
            "canonicalManifestSha256",
        ),
        "compilerProfileSha256": compiler_profile_sha256,
        "sets": results,
        "aggregateStatus": "PASS",
    }


def assemble_feature_decision_result(
    *,
    planned: dict[str, Any],
    planned_sha256: str,
    capability_sha256: str,
    evidence: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """동결된 Scala feature 여섯 개를 effective decision과 exact 연결한다."""

    require_sha(planned_sha256, "plannedDecisionSha256")
    require_sha(capability_sha256, "capabilitySmokeResultSha256")
    entries = planned.get("entries")
    if (
        planned.get("schemaVersion") != "s1.4x-feature-decisions-v1"
        or not isinstance(entries, list)
    ):
        raise T3EvidenceError("FEATURE_PLAN_INVALID")
    scala_entries = [
        item
        for item in entries
        if isinstance(item, dict)
        and isinstance(item.get("featureId"), str)
        and item["featureId"].startswith("scala.")
    ]
    expected_ids = [item["featureId"] for item in scala_entries]
    if len(expected_ids) != 6 or set(evidence) != set(expected_ids):
        raise T3EvidenceError("FEATURE_EVIDENCE_CLOSURE_MISMATCH")

    effective_entries: list[dict[str, Any]] = []
    status_fields = ("smokeStatus", "lintStatus", "testStatus", "evidenceStatus")
    allowed_status = {"PASS", "FAIL", "NOT_APPLICABLE"}
    for planned_item in scala_entries:
        feature_id = planned_item["featureId"]
        item = evidence[feature_id]
        planned_decision = planned_item.get("decision")
        effective_decision = item.get("effectiveDecision")
        if (
            item.get("plannedDecision") != planned_decision
            or planned_decision
            not in {"ADOPT", "CONDITIONAL", "PROBE_ONLY", "REJECT"}
            or effective_decision
            not in {"ADOPT", "FALLBACK", "PROBE_ONLY", "REJECT"}
            or any(item.get(field) not in allowed_status for field in status_fields)
            or type(item.get("parityMismatchCount")) is not int
            or item["parityMismatchCount"] < 0
            or type(item.get("fallbackExecuted")) is not bool
            or item.get("fallbackStatus") not in {"PASS", "FAIL", "NOT_RUN"}
        ):
            raise T3EvidenceError(f"FEATURE_EVIDENCE_INVALID:{feature_id}")
        require_sha(item.get("evidenceSha256"), f"{feature_id}.evidenceSha256")

        all_pass = (
            all(item[field] == "PASS" for field in status_fields)
            and item["parityMismatchCount"] == 0
        )
        if effective_decision == "ADOPT":
            valid = (
                planned_decision in {"ADOPT", "CONDITIONAL"}
                and all_pass
                and not item["fallbackExecuted"]
                and item["fallbackStatus"] == "NOT_RUN"
            )
        elif effective_decision == "FALLBACK":
            valid = (
                planned_decision in {"ADOPT", "CONDITIONAL"}
                and not all_pass
                and item["fallbackExecuted"]
                and item["fallbackStatus"] == "PASS"
            )
        elif effective_decision == "PROBE_ONLY":
            valid = (
                planned_decision == "PROBE_ONLY"
                and not item["fallbackExecuted"]
                and item["fallbackStatus"] == "NOT_RUN"
            )
        else:
            valid = (
                planned_decision == "REJECT"
                and not item["fallbackExecuted"]
                and item["fallbackStatus"] == "NOT_RUN"
            )
        if not valid:
            raise T3EvidenceError(f"FEATURE_DECISION_CONTRADICTION:{feature_id}")
        effective_entries.append({"featureId": feature_id, **item})

    return {
        "schemaVersion": "s1.4x-feature-decision-results-v1",
        "plannedDecisionSha256": planned_sha256,
        "capabilitySmokeResultSha256": capability_sha256,
        "entries": effective_entries,
    }


def assemble_scala_dependency_audit(
    *,
    policy_sha256: str,
    source_input_manifest_sha256: str,
    project_sha256: str,
    dependencies: list[str],
    forbidden_source_findings: list[dict[str, Any]],
) -> dict[str, Any]:
    """Scala candidate의 authored/direct-core/timed native interop edge가 0인지 닫는다."""

    require_sha(policy_sha256, "policySha256")
    require_sha(source_input_manifest_sha256, "sourceInputManifestSha256")
    require_sha(project_sha256, "projectSha256")
    if (
        not isinstance(dependencies, list)
        or not dependencies
        or any(not isinstance(item, str) or not item for item in dependencies)
        or len(dependencies) != len(set(dependencies))
        or not isinstance(forbidden_source_findings, list)
    ):
        raise T3EvidenceError("SCALA_DEPENDENCY_INPUT_INVALID")
    forbidden_dependency_markers = (
        "grpc",
        "netty",
        "jni",
        "scala-native",
        "scalanative",
        "graal",
        "llvm",
        "vector-api",
    )
    native_dependencies = [
        item
        for item in dependencies
        if any(marker in item.lower() for marker in forbidden_dependency_markers)
    ]
    if native_dependencies or forbidden_source_findings:
        raise T3EvidenceError("SCALA_NATIVE_INTEROP_EDGE_FOUND")
    return {
        "schemaVersion": "s1.4x-scala-dependency-native-edge-result-v1",
        "policySha256": policy_sha256,
        "sourceInputManifestSha256": source_input_manifest_sha256,
        "projectSha256": project_sha256,
        "dependencies": [
            {
                "coordinate": item,
                "coordinateSha256": hashlib.sha256(item.encode("utf-8")).hexdigest(),
                "nativeInterop": False,
            }
            for item in sorted(dependencies)
        ],
        "forbiddenSourceFindings": [],
        "candidateAuthoredEdgeCount": 0,
        "candidateAddedNativeDependencyCount": 0,
        "candidateCoreDirectNativeBindingImportCount": 0,
        "candidateCoreDirectNativeBindingCallCount": 0,
        "timedKernelExplicitCandidateNativeInteropCallCount": 0,
        "unknownEdgeCount": 0,
        "aggregateStatus": "PASS",
    }


JVM_FORK_KEYS = {
    "schemaVersion",
    "forkIndex",
    "javaExecutablePathId",
    "javaExecutableSha256",
    "runtimeVersion",
    "vendor",
    "javaHomePathId",
    "inputArguments",
    "inputArgumentFiles",
    "stableSystemProperties",
    "ambientJvmOptionVariables",
    "systemPropertiesSha256",
    "environmentAllowlistSha256",
    "runtimeClasspathSha256",
    "evidenceSha256",
}
JVM_ALLOWLIST_KEYS = {
    "schemaVersion",
    "benchmarkPlanSha256",
    "capabilitySmokePlanSha256",
    "toolchainLockSha256",
    "javaExecutablePathId",
    "javaExecutableSha256",
    "runtimeVersion",
    "vendor",
    "plannedCliJvmArguments",
    "effectiveJvmArguments",
    "stableSystemProperties",
    "ambientJvmOptionVariables",
    "systemPropertiesSha256",
    "environmentAllowlistSha256",
    "smokeForkEvidenceSha256",
    "effectiveArgumentsSha256",
    "status",
}
EXPECTED_STABLE_SYSTEM_PROPERTIES = {
    "java.runtime.version": "25.0.3+9-LTS",
    "java.specification.version": "25",
    "java.vendor": "Eclipse Adoptium",
    "java.vm.name": "OpenJDK 64-Bit Server VM",
}
EXPECTED_AMBIENT_JVM_OPTIONS = {
    "JAVA_TOOL_OPTIONS": "UNSET",
    "_JAVA_OPTIONS": "UNSET",
    "JDK_JAVA_OPTIONS": "UNSET",
}
EXPECTED_BENCHMARK_ENVIRONMENT = {
    "S1_4X_BENCHMARK_CASE_ID": "SET",
    "S1_4X_BENCHMARK_PLAN": "SET",
    "S1_4X_BENCHMARK_PROFILE": "SET",
    "S1_4X_BENCHMARK_RUN_MODE": "SET",
    "S1_4X_EFFECTIVE_JVM_EVIDENCE_DIR": "SET",
    "S1_4X_FIXTURE_ROOT": "SET",
    "S1_4X_JMH_TMPDIR": "SET",
    "S1_4X_MEASUREMENT_READY_MARKER": "SET",
    "S1_4X_SCALA_WORKSPACE": "SET",
    "COURSIER_CACHE": "SET",
    "COURSIER_CONFIG_DIR": "SET",
    "SCALA_CLI_HOME": "SET",
    "SCALA_CLI_CONFIG": "SET",
    "XDG_CONFIG_HOME": "SET",
}
JMH_COMPILE_COMMAND_PREFIX = "-XX:CompileCommandFile="
JMH_COMPILE_COMMAND_PATH_ID = "JMH_COMPILE_COMMAND_FILE"
JMH_TMPDIR_PREFIX = "-Djava.io.tmpdir="
JMH_TMPDIR_PATH_ID = "EVIDENCE_ROOT/jmh-tmp"
JMH_TMPDIR_PORTABLE_ARGUMENT = f"{JMH_TMPDIR_PREFIX}{JMH_TMPDIR_PATH_ID}"
JMH_LAUNCHER_ONLY_ARGUMENTS = (
    "-XX:+UnlockDiagnosticVMOptions",
    "-XX:+UnlockExperimentalVMOptions",
    "-DcompilerBlackholesEnabled=true",
)


def canonical_pairs_sha256(values: dict[str, str]) -> str:
    if not isinstance(values, dict) or any(
        not isinstance(key, str) or not isinstance(value, str)
        for key, value in values.items()
    ):
        raise T3EvidenceError("CANONICAL_PAIR_MAP_INVALID")
    payload = "".join(
        f"{key}={values[key]}\n"
        for key in sorted(values, key=lambda item: item.encode("utf-8"))
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def normalized_jvm_arguments(fork: dict[str, Any]) -> list[str]:
    """Randomized JMH argument file을 captured content identity로 치환한다."""

    arguments = fork.get("inputArguments")
    files = fork.get("inputArgumentFiles")
    if (
        not isinstance(arguments, list)
        or any(type(item) is not str for item in arguments)
        or not isinstance(files, list)
    ):
        raise T3EvidenceError("JVM_ARGUMENT_FILE_WITNESS_INVALID")
    compile_indexes = [
        index
        for index, item in enumerate(arguments)
        if item.startswith(JMH_COMPILE_COMMAND_PREFIX)
    ]
    tmp_indexes = [
        index
        for index, item in enumerate(arguments)
        if item.startswith(JMH_TMPDIR_PREFIX)
    ]
    if (
        bool(compile_indexes) != bool(tmp_indexes)
        or (compile_indexes and len(tmp_indexes) != 1)
    ):
        raise T3EvidenceError("JVM_ARGUMENT_FILE_WITNESS_INVALID")
    tmp_directory: Path | None = None
    if tmp_indexes:
        raw_tmp = arguments[tmp_indexes[0]].removeprefix(JMH_TMPDIR_PREFIX)
        tmp_directory = Path(raw_tmp)
        if (
            not raw_tmp
            or "\x00" in raw_tmp
            or "\r" in raw_tmp
            or not tmp_directory.is_absolute()
            or tmp_directory.name != "jmh-tmp"
            or tmp_directory.is_relative_to(Path("/tmp"))
        ):
            raise T3EvidenceError("JVM_ARGUMENT_FILE_WITNESS_INVALID")
    if len(files) != len(compile_indexes):
        raise T3EvidenceError("JVM_ARGUMENT_FILE_WITNESS_INVALID")
    by_index: dict[int, dict[str, Any]] = {}
    for item in files:
        if (
            not isinstance(item, dict)
            or set(item)
            != {
                "argumentIndex",
                "argumentPrefix",
                "pathId",
                "sha256",
                "fileIdentitySha256",
            }
            or type(item.get("argumentIndex")) is not int
            or item["argumentIndex"] in by_index
            or item.get("argumentPrefix") != JMH_COMPILE_COMMAND_PREFIX
            or item.get("pathId") != JMH_COMPILE_COMMAND_PATH_ID
            or SHA256.fullmatch(str(item.get("sha256"))) is None
            or SHA256.fullmatch(
                str(item.get("fileIdentitySha256"))
            )
            is None
        ):
            raise T3EvidenceError("JVM_ARGUMENT_FILE_WITNESS_INVALID")
        index = item["argumentIndex"]
        if (
            index < 0
            or index >= len(arguments)
            or index not in compile_indexes
        ):
            raise T3EvidenceError("JVM_ARGUMENT_FILE_WITNESS_INVALID")
        raw_path = arguments[index].removeprefix(
            JMH_COMPILE_COMMAND_PREFIX
        )
        if (
            not raw_path
            or "\x00" in raw_path
            or "\r" in raw_path
            or not Path(raw_path).is_absolute()
            or tmp_directory is None
            or Path(raw_path).parent != tmp_directory
        ):
            raise T3EvidenceError("JVM_ARGUMENT_FILE_WITNESS_INVALID")
        by_index[index] = item
    if set(by_index) != set(compile_indexes):
        raise T3EvidenceError("JVM_ARGUMENT_FILE_WITNESS_INVALID")
    return [
        (
            f"{JMH_COMPILE_COMMAND_PREFIX}{JMH_COMPILE_COMMAND_PATH_ID}"
            f"#sha256={by_index[index]['sha256']}"
            if index in by_index
            else JMH_TMPDIR_PORTABLE_ARGUMENT
            if index in tmp_indexes
            else argument
        )
        for index, argument in enumerate(arguments)
    ]


def reported_jvm_arguments(
    effective_jvm_arguments: list[str],
) -> list[str]:
    """JMH JSON이 보고하는 args와 launcher-only actual fork args를 분리한다."""

    if (
        not isinstance(effective_jvm_arguments, list)
        or any(type(item) is not str for item in effective_jvm_arguments)
    ):
        raise T3EvidenceError("JMH_EFFECTIVE_ARGUMENTS_INVALID")
    compile_markers = [
        item
        for item in effective_jvm_arguments
        if item.startswith(
            f"{JMH_COMPILE_COMMAND_PREFIX}{JMH_COMPILE_COMMAND_PATH_ID}"
            "#sha256="
        )
    ]
    launcher_present = any(
        item in JMH_LAUNCHER_ONLY_ARGUMENTS
        for item in effective_jvm_arguments
    )
    if not compile_markers and not launcher_present:
        return list(effective_jvm_arguments)
    if effective_jvm_arguments.count(JMH_TMPDIR_PORTABLE_ARGUMENT) != 1:
        raise T3EvidenceError("JMH_LAUNCHER_ARGUMENT_CLOSURE_INVALID")
    if (
        len(compile_markers) != 1
        or SHA256.fullmatch(
            compile_markers[0].removeprefix(
                f"{JMH_COMPILE_COMMAND_PREFIX}"
                f"{JMH_COMPILE_COMMAND_PATH_ID}#sha256="
            )
        )
        is None
    ):
        raise T3EvidenceError("JMH_LAUNCHER_ARGUMENT_CLOSURE_INVALID")
    suffix = [
        *JMH_LAUNCHER_ONLY_ARGUMENTS,
        compile_markers[0],
    ]
    if effective_jvm_arguments[-len(suffix) :] != suffix:
        raise T3EvidenceError("JMH_LAUNCHER_ARGUMENT_CLOSURE_INVALID")
    return effective_jvm_arguments[: -len(suffix)]


def normalized_native_reported_jvm_arguments(arguments: Any) -> list[str]:
    """Native JSON의 physical output tmpdir만 portable path ID로 치환한다."""

    if (
        not isinstance(arguments, list)
        or any(type(item) is not str for item in arguments)
    ):
        raise T3EvidenceError("JMH_NATIVE_REPORTED_ARGUMENTS_INVALID")
    tmp_indexes = [
        index
        for index, item in enumerate(arguments)
        if item.startswith(JMH_TMPDIR_PREFIX)
    ]
    if not tmp_indexes:
        return list(arguments)
    if len(tmp_indexes) != 1:
        raise T3EvidenceError("JMH_NATIVE_REPORTED_ARGUMENTS_INVALID")
    raw_tmp = arguments[tmp_indexes[0]].removeprefix(JMH_TMPDIR_PREFIX)
    tmp_directory = Path(raw_tmp)
    if (
        not raw_tmp
        or "\x00" in raw_tmp
        or "\r" in raw_tmp
        or not tmp_directory.is_absolute()
        or tmp_directory.name != "jmh-tmp"
        or tmp_directory.is_relative_to(Path("/tmp"))
    ):
        raise T3EvidenceError("JMH_NATIVE_REPORTED_ARGUMENTS_INVALID")
    return [
        JMH_TMPDIR_PORTABLE_ARGUMENT if index in tmp_indexes else item
        for index, item in enumerate(arguments)
    ]


def require_jvm_fork(
    fork: Any,
    *,
    expected_index: int,
    allowed_arguments: list[str] | None,
    java_executable_sha256: str,
    stable_system_properties: dict[str, str],
    ambient_jvm_options: dict[str, str],
) -> dict[str, Any]:
    if (
        not isinstance(fork, dict)
        or set(fork) != JVM_FORK_KEYS
        or fork.get("schemaVersion") != "s1.4x-scala-jvm-fork-evidence-v1"
        or fork.get("forkIndex") != expected_index
        or fork.get("javaExecutablePathId")
        != "TEMURIN_25_0_3_9_LTS/bin/java"
        or fork.get("javaExecutableSha256") != java_executable_sha256
        or fork.get("runtimeVersion") != "25.0.3+9-LTS"
        or fork.get("vendor") != "Eclipse Adoptium"
        or fork.get("javaHomePathId") != "TEMURIN_25_0_3_9_LTS"
        or not isinstance(fork.get("inputArguments"), list)
        or not all(isinstance(item, str) for item in fork["inputArguments"])
        or not isinstance(fork.get("inputArgumentFiles"), list)
        or fork.get("stableSystemProperties") != stable_system_properties
        or fork.get("ambientJvmOptionVariables") != ambient_jvm_options
    ):
        raise T3EvidenceError(f"JVM_FORK_IDENTITY_MISMATCH:{expected_index}")
    normalized_arguments = normalized_jvm_arguments(fork)
    if (
        allowed_arguments is not None
        and normalized_arguments != allowed_arguments
    ):
        raise T3EvidenceError(f"JVM_FORK_IDENTITY_MISMATCH:{expected_index}")
    if (
        fork.get("systemPropertiesSha256")
        != canonical_pairs_sha256(stable_system_properties)
        or fork.get("environmentAllowlistSha256")
        != canonical_pairs_sha256(EXPECTED_BENCHMARK_ENVIRONMENT)
    ):
        raise T3EvidenceError(f"JVM_FORK_HASH_MISMATCH:{expected_index}")
    for key in (
        "systemPropertiesSha256",
        "environmentAllowlistSha256",
        "runtimeClasspathSha256",
        "evidenceSha256",
    ):
        require_sha(fork.get(key), f"fork{expected_index}.{key}")
    return fork


def assemble_jvm_argument_allowlist(
    *,
    forks: Any,
    planned_cli_arguments: list[str],
    benchmark_plan_sha256: str,
    capability_smoke_plan_sha256: str,
    toolchain_lock_sha256: str,
    java_executable_sha256: str,
) -> dict[str, Any]:
    """clean JMH smoke가 관찰한 exact JVM argument/property allowlist를 동결한다."""

    for field, value in (
        ("benchmarkPlanSha256", benchmark_plan_sha256),
        ("capabilitySmokePlanSha256", capability_smoke_plan_sha256),
        ("toolchainLockSha256", toolchain_lock_sha256),
        ("javaExecutableSha256", java_executable_sha256),
    ):
        require_sha(value, field)
    if (
        not isinstance(planned_cli_arguments, list)
        or planned_cli_arguments
        or not isinstance(forks, list)
        or len(forks) != 1
    ):
        raise T3EvidenceError("JVM_ALLOWLIST_SMOKE_INPUT_INVALID")
    fork = require_jvm_fork(
        forks[0],
        expected_index=1,
        allowed_arguments=None,
        java_executable_sha256=java_executable_sha256,
        stable_system_properties=EXPECTED_STABLE_SYSTEM_PROPERTIES,
        ambient_jvm_options=EXPECTED_AMBIENT_JVM_OPTIONS,
    )
    observed_arguments = normalized_jvm_arguments(fork)
    return {
        "schemaVersion": "s1.4x-scala-jvm-argument-allowlist-v1",
        "benchmarkPlanSha256": benchmark_plan_sha256,
        "capabilitySmokePlanSha256": capability_smoke_plan_sha256,
        "toolchainLockSha256": toolchain_lock_sha256,
        "javaExecutablePathId": "TEMURIN_25_0_3_9_LTS/bin/java",
        "javaExecutableSha256": java_executable_sha256,
        "runtimeVersion": "25.0.3+9-LTS",
        "vendor": "Eclipse Adoptium",
        "plannedCliJvmArguments": planned_cli_arguments,
        "effectiveJvmArguments": observed_arguments,
        "stableSystemProperties": EXPECTED_STABLE_SYSTEM_PROPERTIES,
        "ambientJvmOptionVariables": EXPECTED_AMBIENT_JVM_OPTIONS,
        "systemPropertiesSha256": canonical_pairs_sha256(
            EXPECTED_STABLE_SYSTEM_PROPERTIES
        ),
        "environmentAllowlistSha256": canonical_pairs_sha256(
            EXPECTED_BENCHMARK_ENVIRONMENT
        ),
        "smokeForkEvidenceSha256": [fork["evidenceSha256"]],
        "effectiveArgumentsSha256": canonical_sha256(observed_arguments),
        "status": "PASS",
    }


def validate_effective_jvm_evidence(
    forks: Any,
    *,
    expected_forks: int,
    allowlist: dict[str, Any],
    allowlist_sha256: str,
) -> dict[str, Any]:
    """후속 JMH fork가 smoke-produced allowlist와 byte-linked exact identity인지 검증한다."""

    require_sha(allowlist_sha256, "jvmArgumentAllowlistSha256")
    if (
        type(expected_forks) is not int
        or expected_forks < 1
        or not isinstance(forks, list)
        or len(forks) != expected_forks
        or not isinstance(allowlist, dict)
        or set(allowlist) != JVM_ALLOWLIST_KEYS
        or allowlist.get("schemaVersion")
        != "s1.4x-scala-jvm-argument-allowlist-v1"
        or allowlist.get("status") != "PASS"
        or allowlist.get("javaExecutablePathId")
        != "TEMURIN_25_0_3_9_LTS/bin/java"
        or allowlist.get("runtimeVersion") != "25.0.3+9-LTS"
        or allowlist.get("vendor") != "Eclipse Adoptium"
        or allowlist.get("plannedCliJvmArguments") != []
        or not isinstance(allowlist.get("effectiveJvmArguments"), list)
        or not all(
            isinstance(item, str)
            for item in allowlist.get("effectiveJvmArguments", [])
        )
        or any(
            item.startswith(JMH_COMPILE_COMMAND_PREFIX)
            and not item.startswith(
                f"{JMH_COMPILE_COMMAND_PREFIX}"
                f"{JMH_COMPILE_COMMAND_PATH_ID}#sha256="
            )
            for item in allowlist.get("effectiveJvmArguments", [])
        )
        or any(
            item.startswith(JMH_TMPDIR_PREFIX)
            and item != JMH_TMPDIR_PORTABLE_ARGUMENT
            for item in allowlist.get("effectiveJvmArguments", [])
        )
        or allowlist.get("stableSystemProperties")
        != EXPECTED_STABLE_SYSTEM_PROPERTIES
        or allowlist.get("ambientJvmOptionVariables")
        != EXPECTED_AMBIENT_JVM_OPTIONS
        or allowlist.get("systemPropertiesSha256")
        != canonical_pairs_sha256(EXPECTED_STABLE_SYSTEM_PROPERTIES)
        or allowlist.get("environmentAllowlistSha256")
        != canonical_pairs_sha256(EXPECTED_BENCHMARK_ENVIRONMENT)
        or allowlist.get("effectiveArgumentsSha256")
        != canonical_sha256(allowlist.get("effectiveJvmArguments"))
    ):
        raise T3EvidenceError("JVM_ARGUMENT_ALLOWLIST_INVALID")
    reported_jvm_arguments(allowlist["effectiveJvmArguments"])
    for key in (
        "benchmarkPlanSha256",
        "capabilitySmokePlanSha256",
        "toolchainLockSha256",
        "javaExecutableSha256",
    ):
        require_sha(allowlist.get(key), f"allowlist.{key}")
    smoke_hashes = allowlist.get("smokeForkEvidenceSha256")
    if not isinstance(smoke_hashes, list) or len(smoke_hashes) != 1:
        raise T3EvidenceError("JVM_ARGUMENT_ALLOWLIST_SMOKE_CLOSURE_INVALID")
    require_sha(smoke_hashes[0], "allowlist.smokeForkEvidence")

    evidence_hashes = []
    for expected_index, fork in enumerate(forks, start=1):
        validated = require_jvm_fork(
            fork,
            expected_index=expected_index,
            allowed_arguments=allowlist["effectiveJvmArguments"],
            java_executable_sha256=allowlist["javaExecutableSha256"],
            stable_system_properties=allowlist["stableSystemProperties"],
            ambient_jvm_options=allowlist["ambientJvmOptionVariables"],
        )
        evidence_hashes.append(validated["evidenceSha256"])
    return {
        "schemaVersion": "s1.4x-scala-effective-jvm-args-result-v1",
        "policyId": "capability-smoke-effective-jvm-args-v1",
        "jvmArgumentAllowlistSha256": allowlist_sha256,
        "capabilitySmokePlanSha256": allowlist["capabilitySmokePlanSha256"],
        "javaExecutablePathId": allowlist["javaExecutablePathId"],
        "javaExecutableSha256": allowlist["javaExecutableSha256"],
        "effectiveJvmArguments": allowlist["effectiveJvmArguments"],
        "forkEvidenceSha256": evidence_hashes,
        "forkCount": expected_forks,
        "effectiveArgumentsSha256": allowlist["effectiveArgumentsSha256"],
        "aggregateStatus": "PASS",
    }


def validate_jmh_native_json(
    native: Any,
    *,
    expected_benchmark: str,
    expected_forks: int,
    effective_jvm_arguments: list[str],
    expected_warmup_iterations: int,
    expected_warmup_time: str,
    expected_measurement_iterations: int,
    expected_measurement_time: str,
    logical_operations_per_invocation: int,
) -> dict[str, Any]:
    def canonical_time(value: str) -> str:
        match = re.fullmatch(r"([1-9][0-9]*)(ms|s)", value)
        if match is None:
            raise T3EvidenceError("JMH_EXPECTED_TIME_INVALID")
        return f"{match.group(1)} {match.group(2)}"

    if (
        type(expected_forks) is not int
        or expected_forks < 1
        or type(expected_warmup_iterations) is not int
        or expected_warmup_iterations < 1
        or type(expected_measurement_iterations) is not int
        or expected_measurement_iterations < 1
        or type(logical_operations_per_invocation) is not int
        or logical_operations_per_invocation < 1
        or not isinstance(expected_warmup_time, str)
        or not isinstance(expected_measurement_time, str)
    ):
        raise T3EvidenceError("JMH_EXPECTED_EXECUTION_INVALID")
    expected_reported_jvm_arguments = reported_jvm_arguments(
        effective_jvm_arguments
    )
    canonical_warmup_time = canonical_time(expected_warmup_time)
    canonical_measurement_time = canonical_time(expected_measurement_time)
    if not isinstance(native, list) or len(native) != 1 or not isinstance(native[0], dict):
        raise T3EvidenceError("JMH_EXACT_ONE_RESULT_REQUIRED")
    result = native[0]
    actual_reported_jvm_arguments = normalized_native_reported_jvm_arguments(
        result.get("jvmArgs") if isinstance(result, dict) else None
    )
    metric = result.get("primaryMetric")
    if (
        result.get("benchmark") != expected_benchmark
        or str(result.get("mode")).lower() not in {"avgt", "averagetime"}
        or type(result.get("threads")) is not int
        or result.get("threads") != 1
        or type(result.get("forks")) is not int
        or result.get("forks") != expected_forks
        or type(result.get("warmupIterations")) is not int
        or result.get("warmupIterations") != expected_warmup_iterations
        or result.get("warmupTime") != canonical_warmup_time
        or type(result.get("measurementIterations")) is not int
        or result.get("measurementIterations")
        != expected_measurement_iterations
        or result.get("measurementTime") != canonical_measurement_time
        or actual_reported_jvm_arguments
        != expected_reported_jvm_arguments
        or result.get("params") is not None
        or not isinstance(metric, dict)
        or metric.get("scoreUnit") != "ns/op"
    ):
        raise T3EvidenceError("JMH_NATIVE_CONTRACT_MISMATCH")
    score = metric.get("score")
    raw = metric.get("rawData")
    if (
        type(score) not in (int, float)
        or not math.isfinite(score)
        or score <= 0
        or not isinstance(raw, list)
        or len(raw) != expected_forks
        or any(
            not isinstance(fork, list)
            or len(fork) != expected_measurement_iterations
            or any(
                type(value) not in (int, float)
                or not math.isfinite(value)
                or value <= 0
                for value in fork
            )
            for fork in raw
        )
    ):
        raise T3EvidenceError("JMH_NATIVE_VALUE_INVALID")
    normalized_score = float(score) / logical_operations_per_invocation
    if not math.isfinite(normalized_score) or normalized_score <= 0:
        raise T3EvidenceError("JMH_NORMALIZED_VALUE_INVALID")
    return {
        "schemaVersion": "s1.4x-scala-jmh-native-validation-v1",
        "benchmark": expected_benchmark,
        "mode": "AverageTime",
        "timeUnit": "ns/op",
        "threadCount": 1,
        "forks": expected_forks,
        "warmupIterations": expected_warmup_iterations,
        "warmupTime": canonical_warmup_time,
        "measurementIterations": expected_measurement_iterations,
        "measurementTime": canonical_measurement_time,
        "reportedJvmArguments": expected_reported_jvm_arguments,
        "effectiveJvmArguments": effective_jvm_arguments,
        "logicalOperationsPerInvocation": logical_operations_per_invocation,
        "rawScoreNsPerInvocation": float(score),
        "normalizedScoreNsPerLogicalOperation": normalized_score,
        "nativeValue": float(score),
        "rawSampleCount": sum(len(fork) for fork in raw),
        "status": "PASS",
    }


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    subcommands = value.add_subparsers(dest="command", required=True)

    capability = subcommands.add_parser("capability-result")
    capability.add_argument("--plan", type=Path, required=True)
    capability.add_argument("--toolchain-lock", type=Path, required=True)
    capability.add_argument("--evidence", type=Path, required=True)
    capability.add_argument("--output", type=Path, required=True)

    selection = subcommands.add_parser("select-profile")
    selection.add_argument("--plan", type=Path, required=True)
    selection.add_argument("--qualification", type=Path, required=True)
    selection.add_argument("--correctness-root", type=Path, required=True)
    selection.add_argument(
        "--qualification-artifact-root",
        type=Path,
        required=True,
    )
    selection.add_argument("--compiler-profiles", type=Path, required=True)
    selection.add_argument("--selected-profile-source", type=Path, required=True)
    selection.add_argument("--source-manifest", type=Path, required=True)
    selection.add_argument("--scala-root", type=Path, required=True)
    selection.add_argument("--scala-cli-bin", type=Path, required=True)
    selection.add_argument("--toolchain-lock", type=Path, required=True)
    selection.add_argument("--merged-provenance", type=Path, required=True)
    selection.add_argument("--capability-smoke-plan", type=Path, required=True)
    selection.add_argument("--jvm-allowlist", type=Path, required=True)
    selection.add_argument("--output", type=Path, required=True)

    native = subcommands.add_parser("validate-native-jmh")
    native.add_argument("--native", type=Path, required=True)
    native.add_argument("--expected-benchmark", required=True)
    native.add_argument("--expected-forks", type=int, required=True)
    native.add_argument("--expected-warmup-iterations", type=int, required=True)
    native.add_argument("--expected-warmup-time", required=True)
    native.add_argument(
        "--expected-measurement-iterations",
        type=int,
        required=True,
    )
    native.add_argument("--expected-measurement-time", required=True)
    native.add_argument("--effective-jvm-arguments", type=Path, required=True)
    native.add_argument(
        "--logical-operations-per-invocation",
        type=int,
        required=True,
    )
    native.add_argument("--output", type=Path, required=True)

    create_allowlist = subcommands.add_parser("create-jvm-allowlist")
    create_allowlist.add_argument("--fork-evidence", type=Path, required=True)
    create_allowlist.add_argument("--benchmark-plan", type=Path, required=True)
    create_allowlist.add_argument(
        "--capability-smoke-plan",
        type=Path,
        required=True,
    )
    create_allowlist.add_argument("--toolchain-lock", type=Path, required=True)
    create_allowlist.add_argument("--java-executable-sha256", required=True)
    create_allowlist.add_argument("--output", type=Path, required=True)

    effective_jvm = subcommands.add_parser("validate-effective-jvm")
    effective_jvm.add_argument("--fork-evidence", type=Path, required=True)
    effective_jvm.add_argument("--expected-forks", type=int, required=True)
    effective_jvm.add_argument("--jvm-allowlist", type=Path, required=True)
    effective_jvm.add_argument("--output", type=Path, required=True)

    feature = subcommands.add_parser("feature-result")
    feature.add_argument("--planned", type=Path, required=True)
    feature.add_argument("--capability-result", type=Path, required=True)
    feature.add_argument("--evidence", type=Path, required=True)
    feature.add_argument("--output", type=Path, required=True)

    dependency = subcommands.add_parser("dependency-audit")
    dependency.add_argument("--policy", type=Path, required=True)
    dependency.add_argument("--manifest", type=Path, required=True)
    dependency.add_argument("--project", type=Path, required=True)
    dependency.add_argument("--input", type=Path, required=True)
    dependency.add_argument("--output", type=Path, required=True)
    return value


def main() -> int:
    arguments = parser().parse_args()
    try:
        if arguments.command == "capability-result":
            plan = strict_json(arguments.plan)
            evidence = strict_json(arguments.evidence)
            result = assemble_capability_result(
                plan=plan,
                plan_sha256=sha256_file(arguments.plan),
                toolchain_identity_sha256=sha256_file(arguments.toolchain_lock),
                evidence=evidence,
            )
        elif arguments.command == "select-profile":
            correctness_paths = {
                profile: (
                    arguments.correctness_root
                    / profile
                    / "scala-profile-correctness-result.v1.json"
                )
                for profile in SCALA_PROFILES
            }
            correctness = {
                profile: strict_json(path)
                for profile, path in correctness_paths.items()
            }
            toolchain_lock = strict_json(arguments.toolchain_lock)
            pinned_scala_cli_sha256 = require_sha(
                toolchain_lock.get("scalaCli", {}).get("binarySha256"),
                "toolchainLock.scalaCli.binarySha256",
            )
            pinned_java_executable_sha256 = require_sha(
                toolchain_lock.get("jdk", {}).get("javaExecutableSha256"),
                "toolchainLock.jdk.javaExecutableSha256",
            )
            merged_provenance_sha256 = sha256_file(
                arguments.merged_provenance
            )
            if (
                toolchain_lock.get("schemaVersion")
                != "s1.4x-scala-toolchain-lock-v1"
                or toolchain_lock.get("mergedToolchainProvenanceSha256")
                != merged_provenance_sha256
                or arguments.qualification_artifact_root.resolve(strict=True)
                != arguments.qualification.parent.resolve(strict=True)
            ):
                raise T3EvidenceError("PROFILE_SELECTOR_TOOLCHAIN_INPUT_DRIFT")
            result = select_scala_profile(
                plan=strict_json(arguments.plan),
                benchmark_plan_sha256=sha256_file(arguments.plan),
                compiler_profiles=strict_json(arguments.compiler_profiles),
                compiler_profiles_sha256=sha256_file(
                    arguments.compiler_profiles
                ),
                selected_profile_source_sha256=sha256_file(
                    arguments.selected_profile_source
                ),
                correctness=correctness,
                correctness_sha256={
                    profile: sha256_file(path)
                    for profile, path in correctness_paths.items()
                },
                correctness_artifact_root=arguments.correctness_root,
                qualification=strict_json(arguments.qualification),
                qualification_sha256=sha256_file(arguments.qualification),
                qualification_artifact_root=(
                    arguments.qualification_artifact_root
                ),
                source_manifest=strict_json(arguments.source_manifest),
                source_manifest_sha256=sha256_file(
                    arguments.source_manifest
                ),
                scala_root=arguments.scala_root,
                scala_cli=arguments.scala_cli_bin,
                pinned_scala_cli_sha256=pinned_scala_cli_sha256,
                toolchain_lock_sha256=sha256_file(
                    arguments.toolchain_lock
                ),
                merged_toolchain_provenance_sha256=(
                    merged_provenance_sha256
                ),
                pinned_java_executable_sha256=(
                    pinned_java_executable_sha256
                ),
                capability_smoke_plan_sha256=sha256_file(
                    arguments.capability_smoke_plan
                ),
                jvm_allowlist=strict_json(arguments.jvm_allowlist),
                jvm_allowlist_sha256=sha256_file(
                    arguments.jvm_allowlist
                ),
                jvm_allowlist_path=arguments.jvm_allowlist,
            )
        elif arguments.command == "validate-native-jmh":
            native_value = strict_json_value(arguments.native)
            effective = strict_json(arguments.effective_jvm_arguments)
            effective_arguments = effective.get("effectiveJvmArguments")
            if not isinstance(effective_arguments, list) or not all(
                isinstance(item, str) for item in effective_arguments
            ):
                raise T3EvidenceError("EFFECTIVE_JVM_ARGUMENT_LIST_INVALID")
            result = validate_jmh_native_json(
                native_value,
                expected_benchmark=arguments.expected_benchmark,
                expected_forks=arguments.expected_forks,
                effective_jvm_arguments=effective_arguments,
                expected_warmup_iterations=arguments.expected_warmup_iterations,
                expected_warmup_time=arguments.expected_warmup_time,
                expected_measurement_iterations=(
                    arguments.expected_measurement_iterations
                ),
                expected_measurement_time=arguments.expected_measurement_time,
                logical_operations_per_invocation=(
                    arguments.logical_operations_per_invocation
                ),
            )
        elif arguments.command == "create-jvm-allowlist":
            benchmark_plan = strict_json(arguments.benchmark_plan)
            planned_arguments = benchmark_plan.get("scalaJmhPolicy", {}).get(
                "allowedCliJvmArgs"
            )
            if not isinstance(planned_arguments, list):
                raise T3EvidenceError("PLANNED_JVM_ARGUMENT_LIST_INVALID")
            result = assemble_jvm_argument_allowlist(
                forks=strict_json_value(arguments.fork_evidence),
                planned_cli_arguments=planned_arguments,
                benchmark_plan_sha256=sha256_file(arguments.benchmark_plan),
                capability_smoke_plan_sha256=sha256_file(
                    arguments.capability_smoke_plan
                ),
                toolchain_lock_sha256=sha256_file(arguments.toolchain_lock),
                java_executable_sha256=arguments.java_executable_sha256,
            )
        elif arguments.command == "validate-effective-jvm":
            forks = strict_json_value(arguments.fork_evidence)
            allowlist = strict_json(arguments.jvm_allowlist)
            result = validate_effective_jvm_evidence(
                forks,
                expected_forks=arguments.expected_forks,
                allowlist=allowlist,
                allowlist_sha256=sha256_file(arguments.jvm_allowlist),
            )
        elif arguments.command == "feature-result":
            result = assemble_feature_decision_result(
                planned=strict_json(arguments.planned),
                planned_sha256=sha256_file(arguments.planned),
                capability_sha256=sha256_file(arguments.capability_result),
                evidence=strict_json(arguments.evidence),
            )
        elif arguments.command == "dependency-audit":
            dependency_input = strict_json(arguments.input)
            dependencies = dependency_input.get("dependencies")
            findings = dependency_input.get("forbiddenSourceFindings")
            if not isinstance(dependencies, list) or not isinstance(findings, list):
                raise T3EvidenceError("DEPENDENCY_AUDIT_INPUT_INVALID")
            result = assemble_scala_dependency_audit(
                policy_sha256=sha256_file(arguments.policy),
                source_input_manifest_sha256=sha256_file(arguments.manifest),
                project_sha256=sha256_file(arguments.project),
                dependencies=dependencies,
                forbidden_source_findings=findings,
            )
        else:
            raise T3EvidenceError("UNKNOWN_COMMAND")
        write_exclusive_json(arguments.output, result)
    except (OSError, UnicodeError, ValueError, T3EvidenceError) as error:
        print(f"SCALA_T3_EVIDENCE_FAIL:{error}", file=sys.stderr)
        return 1
    print(
        f"SCALA_T3_EVIDENCE_PASS command={arguments.command} output={arguments.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
