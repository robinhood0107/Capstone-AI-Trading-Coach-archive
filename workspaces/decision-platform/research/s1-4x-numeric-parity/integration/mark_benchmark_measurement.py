#!/usr/bin/env python3
"""Candidate setup 완료 뒤 timeout qualification을 measurement 상태로 한 번만 전이한다."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BENCHMARKS = Path(__file__).resolve().parents[1] / "benchmarks"
sys.path.insert(0, str(BENCHMARKS))

from benchmark_contract import (  # type: ignore[import-not-found]  # noqa: E402
    ContractError,
    sha256_file,
)
from run_rotated_blocks import (  # type: ignore[import-not-found]  # noqa: E402
    mark_measurement_entered,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qualification", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        qualification = arguments.qualification.resolve(strict=True)
        mark_measurement_entered(qualification)
        print(
            json.dumps(
                {
                    "qualificationSha256": sha256_file(qualification),
                    "status": "MEASUREMENT",
                },
                sort_keys=True,
            )
        )
    except (ContractError, OSError, ValueError) as exc:
        print(f"BENCHMARK_MEASUREMENT_MARK_FAIL:{exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
