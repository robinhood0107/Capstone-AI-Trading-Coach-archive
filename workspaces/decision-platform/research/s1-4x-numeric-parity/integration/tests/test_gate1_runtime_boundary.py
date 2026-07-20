"""Gate 1 immutable 입력과 Gate 2 runtime/report 경계를 고정한다."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

S1_4X = Path(__file__).resolve().parents[2]
CONTRACT_MANIFEST = S1_4X / "contract/contract-manifest.v1.json"
BASELINE_REPORT = S1_4X / "reports/integration-baseline.v1.json"
INTEGRATION_README = S1_4X / "integration/README.md"
INTEGRATION = S1_4X / "integration"
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
        "hostPolicyAmendment",
        "immutableInputs",
        "freezeSemantics",
    }
    assert report["reportFormat"] == "s1.4x-integration-baseline-v1"
    assert (
        report["freezeSemantics"]
        == "identity-record-with-user-approved-host-policy-amendment"
    )
    assert report["hostPolicyAmendment"] == {
        "approvalToken": "S1_4X_BENCHMARK_HOST_POLICY_APPROVED",
        "containerIdentitySemantics": "count-only-unverified",
        "evidenceScope": "same-run-same-host",
        "maximumRunningContainers": {"before": 0, "after": 4},
        "minimumAvailableMemoryGiB": {"before": 8, "after": 4},
    }
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


def test_contract_manifest_hash_consumers_match_current_bytes() -> None:
    # Manifest closure 갱신 뒤 baseline과 최종 audit pin을 함께 바꾸지 않으면
    # 비싼 aggregate가 마지막 조립 단계에서 실패하므로 시작 전에 고정한다.
    sys.path.insert(0, str(INTEGRATION))
    import final_candidate_audit as audit_module

    manifest = json.loads(CONTRACT_MANIFEST.read_text(encoding="utf-8"))
    report = json.loads(BASELINE_REPORT.read_text(encoding="utf-8"))
    current_sha256 = _sha256(CONTRACT_MANIFEST)
    canonical_roots = {
        Path(root["root"]).name: root["canonicalManifestSha256"]
        for root in manifest["immutableRoots"]
    }

    assert report["immutableInputs"]["contractManifestSha256"] == current_sha256
    assert (
        report["immutableInputs"]["contractCanonicalRootSha256"]
        == canonical_roots["contract"]
    )
    assert (
        report["immutableInputs"]["oracleCanonicalRootSha256"]
        == canonical_roots["oracle"]
    )
    assert audit_module.FROZEN_CONTRACT_MANIFEST_SHA256 == current_sha256


def test_official_full_rotation_uses_integration_runtime_only() -> None:
    documentation = INTEGRATION_README.read_text(encoding="utf-8")
    assert 'python "$S1_4X/integration/run_rotated_blocks.py" run \\' in documentation
    assert 'python "$S1_4X/benchmarks/run_rotated_blocks.py" run \\' not in documentation


def test_integration_runbook_has_no_local_user_or_global_tmp_path() -> None:
    documentation = INTEGRATION_README.read_text(encoding="utf-8")
    assert re.search(r"/home/[^/\s]+", documentation) is None
    assert "TMPDIR=/tmp" not in documentation
    assert 'CACHE_ROOT="${S1_4X_CACHE_ROOT:-$HOME/.cache/s1-4x}"' in documentation


def test_official_runtime_keeps_the_internal_compatibility_surface() -> None:
    # 통합 runtime과 frozen runner의 동명 모듈 충돌이 있어도 기존 호출자는 같은 경계를 본다.
    script = (
        "import sys;"
        f"sys.path.insert(0, {str(INTEGRATION)!r});"
        "import run_rotated_blocks as runner;"
        "assert runner.ScheduledBlock;"
        "assert callable(runner.build_schedule);"
        "assert callable(runner.mark_measurement_entered);"
        "assert callable(runner.main)"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
