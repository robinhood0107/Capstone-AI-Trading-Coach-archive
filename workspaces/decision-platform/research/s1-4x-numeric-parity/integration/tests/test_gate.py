"""S1.4X 교차 언어 process/correctness gate의 fail-closed 회귀 테스트."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest import TestCase
from unittest.mock import Mock, patch

INTEGRATION = Path(__file__).resolve().parents[1]
HASKELL_RUNNER = INTEGRATION / "tools" / "run-haskell-candidate.sh"
SCALA_REPLAY_RUNNER = INTEGRATION / "tools" / "run-scala-replay-candidate.sh"
sys.path.insert(0, str(INTEGRATION))

from gate import (  # noqa: E402
    GateError,
    compare_candidate_results,
    run_candidate,
    run_reference_capture,
    run_transport_case,
    strict_json_load,
    validate_result_batch,
    validate_transport_failure,
)
import run_full_correctness as full_correctness  # noqa: E402


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
    def _route_bound_python(self, root: Path) -> Path:
        """실행 route를 풀면 전용 module이 사라지는 최소 venv를 만든다."""

        base_python = Path(sys._base_executable).resolve(strict=True)
        venv = root / "route-bound-venv"
        python = venv / "bin" / "python"
        python.parent.mkdir(parents=True)
        python.symlink_to(base_python)
        (venv / "pyvenv.cfg").write_text(
            "\n".join(
                (
                    f"home = {base_python.parent}",
                    "include-system-site-packages = false",
                    "version = "
                    f"{sys.version_info.major}.{sys.version_info.minor}."
                    f"{sys.version_info.micro}",
                    f"executable = {base_python}",
                    "",
                )
            ),
            encoding="utf-8",
        )
        site_packages = (
            venv
            / "lib"
            / f"python{sys.version_info.major}.{sys.version_info.minor}"
            / "site-packages"
        )
        site_packages.mkdir(parents=True)
        (site_packages / "route_bound_dependency.py").write_text(
            "ROUTE_BOUND = True\n",
            encoding="utf-8",
        )
        return python

    def test_reference_and_comparator_preserve_venv_python_route(self) -> None:
        """Venv symlink argv[0]를 base Python으로 dereference하지 않는다."""

        temp = self.enterContext(__import__("tempfile").TemporaryDirectory())
        root = Path(temp)
        python = self._route_bound_python(root)
        request = root / "request.json"
        expected = root / "expected.json"
        candidate_a = root / "candidate-a.json"
        candidate_b = root / "candidate-b.json"
        for path in (request, expected, candidate_a, candidate_b):
            path.write_text("{}", encoding="utf-8")
        fixture_root = root / "fixtures"
        production = root / "production"
        research = root / "research"
        for directory in (fixture_root, production, research):
            directory.mkdir()

        capture_script = root / "capture.py"
        capture_script.write_text(
            """import argparse
import json
from pathlib import Path
import route_bound_dependency

parser = argparse.ArgumentParser()
parser.add_argument('--capture-report', type=Path, required=True)
arguments, _ = parser.parse_known_args()
arguments.capture_report.write_text(json.dumps({
    'schemaVersion': 's1.4x-reference-capture-report-v1',
    'status': 'PASS',
    'processCount': 2,
}), encoding='utf-8')
print('REFERENCE_CAPTURE_PASS route-bound')
""",
            encoding="utf-8",
        )
        capture_report = root / "capture-report.json"
        capture = run_reference_capture(
            python_executable=python,
            capture_script=capture_script,
            request_path=request,
            expected_path=expected,
            fixture_root=fixture_root,
            production_project=production,
            research_project=research,
            uv_executable=Path(sys.executable),
            scratch_root=root / "scratch",
            capture_report=capture_report,
        )
        self.assertEqual(capture["status"], "PASS")

        comparator = root / "compare.py"
        comparator.write_text(
            """import argparse
import json
from pathlib import Path
import route_bound_dependency

