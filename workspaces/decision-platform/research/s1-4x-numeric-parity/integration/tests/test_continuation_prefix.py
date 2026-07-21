"""Continuation source manifest import의 최소 보안·closure 계약을 검증한다."""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

INTEGRATION = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(INTEGRATION))

import continuation_prefix as continuation  # noqa: E402

PARENT = "1" * 40
TARGET = "2" * 40


def test_continuation_diff_allows_completion_and_hash_reseal_files() -> None:
    """봉인 run 재개에 필요한 completion과 stale hash 재봉인만 허용한다."""
    expected = {
        str(
            continuation.S1_ROOT
            / "integration/assemble_final_candidate_evidence.py"
        ),
        str(continuation.S1_ROOT / "integration/coverage_execution.py"),
        str(continuation.S1_ROOT / "integration/gate.py"),
        str(continuation.S1_ROOT / "integration/final_candidate_audit.py"),
        str(continuation.S1_ROOT / "integration/run_full_correctness.py"),
        str(
            continuation.S1_ROOT
            / "integration/tools/run-haskell-candidate.sh"
        ),
        str(
            continuation.S1_ROOT
            / "integration/tools/run-integration-correctness.sh"
        ),
        str(
            continuation.S1_ROOT
            / "integration/tests/test_assemble_final_candidate_evidence.py"
        ),
        str(
            continuation.S1_ROOT
            / "integration/tests/test_coverage_execution.py"
        ),
        str(continuation.S1_ROOT / "integration/tests/test_gate.py"),
        str(
            continuation.S1_ROOT / "reports/integration-baseline.v1.json"
        ),
    }

    assert expected <= continuation.CONTINUATION_DIFF_ALLOWLIST


def _sealed_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, Path, bytes]:
    repo = tmp_path / "repo"
    repo.mkdir()
    source = tmp_path / "source"
    source.mkdir()
    payload = b"sealed-prefix\n"
    (source / "artifact.json").write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    artifact = {
        "sourceId": "fixture",
        "sourceRelativePath": "artifact.json",
        "destinationPath": "scala/profiles/A/artifact.json",
        "sha256": digest,
        "sizeBytes": len(payload),
    }
    tree_entry = {
        "sourceRelativePath": artifact["sourceRelativePath"],
        "destinationPath": artifact["destinationPath"],
        "sha256": digest,
        "sizeBytes": len(payload),
    }
    manifest = {
        "schemaVersion": continuation.SOURCE_MANIFEST_SCHEMA,
        "parentRunId": "parent-run",
        "parentSubject": PARENT,
        "targetSubject": TARGET,
        "failedStage": continuation.FAILED_STAGE,
        "currentDiffPaths": ["allowed"],
        "sourceCommits": {
            "scalaQualification": continuation.SCALA_QUALIFICATION_SOURCE_COMMIT,
            "haskellProfile": continuation.HASKELL_PROFILE_SOURCE_COMMIT,
        },
        "sourceTrees": [
            {
                "sourceId": "fixture",
                "sourceRoot": str(source),
                "sourceRelativePath": ".",
                "destinationRelativePath": ".",
                "excludedSubtrees": [],
                "bindings": {},
                "artifactCount": 1,
                "totalSizeBytes": len(payload),
                "treeSha256": continuation._canonical_sha256([tree_entry]),
            }
        ],
        "artifactCount": 1,
        "artifacts": [artifact],
        "status": "SEALED",
    }
    manifest_path = tmp_path / continuation.SOURCE_MANIFEST_NAME
    manifest_path.write_bytes(continuation._canonical_json_bytes(manifest))
    monkeypatch.setattr(
        continuation,
        "validate_current_diff",
        lambda *_args, **_kwargs: ("allowed",),
    )
    return repo, manifest_path, payload


def test_import_copies_exact_closure_and_writes_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, manifest, payload = _sealed_manifest(tmp_path, monkeypatch)
    output = tmp_path / "fresh-correctness"
    output.mkdir()

    receipt = continuation.import_continuation_prefix(
        repository_root=repo,
        manifest_path=manifest,
        output_root=output,
    )

    assert receipt["status"] == "PASS"
    assert receipt["parentSubject"] == PARENT
    assert receipt["currentSubject"] == TARGET
    assert receipt["importedArtifactCount"] == 1
    assert (output / "scala/profiles/A/artifact.json").read_bytes() == payload
    assert (output / continuation.IMPORT_RECEIPT_NAME).read_bytes() == (
        continuation._canonical_json_bytes(receipt)
    )


def test_import_rejects_source_drift_before_creating_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, manifest, _payload = _sealed_manifest(tmp_path, monkeypatch)
    (tmp_path / "source/artifact.json").write_bytes(b"drift\n")
    output = tmp_path / "fresh-correctness"
    output.mkdir()

    with pytest.raises(
        continuation.ContinuationPrefixError,
        match=r"^SOURCE_ARTIFACT_DRIFT:",
    ):
        continuation.import_continuation_prefix(
            repository_root=repo,
            manifest_path=manifest,
            output_root=output,
        )

    assert list(output.iterdir()) == []


@pytest.mark.parametrize("existing_kind", ["nonempty", "symlink"])
def test_import_never_overwrites_existing_output_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    existing_kind: str,
) -> None:
    repo, manifest, _payload = _sealed_manifest(tmp_path, monkeypatch)
    output = tmp_path / "fresh-correctness"
    if existing_kind == "nonempty":
        output.mkdir()
        (output / "owned").write_bytes(b"owned")
    else:
        outside = tmp_path / "outside"
        outside.mkdir()
        output.symlink_to(outside, target_is_directory=True)

    with pytest.raises(
        continuation.ContinuationPrefixError,
        match=r"^(OUTPUT_ROOT_MUST_BE_EMPTY$|DIRECTORY_INVALID:)",
    ):
        continuation.import_continuation_prefix(
            repository_root=repo,
            manifest_path=manifest,
            output_root=output,
        )
