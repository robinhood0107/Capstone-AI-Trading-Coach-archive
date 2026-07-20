"""Detached full-run supervisor의 순서, 실패 경계와 terminal 봉인을 검증한다."""

from __future__ import annotations

import hashlib
import json
import signal
import sys
import tempfile
import unittest
from pathlib import Path

INTEGRATION = Path(__file__).resolve().parents[1]
if str(INTEGRATION) not in sys.path:
    sys.path.insert(0, str(INTEGRATION))

import detached_full_run as supervisor  # noqa: E402, I001


SUBJECT = "a" * 40
PARENT_SUBJECT = "c" * 40
SCALA_SOURCE_COMMIT = "d" * 40
HASKELL_SOURCE_COMMIT = "e" * 40
PARENT_RUN_ID = "20260720t1527z-e2d0a443"
CONTROL_BYTES = b"# sealed supervisor\n"
CONTROL_SHA256 = hashlib.sha256(CONTROL_BYTES).hexdigest()
RUNTIME_PATHS = {
    "uv": "/tools/uv",
    "docker": "/tools/docker",
    "benchmarkPython": "/tools/benchmark-python",
    "scalaCli": "/tools/scala-cli",
    "java": "/tools/java",
    "scalafix": "/tools/scalafix",
    "scalafmt": "/tools/scalafmt",
    "ghcup": "/tools/ghcup",
    "stack": "/tools/stack",
    "authoritativeGhc": "/tools/ghc-9.10.3",
    "compatibilityGhc": "/tools/ghc-9.14.1",
    "hlint": "/tools/hlint",
    "stylishHaskell": "/tools/stylish-haskell",
    "scalafmtArchive": "/tools/scalafmt.zip",
    "vectorSourceArchive": "/tools/vector.tar.gz",
}


def _continuation_manifest() -> dict[str, object]:
    source_trees = []
    artifacts = []
    for index, source_id in enumerate(
        (
            "failedCorrectnessPrefix",
            "scalaQualification",
            "haskellStatic",
            "haskellProfile",
        ),
        start=1,
    ):
        source_trees.append(
            {
                "sourceId": source_id,
                "sourceRoot": f"/sources/{source_id}",
                "sourceRelativePath": ".",
                "destinationRelativePath": source_id,
                "excludedSubtrees": [],
                "bindings": {},
                "artifactCount": 1,
                "totalSizeBytes": index,
                "treeSha256": f"{index:x}" * 64,
            }
        )
        artifacts.append(
            {
                "sourceId": source_id,
                "sourceRelativePath": "artifact.json",
                "destinationPath": f"{source_id}/artifact.json",
                "sha256": f"{index + 4:x}" * 64,
                "sizeBytes": index,
            }
        )
    return {
        "schemaVersion": supervisor.CONTINUATION_MANIFEST_SCHEMA,
        "parentRunId": PARENT_RUN_ID,
        "parentSubject": PARENT_SUBJECT,
        "targetSubject": SUBJECT,
        "failedStage": "correctness-oci-regression",
        "currentDiffPaths": [],
        "sourceCommits": {
            "scalaQualification": SCALA_SOURCE_COMMIT,
            "haskellProfile": HASKELL_SOURCE_COMMIT,
        },
        "sourceTrees": source_trees,
        "artifactCount": len(artifacts),
        "artifacts": artifacts,
        "status": "SEALED",
    }


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        + "\n"
    ).encode()


