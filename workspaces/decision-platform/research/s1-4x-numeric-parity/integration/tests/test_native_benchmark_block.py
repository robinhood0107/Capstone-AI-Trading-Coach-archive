"""Scala/Haskell native wrapper와 frozen block-result 사이의 공통 builder를 검증한다."""

from __future__ import annotations

import copy
import hashlib
import json
import sys
import tempfile
from pathlib import Path
from typing import Any
from unittest import TestCase

INTEGRATION = Path(__file__).resolve().parents[1]
BENCHMARKS = INTEGRATION.parent / "benchmarks"
sys.path.insert(0, str(INTEGRATION))
sys.path.insert(0, str(BENCHMARKS))

from gate import GateError, strict_json_load  # noqa: E402
from mark_benchmark_measurement import main as mark_measurement_main  # noqa: E402
from native_benchmark_block import (  # noqa: E402
    build_block_result,
    validate_native_contract_evidence,
)

PLAN = BENCHMARKS / "benchmark-plan.v1.json"


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
        raw = temporary / "raw/criterion.json"
        raw.parent.mkdir()
        raw.write_text('{"criterion":"raw"}\n', encoding="utf-8")
        raw_sha = hashlib.sha256(raw.read_bytes()).hexdigest()
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
        for case in cases:
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
                            "stack",
                            "bench",
                            (
                                "--benchmark-arguments=--time-limit 5 "
                                f"--json {raw} +RTS -N1 -RTS"
                            ),
                        ],
                        "environment": {
                            "S1_4X_BENCHMARK_CASE_ID": case["caseId"]
                        },
                        "exitCode": 0,
                        "rawEvidencePath": "raw/criterion.json",
                        "rawEvidenceSha256": raw_sha,
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
                    "rawEvidencePath": "raw/criterion.json",
                    "rawEvidenceSha256": raw_sha,
                    "executionReceiptPath": receipt_relative,
                    "executionReceiptSha256": hashlib.sha256(
                        receipt.read_bytes()
                    ).hexdigest(),
                    "status": "PASS",
                }
            )
        validate_native_contract_evidence(
            evidence,
            boundary_id="haskell",
            selector_id="haskell/test",
            block_directory=temporary,
            native_cases=cases,
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
            )

    def test_scala_contract_parses_jmh_forks_iterations_and_score(self) -> None:
        temporary = Path(self.enterContext(tempfile.TemporaryDirectory()))
        raw = temporary / "raw/000.json"
        receipt = temporary / "receipts/000.json"
        raw.parent.mkdir()
        receipt.parent.mkdir()
        native_cases: list[dict[str, Any]] = [
            {
                "caseId": "case-a",
                "nativeValue": 12.0,
                "samples": 30,
                "warmupIterations": 5,
                "measurementIterations": 10,
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
                            "scala-cli",
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
