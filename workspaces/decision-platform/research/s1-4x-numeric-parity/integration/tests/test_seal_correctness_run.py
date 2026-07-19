"""Raw correctness closure manifest의 결정성과 filesystem 경계를 검증한다."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

INTEGRATION = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(INTEGRATION))

import seal_correctness_run as sealer  # noqa: E402

SUBJECT = "0123456789abcdef0123456789abcdef01234567"
MANIFEST_NAME = "correctness-run-manifest.v1.json"

ARTIFACTS = {
    "z-last.bin": b"\x00\xffz",
    "a/inside.txt": "내부\n".encode(),
    "a-.txt": b"hyphen",
    "Z-first.txt": b"uppercase",
    "nested/correctness-run-manifest.v1.json": b"nested artifact",
    "evidence-\u00e9.txt": "증거".encode(),
}


def _canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode()


def _populate(root: Path) -> None:
    root.mkdir()
    for relative, payload in reversed(tuple(ARTIFACTS.items())):
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(payload)


def test_seals_deterministic_canonical_complete_snapshot(tmp_path: Path) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    _populate(first_root)
    _populate(second_root)

    first = sealer.seal_correctness_run(
        correctness_root=first_root,
        benchmark_subject_commit=SUBJECT,
    )
    second = sealer.seal_correctness_run(
        correctness_root=second_root,
        benchmark_subject_commit=SUBJECT,
    )

    expected_paths = sorted(ARTIFACTS, key=lambda value: value.encode())
    expected_artifacts = [
        {
            "path": relative,
            "sha256": hashlib.sha256(ARTIFACTS[relative]).hexdigest(),
            "sizeBytes": len(ARTIFACTS[relative]),
        }
        for relative in expected_paths
    ]
    expected = {
        "schemaVersion": "s1.4x-correctness-run-manifest-v1",
        "benchmarkSubjectCommit": SUBJECT,
        "artifactCount": len(expected_artifacts),
        "artifacts": expected_artifacts,
        "status": "PASS",
    }
    first_bytes = (first_root / MANIFEST_NAME).read_bytes()
    second_bytes = (second_root / MANIFEST_NAME).read_bytes()

    assert first == expected
    assert second == expected
    assert first_bytes == _canonical_json_bytes(expected)
    assert second_bytes == first_bytes
    assert MANIFEST_NAME not in {entry["path"] for entry in expected_artifacts}
    assert "nested/correctness-run-manifest.v1.json" in {
        entry["path"] for entry in expected_artifacts
    }


def test_empty_correctness_closure_is_valid(tmp_path: Path) -> None:
    root = tmp_path / "correctness"
    root.mkdir()

    manifest = sealer.seal_correctness_run(
        correctness_root=root,
        benchmark_subject_commit=SUBJECT,
    )

    assert manifest["artifactCount"] == 0
    assert manifest["artifacts"] == []
    assert (root / MANIFEST_NAME).read_bytes() == _canonical_json_bytes(manifest)


def test_cli_uses_only_root_and_subject_and_prints_canonical_manifest(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "correctness"
    root.mkdir()
    (root / "result.json").write_bytes(b"{}\n")

    exit_code = sealer.main(
        [
            "--correctness-root",
            str(root),
            "--benchmark-subject-commit",
            SUBJECT,
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.err == ""
    assert captured.out.encode() == (root / MANIFEST_NAME).read_bytes()


@pytest.mark.parametrize("existing_kind", ["regular", "symlink"])
def test_existing_manifest_path_is_never_replaced(
    tmp_path: Path,
    existing_kind: str,
) -> None:
    root = tmp_path / "correctness"
    root.mkdir()
    manifest = root / MANIFEST_NAME
    if existing_kind == "regular":
        manifest.write_bytes(b"owned")
    else:
        target = tmp_path / "outside"
        target.write_bytes(b"outside")
        manifest.symlink_to(target)

    with pytest.raises(
        sealer.CorrectnessManifestSealError,
        match=r"^MANIFEST_ALREADY_EXISTS$",
    ):
        sealer.seal_correctness_run(
            correctness_root=root,
            benchmark_subject_commit=SUBJECT,
        )

    if existing_kind == "regular":
        assert manifest.read_bytes() == b"owned"
    else:
        assert manifest.is_symlink()
        assert target.read_bytes() == b"outside"


@pytest.mark.parametrize("link_kind", ["leaf", "intermediate"])
def test_symlinks_are_rejected_recursively(
    tmp_path: Path,
    link_kind: str,
) -> None:
    root = tmp_path / "correctness"
    root.mkdir()
    outside = tmp_path / "outside"
    if link_kind == "leaf":
        outside.write_bytes(b"outside")
        (root / "artifact-link").symlink_to(outside)
    else:
        outside.mkdir()
        (outside / "artifact.bin").write_bytes(b"outside")
        (root / "linked-directory").symlink_to(
            outside,
            target_is_directory=True,
        )

    with pytest.raises(
        sealer.CorrectnessManifestSealError,
        match=r"^SYMLINK_FORBIDDEN:",
    ):
        sealer.seal_correctness_run(
            correctness_root=root,
            benchmark_subject_commit=SUBJECT,
        )

    assert not (root / MANIFEST_NAME).exists()


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO requires POSIX")
def test_non_regular_entry_is_rejected_without_opening_it(
    tmp_path: Path,
) -> None:
    root = tmp_path / "correctness"
    root.mkdir()
    os.mkfifo(root / "blocked.fifo")

    with pytest.raises(
        sealer.CorrectnessManifestSealError,
        match=r"^NON_REGULAR_FORBIDDEN:",
    ):
        sealer.seal_correctness_run(
            correctness_root=root,
            benchmark_subject_commit=SUBJECT,
        )

    assert not (root / MANIFEST_NAME).exists()


@pytest.mark.parametrize(
    "subject",
    [
        "",
        "0" * 39,
        "0" * 41,
        "A" * 40,
        "g" * 40,
    ],
)
def test_invalid_benchmark_subject_is_rejected(
    tmp_path: Path,
    subject: str,
) -> None:
    root = tmp_path / "correctness"
    root.mkdir()

    with pytest.raises(
        sealer.CorrectnessManifestSealError,
        match=r"^BENCHMARK_SUBJECT_COMMIT_INVALID$",
    ):
        sealer.seal_correctness_run(
            correctness_root=root,
            benchmark_subject_commit=subject,
        )

    assert not (root / MANIFEST_NAME).exists()


def test_root_must_be_an_absolute_existing_canonical_directory(
    tmp_path: Path,
) -> None:
    canonical = tmp_path / "canonical"
    canonical.mkdir()
    regular = tmp_path / "regular"
    regular.write_bytes(b"not a directory")
    link = tmp_path / "root-link"
    link.symlink_to(canonical, target_is_directory=True)
    invalid_roots = (
        Path("relative-correctness-root"),
        tmp_path / "missing",
        regular,
        canonical / ".." / canonical.name,
        link,
    )

    for invalid_root in invalid_roots:
        with pytest.raises(
            sealer.CorrectnessManifestSealError,
            match=r"^CORRECTNESS_ROOT_INVALID$",
        ):
            sealer.seal_correctness_run(
                correctness_root=invalid_root,
                benchmark_subject_commit=SUBJECT,
            )

    assert not (canonical / MANIFEST_NAME).exists()


@pytest.mark.parametrize("invalid_name", ["back\\slash.bin", "line\nbreak.bin"])
def test_non_portable_relative_path_is_rejected(
    tmp_path: Path,
    invalid_name: str,
) -> None:
    root = tmp_path / "correctness"
    root.mkdir()
    (root / invalid_name).write_bytes(b"invalid portable path")

    with pytest.raises(
        sealer.CorrectnessManifestSealError,
        match=r"^PATH_NOT_PORTABLE$",
    ):
        sealer.seal_correctness_run(
            correctness_root=root,
            benchmark_subject_commit=SUBJECT,
        )

    assert not (root / MANIFEST_NAME).exists()


def test_concurrent_path_replacement_after_nofollow_open_is_rejected(
    tmp_path: Path,
) -> None:
    root = tmp_path / "correctness"
    root.mkdir()
    artifact = root / "artifact.bin"
    artifact.write_bytes(b"original")
    replacement = tmp_path / "replacement.bin"
    replacement.write_bytes(b"replaced")
    fired = False

    def replace_after_open(stage: str, relative: Path) -> None:
        nonlocal fired
        if stage == "after_open" and relative == Path("artifact.bin"):
            os.replace(replacement, artifact)
            fired = True

    with pytest.raises(
        sealer.CorrectnessManifestSealError,
        match=r"^TREE_CHANGED_DURING_SEAL:",
    ):
        sealer.seal_correctness_run(
            correctness_root=root,
            benchmark_subject_commit=SUBJECT,
            _test_hook=replace_after_open,
        )

    assert fired
    assert artifact.read_bytes() == b"replaced"
    assert not (root / MANIFEST_NAME).exists()


def test_concurrent_same_size_tamper_during_read_is_rejected(
    tmp_path: Path,
) -> None:
    root = tmp_path / "correctness"
    root.mkdir()
    artifact = root / "artifact.bin"
    artifact.write_bytes(b"original")
    fired = False

    def tamper_after_read(stage: str, relative: Path) -> None:
        nonlocal fired
        if stage == "after_read" and relative == Path("artifact.bin"):
            artifact.write_bytes(b"tampered")
            fired = True

    with pytest.raises(
        sealer.CorrectnessManifestSealError,
        match=r"^TREE_CHANGED_DURING_SEAL:",
    ):
        sealer.seal_correctness_run(
            correctness_root=root,
            benchmark_subject_commit=SUBJECT,
            _test_hook=tamper_after_read,
        )

    assert fired
    assert artifact.stat().st_size == len(b"original")
    assert not (root / MANIFEST_NAME).exists()


def test_artifact_added_immediately_after_manifest_write_is_rejected(
    tmp_path: Path,
) -> None:
    root = tmp_path / "correctness"
    root.mkdir()
    (root / "artifact.bin").write_bytes(b"original")
    fired = False

    def add_after_manifest_write(stage: str, relative: Path) -> None:
        nonlocal fired
        if stage == "after_manifest_write" and relative == Path():
            (root / "late-artifact.bin").write_bytes(b"late")
            fired = True

    with pytest.raises(
        sealer.CorrectnessManifestSealError,
        match=r"^TREE_CHANGED_DURING_SEAL:late-artifact\.bin$",
    ):
        sealer.seal_correctness_run(
            correctness_root=root,
            benchmark_subject_commit=SUBJECT,
            _test_hook=add_after_manifest_write,
        )

    assert fired
    assert (root / "late-artifact.bin").read_bytes() == b"late"
    assert not (root / MANIFEST_NAME).exists()
