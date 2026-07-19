"""S1.4X 89-case 계획, 회전, block 결과 fail-closed 계약의 회귀 테스트."""

from __future__ import annotations

import copy
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from benchmark_contract import (
    ContractError,
    sha256_file,
    strict_json_load,
    validate_block_result_semantics,
)
from render_benchmark_plan import check_plan
from run_rotated_blocks import block_directory, build_schedule, reserve_directory
from validate_benchmark_report import DEFAULT_PLAN, validate_block_result, validate_plan


@pytest.fixture(scope="module")
def plan() -> dict[str, Any]:
    return validate_plan(DEFAULT_PLAN, verify_files=False)


def test_plan_freezes_exact_89_case_matrix_and_boundary_counts(
    plan: dict[str, Any],
) -> None:
    family_counts: dict[str, int] = {}
    for case in plan["cases"]:
        family_counts[case["familyId"]] = family_counts.get(case["familyId"], 0) + 1

    assert family_counts == {
        "path-transform": 15,
        "classical-path-risk": 45,
        "intraday-realized": 10,
        "serial-sharpe": 5,
        "probabilistic-scalar": 2,
        "coverage-batch": 12,
    }
    assert [len(boundary["expectedCaseIds"]) for boundary in plan["executionBoundaries"]] == [
        55,
        34,
        34,
        34,
        89,
        89,
    ]
    assert len({case["caseId"] for case in plan["cases"]}) == 89


def test_plan_freezes_native_full_run_settings_and_bare_bigint_trials(
    plan: dict[str, Any],
) -> None:
    execution = plan["execution"]
    assert execution["warmupIterations"] == {"scala": 5}
    assert execution["warmupTimeSeconds"] == {"scala": 1}
    assert execution["measurementIterations"] == {"scala": 10}
    assert execution["measurementTimeSeconds"] == {"scala": 1}
    assert execution["forks"] == {"scala": 3}
    assert execution["criterionTimeLimitSeconds"] == 5
    assert execution["outerRepetitions"] == 3
    assert execution["blockOutputPathTemplate"] == (
        "<run>/<repetition>/<execution-boundary>/<family>/native.json"
    )

    dsr_case = next(
        case for case in plan["cases"] if case["functionId"] == "deflated_sharpe_ratio"
    )
    trial_counts = [
        item["trial_count"]
        for item in dsr_case["functionArguments"]["trial_count_mix"]
    ]
    assert trial_counts == [2, 10**20, 10**308]
    assert all(type(value) is int for value in trial_counts)

    raw_plan = DEFAULT_PLAN.read_text(encoding="utf-8")
    assert f'"trial_count": {10**20}' in raw_plan
    assert f'"trial_count": {10**308}' in raw_plan
    assert '"trial_count": "' not in raw_plan
    assert '"trial_count": 1e' not in raw_plan.lower()


def test_plan_freezes_constrained_local_host_policy() -> None:
    plan = validate_plan(DEFAULT_PLAN, verify_files=False)
    environment = plan["environmentValidity"]

    assert environment["minAvailableMemoryGiB"] == 4
    assert environment["runningContainerCount"] == 4
    assert environment["maxNormalizedLoad1"] == 0.1
    assert environment["loadSampleCount"] == 3
    assert environment["externalProcessCpuPercentThreshold"] == 5
    assert plan["allocationPolicy"]["capBytes"] * 8 == 4 * 1024**3


def test_each_repetition_has_29_blocks_and_three_exact_rotations(
    plan: dict[str, Any],
) -> None:
    schedule = build_schedule(plan)

    assert len(schedule) == 87
    assert [
        sum(block.rotation_id == rotation_id for block in schedule)
        for rotation_id in ("R1", "R2", "R3")
    ] == [29, 29, 29]
    assert [list(item.candidate_order) for item in schedule[::29]] == [
        ["PythonBaselines", "Scala", "Haskell"],
        ["Scala", "Haskell", "PythonBaselines"],
        ["Haskell", "PythonBaselines", "Scala"],
    ]
    assert [list(item.python_boundary_order) for item in schedule[::29]] == [
        [
            "python-numpy-s1-4",
            "python-numpy-s1-4r",
            "python-jax-eager-s1-4r",
            "python-jax-jit-s1-4r",
        ],
        [
            "python-numpy-s1-4r",
            "python-jax-eager-s1-4r",
            "python-jax-jit-s1-4r",
            "python-numpy-s1-4",
        ],
        [
            "python-jax-eager-s1-4r",
            "python-jax-jit-s1-4r",
            "python-numpy-s1-4",
            "python-numpy-s1-4r",
        ],
    ]


