"""S1.4X 대용량 fixture materializer의 output-bound 및 receipt 계약 테스트."""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

S1_4X_ROOT = Path(__file__).resolve().parents[2]
TOOL = S1_4X_ROOT / "integration" / "materialize_large_fixtures.py"
SOURCE_LARGE = S1_4X_ROOT / "contract" / "fixtures" / "large"
GENERATOR_SHA256 = "4e19845c1d1d030dbab3f40527745c3f7803062958b38c3441937ff1674e9d00"
RECEIPT_SCHEMA = "s1.4x-large-fixture-materialization-receipt-v1"
TREE_SCHEMA = "s1.4x-large-fixture-tree-v1"
ROOT_PATH_ID = "S1_4X_LARGE_FIXTURE_ROOT"

MANIFEST_EXPECTATIONS = {
    "large-coverage-forecast-var-n3200000.manifest.json": (
        688,
        "f4c2eeab713a948bfd645dcd43457c0a90c38340f4e66043a8a622f452797142",
        "large-coverage-forecast-var-n3200000.f64le",
        25_600_000,
        "e5e635b28e4025bc1fa71f7c6b92fbf3807861814d3b6010a751ea0e81168d14",
    ),
    "large-coverage-realized-losses-n3200000.manifest.json": (
        696,
        "68b5c6c8e2eb5f502e7297ffdf63b3b635cce9131e27e37dfd1fb578a5e784b8",
        "large-coverage-realized-losses-n3200000.f64le",
        25_600_000,
        "a9bf46f0f836e4fe386723ac517f6caba2ddf31289113ef38a6b6da3fed29139",
    ),
    "large-prices-n100000.manifest.json": (
        648,
        "778abae4d621653b448a40b2b854cdf0f2e6fc63b7f439bdde96aaba9b83e7b5",
        "large-prices-n100000.f64le",
        800_000,
        "a37153a538130dc2118e4f2c8029a5e4becabd3272d964308bc3200232049c12",
    ),
    "large-returns-n100000.manifest.json": (
        651,
        "10000aaf12ae80ba5d813ebf3012753d142088df19742e90a52467ca2c93f99a",
        "large-returns-n100000.f64le",
        800_000,
        "f81251d60ae5c411ef8eb5df83524375c53af411566060e497c2d6cf86988554",
    ),
}


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _materializer_module() -> ModuleType:
    sys.path.insert(0, str(S1_4X_ROOT / "integration"))
    return importlib.import_module("materialize_large_fixtures")


def _expected_receipt() -> dict[str, Any]:
    manifest_entries = []
    payload_entries = []
    for manifest_name, (
        manifest_length,
        manifest_sha,
        payload_name,
        payload_length,
        payload_sha,
    ) in sorted(MANIFEST_EXPECTATIONS.items()):
        manifest_path = f"large/{manifest_name}"
        manifest_entries.append(
            {
                "path": manifest_path,
                "byteLength": manifest_length,
                "sha256": manifest_sha,
            }
        )
        payload_entries.append(
            {
                "path": f"large/generated/{payload_name}",
                "manifestPath": manifest_path,
                "byteLength": payload_length,
                "sha256": payload_sha,
            }
        )
    tree = {
        "schemaVersion": TREE_SCHEMA,
        "manifestEntries": manifest_entries,
        "payloadEntries": payload_entries,
    }
    return {
        "schemaVersion": RECEIPT_SCHEMA,
        "status": "PASS",
        "generatorSha256": GENERATOR_SHA256,
        "materializedRootPathId": ROOT_PATH_ID,
        "manifestEntries": manifest_entries,
        "payloadEntries": payload_entries,
        "fixtureTreeSha256": _sha256(_canonical_bytes(tree)),
    }


def _run(
    mode: str,
    *,
    output_root: Path,
    receipt: Path,
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [
            sys.executable,
            str(TOOL),
            mode,
            "--s1-4x-root",
            str(S1_4X_ROOT),
            "--output-root",
            str(output_root),
            "--receipt",
            str(receipt),
        ],
        cwd=S1_4X_ROOT,
        capture_output=True,
        check=False,
        timeout=120,
    )


def _assert_rejected(
    completed: subprocess.CompletedProcess[bytes],
    error_code: str,
) -> None:
    assert completed.returncode == 2
    assert completed.stdout == b""
    assert completed.stderr == f"LARGE_FIXTURE_MATERIALIZATION_FAIL:{error_code}\n".encode()


