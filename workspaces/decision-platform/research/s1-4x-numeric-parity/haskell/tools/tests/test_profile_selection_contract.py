"""Haskell 4×2×7 qualification marker와 frozen selector contract tests."""

from __future__ import annotations

import importlib.util
import inspect
import json
import os
import subprocess
import sys
import tempfile
import unittest
import venv
from pathlib import Path

from tools import haskell_benchmark_block as benchmark_helper


TOOLS_ROOT = Path(__file__).resolve().parents[1]
HELPER_PATH = TOOLS_ROOT / "profile_workflow.py"
QUALIFICATION_WRAPPER = TOOLS_ROOT / "run-profile-qualification.sh"
SELECTOR_WRAPPER = TOOLS_ROOT / "select-proven-profile.sh"
CASE_ORDER = tuple(f"family/case-{index}" for index in range(7))
PROFILE_ORDER_BLOCKS = (
    ("baseline-o0-fasm", "optimized-o2-fasm"),
    ("optimized-o2-fasm", "baseline-o0-fasm"),
    ("optimized-o2-fasm", "baseline-o0-fasm"),
    ("baseline-o0-fasm", "optimized-o2-fasm"),
)


def load_helper():
    specification = importlib.util.spec_from_file_location(
        "profile_workflow",
        HELPER_PATH,
    )
    if specification is None or specification.loader is None:
        raise AssertionError("unable to load profile_workflow.py")
    module = importlib.util.module_from_spec(specification)
    sys.modules[SPECIFICATION_NAME] = module
    specification.loader.exec_module(module)
    return module


SPECIFICATION_NAME = "profile_workflow"


