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
        self.assertIn("verify_pinned_object", source)

    def test_outer_wrapper_requires_shared_v3_path_sha_and_pinned_fd_triples(
        self,
    ) -> None:
        source = WRAPPER.read_text(encoding="utf-8")
        for prefix in (
            "S1_4X_GHCUP",
            "S1_4X_STACK",
            "S1_4X_AUTHORITATIVE_GHC",
            "S1_4X_LATEST_GHC",
            "S1_4X_HLINT",
            "S1_4X_STYLISH",
        ):
            with self.subTest(prefix=prefix):
                for suffix in ("_BIN", "_SHA256", "_PINNED_FD_PATH"):
                    self.assertIn(f"${{{prefix}{suffix}:?", source)
                    self.assertIn(f'{prefix}{suffix}="', source)
        self.assertNotIn("export S1_4X_GHCUP_SHA256=", source)
        self.assertNotIn("export S1_4X_STACK_SHA256=", source)
        for forbidden_discovery in ("command -v", "which ", "type -P"):
            with self.subTest(forbidden_discovery=forbidden_discovery):
                self.assertNotIn(forbidden_discovery, source)

    def test_outer_wrapper_forwards_profile_evidence_fd_sha_source_triples(
        self,
    ) -> None:
        source = WRAPPER.read_text(encoding="utf-8")
        evidence = {
            "S1_4X_HASKELL_BASELINE_CORRECTNESS": "BASELINE_CORRECTNESS",
            "S1_4X_HASKELL_OPTIMIZED_CORRECTNESS": "OPTIMIZED_CORRECTNESS",
            "S1_4X_HASKELL_QUALIFICATION_ARTIFACT": "QUALIFICATION_ARTIFACT",
        }
        for prefix, local_name in evidence.items():
            with self.subTest(prefix=prefix):
                self.assertIn(f"${{{prefix}:?", source)
                self.assertIn(f"${{{prefix}_SHA256:?", source)
                self.assertIn(f"${{{prefix}_SOURCE_PATH:?", source)
                self.assertIn(f'{prefix}="${local_name}"', source)
                self.assertIn(f'{prefix}_SHA256="', source)
                self.assertIn(f'{prefix}_SOURCE_PATH="', source)

    def test_original_tool_paths_are_layout_only_and_never_reopened(self) -> None:
        source = WRAPPER.read_text(encoding="utf-8")
        self.assertIn("verify_source_path_layout", source)
        self.assertIn("verify_pinned_object", source)
        self.assertNotIn("verify_executable", source)
        self.assertNotIn('/usr/bin/realpath -e -- "$executable"', source)
        self.assertNotIn('/usr/bin/sha256sum "$executable"', source)

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

    def test_benchmark_self_reports_the_exact_executed_runtime_identity(self) -> None:
        source = BENCHMARK_MAIN.read_text(encoding="utf-8")
        for token in (
            "getExecutablePath",
            "exclusiveAtomicWrite",
            '"S1_4X_BENCHMARK_RUNTIME_IDENTITY"',
            '"s1.4x-haskell-benchmark-runtime-identity-v1"',
            '"boundaryId" .= ("haskell"',
            '"selectorId" .= selectorIdText',
            '"executedBenchmarkPath" .= executablePath',
            '"executedBenchmarkSha256" .= executableSha256',
            '"status" .= ("PASS"',
        ):
            with self.subTest(token=token):
                self.assertIn(token, source)
        self.assertLess(
            source.index("publishRuntimeIdentity"),
            source.index("  defaultMain"),
        )

    def test_outer_wrapper_exports_verified_authoritative_ghc_sha(self) -> None:
        source = WRAPPER.read_text(encoding="utf-8")
        self.assertIn(
            'S1_4X_AUTHORITATIVE_GHC_SHA256="$AUTHORITATIVE_GHC_SHA256"',
            source,
        )
        self.assertIn(
            'S1_4X_AUTHORITATIVE_GHC_PINNED_FD_PATH='
            '"$AUTHORITATIVE_GHC_PINNED_FD_PATH"',
            source,
        )

    def test_wrapper_ignores_hostile_home_and_forwards_only_explicit_cache(
        self,
    ) -> None:
        source = WRAPPER.read_text(encoding="utf-8")
        self.assertIn(
            'CACHE_ROOT="${S1_4X_CACHE_ROOT:?S1_4X_CACHE_ROOT is required}"',
            source,
        )
        self.assertIn('HOME="/nonexistent"', source)
        self.assertIn('S1_4X_CACHE_ROOT="$CACHE_ROOT"', source)
        self.assertNotIn('RUNTIME_HOME="${HOME:', source)


if __name__ == "__main__":
    unittest.main()
