#!/usr/bin/env python3
"""Frozen invalid binary manifest catalog의 exit-65 및 semantic exit-0을 전부 재생한다."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from gate import (
    GateError,
    exclusive_json_write,
    run_candidate,
    run_transport_case,
    strict_json_load,
)

Runner = Callable[..., subprocess.CompletedProcess[bytes]]


def _catalog_entries(invalid_root: Path) -> list[dict[str, Any]]:
    catalog = strict_json_load(invalid_root / "invalid-fixtures.v1.json")
    if (
        not isinstance(catalog, dict)
        or catalog.get("schemaVersion")
        != "s1.4x-invalid-fixture-catalog-v1"
        or not isinstance(catalog.get("entries"), list)
    ):
        raise GateError("INVALID_FIXTURE_CATALOG")
    entries = [
        item
        for item in catalog["entries"]
        if isinstance(item, dict)
        and isinstance(item.get("file"), str)
        and item["file"].startswith("manifest-")
    ]
    if len(entries) != 12:
        raise GateError(f"INVALID_MANIFEST_CATALOG_COUNT:{len(entries)}")
    return entries


def _materialize_case(
    *,
    invalid_root: Path,
    case_root: Path,
    entry: dict[str, Any],
    index: int,
) -> tuple[Path, Path, str | None]:
    fixtures = case_root / "fixtures"
    fixtures.mkdir(parents=True)
    source_manifest = invalid_root / entry["file"]
    manifest = strict_json_load(source_manifest)
    if not isinstance(manifest, dict):
        raise GateError(f"INVALID_MANIFEST_DOCUMENT:{entry['file']}")
    shutil.copyfile(source_manifest, fixtures / entry["file"])
    generator = manifest.get("generator")
    payload_hex = generator.get("payloadHex") if isinstance(generator, dict) else None
    file_name = manifest.get("fileName")
    if (
        isinstance(payload_hex, str)
        and isinstance(file_name, str)
        and "/" not in file_name
        and "\\" not in file_name
    ):
        try:
            payload = bytes.fromhex(payload_hex)
        except ValueError as exc:
            raise GateError(f"INVALID_LITERAL_PAYLOAD:{entry['file']}") from exc
        binary_path = fixtures / file_name
        if entry.get("binaryPlacement") == "symlink-escape":
            outside = case_root / "outside.f64le"
            outside.write_bytes(payload)
            binary_path.symlink_to(outside)
        else:
            binary_path.write_bytes(payload)
    expected_semantic = manifest.get("expectedSemanticError")
    fixture_id = entry["fixtureId"]
    request = {
        "schemaVersion": "s1.4x-request-v1",
        "requestId": f"binary-replay-{index:02d}",
        "cases": [
            {
                "fixtureId": fixture_id,
                "functionId": "cumulative_return",
                "arguments": {
                    "returns": {
                        "kind": "binaryFloat64",
                        "manifestFile": entry["file"],
                    }
                },
                **(
                    {"expectedSemanticError": expected_semantic}
                    if isinstance(expected_semantic, str)
                    else {}
                ),
            }
        ],
    }
    request_path = case_root / "request.json"
    exclusive_json_write(request_path, request)
    return request_path, fixtures, (
        expected_semantic if isinstance(expected_semantic, str) else None
    )


def replay_binary_catalog(
    *,
    candidate: Path,
    invalid_root: Path,
    output_directory: Path,
    report_path: Path,
    runner: Runner = subprocess.run,
) -> dict[str, Any]:
    """Catalog의 12 manifest를 격리된 fixture root에서 candidate process로 실행한다."""

    candidate_path = candidate.resolve(strict=True)
    invalid = invalid_root.resolve(strict=True)
    output = output_directory.resolve()
    report = report_path.resolve()
    if output.exists() or output.is_symlink() or report.exists() or report.is_symlink():
        raise GateError("BINARY_REPLAY_OUTPUT_ALREADY_EXISTS")
    output.mkdir(parents=True)
    results = []
    for index, entry in enumerate(_catalog_entries(invalid), start=1):
        fixture_id = entry["fixtureId"]
        case_root = output / fixture_id
        request_path, fixtures, expected_semantic = _materialize_case(
            invalid_root=invalid,
            case_root=case_root,
            entry=entry,
            index=index,
        )
        result_path = case_root / "candidate-result.json"
        disposition = entry.get("expectedDisposition")
        if disposition == "exit-0-input_non_finite":
            candidate_result = run_candidate(
                label=f"{candidate_path.name}/{fixture_id}",
                command_template=[str(candidate_path), "{protocol_args}"],
                request_path=request_path,
                fixture_root=fixtures,
                output_path=result_path,
                runner=runner,
            )
            case_result = candidate_result["results"][0]
            if (
                expected_semantic != "input_non_finite"
                or case_result.get("status") != "error"
                or case_result.get("errorCode") != expected_semantic
            ):
                raise GateError(f"BINARY_SEMANTIC_RESULT_MISMATCH:{fixture_id}")
            results.append(
                {
                    "fixtureId": fixture_id,
                    "expectedDisposition": disposition,
                    "actualExit": 0,
                    "actualCode": expected_semantic,
                    "status": "PASS",
                }
            )
            continue
        expected_code = (
            "binary_invalid"
            if disposition == "exit-65-binary_invalid"
            else "manifest_invalid"
        )
        if disposition not in {
            "exit-65-manifest_invalid",
            "exit-65-binary_invalid",
        }:
            raise GateError(f"BINARY_DISPOSITION_INVALID:{fixture_id}")
        transport = run_transport_case(
            label=f"{candidate_path.name}/{fixture_id}",
            command_template=[str(candidate_path), "{protocol_args}"],
            request_path=request_path,
            fixture_root=fixtures,
            output_path=result_path,
            expected_exit=65,
            expected_code=expected_code,
            runner=runner,
        )
        results.append(
            {
                "fixtureId": fixture_id,
                "expectedDisposition": disposition,
                "actualExit": 65,
                "actualCode": transport["code"],
                "status": "PASS",
            }
        )
    document = {
        "schemaVersion": "s1.4x-binary-contract-replay-v1",
        "candidate": candidate_path.name,
        "caseCount": len(results),
        "transportFailureCount": sum(item["actualExit"] == 65 for item in results),
        "semanticErrorCount": sum(item["actualExit"] == 0 for item in results),
        "cases": results,
        "status": "PASS",
    }
    exclusive_json_write(report, document)
    return document


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--invalid-root", type=Path, required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        report = replay_binary_catalog(
            candidate=arguments.candidate,
            invalid_root=arguments.invalid_root,
            output_directory=arguments.output_directory,
            report_path=arguments.report,
        )
        print(json.dumps(report, allow_nan=False, sort_keys=True))
    except (GateError, OSError, ValueError) as exc:
        print(f"S1_4X_BINARY_REPLAY_FAIL:{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