parser = argparse.ArgumentParser()
parser.add_argument('--output', type=Path, required=True)
arguments, _ = parser.parse_known_args()
arguments.output.write_text(json.dumps({
    'schemaVersion': 's1.4x-comparison-report-v1',
    'status': 'PASS',
    'mismatchCount': 0,
    'implementationCount': 2,
}), encoding='utf-8')
""",
            encoding="utf-8",
        )
        comparison = compare_candidate_results(
            python_executable=python,
            comparator=comparator,
            expected=expected,
            request=request,
            candidates=[candidate_a, candidate_b],
            output=root / "comparison.json",
        )
        self.assertEqual(comparison["status"], "PASS")
        self.assertEqual(comparison["mismatchCount"], 0)

    def test_reference_failure_preserves_raw_streams_and_exact_leaf(self) -> None:
        """실패 원인의 원문과 hash를 다음 run 진단용으로 남긴다."""

        temp = self.enterContext(__import__("tempfile").TemporaryDirectory())
        root = Path(temp)
        inputs = [
            root / name
            for name in ("capture.py", "request.json", "expected.json")
        ]
        for path in inputs:
            path.write_text("{}", encoding="utf-8")
        fixture_root = root / "fixtures"
        production = root / "production"
        research = root / "research"
        for directory in (fixture_root, production, research):
            directory.mkdir()
        report = root / "reference-capture.json"
        stdout = b"diagnostic stdout\n"
        stderr = b"Traceback (most recent call last):\nModuleNotFoundError: exact-leaf\n"

        failed = Mock(
            return_value=subprocess.CompletedProcess(
                ["capture"],
                1,
                stdout,
                stderr,
            )
        )
        with self.assertRaisesRegex(
            GateError,
            "REFERENCE_CAPTURE_FAILED:exit=1:.*leaf=ModuleNotFoundError: exact-leaf",
        ):
            run_reference_capture(
                python_executable=Path(sys.executable),
                capture_script=inputs[0],
                request_path=inputs[1],
                expected_path=inputs[2],
                fixture_root=fixture_root,
                production_project=production,
                research_project=research,
                uv_executable=Path(sys.executable),
                scratch_root=root / "scratch",
                capture_report=report,
                runner=failed,
            )
        self.assertEqual(
            (root / "reference-capture.json.failure.stdout.log").read_bytes(),
            stdout,
        )
        self.assertEqual(
            (root / "reference-capture.json.failure.stderr.log").read_bytes(),
            stderr,
        )

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

    def test_haskell_candidate_and_transport_forward_pinned_python_fd(self) -> None:
        """상위 gate가 봉인한 Python FD 하나만 Haskell child에 상속한다."""

        temp = self.enterContext(__import__("tempfile").TemporaryDirectory())
        root = Path(temp)
        request_path = root / "request.json"
        request_path.write_text(json.dumps(_request()), encoding="utf-8")
        invalid_request = root / "invalid-request.json"
        invalid_request.write_text('{"schemaVersion":"wrong"}', encoding="utf-8")
        fixture_root = root / "fixtures"
        fixture_root.mkdir()
        candidate_output = root / "candidate-result.json"
        scala_output = root / "scala-result.json"
        transport_output = root / "transport-result.json"
        pinned_fd = os.open(sys.executable, os.O_RDONLY)
        self.addCleanup(os.close, pinned_fd)
        pinned_environment = {
            "S1_4X_BENCHMARK_PYTHON_PINNED_FD_PATH": (
                f"/proc/self/fd/{pinned_fd}"
            )
        }

        def candidate_runner(
            command: list[str], **kwargs: Any
        ) -> subprocess.CompletedProcess[bytes]:
            self.assertEqual(kwargs.get("pass_fds"), (pinned_fd,))
            candidate_output.write_text(json.dumps(_result()), encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, b"", b"")

        def transport_runner(
            command: list[str], **kwargs: Any
        ) -> subprocess.CompletedProcess[bytes]:
            self.assertEqual(kwargs.get("pass_fds"), (pinned_fd,))
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

        def scala_runner(
            command: list[str], **kwargs: Any
        ) -> subprocess.CompletedProcess[bytes]:
            self.assertEqual(kwargs.get("pass_fds"), ())
            scala_output.write_text(json.dumps(_result()), encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, b"", b"")

        with patch.dict(os.environ, pinned_environment, clear=False):
            run_candidate(
                label="haskell",
                command_template=[
                    str(HASKELL_RUNNER),
                    "{protocol_args}",
                ],
                request_path=request_path,
                fixture_root=fixture_root,
                output_path=candidate_output,
                runner=candidate_runner,
            )
            run_transport_case(
                label="run-haskell-candidate.sh/request-wrong-version.json",
                command_template=[
                    str(HASKELL_RUNNER),
                    "{protocol_args}",
                ],
                request_path=invalid_request,
                fixture_root=fixture_root,
                output_path=transport_output,
                expected_exit=64,
                expected_code="request_invalid",
                runner=transport_runner,
            )
            run_candidate(
                label="scala",
                command_template=[str(SCALA_REPLAY_RUNNER), "{protocol_args}"],
                request_path=request_path,
                fixture_root=fixture_root,
                output_path=scala_output,
                runner=scala_runner,
            )

    def test_haskell_invalid_pinned_python_fd_fails_before_launch(self) -> None:
        """잘못됐거나 닫힌 FD는 source path 재개방 없이 거부한다."""

        temp = self.enterContext(__import__("tempfile").TemporaryDirectory())
        root = Path(temp)
        request_path = root / "request.json"
        request_path.write_text(json.dumps(_request()), encoding="utf-8")
        fixture_root = root / "fixtures"
        fixture_root.mkdir()
        output = root / "result.json"
        runner = Mock()

        for pinned_path, failure_code in (
            ("/proc/self/fd/2", "BENCHMARK_PYTHON_PINNED_FD_INVALID"),
            ("/proc/self/fd/999999", "BENCHMARK_PYTHON_PINNED_FD_UNAVAILABLE"),
        ):
            with (
                self.subTest(pinned_path=pinned_path),
                patch.dict(
                    os.environ,
                    {
                        "S1_4X_BENCHMARK_PYTHON_PINNED_FD_PATH": pinned_path,
                    },
                    clear=False,
                ),
                self.assertRaisesRegex(GateError, failure_code),
            ):
                run_candidate(
                    label="haskell",
                    command_template=[
                        str(HASKELL_RUNNER),
                        "{protocol_args}",
                    ],
                    request_path=request_path,
                    fixture_root=fixture_root,
                    output_path=output,
                    runner=runner,
                )
        runner.assert_not_called()

    def test_untyped_candidate_failure_preserves_raw_streams(self) -> None:
        """Wrapper usage 오류도 transport parse 예외에 가리지 않고 보존한다."""

        temp = self.enterContext(__import__("tempfile").TemporaryDirectory())
        root = Path(temp)
        request_path = root / "request.json"
        request_path.write_text(json.dumps(_request()), encoding="utf-8")
        fixture_root = root / "fixtures"
        fixture_root.mkdir()
        output = root / "result.json"
        stderr = b"usage: scala-runner run --request ...\n"
        failed = Mock(
            return_value=subprocess.CompletedProcess(
                ["scala-runner"],
                64,
                b"",
                stderr,
            )
        )

        with self.assertRaisesRegex(
            GateError,
            "CANDIDATE_PROCESS_FAILED:scala:exit=64:.*"
            "leaf=usage: scala-runner run --request",
        ):
            run_candidate(
                label="scala",
                command_template=["/candidate", "{protocol_args}"],
                request_path=request_path,
                fixture_root=fixture_root,
                output_path=output,
                runner=failed,
            )
        self.assertEqual(
            (root / "result.json.failure.stderr.log").read_bytes(),
            stderr,
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

        comparator_stderr = b"RuntimeError: comparator-leaf\n"
        failed = Mock(
            return_value=subprocess.CompletedProcess(
                ["compare"],
                1,
                b'{"status":"FAIL"}',
                comparator_stderr,
            )
        )
        with self.assertRaisesRegex(
            GateError,
            "COMPARISON_FAILED:exit=1:.*leaf=RuntimeError: comparator-leaf",
        ):
            compare_candidate_results(
                python_executable=Path(sys.executable),
                comparator=Path("/repo/oracle/compare_results.py"),
                expected=expected,
                request=request,
                candidates=[scala, haskell],
                output=root / "failed.json",
                runner=failed,
            )
        self.assertEqual(
            (root / "failed.json.failure.stderr.log").read_bytes(),
            comparator_stderr,
        )


class FullCorrectnessWiringTests(TestCase):
    def test_integration_haskell_runner_preserves_qualified_source_closure(
        self,
    ) -> None:
        """Integration argv 수정은 qualification에 결속된 Haskell tree 밖에 둔다."""

        s1_4x = INTEGRATION.parent
        qualified_runner = s1_4x / "haskell/tools/run-candidate.sh"
        integration_runner = INTEGRATION / "tools/run-haskell-candidate.sh"
        scala_replay_runner = INTEGRATION / "tools/run-scala-replay-candidate.sh"
        aggregate = (
            INTEGRATION / "tools/run-integration-correctness.sh"
        ).read_text(encoding="utf-8")
        self.assertEqual(
            hashlib.sha256(qualified_runner.read_bytes()).hexdigest(),
            "0bcf11840951d0e31ed5cf476b225a2fb39d46570dd0dabb6dae14cc30530a19",
        )
        self.assertTrue(integration_runner.is_file())
        self.assertTrue(integration_runner.stat().st_mode & 0o111)
        runner_source = integration_runner.read_text(encoding="utf-8")
        self.assertIn('"${STACK_COMMAND[@]}" build \\', runner_source)
        self.assertIn('"${STACK_COMMAND[@]}" exec s1-4x-haskell -- \\', runner_source)
        self.assertIn("SELECTED_PROFILE_SHA256", runner_source)
        self.assertIn("stack-root-integration-candidate-", runner_source)
        self.assertIn("/usr/bin/flock -x", runner_source)
        self.assertIn("STACK_BUILD_STDOUT", runner_source)
        self.assertIn("STACK_BUILD_STDERR", runner_source)
        self.assertIn('if [[ "$build_status" -ne 0 ]]', runner_source)
        self.assertIn('if [[ -s "$STACK_STDERR"', runner_source)
        self.assertNotIn(
            '>"$STACK_STDOUT" \\\n+  2>"$STACK_STDERR"\n)\n',
            runner_source,
        )
        self.assertNotIn("candidate-stack-root", runner_source)
        self.assertIn(
            'HASKELL_RUNNER="$INTEGRATION/tools/run-haskell-candidate.sh"',
            aggregate,
        )
        self.assertEqual(aggregate.count('--haskell-runner "$HASKELL_RUNNER"'), 2)
        self.assertEqual(aggregate.count('--candidate "$HASKELL_RUNNER"'), 2)
        self.assertTrue(scala_replay_runner.is_file())
        self.assertTrue(scala_replay_runner.stat().st_mode & 0o111)
        scala_replay_source = scala_replay_runner.read_text(encoding="utf-8")
        self.assertIn('exec "$QUALIFIED_RUNNER" run "$@"', scala_replay_source)
        self.assertIn(
            'SCALA_REPLAY_RUNNER="$INTEGRATION/tools/run-scala-replay-candidate.sh"',
            aggregate,
        )
        self.assertEqual(aggregate.count('--candidate "$SCALA_REPLAY_RUNNER"'), 2)

    def test_scala_runner_receives_required_run_subcommand(self) -> None:
        """Scala와 Haskell의 서로 다른 CLI contract를 orchestration에 고정한다."""

        temp = self.enterContext(__import__("tempfile").TemporaryDirectory())
        root = Path(temp)
        request = root / "request.json"
        expected = root / "expected.json"
        request.write_text(json.dumps(_request()), encoding="utf-8")
        expected.write_text(json.dumps(_result()), encoding="utf-8")
        fixture_root = root / "fixtures"
        production = root / "production"
        research = root / "research"
        for directory in (fixture_root, production, research):
            directory.mkdir()
        scala_runner = root / "scala-runner"
        haskell_runner = root / "haskell-runner"
        capture_script = root / "capture.py"
        comparator = root / "compare.py"
        for path in (scala_runner, haskell_runner, capture_script, comparator):
            path.write_text("placeholder\n", encoding="utf-8")
        output = root / "output"

        def capture(**arguments: Any) -> dict[str, Any]:
            arguments["capture_report"].write_text("{}", encoding="utf-8")
            return {"status": "PASS"}

        def candidate(**arguments: Any) -> dict[str, Any]:
            payload = _result()
            payload["implementation"] = (
                "scala-3.8.4-jvm25"
                if arguments["label"] == "scala"
                else "haskell-ghc-9.10.3"
            )
            arguments["output_path"].write_text(
                json.dumps(payload),
                encoding="utf-8",
            )
            return payload

        def compare(**arguments: Any) -> dict[str, Any]:
            report = {
                "schemaVersion": "s1.4x-comparison-report-v1",
                "requestId": "full-correctness-v1",
                "implementationCount": 2,
                "mismatchCount": 0,
                "mismatches": [],
                "status": "PASS",
            }
            arguments["output"].write_text(json.dumps(report), encoding="utf-8")
            return report

        with (
            patch.object(
                full_correctness,
                "run_reference_capture",
                side_effect=capture,
            ),
            patch.object(
                full_correctness,
                "run_candidate",
                side_effect=candidate,
            ) as candidate_mock,
            patch.object(
                full_correctness,
                "compare_candidate_results",
                side_effect=compare,
            ),
        ):
            status = full_correctness.main(
                [
                    "--request",
                    str(request),
                    "--fixture-root",
                    str(fixture_root),
                    "--expected",
                    str(expected),
                    "--output-directory",
                    str(output),
                    "--scala-runner",
                    str(scala_runner),
                    "--haskell-runner",
                    str(haskell_runner),
                    "--capture-script",
                    str(capture_script),
                    "--comparator",
                    str(comparator),
                    "--production-project",
                    str(production),
                    "--research-project",
                    str(research),
                    "--uv-executable",
                    sys.executable,
                    "--scratch-root",
                    str(root / "scratch"),
                ]
            )

        self.assertEqual(status, 0)
        scala_call, haskell_call = candidate_mock.call_args_list
        self.assertEqual(
            scala_call.kwargs["command_template"],
            [str(scala_runner.resolve()), "run", "{protocol_args}"],
        )
        self.assertEqual(
            haskell_call.kwargs["command_template"],
            [str(haskell_runner.resolve()), "{protocol_args}"],
        )
        self.assertEqual(haskell_call.kwargs["timeout_seconds"], 600)
