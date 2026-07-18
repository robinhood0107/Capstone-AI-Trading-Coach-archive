#!/usr/bin/env python3
"""S1.4X Haskell source, module-safety, profile evidence를 결정적으로 생성한다."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import tarfile
import tempfile
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


class EvidenceError(RuntimeError):
    """Evidence input이 frozen contract를 만족하지 않을 때 발생한다."""


@dataclass(frozen=True)
class ParsedModule:
    """후보 source 한 개에서 추출한 선언 identity와 direct import 집합."""

    module_name: str
    extensions: tuple[str, ...]
    imports: tuple[str, ...]


@dataclass(frozen=True)
class ProfileSelection:
    """Frozen Criterion selector를 재계산한 authoritative profile 결정."""

    profile_id: str
    selected_by: str
    paired_ratios: tuple[float, ...]
    per_case_maxima: Mapping[str, float]
    aggregate_ratio: float
    improving_outer_repetitions: int


CANDIDATE_ROOTS = ("src", "app", "test", "benchmark")
CONFIGURATION_PATHS = ("package.yaml", "selected-profile.v1.json")
WORKFLOW_INPUT_PATHS = (
    ".hlint.yaml",
    "Containerfile",
    "ghc-compatibility-solve-failure.v1.json",
    "lint-exceptions.v1.json",
    "stack-ghc-9.14.1.yaml",
    "stack-ghc-9.14.1.yaml.lock",
    "stylish-ghc2024-fallback.v1.json",
    "toolchain-lock.v1.json",
    "tools/assert-toolchain.sh",
    "tools/check-format.sh",
    "tools/check-hlint.sh",
    "tools/compatibility_evidence.py",
    "tools/haskell_benchmark_block.py",
    "tools/haskell_evidence.py",
    "tools/hlint_inventory.py",
    "tools/profile_workflow.py",
    "tools/run-benchmark-block.sh",
    "tools/run-correctness-profile.sh",
    "tools/run-ghc-9.14.1-compatibility.sh",
    "tools/run-oci-correctness.sh",
    "tools/run-profile-qualification.sh",
    "tools/run-property-evidence.sh",
    "tools/select-proven-profile.sh",
    "tools/stylish_fallback.py",
    "tools/validate-ghc-9.14.1-compatibility.sh",
)
PROPERTY_CLOSURE_CONFIGURATION_PATHS = (
    "package.yaml",
    "s1-4x-haskell.cabal",
    "stack.yaml",
    "stack.yaml.lock",
    "selected-profile.v1.json",
    "source-inputs.v1.json",
    *WORKFLOW_INPUT_PATHS,
)
FORBIDDEN_COMPILED_SUFFIXES = (".lhs", ".hsc", ".hs-boot")
MANDATORY_INPUT_SETS = {
    "tracked": "files",
    "manifest": "files",
    "format": "files",
    "compile": "files",
    "lint": "files",
    "profileRun": "files",
}
SAFE_SCALAR_MODULES = {
    "S14X.Core.Error",
    "S14X.Core.ScalarTypes",
    "S14X.Core.ScalarValidation",
}
CORE_CATEGORIES = {"safe-scalar", "audited-pure-vector"}
SHELL_CATEGORIES = {"io-shell", "test", "benchmark"}
FORBIDDEN_ALL_IMPORT_PREFIXES = (
    "Foreign",
    "Unsafe",
    "System.IO.Unsafe",
    "GHC.IO.Unsafe",
)
FORBIDDEN_CORE_IMPORT_PREFIXES = (
    "System.IO",
    "Control.Monad.IO.Class",
    "Debug.Trace",
)
FORBIDDEN_SAFE_SCALAR_IMPORT_PREFIXES = ("Data.Vector.Unboxed",)
MODULE_SAFETY_POLICY_FIELDS = {
    "schemaVersion",
    "language",
    "categories",
    "everyModuleExactlyOneCategory",
    "mandatoryCoreExtensions",
    "forbiddenCorePositiveExtensions",
    "candidateSourceSuffixPolicy",
    "candidateDerivingPolicy",
    "forbiddenCandidateModuleDeclarations",
    "forbiddenCoreTypesAndUses",
    "forbiddenPartialAndUnsafeSymbols",
    "forbiddenSourceLocalControls",
    "vectorProvenance",
    "candidateGraphInvariants",
    "resultEdgePartitions",
    "resultEdgeCategoryContract",
    "conditionalOptimizations",
    "authoritativeProfiles",
    "forbiddenOptimizationFlags",
    "hardFailureConditions",
}
EXPECTED_CORE_NEGATIVE_EXTENSIONS = (
    "NoForeignFunctionInterface",
    "NoTemplateHaskell",
    "NoCPP",
    "NoRebindableSyntax",
    "NoLinearTypes",
    "NoMagicHash",
    "NoStrict",
    "NoGeneralizedNewtypeDeriving",
    "NoDerivingVia",
    "NoDeriveAnyClass",
)
EXPECTED_CORE_POSITIVE_EXTENSIONS = (
    "ForeignFunctionInterface",
    "TemplateHaskell",
    "CPP",
    "RebindableSyntax",
    "LinearTypes",
    "MagicHash",
    "Strict",
    "GeneralizedNewtypeDeriving",
    "DerivingVia",
    "DeriveAnyClass",
)
EXPECTED_CORE_TYPES_AND_USES = (
    "IO",
    "MonadIO",
    "environment access",
    "clock",
    "random",
    "network",
    "Control.Exception.throw",
    "Control.Exception.throwIO",
    "foreign import",
    "foreign export",
)
EXPECTED_SOURCE_LOCAL_CONTROLS = (
    "OPTIONS_GHC",
    "HLint ignore",
    "global Strict",
    "LinearTypes",
    "TemplateHaskell",
    "CPP",
    "MagicHash",
)
EXPECTED_PARTIAL_AND_UNSAFE_SYMBOLS = (
    "Prelude.head",
    "Prelude.tail",
    "Prelude.init",
    "Prelude.last",
    "Prelude.!!",
    "Text.Read.read",
    "Data.Maybe.fromJust",
    "Data.Either.fromLeft",
    "Data.Either.fromRight",
    "Data.List.foldl1",
    "Data.List.maximum",
    "Data.List.minimum",
    "Debug.Trace",
    "System.IO.Unsafe",
    "GHC.IO.Unsafe",
    "Foreign.*",
)
CORE_CAPABILITY_IMPORT_PREFIXES = {
    "IO": ("System.IO",),
    "MonadIO": ("Control.Monad.IO.Class",),
    "environment access": ("System.Environment",),
    "clock": ("Data.Time", "System.Clock", "GHC.Clock", "System.CPUTime"),
    "random": ("System.Random",),
    "network": ("Network",),
}
CORE_CAPABILITY_USE_PATTERNS = {
    "IO": r"\bIO\b",
    "MonadIO": r"\bMonadIO\b",
    "environment access": (
        r"\b(?:getArgs|getEnv|getEnvironment|lookupEnv|setEnv|unsetEnv|"
        r"withArgs|withProgName)\b"
    ),
    "clock": (
        r"\b(?:getCurrentTime|getZonedTime|getMonotonicTime|getCPUTime|"
        r"getPOSIXTime)\b"
    ),
    "random": r"\b(?:randomIO|randomRIO|newStdGen|mkStdGen|splitGen)\b",
    "network": r"\b(?:socket|connect|listen|accept|send|recv|getAddrInfo)\b",
    "Control.Exception.throw": r"\bthrow\b",
    "Control.Exception.throwIO": r"\bthrowIO\b",
}
PROFILE_OPTIONS = {
    "baseline-o0-fasm": ["-O0", "-fasm"],
    "optimized-o2-fasm": ["-O2", "-fasm"],
}
PROFILE_ORDER_BLOCKS = [
    ["baseline-o0-fasm", "optimized-o2-fasm"],
    ["optimized-o2-fasm", "baseline-o0-fasm"],
    ["optimized-o2-fasm", "baseline-o0-fasm"],
    ["baseline-o0-fasm", "optimized-o2-fasm"],
]
PENDING_PROFILE_FIELDS = {
    "schemaVersion",
    "selectionStatus",
    "profileId",
    "ghcOptions",
    "compilerVersion",
    "compilerSha256",
    "sourceTreeSha256",
    "optionsSha256",
    "qualificationPlanSha256",
    "selectorConfigSha256",
    "fallbackProfile",
    "selectedBy",
    "fullCorrectnessStatus",
    "qualificationStatus",
}
FINAL_PROFILE_FIELDS = {
    "schemaVersion",
    "profileId",
    "ghcOptions",
    "compilerVersion",
    "compilerSha256",
    "sourceTreeSha256",
    "optionsSha256",
    "fullCorrectnessSha256",
    "qualificationPlanSha256",
    "qualificationArtifactSha256",
    "selectorConfigSha256",
    "fallbackProfile",
    "selectedBy",
}
AUTHORITATIVE_GHC_SHA256 = (
    "d0c0dd79a1bcc5dce3c9e73613c1be51f61b78d5ef7c0970ffe9f142a90a5e2c"
)


def canonical_json_bytes(value: Any, *, trailing_newline: bool = False) -> bytes:
    """정렬 key와 compact separator를 쓰는 프로젝트 canonical JSON bytes."""

    text = json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    if trailing_newline:
        text += "\n"
    return text.encode("utf-8")


def canonical_sha256(value: Any) -> str:
    """Canonical JSON 표현의 lowercase SHA-256을 반환한다."""

    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def canonical_source_manifest_sha256(files: Mapping[str, Mapping[str, str]]) -> str:
    """LC_ALL=C `sha256sum` line closure와 같은 path-sorted manifest SHA-256."""

    lines: list[str] = []
    for path in sorted(files, key=str.encode):
        metadata = files[path]
        digest = metadata.get("sha256")
        if (
            not isinstance(digest, str)
            or re.fullmatch(r"[0-9a-f]{64}", digest) is None
            or "\n" in path
        ):
            raise EvidenceError("invalid source manifest line input")
        lines.append(f"{digest}  {path}\n")
    return hashlib.sha256("".join(lines).encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    """regular file bytes의 lowercase SHA-256을 계산한다."""

    if path.is_symlink() or not path.is_file():
        raise EvidenceError(f"not a regular evidence input: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def strict_json_load(path: Path) -> Any:
    """중복 key와 non-finite constant를 거부하며 JSON을 읽는다."""

    def object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise EvidenceError(f"duplicate JSON key in {path}: {key}")
            result[key] = value
        return result

    def reject_constant(token: str) -> None:
        raise EvidenceError(f"non-finite JSON token in {path}: {token}")

    try:
        return json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=object_pairs,
            parse_constant=reject_constant,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise EvidenceError(f"invalid JSON input {path}: {exc}") from exc


def atomic_write_json(path: Path, value: Any) -> None:
    """같은 directory에서 fsync 후 canonical JSON을 atomic replace한다."""

    if path.is_symlink():
        raise EvidenceError(f"refusing to replace symlink: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = canonical_json_bytes(value, trailing_newline=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _strip_non_pragma_block_comments(text: str) -> str:
    """LANGUAGE pragma는 남기고 source inspection을 흐리는 block comment를 제거한다."""

    return re.sub(r"\{-(?!#).*?-\}", " ", text, flags=re.DOTALL)


def _strip_comments_and_literals(text: str) -> str:
    """보수적 token audit용으로 comment와 string/character literal을 공백화한다."""

    without_blocks = _strip_non_pragma_block_comments(text)
    without_lines = re.sub(r"--[^\n]*", " ", without_blocks)
    without_strings = re.sub(r'"(?:\\.|[^"\\])*"', '""', without_lines)
    return re.sub(r"'(?:\\.|[^'\\])'", "''", without_strings)


def parse_haskell_module(payload: bytes) -> ParsedModule:
    """UTF-8 Haskell module 선언, LANGUAGE pragma, direct import identity를 추출한다."""

    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise EvidenceError("Haskell source must be strict UTF-8") from exc
    pragmas = re.findall(r"\{-#\s*LANGUAGE\s+(.+?)#-\}", text, flags=re.DOTALL)
    extensions: list[str] = []
    for pragma in pragmas:
        for extension in pragma.replace("\n", " ").split(","):
            normalized = extension.strip()
            if normalized:
                extensions.append(normalized)
    if len(extensions) != len(set(extensions)):
        raise EvidenceError("duplicate LANGUAGE extension declaration")

    inspected = _strip_non_pragma_block_comments(text)
    module_match = re.search(
        r"(?m)^\s*module\s+([A-Z][A-Za-z0-9]*(?:\.[A-Z][A-Za-z0-9]*)*)"
        r"\s*(?:\(|where)",
        inspected,
    )
    if module_match is None:
        raise EvidenceError("Haskell source is missing an explicit module declaration")
    imports = re.findall(
        r'(?m)^\s*import\s+(?:safe\s+)?(?:qualified\s+)?'
        r'(?:"[^"]+"\s+)?'
        r"([A-Z][A-Za-z0-9_']*(?:\.[A-Z][A-Za-z0-9_']*)*)",
        inspected,
    )
    return ParsedModule(
        module_name=module_match.group(1),
        extensions=tuple(extensions),
        imports=tuple(imports),
    )


def _candidate_source_paths(root: Path) -> list[Path]:
    """Candidate compile roots의 regular `.hs` closure를 정렬해 반환한다."""

    selected: list[Path] = []
    for relative_root in CANDIDATE_ROOTS:
        source_root = root / relative_root
        if source_root.is_symlink() or not source_root.is_dir():
            raise EvidenceError(f"candidate source root is missing or not regular: {relative_root}")
        for path in sorted(source_root.rglob("*"), key=lambda item: item.as_posix().encode()):
            relative = path.relative_to(root).as_posix()
            if path.is_symlink():
                raise EvidenceError(f"candidate source symlink is forbidden: {relative}")
            if not path.is_file():
                continue
            if relative.endswith(FORBIDDEN_COMPILED_SUFFIXES):
                raise EvidenceError(f"forbidden compilable suffix: {relative}")
            if path.suffix == ".hs":
                selected.append(path)
    if not selected:
        raise EvidenceError("candidate Haskell source closure is empty")
    return selected


def _git_candidate_path_sets(root: Path) -> tuple[set[str], set[str]]:
    """Git cached+untracked closure를 함께 읽고 untracked escape를 분리한다."""

    try:
        repo_root = Path(
            subprocess.run(
                ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
        ).resolve(strict=True)
        relative_root = root.resolve(strict=True).relative_to(repo_root).as_posix()
        if relative_root == ".":
            relative_root = ""
        pathspecs = [
            f"{relative_root}/{candidate_root}".lstrip("/")
            for candidate_root in CANDIDATE_ROOTS
        ] + [f"{relative_root}/{path}".lstrip("/") for path in CONFIGURATION_PATHS]
        combined = subprocess.run(
            [
                "git",
                "-C",
                str(repo_root),
                "ls-files",
                "-z",
                "--cached",
                "--others",
                "--exclude-standard",
                "--",
                *pathspecs,
            ],
            check=True,
            capture_output=True,
        )
        cached = subprocess.run(
            ["git", "-C", str(repo_root), "ls-files", "-z", "--cached", "--", *pathspecs],
            check=True,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError, ValueError) as exc:
        raise EvidenceError("unable to enumerate tracked Haskell inputs") from exc
    prefix = f"{relative_root}/" if relative_root else ""
    def normalize(payload: bytes) -> set[str]:
        result: set[str] = set()
        for raw_path in payload.split(b"\0"):
            if not raw_path:
                continue
            path = raw_path.decode("utf-8")
            if prefix and not path.startswith(prefix):
                raise EvidenceError(f"candidate input escaped Haskell root: {path}")
            result.add(path[len(prefix) :])
        return result

    combined_paths = normalize(combined.stdout)
    cached_paths = normalize(cached.stdout)
    return cached_paths, combined_paths - cached_paths


def _role_for_path(relative: str) -> str:
    if relative in CONFIGURATION_PATHS:
        return "configuration"
    if relative.startswith("test/"):
        return "test"
    if relative.startswith("benchmark/"):
        return "benchmark"
    if relative.startswith(("src/", "app/")):
        return "main"
    raise EvidenceError(f"unclassified source input: {relative}")


def build_source_manifest(
    root: Path,
    *,
    tracked_paths: set[str] | None = None,
) -> dict[str, Any]:
    """Tracked/format/compile/lint/profile consumer가 공유할 exact input manifest를 만든다."""

    root = root.resolve(strict=True)
    source_paths = _candidate_source_paths(root)
    expected = {path.relative_to(root).as_posix() for path in source_paths}
    for relative in CONFIGURATION_PATHS:
        path = root / relative
        if path.is_symlink() or not path.is_file():
            raise EvidenceError(f"required configuration input is missing: {relative}")
        expected.add(relative)
    if tracked_paths is None:
        actual_tracked, untracked = _git_candidate_path_sets(root)
        if untracked:
            raise EvidenceError(f"untracked candidate input: {sorted(untracked)}")
    else:
        actual_tracked = tracked_paths
    forbidden = sorted(
        path for path in actual_tracked if path.endswith(FORBIDDEN_COMPILED_SUFFIXES)
    )
    if forbidden:
        raise EvidenceError(f"forbidden compilable suffix: {forbidden}")
    if actual_tracked != expected:
        raise EvidenceError(
            "tracked source input set mismatch: "
            f"missing={sorted(expected - actual_tracked)}, "
            f"stale={sorted(actual_tracked - expected)}"
        )
    files = {
        relative: {
            "role": _role_for_path(relative),
            "sha256": sha256_file(root / relative),
        }
        for relative in sorted(expected, key=str.encode)
    }
    return {
        "schemaVersion": "s1.4x-source-input-manifest-v1",
        "language": "haskell",
        "files": files,
        "inputSets": dict(MANDATORY_INPUT_SETS),
        "canonicalManifestSha256": canonical_source_manifest_sha256(files),
    }


def validate_source_manifest(root: Path, manifest_path: Path) -> dict[str, Any]:
    """Tracked manifest bytes와 현재 source closure가 byte-for-byte 같은지 검증한다."""

    expected = build_source_manifest(root)
    actual = strict_json_load(manifest_path)
    if actual != expected:
        raise EvidenceError("source-input manifest drift")
    if manifest_path.read_bytes() != canonical_json_bytes(expected, trailing_newline=True):
        raise EvidenceError("source-input manifest is not canonical JSON")
    return expected


def benchmark_source_tree_entries(root: Path) -> list[dict[str, str]]:
    """Profile 자체를 제외한 compile, audit, workflow subject closure."""

    paths = _candidate_source_paths(root)
    for relative in (
        "package.yaml",
        "s1-4x-haskell.cabal",
        "stack.yaml",
        "stack.yaml.lock",
        *WORKFLOW_INPUT_PATHS,
    ):
        path = root / relative
        if path.is_symlink() or not path.is_file():
            raise EvidenceError(f"benchmark source-tree input is missing: {relative}")
        paths.append(path)
    return [
        {
            "path": path.relative_to(root).as_posix(),
            "sha256": sha256_file(path),
        }
        for path in sorted(paths, key=lambda item: item.relative_to(root).as_posix().encode())
    ]


def benchmark_source_tree_sha256(root: Path) -> str:
    """Profile artifact가 묶는 candidate source/build closure SHA-256."""

    return canonical_sha256(benchmark_source_tree_entries(root.resolve(strict=True)))


def property_execution_closure_sha256(root: Path) -> str:
    """Property binary와 evidence가 공유하는 current source/config bytes를 결속한다."""

    root = root.resolve(strict=True)
    paths = _candidate_source_paths(root)
    for relative in PROPERTY_CLOSURE_CONFIGURATION_PATHS:
        path = root / relative
        if path.is_symlink() or not path.is_file():
            raise EvidenceError(f"property closure input is missing or unsafe: {relative}")
        paths.append(path)
    entries: list[bytes] = []
    for path in sorted(
        paths,
        key=lambda item: item.relative_to(root).as_posix().encode(),
    ):
        relative = path.relative_to(root).as_posix()
        entries.append(
            relative.encode("utf-8")
            + b"\0"
            + sha256_file(path).encode("ascii")
            + b"\n"
        )
    return hashlib.sha256(b"".join(entries)).hexdigest()


def _require_sha256(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise EvidenceError(f"selected profile {field} is not lowercase SHA-256")
    return value


def validate_selected_profile_document(
    document: Any,
    *,
    expected_compiler_sha256: str,
    expected_source_tree_sha256: str,
    expected_qualification_plan_sha256: str,
    expected_selector_config_sha256: str,
) -> dict[str, Any]:
    """Pending/final profile의 options와 source/plan identity를 exact-object로 검증한다."""

    if not isinstance(document, dict):
        raise EvidenceError("selected profile must be an object")
    schema_version = document.get("schemaVersion")
    if schema_version == "s1.4x-haskell-selected-profile-pending-v1":
        expected_fields = PENDING_PROFILE_FIELDS
        expected_values = {
            "selectionStatus": "PENDING_BASELINE",
            "profileId": "baseline-o0-fasm",
            "ghcOptions": PROFILE_OPTIONS["baseline-o0-fasm"],
            "compilerVersion": "9.10.3",
            "fallbackProfile": "baseline-o0-fasm",
            "selectedBy": "pending-fail-closed-baseline",
            "fullCorrectnessStatus": "NOT_RUN",
            "qualificationStatus": "NOT_RUN",
        }
        if any(document.get(key) != value for key, value in expected_values.items()):
            raise EvidenceError("pending baseline profile options/status drift")
    elif schema_version == "s1.4x-haskell-selected-profile-v1":
        expected_fields = FINAL_PROFILE_FIELDS
        profile_id = document.get("profileId")
        if profile_id not in PROFILE_OPTIONS:
            raise EvidenceError("selected profile id is invalid")
        if document.get("ghcOptions") != PROFILE_OPTIONS[profile_id]:
            raise EvidenceError("selected profile options do not match profile id")
        if document.get("compilerVersion") != "9.10.3":
            raise EvidenceError("selected profile compiler version drift")
        if document.get("fallbackProfile") != "baseline-o0-fasm":
            raise EvidenceError("selected profile fallback drift")
        if document.get("selectedBy") not in {
            "frozen-criterion-selector",
            "proven-fallback",
        }:
            raise EvidenceError("selected profile selector identity drift")
        for field in ("fullCorrectnessSha256", "qualificationArtifactSha256"):
            _require_sha256(document.get(field), field=field)
    else:
        raise EvidenceError("selected profile schema version drift")

    if set(document) != expected_fields:
        raise EvidenceError("selected profile field set drift")
    expected_hashes = {
        "compilerSha256": expected_compiler_sha256,
        "sourceTreeSha256": expected_source_tree_sha256,
        "qualificationPlanSha256": expected_qualification_plan_sha256,
        "selectorConfigSha256": expected_selector_config_sha256,
    }
    for field, expected in expected_hashes.items():
        _require_sha256(expected, field=f"expected {field}")
        if document.get(field) != expected:
            raise EvidenceError(f"selected profile {field} drift")
    options = document.get("ghcOptions")
    if not isinstance(options, list) or document.get("optionsSha256") != canonical_sha256(options):
        raise EvidenceError("selected profile options SHA-256 drift")
    return document


def build_pending_selected_profile(
    root: Path,
    *,
    qualification_plan: Path,
) -> dict[str, Any]:
    """Qualification 전에는 O0/fasm만 허용하는 fail-closed pending profile을 만든다."""

    plan = strict_json_load(qualification_plan)
    if not isinstance(plan, dict):
        raise EvidenceError("benchmark qualification plan must be an object")
    selector = plan.get("haskellProfileQualification")
    if not isinstance(selector, dict):
        raise EvidenceError("Haskell profile selector configuration is missing")
    options = PROFILE_OPTIONS["baseline-o0-fasm"]
    return {
        "schemaVersion": "s1.4x-haskell-selected-profile-pending-v1",
        "selectionStatus": "PENDING_BASELINE",
        "profileId": "baseline-o0-fasm",
        "ghcOptions": options,
        "compilerVersion": "9.10.3",
        "compilerSha256": AUTHORITATIVE_GHC_SHA256,
        "sourceTreeSha256": benchmark_source_tree_sha256(root),
        "optionsSha256": canonical_sha256(options),
        "qualificationPlanSha256": sha256_file(qualification_plan),
        "selectorConfigSha256": canonical_sha256(selector),
        "fallbackProfile": "baseline-o0-fasm",
        "selectedBy": "pending-fail-closed-baseline",
        "fullCorrectnessStatus": "NOT_RUN",
        "qualificationStatus": "NOT_RUN",
    }


def _parse_default_extensions(package_text: str) -> tuple[str, ...]:
    lines = package_text.splitlines()
    try:
        start = lines.index("default-extensions:")
    except ValueError as exc:
        raise EvidenceError("package.yaml is missing default-extensions") from exc
    extensions: list[str] = []
    for line in lines[start + 1 :]:
        if line and not line.startswith((" ", "\t")):
            break
        match = re.fullmatch(r"\s+-\s+([A-Za-z][A-Za-z0-9]*)\s*", line)
        if match is not None:
            extensions.append(match.group(1))
    if not extensions or len(extensions) != len(set(extensions)):
        raise EvidenceError("package.yaml default-extensions are empty or duplicate")
    return tuple(extensions)


def module_category(relative: str, module_name: str) -> str:
    """Frozen path/module identity로 candidate category를 단 하나 결정한다."""

    if relative.startswith("src/core/"):
        return "safe-scalar" if module_name in SAFE_SCALAR_MODULES else "audited-pure-vector"
    if relative.startswith(("src/contract/", "app/")):
        return "io-shell"
    if relative.startswith("test/"):
        return "test"
    if relative.startswith("benchmark/"):
        return "benchmark"
    raise EvidenceError(f"module path has no category: {relative}")


def _policy_string_tuple(
    policy: Mapping[str, Any],
    key: str,
) -> tuple[str, ...]:
    value = policy.get(key)
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, str) or not item for item in value)
        or len(value) != len(set(value))
    ):
        raise EvidenceError(f"module-safety policy {key} must be unique strings")
    return tuple(value)


def validate_module_safety_policy(policy: Mapping[str, Any]) -> None:
    """정책의 top-level/nested contract와 모든 executable control을 exact 검증한다."""

    if set(policy) != MODULE_SAFETY_POLICY_FIELDS:
        raise EvidenceError("module-safety policy field set drift")
    if (
        policy.get("schemaVersion") != "s1.4x-haskell-module-safety-policy-v1"
        or policy.get("language") != "GHC2024"
        or policy.get("everyModuleExactlyOneCategory") is not True
    ):
        raise EvidenceError("module-safety policy identity drift")

    categories = policy.get("categories")
    expected_category_fields = {
        "safe-scalar": {"mandatoryCompileMode", "allowedRoles", "forbiddenImports"},
        "audited-pure-vector": {
            "mandatoryCompileMode",
            "allowedRoles",
            "allowedDirectExternalImports",
            "forbiddenDirectImports",
        },
        "io-shell": {"mandatoryCompileMode", "allowedRoles", "coreMayDependOnCategory"},
        "test": {"mandatoryCompileMode", "allowedRoles"},
        "benchmark": {"mandatoryCompileMode", "allowedRoles"},
    }
    expected_compile_modes = {
        "safe-scalar": "Safe",
        "audited-pure-vector": "SafeHaskell-None-with-audited-purity-gate",
        "io-shell": "ordinary",
        "test": "ordinary",
        "benchmark": "ordinary",
    }
    if not isinstance(categories, Mapping) or set(categories) != set(
        expected_category_fields
    ):
        raise EvidenceError("module-safety category set drift")
    for category, expected_fields in expected_category_fields.items():
        configuration = categories.get(category)
        if (
            not isinstance(configuration, Mapping)
            or set(configuration) != expected_fields
            or configuration.get("mandatoryCompileMode")
            != expected_compile_modes[category]
            or not isinstance(configuration.get("allowedRoles"), list)
            or not configuration["allowedRoles"]
        ):
            raise EvidenceError(f"module-safety category contract drift: {category}")
    if categories["io-shell"].get("coreMayDependOnCategory") is not False:
        raise EvidenceError("module-safety core-to-shell category policy drift")

    mandatory = _policy_string_tuple(policy, "mandatoryCoreExtensions")
    positive = _policy_string_tuple(policy, "forbiddenCorePositiveExtensions")
    if (
        mandatory != EXPECTED_CORE_NEGATIVE_EXTENSIONS
        or positive != EXPECTED_CORE_POSITIVE_EXTENSIONS
        or tuple(
            extension.removeprefix("No")
            for extension in mandatory
        )
        != positive
    ):
        raise EvidenceError("module-safety core extension policy drift")

    suffix_policy = policy.get("candidateSourceSuffixPolicy")
    if suffix_policy != {
        "allowedSourceSuffixes": [".hs"],
        "forbiddenCompilableSuffixes": [".lhs", ".hsc", ".hs-boot"],
        "allowedNonHsConfigurationPaths": [
            "selected-profile.v1.json",
            "package.yaml",
        ],
        "nonHsEntriesMustHaveRole": "configuration",
    }:
        raise EvidenceError("module-safety candidate suffix policy drift")
    if policy.get("candidateDerivingPolicy") != "deriving stock only":
        raise EvidenceError("module-safety candidate deriving policy drift")
    if policy.get("forbiddenCandidateModuleDeclarations") != [
        "Trustworthy",
        "Unsafe",
    ]:
        raise EvidenceError("module-safety declaration policy drift")
    if _policy_string_tuple(
        policy,
        "forbiddenCoreTypesAndUses",
    ) != EXPECTED_CORE_TYPES_AND_USES:
        raise EvidenceError("module-safety core capability policy drift")
    if _policy_string_tuple(
        policy,
        "forbiddenPartialAndUnsafeSymbols",
    ) != EXPECTED_PARTIAL_AND_UNSAFE_SYMBOLS:
        raise EvidenceError("module-safety partial/unsafe policy drift")
    if _policy_string_tuple(
        policy,
        "forbiddenSourceLocalControls",
    ) != EXPECTED_SOURCE_LOCAL_CONTROLS:
        raise EvidenceError("module-safety source-local control policy drift")

    vector = policy.get("vectorProvenance")
    if not isinstance(vector, Mapping) or set(vector) != {
        "package",
        "version",
        "module",
        "safeHaskell",
        "sourceSha256Semantics",
        "officialArchiveUri",
        "officialArchiveSha256",
        "stackageSnapshotUri",
        "stackageCabalRevisionSha256",
        "stackageCabalRevisionSize",
        "pantryTreeSha256",
        "sourceSha256RequiredAtGate2",
        "upstreamTransitiveAllowlistRequiredFields",
        "upstreamTransitiveAllowedEdgeKinds",
        "upstreamTransitiveAllowlistMode",
        "upstreamTransitiveAllowlist",
        "upstreamTransitiveEdgesMayExist",
        "candidateDirectUnsafeOrPrimopUseCount",
    }:
        raise EvidenceError("module-safety vector provenance field set drift")

    invariants = policy.get("candidateGraphInvariants")
    if not isinstance(invariants, Mapping) or set(invariants) != {
        "candidateDirectForeignImportCount",
        "candidateDirectUnsafeImportCount",
        "candidateHomeCoreToShellEdgeCount",
        "candidateTrustworthyDeclarationCount",
        "candidateUnsafeDeclarationCount",
        "unclassifiedModuleCount",
        "newUnknownUpstreamTransitiveEdgeCount",
    } or any(value != 0 for value in invariants.values()):
        raise EvidenceError("module-safety graph invariant drift")
    if policy.get("resultEdgePartitions") != [
        "candidateDirectImports",
        "candidateHomeModuleEdges",
        "upstreamTransitiveEdges",
    ]:
        raise EvidenceError("module-safety edge partition policy drift")

    edge_contract = policy.get("resultEdgeCategoryContract")
    if not isinstance(edge_contract, Mapping) or set(edge_contract) != {
        "candidateDirectImportsRequiredCategoryFields",
        "candidateHomeModuleEdgesRequiredCategoryFields",
        "embeddedCategoriesMustMatchModuleInventory",
        "coreCategories",
        "shellCategories",
        "allowedHomeClassifications",
        "coreToShellAllowed",
        "allCandidateForbiddenDirectImportPatterns",
        "allCoreAdditionalForbiddenDirectImports",
        "safeScalarAdditionalForbiddenDirectImports",
    }:
        raise EvidenceError("module-safety edge-category contract field set drift")
    if (
        edge_contract.get("coreCategories") != ["safe-scalar", "audited-pure-vector"]
        or edge_contract.get("shellCategories") != ["io-shell", "test", "benchmark"]
        or edge_contract.get("coreToShellAllowed") is not False
        or edge_contract.get("allCandidateForbiddenDirectImportPatterns")
        != ["Foreign.*", "Unsafe.*", "System.IO.Unsafe", "GHC.IO.Unsafe"]
        or edge_contract.get("allCoreAdditionalForbiddenDirectImports")
        != ["System.IO", "Control.Monad.IO.Class", "Debug.Trace"]
        or edge_contract.get("safeScalarAdditionalForbiddenDirectImports")
        != ["Data.Vector.Unboxed"]
    ):
        raise EvidenceError("module-safety edge-category executable policy drift")

    conditional = policy.get("conditionalOptimizations")
    if not isinstance(conditional, Mapping) or set(conditional) != {
        "runSTAndMutableUnboxedVector",
        "strictDataUnpackInlineSpecialise",
    }:
        raise EvidenceError("module-safety conditional optimization field set drift")
    if policy.get("authoritativeProfiles") != {
        "baseline": ["-O0", "-fasm"],
        "optimized": ["-O2", "-fasm"],
        "runtime": ["+RTS", "-N1", "-RTS"],
        "fallbackProfile": "baseline-o0-fasm",
    }:
        raise EvidenceError("module-safety authoritative profile policy drift")
    _policy_string_tuple(policy, "forbiddenOptimizationFlags")
    _policy_string_tuple(policy, "hardFailureConditions")


def audit_candidate_source(
    *,
    relative: str,
    parsed: ParsedModule,
    payload: bytes,
    category: str,
    default_extensions: Sequence[str],
    policy: Mapping[str, Any],
) -> None:
    """모든 candidate local control과 core capability/deriving 경계를 fail-closed한다."""

    text = payload.decode("utf-8")
    code = _strip_comments_and_literals(text)
    core = category in CORE_CATEGORIES
    positive_extensions = set(
        _policy_string_tuple(policy, "forbiddenCorePositiveExtensions")
    )
    if core:
        enabled_forbidden = sorted(
            positive_extensions.intersection((*default_extensions, *parsed.extensions)),
            key=str.encode,
        )
        if enabled_forbidden:
            raise EvidenceError(
                f"forbidden core positive extension in {relative}: {enabled_forbidden}"
            )

    source_controls = set(_policy_string_tuple(policy, "forbiddenSourceLocalControls"))
    local_violations: list[str] = []
    if "OPTIONS_GHC" in source_controls and re.search(
        r"\{-#\s*OPTIONS_GHC\b",
        text,
    ):
        local_violations.append("OPTIONS_GHC")
    if "HLint ignore" in source_controls and re.search(
        r"(?i)\bHLint\s*:?\s*ignore\b",
        text,
    ):
        local_violations.append("HLint ignore")
    extension_controls = {
        "global Strict": "Strict",
        "LinearTypes": "LinearTypes",
        "TemplateHaskell": "TemplateHaskell",
        "CPP": "CPP",
        "MagicHash": "MagicHash",
    }
    for control, extension in extension_controls.items():
        if control in source_controls and extension in parsed.extensions:
            local_violations.append(control)
    if local_violations:
        raise EvidenceError(
            f"forbidden source-local control in {relative}: {sorted(local_violations)}"
        )

    forbidden_declarations = set(
        _policy_string_tuple(policy, "forbiddenCandidateModuleDeclarations")
    )
    declarations = forbidden_declarations.intersection(parsed.extensions)
    if declarations:
        raise EvidenceError(
            f"forbidden Safe Haskell declaration in {relative}: {sorted(declarations)}"
        )
    if re.search(r"\b(?:foreign\s+import|foreign\s+export)\b", code):
        raise EvidenceError(f"candidate native interop form in {relative}")
    if not core:
        return

    forbidden_capabilities = set(
        _policy_string_tuple(policy, "forbiddenCoreTypesAndUses")
    )
    for capability, prefixes in CORE_CAPABILITY_IMPORT_PREFIXES.items():
        if capability not in forbidden_capabilities:
            continue
        if any(_has_prefix(imported, prefixes) for imported in parsed.imports):
            raise EvidenceError(
                f"forbidden core capability import/use in {relative}: {capability}"
            )
    for capability, pattern in CORE_CAPABILITY_USE_PATTERNS.items():
        if capability in forbidden_capabilities and re.search(pattern, code):
            raise EvidenceError(
                f"forbidden core capability import/use in {relative}: {capability}"
            )
    deriving_occurrences = re.findall(r"(?m)^\s*deriving\b([^\n]*)", code)
    if (
        policy.get("candidateDerivingPolicy") == "deriving stock only"
        and any(
            not occurrence.lstrip().startswith("stock")
            for occurrence in deriving_occurrences
        )
    ):
        raise EvidenceError(f"candidate core deriving must be stock in {relative}")
    categories = policy["categories"]
    compile_mode = categories[category]["mandatoryCompileMode"]
    if compile_mode == "Safe" and "Safe" not in parsed.extensions:
        raise EvidenceError(f"safe-scalar module omits Safe: {relative}")
    if (
        compile_mode == "SafeHaskell-None-with-audited-purity-gate"
        and "Safe" in parsed.extensions
    ):
        raise EvidenceError(f"audited-pure-vector must not claim Safe: {relative}")


def _has_prefix(module_name: str, prefixes: Iterable[str]) -> bool:
    return any(
        module_name == prefix or module_name.startswith(prefix + ".") for prefix in prefixes
    )


def parse_show_iface_home_imports(
    output: str,
    *,
    candidate_modules: set[str],
) -> tuple[str, ...]:
    """`ghc --show-iface`의 direct module dependencies에서 candidate home edge만 읽는다."""

    section = re.search(
        r"(?ms)^direct module dependencies:(.*?)^boot module dependencies:",
        output,
    )
    if section is None:
        raise EvidenceError("GHC interface direct dependency section is missing")
    imports: list[str] = []
    for token in section.group(1).split():
        module_name = token.rsplit(":", 1)[-1]
        if module_name in candidate_modules:
            imports.append(module_name)
    if len(imports) != len(set(imports)):
        raise EvidenceError("GHC interface repeats a direct home dependency")
    return tuple(sorted(imports, key=str.encode))


def _shortest_module_path(
    graph: Mapping[str, tuple[str, ...]],
    *,
    start: str,
    target: str,
) -> list[str]:
    queue: deque[list[str]] = deque([[start]])
    visited = {start}
    while queue:
        path = queue.popleft()
        current = path[-1]
        if current == target:
            return path
        for imported in graph.get(current, ()):
            if imported in graph and imported not in visited:
                visited.add(imported)
                queue.append([*path, imported])
    raise EvidenceError(f"vector source graph has no path from {start} to {target}")


def derive_vector_transitive_edges(
    sources: Mapping[str, bytes],
    *,
    source_sha256: str,
    provenance: str,
) -> list[dict[str, str]]:
    """Actual vector source imports에서 두 unsafe leaf와 한 compiler-primop leaf를 도출한다."""

    parsed = {
        module_name: parse_haskell_module(payload)
        for module_name, payload in sources.items()
    }
    if set(parsed) != set(sources):
        raise EvidenceError("vector source module identity is ambiguous")
    for expected_name, module in parsed.items():
        if module.module_name != expected_name:
            raise EvidenceError("vector source map key does not match its module declaration")
    graph = {module_name: module.imports for module_name, module in parsed.items()}
    reachable: set[str] = set()
    queue: deque[str] = deque(["Data.Vector.Unboxed"])
    while queue:
        current = queue.popleft()
        if current in reachable:
            continue
        if current not in graph:
            raise EvidenceError(f"vector source graph is missing module {current}")
        reachable.add(current)
        queue.extend(imported for imported in graph[current] if imported in graph)
    unsafe_targets = sorted(
        module_name
        for module_name in reachable
        if "Unsafe.Coerce" in graph[module_name]
    )
    expected_unsafe_targets = [
        "Data.Vector.Primitive",
        "Data.Vector.Primitive.Mutable",
    ]
    if unsafe_targets != expected_unsafe_targets:
        raise EvidenceError(
            "vector unsafe target set drift: "
            f"expected={expected_unsafe_targets}, actual={unsafe_targets}"
        )
    check_module = "Data.Vector.Internal.Check"
    check_source = sources.get(check_module)
    if (
        check_module not in reachable
        or check_source is None
        or "GHC.Exts" not in graph[check_module]
        or re.search(rb"\bInt#", check_source) is None
    ):
        raise EvidenceError("vector compiler-primop edge drift")

    def identity(import_path: str, edge_kind: str) -> dict[str, str]:
        return {
            "package": "vector",
            "version": "0.13.2.0",
            "sourceSha256": source_sha256,
            "importPath": import_path,
            "provenance": provenance,
            "edgeKind": edge_kind,
        }

    primitive_path = _shortest_module_path(
        graph,
        start="Data.Vector.Unboxed",
        target="Data.Vector.Primitive",
    )
    mutable_path = _shortest_module_path(
        graph,
        start="Data.Vector.Unboxed",
        target="Data.Vector.Primitive.Mutable",
    )
    check_path = _shortest_module_path(
        graph,
        start="Data.Vector.Unboxed",
        target=check_module,
    )
    return [
        identity(" -> ".join([*primitive_path, "Unsafe.Coerce"]), "unsafe-import"),
        identity(" -> ".join([*mutable_path, "Unsafe.Coerce"]), "unsafe-import"),
        identity(" -> ".join([*check_path, "GHC.Exts(Int#)"]), "compiler-primop"),
    ]


def audit_vector_archive(
    archive_path: Path,
    *,
    policy: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Official vector archive bytes와 actual source graph를 frozen exact allowlist와 비교한다."""

    archive_path = archive_path.resolve(strict=True)
    vector = policy.get("vectorProvenance")
    if not isinstance(vector, Mapping):
        raise EvidenceError("vector provenance policy is missing")
    source_sha256 = sha256_file(archive_path)
    if source_sha256 != vector.get("officialArchiveSha256"):
        raise EvidenceError("vector official archive SHA-256 mismatch")
    raw_allowlist = vector.get("upstreamTransitiveAllowlist")
    if not isinstance(raw_allowlist, list) or len(raw_allowlist) != 3:
        raise EvidenceError("vector transitive policy allowlist drift")
    provenance_values = {
        edge.get("provenance") for edge in raw_allowlist if isinstance(edge, Mapping)
    }
    if len(provenance_values) != 1:
        raise EvidenceError("vector transitive policy provenance drift")
    provenance = next(iter(provenance_values))
    if not isinstance(provenance, str) or not provenance:
        raise EvidenceError("vector transitive policy provenance is invalid")

    sources: dict[str, bytes] = {}
    cabal_payload: bytes | None = None
    try:
        with tarfile.open(archive_path, mode="r:gz") as archive:
            for member in archive.getmembers():
                if member.issym() or member.islnk():
                    raise EvidenceError("vector archive links are forbidden")
                if not member.isfile():
                    continue
                if member.name.endswith("/vector.cabal"):
                    extracted = archive.extractfile(member)
                    if extracted is None or cabal_payload is not None:
                        raise EvidenceError("vector archive Cabal identity is ambiguous")
                    cabal_payload = extracted.read()
                    continue
                if "/src/" not in member.name or not member.name.endswith(".hs"):
                    continue
                extracted = archive.extractfile(member)
                if extracted is None:
                    raise EvidenceError("unable to read vector archive source")
                payload = extracted.read()
                parsed = parse_haskell_module(payload)
                if parsed.module_name in sources:
                    raise EvidenceError("duplicate vector source module in archive")
                sources[parsed.module_name] = payload
    except (tarfile.TarError, OSError) as exc:
        raise EvidenceError("invalid vector source archive") from exc
    if cabal_payload is None or re.search(
        rb"(?im)^version:\s*0\.13\.2\.0\s*$",
        cabal_payload,
    ) is None:
        raise EvidenceError("vector archive package version mismatch")
    derived = derive_vector_transitive_edges(
        sources,
        source_sha256=source_sha256,
        provenance=provenance,
    )
    if derived != raw_allowlist:
        raise EvidenceError("vector transitive exact-set allowlist mismatch")
    return [{**edge, "allowlisted": True} for edge in derived]