class ProfileSelectionContractTests(unittest.TestCase):
    @staticmethod
    def _git(repository: Path, *arguments: str) -> str:
        completed = subprocess.run(
            ["/usr/bin/git", "-C", str(repository), *arguments],
            check=True,
            capture_output=True,
            text=True,
        )
        return completed.stdout.strip()

    def _profile_repository(
        self,
        temporary: str,
    ) -> tuple[Path, Path, Path, str]:
        repository = Path(temporary) / "repository"
        profile = repository / "haskell/selected-profile.v1.json"
        manifest = repository / "haskell/source-inputs.v1.json"
        profile.parent.mkdir(parents=True)
        profile.write_text(
            '{"schemaVersion":"s1.4x-haskell-selected-profile-pending-v1"}\n',
            encoding="utf-8",
        )
        manifest.write_text('{"state":"pending"}\n', encoding="utf-8")
        self._git(repository, "init")
        self._git(repository, "config", "user.email", "test@example.invalid")
        self._git(repository, "config", "user.name", "S1.4X Test")
        self._git(repository, "add", ".")
        self._git(repository, "commit", "-m", "pending profile subject")
        return repository, profile, manifest, self._git(
            repository,
            "rev-parse",
            "HEAD",
        )

    def test_selected_profile_materialize_commit_check_is_non_circular(
        self,
    ) -> None:
        helper = load_helper()
        with tempfile.TemporaryDirectory() as temporary:
            repository, profile, manifest, subject = self._profile_repository(
                temporary
            )
            materialize = helper.resolve_selected_profile_commit_fixed_point(
                repository,
                mode="materialize",
                expected_subject_commit=subject,
                profile_relative_path="haskell/selected-profile.v1.json",
                manifest_relative_path="haskell/source-inputs.v1.json",
            )
            self.assertEqual(
                materialize,
                {
                    "currentCommit": subject,
                    "materializationCommit": None,
                    "preMaterializationSubjectCommit": subject,
                },
            )

            profile.write_text(
                '{"schemaVersion":"s1.4x-haskell-selected-profile-v1"}\n',
                encoding="utf-8",
            )
            manifest.write_text('{"state":"final"}\n', encoding="utf-8")
            self._git(repository, "add", ".")
            self._git(repository, "commit", "-m", "materialize selected profile")
            materialization_commit = self._git(
                repository,
                "rev-parse",
                "HEAD",
            )
            check = helper.resolve_selected_profile_commit_fixed_point(
                repository,
                mode="check",
                expected_subject_commit=subject,
                profile_relative_path="haskell/selected-profile.v1.json",
                manifest_relative_path="haskell/source-inputs.v1.json",
            )
            self.assertEqual(
                check,
                {
                    "currentCommit": materialization_commit,
                    "materializationCommit": materialization_commit,
                    "preMaterializationSubjectCommit": subject,
                },
            )

            report = repository / "reports/post-profile.txt"
            report.parent.mkdir()
            report.write_text("report-only descendant\n", encoding="utf-8")
            self._git(repository, "add", ".")
            self._git(repository, "commit", "-m", "report-only descendant")
            descendant = helper.resolve_selected_profile_commit_fixed_point(
                repository,
                mode="check",
                expected_subject_commit=subject,
                profile_relative_path="haskell/selected-profile.v1.json",
                manifest_relative_path="haskell/source-inputs.v1.json",
            )
            self.assertEqual(
                descendant["materializationCommit"],
                materialization_commit,
            )

    def test_selected_profile_commit_rejects_extra_or_later_profile_edits(
        self,
    ) -> None:
        helper = load_helper()
        with tempfile.TemporaryDirectory() as temporary:
            repository, profile, manifest, subject = self._profile_repository(
                temporary
            )
            profile.write_text(
                '{"schemaVersion":"s1.4x-haskell-selected-profile-v1"}\n',
                encoding="utf-8",
            )
            manifest.write_text('{"state":"final"}\n', encoding="utf-8")
            (repository / "unrelated.txt").write_text(
                "must not share the materialization commit\n",
                encoding="utf-8",
            )
            self._git(repository, "add", ".")
            self._git(repository, "commit", "-m", "invalid mixed materialization")
            with self.assertRaisesRegex(
                helper.WorkflowError,
                "SELECTED_PROFILE_MATERIALIZATION_COMMIT_INVALID",
            ):
                helper.resolve_selected_profile_commit_fixed_point(
                    repository,
                    mode="check",
                    expected_subject_commit=subject,
                    profile_relative_path="haskell/selected-profile.v1.json",
                    manifest_relative_path="haskell/source-inputs.v1.json",
                )

        with tempfile.TemporaryDirectory() as temporary:
            repository, profile, manifest, subject = self._profile_repository(
                temporary
            )
            profile.write_text(
                '{"schemaVersion":"s1.4x-haskell-selected-profile-v1"}\n',
                encoding="utf-8",
            )
            manifest.write_text('{"state":"final"}\n', encoding="utf-8")
            self._git(repository, "add", ".")
            self._git(repository, "commit", "-m", "materialize selected profile")
            profile.write_text(
                '{"schemaVersion":"s1.4x-haskell-selected-profile-v1","drift":true}\n',
                encoding="utf-8",
            )
            self._git(repository, "add", ".")
            self._git(repository, "commit", "-m", "later profile drift")
            with self.assertRaisesRegex(
                helper.WorkflowError,
                "SELECTED_PROFILE_MATERIALIZATION_COMMIT_INVALID",
            ):
                helper.resolve_selected_profile_commit_fixed_point(
                    repository,
                    mode="check",
                    expected_subject_commit=subject,
                    profile_relative_path="haskell/selected-profile.v1.json",
                    manifest_relative_path="haskell/source-inputs.v1.json",
                )

    def test_profile_marker_is_exact_same_snapshot_and_single_transition(self) -> None:
        helper = load_helper()
        with tempfile.TemporaryDirectory() as temporary:
            marker_path = Path(temporary) / "measurement-state.json"
            marker = helper.build_profile_marker(
                plan_sha256="1" * 64,
                selector_config_sha256="2" * 64,
                source_tree_sha256="3" * 64,
                order_block=0,
                profile_id="baseline-o0-fasm",
                case_order=CASE_ORDER,
                host_validity_sha256="4" * 64,
                marker_python_path="/usr/bin/python3.14",
                marker_python_pinned_fd_path="/proc/self/fd/70",
                marker_python_sha256="5" * 64,
                marker_script_path="/repo/haskell/tools/profile_workflow.py",
                marker_script_pinned_fd_path="/proc/self/fd/71",
                marker_script_sha256="6" * 64,
                marker_argv=[
                    "/usr/bin/env",
                    "-a",
                    "/usr/bin/python3.14",
                    "/proc/self/fd/70",
                    "/proc/self/fd/71",
                    "mark-measurement-entered",
                    "--qualification",
                    str(marker_path),
                ],
                started_at="2026-07-19T00:00:00.000000Z",
            )
            marker_path.write_bytes(
                helper.canonical_json_bytes(marker, trailing_newline=True)
            )
            before = helper.sha256_file(marker_path)
            result = helper.mark_profile_measurement_entered(marker_path)
            transitioned = helper.strict_json_load(marker_path)

            self.assertEqual(result["preRunSha256"], before)
            self.assertEqual(transitioned["state"], "MEASUREMENT")
            self.assertEqual(
                transitioned["preRunSha256"],
                before,
            )
            self.assertRegex(
                transitioned["measurementEnteredAt"],
                r"^\d{4}-\d{2}-\d{2}T",
            )
            with self.assertRaisesRegex(
                helper.WorkflowError,
                "PROFILE_MARKER_NOT_PRE_RUN",
            ):
                helper.mark_profile_measurement_entered(marker_path)

    def test_sealed_profile_marker_script_survives_source_path_swap(self) -> None:
        helper = load_helper()
        with tempfile.TemporaryDirectory() as temporary:
            temporary_root = Path(temporary)
            venv_root = temporary_root / "oracle-venv"
            venv.EnvBuilder(with_pip=False, symlinks=False).create(venv_root)
            python_source = venv_root / "bin/python"
            site_packages = venv_root / "lib/python3.12/site-packages"
            for package, version in (
                ("jsonschema", "4.26.0"),
                ("numpy", "2.5.1"),
            ):
                package_root = site_packages / package
                package_root.mkdir()
                (package_root / "__init__.py").write_text(
                    f'__version__ = "{version}"\n',
                    encoding="utf-8",
                )
                metadata_root = (
                    site_packages / f"{package}-{version}.dist-info"
                )
                metadata_root.mkdir()
                (metadata_root / "METADATA").write_text(
                    "Metadata-Version: 2.1\n"
                    f"Name: {package}\n"
                    f"Version: {version}\n",
                    encoding="utf-8",
                )
            script_source = temporary_root / "profile_workflow.py"
            script_source.write_bytes(HELPER_PATH.read_bytes())
            pinned_script = benchmark_helper.pin_regular_file(
                script_source,
                label="PROFILE_MARKER_TEST_SCRIPT",
                max_bytes=16 * 1024 * 1024,
            )
            python_descriptor = os.open(
                python_source,
                os.O_RDONLY | getattr(os, "O_CLOEXEC", 0),
            )
            python_fd_path = Path(f"/proc/self/fd/{python_descriptor}")
            marker_path = temporary_root / "measurement-state.json"
            marker_argv = [
                "/usr/bin/env",
                "-a",
                str(python_source),
                str(python_fd_path),
                str(pinned_script.fd_path),
                "mark-measurement-entered",
                "--qualification",
                str(marker_path),
            ]
            marker = helper.build_profile_marker(
                plan_sha256="1" * 64,
                selector_config_sha256="2" * 64,
                source_tree_sha256="3" * 64,
                order_block=0,
                profile_id="baseline-o0-fasm",
                case_order=CASE_ORDER,
                host_validity_sha256="4" * 64,
                marker_python_path=str(python_source),
                marker_python_pinned_fd_path=str(python_fd_path),
                marker_python_sha256=helper.sha256_file(python_source),
                marker_script_path=str(script_source),
                marker_script_pinned_fd_path=str(pinned_script.fd_path),
                marker_script_sha256=pinned_script.sha256,
                marker_argv=marker_argv,
                started_at="2026-07-19T00:00:00.000000Z",
            )
            marker_path.write_bytes(
                helper.canonical_json_bytes(marker, trailing_newline=True)
            )
            script_source.write_text(
                "raise SystemExit(97)\n",
                encoding="utf-8",
            )
            try:
                completed = subprocess.run(
                    marker_argv,
                    cwd=temporary_root,
                    env={
                        "LC_ALL": "C",
                        "PATH": "/usr/bin:/bin",
                        "S1_4X_BENCHMARK_PYTHON_BIN": str(python_source),
                        "S1_4X_BENCHMARK_PYTHON_SHA256": (
                            helper.sha256_file(python_source)
                        ),
                        "S1_4X_BENCHMARK_PYTHON_PINNED_FD_PATH": str(
                            python_fd_path
                        ),
                        "TZ": "UTC",
                    },
                    check=False,
                    capture_output=True,
                    text=True,
                    pass_fds=(
                        python_descriptor,
                        pinned_script.descriptor,
                    ),
                )
            finally:
                os.close(python_descriptor)
                os.close(pinned_script.descriptor)

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(
                helper.strict_json_load(marker_path)["state"],
                "MEASUREMENT",
            )

    def test_selector_uses_four_paired_blocks_and_all_twenty_eight_ratios(
        self,
    ) -> None:
        helper = load_helper()
        blocks = [
            {
                "orderBlock": index,
                "plannedProfileOrder": list(PROFILE_ORDER_BLOCKS[index]),
                "actualProfileOrder": list(PROFILE_ORDER_BLOCKS[index]),
                "ratios": {case_id: 0.95 for case_id in CASE_ORDER},
            }
            for index in range(4)
        ]
        selected = helper.select_profile_from_blocks(
            blocks,
            case_order=CASE_ORDER,
            profile_order_blocks=PROFILE_ORDER_BLOCKS,
        )
        self.assertEqual(selected["profileId"], "optimized-o2-fasm")
        self.assertEqual(selected["selectedBy"], "frozen-criterion-selector")
        self.assertEqual(len(selected["pairedRatios"]), 28)

        regressed = json.loads(json.dumps(blocks))
        regressed[0]["ratios"][CASE_ORDER[0]] = 1.051
        fallback = helper.select_profile_from_blocks(
            regressed,
            case_order=CASE_ORDER,
            profile_order_blocks=PROFILE_ORDER_BLOCKS,
        )
        self.assertEqual(fallback["profileId"], "baseline-o0-fasm")
        self.assertEqual(fallback["selectedBy"], "proven-fallback")

    def test_criterion_parser_rejects_missing_duplicate_and_nonfinite_cases(
        self,
    ) -> None:
        helper = load_helper()
        reports = [
            {
                "reportName": case_id,
                "reportAnalysis": {"anMean": {"estPoint": float(index + 1) / 10.0}},
            }
            for index, case_id in enumerate(CASE_ORDER)
        ]
        parsed = helper.parse_criterion_qualification_reports(
            reports,
            expected_case_order=CASE_ORDER,
        )
        self.assertEqual(tuple(parsed), CASE_ORDER)

        for invalid in (
            reports[:-1],
            [*reports, reports[0]],
            [
                *reports[:-1],
                {
                    "reportName": CASE_ORDER[-1],
                    "reportAnalysis": {"anMean": {"estPoint": "0.7"}},
                },
            ],
        ):
            with self.subTest(invalid=invalid[-1]):
                with self.assertRaises(helper.WorkflowError):
                    helper.parse_criterion_qualification_reports(
                        invalid,
                        expected_case_order=CASE_ORDER,
                    )

    def test_qualification_and_selector_wrappers_are_closed_interfaces(self) -> None:
        for path in (QUALIFICATION_WRAPPER, SELECTOR_WRAPPER):
            self.assertTrue(path.is_file(), f"missing wrapper: {path.name}")
        qualification = QUALIFICATION_WRAPPER.read_text(encoding="utf-8")
        selector = SELECTOR_WRAPPER.read_text(encoding="utf-8")
        for required in (
            "--plan",
            "--profiles",
            "baseline-o0-fasm,optimized-o2-fasm",
            "--enforce-order-plan",
            "--output-dir",
            "profile_workflow.py",
            "qualification",
        ):
            self.assertIn(required, qualification)
        for required in (
            "--materialize",
            "--check",
            "profile_workflow.py",
            "select-profile",
            "S1_4X_HASKELL_BASELINE_CORRECTNESS",
            "S1_4X_HASKELL_OPTIMIZED_CORRECTNESS",
            "S1_4X_HASKELL_QUALIFICATION_ARTIFACT",
        ):
            self.assertIn(required, selector)
        for source in (qualification, selector):
            self.assertNotIn("eval ", source)
            self.assertNotIn("bash -c", source)
            self.assertNotIn("manual-override", source)

    def test_qualification_marker_executes_only_inherited_fd_objects(
        self,
    ) -> None:
        qualification = QUALIFICATION_WRAPPER.read_text(encoding="utf-8")
        workflow = HELPER_PATH.read_text(encoding="utf-8")
        for suffix in ("_BIN", "_SHA256", "_PINNED_FD_PATH"):
            self.assertIn(
                f"${{S1_4X_BENCHMARK_PYTHON{suffix}:?",
                qualification,
            )
        self.assertIn(
            "s1_4x_exec_benchmark_python "
            '"$HASKELL_ROOT/tools/profile_workflow.py"',
            qualification,
        )
        self.assertNotIn(
            'exec /usr/bin/python3 "$HASKELL_ROOT/tools/profile_workflow.py"',
            qualification,
        )
        for token in (
            "pinned_executable_environment",
            "pin_regular_file",
            "markerPythonPinnedFdPath",
            "markerScriptPinnedFdPath",
            "pass_fds=",
        ):
            with self.subTest(token=token):
                self.assertIn(token, workflow)
        self.assertIn(
            '"S1_4X_BENCHMARK_MARKER_PYTHON": '
            "str(marker_python.fd_path)",
            workflow,
        )
        self.assertIn(
            '"S1_4X_BENCHMARK_MARKER_SCRIPT": '
            "str(marker_script.fd_path)",
            workflow,
        )

    def test_portable_qualification_witness_survives_owner_exit_and_rejects_forgery(
        self,
    ) -> None:
        helper = load_helper()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            python_source = root / "python"
            marker_source = root / "profile_workflow.py"
            python_source.write_bytes(b"accepted python bytes")
            python_source.chmod(0o755)
            marker_source.write_bytes(b"accepted marker source")
            read_pipe, write_pipe = os.pipe()
            child_pid = os.fork()
            if child_pid == 0:
                os.close(read_pipe)
                python_descriptor = -1
                script_descriptor = -1
                exit_code = 0
                record: dict[str, object]
                try:
                    python_descriptor = os.open(python_source, os.O_RDONLY)
                    script_descriptor = os.open(marker_source, os.O_RDONLY)
                    python_stat = os.fstat(python_descriptor)
                    script_stat = os.fstat(script_descriptor)
                    raw_argv = [
                        "/usr/bin/env",
                        "-a",
                        str(python_source),
                        f"/proc/self/fd/{python_descriptor}",
                        f"/proc/self/fd/{script_descriptor}",
                        "mark-measurement-entered",
                        "--qualification",
                        str(root / "measurement-state.json"),
                    ]
                    marker_path_id = (
                        "S1_4X_QUALIFICATION_MEASUREMENT_"
                        "BLOCK_1_BASELINE_O0_FASM"
                    )
                    witness = helper.build_qualification_command_witness(
                        owner_pid=os.getpid(),
                        owner_start_ticks=helper._process_start_ticks(
                            os.getpid()
                        ),
                        python_source_path=python_source,
                        python_source_sha256=helper.sha256_file(python_source),
                        python_descriptor=python_descriptor,
                        python_identity=helper._stat_identity(python_stat),
                        script_source_path=marker_source,
                        script_source_sha256=helper.sha256_file(marker_source),
                        script_descriptor=script_descriptor,
                        script_identity=helper._stat_identity(script_stat),
                        marker_argv=raw_argv,
                        marker_path_id=marker_path_id,
                    )
                    record = {"argv": raw_argv, "witness": witness}
                except BaseException as exc:
                    record = {"error": repr(exc)}
                    exit_code = 1
                payload = json.dumps(record, sort_keys=True).encode("utf-8")
                while payload:
                    payload = payload[os.write(write_pipe, payload) :]
                for descriptor in (script_descriptor, python_descriptor):
                    if descriptor >= 0:
                        os.close(descriptor)
                os.close(write_pipe)
                os._exit(exit_code)

            os.close(write_pipe)
            chunks: list[bytes] = []
            while chunk := os.read(read_pipe, 64 * 1024):
                chunks.append(chunk)
            os.close(read_pipe)
            _, wait_status = os.waitpid(child_pid, 0)
            record = json.loads(b"".join(chunks))
            self.assertTrue(
                os.WIFEXITED(wait_status)
                and os.WEXITSTATUS(wait_status) == 0,
                record,
            )
            witness = record["witness"]
            raw_argv = record["argv"]
            marker_path_id = (
                "S1_4X_QUALIFICATION_MEASUREMENT_"
                "BLOCK_1_BASELINE_O0_FASM"
            )
            normalized = witness["normalizedArgv"]
            self.assertNotIn("/proc/self/fd/", " ".join(normalized))
            self.assertNotIn(str(root), " ".join(normalized))
            helper.validate_qualification_command_witness(
                witness,
                marker_argv=raw_argv,
                marker_path_id=marker_path_id,
                python_source_path=python_source,
                python_source_sha256=helper.sha256_file(python_source),
                script_source_path=marker_source,
                script_source_sha256=helper.sha256_file(marker_source),
                require_owner_exit=True,
            )

            for path, replacement in (
                (
                    ("owner", "startTicks"),
                    witness["owner"]["startTicks"] + 1,
                ),
                (
                    ("objects", "python", "fdIdentity", "inode"),
                    witness["objects"]["python"]["fdIdentity"]["inode"] + 1,
                ),
                (
                    ("objects", "script", "sourceBytesSha256"),
                    "f" * 64,
                ),
                (
                    ("normalizedArgv", 0),
                    "FORGED_RUNTIME",
                ),
            ):
                forged = json.loads(json.dumps(witness))
                target = forged
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = replacement
                with self.subTest(path=path):
                    with self.assertRaises(helper.WorkflowError):
                        helper.validate_qualification_command_witness(
                            forged,
                            marker_argv=raw_argv,
                            marker_path_id=marker_path_id,
                            python_source_path=python_source,
                            python_source_sha256=helper.sha256_file(
                                python_source
                            ),
                            script_source_path=marker_source,
                            script_source_sha256=helper.sha256_file(
                                marker_source
                            ),
                            require_owner_exit=False,
                        )

    def test_selector_uses_portable_witness_not_dead_proc_fd_reopen(self) -> None:
        source = inspect.getsource(load_helper()._validate_qualification_artifact)

        self.assertIn("validate_qualification_command_witness", source)
        self.assertIn("require_owner_exit=True", source)
        self.assertNotIn(
            "_pinned_fd_number(marker.get(\"pythonPinnedFdPath\"))",
            source,
        )
        self.assertNotIn(
            "_pinned_fd_number(marker.get(\"scriptPinnedFdPath\"))",
            source,
        )

    def test_qualification_consumes_shared_materialized_large_fixture_root(
        self,
    ) -> None:
        helper = load_helper()
        workflow = HELPER_PATH.read_text(encoding="utf-8")
        wrapper = QUALIFICATION_WRAPPER.read_text(encoding="utf-8")
        for source in (workflow, wrapper):
            self.assertIn("S1_4X_LARGE_FIXTURE_ROOT", source)
            self.assertNotIn("S1_4X_BENCHMARK_FIXTURE_ROOT", source)
        self.assertIn(
            "large_fixture_root = _absolute_existing_directory(",
            workflow,
        )
        self.assertIn(
            '"S1_4X_LARGE_FIXTURE_ROOT": str(large_fixture_root),',
            workflow,
        )
        self.assertIn(
            'LARGE_FIXTURE_ROOT="${S1_4X_LARGE_FIXTURE_ROOT:?',
            wrapper,
        )
        self.assertNotIn(
            'numeric_root / "contract/fixtures"',
            inspect.getsource(helper._qualification),
        )
        for tracked_fixture_consumer in (
            helper._correctness,
            helper._validate_correctness_receipt,
            helper._replay_compatibility_success,
            helper._oci_correctness,
        ):
            with self.subTest(
                tracked_fixture_consumer=tracked_fixture_consumer.__name__,
            ):
                self.assertIn(
                    'numeric_root / "contract/fixtures"',
                    inspect.getsource(tracked_fixture_consumer),
                )

    def test_final_profile_binds_selected_correctness_and_qualification(self) -> None:
        helper = load_helper()
        selection = {
            "profileId": "optimized-o2-fasm",
            "selectedBy": "frozen-criterion-selector",
            "pairedRatios": [0.95] * 28,
            "perCaseMaxima": {case_id: 0.95 for case_id in CASE_ORDER},
            "aggregateRatio": 0.95,
            "improvingOuterRepetitions": 4,
        }
        document = helper.build_final_profile_document(
            selection=selection,
            source_tree_sha256="1" * 64,
            full_correctness_sha256="2" * 64,
            qualification_plan_sha256="3" * 64,
            qualification_artifact_sha256="4" * 64,
            selector_config_sha256="5" * 64,
            compiler_sha256="6" * 64,
        )
        self.assertEqual(document["profileId"], "optimized-o2-fasm")
        self.assertEqual(document["ghcOptions"], ["-O2", "-fasm"])
        self.assertEqual(document["fullCorrectnessSha256"], "2" * 64)
        self.assertEqual(document["qualificationArtifactSha256"], "4" * 64)
        self.assertNotIn("manualOverride", document)


if __name__ == "__main__":
    unittest.main()
