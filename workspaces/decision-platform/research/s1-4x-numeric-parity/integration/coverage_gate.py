#!/usr/bin/env python3
"""Candidate property/function/error evidence를 frozen Gate 1 registries에 exact 결합한다."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from gate import exclusive_json_write, strict_json_load


class CoverageError(ValueError):
    """Coverage report가 frozen plan/registry를 완전히 입증하지 못했음을 나타낸다."""


SHA256 = re.compile(r"^[0-9a-f]{64}$")
TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:+/=-]{0,255}$")
HASKELL_STACK_ROOT_PATH_ID = re.compile(
    r"^S1_4X_CACHE_ROOT/stack-root-property-[0-9a-f]{24}$"
)
HASKELL_PROFILE_OPTIONS = {
    "baseline-o0-fasm": ["-O0", "-fasm"],
    "optimized-o2-fasm": ["-O2", "-fasm"],
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


def _object(value: Any, *, error: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CoverageError(error)
    return value


def _exact_fields(value: Mapping[str, Any], fields: set[str], *, error: str) -> None:
    if set(value) != fields:
        raise CoverageError(f"{error}:fields={sorted(value)}")


def _indexed(
    values: Any,
    *,
    key: str,
    item_fields: set[str],
    error: str,
) -> dict[str, dict[str, Any]]:
    if not isinstance(values, list):
        raise CoverageError(error)
    indexed: dict[str, dict[str, Any]] = {}
    for raw in values:
        item = _object(raw, error=error)
        _exact_fields(item, item_fields, error=error)
        item_id = item.get(key)
        if not isinstance(item_id, str) or not item_id or item_id in indexed:
            raise CoverageError(error)
        indexed[item_id] = item
    return indexed


def _utc_timestamp(value: Any, *, error: str) -> dt.datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise CoverageError(error)
    try:
        parsed = dt.datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise CoverageError(error) from exc
    if parsed.utcoffset() != dt.timedelta(0):
        raise CoverageError(error)
    return parsed


def _valid_seed(value: Any) -> bool:
    return (type(value) is int and value >= 0) or (
        isinstance(value, str) and TOKEN.fullmatch(value) is not None
    )


def validate_candidate_coverage(
    *,
    implementation_label: str,
    property_plan_path: Path,
    function_registry_path: Path,
    error_registry_path: Path,
    property_report: Any,
    registry_report: Any,
    execution_report: Any,
) -> dict[str, Any]:
    """24-seed 25/25 property와 20/20·19+13 registry exact set을 강제한다."""

    if implementation_label not in {"scala", "haskell"}:
        raise CoverageError("IMPLEMENTATION_LABEL_INVALID")
    expected_reported_implementation = (
        "scala-3.8.4-jvm25"
        if implementation_label == "scala"
        else "haskell"
    )
    plan = _object(strict_json_load(property_plan_path), error="PROPERTY_PLAN_INVALID")
    functions = _object(
        strict_json_load(function_registry_path),
        error="FUNCTION_REGISTRY_INVALID",
    )
    errors = _object(
        strict_json_load(error_registry_path),
        error="ERROR_REGISTRY_INVALID",
    )
    if (
        plan.get("schemaVersion") != "s1.4x-property-plan-v1"
        or functions.get("schemaVersion") != "s1.4x-function-registry-v1"
        or errors.get("schemaVersion") != "s1.4x-error-registry-v1"
    ):
        raise CoverageError("FROZEN_COVERAGE_INPUT_VERSION_INVALID")
    expected_properties = {
        item["propertyId"]: item
        for item in plan.get("properties", [])
        if isinstance(item, dict) and isinstance(item.get("propertyId"), str)
    }
    expected_functions = {
        item["functionId"]: item
        for item in functions.get("entries", [])
        if isinstance(item, dict) and isinstance(item.get("functionId"), str)
    }
    expected_errors = {
        item["code"]: item
        for item in errors.get("entries", [])
        if isinstance(item, dict) and isinstance(item.get("code"), str)
    }
    if (
        len(expected_properties) != 25
        or plan.get("seedCount") != 24
        or len(expected_functions) != 20
        or functions.get("functionCount") != 20
        or len(expected_errors) != 32
        or errors.get("errorCodeCount") != 32
        or errors.get("trackCounts") != {"s1.4": 19, "s1.4r": 13}
    ):
        raise CoverageError("FROZEN_COVERAGE_COUNTS_INVALID")
    seed_corpus_relative = plan.get("seedCorpusFile")
    if (
        not isinstance(seed_corpus_relative, str)
        or not seed_corpus_relative
        or Path(seed_corpus_relative).is_absolute()
        or ".." in Path(seed_corpus_relative).parts
    ):
        raise CoverageError("PROPERTY_SEED_CORPUS_INVALID")
    s1_4x_root = property_plan_path.resolve(strict=True).parent.parent
    seed_corpus_path = s1_4x_root / seed_corpus_relative
    try:
        seed_corpus_path.resolve(strict=True).relative_to(s1_4x_root)
    except (OSError, ValueError) as exc:
        raise CoverageError("PROPERTY_SEED_CORPUS_INVALID") from exc
    if seed_corpus_path.is_symlink() or not seed_corpus_path.is_file():
        raise CoverageError("PROPERTY_SEED_CORPUS_INVALID")
    seed_corpus = _object(
        strict_json_load(seed_corpus_path),
        error="PROPERTY_SEED_CORPUS_INVALID",
    )
    _exact_fields(
        seed_corpus,
        {
            "schemaVersion",
            "generator",
            "generatorVersion",
            "seeds",
            "replayContract",
        },
        error="PROPERTY_SEED_CORPUS_INVALID",
    )
    expected_seeds = seed_corpus["seeds"]
    if (
        seed_corpus["schemaVersion"] != "s1.4x-property-seeds-v1"
        or not isinstance(expected_seeds, list)
        or len(expected_seeds) != plan["seedCount"]
        or any(not _valid_seed(seed) for seed in expected_seeds)
        or len(set(expected_seeds)) != len(expected_seeds)
    ):
        raise CoverageError("PROPERTY_SEED_CORPUS_INVALID")
    minimum_per_seed = (
        plan["minimumSuccessfulPerProperty"] + plan["seedCount"] - 1
    ) // plan["seedCount"]

    property_document = _object(
        property_report,
        error="PROPERTY_REPORT_INVALID",
    )
    _exact_fields(
        property_document,
        {
            "schemaVersion",
            "implementation",
            "propertyPlanSha256",
            "properties",
            "status",
        },
        error="PROPERTY_REPORT_INVALID",
    )
    if (
        property_document["schemaVersion"]
        != "s1.4x-candidate-property-coverage-v1"
        or property_document["status"] != "PASS"
        or property_document["implementation"]
        != expected_reported_implementation
        or property_document["propertyPlanSha256"] != _sha256(property_plan_path)
    ):
        raise CoverageError("PROPERTY_REPORT_IDENTITY_INVALID")
    actual_properties = _indexed(
        property_document["properties"],
        key="propertyId",
        item_fields={"propertyId", "successfulTests", "discardedTests", "status"},
        error="PROPERTY_REPORT_ENTRY_INVALID",
    )
    if (
        set(actual_properties) != set(expected_properties)
        or list(actual_properties) != list(expected_properties)
    ):
        raise CoverageError("PROPERTY_ID_SET_MISMATCH")
    minimum_successes = plan["minimumSuccessfulPerProperty"]
    maximum_discarded = plan["maximumDiscardedPerProperty"]
    maximum_ratio = plan["maximumDiscardRatio"]
    for property_id, result in actual_properties.items():
        successes = result["successfulTests"]
        discarded = result["discardedTests"]
        if (
            result["status"] != "PASS"
            or type(successes) is not int
            or type(discarded) is not int
            or successes < minimum_successes
            or discarded < 0
            or discarded > maximum_discarded
            or discarded / max(successes, 1) > maximum_ratio
        ):
            raise CoverageError(f"PROPERTY_EXECUTION_INSUFFICIENT:{property_id}")

    execution_document = _object(
        execution_report,
        error="PROPERTY_EXECUTION_REPORT_INVALID",
    )
    common_execution_fields = {
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
    candidate_execution_fields = (
        {"maximumDiscardRatio", "scalaCliBinarySha256"}
        if implementation_label == "scala"
        else {
            "outerCommandArgvSha256",
            "buildArgvSha256",
            "sourceInputManifestSha256",
            "selectedProfileSha256",
            "sourceTreeSha256",
            "propertyClosureSha256",
            "profileGhcOptions",
            "profileOptionsSha256",
            "stackRootPathId",
        }
    )
    _exact_fields(
        execution_document,
        common_execution_fields | candidate_execution_fields,
        error="PROPERTY_EXECUTION_REPORT_INVALID",
    )
    if (
        execution_document["schemaVersion"]
        != "s1.4x-candidate-property-execution-v1"
        or execution_document["implementation"]
        != property_document["implementation"]
        or execution_document["propertyPlanSha256"]
        != property_document["propertyPlanSha256"]
        or execution_document["seedCorpusSha256"] != _sha256(seed_corpus_path)
        or execution_document["seedCount"] != len(expected_seeds)
        or execution_document["minimumSuccessfulPerSeed"] != minimum_per_seed
        or execution_document["status"] != "PASS"
        or execution_document["exitCode"] != 0
        or not isinstance(execution_document["framework"], str)
        or TOKEN.fullmatch(execution_document["framework"]) is None
        or not isinstance(execution_document["toolchainProfile"], str)
        or TOKEN.fullmatch(execution_document["toolchainProfile"]) is None
        or any(
            SHA256.fullmatch(str(execution_document[field])) is None
            for field in (
                "commandArgvSha256",
                "runnerSha256",
                "sourceClosureSha256",
            )
        )
    ):
        raise CoverageError("PROPERTY_EXECUTION_IDENTITY_INVALID")
    if implementation_label == "scala":
        toolchain_lock_path = s1_4x_root / "scala/toolchain-lock.v1.json"
        if toolchain_lock_path.is_symlink() or not toolchain_lock_path.is_file():
            raise CoverageError("PROPERTY_EXECUTION_IDENTITY_INVALID")
        toolchain_lock = _object(
            strict_json_load(toolchain_lock_path),
            error="PROPERTY_EXECUTION_IDENTITY_INVALID",
        )
        scala_cli = toolchain_lock.get("scalaCli")
        if (
            execution_document["framework"] != "scala-check-1.19.0"
            or execution_document["toolchainProfile"] not in {"A", "B", "C"}
            or type(execution_document["maximumDiscardRatio"]) is not float
            or execution_document["maximumDiscardRatio"]
            != plan["maximumDiscardRatio"]
            or not isinstance(scala_cli, dict)
            or execution_document["scalaCliBinarySha256"]
            != scala_cli.get("binarySha256")
            or SHA256.fullmatch(
                str(execution_document["scalaCliBinarySha256"])
            )
            is None
        ):
            raise CoverageError("PROPERTY_EXECUTION_IDENTITY_INVALID")
    else:
        selected_profile_path = (
            s1_4x_root / "haskell/selected-profile.v1.json"
        )
        source_manifest_path = s1_4x_root / "haskell/source-inputs.v1.json"
        if (
            selected_profile_path.is_symlink()
            or not selected_profile_path.is_file()
            or source_manifest_path.is_symlink()
            or not source_manifest_path.is_file()
        ):
            raise CoverageError("PROPERTY_EXECUTION_IDENTITY_INVALID")
        selected_profile = _object(
            strict_json_load(selected_profile_path),
            error="PROPERTY_EXECUTION_IDENTITY_INVALID",
        )
        profile_id = selected_profile.get("profileId")
        expected_options = HASKELL_PROFILE_OPTIONS.get(str(profile_id))
        selected_options = selected_profile.get("ghcOptions")
        selected_options_sha256 = selected_profile.get("optionsSha256")
        sha_fields = (
            "outerCommandArgvSha256",
            "buildArgvSha256",
            "sourceInputManifestSha256",
            "selectedProfileSha256",
            "sourceTreeSha256",
            "propertyClosureSha256",
            "profileOptionsSha256",
        )
        if (
            selected_profile.get("schemaVersion")
            not in {
                "s1.4x-haskell-selected-profile-pending-v1",
                "s1.4x-haskell-selected-profile-v1",
            }
            or selected_profile.get("compilerVersion") != "9.10.3"
            or expected_options is None
            or selected_options != expected_options
            or selected_options_sha256 != _canonical_sha256(expected_options)
            or SHA256.fullmatch(str(selected_profile.get("sourceTreeSha256")))
            is None
            or execution_document["framework"] != "QuickCheck-2.15.0.1"
            or execution_document["toolchainProfile"]
            != f"haskell-ghc-9.10.3-{profile_id}"
            or execution_document["profileGhcOptions"] != expected_options
            or execution_document["profileOptionsSha256"]
            != selected_options_sha256
            or execution_document["sourceInputManifestSha256"]
            != _sha256(source_manifest_path)
            or execution_document["selectedProfileSha256"]
            != _sha256(selected_profile_path)
            or execution_document["sourceTreeSha256"]
            != selected_profile["sourceTreeSha256"]
            or execution_document["propertyClosureSha256"]
            != execution_document["sourceClosureSha256"]
            or HASKELL_STACK_ROOT_PATH_ID.fullmatch(
                str(execution_document["stackRootPathId"])
            )
            is None
            or any(
                SHA256.fullmatch(str(execution_document[field])) is None
                for field in sha_fields
            )
        ):
            raise CoverageError("PROPERTY_EXECUTION_IDENTITY_INVALID")
    started = _utc_timestamp(
        execution_document["startedAt"],
        error="PROPERTY_EXECUTION_TIMESTAMP_INVALID",
    )
    finished = _utc_timestamp(
        execution_document["finishedAt"],
        error="PROPERTY_EXECUTION_TIMESTAMP_INVALID",
    )
    if finished < started:
        raise CoverageError("PROPERTY_EXECUTION_TIMESTAMP_INVALID")
    actual_execution = _indexed(
        execution_document["properties"],
        key="propertyId",
        item_fields={
            "propertyId",
            "successfulTests",
            "discardedTests",
            "attemptedTests",
            "seedCount",
            "seedExecutions",
            "shrinks",
            "status",
        },
        error="PROPERTY_EXECUTION_ENTRY_INVALID",
    )
    if (
        set(actual_execution) != set(expected_properties)
        or list(actual_execution) != list(expected_properties)
    ):
        raise CoverageError("PROPERTY_EXECUTION_ID_SET_MISMATCH")
    for property_id, executed in actual_execution.items():
        reported = actual_properties[property_id]
        successes = executed["successfulTests"]
        discarded = executed["discardedTests"]
        attempted = executed["attemptedTests"]
        shrinks = executed["shrinks"]
        if (
            executed["status"] != "PASS"
            or type(successes) is not int
            or type(discarded) is not int
            or type(attempted) is not int
            or type(shrinks) is not int
            or successes != reported["successfulTests"]
            or discarded != reported["discardedTests"]
            or attempted != successes + discarded
            or executed["seedCount"] != len(expected_seeds)
            or shrinks < 0
        ):
            raise CoverageError(
                f"PROPERTY_EXECUTION_REPORT_MISMATCH:{property_id}"
            )
        seed_executions = executed["seedExecutions"]
        if (
            not isinstance(seed_executions, list)
            or len(seed_executions) != len(expected_seeds)
        ):
            raise CoverageError(
                f"PROPERTY_EXECUTION_SEED_MISMATCH:{property_id}"
            )
        summed_successes = 0
        summed_discarded = 0
        summed_attempted = 0
        summed_shrinks = 0
        for seed_index, (expected_seed, raw_seed_execution) in enumerate(
            zip(expected_seeds, seed_executions, strict=True)
        ):
            seed_execution = _object(
                raw_seed_execution,
                error=f"PROPERTY_EXECUTION_SEED_MISMATCH:{property_id}",
            )
            _exact_fields(
                seed_execution,
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
                error=f"PROPERTY_EXECUTION_SEED_MISMATCH:{property_id}",
            )
            seed_successes = seed_execution["successfulTests"]
            seed_discarded = seed_execution["discardedTests"]
            seed_attempted = seed_execution["attemptedTests"]
            seed_shrinks = seed_execution["shrinks"]
            replay_token = seed_execution["replayToken"]
            if (
                seed_execution["seedIndex"] != seed_index
                or seed_execution["originalSeed"] != expected_seed
                or type(seed_successes) is not int
                or seed_successes < minimum_per_seed
                or type(seed_discarded) is not int
                or seed_discarded < 0
                or type(seed_attempted) is not int
                or seed_attempted != seed_successes + seed_discarded
                or type(seed_shrinks) is not int
                or seed_shrinks < 0
                or not isinstance(replay_token, str)
                or TOKEN.fullmatch(replay_token) is None
                or seed_execution["status"] != "PASS"
            ):
                raise CoverageError(
                    f"PROPERTY_EXECUTION_SEED_MISMATCH:{property_id}"
                )
            summed_successes += seed_successes
            summed_discarded += seed_discarded
            summed_attempted += seed_attempted
            summed_shrinks += seed_shrinks
        if (
            successes != summed_successes
            or discarded != summed_discarded
            or attempted != summed_attempted
            or shrinks != summed_shrinks
        ):
            raise CoverageError(
                f"PROPERTY_EXECUTION_REPORT_MISMATCH:{property_id}"
            )

    registry_document = _object(
        registry_report,
        error="REGISTRY_REPORT_INVALID",
    )
    _exact_fields(
        registry_document,
        {
            "schemaVersion",
            "implementation",
            "functions",
            "errors",
            "status",
        },
        error="REGISTRY_REPORT_INVALID",
    )
    if (
        registry_document["schemaVersion"]
        != "s1.4x-candidate-registry-coverage-v1"
        or registry_document["status"] != "PASS"
        or registry_document["implementation"]
        != property_document["implementation"]
    ):
        raise CoverageError("REGISTRY_REPORT_IDENTITY_INVALID")
    actual_functions = _indexed(
        registry_document["functions"],
        key="functionId",
        item_fields={"functionId", "status"},
        error="FUNCTION_COVERAGE_INVALID",
    )
    if set(actual_functions) != set(expected_functions) or any(
        item["status"] != "PASS" for item in actual_functions.values()
    ):
        raise CoverageError("FUNCTION_ID_SET_MISMATCH")
    actual_errors = _indexed(
        registry_document["errors"],
        key="errorCode",
        item_fields={
            "errorCode",
            "track",
            "verificationMode",
            "status",
        },
        error="ERROR_COVERAGE_INVALID",
    )
    if set(actual_errors) != set(expected_errors):
        raise CoverageError("ERROR_ID_SET_MISMATCH")
    for error_code, actual in actual_errors.items():
        expected = expected_errors[error_code]
        if (
            actual["track"] != expected["track"]
            or actual["verificationMode"] != expected["verificationMode"]
            or actual["status"] != "PASS"
        ):
            raise CoverageError(f"ERROR_COVERAGE_MISMATCH:{error_code}")
    track_counts = dict(
        sorted(Counter(item["track"] for item in actual_errors.values()).items())
    )
    mode_counts = dict(
        sorted(
            Counter(
                item["verificationMode"] for item in actual_errors.values()
            ).items()
        )
    )
    if (
        track_counts != {"s1.4": 19, "s1.4r": 13}
        or mode_counts
        != {"processDynamic": 29, "referenceObjectModel": 1, "registryStatic": 2}
    ):
        raise CoverageError("ERROR_SUBSET_COUNTS_INVALID")
    return {
        "implementation": implementation_label,
        "reportedImplementation": property_document["implementation"],
        "propertyPlanSha256": property_document["propertyPlanSha256"],
        "propertyCount": len(actual_properties),
        "functionCount": len(actual_functions),
        "errorCount": len(actual_errors),
        "errorTrackCounts": track_counts,
        "errorVerificationModeCounts": mode_counts,
        "processDynamicErrorCount": mode_counts["processDynamic"],
        "staticErrorCount": (
            mode_counts["registryStatic"] + mode_counts["referenceObjectModel"]
        ),
        "propertyExecution": {
            "framework": execution_document["framework"],
            "toolchainProfile": execution_document["toolchainProfile"],
            "seedCorpusSha256": execution_document["seedCorpusSha256"],
            "seedCount": execution_document["seedCount"],
            "minimumSuccessfulPerSeed": execution_document[
                "minimumSuccessfulPerSeed"
            ],
            "runnerSha256": execution_document["runnerSha256"],
            "sourceClosureSha256": execution_document["sourceClosureSha256"],
            "startedAt": execution_document["startedAt"],
            "finishedAt": execution_document["finishedAt"],
        },
        "status": "PASS",
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--property-plan", type=Path, required=True)
    parser.add_argument("--function-registry", type=Path, required=True)
    parser.add_argument("--error-registry", type=Path, required=True)
    parser.add_argument("--scala-property-report", type=Path, required=True)
    parser.add_argument("--scala-registry-report", type=Path, required=True)
    parser.add_argument("--scala-execution-report", type=Path, required=True)
    parser.add_argument("--haskell-property-report", type=Path, required=True)
    parser.add_argument("--haskell-registry-report", type=Path, required=True)
    parser.add_argument("--haskell-execution-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        candidates = []
        for label, property_path, registry_path, execution_path in (
            (
                "scala",
                arguments.scala_property_report,
                arguments.scala_registry_report,
                arguments.scala_execution_report,
            ),
            (
                "haskell",
                arguments.haskell_property_report,
                arguments.haskell_registry_report,
                arguments.haskell_execution_report,
            ),
        ):
            candidates.append(
                validate_candidate_coverage(
                    implementation_label=label,
                    property_plan_path=arguments.property_plan,
                    function_registry_path=arguments.function_registry,
                    error_registry_path=arguments.error_registry,
                    property_report=strict_json_load(property_path),
                    registry_report=strict_json_load(registry_path),
                    execution_report=strict_json_load(execution_path),
                )
            )
        report = {
            "schemaVersion": "s1.4x-integration-coverage-v1",
            "candidateCount": 2,
            "candidates": candidates,
            "propertyCountPerCandidate": 25,
            "functionCountPerCandidate": 20,
            "errorTrackCountsPerCandidate": {"s1.4": 19, "s1.4r": 13},
            "errorVerificationModeCountsPerCandidate": {
                "processDynamic": 29,
                "referenceObjectModel": 1,
                "registryStatic": 2,
            },
            "status": "PASS",
        }
        exclusive_json_write(arguments.output.resolve(), report)
        print(json.dumps(report, allow_nan=False, sort_keys=True))
    except (CoverageError, OSError, ValueError) as exc:
        print(f"S1_4X_COVERAGE_FAIL:{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
