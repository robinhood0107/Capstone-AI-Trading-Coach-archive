#!/usr/bin/env python3
"""Scala project dependency와 candidate source의 native interop edge를 독립 감사한다."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from check_source_policy import strip_comments_and_literals
from source_input_manifest import validated_source_files
from t3_evidence import assemble_scala_dependency_audit
from t3_evidence import sha256_file
from t3_evidence import strict_json
from t3_evidence import write_exclusive_json


class DependencyAuditError(ValueError):
    """Dependency/source native-edge closure mismatch."""


FORBIDDEN_PATTERNS = {
    "system-native-load": r"\bSystem\.(?:load|loadLibrary)\b",
    "java-native-method": r"\bnative\s+def\b",
    "java-foreign-api": r"\bjava\.lang\.foreign\b",
    "jni": r"\bJNI\b",
    "scala-native": r"\b(?:scala\.scalanative|Scala\s+Native)\b",
    "llvm": r"\bLLVM\b",
    "graal": r"\bGraal\b",
    "grpc": r"\b(?:io\.grpc|gRPC)\b",
}


def dependencies(project: Path) -> list[str]:
    prefix = re.compile(r"^//>\s+using\s+(?:test\.)?dep\s+(\S+)\s*$")
    values = []
    for line in project.read_text(encoding="utf-8").splitlines():
        match = prefix.fullmatch(line)
        if match is not None:
            values.append(match.group(1))
    if not values or len(values) != len(set(values)):
        raise DependencyAuditError("PROJECT_DEPENDENCY_SET_INVALID")
    return values


def findings(scala_root: Path, sources: list[Path]) -> list[dict[str, object]]:
    result = []
    for path in sources:
        relative = path.relative_to(scala_root).as_posix()
        stripped = strip_comments_and_literals(path.read_text(encoding="utf-8"))
        for rule, pattern in FORBIDDEN_PATTERNS.items():
            for match in re.finditer(pattern, stripped):
                result.append(
                    {
                        "file": relative,
                        "line": stripped.count("\n", 0, match.start()) + 1,
                        "rule": rule,
                    }
                )
    return result


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scala-root", type=Path, required=True)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--source-policy-result", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()
    try:
        scala_root = arguments.scala_root.resolve(strict=True)
        policy = strict_json(arguments.policy)
        manifest = strict_json(arguments.manifest)
        source_policy_result = strict_json(arguments.source_policy_result)
        if (
            arguments.output.exists()
            or source_policy_result.get("aggregateStatus") != "PASS"
            or source_policy_result.get("policySha256")
            != sha256_file(arguments.policy)
            or source_policy_result.get("sourceInputManifestSha256")
            != sha256_file(arguments.manifest)
        ):
            raise DependencyAuditError("SOURCE_POLICY_RESULT_INVALID")
        sources = validated_source_files(
            scala_root,
            manifest,
            policy=policy,
            require_git_source_equality=True,
        )
        result = assemble_scala_dependency_audit(
            policy_sha256=sha256_file(arguments.policy),
            source_input_manifest_sha256=sha256_file(arguments.manifest),
            project_sha256=sha256_file(scala_root / "project.scala"),
            dependencies=dependencies(scala_root / "project.scala"),
            forbidden_source_findings=findings(scala_root, sources),
        )
        write_exclusive_json(arguments.output, result)
    except (OSError, UnicodeError, ValueError, DependencyAuditError) as error:
        print(f"SCALA_DEPENDENCY_AUDIT_FAIL:{error}", file=sys.stderr)
        return 1
    print(
        "SCALA_DEPENDENCY_AUDIT_PASS "
        "candidateAuthoredEdgeCount=0 "
        "candidateAddedNativeDependencyCount=0 "
        "candidateCoreDirectNativeBindingImportCount=0 "
        "candidateCoreDirectNativeBindingCallCount=0 "
        "timedKernelExplicitCandidateNativeInteropCallCount=0"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
