#!/bin/false
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
    pinned_builtin_diagnostic_count: int


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
        ("getExecutablePath", "lookupEnv"),
        "System.Environment(getExecutablePath,lookupEnv)",
    ),
    (
        "System.Environment",
        "S14X.PropertyEvidence",
        "test/S14X/PropertyEvidence.hs",
        ("getExecutablePath",),
        "System.Environment(getExecutablePath)",
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
    "System.Environment": (
        "Main",
        "S14X.BenchmarkMain",
        "S14X.PropertyEvidence",
        "S14X.TestMain",
    ),
    "System.IO": ("Main", "S14X.AtomicOutputSpec", "S14X.Contract.AtomicOutput"),
}
ENTRY_FIELD_ORDER = (
    "language",
    "file",
    "rule",
    "symbol",
    "reason",
    "focusedTest",
    "owner",
    "expiresWhen",
)
ENTRY_FIELDS = set(ENTRY_FIELD_ORDER)
SUPPRESSION_SCHEMA_VERSION = "s1.4x-suppression-exceptions-v1"
SUPPRESSION_FILE_PATTERN = (
    r"^(?!/)(?!.*(?:^|/)\.\.(?:/|$))[A-Za-z0-9._/-]+$"
)
ENTRY_MAX_LENGTHS = {
    "file": 512,
    "rule": 256,
    "symbol": 512,
    "reason": 1024,
    "focusedTest": 512,
    "expiresWhen": 512,
}
SUPPRESSION_UNIQUE_COMPOSITE = ("language", "file", "rule", "symbol")
FROZEN_SUPPRESSION_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": (
        "https://capstone-ai-trading-coach.invalid/"
        "s1-4x/schemas/suppression-exception.schema.json"
    ),
    "title": "S1.4X reviewed suppression and partial-API exceptions",
    "type": "object",
    "additionalProperties": False,
    "required": ["schemaVersion", "entries"],
    "properties": {
        "schemaVersion": {"const": SUPPRESSION_SCHEMA_VERSION},
        "entries": {
            "type": "array",
            "items": {"$ref": "#/$defs/entry"},
            "x-s1-4x-unique-by-composite": list(
                SUPPRESSION_UNIQUE_COMPOSITE
            ),
            "x-s1-4x-stale-or-unused-entry-is-error": True,
        },
    },
    "$defs": {
        "entry": {
            "type": "object",
            "additionalProperties": False,
            "required": list(ENTRY_FIELD_ORDER),
            "properties": {
                "language": {"enum": ["scala", "haskell"]},
                "file": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": ENTRY_MAX_LENGTHS["file"],
                    "pattern": SUPPRESSION_FILE_PATTERN,
                },
                "rule": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": ENTRY_MAX_LENGTHS["rule"],
                },
                "symbol": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": ENTRY_MAX_LENGTHS["symbol"],
                },
                "reason": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": ENTRY_MAX_LENGTHS["reason"],
                },
                "focusedTest": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": ENTRY_MAX_LENGTHS["focusedTest"],
                },
                "owner": {"const": "S1.4X"},
                "expiresWhen": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": ENTRY_MAX_LENGTHS["expiresWhen"],
                },
            },
        },
    },
}
SOURCE_ROOTS = ("src", "app", "test", "benchmark")
THROW_IO_NAMES = {"Control.Exception.throwIO", "throwIO"}
# HLint 3.10의 --show가 내보내는 비활성 내장 정보성 hint는 suppression inventory와 분리한다.
PINNED_BUILTIN_IGNORED_HINTS = {"Redundant bracket due to operator fixities"}


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
    """한 Haskell suppression entry의 schema와 live source/test 결속을 검증한다."""

    if set(entry) != ENTRY_FIELDS:
        raise InventoryError("lint exception entry field drift")
    if entry.get("language") != "haskell" or entry.get("owner") != "S1.4X":
        raise InventoryError("lint exception language or owner drift")
    if any(
        not isinstance(entry.get(field), str)
        or not 1 <= len(entry[field]) <= ENTRY_MAX_LENGTHS.get(field, 512)
        for field in ENTRY_FIELDS
    ):
        raise InventoryError("lint exception string type or length drift")
    relative = str(entry["file"])
    if re.fullmatch(SUPPRESSION_FILE_PATTERN, relative) is None:
        raise InventoryError("lint exception file escapes the Haskell root")
    source = root / relative
    if (
        source.is_symlink()
        or not source.is_file()
        or source.resolve(strict=True) != source
    ):
        raise InventoryError(f"lint exception source is missing: {relative}")
    focused_path_text, separator, focused_name = str(entry["focusedTest"]).partition(
        ": "
    )
    if (
        not separator
        or not focused_name
        or focused_name != focused_name.strip()
        or re.fullmatch(SUPPRESSION_FILE_PATTERN, focused_path_text) is None
    ):
        raise InventoryError("focused lint test reference must use `path: test name`")
    focused_path = root / focused_path_text
    if (
        focused_path.is_symlink()
        or not focused_path.is_file()
        or focused_path.resolve(strict=True) != focused_path
    ):
        raise InventoryError("focused lint test file is missing")
    if focused_name not in focused_path.read_text(encoding="utf-8"):
        raise InventoryError(f"focused lint test name is stale: {entry['focusedTest']}")


