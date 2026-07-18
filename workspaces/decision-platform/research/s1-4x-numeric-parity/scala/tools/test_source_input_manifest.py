#!/usr/bin/env python3
"""Scala source-input manifest가 frozen 단일 입력 집합을 보존하는지 검증한다."""

from __future__ import annotations

import copy
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

from jsonschema import Draft202012Validator


SCALA_ROOT = Path(__file__).resolve().parents[1]
S1_ROOT = SCALA_ROOT.parent
TOOLS_ROOT = SCALA_ROOT / "tools"
MANIFEST_PATH = SCALA_ROOT / "source-inputs.v1.json"
POLICY_PATH = S1_ROOT / "contract" / "scala-source-policy.v1.json"
SCHEMA_PATH = S1_ROOT / "contract" / "schemas" / "source-input-manifest.schema.json"
INPUT_SET_KEYS = {
    "tracked",
    "manifest",
    "format",
    "compile",
    "lint",
    "profileRun",
}
FORBIDDEN_COMPILED_SUFFIXES = (".sc", ".java", ".kt", ".kts")


def strict_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict), path
    return value


def load_tool(name: str) -> ModuleType:
    path = TOOLS_ROOT / f"{name}.py"
    specification = importlib.util.spec_from_file_location(name, path)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    sys.modules[name] = module
    specification.loader.exec_module(module)
    return module


def production_roots(policy: dict[str, Any]) -> list[str]:
    roots = policy["productionRoots"]
    assert isinstance(roots, list)
    return [str(path).removeprefix("scala/") for path in roots]


def git_sources(roots: list[str]) -> list[str]:
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
        cwd=SCALA_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return sorted(
        {line for line in completed.stdout.splitlines() if line.endswith(".scala")},
        key=lambda value: value.encode("utf-8"),
    )


def expected_role(path: str) -> str:
    if path in {"project.scala", "selected-profile.scala"}:
        return "configuration"
    if path.startswith("src/main/scala/"):
        return "main"
    if path.startswith("src/test/scala/"):
        return "test"
    if path.startswith("benchmarks/"):
        return "benchmark"
    raise AssertionError(f"unexpected production source path: {path}")


def main() -> int:
    schema = strict_json(SCHEMA_PATH)
    manifest = strict_json(MANIFEST_PATH)
    policy = strict_json(POLICY_PATH)
    validator = Draft202012Validator(schema)
    errors = sorted(
        validator.iter_errors(manifest),
        key=lambda error: tuple(str(part) for part in error.absolute_path),
    )
    assert not errors, "\n".join(error.message for error in errors)

    assert manifest["schemaVersion"] == "s1.4x-source-input-manifest-v1"
    assert manifest["language"] == "scala"
    assert set(manifest["inputSets"]) == INPUT_SET_KEYS
    assert set(manifest["inputSets"].values()) == {"files"}

    expected_paths = git_sources(production_roots(policy))
    assert list(manifest["files"]) == expected_paths
    for path in expected_paths:
        metadata = manifest["files"][path]
        assert metadata["role"] == expected_role(path)

    source_manifest = load_tool("source_input_manifest")
    assert (
        manifest["canonicalManifestSha256"]
        == source_manifest.canonical_manifest_sha256(manifest["files"])
    )
    resolved = source_manifest.validated_source_files(
        SCALA_ROOT,
        manifest,
        policy=policy,
        require_git_source_equality=True,
    )
    assert [path.relative_to(SCALA_ROOT).as_posix() for path in resolved] == expected_paths

    run_scalafix = load_tool("run_scalafix")
    check_source_policy = load_tool("check_source_policy")
    assert run_scalafix.source_files is source_manifest.validated_source_files
    assert check_source_policy.collect_sources is source_manifest.validated_source_files

    divergent = copy.deepcopy(manifest)
    divergent["inputSets"]["lint"] = expected_paths
    assert list(validator.iter_errors(divergent))

    stale_hash = copy.deepcopy(manifest)
    first_path = expected_paths[0]
    stale_hash["files"][first_path]["sha256"] = "0" * 64
    try:
        source_manifest.validated_source_files(
            SCALA_ROOT,
            stale_hash,
            policy=policy,
            require_git_source_equality=True,
        )
    except source_manifest.SourceInputManifestError:
        pass
    else:
        raise AssertionError("stale source hash unexpectedly passed")

    for suffix in FORBIDDEN_COMPILED_SUFFIXES:
        escaped = SCALA_ROOT / "benchmarks" / f"SourceInputEscape{suffix}"
        assert not escaped.exists(), escaped
        try:
            escaped.write_text("final class SourceInputEscape {}\n", encoding="utf-8")
            try:
                source_manifest.validated_source_files(
                    SCALA_ROOT,
                    manifest,
                    policy=policy,
                    require_git_source_equality=True,
                )
            except source_manifest.SourceInputManifestError as error:
                assert "FORBIDDEN_COMPILED_SOURCE" in str(error)
            else:
                raise AssertionError(f"compiled source escape unexpectedly passed: {suffix}")
        finally:
            escaped.unlink(missing_ok=True)

    print(
        "SCALA_SOURCE_INPUT_MANIFEST_TEST_PASS "
        f"files={len(expected_paths)} inputSets={len(INPUT_SET_KEYS)} "
        f"forbiddenSuffixes={len(FORBIDDEN_COMPILED_SUFFIXES)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
