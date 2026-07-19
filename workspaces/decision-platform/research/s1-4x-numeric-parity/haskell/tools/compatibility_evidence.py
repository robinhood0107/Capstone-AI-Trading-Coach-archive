#!/bin/false
"""기존 GHC 9.14.1 solve 실패를 portable typed evidence로 고정한다."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import tempfile
from pathlib import Path
from typing import Any, Mapping, Sequence


class CompatibilityEvidenceError(RuntimeError):
    """Compatibility evidence가 관측된 frozen failure와 다를 때 발생한다."""


AUTHORITATIVE_BINDIST_SHA256 = (
    "b6200c32a56f26f5d2ff77c92481a47a53bb3d43cbc82b59a997aed2ad5fd937"
)
COMPATIBILITY_BINDIST_SHA256 = (
    "530f0861d1d7b45476c158a9e68aa89987687aaf9939df7a15995c45726e2344"
)
AUTHORITATIVE_BOOT_SET_SHA256 = (
    "e8136c897c01d61282795557a7303c27c8b92530ecb4b476d7dac6cf25bc330b"
)
COMPATIBILITY_BOOT_SET_SHA256 = (
    "ccbefbb07268503d319ff07e4a98c6fd0dc89289f4a4915ce22c6aa7826ea3e9"
)
DIRECT_PARENT_MANIFEST_SHA256 = (
    "8a3b874a939b2a8e419dcc844d8cd761abdecd9642eb857e5a84b8c2a7f1eccc"
)
CONFIGURATION_AST_SHA256 = (
    "4254014568eab382be04bc83bcaf7c191c16c5df2d475e769c50c7ab24452742"
)
CANDIDATE_SOURCE_TREE_SHA256 = (
    "96c1e32ddd93ef219cf77b42693126bcb67c1c3260a99a5a04e7aca581986b80"
)
EMPTY_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"

RAW_EVIDENCE = {
    "baseUri": "cache://s1-4x/haskell-evidence/ghc914-solve-20260718T174629Z",
    "stderr": {
        "pathId": "STDERR",
        "sha256": "22c3939bedcd8861c0fe1f987ca500c0ebf3f89ded229a2fe8f7107722019e0a",
        "size": 17544,
    },
    "stdout": {
        "pathId": "STDOUT",
        "sha256": EMPTY_SHA256,
        "size": 0,
    },
}

FAILURE_LEAF = {
    "message": "Stack failed to construct a build plan.",
    "prunedBootPackages": [
        {
            "compatibilityBootVersion": "1.3.10.0",
            "package": "directory",
            "snapshotVersion": "1.3.11.0",
        },
        {
            "compatibilityBootVersion": "1.6.26.1",
            "package": "process",
            "requiredBy": "optparse-applicative-0.18.1.0",
            "requiredRange": ">=1.0 && <1.7",
            "snapshotVersion": "1.6.30.0",
        },
    ],
    "stackErrorCode": "S-4804",
    "suggestedExtraDeps": [
        {
            "cabalRevisionSha256": (
                "2346c4f0af05c4ed55e77543e94b26f1b82523efd24da986bdd48a8f8a84c5a0"
            ),
            "cabalRevisionSize": 3113,
            "package": "directory",
            "version": "1.3.11.0",
        },
        {
            "cabalRevisionSha256": (
                "b74eed77eb3237c4ab6a39f08bcce4712b4486b091712ee20e92c8864f1e80a0"
            ),
            "cabalRevisionSize": 3754,
            "package": "process",
            "version": "1.6.30.0",
        },
    ],
}

CONFIGURATION_AST = {
    "compilerCheck": "match-exact",
    "flags": {
        "math-functions": {
            "system-erf": False,
            "system-expm1": False,
        }
    },
    "installGhc": False,
    "packages": ["."],
    "snapshot": "lts-24.50",
    "systemGhc": True,
}


def _parent(
    package: str,
    version: str,
    source_sha256: str,
    effective_flags: Mapping[str, bool] | None = None,
) -> dict[str, Any]:
    return {
        "effectiveFlags": dict(effective_flags or {}),
        "package": package,
        "sourceSha256": source_sha256,
        "sourceUri": (
            f"https://hackage.haskell.org/package/{package}-{version}/"
            f"{package}-{version}.tar.gz"
        ),
        "version": version,
    }


DIRECT_NON_BOOT_PARENTS = sorted(
    [
        _parent(
            "QuickCheck",
            "2.15.0.1",
            "a3b2216ddbaf481dbc82414b6120f8b726d969db3f0b51f20a7a45425ef36e7f",
            {"old-random": False},
        ),
        _parent(
            "SHA",
            "1.6.4.4",
            "6bd950df6b11a3998bb1452d875d2da043ee43385459afc5f16d471d25178b44",
        ),
        _parent(
            "aeson",
            "2.2.5.0",
            "e22f9883adce9e02d77de6a1bba36f2f98d322c8a9fa3bc51596d31493d00ac5",
        ),
        _parent(
            "attoparsec",
            "0.14.4",
            "3f337fe58624565de12426f607c23e60c7b09c86b4e3adfc827ca188c9979e6c",
        ),
        _parent(
            "criterion",
            "1.6.4.0",
            "062bf47a43278dfe8725391b5e550905f185801c79ea772a9cdaa672b2ea2f51",
        ),
        _parent(
            "math-functions",
            "0.3.4.4",
            "2441d8dd50eff445356048b2a0cdf88c4a7ba0f56614293d4975e1b081faf8fa",
            {"system-erf": False, "system-expm1": False},
        ),
        _parent(
            "scientific",
            "0.3.8.1",
            "ad3781be149dfc7936e62eb9c3ad78ded0e9985b4dae16d2f62d9ba957ccdcfe",
        ),
        _parent(
            "tasty",
            "1.5.4",
            "c76120141bd61c4418b3ed5efc5fe3094186d47cfe12d7be552320139f52c6c7",
        ),
        _parent(
            "tasty-hunit",
            "0.10.2",
            "5af01fa7c1ef98b324da062e36f79986a8b1b83ff0cf6fd53f95d976b41e03f6",
        ),
        _parent(
            "tasty-quickcheck",
            "0.11.1",
            "e3d4de7455ed342f8874d84686def897b8a316ce198461da18106d8a1f63246a",
        ),
        _parent(
            "vector",
            "0.13.2.0",
            "28f203c786cbf8ac6dc3fea3378ec36f34173d505fb4a1dd60fc8418ad91c423",
        ),
    ],
    key=lambda item: item["package"].encode(),
)

FROZEN_INPUTS = {
    "authoritativeStackLock": {
        "pathId": "HASKELL_STACK_LOCK",
        "sha256": "e376d075c33c8bc14aebc6f27c6de3a6be81056354a1fc332d71f959f4870154",
    },
    "authoritativeStackYaml": {
        "pathId": "HASKELL_STACK_YAML",
        "sha256": "6e4c63c2e9e918210d01e8d17a3dbb50565d48f00e254bb750bb36379cd6b09e",
    },
    "compatibilityPolicy": {
        "pathId": "GHC_COMPATIBILITY_POLICY_V1",
        "sha256": "288d77cec7627c4e8b4da0f537f789bb33c03f3aeb5d1cfe1da2c9b0ddb64209",
    },
    "compatibilityStackLock": {
        "generatedBySolve": True,
        "pathId": "HASKELL_GHC_914_STACK_LOCK_GENERATED",
        "sha256": "e376d075c33c8bc14aebc6f27c6de3a6be81056354a1fc332d71f959f4870154",
    },
    "compatibilityStackYaml": {
        "pathId": "HASKELL_GHC_914_STACK_YAML",
        "sha256": "7e9effb7194df42a50fac175b079fbb07ce9edd7f09a048fe428bf20cec299da",
    },
    "snapshot": {
        "sha256": "f9f775487e1678844b9f031919ed508a88097aa883002a970d3cd6fc6308b5d2",
        "size": 732679,
        "uri": (
            "https://raw.githubusercontent.com/commercialhaskell/"
            "stackage-snapshots/master/lts/24/50.yaml"
        ),
    },
    "toolchain": {
        "compatibilityCompiler": {
            "pathId": "GHCUP_GHC_9_14_1",
            "sha256": (
                "ecfd54b4161699f574d2b163bdc817c54df08a08a310323e43b41ab5fc413ef1"
            ),
            "version": "9.14.1",
        },
        "ghcup": {
            "pathId": "GHCUP_0_2_6_2_LINUX_X86_64",
            "sha256": (
                "9ed5da5449b48043a0d17e767c05d2ef585e25a639bb934329496c6d2fad9cf8"
            ),
            "version": "0.2.6.2",
        },
        "stack": {
            "pathId": "GHCUP_STACK_3_11_1",
            "sha256": (
                "923dbd137756652c67b376e2447c655b87fcc373f4d104b5073bca913471ecbe"
            ),
            "version": "3.11.1",
        },
        "toolchainProvenanceSha256": (
            "cd9e29a22473fba6203daa4f3a0cbaa57b8b6e5c5fc22de05ca0801c404ffa98"
        ),
    },
}

DOWNSTREAM_NOT_RUN = {
    "candidateCompile": "NOT_RUN",
    "crossReplay": "NOT_RUN",
    "fullCorrectness": "NOT_RUN",
    "oracleReplay": "NOT_RUN",
    "processReplay": "NOT_RUN",
    "stableErrorReplay": "NOT_RUN",
}

OUTER_FIELDS = {
    "bootSets",
    "candidateSourceTree",
    "classification",
    "downstream",
    "execution",
    "failureLeaf",
    "fallbackProof",
    "frozenInputs",
    "laneId",
    "rawEvidence",
    "schemaVersion",
}


def canonical_json_bytes(value: Any, *, trailing_newline: bool = False) -> bytes:
    """정렬 key와 compact separator의 canonical JSON bytes를 만든다."""

    text = json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return (text + ("\n" if trailing_newline else "")).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    """Canonical JSON value의 lowercase SHA-256을 반환한다."""

    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def sha256_file(path: Path) -> str:
    """regular non-symlink file bytes의 lowercase SHA-256을 반환한다."""

    if path.is_symlink() or not path.is_file():
        raise CompatibilityEvidenceError(f"not a regular evidence input: {path}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def strict_json_load(path: Path) -> Any:
    """중복 key와 non-finite JSON을 거부한다."""

    def object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise CompatibilityEvidenceError(f"duplicate JSON key: {path}:{key}")
            result[key] = value
        return result

    try:
        return json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=object_pairs,
            parse_constant=lambda token: (_ for _ in ()).throw(
                CompatibilityEvidenceError(
                    f"non-finite JSON token: {path}:{token}"
                )
            ),
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CompatibilityEvidenceError(f"invalid JSON input: {path}") from exc


def atomic_write_json(path: Path, value: Any) -> None:
    """Canonical JSON을 같은 directory에서 fsync 후 atomic replace한다."""

    if path.is_symlink():
        raise CompatibilityEvidenceError(f"refusing to replace symlink: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(canonical_json_bytes(value, trailing_newline=True))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def parse_current_configuration_ast(path: Path) -> dict[str, Any]:
    """현재 compatibility Stack YAML의 compiler 외 설정을 좁은 AST로 다시 읽는다."""

    if path.is_symlink() or not path.is_file():
        raise CompatibilityEvidenceError("current Stack YAML is missing or unsafe")
    scalar_values: dict[str, str] = {}
    packages: list[str] = []
    flags: dict[str, dict[str, bool]] = {}
    section: str | None = None
    current_flag_package: str | None = None
    allowed_scalars = {
        "snapshot",
        "compiler",
        "compiler-check",
        "system-ghc",
        "install-ghc",
    }
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        if "\t" in raw_line:
            raise CompatibilityEvidenceError("current Stack YAML contains a tab")
        indentation = len(raw_line) - len(raw_line.lstrip(" "))
        stripped = raw_line.strip()
        if indentation == 0:
            match = re.fullmatch(r"([a-z][a-z-]*):(?:\s*(.*))?", stripped)
            if match is None:
                raise CompatibilityEvidenceError(
                    f"current Stack YAML syntax drift at line {line_number}"
                )
            key, value = match.groups()
            if key in scalar_values or key in {"packages", "flags"} and section == key:
                raise CompatibilityEvidenceError(
                    f"duplicate current Stack YAML key: {key}"
                )
            if key in allowed_scalars:
                if not value:
                    raise CompatibilityEvidenceError(
                        f"current Stack YAML scalar missing: {key}"
                    )
                scalar_values[key] = value
                section = None
                current_flag_package = None
            elif key in {"packages", "flags"} and not value:
                section = key
                current_flag_package = None
            else:
                raise CompatibilityEvidenceError(
                    f"current Stack YAML key forbidden: {key}"
                )
            continue
        if section == "packages" and indentation == 2:
            match = re.fullmatch(r"-\s+(\S+)", stripped)
            if match is None:
                raise CompatibilityEvidenceError("current Stack package syntax drift")
            packages.append(match.group(1))
            continue
        if section == "flags" and indentation == 2:
            match = re.fullmatch(r"([A-Za-z0-9][A-Za-z0-9-]*):", stripped)
            if match is None or match.group(1) in flags:
                raise CompatibilityEvidenceError("current Stack flag package drift")
            current_flag_package = match.group(1)
            flags[current_flag_package] = {}
            continue
        if (
            section == "flags"
            and indentation == 4
            and current_flag_package is not None
        ):
            match = re.fullmatch(
                r"([A-Za-z0-9][A-Za-z0-9-]*):\s+(true|false)",
                stripped,
            )
            if match is None or match.group(1) in flags[current_flag_package]:
                raise CompatibilityEvidenceError("current Stack flag value drift")
            flags[current_flag_package][match.group(1)] = match.group(2) == "true"
            continue
        raise CompatibilityEvidenceError(
            f"current Stack YAML indentation drift at line {line_number}"
        )
    if (
        set(scalar_values) != allowed_scalars
        or scalar_values["compiler"] != "ghc-9.14.1"
        or not packages
        or not flags
        or len(packages) != len(set(packages))
    ):
        raise CompatibilityEvidenceError("current Stack YAML field set drift")

    def parse_bool(key: str) -> bool:
        value = scalar_values[key]
        if value not in {"true", "false"}:
            raise CompatibilityEvidenceError(f"current Stack boolean drift: {key}")
        return value == "true"

    return {
        "compilerCheck": scalar_values["compiler-check"],
        "flags": flags,
        "installGhc": parse_bool("install-ghc"),
        "packages": packages,
        "snapshot": scalar_values["snapshot"],
        "systemGhc": parse_bool("system-ghc"),
    }


def _boot_versions_from_dump(path: Path) -> dict[str, str]:
    if path.is_symlink() or not path.is_file():
        raise CompatibilityEvidenceError("boot dump is missing or unsafe")
    versions: dict[str, str] = {}
    for chunk in path.read_text(encoding="utf-8").split("\n---\n"):
        fields: dict[str, str] = {}
        for line in chunk.splitlines():
            match = re.fullmatch(r"(name|version):\s+(.+?)\s*", line)
            if match is not None:
                fields[match.group(1)] = match.group(2)
        if set(fields) == {"name", "version"}:
            if fields["name"] in versions:
                raise CompatibilityEvidenceError("duplicate boot package name")
            versions[fields["name"]] = fields["version"]
    if not versions:
        raise CompatibilityEvidenceError("empty boot package dump")
    return versions


def parse_current_s4804_failure_leaf(
    stderr_text: str,
    *,
    compatibility_boot_versions: Mapping[str, str],
) -> dict[str, Any]:
    """현재 stderr의 단일 exact S-4804 dependency leaf를 구조화한다."""

    if stderr_text.count("Error: [S-4804]") != 1 or stderr_text.count(
        "Stack failed to construct a build plan."
    ) != 1:
        raise CompatibilityEvidenceError("current exact S-4804 identity drift")
    normalized = " ".join(stderr_text.split())
    process_match = re.search(
        r"In the dependencies for "
        r"(?P<required_by>optparse-applicative-(?P<parent_version>[0-9.]+)): "
        r"\* process must match (?P<required_range>.+?), but this GHC boot package "
        r"has been pruned from the Stack configuration\..+?"
        r"\(latest matching version is (?P<snapshot_version>[0-9.]+)\)\.",
        normalized,
    )
    directory_match = re.search(
        r"In the dependencies for s1-4x-haskell-[0-9.]+: "
        r"\* directory needed, but this GHC boot package has been pruned from "
        r"the Stack configuration\..+?"
        r"\(latest matching version is (?P<snapshot_version>[0-9.]+)\)\.",
        normalized,
    )
    suggestions = [
        {
            "package": match.group("package"),
            "version": match.group("version"),
            "cabalRevisionSha256": match.group("sha256"),
            "cabalRevisionSize": int(match.group("size")),
        }
        for match in re.finditer(
            r"(?m)^\s*-\s+"
            r"(?P<package>[A-Za-z][A-Za-z0-9-]*)-"
            r"(?P<version>[0-9]+(?:\.[0-9]+)+)@sha256:"
            r"(?P<sha256>[0-9a-f]{64}),(?P<size>[0-9]+)\s*$",
            stderr_text,
        )
    ]
    suggestions.sort(key=lambda item: item["package"].encode())
    if (
        process_match is None
        or directory_match is None
        or set(compatibility_boot_versions) < {"directory", "process"}
        or [item["package"] for item in suggestions] != ["directory", "process"]
        or process_match.group("required_by") != "optparse-applicative-0.18.1.0"
        or process_match.group("required_range") != ">=1.0 && <1.7"
    ):
        raise CompatibilityEvidenceError("current exact S-4804 leaf drift")
    suggested_versions = {
        item["package"]: item["version"] for item in suggestions
    }
    if (
        suggested_versions["directory"]
        != directory_match.group("snapshot_version")
        or suggested_versions["process"]
        != process_match.group("snapshot_version")
    ):
        raise CompatibilityEvidenceError("current exact S-4804 version drift")
    return {
        "message": "Stack failed to construct a build plan.",
        "prunedBootPackages": [
            {
                "compatibilityBootVersion": compatibility_boot_versions[
                    "directory"
                ],
                "package": "directory",
                "snapshotVersion": suggested_versions["directory"],
            },
            {
                "compatibilityBootVersion": compatibility_boot_versions["process"],
                "package": "process",
                "requiredBy": process_match.group("required_by"),
                "requiredRange": process_match.group("required_range"),
                "snapshotVersion": suggested_versions["process"],
            },
        ],
        "stackErrorCode": "S-4804",
        "suggestedExtraDeps": suggestions,
    }


def _direct_cabal_dependencies(path: Path) -> dict[str, set[str]]:
    if path.is_symlink() or not path.is_file():
        raise CompatibilityEvidenceError("candidate Cabal file is missing or unsafe")
    dependencies: dict[str, set[str]] = {}
    lines = path.read_text(encoding="utf-8").splitlines()
    index = 0
    while index < len(lines):
        match = re.match(r"^(?P<indent>\s*)build-depends:\s*(?P<rest>.*)$", lines[index])
        if match is None:
            index += 1
            continue
        indentation = len(match.group("indent"))
        entries = [match.group("rest")]
        index += 1
        while index < len(lines):
            line = lines[index]
            if not line.strip():
                index += 1
                continue
            current_indentation = len(line) - len(line.lstrip(" "))
            if current_indentation <= indentation:
                break
            entries.append(line.strip())
            index += 1
        for entry in ",".join(entries).split(","):
            normalized = entry.strip()
            if not normalized:
                continue
            dependency = re.fullmatch(
                r"(?P<package>[A-Za-z][A-Za-z0-9-]*)"
                r"(?:\s+(?P<constraint>.+))?",
                normalized,
            )
            if dependency is None:
                raise CompatibilityEvidenceError(
                    f"candidate Cabal dependency syntax drift: {normalized}"
                )
            dependencies.setdefault(dependency.group("package"), set()).add(
                dependency.group("constraint") or ""
            )
    if not dependencies:
        raise CompatibilityEvidenceError("candidate Cabal direct parents missing")
    return dependencies


def _snapshot_package_versions(snapshot_text: str) -> dict[str, str]:
    versions: dict[str, str] = {}
    for match in re.finditer(
        r"(?m)^-\s+hackage:\s+"
        r"(?P<package>[A-Za-z][A-Za-z0-9-]*)-"
        r"(?P<version>[0-9]+(?:\.[0-9]+)+)@sha256:"
        r"[0-9a-f]{64},[0-9]+\s*$",
        snapshot_text,
    ):
        package = match.group("package")
        if package in versions:
            raise CompatibilityEvidenceError("duplicate snapshot package")
        versions[package] = match.group("version")
    if not versions:
        raise CompatibilityEvidenceError("snapshot package list missing")
    return versions


def derive_current_direct_non_boot_parents(
    *,
    cabal_path: Path,
    snapshot_text: str,
    pantry_db: Path,
    boot_package_names: set[str],
    local_package_names: set[str],
    approved_flags: Mapping[str, Mapping[str, bool]],
) -> list[dict[str, Any]]:
    """현재 Cabal direct deps와 현재 snapshot/Pantry identity를 결합한다."""

    dependencies = _direct_cabal_dependencies(cabal_path)
    snapshot_versions = _snapshot_package_versions(snapshot_text)
    selected_names = sorted(
        set(dependencies) - boot_package_names - local_package_names,
        key=str.encode,
    )
    if not selected_names:
        raise CompatibilityEvidenceError("current direct non-boot parents missing")
    connection = sqlite3.connect(f"file:{pantry_db}?mode=ro", uri=True)
    query = """
        SELECT lower(hex(tarball.sha))
        FROM hackage_tarball AS tarball
        JOIN package_name AS package ON package.id = tarball.name
        JOIN version AS version ON version.id = tarball.version
        WHERE package.name = ? AND version.version = ?
    """
    parents: list[dict[str, Any]] = []
    try:
        for package in selected_names:
            version = snapshot_versions.get(package)
            if version is None:
                raise CompatibilityEvidenceError(
                    f"direct parent absent from snapshot: {package}"
                )
            exact_constraints = {
                match.group(1)
                for constraint in dependencies[package]
                if (
                    match := re.fullmatch(
                        r"==\s*([0-9]+(?:\.[0-9]+)+)",
                        constraint,
                    )
                )
                is not None
            }
            if exact_constraints and exact_constraints != {version}:
                raise CompatibilityEvidenceError(
                    f"direct parent exact constraint drift: {package}"
                )
            rows = list(connection.execute(query, (package, version)))
            if len(rows) != 1 or re.fullmatch(r"[0-9a-f]{64}", rows[0][0]) is None:
                raise CompatibilityEvidenceError(
                    f"direct parent Pantry identity drift: {package}"
                )
            parents.append(
                _parent(
                    package,
                    version,
                    rows[0][0],
                    approved_flags.get(package),
                )
            )
    finally:
        connection.close()
    return parents


def read_current_snapshot_from_pantry(
    pantry_db: Path,
    *,
    snapshot_url: str,
    expected_sha256: str,
    expected_size: int,
) -> str:
    """현재 isolated Pantry DB에서 exact frozen snapshot bytes를 다시 읽는다."""

    if pantry_db.is_symlink() or not pantry_db.is_file():
        raise CompatibilityEvidenceError("current Pantry DB is missing or unsafe")
    connection = sqlite3.connect(f"file:{pantry_db}?mode=ro", uri=True)
    try:
        rows = list(
            connection.execute(
                """
