#!/usr/bin/env python3
"""S1.4X Haskell correctness, qualification, selector evidence workflow."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
import re
import stat
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence


CORRECTNESS_SCHEMA_VERSION = "s1.4x-haskell-full-correctness-v1"
CORRECTNESS_PHASES = (
    "build",
    "test",
    "canonical-process",
    "canonical-compare",
    "semantic-process",
    "semantic-compare",
)
PROFILE_OPTIONS = {
    "baseline-o0-fasm": ("-O0", "-fasm"),
    "optimized-o2-fasm": ("-O2", "-fasm"),
}
PROFILE_MARKER_SCHEMA_VERSION = "s1.4x-haskell-profile-measurement-state-v1"
QUALIFICATION_SCHEMA_VERSION = "s1.4x-haskell-profile-qualification-v1"
FINAL_PROFILE_SCHEMA_VERSION = "s1.4x-haskell-selected-profile-v1"
PROFILE_MARKER_FIELDS = {
    "schemaVersion",
    "state",
    "planSha256",
    "selectorConfigSha256",
    "sourceTreeSha256",
    "orderBlock",
    "profileId",
    "ghcOptions",
    "optionsSha256",
    "qualificationCaseOrder",
    "hostValiditySha256",
    "markerPythonPath",
    "markerPythonSha256",
    "markerScriptPath",
    "markerScriptSha256",
    "markerArgv",
    "markerArgvSha256",
    "startedAt",
    "measurementEnteredAt",
    "preRunSha256",
}
PROFILE_ORDER_BLOCKS = (
    ("baseline-o0-fasm", "optimized-o2-fasm"),
    ("optimized-o2-fasm", "baseline-o0-fasm"),
    ("optimized-o2-fasm", "baseline-o0-fasm"),
    ("baseline-o0-fasm", "optimized-o2-fasm"),
)
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}")
FORBIDDEN_STACK_ENVIRONMENT = (
    "STACK_YAML",
    "STACK_ROOT",
    "STACK_OPTS",
    "STACK_CONFIG",
)


class WorkflowError(RuntimeError):
    """Workflow input이나 실행 결과가 frozen contract에서 벗어났을 때 발생한다."""


def canonical_json_bytes(value: Any, *, trailing_newline: bool = False) -> bytes:
    """Non-finite 값을 거부하는 sorted compact JSON bytes를 만든다."""

    payload = json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    if trailing_newline:
        payload += "\n"
    return payload.encode("utf-8")


def canonical_sha256(value: Any) -> str:
    """Canonical JSON의 SHA-256을 계산한다."""

    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def sha256_file(path: Path) -> str:
    """Regular non-symlink file의 SHA-256을 계산한다."""

    if path.is_symlink() or not path.is_file():
        raise WorkflowError(f"REGULAR_FILE_REQUIRED:{path}")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def strict_json_load(path: Path) -> Any:
    """중복 key와 non-finite number를 거부하며 JSON을 읽는다."""

    def object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise WorkflowError(f"DUPLICATE_JSON_KEY:{path}:{key}")
            result[key] = value
        return result

    def reject_constant(token: str) -> None:
        raise WorkflowError(f"NONFINITE_JSON_TOKEN:{path}:{token}")

    try:
        return json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=object_pairs,
            parse_constant=reject_constant,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise WorkflowError(f"INVALID_JSON:{path}") from exc


def atomic_write_json_exclusive(path: Path, value: Any) -> None:
    """새 path에만 canonical JSON을 원자적으로 발행한다."""

    if path.exists() or path.is_symlink():
        raise WorkflowError(f"OUTPUT_ALREADY_EXISTS:{path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(canonical_json_bytes(value, trailing_newline=True))
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError as exc:
            raise WorkflowError(f"OUTPUT_ALREADY_EXISTS:{path}") from exc
    finally:
        temporary.unlink(missing_ok=True)


def atomic_replace_json(path: Path, value: Any) -> None:
    """기존 regular file을 같은 directory의 canonical JSON으로 원자 교체한다."""

    if path.is_symlink() or not path.is_file():
        raise WorkflowError(f"REPLACE_TARGET_NOT_REGULAR:{path}")
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(canonical_json_bytes(value, trailing_newline=True))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory_descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        temporary.unlink(missing_ok=True)


def profile_options(profile_id: object) -> tuple[str, str]:
    """두 authoritative profile ID만 exact compiler option tuple로 변환한다."""

    if type(profile_id) is not str or profile_id not in PROFILE_OPTIONS:
        raise WorkflowError("PROFILE_ID_INVALID")
    return PROFILE_OPTIONS[profile_id]


def build_stack_command(
    *,
    ghcup: Path,
    stack: Path,
    stack_yaml: Path,
    stack_root: Path,
    ghc_version: str,
    operation: Sequence[str],
) -> list[str]:
    """GHCup offline resolver와 exact Stack project command를 조립한다."""

    if ghc_version not in {"9.10.3", "9.14.1"}:
        raise WorkflowError("GHC_VERSION_INVALID")
    if not operation or any(type(argument) is not str or not argument for argument in operation):
        raise WorkflowError("STACK_OPERATION_INVALID")
    return [
        str(ghcup),
        "--offline",
        "run",
        "--quick",
        "--ghc",
        ghc_version,
        "--stack",
        "3.11.1",
        "--",
        str(stack),
        "--stack-root",
        str(stack_root),
        "--stack-yaml",
        str(stack_yaml),
        "--no-terminal",
        "--color",
        "never",
        "--system-ghc",
        "--no-install-ghc",
        "--hpack-force",
        *operation,
    ]


def _require_sha256(value: object, *, label: str) -> str:
    if type(value) is not str or SHA256_PATTERN.fullmatch(value) is None:
        raise WorkflowError(f"SHA256_INVALID:{label}")
    return value


def _require_iso_utc(value: object, *, label: str) -> str:
    if type(value) is not str or re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z",
        value,
    ) is None:
        raise WorkflowError(f"UTC_TIMESTAMP_INVALID:{label}")
    return value


def build_profile_marker(
    *,
    plan_sha256: str,
    selector_config_sha256: str,
    source_tree_sha256: str,
    order_block: int,
    profile_id: str,
    case_order: Sequence[str],
    host_validity_sha256: str,
    marker_python_path: str,
    marker_python_sha256: str,
    marker_script_path: str,
    marker_script_sha256: str,
    marker_argv: Sequence[str],
    started_at: str,
) -> dict[str, Any]:
    """한 profile sub-block의 exact PRE_RUN marker object를 만든다."""

    _require_sha256(plan_sha256, label="plan")
    _require_sha256(selector_config_sha256, label="selector-config")
    _require_sha256(source_tree_sha256, label="source-tree")
    _require_sha256(host_validity_sha256, label="host-validity")
    _require_sha256(marker_python_sha256, label="marker-python")
    _require_sha256(marker_script_sha256, label="marker-script")
    _require_iso_utc(started_at, label="marker-started")
    options = profile_options(profile_id)
    if (
        type(order_block) is not int
        or order_block not in range(4)
        or len(case_order) != 7
        or len(set(case_order)) != 7
        or any(type(case_id) is not str or not case_id for case_id in case_order)
        or not marker_python_path.startswith("/")
        or not marker_script_path.startswith("/")
        or not marker_argv
        or any(type(argument) is not str or not argument for argument in marker_argv)
    ):
        raise WorkflowError("PROFILE_MARKER_INPUT_INVALID")
    marker_argv_list = list(marker_argv)
    return {
        "schemaVersion": PROFILE_MARKER_SCHEMA_VERSION,
        "state": "PRE_RUN",
        "planSha256": plan_sha256,
        "selectorConfigSha256": selector_config_sha256,
        "sourceTreeSha256": source_tree_sha256,
        "orderBlock": order_block,
        "profileId": profile_id,
        "ghcOptions": list(options),
        "optionsSha256": canonical_sha256(list(options)),
        "qualificationCaseOrder": list(case_order),
        "hostValiditySha256": host_validity_sha256,
        "markerPythonPath": marker_python_path,
        "markerPythonSha256": marker_python_sha256,
        "markerScriptPath": marker_script_path,
        "markerScriptSha256": marker_script_sha256,
        "markerArgv": marker_argv_list,
        "markerArgvSha256": canonical_sha256(marker_argv_list),
        "startedAt": started_at,
        "measurementEnteredAt": None,
        "preRunSha256": None,
    }


def _validate_profile_marker(document: object, *, state: str) -> dict[str, Any]:
    if (
        not isinstance(document, dict)
        or set(document) != PROFILE_MARKER_FIELDS
        or document.get("schemaVersion") != PROFILE_MARKER_SCHEMA_VERSION
        or document.get("state") != state
    ):
        raise WorkflowError("PROFILE_MARKER_EXACT_OBJECT_INVALID")
    profile_id = document.get("profileId")
    options = profile_options(profile_id)
    if (
        document.get("ghcOptions") != list(options)
        or document.get("optionsSha256") != canonical_sha256(list(options))
        or type(document.get("orderBlock")) is not int
        or document["orderBlock"] not in range(4)
        or not isinstance(document.get("qualificationCaseOrder"), list)
        or len(document["qualificationCaseOrder"]) != 7
        or len(set(document["qualificationCaseOrder"])) != 7
        or any(
            type(case_id) is not str or not case_id
            for case_id in document["qualificationCaseOrder"]
        )
    ):
        raise WorkflowError("PROFILE_MARKER_CONTRACT_INVALID")
    for field in (
        "planSha256",
        "selectorConfigSha256",
        "sourceTreeSha256",
        "hostValiditySha256",
        "markerPythonSha256",
        "markerScriptSha256",
        "markerArgvSha256",
    ):
        _require_sha256(document.get(field), label=f"marker-{field}")
    marker_argv = document.get("markerArgv")
    if (
        not isinstance(marker_argv, list)
        or not marker_argv
        or any(type(argument) is not str or not argument for argument in marker_argv)
        or document["markerArgvSha256"] != canonical_sha256(marker_argv)
        or document.get("markerPythonPath") != marker_argv[0]
        or len(marker_argv) != 5
        or marker_argv[1] != document.get("markerScriptPath")
        or marker_argv[2:] != [
            "mark-measurement-entered",
            "--qualification",
            marker_argv[4],
        ]
        or not marker_argv[4].startswith("/")
    ):
        raise WorkflowError("PROFILE_MARKER_ARGV_INVALID")
    _require_iso_utc(document.get("startedAt"), label="marker-started")
    if state == "PRE_RUN":
        if (
            document.get("measurementEnteredAt") is not None
            or document.get("preRunSha256") is not None
        ):
            raise WorkflowError("PROFILE_MARKER_NOT_PRE_RUN")
    elif state == "MEASUREMENT":
        _require_iso_utc(
            document.get("measurementEnteredAt"),
            label="measurement-entered",
        )
        _require_sha256(document.get("preRunSha256"), label="pre-run")
    else:
        raise WorkflowError("PROFILE_MARKER_STATE_INVALID")
    return document


def _same_fd_json_snapshot(path: Path) -> tuple[bytes, dict[str, Any], os.stat_result]:
    """O_NOFOLLOW FD 하나에서 bytes/hash/parse에 쓰는 동일 snapshot을 읽는다."""

    if not path.is_absolute():
        raise WorkflowError("PROFILE_MARKER_PATH_NOT_ABSOLUTE")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size > 1024 * 1024:
            raise WorkflowError("PROFILE_MARKER_FILE_INVALID")
        chunks: list[bytes] = []
        while chunk := os.read(descriptor, 64 * 1024):
            chunks.append(chunk)
        after = os.fstat(descriptor)
        if (
            before.st_dev != after.st_dev
            or before.st_ino != after.st_ino
            or before.st_size != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns
        ):
            raise WorkflowError("PROFILE_MARKER_CHANGED_DURING_READ")
    finally:
        os.close(descriptor)
    payload = b"".join(chunks)

    def object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise WorkflowError(f"PROFILE_MARKER_DUPLICATE_KEY:{key}")
            result[key] = value
        return result

    try:
        document = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=object_pairs,
            parse_constant=lambda token: (_ for _ in ()).throw(
                WorkflowError(f"PROFILE_MARKER_NONFINITE:{token}")
            ),
        )
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise WorkflowError("PROFILE_MARKER_JSON_INVALID") from exc
    if not isinstance(document, dict):
        raise WorkflowError("PROFILE_MARKER_JSON_INVALID")
    return payload, document, before


def mark_profile_measurement_entered(path: Path) -> dict[str, str]:
    """Exclusive lock 아래 exact PRE_RUN snapshot을 MEASUREMENT로 한 번만 전이한다."""

    lock_path = path.with_name(f"{path.name}.transition.lock")
    try:
        lock_descriptor = os.open(
            lock_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
    except FileExistsError as exc:
        raise WorkflowError("PROFILE_MARKER_TRANSITION_BUSY") from exc
    try:
        os.close(lock_descriptor)
        payload, raw_document, snapshot = _same_fd_json_snapshot(path)
        try:
            document = _validate_profile_marker(raw_document, state="PRE_RUN")
        except WorkflowError as exc:
            if isinstance(raw_document, dict) and raw_document.get("state") != "PRE_RUN":
                raise WorkflowError("PROFILE_MARKER_NOT_PRE_RUN") from exc
            raise
        current = os.stat(path, follow_symlinks=False)
        if (
            not stat.S_ISREG(current.st_mode)
            or current.st_dev != snapshot.st_dev
            or current.st_ino != snapshot.st_ino
            or current.st_size != snapshot.st_size
            or current.st_mtime_ns != snapshot.st_mtime_ns
        ):
            raise WorkflowError("PROFILE_MARKER_CHANGED_BEFORE_TRANSITION")
        pre_run_sha256 = hashlib.sha256(payload).hexdigest()
        transitioned = dict(document)
        transitioned["state"] = "MEASUREMENT"
        transitioned["measurementEnteredAt"] = _iso_now()
        transitioned["preRunSha256"] = pre_run_sha256
        _validate_profile_marker(transitioned, state="MEASUREMENT")
        atomic_replace_json(path, transitioned)
        return {
            "preRunSha256": pre_run_sha256,
            "measurementSha256": sha256_file(path),
            "status": "MEASUREMENT_ENTERED",
        }
    finally:
        lock_path.unlink(missing_ok=True)


def parse_criterion_qualification_reports(
    reports: object,
    *,
    expected_case_order: Sequence[str],
) -> dict[str, float]:
    """Criterion raw mean seconds를 exact 7-case order로 추출한다."""

    if (
        not isinstance(reports, list)
        or len(expected_case_order) != 7
        or len(set(expected_case_order)) != 7
    ):
        raise WorkflowError("CRITERION_QUALIFICATION_REPORT_SET_INVALID")
    parsed: dict[str, float] = {}
    for report in reports:
        if not isinstance(report, dict):
            raise WorkflowError("CRITERION_QUALIFICATION_REPORT_INVALID")
        name = report.get("reportName")
        analysis = report.get("reportAnalysis")
        mean = analysis.get("anMean") if isinstance(analysis, dict) else None
        estimate = mean.get("estPoint") if isinstance(mean, dict) else None
        if (
            type(name) is not str
            or name not in expected_case_order
            or name in parsed
            or type(estimate) is not float
            or not math.isfinite(estimate)
            or estimate <= 0.0
        ):
            raise WorkflowError("CRITERION_QUALIFICATION_REPORT_INVALID")
        parsed[name] = estimate
    if tuple(parsed) != tuple(expected_case_order):
        raise WorkflowError("CRITERION_QUALIFICATION_CASE_ORDER_INVALID")
    return parsed


def _geometric_mean(values: Sequence[float]) -> float:
    if not values or any(
        type(value) is not float or not math.isfinite(value) or value <= 0.0
        for value in values
    ):
        raise WorkflowError("PROFILE_RATIO_INVALID")
    return math.exp(math.fsum(math.log(value) for value in values) / len(values))


def select_profile_from_blocks(
    blocks: object,
    *,
    case_order: Sequence[str],
    profile_order_blocks: Sequence[Sequence[str]],
) -> dict[str, Any]:
    """Frozen four-block paired ratio selector를 exact order closure에서 재계산한다."""

    if (
        not isinstance(blocks, list)
        or len(blocks) != 4
        or tuple(tuple(block) for block in profile_order_blocks)
        != PROFILE_ORDER_BLOCKS
        or len(case_order) != 7
        or len(set(case_order)) != 7
    ):
        raise WorkflowError("PROFILE_QUALIFICATION_BLOCK_SET_INVALID")
    paired: list[float] = []
    per_case: dict[str, list[float]] = {case_id: [] for case_id in case_order}
    improving = 0
    for index, block in enumerate(blocks):
        if (
            not isinstance(block, dict)
            or set(block)
            != {
                "orderBlock",
                "plannedProfileOrder",
                "actualProfileOrder",
                "ratios",
            }
            or type(block["orderBlock"]) is not int
            or block["orderBlock"] != index
            or block["plannedProfileOrder"] != list(PROFILE_ORDER_BLOCKS[index])
            or block["actualProfileOrder"] != list(PROFILE_ORDER_BLOCKS[index])
            or not isinstance(block["ratios"], dict)
            or set(block["ratios"]) != set(case_order)
        ):
            raise WorkflowError("PROFILE_QUALIFICATION_ORDER_CLOSURE_INVALID")
        block_ratios: list[float] = []
        for case_id in case_order:
            ratio = block["ratios"][case_id]
            if (
                type(ratio) is not float
                or not math.isfinite(ratio)
                or ratio <= 0.0
            ):
                raise WorkflowError("PROFILE_RATIO_INVALID")
            paired.append(ratio)
            block_ratios.append(ratio)
            per_case[case_id].append(ratio)
        if _geometric_mean(block_ratios) < 1.0:
            improving += 1
    maxima = {case_id: max(values) for case_id, values in per_case.items()}
    aggregate = _geometric_mean(paired)
    optimized = (
        all(value <= 1.05 for value in maxima.values())
        and aggregate <= 0.97
        and improving >= 3
    )
    return {
        "profileId": (
            "optimized-o2-fasm" if optimized else "baseline-o0-fasm"
        ),
        "selectedBy": (
            "frozen-criterion-selector" if optimized else "proven-fallback"
        ),
        "pairedRatios": paired,
        "perCaseMaxima": maxima,
        "aggregateRatio": aggregate,
        "improvingOuterRepetitions": improving,
    }


def build_final_profile_document(
    *,
    selection: Mapping[str, Any],
    source_tree_sha256: str,
    full_correctness_sha256: str,
    qualification_plan_sha256: str,
    qualification_artifact_sha256: str,
    selector_config_sha256: str,
    compiler_sha256: str,
) -> dict[str, Any]:
    """Frozen selector와 선택된 correctness receipt를 final profile로 투영한다."""

    profile_id = selection.get("profileId")
    selected_by = selection.get("selectedBy")
    if selected_by not in {"frozen-criterion-selector", "proven-fallback"}:
        raise WorkflowError("PROFILE_SELECTION_IDENTITY_INVALID")
    options = profile_options(profile_id)
    for label, digest in (
        ("source-tree", source_tree_sha256),
        ("full-correctness", full_correctness_sha256),
        ("qualification-plan", qualification_plan_sha256),
        ("qualification-artifact", qualification_artifact_sha256),
        ("selector-config", selector_config_sha256),
        ("compiler", compiler_sha256),
    ):
        _require_sha256(digest, label=label)
    return {
        "schemaVersion": FINAL_PROFILE_SCHEMA_VERSION,
        "profileId": profile_id,
        "ghcOptions": list(options),
        "compilerVersion": "9.10.3",
        "compilerSha256": compiler_sha256,
        "sourceTreeSha256": source_tree_sha256,
        "optionsSha256": canonical_sha256(list(options)),
        "fullCorrectnessSha256": full_correctness_sha256,
        "qualificationPlanSha256": qualification_plan_sha256,
        "qualificationArtifactSha256": qualification_artifact_sha256,
        "selectorConfigSha256": selector_config_sha256,
        "fallbackProfile": "baseline-o0-fasm",
        "selectedBy": selected_by,
    }


def _iso_now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _absolute_regular(path: Path, *, label: str, executable: bool = False) -> Path:
    if (
        not path.is_absolute()
        or path.is_symlink()
        or not path.is_file()
        or path.resolve(strict=True) != path
        or (executable and not os.access(path, os.X_OK))
    ):
        raise WorkflowError(f"{label}_IDENTITY_INVALID")
    return path


def _absolute_existing_directory(path: Path, *, label: str) -> Path:
    if (
        not path.is_absolute()
        or path.is_symlink()
        or not path.is_dir()
        or path.resolve(strict=True) != path
    ):
        raise WorkflowError(f"{label}_DIRECTORY_INVALID")
    return path


def _reserve_directory(path: Path) -> Path:
    if not path.is_absolute():
        raise WorkflowError("OUTPUT_DIRECTORY_NOT_ABSOLUTE")
    parent = _absolute_existing_directory(path.parent, label="OUTPUT_PARENT")
    normalized = parent / path.name
    if normalized != path or path.exists() or path.is_symlink():
        raise WorkflowError("OUTPUT_DIRECTORY_NOT_NEW")
    path.mkdir(mode=0o700)
    return path


def _required_environment_path(name: str, *, executable: bool = True) -> Path:
    value = os.environ.get(name)
    if value is None:
        raise WorkflowError(f"REQUIRED_ENVIRONMENT_MISSING:{name}")
    return _absolute_regular(Path(value), label=name, executable=executable)


def _load_haskell_evidence(haskell_root: Path):
    module_path = _absolute_regular(
        haskell_root / "tools/haskell_evidence.py",
        label="HASKELL_EVIDENCE",
    )
    specification = importlib.util.spec_from_file_location(
        "s1_4x_haskell_evidence",
        module_path,
    )
    if specification is None or specification.loader is None:
        raise WorkflowError("HASKELL_EVIDENCE_IMPORT_FAILED")
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


def _repo_commit(repo_root: Path) -> str:
    completed = subprocess.run(
        ["/usr/bin/git", "-C", str(repo_root), "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    commit = completed.stdout.strip()
    if completed.returncode != 0 or COMMIT_PATTERN.fullmatch(commit) is None:
        raise WorkflowError("CANDIDATE_COMMIT_INVALID")
    dirty = subprocess.run(
        ["/usr/bin/git", "-C", str(repo_root), "status", "--porcelain=v1"],
        check=False,
        capture_output=True,
        text=True,
    )
    if dirty.returncode != 0 or dirty.stdout:
        raise WorkflowError("CANDIDATE_WORKTREE_NOT_CLEAN")
    return commit


def _sealed_child_environment(*, ghc_bin: Path, stack_bin: Path) -> dict[str, str]:
    home = os.environ.get("HOME")
    if home is None:
        raise WorkflowError("HOME_MISSING")
    home_path = _absolute_existing_directory(Path(home), label="HOME")
    environment = {
        "HOME": str(home_path),
        "PATH": (
            f"{ghc_bin.parent}:{stack_bin.parent}:"
            "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
        ),
        "LC_ALL": "C",
        "TZ": "UTC",
        "OMP_NUM_THREADS": "1",
        "OPENBLAS_NUM_THREADS": "1",
        "MKL_NUM_THREADS": "1",
        "NUMEXPR_NUM_THREADS": "1",
        "VECLIB_MAXIMUM_THREADS": "1",
        "BLIS_NUM_THREADS": "1",
    }
    return environment


def _run_logged(
    command: Sequence[str],
    *,
    cwd: Path,
    environment: Mapping[str, str],
    phase: str,
    output_directory: Path,
    expected_exit_codes: frozenset[int] = frozenset({0}),
) -> dict[str, Any]:
    if phase not in CORRECTNESS_PHASES and not phase.startswith(("qualification-", "oci-")):
        raise WorkflowError(f"COMMAND_PHASE_INVALID:{phase}")
    stdout_path = output_directory / f"{phase}.stdout"
    stderr_path = output_directory / f"{phase}.stderr"
    if stdout_path.exists() or stderr_path.exists():
        raise WorkflowError(f"COMMAND_LOG_ALREADY_EXISTS:{phase}")
    started_at = _iso_now()
    with stdout_path.open("xb") as standard_output, stderr_path.open("xb") as standard_error:
        completed = subprocess.run(
            list(command),
            cwd=cwd,
            env=dict(environment),
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=standard_output,
            stderr=standard_error,
        )
    finished_at = _iso_now()
    record = {
        "phase": phase,
        "argv": list(command),
        "argvSha256": canonical_sha256(list(command)),
        "startedAt": started_at,
        "finishedAt": finished_at,
        "exitCode": completed.returncode,
        "stdoutSha256": sha256_file(stdout_path),
        "stderrSha256": sha256_file(stderr_path),
    }
    if completed.returncode not in expected_exit_codes:
        raise WorkflowError(f"COMMAND_FAILED:{phase}:{completed.returncode}")
    return record


def _find_candidate_binary(haskell_root: Path, *, ghc_version: str) -> Path:
    candidates = sorted(
        (
            path.resolve(strict=True)
            for path in (haskell_root / ".stack-work/dist").glob(
                f"*/ghc-{ghc_version}/build/s1-4x-haskell/s1-4x-haskell"
            )
            if path.is_file() and not path.is_symlink() and os.access(path, os.X_OK)
        ),
        key=lambda path: str(path).encode(),
    )
    if len(candidates) != 1:
        raise WorkflowError(f"CANDIDATE_BINARY_CARDINALITY:{len(candidates)}")
    return candidates[0]


def _comparison_status(path: Path) -> dict[str, Any]:
    report = strict_json_load(path)
    if (
        not isinstance(report, dict)
        or report.get("schemaVersion") != "s1.4x-comparison-report-v1"
        or report.get("status") != "PASS"
        or report.get("mismatchCount") != 0
        or report.get("mismatches") != []
    ):
        raise WorkflowError(f"COMPARISON_NOT_PASS:{path}")
    return report


def _correctness(arguments: argparse.Namespace) -> None:
    output = _reserve_directory(arguments.output_dir)
    haskell_root = Path(__file__).resolve(strict=True).parent.parent
    numeric_root = haskell_root.parent
    repo_root = numeric_root.parents[3]
    profile_id = arguments.profile
    options = profile_options(profile_id)
    ghcup = _required_environment_path("S1_4X_GHCUP_BIN")
    stack = _required_environment_path("S1_4X_STACK_BIN")
    ghc = _required_environment_path("S1_4X_AUTHORITATIVE_GHC_BIN")
    cache_root_value = os.environ.get("S1_4X_CACHE_ROOT")
    if cache_root_value is None:
        raise WorkflowError("REQUIRED_ENVIRONMENT_MISSING:S1_4X_CACHE_ROOT")
    cache_root = _absolute_existing_directory(
        Path(cache_root_value),
        label="CACHE_ROOT",
    )
    stack_root = cache_root / f"stack-root-correctness-{profile_id}"
    if stack_root.exists() or stack_root.is_symlink():
        raise WorkflowError("CORRECTNESS_STACK_ROOT_ALREADY_EXISTS")
    stack_root.mkdir(mode=0o700)
    candidate_commit = _repo_commit(repo_root)
    evidence = _load_haskell_evidence(haskell_root)
    source_tree_sha256 = evidence.benchmark_source_tree_sha256(haskell_root)
    environment = _sealed_child_environment(ghc_bin=ghc, stack_bin=stack)
    stack_yaml = _absolute_regular(
        haskell_root / "stack.yaml",
        label="AUTHORITATIVE_STACK_YAML",
    )
    build = build_stack_command(
        ghcup=ghcup,
        stack=stack,
        stack_yaml=stack_yaml,
        stack_root=stack_root,
        ghc_version="9.10.3",
        operation=[
            "build",
            "--test",
            "--bench",
            "--no-run-tests",
            "--no-run-benchmarks",
            "--pedantic",
            f"--ghc-options={' '.join(options)}",
        ],
    )
    test = build_stack_command(
        ghcup=ghcup,
        stack=stack,
        stack_yaml=stack_yaml,
        stack_root=stack_root,
        ghc_version="9.10.3",
        operation=["test", "--pedantic", f"--ghc-options={' '.join(options)}"],
    )
    records = [
        _run_logged(
            build,
            cwd=haskell_root,
            environment=environment,
            phase="build",
            output_directory=output,
        ),
        _run_logged(
            test,
            cwd=haskell_root,
            environment=environment,
            phase="test",
            output_directory=output,
        ),
    ]
    candidate_binary = _find_candidate_binary(haskell_root, ghc_version="9.10.3")
    fixture_root = _absolute_existing_directory(
        numeric_root / "contract/fixtures",
        label="FIXTURE_ROOT",
    )
    compare_script = _absolute_regular(
        numeric_root / "oracle/compare_results.py",
        label="COMPARE_RESULTS",
    )
    python_bin = Path("/usr/bin/python3").resolve(strict=True)
    _absolute_regular(python_bin, label="PYTHON", executable=True)
    requests = (
        (
            "canonical",
            fixture_root / "small/canonical-inputs.v1.json",
            fixture_root / "expected/canonical-results.v1.json",
        ),
        (
            "semantic",
            fixture_root / "invalid/semantic-errors.v1.json",
            fixture_root / "invalid/semantic-errors.expected.v1.json",
        ),
    )
    comparison_artifacts: list[dict[str, Any]] = []
    for label, request, expected in requests:
        _absolute_regular(request, label=f"{label.upper()}_REQUEST")
        _absolute_regular(expected, label=f"{label.upper()}_EXPECTED")
        actual = output / f"{label}.actual.json"
        comparison = output / f"{label}.comparison.json"
        process_phase = f"{label}-process"
        compare_phase = f"{label}-compare"
        records.append(
            _run_logged(
                [
                    str(candidate_binary),
                    "--request",
                    str(request),
                    "--fixture-root",
                    str(fixture_root),
                    "--output",
                    str(actual),
                ],
                cwd=haskell_root,
                environment=environment,
                phase=process_phase,
                output_directory=output,
            )
        )
        _absolute_regular(actual, label=f"{label.upper()}_ACTUAL")
        records.append(
            _run_logged(
                [
                    str(python_bin),
                    str(compare_script),
                    "--expected",
                    str(expected),
                    "--actual",
                    str(actual),
                    "--request",
                    str(request),
                    "--output",
                    str(comparison),
                ],
                cwd=repo_root,
                environment=environment,
                phase=compare_phase,
                output_directory=output,
            )
        )
        _comparison_status(comparison)
        comparison_artifacts.append(
            {
                "matrixId": label,
                "requestSha256": sha256_file(request),
                "expectedSha256": sha256_file(expected),
                "actualSha256": sha256_file(actual),
                "comparisonSha256": sha256_file(comparison),
                "mismatchCount": 0,
                "status": "PASS",
            }
        )
    if tuple(record["phase"] for record in records) != CORRECTNESS_PHASES:
        raise WorkflowError("CORRECTNESS_PHASE_SEQUENCE_DRIFT")
    if evidence.benchmark_source_tree_sha256(haskell_root) != source_tree_sha256:
        raise WorkflowError("SOURCE_TREE_CHANGED_DURING_CORRECTNESS")
    if _repo_commit(repo_root) != candidate_commit:
        raise WorkflowError("CANDIDATE_COMMIT_CHANGED_DURING_CORRECTNESS")
    receipt = {
        "schemaVersion": CORRECTNESS_SCHEMA_VERSION,
        "status": "PASS",
        "profileId": profile_id,
        "ghcOptions": list(options),
        "optionsSha256": canonical_sha256(list(options)),
        "compilerVersion": "9.10.3",
        "compilerSha256": sha256_file(ghc),
        "candidateSourceCommit": candidate_commit,
        "sourceTreeSha256": source_tree_sha256,
        "candidateBinarySha256": sha256_file(candidate_binary),
        "stackYamlSha256": sha256_file(stack_yaml),
        "commands": records,
        "comparisonArtifacts": comparison_artifacts,
        "mismatchCount": 0,
    }
    receipt_path = output / "correctness-receipt.v1.json"
    atomic_write_json_exclusive(receipt_path, receipt)
    print(
        json.dumps(
            {
                "profileId": profile_id,
                "receiptPath": str(receipt_path),
                "receiptSha256": sha256_file(receipt_path),
                "status": "PASS",
            },
            sort_keys=True,
        )
    )


def _qualification_contract(plan: object) -> tuple[dict[str, Any], tuple[str, ...]]:
    if not isinstance(plan, dict):
        raise WorkflowError("QUALIFICATION_PLAN_INVALID")
    configuration = plan.get("haskellProfileQualification")
    if not isinstance(configuration, dict):
        raise WorkflowError("QUALIFICATION_CONFIG_MISSING")
    expected_fields = {
        "qualificationCaseIds",
        "qualificationCaseOrder",
        "profileOrderBlocks",
        "hostValidityBeforeEachProfileBlock",
        "criterionTimeLimitSeconds",
        "outerQualificationRepetitions",
        "ratioPairing",
        "perCaseCollapse",
        "aggregateFormula",
        "improvingBlockFormula",
        "perCaseMaxRegressionRatio",
        "aggregateMaxRatio",
        "minimumImprovingOuterRepetitions",
        "optimizedProfile",
        "fallbackProfile",
    }
    case_order = configuration.get("qualificationCaseOrder")
    if (
        set(configuration) != expected_fields
        or configuration.get("qualificationCaseIds") != case_order
        or not isinstance(case_order, list)
        or len(case_order) != 7
        or len(set(case_order)) != 7
        or configuration.get("profileOrderBlocks")
        != [list(block) for block in PROFILE_ORDER_BLOCKS]
        or configuration.get("hostValidityBeforeEachProfileBlock") is not True
        or configuration.get("criterionTimeLimitSeconds") != 3
        or configuration.get("outerQualificationRepetitions") != 4
        or configuration.get("ratioPairing") != "same-order-block-and-case"
        or configuration.get("perCaseCollapse") != "max-of-four-paired-ratios"
        or configuration.get("aggregateFormula")
        != "geometric-mean-of-all-28-paired-ratios"
        or configuration.get("improvingBlockFormula")
        != "geometric-mean-of-seven-case-ratios"
        or configuration.get("perCaseMaxRegressionRatio") != 1.05
        or configuration.get("aggregateMaxRatio") != 0.97
        or configuration.get("minimumImprovingOuterRepetitions") != 3
        or configuration.get("optimizedProfile") != "optimized-o2-fasm"
        or configuration.get("fallbackProfile") != "baseline-o0-fasm"
    ):
        raise WorkflowError("QUALIFICATION_CONFIG_DRIFT")
    return configuration, tuple(case_order)


def _host_validator_command(
    *,
    numeric_root: Path,
    plan: Mapping[str, Any],
    output: Path,
    root_pid: int,
    python_bin: Path,
) -> list[str]:
    execution = plan.get("execution")
    environment = plan.get("environmentValidity")
    if not isinstance(execution, dict) or not isinstance(environment, dict):
        raise WorkflowError("HOST_POLICY_MISSING")
    cpu_set = execution.get("cpuSet")
    if (
        not isinstance(cpu_set, list)
        or not cpu_set
        or any(type(cpu) is not int or cpu < 0 for cpu in cpu_set)
    ):
        raise WorkflowError("HOST_CPU_SET_INVALID")
    validator = _absolute_regular(
        numeric_root / "oracle/validate_environment.py",
        label="HOST_VALIDATOR",
    )
    return [
        str(python_bin),
        str(validator),
        "--home",
        str(_absolute_existing_directory(Path(os.environ["HOME"]), label="HOME")),
        "--cpu-set",
        ",".join(str(cpu) for cpu in cpu_set),
        "--min-home-free-bytes",
        "32212254720",
        "--min-available-memory-bytes",
        str(environment["minAvailableMemoryGiB"] * 1024**3),
        "--max-normalized-load1",
        str(environment["maxNormalizedLoad1"]),
        "--load-samples",
        str(environment["loadSampleCount"]),
        "--sample-interval-seconds",
        str(environment["loadSampleIntervalSeconds"]),
        "--max-quiet-wait-seconds",
        str(environment["maxQuietWaitSeconds"]),
        "--max-running-containers",
        str(environment["runningContainerCount"]),
        "--external-process-sample-seconds",
        "30",
        "--max-external-process-cpu-percent",
        str(environment["externalProcessCpuPercentThreshold"]),
        "--allowed-process-root-pid",
        str(root_pid),
        "--output",
        str(output),
    ]


def _validate_host_report(path: Path, *, plan: Mapping[str, Any], root_pid: int) -> None:
    report = strict_json_load(path)
    execution = plan["execution"]
    frozen = plan["environmentValidity"]
    expected_policy = {
        "cpu_set": execution["cpuSet"],
        "min_home_free_bytes": 32_212_254_720,
        "min_available_memory_bytes": frozen["minAvailableMemoryGiB"] * 1024**3,
        "max_normalized_load1": frozen["maxNormalizedLoad1"],
        "load_samples": frozen["loadSampleCount"],
        "sample_interval_seconds": frozen["loadSampleIntervalSeconds"],
        "max_quiet_wait_seconds": frozen["maxQuietWaitSeconds"],
        "max_running_containers": frozen["runningContainerCount"],
        "external_process_sample_seconds": 30.0,
        "max_external_process_cpu_percent": frozen[
            "externalProcessCpuPercentThreshold"
        ],
        "allowed_process_root_pid": root_pid,
    }
    if (
        not isinstance(report, dict)
        or report.get("schemaVersion") != "s1.4x-host-validity-v1"
        or report.get("status") != "PASS"
        or report.get("failureCount") != 0
        or report.get("policy") != expected_policy
        or not isinstance(report.get("checks"), list)
        or not report["checks"]
        or any(check.get("status") != "PASS" for check in report["checks"])
    ):
        raise WorkflowError("HOST_VALIDITY_NOT_PASS")


def _criterion_qualification_command(
    *,
    ghcup: Path,
    stack: Path,
    stack_yaml: Path,
    stack_root: Path,
    profile_id: str,
    time_limit_seconds: int,
    raw_report: Path,
    case_order: Sequence[str],
) -> list[str]:
    expression = "^(?:" + "|".join(re.escape(case_id) for case_id in case_order) + ")$"
    return build_stack_command(
        ghcup=ghcup,
        stack=stack,
        stack_yaml=stack_yaml,
        stack_root=stack_root,
        ghc_version="9.10.3",
        operation=[
            "bench",
            f"--ghc-options={' '.join(profile_options(profile_id))}",
            (
                "--benchmark-arguments="
                f"--time-limit {time_limit_seconds} "
                f"--json {raw_report} "
                f"--match pattern {expression} +RTS -N1 -RTS"
            ),
        ],
    )


def _qualification(arguments: argparse.Namespace) -> None:
    if (
        arguments.profiles != "baseline-o0-fasm,optimized-o2-fasm"
        or arguments.enforce_order_plan is not True
    ):
        raise WorkflowError("QUALIFICATION_CLI_CONTRACT_INVALID")
    output = _reserve_directory(arguments.output_dir)
    plan_path = _absolute_regular(arguments.plan, label="QUALIFICATION_PLAN")
    plan = strict_json_load(plan_path)
    configuration, case_order = _qualification_contract(plan)
    haskell_root = Path(__file__).resolve(strict=True).parent.parent
    numeric_root = haskell_root.parent
    repo_root = numeric_root.parents[3]
    candidate_commit = _repo_commit(repo_root)
    evidence = _load_haskell_evidence(haskell_root)
    source_tree_sha256 = evidence.benchmark_source_tree_sha256(haskell_root)
    selector_config_sha256 = canonical_sha256(configuration)
    plan_sha256 = sha256_file(plan_path)
    ghcup = _required_environment_path("S1_4X_GHCUP_BIN")
    stack = _required_environment_path("S1_4X_STACK_BIN")
    ghc = _required_environment_path("S1_4X_AUTHORITATIVE_GHC_BIN")
    marker_python = _required_environment_path("S1_4X_BENCHMARK_PYTHON_BIN")
    configured_python_sha256 = _require_sha256(
        os.environ.get("S1_4X_BENCHMARK_PYTHON_SHA256"),
        label="benchmark-python",
    )
    if sha256_file(marker_python) != configured_python_sha256:
        raise WorkflowError("BENCHMARK_PYTHON_SHA256_MISMATCH")
    marker_script = _absolute_regular(
        Path(__file__).resolve(strict=True),
        label="PROFILE_MARKER_SCRIPT",
    )
    marker_script_sha256 = sha256_file(marker_script)
    cache_value = os.environ.get("S1_4X_CACHE_ROOT")
    if cache_value is None:
        raise WorkflowError("REQUIRED_ENVIRONMENT_MISSING:S1_4X_CACHE_ROOT")
    cache_root = _absolute_existing_directory(Path(cache_value), label="CACHE_ROOT")
    stack_root = cache_root / "stack-root-profile-qualification"
    if stack_root.exists() or stack_root.is_symlink():
        raise WorkflowError("QUALIFICATION_STACK_ROOT_ALREADY_EXISTS")
    stack_root.mkdir(mode=0o700)
    environment = _sealed_child_environment(ghc_bin=ghc, stack_bin=stack)
    stack_yaml = _absolute_regular(
        haskell_root / "stack.yaml",
        label="AUTHORITATIVE_STACK_YAML",
    )
    cpu_set = set(plan["execution"]["cpuSet"])
    os.sched_setaffinity(0, cpu_set)
    if os.sched_getaffinity(0) != cpu_set:
        raise WorkflowError("QUALIFICATION_AFFINITY_MISMATCH")
    blocks: list[dict[str, Any]] = []
    for block_index, order in enumerate(PROFILE_ORDER_BLOCKS):
        profile_records: list[dict[str, Any]] = []
        estimates: dict[str, dict[str, float]] = {}
        for profile_id in order:
            prefix = f"block-{block_index + 1}-{profile_id}"
            host_report = output / f"{prefix}-host-validity.json"
            host_command = _host_validator_command(
                numeric_root=numeric_root,
                plan=plan,
                output=host_report,
                root_pid=os.getpid(),
                python_bin=marker_python,
            )
            host_record = _run_logged(
                host_command,
                cwd=repo_root,
                environment=environment,
                phase=f"qualification-{prefix}-host",
                output_directory=output,
            )
            _validate_host_report(host_report, plan=plan, root_pid=os.getpid())
            marker_path = output / f"{prefix}-measurement-state.json"
            marker_argv = [
                str(marker_python),
                str(marker_script),
                "mark-measurement-entered",
                "--qualification",
                str(marker_path),
            ]
            marker = build_profile_marker(
                plan_sha256=plan_sha256,
                selector_config_sha256=selector_config_sha256,
                source_tree_sha256=source_tree_sha256,
                order_block=block_index,
                profile_id=profile_id,
                case_order=case_order,
                host_validity_sha256=sha256_file(host_report),
                marker_python_path=str(marker_python),
                marker_python_sha256=configured_python_sha256,
                marker_script_path=str(marker_script),
                marker_script_sha256=marker_script_sha256,
                marker_argv=marker_argv,
                started_at=_iso_now(),
            )
            atomic_write_json_exclusive(marker_path, marker)
            pre_run_sha256 = sha256_file(marker_path)
            raw_report = output / f"{prefix}-criterion.json"
            criterion_command = _criterion_qualification_command(
                ghcup=ghcup,
                stack=stack,
                stack_yaml=stack_yaml,
                stack_root=stack_root,
                profile_id=profile_id,
                time_limit_seconds=configuration["criterionTimeLimitSeconds"],
                raw_report=raw_report,
                case_order=case_order,
            )
            profile_environment = dict(environment)
            profile_environment.update(
                {
                    "S1_4X_BENCHMARK_PLAN": str(plan_path),
                    "S1_4X_BENCHMARK_FIXTURE_ROOT": str(
                        numeric_root / "contract/fixtures"
                    ),
                    "S1_4X_BENCHMARK_QUALIFICATION": str(marker_path),
                    "S1_4X_BENCHMARK_MARKER_PYTHON": str(marker_python),
                    "S1_4X_BENCHMARK_MARKER_PYTHON_SHA256": (
                        configured_python_sha256
                    ),
                    "S1_4X_BENCHMARK_MARKER_SCRIPT": str(marker_script),
                    "S1_4X_BENCHMARK_MARKER_SCRIPT_SHA256": (
                        marker_script_sha256
                    ),
                }
            )
            started_at = _iso_now()
            criterion_record = _run_logged(
                criterion_command,
                cwd=haskell_root,
                environment=profile_environment,
                phase=f"qualification-{prefix}-criterion",
                output_directory=output,
            )
            finished_at = _iso_now()
            raw = strict_json_load(raw_report)
            case_estimates = parse_criterion_qualification_reports(
                raw,
                expected_case_order=case_order,
            )
            measurement_marker = _validate_profile_marker(
                strict_json_load(marker_path),
                state="MEASUREMENT",
            )
            if (
                measurement_marker["preRunSha256"] != pre_run_sha256
                or measurement_marker["markerScriptSha256"]
                != marker_script_sha256
                or measurement_marker["markerPythonSha256"]
                != configured_python_sha256
            ):
                raise WorkflowError("PROFILE_MARKER_TRANSITION_EVIDENCE_INVALID")
            estimates[profile_id] = case_estimates
            profile_records.append(
                {
                    "profileId": profile_id,
                    "ghcOptions": list(profile_options(profile_id)),
                    "optionsSha256": canonical_sha256(
                        list(profile_options(profile_id))
                    ),
                    "startedAt": started_at,
                    "finishedAt": finished_at,
                    "hostValidityPath": str(host_report),
                    "hostValiditySha256": sha256_file(host_report),
                    "hostCommand": host_record,
                    "rawCriterionPath": str(raw_report),
                    "rawCriterionSha256": sha256_file(raw_report),
                    "criterionCommand": criterion_record,
                    "caseSecondsPerBatch": case_estimates,
                    "marker": {
                        "path": str(marker_path),
                        "preRunSha256": pre_run_sha256,
                        "measurementSha256": sha256_file(marker_path),
                        "pythonPath": str(marker_python),
                        "pythonSha256": configured_python_sha256,
                        "scriptPath": str(marker_script),
                        "scriptSha256": marker_script_sha256,
                        "argv": marker_argv,
                        "argvSha256": canonical_sha256(marker_argv),
                    },
                }
            )
        ratios = {
            case_id: (
                estimates["optimized-o2-fasm"][case_id]
                / estimates["baseline-o0-fasm"][case_id]
            )
            for case_id in case_order
        }
        blocks.append(
            {
                "orderBlock": block_index,
                "plannedProfileOrder": list(order),
                "actualProfileOrder": [
                    record["profileId"] for record in profile_records
                ],
                "profiles": profile_records,
                "ratios": ratios,
            }
        )
    selector_blocks = [
        {
            "orderBlock": block["orderBlock"],
            "plannedProfileOrder": block["plannedProfileOrder"],
            "actualProfileOrder": block["actualProfileOrder"],
            "ratios": block["ratios"],
        }
        for block in blocks
    ]
    selection = select_profile_from_blocks(
        selector_blocks,
        case_order=case_order,
        profile_order_blocks=PROFILE_ORDER_BLOCKS,
    )
    if evidence.benchmark_source_tree_sha256(haskell_root) != source_tree_sha256:
        raise WorkflowError("SOURCE_TREE_CHANGED_DURING_QUALIFICATION")
    if _repo_commit(repo_root) != candidate_commit:
        raise WorkflowError("CANDIDATE_COMMIT_CHANGED_DURING_QUALIFICATION")
    artifact = {
        "schemaVersion": QUALIFICATION_SCHEMA_VERSION,
        "status": "PASS",
        "candidateSourceCommit": candidate_commit,
        "planPathId": "S1_4X_BENCHMARK_PLAN",
        "planSha256": plan_sha256,
        "selectorConfigSha256": selector_config_sha256,
        "sourceTreeSha256": source_tree_sha256,
        "qualificationCaseOrder": list(case_order),
        "plannedProfileOrderBlocks": [
            list(block) for block in PROFILE_ORDER_BLOCKS
        ],
        "blocks": blocks,
        "selection": selection,
    }
    artifact_path = output / "qualification-artifact.v1.json"
    atomic_write_json_exclusive(artifact_path, artifact)
    print(
        json.dumps(
            {
                "profileId": selection["profileId"],
                "qualificationArtifactPath": str(artifact_path),
                "qualificationArtifactSha256": sha256_file(artifact_path),
                "status": "PASS",
            },
            sort_keys=True,
        )
    )


def _validate_correctness_receipt(
    path: Path,
    *,
    expected_profile_id: str,
    expected_source_tree_sha256: str,
    expected_commit: str,
) -> dict[str, Any]:
    receipt = strict_json_load(_absolute_regular(path, label="CORRECTNESS_RECEIPT"))
    options = profile_options(expected_profile_id)
    if (
        not isinstance(receipt, dict)
        or receipt.get("schemaVersion") != CORRECTNESS_SCHEMA_VERSION
        or receipt.get("status") != "PASS"
        or receipt.get("profileId") != expected_profile_id
        or receipt.get("ghcOptions") != list(options)
        or receipt.get("optionsSha256") != canonical_sha256(list(options))
        or receipt.get("sourceTreeSha256") != expected_source_tree_sha256
        or receipt.get("candidateSourceCommit") != expected_commit
        or receipt.get("compilerVersion") != "9.10.3"
        or receipt.get("mismatchCount") != 0
        or not isinstance(receipt.get("commands"), list)
        or tuple(command.get("phase") for command in receipt["commands"])
        != CORRECTNESS_PHASES
        or any(command.get("exitCode") != 0 for command in receipt["commands"])
    ):
        raise WorkflowError(f"CORRECTNESS_RECEIPT_INVALID:{expected_profile_id}")
    _require_sha256(receipt.get("compilerSha256"), label="correctness-compiler")
    return receipt


def _validate_qualification_artifact(
    path: Path,
    *,
    plan: Mapping[str, Any],
    expected_source_tree_sha256: str,
    expected_commit: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    artifact = strict_json_load(_absolute_regular(path, label="QUALIFICATION_ARTIFACT"))
    configuration, case_order = _qualification_contract(plan)
    if (
        not isinstance(artifact, dict)
        or artifact.get("schemaVersion") != QUALIFICATION_SCHEMA_VERSION
        or artifact.get("status") != "PASS"
        or artifact.get("candidateSourceCommit") != expected_commit
        or artifact.get("sourceTreeSha256") != expected_source_tree_sha256
        or artifact.get("qualificationCaseOrder") != list(case_order)
        or artifact.get("plannedProfileOrderBlocks")
        != [list(block) for block in PROFILE_ORDER_BLOCKS]
        or artifact.get("selectorConfigSha256")
        != canonical_sha256(configuration)
        or not isinstance(artifact.get("blocks"), list)
    ):
        raise WorkflowError("QUALIFICATION_ARTIFACT_INVALID")
    selector_blocks: list[dict[str, Any]] = []
    for index, block in enumerate(artifact["blocks"]):
        if (
            not isinstance(block, dict)
            or set(block)
            != {
                "orderBlock",
                "plannedProfileOrder",
                "actualProfileOrder",
                "profiles",
                "ratios",
            }
            or not isinstance(block.get("profiles"), list)
            or len(block["profiles"]) != 2
        ):
            raise WorkflowError("QUALIFICATION_ARTIFACT_BLOCK_INVALID")
        for profile in block["profiles"]:
            marker = profile.get("marker") if isinstance(profile, dict) else None
            if (
                not isinstance(marker, dict)
                or set(marker)
                != {
                    "path",
                    "preRunSha256",
                    "measurementSha256",
                    "pythonPath",
                    "pythonSha256",
                    "scriptPath",
                    "scriptSha256",
                    "argv",
                    "argvSha256",
                }
                or marker.get("argvSha256")
                != canonical_sha256(marker.get("argv"))
            ):
                raise WorkflowError("QUALIFICATION_MARKER_EVIDENCE_INVALID")
            for field in (
                "preRunSha256",
                "measurementSha256",
                "pythonSha256",
                "scriptSha256",
                "argvSha256",
            ):
                _require_sha256(marker.get(field), label=f"qualification-marker-{field}")
        selector_blocks.append(
            {
                "orderBlock": block["orderBlock"],
                "plannedProfileOrder": block["plannedProfileOrder"],
                "actualProfileOrder": block["actualProfileOrder"],
                "ratios": block["ratios"],
            }
        )
    selection = select_profile_from_blocks(
        selector_blocks,
        case_order=case_order,
        profile_order_blocks=PROFILE_ORDER_BLOCKS,
    )
    if artifact.get("selection") != selection:
        raise WorkflowError("QUALIFICATION_SELECTION_DRIFT")
    return artifact, selection


def _select_profile(arguments: argparse.Namespace) -> None:
    haskell_root = Path(__file__).resolve(strict=True).parent.parent
    numeric_root = haskell_root.parent
    repo_root = numeric_root.parents[3]
    candidate_commit = _repo_commit(repo_root)
    evidence = _load_haskell_evidence(haskell_root)
    source_tree_sha256 = evidence.benchmark_source_tree_sha256(haskell_root)
    plan_path = _absolute_regular(
        numeric_root / "benchmarks/benchmark-plan.v1.json",
        label="QUALIFICATION_PLAN",
    )
    plan = strict_json_load(plan_path)
    configuration, _ = _qualification_contract(plan)
    environment_paths = {}
    for name in (
        "S1_4X_HASKELL_BASELINE_CORRECTNESS",
        "S1_4X_HASKELL_OPTIMIZED_CORRECTNESS",
        "S1_4X_HASKELL_QUALIFICATION_ARTIFACT",
    ):
        value = os.environ.get(name)
        if value is None:
            raise WorkflowError(f"REQUIRED_ENVIRONMENT_MISSING:{name}")
        environment_paths[name] = Path(value)
    baseline = _validate_correctness_receipt(
        environment_paths["S1_4X_HASKELL_BASELINE_CORRECTNESS"],
        expected_profile_id="baseline-o0-fasm",
        expected_source_tree_sha256=source_tree_sha256,
        expected_commit=candidate_commit,
    )
    optimized = _validate_correctness_receipt(
        environment_paths["S1_4X_HASKELL_OPTIMIZED_CORRECTNESS"],
        expected_profile_id="optimized-o2-fasm",
        expected_source_tree_sha256=source_tree_sha256,
        expected_commit=candidate_commit,
    )
    qualification_path = environment_paths[
        "S1_4X_HASKELL_QUALIFICATION_ARTIFACT"
    ]
    qualification, selection = _validate_qualification_artifact(
        qualification_path,
        plan=plan,
        expected_source_tree_sha256=source_tree_sha256,
        expected_commit=candidate_commit,
    )
    if qualification.get("planSha256") != sha256_file(plan_path):
        raise WorkflowError("QUALIFICATION_PLAN_SHA256_DRIFT")
    selected_correctness_path = (
        environment_paths["S1_4X_HASKELL_OPTIMIZED_CORRECTNESS"]
        if selection["profileId"] == "optimized-o2-fasm"
        else environment_paths["S1_4X_HASKELL_BASELINE_CORRECTNESS"]
    )
    selected_receipt = optimized if selection["profileId"] == "optimized-o2-fasm" else baseline
    profile_document = build_final_profile_document(
        selection=selection,
        source_tree_sha256=source_tree_sha256,
        full_correctness_sha256=sha256_file(selected_correctness_path),
        qualification_plan_sha256=sha256_file(plan_path),
        qualification_artifact_sha256=sha256_file(qualification_path),
        selector_config_sha256=canonical_sha256(configuration),
        compiler_sha256=selected_receipt["compilerSha256"],
    )
    profile_path = haskell_root / "selected-profile.v1.json"
    manifest_path = haskell_root / "source-inputs.v1.json"
    if arguments.mode == "materialize":
        pending = strict_json_load(profile_path)
        if pending.get("schemaVersion") != "s1.4x-haskell-selected-profile-pending-v1":
            raise WorkflowError("SELECTED_PROFILE_NOT_PENDING")
        evidence.validate_selected_profile_document(
            pending,
            expected_compiler_sha256=evidence.AUTHORITATIVE_GHC_SHA256,
            expected_source_tree_sha256=source_tree_sha256,
            expected_qualification_plan_sha256=sha256_file(plan_path),
            expected_selector_config_sha256=canonical_sha256(configuration),
        )
        lock_path = profile_path.with_name(f"{profile_path.name}.materialize.lock")
        try:
            descriptor = os.open(
                lock_path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
        except FileExistsError as exc:
            raise WorkflowError("SELECTED_PROFILE_MATERIALIZATION_BUSY") from exc
        try:
            os.close(descriptor)
            atomic_replace_json(profile_path, profile_document)
            manifest = evidence.build_source_manifest(haskell_root)
            atomic_replace_json(manifest_path, manifest)
        finally:
            lock_path.unlink(missing_ok=True)
    else:
        actual = strict_json_load(profile_path)
        if (
            actual != profile_document
            or profile_path.read_bytes()
            != canonical_json_bytes(profile_document, trailing_newline=True)
        ):
            raise WorkflowError("SELECTED_PROFILE_CHECK_FAILED")
        evidence.validate_source_manifest(haskell_root, manifest_path)
    print(
        json.dumps(
            {
                "mode": arguments.mode,
                "profileId": profile_document["profileId"],
                "profileSha256": sha256_file(profile_path),
                "status": "PASS",
            },
            sort_keys=True,
        )
    )


def _mark_measurement(arguments: argparse.Namespace) -> None:
    result = mark_profile_measurement_entered(arguments.qualification)
    if result["status"] != "MEASUREMENT_ENTERED":
        raise WorkflowError("PROFILE_MARKER_TRANSITION_FAILED")
    print(json.dumps({"status": "MEASUREMENT_ENTERED"}))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    correctness = commands.add_parser("correctness")
    correctness.add_argument("--profile", required=True)
    correctness.add_argument("--output-dir", type=Path, required=True)
    correctness.set_defaults(handler=_correctness)
    qualification = commands.add_parser("qualification")
    qualification.add_argument("--plan", type=Path, required=True)
    qualification.add_argument("--profiles", required=True)
    qualification.add_argument("--enforce-order-plan", action="store_true")
    qualification.add_argument("--output-dir", type=Path, required=True)
    qualification.set_defaults(handler=_qualification)
    marker = commands.add_parser("mark-measurement-entered")
    marker.add_argument("--qualification", type=Path, required=True)
    marker.set_defaults(handler=_mark_measurement)
    selector = commands.add_parser("select-profile")
    selector.add_argument(
        "--mode",
        required=True,
        choices=("materialize", "check"),
    )
    selector.set_defaults(handler=_select_profile)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        arguments.handler(arguments)
    except (
        WorkflowError,
        OSError,
        UnicodeError,
        ValueError,
        subprocess.SubprocessError,
    ) as exc:
        print(f"HASKELL_PROFILE_WORKFLOW_FAIL:{exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
