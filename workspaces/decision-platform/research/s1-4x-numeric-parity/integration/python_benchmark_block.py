#!/usr/bin/env python3
"""한 frozen selector의 Python NumPy/JAX native timing과 block evidence를 생성한다."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import os
import statistics
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import fields, is_dataclass
from pathlib import Path
from typing import Any, cast

import numpy as np
from benchmark_input_ledger import (
    build_input_ledger,
    generated_fixture_evidence,
)
from gate import GateError, exclusive_json_write, strict_json_load

Operation = Callable[[], object]
PYTHON_BOUNDARIES = {
    "python-numpy-s1-4",
    "python-numpy-s1-4r",
    "python-jax-eager-s1-4r",
    "python-jax-jit-s1-4r",
}
WARMUPS = 5
MEASUREMENTS = 30


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


def _source_closure_sha256(repo_root: Path, paths: Sequence[Path]) -> str:
    """Checkout 위치가 아니라 repo-relative path와 bytes로 source closure를 만든다."""

    repo = repo_root.resolve(strict=True)
    entries = []
    for path in paths:
        resolved = path.resolve(strict=True)
        if path.is_symlink() or not resolved.is_file():
            raise GateError(f"SOURCE_CLOSURE_FILE_UNSAFE:{path.name}")
        try:
            relative = resolved.relative_to(repo).as_posix()
        except ValueError as exc:
            raise GateError(f"SOURCE_CLOSURE_OUTSIDE_REPO:{path.name}") from exc
        entries.append((relative, resolved))
    digest = hashlib.sha256()
    for relative, path in sorted(entries, key=lambda item: item[0].encode("utf-8")):
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _utc_now() -> str:
    return (
        dt.datetime.now(dt.UTC)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def _nearest_rank_p95(values: Sequence[int]) -> float:
    ordered = sorted(values)
    return float(ordered[max(1, math.ceil(0.95 * len(ordered))) - 1])


def _generated_array(large_root: Path, file_name: str, count: int) -> np.ndarray:
    evidence = generated_fixture_evidence(large_root, file_name, count)
    path = large_root.resolve(strict=True) / "generated" / evidence["fileName"]
    values = np.memmap(path, dtype="<f8", mode="r", shape=(count,))
    if not bool(np.isfinite(values).all()):
        raise GateError(f"GENERATED_FIXTURE_NON_FINITE:{file_name}")
    return values


def _consume(value: object) -> float:
    """Lazy/device 결과까지 실제로 평가하여 dead-code timing을 막는다."""

    block_until_ready = getattr(value, "block_until_ready", None)
    if callable(block_until_ready):
        value = cast(Callable[[], object], block_until_ready)()
        value = np.asarray(value)
    if isinstance(value, np.ndarray):
        return float(np.sum(value, dtype=np.float64))
    if isinstance(value, np.generic):
        return float(value)
    if isinstance(value, (float, int)):
        return float(value)
    if isinstance(value, tuple):
        return sum(_consume(item) for item in value)
    if isinstance(value, list):
        return sum(_consume(item) for item in value)
    if isinstance(value, Mapping):
        return sum(_consume(item) for item in value.values())
    if is_dataclass(value) and not isinstance(value, type):
        return sum(_consume(getattr(value, field.name)) for field in fields(value))
    raise GateError(f"UNSUPPORTED_BENCHMARK_RESULT:{type(value).__name__}")


def _production_operation(
    case: dict[str, Any],
    prices: np.ndarray,
    returns: np.ndarray,
) -> Operation:
    from app.financial_engineering import (  # type: ignore[import-not-found]
        returns as returns_module,
    )
    from app.financial_engineering import risk_metrics  # type: ignore[import-not-found]

    function_id = case["functionId"]
    length = case["vectorLength"]
    arguments = case["functionArguments"]
    source = prices if "prices" in case["fixtureId"] else returns
    # Production validator는 exact ndarray type을 요구하므로 memmap view를 setup에서 격리한다.
    values = np.array(source[:length], dtype=np.float64, copy=True)
    functions: dict[str, Callable[..., object]] = {
        "simple_returns": returns_module.simple_returns,
        "log_returns": returns_module.log_returns,
        "cumulative_return": returns_module.cumulative_return,
        "cagr": returns_module.cagr,
        "realized_volatility": risk_metrics.realized_volatility,
        "annualized_volatility": risk_metrics.annualized_volatility,
        "max_drawdown": risk_metrics.max_drawdown,
        "sharpe_ratio": risk_metrics.sharpe_ratio,
        "sortino_ratio": risk_metrics.sortino_ratio,
        "historical_var": risk_metrics.historical_var,
        "historical_cvar": risk_metrics.historical_cvar,
    }
    function = functions[function_id]
    return lambda: _consume(function(values, **arguments))


def _trial_provenance(trial_count: int) -> object:
    from s1_4r_risk_research.models import (  # type: ignore[import-not-found]
        EffectiveTrialProvenance,
    )

    return EffectiveTrialProvenance(
        schema_version="s1.4r-effective-trials-v1",
        method="externally_estimated_effective_count",
        raw_trial_count=trial_count,
        effective_trial_count=trial_count,
        sampling_frequency="daily",
        trial_registry_sha256="d" * 64,
        variance_ddof=1,
    )


def _numpy_research_operation(
    case: dict[str, Any],
    returns: np.ndarray,
    realized_losses: np.ndarray,
    forecast_vars: np.ndarray,
) -> Operation:
    from s1_4r_risk_research import numpy_reference  # type: ignore[import-not-found]

    function_id = case["functionId"]
    arguments = case["functionArguments"]
    length = case["vectorLength"]
    if function_id in {
        "historical_expected_shortfall",
        "realized_variance",
        "realized_volatility_intraday",
        "lo_adjusted_sharpe_ratio",
    }:
        function = getattr(numpy_reference, function_id)
        values = np.array(returns[:length], dtype=np.float64, copy=True)
        return lambda: _consume(function(values, **arguments))
    if function_id == "probabilistic_sharpe_ratio":
        observed = np.asarray(returns[: case["batchSize"]], dtype=np.float64)

        def probabilistic_batch() -> float:
            return math.fsum(
                float(
                    numpy_reference.probabilistic_sharpe_ratio(
                        float(value),
                        **arguments,
                    )
                )
                for value in observed
            )

        return probabilistic_batch
    if function_id == "deflated_sharpe_ratio":
        observed = np.asarray(returns[: case["batchSize"]], dtype=np.float64)
        trials: list[int] = []
        for group in arguments["trial_count_mix"]:
            trials.extend([group["trial_count"]] * group["evaluation_count"])
        provenances = {
            trial: _trial_provenance(trial) for trial in set(trials)
        }

        def deflated_batch() -> float:
            total = 0.0
            for value, trial_count in zip(observed, trials, strict=True):
                total += numpy_reference.deflated_sharpe_ratio(
                    float(value),
                    sample_size=arguments["sample_size"],
                    skewness=arguments["skewness"],
                    kurtosis=arguments["kurtosis"],
                    trial_count=trial_count,
                    sharpe_estimate_variance=arguments["sharpe_estimate_variance"],
                    trial_provenance=provenances[trial_count],
                )
            return total

        return deflated_batch
    realized = np.asarray(
        realized_losses[: case["batchSize"] * length],
        dtype=np.float64,
    ).reshape(case["batchSize"], length)
    forecast = np.asarray(
        forecast_vars[: case["batchSize"] * length],
        dtype=np.float64,
    ).reshape(case["batchSize"], length)
    function = getattr(numpy_reference, function_id)

    def coverage_batch() -> float:
        return sum(
            _consume(function(loss_row, var_row, **arguments))
            for loss_row, var_row in zip(realized, forecast, strict=True)
        )

    return coverage_batch


def _jax_research_operation(
    case: dict[str, Any],
    returns: np.ndarray,
    realized_losses: np.ndarray,
    forecast_vars: np.ndarray,
    *,
    use_jit: bool,
) -> Operation:
    import jax  # type: ignore[import-not-found]
    import jax.numpy as jnp  # type: ignore[import-not-found]
    from s1_4r_risk_research import _jax_kernels

    if (
        jax.default_backend() != "cpu"
        or jax.config.jax_enable_x64 is not True
        or any(device.platform != "cpu" for device in jax.devices())
    ):
        raise GateError("JAX_CPU_X64_REQUIRED")
    function_id = case["functionId"]
    arguments = case["functionArguments"]
    length = case["vectorLength"]
    if function_id in {
        "historical_expected_shortfall",
        "realized_variance",
        "realized_volatility_intraday",
        "lo_adjusted_sharpe_ratio",
    }:
        values = jax.device_put(np.asarray(returns[:length], dtype=np.float64))
        if function_id == "historical_expected_shortfall":
            def vector_kernel(vector: object) -> object:
                return _jax_kernels.historical_expected_shortfall(
                    vector,
                    jnp.asarray(arguments["confidence"], dtype=jnp.float64),
                )
        elif function_id == "lo_adjusted_sharpe_ratio":
            def vector_kernel(vector: object) -> object:
                return _jax_kernels.lo_adjusted_sharpe_ratio(
                    vector,
                    aggregation_periods=arguments["aggregation_periods"],
                    risk_free_rate=jnp.asarray(
                        arguments["risk_free_rate"],
                        dtype=jnp.float64,
                    ),
                )
        else:
            vector_kernel = getattr(_jax_kernels, function_id)
        selected_vector = jax.jit(vector_kernel) if use_jit else vector_kernel
        return lambda: _consume(selected_vector(values))
    if function_id == "probabilistic_sharpe_ratio":
        observed = jax.device_put(
            np.asarray(returns[: case["batchSize"]], dtype=np.float64)
        )
        def scalar_kernel(value: object) -> object:
            return _jax_kernels.probabilistic_sharpe_ratio(
                value,
                jnp.asarray(arguments["benchmark_sharpe"], dtype=jnp.float64),
                jnp.asarray(arguments["sample_size"], dtype=jnp.float64),
                jnp.asarray(arguments["skewness"], dtype=jnp.float64),
                jnp.asarray(arguments["kurtosis"], dtype=jnp.float64),
            )

        vectorized_scalar = jax.vmap(scalar_kernel)
        selected_scalar = (
            jax.jit(vectorized_scalar) if use_jit else vectorized_scalar
        )
        return lambda: _consume(selected_scalar(observed))
    if function_id == "deflated_sharpe_ratio":
        observed = jax.device_put(
            np.asarray(returns[: case["batchSize"]], dtype=np.float64)
        )
        trial_values: list[float] = []
        for group in arguments["trial_count_mix"]:
            trial_values.extend(
                [float(group["trial_count"])] * group["evaluation_count"]
            )
        trials = jax.device_put(np.asarray(trial_values, dtype=np.float64))
        def dsr_kernel(value: object, trial: object) -> object:
            return _jax_kernels.deflated_sharpe_ratio(
                value,
                jnp.asarray(arguments["sample_size"], dtype=jnp.float64),
                jnp.asarray(arguments["skewness"], dtype=jnp.float64),
                jnp.asarray(arguments["kurtosis"], dtype=jnp.float64),
                trial,
                jnp.asarray(
                    arguments["sharpe_estimate_variance"],
                    dtype=jnp.float64,
                ),
            )

        vectorized_dsr = jax.vmap(dsr_kernel)
        selected_dsr = jax.jit(vectorized_dsr) if use_jit else vectorized_dsr
        return lambda: _consume(selected_dsr(observed, trials))
    realized = jax.device_put(
        np.asarray(
            realized_losses[: case["batchSize"] * length],
            dtype=np.float64,
        ).reshape(case["batchSize"], length)
    )
    forecast = jax.device_put(
        np.asarray(
            forecast_vars[: case["batchSize"] * length],
            dtype=np.float64,
        ).reshape(case["batchSize"], length)
    )
    if function_id == "kupiec_unconditional_coverage_test":
        def coverage_kernel(loss: object, var: object) -> object:
            return _jax_kernels.kupiec_unconditional_coverage_test(
                loss,
                var,
                jnp.asarray(arguments["confidence"], dtype=jnp.float64),
            )
    elif function_id == "christoffersen_independence_test":
        coverage_kernel = _jax_kernels.christoffersen_independence_test
    else:
        def coverage_kernel(loss: object, var: object) -> object:
            return _jax_kernels.christoffersen_conditional_coverage_test(
                loss,
                var,
                jnp.asarray(arguments["confidence"], dtype=jnp.float64),
            )
    vectorized_coverage = jax.vmap(coverage_kernel)
    selected_coverage = (
        jax.jit(vectorized_coverage) if use_jit else vectorized_coverage
    )
    return lambda: _consume(selected_coverage(realized, forecast))


def _operation(
    boundary: str,
    case: dict[str, Any],
    *,
    prices: np.ndarray,
    returns: np.ndarray,
    realized_losses: np.ndarray,
    forecast_vars: np.ndarray,
) -> Operation:
    if boundary == "python-numpy-s1-4":
        return _production_operation(case, prices, returns)
    if boundary == "python-numpy-s1-4r":
        return _numpy_research_operation(
            case,
            returns,
            realized_losses,
            forecast_vars,
        )
    return _jax_research_operation(
        case,
        returns,
        realized_losses,
        forecast_vars,
        use_jit=boundary == "python-jax-jit-s1-4r",
    )


def _prepare_operations_for_measurement(
    operations: Sequence[tuple[dict[str, Any], Operation]],
    *,
    mark_measurement: Callable[[], None],
) -> None:
    """모든 compile/setup 강제 평가와 warmup 뒤에만 timeout 상태를 measurement로 전이한다."""

    for _, operation in operations:
        _consume(operation())
    for _ in range(WARMUPS):
        for _, operation in operations:
            _consume(operation())
    mark_measurement()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--block-dir", type=Path, required=True)
    parser.add_argument("--qualification", type=Path, required=True)
    parser.add_argument("--boundary", choices=sorted(PYTHON_BOUNDARIES), required=True)
    parser.add_argument("--selector", required=True)
    parser.add_argument("--family", required=True)
    parser.add_argument("--rotation", required=True)
    parser.add_argument("--outer-repetition", type=int, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--benchmark-subject-commit", required=True)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--large-fixture-root", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        repo = arguments.repo_root.resolve(strict=True)
        block_dir = arguments.block_dir.resolve(strict=True)
        large_fixture_root = arguments.large_fixture_root
        plan = strict_json_load(arguments.plan.resolve(strict=True))
        qualification = strict_json_load(arguments.qualification.resolve(strict=True))
        if (
            not isinstance(plan, dict)
            or not isinstance(qualification, dict)
            or qualification.get("phase") != "PRE_RUN"
            or qualification.get("measurementEntered") is not False
        ):
            raise GateError("BENCHMARK_INPUT_INVALID")
        selector = next(
            (
                item
                for item in plan["familySelectors"]
                if item["selectorId"] == arguments.selector
            ),
            None,
        )
        if (
            selector is None
            or selector["boundaryId"] != arguments.boundary
            or selector["familyId"] != arguments.family
        ):
            raise GateError("BENCHMARK_SELECTOR_INVALID")
        case_by_id = {item["caseId"]: item for item in plan["cases"]}
        cases = [case_by_id[case_id] for case_id in selector["expectedCaseIds"]]
        input_ledger_path = block_dir / "input-ledger.json"
        exclusive_json_write(
            input_ledger_path,
            build_input_ledger(
                plan=plan,
                plan_path=arguments.plan.resolve(strict=True),
                repo_root=repo,
                large_fixture_root=large_fixture_root,
                boundary_id=arguments.boundary,
                selector_id=arguments.selector,
            ),
        )
        large = large_fixture_root.resolve(strict=True) / "large"
        prices = _generated_array(large, "large-prices-n100000.f64le", 100000)
        returns = _generated_array(large, "large-returns-n100000.f64le", 100000)
        realized_losses = _generated_array(
            large,
            "large-coverage-realized-losses-n3200000.f64le",
            3200000,
        )
        forecast_vars = _generated_array(
            large,
            "large-coverage-forecast-var-n3200000.f64le",
            3200000,
        )
        production = repo / "workspaces/decision-platform/python-services"
        research = (
            repo
            / "workspaces/decision-platform/research/s1-4r-jax-risk"
        )
        sys.path.insert(0, str(production))
        sys.path.insert(0, str(research / "src"))
        operations = [
            (
                case,
                _operation(
                    arguments.boundary,
                    case,
                    prices=prices,
                    returns=returns,
                    realized_losses=realized_losses,
                    forecast_vars=forecast_vars,
                ),
            )
            for case in cases
        ]
        benchmark_dir = arguments.plan.resolve(strict=True).parent
        sys.path.insert(0, str(benchmark_dir))
        from run_rotated_blocks import mark_measurement_entered  # type: ignore[import-not-found]

        # Import, decode, device transfer/JIT compile, validation과 warmup은 timing 밖이다.
        _prepare_operations_for_measurement(
            operations,
            mark_measurement=lambda: mark_measurement_entered(
                arguments.qualification.resolve(strict=True)
            ),
        )
        started = _utc_now()
        native_cases = []
        measured_cases = []
        native_statistics = []
        for case, operation in operations:
            samples: list[int] = []
            for _ in range(MEASUREMENTS):
                before = time.perf_counter_ns()
                _consume(operation())
                samples.append(time.perf_counter_ns() - before)
            median_ns = float(statistics.median(samples))
            if not math.isfinite(median_ns) or median_ns <= 0.0:
                raise GateError(f"INVALID_NATIVE_TIMING:{case['caseId']}")
            logical = case["logicalOperationsPerInvocation"]
            p95_ns = _nearest_rank_p95(samples)
            standard_deviation_ns = statistics.stdev(samples)
            native_cases.append(
                {
                    "caseId": case["caseId"],
                    "rawSamplesNs": samples,
                    "medianNsPerPrecomputedBatch": median_ns,
                    "logicalOperationsPerInvocation": logical,
                }
            )
            measured_cases.append(
                {
                    "caseId": case["caseId"],
                    "functionId": case["functionId"],
                    "fixtureId": case["fixtureId"],
                    "nativeValue": median_ns,
                    "nativeUnit": "ns",
                    "logicalOperationsPerInvocation": logical,
                    "normalizedNsPerLogicalOperation": median_ns / logical,
                    "samples": MEASUREMENTS,
                    "warmupIterations": WARMUPS,
                    "measurementIterations": MEASUREMENTS,
                    "status": "PASS",
                }
            )
            native_statistics.append(
                {
                    "caseId": case["caseId"],
                    "nativeSampleCount": len(samples),
                    "nativeP95": p95_ns,
                    "confidenceLevel": None,
                    "confidenceLow": None,
                    "confidenceHigh": None,
                    "dispersionMetric": "sample-standard-deviation",
                    "dispersionValue": standard_deviation_ns,
                    "nativeUnit": "ns",
                    "logicalOperationsPerInvocation": logical,
                    "normalizedP95NsPerLogicalOperation": p95_ns / logical,
                    "normalizedConfidenceLowNsPerLogicalOperation": None,
                    "normalizedConfidenceHighNsPerLogicalOperation": None,
                    "normalizedDispersionNsPerLogicalOperation": (
                        standard_deviation_ns / logical
                    ),
                }
            )
        finished = _utc_now()
        native_path = block_dir / "native.json"
        native_contract_path = block_dir / "native-contract-validation.json"
        framework = (
            "NumPy"
            if arguments.boundary.startswith("python-numpy")
            else (
                "JAX-jit"
                if arguments.boundary == "python-jax-jit-s1-4r"
                else "JAX-eager"
            )
        )
        framework_version = (
            np.__version__
            if framework == "NumPy"
            else str(__import__("jax").__version__)
        )
        exclusive_json_write(
            native_contract_path,
            {
                "schemaVersion": "s1.4x-native-contract-validation-v1",
                "boundaryId": arguments.boundary,
                "selectorId": arguments.selector,
                "framework": framework,
                "frameworkVersion": framework_version,
                "configuration": {
                    "benchmarkMode": "precomputed-batch",
                    "nativeTimeUnit": "ns",
                    "threads": 1,
                    "warmupIterations": WARMUPS,
                    "measurementIterations": MEASUREMENTS,
                    "compileAndSetupOutsideTiming": True,
                    "measurementMarkerAfterForcedSetup": True,
                },
                "cases": [
                    {
                        "caseId": case["caseId"],
                        "nativeSampleCount": len(case["rawSamplesNs"]),
                        "rawEvidencePath": None,
                        "rawEvidenceSha256": _sha256_json(
                            case["rawSamplesNs"]
                        ),
                        "executionReceiptPath": None,
                        "executionReceiptSha256": None,
                        "status": "PASS",
                    }
                    for case in native_cases
                ],
                "status": "PASS",
            },
        )
        exclusive_json_write(
            native_path,
            {
                "schemaVersion": "s1.4x-python-native-benchmark-v1",
                "boundaryId": arguments.boundary,
                "selectorId": arguments.selector,
                "nativeBenchmarkMode": "precomputed-batch",
                "nativeTimeUnit": "ns",
                "warmupIterations": WARMUPS,
                "measurementIterations": MEASUREMENTS,
                "inputLedgerSha256": _sha256_file(input_ledger_path),
                "nativeContractValidationSha256": _sha256_file(
                    native_contract_path
                ),
                "cases": native_cases,
            },
        )
        exclusive_json_write(
            block_dir / "native-statistics.json",
            {
                "schemaVersion": "s1.4x-native-statistics-v1",
                "boundaryId": arguments.boundary,
                "selectorId": arguments.selector,
                "nativeReportSha256": _sha256_file(native_path),
                "cases": native_statistics,
                "status": "PASS",
            },
        )
        rotation = plan["execution"]["candidateOrderBlocks"][
            arguments.outer_repetition - 1
        ]
        source_paths = (
            [
                production / "app/financial_engineering/__init__.py",
                production / "app/financial_engineering/_validation.py",
                production / "app/financial_engineering/returns.py",
                production / "app/financial_engineering/risk_metrics.py",
            ]
            if arguments.boundary == "python-numpy-s1-4"
            else [
                research / "src/s1_4r_risk_research/__init__.py",
                research / "src/s1_4r_risk_research/_numeric_common.py",
                research / "src/s1_4r_risk_research/_validation.py",
                research / "src/s1_4r_risk_research/errors.py",
                research / "src/s1_4r_risk_research/models.py",
                *(
                    [
                        research
                        / "src/s1_4r_risk_research/numpy_reference.py"
                    ]
                    if arguments.boundary == "python-numpy-s1-4r"
                    else [
                        research / "src/s1_4r_risk_research/_jax_kernels.py"
                    ]
                ),
            ]
        )
        integration = (
            repo
            / "workspaces/decision-platform/research/s1-4x-numeric-parity/"
            "integration"
        )
        toolchain_lock = (
            production / "uv.lock"
            if arguments.boundary == "python-numpy-s1-4"
            else research / "uv.lock"
        )
        runtime_identity = {
            "python": sys.version,
            "numpy": np.__version__,
            "jax": (
                __import__("jax").__version__
                if arguments.boundary.startswith("python-jax")
                else None
            ),
            "argv": sys.argv,
        }
        block_report = {
            "schemaVersion": "s1.4x-benchmark-block-result-v1",
            "planId": plan["planId"],
            "runId": arguments.run_id,
            "benchmarkSubjectCommit": arguments.benchmark_subject_commit,
            "subject": {
                "candidate": arguments.boundary,
                "language": "python",
                "profile": arguments.boundary.removeprefix("python-"),
                "artifactSha256": _source_closure_sha256(
                    repo,
                    [
                        integration / "python_benchmark_block.py",
                        integration / "tools/run-python-benchmark-block.sh",
                    ],
                ),
                "sourceTreeSha256": _source_closure_sha256(repo, source_paths),
                "toolchainLockSha256": _sha256_file(toolchain_lock),
            },
            "rotation": {
                "rotationId": arguments.rotation,
                "outerRepetition": arguments.outer_repetition,
                "candidateOrder": rotation["schedulingGroups"],
                "schedulingGroup": "PythonBaselines",
                "pythonBoundaryOrder": rotation["pythonBoundaries"],
            },
            "block": {
                "boundaryId": arguments.boundary,
                "familyId": arguments.family,
                "selectorId": arguments.selector,
                "affinityCpuSet": [0],
                "actualAffinityCpuSet": sorted(os.sched_getaffinity(0)),
                "threadCount": 1,
                "nativeBenchmarkMode": "precomputed-batch",
                "startedAt": started,
                "finishedAt": finished,
                "status": "PASS",
                "nativeReportPath": (
                    f"{arguments.run_id}/{arguments.rotation}/"
                    f"{arguments.boundary}/{arguments.family}/native.json"
                ),
                "nativeReportSha256": _sha256_file(native_path),
            },
            "environment": {
                "hostFingerprintSha256": qualification["hostValidity"][
                    "portableHostIdSha256"
                ],
                "hostValidityArtifactSha256": qualification["hostValidity"]["sha256"],
                "toolchainProvenanceSha256": _sha256_file(
                    repo
                    / "workspaces/decision-platform/research/s1-4x-numeric-parity/"
                    "contract/toolchain-provenance.v1.json"
                ),
                "fixtureFreezeIdentity": plan["fixtureFreezeIdentity"],
                "effectiveRuntimeArgumentsSha256": _sha256_json(runtime_identity),
            },
            "cases": measured_cases,
        }
        exclusive_json_write(block_dir / "block-result.json", block_report)
    except (GateError, OSError, KeyError, ValueError) as exc:
        print(f"PYTHON_BENCHMARK_BLOCK_FAIL:{exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
