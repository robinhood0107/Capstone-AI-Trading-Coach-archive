#!/usr/bin/env python3
"""Exact wrapper identities로 frozen 6-boundary benchmark command manifest를 만든다."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

from benchmark_commands import (
    BOUNDARY_IDS,
    build_manifest,
    inspect_executable_path,
    write_manifest_exclusive,
)


def _identity(path: Path) -> dict[str, str]:
    inspected = inspect_executable_path(path, role="prepare")
    return {"path": inspected.path, "sha256": inspected.sha256}


def _boundary_command(path: str, boundary: str) -> list[str]:
    return [
        path,
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


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--benchmark-subject-commit", required=True)
    parser.add_argument("--host-wrapper", type=Path, required=True)
    parser.add_argument("--python-wrapper", type=Path, required=True)
    parser.add_argument("--scala-wrapper", type=Path, required=True)
    parser.add_argument("--haskell-wrapper", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sidecar", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        repo = arguments.repo_root.resolve(strict=True)
        completed = subprocess.run(
            ["git", "rev-parse", "--verify", "HEAD"],
            cwd=repo,
            capture_output=True,
            check=False,
            text=True,
            timeout=10,
        )
        subject = arguments.benchmark_subject_commit
        if completed.returncode != 0 or completed.stdout.strip() != subject:
            raise ValueError("benchmark subject does not equal current HEAD")
        identities = {
            "host": _identity(arguments.host_wrapper),
            "python": _identity(arguments.python_wrapper),
            "scala": _identity(arguments.scala_wrapper),
            "haskell": _identity(arguments.haskell_wrapper),
        }
        boundary_identity = {
            boundary: (
                identities["python"]
                if boundary.startswith("python-")
                else identities[boundary]
            )
            for boundary in BOUNDARY_IDS
        }
        manifest = build_manifest(
            benchmark_subject_commit=subject,
            candidate_source_commit=subject,
            host_validator_command=[
                identities["host"]["path"],
                "--output",
                "{host_report}",
                "--allowed-process-root-pid",
                "{allowed_process_root_pid}",
            ],
            boundary_commands={
                boundary: _boundary_command(
                    boundary_identity[boundary]["path"],
                    boundary,
                )
                for boundary in BOUNDARY_IDS
            },
            allowed_executables={
                "hostValidator": identities["host"],
                "boundaries": boundary_identity,
            },
        )
        digest = write_manifest_exclusive(
            arguments.output.resolve(),
            arguments.sidecar.resolve(),
            manifest,
        )
        print(
            json.dumps(
                {
                    "schemaVersion": manifest["schemaVersion"],
                    "boundaryCount": len(manifest["boundaryCommands"]),
                    "sha256": digest,
                    "status": "PASS",
                },
                sort_keys=True,
            )
        )
    except (OSError, ValueError) as exc:
        print(f"PREPARE_BENCHMARK_COMMANDS_FAIL:{exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
