#!/usr/bin/env python3
"""Scala T3 compiler/profile/native/OCI wrapper closure를 정적으로 검증한다."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
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
    toolchain_lock = json.loads(
        (SCALA_ROOT / "toolchain-lock.v1.json").read_text(encoding="utf-8")
    )
    assert toolchain_lock["jdk"]["javacExecutableSha256"] == (
        "5dc287a983c41c8cee0c00e621d87a46ff9dd77202885814b39645074d714dd9"
    )
    assert toolchain_lock["jdk"]["jdkModulesPathId"] == (
        "TEMURIN_25_0_3_9_LTS/lib/modules"
    )
    assert toolchain_lock["jdk"]["jdkModulesSha256"] == (
        "0b4f933e2a29a05a74a869dddd823d1e7bc0ed9b38db0db25a44eab5dfb5c462"
    )
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
    warning_fixtures = {
        item["fixtureId"]: item for item in profiles["warningNegativeFixtures"]
    }
    value_discard = warning_fixtures["value-discard"]
    assert value_discard["expectedDiagnosticPattern"] == (
        r"(?i)discarded (?:non-Unit )?value"
    )
    assert (SCALA_ROOT / value_discard["path"]).read_text(
        encoding="utf-8"
    ) == (
        "object CompilerWarningValueDiscard:\n"
        "  def value: Unit =\n"
        "    Option(1)\n"
    )
    assert warning_fixtures["nonunit-statement"][
        "expectedDiagnosticPattern"
    ] == r"(?i)pure expression does nothing in statement position"

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

    test_dependencies = compiler_tool.project_test_dependencies(
        SCALA_ROOT / "project.scala"
    )
    assert test_dependencies == [
        "org.scalameta::munit:1.3.0",
        "org.scalameta::munit-scalacheck:1.3.0",
        "org.scalacheck::scalacheck:1.19.0",
    ]
    project_compile = compiler_tool.project_compiler_arguments(
        Path("/tool/scala-cli"),
        [SCALA_ROOT / "project.scala", SCALA_ROOT / "selected-profile.scala"],
        profile_arguments=["--scalac-option=-opt"],
        test_dependencies=test_dependencies,
    )
    assert "--test" not in project_compile
    assert project_compile.count("--dependency") == len(test_dependencies)
    for dependency in test_dependencies:
        offset = project_compile.index(dependency)
        assert project_compile[offset - 1 : offset + 1] == [
            "--dependency",
            dependency,
        ]

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
        "test_dependencies",
        '"--dependency"',
        "assemble_profile_correctness.py",
    ):
        assert marker in correctness
    property_evidence = script("run-property-evidence.sh")
    for marker in (
        "source-inputs.v1.json",
        "test_dependencies",
        '"--dependency"',
        "PropertyEvidenceMain",
    ):
        assert marker in property_evidence
    property_evidence_main = (
        SCALA_ROOT
        / "src/test/scala/ai/trading/coach/s14x/shell/PropertyEvidenceMain.scala"
    ).read_text(encoding="utf-8")
    assert ".set[ArrayNode]" not in property_evidence_main
    assert property_evidence_main.count(".set[JsonNode]") == 5

    qualification = script("run-profile-qualification.sh")
    qualification_runner = (
        TOOLS_ROOT / "run_profile_qualification.py"
    ).read_text(encoding="utf-8")
    selector = script("select-proven-profile.sh")
    selected_assertion = script("assert-selected-profile.sh")
    assert "profileOrderBlocks" in qualification
    assert "hostValidityBeforeEachProfileBlock" in qualification
    for marker in (
        'exec {scala_cli_pin_fd}<"$SCALA_CLI"',
        'exec {java_pin_fd}<"$JAVA_EXECUTABLE"',
        'exec {javac_pin_fd}<"$JAVAC_EXECUTABLE"',
        "S1_4X_SCALA_CLI_EXEC_PATH",
        "S1_4X_SCALA_JAVA_PINNED_FD_PATH",
        "S1_4X_SCALA_JAVAC_PINNED_FD_PATH",
        "javacExecutableSha256",
        "compgen -e",
        'python3 -E -s -S "$SCALA_ROOT/tools/run_profile_qualification.py"',
    ):
        assert marker in qualification
    for marker in (
        "SCALA_CLI_PINNED_FD_PATH_REQUIRED",
        "JAVA_PINNED_FD_PATH_REQUIRED",
        "JAVAC_PINNED_FD_PATH_REQUIRED",
        "JAVAC_PINNED_FD_IDENTITY_MISMATCH",
        "generatedJavaPrecompileReceiptSha256",
        "JDK_MODULES_GATE_SNAPSHOT_VARIABLE",
        "_verify_regular_file_snapshot",
        "QUALIFICATION_JDK_MODULES_POST_GATE_DRIFT",
    ):
        assert marker in qualification_runner
    for chain_script in (
        "run-profile-qualification.sh",
        "compile-benchmarks.sh",
        "run-jmh-native-smoke.sh",
        "select-proven-profile.sh",
        "assert-selected-profile.sh",
    ):
        python_lines = [
            line
            for line in script(chain_script).splitlines()
            if "python3 " in line and not line.lstrip().startswith("#")
        ]
        assert python_lines, chain_script
        assert all(
            "python3 -E -s -S " in line for line in python_lines
        ), (chain_script, python_lines)
    assert "select-profile" in selector and "--check" in selector
    assert "selected-profile.scala" in selected_assertion
    assert "source-inputs.v1.json" in selected_assertion

    compile_benchmarks = script("compile-benchmarks.sh")
    native_smoke = script("run-jmh-native-smoke.sh")
    native_full = script("run-jmh-native-full.sh")
    assert "--jmh --jmh-version 1.37" in compile_benchmarks
    assert "--print-classpath" in compile_benchmarks
    assert "precompile_jmh_generated_java.py" in compile_benchmarks
    assert "scala-jmh-generated-java-precompile.v1.json" in (
        compile_benchmarks
    )
    assert '--classpath "$precompiled_classes"' in compile_benchmarks
    assert compile_benchmarks.index(
        'mkdir -p "$precompiled_classes"'
    ) < compile_benchmarks.index(
        "precompile_jmh_generated_java.py"
    )
    assert "S1_4X_SCALA_CLI_EXEC_PATH" in compile_benchmarks
    assert '--workspace "$S1_4X_SCALA_WORKSPACE"' in compile_benchmarks
    assert "--server=true" not in compile_benchmarks
    precompile_helper = (
        TOOLS_ROOT / "precompile_jmh_generated_java.py"
    ).read_text(encoding="utf-8")
    for marker in (
        "capture_classpath_post_run",
        "validate_classpath_post_run_evidence",
        "create_jmh_runtime_closure_evidence",
        "validate_jmh_runtime_closure_evidence",
        "SCALA_CLASS_OUTPUT",
        "JMH_GENERATED_RESOURCES",
        "ROTATED_SAME_BYTES",
        "classpathPostRunSha256",
        "jmhRuntimeClosureSha256",
        "precompileRuntimeClasspathSha256",
    ):
        assert marker in precompile_helper
    for marker in (
        "-l",
        "-rf",
        "json",
        "validate-native-jmh",
        "S1_4X_EFFECTIVE_JVM_EVIDENCE_DIR",
        "S1_4X_SCALA_JAVA_PINNED_FD_PATH",
        "create-jvm-allowlist",
        "--jvm-allowlist",
        "--expected-measurement-iterations",
        "--workspace",
        "COURSIER_CACHE",
        "SCALA_CLI_HOME",
        "commandToolClosureSha256",
        "environmentValuesSha256",
        "runtimeExecutionPathIdentitiesSha256",
        "liveRuntimeArgvWitnessSha256",
        "procOwnerStartTimeTicks",
        "runtimePathSha256",
        "liveExecutionPathIdentitySha256",
        "workspace_index = runtime_argv.index(\"--workspace\")",
        "runtime_argv[3:workspace_index]",
        '"-jvm", "PINNED_JAVA_FD"',
        'JAVAC_EXECUTABLE="${JAVA_HOME:?JAVA_HOME is required}/bin/javac"',
        "S1_4X_SCALA_JAVAC_PINNED_FD_PATH",
        '--classpath "$precompiled_classes"',
        "generatedJavaPrecompileReceiptSha256",
        "JVM_FORK_FILE_COUNT_MISMATCH:expected=",
        "S1_4X_LARGE_FIXTURE_ROOT",
        '--coursier-cache "$COURSIER_CACHE"',
        '--jmh-stdout "$OUTPUT_DIR/jmh.stdout"',
    ):
        assert marker in native_smoke
    assert "$S1_ROOT/contract/fixtures" not in native_smoke
    assert "S1_4X_FIXTURE_MATERIALIZATION_RECEIPT" not in native_smoke
    assert native_smoke.index("S1_4X_LARGE_FIXTURE_ROOT") < (
        native_smoke.index('"$SCALA_ROOT/tools/assert-toolchain.sh"')
    )
    assert native_smoke.count("fixture_root_identity") >= 3
    assert "--server=true" not in native_smoke
    assert native_smoke.index("--server=false") < native_smoke.index(
        '--classpath "$precompiled_classes"'
    )
    assert native_smoke.index('"-jvm", "PINNED_JAVA_FD"') > (
        native_smoke.index("S1_4X_SCALA_JAVAC_PINNED_FD_PATH")
    )
    assert "S1_4X_MEASUREMENT_READY_MARKER" in native_smoke
    assert "measurementReadyMarkerSha256" in native_smoke
    for marker in (
        'jmh_tmpdir="$OUTPUT_DIR/jmh-tmp"',
        'export S1_4X_JMH_TMPDIR="$jmh_tmpdir"',
        '--java-prop "java.io.tmpdir=$jmh_tmpdir"',
        '--fork-evidence "$OUTPUT_DIR/fork-evidence.normalized.json"',
    ):
        assert marker in native_smoke
    assert "--print-classpath" not in native_smoke
    benchmark_invocation = (
        SCALA_ROOT
        / "benchmarks/scala/ai/trading/coach/s14x/benchmark"
        / "BenchmarkInvocation.scala"
    ).read_text(encoding="utf-8")
    forced_evaluation = benchmark_invocation.index(
        "runPrepared(value).isFinite"
    )
    ready_marker = benchmark_invocation.index("markMeasurementReady")
    assert forced_evaluation < ready_marker
    for marker in (
        "assert-selected-profile.sh",
        "--mode full",
        "S1_4X_SCALA_SELECTED_PROFILE_RESULT",
        'basename -- "$(dirname -- "$OUTPUT_DIR")"',
        '"scala-jmh"',
    ):
        assert marker in native_full
    assert "native_benchmark_block.py" not in native_full

    benchmark_invocation = (
        SCALA_ROOT
        / "benchmarks/scala/ai/trading/coach/s14x/benchmark/BenchmarkInvocation.scala"
    ).read_text(encoding="utf-8")
    jvm_evidence_path = (
        SCALA_ROOT
        / "benchmarks/scala/ai/trading/coach/s14x/benchmark/JvmForkEvidence.scala"
    )
    jvm_evidence = jvm_evidence_path.read_text(encoding="utf-8")
    source_policy = load_tool("check_source_policy")
    source_policy_violations = source_policy.audit_file(
        jvm_evidence_path,
        SCALA_ROOT,
    )
    assert source_policy_violations == [], source_policy_violations
    assert "JvmForkEvidence.record()" in benchmark_invocation
    for marker in (
        "RuntimeMXBean",
        "S1_4X_EFFECTIVE_JVM_EVIDENCE_DIR",
        "CREATE_NEW",
        "TEMURIN_25_0_3_9_LTS",
        "inputArgumentFiles",
        "JMH_COMPILE_COMMAND_FILE",
        "CompileCommandFile=",
        "S1_4X_JMH_TMPDIR",
        "FileChannel.open",
        "LinkOption.NOFOLLOW_LINKS",
        "UnixFileIdentity",
        'Path.of("/proc/self/fd")',
        "descriptorReferencesLockedChannel",
        "handleBefore == handleMiddle",
        "handleMiddle == handleAfter",
        "before == handleBefore",
        '"unix:dev,ino,mode,nlink,size,lastModifiedTime,ctime"',
        "java.util.Arrays.equals(firstBytes, secondBytes)",
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
    for marker in (
        'SCALAFMT_BIN="${S1_4X_SCALAFMT_BIN:?',
        'SCALAFMT_RESULT="${S1_4X_SCALA_SCALAFMT_RESULT:?',
        '--scalafmt-bin "$SCALAFMT_BIN"',
        '--scalafmt "$SCALAFMT_RESULT"',
    ):
        assert marker in capability
    assert "feature-decisions.v1.json" in feature
    assert "lint-exceptions.v1.json" in feature
    capability_runner = (
        TOOLS_ROOT / "assemble_capability_results.py"
    ).read_text(encoding="utf-8")
    assert "assemble_input_set_result" in capability_runner
    assert "scala-input-set-equality-result.v1.json" in capability_runner
    assert "SCALA_CAPABILITY_EVIDENCE" not in capability_runner
    capability_tool = load_tool("assemble_capability_results")
    with tempfile.TemporaryDirectory() as temporary:
        temporary_root = Path(temporary)
        stdout = temporary_root / "process.stdout"
        stderr = temporary_root / "process.stderr"
        stdout.write_bytes(b"verified stdout\n")
        stderr.write_bytes(b"")
        portable = ["PINNED_TOOL", "--check", "SCALA_ROOT/source.scala"]
        runtime = ["/opt/pinned/tool", "--check", str(SCALA_ROOT / "source.scala")]
        process = {
            "portableArgv": portable,
            "portableArgvSha256": capability_tool.canonical_sha256(portable),
            "runtimeArgvSha256": capability_tool.canonical_sha256(runtime),
            "exitCode": 0,
            "stdoutSha256": capability_tool.sha256_file(stdout),
            "stderrSha256": capability_tool.sha256_file(stderr),
            "status": "PASS",
        }
        capability_tool.require_process(
            process,
            "contract",
            expected_portable_argv=portable,
            expected_runtime_argv=runtime,
            stdout_path=stdout,
            stderr_path=stderr,
        )
        forged_argv = dict(process)
        forged_argv["portableArgv"] = [*portable, "--forged"]
        try:
            capability_tool.require_process(
                forged_argv,
                "contract",
                expected_portable_argv=portable,
                expected_runtime_argv=runtime,
                stdout_path=stdout,
                stderr_path=stderr,
            )
        except capability_tool.CapabilityAssemblyError:
            pass
        else:
            raise AssertionError("forged capability argv passed")
        stdout.write_bytes(b"tampered stdout\n")
        try:
            capability_tool.require_process(
                process,
                "contract",
                expected_portable_argv=portable,
                expected_runtime_argv=runtime,
                stdout_path=stdout,
                stderr_path=stderr,
            )
        except capability_tool.CapabilityAssemblyError:
            pass
        else:
            raise AssertionError("tampered capability log passed")
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
    for marker in (
        "S1_4X_BASE_IMAGE_ID",
        "S1_4X_CANDIDATE_SHA256",
        "S1_4X_CONTAINERFILE_SHA256",
        "S1_4X_FIXTURE_TREE_SHA256",
        "org.opencontainers.image.s1-4x.candidate-sha256",
        "org.opencontainers.image.s1-4x.base-reference",
        "org.opencontainers.image.s1-4x.base-image-id",
    ):
        assert marker in containerfile
    assert "USER 65532:65532" in containerfile
    build_oci = script("build-oci-image.sh")
    oci = script("run-oci-correctness.sh")
    oci_evidence = (TOOLS_ROOT / "oci_evidence.py").read_text(encoding="utf-8")
    assert "--network none" in oci
    assert "S1_4X_SCALA_IMAGE_REF" not in oci
    assert "--build-result" in oci
    assert "runtime-binding" in oci
    assert "S1_4X_DOCKER_SHA256:?" in oci
    assert '"$IMAGE_ID"' in oci
    assert "/workspace" not in oci
    assert "$HOME:" not in oci
    assert "S1_4X_SCALA_BASE_IMAGE_REF:?" in build_oci
    assert "--iidfile" in oci_evidence
    assert "DOCKER_DAEMON_CHANGED_DURING_BUILD" in oci_evidence

    print(
        "SCALA_T3_TOOLING_CONTRACT_TEST_PASS "
        "compilerFlags=20 profiles=3 warningNegatives=4 capabilitySmokes=8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
