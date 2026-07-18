#!/usr/bin/env python3
"""Scala T3 compiler/profile/native/OCI wrapper closure를 정적으로 검증한다."""

from __future__ import annotations

import importlib.util
import json
import sys
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


def load_tool(name: str):
    path = TOOLS_ROOT / f"{name}.py"
    specification = importlib.util.spec_from_file_location(name, path)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    sys.path.insert(0, str(TOOLS_ROOT))
    try:
        sys.modules[name] = module
        specification.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


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
    assert profiles["profiles"]["A"]["scalaCliArguments"] == []
    assert profiles["profiles"]["B"]["additionalOptions"] == ["-opt"]
    assert profiles["profiles"]["B"]["scalaCliArguments"] == [
        "--scalac-option=-opt"
    ]
    assert profiles["profiles"]["C"]["additionalOptions"] == [
        "-opt",
        "-opt-inline:ai.trading.coach.s14x.**",
    ]
    assert profiles["profiles"]["C"]["scalaCliArguments"] == [
        "--scalac-option=-opt",
        "--scalac-option=-opt-inline:ai.trading.coach.s14x.**",
    ]
    profile_assertion = script("assert-compiler-profiles.sh")
    assert "scalaCliArguments" in profile_assertion
    assert "--scalac-option=-opt-inline:ai.trading.coach.s14x.**" in (
        profile_assertion
    )
    assert len(profiles["warningNegativeFixtures"]) == 4
    for item in profiles["warningNegativeFixtures"]:
        assert (SCALA_ROOT / item["path"]).is_file()
        assert item["requiredOptions"][-1] == "-Werror"
        assert item["expectedExitCode"] == 1
        assert item["expectedDiagnosticPattern"]
        assert item["forbiddenDiagnosticPattern"]

    compiler_tool = load_tool("run_compiler_profile")
    intended = compiler_tool.diagnostic_disposition(
        b"-- Warning: unused import --",
        expected_pattern="(?i)unused import",
        forbidden_pattern="(?i)(syntax error|not found:)",
    )
    assert intended["status"] == "PASS"
    unrelated = compiler_tool.diagnostic_disposition(
        b"-- Error: syntax error --",
        expected_pattern="(?i)unused import",
        forbidden_pattern="(?i)(syntax error|not found:)",
    )
    assert unrelated["status"] == "FAIL"
    crashed = compiler_tool.diagnostic_disposition(
        b"unused import\nOutOfMemoryError",
        expected_pattern="(?i)unused import",
        forbidden_pattern="(?i)(internal compiler error|exception|out ?of ?memory)",
    )
    assert crashed["status"] == "FAIL"
    clean_probe = compiler_tool.diagnostic_probe_disposition(
        b"",
        b"",
        option="-Wsafe-init",
        expected_pattern="(?i)(safe initialization|initialization)",
        exit_code=0,
    )
    assert clean_probe["exitDisposition"] == "CLEAN_NO_BLOCKING_DIAGNOSTIC"
    failed_probe = compiler_tool.diagnostic_probe_disposition(
        b"",
        b"OutOfMemoryError",
        option="-Wsafe-init",
        expected_pattern="(?i)(safe initialization|initialization)",
        exit_code=137,
    )
    assert failed_probe["status"] == "FAIL"

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
        '"compileInputPaths"',
        '"portableArgv"',
        '"resolvedBinarySha256"',
        '"positiveFlags"',
        '"negativeWarnings"',
    ):
        assert marker in compiler_runner
    negative_loop = compiler_runner.index("for index, fixture in enumerate(")
    negative_process = compiler_runner.index(
        'process_id = f"negative-',
        negative_loop,
    )
    assert negative_process > negative_loop
    assert compiler_runner.count('process_id = f"negative-') == 1

    correctness = script("run-correctness-profile.sh")
    for marker in (
        "compiler-profiles.v1.json",
        "scala-profile-correctness-result.v1.json",
        "source-inputs.v1.json",
        "canonical-comparison.json",
        "semantic-comparison.json",
        "scala-profile-unit-test-result.v1.json",
        "--require-tests",
        "assemble_profile_correctness.py",
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
    native_full = script("run-jmh-native-full.sh")
    assert "--jmh --jmh-version 1.37" in compile_benchmarks
    for marker in (
        "-l",
        "-rf",
        "json",
        "validate-native-jmh",
        "S1_4X_EFFECTIVE_JVM_EVIDENCE_DIR",
        "create-jvm-allowlist",
        "--jvm-allowlist",
        "--expected-measurement-iterations",
    ):
        assert marker in native_smoke
    for marker in (
        "assert-selected-profile.sh",
        "--mode full",
        "S1_4X_SCALA_SELECTED_PROFILE_RESULT",
    ):
        assert marker in native_full

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
    capability_runner = (
        TOOLS_ROOT / "assemble_capability_results.py"
    ).read_text(encoding="utf-8")
    assert "assemble_input_set_result" in capability_runner
    assert "scala-input-set-equality-result.v1.json" in capability_runner
    assert "SCALA_CAPABILITY_EVIDENCE" not in capability_runner
    toolchain_receipt = script("run-toolchain-identity.sh")
    assert "scala-toolchain-identity-result.v1.json" in toolchain_receipt
    profile_contract = script("test-profile-correctness.sh")
    assert "test_profile_correctness.py" in profile_contract
    source_policy = script("check-source-policy.sh")
    assert 'S1_ROOT="$(cd -- "$SCALA_ROOT/.."' in source_policy
    selected_profile_assertion = script("assert-selected-profile.sh")
    assert "DUPLICATE_JSON_KEY" in selected_profile_assertion
    assert "NONFINITE_JSON" in selected_profile_assertion

    planned_features = json.loads(
        (SCALA_ROOT.parent / "contract/feature-decisions.v1.json").read_text(
            encoding="utf-8"
        )
    )
    planned_scala = {
        item["featureId"]: item["decision"]
        for item in planned_features["entries"]
        if item["featureId"].startswith("scala.")
    }
    feature_evidence = json.loads(
        (SCALA_ROOT / "feature-evidence.v1.json").read_text(encoding="utf-8")
    )
    assert {
        item["featureId"]: item["decision"]
        for item in feature_evidence["entries"]
    } == planned_scala
    assert all(
        item["status"].startswith("pending-")
        for item in feature_evidence["entries"]
    )

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
