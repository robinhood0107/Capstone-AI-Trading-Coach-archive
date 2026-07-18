"""Authoritative Haskell full-correctness profile wrapper contract tests."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


TOOLS_ROOT = Path(__file__).resolve().parents[1]
HELPER_PATH = TOOLS_ROOT / "profile_workflow.py"
WRAPPER_PATH = TOOLS_ROOT / "run-correctness-profile.sh"


def load_helper():
    """RED 단계에서도 missing implementation을 명시적 실패로 보존한다."""

    if not HELPER_PATH.is_file():
        raise AssertionError("profile_workflow.py is missing")
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


class CorrectnessProfileContractTests(unittest.TestCase):
    def test_exact_profile_ids_map_to_exact_authoritative_options(self) -> None:
        helper = load_helper()
        self.assertEqual(
            helper.profile_options("baseline-o0-fasm"),
            ("-O0", "-fasm"),
        )
        self.assertEqual(
            helper.profile_options("optimized-o2-fasm"),
            ("-O2", "-fasm"),
        )
        for invalid in ("baseline", "optimized", "-O2 -fasm", "", True):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(
                    helper.WorkflowError,
                    "PROFILE_ID_INVALID",
                ):
                    helper.profile_options(invalid)

    def test_stack_command_uses_offline_ghcup_and_no_stack_network_flag(self) -> None:
        helper = load_helper()
        command = helper.build_stack_command(
            ghcup=Path("/tools/ghcup"),
            stack=Path("/tools/stack"),
            stack_yaml=Path("/repo/haskell/stack.yaml"),
            stack_root=Path("/cache/stack-root"),
            ghc_version="9.10.3",
            operation=[
                "test",
                "--pedantic",
                "--ghc-options=-O2 -fasm",
            ],
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
                "--stack-root",
                "/cache/stack-root",
                "--stack-yaml",
                "/repo/haskell/stack.yaml",
                "--no-terminal",
                "--color",
                "never",
                "--system-ghc",
                "--no-install-ghc",
                "--hpack-force",
                "test",
                "--pedantic",
                "--ghc-options=-O2 -fasm",
            ],
        )
        self.assertNotIn("--offline", command[10:])

    def test_wrapper_is_exclusive_typed_and_has_no_arbitrary_command_escape(
        self,
    ) -> None:
        self.assertTrue(WRAPPER_PATH.is_file(), "correctness wrapper is missing")
        source = WRAPPER_PATH.read_text(encoding="utf-8")
        for required in (
            "baseline-o0-fasm",
            "optimized-o2-fasm",
            "--output-dir",
            "ABSOLUTE_NEW_DIRECTORY",
            "assert-toolchain.sh",
            "profile_workflow.py",
            "correctness",
            "STACK_YAML",
            "STACK_ROOT",
            "STACK_OPTS",
            "STACK_CONFIG",
        ):
            with self.subTest(required=required):
                self.assertIn(required, source)
        for forbidden in ("eval ", "bash -c", "sh -c", "--allow-profile"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)

    def test_correctness_receipt_requires_full_typed_matrix(self) -> None:
        helper = load_helper()
        self.assertEqual(
            helper.CORRECTNESS_PHASES,
            (
                "build",
                "test",
                "canonical-process",
                "canonical-compare",
                "semantic-process",
                "semantic-compare",
            ),
        )
        self.assertEqual(
            helper.CORRECTNESS_SCHEMA_VERSION,
            "s1.4x-haskell-full-correctness-v1",
        )


if __name__ == "__main__":
    unittest.main()
