"""GHC 9.14.1 non-scoring current replay wrapper contract tests."""

from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path


TOOLS_ROOT = Path(__file__).resolve().parents[1]
HELPER_PATH = TOOLS_ROOT / "profile_workflow.py"
RUN_WRAPPER = TOOLS_ROOT / "run-ghc-9.14.1-compatibility.sh"
VALIDATE_WRAPPER = TOOLS_ROOT / "validate-ghc-9.14.1-compatibility.sh"


def load_helper():
    specification = importlib.util.spec_from_file_location(
        "profile_workflow",
        HELPER_PATH,
    )
    if specification is None or specification.loader is None:
        raise AssertionError("unable to load profile_workflow.py")
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


class GhcCompatibilityWrapperContractTests(unittest.TestCase):
    def test_current_failure_classification_accepts_only_frozen_dependency(
        self,
    ) -> None:
        helper = load_helper()
        accepted = {
            "schemaVersion": "s1.4x-ghc-compatibility-result-v1",
            "result": "FAIL_FROZEN_DEPENDENCY",
            "nonScoring": True,
            "performanceInput": False,
            "failurePhase": "dependency",
            "expectedBootSetDifferenceOnly": True,
            "nonBootPlanEquivalent": True,
            "forbiddenOverrideKeysPresent": [],
            "candidateSourceTreeSha256": "1" * 64,
            "dependencyQualification": {
                "status": "FAIL",
                "evidenceSha256": "2" * 64,
            },
            "downstreamNotRun": [
                "candidateCompile",
                "fullCorrectness",
                "stableErrorReplay",
                "processReplay",
                "oracleReplay",
                "crossReplay",
            ],
        }
        helper.validate_current_compatibility_status(
            accepted,
            expected_source_tree_sha256="1" * 64,
        )
        for field, value in (
            ("result", "FAIL_CANDIDATE_SOURCE"),
            ("nonScoring", False),
            ("performanceInput", True),
            ("failurePhase", "compile"),
        ):
            with self.subTest(field=field):
                invalid = json.loads(json.dumps(accepted))
                invalid[field] = value
                with self.assertRaises(helper.WorkflowError):
                    helper.validate_current_compatibility_status(
                        invalid,
                        expected_source_tree_sha256="1" * 64,
                    )

    def test_compatibility_command_is_exact_offline_ghcup_dry_run(self) -> None:
        helper = load_helper()
        command = helper.build_stack_command(
            ghcup=Path("/tools/ghcup"),
            stack=Path("/tools/stack"),
            stack_yaml=Path("/repo/haskell/stack-ghc-9.14.1.yaml"),
            stack_root=Path("/cache/stack-root-ghc914"),
            ghc_version="9.14.1",
            operation=[
                "build",
                "--dry-run",
                "--test",
                "--bench",
                "--no-run-tests",
                "--no-run-benchmarks",
            ],
        )
        self.assertEqual(command[0:10], [
            "/tools/ghcup",
            "--offline",
            "run",
            "--quick",
            "--ghc",
            "9.14.1",
            "--stack",
            "3.11.1",
            "--",
            "/tools/stack",
        ])
        self.assertIn("--system-ghc", command)
        self.assertIn("--no-install-ghc", command)
        self.assertNotIn("--resolver", command)
        self.assertNotIn("--extra-dep", " ".join(command))

    def test_run_and_validate_wrappers_preserve_typed_failure_evidence(self) -> None:
        for path in (RUN_WRAPPER, VALIDATE_WRAPPER):
            self.assertTrue(path.is_file(), f"missing wrapper: {path.name}")
        run_source = RUN_WRAPPER.read_text(encoding="utf-8")
        validate_source = VALIDATE_WRAPPER.read_text(encoding="utf-8")
        for required in (
            "--stack-yaml",
            "--full-matrix",
            "--output-dir",
            "--ghc",
            "9.14.1",
            "--stack",
            "3.11.1",
            "--offline",
            "--quick",
            "--system-ghc",
            "--no-install-ghc",
            "--dry-run",
            "capture-compatibility-failure",
            "replay-compatibility-success",
            "authoritative-boot.dump",
            "compatibility-boot.dump",
            "pantry.sqlite3",
        ):
            self.assertIn(required, run_source)
        self.assertNotIn("COMPATIBILITY_SOLVE_UNEXPECTED_SUCCESS", run_source)
        for phase in (
            "candidateCompile",
            "fullCorrectness",
            "stableErrorReplay",
            "processReplay",
            "oracleReplay",
            "crossReplay",
        ):
            with self.subTest(phase=phase):
                self.assertIn(phase, HELPER_PATH.read_text(encoding="utf-8"))
        for required in (
            "validate-compatibility",
            "ghc-9.14.1-compatibility.v1.json",
            "compatibility-failure.v1.json",
        ):
            self.assertIn(required, validate_source)
        for source in (run_source, validate_source):
            self.assertNotIn("--extra-dep", source)
            self.assertNotIn("--allow-newer", source)
            self.assertNotIn("--resolver", source)
            self.assertNotIn("eval ", source)

    def test_current_lock_hashes_read_both_exact_files_independently(self) -> None:
        helper = load_helper()
        hashes = helper.current_compatibility_lock_hashes(
            HELPER_PATH.parent.parent,
        )

        self.assertEqual(
            hashes,
            {
                "authoritativeStackLockSha256": helper.sha256_file(
                    HELPER_PATH.parent.parent / "stack.yaml.lock"
                ),
                "compatibilityStackLockSha256": helper.sha256_file(
                    HELPER_PATH.parent.parent / "stack-ghc-9.14.1.yaml.lock"
                ),
            },
        )
        source = HELPER_PATH.read_text(encoding="utf-8")
        self.assertNotIn(
            'result["compatibilityStackLockSha256"] = result[\n'
            '        "authoritativeStackLockSha256"\n'
            "    ]",
            source,
        )

    def test_pass_result_closes_every_typed_replay(self) -> None:
        helper = load_helper()
        result = helper.build_current_compatibility_pass_result(
            candidate_source_tree_sha256="1" * 64,
            command_records=[
                {
                    "phase": phase,
                    "argv": [phase],
                    "cwdId": "HASKELL_COMPAT_ROOT",
                    "startedAt": "2026-07-19T00:00:00Z",
                    "endedAt": "2026-07-19T00:00:01Z",
                    "exitCode": 0,
                    "stdoutSha256": "2" * 64,
                    "stderrSha256": "3" * 64,
                }
                for phase in (
                    "dependency",
                    "candidateCompile",
                    "fullCorrectness",
                    "stableErrorReplay",
                    "processReplay",
                    "oracleReplay",
                    "crossReplay",
                )
            ],
            phase_evidence_sha256={
                phase: str(index) * 64
                for index, phase in enumerate(
                    (
                        "dependency",
                        "candidateCompile",
                        "fullCorrectness",
                        "stableErrorReplay",
                        "processReplay",
                        "oracleReplay",
                        "crossReplay",
                    ),
                    start=1,
                )
            },
            current_plan={
                "authoritativeBootSetSha256": "8" * 64,
                "compatibilityBootSetSha256": "9" * 64,
                "authoritativeNonBootPlanSha256": "a" * 64,
                "compatibilityNonBootPlanSha256": "a" * 64,
                "authoritativePackageSetSha256": "b" * 64,
                "configurationAstSha256": "c" * 64,
            },
            haskell_root=HELPER_PATH.parent.parent,
        )

        self.assertEqual(result["result"], "PASS")
        self.assertIsNone(result["failurePhase"])
        self.assertEqual(result["downstreamNotRun"], [])
        self.assertIsNone(result["minimalReproducerSha256"])
        self.assertEqual(result["candidateCompile"]["status"], "PASS")
        for phase in (
            "fullCorrectness",
            "stableErrorReplay",
            "processReplay",
            "oracleReplay",
            "crossReplay",
        ):
            self.assertEqual(
                result[phase],
                {
                    "evidenceSha256": result[phase]["evidenceSha256"],
                    "mismatchCount": 0,
                    "status": "PASS",
                },
            )


if __name__ == "__main__":
    unittest.main()
