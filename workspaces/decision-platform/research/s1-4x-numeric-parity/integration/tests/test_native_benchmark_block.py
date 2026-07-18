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
        input_ledger = temporary / "input-ledger.json"
        input_ledger.write_text('{"status":"fixture"}', encoding="utf-8")
        benchmark_executable = temporary / "criterion-benchmark"
        benchmark_executable.write_bytes(b"criterion executable fixture")
        selected_profile = temporary / "haskell-selected-profile.json"
        selected_profile.write_text(
            json.dumps(
                {
                    "schemaVersion": "s1.4x-haskell-selected-profile-v1",
                    "profileId": "baseline-o0-fasm",
                    "optionsSha256": EFFECTIVE_RUNTIME_ARGUMENTS_SHA256,
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        plan = strict_json_load(PLAN)
        receipt_provenance = {
            "planPath": str(PLAN.resolve()),
            "planSha256": hashlib.sha256(PLAN.read_bytes()).hexdigest(),
            "fixtureRootPath": str(FIXTURES.resolve()),
            "fixtureFreezeIdentitySha256": _canonical_sha256(
                plan["fixtureFreezeIdentity"]
            ),
            "inputLedgerPath": str(input_ledger.resolve()),
            "inputLedgerSha256": hashlib.sha256(
                input_ledger.read_bytes()
            ).hexdigest(),
            "selectorId": "haskell/test",
            "caseId": None,
            "benchmarkExecutablePath": str(benchmark_executable.resolve()),
            "benchmarkExecutableSha256": hashlib.sha256(
                benchmark_executable.read_bytes()
            ).hexdigest(),
            "effectiveRuntimeArgumentsSha256": (
                EFFECTIVE_RUNTIME_ARGUMENTS_SHA256
            ),
            "candidateProvenance": {
                "kind": "haskell",
                "selectedProfilePath": str(selected_profile.resolve()),
                "selectedProfileSha256": hashlib.sha256(
                    selected_profile.read_bytes()
                ).hexdigest(),
                "selectedProfileId": "baseline-o0-fasm",
                "effectiveCompilerFlagsSha256": (
                    EFFECTIVE_RUNTIME_ARGUMENTS_SHA256
                ),
            },
        }
        cases: list[dict[str, Any]] = [
            {
                "caseId": "case-a",
                "nativeValue": 0.1,
                "samples": 100,
                "warmupIterations": 0,
                "measurementIterations": 100,
            },
            {
                "caseId": "case-b",
                "nativeValue": 0.2,
                "samples": 100,
                "warmupIterations": 0,
                "measurementIterations": 100,
            },
        ]
        evidence: dict[str, Any] = {
            "schemaVersion": "s1.4x-native-contract-validation-v1",
            "boundaryId": "haskell",
            "selectorId": "haskell/test",
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
        receipts = temporary / "receipts"
        receipts.mkdir()
        statistics_cases = []
        raw_documents: dict[str, list[Any]] = {}
        for case_index, case in enumerate(cases):
            raw = temporary / f"raw/{case_index:03d}.json"
            samples = [
                case["nativeValue"] * (0.9 if index % 2 == 0 else 1.1)
                for index in range(100)
            ]
            mean = statistics.fmean(samples)
            standard_deviation = statistics.stdev(samples)
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
                        "reportNumber": 0,
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
                                sample,
                                sample,
                                100,
                                1,
                                None,
                                None,
                                None,
                                None,
                                None,
                                None,
                                None,
                                None,
                            ]
                            for sample in samples
                        ],
                        "reportAnalysis": {
                            "anRegress": [
                                {
                                    "regResponder": "time",
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
                                }
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
            raw.write_text(json.dumps(raw_document), encoding="utf-8")
            raw_sha = hashlib.sha256(raw.read_bytes()).hexdigest()
            receipt_relative = f"receipts/{case['caseId']}.json"
            receipt = receipts / f"{case['caseId']}.json"
            receipt.write_text(
                json.dumps(
                    {
                        "schemaVersion": (
                            "s1.4x-native-case-execution-receipt-v1"
                        ),
                        "boundaryId": "haskell",
                        "selectorId": "haskell/test",
                        "caseId": case["caseId"],
                        "commandArgv": [
                            str(benchmark_executable.resolve()),
                            "--time-limit",
                            "5",
                            "--json",
                            str(raw),
                            "+RTS",
                            "-N1",
                            "-RTS",
                        ],
                        "environment": {
                            "S1_4X_BENCHMARK_CASE_ID": case["caseId"]
                        },
                        "exitCode": 0,
                        "rawEvidencePath": f"raw/{case_index:03d}.json",
                        "rawEvidenceSha256": raw_sha,
                        "provenance": {
                            **receipt_provenance,
                            "caseId": case["caseId"],
                        },
                        "status": "PASS",
                    },
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
            evidence["cases"].append(
                {
                    "caseId": case["caseId"],
                    "nativeSampleCount": 100,
                    "rawEvidencePath": f"raw/{case_index:03d}.json",
                    "rawEvidenceSha256": raw_sha,
                    "executionReceiptPath": receipt_relative,
                    "executionReceiptSha256": hashlib.sha256(
                        receipt.read_bytes()
                    ).hexdigest(),
                    "status": "PASS",
                }
            )
            statistics_cases.append(
                {
                    "caseId": case["caseId"],
                    "nativeSampleCount": 100,
                    "nativeP95": max(samples),
                    "confidenceLevel": 0.95,
                    "confidenceLow": mean * 0.9,
                    "confidenceHigh": mean * 1.2,
                    "dispersionMetric": (
                        "criterion-bootstrap-standard-deviation-"
                        "seconds-per-invocation"
                    ),
                    "dispersionValue": standard_deviation,
                    "nativeUnit": "s",
                    "logicalOperationsPerInvocation": 1,
                    "normalizedP95NsPerLogicalOperation": max(samples) * 1e9,
                    "normalizedConfidenceLowNsPerLogicalOperation": (
                        mean * 0.9 * 1e9
                    ),
                    "normalizedConfidenceHighNsPerLogicalOperation": (
                        mean * 1.2 * 1e9
                    ),
                    "normalizedDispersionNsPerLogicalOperation": (
                        standard_deviation * 1e9
                    ),
                }
            )

        def install_case_a_raw(document: list[Any]) -> None:
            raw_path = temporary / "raw/000.json"
            raw_path.write_text(json.dumps(document), encoding="utf-8")
            raw_sha = hashlib.sha256(raw_path.read_bytes()).hexdigest()
            evidence["cases"][0]["rawEvidenceSha256"] = raw_sha
            receipt_path = (
                temporary / evidence["cases"][0]["executionReceiptPath"]
            )
            receipt_document = json.loads(
                receipt_path.read_text(encoding="utf-8")
            )
            receipt_document["rawEvidenceSha256"] = raw_sha
            receipt_path.write_text(
                json.dumps(receipt_document, sort_keys=True),
                encoding="utf-8",
            )
            evidence["cases"][0]["executionReceiptSha256"] = hashlib.sha256(
                receipt_path.read_bytes()
            ).hexdigest()

        validate_native_contract_evidence(
            evidence,
            boundary_id="haskell",
            selector_id="haskell/test",
            block_directory=temporary,
            native_cases=cases,
            native_statistics_cases=statistics_cases,
            plan_path=PLAN,
            fixture_root_path=FIXTURES,
            input_ledger_path=input_ledger,
            effective_runtime_arguments_sha256=(
                EFFECTIVE_RUNTIME_ARGUMENTS_SHA256
            ),
            profile="baseline-o0-fasm",
        )

        invalid = copy.deepcopy(evidence)
        invalid["configuration"]["timeLimitSeconds"] = 4
        with self.assertRaisesRegex(GateError, "NATIVE_CONTRACT_CONFIGURATION_INVALID"):
            validate_native_contract_evidence(
                invalid,
                boundary_id="haskell",
                selector_id="haskell/test",
                block_directory=temporary,
                native_cases=cases,
                native_statistics_cases=statistics_cases,
                plan_path=PLAN,
                fixture_root_path=FIXTURES,
                input_ledger_path=input_ledger,
                effective_runtime_arguments_sha256=(
                    EFFECTIVE_RUNTIME_ARGUMENTS_SHA256
                ),
                profile="baseline-o0-fasm",
            )

        wrong_case = copy.deepcopy(raw_documents["case-a"])
        wrong_case[2][0]["reportName"] = "case-forged"
        install_case_a_raw(wrong_case)
        with self.assertRaisesRegex(GateError, "CRITERION_RAW_CONTRACT_INVALID"):
            validate_native_contract_evidence(
                evidence,
                boundary_id="haskell",
                selector_id="haskell/test",
                block_directory=temporary,
                native_cases=cases,
                native_statistics_cases=statistics_cases,
                plan_path=PLAN,
                fixture_root_path=FIXTURES,
                input_ledger_path=input_ledger,
                effective_runtime_arguments_sha256=(
                    EFFECTIVE_RUNTIME_ARGUMENTS_SHA256
                ),
                profile="baseline-o0-fasm",
            )

        missing_bootstrap = copy.deepcopy(raw_documents["case-a"])
        missing_bootstrap[2][0]["reportAnalysis"]["anRegress"] = []
        install_case_a_raw(missing_bootstrap)
        with self.assertRaisesRegex(GateError, "CRITERION_RAW_CONTRACT_INVALID"):
            validate_native_contract_evidence(
                evidence,
                boundary_id="haskell",
                selector_id="haskell/test",
                block_directory=temporary,
                native_cases=cases,
                native_statistics_cases=statistics_cases,
                plan_path=PLAN,
                fixture_root_path=FIXTURES,
                input_ledger_path=input_ledger,
                effective_runtime_arguments_sha256=(
                    EFFECTIVE_RUNTIME_ARGUMENTS_SHA256
                ),
                profile="baseline-o0-fasm",
            )

        install_case_a_raw(raw_documents["case-a"])
        forged_statistics = copy.deepcopy(statistics_cases)
        forged_statistics[0]["dispersionValue"] = math.nextafter(
            forged_statistics[0]["dispersionValue"],
            math.inf,
        ) * 2
        with self.assertRaisesRegex(
            GateError,
            "CRITERION_NATIVE_STATISTICS_MISMATCH",
        ):
            validate_native_contract_evidence(
                evidence,
                boundary_id="haskell",
                selector_id="haskell/test",
                block_directory=temporary,
                native_cases=cases,
                native_statistics_cases=forged_statistics,
                plan_path=PLAN,
                fixture_root_path=FIXTURES,
                input_ledger_path=input_ledger,
                effective_runtime_arguments_sha256=(
                    EFFECTIVE_RUNTIME_ARGUMENTS_SHA256
                ),
                profile="baseline-o0-fasm",
            )

    def test_scala_contract_parses_jmh_forks_iterations_and_score(self) -> None:
        temporary = Path(self.enterContext(tempfile.TemporaryDirectory()))
        raw = temporary / "raw/000.json"
        receipt = temporary / "receipts/000.json"
        raw.parent.mkdir()
        receipt.parent.mkdir()
        input_ledger = temporary / "input-ledger.json"
        input_ledger.write_text('{"status":"fixture"}', encoding="utf-8")
        benchmark_executable = temporary / "benchmark.jar"
        benchmark_executable.write_bytes(b"JMH executable fixture")
        jvm_capability = temporary / "effective-jvm-arguments.json"
        jvm_capability.write_text(
            '{"schemaVersion":"test-effective-jvm-arguments-v1"}',
            encoding="utf-8",
        )
        plan = strict_json_load(PLAN)
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
                "benchmark": "s1_4x.Benchmark.run",
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
                        "schemaVersion": (
                            "s1.4x-native-case-execution-receipt-v1"
                        ),
                        "boundaryId": "scala",
                        "selectorId": "scala/test",
                        "caseId": "case-a",
                        "commandArgv": [
                            "java",
                            "-jar",
                            str(benchmark_executable.resolve()),
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
                        ],
                        "environment": {
                            "S1_4X_BENCHMARK_CASE_ID": "case-a"
                        },
                        "exitCode": 0,
                        "rawEvidencePath": "raw/000.json",
                        "rawEvidenceSha256": raw_sha,
                        "provenance": {
                            "planPath": str(PLAN.resolve()),
                            "planSha256": hashlib.sha256(
                                PLAN.read_bytes()
                            ).hexdigest(),
                            "fixtureRootPath": str(FIXTURES.resolve()),
                            "fixtureFreezeIdentitySha256": _canonical_sha256(
                                plan["fixtureFreezeIdentity"]
                            ),
                            "inputLedgerPath": str(input_ledger.resolve()),
                            "inputLedgerSha256": hashlib.sha256(
                                input_ledger.read_bytes()
                            ).hexdigest(),
                            "selectorId": "scala/test",
                            "caseId": "case-a",
                            "benchmarkExecutablePath": str(
                                benchmark_executable.resolve()
                            ),
                            "benchmarkExecutableSha256": hashlib.sha256(
                                benchmark_executable.read_bytes()
                            ).hexdigest(),
                            "effectiveRuntimeArgumentsSha256": (
                                EFFECTIVE_RUNTIME_ARGUMENTS_SHA256
                            ),
                            "candidateProvenance": {
                                "kind": "scala",
                                "effectiveJvmArgumentsCapabilityPath": str(
                                    jvm_capability.resolve()
                                ),
                                "effectiveJvmArgumentsCapabilitySha256": (
                                    hashlib.sha256(
                                        jvm_capability.read_bytes()
                                    ).hexdigest()
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
                "selectorId": "scala/test",
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
                        "executionReceiptSha256": hashlib.sha256(
                            receipt.read_bytes()
                        ).hexdigest(),
                        "status": "PASS",
                    }
                ],
                "status": "PASS",
            }

        validate_native_contract_evidence(
            evidence_for(raw_document),
            boundary_id="scala",
            selector_id="scala/test",
            block_directory=temporary,
            native_cases=native_cases,
            native_statistics_cases=statistics_cases,
            plan_path=PLAN,
            fixture_root_path=FIXTURES,
            input_ledger_path=input_ledger,
            effective_runtime_arguments_sha256=(
                EFFECTIVE_RUNTIME_ARGUMENTS_SHA256
            ),
            profile="baseline",
        )

        forged_provenance = evidence_for(raw_document)
        receipt_document = json.loads(receipt.read_text(encoding="utf-8"))
        receipt_document["provenance"]["inputLedgerSha256"] = "0" * 64
        receipt.write_text(
            json.dumps(receipt_document, sort_keys=True),
            encoding="utf-8",
        )
        forged_provenance["cases"][0]["executionReceiptSha256"] = (
            hashlib.sha256(receipt.read_bytes()).hexdigest()
        )
        with self.assertRaisesRegex(
            GateError,
            "NATIVE_EXECUTION_RECEIPT_INVALID",
        ):
            validate_native_contract_evidence(
                forged_provenance,
                boundary_id="scala",
                selector_id="scala/test",
                block_directory=temporary,
                native_cases=native_cases,
                native_statistics_cases=statistics_cases,
                plan_path=PLAN,
                fixture_root_path=FIXTURES,
                input_ledger_path=input_ledger,
                effective_runtime_arguments_sha256=(
                    EFFECTIVE_RUNTIME_ARGUMENTS_SHA256
                ),
                profile="baseline",
            )

        wrong_shape = copy.deepcopy(raw_document)
        wrong_shape[0]["primaryMetric"]["rawData"] = [[12.0] * 10] * 2
        with self.assertRaisesRegex(GateError, "JMH_RAW_CONTRACT_INVALID"):
            validate_native_contract_evidence(
                evidence_for(wrong_shape),
                boundary_id="scala",
                selector_id="scala/test",
                block_directory=temporary,
                native_cases=native_cases,
                native_statistics_cases=statistics_cases,
                plan_path=PLAN,
                fixture_root_path=FIXTURES,
                input_ledger_path=input_ledger,
                effective_runtime_arguments_sha256=(
                    EFFECTIVE_RUNTIME_ARGUMENTS_SHA256
                ),
                profile="baseline",
            )

        wrong_score = copy.deepcopy(raw_document)
        wrong_score[0]["primaryMetric"]["score"] = 13.0
        with self.assertRaisesRegex(GateError, "JMH_RAW_CONTRACT_INVALID"):
            validate_native_contract_evidence(
                evidence_for(wrong_score),
                boundary_id="scala",
                selector_id="scala/test",
                block_directory=temporary,
                native_cases=native_cases,
                native_statistics_cases=statistics_cases,
                plan_path=PLAN,
                fixture_root_path=FIXTURES,
                input_ledger_path=input_ledger,
                effective_runtime_arguments_sha256=(
                    EFFECTIVE_RUNTIME_ARGUMENTS_SHA256
                ),
                profile="baseline",
            )

        wrong_version = copy.deepcopy(raw_document)
        wrong_version[0]["jmhVersion"] = "1.36"
        with self.assertRaisesRegex(GateError, "JMH_RAW_CONTRACT_INVALID"):
            validate_native_contract_evidence(
                evidence_for(wrong_version),
                boundary_id="scala",
                selector_id="scala/test",
                block_directory=temporary,
                native_cases=native_cases,
                native_statistics_cases=statistics_cases,
                plan_path=PLAN,
                fixture_root_path=FIXTURES,
                input_ledger_path=input_ledger,
                effective_runtime_arguments_sha256=(
                    EFFECTIVE_RUNTIME_ARGUMENTS_SHA256
                ),
                profile="baseline",
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
                selector_id="scala/test",
                block_directory=temporary,
                native_cases=native_cases,
                native_statistics_cases=statistics_cases,
                plan_path=PLAN,
                fixture_root_path=FIXTURES,
                input_ledger_path=input_ledger,
                effective_runtime_arguments_sha256=(
                    EFFECTIVE_RUNTIME_ARGUMENTS_SHA256
                ),
                profile="baseline",
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