def _plan(root: Path, *, reuse: bool = False) -> dict[str, object]:
    bindings = {
        role: {"path": path, "sha256": "b" * 64} for role, path in RUNTIME_PATHS.items()
    }
    plan: dict[str, object] = {
        "schemaVersion": "s1.4x-detached-full-run-plan-v1",
        "preparedAt": "2026-01-01T00:00:00.000Z",
        "runId": "s1-4x-full-test",
        "unitName": "s1-4x-full-s1-4x-full-test.service",
        "repositoryRoot": str(root / "repo"),
        "runRoot": str(root / "run"),
        "benchmarkSubjectCommit": SUBJECT,
        "branch": "experiment/s1-4x-numeric-parity-test",
        "overallTimeoutSeconds": 61_200,
        "home": str(root / "home"),
        "cacheRoot": str(root / "cache"),
        "lockPath": str(root / "cache/detached-full-run.lock"),
        "executionPath": "/usr/bin:/bin",
        "ghcupInstallBasePrefix": str(root / "home"),
        "controlSupervisor": {
            "relativePath": "control/detached_full_run.py",
            "sha256": CONTROL_SHA256,
            "sizeBytes": len(CONTROL_BYTES),
        },
        "runtimeBindings": bindings,
        "scalaBaseImage": supervisor.SCALA_BASE_IMAGE,
        "haskellBaseImage": supervisor.HASKELL_BASE_IMAGE,
        "stageOrder": [
            "correctness-oci-regression",
            "command-sealing",
            "frozen-timing",
            "typed-finalization",
        ],
        "retryPolicy": supervisor.FRESH_RETRY_POLICY,
    }
    if reuse:
        manifest_bytes = _json_bytes(_continuation_manifest())
        plan["retryPolicy"] = supervisor.REUSE_RETRY_POLICY
        plan["evidenceReuse"] = {
            "mode": supervisor.REUSE_MODE,
            "parentRunId": PARENT_RUN_ID,
            "parentSubject": PARENT_SUBJECT,
            "targetSubject": SUBJECT,
            "sourceCommits": {
                "scalaQualification": SCALA_SOURCE_COMMIT,
                "haskellProfile": HASKELL_SOURCE_COMMIT,
            },
            "controlManifest": {
                "relativePath": (
                    f"control/{supervisor.CONTINUATION_MANIFEST_NAME}"
                ),
                "sha256": hashlib.sha256(manifest_bytes).hexdigest(),
                "sizeBytes": len(manifest_bytes),
            },
        }
    return plan


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, separators=(",", ":"), sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_final_reports(
    directory: Path,
    *,
    status: str = "PASS",
    completed: int = 87,
    partial: int = 0,
    not_measured: int = 0,
    audit_sha256: str = "f" * 64,
) -> None:
    _write_json(
        directory / "benchmark-summary.v1.json",
        {
            "schemaVersion": "s1.4x-full-benchmark-summary-v1",
            "benchmarkSubjectCommit": SUBJECT,
            "scheduledBlockCount": 87,
            "completedBlockCount": completed,
            "partialBlockCount": partial,
            "notMeasuredCount": not_measured,
            "familyCount": 6,
            "candidateCaseCountPerRepetition": 89,
            "outerRepetitions": 3,
            "boundarySummaries": {
                boundary: {"caseCount": 1, "cases": {}}
                for boundary in supervisor.BOUNDARY_IDS
            },
            "status": status,
        },
    )
    _write_json(
        directory / "benchmark-host-ledger.v1.json",
        {
            "schemaVersion": "s1.4x-full-benchmark-host-ledger-v1",
            "benchmarkSubjectCommit": SUBJECT,
            "blockCount": 87,
            "blocks": [{} for _ in range(87)],
            "status": "PASS",
        },
    )
    _write_json(
        directory / "benchmark-raw-hash-manifest.v1.json",
        {
            "schemaVersion": "s1.4x-full-benchmark-raw-hash-manifest-v1",
            "benchmarkSubjectCommit": SUBJECT,
            "artifactCount": 1,
            "artifacts": [
                {
                    "path": "r1/file",
                    "sha256": "e" * 64,
                    "sizeBytes": 1,
                }
            ],
            "status": "PASS",
        },
    )
    _write_json(
        directory / "scorecard.v1.json",
        {
            "schemaVersion": "s1.4x-scorecard-v1",
            "benchmarkSubjectCommit": SUBJECT,
            "auditLedgerSha256": audit_sha256,
            "candidates": {
                "scala": {
                    "eligibility": "QUALIFIED",
                    "status": "PASS",
                    "categories": {
                        "correctness": {"maxPoints": 35.0, "points": 35.0},
                        "purityAuditability": {
                            "maxPoints": 20.0,
                            "points": 20.0,
                        },
                        "reproducibility": {
                            "maxPoints": 15.0,
                            "points": 15.0,
                        },
                        "performance": {"maxPoints": 15.0, "points": 5.0},
                        "maintainability": {
                            "maxPoints": 10.0,
                            "points": 10.0,
                        },
                        "integrationFit": {"maxPoints": 5.0, "points": 5.0},
                    },
                    "totalPoints": 90.0,
                },
                "haskell": {
                    "eligibility": "QUALIFIED",
                    "status": "PASS",
                    "categories": {
                        "correctness": {"maxPoints": 35.0, "points": 35.0},
                        "purityAuditability": {
                            "maxPoints": 20.0,
                            "points": 20.0,
                        },
                        "reproducibility": {
                            "maxPoints": 15.0,
                            "points": 15.0,
                        },
                        "performance": {"maxPoints": 15.0, "points": 6.0},
                        "maintainability": {
                            "maxPoints": 10.0,
                            "points": 10.0,
                        },
                        "integrationFit": {"maxPoints": 5.0, "points": 5.0},
                    },
                    "totalPoints": 91.0,
                },
            },
            "status": "PASS",
        },
    )


