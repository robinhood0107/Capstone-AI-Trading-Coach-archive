"""Haskell Criterion outer wrapper의 sealed-memfd 실행 계약을 고정한다."""

from __future__ import annotations

import os
import stat
import unittest
from pathlib import Path


HASKELL_ROOT = Path(__file__).resolve().parents[2]
WRAPPER = HASKELL_ROOT / "tools" / "run-benchmark-block.sh"
HELPER = HASKELL_ROOT / "tools" / "haskell_benchmark_block.py"
BENCHMARK_MAIN = HASKELL_ROOT / "benchmark" / "Main.hs"
EXPECTED_OPTIONS = (
    "--plan",
    "--block-dir",
    "--qualification",
    "--boundary",
    "--selector",
    "--family",
    "--rotation",
    "--outer-repetition",
    "--run-id",
    "--benchmark-subject-commit",
)


class BenchmarkWrapperContractTests(unittest.TestCase):
    """Outer wrapper가 frozen argv와 self-identity 경계를 바꾸지 못하게 한다."""

    def test_outer_wrapper_is_executable_and_has_one_helper(self) -> None:
        self.assertTrue(WRAPPER.is_file(), "outer benchmark wrapper is missing")
        self.assertTrue(HELPER.is_file(), "outer benchmark helper is missing")
        mode = WRAPPER.stat().st_mode
        self.assertTrue(mode & stat.S_IXUSR, "outer benchmark wrapper is not executable")

    def test_outer_wrapper_uses_exact_twenty_argument_order(self) -> None:
        source = WRAPPER.read_text(encoding="utf-8")
        self.assertIn('[[ "$#" -eq 20 ]]', source)
        offsets = [source.index(f'"{option}"') for option in EXPECTED_OPTIONS]
        self.assertEqual(offsets, sorted(offsets))
        self.assertEqual(source.count('git rev-parse --show-toplevel'), 1)

    def test_sealed_outer_wrapper_never_discovers_its_own_path(self) -> None:
        source = WRAPPER.read_text(encoding="utf-8")
        for forbidden in (
            "BASH_SOURCE",
            'readlink -f "$0"',
            'realpath "$0"',
            "/proc/self/",
            "/proc/$$/",
        ):
            self.assertNotIn(forbidden, source)
        self.assertNotIn("dirname", source)
        self.assertNotIn("source-inputs.v1.json --write", source)

    def test_wrapper_has_no_ambient_stack_escape_hatch(self) -> None:
        source = WRAPPER.read_text(encoding="utf-8")
        for variable in ("STACK_YAML", "STACK_ROOT", "STACK_OPTS", "STACK_CONFIG"):
            self.assertIn(variable, source)
        self.assertIn("haskell_benchmark_block.py", source)
        self.assertNotIn("eval ", source)
        self.assertNotIn("bash -c", source)
        self.assertNotIn("sh -c", source)
        self.assertNotIn("mark-measurement-entered", source)

    def test_wrapper_mode_is_regular_not_symlink(self) -> None:
        self.assertFalse(WRAPPER.is_symlink())
        self.assertTrue(os.path.isfile(WRAPPER))

    def test_criterion_env_owns_the_single_measurement_transition(self) -> None:
        source = BENCHMARK_MAIN.read_text(encoding="utf-8")
        required_in_order = (
            "inputs <- loadFrozenInputs fixtureRoot",
            "traverse (setupPreparedCase inputs)",
            "markMeasurementEntered qualificationPath",
            "pure preparedCases",
        )
        offsets = [source.index(fragment) for fragment in required_in_order]
        self.assertEqual(offsets, sorted(offsets))
        self.assertEqual(
            source.count("markMeasurementEntered qualificationPath"),
            1,
        )
        self.assertIn('"S1_4X_BENCHMARK_QUALIFICATION"', source)
        self.assertIn("INVALID_PRE_RUN_QUALIFICATION_STATE", source)


if __name__ == "__main__":
    unittest.main()
