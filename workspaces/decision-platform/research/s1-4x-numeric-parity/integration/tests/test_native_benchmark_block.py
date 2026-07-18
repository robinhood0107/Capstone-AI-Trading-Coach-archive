"""Scala/Haskell native wrapper와 frozen block-result 사이의 공통 builder를 검증한다."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import statistics
import sys
import tempfile
from pathlib import Path
from typing import Any
from unittest import TestCase

INTEGRATION = Path(__file__).resolve().parents[1]
BENCHMARKS = INTEGRATION.parent / "benchmarks"
FIXTURES = INTEGRATION.parent / "contract/fixtures"
sys.path.insert(0, str(INTEGRATION))
sys.path.insert(0, str(BENCHMARKS))

from gate import GateError, strict_json_load  # noqa: E402
from mark_benchmark_measurement import main as mark_measurement_main  # noqa: E402
from native_benchmark_block import (  # noqa: E402
    build_block_result,
    validate_native_contract_evidence,
)

PLAN = BENCHMARKS / "benchmark-plan.v1.json"
EFFECTIVE_RUNTIME_ARGUMENTS_SHA256 = "e" * 64


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


class NativeBenchmarkBlockTests(TestCase):
    def test_haskell_native_batch_seconds_are_normalized_from_frozen_ops(self) -> None:
        plan = strict_json_load(PLAN)
        selector = next(
            item
            for item in plan["familySelectors"]
            if item["selectorId"] == "haskell/probabilistic-scalar"
        )
        case_by_id = {case["caseId"]: case for case in plan["cases"]}
        native = {
            "schemaVersion": "s1.4x-candidate-native-benchmark-v1",
            "boundaryId": "haskell",
            "selectorId": selector["selectorId"],
            "nativeBenchmarkMode": "Criterion",
            "nativeTimeUnit": "s",
            "profile": "baseline-o0-fasm",
            "artifactSha256": "1" * 64,
            "sourceTreeSha256": "2" * 64,
            "toolchainLockSha256": "3" * 64,
            "effectiveRuntimeArgumentsSha256": "4" * 64,
            "inputLedgerSha256": "9" * 64,
            "nativeContractValidationSha256": "b" * 64,
            "startedAt": "2026-07-18T00:00:00Z",
            "finishedAt": "2026-07-18T00:01:00Z",
            "cases": [
                {
                    "caseId": case_id,
                    "nativeValue": (
                        case_by_id[case_id]["logicalOperationsPerInvocation"]
                        * 1_000.0
                        / 1_000_000_000.0
                    ),
                    "samples": 100,
                    "warmupIterations": 0,
                    "measurementIterations": 100,
                }
                for case_id in selector["expectedCaseIds"]
            ],
            "status": "PASS",
        }
        qualification = {
            "schemaVersion": "s1.4x-timeout-qualification-v1",
            "phase": "MEASUREMENT",
            "measurementEntered": True,
            "subject": {"benchmarkSubjectCommit": "a" * 40},
            "run": {
                "runId": "run-001",
                "rotationId": "R1",
                "outerRepetition": 1,
            },
            "hostValidity": {
                "sha256": "5" * 64,
                "portableHostIdSha256": "6" * 64,
            },
        }
        report = build_block_result(
            plan=plan,
            native=native,
            qualification=qualification,
            family_id="probabilistic-scalar",
            rotation_id="R1",
            outer_repetition=1,
            run_id="run-001",
            benchmark_subject_commit="a" * 40,
            native_report_sha256="7" * 64,
            toolchain_provenance_sha256="8" * 64,
            actual_affinity_cpu_set=[0],
        )
        self.assertEqual(
            [case["normalizedNsPerLogicalOperation"] for case in report["cases"]],
            [1_000.0, 1_000.0],
        )
        self.assertEqual(
            report["block"]["nativeReportPath"],
            "run-001/R1/haskell/probabilistic-scalar/native.json",
        )

        invalid = copy.deepcopy(native)
        invalid["unexpected"] = True
        with self.assertRaisesRegex(GateError, "CANDIDATE_NATIVE_DOCUMENT_INVALID"):
            build_block_result(
                plan=plan,
                native=invalid,
                qualification=qualification,
                family_id="probabilistic-scalar",
                rotation_id="R1",
                outer_repetition=1,
                run_id="run-001",
                benchmark_subject_commit="a" * 40,
                native_report_sha256="7" * 64,
                toolchain_provenance_sha256="8" * 64,
                actual_affinity_cpu_set=[0],
            )

    def test_haskell_contract_evidence_binds_criterion_config_and_raw_bytes(
        self,
    ) -> None:
        temporary = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (temporary / "raw").mkdir()
        (temporary / "receipts").mkdir()
        input_ledger = temporary / "input-ledger.json"
        input_ledger.write_text('{"status":"fixture"}', encoding="utf-8")
        benchmark_executable = temporary / "criterion-benchmark"
        benchmark_executable.write_bytes(b"criterion executable fixture")
        ghcup = temporary / "ghcup"
        ghcup.write_bytes(b"ghcup fixture")
        stack = temporary / "stack"
        stack.write_bytes(b"stack fixture")
        stack_yaml = temporary / "stack.yaml"
        stack_yaml.write_text("resolver: ghc-9.10.3\n", encoding="utf-8")
        selected_options = ["-O0", "-fasm"]
        effective_options_sha256 = _canonical_sha256(selected_options)
        selected_profile = temporary / "haskell-selected-profile.json"
        selected_profile.write_text(
            json.dumps(
                {
                    "schemaVersion": "s1.4x-haskell-selected-profile-v1",
                    "profileId": "baseline-o0-fasm",
                    "ghcOptions": selected_options,
                    "optionsSha256": effective_options_sha256,
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        plan = strict_json_load(PLAN)
        selector = next(
            item
            for item in plan["familySelectors"]
            if item["selectorId"] == "haskell/probabilistic-scalar"
        )
        selector_id = selector["selectorId"]
        expected_case_ids = selector["expectedCaseIds"]
        receipt_provenance = {
            "planPath": str(PLAN.resolve()),
            "planSha256": hashlib.sha256(PLAN.read_bytes()).hexdigest(),
            "fixtureRootPath": str(FIXTURES.resolve()),
            "fixtureFreezeIdentitySha256": _canonical_sha256(plan["fixtureFreezeIdentity"]),
            "inputLedgerPath": str(input_ledger.resolve()),
            "inputLedgerSha256": hashlib.sha256(input_ledger.read_bytes()).hexdigest(),
            "selectorId": selector_id,
            "caseIds": expected_case_ids,
            "benchmarkExecutablePath": str(benchmark_executable.resolve()),
            "benchmarkExecutableSha256": hashlib.sha256(
                benchmark_executable.read_bytes()
            ).hexdigest(),
            "effectiveRuntimeArgumentsSha256": effective_options_sha256,
            "candidateProvenance": {
                "kind": "haskell",
                "selectedProfilePath": str(selected_profile.resolve()),
                "selectedProfileSha256": hashlib.sha256(selected_profile.read_bytes()).hexdigest(),
                "selectedProfileId": "baseline-o0-fasm",
                "effectiveCompilerFlagsSha256": effective_options_sha256,
                "ghcupPath": str(ghcup.resolve()),
                "ghcupSha256": hashlib.sha256(ghcup.read_bytes()).hexdigest(),
                "stackPath": str(stack.resolve()),
                "stackSha256": hashlib.sha256(stack.read_bytes()).hexdigest(),
                "stackYamlPath": str(stack_yaml.resolve()),
                "stackYamlSha256": hashlib.sha256(stack_yaml.read_bytes()).hexdigest(),
                "selectedGhcOptions": selected_options,
            },
        }
        cases: list[dict[str, Any]] = [
            {
                "caseId": expected_case_ids[0],
                "nativeValue": 0.1,
                "samples": 100,
                "warmupIterations": 0,
                "measurementIterations": 100,
            },
            {
                "caseId": expected_case_ids[1],
                "nativeValue": 0.2,
                "samples": 100,
                "warmupIterations": 0,
                "measurementIterations": 100,
            },
        ]
        evidence: dict[str, Any] = {
            "schemaVersion": "s1.4x-native-contract-validation-v1",
            "boundaryId": "haskell",
            "selectorId": selector_id,
            "framework": "Criterion",
            "frameworkVersion": "1.6.4.0",
            "configuration": {
                "benchmarkMode": "Criterion",
                "nativeTimeUnit": "s",
                "threads": 1,
                "timeLimitSeconds": 5,
                "rtsArguments": ["+RTS", "-N1", "-RTS"],
            },
            "cases": [],
            "status": "PASS",
        }
        statistics_cases: list[dict[str, Any]] = []
        raw_documents: dict[str, list[Any]] = {}
        for case_index, case in enumerate(cases):
            samples = [
                case["nativeValue"] * (0.9 if index % 2 == 0 else 1.1) for index in range(100)
            ]
            mean = statistics.fmean(samples)
            standard_deviation = statistics.stdev(samples)
            iteration_counts = [float(index + 1) for index in range(100)]
            elapsed_times = [
                sample * iterations
                for sample, iterations in zip(
                    samples,
                    iteration_counts,
                    strict=True,
                )
            ]
            iteration_mean = statistics.fmean(iteration_counts)
            elapsed_mean = statistics.fmean(elapsed_times)
            regression_slope = math.fsum(
                (iterations - iteration_mean) * (elapsed - elapsed_mean)
                for iterations, elapsed in zip(
                    iteration_counts,
                    elapsed_times,
                    strict=True,
                )
            ) / math.fsum((iterations - iteration_mean) ** 2 for iterations in iteration_counts)
            case["nativeValue"] = regression_slope
            estimate = {
                "estPoint": mean,
                "estError": {
                    "confIntLDX": mean * 0.1,
                    "confIntUDX": mean * 0.2,
                    "confIntCL": 0.05,
                },
            }
            raw_document: list[Any] = [
                "criterion",
                "1.6.4.0",
                [
                    {
                        "reportNumber": case_index,
                        "reportName": case["caseId"],
                        "reportKeys": [
                            "time",
                            "cpuTime",
                            "cycles",
                            "iters",
                            "allocated",
                            "peakMbAllocated",
                            "numGcs",
                            "bytesCopied",
                            "mutatorWallSeconds",
                            "mutatorCpuSeconds",
                            "gcWallSeconds",
                            "gcCpuSeconds",
                        ],
                        "reportMeasured": [
                            [
                                elapsed_times[index],
                                sample,
                                100,
                                index + 1,
                                None,
                                None,
                                None,
                                None,
                                None,
                                None,
                                None,
                                None,
                            ]
                            for index, sample in enumerate(samples)
                        ],
                        "reportAnalysis": {
                            "anRegress": [
                                {
                                    "regResponder": "cpuTime",
                                    "regCoeffs": {
                                        "iters": estimate,
                                        "y": estimate,
                                    },
                                    "regRSquare": {
                                        "estPoint": 1.0,
                                        "estError": {
                                            "confIntLDX": 0.0,
                                            "confIntUDX": 0.0,
                                            "confIntCL": 0.05,
                                        },
                                    },
                                },
                                {
                                    "regResponder": "time",
                                    "regCoeffs": {
                                        "iters": {
                                            "estPoint": regression_slope,
                                            "estError": {
                                                "confIntLDX": (regression_slope * 0.1),
                                                "confIntUDX": (regression_slope * 0.2),
                                                "confIntCL": 0.05,
                                            },
                                        },
                                        "y": estimate,
                                    },
                                    "regRSquare": {
                                        "estPoint": 1.0,
                                        "estError": {
                                            "confIntLDX": 0.0,
                                            "confIntUDX": 0.0,
                                            "confIntCL": 0.05,
                                        },
                                    },
                                },
                            ],
                            "anMean": estimate,
                            "anStdDev": {
                                "estPoint": standard_deviation,
                                "estError": {
                                    "confIntLDX": standard_deviation * 0.1,
                                    "confIntUDX": standard_deviation * 0.2,
                                    "confIntCL": 0.05,
                                },
                            },
                            "anOutlierVar": {
                                "ovEffect": "Unaffected",
                                "ovDesc": "no",
                                "ovFraction": 0.0,
                            },
                        },
                        "reportOutliers": {
                            "samplesSeen": 100,
                            "lowSevere": 0,
                            "lowMild": 0,
                            "highMild": 0,
                            "highSevere": 0,
                        },
                        "reportKDEs": [
                            {
                                "kdeType": "time",
                                "kdeValues": [min(samples), max(samples)],
                                "kdePDF": [1.0, 1.0],
                            }
                        ],
                    }
                ],
            ]
            raw_documents[case["caseId"]] = raw_document
            statistics_cases.append(
                {
                    "caseId": case["caseId"],
                    "nativeSampleCount": 100,
                    "nativeP95": max(samples),
                    "confidenceLevel": 0.95,
                    "confidenceLow": regression_slope * 0.9,
                    "confidenceHigh": regression_slope * 1.2,
                    "dispersionMetric": (
                        "criterion-bootstrap-standard-deviation-seconds-per-invocation"
                    ),
                    "dispersionValue": standard_deviation,
                    "nativeUnit": "s",
                    "logicalOperationsPerInvocation": 1,
                    "normalizedP95NsPerLogicalOperation": max(samples) * 1e9,
                    "normalizedConfidenceLowNsPerLogicalOperation": (regression_slope * 0.9 * 1e9),
                    "normalizedConfidenceHighNsPerLogicalOperation": (regression_slope * 1.2 * 1e9),
                    "normalizedDispersionNsPerLogicalOperation": (standard_deviation * 1e9),
                }
            )

        family_raw_document: list[Any] = [
            "criterion",
            "1.6.4.0",
            [raw_documents[case_id][2][0] for case_id in expected_case_ids],
        ]
        raw_path = temporary / "raw/criterion-family.json"
        receipt_path = temporary / "receipts/criterion-family.json"
        raw_relative = "raw/criterion-family.json"
        receipt_relative = "receipts/criterion-family.json"
        raw_path.write_text(json.dumps(family_raw_document), encoding="utf-8")
        raw_sha = hashlib.sha256(raw_path.read_bytes()).hexdigest()
        receipt_document: dict[str, Any] = {
            "schemaVersion": "s1.4x-native-case-execution-receipt-v1",
            "boundaryId": "haskell",
            "selectorId": selector_id,
            "caseId": None,
            "commandArgv": [
                str(ghcup.resolve()),
                "run",
                "--ghc",
                "9.10.3",
                "--stack",
                "3.11.1",
                "--",
                str(stack.resolve()),
                "--stack-yaml",
                str(stack_yaml.resolve()),
                "--system-ghc",
                "--no-install-ghc",
                "bench",
                "--ghc-options=-O0 -fasm",
                (
                    "--benchmark-arguments=--time-limit 5 "
                    f"--json {raw_path} --match prefix "
                    f"{selector['criterionPrefix']} +RTS -N1 -RTS"
                ),
            ],
            "environment": {"S1_4X_BENCHMARK_SELECTOR_ID": selector_id},
            "exitCode": 0,
            "rawEvidencePath": raw_relative,
            "rawEvidenceSha256": raw_sha,
            "provenance": receipt_provenance,
            "status": "PASS",
        }
        receipt_path.write_text(
            json.dumps(receipt_document, sort_keys=True),
            encoding="utf-8",
        )
        receipt_sha = hashlib.sha256(receipt_path.read_bytes()).hexdigest()
        evidence["cases"] = [
            {
                "caseId": case["caseId"],
                "nativeSampleCount": 100,
                "rawEvidencePath": raw_relative,
                "rawEvidenceSha256": raw_sha,
                "executionReceiptPath": receipt_relative,
                "executionReceiptSha256": receipt_sha,
                "status": "PASS",
            }
            for case in cases
        ]

        def install_family_raw(document: list[Any]) -> None:
            raw_path.write_text(json.dumps(document), encoding="utf-8")
            updated_raw_sha = hashlib.sha256(raw_path.read_bytes()).hexdigest()
            updated_receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            updated_receipt["rawEvidenceSha256"] = updated_raw_sha
            receipt_path.write_text(
                json.dumps(updated_receipt, sort_keys=True),
                encoding="utf-8",
            )
            updated_receipt_sha = hashlib.sha256(receipt_path.read_bytes()).hexdigest()
            for evidence_case in evidence["cases"]:
                evidence_case["rawEvidenceSha256"] = updated_raw_sha
                evidence_case["executionReceiptSha256"] = updated_receipt_sha

        validate_native_contract_evidence(
            evidence,
            boundary_id="haskell",
            selector_id=selector_id,
            block_directory=temporary,
            native_cases=cases,
            native_statistics_cases=statistics_cases,
            plan_path=PLAN,
            fixture_root_path=FIXTURES,
            input_ledger_path=input_ledger,
            effective_runtime_arguments_sha256=effective_options_sha256,
            profile="baseline-o0-fasm",
        )

        invalid = copy.deepcopy(evidence)
        invalid["configuration"]["timeLimitSeconds"] = 4
        with self.assertRaisesRegex(GateError, "NATIVE_CONTRACT_CONFIGURATION_INVALID"):
            validate_native_contract_evidence(
                invalid,
                boundary_id="haskell",
                selector_id=selector_id,
                block_directory=temporary,
                native_cases=cases,
                native_statistics_cases=statistics_cases,
                plan_path=PLAN,
                fixture_root_path=FIXTURES,
                input_ledger_path=input_ledger,
                effective_runtime_arguments_sha256=effective_options_sha256,
                profile="baseline-o0-fasm",
            )

        wrong_case_order = copy.deepcopy(family_raw_document)
        wrong_case_order[2][0]["reportName"], wrong_case_order[2][1]["reportName"] = (
            wrong_case_order[2][1]["reportName"],
            wrong_case_order[2][0]["reportName"],
        )
        install_family_raw(wrong_case_order)
        with self.assertRaisesRegex(GateError, "CRITERION_RAW_CASE_ORDER_INVALID"):
            validate_native_contract_evidence(
                evidence,
                boundary_id="haskell",
                selector_id=selector_id,
                block_directory=temporary,
                native_cases=cases,
                native_statistics_cases=statistics_cases,
                plan_path=PLAN,
                fixture_root_path=FIXTURES,
                input_ledger_path=input_ledger,
                effective_runtime_arguments_sha256=effective_options_sha256,
                profile="baseline-o0-fasm",
            )

        missing_bootstrap = copy.deepcopy(family_raw_document)
        missing_bootstrap[2][0]["reportAnalysis"]["anRegress"] = []
        install_family_raw(missing_bootstrap)
        with self.assertRaisesRegex(GateError, "CRITERION_RAW_CONTRACT_INVALID"):
            validate_native_contract_evidence(
                evidence,
                boundary_id="haskell",
                selector_id=selector_id,
                block_directory=temporary,
                native_cases=cases,
                native_statistics_cases=statistics_cases,
                plan_path=PLAN,
                fixture_root_path=FIXTURES,
                input_ledger_path=input_ledger,
                effective_runtime_arguments_sha256=effective_options_sha256,
                profile="baseline-o0-fasm",
            )

        install_family_raw(family_raw_document)
        forged_statistics = copy.deepcopy(statistics_cases)
        forged_statistics[0]["dispersionValue"] = (
            math.nextafter(
                forged_statistics[0]["dispersionValue"],
                math.inf,
            )
            * 2
        )
        with self.assertRaisesRegex(
            GateError,
            "CRITERION_NATIVE_STATISTICS_MISMATCH",
        ):
            validate_native_contract_evidence(
                evidence,
                boundary_id="haskell",
                selector_id=selector_id,
                block_directory=temporary,
                native_cases=cases,
                native_statistics_cases=forged_statistics,
                plan_path=PLAN,
                fixture_root_path=FIXTURES,
                input_ledger_path=input_ledger,
                effective_runtime_arguments_sha256=effective_options_sha256,
                profile="baseline-o0-fasm",
            )

        def install_receipt(document: dict[str, Any]) -> None:
            receipt_path.write_text(
                json.dumps(document, sort_keys=True),
                encoding="utf-8",
            )
            updated_receipt_sha = hashlib.sha256(receipt_path.read_bytes()).hexdigest()
            for evidence_case in evidence["cases"]:
                evidence_case["executionReceiptSha256"] = updated_receipt_sha

        direct_executable_receipt = copy.deepcopy(receipt_document)
        direct_executable_receipt["commandArgv"] = [
            str(benchmark_executable.resolve()),
            "--json",
            str(raw_path),
        ]
        install_receipt(direct_executable_receipt)
        with self.assertRaisesRegex(GateError, "NATIVE_EXECUTION_ARGV_INVALID"):
            validate_native_contract_evidence(
                evidence,
                boundary_id="haskell",
                selector_id=selector_id,
                block_directory=temporary,
                native_cases=cases,
                native_statistics_cases=statistics_cases,
                plan_path=PLAN,
                fixture_root_path=FIXTURES,
                input_ledger_path=input_ledger,
                effective_runtime_arguments_sha256=effective_options_sha256,
                profile="baseline-o0-fasm",
            )

        forged_provenance_receipt = copy.deepcopy(receipt_document)
        forged_provenance_receipt["provenance"]["candidateProvenance"]["ghcupSha256"] = "0" * 64
        install_receipt(forged_provenance_receipt)
        with self.assertRaisesRegex(
            GateError,
            "NATIVE_EXECUTION_PROVENANCE_INVALID",
        ):
            validate_native_contract_evidence(
                evidence,
                boundary_id="haskell",
                selector_id=selector_id,
                block_directory=temporary,
                native_cases=cases,
                native_statistics_cases=statistics_cases,
                plan_path=PLAN,
                fixture_root_path=FIXTURES,
                input_ledger_path=input_ledger,
                effective_runtime_arguments_sha256=effective_options_sha256,
                profile="baseline-o0-fasm",
            )

        install_receipt(receipt_document)
        alternate_raw = temporary / "raw/alternate.json"
        alternate_raw.write_bytes(raw_path.read_bytes())
        alternate_receipt_path = temporary / "receipts/alternate.json"
        alternate_receipt = copy.deepcopy(receipt_document)
        alternate_receipt["rawEvidencePath"] = "raw/alternate.json"
        alternate_receipt["commandArgv"][-1] = (
            "--benchmark-arguments=--time-limit 5 "
            f"--json {alternate_raw} --match prefix "
            f"{selector['criterionPrefix']} +RTS -N1 -RTS"
        )
        alternate_receipt_path.write_text(
            json.dumps(alternate_receipt, sort_keys=True),
            encoding="utf-8",
        )
        shared_violation = copy.deepcopy(evidence)
        shared_violation["cases"][1]["rawEvidencePath"] = "raw/alternate.json"
        shared_violation["cases"][1]["executionReceiptPath"] = "receipts/alternate.json"
        shared_violation["cases"][1]["executionReceiptSha256"] = hashlib.sha256(
            alternate_receipt_path.read_bytes()
        ).hexdigest()
        with self.assertRaisesRegex(
            GateError,
            "CRITERION_FAMILY_EVIDENCE_NOT_SHARED",
        ):
            validate_native_contract_evidence(
                shared_violation,
                boundary_id="haskell",
                selector_id=selector_id,
                block_directory=temporary,
                native_cases=cases,
                native_statistics_cases=statistics_cases,
                plan_path=PLAN,
                fixture_root_path=FIXTURES,
                input_ledger_path=input_ledger,
                effective_runtime_arguments_sha256=effective_options_sha256,
                profile="baseline-o0-fasm",
            )

    def test_scala_contract_parses_jmh_forks_iterations_and_score(self) -> None:
        temporary = Path(self.enterContext(tempfile.TemporaryDirectory()))
        raw = temporary / "raw/000.json"
        receipt = temporary / "receipts/000.json"
        raw.parent.mkdir()
        receipt.parent.mkdir()
        scala_root = temporary / "scala"
        (scala_root / "src/main/scala").mkdir(parents=True)
        (scala_root / "benchmarks").mkdir()
        project_scala = scala_root / "project.scala"
        project_scala.write_text("//> using scala 3.8.4\n", encoding="utf-8")
        selected_profile = scala_root / "selected-profile.scala"
        selected_profile.write_text(
            "// selected profile: profile-a\n",
            encoding="utf-8",
        )
        source_input_manifest = scala_root / "source-inputs.v1.json"
        source_input_manifest.write_text(
            '{"schemaVersion":"test-source-inputs-v1"}',
            encoding="utf-8",
        )
        input_ledger = temporary / "input-ledger.json"
        input_ledger.write_text('{"status":"fixture"}', encoding="utf-8")
        benchmark_executable = temporary / "scala-cli"
        benchmark_executable.write_bytes(b"Scala CLI fixture")
        jvm_capability = temporary / "effective-jvm-arguments.json"
        jvm_capability.write_text(
            '{"schemaVersion":"test-effective-jvm-arguments-v1"}',
            encoding="utf-8",
        )
        plan = strict_json_load(PLAN)
        selector_id = "scala/test"
        jmh_include_regex = r"^s1_4x\.benchmarks\.test\.case_a$"
        plan["familySelectors"].append(
            {
                "boundaryId": "scala",
                "familyId": "test",
                "selectorId": selector_id,
                "expectedCaseIds": ["case-a"],
                "jmhIncludeRegex": jmh_include_regex,
                "criterionMatchMode": None,
                "criterionPrefix": None,
                "pythonFamilyId": None,
            }
        )
        plan_path = temporary / "benchmarks/benchmark-plan.v1.json"
        plan_path.parent.mkdir()
        plan_path.write_text(json.dumps(plan, sort_keys=True), encoding="utf-8")
        native_cases: list[dict[str, Any]] = [
            {
                "caseId": "case-a",
                "nativeValue": 12.0,
                "samples": 30,
                "warmupIterations": 5,
                "measurementIterations": 10,
            }
        ]
        statistics_cases: list[dict[str, Any]] = [
            {
                "caseId": "case-a",
                "nativeSampleCount": 30,
                "nativeP95": 12.0,
                "confidenceLevel": None,
                "confidenceLow": 11.0,
                "confidenceHigh": 13.0,
                "dispersionMetric": "p95-minus-median-ns-per-invocation",
                "dispersionValue": 0.0,
                "nativeUnit": "ns",
                "logicalOperationsPerInvocation": 1,
                "normalizedP95NsPerLogicalOperation": 12.0,
                "normalizedConfidenceLowNsPerLogicalOperation": 11.0,
                "normalizedConfidenceHighNsPerLogicalOperation": 13.0,
                "normalizedDispersionNsPerLogicalOperation": 0.0,
            }
        ]
        raw_document: list[dict[str, Any]] = [
            {
                "jmhVersion": "1.37",
                "benchmark": "s1_4x.benchmarks.test.case_a",
                "mode": "avgt",
                "threads": 1,
                "forks": 3,
                "warmupIterations": 5,
                "warmupTime": "1 s",
                "measurementIterations": 10,
                "measurementTime": "1 s",
                "primaryMetric": {
                    "score": 12.0,
                    "scoreConfidence": [11.0, 13.0],
                    "scoreUnit": "ns/op",
                    "rawData": [[12.0] * 10 for _ in range(3)],
                },
            }
        ]

        def evidence_for(document: list[dict[str, Any]]) -> dict[str, Any]:
            raw.write_text(json.dumps(document), encoding="utf-8")
            raw_sha = hashlib.sha256(raw.read_bytes()).hexdigest()
            receipt.write_text(
                json.dumps(
                    {
                        "schemaVersion": ("s1.4x-native-case-execution-receipt-v1"),
                        "boundaryId": "scala",
                        "selectorId": selector_id,
                        "caseId": "case-a",
                        "commandArgv": [
                            str(benchmark_executable.resolve()),
                            "--power",
                            "run",
                            str(project_scala.resolve()),
                            str(selected_profile.resolve()),
                            str((scala_root / "src/main/scala").resolve()),
                            str((scala_root / "benchmarks").resolve()),
                            "--server=false",
                            "--jvm",
                            "system",
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
                            "-f",
                            "3",
                            "-wi",
                            "5",
                            "-i",
                            "10",
                            "-w",
                            "1s",
                            "-r",
                            "1s",
                            "-rf",
                            "json",
                            "-rff",
                            str(raw),
                            jmh_include_regex,
                        ],
                        "environment": {"S1_4X_BENCHMARK_CASE_ID": "case-a"},
                        "exitCode": 0,
                        "rawEvidencePath": "raw/000.json",
                        "rawEvidenceSha256": raw_sha,
                        "provenance": {
                            "planPath": str(plan_path.resolve()),
                            "planSha256": hashlib.sha256(
                                plan_path.read_bytes()
                            ).hexdigest(),
                            "fixtureRootPath": str(FIXTURES.resolve()),
                            "fixtureFreezeIdentitySha256": _canonical_sha256(
                                plan["fixtureFreezeIdentity"]
                            ),
                            "inputLedgerPath": str(input_ledger.resolve()),
                            "inputLedgerSha256": hashlib.sha256(
                                input_ledger.read_bytes()
                            ).hexdigest(),
                            "selectorId": selector_id,
                            "caseIds": ["case-a"],
                            "benchmarkExecutablePath": str(benchmark_executable.resolve()),
                            "benchmarkExecutableSha256": hashlib.sha256(
                                benchmark_executable.read_bytes()
                            ).hexdigest(),
                            "effectiveRuntimeArgumentsSha256": (EFFECTIVE_RUNTIME_ARGUMENTS_SHA256),
                            "candidateProvenance": {
                                "kind": "scala",
                                "selectedProfilePath": str(
                                    selected_profile.resolve()
                                ),
                                "selectedProfileSha256": hashlib.sha256(
                                    selected_profile.read_bytes()
                                ).hexdigest(),
                                "selectedProfileId": "profile-a",
                                "sourceInputManifestPath": str(
                                    source_input_manifest.resolve()
                                ),
                                "sourceInputManifestSha256": hashlib.sha256(
                                    source_input_manifest.read_bytes()
                                ).hexdigest(),
                                "effectiveJvmArgumentsCapabilityPath": str(
                                    jvm_capability.resolve()
                                ),
                                "effectiveJvmArgumentsCapabilitySha256": (
                                    hashlib.sha256(jvm_capability.read_bytes()).hexdigest()
                                ),
                            },
                        },
                        "status": "PASS",
                    },
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
            return {
                "schemaVersion": "s1.4x-native-contract-validation-v1",
                "boundaryId": "scala",
                "selectorId": selector_id,
                "framework": "JMH",
                "frameworkVersion": "1.37",
                "configuration": {
                    "benchmarkMode": "AverageTime",
                    "nativeTimeUnit": "ns",
                    "threads": 1,
                    "forks": 3,
                    "warmupIterations": 5,
                    "warmupSeconds": 1,
                    "measurementIterations": 10,
                    "measurementSeconds": 1,
                },
                "cases": [
                    {
                        "caseId": "case-a",
                        "nativeSampleCount": 30,
                        "rawEvidencePath": "raw/000.json",
                        "rawEvidenceSha256": raw_sha,
                        "executionReceiptPath": "receipts/000.json",
                        "executionReceiptSha256": hashlib.sha256(receipt.read_bytes()).hexdigest(),
                        "status": "PASS",
                    }
                ],
                "status": "PASS",
            }

        validate_native_contract_evidence(
            evidence_for(raw_document),
            boundary_id="scala",
            selector_id=selector_id,
            block_directory=temporary,
            native_cases=native_cases,
            native_statistics_cases=statistics_cases,
            plan_path=plan_path,
            fixture_root_path=FIXTURES,
            input_ledger_path=input_ledger,
            effective_runtime_arguments_sha256=(EFFECTIVE_RUNTIME_ARGUMENTS_SHA256),
            profile="profile-a",
        )

        forged_provenance = evidence_for(raw_document)
        receipt_document = json.loads(receipt.read_text(encoding="utf-8"))
        receipt_document["provenance"]["inputLedgerSha256"] = "0" * 64
        receipt.write_text(
            json.dumps(receipt_document, sort_keys=True),
            encoding="utf-8",
        )
        forged_provenance["cases"][0]["executionReceiptSha256"] = hashlib.sha256(
            receipt.read_bytes()
        ).hexdigest()
        with self.assertRaisesRegex(
            GateError,
            "NATIVE_EXECUTION_RECEIPT_INVALID",
        ):
            validate_native_contract_evidence(
                forged_provenance,
                boundary_id="scala",
                selector_id=selector_id,
                block_directory=temporary,
                native_cases=native_cases,
                native_statistics_cases=statistics_cases,
                plan_path=plan_path,
                fixture_root_path=FIXTURES,
                input_ledger_path=input_ledger,
                effective_runtime_arguments_sha256=(EFFECTIVE_RUNTIME_ARGUMENTS_SHA256),
                profile="profile-a",
            )

        direct_runner = evidence_for(raw_document)
        receipt_document = json.loads(receipt.read_text(encoding="utf-8"))
        receipt_document["commandArgv"] = [
            str(benchmark_executable.resolve()),
            "-bm",
            "avgt",
            "-rff",
            str(raw),
            jmh_include_regex,
        ]
        receipt.write_text(
            json.dumps(receipt_document, sort_keys=True),
            encoding="utf-8",
        )
        direct_runner["cases"][0]["executionReceiptSha256"] = hashlib.sha256(
            receipt.read_bytes()
        ).hexdigest()
        with self.assertRaisesRegex(GateError, "NATIVE_EXECUTION_ARGV_INVALID"):
            validate_native_contract_evidence(
                direct_runner,
                boundary_id="scala",
                selector_id=selector_id,
                block_directory=temporary,
                native_cases=native_cases,
                native_statistics_cases=statistics_cases,
                plan_path=plan_path,
                fixture_root_path=FIXTURES,
                input_ledger_path=input_ledger,
                effective_runtime_arguments_sha256=(
                    EFFECTIVE_RUNTIME_ARGUMENTS_SHA256
                ),
                profile="profile-a",
            )

        forged_profile = evidence_for(raw_document)
        receipt_document = json.loads(receipt.read_text(encoding="utf-8"))
        receipt_document["provenance"]["candidateProvenance"][
            "selectedProfileSha256"
        ] = "0" * 64
        receipt.write_text(
            json.dumps(receipt_document, sort_keys=True),
            encoding="utf-8",
        )
        forged_profile["cases"][0]["executionReceiptSha256"] = hashlib.sha256(
            receipt.read_bytes()
        ).hexdigest()
        with self.assertRaisesRegex(
            GateError,
            "NATIVE_EXECUTION_PROVENANCE_INVALID",
        ):
            validate_native_contract_evidence(
                forged_profile,
                boundary_id="scala",
                selector_id=selector_id,
                block_directory=temporary,
                native_cases=native_cases,
                native_statistics_cases=statistics_cases,
                plan_path=plan_path,
                fixture_root_path=FIXTURES,
                input_ledger_path=input_ledger,
                effective_runtime_arguments_sha256=(
                    EFFECTIVE_RUNTIME_ARGUMENTS_SHA256
                ),
                profile="profile-a",
            )

        wrong_shape = copy.deepcopy(raw_document)
        wrong_shape[0]["primaryMetric"]["rawData"] = [[12.0] * 10] * 2
        with self.assertRaisesRegex(GateError, "JMH_RAW_CONTRACT_INVALID"):
            validate_native_contract_evidence(
                evidence_for(wrong_shape),
                boundary_id="scala",
                selector_id=selector_id,
                block_directory=temporary,
                native_cases=native_cases,
                native_statistics_cases=statistics_cases,
                plan_path=plan_path,
                fixture_root_path=FIXTURES,
                input_ledger_path=input_ledger,
                effective_runtime_arguments_sha256=(EFFECTIVE_RUNTIME_ARGUMENTS_SHA256),
                profile="profile-a",
            )

        wrong_score = copy.deepcopy(raw_document)
        wrong_score[0]["primaryMetric"]["score"] = 13.0
        with self.assertRaisesRegex(GateError, "JMH_RAW_CONTRACT_INVALID"):
            validate_native_contract_evidence(
                evidence_for(wrong_score),
                boundary_id="scala",
                selector_id=selector_id,
                block_directory=temporary,
                native_cases=native_cases,
                native_statistics_cases=statistics_cases,
                plan_path=plan_path,
                fixture_root_path=FIXTURES,
                input_ledger_path=input_ledger,
                effective_runtime_arguments_sha256=(EFFECTIVE_RUNTIME_ARGUMENTS_SHA256),
                profile="profile-a",
            )

        wrong_benchmark = copy.deepcopy(raw_document)
        wrong_benchmark[0]["benchmark"] = "s1_4x.benchmarks.other.case_a"
        with self.assertRaisesRegex(GateError, "JMH_RAW_CASE_SELECTION_INVALID"):
            validate_native_contract_evidence(
                evidence_for(wrong_benchmark),
                boundary_id="scala",
                selector_id=selector_id,
                block_directory=temporary,
                native_cases=native_cases,
                native_statistics_cases=statistics_cases,
                plan_path=plan_path,
                fixture_root_path=FIXTURES,
                input_ledger_path=input_ledger,
                effective_runtime_arguments_sha256=(
                    EFFECTIVE_RUNTIME_ARGUMENTS_SHA256
                ),
                profile="profile-a",
            )

        hidden_params = copy.deepcopy(raw_document)
        hidden_params[0]["params"] = {"caseId": "case-forged"}
        with self.assertRaisesRegex(GateError, "JMH_RAW_CASE_SELECTION_INVALID"):
            validate_native_contract_evidence(
                evidence_for(hidden_params),
                boundary_id="scala",
                selector_id=selector_id,
                block_directory=temporary,
                native_cases=native_cases,
                native_statistics_cases=statistics_cases,
                plan_path=plan_path,
                fixture_root_path=FIXTURES,
                input_ledger_path=input_ledger,
                effective_runtime_arguments_sha256=(
                    EFFECTIVE_RUNTIME_ARGUMENTS_SHA256
                ),
                profile="profile-a",
            )

        wrong_version = copy.deepcopy(raw_document)
        wrong_version[0]["jmhVersion"] = "1.36"
        with self.assertRaisesRegex(GateError, "JMH_RAW_CONTRACT_INVALID"):
            validate_native_contract_evidence(
                evidence_for(wrong_version),
                boundary_id="scala",
                selector_id=selector_id,
                block_directory=temporary,
                native_cases=native_cases,
                native_statistics_cases=statistics_cases,
                plan_path=plan_path,
                fixture_root_path=FIXTURES,
                input_ledger_path=input_ledger,
                effective_runtime_arguments_sha256=(EFFECTIVE_RUNTIME_ARGUMENTS_SHA256),
                profile="profile-a",
            )

        tampered_receipt_evidence = evidence_for(raw_document)
        receipt.write_text(
            receipt.read_text(encoding="utf-8") + "\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(
            GateError,
            "NATIVE_EXECUTION_RECEIPT_DIGEST_INVALID",
        ):
            validate_native_contract_evidence(
                tampered_receipt_evidence,
                boundary_id="scala",
                selector_id=selector_id,
                block_directory=temporary,
                native_cases=native_cases,
                native_statistics_cases=statistics_cases,
                plan_path=plan_path,
                fixture_root_path=FIXTURES,
                input_ledger_path=input_ledger,
                effective_runtime_arguments_sha256=(EFFECTIVE_RUNTIME_ARGUMENTS_SHA256),
                profile="profile-a",
            )

    def test_marker_cli_performs_only_pre_run_to_measurement_transition(self) -> None:
        temporary = Path(self.enterContext(tempfile.TemporaryDirectory()))
        qualification = temporary / "timeout-qualification.json"
        qualification.write_text(
            json.dumps(
                {
                    "schemaVersion": "s1.4x-timeout-qualification-v1",
                    "phase": "PRE_RUN",
                    "measurementEntered": False,
                }
            ),
            encoding="utf-8",
        )
        self.assertEqual(
            mark_measurement_main(["--qualification", str(qualification)]),
            0,
        )
        marked = strict_json_load(qualification)
        self.assertEqual(marked["phase"], "MEASUREMENT")
        self.assertIs(marked["measurementEntered"], True)
        self.assertEqual(
            mark_measurement_main(["--qualification", str(qualification)]),
            2,
        )
