#!/usr/bin/env python3
"""ScalaCheck/QuickCheck wrapper를 실행하고 보고서를 실제 process receipt에 결합한다."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from coverage_gate import CoverageError, validate_candidate_coverage
from gate import exclusive_json_write, strict_json_load

Runner = Callable[..., subprocess.CompletedProcess[bytes]]
CANDIDATES = {"scala", "haskell"}
HASKELL_GHC_OPTION_ARGPARSE_FAILURE = (
    b"haskell_evidence.py generated-cabal-provenance: error: argument "
    b"--ghc-option: expected one argument\n"
)


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


def _runner_pass_fds(candidate: str) -> tuple[int, ...]:
    if candidate != "haskell":
        return ()
    pinned_path = os.environ.get("S1_4X_BENCHMARK_PYTHON_PINNED_FD_PATH")
    if pinned_path is None:
        return ()
    matched = re.fullmatch(r"/proc/self/fd/([1-9][0-9]*)", pinned_path)
    if matched is None:
        raise CoverageExecutionError("BENCHMARK_PYTHON_PINNED_FD_INVALID")
    descriptor = int(matched.group(1))
    if descriptor < 3:
        raise CoverageExecutionError("BENCHMARK_PYTHON_PINNED_FD_INVALID")
    try:
        os.fstat(descriptor)
    except OSError as exc:
        raise CoverageExecutionError("BENCHMARK_PYTHON_PINNED_FD_UNAVAILABLE") from exc
    # 상위 gate가 봉인한 Python 실행 FD를 Haskell wrapper까지 유지한다.
    return (descriptor,)


def _exclusive_bytes_write(path: Path, payload: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    with os.fdopen(os.open(path, flags, 0o600), "wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def _process_bytes(value: bytes | str | None) -> bytes:
    if value is None:
        return b""
    return value if isinstance(value, bytes) else value.encode("utf-8")


def _persist_process_failure(
    *,
    candidate: str,
    receipt: Path,
    command_sha256: str,
    runner_sha256: str,
    started_at: str,
    finished_at: str,
    exit_code: int,
    failure_code: str,
    stdout: bytes | str | None,
    stderr: bytes | str | None,
) -> Path:
    """실패한 child stream과 digest를 terminal evidence 수집 경계에 남긴다."""

    standard_output = _process_bytes(stdout)
    standard_error = _process_bytes(stderr)
    output_path = receipt.with_name(f"{receipt.stem}.process.stdout")
    error_path = receipt.with_name(f"{receipt.stem}.process.stderr")
    failure_path = receipt.with_name(f"{receipt.stem}.failure.json")
    _exclusive_bytes_write(output_path, standard_output)
    _exclusive_bytes_write(error_path, standard_error)
    exclusive_json_write(
        failure_path,
        {
            "schemaVersion": "s1.4x-property-execution-failure-v1",
            "candidate": candidate,
            "failureCode": failure_code,
            "runner": {
                "sha256": runner_sha256,
                "commandArgvSha256": command_sha256,
            },
            "process": {
                "startedAt": started_at,
                "finishedAt": finished_at,
                "exitCode": exit_code,
                "stdout": {
                    "path": output_path.name,
                    "sha256": _sha256_bytes(standard_output),
                    "sizeBytes": len(standard_output),
                },
                "stderr": {
                    "path": error_path.name,
                    "sha256": _sha256_bytes(standard_error),
                    "sizeBytes": len(standard_error),
                },
            },
            "status": "FAIL",
        },
    )
    return failure_path


def _generated_cabal_completion_paths(receipt: Path) -> tuple[Path, Path, Path]:
    stem = f"{receipt.stem}.generated-cabal-completion"
    return (
        receipt.with_name(f"{stem}.json"),
        receipt.with_name(f"{stem}.stdout"),
        receipt.with_name(f"{stem}.stderr"),
    )


def _persist_generated_cabal_completion(
    *,
    receipt: Path,
    command: Sequence[str],
    portable_command: Sequence[str],
    original: subprocess.CompletedProcess[bytes],
    original_started_at: str,
    original_finished_at: str,
    completion: subprocess.CompletedProcess[bytes],
    started_at: str,
    finished_at: str,
    provenance_path: Path | None,
    status: str,
    failure_code: str | None,
) -> dict[str, Any]:
    """봉인된 Haskell source를 바꾸지 않는 exact CLI completion을 기록한다."""

    sidecar, output_path, error_path = _generated_cabal_completion_paths(receipt)
    standard_output = _process_bytes(completion.stdout)
    standard_error = _process_bytes(completion.stderr)
    _exclusive_bytes_write(output_path, standard_output)
    _exclusive_bytes_write(error_path, standard_error)
    original_output = _process_bytes(original.stdout)
    original_error = _process_bytes(original.stderr)
    original_output_path = receipt.with_name(f"{receipt.stem}.process.stdout")
    original_error_path = receipt.with_name(f"{receipt.stem}.process.stderr")
    if status == "PASS":
        _exclusive_bytes_write(original_output_path, original_output)
        _exclusive_bytes_write(original_error_path, original_error)
    document: dict[str, Any] = {
        "reason": "ARGPARSE_DASH_PREFIXED_GHC_OPTION",
        "originalProcess": {
            "startedAt": original_started_at,
            "finishedAt": original_finished_at,
            "exitCode": original.returncode,
            "stdout": {
                "path": original_output_path.name,
                "sha256": _sha256_bytes(original_output),
                "sizeBytes": len(original_output),
            },
            "stderr": {
                "path": original_error_path.name,
                "sha256": _sha256_bytes(original_error),
                "sizeBytes": len(original_error),
            },
        },
        "completion": {
            "commandArgvSha256": _canonical_sha256(list(command)),
            "portableArgv": list(portable_command),
            "portableArgvSha256": _canonical_sha256(list(portable_command)),
            "startedAt": started_at,
            "finishedAt": finished_at,
            "exitCode": completion.returncode,
            "stdout": {
                "path": output_path.name,
                "sha256": _sha256_bytes(standard_output),
                "sizeBytes": len(standard_output),
            },
            "stderr": {
                "path": error_path.name,
                "sha256": _sha256_bytes(standard_error),
                "sizeBytes": len(standard_error),
            },
        },
        "status": status,
    }
    if provenance_path is not None:
        document["artifact"] = {
            "path": provenance_path.name,
            "sha256": _sha256_file(provenance_path),
            "sizeBytes": provenance_path.stat().st_size,
        }
    if failure_code is not None:
        document["failureCode"] = failure_code
    if status == "FAIL":
        exclusive_json_write(
            sidecar,
            {
                "schemaVersion": (
                    "s1.4x-haskell-generated-cabal-completion-attempt-v1"
                ),
                **document,
            },
        )
    return document


def _is_exact_haskell_ghc_option_argparse_failure(
    candidate: str,
    completed: subprocess.CompletedProcess[bytes],
) -> bool:
    return (
        candidate == "haskell"
        and completed.returncode == 2
        and _process_bytes(completed.stdout) == b""
        and _process_bytes(completed.stderr).endswith(
            HASKELL_GHC_OPTION_ARGPARSE_FAILURE
        )
    )


def _haskell_completion_portable_argv(
    *,
    subject: str,
    cabal_sha256: str,
    profile_options: list[str],
    stack_root_path_id: str,
    build_argv_sha256: str,
) -> list[str]:
    return [
        "bash",
        "--noprofile",
        "--norc",
        "-c",
        'source "$1"; shift; s1_4x_run_benchmark_python "$@"',
        "s1-4x-generated-cabal-completion",
        "haskell/tools/python-runtime.sh",
        "haskell/tools/haskell_evidence.py",
        "generated-cabal-provenance",
        "--haskell-root",
        "haskell",
        "--output-directory",
        "coverage/haskell",
        "--benchmark-subject-commit",
        subject,
        "--stack-bin",
        "<pinned-stack-bin>",
        "--pre-build-sha256",
        cabal_sha256,
        *[f"--ghc-option={option}" for option in profile_options],
        "--stack-root-path-id",
        stack_root_path_id,
        "--runtime-build-argv-sha256",
        build_argv_sha256,
    ]


def _validate_completed_generated_cabal_provenance(
    *,
    provenance_path: Path,
    output: Path,
    haskell_root: Path,
    stack_bin: Path,
    execution: dict[str, Any],
    subject: str,
    cabal_sha256: str,
    profile_options: list[str],
    completion_stdout: bytes,
) -> None:
    if provenance_path.is_symlink() or not provenance_path.is_file():
        raise CoverageExecutionError("HASKELL_COMPLETION_PROVENANCE_INVALID")
    provenance = strict_json_load(provenance_path)
    expected_fields = {
        "schemaVersion",
        "benchmarkSubjectCommit",
        "toolchainLockSha256",
        "packageYaml",
        "sourceInputManifest",
        "stack",
        "hpack",
        "build",
        "generatedCabal",
        "sourceTreeSha256",
        "propertyClosureSha256",
        "status",
    }
    if not isinstance(provenance, dict) or set(provenance) != expected_fields:
        raise CoverageExecutionError("HASKELL_COMPLETION_PROVENANCE_INVALID")
    build = provenance.get("build")
    generated = provenance.get("generatedCabal")
    manifest = provenance.get("sourceInputManifest")
    package = provenance.get("packageYaml")
    stack = provenance.get("stack")
    hpack = provenance.get("hpack")
    if (
        not isinstance(build, dict)
        or not isinstance(generated, dict)
        or not isinstance(manifest, dict)
        or not isinstance(package, dict)
        or not isinstance(stack, dict)
        or not isinstance(hpack, dict)
    ):
        raise CoverageExecutionError("HASKELL_COMPLETION_PROVENANCE_INVALID")
    if (
        set(package) != {"path", "blobSha256"}
        or set(manifest) != {"path", "blobSha256"}
        or set(stack) != {"pathId", "version", "binarySha256"}
        or set(hpack) != {"version", "versionOutputSha256"}
        or set(build)
        != {
            "portableArgv",
            "portableArgvSha256",
            "runtimeArgvSha256",
            "stackRootPathId",
            "exitCode",
        }
        or set(generated)
        != {
            "repositoryRelativePath",
            "artifactPath",
            "sha256",
            "sizeBytes",
            "preBuildSha256",
            "postBuildSha256",
        }
    ):
        raise CoverageExecutionError("HASKELL_COMPLETION_PROVENANCE_INVALID")
    package_path = haskell_root / "package.yaml"
    source_manifest_path = haskell_root / "source-inputs.v1.json"
    toolchain_lock_path = haskell_root / "toolchain-lock.v1.json"
    if any(
        path.is_symlink() or not path.is_file()
        for path in (package_path, source_manifest_path, toolchain_lock_path)
    ):
        raise CoverageExecutionError("HASKELL_COMPLETION_PROVENANCE_INVALID")
    toolchain_lock = strict_json_load(toolchain_lock_path)
    if not isinstance(toolchain_lock, dict):
        raise CoverageExecutionError("HASKELL_COMPLETION_PROVENANCE_INVALID")
    try:
        stack_lock = toolchain_lock["resolvedTools"]["stack"]
    except (KeyError, TypeError) as exc:
        raise CoverageExecutionError(
            "HASKELL_COMPLETION_PROVENANCE_INVALID"
        ) from exc
    if not isinstance(stack_lock, dict):
        raise CoverageExecutionError("HASKELL_COMPLETION_PROVENANCE_INVALID")
    build_portable_argv = [
        "stack",
        "--stack-root",
        "<isolated-stack-root>",
        "--work-dir",
        "<isolated-stack-work-dir>",
        "--system-ghc",
        "--no-install-ghc",
        "--stack-yaml",
        "haskell/stack.yaml",
        "--hpack-force",
        "build",
        "--test",
        "--no-run-tests",
        "--no-terminal",
        "--ghc-options",
        " ".join(profile_options),
    ]
    generated_artifact = output / "generated/s1-4x-haskell.cabal"
    expected_stdout = (
        json.dumps(provenance, allow_nan=False, sort_keys=True) + "\n"
    ).encode("utf-8")
    if (
        generated_artifact.is_symlink()
        or not generated_artifact.is_file()
        or provenance.get("schemaVersion")
        != "s1.4x-haskell-generated-cabal-provenance-v1"
        or provenance.get("benchmarkSubjectCommit") != subject
        or provenance.get("toolchainLockSha256")
        != _sha256_file(toolchain_lock_path)
        or package
        != {
            "path": (
                "workspaces/decision-platform/research/"
                "s1-4x-numeric-parity/haskell/package.yaml"
            ),
            "blobSha256": _sha256_file(package_path),
        }
        or manifest
        != {
            "path": (
                "workspaces/decision-platform/research/"
                "s1-4x-numeric-parity/haskell/source-inputs.v1.json"
            ),
            "blobSha256": _sha256_file(source_manifest_path),
        }
        or manifest.get("blobSha256")
        != execution.get("sourceInputManifestSha256")
        or stack
        != {
            "pathId": stack_lock.get("pathId"),
            "version": stack_lock.get("version"),
            "binarySha256": stack_lock.get("sha256"),
        }
        or _sha256_file(stack_bin) != stack_lock.get("sha256")
        or hpack
        != {
            "version": "0.39.6",
            "versionOutputSha256": _sha256_bytes(b"0.39.6\n"),
        }
        or build.get("portableArgv") != build_portable_argv
        or build.get("portableArgvSha256")
        != _canonical_sha256(build_portable_argv)
        or provenance.get("sourceTreeSha256") != execution.get("sourceTreeSha256")
        or provenance.get("propertyClosureSha256")
        != execution.get("propertyClosureSha256")
        or build.get("runtimeArgvSha256") != execution.get("buildArgvSha256")
        or build.get("stackRootPathId") != execution.get("stackRootPathId")
        or build.get("exitCode") != 0
        or generated.get("artifactPath")
        != "coverage/haskell/generated/s1-4x-haskell.cabal"
        or generated.get("repositoryRelativePath")
        != (
            "workspaces/decision-platform/research/"
            "s1-4x-numeric-parity/haskell/s1-4x-haskell.cabal"
        )
        or generated.get("sha256") != cabal_sha256
        or generated.get("preBuildSha256") != cabal_sha256
        or generated.get("postBuildSha256") != cabal_sha256
        or generated.get("sizeBytes") != generated_artifact.stat().st_size
        or _sha256_file(generated_artifact) != cabal_sha256
        or completion_stdout != expected_stdout
        or provenance.get("status") != "PASS"
    ):
        raise CoverageExecutionError("HASKELL_COMPLETION_PROVENANCE_INVALID")


def _complete_haskell_generated_cabal_provenance(
    *,
    completed: subprocess.CompletedProcess[bytes],
    configured_runner: Path,
    output: Path,
    receipt: Path,
    property_plan: Path,
    execution: dict[str, Any],
    original_started_at: str,
    original_finished_at: str,
    child_environment: dict[str, str] | None,
    timeout_seconds: int,
    provenance_runner: Runner,
) -> dict[str, Any]:
    """이미 PASS한 property run 뒤 누락된 generated-Cabal 증거만 1회 완성한다."""

    haskell_root = property_plan.parent.parent / "haskell"
    expected_runner = haskell_root / "tools/run-property-evidence.sh"
    if configured_runner != expected_runner.resolve(strict=True):
        raise CoverageExecutionError("HASKELL_COMPLETION_RUNNER_MISMATCH")
    if output.is_symlink() or not output.is_dir():
        raise CoverageExecutionError("HASKELL_COMPLETION_OUTPUT_INVALID")
    profile_options = execution.get("profileGhcOptions")
    if profile_options not in (["-O0", "-fasm"], ["-O2", "-fasm"]):
        raise CoverageExecutionError("HASKELL_COMPLETION_PROFILE_INVALID")
    build_argv_sha256 = execution.get("buildArgvSha256")
    stack_root_path_id = execution.get("stackRootPathId")
    if (
        not isinstance(build_argv_sha256, str)
        or re.fullmatch(r"[0-9a-f]{64}", build_argv_sha256) is None
        or not isinstance(stack_root_path_id, str)
    ):
        raise CoverageExecutionError("HASKELL_COMPLETION_EXECUTION_INVALID")
    subject = os.environ.get("S1_4X_BENCHMARK_SUBJECT_COMMIT", "")
    stack_bin = Path(os.environ.get("S1_4X_STACK_BIN", ""))
    if (
        re.fullmatch(r"[0-9a-f]{40}", subject) is None
        or not stack_bin.is_absolute()
        or stack_bin.is_symlink()
        or not stack_bin.is_file()
        or not os.access(stack_bin, os.X_OK)
    ):
        raise CoverageExecutionError("HASKELL_COMPLETION_ENVIRONMENT_INVALID")
    cabal_path = haskell_root / "s1-4x-haskell.cabal"
    if cabal_path.is_symlink() or not cabal_path.is_file():
        raise CoverageExecutionError("HASKELL_COMPLETION_CABAL_DRIFT")
    cabal_sha256 = _sha256_file(cabal_path)
    evidence_script = haskell_root / "tools/haskell_evidence.py"
    runtime_script = haskell_root / "tools/python-runtime.sh"
    if any(
        path.is_symlink() or not path.is_file()
        for path in (evidence_script, runtime_script)
    ):
        raise CoverageExecutionError("HASKELL_COMPLETION_TOOL_INVALID")
    command = [
        "/usr/bin/bash",
        "--noprofile",
        "--norc",
        "-c",
        'source "$1"; shift; s1_4x_run_benchmark_python "$@"',
        "s1-4x-generated-cabal-completion",
        str(runtime_script),
        str(evidence_script),
        "generated-cabal-provenance",
        "--haskell-root",
        str(haskell_root),
        "--output-directory",
        str(output),
        "--benchmark-subject-commit",
        subject,
        "--stack-bin",
        str(stack_bin),
        "--pre-build-sha256",
        cabal_sha256,
        *[f"--ghc-option={option}" for option in profile_options],
        "--stack-root-path-id",
        stack_root_path_id,
        "--runtime-build-argv-sha256",
        build_argv_sha256,
    ]
    portable_command = _haskell_completion_portable_argv(
        subject=subject,
        cabal_sha256=cabal_sha256,
        profile_options=profile_options,
        stack_root_path_id=stack_root_path_id,
        build_argv_sha256=build_argv_sha256,
    )
    completion_environment = dict(child_environment or os.environ)
    if "BASH_ENV" in completion_environment or "ENV" in completion_environment:
        raise CoverageExecutionError("HASKELL_COMPLETION_ENVIRONMENT_INVALID")
    started = _utc_now()
    try:
        completion = provenance_runner(
            command,
            cwd=output.parent,
            capture_output=True,
            check=False,
            env=completion_environment,
            pass_fds=_runner_pass_fds("haskell"),
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        completion = subprocess.CompletedProcess(
            command,
            124,
            _process_bytes(exc.stdout),
            _process_bytes(exc.stderr),
        )
    except OSError as exc:
        completion = subprocess.CompletedProcess(
            command,
            71,
            b"",
            str(exc).encode("utf-8", errors="replace"),
        )
    finished = _utc_now()
    provenance_path = output / "haskell-generated-cabal-provenance.v1.json"
    try:
        if completion.returncode != 0:
            raise CoverageExecutionError("HASKELL_COMPLETION_PROCESS_FAILED")
        _validate_completed_generated_cabal_provenance(
            provenance_path=provenance_path,
            output=output,
            haskell_root=haskell_root,
            stack_bin=stack_bin,
            execution=execution,
            subject=subject,
            cabal_sha256=cabal_sha256,
            profile_options=profile_options,
            completion_stdout=_process_bytes(completion.stdout),
        )
    except (CoverageExecutionError, OSError, UnicodeError, ValueError) as exc:
        _persist_generated_cabal_completion(
            receipt=receipt,
            command=command,
            portable_command=portable_command,
            original=completed,
            original_started_at=original_started_at,
            original_finished_at=original_finished_at,
            completion=completion,
            started_at=started,
            finished_at=finished,
            provenance_path=None,
            status="FAIL",
            failure_code="HASKELL_GENERATED_CABAL_COMPLETION_FAILED",
        )
        raise CoverageExecutionError(
            "HASKELL_GENERATED_CABAL_COMPLETION_FAILED"
        ) from exc
    return _persist_generated_cabal_completion(
        receipt=receipt,
        command=command,
        portable_command=portable_command,
        original=completed,
        original_started_at=original_started_at,
        original_finished_at=original_finished_at,
        completion=completion,
        started_at=started,
        finished_at=finished,
        provenance_path=provenance_path,
        status="PASS",
        failure_code=None,
    )


def _pantry_prewarm_paths(receipt: Path) -> tuple[Path, Path, Path]:
    stem = f"{receipt.stem}.pantry-prewarm"
    return (
        receipt.with_name(f"{stem}.json"),
        receipt.with_name(f"{stem}.stdout"),
        receipt.with_name(f"{stem}.stderr"),
    )


def _persist_pantry_prewarm(
    *,
    receipt: Path,
    command: list[str],
    stack_sha256: str,
    stack_root: Path,
    pantry_root: Path,
    started_at: str,
    finished_at: str,
    exit_code: int,
    stdout: bytes | str | None,
    stderr: bytes | str | None,
    status: str,
    failure_code: str | None,
    artifacts: list[dict[str, Any]],
) -> Path:
    """Stack index 선행 초기화의 process와 cache marker를 별도 sidecar로 보존한다."""

    sidecar, output_path, error_path = _pantry_prewarm_paths(receipt)
    standard_output = _process_bytes(stdout)
    standard_error = _process_bytes(stderr)
    _exclusive_bytes_write(output_path, standard_output)
    _exclusive_bytes_write(error_path, standard_error)
    document: dict[str, Any] = {
        "schemaVersion": "s1.4x-haskell-pantry-prewarm-v1",
        "command": {
            "argvSha256": _canonical_sha256(command),
            "stackBinarySha256": stack_sha256,
        },
        "paths": {
            "stackRoot": str(stack_root),
            "pantryRoot": str(pantry_root),
            "stackRootPathId": "HOME/.stack",
            "pantryRootPathId": "HOME/.stack/pantry",
        },
        "process": {
            "startedAt": started_at,
            "finishedAt": finished_at,
            "exitCode": exit_code,
            "stdout": {
                "path": output_path.name,
                "sha256": _sha256_bytes(standard_output),
                "sizeBytes": len(standard_output),
            },
            "stderr": {
                "path": error_path.name,
                "sha256": _sha256_bytes(standard_error),
                "sizeBytes": len(standard_error),
            },
        },
        "artifacts": artifacts,
        "status": status,
    }
    if failure_code is not None:
        document["failureCode"] = failure_code
    exclusive_json_write(sidecar, document)
    return sidecar


def _required_stack_binary() -> Path:
    configured = os.environ.get("S1_4X_STACK_BIN")
    if configured is None or not Path(configured).is_absolute():
        raise CoverageExecutionError("HASKELL_PANTRY_STACK_PATH_INVALID")
    path = Path(configured)
    resolved = path.resolve(strict=True)
    if path.is_symlink() or not resolved.is_file() or not os.access(resolved, os.X_OK):
        raise CoverageExecutionError("HASKELL_PANTRY_STACK_PATH_INVALID")
    return resolved


def _shared_stack_root() -> Path:
    configured_home = os.environ.get("HOME")
    if configured_home is None or not Path(configured_home).is_absolute():
        raise CoverageExecutionError("HASKELL_PANTRY_HOME_INVALID")
    home = Path(configured_home)
    resolved_home = home.resolve(strict=True)
    if home.is_symlink() or resolved_home != home:
        raise CoverageExecutionError("HASKELL_PANTRY_HOME_INVALID")
    stack_root = resolved_home / ".stack"
    if stack_root.is_symlink():
        raise CoverageExecutionError("HASKELL_PANTRY_STACK_ROOT_INVALID")
    return stack_root


def _pantry_markers(
    stack_root: Path,
    pantry_root: Path,
) -> list[dict[str, Any]]:
    if (
        stack_root.is_symlink()
        or not stack_root.is_dir()
        or stack_root.resolve(strict=True) != stack_root
        or pantry_root.is_symlink()
        or not pantry_root.is_dir()
        or pantry_root.resolve(strict=True) != pantry_root
    ):
        raise CoverageExecutionError("HASKELL_PANTRY_PREWARM_ARTIFACT_INVALID")
    artifacts: list[dict[str, Any]] = []
    for relative in ("pantry.sqlite3", "hackage/00-index.tar"):
        path = pantry_root / relative
        if path.is_symlink() or not path.is_file() or path.stat().st_size < 1:
            raise CoverageExecutionError("HASKELL_PANTRY_PREWARM_ARTIFACT_INVALID")
        artifacts.append(
            {
                "path": relative,
                "sizeBytes": path.stat().st_size,
            }
        )
    return artifacts


def _prewarm_haskell_pantry(
    *,
    receipt: Path,
    property_plan: Path,
    timeout_seconds: int,
    runner: Runner,
) -> dict[str, str]:
    """공유 Pantry를 단일 `stack update`로 완성한 뒤 child 전용 환경을 만든다."""

    for variable in (
        "PANTRY_ROOT",
        "STACK_CONFIG",
        "STACK_GLOBAL_CONFIG",
        "STACK_OPTS",
        "STACK_ROOT",
        "STACK_WORK",
        "STACK_XDG",
        "STACK_YAML",
    ):
        if variable in os.environ:
            raise CoverageExecutionError(
                f"AMBIENT_STACK_CONFIGURATION_FORBIDDEN:{variable}"
            )
    stack = _required_stack_binary()
    stack_sha256 = _sha256_file(stack)
    stack_root = _shared_stack_root()
    pantry_root = stack_root / "pantry"
    stack_yaml = property_plan.parent.parent / "haskell/stack.yaml"
    if stack_yaml.is_symlink() or not stack_yaml.is_file():
        raise CoverageExecutionError("HASKELL_PANTRY_STACK_YAML_INVALID")
    stack_yaml = stack_yaml.resolve(strict=True)
    command = [
        str(stack),
        "--stack-root",
        str(stack_root),
        "--system-ghc",
        "--no-install-ghc",
        "--stack-yaml",
        str(stack_yaml),
        "update",
        "--no-terminal",
    ]
    environment = dict(os.environ)
    started = _utc_now()
    try:
        completed = runner(
            command,
            cwd=stack_yaml.parent,
            capture_output=True,
            check=False,
            env=environment,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        sidecar = _persist_pantry_prewarm(
            receipt=receipt,
            command=command,
            stack_sha256=stack_sha256,
            stack_root=stack_root,
            pantry_root=pantry_root,
            started_at=started,
            finished_at=_utc_now(),
            exit_code=124,
            stdout=exc.stdout,
            stderr=exc.stderr,
            status="FAIL",
            failure_code="HASKELL_PANTRY_PREWARM_TIMEOUT",
            artifacts=[],
        )
        raise CoverageExecutionError(
            f"HASKELL_PANTRY_PREWARM_TIMEOUT:evidence={sidecar.name}"
        ) from exc
    except OSError as exc:
        sidecar = _persist_pantry_prewarm(
            receipt=receipt,
            command=command,
            stack_sha256=stack_sha256,
            stack_root=stack_root,
            pantry_root=pantry_root,
            started_at=started,
            finished_at=_utc_now(),
            exit_code=127,
            stdout=b"",
            stderr=str(exc).encode("utf-8", errors="replace"),
            status="FAIL",
            failure_code="HASKELL_PANTRY_PREWARM_SPAWN_FAILED",
            artifacts=[],
        )
        raise CoverageExecutionError(
            f"HASKELL_PANTRY_PREWARM_SPAWN_FAILED:evidence={sidecar.name}"
        ) from exc
    finished = _utc_now()
    if completed.returncode != 0:
        sidecar = _persist_pantry_prewarm(
            receipt=receipt,
            command=command,
            stack_sha256=stack_sha256,
            stack_root=stack_root,
            pantry_root=pantry_root,
            started_at=started,
            finished_at=finished,
            exit_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            status="FAIL",
            failure_code="HASKELL_PANTRY_PREWARM_FAILED",
            artifacts=[],
        )
        raise CoverageExecutionError(
            "HASKELL_PANTRY_PREWARM_FAILED:"
            f"exit={completed.returncode}:evidence={sidecar.name}"
        )
    try:
        artifacts = _pantry_markers(stack_root, pantry_root)
    except CoverageExecutionError:
        sidecar = _persist_pantry_prewarm(
            receipt=receipt,
            command=command,
            stack_sha256=stack_sha256,
            stack_root=stack_root,
            pantry_root=pantry_root,
            started_at=started,
            finished_at=finished,
            exit_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            status="FAIL",
            failure_code="HASKELL_PANTRY_PREWARM_ARTIFACT_INVALID",
            artifacts=[],
        )
        raise CoverageExecutionError(
            f"HASKELL_PANTRY_PREWARM_ARTIFACT_INVALID:evidence={sidecar.name}"
        ) from None
    _persist_pantry_prewarm(
        receipt=receipt,
        command=command,
        stack_sha256=stack_sha256,
        stack_root=stack_root,
        pantry_root=pantry_root,
        started_at=started,
        finished_at=finished,
        exit_code=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
        status="PASS",
        failure_code=None,
        artifacts=artifacts,
    )
    child_environment = dict(environment)
    child_environment["PANTRY_ROOT"] = str(pantry_root.resolve(strict=True))
    return child_environment


def _report_paths(candidate: str, output_directory: Path) -> dict[str, Path]:
    return {
        "property": output_directory / f"{candidate}-property-report.v1.json",
        "registry": output_directory / f"{candidate}-registry-report.v1.json",
        "execution": (
            output_directory / f"{candidate}-property-execution-evidence.v1.json"
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
    prewarm_haskell_pantry: bool = False,
    runner: Runner = subprocess.run,
    prewarm_runner: Runner = subprocess.run,
    provenance_runner: Runner = subprocess.run,
) -> dict[str, Any]:
    """한 candidate wrapper의 exit와 산출물 bytes를 독립 coverage 검증에 묶는다."""

    if candidate not in CANDIDATES:
        raise CoverageExecutionError("CANDIDATE_INVALID")
    if candidate == "scala":
        if candidate_profile not in {"A", "B", "C"}:
            raise CoverageExecutionError("SCALA_PROFILE_INVALID")
        if prewarm_haskell_pantry:
            raise CoverageExecutionError("HASKELL_PANTRY_PREWARM_CANDIDATE_INVALID")
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
    if (
        output.exists()
        or output.is_symlink()
        or receipt.exists()
        or receipt.is_symlink()
    ):
        raise CoverageExecutionError("COVERAGE_OUTPUT_ALREADY_EXISTS")
    if timeout_seconds < 1:
        raise CoverageExecutionError("COVERAGE_TIMEOUT_INVALID")
    command = [str(configured_runner), "--output-dir", str(output)]
    if candidate_profile is not None:
        command.extend(["--profile", candidate_profile])
    command_sha256 = _canonical_sha256(command)
    runner_sha256 = _sha256_file(configured_runner)
    property_plan = property_plan_path.resolve(strict=True)
    child_environment = None
    if prewarm_haskell_pantry:
        child_environment = _prewarm_haskell_pantry(
            receipt=receipt,
            property_plan=property_plan,
            timeout_seconds=timeout_seconds,
            runner=prewarm_runner,
        )
    started = _utc_now()
    try:
        runner_arguments: dict[str, Any] = {
            "cwd": output.parent,
            "capture_output": True,
            "check": False,
            "pass_fds": _runner_pass_fds(candidate),
            "timeout": timeout_seconds,
        }
        if child_environment is not None:
            runner_arguments["env"] = child_environment
        completed = runner(command, **runner_arguments)
    except subprocess.TimeoutExpired as exc:
        failure_path = _persist_process_failure(
            candidate=candidate,
            receipt=receipt,
            command_sha256=command_sha256,
            runner_sha256=runner_sha256,
            started_at=started,
            finished_at=_utc_now(),
            exit_code=124,
            failure_code="COVERAGE_PROCESS_TIMEOUT",
            stdout=exc.stdout,
            stderr=exc.stderr,
        )
        raise CoverageExecutionError(
            f"COVERAGE_PROCESS_TIMEOUT:evidence={failure_path.name}"
        ) from exc
    finished = _utc_now()
    requires_generated_cabal_completion = _is_exact_haskell_ghc_option_argparse_failure(
        candidate, completed
    )
    if completed.returncode != 0 and not requires_generated_cabal_completion:
        failure_path = _persist_process_failure(
            candidate=candidate,
            receipt=receipt,
            command_sha256=command_sha256,
            runner_sha256=runner_sha256,
            started_at=started,
            finished_at=finished,
            exit_code=completed.returncode,
            failure_code="COVERAGE_PROCESS_FAILED",
            stdout=completed.stdout,
            stderr=completed.stderr,
        )
        raise CoverageExecutionError(
            f"COVERAGE_PROCESS_FAILED:exit={completed.returncode}:"
            f"evidence={failure_path.name}"
        )
    if output.is_symlink() or not output.is_dir():
        raise CoverageExecutionError("COVERAGE_OUTPUT_DIRECTORY_MISSING")
    paths = _report_paths(candidate, output)
    execution = strict_json_load(paths["execution"])
    if not isinstance(execution, dict):
        raise CoverageExecutionError("EXECUTION_REPORT_INVALID")
    outer_command_field = (
        "commandArgvSha256" if candidate == "scala" else "outerCommandArgvSha256"
    )
    if execution.get(outer_command_field) != command_sha256:
        raise CoverageExecutionError("EXECUTION_COMMAND_DIGEST_MISMATCH")
    if execution.get("runnerSha256") != runner_sha256:
        raise CoverageExecutionError("EXECUTION_RUNNER_DIGEST_MISMATCH")
    if candidate == "haskell":
        expected_stack_root_path_id = (
            "S1_4X_CACHE_ROOT/stack-root-property-"
            + hashlib.sha256(b"property\0" + str(output).encode("utf-8")).hexdigest()[
                :24
            ]
        )
        if execution.get("stackRootPathId") != expected_stack_root_path_id:
            raise CoverageExecutionError("EXECUTION_STACK_ROOT_PATH_ID_MISMATCH")
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
        property_plan_path=property_plan,
        function_registry_path=function_registry_path.resolve(strict=True),
        error_registry_path=error_registry_path.resolve(strict=True),
        property_report=strict_json_load(paths["property"]),
        registry_report=strict_json_load(paths["registry"]),
        execution_report=execution,
    )
    completion_document: dict[str, Any] | None = None
    if requires_generated_cabal_completion:
        try:
            completion_document = _complete_haskell_generated_cabal_provenance(
                completed=completed,
                configured_runner=configured_runner,
                output=output,
                receipt=receipt,
                property_plan=property_plan,
                execution=execution,
                original_started_at=started,
                original_finished_at=finished,
                child_environment=child_environment,
                timeout_seconds=timeout_seconds,
                provenance_runner=provenance_runner,
            )
        except CoverageExecutionError as exc:
            failure_path = _persist_process_failure(
                candidate=candidate,
                receipt=receipt,
                command_sha256=command_sha256,
                runner_sha256=runner_sha256,
                started_at=started,
                finished_at=finished,
                exit_code=completed.returncode,
                failure_code="HASKELL_GENERATED_CABAL_COMPLETION_FAILED",
                stdout=completed.stdout,
                stderr=completed.stderr,
            )
            raise CoverageExecutionError(
                "HASKELL_GENERATED_CABAL_COMPLETION_FAILED:"
                f"evidence={failure_path.name}"
            ) from exc
    receipt_document: dict[str, Any] = {
        "schemaVersion": (
            "s1.4x-property-execution-receipt-v2"
            if completion_document is not None
            else "s1.4x-property-execution-receipt-v1"
        ),
        "candidate": candidate,
        "runner": {
            "sha256": runner_sha256,
            "commandArgvSha256": command_sha256,
        },
        "process": (
            completion_document["originalProcess"]
            if completion_document is not None
            else {
                "startedAt": started,
                "finishedAt": finished,
                "exitCode": completed.returncode,
                "stdoutSha256": _sha256_bytes(_process_bytes(completed.stdout)),
                "stderrSha256": _sha256_bytes(_process_bytes(completed.stderr)),
            }
        ),
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
    if completion_document is not None:
        receipt_document["completion"] = {
            "reason": completion_document["reason"],
            "process": completion_document["completion"],
            "artifact": completion_document["artifact"],
            "status": completion_document["status"],
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
    parser.add_argument("--prewarm-haskell-pantry", action="store_true")
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
            prewarm_haskell_pantry=arguments.prewarm_haskell_pantry,
        )
        print(json.dumps(receipt, allow_nan=False, sort_keys=True))
    except (CoverageError, CoverageExecutionError, OSError, ValueError) as exc:
        print(f"S1_4X_COVERAGE_EXECUTION_FAIL:{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
