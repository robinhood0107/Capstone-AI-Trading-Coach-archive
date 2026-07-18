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
from typing import Any

BENCHMARKS_DIRECTORY = Path(__file__).resolve().parents[1] / "benchmarks"
if str(BENCHMARKS_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(BENCHMARKS_DIRECTORY))

from executable_identity import (  # noqa: E402
    ExecutableIdentityError as CommandManifestError,
)
from executable_identity import (  # noqa: E402
    inspect_executable_identity,
)

BOUNDARY_IDS = (
    "python-numpy-s1-4",
    "python-numpy-s1-4r",
    "python-jax-eager-s1-4r",
    "python-jax-jit-s1-4r",
    "scala",
    "haskell",
)
SHA256 = re.compile(r"^[0-9a-f]{64}$")
COMMIT = re.compile(r"^[0-9a-f]{40}$")


def _strict_json_load(path: Path) -> Any:
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
            path.read_text(encoding="utf-8", errors="strict"),
            object_pairs_hook=unique,
            parse_constant=reject_constant,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CommandManifestError("INVALID_JSON_FILE") from exc


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


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


def validate_manifest(value: Any) -> dict[str, Any]:
    """Frozen runner가 요구하는 exact v2 shape와 placeholder/identity를 선검증한다."""

    if not isinstance(value, dict) or set(value) != {
        "schemaVersion",
        "benchmarkSubjectCommit",
        "candidateSourceCommit",
        "hostValidatorCommand",
        "boundaryCommands",
        "allowedExecutables",
    }:
        raise CommandManifestError("MANIFEST_FIELDS_INVALID")
    if value["schemaVersion"] != "s1.4x-benchmark-command-manifest-v2":
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
    if (
        not isinstance(host, list)
        or not host
        or not all(isinstance(item, str) and item for item in host)
        or not isinstance(boundaries, dict)
        or set(boundaries) != set(BOUNDARY_IDS)
        or not isinstance(identities, dict)
        or set(identities) != {"hostValidator", "boundaries"}
        or not isinstance(identities["boundaries"], dict)
        or set(identities["boundaries"]) != set(BOUNDARY_IDS)
    ):
        raise CommandManifestError("MANIFEST_COMMANDS_INVALID")
    if sum(item.count("{host_report}") for item in host) != 1:
        raise CommandManifestError("HOST_REPORT_PLACEHOLDER_COUNT")
    if any("{qualification}" in item for item in host):
        raise CommandManifestError("QUALIFICATION_IN_HOST_COMMAND")
    _validate_identity(host, identities["hostValidator"], role="hostValidator")
    for boundary in BOUNDARY_IDS:
        command = boundaries[boundary]
        if (
            not isinstance(command, list)
            or not command
            or not all(isinstance(item, str) and item for item in command)
        ):
            raise CommandManifestError(f"BOUNDARY_COMMAND_INVALID:{boundary}")
        if sum(item.count("{qualification}") for item in command) != 1:
            raise CommandManifestError(
                f"QUALIFICATION_PLACEHOLDER_COUNT:{boundary}"
            )
        if any("{host_report}" in item for item in command):
            raise CommandManifestError(f"HOST_REPORT_IN_BOUNDARY:{boundary}")
        _validate_identity(
            command,
            identities["boundaries"][boundary],
            role=boundary,
        )
    return value


def build_manifest(
    *,
    benchmark_subject_commit: str,
    candidate_source_commit: str,
    host_validator_command: Sequence[str],
    boundary_commands: Mapping[str, Sequence[str]],
    allowed_executables: Mapping[str, Any],
) -> dict[str, Any]:
    """명시적으로 제공된 argv/identity만 사용해 v2 manifest를 만든다."""

    return validate_manifest(
        {
            "schemaVersion": "s1.4x-benchmark-command-manifest-v2",
            "benchmarkSubjectCommit": benchmark_subject_commit,
            "candidateSourceCommit": candidate_source_commit,
            "hostValidatorCommand": list(host_validator_command),
            "boundaryCommands": {
                key: list(command) for key, command in boundary_commands.items()
            },
            "allowedExecutables": dict(allowed_executables),
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
    if path.is_symlink() or not path.is_file():
        raise CommandManifestError("MANIFEST_NOT_REGULAR_FILE")
    if _file_sha256(path) != expected_sha256:
        raise CommandManifestError("MANIFEST_SHA256_MISMATCH")
    return validate_manifest(_strict_json_load(path))


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
