#!/usr/bin/env python3
"""S1.4X oracle/Scala/Haskell process와 typed comparator를 fail-closed로 연결한다."""

from __future__ import annotations

import json
import math
import os
import re
import subprocess
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

Runner = Callable[..., subprocess.CompletedProcess[bytes]]

REQUEST_ID = re.compile(r"^[a-z0-9](?:[a-z0-9._:-]{0,126}[a-z0-9])?$")
IMPLEMENTATION_ID = re.compile(r"^[a-z0-9][a-z0-9._:+-]{0,127}$")
FIELD_ID = re.compile(r"^[a-zA-Z][a-zA-Z0-9_.-]{0,63}$")
FUNCTION_IDS = {
    "simple_returns",
    "log_returns",
    "cumulative_return",
    "cagr",
    "realized_volatility",
    "annualized_volatility",
    "max_drawdown",
    "sharpe_ratio",
    "sortino_ratio",
    "historical_var",
    "historical_cvar",
    "historical_expected_shortfall",
    "realized_variance",
    "realized_volatility_intraday",
    "lo_adjusted_sharpe_ratio",
    "probabilistic_sharpe_ratio",
    "deflated_sharpe_ratio",
    "kupiec_unconditional_coverage_test",
    "christoffersen_independence_test",
    "christoffersen_conditional_coverage_test",
}
ERROR_CODES = {
    "input_type_invalid",
    "input_shape_invalid",
    "input_empty",
    "input_too_short",
    "input_too_long",
    "input_bool_invalid",
    "input_complex_invalid",
    "input_non_finite",
    "prices_non_positive",
    "equity_initial_non_positive",
    "equity_negative",
    "simple_return_below_minus_one",
    "periods_per_year_invalid",
    "risk_free_rate_invalid",
    "target_return_invalid",
    "confidence_invalid",
    "denominator_zero",
    "tail_empty",
    "result_non_finite",
    "research_input_invalid",
    "research_input_too_short",
    "aggregation_periods_invalid",
    "moment_invalid",
    "trial_count_invalid",
    "trial_variance_invalid",
    "trial_provenance_invalid",
    "significance_invalid",
    "forecast_shape_invalid",
    "forecast_var_negative",
    "insufficient_sample",
    "likelihood_invalid",
    "research_result_non_finite",
}
TRANSPORT_EXIT_BY_CODE = {
    "request_invalid": 64,
    "manifest_invalid": 65,
    "binary_invalid": 65,
    "internal_error": 70,
}


class GateError(ValueError):
    """교차 언어 gate가 수락할 수 없는 계약 위반을 나타낸다."""


def _reject_constant(token: str) -> Any:
    raise GateError(f"NON_FINITE_JSON:{token}")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise GateError(f"DUPLICATE_JSON_KEY:{key}")
        result[key] = value
    return result


def strict_json_load(source: bytes | str | Path) -> Any:
    """UTF-8·decoded-key uniqueness·finite number를 보존하며 JSON 하나만 읽는다."""

    if isinstance(source, Path):
        if source.is_symlink() or not source.is_file():
            raise GateError(f"JSON_NOT_REGULAR_FILE:{source.name}")
        payload = source.read_bytes()
    elif isinstance(source, bytes):
        payload = source
    else:
        payload = source.encode("utf-8")
    try:
        text = payload.decode("utf-8", errors="strict")
        return json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except UnicodeDecodeError as exc:
        raise GateError("INVALID_UTF8") from exc
    except json.JSONDecodeError as exc:
        raise GateError(f"INVALID_JSON:{exc.msg}") from exc


def _require_exact_fields(
    value: Mapping[str, Any],
    required: set[str],
    optional: set[str],
    *,
    error: str,
) -> None:
    actual = set(value)
    if not required.issubset(actual) or not actual.issubset(required | optional):
        raise GateError(f"{error}:fields={sorted(actual)}")


