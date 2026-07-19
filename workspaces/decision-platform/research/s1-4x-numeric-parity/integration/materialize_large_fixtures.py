"""Frozen generator로 output-bound 대용량 fixture와 검증 receipt를 만든다."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import site
import stat
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

EXPECTED_PYTHON = (3, 12, 13)
GENERATOR_RELATIVE_PATH = Path("oracle") / "generate_large_fixtures.py"
GENERATOR_SHA256 = "4e19845c1d1d030dbab3f40527745c3f7803062958b38c3441937ff1674e9d00"
RECEIPT_SCHEMA = "s1.4x-large-fixture-materialization-receipt-v1"
TREE_SCHEMA = "s1.4x-large-fixture-tree-v1"
ROOT_PATH_ID = "S1_4X_LARGE_FIXTURE_ROOT"
FileIdentity = tuple[int, int]


class MaterializationError(RuntimeError):
    """외부 경로를 노출하지 않는 stable fail-closed 오류 코드를 보관한다."""


@dataclass(frozen=True, slots=True)
class FixtureExpectation:
    """Frozen manifest와 그 manifest가 선언한 payload 계약을 묶는다."""

    manifest_name: str
    manifest_byte_length: int
    manifest_sha256: str
    payload_name: str
    payload_byte_length: int
    payload_sha256: str

    @property
    def manifest_relative_path(self) -> str:
        return f"large/{self.manifest_name}"

    @property
    def payload_relative_path(self) -> str:
        return f"large/generated/{self.payload_name}"


FIXTURES = (
    FixtureExpectation(
        manifest_name="large-coverage-forecast-var-n3200000.manifest.json",
        manifest_byte_length=688,
        manifest_sha256=(
            "f4c2eeab713a948bfd645dcd43457c0a90c38340f4e66043a8a622f452797142"
        ),
        payload_name="large-coverage-forecast-var-n3200000.f64le",
        payload_byte_length=25_600_000,
        payload_sha256=(
            "e5e635b28e4025bc1fa71f7c6b92fbf3807861814d3b6010a751ea0e81168d14"
        ),
    ),
    FixtureExpectation(
        manifest_name="large-coverage-realized-losses-n3200000.manifest.json",
        manifest_byte_length=696,
        manifest_sha256=(
            "68b5c6c8e2eb5f502e7297ffdf63b3b635cce9131e27e37dfd1fb578a5e784b8"
        ),
        payload_name="large-coverage-realized-losses-n3200000.f64le",
        payload_byte_length=25_600_000,
        payload_sha256=(
            "a9bf46f0f836e4fe386723ac517f6caba2ddf31289113ef38a6b6da3fed29139"
        ),
    ),
    FixtureExpectation(
        manifest_name="large-prices-n100000.manifest.json",
        manifest_byte_length=648,
        manifest_sha256=(
            "778abae4d621653b448a40b2b854cdf0f2e6fc63b7f439bdde96aaba9b83e7b5"
        ),
        payload_name="large-prices-n100000.f64le",
        payload_byte_length=800_000,
        payload_sha256=(
            "a37153a538130dc2118e4f2c8029a5e4becabd3272d964308bc3200232049c12"
        ),
    ),
    FixtureExpectation(
        manifest_name="large-returns-n100000.manifest.json",
        manifest_byte_length=651,
        manifest_sha256=(
            "10000aaf12ae80ba5d813ebf3012753d142088df19742e90a52467ca2c93f99a"
        ),
        payload_name="large-returns-n100000.f64le",
        payload_byte_length=800_000,
        payload_sha256=(
            "f81251d60ae5c411ef8eb5df83524375c53af411566060e497c2d6cf86988554"
        ),
    ),
)


def _canonical_json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise MaterializationError("CANONICAL_JSON_INVALID") from exc


def _strict_json_load(payload: bytes) -> Any:
    def reject_constant(_: str) -> None:
        raise ValueError("non-finite JSON constant")

    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("duplicate JSON key")
            value[key] = item
        return value

    try:
        return json.loads(
            payload,
            parse_constant=reject_constant,
            object_pairs_hook=reject_duplicate_keys,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise MaterializationError("JSON_INVALID") from exc


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
    except OSError as exc:
        raise MaterializationError("FILE_READ_FAILED") from exc
    return digest.hexdigest()


def _sha256_descriptor(descriptor: int) -> str:
    digest = hashlib.sha256()
    try:
        os.lseek(descriptor, 0, os.SEEK_SET)
        while chunk := os.read(descriptor, 1024 * 1024):
            digest.update(chunk)
        os.lseek(descriptor, 0, os.SEEK_SET)
    except OSError as exc:
        raise MaterializationError("GENERATOR_DESCRIPTOR_READ_FAILED") from exc
    return digest.hexdigest()


def _file_identity(metadata: os.stat_result) -> FileIdentity:
    return metadata.st_dev, metadata.st_ino


def _verify_generator_fd_and_path(descriptor: int, path: Path) -> FileIdentity:
    try:
        descriptor_metadata = os.fstat(descriptor)
        path_metadata = path.lstat()
    except OSError as exc:
        raise MaterializationError("FROZEN_GENERATOR_IDENTITY_INVALID") from exc
    if (
        not stat.S_ISREG(descriptor_metadata.st_mode)
        or stat.S_ISLNK(path_metadata.st_mode)
        or not stat.S_ISREG(path_metadata.st_mode)
        or _file_identity(descriptor_metadata) != _file_identity(path_metadata)
    ):
        raise MaterializationError("FROZEN_GENERATOR_IDENTITY_INVALID")
    if _sha256_descriptor(descriptor) != GENERATOR_SHA256:
        raise MaterializationError("FROZEN_GENERATOR_HASH_MISMATCH")
    return _file_identity(descriptor_metadata)


def _require_absolute_canonical(path: Path, *, code: str) -> Path:
    if not path.is_absolute():
        raise MaterializationError(code)
    try:
        resolved = path.resolve(strict=False)
    except OSError as exc:
        raise MaterializationError(code) from exc
    if resolved != path:
        raise MaterializationError(code)
    return path


def _lstat(path: Path, *, missing_code: str) -> os.stat_result:
    try:
        return path.lstat()
    except FileNotFoundError as exc:
        raise MaterializationError(missing_code) from exc
    except OSError as exc:
        raise MaterializationError("PATH_STAT_FAILED") from exc


def _require_directory(path: Path, *, invalid_code: str) -> None:
    metadata = _lstat(path, missing_code=invalid_code)
    if stat.S_ISLNK(metadata.st_mode):
        raise MaterializationError("OUTPUT_SYMLINK_FORBIDDEN")
    if not stat.S_ISDIR(metadata.st_mode):
        raise MaterializationError(invalid_code)


def _require_regular_file(path: Path, *, invalid_code: str) -> os.stat_result:
    metadata = _lstat(path, missing_code=invalid_code)
    if stat.S_ISLNK(metadata.st_mode):
        raise MaterializationError("OUTPUT_SYMLINK_FORBIDDEN")
    if not stat.S_ISREG(metadata.st_mode):
        raise MaterializationError(invalid_code)
    return metadata


def _directory_names(path: Path) -> set[str]:
    try:
        with os.scandir(path) as entries:
            return {entry.name for entry in entries}
    except OSError as exc:
        raise MaterializationError("OUTPUT_TREE_READ_FAILED") from exc


def _unlink_owned_file(path: Path, identity: FileIdentity) -> None:
    try:
        metadata = path.lstat()
        if stat.S_ISREG(metadata.st_mode) and _file_identity(metadata) == identity:
            path.unlink()
    except OSError:
        return


def _cleanup_owned_root(path: Path, identity: FileIdentity) -> None:
    if not shutil.rmtree.avoids_symlink_attacks:
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        parent_descriptor = os.open(path.parent, flags)
    except OSError:
        return
    try:
        metadata = os.stat(path.name, dir_fd=parent_descriptor, follow_symlinks=False)
        if not stat.S_ISDIR(metadata.st_mode) or _file_identity(metadata) != identity:
            return
        shutil.rmtree(path.name, dir_fd=parent_descriptor)
    except OSError:
        return
    finally:
        os.close(parent_descriptor)


def _exclusive_write(path: Path, payload: bytes) -> FileIdentity:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as exc:
        raise MaterializationError("EXCLUSIVE_WRITE_FAILED") from exc
    identity = _file_identity(os.fstat(descriptor))
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        _unlink_owned_file(path, identity)
        raise
    return identity


def _validate_runtime() -> None:
    if sys.version_info[:3] != EXPECTED_PYTHON:
        raise MaterializationError("PYTHON_RUNTIME_MISMATCH")


def _frozen_site_packages() -> Path:
    try:
        import numpy as np
    except ImportError as exc:
        raise MaterializationError("FROZEN_NUMPY_ENV_INVALID") from exc
    site_roots = site.getsitepackages()
    if len(site_roots) != 1 or np.__version__ != "2.5.1" or np.__file__ is None:
        raise MaterializationError("FROZEN_NUMPY_ENV_INVALID")
    site_root = Path(site_roots[0])
    venv_root = Path(sys.prefix)
    expected_site_root = (
        venv_root
        / "lib"
        / f"python{EXPECTED_PYTHON[0]}.{EXPECTED_PYTHON[1]}"
        / "site-packages"
    )
    numpy_init = site_root / "numpy" / "__init__.py"
    try:
        if (
            sys.prefix == sys.base_prefix
            or not site_root.is_absolute()
            or site_root.resolve(strict=True) != site_root
            or venv_root.resolve(strict=True) != venv_root
            or site_root != expected_site_root
            or numpy_init.resolve(strict=True) != Path(np.__file__).resolve(strict=True)
        ):
            raise MaterializationError("FROZEN_NUMPY_ENV_INVALID")
    except OSError as exc:
        raise MaterializationError("FROZEN_NUMPY_ENV_INVALID") from exc
    _require_directory(site_root, invalid_code="FROZEN_NUMPY_ENV_INVALID")
    _require_regular_file(numpy_init, invalid_code="FROZEN_NUMPY_ENV_INVALID")
    return site_root


def _validate_manifest_file(
    path: Path,
    fixture: FixtureExpectation,
    *,
    invalid_file_code: str,
    bytes_mismatch_code: str,
    contract_mismatch_code: str,
) -> bytes:
    metadata = _require_regular_file(path, invalid_code=invalid_file_code)
    try:
        manifest_bytes = path.read_bytes()
    except OSError as exc:
        raise MaterializationError(invalid_file_code) from exc
    if (
        metadata.st_size != fixture.manifest_byte_length
        or len(manifest_bytes) != fixture.manifest_byte_length
        or _sha256_bytes(manifest_bytes) != fixture.manifest_sha256
    ):
        raise MaterializationError(bytes_mismatch_code)
    try:
        manifest = _strict_json_load(manifest_bytes)
    except MaterializationError as exc:
        raise MaterializationError(contract_mismatch_code) from exc
    if (
        not isinstance(manifest, dict)
        or manifest.get("schemaVersion") != "s1.4x-binary-array-v1"
        or manifest.get("fileName") != fixture.payload_name
        or manifest.get("byteLength") != fixture.payload_byte_length
        or manifest.get("sha256") != fixture.payload_sha256
    ):
        raise MaterializationError(contract_mismatch_code)
    return manifest_bytes


def _validate_frozen_manifest(path: Path, fixture: FixtureExpectation) -> bytes:
    return _validate_manifest_file(
        path,
        fixture,
        invalid_file_code="FROZEN_MANIFEST_INVALID",
        bytes_mismatch_code="FROZEN_MANIFEST_BYTES_MISMATCH",
        contract_mismatch_code="FROZEN_MANIFEST_CONTRACT_MISMATCH",
    )


def _validate_materialized_manifest(
    path: Path,
    fixture: FixtureExpectation,
) -> bytes:
    return _validate_manifest_file(
        path,
        fixture,
        invalid_file_code="OUTPUT_FILE_INVALID",
        bytes_mismatch_code="MANIFEST_BYTES_MISMATCH",
        contract_mismatch_code="MANIFEST_CONTRACT_MISMATCH",
    )


def _validate_source(s1_4x_root: Path) -> tuple[Path, Path]:
    _require_directory(s1_4x_root, invalid_code="S1_4X_ROOT_INVALID")
    generator = s1_4x_root / GENERATOR_RELATIVE_PATH
    _require_regular_file(generator, invalid_code="FROZEN_GENERATOR_INVALID")
    if _sha256_file(generator) != GENERATOR_SHA256:
        raise MaterializationError("FROZEN_GENERATOR_HASH_MISMATCH")

    source_large = s1_4x_root / "contract" / "fixtures" / "large"
    _require_directory(source_large, invalid_code="FROZEN_MANIFEST_ROOT_INVALID")
    actual_manifest_names = {
        name for name in _directory_names(source_large) if name.endswith(".manifest.json")
    }
    expected_manifest_names = {fixture.manifest_name for fixture in FIXTURES}
    if actual_manifest_names != expected_manifest_names:
        raise MaterializationError("FROZEN_MANIFEST_SET_MISMATCH")

    for fixture in FIXTURES:
        manifest_path = source_large / fixture.manifest_name
        _validate_frozen_manifest(manifest_path, fixture)
    return generator, source_large


def _manifest_entries() -> list[dict[str, Any]]:
    return [
        {
            "path": fixture.manifest_relative_path,
            "byteLength": fixture.manifest_byte_length,
            "sha256": fixture.manifest_sha256,
        }
        for fixture in FIXTURES
    ]


def _payload_entries() -> list[dict[str, Any]]:
    return [
        {
            "path": fixture.payload_relative_path,
            "manifestPath": fixture.manifest_relative_path,
            "byteLength": fixture.payload_byte_length,
            "sha256": fixture.payload_sha256,
        }
        for fixture in FIXTURES
    ]


def _expected_receipt() -> dict[str, Any]:
    manifest_entries = _manifest_entries()
    payload_entries = _payload_entries()
    fixture_tree = {
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
        "fixtureTreeSha256": _sha256_bytes(_canonical_json_bytes(fixture_tree)),
    }


def _validate_generator_report(report_bytes: bytes) -> None:
    report = _strict_json_load(report_bytes)
    expected_results = [
        {
            "manifest": fixture.manifest_name,
            "fileName": fixture.payload_name,
            "expectedByteLength": fixture.payload_byte_length,
            "actualByteLength": fixture.payload_byte_length,
            "expectedSha256": fixture.payload_sha256,
            "actualSha256": fixture.payload_sha256,
            "status": "PASS",
        }
        for fixture in FIXTURES
    ]
    expected_report = {
        "schemaVersion": "s1.4x-large-fixture-generation-result-v1",
        "generator": "numpy-pcg64",
        "generatorVersion": "numpy-2.5.1",
        "manifestCount": len(FIXTURES),
        "results": expected_results,
        "failures": [],
        "status": "PASS",
    }
    if (
        report != expected_report
        or report_bytes != _canonical_json_bytes(expected_report) + b"\n"
    ):
        raise MaterializationError("GENERATOR_REPORT_INVALID")


def _run_generator(
    *,
    generator: Path,
    manifest_root: Path,
    s1_4x_root: Path,
    generated_root: Path,
) -> None:
    try:
        descriptor = os.open(
            generator,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
    except OSError as exc:
        raise MaterializationError("GENERATOR_EXECUTION_FAILED") from exc
    try:
        identity = _verify_generator_fd_and_path(descriptor, generator)
        command = [
            "/proc/self/exe",
            f"/proc/self/fd/{descriptor}",
            "--root",
            str(s1_4x_root),
            "--generated-dir",
            str(generated_root),
            "--check",
        ]
        for fixture in FIXTURES:
            command.extend(["--manifest", str(manifest_root / fixture.manifest_name)])
        site_root = _frozen_site_packages()
        environment = {
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONHASHSEED": "0",
            "PYTHONNOUSERSITE": "1",
            "PYTHONPATH": os.pathsep.join([str(generator.parent), str(site_root)]),
        }
        try:
            completed = subprocess.run(
                command,
                cwd=s1_4x_root,
                env=environment,
                capture_output=True,
                check=False,
                timeout=120,
                pass_fds=(descriptor,),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise MaterializationError("GENERATOR_EXECUTION_FAILED") from exc
        if _verify_generator_fd_and_path(descriptor, generator) != identity:
            raise MaterializationError("FROZEN_GENERATOR_IDENTITY_INVALID")
    finally:
        os.close(descriptor)
    if completed.returncode != 0:
        raise MaterializationError("GENERATOR_EXIT_NONZERO")
    if completed.stderr:
        raise MaterializationError("GENERATOR_STDERR_NOT_EMPTY")
    _validate_generator_report(completed.stdout)


def _validate_output_tree(
    *,
    output_root: Path,
    expected_root_identity: FileIdentity | None = None,
) -> dict[str, Any]:
    _require_directory(output_root, invalid_code="OUTPUT_ROOT_INVALID")
    if (
        expected_root_identity is not None
        and _file_identity(_lstat(output_root, missing_code="OUTPUT_ROOT_INVALID"))
        != expected_root_identity
    ):
        raise MaterializationError("OUTPUT_ROOT_IDENTITY_CHANGED")
    if _directory_names(output_root) != {"large"}:
        raise MaterializationError("OUTPUT_TREE_CLOSURE_INVALID")

    large_root = output_root / "large"
    _require_directory(large_root, invalid_code="OUTPUT_TREE_INVALID")
    expected_large_names = {fixture.manifest_name for fixture in FIXTURES} | {"generated"}
    if _directory_names(large_root) != expected_large_names:
        raise MaterializationError("OUTPUT_TREE_CLOSURE_INVALID")

    generated_root = large_root / "generated"
    _require_directory(generated_root, invalid_code="OUTPUT_TREE_INVALID")
    expected_payload_names = {fixture.payload_name for fixture in FIXTURES}
    if _directory_names(generated_root) != expected_payload_names:
        raise MaterializationError("OUTPUT_TREE_CLOSURE_INVALID")

    for fixture in FIXTURES:
        output_manifest = large_root / fixture.manifest_name
        _validate_materialized_manifest(output_manifest, fixture)

        output_payload = generated_root / fixture.payload_name
        metadata = _require_regular_file(
            output_payload,
            invalid_code="OUTPUT_FILE_INVALID",
        )
        if metadata.st_size != fixture.payload_byte_length:
            raise MaterializationError("PAYLOAD_LENGTH_MISMATCH")
        if _sha256_file(output_payload) != fixture.payload_sha256:
            raise MaterializationError("PAYLOAD_HASH_MISMATCH")
    return _expected_receipt()


def _validate_paths(
    *,
    s1_4x_root: Path,
    output_root: Path,
    receipt: Path,
) -> tuple[Path, Path, Path]:
    s1_4x_root = _require_absolute_canonical(
        s1_4x_root,
        code="S1_4X_ROOT_PATH_INVALID",
    )
    output_root = _require_absolute_canonical(
        output_root,
        code="OUTPUT_ROOT_PATH_INVALID",
    )
    receipt = _require_absolute_canonical(receipt, code="RECEIPT_PATH_INVALID")
    if receipt == output_root or receipt.is_relative_to(output_root):
        raise MaterializationError("RECEIPT_INSIDE_OUTPUT_ROOT")
    _require_directory(output_root.parent, invalid_code="OUTPUT_PARENT_INVALID")
    _require_directory(receipt.parent, invalid_code="RECEIPT_PARENT_INVALID")
    return s1_4x_root, output_root, receipt


def materialize(
    *,
    s1_4x_root: Path,
    output_root: Path,
    receipt: Path,
) -> dict[str, Any]:
    """새 root에만 4+4 fixture를 만들고 raw 절대경로 없는 canonical receipt를 기록한다."""

    _validate_runtime()
    if os.path.lexists(output_root):
        raise MaterializationError("OUTPUT_ROOT_ALREADY_EXISTS")
    if os.path.lexists(receipt):
        raise MaterializationError("RECEIPT_ALREADY_EXISTS")
    s1_4x_root, output_root, receipt = _validate_paths(
        s1_4x_root=s1_4x_root,
        output_root=output_root,
        receipt=receipt,
    )
    generator, source_large = _validate_source(s1_4x_root)

    output_root_identity: FileIdentity | None = None
    receipt_identity: FileIdentity | None = None
    output_root_descriptor: int | None = None
    try:
        output_root.mkdir(mode=0o700, parents=False, exist_ok=False)
        output_root_descriptor = os.open(
            output_root,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        output_root_identity = _file_identity(os.fstat(output_root_descriptor))
        large_root = output_root / "large"
        large_root.mkdir(mode=0o700)
        generated_root = large_root / "generated"
        generated_root.mkdir(mode=0o700)
        for fixture in FIXTURES:
            source_bytes = _validate_frozen_manifest(
                source_large / fixture.manifest_name,
                fixture,
            )
            _exclusive_write(large_root / fixture.manifest_name, source_bytes)

        _run_generator(
            generator=generator,
            manifest_root=large_root,
            s1_4x_root=s1_4x_root,
            generated_root=generated_root,
        )
        receipt_value = _validate_output_tree(
            output_root=output_root,
            expected_root_identity=output_root_identity,
        )
        receipt_identity = _exclusive_write(
            receipt,
            _canonical_json_bytes(receipt_value),
        )
        if (
            _file_identity(_lstat(output_root, missing_code="OUTPUT_ROOT_INVALID"))
            != output_root_identity
        ):
            raise MaterializationError("OUTPUT_ROOT_IDENTITY_CHANGED")
        return receipt_value
    except BaseException:
        if receipt_identity is not None:
            _unlink_owned_file(receipt, receipt_identity)
        if output_root_identity is not None:
            _cleanup_owned_root(output_root, output_root_identity)
        raise
    finally:
        if output_root_descriptor is not None:
            os.close(output_root_descriptor)


def check_materialization(
    *,
    s1_4x_root: Path,
    output_root: Path,
    receipt: Path,
) -> dict[str, Any]:
    """기존 fixture tree와 canonical receipt를 무수정으로 다시 검증한다."""

    _validate_runtime()
    s1_4x_root, output_root, receipt = _validate_paths(
        s1_4x_root=s1_4x_root,
        output_root=output_root,
        receipt=receipt,
    )
    _validate_source(s1_4x_root)
    expected_receipt = _validate_output_tree(
        output_root=output_root,
    )
    _require_regular_file(receipt, invalid_code="RECEIPT_INVALID")
    try:
        receipt_bytes = receipt.read_bytes()
        receipt_value = _strict_json_load(receipt_bytes)
    except (OSError, MaterializationError) as exc:
        raise MaterializationError("RECEIPT_MISMATCH") from exc
    if (
        receipt_value != expected_receipt
        or receipt_bytes != _canonical_json_bytes(expected_receipt)
    ):
        raise MaterializationError("RECEIPT_MISMATCH")
    return expected_receipt


def _parse_arguments(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Materialize or check the frozen S1.4X large fixture tree."
    )
    parser.add_argument("mode", choices=("materialize", "check"))
    parser.add_argument("--s1-4x-root", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--receipt", required=True, type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI 성공 시 canonical receipt만 stdout에, 실패 시 stable code만 stderr에 쓴다."""

    arguments = _parse_arguments(argv)
    try:
        if arguments.mode == "materialize":
            receipt_value = materialize(
                s1_4x_root=arguments.s1_4x_root,
                output_root=arguments.output_root,
                receipt=arguments.receipt,
            )
        else:
            receipt_value = check_materialization(
                s1_4x_root=arguments.s1_4x_root,
                output_root=arguments.output_root,
                receipt=arguments.receipt,
            )
    except MaterializationError as exc:
        print(f"LARGE_FIXTURE_MATERIALIZATION_FAIL:{exc}", file=sys.stderr)
        return 2
    except (OSError, ValueError, subprocess.SubprocessError):
        print(
            "LARGE_FIXTURE_MATERIALIZATION_FAIL:UNEXPECTED_OPERATION_FAILED",
            file=sys.stderr,
        )
        return 2
    print(_canonical_json_bytes(receipt_value).decode("utf-8"), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