@pytest.fixture(scope="module")
def materialized(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[tuple[Path, Path, bytes]]:
    root = tmp_path_factory.mktemp("large-fixture-materialization")
    output_root = root / "fixture-root"
    receipt = root / "receipt.json"
    completed = _run("materialize", output_root=output_root, receipt=receipt)
    assert completed.returncode == 0, completed.stderr.decode("utf-8", errors="replace")
    assert completed.stderr == b""
    canonical_receipt = _canonical_bytes(_expected_receipt())
    assert completed.stdout == canonical_receipt
    assert receipt.read_bytes() == canonical_receipt
    yield output_root, receipt, canonical_receipt


def test_materialize_creates_exact_output_bound_tree_and_canonical_receipt(
    materialized: tuple[Path, Path, bytes],
) -> None:
    output_root, receipt, canonical_receipt = materialized
    large_root = output_root / "large"
    generated_root = large_root / "generated"

    assert {entry.name for entry in os.scandir(output_root)} == {"large"}
    assert {entry.name for entry in os.scandir(large_root)} == {
        *MANIFEST_EXPECTATIONS,
        "generated",
    }
    assert {entry.name for entry in os.scandir(generated_root)} == {
        expectation[2] for expectation in MANIFEST_EXPECTATIONS.values()
    }
    assert large_root.is_dir() and not large_root.is_symlink()
    assert generated_root.is_dir() and not generated_root.is_symlink()

    for manifest_name, expectation in MANIFEST_EXPECTATIONS.items():
        manifest_path = large_root / manifest_name
        payload_path = generated_root / expectation[2]
        assert manifest_path.is_file() and not manifest_path.is_symlink()
        assert payload_path.is_file() and not payload_path.is_symlink()
        assert manifest_path.read_bytes() == (SOURCE_LARGE / manifest_name).read_bytes()
        assert manifest_path.stat().st_size == expectation[0]
        assert _sha256(manifest_path.read_bytes()) == expectation[1]
        assert payload_path.stat().st_size == expectation[3]
        assert _sha256(payload_path.read_bytes()) == expectation[4]

    receipt_value = json.loads(receipt.read_bytes())
    assert receipt_value == _expected_receipt()
    assert str(output_root).encode() not in canonical_receipt

    checked = _run("check", output_root=output_root, receipt=receipt)
    assert checked.returncode == 0
    assert checked.stderr == b""
    assert checked.stdout == canonical_receipt


def test_materialize_rejects_preexisting_output_receipt_and_symlink(
    tmp_path: Path,
) -> None:
    output_root = tmp_path / "preexisting-output"
    output_root.mkdir()
    receipt = tmp_path / "receipt.json"
    _assert_rejected(
        _run("materialize", output_root=output_root, receipt=receipt),
        "OUTPUT_ROOT_ALREADY_EXISTS",
    )

    output_root.rmdir()
    receipt.write_bytes(b"occupied")
    _assert_rejected(
        _run("materialize", output_root=output_root, receipt=receipt),
        "RECEIPT_ALREADY_EXISTS",
    )
    receipt.unlink()

    target = tmp_path / "target"
    target.mkdir()
    output_root.symlink_to(target, target_is_directory=True)
    _assert_rejected(
        _run("materialize", output_root=output_root, receipt=receipt),
        "OUTPUT_ROOT_ALREADY_EXISTS",
    )


def test_generator_executes_the_verified_fd_and_rechecks_identity(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    materializer = _materializer_module()
    generator = S1_4X_ROOT / "oracle" / "generate_large_fixtures.py"
    identity_checks: list[tuple[int, Path]] = []
    original_verify = materializer._verify_generator_fd_and_path

    def counting_verify(descriptor: int, path: Path) -> tuple[int, int]:
        identity_checks.append((descriptor, path))
        return original_verify(descriptor, path)

    def fake_run(
        command: list[str],
        **kwargs: Any,
    ) -> subprocess.CompletedProcess[bytes]:
        passed_descriptors = kwargs["pass_fds"]
        assert isinstance(passed_descriptors, tuple)
        assert len(passed_descriptors) == 1
        descriptor = passed_descriptors[0]
        assert command[:2] == ["/proc/self/exe", f"/proc/self/fd/{descriptor}"]
        assert os.fstat(descriptor).st_size == generator.stat().st_size
        return subprocess.CompletedProcess(command, 0, b"verified-report\n", b"")

    monkeypatch.setattr(
        materializer,
        "_verify_generator_fd_and_path",
        counting_verify,
    )
    monkeypatch.setattr(materializer, "_validate_generator_report", lambda _: None)
    monkeypatch.setattr(materializer.subprocess, "run", fake_run)

    materializer._run_generator(
        generator=generator,
        manifest_root=SOURCE_LARGE,
        s1_4x_root=S1_4X_ROOT,
        generated_root=tmp_path,
    )

    assert [path for _, path in identity_checks] == [generator, generator]
    assert identity_checks[0][0] == identity_checks[1][0]


def test_materialized_manifest_is_validated_against_static_bytes_and_contract(
    tmp_path: Path,
) -> None:
    materializer = _materializer_module()
    fixture = next(
        item
        for item in materializer.FIXTURES
        if item.manifest_name == "large-prices-n100000.manifest.json"
    )
    manifest_path = tmp_path / fixture.manifest_name
    valid_bytes = (SOURCE_LARGE / fixture.manifest_name).read_bytes()
    manifest_path.write_bytes(valid_bytes)

    materializer._validate_materialized_manifest(manifest_path, fixture)

    manifest_path.write_bytes(valid_bytes + b" ")
    with pytest.raises(
        materializer.MaterializationError,
        match="^MANIFEST_BYTES_MISMATCH$",
    ):
        materializer._validate_materialized_manifest(manifest_path, fixture)

    invalid_contract = json.loads(valid_bytes)
    invalid_contract["fileName"] = "wrong-payload.f64le"
    invalid_bytes = _canonical_bytes(invalid_contract)
    contract_fixture = materializer.FixtureExpectation(
        manifest_name=fixture.manifest_name,
        manifest_byte_length=len(invalid_bytes),
        manifest_sha256=_sha256(invalid_bytes),
        payload_name=fixture.payload_name,
        payload_byte_length=fixture.payload_byte_length,
        payload_sha256=fixture.payload_sha256,
    )
    manifest_path.write_bytes(invalid_bytes)
    with pytest.raises(
        materializer.MaterializationError,
        match="^MANIFEST_CONTRACT_MISMATCH$",
    ):
        materializer._validate_materialized_manifest(manifest_path, contract_fixture)


def test_check_rejects_extra_and_tampered_output(
    materialized: tuple[Path, Path, bytes],
) -> None:
    output_root, receipt, _ = materialized
    generated_root = output_root / "large" / "generated"

    extra = generated_root / "unexpected.bin"
    extra.write_bytes(b"unexpected")
    try:
        _assert_rejected(
            _run("check", output_root=output_root, receipt=receipt),
            "OUTPUT_TREE_CLOSURE_INVALID",
        )
    finally:
        extra.unlink()

    payload = generated_root / "large-prices-n100000.f64le"
    original_payload = payload.read_bytes()
    payload.write_bytes(bytes([original_payload[0] ^ 1]) + original_payload[1:])
    try:
        _assert_rejected(
            _run("check", output_root=output_root, receipt=receipt),
            "PAYLOAD_HASH_MISMATCH",
        )
    finally:
        payload.write_bytes(original_payload)

    manifest = output_root / "large" / "large-prices-n100000.manifest.json"
    original_manifest = manifest.read_bytes()
    manifest.write_bytes(original_manifest + b" ")
    try:
        _assert_rejected(
            _run("check", output_root=output_root, receipt=receipt),
            "MANIFEST_BYTES_MISMATCH",
        )
    finally:
        manifest.write_bytes(original_manifest)


def test_check_rejects_output_symlink_and_receipt_tamper(
    materialized: tuple[Path, Path, bytes],
) -> None:
    output_root, receipt, canonical_receipt = materialized
    generated_root = output_root / "large" / "generated"
    payload = generated_root / "large-prices-n100000.f64le"
    original_payload = payload.read_bytes()
    payload.unlink()
    payload.symlink_to(generated_root / "large-returns-n100000.f64le")
    try:
        _assert_rejected(
            _run("check", output_root=output_root, receipt=receipt),
            "OUTPUT_SYMLINK_FORBIDDEN",
        )
    finally:
        payload.unlink()
        payload.write_bytes(original_payload)

    tampered_receipt = json.loads(canonical_receipt)
    tampered_receipt["unexpected"] = True
    receipt.write_bytes(_canonical_bytes(tampered_receipt))
    try:
        _assert_rejected(
            _run("check", output_root=output_root, receipt=receipt),
            "RECEIPT_MISMATCH",
        )
    finally:
        receipt.write_bytes(canonical_receipt)