def test_output_directory_reservation_refuses_overwrite(
    tmp_path: Path,
    plan: dict[str, Any],
) -> None:
    run_directory = reserve_directory(tmp_path / "run-001")
    first_block = build_schedule(plan)[0]
    path = block_directory(run_directory, first_block)
    assert path.relative_to(run_directory) == (
        Path("R1") / first_block.boundary_id / first_block.family_id
    )
    reserve_directory(path)

    with pytest.raises(ContractError, match="OUTPUT_ALREADY_EXISTS"):
        reserve_directory(path)


def _valid_report(
    plan: dict[str, Any],
    *,
    native_report_sha256: str = "3" * 64,
) -> dict[str, Any]:
    selector = next(
        item
        for item in plan["familySelectors"]
        if item["selectorId"] == "python-numpy-s1-4r/probabilistic-scalar"
    )
    case_by_id = {case["caseId"]: case for case in plan["cases"]}
    measured_cases = []
    for case_id in selector["expectedCaseIds"]:
        frozen = case_by_id[case_id]
        logical_operations = frozen["logicalOperationsPerInvocation"]
        measured_cases.append(
            {
                "caseId": case_id,
                "functionId": frozen["functionId"],
                "fixtureId": frozen["fixtureId"],
                "nativeValue": float(logical_operations * 10),
                "nativeUnit": "ns",
                "logicalOperationsPerInvocation": logical_operations,
                "normalizedNsPerLogicalOperation": 10.0,
                "samples": 30,
                "warmupIterations": 1,
                "measurementIterations": 10,
                "status": "PASS",
            }
        )
    return {
        "schemaVersion": "s1.4x-benchmark-block-result-v1",
        "planId": plan["planId"],
        "runId": "run-001",
        "benchmarkSubjectCommit": "0" * 40,
        "subject": {
            "candidate": "python-numpy-s1-4r",
            "language": "python",
            "profile": "numpy-s1-4r",
            "artifactSha256": "0" * 64,
            "sourceTreeSha256": "1" * 64,
            "toolchainLockSha256": "2" * 64,
        },
        "rotation": {
            "rotationId": "R1",
            "outerRepetition": 1,
            "candidateOrder": ["PythonBaselines", "Scala", "Haskell"],
            "schedulingGroup": "PythonBaselines",
            "pythonBoundaryOrder": [
                "python-numpy-s1-4",
                "python-numpy-s1-4r",
                "python-jax-eager-s1-4r",
                "python-jax-jit-s1-4r",
            ],
        },
        "block": {
            "boundaryId": "python-numpy-s1-4r",
            "familyId": "probabilistic-scalar",
            "selectorId": selector["selectorId"],
            "nativeBenchmarkMode": "precomputed-batch",
            "affinityCpuSet": [0],
            "actualAffinityCpuSet": [0],
            "threadCount": 1,
            "startedAt": "2026-07-18T00:00:00Z",
            "finishedAt": "2026-07-18T00:00:01Z",
            "status": "PASS",
            "nativeReportPath": (
                "run-001/R1/python-numpy-s1-4r/"
                "probabilistic-scalar/native.json"
            ),
            "nativeReportSha256": native_report_sha256,
        },
        "environment": {
            "hostFingerprintSha256": "4" * 64,
            "hostValidityArtifactSha256": "5" * 64,
            "toolchainProvenanceSha256": "6" * 64,
            "fixtureFreezeIdentity": plan["fixtureFreezeIdentity"],
            "effectiveRuntimeArgumentsSha256": "7" * 64,
        },
        "cases": measured_cases,
    }


def _valid_scala_report(plan: dict[str, Any]) -> dict[str, Any]:
    report = _valid_report(plan)
    selector = next(
        item
        for item in plan["familySelectors"]
        if item["selectorId"] == "scala/probabilistic-scalar"
    )
    case_by_id = {case["caseId"]: case for case in plan["cases"]}
    report["subject"].update(
        {
            "candidate": "scala",
            "language": "scala",
            "profile": "A",
        }
    )
    report["rotation"]["schedulingGroup"] = "Scala"
    report["block"].update(
        {
            "boundaryId": "scala",
            "selectorId": selector["selectorId"],
            "nativeBenchmarkMode": "AverageTime",
            "nativeReportPath": (
                "run-001/R1/scala/probabilistic-scalar/native.json"
            ),
        }
    )
    report["cases"] = [
        {
            "caseId": case_id,
            "functionId": case_by_id[case_id]["functionId"],
            "fixtureId": case_by_id[case_id]["fixtureId"],
            "nativeValue": float(
                case_by_id[case_id]["logicalOperationsPerInvocation"] * 10
            ),
            "nativeUnit": "ns",
            "logicalOperationsPerInvocation": case_by_id[case_id][
                "logicalOperationsPerInvocation"
            ],
            "normalizedNsPerLogicalOperation": 10.0,
            "samples": 30,
            "warmupIterations": 5,
            "measurementIterations": 10,
            "status": "PASS",
        }
        for case_id in selector["expectedCaseIds"]
    ]
    return report