def validate_request_envelope(value: Any) -> dict[str, Any]:
    """Candidate 실행 전에 공통 envelope와 exact case identity를 확인한다."""

    if not isinstance(value, dict):
        raise GateError("REQUEST_NOT_OBJECT")
    _require_exact_fields(
        value,
        {"schemaVersion", "requestId", "cases"},
        set(),
        error="REQUEST_FIELDS_INVALID",
    )
    request_id = value["requestId"]
    cases = value["cases"]
    if value["schemaVersion"] != "s1.4x-request-v1":
        raise GateError("REQUEST_VERSION_INVALID")
    if not isinstance(request_id, str) or REQUEST_ID.fullmatch(request_id) is None:
        raise GateError("REQUEST_ID_INVALID")
    if not isinstance(cases, list) or not 1 <= len(cases) <= 4096:
        raise GateError("REQUEST_CASE_COUNT_INVALID")
    seen: set[str] = set()
    for index, case in enumerate(cases):
        if not isinstance(case, dict):
            raise GateError(f"REQUEST_CASE_NOT_OBJECT:{index}")
        _require_exact_fields(
            case,
            {"fixtureId", "functionId", "arguments"},
            {"expectedSemanticError", "toleranceClass"},
            error=f"REQUEST_CASE_FIELDS_INVALID:{index}",
        )
        fixture_id = case["fixtureId"]
        if (
            not isinstance(fixture_id, str)
            or REQUEST_ID.fullmatch(fixture_id) is None
            or fixture_id in seen
        ):
            raise GateError(f"REQUEST_FIXTURE_ID_INVALID:{index}")
        seen.add(fixture_id)
        if case["functionId"] not in FUNCTION_IDS:
            raise GateError(f"REQUEST_FUNCTION_ID_INVALID:{fixture_id}")
        if not isinstance(case["arguments"], dict) or not case["arguments"]:
            raise GateError(f"REQUEST_ARGUMENTS_INVALID:{fixture_id}")
    return value


