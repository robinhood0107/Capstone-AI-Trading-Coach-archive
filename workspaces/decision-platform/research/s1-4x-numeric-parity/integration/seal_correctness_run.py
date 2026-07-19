#!/usr/bin/env python3
"""Raw correctness artifact tree를 immutable canonical manifest로 봉인한다."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MANIFEST_NAME = "correctness-run-manifest.v1.json"
SCHEMA_VERSION = "s1.4x-correctness-run-manifest-v1"
SUBJECT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
READ_BLOCK_BYTES = 1024 * 1024

FileIdentity = tuple[int, int, int, int, int, int, int]
NodeIdentity = tuple[int, int, int]
SealTestHook = Callable[[str, Path], None]


class CorrectnessManifestSealError(ValueError):
    """Raw closure path, snapshot 또는 exclusive output 계약 위반이다."""


@dataclass(frozen=True, slots=True)
class _ArtifactSnapshot:
    """동일 no-follow FD에서 읽고 검증한 한 regular artifact snapshot이다."""

    path: str
    sha256: str
    size_bytes: int
    identity: FileIdentity

    def manifest_entry(self) -> dict[str, object]:
        return {
            "path": self.path,
            "sha256": self.sha256,
            "sizeBytes": self.size_bytes,
        }


def _error(code: str, relative: str | None = None) -> CorrectnessManifestSealError:
    if relative is None:
        return CorrectnessManifestSealError(code)
    return CorrectnessManifestSealError(f"{code}:{relative}")


def _stable_identity(metadata: os.stat_result) -> FileIdentity:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_nlink,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _node_identity(metadata: os.stat_result) -> NodeIdentity:
    return (metadata.st_dev, metadata.st_ino, metadata.st_mode)


def _require_nofollow_flags() -> tuple[int, int]:
    no_follow = getattr(os, "O_NOFOLLOW", None)
    directory = getattr(os, "O_DIRECTORY", None)
    if no_follow is None or directory is None:
        raise _error("NOFOLLOW_UNSUPPORTED")
    return no_follow, directory


def _canonical_root_metadata(root: Path) -> os.stat_result:
    if not root.is_absolute():
        raise _error("CORRECTNESS_ROOT_INVALID")
    try:
        resolved = root.resolve(strict=True)
        metadata = root.lstat()
    except (OSError, RuntimeError) as exc:
        raise _error("CORRECTNESS_ROOT_INVALID") from exc
    if resolved != root or stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise _error("CORRECTNESS_ROOT_INVALID")
    return metadata


def _open_canonical_root(root: Path) -> tuple[int, FileIdentity]:
    before = _canonical_root_metadata(root)
    no_follow, directory = _require_nofollow_flags()
    flags = os.O_RDONLY | os.O_CLOEXEC | directory | no_follow
    try:
        descriptor = os.open(root, flags)
    except OSError as exc:
        raise _error("CORRECTNESS_ROOT_INVALID") from exc
    try:
        opened = os.fstat(descriptor)
        current = _canonical_root_metadata(root)
        if (
            not stat.S_ISDIR(opened.st_mode)
            or _stable_identity(before) != _stable_identity(opened)
            or _stable_identity(opened) != _stable_identity(current)
        ):
            raise _error("CORRECTNESS_ROOT_INVALID")
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor, _stable_identity(opened)


def _require_root_unchanged(
    root: Path,
    descriptor: int,
    expected: FileIdentity,
    *,
    metadata_may_change: bool,
) -> None:
    try:
        opened = os.fstat(descriptor)
        current = _canonical_root_metadata(root)
    except (CorrectnessManifestSealError, OSError) as exc:
        raise _error("TREE_CHANGED_DURING_SEAL", ".") from exc
    if metadata_may_change:
        unchanged = (
            _node_identity(opened)
            == _node_identity(current)
            == (expected[0], expected[1], expected[2])
        )
    else:
        unchanged = _stable_identity(opened) == _stable_identity(current) == expected
    if not unchanged:
        raise _error("TREE_CHANGED_DURING_SEAL", ".")


def _portable_component(name: str) -> bytes:
    if (
        not name
        or name in {".", ".."}
        or "/" in name
        or "\\" in name
        or any(ord(character) < 32 or ord(character) == 127 for character in name)
    ):
        raise _error("PATH_NOT_PORTABLE")
    try:
        return name.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise _error("PATH_NOT_PORTABLE") from exc


def _directory_names(
    descriptor: int,
    prefix: tuple[str, ...],
) -> list[str]:
    try:
        names = os.listdir(descriptor)
    except OSError as exc:
        raise _error("TREE_READ_FAILED", "/".join(prefix) or ".") from exc
    encoded = [(_portable_component(name), name) for name in names]
    encoded.sort(key=lambda item: item[0])
    return [name for _, name in encoded]


def _entry_metadata(
    directory_descriptor: int,
    name: str,
    *,
    relative: str,
) -> os.stat_result:
    try:
        return os.stat(
            name,
            dir_fd=directory_descriptor,
            follow_symlinks=False,
        )
    except OSError as exc:
        raise _error("TREE_CHANGED_DURING_SEAL", relative) from exc


def _open_directory(
    parent_descriptor: int,
    name: str,
    *,
    relative: str,
    before: os.stat_result,
) -> int:
    no_follow, directory = _require_nofollow_flags()
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY | os.O_CLOEXEC | directory | no_follow,
            dir_fd=parent_descriptor,
        )
    except OSError as exc:
        raise _error("TREE_CHANGED_DURING_SEAL", relative) from exc
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISDIR(opened.st_mode) or _stable_identity(before) != _stable_identity(opened):
            raise _error("TREE_CHANGED_DURING_SEAL", relative)
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


def _snapshot_regular_file(
    parent_descriptor: int,
    name: str,
    *,
    relative: str,
    before: os.stat_result,
    test_hook: SealTestHook | None,
) -> _ArtifactSnapshot:
    no_follow, _ = _require_nofollow_flags()
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY | os.O_CLOEXEC | no_follow,
            dir_fd=parent_descriptor,
        )
    except OSError as exc:
        raise _error("TREE_CHANGED_DURING_SEAL", relative) from exc
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or _stable_identity(before) != _stable_identity(opened):
            raise _error("TREE_CHANGED_DURING_SEAL", relative)
        if test_hook is not None:
            test_hook("after_open", Path(relative))

        digest = hashlib.sha256()
        size_bytes = 0
        while True:
            block = os.read(descriptor, READ_BLOCK_BYTES)
            if not block:
                break
            digest.update(block)
            size_bytes += len(block)

        if test_hook is not None:
            test_hook("after_read", Path(relative))
        after = os.fstat(descriptor)
        current = _entry_metadata(
            parent_descriptor,
            name,
            relative=relative,
        )
        if (
            not stat.S_ISREG(current.st_mode)
            or _stable_identity(before) != _stable_identity(after)
            or _stable_identity(after) != _stable_identity(current)
            or size_bytes != after.st_size
        ):
            raise _error("TREE_CHANGED_DURING_SEAL", relative)
    except CorrectnessManifestSealError:
        raise
    except OSError as exc:
        raise _error("ARTIFACT_READ_FAILED", relative) from exc
    finally:
        os.close(descriptor)
    return _ArtifactSnapshot(
        path=relative,
        sha256=digest.hexdigest(),
        size_bytes=size_bytes,
        identity=_stable_identity(after),
    )


def _walk_snapshots(
    directory_descriptor: int,
    *,
    prefix: tuple[str, ...],
    test_hook: SealTestHook | None,
) -> list[_ArtifactSnapshot]:
    try:
        directory_before = os.fstat(directory_descriptor)
    except OSError as exc:
        raise _error("TREE_READ_FAILED", "/".join(prefix) or ".") from exc
    if not stat.S_ISDIR(directory_before.st_mode):
        raise _error("TREE_CHANGED_DURING_SEAL", "/".join(prefix) or ".")

    snapshots: list[_ArtifactSnapshot] = []
    for name in _directory_names(directory_descriptor, prefix):
        relative_parts = (*prefix, name)
        relative = "/".join(relative_parts)
        if not prefix and name == MANIFEST_NAME:
            raise _error("MANIFEST_ALREADY_EXISTS")
        before = _entry_metadata(
            directory_descriptor,
            name,
            relative=relative,
        )
        if stat.S_ISLNK(before.st_mode):
            raise _error("SYMLINK_FORBIDDEN", relative)
        if stat.S_ISDIR(before.st_mode):
            child_descriptor = _open_directory(
                directory_descriptor,
                name,
                relative=relative,
                before=before,
            )
            try:
                snapshots.extend(
                    _walk_snapshots(
                        child_descriptor,
                        prefix=relative_parts,
                        test_hook=test_hook,
                    )
                )
                opened_after = os.fstat(child_descriptor)
                current = _entry_metadata(
                    directory_descriptor,
                    name,
                    relative=relative,
                )
                if _stable_identity(before) != _stable_identity(opened_after) or _stable_identity(
                    opened_after
                ) != _stable_identity(current):
                    raise _error("TREE_CHANGED_DURING_SEAL", relative)
            finally:
                os.close(child_descriptor)
        elif stat.S_ISREG(before.st_mode):
            snapshots.append(
                _snapshot_regular_file(
                    directory_descriptor,
                    name,
                    relative=relative,
                    before=before,
                    test_hook=test_hook,
                )
            )
        else:
            raise _error("NON_REGULAR_FORBIDDEN", relative)

    try:
        directory_after = os.fstat(directory_descriptor)
    except OSError as exc:
        raise _error("TREE_CHANGED_DURING_SEAL", "/".join(prefix) or ".") from exc
    if _stable_identity(directory_before) != _stable_identity(directory_after):
        raise _error("TREE_CHANGED_DURING_SEAL", "/".join(prefix) or ".")
    return snapshots


def _walk_identity_inventory(
    directory_descriptor: int,
    *,
    prefix: tuple[str, ...],
    exclude_root_manifest: bool,
) -> dict[str, FileIdentity]:
    try:
        directory_before = os.fstat(directory_descriptor)
    except OSError as exc:
        raise _error("TREE_READ_FAILED", "/".join(prefix) or ".") from exc
    inventory: dict[str, FileIdentity] = {}
    for name in _directory_names(directory_descriptor, prefix):
        relative_parts = (*prefix, name)
        relative = "/".join(relative_parts)
        if not prefix and name == MANIFEST_NAME:
            if exclude_root_manifest:
                continue
            raise _error("MANIFEST_ALREADY_EXISTS")
        before = _entry_metadata(
            directory_descriptor,
            name,
            relative=relative,
        )
        if stat.S_ISLNK(before.st_mode):
            raise _error("SYMLINK_FORBIDDEN", relative)
        if stat.S_ISDIR(before.st_mode):
            child_descriptor = _open_directory(
                directory_descriptor,
                name,
                relative=relative,
                before=before,
            )
            try:
                inventory.update(
                    _walk_identity_inventory(
                        child_descriptor,
                        prefix=relative_parts,
                        exclude_root_manifest=exclude_root_manifest,
                    )
                )
                opened_after = os.fstat(child_descriptor)
                current = _entry_metadata(
                    directory_descriptor,
                    name,
                    relative=relative,
                )
                if _stable_identity(before) != _stable_identity(opened_after) or _stable_identity(
                    opened_after
                ) != _stable_identity(current):
                    raise _error("TREE_CHANGED_DURING_SEAL", relative)
            finally:
                os.close(child_descriptor)
        elif stat.S_ISREG(before.st_mode):
            inventory[relative] = _stable_identity(before)
        else:
            raise _error("NON_REGULAR_FORBIDDEN", relative)

    try:
        directory_after = os.fstat(directory_descriptor)
    except OSError as exc:
        raise _error("TREE_CHANGED_DURING_SEAL", "/".join(prefix) or ".") from exc
    if _stable_identity(directory_before) != _stable_identity(directory_after):
        raise _error("TREE_CHANGED_DURING_SEAL", "/".join(prefix) or ".")
    return inventory


def _verify_snapshot_inventory(
    descriptor: int,
    snapshots: Sequence[_ArtifactSnapshot],
    *,
    exclude_root_manifest: bool = False,
) -> None:
    expected = {snapshot.path: snapshot.identity for snapshot in snapshots}
    current = _walk_identity_inventory(
        descriptor,
        prefix=(),
        exclude_root_manifest=exclude_root_manifest,
    )
    all_paths = sorted(
        expected.keys() | current.keys(),
        key=lambda value: value.encode("utf-8"),
    )
    for relative in all_paths:
        if expected.get(relative) != current.get(relative):
            raise _error("TREE_CHANGED_DURING_SEAL", relative)


def _ensure_manifest_absent(root_descriptor: int) -> None:
    try:
        os.stat(
            MANIFEST_NAME,
            dir_fd=root_descriptor,
            follow_symlinks=False,
        )
    except FileNotFoundError:
        return
    except OSError as exc:
        raise _error("TREE_READ_FAILED", MANIFEST_NAME) from exc
    raise _error("MANIFEST_ALREADY_EXISTS")


def _canonical_json_bytes(value: Any) -> bytes:
    try:
        return (
            json.dumps(
                value,
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise _error("MANIFEST_JSON_INVALID") from exc


def _remove_owned_manifest(
    root_descriptor: int,
    identity: NodeIdentity,
) -> None:
    try:
        current = os.stat(
            MANIFEST_NAME,
            dir_fd=root_descriptor,
            follow_symlinks=False,
        )
        if _node_identity(current) == identity and stat.S_ISREG(current.st_mode):
            os.unlink(MANIFEST_NAME, dir_fd=root_descriptor)
    except OSError:
        return


def _write_exclusive_manifest(
    root_descriptor: int,
    payload: bytes,
) -> FileIdentity:
    no_follow, _ = _require_nofollow_flags()
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | no_follow
    descriptor: int | None = None
    owned_identity: NodeIdentity | None = None
    try:
        descriptor = os.open(
            MANIFEST_NAME,
            flags,
            0o600,
            dir_fd=root_descriptor,
        )
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise _error("MANIFEST_WRITE_FAILED")
        owned_identity = _node_identity(opened)
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written < 1:
                raise OSError("zero-byte manifest write")
            view = view[written:]
        os.fsync(descriptor)
        after = os.fstat(descriptor)
        current = os.stat(
            MANIFEST_NAME,
            dir_fd=root_descriptor,
            follow_symlinks=False,
        )
        if (
            not stat.S_ISREG(after.st_mode)
            or _stable_identity(after) != _stable_identity(current)
            or after.st_size != len(payload)
        ):
            raise _error("MANIFEST_CHANGED_DURING_WRITE")
    except FileExistsError as exc:
        raise _error("MANIFEST_ALREADY_EXISTS") from exc
    except CorrectnessManifestSealError:
        if owned_identity is not None:
            _remove_owned_manifest(root_descriptor, owned_identity)
        raise
    except OSError as exc:
        if owned_identity is not None:
            _remove_owned_manifest(root_descriptor, owned_identity)
        raise _error("MANIFEST_WRITE_FAILED") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
    try:
        os.fsync(root_descriptor)
    except OSError as exc:
        if owned_identity is not None:
            _remove_owned_manifest(root_descriptor, owned_identity)
        raise _error("MANIFEST_WRITE_FAILED") from exc
    return _stable_identity(after)


def _require_written_manifest(
    root_descriptor: int,
    expected: FileIdentity,
) -> None:
    try:
        current = os.stat(
            MANIFEST_NAME,
            dir_fd=root_descriptor,
            follow_symlinks=False,
        )
    except OSError as exc:
        raise _error("MANIFEST_CHANGED_DURING_WRITE") from exc
    if not stat.S_ISREG(current.st_mode) or _stable_identity(current) != expected:
        raise _error("MANIFEST_CHANGED_DURING_WRITE")


def seal_correctness_run(
    *,
    correctness_root: Path,
    benchmark_subject_commit: str,
    _test_hook: SealTestHook | None = None,
) -> dict[str, Any]:
    """절대 canonical directory의 모든 raw regular file을 hash closure로 봉인한다.

    출력은 root 바로 아래에 exclusive 생성하며, repository 상태는 검사하지 않는다.
    symlink/non-regular 경로와 읽기 중 identity·size 변화는 manifest 생성 전에 거부한다.
    """

    if SUBJECT_PATTERN.fullmatch(benchmark_subject_commit) is None:
        raise _error("BENCHMARK_SUBJECT_COMMIT_INVALID")
    root_descriptor, root_identity = _open_canonical_root(correctness_root)
    written_identity: FileIdentity | None = None
    try:
        _ensure_manifest_absent(root_descriptor)
        snapshots = _walk_snapshots(
            root_descriptor,
            prefix=(),
            test_hook=_test_hook,
        )
        if _test_hook is not None:
            _test_hook("before_final_validation", Path())
        _verify_snapshot_inventory(root_descriptor, snapshots)
        _require_root_unchanged(
            correctness_root,
            root_descriptor,
            root_identity,
            metadata_may_change=False,
        )

        snapshots.sort(key=lambda item: item.path.encode("utf-8"))
        artifacts = [snapshot.manifest_entry() for snapshot in snapshots]
        manifest: dict[str, Any] = {
            "schemaVersion": SCHEMA_VERSION,
            "benchmarkSubjectCommit": benchmark_subject_commit,
            "artifactCount": len(artifacts),
            "artifacts": artifacts,
            "status": "PASS",
        }
        payload = _canonical_json_bytes(manifest)
        written_identity = _write_exclusive_manifest(
            root_descriptor,
            payload,
        )
        if _test_hook is not None:
            _test_hook("after_manifest_write", Path())
        # manifest 생성 자체를 제외한 raw closure가 최종 write 뒤에도 동일해야 한다.
        _verify_snapshot_inventory(
            root_descriptor,
            snapshots,
            exclude_root_manifest=True,
        )
        _require_root_unchanged(
            correctness_root,
            root_descriptor,
            root_identity,
            metadata_may_change=True,
        )
        _require_written_manifest(root_descriptor, written_identity)
        return manifest
    except BaseException:
        if written_identity is not None:
            _remove_owned_manifest(
                root_descriptor,
                (
                    written_identity[0],
                    written_identity[1],
                    written_identity[2],
                ),
            )
        raise
    finally:
        os.close(root_descriptor)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--correctness-root", type=Path, required=True)
    parser.add_argument("--benchmark-subject-commit", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI는 canonical manifest를 stdout에 복제하고 stable failure만 stderr에 쓴다."""

    arguments = _parser().parse_args(argv)
    try:
        manifest = seal_correctness_run(
            correctness_root=arguments.correctness_root,
            benchmark_subject_commit=arguments.benchmark_subject_commit,
        )
    except CorrectnessManifestSealError as exc:
        print(f"CORRECTNESS_RUN_SEAL_FAIL:{exc}", file=sys.stderr)
        return 2
    except (OSError, ValueError):
        print(
            "CORRECTNESS_RUN_SEAL_FAIL:UNEXPECTED_OPERATION_FAILED",
            file=sys.stderr,
        )
        return 2
    print(_canonical_json_bytes(manifest).decode("utf-8"), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