def validate_suppression_contract(
    root: Path,
    schema: object,
    manifest: object,
) -> tuple[Mapping[str, object], ...]:
    """Frozen schema와 Haskell manifest를 stdlib만으로 exact/live 검증한다."""

    root = root.resolve(strict=True)
    if schema != FROZEN_SUPPRESSION_SCHEMA:
        raise InventoryError("suppression schema drift")
    if (
        not isinstance(manifest, dict)
        or set(manifest) != {"schemaVersion", "entries"}
        or manifest.get("schemaVersion") != SUPPRESSION_SCHEMA_VERSION
        or not isinstance(manifest.get("entries"), list)
    ):
        raise InventoryError("suppression manifest outer shape drift")
    entries = manifest["entries"]
    composites: set[tuple[str, str, str, str]] = set()
    validated: list[Mapping[str, object]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise InventoryError("suppression manifest entry type drift")
        _validate_entry(root, entry)
        composite = tuple(
            str(entry[field]) for field in SUPPRESSION_UNIQUE_COMPOSITE
        )
        if composite in composites:
            raise InventoryError("duplicate lint exception composite")
        composites.add(composite)
        validated.append(entry)
    return tuple(validated)


def _yaml_scalar(token: str) -> str:
    """HLint inventory에 쓰는 단순 YAML scalar를 보수적으로 정규화한다."""

    value = token.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    if not value or any(character in value for character in "\r\n[]{}"):
        raise InventoryError(f"unsupported HLint YAML scalar: {token!r}")
    return value


def _flow_parts(value: str) -> list[str]:
    """따옴표와 중첩 flow collection을 보존하며 쉼표 단위로 나눈다."""

    parts: list[str] = []
    start = 0
    depth = 0
    quote: str | None = None
    escaped = False
    for index, character in enumerate(value):
        if quote is not None:
            if escaped:
                escaped = False
            elif character == "\\" and quote == '"':
                escaped = True
            elif character == quote:
                quote = None
            continue
        if character in {"'", '"'}:
            quote = character
        elif character in "[{":
            depth += 1
        elif character in "]}":
            depth -= 1
            if depth < 0:
                raise InventoryError("unbalanced HLint YAML flow collection")
        elif character == "," and depth == 0:
            parts.append(value[start:index].strip())
            start = index + 1
    if quote is not None or depth != 0:
        raise InventoryError("unterminated HLint YAML flow collection")
    parts.append(value[start:].strip())
    return [part for part in parts if part]


def _yaml_values(token: str) -> tuple[str, ...]:
    value = token.strip()
    if value.startswith("["):
        if not value.endswith("]"):
            raise InventoryError("unterminated HLint YAML flow sequence")
        inner = value[1:-1].strip()
        return tuple(_yaml_scalar(part) for part in _flow_parts(inner)) if inner else ()
    return (_yaml_scalar(value),)


def _flow_mapping(token: str) -> dict[str, str]:
    value = token.strip()
    if not (value.startswith("{") and value.endswith("}")):
        raise InventoryError("HLint YAML flow mapping must use braces")
    mapping: dict[str, str] = {}
    for part in _flow_parts(value[1:-1]):
        key, separator, item = part.partition(":")
        if not separator:
            raise InventoryError("HLint YAML flow mapping entry is missing a colon")
        normalized_key = _yaml_scalar(key)
        if normalized_key in mapping:
            raise InventoryError(f"duplicate HLint YAML key: {normalized_key}")
        mapping[normalized_key] = item.strip()
    return mapping


def _block_mapping(lines: Sequence[str]) -> dict[str, tuple[str, ...]]:
    """ignore block의 name/within을 block/flow sequence 양쪽에서 읽는다."""

    mapping: dict[str, tuple[str, ...]] = {}
    index = 0
    while index < len(lines):
        line = lines[index]
        matched = re.match(r"^\s+(name|within):\s*(.*?)\s*$", line)
        if matched is None:
            if line.strip() and not line.lstrip().startswith("#"):
                raise InventoryError(f"unsupported HLint ignore YAML line: {line}")
            index += 1
            continue
        key, inline = matched.groups()
        if key in mapping:
            raise InventoryError(f"duplicate HLint ignore key: {key}")
        index += 1
        if inline:
            mapping[key] = _yaml_values(inline)
            continue
        values: list[str] = []
        while index < len(lines):
            item = re.match(r"^\s+-\s+(.+?)\s*$", lines[index])
            if item is None:
                break
            values.append(_yaml_scalar(item.group(1)))
            index += 1
        mapping[key] = tuple(values)
    return mapping


def _ignore_pairs(configuration: str) -> set[tuple[str, str]]:
    """top-level ignore를 block/inline YAML 표기와 무관하게 exact pair로 읽는다."""

    lines = configuration.splitlines()
    pairs: set[tuple[str, str]] = set()
    index = 0
    while index < len(lines):
        matched = re.match(r"^- ignore:\s*(.*?)\s*$", lines[index])
        if matched is None:
            index += 1
            continue
        inline = matched.group(1)
        index += 1
        end = index
        while end < len(lines) and not re.match(r"^-\s+", lines[end]):
            end += 1
        if inline:
            flow = _flow_mapping(inline)
            if set(flow) != {"name", "within"}:
                raise InventoryError("HLint inline ignore field drift")
            names = _yaml_values(flow["name"])
            within = _yaml_values(flow["within"])
        else:
            block = _block_mapping(lines[index:end])
            if set(block) != {"name", "within"}:
                raise InventoryError("HLint block ignore field drift")
            names = block["name"]
            within = block["within"]
        if len(names) != 1 or not within:
            raise InventoryError("HLint ignore must name one rule and at least one module")
        for module in within:
            pair = (names[0], module)
            if pair in pairs:
                raise InventoryError(f"duplicate HLint ignore pair: {pair}")
            pairs.add(pair)
        index = end
    return pairs


def _function_restrictions(configuration: str) -> list[tuple[tuple[str, ...], tuple[str, ...]]]:
    """functions section의 name/within block을 읽어 전역 금지와 whitelist를 구분한다."""

    lines = configuration.splitlines()
    try:
        start = lines.index("- functions:") + 1
    except ValueError as exc:
        raise InventoryError("HLint function restriction section drift") from exc
    end = next(
        (
            index
            for index in range(start, len(lines))
            if re.match(r"^-\s+", lines[index])
        ),
        len(lines),
    )
    restrictions: list[tuple[tuple[str, ...], tuple[str, ...]]] = []
    index = start
    while index < end:
        flow_match = re.match(r"^\s{4}-\s*(\{.*\})\s*$", lines[index])
        name_match = re.match(r"^\s{4}- name:\s*(.*?)\s*$", lines[index])
        if flow_match is not None:
            flow = _flow_mapping(flow_match.group(1))
            if set(flow) != {"name", "within"}:
                raise InventoryError("HLint function flow restriction field drift")
            restrictions.append(
                (_yaml_values(flow["name"]), _yaml_values(flow["within"]))
            )
            index += 1
            continue
        if name_match is None:
            if lines[index].strip() and not lines[index].lstrip().startswith("#"):
                raise InventoryError(
                    f"unsupported HLint function restriction line: {lines[index]}"
                )
            index += 1
            continue
        inline_names = name_match.group(1)
        index += 1
        names: tuple[str, ...]
        if inline_names:
            names = _yaml_values(inline_names)
        else:
            collected_names: list[str] = []
            while index < end:
                item = re.match(r"^\s{8}-\s+(.+?)\s*$", lines[index])
                if item is None:
                    break
                collected_names.append(_yaml_scalar(item.group(1)))
                index += 1
            names = tuple(collected_names)
        if index >= end:
            raise InventoryError("HLint function restriction is missing within")
        within_match = re.match(r"^\s{6}within:\s*(.*?)\s*$", lines[index])
        if within_match is None:
            raise InventoryError("HLint function restriction within block drift")
        inline_within = within_match.group(1)
        index += 1
        if inline_within:
            within = _yaml_values(inline_within)
        else:
            collected_within: list[str] = []
            while index < end:
                item = re.match(r"^\s{8}-\s+(.+?)\s*$", lines[index])
                if item is None:
                    break
                collected_within.append(_yaml_scalar(item.group(1)))
                index += 1
            within = tuple(collected_within)
        if not names:
            raise InventoryError("HLint function restriction name list is empty")
        restrictions.append((names, within))
    return restrictions


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


def validate_throw_io_restrictions(configuration: str) -> None:
    """qualified/unqualified throwIO가 모두 global ban이며 whitelist가 아님을 검증한다."""

    occurrences: dict[str, list[tuple[str, ...]]] = {
        name: [] for name in THROW_IO_NAMES
    }
    for names, within in _function_restrictions(configuration):
        for name in THROW_IO_NAMES.intersection(names):
            occurrences[name].append(within)
    if set(occurrences) != THROW_IO_NAMES or any(
        values != [()] for values in occurrences.values()
    ):
        raise InventoryError(
            f"throwIO restriction must be one global ban per name: {occurrences}"
        )


def validate_no_source_local_suppressions(root: Path) -> None:
    """candidate 네 source root 안의 HLint comment/ANN 우회를 모두 거부한다."""

    root = root.resolve(strict=True)
    for relative_root in SOURCE_ROOTS:
        directory = root / relative_root
        if not directory.exists():
            continue
        if directory.is_symlink() or not directory.is_dir():
            raise InventoryError(f"HLint source root is unsafe: {relative_root}")
        for source in sorted(directory.rglob("*.hs"), key=lambda item: item.as_posix()):
            if source.is_symlink() or not source.is_file():
                raise InventoryError(
                    f"HLint source input is unsafe: {source.relative_to(root)}"
                )
            text = source.read_text(encoding="utf-8")
            has_directive = re.search(r"(?i)\bHLint\s*:", text) is not None
            has_ann_suppression = (
                re.search(r"(?is)\{\-\#\s*ANN\b.*?\bHLint\b.*?#-\}", text)
                is not None
            )
            if has_directive or has_ann_suppression:
                raise InventoryError(
                    "source-local HLint suppression is forbidden: "
                    f"{source.relative_to(root).as_posix()}"
                )


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
    pinned_builtin: list[Mapping[str, object]] = []
    for diagnostic in diagnostics:
        modules = diagnostic.get("module")
        if diagnostic.get("severity") != "Ignore":
            continue
        identity = _diagnostic_identity(root, diagnostic)
        if (
            isinstance(modules, list)
            and len(modules) == 1
            and (diagnostic.get("hint"), modules[0]) in configured_pairs
        ):
            managed.append(diagnostic)
        elif diagnostic.get("hint") in PINNED_BUILTIN_IGNORED_HINTS:
            pinned_builtin.append(diagnostic)
        else:
            raise InventoryError(
                "unknown ignored HLint diagnostic: "
                f"{identity[0]}:{identity[1]}:{identity[2]}"
            )
    managed_identities = [_diagnostic_identity(root, diagnostic) for diagnostic in managed]
    if len(managed_identities) != len(set(managed_identities)):
        raise InventoryError("duplicate managed ignored diagnostic identity")

    bound_identities: set[tuple[str, str, str, tuple[str, ...], str]] = set()
    manifest_pairs: set[tuple[str, str]] = set()
    for entry in entries:
        if entry["rule"] == "Avoid restricted module":
            continue
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
        pinned_builtin_diagnostic_count=len(pinned_builtin),
    )


