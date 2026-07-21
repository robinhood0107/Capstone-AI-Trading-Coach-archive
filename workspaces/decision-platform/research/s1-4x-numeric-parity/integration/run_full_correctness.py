#!/usr/bin/env python3
"""Frozen oracle와 두 candidate를 한 request로 실행해 mismatch 0 evidence를 만든다."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from gate import (
    GateError,
    compare_candidate_results,
    exclusive_json_write,
    run_candidate,
    run_reference_capture,
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--fixture-root", type=Path, required=True)
    parser.add_argument("--expected", type=Path, required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--scala-runner", type=Path, required=True)
    parser.add_argument("--haskell-runner", type=Path, required=True)
    parser.add_argument("--python-executable", type=Path, default=Path(sys.executable))
    parser.add_argument("--capture-script", type=Path, required=True)
    parser.add_argument("--comparator", type=Path, required=True)
    parser.add_argument("--production-project", type=Path, required=True)
    parser.add_argument("--research-project", type=Path, required=True)
    parser.add_argument("--uv-executable", type=Path, required=True)
    parser.add_argument("--scratch-root", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    output = arguments.output_directory.resolve()
    try:
        if output.exists():
            raise GateError("CORRECTNESS_OUTPUT_DIRECTORY_ALREADY_EXISTS")
        output.mkdir(parents=True)
        reference_report = output / "reference-capture.json"
        scala_result = output / "scala-results.json"
        haskell_result = output / "haskell-results.json"
        comparison = output / "comparison-report.json"
        summary = output / "correctness-summary.json"
        capture = run_reference_capture(
            python_executable=arguments.python_executable,
            capture_script=arguments.capture_script,
            request_path=arguments.request,
            expected_path=arguments.expected,
            fixture_root=arguments.fixture_root,
            production_project=arguments.production_project,
            research_project=arguments.research_project,
            uv_executable=arguments.uv_executable,
            scratch_root=arguments.scratch_root,
            capture_report=reference_report,
        )
        scala = run_candidate(
            label="scala",
            command_template=[
                str(arguments.scala_runner.resolve(strict=True)),
                "run",
                "{protocol_args}",
            ],
            request_path=arguments.request,
            fixture_root=arguments.fixture_root,
            output_path=scala_result,
        )
        haskell = run_candidate(
            label="haskell",
            command_template=[
                str(arguments.haskell_runner.resolve(strict=True)),
                "{protocol_args}",
            ],
            request_path=arguments.request,
            fixture_root=arguments.fixture_root,
            output_path=haskell_result,
            timeout_seconds=600,
        )
        report = compare_candidate_results(
            python_executable=arguments.python_executable,
            comparator=arguments.comparator,
            expected=arguments.expected,
            request=arguments.request,
            candidates=[scala_result, haskell_result],
            output=comparison,
        )
        evidence: dict[str, Any] = {
            "schemaVersion": "s1.4x-integration-correctness-v1",
            "requestId": report["requestId"],
            "oracleImplementation": "python-frozen-oracle",
            "candidateImplementations": [
                scala["implementation"],
                haskell["implementation"],
            ],
            "caseCount": len(scala["results"]),
            "mismatchCount": 0,
            "artifacts": {
                path.name: _sha256(path)
                for path in (
                    reference_report,
                    scala_result,
                    haskell_result,
                    comparison,
                )
            },
            "referenceCaptureStatus": capture["status"],
            "status": "PASS",
        }
        exclusive_json_write(summary, evidence)
        print(json.dumps(evidence, allow_nan=False, sort_keys=True))
    except (GateError, OSError) as exc:
        print(f"S1_4X_INTEGRATION_CORRECTNESS_FAIL:{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
