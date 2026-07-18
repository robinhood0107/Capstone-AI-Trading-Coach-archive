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
    boundary_command_template,
    build_manifest,
    host_command_template,
    write_manifest_exclusive,
)
from executable_identity import inspect_executable_identity, inspect_executable_path


def _identity(path: Path) -> dict[str, str]:
    inspected = inspect_executable_path(path, role="prepare")
    return {"path": inspected.path, "sha256": inspected.sha256}


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
        git = inspect_executable_identity(
            {
                "path": "/usr/bin/git",
                "sha256": (
                    "5516c9f362c29376ab9a499a33082f9f611941d8c75930c880e30ad109e39c9a"
                ),
            },
            role="prepareGit",
        ).path
        completed = subprocess.run(
            [
                git,
                "-c",
                "core.fsmonitor=false",
                "rev-parse",
                "--verify",
                "HEAD",
            ],
            cwd=repo,
            capture_output=True,
            check=False,
            text=True,
            env={
                "HOME": "/home/pjjpj",
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
                "PATH": "/usr/bin:/bin",
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": "/dev/null",
                "GIT_OPTIONAL_LOCKS": "0",
                "GIT_TERMINAL_PROMPT": "0",
            },
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
            host_validator_command=host_command_template(
                identities["host"]["path"]
            ),
            boundary_commands={
                boundary: boundary_command_template(
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