def _import_symbols(source: str, module: str) -> tuple[str, ...]:
    matched = re.search(
        rf"(?ms)^import\s+{re.escape(module)}\s*\((.*?)\)\s*$",
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
    schema = strict_json_load(arguments.schema)
    manifest = strict_json_load(arguments.manifest)
    diagnostics = strict_json_load(arguments.diagnostics)
    entries = validate_suppression_contract(root, schema, manifest)
    if (
        not isinstance(diagnostics, list)
        or any(not isinstance(item, dict) for item in diagnostics)
    ):
        raise InventoryError("HLint diagnostic inventory shape drift")
    validate_throw_io_restrictions(configuration)
    validate_no_source_local_suppressions(root)
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
                "pinnedBuiltinIgnoredDiagnosticCount": (
                    managed.pinned_builtin_diagnostic_count
                ),
                "restrictedModuleAllowanceCount": allowances.allowance_count,
                "restrictedModuleImportedSymbolCount": allowances.imported_symbol_count,
                "suppressionEntryCount": len(entries),
                "suppressionSchemaStatus": "PASS",
                "status": "PASS",
            },
            sort_keys=True,
        )
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--haskell-root", type=Path, required=True)
    parser.add_argument("--configuration", type=Path, required=True)
    parser.add_argument("--schema", type=Path, required=True)
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
