#!/usr/bin/env python3
"""Frozen Scala source-input manifest를 fail-closed로 해석한다."""

from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path
from typing import Any, Mapping


class SourceInputManifestError(ValueError):
    """Source manifest와 실제 Scala production input closure가 다름을 나타낸다."""


SCHEMA_VERSION = "s1.4x-source-input-manifest-v1"
MANIFEST_KEYS = {
    "schemaVersion",
    "language",
    "files",
    "inputSets",
    "canonicalManifestSha256",
}
INPUT_SETS = {
    "tracked": "files",
    "manifest": "files",
    "format": "files",
    "compile": "files",
    "lint": "files",
    "profileRun": "files",
}
METADATA_KEYS = {"role", "sha256"}
SHA256 = re.compile(r"^[0-9a-f]{64}$")
SAFE_PATH = re.compile(
    r"^(?!/)(?!.*(?:^|/)\.\.(?:/|$))[A-Za-z0-9._/-]+$"
)
FORBIDDEN_COMPILED_SUFFIXES = (".sc", ".java", ".kt", ".kts")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_manifest_sha256(files: Mapping[str, Any]) -> str:
    """LC_ALL=C path 순서의 `<sha>  <path>\\n` closure hash를 계산한다."""

    lines: list[bytes] = []
    for path in sorted(files, key=lambda value: value.encode("utf-8")):
        metadata = files[path]
        if not isinstance(metadata, Mapping):
            raise SourceInputManifestError(f"FILE_METADATA_INVALID:{path}")
        digest = metadata.get("sha256")
        if not isinstance(digest, str) or SHA256.fullmatch(digest) is None:
            raise SourceInputManifestError(f"FILE_SHA256_INVALID:{path}")
        lines.append(f"{digest}  {path}\n".encode("utf-8"))
    return hashlib.sha256(b"".join(lines)).hexdigest()


def production_roots(policy: Mapping[str, Any]) -> list[str]:
    roots = policy.get("productionRoots")
    if (
        policy.get("schemaVersion") != "s1.4x-scala-source-policy-v1"
        or not isinstance(roots, list)
        or not roots
        or any(not isinstance(item, str) for item in roots)
    ):
        raise SourceInputManifestError("SCALA_SOURCE_POLICY_INVALID")
    relative_roots: list[str] = []
    for value in roots:
        if not value.startswith("scala/"):
            raise SourceInputManifestError(f"PRODUCTION_ROOT_INVALID:{value}")
        relative = value.removeprefix("scala/")
        if (
            not relative
            or SAFE_PATH.fullmatch(relative) is None
            or relative in relative_roots
        ):
            raise SourceInputManifestError(f"PRODUCTION_ROOT_INVALID:{value}")
        relative_roots.append(relative)
    return relative_roots


def expected_role(path: str) -> str:
    if path in {"project.scala", "selected-profile.scala"}:
        return "configuration"
    if path.startswith("src/main/scala/"):
        return "main"
    if path.startswith("src/test/scala/"):
        return "test"
    if path.startswith("benchmarks/"):
        return "benchmark"
    raise SourceInputManifestError(f"SOURCE_ROLE_UNMAPPED:{path}")


def under_root(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip("/") + "/")


def git_source_files(scala_root: Path, roots: list[str]) -> list[str]:
    """Git production closure의 compiled escape를 거부한 뒤 `.scala` 집합을 반환한다."""

    completed = subprocess.run(
        [
            "git",
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
            "--",
            *roots,
        ],
        cwd=scala_root,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise SourceInputManifestError("GIT_SOURCE_ENUMERATION_FAILED")
    production_files = sorted(
        set(completed.stdout.splitlines()),
        key=lambda value: value.encode("utf-8"),
    )
    forbidden = [
        path
        for path in production_files
        if path.endswith(FORBIDDEN_COMPILED_SUFFIXES)
    ]
    if forbidden:
        raise SourceInputManifestError(
            f"FORBIDDEN_COMPILED_SOURCE:{forbidden[0]}"
        )
    return sorted(
        {line for line in production_files if line.endswith(".scala")},
        key=lambda value: value.encode("utf-8"),
    )


def has_symlink_component(scala_root: Path, relative: str) -> bool:
    current = scala_root
    for part in Path(relative).parts:
        current = current / part
        if current.is_symlink():
            return True
    return False


def validated_source_files(
    scala_root: Path,
    manifest: dict[str, Any],
    *,
    policy: Mapping[str, Any] | None = None,
    require_git_source_equality: bool = False,
) -> list[Path]:
    """명시 files의 schema shape·role·bytes·Git closure를 검증하고 정렬된 경로를 반환한다."""

    resolved_root = scala_root.resolve(strict=True)
    if (
        set(manifest) != MANIFEST_KEYS
        or manifest.get("schemaVersion") != SCHEMA_VERSION
        or manifest.get("language") != "scala"
        or manifest.get("inputSets") != INPUT_SETS
    ):
        raise SourceInputManifestError("SOURCE_INPUT_MANIFEST_INVALID")
    files = manifest.get("files")
    if not isinstance(files, dict) or not files:
        raise SourceInputManifestError("SOURCE_INPUT_FILES_INVALID")
    ordered_paths = sorted(files, key=lambda value: value.encode("utf-8"))
    if list(files) != ordered_paths:
        raise SourceInputManifestError("SOURCE_INPUT_FILES_NOT_CANONICALLY_ORDERED")

    roots = production_roots(policy) if policy is not None else []
    resolved: list[Path] = []
    for relative in ordered_paths:
        metadata = files[relative]
        if (
            not isinstance(relative, str)
            or SAFE_PATH.fullmatch(relative) is None
            or not relative.endswith(".scala")
            or not isinstance(metadata, dict)
            or set(metadata) != METADATA_KEYS
            or metadata.get("role") != expected_role(relative)
            or not isinstance(metadata.get("sha256"), str)
            or SHA256.fullmatch(metadata["sha256"]) is None
        ):
            raise SourceInputManifestError(f"SOURCE_INPUT_ENTRY_INVALID:{relative}")
        if roots and not any(under_root(relative, root) for root in roots):
            raise SourceInputManifestError(f"SOURCE_OUTSIDE_POLICY_ROOTS:{relative}")
        candidate = resolved_root / relative
        if (
            has_symlink_component(resolved_root, relative)
            or not candidate.is_file()
            or not candidate.resolve(strict=True).is_relative_to(resolved_root)
        ):
            raise SourceInputManifestError(f"UNSAFE_OR_MISSING_SOURCE:{relative}")
        if sha256_file(candidate) != metadata["sha256"]:
            raise SourceInputManifestError(f"SOURCE_SHA256_MISMATCH:{relative}")
        resolved.append(candidate)

    expected_canonical = canonical_manifest_sha256(files)
    if manifest.get("canonicalManifestSha256") != expected_canonical:
        raise SourceInputManifestError("CANONICAL_MANIFEST_SHA256_MISMATCH")
    if require_git_source_equality:
        if policy is None:
            raise SourceInputManifestError("POLICY_REQUIRED_FOR_GIT_SOURCE_EQUALITY")
        if ordered_paths != git_source_files(resolved_root, roots):
            raise SourceInputManifestError("TRACKED_MANIFEST_SOURCE_SET_MISMATCH")
    return resolved
