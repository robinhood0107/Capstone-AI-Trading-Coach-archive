#!/usr/bin/env python3
"""Exact wrapper identities로 frozen 6-boundary benchmark command manifest를 만든다."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

from benchmark_commands import (
    BOUNDARY_IDS,
    RUNTIME_DEPENDENCY_ROLES_BY_BOUNDARY,
    RUNTIME_EVIDENCE_ROLES_BY_BOUNDARY,
    boundary_command_template,
    build_manifest,
    host_command_template,
    write_manifest_exclusive,
)
from executable_identity import (
    inspect_executable_identity,
    inspect_executable_path,
    inspect_regular_file_path,
)


def _identity(path: Path) -> dict[str, str]:
    inspected = inspect_executable_path(path, role="prepare")
    return {"path": inspected.path, "sha256": inspected.sha256}


def _evidence_identity(path: Path) -> dict[str, str]:
    inspected = inspect_regular_file_path(path, role="prepareEvidence")
    return {
        "path": str(path),
        "sha256": inspected.sha256,
    }


def _parse_role_paths(
    values: Sequence[str],
    *,
    expected_roles: set[str],
    label: str,
) -> dict[str, Path]:
    parsed: dict[str, Path] = {}
    for value in values:
        role, separator, raw_path = value.partition("=")
        path = Path(raw_path)
        if (
            separator != "="
            or role not in expected_roles
            or role in parsed
            or not raw_path
            or not path.is_absolute()
        ):
            raise ValueError(f"{label} role/path is invalid: {value}")
        parsed[role] = path
    if set(parsed) != expected_roles:
        missing = sorted(expected_roles - set(parsed))
        extra = sorted(set(parsed) - expected_roles)
        raise ValueError(
            f"{label} role set mismatch: missing={missing}, extra={extra}"
        )
    return parsed


def _assert_distinct_role_paths(
    executable_paths: dict[str, Path],
    evidence_paths: dict[str, Path],
) -> None:
    """서로 다른 semantic role이 같은 inode를 공유해 검증을 우회하지 못하게 한다."""

    seen: dict[tuple[int, int], str] = {}
    for kind, paths in (
        ("executable", executable_paths),
        ("evidence", evidence_paths),
    ):
        for role, path in paths.items():
            try:
                metadata = path.stat(follow_symlinks=False)
            except OSError as exc:
                raise ValueError(
                    f"runtime role path unavailable: {kind}:{role}"
                ) from exc
            inode = (metadata.st_dev, metadata.st_ino)
            current = f"{kind}:{role}"
            previous = seen.get(inode)
            if previous is not None:
                raise ValueError(
                    f"runtime role file alias: {previous}={current}"
                )
            seen[inode] = current


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--benchmark-subject-commit", required=True)
    parser.add_argument("--host-wrapper", type=Path, required=True)
    parser.add_argument("--python-wrapper", type=Path, required=True)
    parser.add_argument("--scala-wrapper", type=Path, required=True)
    parser.add_argument("--haskell-wrapper", type=Path, required=True)
    parser.add_argument(
        "--runtime-executable",
        action="append",
        default=[],
        metavar="ROLE=/ABSOLUTE/PATH",
    )
    parser.add_argument(
        "--runtime-evidence",
        action="append",
        default=[],
        metavar="ROLE=/ABSOLUTE/PATH",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sidecar", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        repo = arguments.repo_root.resolve(strict=True)
        home_value = os.environ.get("HOME")
        if not home_value or not Path(home_value).is_absolute():
            raise ValueError("HOME must be an absolute path")
        home = Path(home_value).resolve(strict=True)
        if not home.is_dir():
            raise ValueError("HOME must resolve to a directory")
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
                "HOME": str(home),
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
        wrapper_identities = {
            "host": _identity(arguments.host_wrapper),
            "python": _identity(arguments.python_wrapper),
            "scala": _identity(arguments.scala_wrapper),
            "haskell": _identity(arguments.haskell_wrapper),
        }
        executable_roles = {
            role
            for roles in RUNTIME_DEPENDENCY_ROLES_BY_BOUNDARY.values()
            for role in roles
        }
        evidence_roles = {
            role
            for roles in RUNTIME_EVIDENCE_ROLES_BY_BOUNDARY.values()
            for role in roles
        }
        executable_paths = _parse_role_paths(
            arguments.runtime_executable,
            expected_roles=executable_roles,
            label="runtime executable",
        )
        evidence_paths = _parse_role_paths(
            arguments.runtime_evidence,
            expected_roles=evidence_roles,
            label="runtime evidence",
        )
        _assert_distinct_role_paths(executable_paths, evidence_paths)
        runtime_identities = {
            role: _identity(path)
            for role, path in executable_paths.items()
        }
        evidence_identities = {
            role: _evidence_identity(path)
            for role, path in evidence_paths.items()
        }
        boundary_identity = {
            boundary: (
                wrapper_identities["python"]
                if boundary.startswith("python-")
                else wrapper_identities[boundary]
            )
            for boundary in BOUNDARY_IDS
        }
        manifest = build_manifest(
            benchmark_subject_commit=subject,
            candidate_source_commit=subject,
            host_validator_command=host_command_template(
                wrapper_identities["host"]["path"]
            ),
            boundary_commands={
                boundary: boundary_command_template(
                    boundary_identity[boundary]["path"],
                    boundary,
                )
                for boundary in BOUNDARY_IDS
            },
            allowed_executables={
                "hostValidator": wrapper_identities["host"],
                "boundaries": boundary_identity,
                "runtimeDependenciesByBoundary": {
                    boundary: {
                        role: runtime_identities[role]
                        for role in roles
                    }
                    for boundary, roles
                    in RUNTIME_DEPENDENCY_ROLES_BY_BOUNDARY.items()
                },
            },
            allowed_evidence_by_boundary={
                boundary: {
                    role: evidence_identities[role]
                    for role in roles
                }
                for boundary, roles
                in RUNTIME_EVIDENCE_ROLES_BY_BOUNDARY.items()
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
