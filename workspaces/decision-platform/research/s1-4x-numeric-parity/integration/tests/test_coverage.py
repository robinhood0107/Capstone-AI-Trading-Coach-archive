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


def _candidate_reports(
    implementation_label: str = "scala",
    *,
    scala_profile: str = "B",
) -> tuple[
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
]:
    if implementation_label not in {"scala", "haskell"}:
        raise AssertionError("test candidate must be scala or haskell")
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
    reported_implementation = (
        "scala-3.8.4-jvm25"
        if implementation_label == "scala"
        else "haskell"
    )
    property_report = {
        "schemaVersion": "s1.4x-candidate-property-coverage-v1",
        "implementation": reported_implementation,
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
        "implementation": reported_implementation,
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
    if implementation_label == "scala":
        toolchain = cast(
            dict[str, Any],
            json.loads(
                (S1_4X / "scala/toolchain-lock.v1.json").read_text(
                    encoding="utf-8"
                )
            ),
        )
        candidate_fields = {
            "maximumDiscardRatio": plan["maximumDiscardRatio"],
            "scalaCliBinarySha256": toolchain["scalaCli"]["binarySha256"],
        }
        framework = "scala-check-1.19.0"
        toolchain_profile = scala_profile
    else:
        selected_path = S1_4X / "haskell/selected-profile.v1.json"
        source_manifest_path = S1_4X / "haskell/source-inputs.v1.json"
        selected = cast(
            dict[str, Any],
            json.loads(selected_path.read_text(encoding="utf-8")),
        )
        candidate_fields = {
            "outerCommandArgvSha256": "d" * 64,
            "buildArgvSha256": "e" * 64,
            "sourceInputManifestSha256": hashlib.sha256(
                source_manifest_path.read_bytes()
            ).hexdigest(),
            "selectedProfileSha256": hashlib.sha256(
                selected_path.read_bytes()
            ).hexdigest(),
            "sourceTreeSha256": selected["sourceTreeSha256"],
            "propertyClosureSha256": "c" * 64,
            "profileGhcOptions": selected["ghcOptions"],
            "profileOptionsSha256": _canonical_sha256(
                selected["ghcOptions"]
            ),
            "stackRootPathId": (
                "S1_4X_CACHE_ROOT/stack-root-property-" + "1" * 24
            ),
        }
        framework = "QuickCheck-2.15.0.1"
        toolchain_profile = (
            f"haskell-ghc-9.10.3-{selected['profileId']}"
        )
    execution_report = {
        "schemaVersion": "s1.4x-candidate-property-execution-v1",
        "implementation": reported_implementation,
        "propertyPlanSha256": plan_sha,
        "seedCorpusSha256": seed_corpus_sha,
        "seedCount": len(seed_corpus["seeds"]),
        "minimumSuccessfulPerSeed": successes_per_seed,
        "framework": framework,
        "toolchainProfile": toolchain_profile,
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
        **candidate_fields,
    }
    return property_report, registry_report, execution_report


class CandidateCoverageTests(TestCase):
    def test_exact_25_20_19_plus_13_and_dynamic_static_closure(self) -> None:
        for implementation_label in ("scala", "haskell"):
            with self.subTest(implementation_label=implementation_label):
                property_report, registry_report, execution_report = (
                    _candidate_reports(implementation_label)
                )
                evidence = validate_candidate_coverage(
                    implementation_label=implementation_label,
                    property_plan_path=CONTRACT / "property-plan.v1.json",
                    function_registry_path=CONTRACT / "function-registry.v1.json",
                    error_registry_path=CONTRACT / "error-registry.v1.json",
                    property_report=property_report,
                    registry_report=registry_report,
                    execution_report=execution_report,
                )
                self.assertEqual(evidence["propertyCount"], 25)
                self.assertEqual(evidence["functionCount"], 20)
                self.assertEqual(
                    evidence["errorTrackCounts"],
                    {"s1.4": 19, "s1.4r": 13},
                )
                self.assertEqual(
                    evidence["errorVerificationModeCounts"],
                    {
                        "processDynamic": 29,
                        "referenceObjectModel": 1,
                        "registryStatic": 2,
                    },
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

        property_report, registry_report, execution_report = _candidate_reports(
            "haskell"
        )
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

        property_report, registry_report, execution_report = _candidate_reports(
            "haskell"
        )
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
        property_report, registry_report, execution_report = _candidate_reports(
            "haskell"
        )
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

    def test_candidate_specific_execution_shape_is_exact(self) -> None:
        for implementation_label in ("scala", "haskell"):
            with self.subTest(implementation_label=implementation_label):
                property_report, registry_report, execution_report = (
                    _candidate_reports(implementation_label)
                )
                execution_report["inventedField"] = "forbidden"
                with self.assertRaisesRegex(
                    CoverageError,
                    "PROPERTY_EXECUTION_REPORT_INVALID",
                ):
                    validate_candidate_coverage(
                        implementation_label=implementation_label,
                        property_plan_path=CONTRACT / "property-plan.v1.json",
                        function_registry_path=(
                            CONTRACT / "function-registry.v1.json"
                        ),
                        error_registry_path=(
                            CONTRACT / "error-registry.v1.json"
                        ),
                        property_report=property_report,
                        registry_report=registry_report,
                        execution_report=execution_report,
                    )

    def test_scala_proven_fallback_profile_is_valid(self) -> None:
        property_report, registry_report, execution_report = (
            _candidate_reports("scala", scala_profile="A")
        )
        evidence = validate_candidate_coverage(
            implementation_label="scala",
            property_plan_path=CONTRACT / "property-plan.v1.json",
            function_registry_path=CONTRACT / "function-registry.v1.json",
            error_registry_path=CONTRACT / "error-registry.v1.json",
            property_report=property_report,
            registry_report=registry_report,
            execution_report=execution_report,
        )
        self.assertEqual(
            evidence["propertyExecution"]["toolchainProfile"],
            "A",
        )

    def test_scala_execution_is_bound_to_selected_profile_and_toolchain(
        self,
    ) -> None:
        mutations = {
            "maximumDiscardRatio": 0.2,
            "scalaCliBinarySha256": "0" * 64,
            "toolchainProfile": "D",
            "framework": "invented-framework",
        }
        for field, replacement in mutations.items():
            with self.subTest(field=field):
                property_report, registry_report, execution_report = (
                    _candidate_reports("scala")
                )
                execution_report[field] = replacement
                with self.assertRaisesRegex(
                    CoverageError,
                    "PROPERTY_EXECUTION_IDENTITY_INVALID",
                ):
                    validate_candidate_coverage(
                        implementation_label="scala",
                        property_plan_path=CONTRACT / "property-plan.v1.json",
                        function_registry_path=(
                            CONTRACT / "function-registry.v1.json"
                        ),
                        error_registry_path=(
                            CONTRACT / "error-registry.v1.json"
                        ),
                        property_report=property_report,
                        registry_report=registry_report,
                        execution_report=execution_report,
                    )

    def test_haskell_execution_is_bound_to_selected_profile_and_sources(
        self,
    ) -> None:
        mutations = {
            "sourceInputManifestSha256": "0" * 64,
            "selectedProfileSha256": "0" * 64,
            "sourceTreeSha256": "0" * 64,
            "propertyClosureSha256": "0" * 64,
            "profileGhcOptions": ["-O3"],
            "profileOptionsSha256": "0" * 64,
            "stackRootPathId": "invented/root",
            "toolchainProfile": "haskell-baseline-o0-fasm",
            "framework": "invented-framework",
        }
        for field, replacement in mutations.items():
            with self.subTest(field=field):
                property_report, registry_report, execution_report = (
                    _candidate_reports("haskell")
                )
                execution_report[field] = replacement
                with self.assertRaisesRegex(
                    CoverageError,
                    "PROPERTY_EXECUTION_IDENTITY_INVALID",
                ):
                    validate_candidate_coverage(
                        implementation_label="haskell",
                        property_plan_path=CONTRACT / "property-plan.v1.json",
                        function_registry_path=(
                            CONTRACT / "function-registry.v1.json"
                        ),
                        error_registry_path=(
                            CONTRACT / "error-registry.v1.json"
                        ),
                        property_report=property_report,
                        registry_report=registry_report,
                        execution_report=execution_report,
                    )
