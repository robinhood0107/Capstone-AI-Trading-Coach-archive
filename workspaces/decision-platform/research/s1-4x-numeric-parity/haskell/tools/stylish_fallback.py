#!/usr/bin/env python3
"""stylish-haskell 0.15.1.0의 GHC2024 parser fallback을 fail-closed 검증한다."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence


class FallbackError(RuntimeError):
    """Formatter fallback의 동결 입력이나 관측 leaf가 달라졌을 때 발생한다."""


@dataclass(frozen=True)
class ValidatedFallback:
    """검증된 공식 edition 목록과 formatter-only 유효 목록의 identity."""

    official_extension_count: int
    effective_extension_count: int
    official_extensions_sha256: str
    effective_extensions_sha256: str
    mandated_configuration_sha256: str
    derived_configuration_sha256: str


OFFICIAL_SOURCE_URI = (
    "https://downloads.haskell.org/~ghc/9.10.3/docs/users_guide/exts/control.html"
)
OFFICIAL_SOURCE_CONTENT_SHA256 = (
    "1abd26d27eb68a9aeca6aeae99b5c232e7d9cfe4339e0409d3f6465c035c8d13"
)
OFFICIAL_RST_SOURCE_SHA256 = (
    "3070250ae590b5ff3498a4a4c82e3db72a5a6f46bca5157174113d62be275292"
)
OFFICIAL_EXTENSIONS = (
    "BangPatterns",
    "BinaryLiterals",
    "ConstrainedClassMethods",
    "ConstraintKinds",
    "DataKinds",
    "DeriveDataTypeable",
    "DeriveFoldable",
    "DeriveFunctor",
    "DeriveGeneric",
    "DeriveLift",
    "DeriveTraversable",
    "DerivingStrategies",
    "DisambiguateRecordFields",
    "DoAndIfThenElse",
    "EmptyCase",
    "EmptyDataDecls",
    "EmptyDataDeriving",
    "ExistentialQuantification",
    "ExplicitForAll",
    "ExplicitNamespaces",
    "FieldSelectors",
    "FlexibleContexts",
    "FlexibleInstances",
    "ForeignFunctionInterface",
    "GADTs",
    "GADTSyntax",
    "GeneralisedNewtypeDeriving",
    "HexFloatLiterals",
    "ImplicitPrelude",
    "ImportQualifiedPost",
    "InstanceSigs",
    "KindSignatures",
    "LambdaCase",
    "MonoLocalBinds",
    "MonomorphismRestriction",
    "MultiParamTypeClasses",
    "NamedFieldPuns",
    "NamedWildCards",
    "NumericUnderscores",
    "PatternGuards",
    "PolyKinds",
    "PostfixOperators",
    "RankNTypes",
    "RelaxedPolyRec",
    "RoleAnnotations",
    "ScopedTypeVariables",
    "StandaloneDeriving",
    "StandaloneKindSignatures",
    "StarIsType",
    "TraditionalRecordSyntax",
    "TupleSections",
    "TypeApplications",
    "TypeOperators",
    "TypeSynonymInstances",
)
PROJECT_EXPLICIT_NO_EXTENSIONS = (
    "NoCPP",
    "NoDeriveAnyClass",
    "NoDerivingVia",
    "NoForeignFunctionInterface",
    "NoGeneralizedNewtypeDeriving",
    "NoLinearTypes",
    "NoMagicHash",
    "NoRebindableSyntax",
    "NoStrict",
    "NoTemplateHaskell",
)
PROJECT_NON_EDITION_ENABLED_EXTENSIONS = ("OverloadedStrings",)
GHC2024_DISABLED_EXTENSIONS = (
    "ForeignFunctionInterface",
    "GeneralisedNewtypeDeriving",
)
EFFECTIVE_EXTENSIONS = tuple(
    extension
    for extension in OFFICIAL_EXTENSIONS
    if extension not in GHC2024_DISABLED_EXTENSIONS
)
MANDATED_CONFIGURATION_SHA256 = (
    "da1cd6b98f504191921a1197b6822cace841bdbc83fe7882b193e6161fcfd184"
)
DERIVED_CONFIGURATION_SHA256 = (
    "55a8fdffbb83679ca4ecff7232efa6324df27a4163d58d8762465d1757c8b9f0"
)
FORMATTER_SHA256 = "385dc27bc2d0fb654e76ecadfb57bc0b7e1c58afe74f19923e20b696e6fe0d7b"
FIXTURE_SHA256 = "63d987d33841e43feb385aae66c19e06d27615ee7cd59ef5ee3341854d567037"
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
MANDATED_STDERR = b'Unknown extension: "GHC2024"\n'
MANDATED_STDERR_SHA256 = hashlib.sha256(MANDATED_STDERR).hexdigest()
DERIVED_STDOUT_SHA256 = (
    "6aeb47fa182fcae71756433963017d80d7b649e00e879f5e8af2c6ca53f8b5ba"
)


def canonical_json_bytes(value: Any) -> bytes:
    """Fallback identity용 compact canonical JSON bytes를 반환한다."""

    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    """Fallback identity를 lowercase SHA-256으로 고정한다."""

    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def bytes_sha256(payload: bytes) -> str:
    """관측한 stdout, stderr, 설정 bytes의 lowercase SHA-256을 반환한다."""

    return hashlib.sha256(payload).hexdigest()


def file_sha256(path: Path) -> str:
    """Symlink가 아닌 regular file만 hash 입력으로 허용한다."""

    if path.is_symlink() or not path.is_file():
        raise FallbackError(f"not a regular fallback input: {path}")
    return bytes_sha256(path.read_bytes())


def strict_json_load(path: Path) -> dict[str, object]:
    """중복 key와 non-finite 수를 거부하고 fallback JSON object를 읽는다."""

    def reject_duplicate(pairs: list[tuple[str, object]]) -> dict[str, object]:
        value: dict[str, object] = {}
        for key, item in pairs:
            if key in value:
                raise FallbackError(f"duplicate JSON key: {key}")
            value[key] = item
        return value

    try:
        loaded = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=reject_duplicate,
            parse_constant=lambda token: (_ for _ in ()).throw(
                FallbackError(f"non-finite JSON constant: {token}")
            ),
        )
    except json.JSONDecodeError as exc:
        raise FallbackError(f"invalid fallback JSON: {exc}") from exc
    if not isinstance(loaded, dict):
        raise FallbackError("fallback contract must be a JSON object")
    return loaded


def _object(value: object, label: str, expected_keys: set[str]) -> Mapping[str, object]:
    if not isinstance(value, dict) or set(value) != expected_keys:
        raise FallbackError(f"{label} field drift")
    return value


def _string_list(value: object, label: str) -> tuple[str, ...]:
    if (
        not isinstance(value, list)
        or any(not isinstance(item, str) or not item for item in value)
        or len(value) != len(set(value))
    ):
        raise FallbackError(f"{label} must be a unique nonempty string list")
    return tuple(value)


def _package_default_extensions(package_configuration: bytes) -> tuple[str, ...]:
    try:
        text = package_configuration.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise FallbackError("package.yaml must be UTF-8") from exc
    if not re.search(r"(?m)^language: GHC2024$", text):
        raise FallbackError("compiler language edition drift")
    matched = re.search(
        r"(?ms)^default-extensions:\n((?:  - [^\n]+\n)+)",
        text,
    )
    if matched is None:
        raise FallbackError("package default extensions are missing")
    return tuple(
        line.removeprefix("  - ")
        for line in matched.group(1).splitlines()
    )


def _expected_derived_configuration(mandated_configuration: bytes) -> bytes:
    marker = b"language_extensions:\n  - GHC2024\n"
    if mandated_configuration.count(marker) != 1:
        raise FallbackError("mandated GHC2024 configuration shape drift")
    expansion = b"language_extensions:\n" + b"".join(
        f"  - {extension}\n".encode("ascii")
        for extension in EFFECTIVE_EXTENSIONS
    )
    return mandated_configuration.replace(marker, expansion, 1)


def validate_fallback_contract(
    contract: Mapping[str, object],
    *,
    mandated_configuration: bytes,
    derived_configuration: bytes,
    package_configuration: bytes,
) -> ValidatedFallback:
    """공식 목록, No* override, 두 formatter 설정과 failure leaf를 함께 검증한다."""

    if set(contract) != {
        "schemaVersion",
        "formatter",
        "officialSource",
        "projectExtensionOverrides",
        "effectiveFormatterEditionExpansion",
        "knownCapabilityFailure",
        "derivedCapabilityProbe",
        "fallbackSemantics",
    } or contract.get("schemaVersion") != "s1.4x-stylish-ghc2024-fallback-v1":
        raise FallbackError("fallback contract outer field drift")

    formatter = _object(
        contract["formatter"],
        "formatter",
        {"name", "version", "executableSha256"},
    )
    if formatter != {
        "name": "stylish-haskell",
        "version": "0.15.1.0",
        "executableSha256": FORMATTER_SHA256,
    }:
        raise FallbackError("formatter identity drift")

    official = _object(
        contract["officialSource"],
        "official source",
        {
            "compilerVersion",
            "edition",
            "uri",
            "contentSha256",
            "installedRstSourceSha256",
            "section",
            "ghc2024Extensions",
            "ghc2024ExtensionsCanonicalJsonSha256",
        },
    )
    official_extensions = _string_list(
        official["ghc2024Extensions"],
        "official GHC2024 extension list",
    )
    official_hash = canonical_sha256(list(official_extensions))
    if (
        official_extensions != OFFICIAL_EXTENSIONS
        or official_hash
        != "3822e8f4c0597c4bb84f628f08e00617e8dac8da1f0eb532991355402e3537cd"
        or official.get("ghc2024ExtensionsCanonicalJsonSha256") != official_hash
    ):
        raise FallbackError("official GHC2024 extension list drift")
    if (
        official.get("compilerVersion") != "9.10.3"
        or official.get("edition") != "GHC2024"
        or official.get("uri") != OFFICIAL_SOURCE_URI
        or official.get("contentSha256") != OFFICIAL_SOURCE_CONTENT_SHA256
        or official.get("installedRstSourceSha256") != OFFICIAL_RST_SOURCE_SHA256
        or official.get("section") != "GHC2024"
    ):
        raise FallbackError("official GHC2024 source identity drift")

    overrides = _object(
        contract["projectExtensionOverrides"],
        "project extension overrides",
        {
            "explicitNoExtensions",
            "ghc2024DisabledExtensions",
            "nonEditionEnabledExtensions",
            "nonEditionEnabledExtensionsAreNotPartOfFallbackExpansion",
        },
    )
    explicit_no = _string_list(
        overrides["explicitNoExtensions"],
        "project explicit No extensions",
    )
    if explicit_no != PROJECT_EXPLICIT_NO_EXTENSIONS:
        raise FallbackError("project explicit No extension drift")
    if _string_list(
        overrides["ghc2024DisabledExtensions"],
        "GHC2024 disabled extensions",
    ) != GHC2024_DISABLED_EXTENSIONS:
        raise FallbackError("GHC2024 disabled extension drift")
    if _string_list(
        overrides["nonEditionEnabledExtensions"],
        "project non-edition enabled extensions",
    ) != PROJECT_NON_EDITION_ENABLED_EXTENSIONS or (
        overrides["nonEditionEnabledExtensionsAreNotPartOfFallbackExpansion"] is not True
    ):
        raise FallbackError("project non-edition extension accounting drift")
    if _package_default_extensions(package_configuration) != (
        *PROJECT_EXPLICIT_NO_EXTENSIONS,
        *PROJECT_NON_EDITION_ENABLED_EXTENSIONS,
    ):
        raise FallbackError("package default extension projection drift")

    effective = _object(
        contract["effectiveFormatterEditionExpansion"],
        "effective formatter edition expansion",
        {
            "extensions",
            "extensionsCanonicalJsonSha256",
            "derivedConfigurationPath",
            "derivedConfigurationSha256",
        },
    )
    effective_extensions = _string_list(
        effective["extensions"],
        "effective formatter extensions",
    )
    effective_hash = canonical_sha256(list(effective_extensions))
    if effective_extensions != EFFECTIVE_EXTENSIONS or effective_hash != (
        "a13ee7bdfe5bb58a13c69fa1faba5788d1d4eeea0fc5f0fd04c5519a42955033"
    ):
        raise FallbackError("effective formatter extension list drift")
    if (
        effective["extensionsCanonicalJsonSha256"] != effective_hash
        or effective["derivedConfigurationPath"]
        != ".stylish-haskell-ghc2024-expanded.yaml"
        or effective["derivedConfigurationSha256"] != DERIVED_CONFIGURATION_SHA256
    ):
        raise FallbackError("effective formatter expansion identity drift")

    mandated_hash = bytes_sha256(mandated_configuration)
    if mandated_hash != MANDATED_CONFIGURATION_SHA256:
        raise FallbackError("mandated formatter configuration drift")
    expected_derived = _expected_derived_configuration(mandated_configuration)
    derived_hash = bytes_sha256(derived_configuration)
    if (
        derived_configuration != expected_derived
        or derived_hash != DERIVED_CONFIGURATION_SHA256
    ):
        raise FallbackError("derived formatter configuration drift")

    known_failure = _object(
        contract["knownCapabilityFailure"],
        "known formatter failure leaf",
        {
            "mandatedConfigurationPath",
            "mandatedConfigurationSha256",
            "fixturePath",
            "fixtureSha256",
            "argvTemplate",
            "argvTemplateCanonicalJsonSha256",
            "exitCode",
            "stdoutSha256",
            "stderrSha256",
            "stderrText",
            "sourceMutationCount",
        },
    )
    known_argv = [
        "stylish-haskell",
        "--config=.stylish-haskell.yaml",
        "tools/fixtures/stylish/misformatted.hs",
    ]
    if known_failure != {
        "mandatedConfigurationPath": ".stylish-haskell.yaml",
        "mandatedConfigurationSha256": MANDATED_CONFIGURATION_SHA256,
        "fixturePath": "tools/fixtures/stylish/misformatted.hs",
        "fixtureSha256": FIXTURE_SHA256,
        "argvTemplate": known_argv,
        "argvTemplateCanonicalJsonSha256": canonical_sha256(known_argv),
        "exitCode": 1,
        "stdoutSha256": EMPTY_SHA256,
        "stderrSha256": MANDATED_STDERR_SHA256,
        "stderrText": MANDATED_STDERR.decode("utf-8"),
        "sourceMutationCount": 0,
    }:
        raise FallbackError("known formatter failure leaf drift")

    derived_probe = _object(
        contract["derivedCapabilityProbe"],
        "derived capability probe",
        {
            "argvTemplate",
            "argvTemplateCanonicalJsonSha256",
            "exitCode",
            "stdoutSha256",
            "stderrSha256",
            "sourceMutationCount",
        },
    )
    derived_argv = [
        "stylish-haskell",
        "--config=.stylish-haskell-ghc2024-expanded.yaml",
        "tools/fixtures/stylish/misformatted.hs",
    ]
    if derived_probe != {
        "argvTemplate": derived_argv,
        "argvTemplateCanonicalJsonSha256": canonical_sha256(derived_argv),
        "exitCode": 1,
        "stdoutSha256": DERIVED_STDOUT_SHA256,
        "stderrSha256": EMPTY_SHA256,
        "sourceMutationCount": 0,
    }:
        raise FallbackError("derived formatter capability probe drift")

    semantics = _object(
        contract["fallbackSemantics"],
        "fallback semantics",
        {
            "status",
            "mandatedConfigurationPreservedByteForByte",
            "formatterReplacementAllowed",
            "compilerLanguageEditionRemains",
            "hlintLanguageEditionRemains",
            "limitation",
        },
    )
    if (
        semantics.get("status") != "PINNED_PARSER_COMPATIBILITY_FALLBACK"
        or semantics.get("mandatedConfigurationPreservedByteForByte") is not True
        or semantics.get("formatterReplacementAllowed") is not False
        or semantics.get("compilerLanguageEditionRemains") != "GHC2024"
        or semantics.get("hlintLanguageEditionRemains") != "GHC2024"
        or not isinstance(semantics.get("limitation"), str)
        or "stylish-haskell 0.15.1.0" not in str(semantics.get("limitation"))
    ):
        raise FallbackError("fallback semantic boundary drift")

    return ValidatedFallback(
        official_extension_count=len(official_extensions),
        effective_extension_count=len(effective_extensions),
        official_extensions_sha256=official_hash,
        effective_extensions_sha256=effective_hash,
        mandated_configuration_sha256=mandated_hash,
        derived_configuration_sha256=derived_hash,
    )


def _atomic_write_json(path: Path, value: Mapping[str, object]) -> None:
    if path.exists() or path.is_symlink():
        raise FallbackError(f"fallback evidence output already exists: {path}")
    parent = path.parent.resolve(strict=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    payload = canonical_json_bytes(value) + b"\n"
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _load_and_validate(root: Path) -> tuple[Mapping[str, object], ValidatedFallback]:
    root = root.resolve(strict=True)
    contract = strict_json_load(root / "stylish-ghc2024-fallback.v1.json")
    validated = validate_fallback_contract(
        contract,
        mandated_configuration=(root / ".stylish-haskell.yaml").read_bytes(),
        derived_configuration=(
            root / ".stylish-haskell-ghc2024-expanded.yaml"
        ).read_bytes(),
        package_configuration=(root / "package.yaml").read_bytes(),
    )
    return contract, validated


def _verify_command(arguments: argparse.Namespace) -> None:
    _, validated = _load_and_validate(arguments.haskell_root)
    print(
        json.dumps(
            {
                "derivedConfigurationSha256": validated.derived_configuration_sha256,
                "effectiveExtensionCount": validated.effective_extension_count,
                "officialExtensionCount": validated.official_extension_count,
                "status": "PASS",
            },
            sort_keys=True,
        )
    )


def _run_probe(
    *,
    root: Path,
    formatter: Path,
    configuration: str,
    fixture: str,
) -> tuple[list[str], subprocess.CompletedProcess[bytes], str, str]:
    argv = [
        str(formatter),
        f"--config={configuration}",
        fixture,
    ]
    fixture_path = root / fixture
    before = file_sha256(fixture_path)
    completed = subprocess.run(
        argv,
        cwd=root,
        check=False,
        capture_output=True,
    )
    after = file_sha256(fixture_path)
    return argv, completed, before, after


def _probe_command(arguments: argparse.Namespace) -> None:
    root = arguments.haskell_root.resolve(strict=True)
    contract, validated = _load_and_validate(root)
    formatter = arguments.formatter_bin.resolve(strict=True)
    if file_sha256(formatter) != FORMATTER_SHA256:
        raise FallbackError("formatter executable SHA-256 drift")
    fixture = "tools/fixtures/stylish/misformatted.hs"
    mandated_argv, mandated, mandated_before, mandated_after = _run_probe(
        root=root,
        formatter=formatter,
        configuration=".stylish-haskell.yaml",
        fixture=fixture,
    )
    if (
        mandated.returncode != 1
        or bytes_sha256(mandated.stdout) != EMPTY_SHA256
        or bytes_sha256(mandated.stderr) != MANDATED_STDERR_SHA256
        or mandated_before != FIXTURE_SHA256
        or mandated_after != mandated_before
    ):
        raise FallbackError("known formatter failure leaf no longer reproduces exactly")

    derived_argv, derived, derived_before, derived_after = _run_probe(
        root=root,
        formatter=formatter,
        configuration=".stylish-haskell-ghc2024-expanded.yaml",
        fixture=fixture,
    )
    if (
        derived.returncode != 1
        or bytes_sha256(derived.stdout) != DERIVED_STDOUT_SHA256
        or bytes_sha256(derived.stderr) != EMPTY_SHA256
        or derived_before != FIXTURE_SHA256
        or derived_after != derived_before
    ):
        raise FallbackError("derived formatter capability probe drift")

    output = arguments.output.resolve(strict=False)
    receipt: dict[str, object] = {
        "schemaVersion": "s1.4x-stylish-ghc2024-capability-result-v1",
        "formatterPathId": "STYLISH_HASKELL_0_15_1_0",
        "formatterPath": str(formatter),
        "formatterSha256": FORMATTER_SHA256,
        "formatterVersion": "0.15.1.0",
        "fallbackContractPath": str(
            (root / "stylish-ghc2024-fallback.v1.json").resolve(strict=True)
        ),
        "fallbackContractSha256": file_sha256(
            root / "stylish-ghc2024-fallback.v1.json"
        ),
        "officialSourceUri": OFFICIAL_SOURCE_URI,
        "officialSourceContentSha256": OFFICIAL_SOURCE_CONTENT_SHA256,
        "officialExtensionsCanonicalJsonSha256": (
            validated.official_extensions_sha256
        ),
        "effectiveExtensionsCanonicalJsonSha256": (
            validated.effective_extensions_sha256
        ),
        "mandatedConfigurationPath": str(
            (root / ".stylish-haskell.yaml").resolve(strict=True)
        ),
        "mandatedConfigurationSha256": validated.mandated_configuration_sha256,
        "mandatedArgv": mandated_argv,
        "mandatedExitCode": mandated.returncode,
        "mandatedStdoutSha256": bytes_sha256(mandated.stdout),
        "mandatedStderrSha256": bytes_sha256(mandated.stderr),
        "derivedConfigurationPath": str(
            (root / ".stylish-haskell-ghc2024-expanded.yaml").resolve(strict=True)
        ),
        "derivedConfigurationSha256": validated.derived_configuration_sha256,
        "derivedArgv": derived_argv,
        "derivedExitCode": derived.returncode,
        "derivedStdoutSha256": bytes_sha256(derived.stdout),
        "derivedStderrSha256": bytes_sha256(derived.stderr),
        "fixturePath": str((root / fixture).resolve(strict=True)),
        "fixtureSha256Before": mandated_before,
        "fixtureSha256AfterMandatedProbe": mandated_after,
        "fixtureSha256AfterDerivedProbe": derived_after,
        "sourceMutationCount": 0,
        "fallbackStatus": "PINNED_PARSER_COMPATIBILITY_FALLBACK",
        "limitation": contract["fallbackSemantics"]["limitation"],
        "status": "PASS",
    }
    _atomic_write_json(output, receipt)
    print(
        json.dumps(
            {
                "receiptPath": str(output),
                "receiptSha256": file_sha256(output),
                "status": "PASS",
            },
            sort_keys=True,
        )
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    verify = commands.add_parser("verify")
    verify.add_argument("--haskell-root", type=Path, required=True)
    verify.set_defaults(handler=_verify_command)

    probe = commands.add_parser("probe")
    probe.add_argument("--haskell-root", type=Path, required=True)
    probe.add_argument("--formatter-bin", type=Path, required=True)
    probe.add_argument("--output", type=Path, required=True)
    probe.set_defaults(handler=_probe_command)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        arguments.handler(arguments)
    except (FallbackError, OSError, UnicodeError, ValueError) as exc:
        print(f"STYLISH_FALLBACK_FAIL:{exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
