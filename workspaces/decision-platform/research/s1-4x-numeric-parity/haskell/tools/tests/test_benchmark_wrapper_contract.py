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
        self.assertEqual(source.splitlines()[0], "#!/usr/bin/bash")
        self.assertIn('[[ "$#" -eq 20 ]]', source)
        offsets = [source.index(f'"{option}"') for option in EXPECTED_OPTIONS]
        self.assertEqual(offsets, sorted(offsets))
        self.assertEqual(
            source.count('/usr/bin/git -C "$PWD" rev-parse --show-toplevel'),
            1,
        )

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

    def test_outer_wrapper_scrubs_interpreter_and_git_injection(self) -> None:
        source = WRAPPER.read_text(encoding="utf-8")
        self.assertIn('export PATH="/usr/bin:/bin"', source)
        self.assertIn("compgen -e", source)
        for forbidden_environment in (
            "BASH_ENV",
            "ENV",
            "PYTHONPATH",
            "PYTHONHOME",
            "VIRTUAL_ENV",
            "JAVA_TOOL_OPTIONS",
            "JDK_JAVA_OPTIONS",
            "_JAVA_OPTIONS",
            "GIT_*",
        ):
            self.assertIn(forbidden_environment, source)
        self.assertIn("/usr/bin/sha256sum", source)
        self.assertIn("/usr/bin/awk", source)

    def test_outer_wrapper_requires_absolute_frozen_haskell_tool_paths(self) -> None:
        source = WRAPPER.read_text(encoding="utf-8")
        for variable in (
            "S1_4X_GHCUP_BIN",
            "S1_4X_STACK_BIN",
        ):
            with self.subTest(variable=variable):
                self.assertIn(f"${{{variable}:?", source)
        self.assertIn(
            'export S1_4X_GHCUP_SHA256="'
            '9ed5da5449b48043a0d17e767c05d2ef585e25a639bb934329496c6d2fad9cf8"',
            source,
        )
        self.assertIn(
            'export S1_4X_STACK_SHA256="'
            '923dbd137756652c67b376e2447c655b87fcc373f4d104b5073bca913471ecbe"',
            source,
        )
        for forbidden_discovery in ("command -v", "which ", "type -P"):
            with self.subTest(forbidden_discovery=forbidden_discovery):
                self.assertNotIn(forbidden_discovery, source)

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