def _validate_finite_and_normalized(value: Any, *, path: str) -> None:
    if isinstance(value, bool) or value is None or isinstance(value, (str, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise GateError(f"NON_FINITE_RESULT:{path}")
        if value == 0.0 and math.copysign(1.0, value) < 0.0:
            raise GateError(f"NEGATIVE_ZERO:{path}")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_finite_and_normalized(item, path=f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            _validate_finite_and_normalized(item, path=f"{path}.{key}")
        return
    raise GateError(f"UNSUPPORTED_RESULT_VALUE:{path}")


def validate_result_batch(
    value: Any,
    request: Mapping[str, Any],
    *,
    label: str,
) -> dict[str, Any]:
    """Candidate batch의 schema-independent invariants와 request 순서를 확인한다."""

    if not isinstance(value, dict):
        raise GateError(f"RESULT_BATCH_NOT_OBJECT:{label}")
    _require_exact_fields(
        value,
        {"schemaVersion", "requestId", "implementation", "results"},
        set(),
        error=f"RESULT_BATCH_FIELDS_INVALID:{label}",
    )
    if value["schemaVersion"] != "s1.4x-result-batch-v1":
        raise GateError(f"RESULT_BATCH_VERSION_INVALID:{label}")
    if value["requestId"] != request.get("requestId"):
        raise GateError(f"RESULT_REQUEST_ID_MISMATCH:{label}")
    implementation = value["implementation"]
    if (
        not isinstance(implementation, str)
        or IMPLEMENTATION_ID.fullmatch(implementation) is None
    ):
        raise GateError(f"RESULT_IMPLEMENTATION_INVALID:{label}")
    results = value["results"]
    cases = request.get("cases")
    if not isinstance(results, list) or not isinstance(cases, list):
        raise GateError(f"RESULTS_NOT_ARRAY:{label}")
    if len(results) != len(cases):
        raise GateError(f"RESULT_CASE_COUNT_MISMATCH:{label}")
    for index, (result, case) in enumerate(zip(results, cases, strict=True)):
        if not isinstance(result, dict) or not isinstance(case, dict):
            raise GateError(f"RESULT_CASE_NOT_OBJECT:{label}:{index}")
        common = {"schemaVersion", "functionId", "fixtureId", "status"}
        if result.get("status") == "ok":
            _require_exact_fields(
                result,
                common | {"values"},
                set(),
                error=f"RESULT_STATUS_SHAPE_INVALID:{label}:{index}",
            )
        elif result.get("status") == "error":
            _require_exact_fields(
                result,
                common | {"errorCode"},
                set(),
                error=f"RESULT_STATUS_SHAPE_INVALID:{label}:{index}",
            )
            if result["errorCode"] not in ERROR_CODES:
                raise GateError(f"RESULT_ERROR_CODE_INVALID:{label}:{index}")
        else:
            raise GateError(f"RESULT_STATUS_SHAPE_INVALID:{label}:{index}")
        if result["schemaVersion"] != "s1.4x-result-v1":
            raise GateError(f"RESULT_CASE_VERSION_INVALID:{label}:{index}")
        if (
            result["fixtureId"] != case.get("fixtureId")
            or result["functionId"] != case.get("functionId")
        ):
            raise GateError(f"RESULT_CASE_IDENTITY_MISMATCH:{label}:{index}")
        _validate_finite_and_normalized(result, path=f"{label}.results[{index}]")
    return value


def validate_transport_failure(
    *,
    exit_code: int,
    stdout: bytes,
    stderr: bytes,
    output_exists: bool,
) -> dict[str, Any]:
    """Nonzero candidate 결과가 sanitized transport protocol 하나인지 검증한다."""

    if output_exists:
        raise GateError("TRANSPORT_OUTPUT_PRESENT")
    if stdout:
        raise GateError("TRANSPORT_STDOUT_NOT_EMPTY")
    value = strict_json_load(stderr)
    if not isinstance(value, dict):
        raise GateError("TRANSPORT_ERROR_NOT_OBJECT")
    _require_exact_fields(
        value,
        {"schemaVersion", "code"},
        {"requestId", "fixtureId", "field"},
        error="TRANSPORT_ERROR_FIELDS_INVALID",
    )
    if value["schemaVersion"] != "s1.4x-transport-error-v1":
        raise GateError("TRANSPORT_ERROR_VERSION_INVALID")
    code = value["code"]
    if code not in TRANSPORT_EXIT_BY_CODE:
        raise GateError("TRANSPORT_ERROR_CODE_INVALID")
    if exit_code != TRANSPORT_EXIT_BY_CODE[code]:
        raise GateError(
            f"TRANSPORT_EXIT_CODE_MISMATCH:code={code}:exit={exit_code}"
        )
    for key in ("requestId", "fixtureId"):
        if key in value and (
            not isinstance(value[key], str)
            or REQUEST_ID.fullmatch(value[key]) is None
        ):
            raise GateError(f"TRANSPORT_IDENTIFIER_INVALID:{key}")
    if "field" in value and (
        not isinstance(value["field"], str)
        or FIELD_ID.fullmatch(value["field"]) is None
    ):
        raise GateError("TRANSPORT_FIELD_INVALID")
    return value


def _render_candidate_command(
    command_template: Sequence[str],
    *,
    request_path: Path,
    fixture_root: Path,
    output_path: Path,
) -> list[str]:
    if (
        not command_template
        or not all(isinstance(item, str) and item for item in command_template)
        or not Path(command_template[0]).is_absolute()
        or command_template.count("{protocol_args}") != 1
    ):
        raise GateError("CANDIDATE_COMMAND_TEMPLATE_INVALID")
    rendered: list[str] = []
    for argument in command_template:
        if argument == "{protocol_args}":
            rendered.extend(
                [
                    "--request",
                    str(request_path),
                    "--fixture-root",
                    str(fixture_root),
                    "--output",
                    str(output_path),
                ]
            )
        elif "{" in argument or "}" in argument:
            raise GateError("CANDIDATE_COMMAND_PLACEHOLDER_INVALID")
        else:
            rendered.append(argument)
    return rendered


def run_candidate(
    *,
    label: str,
    command_template: Sequence[str],
    request_path: Path,
    fixture_root: Path,
    output_path: Path,
    timeout_seconds: int = 120,
    runner: Runner = subprocess.run,
) -> dict[str, Any]:
    """공통 CLI argv로 한 candidate를 실행하고 process/result 계약을 원자 검증한다."""

    request_path = request_path.resolve(strict=True)
    fixture_root = fixture_root.resolve(strict=True)
    output_path = output_path.resolve()
    request = validate_request_envelope(strict_json_load(request_path))
    if output_path.exists() or output_path.is_symlink():
        raise GateError(f"CANDIDATE_OUTPUT_ALREADY_EXISTS:{label}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    command = _render_candidate_command(
        command_template,
        request_path=request_path,
        fixture_root=fixture_root,
        output_path=output_path,
    )
    try:
        completed = runner(
            command,
            cwd=output_path.parent,
            capture_output=True,
            check=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        if output_path.exists():
            raise GateError(f"CANDIDATE_TIMEOUT_LEFT_OUTPUT:{label}") from exc
        raise GateError(f"CANDIDATE_TIMEOUT:{label}:exit=124") from exc
    if completed.returncode != 0:
        transport = validate_transport_failure(
            exit_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            output_exists=output_path.exists() or output_path.is_symlink(),
        )
        raise GateError(f"CANDIDATE_TRANSPORT_FAILURE:{label}:{transport['code']}")
    if completed.stdout or completed.stderr:
        raise GateError(f"SUCCESS_STREAM_NOT_EMPTY:{label}")
    if output_path.is_symlink() or not output_path.is_file():
        raise GateError(f"CANDIDATE_RESULT_MISSING:{label}")
    return validate_result_batch(
        strict_json_load(output_path),
        request,
        label=label,
    )


def run_transport_case(
    *,
    label: str,
    command_template: Sequence[str],
    request_path: Path,
    fixture_root: Path,
    output_path: Path,
    expected_exit: int,
    expected_code: str,
    timeout_seconds: int = 120,
    runner: Runner = subprocess.run,
) -> dict[str, Any]:
    """의도적으로 invalid인 request를 실행해 exact transport exit/code를 replay한다."""

    request_path = request_path.resolve(strict=True)
    fixture_root = fixture_root.resolve(strict=True)
    output_path = output_path.resolve()
    if output_path.exists() or output_path.is_symlink():
        raise GateError(f"TRANSPORT_REPLAY_OUTPUT_ALREADY_EXISTS:{label}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    command = _render_candidate_command(
        command_template,
        request_path=request_path,
        fixture_root=fixture_root,
        output_path=output_path,
    )
    try:
        completed = runner(
            command,
            cwd=output_path.parent,
            capture_output=True,
            check=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise GateError(f"TRANSPORT_REPLAY_TIMEOUT:{label}:exit=124") from exc
    transport = validate_transport_failure(
        exit_code=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
        output_exists=output_path.exists() or output_path.is_symlink(),
    )
    if completed.returncode != expected_exit or transport["code"] != expected_code:
        raise GateError(
            f"TRANSPORT_REPLAY_EXPECTATION_MISMATCH:{label}:"
            f"expected={expected_exit}/{expected_code}:"
            f"actual={completed.returncode}/{transport['code']}"
        )
    return transport


def run_reference_capture(
    *,
    python_executable: Path,
    capture_script: Path,
    request_path: Path,
    expected_path: Path,
    fixture_root: Path,
    production_project: Path,
    research_project: Path,
    uv_executable: Path,
    scratch_root: Path,
    capture_report: Path,
    runner: Runner = subprocess.run,
) -> dict[str, Any]:
    """Frozen two-process oracle를 check mode로 재생하고 typed capture evidence를 읽는다."""

    if capture_report.exists():
        raise GateError("REFERENCE_CAPTURE_REPORT_ALREADY_EXISTS")
    capture_report.parent.mkdir(parents=True, exist_ok=True)
    command = [
        str(python_executable.resolve(strict=True)),
        str(capture_script.resolve(strict=True)),
        "--request",
        str(request_path.resolve(strict=True)),
        "--output",
        str(expected_path.resolve(strict=True)),
        "--fixture-root",
        str(fixture_root.resolve(strict=True)),
        "--production-project",
        str(production_project.resolve(strict=True)),
        "--research-project",
        str(research_project.resolve(strict=True)),
        "--uv-bin",
        str(uv_executable.resolve(strict=True)),
        "--scratch-root",
        str(scratch_root.resolve()),
        "--capture-report",
        str(capture_report.resolve()),
        "--check",
    ]
    completed = runner(
        command,
        cwd=capture_report.parent,
        capture_output=True,
        check=False,
        timeout=300,
    )
    if completed.returncode != 0:
        raise GateError(
            f"REFERENCE_CAPTURE_FAILED:exit={completed.returncode}:"
            f"stdout={completed.stdout.decode('utf-8', errors='replace')[:256]}"
        )
    if completed.stderr or not completed.stdout.startswith(b"REFERENCE_CAPTURE_PASS "):
        raise GateError("REFERENCE_CAPTURE_STREAM_INVALID")
    report = strict_json_load(capture_report)
    if (
        not isinstance(report, dict)
        or report.get("schemaVersion") != "s1.4x-reference-capture-report-v1"
        or report.get("status") != "PASS"
        or report.get("processCount") != 2
    ):
        raise GateError("REFERENCE_CAPTURE_REPORT_INVALID")
    return report


def compare_candidate_results(
    *,
    python_executable: Path,
    comparator: Path,
    expected: Path,
    request: Path,
    candidates: Sequence[Path],
    output: Path,
    runner: Runner = subprocess.run,
) -> dict[str, Any]:
    """Frozen comparator 한 번으로 oracle→각 candidate와 candidate 상호 parity를 검증한다."""

    if len(candidates) != 2:
        raise GateError("COMPARATOR_REQUIRES_TWO_CANDIDATES")
    if output.exists():
        raise GateError("COMPARISON_OUTPUT_ALREADY_EXISTS")
    command = [
        str(python_executable.resolve(strict=True)),
        str(comparator),
        "--expected",
        str(expected.resolve(strict=True)),
        "--request",
        str(request.resolve(strict=True)),
    ]
    for candidate in candidates:
        command.extend(["--actual", str(candidate.resolve(strict=True))])
    command.extend(["--output", str(output.resolve())])
    completed = runner(
        command,
        cwd=output.parent,
        capture_output=True,
        check=False,
        timeout=120,
    )
    if completed.returncode != 0:
        raise GateError(f"COMPARISON_FAILED:exit={completed.returncode}")
    if completed.stderr:
        raise GateError("COMPARISON_STDERR_NOT_EMPTY")
    report = strict_json_load(output)
    if (
        not isinstance(report, dict)
        or report.get("schemaVersion") != "s1.4x-comparison-report-v1"
        or report.get("status") != "PASS"
        or report.get("mismatchCount") != 0
        or report.get("implementationCount") != len(candidates)
    ):
        raise GateError("COMPARISON_REPORT_INVALID")
    return report


def exclusive_json_write(path: Path, value: Any) -> None:
    """Evidence를 기존 파일 위에 덮어쓰지 않고 finite canonical JSON으로 기록한다."""

    payload = json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
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
