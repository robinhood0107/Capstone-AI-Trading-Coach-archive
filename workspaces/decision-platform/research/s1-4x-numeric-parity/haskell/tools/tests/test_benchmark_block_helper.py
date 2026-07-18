"""Haskell Criterion raw report와 frozen inner command receipt 계약 테스트."""

from __future__ import annotations

import importlib.util
import math
import sys
import unittest
from pathlib import Path


TOOLS_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = TOOLS_ROOT / "haskell_benchmark_block.py"
MEASURE_KEYS = [
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
]


def load_helper():
    """구현 전 RED도 collection error 대신 명시적 assertion으로 남긴다."""

    if not MODULE_PATH.is_file():
        raise AssertionError("Haskell benchmark block helper is missing")
    specification = importlib.util.spec_from_file_location(
        "haskell_benchmark_block",
        MODULE_PATH,
    )
    if specification is None or specification.loader is None:
        raise AssertionError("unable to load Haskell benchmark block helper")
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


def criterion_report(
    name: str,
    *,
    measured: list[list[object]] | None = None,
    mean: float = 99.0,
) -> list[object]:
    rows = measured or [
        [4.0, 4.0, 40, 2, None, None, None, None, None, None, None, None],
        [9.0, 9.0, 90, 3, None, None, None, None, None, None, None, None],
    ]
    report = {
        "reportNumber": 0,
        "reportName": name,
        "reportKeys": MEASURE_KEYS,
        "reportMeasured": rows,
        "reportAnalysis": {
            "anRegress": [],
            "anMean": {
                "estPoint": mean,
                "estLowerBound": mean,
                "estUpperBound": mean,
                "estConfidenceLevel": 0.95,
            },
            "anStdDev": {
                "estPoint": 1.0,
                "estLowerBound": 1.0,
                "estUpperBound": 1.0,
                "estConfidenceLevel": 0.95,
            },
            "anOutlierVar": {
                "ovEffect": "Unaffected",
                "ovDesc": "unaffected",
                "ovFraction": 0.0,
            },
        },
        "reportOutliers": {
            "samplesSeen": 2,
            "lowSevere": 0,
            "lowMild": 0,
            "highMild": 0,
            "highSevere": 0,
        },
        "reportKDEs": [],
    }
    return ["criterion", "1.6.4.0", [report]]


class BenchmarkBlockHelperTests(unittest.TestCase):
    """Raw samples가 self-reported summary나 ambient command를 대체하지 못하게 한다."""

    def test_inner_command_is_exact_network_free_frozen_argv(self) -> None:
        helper = load_helper()
        command = helper.build_stack_benchmark_command(
            ghcup_bin=Path("/tools/ghcup"),
            stack_bin=Path("/tools/stack"),
            stack_yaml=Path("/repo/haskell/stack.yaml"),
            profile_options=["-O2", "-fasm"],
            time_limit_seconds=5,
            native_report=Path("/out/native.json"),
            criterion_prefix="path-transform/",
        )
        self.assertEqual(
            command,
            [
                "/tools/ghcup",
                "--offline",
                "run",
                "--quick",
                "--ghc",
                "9.10.3",
                "--stack",
                "3.11.1",
                "--",
                "/tools/stack",
                "--stack-yaml",
                "/repo/haskell/stack.yaml",
                "--no-terminal",
                "--color",
                "never",
                "--system-ghc",
                "--no-install-ghc",
                "bench",
                "--ghc-options=-O2 -fasm",
                (
                    "--benchmark-arguments=--time-limit 5 --json /out/native.json "
                    "--match prefix path-transform/ +RTS -N1 -RTS"
                ),
            ],
        )
        self.assertNotIn("--offline", command[10:])

    def test_raw_sample_median_not_claimed_analysis_drives_native_value(self) -> None:
        helper = load_helper()
        measurements = helper.parse_criterion_reports(
            criterion_report("path-transform/simple_returns/n32/b1"),
            ["path-transform/simple_returns/n32/b1"],
        )
        self.assertEqual(len(measurements), 1)
        self.assertEqual(measurements[0].sample_count, 2)
        self.assertEqual(measurements[0].native_seconds, 2.5)
        self.assertNotEqual(measurements[0].native_seconds, 99.0)

    def test_raw_parser_rejects_nonfinite_or_case_order_drift(self) -> None:
        helper = load_helper()
        nonfinite = criterion_report(
            "path-transform/simple_returns/n32/b1",
            measured=[
                [
                    math.inf,
                    1.0,
                    1,
                    1,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                ],
                [1.0, 1.0, 1, 1, None, None, None, None, None, None, None, None],
            ],
        )
        with self.assertRaisesRegex(helper.BlockError, "NONFINITE"):
            helper.parse_criterion_reports(
                nonfinite,
                ["path-transform/simple_returns/n32/b1"],
            )
        with self.assertRaisesRegex(helper.BlockError, "CASE_SET_OR_ORDER"):
            helper.parse_criterion_reports(
                criterion_report("path-transform/simple_returns/n32/b1"),
                ["path-transform/log_returns/n32/b1"],
            )

    def test_receipt_field_set_binds_marker_and_inner_commands(self) -> None:
        helper = load_helper()
        self.assertEqual(
            helper.RECEIPT_FIELDS,
            {
                "schemaVersion",
                "status",
                "boundaryId",
                "selectorId",
                "familyId",
                "rotationId",
                "outerRepetition",
                "runId",
                "benchmarkSubjectCommit",
                "planSha256",
                "qualificationSha256",
                "selectedProfileSha256",
                "sourceInputManifestSha256",
                "toolchainLockSha256",
                "benchmarkArtifactSha256",
                "markerPython",
                "markerScript",
                "markerArgv",
                "markerArgvSha256",
                "commandArgv",
                "commandArgvSha256",
                "effectiveRuntimeArguments",
                "effectiveRuntimeArgumentsSha256",
                "selectorInputClosureSha256",
                "nativeReportSha256",
                "blockResultSha256",
                "caseCount",
            },
        )


if __name__ == "__main__":
    unittest.main()
