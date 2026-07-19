#!/usr/bin/env python3
"""ScalaCheck/QuickCheck wrapper를 실행하고 보고서를 실제 process receipt에 결합한다."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from coverage_gate import CoverageError, validate_candidate_coverage
from gate import exclusive_json_write, strict_json_load

Runner = Callable[..., subprocess.CompletedProcess[bytes]]
CANDIDATES = {"scala", "haskell"}


class CoverageExecutionError(ValueError):
    """Property evidence가 실제 subprocess와 byte identity로 결합되지 않았다."""


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical_sha256(value: Any) -> str:
    return _sha256_bytes(
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    )


def _utc_now() -> str:
    return (
        dt.datetime.now(dt.UTC)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def _report_paths(candidate: str, output_directory: Path) -> dict[str, Path]:
    return {
        "property": output_directory / f"{candidate}-property-report.v1.json",
        "registry": output_directory / f"{candidate}-registry-report.v1.json",
        "execution": (
            output_directory
            / f"{candidate}-property-execution-evidence.v1.json"
        ),
    }


def run_candidate_coverage(
    *,
    candidate: str,
    candidate_profile: str | None,
    runner_path: Path,
    output_directory: Path,
    receipt_path: Path,
    property_plan_path: Path,
    function_registry_path: Path,
    error_registry_path: Path,
    timeout_seconds: int = 7200,
    runner: Runner = subprocess.run,
) -> dict[str, Any]:
    """한 candidate wrapper의 exit와 산출물 bytes를 독립 coverage 검증에 묶는다."""

    if candidate not in CANDIDATES:
        raise CoverageExecutionError("CANDIDATE_INVALID")
    if candidate == "scala":
        if candidate_profile not in {"A", "B", "C"}:
            raise CoverageExecutionError("SCALA_PROFILE_INVALID")
    elif candidate_profile is not None:
        raise CoverageExecutionError("HASKELL_PROFILE_ARGUMENT_FORBIDDEN")
    configured_runner = runner_path.resolve(strict=True)
    if (
        runner_path.is_symlink()
        or not configured_runner.is_file()
        or not os.access(configured_runner, os.X_OK)
    ):
        raise CoverageExecutionError("RUNNER_NOT_SAFE_EXECUTABLE")
    output = output_directory.resolve()
    receipt = receipt_path.resolve()
    if output.exists() or output.is_symlink() or receipt.exists() or receipt.is_symlink():
        raise CoverageExecutionError("COVERAGE_OUTPUT_ALREADY_EXISTS")
    if timeout_seconds < 1:
        raise CoverageExecutionError("COVERAGE_TIMEOUT_INVALID")
    command = [str(configured_runner), "--output-dir", str(output)]
    if candidate_profile is not None:
        command.extend(["--profile", candidate_profile])
    command_sha256 = _canonical_sha256(command)
    runner_sha256 = _sha256_file(configured_runner)
    started = _utc_now()
    try:
        completed = runner(
            command,
            cwd=output.parent,
            capture_output=True,
            check=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise CoverageExecutionError("COVERAGE_PROCESS_TIMEOUT") from exc
    finished = _utc_now()
    if completed.returncode != 0:
        raise CoverageExecutionError(
            f"COVERAGE_PROCESS_FAILED:exit={completed.returncode}"
        )
    if output.is_symlink() or not output.is_dir():
        raise CoverageExecutionError("COVERAGE_OUTPUT_DIRECTORY_MISSING")
    paths = _report_paths(candidate, output)
    execution = strict_json_load(paths["execution"])
    if not isinstance(execution, dict):
        raise CoverageExecutionError("EXECUTION_REPORT_INVALID")
    outer_command_field = (
        "commandArgvSha256"
        if candidate == "scala"
        else "outerCommandArgvSha256"
    )
    if execution.get(outer_command_field) != command_sha256:
        raise CoverageExecutionError("EXECUTION_COMMAND_DIGEST_MISMATCH")
    if execution.get("runnerSha256") != runner_sha256:
        raise CoverageExecutionError("EXECUTION_RUNNER_DIGEST_MISMATCH")
    if candidate == "haskell":
        expected_stack_root_path_id = (
            "S1_4X_CACHE_ROOT/stack-root-property-"
            + hashlib.sha256(
                b"property\0" + str(output).encode("utf-8")
            ).hexdigest()[:24]
        )
        if (
            execution.get("stackRootPathId")
            != expected_stack_root_path_id
        ):
            raise CoverageExecutionError(
                "EXECUTION_STACK_ROOT_PATH_ID_MISMATCH"
            )
    if (
        candidate_profile is not None
        and execution.get("toolchainProfile") != candidate_profile
    ):
        raise CoverageExecutionError("EXECUTION_PROFILE_MISMATCH")
    for path in paths.values():
        if path.is_symlink() or not path.is_file():
            raise CoverageExecutionError(f"COVERAGE_ARTIFACT_MISSING:{path.name}")
    coverage = validate_candidate_coverage(
        implementation_label=candidate,
        property_plan_path=property_plan_path.resolve(strict=True),
        function_registry_path=function_registry_path.resolve(strict=True),
        error_registry_path=error_registry_path.resolve(strict=True),
        property_report=strict_json_load(paths["property"]),
        registry_report=strict_json_load(paths["registry"]),
        execution_report=execution,
    )
    receipt_document = {
        "schemaVersion": "s1.4x-property-execution-receipt-v1",
        "candidate": candidate,
        "runner": {
            "sha256": runner_sha256,
            "commandArgvSha256": command_sha256,
        },
        "process": {
            "startedAt": started,
            "finishedAt": finished,
            "exitCode": completed.returncode,
            "stdoutSha256": _sha256_bytes(completed.stdout),
            "stderrSha256": _sha256_bytes(completed.stderr),
        },
        "artifacts": [
            {
                "path": path.name,
                "sha256": _sha256_file(path),
                "sizeBytes": path.stat().st_size,
            }
            for path in paths.values()
        ],
        "coverage": coverage,
        "status": "PASS",
    }
    exclusive_json_write(receipt, receipt_document)
    return receipt_document


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", choices=sorted(CANDIDATES), required=True)
    parser.add_argument("--profile", choices=("A", "B", "C"))
    parser.add_argument("--runner", type=Path, required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--property-plan", type=Path, required=True)
    parser.add_argument("--function-registry", type=Path, required=True)
    parser.add_argument("--error-registry", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=int, default=7200)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        receipt = run_candidate_coverage(
            candidate=arguments.candidate,
            candidate_profile=arguments.profile,
            runner_path=arguments.runner,
            output_directory=arguments.output_directory,
            receipt_path=arguments.receipt,
            property_plan_path=arguments.property_plan,
            function_registry_path=arguments.function_registry,
            error_registry_path=arguments.error_registry,
            timeout_seconds=arguments.timeout_seconds,
        )
        print(json.dumps(receipt, allow_nan=False, sort_keys=True))
    except (CoverageError, CoverageExecutionError, OSError, ValueError) as exc:
        print(f"S1_4X_COVERAGE_EXECUTION_FAIL:{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
