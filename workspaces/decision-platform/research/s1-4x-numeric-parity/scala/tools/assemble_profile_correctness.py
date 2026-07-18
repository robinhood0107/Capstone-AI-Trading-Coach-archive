#!/usr/bin/env python3
"""A/B/C별 unit/property/registry/oracle evidence를 검증해 correctness result를 만든다."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import sys
from pathlib import Path
from typing import Any

from t3_evidence import canonical_sha256
from t3_evidence import require_portable_argv
from t3_evidence import require_sha
from t3_evidence import sha256_file
from t3_evidence import strict_json
from t3_evidence import write_exclusive_json


class ProfileCorrectnessError(ValueError):
    """Profile evidence가 실제 실행·frozen registry·source closure와 어긋났음을 나타낸다."""


def source_closure_sha256(scala_root: Path, paths: list[str]) -> str:
    digest = hashlib.sha256()
    for relative in paths:
        path = scala_root / relative
        if not path.is_file() or path.is_symlink():
            raise ProfileCorrectnessError(f"PROFILE_SOURCE_MISSING:{relative}")
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def profile_input_paths(manifest: dict[str, Any]) -> list[str]:
    files = manifest.get("files")
    if not isinstance(files, dict):
        raise ProfileCorrectnessError("SOURCE_MANIFEST_FILES_INVALID")
    return [
        path
        for path, metadata in files.items()
        if isinstance(metadata, dict)
        and metadata.get("role") in {"configuration", "main", "test"}
    ]


def validate_unit_result(
    result: dict[str, Any],
    *,
    profile: str,
    compiler_profiles_sha256: str,
    profile_options_sha256: str,
    source_manifest_sha256: str,
    scala_cli_binary_sha256: str,
    expected_inputs: list[str],
) -> None:
    expected_keys = {
        "schemaVersion",
        "profileId",
        "compilerProfilesSha256",
        "profileOptionsSha256",
        "sourceInputManifestSha256",
        "inputPaths",
        "portableArgv",
        "portableArgvSha256",
        "runtimeArgvSha256",
        "scalaCliBinarySha256",
        "exitCode",
        "stdoutSha256",
        "stderrSha256",
        "status",
    }
    if (
        set(result) != expected_keys
        or result.get("schemaVersion")
        != "s1.4x-scala-profile-unit-test-result-v1"
        or result.get("profileId") != profile
        or result.get("compilerProfilesSha256")
        != compiler_profiles_sha256
        or result.get("profileOptionsSha256") != profile_options_sha256
        or result.get("sourceInputManifestSha256")
        != source_manifest_sha256
        or result.get("inputPaths") != expected_inputs
        or result.get("scalaCliBinarySha256")
        != scala_cli_binary_sha256
        or type(result.get("exitCode")) is not int
        or result.get("exitCode") != 0
        or result.get("status") != "PASS"
    ):
        raise ProfileCorrectnessError("UNIT_TEST_RECEIPT_INVALID")
    portable = require_portable_argv(result.get("portableArgv"), "unitTest")
    if result.get("portableArgvSha256") != canonical_sha256(portable):
        raise ProfileCorrectnessError("UNIT_TEST_PORTABLE_ARGV_DRIFT")
    for key in (
        "runtimeArgvSha256",
        "stdoutSha256",
        "stderrSha256",
    ):
        require_sha(result.get(key), f"unitTest.{key}")


def validate_property_reports(
    *,
    property_report: dict[str, Any],
    execution: dict[str, Any],
    property_plan: dict[str, Any],
    property_plan_sha256: str,
    seed_corpus: dict[str, Any],
    seed_corpus_sha256: str,
    profile: str,
    source_closure_sha256_value: str,
    expected_command_sha256: str,
    expected_runner_sha256: str,
    expected_scala_cli_sha256: str,
) -> None:
    expected_ids = [
        item.get("propertyId") for item in property_plan.get("properties", [])
    ]
    properties = property_report.get("properties")
    detailed = execution.get("properties")
    property_report_keys = {
        "schemaVersion",
        "implementation",
        "propertyPlanSha256",
        "properties",
        "status",
    }
    execution_keys = {
        "schemaVersion",
        "implementation",
        "propertyPlanSha256",
        "seedCorpusSha256",
        "seedCount",
        "minimumSuccessfulPerSeed",
        "maximumDiscardRatio",
        "framework",
        "toolchainProfile",
        "scalaCliBinarySha256",
        "commandArgvSha256",
        "runnerSha256",
        "sourceClosureSha256",
        "startedAt",
        "finishedAt",
        "exitCode",
        "properties",
        "status",
    }
    frozen_seeds = seed_corpus.get("seeds")
    if (
        set(seed_corpus)
        != {
            "schemaVersion",
            "generator",
            "generatorVersion",
            "seeds",
            "replayContract",
        }
        or seed_corpus.get("schemaVersion") != "s1.4x-property-seeds-v1"
        or seed_corpus.get("generator") != "numpy-pcg64"
        or seed_corpus.get("generatorVersion") != "numpy-2.5.1"
        or not isinstance(frozen_seeds, list)
        or len(frozen_seeds) != 24
        or len(frozen_seeds) != len(set(frozen_seeds))
        or any(type(seed) is not int or seed < 0 for seed in frozen_seeds)
        or seed_corpus.get("replayContract")
        != {
            "seedInterpretation": "unsigned exact integer",
            "candidateRngParityRequired": False,
            "wrapperMustRecord": [
                "successful",
                "discarded",
                "attempted",
                "seed",
                "shrinks",
                "replayToken",
            ],
        }
    ):
        raise ProfileCorrectnessError("PROPERTY_SEED_CORPUS_INVALID")
    plan_shape_valid = (
        property_plan.get("schemaVersion") == "s1.4x-property-plan-v1"
        and property_plan.get("seedCorpusFile")
        == "contract/fixtures/property/property-seeds.v1.json"
        and property_plan.get("seedCount") == len(frozen_seeds)
        and property_plan.get("minimumSuccessfulPerProperty") == 1000
        and property_plan.get("maximumDiscardedPerProperty") == 100
        and property_plan.get("maximumDiscardRatio") == 0.1
        and property_plan.get("shrinkArtifactRequired") is True
    )
    minimum_successful_per_seed = (
        (property_plan["minimumSuccessfulPerProperty"] + len(frozen_seeds) - 1)
        // len(frozen_seeds)
        if plan_shape_valid
        else 0
    )
    if (
        not plan_shape_valid
        or len(expected_ids) != 25
        or len(expected_ids) != len(set(expected_ids))
        or set(property_report) != property_report_keys
        or property_report.get("schemaVersion")
        != "s1.4x-candidate-property-coverage-v1"
        or property_report.get("implementation") != "scala-3.8.4-jvm25"
        or property_report.get("propertyPlanSha256") != property_plan_sha256
        or property_report.get("status") != "PASS"
        or not isinstance(properties, list)
        or [item.get("propertyId") for item in properties] != expected_ids
        or set(execution) != execution_keys
        or execution.get("schemaVersion")
        != "s1.4x-candidate-property-execution-v1"
        or execution.get("implementation") != "scala-3.8.4-jvm25"
        or execution.get("propertyPlanSha256") != property_plan_sha256
        or execution.get("seedCorpusSha256") != seed_corpus_sha256
        or execution.get("seedCount") != 24
        or execution.get("minimumSuccessfulPerSeed")
        != minimum_successful_per_seed
        or execution.get("maximumDiscardRatio") != 0.1
        or execution.get("framework") != "scala-check-1.19.0"
        or execution.get("toolchainProfile") != profile
        or execution.get("scalaCliBinarySha256")
        != expected_scala_cli_sha256
        or execution.get("commandArgvSha256") != expected_command_sha256
        or execution.get("runnerSha256") != expected_runner_sha256
        or execution.get("sourceClosureSha256")
        != source_closure_sha256_value
        or type(execution.get("exitCode")) is not int
        or execution.get("exitCode") != 0
        or execution.get("status") != "PASS"
        or not isinstance(detailed, list)
        or [item.get("propertyId") for item in detailed] != expected_ids
    ):
        raise ProfileCorrectnessError("PROPERTY_REPORT_IDENTITY_INVALID")
    started = execution.get("startedAt")
    finished = execution.get("finishedAt")
    if (
        not isinstance(started, str)
        or not started.endswith("Z")
        or not isinstance(finished, str)
        or not finished.endswith("Z")
    ):
        raise ProfileCorrectnessError("PROPERTY_EXECUTION_TIMESTAMP_INVALID")
    try:
        started_at = dt.datetime.fromisoformat(
            started.removesuffix("Z") + "+00:00"
        )
        finished_at = dt.datetime.fromisoformat(
            finished.removesuffix("Z") + "+00:00"
        )
    except ValueError as error:
        raise ProfileCorrectnessError(
            "PROPERTY_EXECUTION_TIMESTAMP_INVALID"
        ) from error
    if finished_at < started_at:
        raise ProfileCorrectnessError("PROPERTY_EXECUTION_TIMESTAMP_ORDER_INVALID")

    minimum_successful = property_plan["minimumSuccessfulPerProperty"]
    maximum_discarded = property_plan["maximumDiscardedPerProperty"]
    for summary, detail in zip(properties, detailed, strict=True):
        seed_executions = detail.get("seedExecutions")
        detail_keys = {
            "propertyId",
            "successfulTests",
            "discardedTests",
            "attemptedTests",
            "shrinks",
            "seedCount",
            "seedExecutions",
            "status",
        }
        seed_keys = {
            "seedIndex",
            "originalSeed",
            "successfulTests",
            "discardedTests",
            "attemptedTests",
            "replayToken",
            "shrinks",
            "status",
        }
        if (
            set(summary)
            != {
                "propertyId",
                "successfulTests",
                "discardedTests",
                "status",
            }
            or summary.get("status") != "PASS"
            or type(summary.get("successfulTests")) is not int
            or summary["successfulTests"] < minimum_successful
            or type(summary.get("discardedTests")) is not int
            or summary["discardedTests"] < 0
            or summary["discardedTests"] > maximum_discarded
            or not isinstance(detail, dict)
            or set(detail) != detail_keys
            or detail.get("status") != "PASS"
            or detail.get("successfulTests") != summary["successfulTests"]
            or detail.get("discardedTests") != summary["discardedTests"]
            or detail.get("attemptedTests")
            != detail["successfulTests"] + detail["discardedTests"]
            or detail.get("seedCount") != 24
            or not isinstance(seed_executions, list)
            or len(seed_executions) != 24
        ):
            raise ProfileCorrectnessError(
                f"PROPERTY_RESULT_INVALID:{summary.get('propertyId')}"
            )
        replay_tokens: list[str] = []
        for seed_index, seed in enumerate(seed_executions):
            if (
                not isinstance(seed, dict)
                or set(seed) != seed_keys
                or type(seed.get("seedIndex")) is not int
                or seed.get("seedIndex") != seed_index
                or seed.get("status") != "PASS"
                or type(seed.get("successfulTests")) is not int
                or seed.get("successfulTests") != 42
                or type(seed.get("discardedTests")) is not int
                or seed["discardedTests"] < 0
                or seed["discardedTests"]
                > int(
                    execution["minimumSuccessfulPerSeed"]
                    * execution["maximumDiscardRatio"]
                )
                or type(seed.get("attemptedTests")) is not int
                or seed.get("attemptedTests")
                != seed["successfulTests"] + seed["discardedTests"]
                or seed.get("originalSeed") != frozen_seeds[seed_index]
                or not isinstance(seed.get("replayToken"), str)
                or not seed["replayToken"]
                or seed.get("shrinks") != 0
            ):
                raise ProfileCorrectnessError(
                    f"PROPERTY_SEED_INVALID:{summary.get('propertyId')}:{seed_index}"
                )
            replay_tokens.append(seed["replayToken"])
        if (
            len(replay_tokens) != len(set(replay_tokens))
            or summary["successfulTests"]
            != sum(seed["successfulTests"] for seed in seed_executions)
            or summary["discardedTests"]
            != sum(seed["discardedTests"] for seed in seed_executions)
            or detail["attemptedTests"]
            != sum(seed["attemptedTests"] for seed in seed_executions)
            or detail["shrinks"]
            != sum(seed["shrinks"] for seed in seed_executions)
        ):
            raise ProfileCorrectnessError(
                f"PROPERTY_SEED_SUMMARY_MISMATCH:{summary.get('propertyId')}"
            )


def validate_registry_report(
    report: dict[str, Any],
    *,
    function_registry: dict[str, Any],
    error_registry: dict[str, Any],
) -> None:
    expected_functions = [
        item.get("functionId") for item in function_registry.get("entries", [])
    ]
    expected_errors = error_registry.get("entries")
    functions = report.get("functions")
    errors = report.get("errors")
    if (
        set(report)
        != {
            "schemaVersion",
            "implementation",
            "functions",
            "errors",
            "status",
        }
        or
        report.get("schemaVersion")
        != "s1.4x-candidate-registry-coverage-v1"
        or report.get("implementation") != "scala-3.8.4-jvm25"
        or report.get("status") != "PASS"
        or function_registry.get("schemaVersion")
        != "s1.4x-function-registry-v1"
        or function_registry.get("functionCount") != 20
        or len(expected_functions) != 20
        or len(expected_functions) != len(set(expected_functions))
        or any(
            not isinstance(function_id, str) or not function_id
            for function_id in expected_functions
        )
        or error_registry.get("schemaVersion")
        != "s1.4x-error-registry-v1"
        or error_registry.get("errorCodeCount") != 32
        or not isinstance(expected_errors, list)
        or len(expected_errors) != 32
        or len({item.get("code") for item in expected_errors}) != 32
        or not isinstance(functions, list)
        or not isinstance(errors, list)
        or [item.get("functionId") for item in functions]
        != expected_functions
        or [item.get("errorCode") for item in errors]
        != [item.get("code") for item in expected_errors]
        or any(item.get("status") != "PASS" for item in functions)
        or any(item.get("status") != "PASS" for item in errors)
        or any(
            set(item) != {"functionId", "status"} for item in functions
        )
        or any(
            set(item)
            != {
                "errorCode",
                "track",
                "verificationMode",
                "status",
            }
            for item in errors
        )
    ):
        raise ProfileCorrectnessError("REGISTRY_REPORT_INVALID")
    for actual, expected in zip(errors, expected_errors, strict=True):
        if (
            actual.get("track") != expected.get("track")
            or actual.get("verificationMode")
            != expected.get("verificationMode")
        ):
            raise ProfileCorrectnessError("ERROR_REGISTRY_IDENTITY_DRIFT")


def validate_comparison(
    value: dict[str, Any],
    field: str,
    *,
    expected_request_id: str,
) -> None:
    if (
        set(value)
        != {
            "schemaVersion",
            "requestId",
            "implementationCount",
            "mismatchCount",
            "mismatches",
            "status",
        }
        or
        value.get("schemaVersion") != "s1.4x-comparison-report-v1"
        or value.get("requestId") != expected_request_id
        or value.get("implementationCount") != 1
        or value.get("status") != "PASS"
        or value.get("mismatchCount") != 0
        or value.get("mismatches") != []
    ):
        raise ProfileCorrectnessError(f"COMPARISON_RESULT_INVALID:{field}")


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=("A", "B", "C"), required=True)
    parser.add_argument("--scala-root", type=Path, required=True)
    parser.add_argument("--compiler-profiles", type=Path, required=True)
    parser.add_argument("--source-manifest", type=Path, required=True)
    parser.add_argument("--toolchain-lock", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--unit-test-result", type=Path, required=True)
    parser.add_argument("--property-report", type=Path, required=True)
    parser.add_argument("--registry-report", type=Path, required=True)
    parser.add_argument("--property-execution", type=Path, required=True)
    parser.add_argument("--property-plan", type=Path, required=True)
    parser.add_argument("--property-seeds", type=Path, required=True)
    parser.add_argument("--function-registry", type=Path, required=True)
    parser.add_argument("--error-registry", type=Path, required=True)
    parser.add_argument("--property-runner", type=Path, required=True)
    parser.add_argument("--property-output-dir", type=Path, required=True)
    parser.add_argument("--canonical-comparison", type=Path, required=True)
    parser.add_argument("--semantic-comparison", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()
    try:
        config = strict_json(arguments.compiler_profiles)
        manifest = strict_json(arguments.source_manifest)
        toolchain_lock = strict_json(arguments.toolchain_lock)
        unit = strict_json(arguments.unit_test_result)
        property_report = strict_json(arguments.property_report)
        registry_report = strict_json(arguments.registry_report)
        execution = strict_json(arguments.property_execution)
        property_plan = strict_json(arguments.property_plan)
        seed_corpus = strict_json(arguments.property_seeds)
        function_registry = strict_json(arguments.function_registry)
        error_registry = strict_json(arguments.error_registry)
        canonical = strict_json(arguments.canonical_comparison)
        semantic = strict_json(arguments.semantic_comparison)
        profile = arguments.profile
        options = config["profiles"][profile]["additionalOptions"]
        expected_inputs = profile_input_paths(manifest)
        manifest_sha = sha256_file(arguments.source_manifest)
        config_sha = sha256_file(arguments.compiler_profiles)
        options_sha = canonical_sha256(options)
        scala_cli_sha = toolchain_lock.get("scalaCli", {}).get("binarySha256")
        if (
            toolchain_lock.get("schemaVersion")
            != "s1.4x-scala-toolchain-lock-v1"
            or not isinstance(scala_cli_sha, str)
        ):
            raise ProfileCorrectnessError("TOOLCHAIN_LOCK_INVALID")
        require_sha(scala_cli_sha, "toolchain.scalaCliBinarySha256")
        validate_unit_result(
            unit,
            profile=profile,
            compiler_profiles_sha256=config_sha,
            profile_options_sha256=options_sha,
            source_manifest_sha256=manifest_sha,
            scala_cli_binary_sha256=scala_cli_sha,
            expected_inputs=expected_inputs,
        )
        expected_property_command_sha = canonical_sha256(
            [
                str(arguments.property_runner.resolve(strict=True)),
                "--profile",
                profile,
                "--output-dir",
                str(arguments.property_output_dir),
            ]
        )
        validate_property_reports(
            property_report=property_report,
            execution=execution,
            property_plan=property_plan,
            property_plan_sha256=sha256_file(arguments.property_plan),
            seed_corpus=seed_corpus,
            seed_corpus_sha256=sha256_file(arguments.property_seeds),
            profile=profile,
            source_closure_sha256_value=source_closure_sha256(
                arguments.scala_root,
                expected_inputs,
            ),
            expected_command_sha256=expected_property_command_sha,
            expected_runner_sha256=sha256_file(arguments.property_runner),
            expected_scala_cli_sha256=scala_cli_sha,
        )
        validate_registry_report(
            registry_report,
            function_registry=function_registry,
            error_registry=error_registry,
        )
        validate_comparison(
            canonical,
            "canonical",
            expected_request_id="s1.4x-canonical-small-v1",
        )
        validate_comparison(
            semantic,
            "semantic",
            expected_request_id="s1.4x-semantic-errors-v1",
        )
        result = {
            "schemaVersion": "s1.4x-scala-profile-correctness-v1",
            "profileId": profile,
            "compilerProfilesSha256": config_sha,
            "profileOptions": options,
            "profileOptionsSha256": options_sha,
            "sourceInputManifestSha256": manifest_sha,
            "toolchainLockSha256": sha256_file(arguments.toolchain_lock),
            "scalaCliBinarySha256": scala_cli_sha,
            "profileRunInputPaths": expected_inputs,
            "candidateSha256": sha256_file(arguments.candidate),
            "matrix": {
                "candidateResultSha256": sha256_file(
                    arguments.canonical_comparison.parent
                    / "canonical-results.json"
                ),
                "semanticResultSha256": sha256_file(
                    arguments.semantic_comparison.parent
                    / "semantic-errors.json"
                ),
                "unitTestResultSha256": sha256_file(
                    arguments.unit_test_result
                ),
                "unitStdoutSha256": unit["stdoutSha256"],
                "unitStderrSha256": unit["stderrSha256"],
                "canonicalComparisonSha256": sha256_file(
                    arguments.canonical_comparison
                ),
                "semanticComparisonSha256": sha256_file(
                    arguments.semantic_comparison
                ),
                "propertyReportSha256": sha256_file(
                    arguments.property_report
                ),
                "registryReportSha256": sha256_file(
                    arguments.registry_report
                ),
                "propertyExecutionEvidenceSha256": sha256_file(
                    arguments.property_execution
                ),
                "propertyPlanSha256": sha256_file(
                    arguments.property_plan
                ),
                "propertySeedCorpusSha256": sha256_file(
                    arguments.property_seeds
                ),
                "functionRegistrySha256": sha256_file(
                    arguments.function_registry
                ),
                "errorRegistrySha256": sha256_file(
                    arguments.error_registry
                ),
            },
            "mismatchCount": 0,
            "status": "PASS",
        }
        write_exclusive_json(arguments.output, result)
    except (
        KeyError,
        OSError,
        UnicodeError,
        ValueError,
        ProfileCorrectnessError,
    ) as error:
        print(f"SCALA_PROFILE_CORRECTNESS_FAIL:{error}", file=sys.stderr)
        return 1
    print(
        "SCALA_PROFILE_CORRECTNESS_PASS "
        f"profile={arguments.profile} result={arguments.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
