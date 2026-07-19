#!/usr/bin/env python3
"""Scala outer benchmark wrapper의 shared producer 경계를 고정한다."""

from __future__ import annotations

import importlib.util
from pathlib import Path


SCALA_ROOT = Path(__file__).resolve().parents[1]
TOOLS_ROOT = SCALA_ROOT / "tools"
HELPER_PATH = TOOLS_ROOT / "scala_benchmark_block.py"
WRAPPER_PATH = TOOLS_ROOT / "run-benchmark-block.sh"


def load_helper():
    spec = importlib.util.spec_from_file_location("scala_benchmark_block", HELPER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    helper = load_helper()
    wrapper = WRAPPER_PATH.read_text(encoding="utf-8")
    source = HELPER_PATH.read_text(encoding="utf-8")

    assert wrapper.startswith("#!/usr/bin/bash\n")
    for marker in (
        "/usr/bin/env -i",
        "S1_4X_BENCHMARK_PYTHON_BIN",
        "S1_4X_BENCHMARK_PYTHON_SHA256",
        "S1_4X_BENCHMARK_PYTHON_PINNED_FD_PATH",
        "S1_4X_SCALA_CLI_BIN",
        "S1_4X_SCALAFIX_BIN",
        "S1_4X_SCALAFMT_ARCHIVE",
        "S1_4X_SCALAFMT_BIN",
        "S1_4X_SCALA_SELECTED_PROFILE_RESULT",
        "S1_4X_SCALA_QUALIFICATION_RESULT",
        "S1_4X_SCALA_CORRECTNESS_ROOT",
        "S1_4X_SCALA_JVM_ALLOWLIST_RESULT",
        "S1_4X_BENCHMARK_SUBJECT_COMMIT",
        "JAVA_HOME",
        "--benchmark-subject-commit",
    ):
        assert marker in wrapper
    for forbidden in (
        "command" + " -v",
        "/" + "tmp",
        "/home/" + "pjjpj",
        "benchmarks/run_rotated_blocks.py",
    ):
        assert forbidden not in wrapper
    assert '"$BENCHMARK_PYTHON_EXEC" "$HELPER"' in wrapper
    assert '"$BENCHMARK_PYTHON" "$HELPER"' not in wrapper

    runner = Path("/repo/numeric/scala/tools/run-jmh-native-full.sh")
    plan = Path("/repo/numeric/benchmarks/benchmark-plan.v1.json")
    allowlist = Path("/evidence/scala-jvm-argument-allowlist.v1.json")
    case_root = Path("/run/r1/scala/path-transform/scala-jmh/case-001")
    assert helper.build_full_case_command(
        runner=runner,
        plan=plan,
        profile="B",
        case_id="path-transform/simple_returns/n32/b1",
        jvm_allowlist=allowlist,
        output_directory=case_root,
    ) == [
        str(runner),
        "--plan",
        str(plan),
        "--profile",
        "B",
        "--case-id",
        "path-transform/simple_returns/n32/b1",
        "--jvm-allowlist",
        str(allowlist),
        "--output-dir",
        str(case_root),
    ]

    python = Path("/proc/self/fd/101")
    producer = Path("/repo/numeric/integration/native_benchmark_block.py")
    repo = Path("/repo")
    block = Path("/run/r1/scala/path-transform")
    fixture = Path("/repo/numeric/contract/fixtures")
    selected_result = Path("/evidence/selected-profile.json")
    selected_source = Path("/repo/numeric/scala/selected-profile.scala")
    source_manifest = Path("/repo/numeric/scala/source-inputs.v1.json")
    compiler_profiles = Path("/repo/numeric/scala/compiler-profiles.v1.json")
    lock = Path("/repo/numeric/scala/toolchain-lock.v1.json")
    provenance = Path("/repo/numeric/contract/toolchain-provenance.v1.json")
    scala_cli = Path("/tools/scala-cli")
    java = Path("/tools/java")
    producer_command = helper.build_producer_command(
        python=python,
        producer=producer,
        repo_root=repo,
        plan=plan,
        block_directory=block,
        selector_id="scala/path-transform",
        scala_jmh_root=block / "scala-jmh",
        input_ledger=block / "input-ledger.json",
        fixture_root=fixture,
        selected_profile_result=selected_result,
        selected_profile_source=selected_source,
        source_input_manifest=source_manifest,
        compiler_profiles=compiler_profiles,
        toolchain_lock=lock,
        toolchain_provenance=provenance,
        jvm_argument_capability=allowlist,
        scala_cli=scala_cli,
        java_executable=java,
        started_at="2026-07-18T00:00:00Z",
        finished_at="2026-07-18T00:01:00Z",
    )
    assert producer_command[:3] == [
        str(python),
        str(producer),
        "produce-scala-native",
    ]
    assert producer_command[3:] == [
        "--repo-root",
        str(repo),
        "--plan",
        str(plan),
        "--block-dir",
        str(block),
        "--selector",
        "scala/path-transform",
        "--scala-jmh-root",
        str(block / "scala-jmh"),
        "--input-ledger",
        str(block / "input-ledger.json"),
        "--fixture-root",
        str(fixture),
        "--selected-profile-result",
        str(selected_result),
        "--selected-profile-source",
        str(selected_source),
        "--source-input-manifest",
        str(source_manifest),
        "--compiler-profiles",
        str(compiler_profiles),
        "--toolchain-lock",
        str(lock),
        "--toolchain-provenance",
        str(provenance),
        "--jvm-argument-capability",
        str(allowlist),
        "--scala-cli",
        str(scala_cli),
        "--java-executable",
        str(java),
        "--started-at",
        "2026-07-18T00:00:00Z",
        "--finished-at",
        "2026-07-18T00:01:00Z",
    ]

    builder_command = helper.build_block_result_command(
        python=python,
        producer=producer,
        repo_root=repo,
        plan=plan,
        block_directory=block,
        qualification=block / "timeout-qualification.json",
        selector_id="scala/path-transform",
        family_id="path-transform",
        rotation_id="R1",
        outer_repetition=1,
        run_id="run-001",
        benchmark_subject_commit="a" * 40,
    )
    assert builder_command == [
        str(python),
        str(producer),
        "--repo-root",
        str(repo),
        "--plan",
        str(plan),
        "--block-dir",
        str(block),
        "--qualification",
        str(block / "timeout-qualification.json"),
        "--boundary",
        "scala",
        "--selector",
        "scala/path-transform",
        "--family",
        "path-transform",
        "--rotation",
        "R1",
        "--outer-repetition",
        "1",
        "--run-id",
        "run-001",
        "--benchmark-subject-commit",
        "a" * 40,
    ]

    assert helper.case_directory_name(1) == "case-001"
    assert helper.case_directory_name(89) == "case-089"
    try:
        helper.case_directory_name(0)
    except helper.BlockError:
        pass
    else:
        raise AssertionError("zero case index must be rejected")

    marker = Path("/repo/numeric/integration/run_rotated_blocks.py")
    qualification = block / "timeout-qualification.json"
    assert helper.build_measurement_marker_command(
        python=python,
        marker=marker,
        qualification=qualification,
    ) == [
        str(python),
        str(marker),
        "mark-measurement-entered",
        "--qualification",
        str(qualification),
    ]

    assert helper.RAW_CASE_FILES == (
        "native.json",
        "scala-jmh-run-result.v1.json",
        "scala-jmh-native-validation.v1.json",
        "scala-effective-jvm-args-result.v1.json",
        "measurement-ready.v1.json",
        "scala-jmh-generated-java-precompile.v1.json",
        "scala-jmh-precompile.stdout",
        "scala-jmh-precompile.stderr",
        "scala-javac.stdout",
        "scala-javac.stderr",
        "fork-evidence.normalized.json",
        "jmh.stdout",
        "jmh.stderr",
        "jmh-list.txt",
    )
    assert helper.CANDIDATE_PROVENANCE_FIELDS == frozenset(
        {
            "kind",
            "selectedProfileResultPath",
            "selectedProfileResultSha256",
            "selectedProfileSourcePath",
            "selectedProfileSourceSha256",
            "selectedProfileId",
            "sourceInputManifestPath",
            "sourceInputManifestSha256",
            "compilerProfilesPath",
            "compilerProfilesSha256",
            "toolchainLockPath",
            "toolchainLockSha256",
            "mergedToolchainProvenancePath",
            "mergedToolchainProvenanceSha256",
            "effectiveJvmArgumentsCapabilityPath",
            "effectiveJvmArgumentsCapabilitySha256",
            "scalaCliPath",
            "scalaCliBinarySha256",
            "javaExecutablePath",
            "javaExecutableSha256",
        }
    )
    assert helper.RECORDED_ENVIRONMENT_NAMES == (
        "S1_4X_BENCHMARK_CASE_ID",
        "S1_4X_BENCHMARK_PLAN",
        "S1_4X_BENCHMARK_PROFILE",
        "S1_4X_BENCHMARK_RUN_MODE",
        "S1_4X_FIXTURE_ROOT",
        "S1_4X_EFFECTIVE_JVM_EVIDENCE_DIR",
        "S1_4X_MEASUREMENT_READY_MARKER",
        "S1_4X_SCALA_WORKSPACE",
        "COURSIER_CACHE",
        "COURSIER_CONFIG_DIR",
        "SCALA_CLI_HOME",
        "SCALA_CLI_CONFIG",
        "XDG_CONFIG_HOME",
        "JAVA_HOME",
    )
    assert helper.FORBIDDEN_AMBIENT_JVM_VARIABLES == (
        "JAVA_TOOL_OPTIONS",
        "_JAVA_OPTIONS",
        "JDK_JAVA_OPTIONS",
    )
    assert 'integration_root / "run_rotated_blocks.py"' in source
    assert "benchmarks/run_rotated_blocks.py" not in source
    assert "produce-scala-native" in source
    assert "exclusive_json_write" not in source
    assert "import statistics" not in source
    assert "import math" not in source

    print(
        "SCALA_BENCHMARK_BLOCK_CONTRACT_PASS "
        "rawFiles=9 provenanceFields=20 environmentFields=14"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
