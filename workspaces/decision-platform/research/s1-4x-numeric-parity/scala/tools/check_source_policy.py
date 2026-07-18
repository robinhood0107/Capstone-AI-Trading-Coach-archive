#!/usr/bin/env python3
"""Conservative token and receiver audit for the frozen Scala source policy."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def strip_comments_and_literals(source: str) -> str:
    """Replace comments/string/char bodies with spaces while preserving line positions."""

    output: list[str] = []
    index = 0
    block_depth = 0
    state = "code"
    while index < len(source):
        current = source[index]
        following = source[index + 1] if index + 1 < len(source) else ""
        triple = source[index : index + 3]
        if state == "code":
            if current == "/" and following == "/":
                output.extend((" ", " "))
                index += 2
                state = "line-comment"
            elif current == "/" and following == "*":
                output.extend((" ", " "))
                index += 2
                state = "block-comment"
                block_depth = 1
            elif triple == '"""':
                output.extend((" ", " ", " "))
                index += 3
                state = "triple-string"
            elif current == '"':
                output.append(" ")
                index += 1
                state = "string"
            elif current == "'":
                output.append(" ")
                index += 1
                state = "char"
            else:
                output.append(current)
                index += 1
        elif state == "line-comment":
            output.append("\n" if current == "\n" else " ")
            index += 1
            if current == "\n":
                state = "code"
        elif state == "block-comment":
            if current == "/" and following == "*":
                output.extend((" ", " "))
                index += 2
                block_depth += 1
            elif current == "*" and following == "/":
                output.extend((" ", " "))
                index += 2
                block_depth -= 1
                if block_depth == 0:
                    state = "code"
            else:
                output.append("\n" if current == "\n" else " ")
                index += 1
        elif state == "triple-string":
            if triple == '"""':
                output.extend((" ", " ", " "))
                index += 3
                state = "code"
            else:
                output.append("\n" if current == "\n" else " ")
                index += 1
        elif state in {"string", "char"}:
            if current == "\\":
                output.append(" ")
                if following:
                    output.append("\n" if following == "\n" else " ")
                index += 2
            elif (state == "string" and current == '"') or (
                state == "char" and current == "'"
            ):
                output.append(" ")
                index += 1
                state = "code"
            else:
                output.append("\n" if current == "\n" else " ")
                index += 1
    return "".join(output)


def line_number(source: str, offset: int) -> int:
    return source.count("\n", 0, offset) + 1


def add_matches(
    violations: list[dict[str, Any]],
    relative: str,
    source: str,
    pattern: str,
    rule: str,
) -> None:
    for match in re.finditer(pattern, source, flags=re.MULTILINE):
        violations.append(
            {
                "file": relative,
                "line": line_number(source, match.start()),
                "rule": rule,
            }
        )


def collect_sources(scala_root: Path, manifest: dict[str, Any]) -> tuple[list[Path], list[str]]:
    files: list[Path] = []
    violations: list[str] = []
    for root_name in manifest["roots"]:
        root = scala_root / root_name
        if root.is_file():
            if root.is_symlink():
                violations.append(f"{root_name}:symlink-source")
            elif root.suffix == ".scala":
                files.append(root)
        elif root.is_dir():
            for candidate in sorted(path for path in root.rglob("*") if path.is_file()):
                if candidate.is_symlink():
                    violations.append(
                        f"{candidate.relative_to(scala_root).as_posix()}:symlink-source"
                    )
                elif candidate.suffix == ".scala":
                    files.append(candidate)
                elif candidate.suffix in {".java", ".kt", ".kts"}:
                    violations.append(
                        f"{candidate.relative_to(scala_root).as_posix()}:non-scala-source"
                    )
        else:
            violations.append(f"{root_name}:missing-source-root")
    unique = sorted(set(files), key=lambda path: path.relative_to(scala_root).as_posix())
    return unique, violations


def git_source_files(scala_root: Path, roots: list[str]) -> tuple[list[str], list[str]]:
    """Manifest roots의 tracked+untracked non-ignored Scala 입력을 Git에서 독립 열거한다."""

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
        return [], ["git-source-enumeration-failed"]
    files = sorted(
        {
            line
            for line in completed.stdout.splitlines()
            if line.endswith(".scala")
        }
    )
    return files, []


