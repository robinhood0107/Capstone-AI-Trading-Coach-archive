#!/usr/bin/env python3
"""Candidate의 frozen invalid request exit-64 transport contract를 replay한다."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from gate import GateError, exclusive_json_write, run_transport_case

REQUEST_CASES = (
    ("request-unknown-key.json", 64, "request_invalid"),
    ("request-wrong-version.json", 64, "request_invalid"),
    ("request-unknown-function.json", 64, "request_invalid"),
    ("request-duplicate-key.json", 64, "request_invalid"),
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--fixture-root", type=Path, required=True)
    parser.add_argument("--invalid-root", type=Path, required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        candidate = arguments.candidate.resolve(strict=True)
        invalid_root = arguments.invalid_root.resolve(strict=True)
        output_directory = arguments.output_directory.resolve()
        if output_directory.exists():
            raise GateError("TRANSPORT_REPLAY_OUTPUT_DIRECTORY_ALREADY_EXISTS")
        output_directory.mkdir(parents=True)
        results = []
        for file_name, expected_exit, expected_code in REQUEST_CASES:
            transport = run_transport_case(
                label=f"{candidate.name}/{file_name}",
                command_template=[str(candidate), "{protocol_args}"],
                request_path=invalid_root / file_name,
                fixture_root=arguments.fixture_root,
                output_path=output_directory / f"{file_name}.result.json",
                expected_exit=expected_exit,
                expected_code=expected_code,
            )
            results.append(
                {
                    "requestFile": file_name,
                    "expectedExit": expected_exit,
                    "expectedCode": expected_code,
                    "actualCode": transport["code"],
                    "status": "PASS",
                }
            )
        report = {
            "schemaVersion": "s1.4x-transport-replay-v1",
            "candidate": candidate.name,
            "caseCount": len(results),
            "cases": results,
            "status": "PASS",
        }
        exclusive_json_write(arguments.report.resolve(), report)
        print(json.dumps(report, allow_nan=False, sort_keys=True))
    except (GateError, OSError) as exc:
        print(f"S1_4X_TRANSPORT_REPLAY_FAIL:{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
