"""Authoritative Haskell full-correctness profile wrapper contract tests."""

from __future__ import annotations

import ast
import importlib.util
import os
import re
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
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
    def test_oracle_comparator_executes_only_pinned_module_bytes_with_source_semantics(
        self,
    ) -> None:
        helper = load_helper()
        with tempfile.TemporaryDirectory() as temporary:
            numeric_root = Path(temporary).resolve() / "numeric"
            oracle_root = numeric_root / "oracle"
            schema_root = numeric_root / "contract"
            oracle_root.mkdir(parents=True)
            schema_root.mkdir()
            compare_source = oracle_root / "compare_results.py"
            common_source = oracle_root / "oracle_common.py"
            schema_source = schema_root / "schema.txt"
            output = numeric_root / "comparison.txt"
            common_source.write_text(
                "PINNED_VALUE = 'pinned-common'\n",
                encoding="utf-8",
            )
            compare_source.write_text(
                "from pathlib import Path\n"
                "import sys\n"
                "from oracle_common import PINNED_VALUE\n"
                "schema = Path(__file__).resolve().parent.parent "
                "/ 'contract' / 'schema.txt'\n"
                "Path(sys.argv[1]).write_text(\n"
                "    '|'.join((PINNED_VALUE, schema.read_text(), __file__)),\n"
                "    encoding='utf-8',\n"
                ")\n",
                encoding="utf-8",
            )
            schema_source.write_text("schema-ok", encoding="utf-8")
            comparator = helper._pin_oracle_comparator(numeric_root)

            # Pin 이후 pathname을 바꿔도 child는 retained FD bytes만 실행해야 한다.
            compare_source.write_text(
                "raise SystemExit(91)\n",
                encoding="utf-8",
            )
            common_source.write_text(
                "PINNED_VALUE = 'poisoned-common'\n",
                encoding="utf-8",
            )
            command = helper._oracle_compare_command(
                python_path=Path(sys.executable),
                comparator=comparator,
                arguments=[str(output)],
            )
            completed = subprocess.run(
                command,
                env={
                    "LC_ALL": "C.UTF-8",
                    "PATH": "/usr/bin:/bin",
                    "PYTHONPATH": str(numeric_root / "hostile-python-path"),
                },
                check=False,
                capture_output=True,
                pass_fds=(
                    comparator.compare_script.descriptor,
                    comparator.common_module.descriptor,
                ),
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(
                output.read_text(encoding="utf-8"),
                f"pinned-common|schema-ok|{compare_source}",
            )
            self.assertEqual(command[1:3], ["-I", "-c"])
            self.assertEqual(command[4], str(comparator.compare_script.fd_path))
            self.assertEqual(command[5], str(comparator.common_module.fd_path))
            runtime = helper.BenchmarkPythonRuntime(
                source_path=Path("/tools/python3.12"),
                fd_path=Path("/proc/self/fd/91"),
                descriptor=91,
                sha256="a" * 64,
                mode=0o100755,
                identity=(1, 2, 3, 4, 5, 1),
                configuration_path=Path("/tools/pyvenv.cfg"),
                configuration_sha256="b" * 64,
                configuration_identity=(6, 7, 8, 0o100644, 9, 10, 1),
                dependency_closure=("4.26.0", "2.5.1", "2.5.1"),
            )
            portable_command = helper._portable_argv(
                helper._oracle_compare_command(
                    python_path=runtime.fd_path,
                    comparator=comparator,
                    arguments=["--output", str(output)],
                ),
                helper._oracle_compare_path_ids(runtime, comparator),
            )
            self.assertFalse(
                any(
                    "/proc/self/fd/" in argument
                    or str(oracle_root) in argument
                    for argument in portable_command
                )
            )
            self.assertEqual(
                portable_command[4],
                helper._pinned_file_path_id(comparator.compare_script),
            )
            self.assertEqual(
                portable_command[5],
                helper._pinned_file_path_id(comparator.common_module),
            )
            self.assertEqual(
                portable_command[6:8],
                [
                    helper.ORACLE_COMPARE_SOURCE_PATH_ID,
                    helper.ORACLE_COMMON_SOURCE_PATH_ID,
                ],
            )

    def test_every_oracle_comparison_uses_shared_pinned_invocation(self) -> None:
        source = HELPER_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        shared_calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_oracle_compare_command"
        ]
        self.assertEqual(len(shared_calls), 6)
        for call in shared_calls:
            with self.subTest(line=call.lineno):
                self.assertEqual(
                    {
                        keyword.arg
                        for keyword in call.keywords
                        if keyword.arg is not None
                    },
                    {"python_path", "comparator", "arguments"},
                )
        self.assertNotIn("str(compare_script.fd_path)", source)
        self.assertEqual(
            source.count("str(comparator.compare_script.fd_path)"),
            2,
        )

    def test_sealed_child_environment_preserves_exact_ghcup_prefix(self) -> None:
        helper = load_helper()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            home = root / "home"
            ghcup_prefix = root / "job-local-ghcup"
            home.mkdir()
            ghcup_prefix.mkdir()
            runtime = helper.BenchmarkPythonRuntime(
                source_path=Path("/tools/python3.12"),
                fd_path=Path("/proc/self/fd/91"),
                descriptor=91,
                sha256="a" * 64,
                mode=0o100755,
                identity=(1, 2, 3, 4, 5, 1),
                configuration_path=Path("/tools/pyvenv.cfg"),
                configuration_sha256="b" * 64,
                configuration_identity=(6, 7, 8, 0o100644, 9, 10, 1),
                dependency_closure=("4.26.0", "2.5.1", "2.5.1"),
            )
            with mock.patch.dict(
                os.environ,
                {
                    "HOME": str(home),
                    "GHCUP_INSTALL_BASE_PREFIX": str(ghcup_prefix),
                },
                clear=True,
            ):
                environment = helper._sealed_child_environment(
                    ghc_bin=Path("/tools/ghc"),
                    stack_bin=Path("/tools/stack"),
                    python_runtime=runtime,
                )

            self.assertEqual(
                environment["GHCUP_INSTALL_BASE_PREFIX"],
                str(ghcup_prefix),
            )
            self.assertEqual(environment["LC_ALL"], "C.UTF-8")

            forged_link = root / "forged-prefix"
            forged_link.symlink_to(ghcup_prefix, target_is_directory=True)
            for invalid in (
                None,
                "relative-ghcup-prefix",
                str(root / "missing-prefix"),
                str(forged_link),
            ):
                ambient = {"HOME": str(home)}
                if invalid is not None:
                    ambient["GHCUP_INSTALL_BASE_PREFIX"] = invalid
                with self.subTest(invalid=invalid), mock.patch.dict(
                    os.environ,
                    ambient,
                    clear=True,
                ):
                    with self.assertRaisesRegex(
                        helper.WorkflowError,
                        "GHCUP_INSTALL_BASE_PREFIX",
                    ):
                        helper._sealed_child_environment(
                            ghc_bin=Path("/tools/ghc"),
                            stack_bin=Path("/tools/stack"),
                            python_runtime=runtime,
                        )

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
            Path(f".stack-work-s1-4x-{first.name}"),
        )
        self.assertFalse(first_work.is_absolute())
        self.assertEqual(len(first_work.parts), 1)
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
                ".stack-work-s1-4x-stack-root",
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
            work_a = Path(".stack-work-s1-4x-run-a")
            work_b = Path(".stack-work-s1-4x-run-b")
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
                self.assertIn('STACK_WORK_DIR=".stack-work-s1-4x-', source)
                self.assertIn('--work-dir "$STACK_WORK_DIR"', source)
                self.assertIn('export LC_ALL="C.UTF-8"', source)

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
