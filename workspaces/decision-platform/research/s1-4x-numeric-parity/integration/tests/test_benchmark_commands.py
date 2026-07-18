"""Frozen rotated benchmark command manifest 생성기의 회귀 테스트."""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from pathlib import Path
from unittest import TestCase

INTEGRATION = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(INTEGRATION))

from benchmark_commands import (  # noqa: E402
    BOUNDARY_IDS,
    CommandManifestError,
    build_manifest,
    validate_manifest,
    write_manifest_exclusive,
)


class BenchmarkCommandManifestTests(TestCase):
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
