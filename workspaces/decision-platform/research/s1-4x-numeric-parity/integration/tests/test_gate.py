"""S1.4X 교차 언어 process/correctness gate의 fail-closed 회귀 테스트."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest import TestCase
from unittest.mock import Mock

INTEGRATION = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(INTEGRATION))

from gate import (  # noqa: E402
    GateError,
    compare_candidate_results,
    run_candidate,
    run_transport_case,
    strict_json_load,
    validate_result_batch,
    validate_transport_failure,
)


def _request() -> dict[str, Any]:
    return {
        "schemaVersion": "s1.4x-request-v1",
        "requestId": "full-correctness-v1",
        "cases": [
            {
                "fixtureId": "case-a",
                "functionId": "historical_var",
                "arguments": {"returns": [-0.1, 0.0, 0.1], "confidence": 0.8},
            },
            {
                "fixtureId": "case-b",
                "functionId": "historical_cvar",
                "arguments": {"returns": [-0.1, 0.0, 0.1], "confidence": 0.8},
                "expectedSemanticError": "tail_empty",
            },
        ],
    }


def _result() -> dict[str, Any]:
    return {
        "schemaVersion": "s1.4x-result-batch-v1",
        "requestId": "full-correctness-v1",
        "implementation": "scala-3.8.4-jvm25",
        "results": [
            {
                "schemaVersion": "s1.4x-result-v1",
                "functionId": "historical_var",
                "fixtureId": "case-a",
                "status": "ok",
                "values": 0.08,
            },
            {
                "schemaVersion": "s1.4x-result-v1",
                "functionId": "historical_cvar",
                "fixtureId": "case-b",
                "status": "error",
                "errorCode": "tail_empty",
            },
        ],
    }


class StrictJsonTests(TestCase):
    def test_duplicate_decoded_key_is_rejected(self) -> None:
        with self.assertRaisesRegex(GateError, "DUPLICATE_JSON_KEY"):
            strict_json_load(b'{"a":1,"\\u0061":2}')

    def test_non_finite_json_constant_is_rejected(self) -> None:
        with self.assertRaisesRegex(GateError, "NON_FINITE_JSON"):
            strict_json_load(b'{"value":NaN}')


class ResultContractTests(TestCase):
    def test_result_order_and_case_identity_must_match_request(self) -> None:
        valid = validate_result_batch(_result(), _request(), label="scala")
        self.assertEqual([item["fixtureId"] for item in valid["results"]], ["case-a", "case-b"])

        reordered = _result()
        reordered["results"] = list(reversed(reordered["results"]))
        with self.assertRaisesRegex(GateError, "RESULT_CASE_IDENTITY_MISMATCH"):
            validate_result_batch(reordered, _request(), label="scala")

    def test_negative_zero_is_rejected_recursively(self) -> None:
        result = _result()
        result["results"][0]["values"] = {"nested": [0.0, -0.0]}
        with self.assertRaisesRegex(GateError, "NEGATIVE_ZERO"):
            validate_result_batch(result, _request(), label="scala")

    def test_ok_and_error_shapes_are_disjoint(self) -> None:
        result = _result()
        result["results"][0]["errorCode"] = "result_non_finite"
        with self.assertRaisesRegex(GateError, "RESULT_STATUS_SHAPE_INVALID"):
            validate_result_batch(result, _request(), label="scala")


class ProcessContractTests(TestCase):
    def test_candidate_success_requires_empty_streams_and_atomic_result(self) -> None:
        request = _request()
        with self.subTest("success"):
            temp = self.enterContext(__import__("tempfile").TemporaryDirectory())
            root = Path(temp)
            request_path = root / "request.json"
            request_path.write_text(json.dumps(request), encoding="utf-8")
            fixture_root = root / "fixtures"
            fixture_root.mkdir()
            output = root / "result.json"

            def runner(command: list[str], **_: Any) -> subprocess.CompletedProcess[bytes]:
                self.assertEqual(command[-6:], [
                    "--request",
                    str(request_path.resolve()),
                    "--fixture-root",
                    str(fixture_root.resolve()),
                    "--output",
                    str(output.resolve()),
                ])
                output.write_text(json.dumps(_result()), encoding="utf-8")
                return subprocess.CompletedProcess(command, 0, b"", b"")

            batch = run_candidate(
                label="scala",
                command_template=["/candidate", "run", "{protocol_args}"],
                request_path=request_path,
                fixture_root=fixture_root,
                output_path=output,
                runner=runner,
            )
            self.assertEqual(batch["implementation"], "scala-3.8.4-jvm25")

        with self.subTest("stdout"):
            temp = self.enterContext(__import__("tempfile").TemporaryDirectory())
            root = Path(temp)
            request_path = root / "request.json"
            request_path.write_text(json.dumps(request), encoding="utf-8")
            fixture_root = root / "fixtures"
            fixture_root.mkdir()
            output = root / "result.json"

            def noisy_runner(command: list[str], **_: Any) -> subprocess.CompletedProcess[bytes]:
                output.write_text(json.dumps(_result()), encoding="utf-8")
                return subprocess.CompletedProcess(command, 0, b"noise", b"")

            with self.assertRaisesRegex(GateError, "SUCCESS_STREAM_NOT_EMPTY"):
                run_candidate(
                    label="scala",
                    command_template=["/candidate", "run", "{protocol_args}"],
                    request_path=request_path,
                    fixture_root=fixture_root,
                    output_path=output,
                    runner=noisy_runner,
                )

    def test_transport_failure_is_single_sanitized_json_and_has_no_output(self) -> None:
        transport = {
            "schemaVersion": "s1.4x-transport-error-v1",
            "code": "manifest_invalid",
            "requestId": "full-correctness-v1",
            "fixtureId": "case-a",
            "field": "sha256",
        }
        validate_transport_failure(
            exit_code=65,
            stdout=b"",
            stderr=json.dumps(transport).encode("utf-8"),
            output_exists=False,
        )
        with self.assertRaisesRegex(GateError, "TRANSPORT_EXIT_CODE_MISMATCH"):
            validate_transport_failure(
                exit_code=64,
                stdout=b"",
                stderr=json.dumps(transport).encode("utf-8"),
                output_exists=False,
            )
        with self.assertRaisesRegex(GateError, "TRANSPORT_OUTPUT_PRESENT"):
            validate_transport_failure(
                exit_code=65,
                stdout=b"",
                stderr=json.dumps(transport).encode("utf-8"),
                output_exists=True,
            )

    def test_transport_replay_binds_expected_exit_and_code(self) -> None:
        temp = self.enterContext(__import__("tempfile").TemporaryDirectory())
        root = Path(temp)
        request = root / "invalid-request.json"
        request.write_text('{"schemaVersion":"wrong"}', encoding="utf-8")
        fixtures = root / "fixtures"
        fixtures.mkdir()
        output = root / "must-not-exist.json"

        def runner(command: list[str], **_: Any) -> subprocess.CompletedProcess[bytes]:
            return subprocess.CompletedProcess(
                command,
                64,
                b"",
                json.dumps(
                    {
                        "schemaVersion": "s1.4x-transport-error-v1",
                        "code": "request_invalid",
                    }
                ).encode("utf-8"),
            )

        result = run_transport_case(
            label="scala/request-wrong-version",
            command_template=["/candidate", "{protocol_args}"],
            request_path=request,
            fixture_root=fixtures,
            output_path=output,
            expected_exit=64,
            expected_code="request_invalid",
            runner=runner,
        )
        self.assertEqual(result["code"], "request_invalid")


class ComparatorTests(TestCase):
    def test_comparator_is_invoked_once_for_oracle_and_both_candidates(self) -> None:
        temp = self.enterContext(__import__("tempfile").TemporaryDirectory())
        root = Path(temp)
        expected = root / "expected.json"
        scala = root / "scala.json"
        haskell = root / "haskell.json"
        request = root / "request.json"
        report = root / "comparison.json"
        for path, payload in (
            (expected, _result()),
            (scala, _result()),
            (haskell, _result()),
            (request, _request()),
        ):
            path.write_text(json.dumps(payload), encoding="utf-8")

        def runner(command: list[str], **_: Any) -> subprocess.CompletedProcess[bytes]:
            self.assertEqual(command.count("--actual"), 2)
            report.write_text(
                json.dumps(
                    {
                        "schemaVersion": "s1.4x-comparison-report-v1",
                        "requestId": "full-correctness-v1",
                        "implementationCount": 2,
                        "mismatchCount": 0,
                        "mismatches": [],
                        "status": "PASS",
                    }
                ),
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(command, 0, b'{"status":"PASS"}', b"")

        result = compare_candidate_results(
            python_executable=Path(sys.executable),
            comparator=Path("/repo/oracle/compare_results.py"),
            expected=expected,
            request=request,
            candidates=[scala, haskell],
            output=report,
            runner=runner,
        )
        self.assertEqual(result["mismatchCount"], 0)

        failed = Mock(
            return_value=subprocess.CompletedProcess(
                ["compare"],
                1,
                b'{"status":"FAIL"}',
                b"",
            )
        )
        with self.assertRaisesRegex(GateError, "COMPARISON_FAILED"):
            compare_candidate_results(
                python_executable=Path(sys.executable),
                comparator=Path("/repo/oracle/compare_results.py"),
                expected=expected,
                request=request,
                candidates=[scala, haskell],
                output=root / "failed.json",
                runner=failed,
            )
