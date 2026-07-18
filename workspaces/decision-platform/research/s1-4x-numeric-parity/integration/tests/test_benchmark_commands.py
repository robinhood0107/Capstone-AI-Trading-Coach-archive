"""Frozen rotated benchmark command manifest 생성기의 회귀 테스트."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

INTEGRATION = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(INTEGRATION))

from benchmark_commands import (  # noqa: E402
    BOUNDARY_IDS,
    CommandManifestError,
    build_manifest,
    inspect_executable_identity,
    validate_manifest,
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
            "hostValidatorCommand": [executable, "{host_report}"],
            "boundaryCommands": {
                boundary: [executable, "{qualification}"]
                for boundary in BOUNDARY_IDS
            },
            "allowedExecutables": {
                "hostValidator": identity,
                "boundaries": {
                    boundary: identity for boundary in BOUNDARY_IDS
                },
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
            host_validator_command=[
                str(executable),
                "host.py",
                "--output",
                "{host_report}",
            ],
            boundary_commands={
                boundary: [
                    str(executable),
                    f"{boundary}.py",
                    "--qualification",
                    "{qualification}",
                ]
                for boundary in BOUNDARY_IDS
            },
            allowed_executables={
                "hostValidator": identity,
                "boundaries": {boundary: identity for boundary in BOUNDARY_IDS},
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
                },
            )

        commands["scala"] = [str(executable), "{qualification}", "{qualification}"]
        with self.assertRaisesRegex(
            CommandManifestError,
            "QUALIFICATION_PLACEHOLDER_COUNT",
        ):
            build_manifest(
                benchmark_subject_commit="a" * 40,
                candidate_source_commit="a" * 40,
                host_validator_command=[str(executable), "{host_report}"],
                boundary_commands=commands,
                allowed_executables={
                    "hostValidator": identity,
                    "boundaries": {boundary: identity for boundary in BOUNDARY_IDS},
                },
            )

    def test_manifest_write_is_exclusive_canonical_and_digest_bound(self) -> None:
        executable = Path(sys.executable).resolve()
        digest = hashlib.sha256(executable.read_bytes()).hexdigest()
        identity = {"path": str(executable), "sha256": digest}
        manifest = build_manifest(
            benchmark_subject_commit="b" * 40,
            candidate_source_commit="b" * 40,
            host_validator_command=[str(executable), "{host_report}"],
            boundary_commands={
                boundary: [str(executable), "{qualification}"]
                for boundary in BOUNDARY_IDS
            },
            allowed_executables={
                "hostValidator": identity,
                "boundaries": {boundary: identity for boundary in BOUNDARY_IDS},
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

            with patch("benchmark_commands.os.open", side_effect=tracking_open), patch(
                "benchmark_commands.os.read",
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