def _audit_pass(
    _paths: supervisor.RunPaths,
    _plan_value: dict[str, object],
) -> dict[str, object]:
    return {"status": "PASS"}


def _write_finalizer_receipt(run_root: Path, *, status: str = "PASS") -> None:
    documents = {}
    for name in supervisor.FINAL_REPORT_NAMES:
        path = run_root / "final-reports" / name
        documents[name] = {
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "sizeBytes": path.stat().st_size,
        }
    _write_json(
        run_root / "stages/typed-finalization/stdout.log",
        {
            "schemaVersion": "s1.4x-benchmark-finalization-v1",
            "benchmarkSubjectCommit": SUBJECT,
            "documents": documents,
            "completedBlockCount": 87,
            "validPerformanceTimeoutCount": 0,
            "partialBlockCount": 0,
            "notMeasuredCount": 0,
            "status": status,
        },
    )


class DetachedFullRunCommandTest(unittest.TestCase):
    def test_builds_exact_four_stage_order_and_v3_bindings(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            plan = _plan(Path(temporary))
            commands = supervisor.build_stage_commands(plan)

        self.assertEqual(
            [command.name for command in commands],
            [
                "correctness-oci-regression",
                "command-sealing",
                "frozen-timing",
                "typed-finalization",
            ],
        )
        command_sealing = commands[1].argv
        for role in (
            "uv",
            "docker",
            "benchmarkPython",
            "scalaCli",
            "java",
            "scalafix",
            "scalafmt",
            "ghcup",
            "stack",
            "authoritativeGhc",
            "compatibilityGhc",
            "hlint",
            "stylishHaskell",
        ):
            self.assertIn(f"{role}={RUNTIME_PATHS[role]}", command_sealing)
        for role in (
            "scalafmtArchive",
            "selectedProfileResult",
            "profileQualificationResult",
            "jvmAllowlistResult",
            "correctnessA",
            "correctnessB",
            "correctnessC",
            "baselineCorrectness",
            "optimizedCorrectness",
            "profileQualification",
        ):
            self.assertIn(role, " ".join(command_sealing))
        self.assertIn("--large-fixture-root", commands[2].argv)
        self.assertIn("--large-fixture-receipt", commands[2].argv)
        self.assertIn(
            "final-candidate-audit.json",
            " ".join(commands[3].argv),
        )
        self.assertNotIn(
            "--sealed-continuation-manifest",
            commands[0].argv,
        )

    def test_reuse_plan_adds_only_the_sealed_manifest_to_first_stage(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plan = _plan(root, reuse=True)
            commands = supervisor.build_stage_commands(plan)

        self.assertEqual(
            commands[0].argv[-2:],
            (
                "--sealed-continuation-manifest",
                str(
                    root
                    / "run/control"
                    / supervisor.CONTINUATION_MANIFEST_NAME
                ),
            ),
        )
        self.assertEqual(
            [command.name for command in commands],
            list(supervisor.STAGE_ORDER),
        )

    def test_execution_environment_does_not_leak_ambient_stack_root(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            environment = supervisor._execution_environment(_plan(root))
            reuse_environment = supervisor._execution_environment(
                _plan(root, reuse=True)
            )

        self.assertNotIn("STACK_ROOT", environment)
        self.assertNotIn("S1_4X_IGNORE_AMBIENT_HOST_ACTIVITY", environment)
        self.assertEqual(
            reuse_environment["S1_4X_IGNORE_AMBIENT_HOST_ACTIVITY"],
            "1",
        )
        self.assertEqual(
            environment["GHCUP_INSTALL_BASE_PREFIX"],
            str(Path(temporary) / "home"),
        )

    def test_ghcup_prefix_is_derived_from_exact_ghc_and_stack_layout(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            prefix = Path(temporary)
            authoritative = prefix / ".ghcup/ghc/9.10.3/bin/ghc-9.10.3"
            stack = prefix / ".ghcup/stack/3.11.1/stack"
            authoritative.parent.mkdir(parents=True)
            stack.parent.mkdir(parents=True)
            bindings = {
                "authoritativeGhc": {"path": str(authoritative)},
                "stack": {"path": str(stack)},
            }

            self.assertEqual(
                supervisor._ghcup_install_base_prefix(bindings),
                prefix,
            )

    def test_stage_sequence_stops_at_first_failure_without_retry(self) -> None:
        commands = [
            supervisor.StageCommand("one", ("/bin/one",)),
            supervisor.StageCommand("two", ("/bin/two",)),
            supervisor.StageCommand("three", ("/bin/three",)),
        ]
        calls: list[str] = []

        def fake_run(
            command: supervisor.StageCommand,
            _paths: supervisor.RunPaths,
            _environment: dict[str, str],
            _deadline: float,
        ) -> supervisor.StageReceipt:
            calls.append(command.name)
            if command.name == "two":
                raise supervisor.StageFailure("two", 17)
            return supervisor.StageReceipt.for_test(command.name)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = supervisor.RunPaths.from_run_root(root)
            with self.assertRaises(supervisor.StageFailure):
                supervisor.execute_stage_sequence(
                    commands,
                    paths=paths,
                    environment={},
                    deadline=100.0,
                    stage_runner=fake_run,
                )

        self.assertEqual(calls, ["one", "two"])

    def test_between_stage_signal_prevents_any_later_stage(self) -> None:
        commands = [supervisor.StageCommand("one", ("/bin/one",))]
        calls: list[str] = []

        def fake_run(
            command: supervisor.StageCommand,
            _paths: supervisor.RunPaths,
            _environment: dict[str, str],
            _deadline: float,
        ) -> supervisor.StageReceipt:
            calls.append(command.name)
            return supervisor.StageReceipt.for_test(command.name)

        previous = supervisor._INTERRUPTED_SIGNAL
        supervisor._INTERRUPTED_SIGNAL = signal.SIGTERM
        try:
            with (
                tempfile.TemporaryDirectory() as temporary,
                self.assertRaisesRegex(
                    supervisor.StageFailure,
                    "INTERRUPTED",
                ),
            ):
                supervisor.execute_stage_sequence(
                    commands,
                    paths=supervisor.RunPaths.from_run_root(Path(temporary)),
                    environment={},
                    deadline=100.0,
                    stage_runner=fake_run,
                )
        finally:
            supervisor._INTERRUPTED_SIGNAL = previous

        self.assertEqual(calls, [])


class DetachedFullRunAtomicWriteTest(unittest.TestCase):
    def test_existing_target_is_never_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "PASS.json"
            target.write_bytes(b"original\n")

            with self.assertRaises(FileExistsError):
                supervisor._exclusive_bytes(target, b"replacement\n")

            self.assertEqual(target.read_bytes(), b"original\n")
            self.assertEqual(
                list(Path(temporary).glob(".PASS.json.*.tmp")),
                [],
            )


class DetachedFullRunContinuationContractTest(unittest.TestCase):
    def test_source_paths_are_all_or_none(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            sources = tuple(root / name for name in ("failed", "scala", "static", "profile"))
            for source in sources:
                source.mkdir()

            self.assertIsNone(
                supervisor._continuation_source_paths(
                    failed_run_root=None,
                    scala_qualification_source=None,
                    haskell_static_source=None,
                    haskell_profile_source=None,
                )
            )
            with self.assertRaisesRegex(
                supervisor.FullRunError,
                "CONTINUATION_SOURCE_SET_INCOMPLETE",
            ):
                supervisor._continuation_source_paths(
                    failed_run_root=sources[0],
                    scala_qualification_source=None,
                    haskell_static_source=None,
                    haskell_profile_source=None,
                )
            self.assertEqual(
                supervisor._continuation_source_paths(
                    failed_run_root=sources[0],
                    scala_qualification_source=sources[1],
                    haskell_static_source=sources[2],
                    haskell_profile_source=sources[3],
                ),
                sources,
            )

    def test_strict_plan_accepts_reuse_and_rejects_plan_subject_drift(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plan_path = root / "run-plan.v1.json"
            plan = _plan(root, reuse=True)
            _write_json(plan_path, plan)

            loaded = supervisor._load_run_plan(plan_path, strict=True)
            self.assertEqual(loaded["retryPolicy"], supervisor.REUSE_RETRY_POLICY)

            plan["evidenceReuse"]["targetSubject"] = "f" * 40
            plan_path.unlink()
            _write_json(plan_path, plan)
            with self.assertRaisesRegex(
                supervisor.FullRunError,
                "RUN_PLAN_EVIDENCE_REUSE_INVALID",
            ):
                supervisor._load_run_plan(plan_path, strict=True)

    def test_control_manifest_hash_drift_is_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = supervisor.RunPaths.from_run_root(root / "run")
            manifest_path = paths.control / supervisor.CONTINUATION_MANIFEST_NAME
            _write_json(manifest_path, _continuation_manifest())
            plan = _plan(root, reuse=True)

            self.assertEqual(
                supervisor._validate_evidence_reuse(paths, plan),
                manifest_path,
            )
            manifest_path.write_bytes(b"{}\n")
            with self.assertRaisesRegex(
                supervisor.FullRunError,
                "CONTINUATION_CONTROL_MANIFEST_DRIFT",
            ):
                supervisor._validate_evidence_reuse(paths, plan)

    def test_control_manifest_subject_is_revalidated_after_copy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = supervisor.RunPaths.from_run_root(root / "run")
            manifest = _continuation_manifest()
            manifest["targetSubject"] = "f" * 40
            manifest_path = paths.control / supervisor.CONTINUATION_MANIFEST_NAME
            _write_json(manifest_path, manifest)
            plan = _plan(root, reuse=True)
            control = plan["evidenceReuse"]["controlManifest"]
            control["sha256"] = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
            control["sizeBytes"] = manifest_path.stat().st_size

            with self.assertRaisesRegex(
                supervisor.FullRunError,
                "CONTINUATION_SOURCE_MANIFEST_INVALID",
            ):
                supervisor._validate_evidence_reuse(paths, plan)

    def test_control_manifest_must_match_plan_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = supervisor.RunPaths.from_run_root(root / "run")
            manifest_path = paths.control / supervisor.CONTINUATION_MANIFEST_NAME
            _write_json(manifest_path, _continuation_manifest())
            plan = _plan(root, reuse=True)
            plan["evidenceReuse"]["parentSubject"] = "f" * 40

            with self.assertRaisesRegex(
                supervisor.FullRunError,
                "CONTINUATION_CONTROL_MANIFEST_PLAN_MISMATCH",
            ):
                supervisor._validate_evidence_reuse(paths, plan)


class DetachedFullRunFinalReportTest(unittest.TestCase):
    def test_accepts_pass_and_valid_performance_timeouts(self) -> None:
        for status in ("PASS", "PASS_WITH_VALID_PERFORMANCE_TIMEOUTS"):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as tmp:
                reports = Path(tmp)
                _write_final_reports(reports, status=status)
                result = supervisor.validate_final_reports(reports, SUBJECT)
                self.assertEqual(result["status"], status)
                self.assertEqual(result["completedBlockCount"], 87)

    def test_rejects_partial_or_unqualified_result(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            reports = Path(temporary)
            _write_final_reports(reports, completed=86, partial=1)
            with self.assertRaisesRegex(
                supervisor.FullRunError,
                "FINAL_REPORT_COMPLETENESS_INVALID",
            ):
                supervisor.validate_final_reports(reports, SUBJECT)

        with tempfile.TemporaryDirectory() as temporary:
            reports = Path(temporary)
            _write_final_reports(reports)
            scorecard = json.loads(
                (reports / "scorecard.v1.json").read_text(encoding="utf-8")
            )
            scorecard["candidates"]["scala"]["eligibility"] = "DISQUALIFIED"
            _write_json(reports / "scorecard.v1.json", scorecard)
            with self.assertRaisesRegex(
                supervisor.FullRunError,
                "FINAL_SCORECARD_INVALID",
            ):
                supervisor.validate_final_reports(reports, SUBJECT)


class DetachedFullRunTerminalTest(unittest.TestCase):
    def _prepared_run(self, root: Path) -> Path:
        run_root = root / "run"
        (run_root / "candidate").mkdir(parents=True)
        (run_root / "terminal").mkdir()
        (run_root / "stages").mkdir()
        (run_root / "checkpoints").mkdir()
        (run_root / "logs").mkdir()
        (run_root / "control").mkdir()
        (run_root / "control/detached_full_run.py").write_bytes(CONTROL_BYTES)
        plan_path = run_root / "run-plan.v1.json"
        _write_json(plan_path, _plan(root))
        (run_root / "run-plan.v1.sha256").write_text(
            f"{hashlib.sha256(plan_path.read_bytes()).hexdigest()}  {plan_path.name}\n",
            encoding="ascii",
        )
        _write_json(
            run_root / "candidate/PASS.json",
            {
                "schemaVersion": "s1.4x-detached-full-run-candidate-v1",
                "benchmarkSubjectCommit": SUBJECT,
                "status": "BENCHMARK_EVIDENCE_READY_FOR_REVIEW",
            },
        )
        correctness_raw = run_root / "correctness/raw.json"
        correctness_raw.parent.mkdir()
        correctness_raw.write_bytes(b"correctness\n")
        _write_json(
            run_root / "correctness/correctness-run-manifest.v1.json",
            {
                "schemaVersion": "s1.4x-correctness-run-manifest-v1",
                "benchmarkSubjectCommit": SUBJECT,
                "artifactCount": 1,
                "artifacts": [
                    {
                        "path": "raw.json",
                        "sha256": hashlib.sha256(b"correctness\n").hexdigest(),
                        "sizeBytes": len(b"correctness\n"),
                    }
                ],
                "status": "PASS",
            },
        )
        audit_ledger = run_root / "correctness-final-audit/final-candidate-audit.json"
        _write_json(audit_ledger, {"status": "PASS"})
        _write_final_reports(
            run_root / "final-reports",
            audit_sha256=hashlib.sha256(audit_ledger.read_bytes()).hexdigest(),
        )
        raw_file = run_root / "benchmark/run/s1-4x-full-test/r1/file"
        raw_file.parent.mkdir(parents=True)
        raw_file.write_bytes(b"x")
        raw_manifest = {
            "schemaVersion": "s1.4x-full-benchmark-raw-hash-manifest-v1",
            "benchmarkSubjectCommit": SUBJECT,
            "artifactCount": 1,
            "artifacts": [
                {
                    "path": "r1/file",
                    "sha256": hashlib.sha256(b"x").hexdigest(),
                    "sizeBytes": 1,
                }
            ],
            "status": "PASS",
        }
        _write_json(
            run_root / "final-reports/benchmark-raw-hash-manifest.v1.json",
            raw_manifest,
        )
        _write_finalizer_receipt(run_root)
        return run_root

    def test_service_finalize_writes_pass_last_for_exact_service_success(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_root = self._prepared_run(Path(temporary))
            result = supervisor.service_finalize(
                run_root,
                service_result="success",
                exit_code="exited",
                exit_status="0",
                audit_revalidator=_audit_pass,
            )

            self.assertEqual(result["status"], "PASS")
            self.assertTrue((run_root / "terminal/evidence-index.json").is_file())
            self.assertTrue((run_root / "terminal/PASS.json").is_file())
            self.assertFalse((run_root / "terminal/FAIL.json").exists())

    def test_service_failure_cannot_promote_candidate_pass(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_root = self._prepared_run(Path(temporary))
            result = supervisor.service_finalize(
                run_root,
                service_result="timeout",
                exit_code="killed",
                exit_status="TERM",
                audit_revalidator=_audit_pass,
            )

            self.assertEqual(result["status"], "FAIL")
            self.assertTrue((run_root / "terminal/FAIL.json").is_file())
            self.assertFalse((run_root / "terminal/PASS.json").exists())

    def test_candidate_marker_xor_violation_is_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_root = self._prepared_run(Path(temporary))
            _write_json(
                run_root / "candidate/FAIL.json",
                {
                    "schemaVersion": ("s1.4x-detached-full-run-candidate-v1"),
                    "benchmarkSubjectCommit": SUBJECT,
                    "status": "FAIL",
                },
            )
            result = supervisor.service_finalize(
                run_root,
                service_result="success",
                exit_code="exited",
                exit_status="0",
                audit_revalidator=_audit_pass,
            )

            self.assertEqual(result["status"], "FAIL")
            self.assertEqual(result["failureCode"], "CANDIDATE_MARKER_XOR_INVALID")

    def test_raw_artifact_drift_cannot_reach_terminal_pass(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_root = self._prepared_run(Path(temporary))
            (run_root / "benchmark/run/s1-4x-full-test/r1/file").write_bytes(b"changed")

            result = supervisor.service_finalize(
                run_root,
                service_result="success",
                exit_code="exited",
                exit_status="0",
                audit_revalidator=_audit_pass,
            )

            self.assertEqual(result["status"], "FAIL")
            self.assertEqual(
                result["failureCode"],
                "FINAL_REPORT_REVALIDATION_FAILED",
            )

    def test_correctness_or_audit_drift_cannot_reach_terminal_pass(
        self,
    ) -> None:
        drift_paths = (
            "correctness/raw.json",
            "correctness-final-audit/final-candidate-audit.json",
        )
        for relative in drift_paths:
            with (
                self.subTest(relative=relative),
                tempfile.TemporaryDirectory() as temporary,
            ):
                run_root = self._prepared_run(Path(temporary))
                (run_root / relative).write_bytes(b"changed")

                result = supervisor.service_finalize(
                    run_root,
                    service_result="success",
                    exit_code="exited",
                    exit_status="0",
                    audit_revalidator=_audit_pass,
                )

                self.assertEqual(result["status"], "FAIL")
                self.assertEqual(
                    result["failureCode"],
                    "FINAL_REPORT_REVALIDATION_FAILED",
                )

    def test_status_never_confuses_candidate_pass_with_terminal_pass(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_root = self._prepared_run(Path(temporary))
            pending = supervisor.inspect_status(run_root)
            self.assertEqual(pending["status"], "FINALIZING")

            supervisor.service_finalize(
                run_root,
                service_result="success",
                exit_code="exited",
                exit_status="0",
                audit_revalidator=_audit_pass,
            )
            passed = supervisor.inspect_status(run_root)
            self.assertEqual(passed["status"], "PASS")


class DetachedFullRunLauncherTest(unittest.TestCase):
    def test_launcher_uses_bounded_user_systemd_without_restart(self) -> None:
        launcher = (INTEGRATION / "tools/launch-detached-full-run.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("systemd-run", launcher)
        self.assertIn("--user", launcher)
        self.assertIn("--service-type=exec", launcher)
        self.assertIn("Restart=no", launcher)
        self.assertIn("KillMode=control-group", launcher)
        self.assertIn("RuntimeMaxSec=18h", launcher)
        self.assertIn("ExecStopPost=", launcher)
        self.assertIn("CONTROL_SUPERVISOR=", launcher)
        self.assertIn("ActiveState", launcher)
        self.assertIn("[[:space:]%]", launcher)
        for flag in (
            "--failed-run-root",
            "--scala-qualification-source",
            "--haskell-static-source",
            "--haskell-profile-source",
        ):
            self.assertIn(flag, launcher)
        self.assertIn("parentRun=%s", launcher)
        self.assertNotIn("nohup", launcher)
        self.assertNotIn("--collect", launcher)
        self.assertNotIn("Restart=on-", launcher)

    def test_supervisor_source_has_no_resume_or_retry_entrypoint(self) -> None:
        source = (INTEGRATION / "detached_full_run.py").read_text(encoding="utf-8")
        self.assertNotIn("shell=True", source)
        self.assertNotIn('add_parser("resume")', source)
        self.assertNotIn('add_parser("retry")', source)


if __name__ == "__main__":
    unittest.main()
