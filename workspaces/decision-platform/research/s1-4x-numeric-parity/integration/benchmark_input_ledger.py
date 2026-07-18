#!/usr/bin/env python3
"""Frozen benchmark plan, large fixture bytes, slices와 DSR provenance를 한 ledger로 결합한다."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from gate import GateError, exclusive_json_write, strict_json_load

BENCHMARKS = Path(__file__).resolve().parents[1] / "benchmarks"
sys.path.insert(0, str(BENCHMARKS))

from benchmark_contract import (  # type: ignore[import-not-found]  # noqa: E402
    ContractError,
    sha256_file,
)
from validate_benchmark_report import validate_plan  # type: ignore[import-not-found]  # noqa: E402

FIXTURE_FILES = {
    "large-prices-n100000": ("large-prices-n100000.f64le", 100000),
    "large-returns-n100000": ("large-returns-n100000.f64le", 100000),
    "large-coverage-realized-losses-n3200000": (
        "large-coverage-realized-losses-n3200000.f64le",
        3200000,
    ),
    "large-coverage-forecast-var-n3200000": (
        "large-coverage-forecast-var-n3200000.f64le",
        3200000,
    ),
}
FIXTURE_ORDER = tuple(FIXTURE_FILES)
MANIFEST_FIELDS = {
    "schemaVersion",
    "fixtureId",
    "argumentName",
    "fileName",
    "encoding",
    "dtype",
    "byteOrder",
    "arrayOrder",
    "shape",
    "count",
    "byteLength",
    "sha256",
    "generator",
}
GENERATOR_FIELDS = {
    "algorithm",
    "seed",
    "generatorVersion",
    "distribution",
    "parameters",
    "chunkLength",
}


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


def _contract_file_sha256(
    contract_manifest: dict[str, Any],
    *,
    root_suffix: str,
    relative_path: str,
) -> str:
    roots = contract_manifest.get("immutableRoots")
    if not isinstance(roots, list):
        raise GateError("FROZEN_CONTRACT_MANIFEST_INVALID")
    matching = [
        root
        for root in roots
        if isinstance(root, dict)
        and isinstance(root.get("root"), str)
        and root["root"].endswith(root_suffix)
    ]
    if len(matching) != 1 or not isinstance(matching[0].get("files"), list):
        raise GateError("FROZEN_CONTRACT_ROOT_INVALID")
    entries = [
        item
        for item in matching[0]["files"]
        if isinstance(item, dict) and item.get("path") == relative_path
    ]
    if (
        len(entries) != 1
        or not isinstance(entries[0].get("sha256"), str)
        or len(entries[0]["sha256"]) != 64
    ):
        raise GateError(f"FROZEN_CONTRACT_FILE_INVALID:{relative_path}")
    return str(entries[0]["sha256"])


def _generated_fixture_evidence(
    large_root_text: str,
    file_name: str,
    count: int,
) -> dict[str, Any]:
    large_root = Path(large_root_text)
    if large_root.is_symlink() or not large_root.is_dir():
        raise GateError("GENERATED_FIXTURE_ROOT_UNSAFE")
    large = large_root.resolve(strict=True)
    if Path(file_name).name != file_name or not file_name.endswith(".f64le"):
        raise GateError(f"GENERATED_FIXTURE_PATH_INVALID:{file_name}")
    manifest_path = large / f"{file_name.removesuffix('.f64le')}.manifest.json"
    generated_root = large / "generated"
    payload_path = generated_root / file_name
    if (
        manifest_path.is_symlink()
        or not manifest_path.is_file()
        or manifest_path.resolve(strict=True).parent != large
        or generated_root.is_symlink()
        or not generated_root.is_dir()
        or payload_path.is_symlink()
        or not payload_path.is_file()
        or payload_path.resolve(strict=True).parent
        != generated_root.resolve(strict=True)
    ):
        raise GateError(f"GENERATED_FIXTURE_UNSAFE:{file_name}")
    s1_4x = large.parents[2]
    contract_manifest_path = s1_4x / "contract/contract-manifest.v1.json"
    generator_script = s1_4x / "oracle/generate_large_fixtures.py"
    if (
        contract_manifest_path.is_symlink()
        or not contract_manifest_path.is_file()
        or generator_script.is_symlink()
        or not generator_script.is_file()
    ):
        raise GateError("GENERATED_FIXTURE_PROVENANCE_UNSAFE")
    contract_manifest = strict_json_load(contract_manifest_path)
    if not isinstance(contract_manifest, dict):
        raise GateError("FROZEN_CONTRACT_MANIFEST_INVALID")
    manifest_relative = f"fixtures/large/{manifest_path.name}"
    expected_manifest_sha = _contract_file_sha256(
        contract_manifest,
        root_suffix="s1-4x-numeric-parity/contract",
        relative_path=manifest_relative,
    )
    expected_generator_sha = _contract_file_sha256(
        contract_manifest,
        root_suffix="s1-4x-numeric-parity/oracle",
        relative_path="generate_large_fixtures.py",
    )
    if (
        sha256_file(manifest_path) != expected_manifest_sha
        or sha256_file(generator_script) != expected_generator_sha
    ):
        raise GateError("GENERATED_FIXTURE_PROVENANCE_DIGEST_MISMATCH")
    manifest = strict_json_load(manifest_path)
    if (
        not isinstance(manifest, dict)
        or set(manifest) != MANIFEST_FIELDS
        or manifest["schemaVersion"] != "s1.4x-binary-array-v1"
        or manifest["fileName"] != file_name
        or manifest["encoding"] != "ieee754-binary64"
        or manifest["dtype"] != "float64"
        or manifest["byteOrder"] != "little"
        or manifest["arrayOrder"] != "C"
        or manifest["shape"] != [count]
        or manifest["count"] != count
        or manifest["byteLength"] != count * 8
        or not isinstance(manifest["generator"], dict)
        or set(manifest["generator"]) != GENERATOR_FIELDS
        or manifest["generator"]["chunkLength"] != count
    ):
        raise GateError(f"GENERATED_FIXTURE_MANIFEST_INVALID:{file_name}")
    if (
        payload_path.stat().st_size != manifest["byteLength"]
        or sha256_file(payload_path) != manifest["sha256"]
    ):
        raise GateError(f"GENERATED_FIXTURE_DIGEST_MISMATCH:{file_name}")
    return {
        "fixtureId": manifest["fixtureId"],
        "manifestPath": (
            "workspaces/decision-platform/research/s1-4x-numeric-parity/"
            f"contract/fixtures/large/{manifest_path.name}"
        ),
        "manifestSha256": expected_manifest_sha,
        "fileName": file_name,
        "payloadSha256": manifest["sha256"],
        "byteLength": manifest["byteLength"],
        "elementCount": manifest["count"],
        "shape": manifest["shape"],
        "dtype": manifest["dtype"],
        "byteOrder": manifest["byteOrder"],
        "encoding": manifest["encoding"],
        "arrayOrder": manifest["arrayOrder"],
        "generator": manifest["generator"],
    }


def generated_fixture_evidence(
    large_root: Path,
    file_name: str,
    count: int,
) -> dict[str, Any]:
    """Manifest, contract freeze, generator source와 payload bytes를 함께 검증한다."""

    return copy.deepcopy(
        _generated_fixture_evidence(
            str(large_root.resolve(strict=True)),
            file_name,
            count,
        )
    )


def _case_input_slices(case: dict[str, Any]) -> list[dict[str, Any]]:
    function_id = case["functionId"]
    vector_length = case["vectorLength"]
    batch_size = case["batchSize"]
    if function_id in {
        "kupiec_unconditional_coverage_test",
        "christoffersen_independence_test",
        "christoffersen_conditional_coverage_test",
    }:
        length = vector_length * batch_size
        return [
            {
                "argumentName": "realized_losses",
                "sourceFixtureId": "large-coverage-realized-losses-n3200000",
                "offsetElements": 0,
                "lengthElements": length,
                "shape": [batch_size, vector_length],
            },
            {
                "argumentName": "forecast_vars",
                "sourceFixtureId": "large-coverage-forecast-var-n3200000",
                "offsetElements": 0,
                "lengthElements": length,
                "shape": [batch_size, vector_length],
            },
        ]
    source = (
        "large-prices-n100000"
        if "prices" in case["fixtureId"]
        else "large-returns-n100000"
    )
    argument_name = "prices" if source == "large-prices-n100000" else "returns"
    return [
        {
            "argumentName": argument_name,
            "sourceFixtureId": source,
            "offsetElements": 0,
            "lengthElements": vector_length,
            "shape": [vector_length],
        }
    ]


def _dsr_provenance(case: dict[str, Any]) -> dict[str, Any] | None:
    if case["functionId"] != "deflated_sharpe_ratio":
        return None
    mix = case["functionArguments"]["trial_count_mix"]
    expected = [
        {"evaluation_count": 5462, "trial_count": 2},
        {"evaluation_count": 5461, "trial_count": 10**20},
        {"evaluation_count": 5461, "trial_count": 10**308},
    ]
    if mix != expected or sum(item["evaluation_count"] for item in mix) != 16384:
        raise GateError("BENCHMARK_DSR_MIX_INVALID")
    return {
        "samplingFrequency": "daily",
        "trialRegistrySha256": "d" * 64,
        "varianceDof": 1,
        "groups": [
            {
                "trialCount": item["trial_count"],
                "evaluationCount": item["evaluation_count"],
                "rawTrialCount": item["trial_count"],
                "effectiveTrialCount": item["trial_count"],
            }
            for item in mix
        ],
    }


def build_input_ledger(
    *,
    plan: dict[str, Any],
    plan_path: Path,
    repo_root: Path,
    boundary_id: str,
    selector_id: str,
) -> dict[str, Any]:
    """한 selector가 실제 읽어야 할 case와 frozen binary slice를 완전히 펼친다."""

    repo = repo_root.resolve(strict=True)
    selector = next(
        (
            item
            for item in plan.get("familySelectors", [])
            if item.get("selectorId") == selector_id
        ),
        None,
    )
    if selector is None or selector["boundaryId"] != boundary_id:
        raise GateError("BENCHMARK_INPUT_SELECTOR_INVALID")
    case_by_id = {case["caseId"]: case for case in plan["cases"]}
    cases = [case_by_id[case_id] for case_id in selector["expectedCaseIds"]]
    case_entries = [
        {
            "caseId": case["caseId"],
            "functionId": case["functionId"],
            "fixtureId": case["fixtureId"],
            "functionArguments": case["functionArguments"],
            "functionArgumentsSha256": _canonical_sha256(
                case["functionArguments"]
            ),
            "logicalOperationsPerInvocation": case[
                "logicalOperationsPerInvocation"
            ],
            "inputSlices": _case_input_slices(case),
            "dsrProvenance": _dsr_provenance(case),
        }
        for case in cases
    ]
    used_fixture_ids = {
        input_slice["sourceFixtureId"]
        for case in case_entries
        for input_slice in case["inputSlices"]
    }
    large = (
        repo
        / "workspaces/decision-platform/research/s1-4x-numeric-parity/"
        "contract/fixtures/large"
    )
    fixtures = [
        generated_fixture_evidence(large, *FIXTURE_FILES[fixture_id])
        for fixture_id in FIXTURE_ORDER
        if fixture_id in used_fixture_ids
    ]
    generator_script = (
        repo
        / "workspaces/decision-platform/research/s1-4x-numeric-parity/"
        "oracle/generate_large_fixtures.py"
    )
    return {
        "schemaVersion": "s1.4x-benchmark-input-ledger-v1",
        "planId": plan["planId"],
        "planSha256": sha256_file(plan_path),
        "boundaryId": boundary_id,
        "selectorId": selector_id,
        "generatorScriptPath": (
            "workspaces/decision-platform/research/s1-4x-numeric-parity/"
            "oracle/generate_large_fixtures.py"
        ),
        "generatorScriptSha256": sha256_file(generator_script),
        "fixtures": fixtures,
        "cases": case_entries,
        "status": "PASS",
    }


def validate_input_ledger(
    value: Any,
    *,
    plan: dict[str, Any],
    plan_path: Path,
    repo_root: Path,
    boundary_id: str,
    selector_id: str,
) -> dict[str, Any]:
    """Self-report를 사용하지 않고 current frozen inputs에서 expected ledger를 재구성한다."""

    expected = build_input_ledger(
        plan=plan,
        plan_path=plan_path,
        repo_root=repo_root,
        boundary_id=boundary_id,
        selector_id=selector_id,
    )
    if value != expected:
        raise GateError(f"BENCHMARK_INPUT_LEDGER_MISMATCH:{selector_id}")
    return expected


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--boundary", required=True)
    parser.add_argument("--selector", required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        plan_path = arguments.plan.resolve(strict=True)
        plan = validate_plan(plan_path)
        ledger = build_input_ledger(
            plan=plan,
            plan_path=plan_path,
            repo_root=arguments.repo_root,
            boundary_id=arguments.boundary,
            selector_id=arguments.selector,
        )
        exclusive_json_write(arguments.output, ledger)
    except (ContractError, GateError, OSError, KeyError, ValueError) as exc:
        print(f"BENCHMARK_INPUT_LEDGER_FAIL:{exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "boundaryId": arguments.boundary,
                "selectorId": arguments.selector,
                "status": "PASS",
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
