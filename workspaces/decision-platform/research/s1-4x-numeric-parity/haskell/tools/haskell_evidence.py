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
import tempfile
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
    """Selected profile을 제외한 authoritative compile/benchmark subject closure."""

    paths = _candidate_source_paths(root)
    for relative in (
        "package.yaml",
        "s1-4x-haskell.cabal",
        "stack.yaml",
        "stack.yaml.lock",
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


def _module_category(relative: str, module_name: str) -> str:
    if relative.startswith("src/core/"):
        return "safe-scalar" if module_name in SAFE_SCALAR_MODULES else "audited-pure-vector"
    if relative.startswith(("src/contract/", "app/")):
        return "io-shell"
    if relative.startswith("test/"):
        return "test"
    if relative.startswith("benchmark/"):
        return "benchmark"
    raise EvidenceError(f"module path has no category: {relative}")


def _has_prefix(module_name: str, prefixes: Iterable[str]) -> bool:
    return any(
        module_name == prefix or module_name.startswith(prefix + ".") for prefix in prefixes
    )


def _audit_core_source(
    *,
    relative: str,
    parsed: ParsedModule,
    payload: bytes,
    category: str,
) -> None:
    text = payload.decode("utf-8")
    code = _strip_comments_and_literals(text)
    if re.search(r"\b(?:foreign\s+import|foreign\s+export)\b", code):
        raise EvidenceError(f"candidate native interop form in {relative}")
    if category not in CORE_CATEGORIES:
        return
    if re.search(r"\bIO\b", code):
        raise EvidenceError(f"candidate core IO type/use in {relative}")
    if re.search(r"\b(?:throw|throwIO|unsafeCoerce|unsafePerformIO)\b", code):
        raise EvidenceError(f"candidate core exception/unsafe use in {relative}")
    deriving_occurrences = re.findall(r"(?m)^\s*deriving\b([^\n]*)", code)
    if any(not occurrence.lstrip().startswith("stock") for occurrence in deriving_occurrences):
        raise EvidenceError(f"candidate core deriving must be stock in {relative}")
    if category == "safe-scalar" and "Safe" not in parsed.extensions:
        raise EvidenceError(f"safe-scalar module omits Safe: {relative}")
    if category == "audited-pure-vector" and "Safe" in parsed.extensions:
        raise EvidenceError(f"audited-pure-vector must not claim Safe: {relative}")


def build_module_safety_result(
    root: Path,
    *,
    numeric_root: Path,
    source_manifest_path: Path,
) -> dict[str, Any]:
    """Candidate module/import graph와 frozen vector provenance를 typed report로 만든다."""

    root = root.resolve(strict=True)
    numeric_root = numeric_root.resolve(strict=True)
    manifest = validate_source_manifest(root, source_manifest_path)
    policy_path = numeric_root / "contract/haskell-module-safety-policy.v1.json"
    policy = strict_json_load(policy_path)
    if not isinstance(policy, dict):
        raise EvidenceError("module-safety policy must be an object")
    mandatory = tuple(policy.get("mandatoryCoreExtensions", []))
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
        category = _module_category(relative, parsed.module_name)
        _audit_core_source(
            relative=relative,
            parsed=parsed,
            payload=payload,
            category=category,
        )
        effective_extensions = tuple(dict.fromkeys((*default_extensions, *parsed.extensions)))
        if any(extension in {"Trustworthy", "Unsafe"} for extension in effective_extensions):
            raise EvidenceError(f"forbidden Safe Haskell declaration in {relative}")
        compile_mode = (
            "Safe"
            if category == "safe-scalar"
            else (
                "SafeHaskell-None-with-audited-purity-gate"
                if category == "audited-pure-vector"
                else "ordinary"
            )
        )
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

    direct_imports: list[dict[str, str]] = []
    home_edges: list[dict[str, str]] = []
    for module_name, (_, category, parsed, _) in inventory.items():
        for imported in parsed.imports:
            target = inventory.get(imported)
            if target is not None:
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

    vector_provenance = policy.get("vectorProvenance")
    if not isinstance(vector_provenance, dict):
        raise EvidenceError("vector provenance policy is missing")
    policy_edges = vector_provenance.get("upstreamTransitiveAllowlist")
    if not isinstance(policy_edges, list) or len(policy_edges) != 3:
        raise EvidenceError("vector transitive allowlist must contain exactly three edges")
    upstream_edges: list[dict[str, Any]] = []
    for raw_edge in policy_edges:
        if not isinstance(raw_edge, dict):
            raise EvidenceError("vector transitive edge must be an object")
        edge = dict(raw_edge)
        edge["allowlisted"] = True
        upstream_edges.append(edge)

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
    if len(case_order) != 7 or len(case_order) != len(set(case_order)):
        raise EvidenceError("qualification case order must contain seven unique cases")
    if len(blocks) != 4:
        raise EvidenceError("qualification must contain exactly four order blocks")
    paired: list[float] = []
    by_case: dict[str, list[float]] = {case_id: [] for case_id in case_order}
    improving = 0
    for expected_index, block in enumerate(blocks):
        if block.get("orderBlock") != expected_index:
            raise EvidenceError("qualification order block sequence drift")
        ratios = block.get("ratios")
        if not isinstance(ratios, Mapping) or set(ratios) != set(case_order):
            raise EvidenceError("qualification block case set drift")
        ordered = [float(ratios[case_id]) for case_id in case_order]
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
    module_safety.add_argument("--output", type=Path, required=True)
    module_safety.add_argument("--write", action="store_true")
    module_safety.set_defaults(handler=_module_safety_command)

    cabal = commands.add_parser("cabal")
    cabal.add_argument("--cabal", type=Path, required=True)
    cabal.set_defaults(handler=_cabal_command)

    source_tree = commands.add_parser("source-tree")
    source_tree.add_argument("--haskell-root", type=Path, required=True)
    source_tree.set_defaults(handler=_source_tree_command)
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