def test_valid_report_passes_schema_and_semantic_validator(
    tmp_path: Path,
    plan: dict[str, Any],
) -> None:
    block_path = (
        tmp_path
        / "run-001"
        / "R1"
        / "python-numpy-s1-4r"
        / "probabilistic-scalar"
    )
    block_path.mkdir(parents=True)
    native_report_path = block_path / "native.json"
    native_report_path.write_text('{"native":"evidence"}\n', encoding="utf-8")
    report_path = block_path / "block-result.json"
    report_path.write_text(
        json.dumps(
            _valid_report(
                plan,
                native_report_sha256=sha256_file(native_report_path),
            ),
            allow_nan=False,
        ),
        encoding="utf-8",
    )

    validated = validate_block_result(
        report_path,
        plan_path=DEFAULT_PLAN,
        native_report_path=native_report_path,
        expected_boundary_id="python-numpy-s1-4r",
        expected_selector_id="python-numpy-s1-4r/probabilistic-scalar",
        verify_plan_files=False,
    )

    assert validated["block"]["status"] == "PASS"


def _remove_last_case(report: dict[str, Any]) -> None:
    report["cases"].pop()


def _duplicate_first_case(report: dict[str, Any]) -> None:
    report["cases"].append(copy.deepcopy(report["cases"][0]))


def _set_wrong_mode(report: dict[str, Any]) -> None:
    report["block"]["nativeBenchmarkMode"] = "Throughput"


def _set_wrong_unit(report: dict[str, Any]) -> None:
    report["cases"][0]["nativeUnit"] = "us"


def _set_nonfinite(report: dict[str, Any]) -> None:
    report["cases"][0]["nativeValue"] = float("nan")


def _set_one_sample(report: dict[str, Any]) -> None:
    report["cases"][0]["samples"] = 1


def _set_one_measurement_iteration(report: dict[str, Any]) -> None:
    report["cases"][0]["measurementIterations"] = 1


def _set_wrong_native_report_path(report: dict[str, Any]) -> None:
    report["block"]["nativeReportPath"] = "forged/native.json"


@pytest.mark.parametrize(
    ("mutator", "failure"),
    [
        (_remove_last_case, "CASE_SET_OR_ORDER_MISMATCH"),
        (_duplicate_first_case, "DUPLICATE_CASE"),
        (_set_wrong_mode, "WRONG_NATIVE_MODE"),
        (_set_wrong_unit, "WRONG_NATIVE_UNIT"),
        (_set_nonfinite, "NONFINITE_TIMING"),
        (_set_one_sample, "WRONG_SAMPLE_COUNT"),
        (_set_one_measurement_iteration, "WRONG_MEASUREMENT_ITERATIONS"),
        (_set_wrong_native_report_path, "WRONG_NATIVE_REPORT_PATH"),
    ],
)
def test_report_validator_fails_closed_for_invalid_block_results(
    plan: dict[str, Any],
    mutator: Callable[[dict[str, Any]], None],
    failure: str,
) -> None:
    report = _valid_report(plan)
    mutator(report)

    with pytest.raises(ContractError, match=failure):
        validate_block_result_semantics(report, plan)


@pytest.mark.parametrize(
    ("field", "value", "failure"),
    [
        ("samples", 29, "WRONG_SAMPLE_COUNT_OR_FORKS"),
        ("warmupIterations", 4, "WRONG_WARMUP_ITERATIONS"),
        ("measurementIterations", 9, "WRONG_MEASUREMENT_ITERATIONS"),
    ],
)
def test_scala_report_enforces_planned_iterations_and_fork_sample_product(
    plan: dict[str, Any],
    field: str,
    value: int,
    failure: str,
) -> None:
    report = _valid_scala_report(plan)
    report["cases"][0][field] = value

    with pytest.raises(ContractError, match=failure):
        validate_block_result_semantics(report, plan)


