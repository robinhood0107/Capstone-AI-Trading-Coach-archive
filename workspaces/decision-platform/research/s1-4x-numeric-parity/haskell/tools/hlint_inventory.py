#!/usr/bin/env python3
"""HLint managed ignores와 restricted-module import allowances를 exact 검증한다."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence


class InventoryError(RuntimeError):
    """HLint ignore나 module allowance inventory가 실제 source와 달라졌을 때 발생한다."""


@dataclass(frozen=True)
class ManagedIgnoreSummary:
    """중앙 ignore로 숨겨진 진단과 typed exception의 exact 결속 요약."""

    managed_diagnostic_count: int
    configured_pair_count: int


@dataclass(frozen=True)
class ModuleAllowanceSummary:
    """허용된 restricted-module import statement와 symbol 수 요약."""

    allowance_count: int
    imported_symbol_count: int


MODULE_ALLOWANCES = (
    (
        "System.Environment",
        "Main",
        "app/Main.hs",
        ("getArgs",),
        "System.Environment(getArgs)",
    ),
    (
        "System.IO",
        "Main",
        "app/Main.hs",
        ("stderr",),
        "System.IO(stderr)",
    ),
    (
        "System.Environment",
        "S14X.BenchmarkMain",
        "benchmark/Main.hs",
        ("lookupEnv",),
        "System.Environment(lookupEnv)",
    ),
    (
        "System.Environment",
        "S14X.TestMain",
        "test/Main.hs",
        ("getArgs",),
        "System.Environment(getArgs)",
    ),
    (
        "System.IO",
        "S14X.AtomicOutputSpec",
        "test/S14X/AtomicOutputSpec.hs",
        ("hClose", "openBinaryTempFile"),
        "System.IO(hClose,openBinaryTempFile)",
    ),
    (
        "System.IO",
        "S14X.Contract.AtomicOutput",
        "src/contract/S14X/Contract/AtomicOutput.hs",
        ("Handle", "hClose", "hFlush", "hSetBinaryMode", "openBinaryTempFile"),
        "System.IO(Handle,hClose,hFlush,hSetBinaryMode,openBinaryTempFile)",
    ),
)
EXPECTED_NONEMPTY_MODULE_WITHIN = {
    "System.Environment": ("Main", "S14X.BenchmarkMain", "S14X.TestMain"),
    "System.IO": ("Main", "S14X.AtomicOutputSpec", "S14X.Contract.AtomicOutput"),
}
ENTRY_FIELDS = {
    "language",
    "file",
    "rule",
    "symbol",
    "reason",
    "focusedTest",
    "owner",
    "expiresWhen",
}


def strict_json_load(path: Path) -> Any:
    """중복 key와 non-finite constant를 거부해 HLint evidence JSON을 읽는다."""

    def object_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
        value: dict[str, object] = {}
        for key, item in pairs:
            if key in value:
                raise InventoryError(f"duplicate JSON key: {path}:{key}")
            value[key] = item
        return value

    try:
        return json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=object_pairs,
            parse_constant=lambda token: (_ for _ in ()).throw(
                InventoryError(f"non-finite JSON constant: {path}:{token}")
            ),
        )
    except json.JSONDecodeError as exc:
        raise InventoryError(f"invalid JSON: {path}:{exc}") from exc


def _validate_entry(root: Path, entry: Mapping[str, object]) -> None:
    if set(entry) != ENTRY_FIELDS:
        raise InventoryError("lint exception entry field drift")
    if entry.get("language") != "haskell" or entry.get("owner") != "S1.4X":
        raise InventoryError("lint exception language or owner drift")
    if any(
        not isinstance(entry.get(field), str) or not entry[field]
        for field in ENTRY_FIELDS
    ):
        raise InventoryError("lint exception contains an empty field")
    relative = str(entry["file"])
    if relative.startswith("/") or ".." in Path(relative).parts:
        raise InventoryError("lint exception file escapes the Haskell root")
    source = root / relative
    if source.is_symlink() or not source.is_file():
        raise InventoryError(f"lint exception source is missing: {relative}")
    focused_path_text, separator, focused_name = str(entry["focusedTest"]).partition(
        ": "
    )
    if not separator:
        raise InventoryError("focused lint test reference must use `path: test name`")
    focused_path = root / focused_path_text
    if focused_path.is_symlink() or not focused_path.is_file():
        raise InventoryError("focused lint test file is missing")
    if focused_name not in focused_path.read_text(encoding="utf-8"):
        raise InventoryError(f"focused lint test name is stale: {entry['focusedTest']}")


def _ignore_pairs(configuration: str) -> set[tuple[str, str]]:
    blocks = re.findall(
        r"(?ms)^- ignore:\n"
        r"\s+name:\s*([^\n]+)\n"
        r"\s+within:\n"
        r"((?:\s+-\s+[^\n]+\n)+)",
        configuration,
    )
    return {
        (name.strip(), module.strip())
        for name, within_block in blocks
        for module in re.findall(
            r"^\s+-\s+([^\n]+)$",
            within_block,
            flags=re.MULTILINE,
        )
    }


def _module_within(configuration: str) -> dict[str, tuple[str, ...]]:
    lines = configuration.splitlines()
    try:
        start = lines.index("- modules:") + 1
        end = lines.index("- functions:")
    except ValueError as exc:
        raise InventoryError("HLint module/function restriction section drift") from exc
    mapping: dict[str, tuple[str, ...]] = {}
    index = start
    while index < end:
        if lines[index] != "    - name:":
            index += 1
            continue
        index += 1
        names: list[str] = []
        while index < end and lines[index].startswith("        - "):
            names.append(lines[index].removeprefix("        - "))
            index += 1
        if index >= end or not lines[index].startswith("      within:"):
            raise InventoryError("HLint module within block drift")
        inline = lines[index].removeprefix("      within:")
        index += 1
        within: list[str] = []
        if inline.strip() == "[]":
            pass
        elif inline.strip():
            raise InventoryError("HLint module inline within value drift")
        else:
            while index < end and lines[index].startswith("        - "):
                within.append(lines[index].removeprefix("        - "))
                index += 1
        for name in names:
            if name in mapping:
                raise InventoryError(f"duplicate HLint restricted module block: {name}")
            mapping[name] = tuple(within)
    return mapping


def validate_no_throw_io_allowance(configuration: str) -> None:
    """현재 source에 없는 throwIO capability가 HLint allowlist에 없음을 검증한다."""

    forbidden = re.compile(
        r"(?m)^\s+- (?:Control\.Exception\.)?throwIO\s*$"
    )
    if forbidden.search(configuration):
        raise InventoryError("unused throwIO allowance is forbidden")


def _source_relative(root: Path, diagnostic_file: object) -> str:
    if not isinstance(diagnostic_file, str) or not diagnostic_file:
        raise InventoryError("HLint diagnostic file identity is missing")
    candidate = Path(diagnostic_file)
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        return candidate.resolve(strict=True).relative_to(root.resolve(strict=True)).as_posix()
    except (OSError, ValueError) as exc:
        raise InventoryError("HLint diagnostic path escapes the Haskell root") from exc


def _diagnostic_identity(
    root: Path,
    diagnostic: Mapping[str, object],
) -> tuple[str, str, str, tuple[str, ...], str]:
    modules = diagnostic.get("module")
    declarations = diagnostic.get("decl")
    if (
        not isinstance(modules, list)
        or len(modules) != 1
        or not isinstance(modules[0], str)
        or not isinstance(declarations, list)
        or not declarations
        or any(not isinstance(value, str) or not value for value in declarations)
        or not isinstance(diagnostic.get("hint"), str)
        or not isinstance(diagnostic.get("from"), str)
    ):
        raise InventoryError("managed HLint diagnostic identity is ambiguous")
    return (
        _source_relative(root, diagnostic.get("file")),
        str(diagnostic["hint"]),
        modules[0],
        tuple(declarations),
        str(diagnostic["from"]),
    )


def validate_managed_ignored_diagnostics(
    root: Path,
    configuration: str,
    entries: Sequence[Mapping[str, object]],
    diagnostics: Sequence[Mapping[str, object]],
) -> ManagedIgnoreSummary:
    """중앙 module-level ignore로 숨겨진 진단과 typed manifest를 set-equal로 결속한다."""

    root = root.resolve(strict=True)
    configured_pairs = _ignore_pairs(configuration)
    managed: list[Mapping[str, object]] = []
    for diagnostic in diagnostics:
        modules = diagnostic.get("module")
        if (
            diagnostic.get("severity") == "Ignore"
            and isinstance(modules, list)
            and len(modules) == 1
            and (diagnostic.get("hint"), modules[0]) in configured_pairs
        ):
            managed.append(diagnostic)
    managed_identities = [_diagnostic_identity(root, diagnostic) for diagnostic in managed]
    if len(managed_identities) != len(set(managed_identities)):
        raise InventoryError("duplicate managed ignored diagnostic identity")

    bound_identities: set[tuple[str, str, str, tuple[str, ...], str]] = set()
    manifest_pairs: set[tuple[str, str]] = set()
    composites: set[tuple[str, str, str]] = set()
    for entry in entries:
        _validate_entry(root, entry)
        if entry["rule"] == "Avoid restricted module":
            continue
        composite = (str(entry["file"]), str(entry["rule"]), str(entry["symbol"]))
        if composite in composites:
            raise InventoryError("duplicate lint exception composite")
        composites.add(composite)
        declaration, separator, from_token = str(entry["symbol"]).partition(":")
        matches: list[tuple[str, str, str, tuple[str, ...], str]] = []
        for identity in managed_identities:
            relative, hint, _, declarations, source_from = identity
            if (
                relative == entry["file"]
                and hint == entry["rule"]
                and declaration in declarations
                and (not separator or source_from == from_token)
            ):
                matches.append(identity)
        if len(matches) != 1:
            raise InventoryError(
                "lint exception must bind exactly one live managed ignored diagnostic: "
                f"{entry['file']}:{entry['rule']}:{entry['symbol']}"
            )
        identity = matches[0]
        if identity in bound_identities:
            raise InventoryError("managed ignored diagnostic has duplicate manifest bindings")
        bound_identities.add(identity)
        manifest_pairs.add((identity[1], identity[2]))

    if manifest_pairs != configured_pairs:
        raise InventoryError(
            "central HLint ignore/typed exception pair mismatch: "
            f"configured={sorted(configured_pairs)}, manifest={sorted(manifest_pairs)}"
        )
    extra = set(managed_identities) - bound_identities
    missing = bound_identities - set(managed_identities)
    if extra or missing:
        raise InventoryError(
            "unbound managed ignored diagnostics: "
            f"extra={sorted(extra)}, missing={sorted(missing)}"
        )
    return ManagedIgnoreSummary(
        managed_diagnostic_count=len(managed_identities),
        configured_pair_count=len(configured_pairs),
    )


def _import_symbols(source: str, module: str) -> tuple[str, ...]:
    matched = re.search(
        rf"(?ms)^import {re.escape(module)}\s*\((.*?)\)\s*$",
        source,
    )
    if matched is None:
        raise InventoryError(f"reviewed import is missing: {module}")
    symbols = tuple(
        token
        for token in (
            re.sub(r"\s+", "", part)
            for part in matched.group(1).split(",")
        )
        if token
    )
    if not symbols:
        raise InventoryError(f"reviewed import symbol list is empty: {module}")
    return symbols


def validate_module_allowances(
    root: Path,
    configuration: str,
    entries: Sequence[Mapping[str, object]],
) -> ModuleAllowanceSummary:
    """restricted module의 nonempty within과 실제 explicit import를 typed set으로 검증한다."""

    root = root.resolve(strict=True)
    within = _module_within(configuration)
    nonempty = {
        module: modules
        for module, modules in within.items()
        if modules
    }
    if nonempty != EXPECTED_NONEMPTY_MODULE_WITHIN:
        raise InventoryError(
            "HLint restricted-module nonempty allowlist drift: "
            f"actual={nonempty}"
        )

    expected_entries = {
        (module, source_module, relative, symbol)
        for module, source_module, relative, _, symbol in MODULE_ALLOWANCES
    }
    actual_entries: set[tuple[str, str, str, str]] = set()
    imported_symbol_count = 0
    for module, source_module, relative, expected_symbols, symbol in MODULE_ALLOWANCES:
        source_path = root / relative
        if source_path.is_symlink() or not source_path.is_file():
            raise InventoryError(f"reviewed module allowance source missing: {relative}")
        source = source_path.read_text(encoding="utf-8")
        if not re.search(
            rf"(?m)^module {re.escape(source_module)}(?:\s|\()",
            source,
        ):
            raise InventoryError(f"reviewed module declaration drift: {relative}")
        observed_symbols = _import_symbols(source, module)
        if observed_symbols != expected_symbols:
            raise InventoryError(
                f"reviewed import symbol drift: {relative}:{module}:"
                f"{observed_symbols}"
            )
        imported_symbol_count += len(observed_symbols)
        if source_module not in within.get(module, ()):
            raise InventoryError(
                f"reviewed import missing HLint within allowance: {module}:{source_module}"
            )

    for entry in entries:
        _validate_entry(root, entry)
        if entry["rule"] != "Avoid restricted module":
            continue
        relative = str(entry["file"])
        matches = [
            (module, source_module, expected_relative, symbol)
            for module, source_module, expected_relative, _, symbol in MODULE_ALLOWANCES
            if expected_relative == relative and symbol == entry["symbol"]
        ]
        if len(matches) != 1:
            raise InventoryError(
                "restricted-module typed exception does not bind one reviewed import: "
                f"{relative}:{entry['symbol']}"
            )
        actual_entries.add(matches[0])
    if actual_entries != expected_entries:
        raise InventoryError(
            "restricted-module typed inventory mismatch: "
            f"missing={sorted(expected_entries - actual_entries)}, "
            f"extra={sorted(actual_entries - expected_entries)}"
        )
    return ModuleAllowanceSummary(
        allowance_count=len(MODULE_ALLOWANCES),
        imported_symbol_count=imported_symbol_count,
    )


def _validate_command(arguments: argparse.Namespace) -> None:
    root = arguments.haskell_root.resolve(strict=True)
    configuration = arguments.configuration.read_text(encoding="utf-8")
    manifest = strict_json_load(arguments.manifest)
    diagnostics = strict_json_load(arguments.diagnostics)
    if (
        not isinstance(manifest, dict)
        or manifest.get("schemaVersion") != "s1.4x-suppression-exceptions-v1"
        or set(manifest) != {"schemaVersion", "entries"}
        or not isinstance(manifest["entries"], list)
        or not isinstance(diagnostics, list)
        or any(not isinstance(item, dict) for item in diagnostics)
    ):
        raise InventoryError("HLint inventory input shape drift")
    entries = manifest["entries"]
    validate_no_throw_io_allowance(configuration)
    managed = validate_managed_ignored_diagnostics(
        root,
        configuration,
        entries,
        diagnostics,
    )
    allowances = validate_module_allowances(root, configuration, entries)
    print(
        json.dumps(
            {
                "configuredIgnorePairCount": managed.configured_pair_count,
                "managedIgnoredDiagnosticCount": managed.managed_diagnostic_count,
                "restrictedModuleAllowanceCount": allowances.allowance_count,
                "restrictedModuleImportedSymbolCount": allowances.imported_symbol_count,
                "status": "PASS",
            },
            sort_keys=True,
        )
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--haskell-root", type=Path, required=True)
    parser.add_argument("--configuration", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--diagnostics", type=Path, required=True)
    parser.set_defaults(handler=_validate_command)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        arguments.handler(arguments)
    except (InventoryError, OSError, UnicodeError, ValueError) as exc:
        print(f"HLINT_INVENTORY_FAIL:{exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
