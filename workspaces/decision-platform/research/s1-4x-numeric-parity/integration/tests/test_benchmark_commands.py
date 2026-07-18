"""Frozen rotated benchmark command manifest 생성기의 회귀 테스트."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import cast
from unittest import TestCase
from unittest.mock import patch

INTEGRATION = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(INTEGRATION))

from benchmark_commands import (  # noqa: E402
    BOUNDARY_IDS,
    CommandManifestError,
    boundary_command_template,
    build_manifest,
    host_command_template,
    inspect_executable_identity,
    validate_manifest,
    validate_manifest_file,
    write_manifest_exclusive,
)
from prepare_benchmark_commands import _identity  # noqa: E402


class BenchmarkCommandManifestTests(TestCase):
    def _manifest_for_identity(self, identity: dict[str, str]) -> dict[str, object]:
        executable = identity["path"]
        return {
            "schemaVersion": "s1.4x-benchmark-command-manifest-v2",
            "benchmarkSubjectCommit": "a" * 40,
            "candidateSourceCommit": "a" * 40,
            "hostValidatorCommand": [
                executable,
                "--output",
                "{host_report}",
                "--allowed-process-root-pid",
                "{allowed_process_root_pid}",
            ],
            "boundaryCommands": {
                boundary: [
                    executable,
                    "--plan",
                    "{plan}",
                    "--block-dir",
                    "{block_dir}",
                    "--qualification",
                    "{qualification}",
                    "--boundary",
                    boundary,
                    "--selector",
                    "{selector_id}",
                    "--family",
                    "{family_id}",
                    "--rotation",
                    "{rotation_id}",
                    "--outer-repetition",
                    "{outer_repetition}",
                    "--run-id",
                    "{run_id}",
                    "--benchmark-subject-commit",
                    "{benchmark_subject_commit}",
                ]
                for boundary in BOUNDARY_IDS
            },
            "allowedExecutables": {
                "hostValidator": identity,
                "boundaries": {
                    boundary: identity for boundary in BOUNDARY_IDS
                },
                "runtimeDependencies": {"uv": identity},
            },
        }

    def test_manifest_covers_all_boundaries_and_binds_exact_commit(self) -> None:
        executable = Path(sys.executable).resolve()
        identity = {
            "path": str(executable),
            "sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
        }
        commit = "a" * 40
        manifest = build_manifest(
            benchmark_subject_commit=commit,
            candidate_source_commit=commit,
            host_validator_command=host_command_template(str(executable)),
            boundary_commands={
                boundary: boundary_command_template(str(executable), boundary)
                for boundary in BOUNDARY_IDS
            },
            allowed_executables={
                "hostValidator": identity,
                "boundaries": {boundary: identity for boundary in BOUNDARY_IDS},
                "runtimeDependencies": {"uv": identity},
            },
        )
        self.assertEqual(validate_manifest(manifest)["benchmarkSubjectCommit"], commit)
        self.assertEqual(set(manifest["boundaryCommands"]), set(BOUNDARY_IDS))

    def test_manifest_rejects_shell_and_unbound_placeholder_shapes(self) -> None:
        executable = Path(sys.executable).resolve()
        identity = {
            "path": str(executable),
            "sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
        }
        commands = {
            boundary: [str(executable), "{qualification}"]
            for boundary in BOUNDARY_IDS
        }
        with self.assertRaisesRegex(CommandManifestError, "COMMAND_EXECUTABLE_MISMATCH"):
            build_manifest(
                benchmark_subject_commit="a" * 40,
                candidate_source_commit="a" * 40,
                host_validator_command=["/bin/sh", "-c", "{host_report}"],
                boundary_commands=commands,
                allowed_executables={
                    "hostValidator": identity,
                    "boundaries": {boundary: identity for boundary in BOUNDARY_IDS},
                    "runtimeDependencies": {"uv": identity},
                },
            )

    def test_manifest_rejects_escaped_placeholder_and_extra_argv(self) -> None:
        executable = Path(sys.executable).resolve()
        identity = {
            "path": str(executable),
            "sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
        }
        manifest = self._manifest_for_identity(identity)
        manifest["hostValidatorCommand"] = [
            str(executable),
            "--output",
            "{host_report}",
            "--allowed-process-root-pid",
            "{allowed_process_root_pid}",
        ]
        manifest["boundaryCommands"] = {
            boundary: [
                str(executable),
                "--plan",
                "{plan}",
                "--block-dir",
                "{block_dir}",
                "--qualification",
                "{qualification}",
                "--boundary",
                boundary,
                "--selector",
                "{selector_id}",
                "--family",
                "{family_id}",
                "--rotation",
                "{rotation_id}",
                "--outer-repetition",
                "{outer_repetition}",
                "--run-id",
                "{run_id}",
                "--benchmark-subject-commit",
                "{benchmark_subject_commit}",
            ]
            for boundary in BOUNDARY_IDS
        }
        escaped = json.loads(json.dumps(manifest))
        escaped["boundaryCommands"]["scala"][6] = "{{qualification}}"
        with self.assertRaisesRegex(
            CommandManifestError,
            "BOUNDARY_COMMAND_TEMPLATE_MISMATCH",
        ):
            validate_manifest(escaped)

        extra = json.loads(json.dumps(manifest))
        extra["boundaryCommands"]["haskell"].extend(["--override", "forged"])
        with self.assertRaisesRegex(
            CommandManifestError,
            "BOUNDARY_COMMAND_TEMPLATE_MISMATCH",
        ):
            validate_manifest(extra)

    def test_manifest_file_hash_and_parse_share_one_snapshot(self) -> None:
        executable = Path(sys.executable).resolve()
        identity = {
            "path": str(executable),
            "sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
        }
        manifest = self._manifest_for_identity(identity)
        manifest["hostValidatorCommand"] = [
            str(executable),
            "--output",
            "{host_report}",
            "--allowed-process-root-pid",
            "{allowed_process_root_pid}",
        ]
        manifest["boundaryCommands"] = {
            boundary: [
                str(executable),
                "--plan",
                "{plan}",
                "--block-dir",
                "{block_dir}",
                "--qualification",
                "{qualification}",
                "--boundary",
                boundary,
                "--selector",
                "{selector_id}",
                "--family",
                "{family_id}",
                "--rotation",
                "{rotation_id}",
                "--outer-repetition",
                "{outer_repetition}",
                "--run-id",
                "{run_id}",
                "--benchmark-subject-commit",
                "{benchmark_subject_commit}",
            ]
            for boundary in BOUNDARY_IDS
        }
        forged = json.loads(json.dumps(manifest))
        forged["boundaryCommands"]["scala"].extend(["--override", "forged"])

        with tempfile.TemporaryDirectory(dir="/tmp") as directory:
            path = Path(directory) / "commands.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            expected = hashlib.sha256(path.read_bytes()).hexdigest()
            replacement = path.with_name("replacement.json")
            replacement.write_text(json.dumps(forged), encoding="utf-8")
            real_hash = hashlib.sha256
            swapped = False

            def replace_after_hash(candidate: Path) -> str:
                nonlocal swapped
                digest = real_hash(candidate.read_bytes()).hexdigest()
                if not swapped:
                    replacement.replace(candidate)
                    swapped = True
                return digest

            with patch(
                "benchmark_commands._strict_json_load",
                side_effect=replace_after_hash,
            ):
                validated = validate_manifest_file(path, expected)

            self.assertEqual(validated, manifest)
            self.assertFalse(swapped)

    def test_official_benchmark_wrappers_require_fd_bound_uv(self) -> None:
        numeric_root = INTEGRATION.parent
        wrappers = [
            numeric_root / "integration/tools/run-host-validator.sh",
            numeric_root / "integration/tools/run-python-benchmark-block.sh",
        ]
        for wrapper in wrappers:
            with self.subTest(wrapper=wrapper.name):
                source = wrapper.read_text(encoding="utf-8")
                self.assertTrue(source.startswith("#!/usr/bin/bash\n"))
                self.assertIn("/usr/bin/git", source)
                self.assertNotRegex(source, r"/home/[^/\s]+/\.local/bin/uv")
                self.assertNotIn("command -v", source)
                self.assertIn("S1_4X_UV_BIN", source)
                self.assertIn("/proc/self/fd/", source)

    def test_prepare_command_does_not_embed_a_local_user_home(self) -> None:
        source = (INTEGRATION / "prepare_benchmark_commands.py").read_text(
            encoding="utf-8"
        )
        self.assertIsNone(re.search(r"/home/[^/\s]+", source))
        self.assertIn('os.environ.get("HOME")', source)

    def test_manifest_requires_exact_uv_runtime_dependency(self) -> None:
        executable = Path(sys.executable).resolve()
        identity = {
            "path": str(executable),
            "sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
        }
        manifest = self._manifest_for_identity(identity)
        runtime = cast(
            dict[str, dict[str, str]],
            cast(dict[str, object], manifest["allowedExecutables"])[
                "runtimeDependencies"
            ],
        )
        self.assertEqual(validate_manifest(manifest), manifest)

        del runtime["uv"]
        with self.assertRaisesRegex(
            CommandManifestError,
            "MANIFEST_COMMANDS_INVALID",
        ):
            validate_manifest(manifest)

    def test_manifest_rejects_duplicate_qualification_placeholder(self) -> None:
        executable = Path(sys.executable).resolve()
        identity = {
            "path": str(executable),
            "sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
        }
        manifest = self._manifest_for_identity(identity)
        commands = cast(dict[str, list[str]], manifest["boundaryCommands"])
        commands["scala"] = [str(executable), "{qualification}", "{qualification}"]
        with self.assertRaisesRegex(
            CommandManifestError,
            "BOUNDARY_COMMAND_TEMPLATE_MISMATCH",
        ):
            build_manifest(
                benchmark_subject_commit="a" * 40,
                candidate_source_commit="a" * 40,
                host_validator_command=host_command_template(str(executable)),
                boundary_commands=commands,
                allowed_executables={
                    "hostValidator": identity,
                    "boundaries": {boundary: identity for boundary in BOUNDARY_IDS},
                    "runtimeDependencies": {"uv": identity},
                },
            )

    def test_manifest_write_is_exclusive_canonical_and_digest_bound(self) -> None:
        executable = Path(sys.executable).resolve()
        digest = hashlib.sha256(executable.read_bytes()).hexdigest()
        identity = {"path": str(executable), "sha256": digest}
        manifest = build_manifest(
            benchmark_subject_commit="b" * 40,
            candidate_source_commit="b" * 40,
            host_validator_command=host_command_template(str(executable)),
            boundary_commands={
                boundary: boundary_command_template(str(executable), boundary)
                for boundary in BOUNDARY_IDS
            },
            allowed_executables={
                "hostValidator": identity,
                "boundaries": {boundary: identity for boundary in BOUNDARY_IDS},
                "runtimeDependencies": {"uv": identity},
            },
        )
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "commands.json"
            sidecar = Path(directory) / "commands.sha256"
            written = write_manifest_exclusive(output, sidecar, manifest)
            self.assertEqual(
                output.read_bytes(),
                json.dumps(
                    manifest,
                    allow_nan=False,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode("utf-8"),
            )
            self.assertEqual(sidecar.read_text(encoding="ascii"), f"{written}  commands.json\n")
            with self.assertRaisesRegex(CommandManifestError, "OUTPUT_ALREADY_EXISTS"):
                write_manifest_exclusive(output, sidecar, manifest)

    def test_manifest_rejects_missing_directory_symlink_and_non_executable_identity(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as directory:
            root = Path(directory)
            missing = root / "missing"
            cases = [
                (
                    {
                        "path": str(missing),
                        "sha256": "1" * 64,
                    },
                    "COMMAND_EXECUTABLE_UNAVAILABLE",
                ),
                (
                    {
                        "path": str(root),
                        "sha256": "2" * 64,
                    },
                    "COMMAND_EXECUTABLE_NOT_REGULAR",
                ),
            ]
            regular = root / "wrapper"
            regular.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            os.chmod(regular, 0o600)
            self.assertEqual(regular.stat().st_mode & 0o111, 0)
            self.assertFalse(os.access(regular, os.X_OK))
            regular_digest = hashlib.sha256(regular.read_bytes()).hexdigest()
            cases.append(
                (
                    {
                        "path": str(regular),
                        "sha256": regular_digest,
                    },
                    "COMMAND_EXECUTABLE_NOT_EXECUTABLE",
                )
            )
            target = root / "target"
            target.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            os.chmod(target, 0o700)
            link = root / "wrapper-link"
            link.symlink_to(target)
            cases.append(
                (
                    {
                        "path": str(link),
                        "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                    },
                    "COMMAND_EXECUTABLE_NOT_REGULAR",
                )
            )

            for identity, expected_error in cases:
                with self.subTest(
                    expected_error=expected_error
                ), self.assertRaisesRegex(
                    CommandManifestError,
                    expected_error,
                ):
                    validate_manifest(self._manifest_for_identity(identity))

    def test_manifest_and_prepare_reject_intermediate_symlink_component(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as directory:
            root = Path(directory)
            real_parent = root / "real-parent"
            real_parent.mkdir()
            wrapper = real_parent / "wrapper"
            wrapper.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            os.chmod(wrapper, 0o700)
            linked_parent = root / "linked-parent"
            linked_parent.symlink_to(real_parent, target_is_directory=True)
            supplied = linked_parent / "wrapper"
            identity = {
                "path": str(supplied),
                "sha256": hashlib.sha256(wrapper.read_bytes()).hexdigest(),
            }

            with self.assertRaisesRegex(
                CommandManifestError,
                "COMMAND_EXECUTABLE_SYMLINK_COMPONENT",
            ):
                validate_manifest(self._manifest_for_identity(identity))
            with self.assertRaisesRegex(
                ValueError,
                "COMMAND_EXECUTABLE_SYMLINK_COMPONENT",
            ):
                _identity(supplied)

    def test_manifest_and_prepare_require_effective_current_user_execute_access(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as directory:
            wrapper = Path(directory) / "wrapper"
            wrapper.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            os.chmod(wrapper, 0o410)
            self.assertEqual(wrapper.stat().st_uid, os.geteuid())
            self.assertFalse(os.access(wrapper, os.X_OK, effective_ids=True))
            identity = {
                "path": str(wrapper),
                "sha256": hashlib.sha256(wrapper.read_bytes()).hexdigest(),
            }

            with self.assertRaisesRegex(
                CommandManifestError,
                "COMMAND_EXECUTABLE_NOT_EXECUTABLE",
            ):
                validate_manifest(self._manifest_for_identity(identity))
            with self.assertRaisesRegex(ValueError, "COMMAND_EXECUTABLE_NOT_EXECUTABLE"):
                _identity(wrapper)

    def test_identity_read_failure_closes_every_owned_descriptor(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as directory:
            wrapper = Path(directory) / "wrapper"
            wrapper.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            os.chmod(wrapper, 0o700)
            identity = {
                "path": str(wrapper),
                "sha256": hashlib.sha256(wrapper.read_bytes()).hexdigest(),
            }
            opened: list[int] = []
            real_open = os.open

            def tracking_open(*args: object, **kwargs: object) -> int:
                descriptor = real_open(*args, **kwargs)  # type: ignore[arg-type]
                opened.append(descriptor)
                return descriptor

            with patch("executable_identity.os.open", side_effect=tracking_open), patch(
                "executable_identity.os.read",
                side_effect=OSError("forced read failure"),
            ), self.assertRaisesRegex(
                CommandManifestError,
                "COMMAND_EXECUTABLE_CHANGED_DURING_VALIDATION",
            ):
                inspect_executable_identity(identity, role="test")

            self.assertGreaterEqual(len(opened), 2)
            for descriptor in opened:
                with self.assertRaises(OSError):
                    os.fstat(descriptor)
