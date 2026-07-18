"""Candidate property wrapper를 실제 subprocess receipt에 묶는 회귀 테스트."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any
from unittest import TestCase

INTEGRATION = Path(__file__).resolve().parents[1]
S1_4X = INTEGRATION.parent
CONTRACT = S1_4X / "contract"
sys.path.insert(0, str(INTEGRATION))

from coverage_execution import CoverageExecutionError, run_candidate_coverage  # noqa: E402


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


class CandidateCoverageExecutionTests(TestCase):
    def test_receipt_binds_actual_runner_command_and_artifact_bytes(self) -> None:
        temporary = self.enterContext(tempfile.TemporaryDirectory())
        root = Path(temporary)
        runner_path = root / "scala-runner"
        runner_path.write_bytes(b"executable-test-runner")
        runner_path.chmod(0o700)
        output = root / "candidate-output"
        receipt = root / "receipt.json"

        def runner(command: list[str], **_: Any) -> subprocess.CompletedProcess[bytes]:
            output.mkdir()
            plan = json.loads(
                (CONTRACT / "property-plan.v1.json").read_text(encoding="utf-8")
            )
            functions = json.loads(
                (CONTRACT / "function-registry.v1.json").read_text(encoding="utf-8")
            )
            errors = json.loads(
                (CONTRACT / "error-registry.v1.json").read_text(encoding="utf-8")
            )
            plan_sha = hashlib.sha256(
                (CONTRACT / "property-plan.v1.json").read_bytes()
            ).hexdigest()
            implementation = "scala-test"
            properties = [
                {
                    "propertyId": item["propertyId"],
                    "successfulTests": 1000,
                    "discardedTests": 0,
                    "status": "PASS",
                }
                for item in plan["properties"]
            ]
            execution_properties = [
                {
                    **item,
                    "attemptedTests": 1000,
                    "originalSeed": index,
                    "replayToken": f"scalacheck:{index}",
                    "shrinks": 0,
                }
                for index, item in enumerate(properties)
            ]
            documents = {
                "scala-property-report.v1.json": {
                    "schemaVersion": "s1.4x-candidate-property-coverage-v1",
                    "implementation": implementation,
                    "propertyPlanSha256": plan_sha,
                    "properties": properties,
                    "status": "PASS",
                },
                "scala-registry-report.v1.json": {
                    "schemaVersion": "s1.4x-candidate-registry-coverage-v1",
                    "implementation": implementation,
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
                },
                "scala-property-execution-evidence.v1.json": {
                    "schemaVersion": "s1.4x-candidate-property-execution-v1",
                    "implementation": implementation,
                    "propertyPlanSha256": plan_sha,
                    "framework": "scala-check-1.19.0",
                    "toolchainProfile": "A",
                    "commandArgvSha256": _canonical_sha256(command),
                    "runnerSha256": hashlib.sha256(runner_path.read_bytes()).hexdigest(),
                    "sourceClosureSha256": "c" * 64,
                    "startedAt": "2026-07-18T12:00:00.000000Z",
                    "finishedAt": "2026-07-18T12:00:01.000000Z",
                    "exitCode": 0,
                    "properties": execution_properties,
                    "status": "PASS",
                },
            }
            for name, document in documents.items():
                (output / name).write_text(
                    json.dumps(document),
                    encoding="utf-8",
                )
            return subprocess.CompletedProcess(command, 0, b"test output", b"")

        result = run_candidate_coverage(
            candidate="scala",
            runner_path=runner_path,
            output_directory=output,
            receipt_path=receipt,
            property_plan_path=CONTRACT / "property-plan.v1.json",
            function_registry_path=CONTRACT / "function-registry.v1.json",
            error_registry_path=CONTRACT / "error-registry.v1.json",
            runner=runner,
        )
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["process"]["exitCode"], 0)
        self.assertEqual(result["coverage"]["propertyCount"], 25)
        self.assertEqual(len(result["artifacts"]), 3)

    def test_sidecar_command_hash_cannot_be_detached_from_invocation(self) -> None:
        temporary = self.enterContext(tempfile.TemporaryDirectory())
        root = Path(temporary)
        runner_path = root / "runner"
        runner_path.write_bytes(b"runner")
        runner_path.chmod(0o700)

        def runner(command: list[str], **_: Any) -> subprocess.CompletedProcess[bytes]:
            output = Path(command[-1])
            output.mkdir()
            # Binding 검증이 coverage parsing보다 먼저 실패해야 한다.
            (output / "haskell-property-execution-evidence.v1.json").write_text(
                json.dumps(
                    {
                        "schemaVersion": "s1.4x-candidate-property-execution-v1",
                        "commandArgvSha256": "0" * 64,
                        "runnerSha256": hashlib.sha256(runner_path.read_bytes()).hexdigest(),
                    }
                ),
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(command, 0, b"", b"")

        with self.assertRaisesRegex(
            CoverageExecutionError,
            "EXECUTION_COMMAND_DIGEST_MISMATCH",
        ):
            run_candidate_coverage(
                candidate="haskell",
                runner_path=runner_path,
                output_directory=root / "output",
                receipt_path=root / "receipt.json",
                property_plan_path=CONTRACT / "property-plan.v1.json",
                function_registry_path=CONTRACT / "function-registry.v1.json",
                error_registry_path=CONTRACT / "error-registry.v1.json",
                runner=runner,
            )
