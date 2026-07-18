#!/usr/bin/env python3
"""실제 typed receipts로 Scala 여덟 capability와 six-way input closure를 조립한다."""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path
from typing import Any

from source_input_manifest import git_source_files
from source_input_manifest import production_roots
from t3_evidence import JMH_RUN_RESULT_KEYS
from t3_evidence import PROFILE_CLI_ARGUMENTS
from t3_evidence import PROFILE_OPTIONS
from t3_evidence import SCALA_PROFILES
from t3_evidence import assemble_capability_result
from t3_evidence import assemble_input_set_result
from t3_evidence import benchmark_case_contract
from t3_evidence import canonical_sha256
from t3_evidence import require_portable_argv
from t3_evidence import require_sha
from t3_evidence import sha256_file
from t3_evidence import strict_json
from t3_evidence import strict_json_value
from t3_evidence import validate_correctness
from t3_evidence import validate_effective_jvm_evidence
from t3_evidence import validate_jmh_native_json
from t3_evidence import validate_measurement_ready_marker
from t3_evidence import write_exclusive_json


class CapabilityAssemblyError(ValueError):
    """Capability receipt가 누락·위조·실패했음을 나타낸다."""


def require_process(
    value: Any,
    field: str,
    *,
    expected_portable_argv: list[str],
    expected_runtime_argv: list[str],
    stdout_path: Path,
    stderr_path: Path,
) -> dict[str, Any]:
    if (
        not isinstance(value, dict)
        or type(value.get("exitCode")) is not int
        or value.get("exitCode") != 0
        or value.get("status", "PASS") != "PASS"
    ):
        raise CapabilityAssemblyError(f"PROCESS_RESULT_INVALID:{field}")
    portable = require_portable_argv(
        value.get("portableArgv"),
        f"{field}.argv",
    )
    if (
        portable != expected_portable_argv
        or value.get("portableArgvSha256")
        != canonical_sha256(expected_portable_argv)
    ):
        raise CapabilityAssemblyError(f"PROCESS_PORTABLE_ARGV_DRIFT:{field}")
    runtime_sha256 = canonical_sha256(expected_runtime_argv)
    runtime_keys = {
        key
        for key in ("runtimeArgvSha256", "commandArgvSha256")
        if key in value
    }
    if not runtime_keys or any(
        value.get(key) != runtime_sha256 for key in runtime_keys
    ):
        raise CapabilityAssemblyError(f"PROCESS_RUNTIME_ARGV_DRIFT:{field}")
    for path, key in (
        (stdout_path, "stdoutSha256"),
        (stderr_path, "stderrSha256"),
    ):
        if (
            path.is_symlink()
            or not path.is_file()
            or value.get(key) != sha256_file(path)
        ):
            raise CapabilityAssemblyError(
                f"PROCESS_LOG_BYTE_DRIFT:{field}:{key}"
            )
    if "evidenceSha256" in value:
        evidence = {
            key: item
            for key, item in value.items()
            if key != "evidenceSha256"
        }
        if value["evidenceSha256"] != canonical_sha256(evidence):
            raise CapabilityAssemblyError(
                f"PROCESS_EVIDENCE_HASH_DRIFT:{field}"
            )
    return value


def require_binary(path: Path, expected_sha256: Any, field: str) -> Path:
    if (
        not path.is_absolute()
        or path.is_symlink()
        or not path.is_file()
        or sha256_file(path) != expected_sha256
    ):
        raise CapabilityAssemblyError(f"TOOL_BINARY_IDENTITY_DRIFT:{field}")
    return path


def require_regular_file(path: Path, field: str) -> Path:
    if path.is_symlink() or not path.is_file():
        raise CapabilityAssemblyError(f"ARTIFACT_FILE_INVALID:{field}")
    return path


def source_paths(
    manifest: dict[str, Any],
    *,
    roles: set[str] | None = None,
) -> list[str]:
    files = manifest.get("files")
    if not isinstance(files, dict):
        raise CapabilityAssemblyError("SOURCE_MANIFEST_FILES_INVALID")
    result = []
    for path, metadata in files.items():
        if (
            not isinstance(path, str)
            or not isinstance(metadata, dict)
            or not isinstance(metadata.get("role"), str)
        ):
            raise CapabilityAssemblyError("SOURCE_MANIFEST_ENTRY_INVALID")
        if roles is None or metadata["role"] in roles:
            result.append(path)
    if not result:
        raise CapabilityAssemblyError("SOURCE_MANIFEST_SELECTION_EMPTY")
    return result


