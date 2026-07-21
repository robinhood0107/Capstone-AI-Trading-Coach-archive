"""Candidate property wrapper를 실제 subprocess receipt에 묶는 회귀 테스트."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any
from unittest import TestCase
from unittest.mock import patch

INTEGRATION = Path(__file__).resolve().parents[1]
S1_4X = INTEGRATION.parent
CONTRACT = S1_4X / "contract"
SEED_CORPUS = CONTRACT / "fixtures/property/property-seeds.v1.json"
REAL_WRAPPER_FIXTURE = INTEGRATION / "tests/fixtures/coverage_wrapper.py"
sys.path.insert(0, str(INTEGRATION))

from coverage_execution import (  # noqa: E402
    CoverageExecutionError,
    _validate_completed_generated_cabal_provenance,
    run_candidate_coverage,
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


def _materialize_haskell_completion_tree(
    root: Path,
    *,
    stack_bin: Path,
) -> tuple[Path, Path, bytes]:
    """Ignored Hpack output에 의존하지 않는 최소 completion source tree를 만든다."""

    numeric = root / "numeric"
    contract = numeric / "contract"
    haskell = numeric / "haskell"
    tools = haskell / "tools"
    contract.mkdir(parents=True)
    tools.mkdir(parents=True)
    (contract / "property-plan.v1.json").write_bytes(
        (CONTRACT / "property-plan.v1.json").read_bytes()
    )
    seed_target = numeric / "contract/fixtures/property/property-seeds.v1.json"
    seed_target.parent.mkdir(parents=True)
    seed_target.write_bytes(SEED_CORPUS.read_bytes())
    for name in (
        "package.yaml",
        "selected-profile.v1.json",
        "source-inputs.v1.json",
    ):
        (haskell / name).write_bytes((S1_4X / "haskell" / name).read_bytes())
    toolchain = json.loads(
        (S1_4X / "haskell/toolchain-lock.v1.json").read_text(encoding="utf-8")
    )
    toolchain["resolvedTools"]["stack"]["sha256"] = hashlib.sha256(
        stack_bin.read_bytes()
    ).hexdigest()
    (haskell / "toolchain-lock.v1.json").write_text(
        json.dumps(toolchain, allow_nan=False, sort_keys=True),
        encoding="utf-8",
    )
    for name in (
        "run-property-evidence.sh",
        "haskell_evidence.py",
        "python-runtime.sh",
    ):
        target = tools / name
        target.write_bytes((S1_4X / "haskell/tools" / name).read_bytes())
        if name.endswith(".sh"):
            target.chmod(0o700)
    cabal = b"cabal-version: 2.0\nname: s1-4x-haskell\nversion: 0.1.0.0\n"
    (haskell / "s1-4x-haskell.cabal").write_bytes(cabal)
    return numeric, tools / "run-property-evidence.sh", cabal


class CandidateCoverageExecutionTests(TestCase):
    def test_real_wrapper_subprocess_binds_outer_argv_and_wrapper_bytes(self) -> None:
        temporary = Path(self.enterContext(tempfile.TemporaryDirectory()))
        output = temporary / "candidate-output"
        receipt = temporary / "receipt.json"

        result = run_candidate_coverage(
            candidate="scala",
            candidate_profile="B",
            runner_path=REAL_WRAPPER_FIXTURE,
            output_directory=output,
            receipt_path=receipt,
            property_plan_path=CONTRACT / "property-plan.v1.json",
            function_registry_path=CONTRACT / "function-registry.v1.json",
            error_registry_path=CONTRACT / "error-registry.v1.json",
        )

        expected_command = [
            str(REAL_WRAPPER_FIXTURE.resolve()),
            "--output-dir",
            str(output.resolve()),
            "--profile",
            "B",
        ]
        self.assertEqual(
            result["runner"]["commandArgvSha256"],
            _canonical_sha256(expected_command),
        )
        self.assertEqual(
            result["runner"]["sha256"],
            hashlib.sha256(REAL_WRAPPER_FIXTURE.read_bytes()).hexdigest(),
        )

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
            seed_corpus = json.loads(SEED_CORPUS.read_text(encoding="utf-8"))
            seed_corpus_sha = hashlib.sha256(SEED_CORPUS.read_bytes()).hexdigest()
            successes_per_seed = 42
            successful_tests = len(seed_corpus["seeds"]) * successes_per_seed
            implementation = "scala-3.8.4-jvm25"
            toolchain = json.loads(
                (S1_4X / "scala/toolchain-lock.v1.json").read_text(encoding="utf-8")
            )
            properties = [
                {
                    "propertyId": item["propertyId"],
                    "successfulTests": successful_tests,
                    "discardedTests": 0,
                    "status": "PASS",
                }
                for item in plan["properties"]
            ]
            execution_properties = [
                {
                    **item,
                    "attemptedTests": successful_tests,
                    "seedCount": len(seed_corpus["seeds"]),
                    "seedExecutions": [
                        {
                            "seedIndex": seed_index,
                            "originalSeed": seed,
                            "successfulTests": successes_per_seed,
                            "discardedTests": 0,
                            "attemptedTests": successes_per_seed,
                            "replayToken": (f"scalacheck:{index}:{seed_index}"),
                            "shrinks": 0,
                            "status": "PASS",
                        }
                        for seed_index, seed in enumerate(seed_corpus["seeds"])
                    ],
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
                    "seedCorpusSha256": seed_corpus_sha,
                    "seedCount": len(seed_corpus["seeds"]),
                    "minimumSuccessfulPerSeed": successes_per_seed,
                    "maximumDiscardRatio": plan["maximumDiscardRatio"],
                    "framework": "scala-check-1.19.0",
                    "toolchainProfile": "B",
                    "scalaCliBinarySha256": toolchain["scalaCli"]["binarySha256"],
                    "commandArgvSha256": _canonical_sha256(command),
                    "runnerSha256": hashlib.sha256(
                        runner_path.read_bytes()
                    ).hexdigest(),
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
            candidate_profile="B",
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
                        "outerCommandArgvSha256": "0" * 64,
                        "runnerSha256": hashlib.sha256(
                            runner_path.read_bytes()
                        ).hexdigest(),
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
                candidate_profile=None,
                runner_path=runner_path,
                output_directory=root / "output",
                receipt_path=root / "receipt.json",
                property_plan_path=CONTRACT / "property-plan.v1.json",
                function_registry_path=CONTRACT / "function-registry.v1.json",
                error_registry_path=CONTRACT / "error-registry.v1.json",
                runner=runner,
            )

    def test_scala_profile_is_required(self) -> None:
        temporary = Path(self.enterContext(tempfile.TemporaryDirectory()))
        runner_path = temporary / "runner"
        runner_path.write_bytes(b"runner")
        runner_path.chmod(0o700)

        with self.assertRaisesRegex(
            CoverageExecutionError,
            "SCALA_PROFILE_INVALID",
        ):
            run_candidate_coverage(
                candidate="scala",
                candidate_profile=None,
                runner_path=runner_path,
                output_directory=temporary / "output",
                receipt_path=temporary / "receipt.json",
                property_plan_path=CONTRACT / "property-plan.v1.json",
                function_registry_path=CONTRACT / "function-registry.v1.json",
                error_registry_path=CONTRACT / "error-registry.v1.json",
            )

    def test_scala_proven_fallback_profile_is_accepted(self) -> None:
        temporary = Path(self.enterContext(tempfile.TemporaryDirectory()))
        result = run_candidate_coverage(
            candidate="scala",
            candidate_profile="A",
            runner_path=REAL_WRAPPER_FIXTURE,
            output_directory=temporary / "fallback-output",
            receipt_path=temporary / "fallback-receipt.json",
            property_plan_path=CONTRACT / "property-plan.v1.json",
            function_registry_path=CONTRACT / "function-registry.v1.json",
            error_registry_path=CONTRACT / "error-registry.v1.json",
        )
        self.assertEqual(
            result["coverage"]["propertyExecution"]["toolchainProfile"],
            "A",
        )

    def test_haskell_outer_wrapper_argv_is_distinct_from_inner_runner(
        self,
    ) -> None:
        temporary = Path(self.enterContext(tempfile.TemporaryDirectory()))
        output = temporary / "haskell-output"
        with patch.dict(
            os.environ,
            {"S1_4X_TEST_COVERAGE_CANDIDATE": "haskell"},
        ):
            result = run_candidate_coverage(
                candidate="haskell",
                candidate_profile=None,
                runner_path=REAL_WRAPPER_FIXTURE,
                output_directory=output,
                receipt_path=temporary / "haskell-receipt.json",
                property_plan_path=CONTRACT / "property-plan.v1.json",
                function_registry_path=CONTRACT / "function-registry.v1.json",
                error_registry_path=CONTRACT / "error-registry.v1.json",
            )
        expected_outer = _canonical_sha256(
            [
                str(REAL_WRAPPER_FIXTURE.resolve()),
                "--output-dir",
                str(output.resolve()),
            ]
        )
        execution = json.loads(
            (output / "haskell-property-execution-evidence.v1.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            result["runner"]["commandArgvSha256"],
            expected_outer,
        )
        self.assertEqual(execution["outerCommandArgvSha256"], expected_outer)
        self.assertNotEqual(
            execution["commandArgvSha256"],
            execution["outerCommandArgvSha256"],
        )

    def test_haskell_exact_ghc_option_argparse_failure_is_completed_once(
        self,
    ) -> None:
        temporary = Path(self.enterContext(tempfile.TemporaryDirectory()))
        output = temporary / "haskell-output"
        receipt = temporary / "haskell-receipt.json"
        stack_bin = temporary / "stack"
        stack_bin.write_bytes(b"pinned-stack")
        stack_bin.chmod(0o700)
        numeric, haskell_runner, cabal = _materialize_haskell_completion_tree(
            temporary,
            stack_bin=stack_bin,
        )
        haskell_root = numeric / "haskell"
        pinned_fd = os.open(REAL_WRAPPER_FIXTURE, os.O_RDONLY)
        self.addCleanup(os.close, pinned_fd)
        subject = subprocess.run(
            ["git", "-C", str(S1_4X), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        completion_calls: list[list[str]] = []

        def runner(
            command: list[str],
            **kwargs: Any,
        ) -> subprocess.CompletedProcess[bytes]:
            environment = dict(os.environ)
            environment["S1_4X_TEST_COVERAGE_CANDIDATE"] = "haskell"
            fixture_command = [str(REAL_WRAPPER_FIXTURE), *command[1:]]
            completed = subprocess.run(
                fixture_command,
                **{**kwargs, "env": environment},
            )
            execution_path = output / "haskell-property-execution-evidence.v1.json"
            execution = json.loads(execution_path.read_text(encoding="utf-8"))
            execution["outerCommandArgvSha256"] = _canonical_sha256(command)
            execution["runnerSha256"] = hashlib.sha256(
                haskell_runner.read_bytes()
            ).hexdigest()
            execution_path.write_text(
                json.dumps(execution, allow_nan=False, sort_keys=True),
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(
                command,
                2,
                completed.stdout,
                (
                    b"haskell_evidence.py generated-cabal-provenance: error: "
                    b"argument --ghc-option: expected one argument\n"
                ),
            )

        def provenance_runner(
            command: list[str],
            **kwargs: Any,
        ) -> subprocess.CompletedProcess[bytes]:
            completion_calls.append(command)
            self.assertEqual(
                command[:4],
                [
                    "/usr/bin/bash",
                    "--noprofile",
                    "--norc",
                    "-c",
                ],
            )
            self.assertEqual(kwargs["pass_fds"], (pinned_fd,))
            generated = output / "generated"
            generated.mkdir()
            generated_cabal = generated / "s1-4x-haskell.cabal"
            generated_cabal.write_bytes(cabal)
            cabal_sha256 = hashlib.sha256(cabal).hexdigest()
            execution = json.loads(
                (output / "haskell-property-execution-evidence.v1.json").read_text(
                    encoding="utf-8"
                )
            )
            toolchain_path = haskell_root / "toolchain-lock.v1.json"
            toolchain = json.loads(toolchain_path.read_text(encoding="utf-8"))
            profile_options = execution["profileGhcOptions"]
            build_portable_argv = [
                "stack",
                "--stack-root",
                "<isolated-stack-root>",
                "--work-dir",
                "<isolated-stack-work-dir>",
                "--system-ghc",
                "--no-install-ghc",
                "--stack-yaml",
                "haskell/stack.yaml",
                "--hpack-force",
                "build",
                "--test",
                "--no-run-tests",
                "--no-terminal",
                "--ghc-options",
                " ".join(profile_options),
            ]
            report = {
                "schemaVersion": (
                    "s1.4x-haskell-generated-cabal-provenance-v1"
                ),
                "benchmarkSubjectCommit": subject,
                "toolchainLockSha256": hashlib.sha256(
                    toolchain_path.read_bytes()
                ).hexdigest(),
                "packageYaml": {
                    "path": (
                        "workspaces/decision-platform/research/"
                        "s1-4x-numeric-parity/haskell/package.yaml"
                    ),
                    "blobSha256": hashlib.sha256(
                        (haskell_root / "package.yaml").read_bytes()
                    ).hexdigest(),
                },
                "sourceInputManifest": {
                    "path": (
                        "workspaces/decision-platform/research/"
                        "s1-4x-numeric-parity/haskell/source-inputs.v1.json"
                    ),
                    "blobSha256": execution["sourceInputManifestSha256"],
                },
                "stack": {
                    "pathId": toolchain["resolvedTools"]["stack"]["pathId"],
                    "version": toolchain["resolvedTools"]["stack"]["version"],
                    "binarySha256": toolchain["resolvedTools"]["stack"]["sha256"],
                },
                "hpack": {
                    "version": "0.39.6",
                    "versionOutputSha256": hashlib.sha256(b"0.39.6\n").hexdigest(),
                },
                "build": {
                    "portableArgv": build_portable_argv,
                    "portableArgvSha256": _canonical_sha256(build_portable_argv),
                    "runtimeArgvSha256": execution["buildArgvSha256"],
                    "stackRootPathId": execution["stackRootPathId"],
                    "exitCode": 0,
                },
                "generatedCabal": {
                    "repositoryRelativePath": (
                        "workspaces/decision-platform/research/"
                        "s1-4x-numeric-parity/haskell/s1-4x-haskell.cabal"
                    ),
                    "artifactPath": (
                        "coverage/haskell/generated/s1-4x-haskell.cabal"
                    ),
                    "sha256": cabal_sha256,
                    "sizeBytes": len(cabal),
                    "preBuildSha256": cabal_sha256,
                    "postBuildSha256": cabal_sha256,
                },
                "sourceTreeSha256": execution["sourceTreeSha256"],
                "propertyClosureSha256": execution["propertyClosureSha256"],
                "status": "PASS",
            }
            (output / "haskell-generated-cabal-provenance.v1.json").write_text(
                json.dumps(report, allow_nan=False, sort_keys=True),
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(
                command,
                0,
                (json.dumps(report, allow_nan=False, sort_keys=True) + "\n").encode(),
                b"",
            )

        with patch.dict(
            os.environ,
            {
                "S1_4X_BENCHMARK_SUBJECT_COMMIT": subject,
                "S1_4X_BENCHMARK_PYTHON_PINNED_FD_PATH": (f"/proc/self/fd/{pinned_fd}"),
                "S1_4X_STACK_BIN": str(stack_bin),
            },
            clear=False,
        ):
            result = run_candidate_coverage(
                candidate="haskell",
                candidate_profile=None,
                runner_path=haskell_runner,
                output_directory=output,
                receipt_path=receipt,
                property_plan_path=numeric / "contract/property-plan.v1.json",
                function_registry_path=CONTRACT / "function-registry.v1.json",
                error_registry_path=CONTRACT / "error-registry.v1.json",
                runner=runner,
                provenance_runner=provenance_runner,
            )

        self.assertEqual(result["status"], "PASS")
        self.assertEqual(
            result["schemaVersion"],
            "s1.4x-property-execution-receipt-v2",
        )
        self.assertEqual(result["process"]["exitCode"], 2)
        self.assertEqual(result["completion"]["process"]["exitCode"], 0)
        self.assertEqual(
            result["completion"]["process"]["portableArgvSha256"],
            _canonical_sha256(result["completion"]["process"]["portableArgv"]),
        )
        self.assertEqual(len(completion_calls), 1)
        self.assertIn("--ghc-option=-O0", completion_calls[0])
        self.assertIn("--ghc-option=-fasm", completion_calls[0])
        self.assertEqual(
            receipt.with_name(f"{receipt.stem}.process.stderr").read_bytes(),
            (
                b"haskell_evidence.py generated-cabal-provenance: error: "
                b"argument --ghc-option: expected one argument\n"
            ),
        )
        provenance_path = output / "haskell-generated-cabal-provenance.v1.json"
        completion_stdout = receipt.with_name(
            f"{receipt.stem}.generated-cabal-completion.stdout"
        ).read_bytes()
        self.assertEqual(
            json.loads(completion_stdout),
            json.loads(provenance_path.read_bytes()),
        )
        forged = json.loads(provenance_path.read_text(encoding="utf-8"))
        forged["sourceTreeSha256"] = "0" * 64
        provenance_path.write_text(json.dumps(forged), encoding="utf-8")
        execution = json.loads(
            (output / "haskell-property-execution-evidence.v1.json").read_text(
                encoding="utf-8"
            )
        )
        with self.assertRaisesRegex(
            CoverageExecutionError,
            "HASKELL_COMPLETION_PROVENANCE_INVALID",
        ):
            _validate_completed_generated_cabal_provenance(
                provenance_path=provenance_path,
                output=output,
                haskell_root=haskell_root,
                stack_bin=stack_bin,
                execution=execution,
                subject=subject,
                cabal_sha256=hashlib.sha256(cabal).hexdigest(),
                profile_options=execution["profileGhcOptions"],
                completion_stdout=(
                    json.dumps(forged, allow_nan=False, sort_keys=True) + "\n"
                ).encode(),
            )

    def test_haskell_argparse_near_miss_never_runs_completion(self) -> None:
        temporary = Path(self.enterContext(tempfile.TemporaryDirectory()))
        receipt = temporary / "haskell-receipt.json"
        haskell_runner = S1_4X / "haskell/tools/run-property-evidence.sh"
        completion_calls = 0
        stderr = (
            b"haskell_evidence.py generated-cabal-provenance: error: "
            b"argument --ghc-option: expected one argument!\n"
        )

        def runner(
            command: list[str],
            **_: Any,
        ) -> subprocess.CompletedProcess[bytes]:
            return subprocess.CompletedProcess(command, 2, b"", stderr)

        def provenance_runner(
            command: list[str],
            **_: Any,
        ) -> subprocess.CompletedProcess[bytes]:
            nonlocal completion_calls
            completion_calls += 1
            return subprocess.CompletedProcess(command, 0, b"", b"")

        with self.assertRaisesRegex(
            CoverageExecutionError,
            "COVERAGE_PROCESS_FAILED:exit=2",
        ):
            run_candidate_coverage(
                candidate="haskell",
                candidate_profile=None,
                runner_path=haskell_runner,
                output_directory=temporary / "output",
                receipt_path=receipt,
                property_plan_path=CONTRACT / "property-plan.v1.json",
                function_registry_path=CONTRACT / "function-registry.v1.json",
                error_registry_path=CONTRACT / "error-registry.v1.json",
                runner=runner,
                provenance_runner=provenance_runner,
            )

        self.assertEqual(completion_calls, 0)
        self.assertFalse(receipt.exists())
        self.assertEqual(
            receipt.with_name(f"{receipt.stem}.process.stderr").read_bytes(),
            stderr,
        )
        failure = json.loads(
            receipt.with_name(f"{receipt.stem}.failure.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(failure["failureCode"], "COVERAGE_PROCESS_FAILED")

    def test_haskell_completion_failure_preserves_both_processes(self) -> None:
        temporary = Path(self.enterContext(tempfile.TemporaryDirectory()))
        output = temporary / "haskell-output"
        receipt = temporary / "haskell-receipt.json"
        stack_bin = temporary / "stack"
        stack_bin.write_bytes(b"pinned-stack")
        stack_bin.chmod(0o700)
        numeric, haskell_runner, _cabal = _materialize_haskell_completion_tree(
            temporary,
            stack_bin=stack_bin,
        )
        subject = subprocess.run(
            ["git", "-C", str(S1_4X), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        original_error = (
            b"haskell_evidence.py generated-cabal-provenance: error: "
            b"argument --ghc-option: expected one argument\n"
        )

        def runner(
            command: list[str],
            **kwargs: Any,
        ) -> subprocess.CompletedProcess[bytes]:
            environment = dict(os.environ)
            environment["S1_4X_TEST_COVERAGE_CANDIDATE"] = "haskell"
            completed = subprocess.run(
                [str(REAL_WRAPPER_FIXTURE), *command[1:]],
                **{**kwargs, "env": environment},
            )
            execution_path = output / "haskell-property-execution-evidence.v1.json"
            execution = json.loads(execution_path.read_text(encoding="utf-8"))
            execution["outerCommandArgvSha256"] = _canonical_sha256(command)
            execution["runnerSha256"] = hashlib.sha256(
                haskell_runner.read_bytes()
            ).hexdigest()
            execution_path.write_text(
                json.dumps(execution, allow_nan=False, sort_keys=True),
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(
                command,
                2,
                completed.stdout,
                original_error,
            )

        def provenance_runner(
            command: list[str],
            **_: Any,
        ) -> subprocess.CompletedProcess[bytes]:
            return subprocess.CompletedProcess(
                command,
                9,
                b"completion output\n",
                b"completion failed\n",
            )

        with (
            patch.dict(
                os.environ,
                {
                    "S1_4X_BENCHMARK_SUBJECT_COMMIT": subject,
                    "S1_4X_STACK_BIN": str(stack_bin),
                },
                clear=False,
            ),
            self.assertRaisesRegex(
                CoverageExecutionError,
                "HASKELL_GENERATED_CABAL_COMPLETION_FAILED",
            ),
        ):
            run_candidate_coverage(
                candidate="haskell",
                candidate_profile=None,
                runner_path=haskell_runner,
                output_directory=output,
                receipt_path=receipt,
                property_plan_path=numeric / "contract/property-plan.v1.json",
                function_registry_path=CONTRACT / "function-registry.v1.json",
                error_registry_path=CONTRACT / "error-registry.v1.json",
                runner=runner,
                provenance_runner=provenance_runner,
            )

        self.assertFalse(receipt.exists())
        self.assertEqual(
            receipt.with_name(f"{receipt.stem}.process.stderr").read_bytes(),
            original_error,
        )
        attempt_path = receipt.with_name(
            f"{receipt.stem}.generated-cabal-completion.json"
        )
        attempt = json.loads(attempt_path.read_text(encoding="utf-8"))
        self.assertEqual(attempt["status"], "FAIL")
        self.assertEqual(attempt["completion"]["exitCode"], 9)
        self.assertEqual(
            receipt.with_name(
                f"{receipt.stem}.generated-cabal-completion.stderr"
            ).read_bytes(),
            b"completion failed\n",
        )
        failure = json.loads(
            receipt.with_name(f"{receipt.stem}.failure.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            failure["failureCode"],
            "HASKELL_GENERATED_CABAL_COMPLETION_FAILED",
        )

    def test_haskell_pantry_is_updated_before_the_fresh_stack_root_build(
        self,
    ) -> None:
        temporary = Path(self.enterContext(tempfile.TemporaryDirectory()))
        home = temporary / "home"
        home.mkdir()
        stack_bin = temporary / "stack"
        stack_bin.write_bytes(b"pinned-stack")
        stack_bin.chmod(0o700)
        output = temporary / "haskell-output"
        receipt = temporary / "haskell-receipt.json"
        calls: list[str] = []

        def prewarm_runner(
            command: list[str],
            **kwargs: Any,
        ) -> subprocess.CompletedProcess[bytes]:
            calls.append("prewarm")
            self.assertEqual(command[0], str(stack_bin.resolve()))
            self.assertIn("update", command)
            self.assertNotIn("PANTRY_ROOT", kwargs["env"])
            pantry = home / ".stack/pantry"
            (pantry / "hackage").mkdir(parents=True)
            (pantry / "pantry.sqlite3").write_bytes(b"sqlite")
            (pantry / "hackage/00-index.tar").write_bytes(b"index")
            return subprocess.CompletedProcess(
                command,
                0,
                b"index updated\n",
                b"",
            )

        def runner(
            command: list[str],
            **kwargs: Any,
        ) -> subprocess.CompletedProcess[bytes]:
            calls.append("runner")
            environment = dict(kwargs["env"])
            self.assertEqual(
                environment["PANTRY_ROOT"],
                str((home / ".stack/pantry").resolve()),
            )
            environment["S1_4X_TEST_COVERAGE_CANDIDATE"] = "haskell"
            return subprocess.run(
                command,
                **{**kwargs, "env": environment},
            )

        with patch.dict(
            os.environ,
            {
                "HOME": str(home),
                "S1_4X_STACK_BIN": str(stack_bin),
            },
            clear=False,
        ):
            os.environ.pop("PANTRY_ROOT", None)
            result = run_candidate_coverage(
                candidate="haskell",
                candidate_profile=None,
                runner_path=REAL_WRAPPER_FIXTURE,
                output_directory=output,
                receipt_path=receipt,
                property_plan_path=CONTRACT / "property-plan.v1.json",
                function_registry_path=CONTRACT / "function-registry.v1.json",
                error_registry_path=CONTRACT / "error-registry.v1.json",
                prewarm_haskell_pantry=True,
                runner=runner,
                prewarm_runner=prewarm_runner,
            )

        self.assertEqual(calls, ["prewarm", "runner"])
        self.assertEqual(result["status"], "PASS")
        prewarm = json.loads(
            receipt.with_name(f"{receipt.stem}.pantry-prewarm.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(prewarm["status"], "PASS")
        self.assertEqual(prewarm["process"]["exitCode"], 0)
        self.assertEqual(
            receipt.with_name(f"{receipt.stem}.pantry-prewarm.stdout").read_bytes(),
            b"index updated\n",
        )

    def test_haskell_pantry_update_failure_stops_before_the_runner(self) -> None:
        temporary = Path(self.enterContext(tempfile.TemporaryDirectory()))
        home = temporary / "home"
        home.mkdir()
        stack_bin = temporary / "stack"
        stack_bin.write_bytes(b"pinned-stack")
        stack_bin.chmod(0o700)
        receipt = temporary / "haskell-receipt.json"
        runner_called = False

        def prewarm_runner(
            command: list[str],
            **_: Any,
        ) -> subprocess.CompletedProcess[bytes]:
            return subprocess.CompletedProcess(
                command,
                17,
                b"partial\n",
                b"update failed\n",
            )

        def runner(
            command: list[str],
            **_: Any,
        ) -> subprocess.CompletedProcess[bytes]:
            nonlocal runner_called
            runner_called = True
            return subprocess.CompletedProcess(command, 0, b"", b"")

        with (
            patch.dict(
                os.environ,
                {
                    "HOME": str(home),
                    "S1_4X_STACK_BIN": str(stack_bin),
                },
                clear=False,
            ),
            self.assertRaisesRegex(
                CoverageExecutionError,
                "HASKELL_PANTRY_PREWARM_FAILED:exit=17",
            ),
        ):
            os.environ.pop("PANTRY_ROOT", None)
            run_candidate_coverage(
                candidate="haskell",
                candidate_profile=None,
                runner_path=REAL_WRAPPER_FIXTURE,
                output_directory=temporary / "haskell-output",
                receipt_path=receipt,
                property_plan_path=CONTRACT / "property-plan.v1.json",
                function_registry_path=CONTRACT / "function-registry.v1.json",
                error_registry_path=CONTRACT / "error-registry.v1.json",
                prewarm_haskell_pantry=True,
                runner=runner,
                prewarm_runner=prewarm_runner,
            )

        self.assertFalse(runner_called)
        prewarm = json.loads(
            receipt.with_name(f"{receipt.stem}.pantry-prewarm.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(prewarm["status"], "FAIL")
        self.assertEqual(prewarm["process"]["exitCode"], 17)
        self.assertEqual(
            receipt.with_name(f"{receipt.stem}.pantry-prewarm.stderr").read_bytes(),
            b"update failed\n",
        )

    def test_haskell_runner_inherits_pinned_benchmark_python_fd(self) -> None:
        temporary = Path(self.enterContext(tempfile.TemporaryDirectory()))
        runner_path = temporary / "runner"
        runner_path.write_bytes(b"runner")
        runner_path.chmod(0o700)
        pinned_fd = os.open(REAL_WRAPPER_FIXTURE, os.O_RDONLY)
        self.addCleanup(os.close, pinned_fd)

        def runner(
            command: list[str],
            **kwargs: Any,
        ) -> subprocess.CompletedProcess[bytes]:
            self.assertEqual(kwargs.get("pass_fds"), (pinned_fd,))
            return subprocess.CompletedProcess(
                command,
                69,
                b"",
                b"benchmark Python pinned FD identity is unsafe\n",
            )

        with (
            patch.dict(
                os.environ,
                {
                    "S1_4X_BENCHMARK_PYTHON_PINNED_FD_PATH": (
                        f"/proc/self/fd/{pinned_fd}"
                    ),
                },
            ),
            self.assertRaisesRegex(
                CoverageExecutionError,
                "COVERAGE_PROCESS_FAILED:exit=69",
            ),
        ):
            run_candidate_coverage(
                candidate="haskell",
                candidate_profile=None,
                runner_path=runner_path,
                output_directory=temporary / "output",
                receipt_path=temporary / "receipt.json",
                property_plan_path=CONTRACT / "property-plan.v1.json",
                function_registry_path=CONTRACT / "function-registry.v1.json",
                error_registry_path=CONTRACT / "error-registry.v1.json",
                runner=runner,
            )

        failure = json.loads(
            (temporary / "receipt.failure.json").read_text(encoding="utf-8")
        )
        self.assertEqual(failure["failureCode"], "COVERAGE_PROCESS_FAILED")
        self.assertEqual(failure["process"]["exitCode"], 69)
        self.assertEqual(
            (temporary / "receipt.process.stderr").read_bytes(),
            b"benchmark Python pinned FD identity is unsafe\n",
        )
        self.assertEqual(
            (temporary / "receipt.process.stdout").read_bytes(),
            b"",
        )

    def test_haskell_stack_root_id_is_bound_to_output_directory(self) -> None:
        temporary = Path(self.enterContext(tempfile.TemporaryDirectory()))
        output = temporary / "haskell-output"

        def runner(
            command: list[str],
            **_: Any,
        ) -> subprocess.CompletedProcess[bytes]:
            environment = dict(os.environ)
            environment["S1_4X_TEST_COVERAGE_CANDIDATE"] = "haskell"
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                env=environment,
            )
            execution_path = output / "haskell-property-execution-evidence.v1.json"
            execution = json.loads(execution_path.read_text(encoding="utf-8"))
            execution["stackRootPathId"] = (
                "S1_4X_CACHE_ROOT/stack-root-property-" + "2" * 24
            )
            execution_path.write_text(
                json.dumps(execution, allow_nan=False, sort_keys=True),
                encoding="utf-8",
            )
            return completed

        with self.assertRaisesRegex(
            CoverageExecutionError,
            "EXECUTION_STACK_ROOT_PATH_ID_MISMATCH",
        ):
            run_candidate_coverage(
                candidate="haskell",
                candidate_profile=None,
                runner_path=REAL_WRAPPER_FIXTURE,
                output_directory=output,
                receipt_path=temporary / "receipt.json",
                property_plan_path=CONTRACT / "property-plan.v1.json",
                function_registry_path=CONTRACT / "function-registry.v1.json",
                error_registry_path=CONTRACT / "error-registry.v1.json",
                runner=runner,
            )
