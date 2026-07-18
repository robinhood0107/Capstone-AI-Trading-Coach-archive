#!/usr/bin/env python3
"""Frozen Haskell Criterion family를 실행하고 shared native evidence pipeline에 연결한다."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence


class BlockError(RuntimeError):
    """Benchmark block의 frozen 입력, 실행 또는 shared evidence가 유효하지 않음."""


SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
RAW_RELATIVE = "raw/criterion-family.json"
RECEIPT_RELATIVE = "receipts/criterion-family.json"
LEDGER_RELATIVE = "input-ledger.json"
NATIVE_CONTRACT_RELATIVE = "native-contract-validation.json"
NATIVE_STATISTICS_RELATIVE = "native-statistics.json"
NATIVE_RELATIVE = "native.json"
BLOCK_RESULT_RELATIVE = "block-result.json"
RUNTIME_IDENTITY_RELATIVE = "benchmark-runtime-identity.json"
THREAD_ENVIRONMENT = {
    "OMP_NUM_THREADS": "1",
    "OPENBLAS_NUM_THREADS": "1",
    "MKL_NUM_THREADS": "1",
    "VECLIB_MAXIMUM_THREADS": "1",
    "NUMEXPR_NUM_THREADS": "1",
    "JAX_NUM_THREADS": "1",
    "XLA_FLAGS": "--xla_cpu_multi_thread_eigen=false intra_op_parallelism_threads=1",
    "S1_4X_THREAD_COUNT": "1",
}


def sha256_file(path: Path) -> str:
    """Regular non-symlink file의 bytes를 SHA-256으로 고정한다."""

    if path.is_symlink() or not path.is_file():
        raise BlockError(f"UNSAFE_OR_MISSING_FILE:{path}")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_sha256(value: Any) -> str:
    """중첩 evidence object의 canonical JSON SHA-256을 계산한다."""

    try:
        payload = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    except (TypeError, ValueError) as exc:
        raise BlockError("NON_CANONICAL_HASH_INPUT") from exc
    return hashlib.sha256(payload).hexdigest()


def _strict_json_decode(payload: str, *, label: str) -> Any:
    """Duplicate key와 비유한 숫자를 거부하며 JSON text를 읽는다."""

    def object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise BlockError(f"DUPLICATE_JSON_KEY:{label}:{key}")
            result[key] = value
        return result

    try:
        return json.loads(
            payload,
            object_pairs_hook=object_pairs,
            parse_constant=lambda token: (_ for _ in ()).throw(
                BlockError(f"NONFINITE_JSON_TOKEN:{label}:{token}")
            ),
        )
    except json.JSONDecodeError as exc:
        raise BlockError(f"INVALID_JSON:{label}") from exc


def strict_json_load(path: Path) -> Any:
    """Regular JSON file을 strict decoder로 읽는다."""

    try:
        return _strict_json_decode(path.read_text(encoding="utf-8"), label=str(path))
    except (OSError, UnicodeError) as exc:
        raise BlockError(f"INVALID_JSON:{path}") from exc


def build_stack_benchmark_command(
    *,
    ghcup_bin: Path,
    stack_bin: Path,
    stack_yaml: Path,
    profile_options: Sequence[str],
    time_limit_seconds: int,
    native_report: Path,
    criterion_prefix: str,
) -> list[str]:
    """GHCup offline resolver와 exact Stack/Criterion argv를 구성한다."""

    if list(profile_options) not in (["-O0", "-fasm"], ["-O2", "-fasm"]):
        raise BlockError("INVALID_SELECTED_PROFILE_OPTIONS")
    if (
        not isinstance(time_limit_seconds, int)
        or isinstance(time_limit_seconds, bool)
        or time_limit_seconds != 5
        or not criterion_prefix
        or any(character.isspace() for character in criterion_prefix)
    ):
        raise BlockError("INVALID_CRITERION_COMMAND_INPUT")
    criterion_arguments = (
        f"--time-limit {time_limit_seconds} --json {native_report} "
        f"--match prefix {criterion_prefix} +RTS -N1 -RTS"
    )
    return [
        str(ghcup_bin),
        "--offline",
        "run",
        "--quick",
        "--ghc",
        "9.10.3",
        "--stack",
        "3.11.1",
        "--",
        str(stack_bin),
        "--stack-yaml",
        str(stack_yaml),
        "--no-terminal",
        "--color",
        "never",
        "--system-ghc",
        "--no-install-ghc",
        "bench",
        f"--ghc-options={' '.join(profile_options)}",
        f"--benchmark-arguments={criterion_arguments}",
    ]


def _require_absolute_regular(path: Path, *, label: str) -> Path:
    if (
        not path.is_absolute()
        or path.is_symlink()
        or not path.is_file()
        or path.resolve(strict=True) != path
    ):
        raise BlockError(f"{label}_NOT_CANONICAL_REGULAR_FILE")
    return path


def _require_absolute_directory(path: Path, *, label: str) -> Path:
    if (
        not path.is_absolute()
        or path.is_symlink()
        or not path.is_dir()
        or path.resolve(strict=True) != path
    ):
        raise BlockError(f"{label}_NOT_CANONICAL_DIRECTORY")
    return path


def _required_environment(name: str) -> str:
    value = os.environ.get(name)
    if value is None or not value:
        raise BlockError(f"REQUIRED_ENVIRONMENT_MISSING:{name}")
    return value


def validate_runtime_identity(
    path: Path,
    *,
    selector_id: str,
) -> tuple[Path, str]:
    """Benchmark process가 self-report한 executable exact-object를 검증한다."""

    _require_absolute_regular(path, label="BENCHMARK_RUNTIME_IDENTITY")
    document = strict_json_load(path)
    expected_fields = {
        "schemaVersion",
        "boundaryId",
        "selectorId",
        "executedBenchmarkPath",
        "executedBenchmarkSha256",
        "status",
    }
    if (
        not isinstance(document, dict)
        or set(document) != expected_fields
        or document.get("schemaVersion")
        != "s1.4x-haskell-benchmark-runtime-identity-v1"
        or document.get("boundaryId") != "haskell"
        or document.get("selectorId") != selector_id
        or document.get("status") != "PASS"
    ):
        raise BlockError("BENCHMARK_RUNTIME_IDENTITY_INVALID")
    executed = _require_absolute_regular(
        Path(str(document["executedBenchmarkPath"])),
        label="EXECUTED_BENCHMARK",
    )
    expected_sha256 = document["executedBenchmarkSha256"]
    if (
        not isinstance(expected_sha256, str)
        or SHA256_PATTERN.fullmatch(expected_sha256) is None
        or sha256_file(executed) != expected_sha256
    ):
        raise BlockError("EXECUTED_BENCHMARK_SHA256_MISMATCH")
    return executed, expected_sha256


def _verified_environment_executable(
    path_name: str,
    sha_name: str,
    *,
    label: str,
) -> tuple[Path, str]:
    path = _require_absolute_regular(
        Path(_required_environment(path_name)),
        label=label,
    )
    if not os.access(path, os.X_OK):
        raise BlockError(f"{label}_NOT_EXECUTABLE")
    expected_sha256 = _required_environment(sha_name)
    if SHA256_PATTERN.fullmatch(expected_sha256) is None:
        raise BlockError(f"{label}_EXPECTED_SHA256_INVALID")
    actual_sha256 = sha256_file(path)
    if actual_sha256 != expected_sha256:
        raise BlockError(f"{label}_SHA256_MISMATCH")
    return path, actual_sha256


def _import_repo_modules(
    *,
    haskell_root: Path,
    numeric_root: Path,
) -> tuple[Any, Any, Any]:
    for directory in (
        haskell_root / "tools",
        numeric_root / "benchmarks",
        numeric_root / "integration",
    ):
        value = str(directory)
        if value not in sys.path:
            sys.path.insert(0, value)
    try:
        import gate  # type: ignore[import-not-found]
        import haskell_evidence  # type: ignore[import-not-found]
        import validate_benchmark_report  # type: ignore[import-not-found]
    except ImportError as exc:
        raise BlockError("BENCHMARK_VALIDATOR_IMPORT_FAILED") from exc
    return haskell_evidence, validate_benchmark_report, gate


def _selector_and_cases(
    plan: Mapping[str, Any],
    *,
    selector_id: str,
    family_id: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    selector = next(
        (
            item
            for item in plan["familySelectors"]
            if item["selectorId"] == selector_id
        ),
        None,
    )
    if (
        not isinstance(selector, dict)
        or selector.get("boundaryId") != "haskell"
        or selector.get("familyId") != family_id
        or selector.get("criterionMatchMode") != "prefix"
        or selector.get("criterionPrefix") != f"{family_id}/"
    ):
        raise BlockError("SELECTOR_IDENTITY_MISMATCH")
    by_id = {item["caseId"]: item for item in plan["cases"]}
    try:
        cases = [by_id[case_id] for case_id in selector["expectedCaseIds"]]
    except (KeyError, TypeError) as exc:
        raise BlockError("SELECTOR_CASE_CLOSURE_INVALID") from exc
    if (
        not 2 <= len(cases) <= 45
        or [case["familyId"] for case in cases] != [family_id] * len(cases)
    ):
        raise BlockError("SELECTOR_FAMILY_CLOSURE_INVALID")
    return selector, cases


def _selector_input_closure(
    plan: Mapping[str, Any],
    selector: Mapping[str, Any],
    cases: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    return {
        "boundaryId": "haskell",
        "familyId": selector["familyId"],
        "selectorId": selector["selectorId"],
        "expectedCaseIds": selector["expectedCaseIds"],
        "expectedCaseCount": len(cases),
        "inputClosureSha256": canonical_sha256(
            {
                "fixtureFreezeIdentity": plan["fixtureFreezeIdentity"],
                "selector": selector,
                "cases": list(cases),
            }
        ),
    }


def _validate_qualification(
    *,
    path: Path,
    plan: Mapping[str, Any],
    plan_sha256: str,
    selector: Mapping[str, Any],
    cases: Sequence[Mapping[str, Any]],
    rotation_id: str,
    outer_repetition: int,
    run_id: str,
    benchmark_subject_commit: str,
    block_dir: Path,
) -> dict[str, Any]:
    _require_absolute_regular(path, label="QUALIFICATION")
    if path.parent != block_dir or path.name != "timeout-qualification.json":
        raise BlockError("QUALIFICATION_PATH_MISMATCH")
    qualification = strict_json_load(path)
    exact_fields = {
        "schemaVersion",
        "phase",
        "measurementEntered",
        "plan",
        "subject",
        "run",
        "hostValidity",
        "selectorInputClosure",
        "command",
    }
    if (
        not isinstance(qualification, dict)
        or set(qualification) != exact_fields
        or qualification["schemaVersion"] != "s1.4x-timeout-qualification-v1"
        or qualification["phase"] != "PRE_RUN"
        or qualification["measurementEntered"] is not False
    ):
        raise BlockError("INVALID_PRE_RUN_QUALIFICATION_STATE")
    if qualification["plan"] != {
        "planId": plan["planId"],
        "sha256": plan_sha256,
    }:
        raise BlockError("QUALIFICATION_PLAN_MISMATCH")
    if qualification["subject"] != {
        "benchmarkSubjectCommit": benchmark_subject_commit,
        "candidateSourceCommit": benchmark_subject_commit,
    }:
        raise BlockError("QUALIFICATION_SUBJECT_MISMATCH")
    timeout = plan["execution"]["familyBlockTimeoutSeconds"][selector["selectorId"]]
    if qualification["run"] != {
        "runId": run_id,
        "rotationId": rotation_id,
        "outerRepetition": outer_repetition,
        "timeoutSeconds": timeout,
    }:
        raise BlockError("QUALIFICATION_RUN_MISMATCH")
    if qualification["selectorInputClosure"] != _selector_input_closure(
        plan,
        selector,
        cases,
    ):
        raise BlockError("QUALIFICATION_SELECTOR_CLOSURE_MISMATCH")
    host_validity = qualification["hostValidity"]
    if (
        not isinstance(host_validity, dict)
        or set(host_validity)
        != {
            "artifactPath",
            "sha256",
            "status",
            "policySha256",
            "portableHostIdSha256",
        }
        or host_validity["artifactPath"] != "host-validity.json"
        or host_validity["status"] != "PASS"
        or any(
            SHA256_PATTERN.fullmatch(str(host_validity[field])) is None
            for field in ("sha256", "policySha256", "portableHostIdSha256")
        )
    ):
        raise BlockError("QUALIFICATION_HOST_VALIDITY_INVALID")
    if sha256_file(block_dir / host_validity["artifactPath"]) != host_validity["sha256"]:
        raise BlockError("QUALIFICATION_HOST_VALIDITY_SHA256_MISMATCH")
    command = qualification["command"]
    if (
        not isinstance(command, dict)
        or set(command)
        != {
            "commandManifestSha256",
            "allowedExecutable",
            "renderedArgvSha256",
        }
        or SHA256_PATTERN.fullmatch(str(command["commandManifestSha256"])) is None
        or SHA256_PATTERN.fullmatch(str(command["renderedArgvSha256"])) is None
        or not isinstance(command["allowedExecutable"], dict)
        or set(command["allowedExecutable"]) != {"path", "resolvedPath", "sha256"}
        or SHA256_PATTERN.fullmatch(
            str(command["allowedExecutable"]["sha256"])
        )
        is None
    ):
        raise BlockError("QUALIFICATION_COMMAND_INVALID")
    return qualification


def _validate_measurement_qualification(
    *,
    path: Path,
    pre_run: Mapping[str, Any],
) -> None:
    actual = strict_json_load(path)
    expected = dict(pre_run)
    expected["phase"] = "MEASUREMENT"
    expected["measurementEntered"] = True
    if actual != expected:
        raise BlockError("INVALID_MEASUREMENT_QUALIFICATION")


def _profile_and_source_evidence(
    *,
    haskell_root: Path,
    plan_path: Path,
    haskell_evidence: Any,
) -> tuple[dict[str, Any], Path, Path]:
    profile_path = _require_absolute_regular(
        haskell_root / "selected-profile.v1.json",
        label="SELECTED_PROFILE",
    )
    manifest_path = _require_absolute_regular(
        haskell_root / "source-inputs.v1.json",
        label="SOURCE_INPUT_MANIFEST",
    )
    plan = strict_json_load(plan_path)
    profile = strict_json_load(profile_path)
    if profile.get("schemaVersion") != "s1.4x-haskell-selected-profile-v1":
        raise BlockError("FINAL_SELECTED_PROFILE_REQUIRED")
    haskell_evidence.validate_selected_profile_document(
        profile,
        expected_compiler_sha256=haskell_evidence.AUTHORITATIVE_GHC_SHA256,
        expected_source_tree_sha256=haskell_evidence.benchmark_source_tree_sha256(
            haskell_root
        ),
        expected_qualification_plan_sha256=sha256_file(plan_path),
        expected_selector_config_sha256=canonical_sha256(
            plan["haskellProfileQualification"]
        ),
    )
    haskell_evidence.validate_source_manifest(haskell_root, manifest_path)
    return profile, profile_path, manifest_path


def _verify_subject_commit(repo_root: Path, expected: str) -> None:
    if COMMIT_PATTERN.fullmatch(expected) is None:
        raise BlockError("BENCHMARK_SUBJECT_COMMIT_INVALID")
    completed = subprocess.run(
        ["/usr/bin/git", "-C", str(repo_root), "rev-parse", "--verify", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if completed.returncode != 0 or completed.stdout.strip() != expected:
        raise BlockError("BENCHMARK_SUBJECT_COMMIT_MISMATCH")


def _run_toolchain_assertion(haskell_root: Path) -> None:
    completed = subprocess.run(
        [str(haskell_root / "tools/assert-toolchain.sh")],
        cwd=haskell_root,
        check=False,
        stdin=subprocess.DEVNULL,
        timeout=120,
    )
    if completed.returncode != 0:
        raise BlockError(f"TOOLCHAIN_ASSERTION_FAILED:{completed.returncode}")


def _find_benchmark_artifact(haskell_root: Path) -> Path:
    candidates = sorted(
        (
            path
            for path in (haskell_root / ".stack-work/dist").glob(
                "*/ghc-9.10.3/build/"
                "s1-4x-haskell-benchmark/s1-4x-haskell-benchmark"
            )
            if path.is_file() and not path.is_symlink() and os.access(path, os.X_OK)
        ),
        key=lambda path: str(path).encode(),
    )
    if len(candidates) != 1:
        raise BlockError(f"BENCHMARK_ARTIFACT_COUNT_INVALID:{len(candidates)}")
    return candidates[0].resolve(strict=True)


def _iso_now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _validate_rotation(
    *,
    rotation_id: str,
    outer_repetition_text: str,
) -> int:
    if (
        not outer_repetition_text.isascii()
        or not outer_repetition_text.isdigit()
        or outer_repetition_text.startswith("0")
    ):
        raise BlockError("OUTER_REPETITION_INVALID")
    outer_repetition = int(outer_repetition_text)
    if outer_repetition not in (1, 2, 3) or rotation_id != f"R{outer_repetition}":
        raise BlockError("ROTATION_REPETITION_MISMATCH")
    return outer_repetition


def _run_shared_json_command(
    command: Sequence[str],
    *,
    label: str,
    timeout: int = 300,
) -> dict[str, Any]:
    completed = subprocess.run(
        list(command),
        check=False,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if completed.returncode != 0:
        raise BlockError(f"{label}_FAILED:{completed.returncode}")
    if completed.stderr:
        raise BlockError(f"{label}_UNEXPECTED_STDERR")
    document = _strict_json_decode(completed.stdout, label=label)
    if not isinstance(document, dict):
        raise BlockError(f"{label}_OUTPUT_INVALID")
    return document


def _require_sha_fields(document: Mapping[str, Any], fields: Sequence[str]) -> None:
    if any(SHA256_PATTERN.fullmatch(str(document.get(field))) is None for field in fields):
        raise BlockError("SHARED_OUTPUT_SHA256_INVALID")


def run_block(arguments: argparse.Namespace) -> dict[str, Any]:
    """한 family의 preflight, Criterion 실행, shared evidence 발행을 수행한다."""

    repo_root = _require_absolute_directory(arguments.repo_root, label="REPO_ROOT")
    numeric_root = (
        repo_root
        / "workspaces"
        / "decision-platform"
        / "research"
        / "s1-4x-numeric-parity"
    )
    haskell_root = _require_absolute_directory(
        numeric_root / "haskell",
        label="HASKELL_ROOT",
    )
    integration_root = _require_absolute_directory(
        numeric_root / "integration",
        label="INTEGRATION_ROOT",
    )
    expected_plan = _require_absolute_regular(
        numeric_root / "benchmarks/benchmark-plan.v1.json",
        label="FROZEN_PLAN",
    )
    plan_path = _require_absolute_regular(arguments.plan, label="PLAN")
    if plan_path != expected_plan:
        raise BlockError("PLAN_PATH_MISMATCH")
    block_dir = _require_absolute_directory(arguments.block_dir, label="BLOCK_DIR")
    qualification_path = _require_absolute_regular(
        arguments.qualification,
        label="QUALIFICATION",
    )
    if arguments.boundary != "haskell":
        raise BlockError("BOUNDARY_MISMATCH")
    if arguments.selector != f"haskell/{arguments.family}":
        raise BlockError("SELECTOR_FAMILY_MISMATCH")
    if RUN_ID_PATTERN.fullmatch(arguments.run_id) is None:
        raise BlockError("RUN_ID_INVALID")
    outer_repetition = _validate_rotation(
        rotation_id=arguments.rotation,
        outer_repetition_text=arguments.outer_repetition,
    )
    expected_tail = (
        Path(arguments.run_id)
        / arguments.rotation
        / "haskell"
        / arguments.family
    )
    if tuple(block_dir.parts[-len(expected_tail.parts) :]) != expected_tail.parts:
        raise BlockError("BLOCK_DIRECTORY_LAYOUT_MISMATCH")

    raw_path = block_dir / RAW_RELATIVE
    receipt_path = block_dir / RECEIPT_RELATIVE
    input_ledger_path = block_dir / LEDGER_RELATIVE
    native_contract_path = block_dir / NATIVE_CONTRACT_RELATIVE
    native_statistics_path = block_dir / NATIVE_STATISTICS_RELATIVE
    native_path = block_dir / NATIVE_RELATIVE
    result_path = block_dir / BLOCK_RESULT_RELATIVE
    runtime_identity_path = block_dir / RUNTIME_IDENTITY_RELATIVE
    output_paths = (
        raw_path,
        receipt_path,
        input_ledger_path,
        native_contract_path,
        native_statistics_path,
        native_path,
        result_path,
        runtime_identity_path,
        raw_path.parent,
        receipt_path.parent,
    )
    if any(path.exists() or path.is_symlink() for path in output_paths):
        raise BlockError("BENCHMARK_OUTPUT_ALREADY_EXISTS")

    _verify_subject_commit(repo_root, arguments.benchmark_subject_commit)
    _run_toolchain_assertion(haskell_root)
    haskell_evidence, report_validator, gate = _import_repo_modules(
        haskell_root=haskell_root,
        numeric_root=numeric_root,
    )
    plan = report_validator.validate_plan(plan_path)
    if not isinstance(plan, dict):
        raise BlockError("PLAN_VALIDATOR_RETURNED_NON_OBJECT")
    expected_affinity = plan["execution"]["cpuSet"]
    actual_affinity = sorted(os.sched_getaffinity(0))
    if actual_affinity != expected_affinity:
        raise BlockError(f"ACTUAL_CPU_AFFINITY_MISMATCH:{actual_affinity}")
    selector, cases = _selector_and_cases(
        plan,
        selector_id=arguments.selector,
        family_id=arguments.family,
    )
    qualification = _validate_qualification(
        path=qualification_path,
        plan=plan,
        plan_sha256=sha256_file(plan_path),
        selector=selector,
        cases=cases,
        rotation_id=arguments.rotation,
        outer_repetition=outer_repetition,
        run_id=arguments.run_id,
        benchmark_subject_commit=arguments.benchmark_subject_commit,
        block_dir=block_dir,
    )
    profile, profile_path, source_manifest_path = _profile_and_source_evidence(
        haskell_root=haskell_root,
        plan_path=plan_path,
        haskell_evidence=haskell_evidence,
    )
    toolchain_lock_path = _require_absolute_regular(
        haskell_root / "toolchain-lock.v1.json",
        label="TOOLCHAIN_LOCK",
    )
    merged_provenance_path = _require_absolute_regular(
        numeric_root / "contract/toolchain-provenance.v1.json",
        label="TOOLCHAIN_PROVENANCE",
    )
    stack_yaml_path = _require_absolute_regular(
        haskell_root / "stack.yaml",
        label="STACK_YAML",
    )

    ghcup_bin, ghcup_sha256 = _verified_environment_executable(
        "S1_4X_GHCUP_BIN",
        "S1_4X_GHCUP_SHA256",
        label="GHCUP",
    )
    stack_bin, stack_sha256 = _verified_environment_executable(
        "S1_4X_STACK_BIN",
        "S1_4X_STACK_SHA256",
        label="STACK",
    )
    authoritative_ghc, authoritative_ghc_sha256 = _verified_environment_executable(
        "S1_4X_AUTHORITATIVE_GHC_BIN",
        "S1_4X_AUTHORITATIVE_GHC_SHA256",
        label="AUTHORITATIVE_GHC",
    )
    marker_python, marker_python_sha256 = _verified_environment_executable(
        "S1_4X_BENCHMARK_PYTHON_BIN",
        "S1_4X_BENCHMARK_PYTHON_SHA256",
        label="MARKER_PYTHON",
    )
    if Path(sys.executable).resolve(strict=True) != marker_python:
        raise BlockError("HELPER_PYTHON_IDENTITY_MISMATCH")
    marker_script = _require_absolute_regular(
        numeric_root / "benchmarks/run_rotated_blocks.py",
        label="MARKER_SCRIPT",
    )
    marker_script_sha256 = sha256_file(marker_script)
    marker_argv = [
        str(marker_python),
        str(marker_script),
        "mark-measurement-entered",
        "--qualification",
        str(qualification_path),
    ]
    ledger_script = _require_absolute_regular(
        integration_root / "benchmark_input_ledger.py",
        label="BENCHMARK_INPUT_LEDGER_SCRIPT",
    )
    native_script = _require_absolute_regular(
        integration_root / "native_benchmark_block.py",
        label="NATIVE_BENCHMARK_BLOCK_SCRIPT",
    )

    ledger_result = _run_shared_json_command(
        [
            str(marker_python),
            str(ledger_script),
            "--repo-root",
            str(repo_root),
            "--plan",
            str(plan_path),
            "--boundary",
            "haskell",
            "--selector",
            arguments.selector,
            "--output",
            str(input_ledger_path),
        ],
        label="BENCHMARK_INPUT_LEDGER",
    )
    if ledger_result != {
        "boundaryId": "haskell",
        "selectorId": arguments.selector,
        "status": "PASS",
    }:
        raise BlockError("BENCHMARK_INPUT_LEDGER_OUTPUT_INVALID")
    raw_path.parent.mkdir(mode=0o700)
    receipt_path.parent.mkdir(mode=0o700)

    command = build_stack_benchmark_command(
        ghcup_bin=ghcup_bin,
        stack_bin=stack_bin,
        stack_yaml=stack_yaml_path,
        profile_options=profile["ghcOptions"],
        time_limit_seconds=plan["execution"]["criterionTimeLimitSeconds"],
        native_report=raw_path,
        criterion_prefix=selector["criterionPrefix"],
    )
    environment = dict(os.environ)
    environment.update(THREAD_ENVIRONMENT)
    environment.update(
        {
            "S1_4X_BENCHMARK_PLAN": str(plan_path),
            "S1_4X_BENCHMARK_FIXTURE_ROOT": str(
                numeric_root / "contract/fixtures"
            ),
            "S1_4X_BENCHMARK_QUALIFICATION": str(qualification_path),
            "S1_4X_BENCHMARK_SELECTOR_ID": arguments.selector,
            "S1_4X_BENCHMARK_RUNTIME_IDENTITY": str(runtime_identity_path),
            "S1_4X_BENCHMARK_MARKER_PYTHON": str(marker_python),
            "S1_4X_BENCHMARK_MARKER_PYTHON_SHA256": marker_python_sha256,
            "S1_4X_BENCHMARK_MARKER_SCRIPT": str(marker_script),
            "S1_4X_BENCHMARK_MARKER_SCRIPT_SHA256": marker_script_sha256,
        }
    )
    started_at = _iso_now()
    completed = subprocess.run(
        command,
        cwd=haskell_root,
        env=environment,
        check=False,
        stdin=subprocess.DEVNULL,
    )
    finished_at = _iso_now()
    if completed.returncode != 0:
        raise BlockError(f"INNER_BENCHMARK_FAILED:{completed.returncode}")
    _validate_measurement_qualification(
        path=qualification_path,
        pre_run=qualification,
    )
    if (
        sha256_file(marker_python) != marker_python_sha256
        or sha256_file(marker_script) != marker_script_sha256
    ):
        raise BlockError("MARKER_IDENTITY_CHANGED_DURING_RUN")
    _require_absolute_regular(raw_path, label="CRITERION_FAMILY_RAW")
    executed_benchmark, executed_benchmark_sha256 = validate_runtime_identity(
        runtime_identity_path,
        selector_id=arguments.selector,
    )
    artifact = _find_benchmark_artifact(haskell_root)
    if (
        artifact != executed_benchmark
        or sha256_file(artifact) != executed_benchmark_sha256
    ):
        raise BlockError("BENCHMARK_ARTIFACT_RUNTIME_IDENTITY_MISMATCH")
    fixture_root = _require_absolute_directory(
        numeric_root / "contract/fixtures",
        label="FIXTURE_ROOT",
    )
    receipt = {
        "schemaVersion": "s1.4x-native-case-execution-receipt-v1",
        "boundaryId": "haskell",
        "selectorId": arguments.selector,
        "caseId": None,
        "commandArgv": command,
        "environment": {"S1_4X_BENCHMARK_SELECTOR_ID": arguments.selector},
        "exitCode": 0,
        "rawEvidencePath": RAW_RELATIVE,
        "rawEvidenceSha256": sha256_file(raw_path),
        "provenance": {
            "planPath": str(plan_path),
            "planSha256": sha256_file(plan_path),
            "fixtureRootPath": str(fixture_root),
            "fixtureFreezeIdentitySha256": canonical_sha256(
                plan["fixtureFreezeIdentity"]
            ),
            "inputLedgerPath": str(input_ledger_path),
            "inputLedgerSha256": sha256_file(input_ledger_path),
            "selectorId": arguments.selector,
            "caseIds": selector["expectedCaseIds"],
            "benchmarkExecutablePath": str(executed_benchmark),
            "benchmarkExecutableSha256": executed_benchmark_sha256,
            "effectiveRuntimeArgumentsSha256": profile["optionsSha256"],
            "candidateProvenance": {
                "kind": "haskell",
                "selectedProfilePath": str(profile_path),
                "selectedProfileSha256": sha256_file(profile_path),
                "selectedProfileId": profile["profileId"],
                "sourceInputManifestPath": str(source_manifest_path),
                "sourceInputManifestSha256": sha256_file(source_manifest_path),
                "effectiveCompilerFlagsSha256": profile["optionsSha256"],
                "runtimeIdentityPath": str(runtime_identity_path),
                "runtimeIdentitySha256": sha256_file(runtime_identity_path),
                "executedBenchmarkPath": str(executed_benchmark),
                "executedBenchmarkSha256": executed_benchmark_sha256,
                "authoritativeGhcPath": str(authoritative_ghc),
                "authoritativeGhcSha256": authoritative_ghc_sha256,
                "markerPythonPath": str(marker_python),
                "markerPythonSha256": marker_python_sha256,
                "markerScriptPath": str(marker_script),
                "markerScriptSha256": marker_script_sha256,
                "markerArgv": marker_argv,
                "markerArgvSha256": canonical_sha256(marker_argv),
                "ghcupPath": str(ghcup_bin),
                "ghcupSha256": ghcup_sha256,
                "stackPath": str(stack_bin),
                "stackSha256": stack_sha256,
                "stackYamlPath": str(stack_yaml_path),
                "stackYamlSha256": sha256_file(stack_yaml_path),
                "selectedGhcOptions": profile["ghcOptions"],
                "toolchainLockPath": str(toolchain_lock_path),
                "toolchainLockSha256": sha256_file(toolchain_lock_path),
                "mergedToolchainProvenancePath": str(merged_provenance_path),
                "mergedToolchainProvenanceSha256": sha256_file(
                    merged_provenance_path
                ),
            },
        },
        "status": "PASS",
    }
    gate.exclusive_json_write(receipt_path, receipt)

    producer_result = _run_shared_json_command(
        [
            str(marker_python),
            str(native_script),
            "produce-haskell-native",
            "--repo-root",
            str(repo_root),
            "--plan",
            str(plan_path),
            "--block-dir",
            str(block_dir),
            "--selector",
            arguments.selector,
            "--criterion-raw",
            str(raw_path),
            "--execution-receipt",
            str(receipt_path),
            "--input-ledger",
            str(input_ledger_path),
            "--fixture-root",
            str(fixture_root),
            "--selected-profile",
            str(profile_path),
            "--source-input-manifest",
            str(source_manifest_path),
            "--toolchain-lock",
            str(toolchain_lock_path),
            "--toolchain-provenance",
            str(merged_provenance_path),
            "--benchmark-artifact",
            str(artifact),
            "--started-at",
            started_at,
            "--finished-at",
            finished_at,
        ],
        label="HASKELL_NATIVE_PRODUCER",
    )
    if (
        set(producer_result)
        != {
            "boundaryId",
            "selectorId",
            "caseCount",
            "nativeContractValidationSha256",
            "nativeReportSha256",
            "nativeStatisticsSha256",
            "status",
        }
        or producer_result["boundaryId"] != "haskell"
        or producer_result["selectorId"] != arguments.selector
        or producer_result["caseCount"] != len(cases)
        or producer_result["status"] != "PASS"
    ):
        raise BlockError("HASKELL_NATIVE_PRODUCER_OUTPUT_INVALID")
    _require_sha_fields(
        producer_result,
        (
            "nativeContractValidationSha256",
            "nativeReportSha256",
            "nativeStatisticsSha256",
        ),
    )

    block_result = _run_shared_json_command(
        [
            str(marker_python),
            str(native_script),
            "--repo-root",
            str(repo_root),
            "--plan",
            str(plan_path),
            "--block-dir",
            str(block_dir),
            "--qualification",
            str(qualification_path),
            "--boundary",
            "haskell",
            "--selector",
            arguments.selector,
            "--family",
            arguments.family,
            "--rotation",
            arguments.rotation,
            "--outer-repetition",
            str(outer_repetition),
            "--run-id",
            arguments.run_id,
            "--benchmark-subject-commit",
            arguments.benchmark_subject_commit,
        ],
        label="NATIVE_BENCHMARK_BLOCK",
    )
    if (
        set(block_result)
        != {"boundaryId", "selectorId", "blockResultSha256", "status"}
        or block_result["boundaryId"] != "haskell"
        or block_result["selectorId"] != arguments.selector
        or block_result["status"] != "PASS"
    ):
        raise BlockError("NATIVE_BENCHMARK_BLOCK_OUTPUT_INVALID")
    _require_sha_fields(block_result, ("blockResultSha256",))
    report_validator.validate_block_result(
        result_path,
        plan_path=plan_path,
        native_report_path=native_path,
        expected_boundary_id="haskell",
        expected_selector_id=arguments.selector,
    )
    return {
        "status": "PASS",
        "selectorId": arguments.selector,
        "caseCount": len(cases),
        "receiptSha256": sha256_file(receipt_path),
        "nativeReportSha256": sha256_file(native_path),
        "blockResultSha256": sha256_file(result_path),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--block-dir", type=Path, required=True)
    parser.add_argument("--qualification", type=Path, required=True)
    parser.add_argument("--boundary", required=True)
    parser.add_argument("--selector", required=True)
    parser.add_argument("--family", required=True)
    parser.add_argument("--rotation", required=True)
    parser.add_argument("--outer-repetition", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--benchmark-subject-commit", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        result = run_block(arguments)
    except (
        BlockError,
        OSError,
        KeyError,
        TypeError,
        ValueError,
        subprocess.SubprocessError,
    ) as exc:
        print(f"HASKELL_BENCHMARK_BLOCK_FAIL:{exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, allow_nan=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
