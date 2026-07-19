#!/usr/bin/env python3
"""Profile correctness assembler가 forged PASS와 malformed report를 거부하는지 검증한다."""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path


SCALA_ROOT = Path(__file__).resolve().parents[1]
S1_ROOT = SCALA_ROOT.parent
TOOLS_ROOT = SCALA_ROOT / "tools"
SHA = "1" * 64


def load_tool():
    path = TOOLS_ROOT / "assemble_profile_correctness.py"
    specification = importlib.util.spec_from_file_location(
        "assemble_profile_correctness",
        path,
    )
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    sys.path.insert(0, str(TOOLS_ROOT))
    try:
        sys.modules["assemble_profile_correctness"] = module
        specification.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


def expect_error(module, operation, message: str) -> None:
    try:
        operation()
    except (module.ProfileCorrectnessError, KeyError, TypeError, ValueError):
        pass
    else:
        raise AssertionError(message)


def property_reports(plan: dict, seed_corpus: dict) -> tuple[dict, dict]:
    summaries = []
    details = []
    for property_item in plan["properties"]:
        property_id = property_item["propertyId"]
        summaries.append(
            {
                "propertyId": property_id,
                "successfulTests": 1008,
                "discardedTests": 0,
                "status": "PASS",
            }
        )
        seeds = [
            {
                "seedIndex": index,
                "originalSeed": seed_corpus["seeds"][index],
                "successfulTests": 42,
                "discardedTests": 0,
                "attemptedTests": 42,
                "replayToken": f"seed-{index}",
                "shrinks": 0,
                "status": "PASS",
            }
            for index in range(24)
        ]
        details.append(
            {
                "propertyId": property_id,
                "successfulTests": 1008,
                "discardedTests": 0,
                "attemptedTests": 1008,
                "shrinks": 0,
                "seedCount": 24,
                "seedExecutions": seeds,
                "status": "PASS",
            }
        )
    report = {
        "schemaVersion": "s1.4x-candidate-property-coverage-v1",
        "implementation": "scala-3.8.4-jvm25",
        "propertyPlanSha256": SHA,
        "properties": summaries,
        "status": "PASS",
    }
    execution = {
        "schemaVersion": "s1.4x-candidate-property-execution-v1",
        "implementation": "scala-3.8.4-jvm25",
        "propertyPlanSha256": SHA,
        "seedCorpusSha256": "2" * 64,
        "seedCount": 24,
        "minimumSuccessfulPerSeed": 42,
        "maximumDiscardRatio": 0.1,
        "framework": "scala-check-1.19.0",
        "toolchainProfile": "A",
        "scalaCliBinarySha256": "6" * 64,
        "commandArgvSha256": "3" * 64,
        "runnerSha256": "4" * 64,
        "sourceClosureSha256": "5" * 64,
        "startedAt": "2026-07-18T00:00:00Z",
        "finishedAt": "2026-07-18T00:00:01Z",
        "exitCode": 0,
        "properties": details,
        "status": "PASS",
    }
    return report, execution


def main() -> int:
    module = load_tool()
    property_plan = json.loads(
        (S1_ROOT / "contract/property-plan.v1.json").read_text(
            encoding="utf-8"
        )
    )
    seed_corpus = json.loads(
        (
            S1_ROOT
            / "contract/fixtures/property/property-seeds.v1.json"
        ).read_text(encoding="utf-8")
    )
    report, execution = property_reports(property_plan, seed_corpus)
    module.validate_property_reports(
        property_report=report,
        execution=execution,
        property_plan=property_plan,
        property_plan_sha256=SHA,
        seed_corpus=seed_corpus,
        seed_corpus_sha256="2" * 64,
        profile="A",
        source_closure_sha256_value="5" * 64,
        expected_command_sha256="3" * 64,
        expected_runner_sha256="4" * 64,
        expected_scala_cli_sha256="6" * 64,
    )

    forged = copy.deepcopy(report)
    forged["properties"][0]["successfulTests"] = 0
    expect_error(
        module,
        lambda: module.validate_property_reports(
            property_report=forged,
            execution=execution,
            property_plan=property_plan,
            property_plan_sha256=SHA,
            seed_corpus=seed_corpus,
            seed_corpus_sha256="2" * 64,
            profile="A",
            source_closure_sha256_value="5" * 64,
            expected_command_sha256="3" * 64,
            expected_runner_sha256="4" * 64,
            expected_scala_cli_sha256="6" * 64,
        ),
        "forged property PASS was promoted",
    )

    malformed = copy.deepcopy(execution)
    malformed["properties"][0]["seedExecutions"].pop()
    expect_error(
        module,
        lambda: module.validate_property_reports(
            property_report=report,
            execution=malformed,
            property_plan=property_plan,
            property_plan_sha256=SHA,
            seed_corpus=seed_corpus,
            seed_corpus_sha256="2" * 64,
            profile="A",
            source_closure_sha256_value="5" * 64,
            expected_command_sha256="3" * 64,
            expected_runner_sha256="4" * 64,
            expected_scala_cli_sha256="6" * 64,
        ),
        "malformed seed closure was promoted",
    )

    forged_seed = copy.deepcopy(execution)
    forged_seed["properties"][0]["seedExecutions"][0]["originalSeed"] = 999
    expect_error(
        module,
        lambda: module.validate_property_reports(
            property_report=report,
            execution=forged_seed,
            property_plan=property_plan,
            property_plan_sha256=SHA,
            seed_corpus=seed_corpus,
            seed_corpus_sha256="2" * 64,
            profile="A",
            source_closure_sha256_value="5" * 64,
            expected_command_sha256="3" * 64,
            expected_runner_sha256="4" * 64,
            expected_scala_cli_sha256="6" * 64,
        ),
        "forged frozen seed was promoted",
    )

    forged_sum = copy.deepcopy(execution)
    forged_sum["properties"][0]["seedExecutions"][0]["discardedTests"] = 1
    forged_sum["properties"][0]["seedExecutions"][0]["attemptedTests"] = 43
    expect_error(
        module,
        lambda: module.validate_property_reports(
            property_report=report,
            execution=forged_sum,
            property_plan=property_plan,
            property_plan_sha256=SHA,
            seed_corpus=seed_corpus,
            seed_corpus_sha256="2" * 64,
            profile="A",
            source_closure_sha256_value="5" * 64,
            expected_command_sha256="3" * 64,
            expected_runner_sha256="4" * 64,
            expected_scala_cli_sha256="6" * 64,
        ),
        "forged seed aggregate was promoted",
    )

    print(
        "SCALA_PROFILE_CORRECTNESS_CONTRACT_PASS "
        "properties=25 seedsPerProperty=24 forgedPass=REJECT"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
