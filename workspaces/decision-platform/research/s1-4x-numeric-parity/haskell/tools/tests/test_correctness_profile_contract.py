"""Authoritative Haskell full-correctness profile wrapper contract tests."""

from __future__ import annotations

import ast
import importlib.util
import re
import sys
import tempfile
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
    def test_every_workflow_stack_root_is_purpose_and_output_bound(self) -> None:
        helper = load_helper()
        cache = Path("/cache/s1-4x")
        first = helper.isolated_stack_root(
            cache,
            purpose="correctness-baseline",
            output_path=Path("/evidence/run-a"),
        )
        second = helper.isolated_stack_root(
            cache,
            purpose="correctness-baseline",
            output_path=Path("/evidence/run-b"),
        )
        other_purpose = helper.isolated_stack_root(
            cache,
            purpose="qualification",
            output_path=Path("/evidence/run-a"),
        )

        self.assertNotEqual(first, second)
        self.assertNotEqual(first, other_purpose)
        self.assertEqual(first.parent, cache)
        first_work = helper.isolated_stack_work_dir(first)
        second_work = helper.isolated_stack_work_dir(second)
        self.assertEqual(
            first_work,
            Path(".stack-work/s1-4x") / first.name,
        )
        self.assertFalse(first_work.is_absolute())
        self.assertNotEqual(first_work, second_work)
        self.assertRegex(
            first.name,
            re.compile(r"^stack-root-correctness-baseline-[0-9a-f]{24}$"),
        )
        with self.assertRaises(helper.WorkflowError):
            helper.isolated_stack_root(
                Path("relative-cache"),
                purpose="correctness",
                output_path=Path("/evidence/run"),
            )

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
        stack_root = Path("/cache/stack-root")
        work_dir = helper.isolated_stack_work_dir(stack_root)
        command = helper.build_stack_command(
            ghcup=Path("/tools/ghcup"),
            stack=Path("/tools/stack"),
            stack_yaml=Path("/repo/haskell/stack.yaml"),
            stack_root=stack_root,
            work_dir=work_dir,
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
                "--work-dir",
                ".stack-work/s1-4x/stack-root",
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
        self.assertNotIn("--offline", command[12:])

    def test_candidate_lookup_is_scoped_to_the_exact_stack_work_directory(
        self,
    ) -> None:
        helper = load_helper()
        with tempfile.TemporaryDirectory() as temporary:
            haskell_root = Path(temporary).resolve()
            work_a = Path(".stack-work/s1-4x/run-a")
            work_b = Path(".stack-work/s1-4x/run-b")
            suffix = (
                "dist/platform/ghc-9.10.3/build/"
                "s1-4x-haskell/s1-4x-haskell"
            )
            binary_a = haskell_root / work_a / suffix
            binary_b = haskell_root / work_b / suffix
            for binary, payload in (
                (binary_a, b"candidate-a"),
                (binary_b, b"candidate-b"),
            ):
                binary.parent.mkdir(parents=True)
                binary.write_bytes(payload)
                binary.chmod(0o755)

            self.assertEqual(
                helper._find_candidate_binary(
                    haskell_root,
                    work_dir=work_a,
                    ghc_version="9.10.3",
                ),
                binary_a,
            )

    def test_every_stack_command_call_and_shell_lane_sets_isolated_work_dir(
        self,
    ) -> None:
        tree = ast.parse(HELPER_PATH.read_text(encoding="utf-8"))
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "build_stack_command"
        ]
        self.assertGreaterEqual(len(calls), 10)
        for call in calls:
            with self.subTest(line=call.lineno):
                self.assertIn(
                    "work_dir",
                    {
                        keyword.arg
                        for keyword in call.keywords
                        if keyword.arg is not None
                    },
                )

        for name in (
            "run-candidate.sh",
            "run-property-evidence.sh",
            "run-ghc-9.14.1-compatibility.sh",
        ):
            source = (TOOLS_ROOT / name).read_text(encoding="utf-8")
            with self.subTest(wrapper=name):
                self.assertIn('STACK_WORK_DIR=".stack-work/s1-4x/', source)
                self.assertIn('--work-dir "$STACK_WORK_DIR"', source)

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