def test_report_validator_recomputes_native_report_digest(
    tmp_path: Path,
    plan: dict[str, Any],
) -> None:
    block_path = (
        tmp_path
        / "run-001"
        / "R1"
        / "python-numpy-s1-4r"
        / "probabilistic-scalar"
    )
    block_path.mkdir(parents=True)
    native_report_path = block_path / "native.json"
    native_report_path.write_text('{"native":"actual"}\n', encoding="utf-8")
    report_path = block_path / "block-result.json"
    report_path.write_text(
        json.dumps(_valid_report(plan, native_report_sha256="f" * 64)),
        encoding="utf-8",
    )

    with pytest.raises(ContractError, match="NATIVE_REPORT_DIGEST_MISMATCH"):
        validate_block_result(
            report_path,
            plan_path=DEFAULT_PLAN,
            native_report_path=native_report_path,
            verify_plan_files=False,
        )


def test_report_validator_rejects_actual_native_report_at_wrong_path(
    tmp_path: Path,
    plan: dict[str, Any],
) -> None:
    native_report_path = tmp_path / "native.json"
    native_report_path.write_text('{"native":"actual"}\n', encoding="utf-8")
    report_path = tmp_path / "block-result.json"
    report_path.write_text(
        json.dumps(
            _valid_report(
                plan,
                native_report_sha256=sha256_file(native_report_path),
            )
        ),
        encoding="utf-8",
    )

    with pytest.raises(ContractError, match="NATIVE_REPORT_ACTUAL_PATH_MISMATCH"):
        validate_block_result(
            report_path,
            plan_path=DEFAULT_PLAN,
            native_report_path=native_report_path,
            verify_plan_files=False,
        )


def test_report_validator_rejects_native_report_symlink(
    tmp_path: Path,
    plan: dict[str, Any],
) -> None:
    block_path = (
        tmp_path
        / "run-001"
        / "R1"
        / "python-numpy-s1-4r"
        / "probabilistic-scalar"
    )
    block_path.mkdir(parents=True)
    actual_path = block_path / "actual-native.json"
    actual_path.write_text('{"native":"actual"}\n', encoding="utf-8")
    native_report_path = block_path / "native.json"
    native_report_path.symlink_to(actual_path.name)
    report_path = block_path / "block-result.json"
    report_path.write_text(
        json.dumps(
            _valid_report(
                plan,
                native_report_sha256=sha256_file(actual_path),
            )
        ),
        encoding="utf-8",
    )

    with pytest.raises(ContractError, match="NATIVE_REPORT_MISSING_OR_UNSAFE"):
        validate_block_result(
            report_path,
            plan_path=DEFAULT_PLAN,
            native_report_path=native_report_path,
            verify_plan_files=False,
        )


def test_strict_json_rejects_duplicate_keys_and_nonfinite_numbers(
    tmp_path: Path,
) -> None:
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text('{"caseId":"a","caseId":"b"}', encoding="utf-8")
    nonfinite = tmp_path / "nonfinite.json"
    nonfinite.write_text('{"nativeValue":NaN}', encoding="utf-8")

    with pytest.raises(ContractError, match="DUPLICATE_JSON_KEY"):
        strict_json_load(duplicate)
    with pytest.raises(ContractError, match="NONFINITE_JSON_NUMBER"):
        strict_json_load(nonfinite)


def test_tracked_plan_and_sidecar_are_reproducible() -> None:
    check_plan(DEFAULT_PLAN)


def test_workflow_runs_both_triggers_and_accounts_for_263_snapshot_tests() -> None:
    repo_root = Path(__file__).resolve().parents[6]
    workflow = (
        repo_root / ".github" / "workflows" / "s1-4x-contract-correctness.yml"
    ).read_text(encoding="utf-8")

    assert "pull_request:" in workflow
    assert "push:" in workflow
    assert 'branches:\n      - main' in workflow
    assert "referenceBaseCommit" in workflow
    assert "git clone --no-local --no-hardlinks" in workflow
    assert 'update-ref refs/remotes/origin/main "$BASE"' in workflow
    assert "UV_CACHE_DIR=$S1_4X_RUNTIME/uv" in workflow
    assert "TMPDIR: /tmp" not in workflow
    assert 'evidence["sourceTreeCount"] == 4' in workflow
    assert "assert tests == 263" in workflow
    assert "S1.4R_REFERENCE_REGRESSION_PASS tests=263" in workflow

    production_step = workflow.split(
        "- name: Run current frozen production regression",
        maxsplit=1,
    )[1].split("\n      - name:", maxsplit=1)[0]
    assert production_step.count("\n        run: |") == 1
