"""Haskell correctness/qualification raw evidence 위조 방어 테스트."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


TOOLS_ROOT = Path(__file__).resolve().parents[1]
HELPER_PATH = TOOLS_ROOT / "profile_workflow.py"
PLAN_PATH = TOOLS_ROOT.parent.parent / "benchmarks/benchmark-plan.v1.json"
CASE_ORDER = tuple(f"family/case-{index}" for index in range(7))


def load_helper():
    specification = importlib.util.spec_from_file_location(
        "profile_workflow_forgery",
        HELPER_PATH,
    )
    if specification is None or specification.loader is None:
        raise AssertionError("unable to load profile_workflow.py")
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


def raw_reports(multiplier: float) -> list[object]:
    return [
        "criterion",
        "1.6.4.0",
        [
            {
                "reportName": case_id,
                "reportAnalysis": {
                    "anMean": {"estPoint": float(index + 1) * multiplier}
                },
            }
            for index, case_id in enumerate(CASE_ORDER)
        ],
    ]


class ProfileEvidenceForgeryTests(unittest.TestCase):
    def test_receipt_and_qualification_require_exact_top_level_schema(
        self,
    ) -> None:
        helper = load_helper()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            correctness = root / "correctness.json"
            qualification = root / "qualification.json"
            correctness.write_bytes(
                helper.canonical_json_bytes(
                    {"schemaVersion": helper.CORRECTNESS_SCHEMA_VERSION},
                    trailing_newline=True,
                )
            )
            qualification.write_bytes(
                helper.canonical_json_bytes(
                    {"schemaVersion": helper.QUALIFICATION_SCHEMA_VERSION},
                    trailing_newline=True,
                )
            )
            with self.assertRaisesRegex(
                helper.WorkflowError,
                "CORRECTNESS_RECEIPT_INVALID",
            ):
                helper._validate_correctness_receipt(
                    correctness,
                    expected_profile_id="baseline-o0-fasm",
                    expected_source_tree_sha256="1" * 64,
                    expected_commit="2" * 40,
                )
            with self.assertRaisesRegex(
                helper.WorkflowError,
                "QUALIFICATION_ARTIFACT_INVALID",
            ):
                helper._validate_qualification_artifact(
                    qualification,
                    plan=helper.strict_json_load(PLAN_PATH),
                    expected_source_tree_sha256="1" * 64,
                    expected_commit="2" * 40,
                )

    def test_logged_command_reopens_logs_and_rejects_forged_argv(self) -> None:
        helper = load_helper()
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            standard_output = output / "build.stdout"
            standard_error = output / "build.stderr"
            standard_output.write_bytes(b"compiled\n")
            standard_error.write_bytes(b"")
            argv = ["/tool/ghcup", "--offline", "run"]
            record = {
                "phase": "build",
                "argv": argv,
                "argvSha256": helper.canonical_sha256(argv),
                "cwdPath": str(output),
                "startedAt": "2026-07-19T00:00:00.000000Z",
                "finishedAt": "2026-07-19T00:00:01.000000Z",
                "exitCode": 0,
                "stdoutPath": str(standard_output),
                "stdoutSha256": hashlib.sha256(b"compiled\n").hexdigest(),
                "stderrPath": str(standard_error),
                "stderrSha256": hashlib.sha256(b"").hexdigest(),
            }
            validated = helper.validate_logged_command_record(
                record,
                expected_phase="build",
                expected_argv=argv,
                expected_cwd=output,
                evidence_directory=output,
            )
            self.assertEqual(validated, record)

            forged_argv = copy.deepcopy(record)
            forged_argv["argv"] = ["/tool/ghcup", "--offline", "install"]
            forged_argv["argvSha256"] = helper.canonical_sha256(
                forged_argv["argv"]
            )
            with self.assertRaisesRegex(
                helper.WorkflowError,
                "COMMAND_ARGV_DRIFT",
            ):
                helper.validate_logged_command_record(
                    forged_argv,
                    expected_phase="build",
                    expected_argv=argv,
                    expected_cwd=output,
                    evidence_directory=output,
                )

            standard_output.write_bytes(b"forged\n")
            with self.assertRaisesRegex(
                helper.WorkflowError,
                "COMMAND_LOG_SHA256_DRIFT",
            ):
                helper.validate_logged_command_record(
                    record,
                    expected_phase="build",
                    expected_argv=argv,
                    expected_cwd=output,
                    evidence_directory=output,
                )

    def test_same_fd_json_rejects_hash_and_duplicate_key_forgery(self) -> None:
        helper = load_helper()
        with tempfile.TemporaryDirectory() as temporary:
            evidence = Path(temporary) / "raw.json"
            evidence.write_text('{"status":"PASS"}\n', encoding="utf-8")
            digest = hashlib.sha256(evidence.read_bytes()).hexdigest()
            document = helper.read_same_fd_json_evidence(
                evidence,
                expected_sha256=digest,
                label="RAW",
            )
            self.assertEqual(document, {"status": "PASS"})
            with self.assertRaisesRegex(
                helper.WorkflowError,
                "RAW_SHA256_DRIFT",
            ):
                helper.read_same_fd_json_evidence(
                    evidence,
                    expected_sha256="0" * 64,
                    label="RAW",
                )

            evidence.write_text(
                '{"status":"PASS","status":"FAIL"}\n',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                helper.WorkflowError,
                "RAW_DUPLICATE_KEY",
            ):
                helper.read_same_fd_json_evidence(
                    evidence,
                    expected_sha256=hashlib.sha256(
                        evidence.read_bytes()
                    ).hexdigest(),
                    label="RAW",
                )

    def test_measurement_marker_reconstructs_exact_pre_run_bytes(self) -> None:
        helper = load_helper()
        marker = helper.build_profile_marker(
            plan_sha256="1" * 64,
            selector_config_sha256="2" * 64,
            source_tree_sha256="3" * 64,
            order_block=0,
            profile_id="baseline-o0-fasm",
            case_order=CASE_ORDER,
            host_validity_sha256="4" * 64,
            marker_python_path="/usr/bin/python3",
            marker_python_pinned_fd_path="/proc/self/fd/70",
            marker_python_sha256="5" * 64,
            marker_script_path="/repo/profile_workflow.py",
            marker_script_pinned_fd_path="/proc/self/fd/71",
            marker_script_sha256="6" * 64,
            marker_argv=[
                "/usr/bin/env",
                "-a",
                "/usr/bin/python3",
                "/proc/self/fd/70",
                "/proc/self/fd/71",
                "mark-measurement-entered",
                "--qualification",
                "/evidence/marker.json",
            ],
            started_at="2026-07-19T00:00:00.000000Z",
        )
        pre_run_sha256 = hashlib.sha256(
            helper.canonical_json_bytes(marker, trailing_newline=True)
        ).hexdigest()
        measurement = {
            **marker,
            "state": "MEASUREMENT",
            "measurementEnteredAt": "2026-07-19T00:00:01.000000Z",
            "preRunSha256": pre_run_sha256,
        }
        self.assertEqual(
            helper.profile_marker_pre_run_sha256(measurement),
            pre_run_sha256,
        )
        measurement["preRunSha256"] = "0" * 64
        with self.assertRaisesRegex(
            helper.WorkflowError,
            "PROFILE_MARKER_PRE_RUN_SHA256_DRIFT",
        ):
            helper.profile_marker_pre_run_sha256(measurement)

    def test_qualification_ratios_are_recomputed_from_raw_criterion(self) -> None:
        helper = load_helper()
        ratios, estimates = helper.recompute_qualification_ratios(
            {
                "baseline-o0-fasm": raw_reports(1.0),
                "optimized-o2-fasm": raw_reports(0.5),
            },
            expected_case_order=CASE_ORDER,
        )
        self.assertEqual(ratios, {case_id: 0.5 for case_id in CASE_ORDER})
        self.assertEqual(
            tuple(estimates["baseline-o0-fasm"]),
            CASE_ORDER,
        )

        forged = json.loads(json.dumps(raw_reports(0.5)))
        forged[2][-1]["reportAnalysis"]["anMean"]["estPoint"] = 100.0
        forged_ratios, _ = helper.recompute_qualification_ratios(
            {
                "baseline-o0-fasm": raw_reports(1.0),
                "optimized-o2-fasm": forged,
            },
            expected_case_order=CASE_ORDER,
        )
        self.assertNotEqual(forged_ratios[CASE_ORDER[-1]], 0.5)


if __name__ == "__main__":
    unittest.main()
