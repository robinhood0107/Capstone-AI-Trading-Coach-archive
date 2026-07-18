#!/usr/bin/env python3
"""Pinned Scalafix와 Scala 3 SemanticDB를 실행하고 byte-bound receipt를 만든다."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any

from source_input_manifest import validated_source_files as source_files


class SemanticPolicyError(ValueError):
    """Semantic source-policy evidence를 완전하게 만들 수 없음을 나타낸다."""


SHA256 = re.compile(r"^[0-9a-f]{64}$")
ANSI = re.compile(r"\x1b\[[0-9;]*m")
DIAGNOSTIC = re.compile(
    r"forbidden semantic symbol: ([^ \r\n]+) resolved=([^ \r\n]+)"
)


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return sha256_bytes(payload)


def tree_sha256(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise SemanticPolicyError(f"SYMLINK_IN_EVIDENCE_TREE:{path}")
        if path.is_file():
            relative = path.relative_to(root).as_posix().encode("utf-8")
            digest.update(relative)
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
    return digest.hexdigest()


def utc_now() -> str:
    return (
        dt.datetime.now(dt.timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def strict_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as stream:
        value = json.load(
            stream,
            parse_constant=lambda token: (_ for _ in ()).throw(
                SemanticPolicyError(f"NONFINITE_JSON:{token}")
            ),
        )
    if not isinstance(value, dict):
        raise SemanticPolicyError(f"JSON_OBJECT_REQUIRED:{path}")
    return value


def write_exclusive_json(path: Path, value: Any) -> None:
    payload = (
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    )
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(payload)


def write_exclusive_bytes(path: Path, payload: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(payload)


def run_process(
    command: list[str],
    *,
    cwd: Path,
    environment: dict[str, str],
    log_root: Path,
    log_id: str,
) -> subprocess.CompletedProcess[bytes]:
    completed = subprocess.run(
        command,
        cwd=cwd,
        env=environment,
        check=False,
        capture_output=True,
    )
    write_exclusive_bytes(log_root / f"{log_id}.stdout", completed.stdout)
    write_exclusive_bytes(log_root / f"{log_id}.stderr", completed.stderr)
    return completed


def fixture_path(fixture_root: Path, file_name: str) -> Path:
    candidate = (fixture_root / file_name).resolve(strict=True)
    if (
        candidate.parent != fixture_root.resolve(strict=True)
        or candidate.is_symlink()
        or not candidate.is_file()
    ):
        raise SemanticPolicyError(f"UNSAFE_NEGATIVE_FIXTURE:{file_name}")
    return candidate


def parse_classpath(stdout: bytes) -> tuple[str, list[str]]:
    lines = [
        ANSI.sub("", line).strip()
        for line in stdout.decode("utf-8", errors="strict").splitlines()
        if line.strip()
    ]
    candidates = [line for line in lines if os.pathsep in line or Path(line).exists()]
    if not candidates:
        raise SemanticPolicyError("SEMANTIC_CLASSPATH_MISSING")
    classpath = candidates[-1]
    entries = classpath.split(os.pathsep)
    if not entries or any(not Path(entry).is_absolute() for entry in entries):
        raise SemanticPolicyError("SEMANTIC_CLASSPATH_NOT_ABSOLUTE")
    if any(not Path(entry).exists() for entry in entries):
        raise SemanticPolicyError("SEMANTIC_CLASSPATH_ENTRY_MISSING")
    return classpath, entries


def extract_policy_symbols(rule_source: Path) -> list[str]:
    source = rule_source.read_text(encoding="utf-8")
    match = re.search(
        r"policyForbiddenSymbols:\s*List\[String\]\s*=\s*List\((.*?)\n\s*\)",
        source,
        flags=re.DOTALL,
    )
    if match is None:
        raise SemanticPolicyError("RULE_POLICY_SYMBOL_BLOCK_MISSING")
    return re.findall(r'"([^"]+)"', match.group(1))


def scalafix_rule_classpath_sha256(scalafix: Path) -> str:
    with zipfile.ZipFile(scalafix) as archive:
        lock = archive.read("META-INF/coursier/lock-file")
    return sha256_bytes(lock)


def process_evidence(
    command: list[str],
    completed: subprocess.CompletedProcess[bytes],
) -> dict[str, Any]:
    value = {
        "commandArgvSha256": canonical_sha256(command),
        "exitCode": completed.returncode,
        "stdoutSha256": sha256_bytes(completed.stdout),
        "stderrSha256": sha256_bytes(completed.stderr),
    }
    return {**value, "evidenceSha256": canonical_sha256(value)}


def clean_semantic_commands(
    *,
    scalafix: Path,
    scala_root: Path,
    scalafix_config: Path,
    rule_source: Path,
    classpath: str,
    semanticdb_root: Path,
    sources: list[Path],
) -> dict[str, list[str]]:
    """Built-in semantic rule과 custom source rule을 별도 process로 강제한다."""

    common = [
        str(scalafix),
        "--check",
        "--scala-version",
        "3.8.4",
        "--classpath",
        classpath,
        "--sourceroot",
        str(scala_root),
        "--semanticdb-targetroots",
        str(semanticdb_root),
        "--config",
        str(scalafix_config),
    ]
    files = ["--files", *map(str, sources)]
    return {
        "explicit-result-types": [
            *common,
            "--rules",
            "ExplicitResultTypes",
            *files,
        ],
        "custom-rule": [
            *common,
            "--rules",
            f"file:{rule_source}",
            *files,
        ],
    }


def validate_paths(arguments: argparse.Namespace) -> None:
    for name in ("scala_root", "policy", "fixture_matrix", "scala_cli", "scalafix"):
        path = getattr(arguments, name)
        if not path.is_absolute() or path.is_symlink():
            raise SemanticPolicyError(f"UNSAFE_INPUT_PATH:{name}")
    if (
        not arguments.scala_root.is_dir()
        or not arguments.policy.is_file()
        or not arguments.fixture_matrix.is_file()
        or not arguments.scala_cli.is_file()
        or not arguments.scalafix.is_file()
    ):
        raise SemanticPolicyError("INPUT_PATH_MISSING")
    if (
        not arguments.output_dir.is_absolute()
        or arguments.output_dir.exists()
        or arguments.output_dir.is_symlink()
    ):
        raise SemanticPolicyError("OUTPUT_DIRECTORY_MUST_BE_NEW")


def run(arguments: argparse.Namespace) -> Path:
    validate_paths(arguments)
    scala_root = arguments.scala_root.resolve(strict=True)
    policy_path = arguments.policy.resolve(strict=True)
    fixture_matrix_path = arguments.fixture_matrix.resolve(strict=True)
    scala_cli = arguments.scala_cli.resolve(strict=True)
    scalafix = arguments.scalafix.resolve(strict=True)
    output_root = arguments.output_dir
    manifest_path = scala_root / "source-inputs.v1.json"
    scalafix_config = scala_root / ".scalafix.conf"
    rule_source = scala_root / "tools/scalafix/S1_4XForbiddenSymbols.scala"
    for required in (manifest_path, scalafix_config, rule_source):
        if required.is_symlink() or not required.is_file():
            raise SemanticPolicyError(f"REQUIRED_FILE_MISSING:{required}")

    policy = strict_json(policy_path)
    manifest = strict_json(manifest_path)
    matrix = strict_json(fixture_matrix_path)
    expected_symbols = policy.get("forbiddenFullyQualifiedSymbols")
    if (
        policy.get("schemaVersion") != "s1.4x-scala-source-policy-v1"
        or not isinstance(expected_symbols, list)
        or extract_policy_symbols(rule_source) != expected_symbols
    ):
        raise SemanticPolicyError("SEMANTIC_RULE_POLICY_CLOSURE_MISMATCH")
    fixtures = matrix.get("fixtures")
    if (
        matrix.get("schemaVersion")
        != "s1.4x-scala-source-policy-negative-matrix-v1"
        or not isinstance(fixtures, list)
        or len(fixtures) != len(
            {
                item.get("fixtureId")
                for item in fixtures
                if isinstance(item, dict)
            }
        )
    ):
        raise SemanticPolicyError("NEGATIVE_FIXTURE_MATRIX_INVALID")

    sources = source_files(
        scala_root,
        manifest,
        policy=policy,
        require_git_source_equality=True,
    )
    checked_files = [
        path.relative_to(scala_root).as_posix() for path in sources
    ]
    output_root.mkdir()
    log_root = output_root / "logs"
    semanticdb_root = output_root / "semanticdb"
    negative_semanticdb_root = output_root / "negative-semanticdb"
    log_root.mkdir()
    semanticdb_root.mkdir()
    negative_semanticdb_root.mkdir()

    environment = os.environ.copy()
    environment["NO_COLOR"] = "1"
    started_at = utc_now()

    syntactic_command = [
        str(scalafix),
        "--check",
        "--syntactic",
        "--scala-version",
        "3.8.4",
        "--config",
        str(scalafix_config),
        "--rules",
        "DisableSyntax",
        "--files",
        *map(str, sources),
    ]
    syntactic = run_process(
        syntactic_command,
        cwd=scala_root,
        environment=environment,
        log_root=log_root,
        log_id="clean-syntactic",
    )
    if syntactic.returncode != 0:
        raise SemanticPolicyError("CLEAN_DISABLE_SYNTAX_FAILED")

    compile_command = [
        str(scala_cli),
        "--power",
        "compile",
        str(scala_root / "project.scala"),
        str(scala_root / "selected-profile.scala"),
        str(scala_root / "src"),
        str(scala_root / "benchmarks"),
        "--test",
        "--server=false",
        "--jvm",
        "system",
        "--coursier-validate-checksums",
        "--semanticdb",
        "--semanticdb-targetroot",
        str(semanticdb_root),
        "--semanticdb-sourceroot",
        str(scala_root),
        "--print-classpath",
    ]
    compiled = run_process(
        compile_command,
        cwd=scala_root,
        environment=environment,
        log_root=log_root,
        log_id="clean-semanticdb-compile",
    )
    if compiled.returncode != 0:
        raise SemanticPolicyError("CLEAN_SEMANTICDB_COMPILE_FAILED")
    classpath, classpath_entries = parse_classpath(compiled.stdout)
    write_exclusive_bytes(
        output_root / "semantic-classpath.txt",
        (classpath + "\n").encode("utf-8"),
    )
    semanticdb_files = sorted(semanticdb_root.rglob("*.semanticdb"))
    if not semanticdb_files or any(path.is_symlink() for path in semanticdb_files):
        raise SemanticPolicyError("SEMANTICDB_OUTPUT_MISSING")

    semantic_commands = clean_semantic_commands(
        scalafix=scalafix,
        scala_root=scala_root,
        scalafix_config=scalafix_config,
        rule_source=rule_source,
        classpath=classpath,
        semanticdb_root=semanticdb_root,
        sources=sources,
    )
    explicit_result_types_command = semantic_commands["explicit-result-types"]
    explicit_result_types = run_process(
        explicit_result_types_command,
        cwd=scala_root,
        environment=environment,
        log_root=log_root,
        log_id="clean-explicit-result-types",
    )
    if explicit_result_types.returncode != 0:
        raise SemanticPolicyError("CLEAN_EXPLICIT_RESULT_TYPES_FAILED")
    custom_rule_command = semantic_commands["custom-rule"]
    custom_rule = run_process(
        custom_rule_command,
        cwd=scala_root,
        environment=environment,
        log_root=log_root,
        log_id="clean-custom-semantic-rule",
    )
    if custom_rule.returncode != 0:
        raise SemanticPolicyError("CLEAN_CUSTOM_SEMANTIC_RULE_FAILED")

    semantic_fixtures = [
        item
        for item in fixtures
        if item.get("expectedDisposition") == "SEMANTIC_REJECT"
    ]
    semantic_fixture_paths = [
        fixture_path(fixture_matrix_path.parent, str(item.get("file")))
        for item in semantic_fixtures
    ]
    negative_compile_command = [
        str(scala_cli),
        "--power",
        "compile",
        *map(str, semantic_fixture_paths),
        "--server=false",
        "--scala",
        "3.8.4",
        "--jvm",
        "system",
        "--coursier-validate-checksums",
        "--scalac-option=-release:25",
        "--semanticdb",
        "--semanticdb-targetroot",
        str(negative_semanticdb_root),
        "--semanticdb-sourceroot",
        str(fixture_matrix_path.parent),
        "--print-classpath",
    ]
    negative_compiled = run_process(
        negative_compile_command,
        cwd=scala_root,
        environment=environment,
        log_root=log_root,
        log_id="negative-semanticdb-compile",
    )
    if negative_compiled.returncode != 0:
        raise SemanticPolicyError("NEGATIVE_SEMANTICDB_COMPILE_FAILED")
    negative_classpath, _ = parse_classpath(negative_compiled.stdout)

    negative_results: list[dict[str, Any]] = []
    for item in fixtures:
        if not isinstance(item, dict):
            raise SemanticPolicyError("NEGATIVE_FIXTURE_ENTRY_INVALID")
        fixture_id = item.get("fixtureId")
        file_name = item.get("file")
        expected_symbol = item.get("expectedSymbol")
        disposition = item.get("expectedDisposition")
        if not all(
            isinstance(value, str) and value
            for value in (fixture_id, file_name, expected_symbol, disposition)
        ):
            raise SemanticPolicyError("NEGATIVE_FIXTURE_ENTRY_INVALID")
        selected_fixture = fixture_path(fixture_matrix_path.parent, file_name)
        if disposition == "SYNTAX_REJECT":
            command = [
                str(scalafix),
                "--check",
                "--syntactic",
                "--scala-version",
                "3.8.4",
                "--config",
                str(scalafix_config),
                "--rules",
                "DisableSyntax",
                "--files",
                str(selected_fixture),
            ]
        elif disposition == "COMPILE_REJECT":
            command = [
                str(scala_cli),
                "--power",
                "compile",
                str(selected_fixture),
                "--server=false",
                "--scala",
                "3.8.4",
                "--jvm",
                "system",
                "--coursier-validate-checksums",
                "--scalac-option=-release:25",
            ]
        elif disposition == "SEMANTIC_REJECT":
            rule = (
                "ExplicitResultTypes"
                if expected_symbol == "ExplicitResultTypes"
                else f"file:{rule_source}"
            )
            command = [
                str(scalafix),
                "--check",
                "--scala-version",
                "3.8.4",
                "--classpath",
                negative_classpath,
                "--sourceroot",
                str(fixture_matrix_path.parent),
                "--semanticdb-targetroots",
                str(negative_semanticdb_root),
                "--config",
                str(scalafix_config),
                "--rules",
                rule,
                "--files",
                str(selected_fixture),
            ]
        else:
            raise SemanticPolicyError(f"UNKNOWN_NEGATIVE_DISPOSITION:{disposition}")

        completed = run_process(
            command,
            cwd=scala_root,
            environment=environment,
            log_root=log_root,
            log_id=f"negative-{fixture_id}",
        )
        combined = (completed.stdout + b"\n" + completed.stderr).decode(
            "utf-8", errors="replace"
        )
        detected = [
            {"policySymbol": policy_symbol, "resolvedSymbol": resolved_symbol}
            for policy_symbol, resolved_symbol in DIAGNOSTIC.findall(combined)
        ]
        if disposition == "SEMANTIC_REJECT" and expected_symbol != "ExplicitResultTypes":
            detected_expected = any(
                value["policySymbol"] == expected_symbol for value in detected
            )
        elif disposition == "SEMANTIC_REJECT":
            detected_expected = (
                "ExplicitResultTypes" in combined
                or "def inferred:" in combined
                or "+  def inferred:" in combined
            )
        elif disposition == "COMPILE_REJECT":
            detected_expected = (
                "missingFunction" in combined
                and ("Not found" in combined or "Not Found" in combined)
            )
        else:
            detected_expected = expected_symbol in combined
        if completed.returncode == 0 or not detected_expected:
            raise SemanticPolicyError(f"NEGATIVE_FIXTURE_NOT_DETECTED:{fixture_id}")
        evidence = process_evidence(command, completed)
        negative_results.append(
            {
                "fixtureId": fixture_id,
                "expectedPolicySymbol": expected_symbol,
                "expectedDisposition": disposition,
                "detectedResolvedSymbols": detected,
                **evidence,
                "status": "PASS",
            }
        )

    finished_at = utc_now()
    receipt = {
        "schemaVersion": "s1.4x-scala-semantic-policy-receipt-v1",
        "policySha256": sha256_file(policy_path),
        "sourceInputManifestSha256": sha256_file(manifest_path),
        "checkedFiles": checked_files,
        "sourceTreeSha256": canonical_sha256(
            [
                {
                    "path": path.relative_to(scala_root).as_posix(),
                    "sha256": sha256_file(path),
                }
                for path in sources
            ]
        ),
        "checkerMode": "semanticdb",
        "semanticSmokeStatus": "PASS",
        "semanticdb": {
            "rootPath": str(semanticdb_root),
            "rootSha256": tree_sha256(semanticdb_root),
            "fileCount": len(semanticdb_files),
            "classpathSha256": canonical_sha256(classpath_entries),
            "compileCommandArgvSha256": canonical_sha256(compile_command),
        },
        "scalafix": {
            "binaryPath": str(scalafix),
            "binarySha256": sha256_file(scalafix),
            "version": "0.14.7",
            "commandArgvSha256": canonical_sha256(semantic_commands),
            "explicitResultTypesCommandArgvSha256": canonical_sha256(
                explicit_result_types_command
            ),
            "customRuleCommandArgvSha256": canonical_sha256(custom_rule_command),
            "syntacticCommandArgvSha256": canonical_sha256(syntactic_command),
        },
        "rule": {
            "sourcePath": str(rule_source),
            "sourceSha256": sha256_file(rule_source),
            "classpathSha256": scalafix_rule_classpath_sha256(scalafix),
        },
        "execution": {
            "startedAt": started_at,
            "finishedAt": finished_at,
            "cleanSyntactic": process_evidence(syntactic_command, syntactic),
            "cleanExplicitResultTypes": process_evidence(
                explicit_result_types_command,
                explicit_result_types,
            ),
            "cleanCustomSemanticRule": process_evidence(
                custom_rule_command,
                custom_rule,
            ),
        },
        "negativeMatrix": negative_results,
        "status": "PASS",
    }
    receipt_path = output_root / "scala-semantic-policy-receipt.v1.json"
    write_exclusive_json(receipt_path, receipt)
    return receipt_path


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--scala-root", type=Path, required=True)
    value.add_argument("--policy", type=Path, required=True)
    value.add_argument("--fixture-matrix", type=Path, required=True)
    value.add_argument("--output-dir", type=Path, required=True)
    value.add_argument("--scala-cli", type=Path, required=True)
    value.add_argument("--scalafix", type=Path, required=True)
    return value


def main() -> int:
    arguments = parser().parse_args()
    try:
        receipt = run(arguments)
        print(f"SCALA_SEMANTIC_POLICY_PASS receipt={receipt}")
    except (OSError, SemanticPolicyError, UnicodeError, ValueError) as error:
        print(f"SCALA_SEMANTIC_POLICY_FAIL:{error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