SELECT blob.contents
FROM url_blob AS url
JOIN blob AS blob ON blob.id = url.blob
WHERE url.url = ?
ORDER BY url.time DESC
""",
                (snapshot_url,),
            )
        )
        if not rows:
            # Stack 3.11의 bulk Pantry cache는 URL 연결 없이 snapshot을
            # content-addressed blob으로만 보관하므로 frozen lock SHA로 다시 찾는다.
            rows = list(
                connection.execute(
                    """
SELECT contents
FROM blob
WHERE sha = ? AND size = ?
""",
                    (bytes.fromhex(expected_sha256), expected_size),
                )
            )
    finally:
        connection.close()
    if not rows:
        raise CompatibilityEvidenceError("current frozen snapshot cardinality drift")
    payloads = [bytes(row[0]) for row in rows]
    if any(
        len(payload) != expected_size
        or hashlib.sha256(payload).hexdigest() != expected_sha256
        for payload in payloads
    ):
        raise CompatibilityEvidenceError("current frozen snapshot bytes drift")
    payload = payloads[0]
    try:
        return payload.decode("utf-8")
    except UnicodeError as exc:
        raise CompatibilityEvidenceError("current frozen snapshot encoding drift") from exc


def _parse_boot_dump(
    path: Path,
    *,
    pantry_db: Path,
    bindist_sha256: str,
) -> list[dict[str, str]]:
    """ghc-pkg dump identity를 Pantry tarball SHA 또는 bindist SHA와 결합한다."""

    if path.is_symlink() or not path.is_file():
        raise CompatibilityEvidenceError("boot dump is missing or unsafe")
    connection = sqlite3.connect(f"file:{pantry_db}?mode=ro", uri=True)
    query = """
        SELECT lower(hex(tarball.sha))
        FROM hackage_tarball AS tarball
        JOIN package_name AS package ON package.id = tarball.name
        JOIN version AS version ON version.id = tarball.version
        WHERE package.name = ? AND version.version = ?
    """
    packages: list[dict[str, str]] = []
    try:
        for chunk in path.read_text(encoding="utf-8").split("\n---\n"):
            identity: dict[str, str] = {}
            for line in chunk.splitlines():
                match = re.fullmatch(r"(name|version|id):\s+(.+?)\s*", line)
                if match is not None:
                    identity[match.group(1)] = match.group(2)
            if set(identity) != {"name", "version", "id"}:
                continue
            rows = list(
                connection.execute(
                    query,
                    (identity["name"], identity["version"]),
                )
            )
            if len(rows) > 1:
                raise CompatibilityEvidenceError("duplicate Pantry boot source identity")
            packages.append(
                {
                    "package": identity["name"],
                    "sourceSha256": rows[0][0] if rows else bindist_sha256,
                    "unitId": identity["id"],
                    "version": identity["version"],
                }
            )
    finally:
        connection.close()
    packages.sort(
        key=lambda item: (
            item["package"].encode(),
            item["version"].encode(),
            item["unitId"].encode(),
        )
    )
    if len({item["unitId"] for item in packages}) != len(packages):
        raise CompatibilityEvidenceError("duplicate boot package unitId")
    return packages


def _boot_set(
    *,
    compiler_version: str,
    dump_path: Path,
    dump_path_id: str,
    pantry_db: Path,
    bindist_sha256: str,
) -> dict[str, Any]:
    packages = _parse_boot_dump(
        dump_path,
        pantry_db=pantry_db,
        bindist_sha256=bindist_sha256,
    )
    fallback_count = sum(
        item["sourceSha256"] == bindist_sha256 for item in packages
    )
    return {
        "bindistFallbackCount": fallback_count,
        "compilerVersion": compiler_version,
        "hackageSourceCount": len(packages) - fallback_count,
        "manifestSha256": canonical_sha256(packages),
        "packageCount": len(packages),
        "packages": packages,
        "rawDump": {
            "pathId": dump_path_id,
            "sha256": sha256_file(dump_path),
            "size": dump_path.stat().st_size,
        },
    }


def _stack_lock_snapshot(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise CompatibilityEvidenceError("compatibility Stack lock is missing or unsafe")
    text = path.read_text(encoding="utf-8")
    match = re.search(
        r"(?ms)^snapshots:\s*\n"
        r"- completed:\s*\n"
        r"\s+sha256:\s+(?P<sha256>[0-9a-f]{64})\s*\n"
        r"\s+size:\s+(?P<size>[0-9]+)\s*\n"
        r"\s+url:\s+(?P<url>https://\S+)\s*\n"
        r"\s+original:\s+(?P<snapshot>[A-Za-z0-9.-]+)\s*$",
        text,
    )
    if match is None:
        raise CompatibilityEvidenceError("compatibility Stack lock snapshot drift")
    return {
        "sha256": match.group("sha256"),
        "size": int(match.group("size")),
        "url": match.group("url"),
        "snapshot": match.group("snapshot"),
    }


def build_current_plan_proof(
    *,
    haskell_root: Path,
    stack_yaml: Path,
    authoritative_boot_dump: Path,
    compatibility_boot_dump: Path,
    pantry_db: Path,
) -> dict[str, Any]:
    """현재 frozen config, snapshot, Cabal, boot dump에서 plan proof를 계산한다."""

    configuration = parse_current_configuration_ast(stack_yaml)
    lock_snapshot = _stack_lock_snapshot(
        haskell_root / "stack-ghc-9.14.1.yaml.lock"
    )
    if configuration["snapshot"] != lock_snapshot["snapshot"]:
        raise CompatibilityEvidenceError("current snapshot YAML/lock mismatch")
    snapshot_text = read_current_snapshot_from_pantry(
        pantry_db,
        snapshot_url=lock_snapshot["url"],
        expected_sha256=lock_snapshot["sha256"],
        expected_size=lock_snapshot["size"],
    )
    authoritative_boot = _boot_set(
        compiler_version="9.10.3",
        dump_path=authoritative_boot_dump,
        dump_path_id="CURRENT_GHC_9_10_3_GLOBAL_PACKAGE_DUMP",
        pantry_db=pantry_db,
        bindist_sha256=AUTHORITATIVE_BINDIST_SHA256,
    )
    compatibility_boot = _boot_set(
        compiler_version="9.14.1",
        dump_path=compatibility_boot_dump,
        dump_path_id="CURRENT_GHC_9_14_1_GLOBAL_PACKAGE_DUMP",
        pantry_db=pantry_db,
        bindist_sha256=COMPATIBILITY_BINDIST_SHA256,
    )
    compatibility_boot_versions = {
        item["package"]: item["version"]
        for item in compatibility_boot["packages"]
    }
    direct_parents = derive_current_direct_non_boot_parents(
        cabal_path=haskell_root / "s1-4x-haskell.cabal",
        snapshot_text=snapshot_text,
        pantry_db=pantry_db,
        boot_package_names=set(compatibility_boot_versions),
        local_package_names={"s1-4x-core", "s1-4x-haskell"},
        approved_flags=configuration["flags"],
    )
    direct_parent_sha256 = canonical_sha256(direct_parents)
    configuration_sha256 = canonical_sha256(configuration)
    return {
        "authoritativeBootSet": authoritative_boot,
        "authoritativeBootSetSha256": authoritative_boot["manifestSha256"],
        "authoritativeDirectNonBootParents": direct_parents,
        "authoritativeNonBootPlanSha256": direct_parent_sha256,
        "authoritativePackageSetSha256": lock_snapshot["sha256"],
        "compatibilityBootSet": compatibility_boot,
        "compatibilityBootSetSha256": compatibility_boot["manifestSha256"],
        "compatibilityDirectNonBootParents": direct_parents,
        "compatibilityNonBootPlanSha256": direct_parent_sha256,
        "configurationAst": configuration,
        "configurationAstSha256": configuration_sha256,
        "directNonBootParentManifestSha256": direct_parent_sha256,
        "snapshot": lock_snapshot,
    }


def build_current_failure_proof(
    *,
    haskell_root: Path,
    stack_yaml: Path,
    authoritative_boot_dump: Path,
    compatibility_boot_dump: Path,
    pantry_db: Path,
    stderr_path: Path,
) -> dict[str, Any]:
    """현재 solve 입력과 raw dump만으로 frozen dependency 증명을 다시 계산한다."""

    proof = build_current_plan_proof(
        haskell_root=haskell_root,
        stack_yaml=stack_yaml,
        authoritative_boot_dump=authoritative_boot_dump,
        compatibility_boot_dump=compatibility_boot_dump,
        pantry_db=pantry_db,
    )
    compatibility_boot_versions = {
        item["package"]: item["version"]
        for item in proof["compatibilityBootSet"]["packages"]
    }
    failure_leaf = parse_current_s4804_failure_leaf(
        stderr_path.read_text(encoding="utf-8"),
        compatibility_boot_versions=compatibility_boot_versions,
    )
    proof["failedPartialPlanSha256"] = canonical_sha256(failure_leaf)
    proof["failureLeaf"] = failure_leaf
    return proof


def _historical_execution() -> dict[str, Any]:
    argv = [
        "GHCUP_0_2_6_2_LINUX_X86_64",
        "--offline",
        "run",
        "--quick",
        "--ghc",
        "9.14.1",
        "--stack",
        "3.11.1",
        "--",
        "stack",
        "--stack-yaml",
        "HASKELL_GHC_914_STACK_YAML",
        "--no-terminal",
        "--color",
        "never",
        "--system-ghc",
        "--no-install-ghc",
        "--hpack-force",
        "build",
        "--dry-run",
        "--test",
        "--bench",
        "--no-run-tests",
        "--no-run-benchmarks",
    ]
    return {
        "canonicalReproducer": {
            "argv": argv,
            "cwdId": "HASKELL_COMPAT_ROOT",
            "requiredEnvironment": {
                "S1_4X_AUTHORITATIVE_GHC_BIN": "GHCUP_GHC_9_10_3",
                "S1_4X_CACHE_ROOT": "CACHE_ROOT",
                "S1_4X_GHCUP_BIN": "GHCUP_0_2_6_2_LINUX_X86_64",
                "S1_4X_HLINT_BIN": "HLINT_3_10",
                "S1_4X_LATEST_GHC_BIN": "GHCUP_GHC_9_14_1",
                "S1_4X_STACK_BIN": "GHCUP_STACK_3_11_1",
                "S1_4X_STYLISH_BIN": "STYLISH_HASKELL_0_15_1_0",
            },
            "stackRootPathId": "CACHE_ROOT/stack-root-ghc914",
        },
        "endedAt": "2026-07-18T17:47:55.670932Z",
        "exitCode": 1,
        "historicalCommand": {
            "argv": argv,
            "cwdId": "HASKELL_COMPAT_ROOT",
            "legacyEnvironment": {
                "S1_4X_GHCUP_BIN": "GHCUP_0_2_6_2_LINUX_X86_64",
                "S1_4X_GHC_914_BIN": "GHCUP_GHC_9_14_1",
                "S1_4X_GHC_BIN": "GHCUP_GHC_9_10_3",
                "S1_4X_HLINT_BIN": "HLINT_3_10",
                "S1_4X_STACK_BIN": "GHCUP_STACK_3_11_1",
                "S1_4X_STYLISH_HASKELL_BIN": "STYLISH_HASKELL_0_15_1_0",
                "STACK_ROOT": "CACHE_ROOT/stack-root-ghc914",
            },
            "shellSetup": {
                "directoryMode": "0700",
                "outputPathTemplate": "CACHE_ROOT/haskell-evidence/$RUN_ID",
                "runIdExpression": (
                    "ghc914-solve-$(date -u +%Y%m%dT%H%M%SZ)"
                ),
                "stackRootPathId": "CACHE_ROOT/stack-root-ghc914",
            },
            "stderrPathId": "STDERR",
            "stdoutPathId": "STDOUT",
        },
        "rolloutCallId": "call_0AtmO5VLjaiRGKBkS7iRyb0H",
        "startedAt": "2026-07-18T17:46:29.243579Z",
        "timeoutMs": 900000,
    }


def build_failure_evidence(
    *,
    authoritative_boot_dump: Path,
    compatibility_boot_dump: Path,
    pantry_db: Path,
    stdout_path: Path,
    stderr_path: Path,
) -> dict[str, Any]:
    """이미 실행된 solve raw receipts와 boot dumps에서 companion을 만든다."""

    observed_raw = {
        "stderr": {
            "sha256": sha256_file(stderr_path),
            "size": stderr_path.stat().st_size,
        },
        "stdout": {
            "sha256": sha256_file(stdout_path),
            "size": stdout_path.stat().st_size,
        },
    }
    for stream in ("stdout", "stderr"):
        if observed_raw[stream] != {
            "sha256": RAW_EVIDENCE[stream]["sha256"],
            "size": RAW_EVIDENCE[stream]["size"],
        }:
            raise CompatibilityEvidenceError(f"observed {stream} receipt drift")

    authoritative_boot = _boot_set(
        compiler_version="9.10.3",
        dump_path=authoritative_boot_dump,
        dump_path_id="GHC_9_10_3_GLOBAL_PACKAGE_DUMP",
        pantry_db=pantry_db,
        bindist_sha256=AUTHORITATIVE_BINDIST_SHA256,
    )
    compatibility_boot = _boot_set(
        compiler_version="9.14.1",
        dump_path=compatibility_boot_dump,
        dump_path_id="GHC_9_14_1_GLOBAL_PACKAGE_DUMP",
        pantry_db=pantry_db,
        bindist_sha256=COMPATIBILITY_BINDIST_SHA256,
    )
    failure_partial_plan_sha256 = canonical_sha256(FAILURE_LEAF)
    return {
        "bootSets": {
            "authoritative": authoritative_boot,
            "compatibility": compatibility_boot,
        },
        "candidateSourceTree": {
            "entryCount": 29,
            "generatedCabalSha256": (
                "b7d0eaab5149666124717378ef8bcfad0e533f47b8d9f555fdae61d18d509428"
            ),
            "historicalCommit": "7eedbf152bb7178e16bf05cc9452cf42dec9c09e",
            "sha256": CANDIDATE_SOURCE_TREE_SHA256,
        },
        "classification": "FAIL_FROZEN_DEPENDENCY",
        "downstream": dict(DOWNSTREAM_NOT_RUN),
        "execution": _historical_execution(),
        "failureLeaf": FAILURE_LEAF,
        "fallbackProof": {
            "authoritativeDirectNonBootParents": DIRECT_NON_BOOT_PARENTS,
            "authoritativeNonBootPlanSha256": DIRECT_PARENT_MANIFEST_SHA256,
            "authoritativePackageSetSha256": (
                "f9f775487e1678844b9f031919ed508a88097aa883002a970d3cd6fc6308b5d2"
            ),
            "compatibilityDirectNonBootParents": DIRECT_NON_BOOT_PARENTS,
            "compatibilityNonBootPlanSha256": DIRECT_PARENT_MANIFEST_SHA256,
            "configurationAst": CONFIGURATION_AST,
            "configurationAstExcludingCompilerSha256": CONFIGURATION_AST_SHA256,
            "directNonBootParentManifestSha256": DIRECT_PARENT_MANIFEST_SHA256,
            "failedPartialPlanSha256": failure_partial_plan_sha256,
            "fullCompatibilityPlanAvailable": False,
            "proofMode": "DIRECT_PARENT_AND_CONFIG_AST_FALLBACK",
            "scope": (
                "direct non-boot parents and configuration AST excluding compiler; "
                "not a full compatibility plan"
            ),
        },
        "frozenInputs": FROZEN_INPUTS,
        "laneId": "ghc-9.14.1-non-scoring",
        "rawEvidence": RAW_EVIDENCE,
        "schemaVersion": "s1.4x-ghc-compatibility-solve-failure-v1",
    }


def _assert_portable(value: Any) -> None:
    forbidden_fragments = (
        "/" + "home" + "/",
        "/mnt/" + "c/Users/",
    )

    def visit(item: Any) -> None:
        if isinstance(item, str):
            if (
                item.startswith("/")
                or re.match(r"^[A-Za-z]:[\\/]", item) is not None
                or any(fragment in item for fragment in forbidden_fragments)
            ):
                raise CompatibilityEvidenceError("tracked evidence must be portable")
        elif isinstance(item, list):
            for nested in item:
                visit(nested)
        elif isinstance(item, dict):
            for nested in item.values():
                visit(nested)

    visit(value)


def _validate_boot_set(
    boot_set: Any,
    *,
    label: str,
    compiler_version: str,
    package_count: int,
    hackage_source_count: int,
    raw_dump_sha256: str,
    raw_dump_size: int,
    manifest_sha256: str,
    bindist_sha256: str,
) -> None:
    if not isinstance(boot_set, dict) or set(boot_set) != {
        "bindistFallbackCount",
        "compilerVersion",
        "hackageSourceCount",
        "manifestSha256",
        "packageCount",
        "packages",
        "rawDump",
    }:
        raise CompatibilityEvidenceError(f"{label} boot set field drift")
    packages = boot_set["packages"]
    if not isinstance(packages, list) or any(
        not isinstance(item, dict)
        or set(item) != {"package", "sourceSha256", "unitId", "version"}
        for item in packages
    ):
        raise CompatibilityEvidenceError(f"{label} boot set package shape drift")
    expected_order = sorted(
        packages,
        key=lambda item: (
            str(item["package"]).encode(),
            str(item["version"]).encode(),
            str(item["unitId"]).encode(),
        ),
    )
    if packages != expected_order or len({item["unitId"] for item in packages}) != len(
        packages
    ):
        raise CompatibilityEvidenceError(f"{label} boot set identity order drift")
    fallback_count = sum(
        item.get("sourceSha256") == bindist_sha256 for item in packages
    )
    expected = {
        "bindistFallbackCount": package_count - hackage_source_count,
        "compilerVersion": compiler_version,
        "hackageSourceCount": hackage_source_count,
        "manifestSha256": manifest_sha256,
        "packageCount": package_count,
        "rawDump": {
            "pathId": (
                "GHC_9_10_3_GLOBAL_PACKAGE_DUMP"
                if label == "authoritative"
                else "GHC_9_14_1_GLOBAL_PACKAGE_DUMP"
            ),
            "sha256": raw_dump_sha256,
            "size": raw_dump_size,
        },
    }
    observed = {key: boot_set[key] for key in expected}
    if observed != expected or fallback_count != package_count - hackage_source_count:
        raise CompatibilityEvidenceError(f"{label} boot set metadata drift")
    if canonical_sha256(packages) != manifest_sha256:
        raise CompatibilityEvidenceError(f"{label} boot set SHA-256 drift")


def validate_failure_evidence(evidence: Any) -> dict[str, Any]:
    """Companion을 관측된 failure leaf와 exact portable object로 검증한다."""

    if not isinstance(evidence, dict) or set(evidence) != OUTER_FIELDS:
        raise CompatibilityEvidenceError("compatibility evidence outer field set drift")
    _assert_portable(evidence)
    if (
        evidence["schemaVersion"]
        != "s1.4x-ghc-compatibility-solve-failure-v1"
        or evidence["laneId"] != "ghc-9.14.1-non-scoring"
        or evidence["classification"] != "FAIL_FROZEN_DEPENDENCY"
    ):
        raise CompatibilityEvidenceError("compatibility evidence identity drift")
    if evidence["rawEvidence"] != RAW_EVIDENCE:
        raise CompatibilityEvidenceError("raw evidence receipt drift")
    if evidence["failureLeaf"].get("prunedBootPackages") != FAILURE_LEAF[
        "prunedBootPackages"
    ]:
        raise CompatibilityEvidenceError("pruned boot package set drift")
    if evidence["failureLeaf"].get("suggestedExtraDeps") != FAILURE_LEAF[
        "suggestedExtraDeps"
    ]:
        raise CompatibilityEvidenceError("suggested extra-dep set drift")
    if evidence["failureLeaf"] != FAILURE_LEAF:
        raise CompatibilityEvidenceError("Stack failure leaf drift")
    if evidence["frozenInputs"] != FROZEN_INPUTS:
        raise CompatibilityEvidenceError("frozen compatibility input drift")
    if evidence["downstream"] != DOWNSTREAM_NOT_RUN:
        raise CompatibilityEvidenceError("downstream NOT_RUN closure drift")

    fallback = evidence["fallbackProof"]
    if not isinstance(fallback, dict) or set(fallback) != {
        "authoritativeDirectNonBootParents",
        "authoritativeNonBootPlanSha256",
        "authoritativePackageSetSha256",
        "compatibilityDirectNonBootParents",
        "compatibilityNonBootPlanSha256",
        "configurationAst",
        "configurationAstExcludingCompilerSha256",
        "directNonBootParentManifestSha256",
        "failedPartialPlanSha256",
        "fullCompatibilityPlanAvailable",
        "proofMode",
        "scope",
    }:
        raise CompatibilityEvidenceError("fallback proof field drift")
    if fallback["fullCompatibilityPlanAvailable"] is not False:
        raise CompatibilityEvidenceError("full compatibility plan must remain unavailable")
    if (
        fallback["authoritativeDirectNonBootParents"] != DIRECT_NON_BOOT_PARENTS
        or fallback["compatibilityDirectNonBootParents"] != DIRECT_NON_BOOT_PARENTS
        or canonical_sha256(DIRECT_NON_BOOT_PARENTS)
        != fallback["directNonBootParentManifestSha256"]
        or fallback["authoritativeNonBootPlanSha256"]
        != DIRECT_PARENT_MANIFEST_SHA256
        or fallback["compatibilityNonBootPlanSha256"]
        != DIRECT_PARENT_MANIFEST_SHA256
    ):
        raise CompatibilityEvidenceError("direct non-boot parent manifest drift")
    if (
        fallback["configurationAst"] != CONFIGURATION_AST
        or canonical_sha256(CONFIGURATION_AST)
        != fallback["configurationAstExcludingCompilerSha256"]
    ):
        raise CompatibilityEvidenceError("configuration AST fallback proof drift")
    if fallback["failedPartialPlanSha256"] != canonical_sha256(FAILURE_LEAF):
        raise CompatibilityEvidenceError("failed partial plan SHA-256 drift")

    boot_sets = evidence["bootSets"]
    if not isinstance(boot_sets, dict) or set(boot_sets) != {
        "authoritative",
        "compatibility",
    }:
        raise CompatibilityEvidenceError("boot set partition drift")
    _validate_boot_set(
        boot_sets["authoritative"],
        label="authoritative",
        compiler_version="9.10.3",
        package_count=42,
        hackage_source_count=37,
        raw_dump_sha256=(
            "53ed8cdf3369f8c55ea5cf534c2d837792851bf25caff0fc4f28fa8cfcdd913f"
        ),
        raw_dump_size=148331,
        manifest_sha256=AUTHORITATIVE_BOOT_SET_SHA256,
        bindist_sha256=AUTHORITATIVE_BINDIST_SHA256,
    )
    _validate_boot_set(
        boot_sets["compatibility"],
        label="compatibility",
        compiler_version="9.14.1",
        package_count=47,
        hackage_source_count=39,
        raw_dump_sha256=(
            "944041ae811627539753a4ac532db2d2e075ff480f6fe0c56e785902085c26be"
        ),
        raw_dump_size=159009,
        manifest_sha256=COMPATIBILITY_BOOT_SET_SHA256,
        bindist_sha256=COMPATIBILITY_BINDIST_SHA256,
    )
    if evidence["execution"] != _historical_execution():
        raise CompatibilityEvidenceError("historical/canonical command metadata drift")
    if evidence["candidateSourceTree"] != {
        "entryCount": 29,
        "generatedCabalSha256": (
            "b7d0eaab5149666124717378ef8bcfad0e533f47b8d9f555fdae61d18d509428"
        ),
        "historicalCommit": "7eedbf152bb7178e16bf05cc9452cf42dec9c09e",
        "sha256": CANDIDATE_SOURCE_TREE_SHA256,
    }:
        raise CompatibilityEvidenceError("historical candidate source tree drift")
    return evidence


def _phase(status: str, evidence_sha256: str | None) -> dict[str, Any]:
    return {"evidenceSha256": evidence_sha256, "status": status}


def _replay_not_run() -> dict[str, Any]:
    return {"evidenceSha256": None, "mismatchCount": None, "status": "NOT_RUN"}


def build_result(evidence: Mapping[str, Any]) -> dict[str, Any]:
    """Validated companion에서 schema-valid frozen dependency result를 투영한다."""

    validate_failure_evidence(dict(evidence))
    evidence_sha256 = hashlib.sha256(
        canonical_json_bytes(evidence, trailing_newline=True)
    ).hexdigest()
    frozen = evidence["frozenInputs"]
    fallback = evidence["fallbackProof"]
    execution = evidence["execution"]
    boot_sets = evidence["bootSets"]
    return {
        "authoritativeBootSetSha256": boot_sets["authoritative"][
            "manifestSha256"
        ],
        "authoritativeNonBootPlanSha256": fallback[
            "authoritativeNonBootPlanSha256"
        ],
        "authoritativePackageSetSha256": fallback[
            "authoritativePackageSetSha256"
        ],
        "authoritativeStackLockSha256": frozen["authoritativeStackLock"][
            "sha256"
        ],
        "authoritativeStackYamlSha256": frozen["authoritativeStackYaml"][
            "sha256"
        ],
        "candidateCompile": _phase("NOT_RUN", None),
        "candidateSourceTreeSha256": evidence["candidateSourceTree"]["sha256"],
        "commands": [
            {
                "argv": execution["historicalCommand"]["argv"],
                "cwdId": execution["historicalCommand"]["cwdId"],
                "endedAt": execution["endedAt"],
                "exitCode": execution["exitCode"],
                "phase": "dependency",
                "startedAt": execution["startedAt"],
                "stderrSha256": evidence["rawEvidence"]["stderr"]["sha256"],
                "stdoutSha256": evidence["rawEvidence"]["stdout"]["sha256"],
            }
        ],
        "compatibilityBootSetSha256": boot_sets["compatibility"][
            "manifestSha256"
        ],
        "compatibilityNonBootPlanSha256": fallback[
            "compatibilityNonBootPlanSha256"
        ],
        "compatibilityPolicySha256": frozen["compatibilityPolicy"]["sha256"],
        "compatibilityStackLockSha256": frozen["compatibilityStackLock"][
            "sha256"
        ],
        "compatibilityStackYamlSha256": frozen["compatibilityStackYaml"][
            "sha256"
        ],
        "compilerPathId": "GHCUP_GHC_9_14_1",
        "compilerSha256": frozen["toolchain"]["compatibilityCompiler"][
            "sha256"
        ],
        "compilerVersion": "9.14.1",
        "configurationQualification": _phase(
            "PASS",
            canonical_sha256(fallback["configurationAst"]),
        ),
        "crossReplay": _replay_not_run(),
        "dependencyQualification": _phase("FAIL", evidence_sha256),
        "downstreamNotRun": [
            "candidateCompile",
            "fullCorrectness",
            "stableErrorReplay",
            "processReplay",
            "oracleReplay",
            "crossReplay",
        ],
        "expectedBootSetDifferenceOnly": True,
        "failurePhase": "dependency",
        "forbiddenOverrideKeysPresent": [],
        "fullCorrectness": _replay_not_run(),
        "ghcupMetadataCommit": "0341867f2d419567cf42ea6931e031b00ab3a922",
        "ghcupMetadataUri": (
            "https://github.com/haskell/ghcup-metadata/commit/"
            "0341867f2d419567cf42ea6931e031b00ab3a922"
        ),
        "ghcupSha256": (
            "9ed5da5449b48043a0d17e767c05d2ef585e25a639bb934329496c6d2fad9cf8"
        ),
        "ghcupToolId": "GHCUP_0_2_6_2_LINUX_X86_64",
        "ghcupVersion": "0.2.6.2",
        "laneId": "ghc-9.14.1-non-scoring",
        "minimalReproducerSha256": evidence_sha256,
        "nonBootPlanEquivalent": True,
        "nonScoring": True,
        "oracleReplay": _replay_not_run(),
        "performanceInput": False,
        "processReplay": _replay_not_run(),
        "result": "FAIL_FROZEN_DEPENDENCY",
        "schemaVersion": "s1.4x-ghc-compatibility-result-v1",
        "stableErrorReplay": _replay_not_run(),
        "stackArchiveSha256": (
            "ca3cc5e89d87d1b85594a866de4062671d19ec039cd2401df70d4ccff03ffed9"
        ),
        "stackArchiveUri": (
            "https://downloads.haskell.org/~ghcup/unofficial-bindists/"
            "stack/3.11.1/stack-3.11.1-linux-x86_64.tar.gz"
        ),
        "stackBinPathId": "GHCUP_STACK_3_11_1",
        "stackBinSha256": (
            "923dbd137756652c67b376e2447c655b87fcc373f4d104b5073bca913471ecbe"
        ),
        "stackDistributionChannel": "ghcup-managed",
        "stackInstallCommand": "ghcup install stack 3.11.1",
        "stackNumericVersion": "3.11.1",
        "stackPolicy": "GHCup-managed exact-version installation",
        "toolchainProvenanceSha256": frozen["toolchain"][
            "toolchainProvenanceSha256"
        ],
        "toolchainQualification": _phase(
            "PASS",
            canonical_sha256(frozen["toolchain"]),
        ),
        "upstreamStandaloneAssetRole": (
            "comparison-only-not-installed-provenance"
        ),
        "upstreamStandaloneAssetSha256": (
            "67c66e918801c41ae4d286b1c91f9124f691c1c7d56071b53889cf4a5c667550"
        ),
    }


def validate_result_binding(
    result: Any,
    evidence: Mapping[str, Any],
) -> dict[str, Any]:
    """Typed result가 companion bytes를 minimal reproducer로 exact 결속하는지 검증한다."""

    if not isinstance(result, dict):
        raise CompatibilityEvidenceError("compatibility result must be an object")
    expected = build_result(evidence)
    if result.get("minimalReproducerSha256") != expected[
        "minimalReproducerSha256"
    ]:
        raise CompatibilityEvidenceError("minimal reproducer SHA-256 drift")
    if result != expected:
        raise CompatibilityEvidenceError("typed compatibility result projection drift")
    _assert_portable(result)
    return result


def _capture_command(arguments: argparse.Namespace) -> None:
    evidence = build_failure_evidence(
        authoritative_boot_dump=arguments.authoritative_boot_dump,
        compatibility_boot_dump=arguments.compatibility_boot_dump,
        pantry_db=arguments.pantry_db,
        stdout_path=arguments.stdout,
        stderr_path=arguments.stderr,
    )
    validate_failure_evidence(evidence)
    atomic_write_json(arguments.evidence, evidence)
    result = build_result(evidence)
    atomic_write_json(arguments.result, result)
    print(
        json.dumps(
            {
                "evidenceSha256": sha256_file(arguments.evidence),
                "resultSha256": sha256_file(arguments.result),
                "status": "PASS",
            },
            sort_keys=True,
        )
    )


def _validate_command(arguments: argparse.Namespace) -> None:
    evidence = strict_json_load(arguments.evidence)
    validate_failure_evidence(evidence)
    if arguments.evidence.read_bytes() != canonical_json_bytes(
        evidence,
        trailing_newline=True,
    ):
        raise CompatibilityEvidenceError("failure evidence is not canonical JSON")
    result = strict_json_load(arguments.result)
    validate_result_binding(result, evidence)
    if arguments.result.read_bytes() != canonical_json_bytes(
        result,
        trailing_newline=True,
    ):
        raise CompatibilityEvidenceError("compatibility result is not canonical JSON")
    print(
        json.dumps(
            {
                "evidenceSha256": sha256_file(arguments.evidence),
                "resultSha256": sha256_file(arguments.result),
                "status": "PASS",
            },
            sort_keys=True,
        )
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    capture = commands.add_parser("capture")
    capture.add_argument("--authoritative-boot-dump", type=Path, required=True)
    capture.add_argument("--compatibility-boot-dump", type=Path, required=True)
    capture.add_argument("--pantry-db", type=Path, required=True)
    capture.add_argument("--stdout", type=Path, required=True)
    capture.add_argument("--stderr", type=Path, required=True)
    capture.add_argument("--evidence", type=Path, required=True)
    capture.add_argument("--result", type=Path, required=True)
    capture.set_defaults(handler=_capture_command)

    validate = commands.add_parser("validate")
    validate.add_argument("--evidence", type=Path, required=True)
    validate.add_argument("--result", type=Path, required=True)
    validate.set_defaults(handler=_validate_command)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        arguments.handler(arguments)
    except (
        CompatibilityEvidenceError,
        OSError,
        UnicodeError,
        ValueError,
        sqlite3.DatabaseError,
    ) as exc:
        print(f"COMPATIBILITY_EVIDENCE_FAIL:{exc}", file=os.sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
