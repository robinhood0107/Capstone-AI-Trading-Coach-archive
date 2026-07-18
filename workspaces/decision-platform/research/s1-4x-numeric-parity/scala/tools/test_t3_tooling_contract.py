#!/usr/bin/env python3
"""Scala T3 compiler/profile/native/OCI wrapper closure를 정적으로 검증한다."""

from __future__ import annotations

import json
from pathlib import Path


SCALA_ROOT = Path(__file__).resolve().parents[1]
TOOLS_ROOT = SCALA_ROOT / "tools"

BASE_OPTIONS = [
    ["-source:3.8"],
    ["-release:25"],
    ["-encoding", "UTF-8"],
    ["-deprecation"],
    ["-feature"],
    ["-unchecked"],
    ["-Wunused:all"],
    ["-Wvalue-discard"],
    ["-Wnonunit-statement"],
    ["-Wenum-comment-discard"],
    ["-Wimplausible-patterns"],
    ["-WunstableInlineAccessors"],
    ["-Wtostring-interpolated"],
    ["-Wrecurse-with-default"],
    ["-Wwrong-arrow"],
    ["-Winfer-union"],
    ["-Wshadow:all"],
    ["-language:strictEquality"],
    ["-language:noAutoTupling"],
    ["-Werror"],
]


def script(name: str) -> str:
    path = TOOLS_ROOT / name
    assert path.is_file(), path
    assert path.stat().st_mode & 0o111, path
    return path.read_text(encoding="utf-8")


def main() -> int:
    profiles = json.loads(
        (SCALA_ROOT / "compiler-profiles.v1.json").read_text(encoding="utf-8")
    )
    assert profiles["schemaVersion"] == "s1.4x-scala-compiler-profiles-v1"
    assert profiles["scalaVersion"] == "3.8.4"
    assert profiles["jdkRelease"] == "25"
    assert profiles["baseOptionGroups"] == BASE_OPTIONS
    assert list(profiles["profiles"]) == ["A", "B", "C"]
    assert profiles["profiles"]["A"]["additionalOptions"] == []
    assert profiles["profiles"]["B"]["additionalOptions"] == ["-opt"]
    assert profiles["profiles"]["C"]["additionalOptions"] == [
        "-opt",
        "-opt-inline:ai.trading.coach.s14x.**",
    ]
    assert len(profiles["warningNegativeFixtures"]) == 4
    for item in profiles["warningNegativeFixtures"]:
        assert (SCALA_ROOT / item["path"]).is_file()
        assert item["requiredOptions"][-1] == "-Werror"

    project = (SCALA_ROOT / "project.scala").read_text(encoding="utf-8")
    flattened = [option for group in BASE_OPTIONS for option in group]
    directive_options = []
    for line in project.splitlines():
        prefix = "//> using option "
        if line.startswith(prefix):
            directive_options.append(line.removeprefix(prefix))
    assert directive_options == flattened

    hard_compiler = script("run-hard-compiler-profile.sh")
    assert "run_compiler_profile.py" in hard_compiler
    assert "S1_4X_SCALA_CLI_BIN:?" in hard_compiler
    assert "compiler-profiles.v1.json" in hard_compiler
    compiler_runner = (TOOLS_ROOT / "run_compiler_profile.py").read_text(
        encoding="utf-8"
    )
    for marker in (
        '"s1.4x-scala-hard-compiler-result-v1"',
        '"s1.4x-scala-input-set-equality-result-v1"',
        '"portableArgv"',
        '"resolvedBinarySha256"',
        '"positiveFlags"',
        '"negativeWarnings"',
    ):
        assert marker in compiler_runner

    correctness = script("run-correctness-profile.sh")
    for marker in (
        "compiler-profiles.v1.json",
        "scala-profile-correctness-result.v1.json",
        "source-inputs.v1.json",
        "canonical-comparison.json",
        "semantic-comparison.json",
    ):
        assert marker in correctness

    qualification = script("run-profile-qualification.sh")
    selector = script("select-proven-profile.sh")
    selected_assertion = script("assert-selected-profile.sh")
    assert "profileOrderBlocks" in qualification
    assert "hostValidityBeforeEachProfileBlock" in qualification
    assert "select-profile" in selector and "--check" in selector
    assert "selected-profile.scala" in selected_assertion
    assert "source-inputs.v1.json" in selected_assertion

    compile_benchmarks = script("compile-benchmarks.sh")
    native_smoke = script("run-jmh-native-smoke.sh")
    assert "--jmh --jmh-version 1.37" in compile_benchmarks
    for marker in (
        "-l",
        "-rf",
        "json",
        "validate-native-jmh",
        "S1_4X_EFFECTIVE_JVM_EVIDENCE_DIR",
    ):
        assert marker in native_smoke

    benchmark_invocation = (
        SCALA_ROOT
        / "benchmarks/scala/ai/trading/coach/s14x/benchmark/BenchmarkInvocation.scala"
    ).read_text(encoding="utf-8")
    jvm_evidence = (
        SCALA_ROOT
        / "benchmarks/scala/ai/trading/coach/s14x/benchmark/JvmForkEvidence.scala"
    ).read_text(encoding="utf-8")
    assert "JvmForkEvidence.record()" in benchmark_invocation
    for marker in (
        "RuntimeMXBean",
        "S1_4X_EFFECTIVE_JVM_EVIDENCE_DIR",
        "CREATE_NEW",
        "TEMURIN_25_0_3_9_LTS",
    ):
        assert marker in jvm_evidence

    dependency_audit = script("audit-scala-dependency-edges.sh")
    assert "scala-dependency-native-edge-result.v1.json" in dependency_audit
    assert "candidateAuthoredEdgeCount" in dependency_audit
    dependency_runner = (TOOLS_ROOT / "audit_scala_dependencies.py").read_text(
        encoding="utf-8"
    )
    for rejected in ("scala-native", "llvm", "graal", "jni", "grpc"):
        assert rejected in dependency_runner

    capability = script("assemble-capability-results.sh")
    feature = script("assemble-feature-results.sh")
    assert "capability-smoke-plan.v1.json" in capability
    assert "feature-decisions.v1.json" in feature
    assert "lint-exceptions.v1.json" in feature

    containerfile = (SCALA_ROOT / "Containerfile").read_text(encoding="utf-8")
    assert "ARG S1_4X_SCALA_BASE_IMAGE" in containerfile
    assert "ARG S1_4X_SCALA_BASE_IMAGE=" not in containerfile
    assert "FROM ${S1_4X_SCALA_BASE_IMAGE}" in containerfile
    assert "USER 65532:65532" in containerfile
    oci = script("run-oci-correctness.sh")
    assert "--network none" in oci
    assert "S1_4X_SCALA_IMAGE_REF:?" in oci
    assert "/workspace" not in oci
    assert "$HOME:" not in oci

    print(
        "SCALA_T3_TOOLING_CONTRACT_TEST_PASS "
        "compilerFlags=20 profiles=3 warningNegatives=4 capabilitySmokes=8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
