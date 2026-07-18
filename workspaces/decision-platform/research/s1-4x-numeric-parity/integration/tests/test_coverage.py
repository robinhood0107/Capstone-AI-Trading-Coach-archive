"""S1.4X 25 properties, 20 functions, 19+13 errors closure 회귀 테스트."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any, cast
from unittest import TestCase

INTEGRATION = Path(__file__).resolve().parents[1]
S1_4X = INTEGRATION.parent
CONTRACT = S1_4X / "contract"
SEED_CORPUS = CONTRACT / "fixtures/property/property-seeds.v1.json"
sys.path.insert(0, str(INTEGRATION))

from coverage_gate import CoverageError, validate_candidate_coverage  # noqa: E402


def _load(name: str) -> dict[str, Any]:
    return cast(
        dict[str, Any],
        json.loads((CONTRACT / name).read_text(encoding="utf-8")),
    )


def _candidate_reports() -> tuple[
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
]:
    plan = _load("property-plan.v1.json")
    functions = _load("function-registry.v1.json")
    errors = _load("error-registry.v1.json")
    plan_sha = hashlib.sha256(
        (CONTRACT / "property-plan.v1.json").read_bytes()
    ).hexdigest()
    seed_corpus = json.loads(SEED_CORPUS.read_text(encoding="utf-8"))
    seed_corpus_sha = hashlib.sha256(SEED_CORPUS.read_bytes()).hexdigest()
    successes_per_seed = 42
    successful_tests = len(seed_corpus["seeds"]) * successes_per_seed
    property_report = {
        "schemaVersion": "s1.4x-candidate-property-coverage-v1",
        "implementation": "candidate-test",
        "propertyPlanSha256": plan_sha,
        "properties": [
            {
                "propertyId": item["propertyId"],
                "successfulTests": successful_tests,
                "discardedTests": 0,
                "status": "PASS",
            }
            for item in plan["properties"]
        ],
        "status": "PASS",
    }
    registry_report = {
        "schemaVersion": "s1.4x-candidate-registry-coverage-v1",
        "implementation": "candidate-test",
        "functions": [
            {"functionId": item["functionId"], "status": "PASS"}
            for item in functions["entries"]
        ],
        "errors": [
            {
                "errorCode": item["code"],
                "track": item["track"],
                "verificationMode": item["verificationMode"],
                "status": "PASS",
            }
            for item in errors["entries"]
        ],
        "status": "PASS",
    }
    execution_report = {
        "schemaVersion": "s1.4x-candidate-property-execution-v1",
        "implementation": "candidate-test",
        "propertyPlanSha256": plan_sha,
        "seedCorpusSha256": seed_corpus_sha,
        "seedCount": len(seed_corpus["seeds"]),
        "minimumSuccessfulPerSeed": successes_per_seed,
        "framework": "test-framework-1.0",
        "toolchainProfile": "test-profile",
        "commandArgvSha256": "a" * 64,
        "runnerSha256": "b" * 64,
        "sourceClosureSha256": "c" * 64,
        "startedAt": "2026-07-18T12:00:00.000000Z",
        "finishedAt": "2026-07-18T12:00:01.000000Z",
        "exitCode": 0,
        "properties": [
            {
                "propertyId": item["propertyId"],
                "successfulTests": successful_tests,
                "discardedTests": 0,
                "attemptedTests": successful_tests,
                "seedCount": len(seed_corpus["seeds"]),
                "seedExecutions": [
                    {
                        "seedIndex": seed_index,
                        "originalSeed": seed,
                        "successfulTests": successes_per_seed,
                        "discardedTests": 0,
                        "attemptedTests": successes_per_seed,
                        "replayToken": f"test:{index}:{seed_index}",
                        "shrinks": 0,
                        "status": "PASS",
                    }
                    for seed_index, seed in enumerate(seed_corpus["seeds"])
                ],
                "shrinks": 0,
                "status": "PASS",
            }
            for index, item in enumerate(plan["properties"])
        ],
        "status": "PASS",
    }
    return property_report, registry_report, execution_report


class CandidateCoverageTests(TestCase):
    def test_exact_25_20_19_plus_13_and_dynamic_static_closure(self) -> None:
        property_report, registry_report, execution_report = _candidate_reports()
        evidence = validate_candidate_coverage(
            implementation_label="scala",
            property_plan_path=CONTRACT / "property-plan.v1.json",
            function_registry_path=CONTRACT / "function-registry.v1.json",
            error_registry_path=CONTRACT / "error-registry.v1.json",
            property_report=property_report,
            registry_report=registry_report,
            execution_report=execution_report,
        )
        self.assertEqual(evidence["propertyCount"], 25)
        self.assertEqual(evidence["functionCount"], 20)
        self.assertEqual(evidence["errorTrackCounts"], {"s1.4": 19, "s1.4r": 13})
        self.assertEqual(
            evidence["errorVerificationModeCounts"],
            {"processDynamic": 29, "referenceObjectModel": 1, "registryStatic": 2},
        )

    def test_missing_property_or_mode_drift_fails_closed(self) -> None:
        property_report, registry_report, execution_report = _candidate_reports()
        property_report["properties"].pop()
        with self.assertRaisesRegex(CoverageError, "PROPERTY_ID_SET_MISMATCH"):
            validate_candidate_coverage(
                implementation_label="scala",
                property_plan_path=CONTRACT / "property-plan.v1.json",
                function_registry_path=CONTRACT / "function-registry.v1.json",
                error_registry_path=CONTRACT / "error-registry.v1.json",
                property_report=property_report,
                registry_report=registry_report,
                execution_report=execution_report,
            )

        property_report, registry_report, execution_report = _candidate_reports()
        registry_report["errors"][0]["verificationMode"] = "registryStatic"
        with self.assertRaisesRegex(CoverageError, "ERROR_COVERAGE_MISMATCH"):
            validate_candidate_coverage(
                implementation_label="haskell",
                property_plan_path=CONTRACT / "property-plan.v1.json",
                function_registry_path=CONTRACT / "function-registry.v1.json",
                error_registry_path=CONTRACT / "error-registry.v1.json",
                property_report=property_report,
                registry_report=registry_report,
                execution_report=execution_report,
            )

    def test_success_and_discard_thresholds_are_enforced(self) -> None:
        property_report, registry_report, execution_report = _candidate_reports()
        property_report["properties"][0]["successfulTests"] = 999
        with self.assertRaisesRegex(CoverageError, "PROPERTY_EXECUTION_INSUFFICIENT"):
            validate_candidate_coverage(
                implementation_label="scala",
                property_plan_path=CONTRACT / "property-plan.v1.json",
                function_registry_path=CONTRACT / "function-registry.v1.json",
                error_registry_path=CONTRACT / "error-registry.v1.json",
                property_report=property_report,
                registry_report=registry_report,
                execution_report=execution_report,
            )

    def test_property_counts_must_match_actual_execution_sidecar(self) -> None:
        property_report, registry_report, execution_report = _candidate_reports()
        execution_report["properties"][0]["successfulTests"] += 1
        execution_report["properties"][0]["attemptedTests"] += 1
        with self.assertRaisesRegex(CoverageError, "PROPERTY_EXECUTION_REPORT_MISMATCH"):
            validate_candidate_coverage(
                implementation_label="scala",
                property_plan_path=CONTRACT / "property-plan.v1.json",
                function_registry_path=CONTRACT / "function-registry.v1.json",
                error_registry_path=CONTRACT / "error-registry.v1.json",
                property_report=property_report,
                registry_report=registry_report,
                execution_report=execution_report,
            )

    def test_every_frozen_seed_is_bound_in_order_with_per_seed_minimum(self) -> None:
        property_report, registry_report, execution_report = _candidate_reports()
        execution_report["properties"][0]["seedExecutions"][0]["originalSeed"] = 999
        with self.assertRaisesRegex(CoverageError, "PROPERTY_EXECUTION_SEED_MISMATCH"):
            validate_candidate_coverage(
                implementation_label="scala",
                property_plan_path=CONTRACT / "property-plan.v1.json",
                function_registry_path=CONTRACT / "function-registry.v1.json",
                error_registry_path=CONTRACT / "error-registry.v1.json",
                property_report=property_report,
                registry_report=registry_report,
                execution_report=execution_report,
            )

        property_report, registry_report, execution_report = _candidate_reports()
        execution_report["properties"][0]["seedExecutions"][0][
            "successfulTests"
        ] = 41
        with self.assertRaisesRegex(CoverageError, "PROPERTY_EXECUTION_SEED_MISMATCH"):
            validate_candidate_coverage(
                implementation_label="haskell",
                property_plan_path=CONTRACT / "property-plan.v1.json",
                function_registry_path=CONTRACT / "function-registry.v1.json",
                error_registry_path=CONTRACT / "error-registry.v1.json",
                property_report=property_report,
                registry_report=registry_report,
                execution_report=execution_report,
            )

    def test_seed_corpus_digest_is_bound_to_frozen_bytes(self) -> None:
        property_report, registry_report, execution_report = _candidate_reports()
        execution_report["seedCorpusSha256"] = "0" * 64
        with self.assertRaisesRegex(CoverageError, "PROPERTY_EXECUTION_IDENTITY_INVALID"):
            validate_candidate_coverage(
                implementation_label="scala",
                property_plan_path=CONTRACT / "property-plan.v1.json",
                function_registry_path=CONTRACT / "function-registry.v1.json",
                error_registry_path=CONTRACT / "error-registry.v1.json",
                property_report=property_report,
                registry_report=registry_report,
                execution_report=execution_report,
            )

    def test_detached_or_failed_execution_sidecar_is_rejected(self) -> None:
        property_report, registry_report, execution_report = _candidate_reports()
        execution_report["exitCode"] = 1
        with self.assertRaisesRegex(CoverageError, "PROPERTY_EXECUTION_IDENTITY_INVALID"):
            validate_candidate_coverage(
                implementation_label="haskell",
                property_plan_path=CONTRACT / "property-plan.v1.json",
                function_registry_path=CONTRACT / "function-registry.v1.json",
                error_registry_path=CONTRACT / "error-registry.v1.json",
                property_report=property_report,
                registry_report=registry_report,
                execution_report=execution_report,
            )