def audit_file(path: Path, scala_root: Path) -> list[dict[str, Any]]:
    relative = path.relative_to(scala_root).as_posix()
    original = path.read_text(encoding="utf-8")
    source = strip_comments_and_literals(original)
    violations: list[dict[str, Any]] = []

    for pattern, rule in {
        r"\bscalafix\s*:\s*(?:off|ok)\b": "suppression:scalafix",
        r"@SuppressWarnings\b": "suppression:SuppressWarnings",
    }.items():
        add_matches(violations, relative, original, pattern, rule)

    token_rules = {
        r"\bnull\b": "forbidden:null",
        r"\breturn\b": "forbidden:return",
        r"\basInstanceOf\b": "forbidden:asInstanceOf",
        r"\bisInstanceOf\b": "forbidden:isInstanceOf",
        r"\bthrow\b": "forbidden:throw",
        r"\?\?\?": "forbidden:unimplemented",
        r"\bFloat\b": "forbidden:Float",
        r"\btoFloat\b": "forbidden:toFloat",
        r"\bruntimeChecked\b": "forbidden:runtimeChecked",
        r"\bmacro\b": "forbidden:macro",
        r"\bquotes\b": "forbidden:quotes",
        r"\bvar\b": "mutation:var",
        r"\bwhile\b": "mutation:while",
        r"\bscala\.collection\.mutable\b|\bcollection\.mutable\b": "mutation:mutable",
        r"\bMath\.fma\b": "numeric:Math.fma",
        r"\bscala\.sys\.error\b": "partial:scala.sys.error",
        r"\bscala\.language\.implicitConversions\b": "feature:implicitConversions",
        r"\bgiven\s+Conversion\b": "feature:given-Conversion",
        r"\bimport\s+scala\.language\.experimental\b": "feature:experimental",
        r"@unchecked\b": "suppression:unchecked",
        r"@nowarn\b": "suppression:nowarn",
        r"\bSystem\.(?:load|loadLibrary)\b": "native:System-load",
        r"\bscala\.scalanative\b|\bLLVM\b|\bGraal\b|\bJNI\b": "native:forbidden",
    }
    for pattern, rule in token_rules.items():
        add_matches(violations, relative, source, pattern, rule)

    if relative.startswith("src/main/scala/") or relative.startswith("benchmarks/"):
        for pattern, rule in {
            r"(?<![\w.])require\s*\(": "partial:Predef.require",
            r"(?<![\w.])assert\s*\(": "partial:Predef.assert",
            r"(?<![\w.])assume\s*\(": "partial:Predef.assume",
            r"\.(?:head|tail|init|last|reduce|reduceLeft|reduceRight|maxBy|minBy|next)\b": (
                "partial:collection"
            ),
            r"(?<!\bmath)(?<!\bMath)\.(?:max|min)\b": "partial:collection-extrema",
            r"\bArray\s*\(": "partial:Array.apply",
        }.items():
            add_matches(violations, relative, source, pattern, rule)

        aliases: dict[str, str] = {}
        for match in re.finditer(
            r"\btype\s+([A-Za-z_]\w*)\s*=\s*(Map|Seq|Vector|Array|Option|Try|Either)\b",
            source,
        ):
            aliases[match.group(1)] = match.group(2)
        type_terms = ["Map", "Seq", "Vector", "Array", *map(re.escape, aliases)]
        type_names = "|".join(type_terms)
        receiver_pattern = re.compile(
            rf"\b(?:val|lazy\s+val)\s+([A-Za-z_]\w*)\s*:\s*(?:{type_names})\b"
        )
        receivers = {match.group(1) for match in receiver_pattern.finditer(source)}
        parameter_pattern = re.compile(
            rf"\b([A-Za-z_]\w*)\s*:\s*(?:{type_names})(?:\[[^\]]*\])?"
        )
        receivers.update(match.group(1) for match in parameter_pattern.finditer(source))
        method_names = {
            match.group(1)
            for match in re.finditer(r"\bdef\s+([A-Za-z_]\w*)\s*\(", source)
        }
        receivers.difference_update(method_names)
        for receiver in sorted(receivers):
            add_matches(
                violations,
                relative,
                source,
                rf"(?<!\.)\b{re.escape(receiver)}\s*\(",
                "partial:indexed-apply",
            )

        option_like = re.compile(
            r"\b(?:val|lazy\s+val|def)\s+([A-Za-z_]\w*)\s*:\s*"
            r"(?:Option|Try|Either)(?:\[[^\]]*\])?"
        )
        for match in option_like.finditer(source):
            receiver = re.escape(match.group(1))
            add_matches(
                violations,
                relative,
                source,
                rf"\b{receiver}\.get\b",
                "partial:unsafe-get",
            )
        add_matches(
            violations,
            relative,
            source,
            r"\.(?:toOption|find|collectFirst)\b[^\n]{0,120}\.get\b",
            "partial:unsafe-get",
        )

        string_receivers = {
            match.group(1)
            for match in re.finditer(r"\b([A-Za-z_]\w*)\s*:\s*String\b", source)
        }
        for receiver in sorted(string_receivers):
            add_matches(
                violations,
                relative,
                source,
                rf"\b{re.escape(receiver)}\.to(?:Int|Long|Double)\b",
                "partial:throwing-string-conversion",
            )

    if relative.startswith("benchmarks/"):
        allowed = {
            "@Benchmark",
            "@Setup(Level.Trial)",
            "@State(Scope.Benchmark)",
        }
        for line_index, line in enumerate(original.splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith("@") and stripped not in allowed:
                violations.append(
                    {
                        "file": relative,
                        "line": line_index,
                        "rule": f"jmh:annotation:{stripped}",
                    }
                )

    return violations


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scala-root", type=Path, required=True)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--semantic-receipt", type=Path)
    parser.add_argument("--require-git-source-equality", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def semantic_receipt_valid(
    receipt: dict[str, Any],
    *,
    policy_sha256: str,
    manifest_sha256: str,
    checked_files: list[str],
) -> bool:
    sha256_pattern = re.compile(r"^[0-9a-f]{64}$")
    semanticdb = receipt.get("semanticdb")
    scalafix = receipt.get("scalafix")
    rule = receipt.get("rule")
    negative = receipt.get("negativeMatrix")
    return (
        receipt.get("schemaVersion")
        == "s1.4x-scala-semantic-policy-receipt-v1"
        and receipt.get("policySha256") == policy_sha256
        and receipt.get("sourceInputManifestSha256") == manifest_sha256
        and receipt.get("checkedFiles") == checked_files
        and receipt.get("checkerMode") == "semanticdb"
        and receipt.get("semanticSmokeStatus") == "PASS"
        and receipt.get("status") == "PASS"
        and isinstance(semanticdb, dict)
        and type(semanticdb.get("fileCount")) is int
        and semanticdb["fileCount"] > 0
        and sha256_pattern.fullmatch(str(semanticdb.get("rootSha256"))) is not None
        and sha256_pattern.fullmatch(str(semanticdb.get("classpathSha256")))
        is not None
        and isinstance(scalafix, dict)
        and scalafix.get("version") == "0.14.7"
        and sha256_pattern.fullmatch(str(scalafix.get("binarySha256"))) is not None
        and sha256_pattern.fullmatch(str(scalafix.get("commandArgvSha256")))
        is not None
        and isinstance(rule, dict)
        and sha256_pattern.fullmatch(str(rule.get("sourceSha256"))) is not None
        and sha256_pattern.fullmatch(str(rule.get("classpathSha256"))) is not None
        and isinstance(negative, list)
        and len(negative) >= 1
        and all(
            isinstance(item, dict)
            and item.get("status") == "PASS"
            and sha256_pattern.fullmatch(str(item.get("evidenceSha256"))) is not None
            for item in negative
        )
    )


def main() -> int:
    arguments = parse_arguments()
    scala_root = arguments.scala_root.resolve()
    policy = json.loads(arguments.policy.read_text(encoding="utf-8"))
    manifest = json.loads(arguments.manifest.read_text(encoding="utf-8"))
    if policy.get("schemaVersion") != "s1.4x-scala-source-policy-v1":
        print("invalid Scala source policy", file=sys.stderr)
        return 1
    if manifest.get("schemaVersion") != "s1.4x-scala-source-inputs-v1":
        print("invalid Scala source input manifest", file=sys.stderr)
        return 1
    expected_roots = [
        path.removeprefix("scala/")
        for path in policy.get("productionRoots", [])
        if isinstance(path, str) and path.startswith("scala/")
    ]
    if (
        manifest.get("roots") != expected_roots
        or manifest.get("sourceLocalExclusions") != []
        or manifest.get("generatedSourcesTracked") is not False
    ):
        print("Scala source input manifest closure mismatch", file=sys.stderr)
        return 1

    files, source_set_violations = collect_sources(scala_root, manifest)
    checked_files = [path.relative_to(scala_root).as_posix() for path in files]
    if arguments.require_git_source_equality:
        git_files, git_violations = git_source_files(scala_root, manifest["roots"])
        source_set_violations.extend(git_violations)
        if checked_files != git_files:
            source_set_violations.append("tracked-manifest-source-set-mismatch")
    violations = [
        {"file": item.split(":", 1)[0], "line": 1, "rule": item.split(":", 1)[1]}
        for item in source_set_violations
    ]
    for path in files:
        violations.extend(audit_file(path, scala_root))
    if violations:
        print(json.dumps({"violations": violations}, ensure_ascii=False), file=sys.stderr)
        return 1

    if arguments.semantic_receipt is None or not arguments.semantic_receipt.is_file():
        print("semantic receipt is required; token audit is supplemental only", file=sys.stderr)
        return 1
    try:
        semantic_receipt = json.loads(
            arguments.semantic_receipt.read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as error:
        print(f"invalid semantic receipt: {error}", file=sys.stderr)
        return 1
    policy_sha256 = sha256(arguments.policy)
    manifest_sha256 = sha256(arguments.manifest)
    if not isinstance(semantic_receipt, dict) or not semantic_receipt_valid(
        semantic_receipt,
        policy_sha256=policy_sha256,
        manifest_sha256=manifest_sha256,
        checked_files=checked_files,
    ):
        print("semantic receipt identity mismatch", file=sys.stderr)
        return 1

    result = {
        "schemaVersion": "s1.4x-scala-source-policy-result-v1",
        "policySha256": policy_sha256,
        "sourceInputManifestSha256": manifest_sha256,
        "checkerMode": "semanticdb",
        "semanticSmokeStatus": "PASS",
        "checkedFiles": checked_files,
        "violations": [],
        "usedAllowlistEntries": [],
        "staleAllowlistEntries": [],
        "sourceSetExact": True,
        "aggregateStatus": "PASS",
    }
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    with arguments.output.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(result, stream, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        stream.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