def collect_interface_home_imports(
    *,
    interface_root: Path,
    ghc_bin: Path,
    inventory: Mapping[str, tuple[str, str, ParsedModule, bytes]],
) -> dict[str, tuple[str, ...]]:
    """Fresh build `.hi` closure를 `ghc --show-iface`로 읽어 actual home graph를 반환한다."""

    interface_root = interface_root.resolve(strict=True)
    ghc_bin = ghc_bin.resolve(strict=True)
    if interface_root.is_symlink() or not interface_root.is_dir():
        raise EvidenceError("compiler interface root must be a regular directory")
    if ghc_bin.is_symlink() or not ghc_bin.is_file() or not os.access(ghc_bin, os.X_OK):
        raise EvidenceError("GHC interface reader must be a regular executable")
    version = subprocess.run(
        [str(ghc_bin), "--numeric-version"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if version != "9.10.3":
        raise EvidenceError("module-safety interface audit requires GHC 9.10.3")

    candidate_modules = set(inventory)
    results: dict[str, tuple[str, ...]] = {}
    for module_name, (_, _, parsed, _) in inventory.items():
        suffix = module_name.replace(".", "/") + ".hi"
        candidates = sorted(
            (
                path
                for path in interface_root.rglob("*.hi")
                if not path.is_symlink() and path.as_posix().endswith("/" + suffix)
            ),
            key=lambda item: item.as_posix().encode(),
        )
        if not candidates:
            raise EvidenceError(f"compiled interface is missing for {module_name}")
        observed: set[tuple[str, ...]] = set()
        for interface in candidates:
            completed = subprocess.run(
                [str(ghc_bin), "--show-iface", str(interface)],
                check=True,
                capture_output=True,
                text=True,
            )
            if f"interface {module_name} 9103" not in completed.stdout:
                raise EvidenceError(f"compiled interface identity mismatch for {module_name}")
            observed.add(
                parse_show_iface_home_imports(
                    completed.stdout,
                    candidate_modules=candidate_modules,
                )
            )
        if len(observed) != 1:
            raise EvidenceError(f"compiled interface graph disagrees across artifacts: {module_name}")
        actual = next(iter(observed))
        supplemental = tuple(
            sorted(
                (imported for imported in parsed.imports if imported in candidate_modules),
                key=str.encode,
            )
        )
        if actual != supplemental:
            raise EvidenceError(
                f"compiler/source home graph mismatch for {module_name}: "
                f"compiler={actual}, source={supplemental}"
            )
        results[module_name] = actual
    return results


def build_module_safety_result(
    root: Path,
    *,
    numeric_root: Path,
    source_manifest_path: Path,
    interface_root: Path,
    ghc_bin: Path,
    vector_archive: Path,
) -> dict[str, Any]:
    """Candidate module/import graph와 frozen vector provenance를 typed report로 만든다."""

    root = root.resolve(strict=True)
    numeric_root = numeric_root.resolve(strict=True)
    manifest = validate_source_manifest(root, source_manifest_path)
    policy_path = numeric_root / "contract/haskell-module-safety-policy.v1.json"
    policy = strict_json_load(policy_path)
    if not isinstance(policy, dict):
        raise EvidenceError("module-safety policy must be an object")
    validate_module_safety_policy(policy)
    mandatory = _policy_string_tuple(policy, "mandatoryCoreExtensions")
    default_extensions = _parse_default_extensions(
        (root / "package.yaml").read_text(encoding="utf-8")
    )
    if not set(mandatory).issubset(default_extensions):
        raise EvidenceError("package.yaml omits mandatory core extensions")
    if "language: GHC2024" not in (root / "package.yaml").read_text(encoding="utf-8"):
        raise EvidenceError("package.yaml must use GHC2024")

    inventory: dict[str, tuple[str, str, ParsedModule, bytes]] = {}
    module_names: set[str] = set()
    modules: list[dict[str, Any]] = []
    manifest_files = manifest["files"]
    for path in _candidate_source_paths(root):
        relative = path.relative_to(root).as_posix()
        payload = path.read_bytes()
        parsed = parse_haskell_module(payload)
        if parsed.module_name in module_names:
            raise EvidenceError(f"duplicate candidate module name: {parsed.module_name}")
        module_names.add(parsed.module_name)
        category = module_category(relative, parsed.module_name)
        audit_candidate_source(
            relative=relative,
            parsed=parsed,
            payload=payload,
            category=category,
            default_extensions=default_extensions,
            policy=policy,
        )
        effective_extensions = tuple(dict.fromkeys((*default_extensions, *parsed.extensions)))
        compile_mode = policy["categories"][category]["mandatoryCompileMode"]
        source_sha256 = sha256_file(path)
        if manifest_files.get(relative, {}).get("sha256") != source_sha256:
            raise EvidenceError(f"module/source manifest hash mismatch: {relative}")
        inventory[parsed.module_name] = (relative, category, parsed, payload)
        modules.append(
            {
                "moduleName": parsed.module_name,
                "path": relative,
                "category": category,
                "compileMode": compile_mode,
                "extensions": list(effective_extensions),
                "sourceSha256": source_sha256,
            }
        )

    compiler_home_imports = collect_interface_home_imports(
        interface_root=interface_root,
        ghc_bin=ghc_bin,
        inventory=inventory,
    )
    direct_imports: list[dict[str, str]] = []
    home_edges: list[dict[str, str]] = []
    for module_name, (_, category, parsed, _) in inventory.items():
        for imported in parsed.imports:
            target = inventory.get(imported)
            if target is not None:
                continue
            if _has_prefix(imported, FORBIDDEN_ALL_IMPORT_PREFIXES):
                raise EvidenceError(f"forbidden candidate direct import: {module_name}->{imported}")
            if category in CORE_CATEGORIES and _has_prefix(
                imported,
                FORBIDDEN_CORE_IMPORT_PREFIXES,
            ):
                raise EvidenceError(f"forbidden candidate core import: {module_name}->{imported}")
            if category == "safe-scalar" and _has_prefix(
                imported,
                FORBIDDEN_SAFE_SCALAR_IMPORT_PREFIXES,
            ):
                raise EvidenceError(f"forbidden safe-scalar import: {module_name}->{imported}")
            direct_imports.append(
                {
                    "fromModule": module_name,
                    "fromCategory": category,
                    "importedModule": imported,
                    "classification": (
                        "allowed-pure" if category in CORE_CATEGORIES else "allowed-shell"
                    ),
                }
            )
        for imported in compiler_home_imports[module_name]:
            target = inventory.get(imported)
            if target is None:
                raise EvidenceError(f"compiler home edge target is missing: {imported}")
            target_category = target[1]
            if category in CORE_CATEGORIES and target_category in SHELL_CATEGORIES:
                raise EvidenceError(f"candidate core-to-shell edge: {module_name}->{imported}")
            if category in CORE_CATEGORIES:
                classification = "core-to-core"
            elif target_category in CORE_CATEGORIES:
                classification = "shell-to-core"
            else:
                classification = "shell-to-shell"
            home_edges.append(
                {
                    "fromModule": module_name,
                    "fromCategory": category,
                    "toModule": imported,
                    "toCategory": target_category,
                    "classification": classification,
                }
            )
    upstream_edges = audit_vector_archive(vector_archive, policy=policy)

    return {
        "schemaVersion": "s1.4x-haskell-module-safety-result-v1",
        "policySha256": sha256_file(policy_path),
        "sourceInputManifestSha256": sha256_file(source_manifest_path),
        "modules": sorted(modules, key=lambda item: item["moduleName"].encode()),
        "candidateDirectImports": sorted(
            direct_imports,
            key=lambda item: (item["fromModule"].encode(), item["importedModule"].encode()),
        ),
        "candidateHomeModuleEdges": sorted(
            home_edges,
            key=lambda item: (item["fromModule"].encode(), item["toModule"].encode()),
        ),
        "upstreamTransitiveEdges": upstream_edges,
        "unclassifiedModuleCount": 0,
        "candidateTrustworthyUnsafeDeclarationCount": 0,
        "candidateDirectUnsafeIoForeignImportCount": 0,
        "coreToShellEdgeCount": 0,
        "unknownTransitiveEdgeCount": 0,
        "staleAllowlistCount": 0,
        "aggregateStatus": "PASS",
    }


def _geometric_mean(values: Sequence[float]) -> float:
    if not values or any(not math.isfinite(value) or value <= 0.0 for value in values):
        raise EvidenceError("profile ratio must be finite and positive")
    return math.exp(math.fsum(math.log(value) for value in values) / len(values))


def select_haskell_profile(
    blocks: Sequence[Mapping[str, Any]],
    *,
    qualification_case_order: Sequence[str],
) -> ProfileSelection:
    """Frozen 4×7 paired-ratio selector로 optimized 또는 proven baseline을 고른다."""

    case_order = list(qualification_case_order)
    if (
        len(case_order) != 7
        or len(case_order) != len(set(case_order))
        or any(type(case_id) is not str or not case_id for case_id in case_order)
    ):
        raise EvidenceError("qualification case order must contain seven unique cases")
    if len(blocks) != 4:
        raise EvidenceError("qualification must contain exactly four order blocks")
    paired: list[float] = []
    by_case: dict[str, list[float]] = {case_id: [] for case_id in case_order}
    improving = 0
    for expected_index, block in enumerate(blocks):
        if type(block) is not dict or set(block) != {"orderBlock", "ratios"}:
            raise EvidenceError("qualification block field set drift")
        if type(block["orderBlock"]) is not int:
            raise EvidenceError("qualification order block index type drift")
        if block["orderBlock"] != expected_index:
            raise EvidenceError("qualification order block sequence drift")
        ratios = block["ratios"]
        if type(ratios) is not dict or set(ratios) != set(case_order):
            raise EvidenceError("qualification block case set drift")
        ordered: list[float] = []
        for case_id in case_order:
            ratio = ratios[case_id]
            if type(ratio) is not float or not math.isfinite(ratio) or ratio <= 0.0:
                raise EvidenceError(
                    "qualification ratio must be a finite JSON decimal"
                )
            ordered.append(ratio)
        _geometric_mean(ordered)
        paired.extend(ordered)
        for case_id, value in zip(case_order, ordered, strict=True):
            by_case[case_id].append(value)
        if _geometric_mean(ordered) < 1.0:
            improving += 1
    aggregate = _geometric_mean(paired)
    maxima = {case_id: max(values) for case_id, values in by_case.items()}
    optimized = (
        all(value <= 1.05 for value in maxima.values())
        and aggregate <= 0.97
        and improving >= 3
    )
    return ProfileSelection(
        profile_id=("optimized-o2-fasm" if optimized else "baseline-o0-fasm"),
        selected_by=("frozen-criterion-selector" if optimized else "proven-fallback"),
        paired_ratios=tuple(paired),
        per_case_maxima=maxima,
        aggregate_ratio=aggregate,
        improving_outer_repetitions=improving,
    )


def validate_cabal_projection(cabal_text: str) -> None:
    """Generated Cabal이 core/shell split과 current source modules를 투영하는지 검사한다."""

    required_tokens = (
        "library s1-4x-core",
        "src/core",
        "src/contract",
        "s1-4x-core",
        "math-functions ==0.3.4.4",
        "vector ==0.13.2.0",
        "S14X.Contract.BenchmarkValidation",
        "S14X.BenchmarkStaticSpec",
    )
    forbidden_tokens = (
        "hs-source-dirs: src\n",
        "statistics ==0.16.5.0",
        "foreign-library",
        "c-sources:",
        "cxx-sources:",
        "js-sources:",
        "extra-libraries:",
        "extra-lib-dirs:",
        "install-includes:",
        "frameworks:",
    )
    if any(token not in cabal_text for token in required_tokens) or any(
        token in cabal_text for token in forbidden_tokens
    ):
        raise EvidenceError("generated Cabal projection does not match package.yaml")
    core_match = re.search(
        r"(?ms)^library s1-4x-core\s*$\n(.*?)(?=^[A-Za-z][^\n]*\s*$|\Z)",
        cabal_text,
    )
    if core_match is None:
        raise EvidenceError("generated Cabal projection is missing the core component")
    shell_only = (
        "SHA",
        "aeson",
        "attoparsec",
        "binary",
        "bytestring",
        "directory",
        "filepath",
        "scientific",
        "text",
        "unix",
    )
    if any(package in core_match.group(1) for package in shell_only):
        raise EvidenceError("generated Cabal projection leaks shell dependencies into core")


def _source_inputs_command(arguments: argparse.Namespace) -> None:
    root = arguments.haskell_root.resolve(strict=True)
    manifest_path = arguments.manifest.resolve(strict=False)
    if arguments.write:
        atomic_write_json(manifest_path, build_source_manifest(root))
    else:
        validate_source_manifest(root, manifest_path)
    print(
        json.dumps(
            {
                "manifestPath": str(manifest_path),
                "manifestSha256": sha256_file(manifest_path),
                "status": "PASS",
            },
            sort_keys=True,
        )
    )


def _module_safety_command(arguments: argparse.Namespace) -> None:
    output = arguments.output.resolve(strict=False)
    result = build_module_safety_result(
        arguments.haskell_root,
        numeric_root=arguments.numeric_root,
        source_manifest_path=arguments.manifest,
        interface_root=arguments.interface_root,
        ghc_bin=arguments.ghc_bin,
        vector_archive=arguments.vector_archive,
    )
    if arguments.write:
        atomic_write_json(output, result)
    else:
        if strict_json_load(output) != result:
            raise EvidenceError("module-safety report drift")
        if output.read_bytes() != canonical_json_bytes(result, trailing_newline=True):
            raise EvidenceError("module-safety report is not canonical JSON")
    print(
        json.dumps(
            {
                "moduleCount": len(result["modules"]),
                "reportPath": str(output),
                "reportSha256": sha256_file(output),
                "status": "PASS",
            },
            sort_keys=True,
        )
    )


def _cabal_command(arguments: argparse.Namespace) -> None:
    cabal_path = arguments.cabal.resolve(strict=True)
    validate_cabal_projection(cabal_path.read_text(encoding="utf-8"))
    print(
        json.dumps(
            {
                "cabalPath": str(cabal_path),
                "cabalSha256": sha256_file(cabal_path),
                "status": "PASS",
            },
            sort_keys=True,
        )
    )


def _source_tree_command(arguments: argparse.Namespace) -> None:
    entries = benchmark_source_tree_entries(arguments.haskell_root.resolve(strict=True))
    print(
        json.dumps(
            {
                "entries": entries,
                "sourceTreeSha256": canonical_sha256(entries),
                "status": "PASS",
            },
            sort_keys=True,
        )
    )


def _selected_profile_command(arguments: argparse.Namespace) -> None:
    root = arguments.haskell_root.resolve(strict=True)
    profile_path = arguments.profile.resolve(strict=False)
    plan_path = arguments.qualification_plan.resolve(strict=True)
    if arguments.write_pending:
        atomic_write_json(
            profile_path,
            build_pending_selected_profile(root, qualification_plan=plan_path),
        )
    plan = strict_json_load(plan_path)
    if not isinstance(plan, dict) or not isinstance(
        plan.get("haskellProfileQualification"),
        dict,
    ):
        raise EvidenceError("Haskell profile selector configuration is missing")
    profile = strict_json_load(profile_path)
    validate_selected_profile_document(
        profile,
        expected_compiler_sha256=AUTHORITATIVE_GHC_SHA256,
        expected_source_tree_sha256=benchmark_source_tree_sha256(root),
        expected_qualification_plan_sha256=sha256_file(plan_path),
        expected_selector_config_sha256=canonical_sha256(
            plan["haskellProfileQualification"]
        ),
    )
    if profile_path.read_bytes() != canonical_json_bytes(profile, trailing_newline=True):
        raise EvidenceError("selected profile is not canonical JSON")
    print(
        json.dumps(
            {
                "ghcOptions": profile["ghcOptions"],
                "optionsSha256": profile["optionsSha256"],
                "profileId": profile["profileId"],
                "profilePath": str(profile_path),
                "profileSha256": sha256_file(profile_path),
                "schemaVersion": profile["schemaVersion"],
                "sourceTreeSha256": profile["sourceTreeSha256"],
                "status": "PASS",
            },
            sort_keys=True,
        )
    )


def _property_closure_command(arguments: argparse.Namespace) -> None:
    root = arguments.haskell_root.resolve(strict=True)
    print(
        json.dumps(
            {
                "propertyClosureSha256": property_execution_closure_sha256(root),
                "status": "PASS",
            },
            sort_keys=True,
        )
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    source_inputs = commands.add_parser("source-inputs")
    source_inputs.add_argument("--haskell-root", type=Path, required=True)
    source_inputs.add_argument("--manifest", type=Path, required=True)
    source_inputs.add_argument("--write", action="store_true")
    source_inputs.set_defaults(handler=_source_inputs_command)

    module_safety = commands.add_parser("module-safety")
    module_safety.add_argument("--haskell-root", type=Path, required=True)
    module_safety.add_argument("--numeric-root", type=Path, required=True)
    module_safety.add_argument("--manifest", type=Path, required=True)
    module_safety.add_argument("--interface-root", type=Path, required=True)
    module_safety.add_argument("--ghc-bin", type=Path, required=True)
    module_safety.add_argument("--vector-archive", type=Path, required=True)
    module_safety.add_argument("--output", type=Path, required=True)
    module_safety.add_argument("--write", action="store_true")
    module_safety.set_defaults(handler=_module_safety_command)

    cabal = commands.add_parser("cabal")
    cabal.add_argument("--cabal", type=Path, required=True)
    cabal.set_defaults(handler=_cabal_command)

    source_tree = commands.add_parser("source-tree")
    source_tree.add_argument("--haskell-root", type=Path, required=True)
    source_tree.set_defaults(handler=_source_tree_command)

    selected_profile = commands.add_parser("selected-profile")
    selected_profile.add_argument("--haskell-root", type=Path, required=True)
    selected_profile.add_argument("--profile", type=Path, required=True)
    selected_profile.add_argument("--qualification-plan", type=Path, required=True)
    selected_profile.add_argument("--write-pending", action="store_true")
    selected_profile.set_defaults(handler=_selected_profile_command)

    property_closure = commands.add_parser("property-closure")
    property_closure.add_argument("--haskell-root", type=Path, required=True)
    property_closure.set_defaults(handler=_property_closure_command)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        arguments.handler(arguments)
    except (EvidenceError, OSError, UnicodeError, ValueError) as exc:
        print(f"HASKELL_EVIDENCE_FAIL:{exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
