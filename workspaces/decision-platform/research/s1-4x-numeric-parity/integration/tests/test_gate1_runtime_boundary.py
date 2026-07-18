"""Gate 1 immutable 입력과 Gate 2 runtime/report 경계를 고정한다."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

S1_4X = Path(__file__).resolve().parents[2]
CONTRACT_MANIFEST = S1_4X / "contract/contract-manifest.v1.json"
BASELINE_REPORT = S1_4X / "reports/integration-baseline.v1.json"
INTEGRATION_README = S1_4X / "integration/README.md"
SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def test_gate1_benchmark_root_matches_frozen_manifest_exactly() -> None:
    manifest = json.loads(CONTRACT_MANIFEST.read_text(encoding="utf-8"))
    frozen = next(
        root
        for root in manifest["immutableRoots"]
        if root["root"].endswith("/benchmarks")
    )
    benchmark_root = S1_4X / "benchmarks"
    expected = {entry["path"]: entry["sha256"] for entry in frozen["files"]}
    actual_paths: set[str] = set()
    for pattern in frozen["includeGlobs"]:
        actual_paths.update(
            path.relative_to(benchmark_root).as_posix()
            for path in benchmark_root.glob(pattern)
            if path.is_file() and not path.is_symlink()
        )

    assert actual_paths == set(expected)
    assert len(expected) == frozen["fileCount"]
    for relative, expected_sha256 in expected.items():
        assert _sha256(benchmark_root / relative) == expected_sha256


def test_gate2_baseline_report_has_local_non_contract_format() -> None:
    report = json.loads(BASELINE_REPORT.read_text(encoding="utf-8"))
    assert set(report) == {
        "reportFormat",
        "issue",
        "originMainBaseSha",
        "originMainBaseTreeSha",
        "gate0MergeSha",
        "fixtureFreezeMergeSha",
        "fixtureFreezeReviewedHeadSha",
        "immutableInputs",
        "freezeSemantics",
    }
    assert report["reportFormat"] == "s1.4x-integration-baseline-v1"
    assert report["freezeSemantics"] == "identity-record-only"
    assert report["issue"] == 26
    assert set(report["immutableInputs"]) == {
        "benchmarkPlanSha256",
        "canonicalInputsSha256",
        "canonicalResultsSha256",
        "contractCanonicalRootSha256",
        "contractManifestSha256",
        "oracleCanonicalRootSha256",
        "referenceLockSha256",
        "toolchainProvenanceSha256",
    }
    for field in (
        "originMainBaseSha",
        "gate0MergeSha",
        "fixtureFreezeMergeSha",
        "fixtureFreezeReviewedHeadSha",
    ):
        assert re.fullmatch(r"[0-9a-f]{40}", report[field])
    for digest in report["immutableInputs"].values():
        assert SHA256.fullmatch(digest)


def test_official_full_rotation_uses_integration_runtime_only() -> None:
    documentation = INTEGRATION_README.read_text(encoding="utf-8")
    assert 'python "$S1_4X/integration/run_rotated_blocks.py" run \\' in documentation
    assert 'python "$S1_4X/benchmarks/run_rotated_blocks.py" run \\' not in documentation