def absolute_sources(scala_root: Path, sources: list[str]) -> list[str]:
    return [str(scala_root / path) for path in sources]


def portable_sources(sources: list[str]) -> list[str]:
    return [f"SCALA_ROOT/{path}" for path in sources]


def semantic_portable_argv(
    command: list[str],
    *,
    scala_root: Path,
    semantic_root: Path,
    scala_cli: Path,
    scalafix: Path,
) -> list[str]:
    result = []
    for item in command:
        replaced = item.replace(str(scala_root), "SCALA_ROOT").replace(
            str(semantic_root),
            "SEMANTIC_EVIDENCE_ROOT",
        )
        if item == str(scala_cli):
            replaced = "SCALA_CLI_1_15_0"
        elif item == str(scalafix):
            replaced = "SCALAFIX_0_14_7"
        elif replaced.startswith("/"):
            replaced = (
                "ABSOLUTE_RUNTIME_INPUT_SHA256:"
                f"{hashlib.sha256(item.encode('utf-8')).hexdigest()}"
            )
        result.append(replaced)
    return result


def smoke_evidence(
    *,
    process: dict[str, Any],
    artifact: Path,
    fallback: str,
) -> dict[str, Any]:
    return {
        "compilerStatus": "stable",
        "argv": process["portableArgv"],
        "exitCode": process["exitCode"],
        "stdoutSha256": process["stdoutSha256"],
        "stderrSha256": process["stderrSha256"],
        "artifactSha256": sha256_file(artifact),
        "status": "PASS",
        "disposition": "ADOPT",
        "provenFallback": fallback,
        "fallbackExecuted": False,
    }


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scala-root", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--benchmark-plan", type=Path, required=True)
    parser.add_argument("--source-policy-config", type=Path, required=True)
    parser.add_argument("--source-manifest", type=Path, required=True)
    parser.add_argument("--compiler-profiles", type=Path, required=True)
    parser.add_argument("--toolchain-lock", type=Path, required=True)
    parser.add_argument("--merged-provenance", type=Path, required=True)
    parser.add_argument("--scala-cli-bin", type=Path, required=True)
    parser.add_argument("--scalafix-bin", type=Path, required=True)
    parser.add_argument("--scalafmt-bin", type=Path, required=True)
    parser.add_argument("--toolchain-result", type=Path, required=True)
    parser.add_argument("--hard-compiler", type=Path, required=True)
    parser.add_argument("--scalafmt", type=Path, required=True)
    parser.add_argument("--semantic", type=Path, required=True)
    parser.add_argument("--source-policy", type=Path, required=True)
    parser.add_argument("--jmh-run", type=Path, required=True)
    parser.add_argument("--jvm-allowlist", type=Path, required=True)
    parser.add_argument("--correctness-root", type=Path, required=True)
    parser.add_argument("--qualification", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()
    try:
        if arguments.output_dir.exists() or arguments.output_dir.is_symlink():
            raise CapabilityAssemblyError("OUTPUT_DIRECTORY_MUST_BE_NEW")
        if (
            not arguments.scala_root.is_absolute()
            or arguments.scala_root.is_symlink()
            or not arguments.scala_root.is_dir()
        ):
            raise CapabilityAssemblyError("SCALA_ROOT_INVALID")
        scala_root = arguments.scala_root.resolve(strict=True)
        s1_root = scala_root.parent
        expected_repo_paths = {
            "plan": s1_root / "contract/capability-smoke-plan.v1.json",
            "benchmark_plan": s1_root / "benchmarks/benchmark-plan.v1.json",
            "source_policy_config": (
                s1_root / "contract/scala-source-policy.v1.json"
            ),
            "source_manifest": scala_root / "source-inputs.v1.json",
            "compiler_profiles": scala_root / "compiler-profiles.v1.json",
            "toolchain_lock": scala_root / "toolchain-lock.v1.json",
            "merged_provenance": (
                s1_root / "contract/toolchain-provenance.v1.json"
            ),
        }
        for field, expected in expected_repo_paths.items():
            actual = getattr(arguments, field)
            if (
                not actual.is_absolute()
                or actual.is_symlink()
                or not actual.is_file()
                or actual.resolve(strict=True) != expected
            ):
                raise CapabilityAssemblyError(
                    f"FROZEN_REPO_INPUT_PATH_DRIFT:{field}"
                )
        plan = strict_json(arguments.plan)
        benchmark_plan = strict_json(arguments.benchmark_plan)
        policy = strict_json(arguments.source_policy_config)
        manifest = strict_json(arguments.source_manifest)
        compiler_profiles = strict_json(arguments.compiler_profiles)
        toolchain = strict_json(arguments.toolchain_result)
        toolchain_lock = strict_json(arguments.toolchain_lock)
        strict_json(arguments.merged_provenance)
        compiler = strict_json(arguments.hard_compiler)
        scalafmt = strict_json(arguments.scalafmt)
        semantic = strict_json(arguments.semantic)
        source_policy = strict_json(arguments.source_policy)
        jmh = strict_json(arguments.jmh_run)
        jvm_allowlist = strict_json(arguments.jvm_allowlist)
        qualification = strict_json(arguments.qualification)
        correctness_paths = {
            profile: (
                arguments.correctness_root
                / profile
                / "scala-profile-correctness-result.v1.json"
            )
            for profile in SCALA_PROFILES
        }
        correctness = {
            profile: strict_json(path)
            for profile, path in correctness_paths.items()
        }
        validate_correctness(correctness)

        manifest_sha = sha256_file(arguments.source_manifest)
        compiler_profiles_sha = sha256_file(arguments.compiler_profiles)
        toolchain_lock_sha = sha256_file(arguments.toolchain_lock)
        merged_provenance_sha = sha256_file(arguments.merged_provenance)
        capability_plan_sha = sha256_file(arguments.plan)
        benchmark_plan_sha = sha256_file(arguments.benchmark_plan)
        scala_cli_sha = toolchain_lock.get("scalaCli", {}).get("binarySha256")
        scalafix_sha = toolchain_lock.get("scalafix", {}).get("binarySha256")
        scalafmt_sha = toolchain_lock.get("scalafmt", {}).get(
            "executableSha256"
        )
        java_sha = toolchain_lock.get("jdk", {}).get(
            "javaExecutableSha256"
        )
        scala_cli = require_binary(
            arguments.scala_cli_bin,
            scala_cli_sha,
            "scalaCli",
        )
        scalafix_bin = require_binary(
            arguments.scalafix_bin,
            scalafix_sha,
            "scalafix",
        )
        scalafmt_bin = require_binary(
            arguments.scalafmt_bin,
            scalafmt_sha,
            "scalafmt",
        )
        if (
            benchmark_plan.get("schemaVersion")
            != "s1.4x-benchmark-plan-v1"
            or compiler_profiles.get("schemaVersion")
            != "s1.4x-scala-compiler-profiles-v1"
            or toolchain_lock.get("schemaVersion")
            != "s1.4x-scala-toolchain-lock-v1"
            or toolchain_lock.get("mergedToolchainProvenanceSha256")
            != merged_provenance_sha
            or toolchain.get("schemaVersion")
            != "s1.4x-scala-toolchain-identity-result-v1"
            or toolchain.get("status") != "PASS"
            or toolchain.get("toolchainLockSha256")
            != toolchain_lock_sha
            or toolchain.get("mergedProvenanceSha256")
            != merged_provenance_sha
            or compiler.get("schemaVersion")
            != "s1.4x-scala-hard-compiler-result-v1"
            or compiler.get("aggregateStatus") != "PASS"
            or compiler.get("profileId") != "A"
            or compiler.get("sourceInputManifestSha256") != manifest_sha
            or compiler.get("compilerProfilesSha256")
            != compiler_profiles_sha
            or compiler.get("toolchainLockSha256") != toolchain_lock_sha
            or compiler.get("resolvedBinarySha256") != scala_cli_sha
            or scalafmt.get("schemaVersion")
            != "s1.4x-scala-scalafmt-idempotence-result-v1"
            or scalafmt.get("status") != "PASS"
            or scalafmt.get("sourceInputManifestSha256") != manifest_sha
            or scalafmt.get("toolchainLockSha256") != toolchain_lock_sha
            or scalafmt.get("scalafmtArtifact", {}).get(
                "executableSha256"
            )
            != scalafmt_sha
            or semantic.get("schemaVersion")
            != "s1.4x-scala-semantic-policy-receipt-v1"
            or semantic.get("status") != "PASS"
            or semantic.get("sourceInputManifestSha256") != manifest_sha
            or semantic.get("scalafix", {}).get("binarySha256")
            != toolchain_lock.get("scalafix", {}).get("binarySha256")
            or source_policy.get("schemaVersion")
            != "s1.4x-scala-source-policy-result-v1"
            or source_policy.get("aggregateStatus") != "PASS"
            or source_policy.get("sourceInputManifestSha256")
            != manifest_sha
            or source_policy.get("semanticReceiptSha256")
            != sha256_file(arguments.semantic)
            or jmh.get("schemaVersion")
            != "s1.4x-scala-jmh-run-result-v1"
            or jmh.get("runMode") != "smoke"
            or jmh.get("aggregateStatus") != "PASS"
            or jmh.get("profileId") != "A"
            or jmh.get("sourceInputManifestSha256") != manifest_sha
            or jmh.get("benchmarkPlanSha256") != benchmark_plan_sha
            or jmh.get("scalaCliBinarySha256") != scala_cli_sha
            or jmh.get("compilerProfilesSha256")
            != compiler_profiles_sha
            or jmh.get("jvmArgumentAllowlistSha256")
            != sha256_file(arguments.jvm_allowlist)
            or jvm_allowlist.get("schemaVersion")
            != "s1.4x-scala-jvm-argument-allowlist-v1"
            or jvm_allowlist.get("status") != "PASS"
            or jvm_allowlist.get("benchmarkPlanSha256")
            != benchmark_plan_sha
            or jvm_allowlist.get("capabilitySmokePlanSha256")
            != capability_plan_sha
            or jvm_allowlist.get("toolchainLockSha256")
            != toolchain_lock_sha
            or jvm_allowlist.get("javaExecutableSha256") != java_sha
            or qualification.get("schemaVersion")
            != "s1.4x-scala-profile-qualification-v1"
            or qualification.get("status") != "PASS"
            or qualification.get("sourceInputManifestSha256")
            != manifest_sha
            or qualification.get("benchmarkPlanSha256")
            != benchmark_plan_sha
            or qualification.get("scalaCliBinarySha256") != scala_cli_sha
            or qualification.get("jvmArgumentAllowlistSha256")
            != sha256_file(arguments.jvm_allowlist)
            or any(
                correctness[profile].get("toolchainLockSha256")
                != toolchain_lock_sha
                or correctness[profile].get("scalaCliBinarySha256")
                != scala_cli_sha
                for profile in SCALA_PROFILES
            )
        ):
            raise CapabilityAssemblyError("CAPABILITY_INPUT_IDENTITY_FAILED")

        all_inputs = source_paths(manifest)
        benchmark_inputs = source_paths(
            manifest,
            roles={"configuration", "main", "benchmark"},
        )
        unit_inputs = source_paths(
            manifest,
            roles={"configuration", "main", "test"},
        )
        if (
            compiler_profiles.get("profiles", {})
            .get("A", {})
            .get("scalaCliArguments")
            != PROFILE_CLI_ARGUMENTS["A"]
            or compiler_profiles.get("profiles", {})
            .get("A", {})
            .get("additionalOptions")
            != PROFILE_OPTIONS["A"]
        ):
            raise CapabilityAssemblyError("BASELINE_PROFILE_MAPPING_DRIFT")

        toolchain_runtime = [
            str(scala_root / "tools/assert-toolchain.sh"),
            "--lock",
            str(arguments.toolchain_lock),
            "--merged-provenance",
            str(arguments.merged_provenance),
        ]
        toolchain_portable = [
            "SCALA_ROOT/tools/assert-toolchain.sh",
            "--lock",
            "SCALA_ROOT/toolchain-lock.v1.json",
            "--merged-provenance",
            "S1_ROOT/contract/toolchain-provenance.v1.json",
        ]
        toolchain_root = arguments.toolchain_result.parent
        toolchain_process = require_process(
            toolchain,
            "toolchain",
            expected_portable_argv=toolchain_portable,
            expected_runtime_argv=toolchain_runtime,
            stdout_path=toolchain_root / "toolchain.stdout",
            stderr_path=toolchain_root / "toolchain.stderr",
        )

        compiler_runtime = [
            str(scala_cli),
            "--power",
            "compile",
            *absolute_sources(scala_root, all_inputs),
            "--test",
            "--server=false",
            "--jvm",
            "system",
            "--coursier-validate-checksums",
            *PROFILE_CLI_ARGUMENTS["A"],
        ]
        compiler_portable = [
            "SCALA_CLI_1_15_0",
            "--power",
            "compile",
            *portable_sources(all_inputs),
            "--test",
            "--server=false",
            "--jvm",
            "system",
            "--coursier-validate-checksums",
            *PROFILE_CLI_ARGUMENTS["A"],
        ]
        compiler_root = arguments.hard_compiler.parent
        compiler_process_value = compiler.get("fullCompile")
        if (
            not isinstance(compiler_process_value, dict)
            or compiler_process_value.get("processId")
            != "profile-A-full-compile"
        ):
            raise CapabilityAssemblyError("COMPILER_PROCESS_ID_DRIFT")
        compiler_process = require_process(
            compiler_process_value,
            "hardCompiler",
            expected_portable_argv=compiler_portable,
            expected_runtime_argv=compiler_runtime,
            stdout_path=(
                compiler_root / "logs/profile-A-full-compile.stdout"
            ),
            stderr_path=(
                compiler_root / "logs/profile-A-full-compile.stderr"
            ),
        )

        scalafmt_runtime = [
            str(scala_cli),
            "--power",
            "format",
            *absolute_sources(scala_root, all_inputs),
            "--server=false",
            "--scalafmt-version",
            "3.11.4",
            "--scalafmt-conf",
            str(scala_root / ".scalafmt.conf"),
            "--scalafmt-launcher",
            str(scalafmt_bin),
            "--offline",
            "--check",
        ]
        scalafmt_portable = [
            "SCALA_CLI_1_15_0",
            "--power",
            "format",
            *portable_sources(all_inputs),
            "--server=false",
            "--scalafmt-version",
            "3.11.4",
            "--scalafmt-conf",
            "SCALA_ROOT/.scalafmt.conf",
            "--scalafmt-launcher",
            "SCALAFMT_3_11_4",
            "--offline",
            "--check",
        ]
        scalafmt_root = arguments.scalafmt.parent
        scalafmt_process_value = scalafmt.get("nonMutatingCheck")
        if (
            not isinstance(scalafmt_process_value, dict)
            or scalafmt_process_value.get("downloadLineCount") != 0
        ):
            raise CapabilityAssemblyError("SCALAFMT_PROCESS_POLICY_DRIFT")
        scalafmt_process = require_process(
            scalafmt_process_value,
            "scalafmt",
            expected_portable_argv=scalafmt_portable,
            expected_runtime_argv=scalafmt_runtime,
            stdout_path=(
                scalafmt_root / "logs/real-source-non-mutating-check.stdout"
            ),
            stderr_path=(
                scalafmt_root / "logs/real-source-non-mutating-check.stderr"
            ),
        )

        semantic_root = arguments.semantic.parent
        semantic_classpath_path = require_regular_file(
            semantic_root / "semantic-classpath.txt",
            "semanticClasspath",
        )
        classpath_bytes = semantic_classpath_path.read_bytes()
        if (
            not classpath_bytes.endswith(b"\n")
            or b"\n" in classpath_bytes[:-1]
        ):
            raise CapabilityAssemblyError("SEMANTIC_CLASSPATH_FILE_INVALID")
        semantic_classpath = classpath_bytes[:-1].decode("utf-8")
        semanticdb_root = semantic_root / "semanticdb"
        if semanticdb_root.is_symlink() or not semanticdb_root.is_dir():
            raise CapabilityAssemblyError("SEMANTICDB_ROOT_INVALID")
        semantic_compile_runtime = [
            str(scala_cli),
            "--power",
            "compile",
            *absolute_sources(scala_root, all_inputs),
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
        if (
            semantic.get("semanticdb", {}).get("rootPath")
            != str(semanticdb_root)
            or semantic.get("semanticdb", {}).get(
                "compileCommandArgvSha256"
            )
            != canonical_sha256(semantic_compile_runtime)
        ):
            raise CapabilityAssemblyError("SEMANTICDB_COMPILE_COMMAND_DRIFT")
        syntactic_runtime = [
            str(scalafix_bin),
            "--check",
            "--syntactic",
            "--scala-version",
            "3.8.4",
            "--config",
            str(scala_root / ".scalafix.conf"),
            "--rules",
            "DisableSyntax",
            "--files",
            *absolute_sources(scala_root, all_inputs),
        ]
        syntactic_portable = semantic_portable_argv(
            syntactic_runtime,
            scala_root=scala_root,
            semantic_root=semantic_root,
            scala_cli=scala_cli,
            scalafix=scalafix_bin,
        )
        explicit_runtime = [
            str(scalafix_bin),
            "--check",
            "--scala-version",
            "3.8.4",
            "--classpath",
            semantic_classpath,
            "--sourceroot",
            str(scala_root),
            "--semanticdb-targetroots",
            str(semanticdb_root),
            "--config",
            str(scala_root / ".scalafix.conf"),
            "--rules",
            "ExplicitResultTypes",
            "--files",
            *absolute_sources(scala_root, all_inputs),
        ]
        explicit_portable = semantic_portable_argv(
            explicit_runtime,
            scala_root=scala_root,
            semantic_root=semantic_root,
            scala_cli=scala_cli,
            scalafix=scalafix_bin,
        )
        syntactic_process = require_process(
            semantic.get("execution", {}).get("cleanSyntactic"),
            "scalafix",
            expected_portable_argv=syntactic_portable,
            expected_runtime_argv=syntactic_runtime,
            stdout_path=semantic_root / "logs/clean-syntactic.stdout",
            stderr_path=semantic_root / "logs/clean-syntactic.stderr",
        )
        explicit_process = require_process(
            semantic.get("execution", {}).get("cleanExplicitResultTypes"),
            "explicitResultTypes",
            expected_portable_argv=explicit_portable,
            expected_runtime_argv=explicit_runtime,
            stdout_path=(
                semantic_root / "logs/clean-explicit-result-types.stdout"
            ),
            stderr_path=(
                semantic_root / "logs/clean-explicit-result-types.stderr"
            ),
        )
        if (
            semantic.get("scalafix", {}).get(
                "syntacticCommandArgvSha256"
            )
            != canonical_sha256(syntactic_runtime)
            or semantic.get("scalafix", {}).get(
                "explicitResultTypesCommandArgvSha256"
            )
            != canonical_sha256(explicit_runtime)
        ):
            raise CapabilityAssemblyError("SEMANTIC_COMMAND_LINK_DRIFT")

        source_core = Path(f"{arguments.source_policy}.core")
        source_runtime = [
            "python3",
            str(scala_root / "tools/check_source_policy.py"),
            "--scala-root",
            str(scala_root),
            "--policy",
            str(arguments.source_policy_config),
            "--manifest",
            str(arguments.source_manifest),
            "--semantic-receipt",
            str(arguments.semantic),
            "--require-git-source-equality",
            "--output",
            str(source_core),
        ]
        source_portable = [
            "python3",
            "SCALA_ROOT/tools/check_source_policy.py",
            "--scala-root",
            "SCALA_ROOT",
            "--policy",
            "S1_ROOT/contract/scala-source-policy.v1.json",
            "--manifest",
            "SCALA_ROOT/source-inputs.v1.json",
            "--semantic-receipt",
            "SEMANTIC_RECEIPT",
            "--require-git-source-equality",
            "--output",
            "SOURCE_POLICY_RESULT",
        ]
        require_regular_file(source_core, "sourcePolicyCore")
        if source_policy.get("coreResultSha256") != sha256_file(source_core):
            raise CapabilityAssemblyError("SOURCE_POLICY_CORE_LINK_DRIFT")
        source_process = require_process(
            source_policy.get("process"),
            "sourcePolicy",
            expected_portable_argv=source_portable,
            expected_runtime_argv=source_runtime,
            stdout_path=Path(f"{arguments.source_policy}.stdout"),
            stderr_path=Path(f"{arguments.source_policy}.stderr"),
        )

        jmh_root = arguments.jmh_run.parent
        native_path = require_regular_file(
            jmh_root / "native.json",
            "jmhNative",
        )
        fork_path = require_regular_file(
            jmh_root / "fork-evidence.normalized.json",
            "jmhForkEvidence",
        )
        effective_path = require_regular_file(
            jmh_root / "scala-effective-jvm-args-result.v1.json",
            "jmhEffectiveArguments",
        )
        validation_path = require_regular_file(
            jmh_root / "scala-jmh-native-validation.v1.json",
            "jmhNativeValidation",
        )
        marker_path = require_regular_file(
            jmh_root / "measurement-ready.v1.json",
            "jmhMeasurementReady",
        )
        effective = validate_effective_jvm_evidence(
            strict_json_value(fork_path),
            expected_forks=1,
            allowlist=jvm_allowlist,
            allowlist_sha256=sha256_file(arguments.jvm_allowlist),
        )
        if strict_json(effective_path) != effective:
            raise CapabilityAssemblyError("JMH_EFFECTIVE_ARGUMENT_DRIFT")
        benchmark, logical_operations, include_regex = (
            benchmark_case_contract(
                benchmark_plan,
                str(jmh.get("caseId")),
            )
        )
        native_validation = validate_jmh_native_json(
            strict_json_value(native_path),
            expected_benchmark=benchmark,
            expected_forks=1,
            effective_jvm_arguments=effective["effectiveJvmArguments"],
            expected_warmup_iterations=1,
            expected_warmup_time="200ms",
            expected_measurement_iterations=1,
            expected_measurement_time="200ms",
            logical_operations_per_invocation=logical_operations,
        )
        if strict_json(validation_path) != native_validation:
            raise CapabilityAssemblyError("JMH_NATIVE_VALIDATION_DRIFT")
        marker_sha256 = validate_measurement_ready_marker(
            marker_path,
            expected_benchmark_plan_sha256=benchmark_plan_sha,
            expected_case_id=str(jmh.get("caseId")),
            expected_profile="A",
            expected_run_mode="smoke",
        )
        jmh_common = [
            "--server=false",
            "--jvm",
            "system",
            "--coursier-validate-checksums",
            *PROFILE_CLI_ARGUMENTS["A"],
            "--jmh",
            "--jmh-version",
            "1.37",
            "--",
            "-bm",
            "avgt",
            "-tu",
            "ns",
            "-t",
            "1",
            "-f",
            "1",
            "-wi",
            "1",
            "-i",
            "1",
            "-w",
            "200ms",
            "-r",
            "200ms",
            "-rf",
            "json",
        ]
        jmh_runtime = [
            str(scala_cli),
            "--power",
            "run",
            *absolute_sources(scala_root, benchmark_inputs),
            *jmh_common,
            "-rff",
            str(native_path),
            include_regex,
        ]
        jmh_portable = [
            "SCALA_CLI_1_15_0",
            "--power",
            "run",
            *portable_sources(benchmark_inputs),
            *jmh_common,
            "-rff",
            "EVIDENCE_ROOT/native.json",
            include_regex,
        ]
        if (
            set(jmh) != JMH_RUN_RESULT_KEYS
            or jmh.get("caseId") not in {
                item.get("caseId") for item in benchmark_plan.get("cases", [])
            }
            or jmh.get("logicalOperationsPerInvocation")
            != logical_operations
            or jmh.get("rawScoreNsPerInvocation")
            != native_validation["rawScoreNsPerInvocation"]
            or jmh.get("normalizedScoreNsPerLogicalOperation")
            != native_validation[
                "normalizedScoreNsPerLogicalOperation"
            ]
            or jmh.get("profileOptionsSha256")
            != canonical_sha256(PROFILE_OPTIONS["A"])
            or jmh.get("inputPaths") != benchmark_inputs
            or jmh.get("rawNativeJsonSha256") != sha256_file(native_path)
            or jmh.get("effectiveJvmArgsSha256")
            != sha256_file(effective_path)
            or jmh.get("nativeValidationSha256")
            != sha256_file(validation_path)
            or jmh.get("measurementReadyMarkerSha256") != marker_sha256
        ):
            raise CapabilityAssemblyError("JMH_RUN_ARTIFACT_LINK_DRIFT")
        jmh_process = require_process(
            jmh,
            "jmh",
            expected_portable_argv=jmh_portable,
            expected_runtime_argv=jmh_runtime,
            stdout_path=jmh_root / "jmh.stdout",
            stderr_path=jmh_root / "jmh.stderr",
        )

        unit_a_path = (
            arguments.correctness_root
            / "A"
            / "scala-profile-unit-test-result.v1.json"
        )
        unit_a = strict_json(unit_a_path)
        unit_runtime = [
            str(scala_cli),
            "test",
            *absolute_sources(scala_root, unit_inputs),
            "--server=false",
            "--jvm",
            "system",
            "--require-tests",
            "--coursier-validate-checksums",
            *PROFILE_CLI_ARGUMENTS["A"],
        ]
        unit_portable = [
            "SCALA_CLI_1_15_0",
            "test",
            *portable_sources(unit_inputs),
            "--server=false",
            "--jvm",
            "system",
            "--require-tests",
            "--coursier-validate-checksums",
            *PROFILE_CLI_ARGUMENTS["A"],
        ]
        if (
            unit_a.get("schemaVersion")
            != "s1.4x-scala-profile-unit-test-result-v1"
            or unit_a.get("profileId") != "A"
            or unit_a.get("compilerProfilesSha256")
            != compiler_profiles_sha
            or unit_a.get("sourceInputManifestSha256") != manifest_sha
            or unit_a.get("scalaCliBinarySha256") != scala_cli_sha
            or unit_a.get("profileOptionsSha256")
            != canonical_sha256(PROFILE_OPTIONS["A"])
            or unit_a.get("inputPaths") != unit_inputs
        ):
            raise CapabilityAssemblyError("UNIT_TEST_RESULT_IDENTITY_DRIFT")
        unit_process = require_process(
            unit_a,
            "profileCorrectness",
            expected_portable_argv=unit_portable,
            expected_runtime_argv=unit_runtime,
            stdout_path=unit_a_path.parent / "unit-test.stdout",
            stderr_path=unit_a_path.parent / "unit-test.stderr",
        )
        if (
            correctness["A"]["matrix"]["unitTestResultSha256"]
            != sha256_file(unit_a_path)
        ):
            raise CapabilityAssemblyError("UNIT_TEST_RESULT_LINK_DRIFT")

        tracked_inputs = git_source_files(
            arguments.scala_root,
            production_roots(policy),
        )
        manifest_inputs = list(manifest["files"])
        profile_inputs = set(qualification.get("profileRunInputPaths", []))
        for profile in SCALA_PROFILES:
            profile_inputs.update(
                correctness[profile]["profileRunInputPaths"]
            )
        input_result = assemble_input_set_result(
            manifest=manifest,
            manifest_sha256=manifest_sha,
            compiler_profile_sha256=compiler_profiles_sha,
            input_sets={
                "tracked": tracked_inputs,
                "manifest": manifest_inputs,
                "format": scalafmt.get("checkedFiles"),
                "compile": compiler.get("compileInputPaths"),
                "lint": semantic.get("checkedFiles"),
                "profileRun": sorted(
                    profile_inputs,
                    key=lambda value: value.encode("utf-8"),
                ),
            },
        )

        arguments.output_dir.mkdir(parents=True)
        input_result_path = (
            arguments.output_dir
            / "scala-input-set-equality-result.v1.json"
        )
        write_exclusive_json(input_result_path, input_result)
        correctness_closure = {
            "schemaVersion": "s1.4x-scala-profile-correctness-closure-v1",
            "profiles": {
                profile: sha256_file(correctness_paths[profile])
                for profile in SCALA_PROFILES
            },
            "sourceInputManifestSha256": manifest_sha,
            "inputSetResultSha256": sha256_file(input_result_path),
            "mismatchCount": 0,
            "aggregateStatus": "PASS",
        }
        correctness_closure_path = (
            arguments.output_dir
            / "scala-profile-correctness-closure.v1.json"
        )
        write_exclusive_json(correctness_closure_path, correctness_closure)

        smoke_plan = {
            item["smokeId"]: item
            for item in plan["languages"]["scala"]["smokes"]
        }
        evidence_specs = {
            "scala-toolchain-identity": (
                toolchain_process,
                arguments.toolchain_result,
            ),
            "scala-stable-compiler-profile": (
                compiler_process,
                arguments.hard_compiler,
            ),
            "scala-scalafmt-idempotence": (
                scalafmt_process,
                arguments.scalafmt,
            ),
            "scala-scalafix-disable-syntax": (
                syntactic_process,
                arguments.semantic,
            ),
            "scala-source-policy": (
                source_process,
                arguments.source_policy,
            ),
            "scala-explicit-result-types": (
                explicit_process,
                arguments.semantic,
            ),
            "scala-jmh-native-json": (
                jmh_process,
                arguments.jmh_run,
            ),
            "scala-profile-abc-correctness": (
                unit_process,
                correctness_closure_path,
            ),
        }
        evidence = {
            smoke_id: smoke_evidence(
                process=process,
                artifact=artifact,
                fallback=smoke_plan[smoke_id]["provenFallback"],
            )
            for smoke_id, (process, artifact) in evidence_specs.items()
        }
        write_exclusive_json(
            arguments.output_dir
            / "scala-capability-evidence-index.v1.json",
            evidence,
        )
        result = assemble_capability_result(
            plan=plan,
            plan_sha256=sha256_file(arguments.plan),
            toolchain_identity_sha256=sha256_file(
                arguments.toolchain_result
            ),
            evidence=evidence,
        )
        write_exclusive_json(
            arguments.output_dir
            / "scala-capability-smoke-result.v1.json",
            result,
        )
    except (
        KeyError,
        OSError,
        UnicodeError,
        ValueError,
        CapabilityAssemblyError,
    ) as error:
        print(f"SCALA_CAPABILITY_ASSEMBLY_FAIL:{error}", file=sys.stderr)
        return 1
    print(
        "SCALA_CAPABILITY_ASSEMBLY_PASS "
        f"smokes={len(result['results'])} "
        f"inputSetSha256={sha256_file(input_result_path)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
