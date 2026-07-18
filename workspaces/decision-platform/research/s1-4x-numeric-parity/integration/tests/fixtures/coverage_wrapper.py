#!/usr/bin/env python3
"""실제 subprocess 경계에서 outer wrapper digest 결합을 검증하는 fixture."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

S1_4X = Path(__file__).resolve().parents[3]
CONTRACT = S1_4X / "contract"


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


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"OBJECT_REQUIRED:{path.name}")
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    arguments = parser.parse_args()
    output = arguments.output_dir.resolve()
    output.mkdir()
    wrapper = Path(__file__).resolve()
    command = [str(wrapper), "--output-dir", str(output)]
    plan_path = CONTRACT / "property-plan.v1.json"
    seed_path = CONTRACT / "fixtures/property/property-seeds.v1.json"
    plan = _load(plan_path)
    seeds = _load(seed_path)["seeds"]
    functions = _load(CONTRACT / "function-registry.v1.json")
    errors = _load(CONTRACT / "error-registry.v1.json")
    successes_per_seed = 42
    successful_tests = len(seeds) * successes_per_seed
    properties = [
        {
            "propertyId": item["propertyId"],
            "successfulTests": successful_tests,
            "discardedTests": 0,
            "status": "PASS",
        }
        for item in plan["properties"]
    ]
    execution_properties = [
        {
            **item,
            "attemptedTests": successful_tests,
            "seedCount": len(seeds),
            "seedExecutions": [
                {
                    "seedIndex": seed_index,
                    "originalSeed": seed,
                    "successfulTests": successes_per_seed,
                    "discardedTests": 0,
                    "attemptedTests": successes_per_seed,
                    "replayToken": f"fixture:{property_index}:{seed_index}",
                    "shrinks": 0,
                    "status": "PASS",
                }
                for seed_index, seed in enumerate(seeds)
            ],
            "shrinks": 0,
        }
        for property_index, item in enumerate(properties)
    ]
    implementation = "scala-wrapper-fixture"
    plan_sha256 = hashlib.sha256(plan_path.read_bytes()).hexdigest()
    documents = {
        "scala-property-report.v1.json": {
            "schemaVersion": "s1.4x-candidate-property-coverage-v1",
            "implementation": implementation,
            "propertyPlanSha256": plan_sha256,
            "properties": properties,
            "status": "PASS",
        },
        "scala-registry-report.v1.json": {
            "schemaVersion": "s1.4x-candidate-registry-coverage-v1",
            "implementation": implementation,
            "functions": [
                {"functionId": item["functionId"], "status": "PASS"}
                for item in functions["entries"]
            ],
            "errors": [
                {
                    "errorCode": item["code"],
                    "track": item["track"],
                    "verificationMode": item["verificationMode"],
                    "status": "PASS",
                }
                for item in errors["entries"]
            ],
            "status": "PASS",
        },
        "scala-property-execution-evidence.v1.json": {
            "schemaVersion": "s1.4x-candidate-property-execution-v1",
            "implementation": implementation,
            "propertyPlanSha256": plan_sha256,
            "seedCorpusSha256": hashlib.sha256(seed_path.read_bytes()).hexdigest(),
            "seedCount": len(seeds),
            "minimumSuccessfulPerSeed": successes_per_seed,
            "framework": "fixture-1.0",
            "toolchainProfile": "fixture",
            "commandArgvSha256": _canonical_sha256(command),
            "runnerSha256": hashlib.sha256(wrapper.read_bytes()).hexdigest(),
            "sourceClosureSha256": hashlib.sha256(wrapper.read_bytes()).hexdigest(),
            "startedAt": "2026-07-18T12:00:00.000000Z",
            "finishedAt": "2026-07-18T12:00:01.000000Z",
            "exitCode": 0,
            "properties": execution_properties,
            "status": "PASS",
        },
    }
    for name, document in documents.items():
        (output / name).write_text(
            json.dumps(document, allow_nan=False, sort_keys=True),
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
