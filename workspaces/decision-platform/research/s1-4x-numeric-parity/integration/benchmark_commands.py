#!/usr/bin/env python3
"""Frozen 87-block runner용 shell-free command manifest를 생성·검증한다."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from string import Formatter
from typing import Any

BENCHMARKS_DIRECTORY = Path(__file__).resolve().parents[1] / "benchmarks"
if str(BENCHMARKS_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(BENCHMARKS_DIRECTORY))

from executable_identity import (  # noqa: E402
    ExecutableIdentityError as CommandManifestError,
)
from executable_identity import (  # noqa: E402
    inspect_executable_identity,
    inspect_regular_file_path,
)

__all__ = [
    "BOUNDARY_IDS",
    "CommandManifestError",
    "RUNTIME_DEPENDENCY_ROLES_BY_BOUNDARY",
    "RUNTIME_EVIDENCE_ROLES_BY_BOUNDARY",
    "boundary_command_template",
    "build_manifest",
    "host_command_template",
    "inspect_executable_identity",
    "validate_manifest",
    "validate_manifest_file",
    "write_manifest_exclusive",
]

BOUNDARY_IDS = (
    "python-numpy-s1-4",
    "python-numpy-s1-4r",
    "python-jax-eager-s1-4r",
    "python-jax-jit-s1-4r",
    "scala",
    "haskell",
)
RUNTIME_DEPENDENCY_ROLES_BY_BOUNDARY = {
    "hostValidator": ("uv",),
    "python-numpy-s1-4": ("uv", "benchmarkPython"),
    "python-numpy-s1-4r": ("uv", "benchmarkPython"),
    "python-jax-eager-s1-4r": ("uv", "benchmarkPython"),
    "python-jax-jit-s1-4r": ("uv", "benchmarkPython"),
    "scala": (
        "benchmarkPython",
        "scalaCli",
        "java",
        "scalafix",
        "scalafmt",
    ),
    "haskell": (
        "benchmarkPython",
        "ghcup",
        "stack",
        "authoritativeGhc",
        "compatibilityGhc",
        "hlint",
        "stylishHaskell",
    ),
}
RUNTIME_EVIDENCE_ROLES_BY_BOUNDARY = {
    "hostValidator": (),
    "python-numpy-s1-4": (),
    "python-numpy-s1-4r": (),
    "python-jax-eager-s1-4r": (),
    "python-jax-jit-s1-4r": (),
    "scala": (
        "scalafmtArchive",
        "selectedProfileResult",
        "profileQualificationResult",
        "jvmAllowlistResult",
        "correctnessA",
        "correctnessB",
        "correctnessC",
    ),
    "haskell": (
        "baselineCorrectness",
        "optimizedCorrectness",
        "profileQualification",
    ),
}
SHA256 = re.compile(r"^[0-9a-f]{64}$")
COMMIT = re.compile(r"^[0-9a-f]{40}$")


def _strict_json_load_bytes(payload: bytes) -> Any:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise CommandManifestError(f"DUPLICATE_JSON_KEY:{key}")
            result[key] = value
        return result

    def reject_constant(token: str) -> Any:
        raise CommandManifestError(f"NON_FINITE_JSON:{token}")

    try:
        return json.loads(
            payload.decode("utf-8", errors="strict"),
            object_pairs_hook=unique,
            parse_constant=reject_constant,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CommandManifestError("INVALID_JSON_FILE") from exc


def _strict_json_load(path: Path) -> Any:
    try:
        snapshot = inspect_regular_file_path(path, role="commandManifestInput")
    except ValueError as exc:
        raise CommandManifestError(str(exc)) from exc
    return _strict_json_load_bytes(snapshot.payload)


def _validate_identity(
    command: Sequence[str],
    identity: Any,
    *,
    role: str,
) -> None:
    if (
        not isinstance(identity, dict)
        or set(identity) != {"path", "sha256"}
        or not isinstance(identity["path"], str)
        or not Path(identity["path"]).is_absolute()
        or SHA256.fullmatch(str(identity["sha256"])) is None
        or not command
        or command[0] != identity["path"]
    ):
        raise CommandManifestError(f"COMMAND_EXECUTABLE_MISMATCH:{role}")
    inspect_executable_identity(identity, role=role)


def _validate_runtime_identity(identity: Any, *, role: str) -> None:
    if (
        not isinstance(identity, dict)
        or set(identity) != {"path", "sha256"}
        or not isinstance(identity["path"], str)
        or not Path(identity["path"]).is_absolute()
        or SHA256.fullmatch(str(identity["sha256"])) is None
    ):
        raise CommandManifestError(f"COMMAND_EXECUTABLE_MISMATCH:{role}")
    inspect_executable_identity(identity, role=role)


def _validate_evidence_identity(identity: Any, *, role: str) -> None:
    if (
        not isinstance(identity, dict)
        or set(identity) != {"path", "sha256"}
        or not isinstance(identity["path"], str)
        or not Path(identity["path"]).is_absolute()
        or SHA256.fullmatch(str(identity["sha256"])) is None
    ):
        raise CommandManifestError(f"COMMAND_EVIDENCE_MISMATCH:{role}")
    snapshot = inspect_regular_file_path(
        Path(identity["path"]),
        role=f"evidence:{role}",
    )
    if snapshot.sha256 != identity["sha256"]:
        raise CommandManifestError(f"COMMAND_EVIDENCE_MISMATCH:{role}")


def host_command_template(executable: str) -> list[str]:
    """Host validator wrapper의 frozen shell-free argv다."""

    return [
        executable,
        "--output",
        "{host_report}",
        "--allowed-process-root-pid",
        "{allowed_process_root_pid}",
    ]


def boundary_command_template(executable: str, boundary: str) -> list[str]:
    """모든 native boundary wrapper가 공유하는 frozen argv 순서다."""

    return [
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


def _validate_placeholder_grammar(command: Sequence[str], *, error: str) -> None:
    allowed = {
        "host_report",
        "allowed_process_root_pid",
        "plan",
        "block_dir",
        "qualification",
        "selector_id",
        "family_id",
        "rotation_id",
        "outer_repetition",
        "run_id",
        "benchmark_subject_commit",
    }
    for argument in command:
        try:
            for _, field, format_spec, conversion in Formatter().parse(argument):
                if field is None:
                    continue
                if (
                    field not in allowed
                    or not field.isidentifier()
                    or format_spec
                    or conversion is not None
                ):
                    raise CommandManifestError(error)
        except ValueError as exc:
            raise CommandManifestError(error) from exc


def validate_manifest(value: Any) -> dict[str, Any]:
    """Frozen runner가 요구하는 exact v2 shape와 placeholder/identity를 선검증한다."""

    if not isinstance(value, dict) or set(value) != {
        "schemaVersion",
        "benchmarkSubjectCommit",
        "candidateSourceCommit",
        "hostValidatorCommand",
        "boundaryCommands",
        "allowedExecutables",
        "allowedEvidenceByBoundary",
    }:
        raise CommandManifestError("MANIFEST_FIELDS_INVALID")
    if value["schemaVersion"] != "s1.4x-benchmark-command-manifest-v3":
        raise CommandManifestError("MANIFEST_VERSION_INVALID")
    benchmark_commit = value["benchmarkSubjectCommit"]
    source_commit = value["candidateSourceCommit"]
    if (
        not isinstance(benchmark_commit, str)
        or COMMIT.fullmatch(benchmark_commit) is None
        or source_commit != benchmark_commit
    ):
        raise CommandManifestError("SUBJECT_SOURCE_COMMIT_MISMATCH")
    host = value["hostValidatorCommand"]
    boundaries = value["boundaryCommands"]
    identities = value["allowedExecutables"]
    evidence_by_boundary = value["allowedEvidenceByBoundary"]
    if (
        not isinstance(host, list)
        or not host
        or not all(isinstance(item, str) and item for item in host)
        or not isinstance(boundaries, dict)
        or set(boundaries) != set(BOUNDARY_IDS)
        or not isinstance(identities, dict)
        or set(identities)
        != {
            "hostValidator",
            "boundaries",
            "runtimeDependenciesByBoundary",
        }
        or not isinstance(identities["boundaries"], dict)
        or set(identities["boundaries"]) != set(BOUNDARY_IDS)
        or not isinstance(
            identities["runtimeDependenciesByBoundary"],
            dict,
        )
        or set(identities["runtimeDependenciesByBoundary"])
        != set(RUNTIME_DEPENDENCY_ROLES_BY_BOUNDARY)
    ):
        raise CommandManifestError("MANIFEST_COMMANDS_INVALID")
    _validate_identity(host, identities["hostValidator"], role="hostValidator")
    for boundary_id, expected_roles in (
        RUNTIME_DEPENDENCY_ROLES_BY_BOUNDARY.items()
    ):
        dependencies = identities["runtimeDependenciesByBoundary"][
            boundary_id
        ]
        if (
            not isinstance(dependencies, dict)
            or tuple(dependencies) != expected_roles
        ):
            raise CommandManifestError("MANIFEST_COMMANDS_INVALID")
        for role, identity in dependencies.items():
            _validate_runtime_identity(
                identity,
                role=f"runtime:{boundary_id}:{role}",
            )
    if (
        not isinstance(evidence_by_boundary, dict)
        or set(evidence_by_boundary)
        != set(RUNTIME_EVIDENCE_ROLES_BY_BOUNDARY)
    ):
        raise CommandManifestError("MANIFEST_EVIDENCE_INVALID")
    for boundary_id, expected_roles in (
        RUNTIME_EVIDENCE_ROLES_BY_BOUNDARY.items()
    ):
        evidence = evidence_by_boundary[boundary_id]
        if (
            not isinstance(evidence, dict)
            or tuple(evidence) != expected_roles
        ):
            raise CommandManifestError("MANIFEST_EVIDENCE_INVALID")
        for role, identity in evidence.items():
            _validate_evidence_identity(
                identity,
                role=f"{boundary_id}:{role}",
            )
    _validate_placeholder_grammar(host, error="HOST_COMMAND_TEMPLATE_MISMATCH")
    if host != host_command_template(host[0]):
        raise CommandManifestError("HOST_COMMAND_TEMPLATE_MISMATCH")
    for boundary in BOUNDARY_IDS:
        command = boundaries[boundary]
        if (
            not isinstance(command, list)
            or not command
            or not all(isinstance(item, str) and item for item in command)
        ):
            raise CommandManifestError(f"BOUNDARY_COMMAND_INVALID:{boundary}")
        _validate_identity(
            command,
            identities["boundaries"][boundary],
            role=boundary,
        )
        _validate_placeholder_grammar(
            command,
            error=f"BOUNDARY_COMMAND_TEMPLATE_MISMATCH:{boundary}",
        )
        if command != boundary_command_template(command[0], boundary):
            raise CommandManifestError(
                f"BOUNDARY_COMMAND_TEMPLATE_MISMATCH:{boundary}"
            )
    return value


def build_manifest(
    *,
    benchmark_subject_commit: str,
    candidate_source_commit: str,
    host_validator_command: Sequence[str],
    boundary_commands: Mapping[str, Sequence[str]],
    allowed_executables: Mapping[str, Any],
    allowed_evidence_by_boundary: Mapping[str, Any],
) -> dict[str, Any]:
    """명시적으로 제공된 argv/identity만 사용해 v2 manifest를 만든다."""

    return validate_manifest(
        {
            "schemaVersion": "s1.4x-benchmark-command-manifest-v3",
            "benchmarkSubjectCommit": benchmark_subject_commit,
            "candidateSourceCommit": candidate_source_commit,
            "hostValidatorCommand": list(host_validator_command),
            "boundaryCommands": {
                key: list(command) for key, command in boundary_commands.items()
            },
            "allowedExecutables": dict(allowed_executables),
            "allowedEvidenceByBoundary": dict(
                allowed_evidence_by_boundary
            ),
        }
    )


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _exclusive_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def write_manifest_exclusive(
    output: Path,
    sidecar: Path,
    manifest: Mapping[str, Any],
) -> str:
    """Canonical manifest와 GNU-style sidecar를 기존 evidence 위에 쓰지 않는다."""

    if output.exists() or sidecar.exists():
        raise CommandManifestError("OUTPUT_ALREADY_EXISTS")
    validated = validate_manifest(dict(manifest))
    payload = _canonical_bytes(validated)
    digest = hashlib.sha256(payload).hexdigest()
    try:
        _exclusive_write(output, payload)
        _exclusive_write(sidecar, f"{digest}  {output.name}\n".encode("ascii"))
    except BaseException:
        output.unlink(missing_ok=True)
        sidecar.unlink(missing_ok=True)
        raise
    return digest


def validate_manifest_file(path: Path, expected_sha256: str) -> dict[str, Any]:
    """Tracked/recorded digest와 bytes를 먼저 묶은 뒤 semantic shape를 검증한다."""

    if SHA256.fullmatch(expected_sha256) is None:
        raise CommandManifestError("EXPECTED_SHA256_INVALID")
    try:
        snapshot = inspect_regular_file_path(path, role="commandManifest")
    except ValueError as exc:
        raise CommandManifestError(str(exc)) from exc
    if snapshot.sha256 != expected_sha256:
        raise CommandManifestError("MANIFEST_SHA256_MISMATCH")
    return validate_manifest(_strict_json_load_bytes(snapshot.payload))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    generate = subcommands.add_parser("generate")
    generate.add_argument("--spec", type=Path, required=True)
    generate.add_argument("--output", type=Path, required=True)
    generate.add_argument("--sidecar", type=Path, required=True)
    validate = subcommands.add_parser("validate")
    validate.add_argument("--manifest", type=Path, required=True)
    validate.add_argument("--sha256", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        if arguments.command == "generate":
            spec = _strict_json_load(arguments.spec)
            manifest = build_manifest(
                benchmark_subject_commit=spec["benchmarkSubjectCommit"],
                candidate_source_commit=spec["candidateSourceCommit"],
                host_validator_command=spec["hostValidatorCommand"],
                boundary_commands=spec["boundaryCommands"],
                allowed_executables=spec["allowedExecutables"],
                allowed_evidence_by_boundary=spec[
                    "allowedEvidenceByBoundary"
                ],
            )
            digest = write_manifest_exclusive(
                arguments.output,
                arguments.sidecar,
                manifest,
            )
            print(
                json.dumps(
                    {"schemaVersion": manifest["schemaVersion"], "sha256": digest},
                    sort_keys=True,
                )
            )
        else:
            manifest = validate_manifest_file(arguments.manifest, arguments.sha256)
            print(
                json.dumps(
                    {
                        "schemaVersion": manifest["schemaVersion"],
                        "status": "PASS",
                    },
                    sort_keys=True,
                )
            )
    except (CommandManifestError, KeyError, TypeError) as exc:
        print(f"BENCHMARK_COMMAND_MANIFEST_FAIL:{exc}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
