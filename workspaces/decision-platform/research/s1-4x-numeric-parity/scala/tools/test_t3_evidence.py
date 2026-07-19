#!/usr/bin/env python3
"""Scala T3 portable evidence contracts and frozen selector regression tests."""

from __future__ import annotations

import importlib.util
import hashlib
import json
import math
import os
import sys
import tempfile
from pathlib import Path


SCALA_ROOT = Path(__file__).resolve().parents[1]
S1_ROOT = SCALA_ROOT.parent
TOOLS_ROOT = SCALA_ROOT / "tools"
SHA = "1" * 64
PROFILE_OPTIONS = {
    "A": [],
    "B": ["-opt"],
    "C": ["-opt", "-opt-inline:ai.trading.coach.s14x.**"],
}


def canonical_sha256(value: object) -> str:
    payload = json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def load_module():
    path = TOOLS_ROOT / "t3_evidence.py"
    specification = importlib.util.spec_from_file_location("t3_evidence", path)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    sys.modules["t3_evidence"] = module
    specification.loader.exec_module(module)
    return module


def expect_t3_error(module, operation, message: str) -> None:
    try:
        operation()
    except module.T3EvidenceError:
        pass
    else:
        raise AssertionError(message)


def semantic_receipt(policy: dict, matrix: dict) -> dict:
    checked = ["project.scala", "src/main/scala/example.scala"]
    negative = []
    for index, fixture in enumerate(matrix["fixtures"]):
        expected = fixture["expectedSymbol"]
        disposition = fixture["expectedDisposition"]
        detected = []
        if disposition == "SEMANTIC_REJECT" and expected != "ExplicitResultTypes":
            detected = [
                {
                    "policySymbol": expected,
                    "resolvedSymbol": f"resolved/{index}.",
                }
            ]
        negative.append(
            {
                "fixtureId": fixture["fixtureId"],
                "expectedPolicySymbol": expected,
                "expectedDisposition": disposition,
                "detectedResolvedSymbols": detected,
                "commandArgvSha256": f"{index + 2:064x}",
                "exitCode": 1,
                "stdoutSha256": SHA,
                "stderrSha256": SHA,
                "evidenceSha256": f"{index + 100:064x}",
                "status": "PASS",
            }
        )
    execution = {
        "cleanSyntactic": {
            "commandArgvSha256": "a" * 64,
            "exitCode": 0,
            "stdoutSha256": SHA,
            "stderrSha256": SHA,
            "evidenceSha256": "b" * 64,
        },
        "cleanExplicitResultTypes": {
            "commandArgvSha256": "c" * 64,
            "exitCode": 0,
            "stdoutSha256": SHA,
            "stderrSha256": SHA,
            "evidenceSha256": "d" * 64,
        },
        "cleanCustomSemanticRule": {
            "commandArgvSha256": "e" * 64,
            "exitCode": 0,
            "stdoutSha256": SHA,
            "stderrSha256": SHA,
            "evidenceSha256": "f" * 64,
        },
    }
    return {
        "schemaVersion": "s1.4x-scala-semantic-policy-receipt-v1",
        "policySha256": "2" * 64,
        "sourceInputManifestSha256": "3" * 64,
        "checkedFiles": checked,
        "sourceTreeSha256": "4" * 64,
        "checkerMode": "semanticdb",
        "semanticSmokeStatus": "PASS",
        "semanticdb": {
            "rootPath": "/local/evidence/semanticdb",
            "rootSha256": "5" * 64,
            "fileCount": len(checked),
            "classpathSha256": "6" * 64,
            "compileCommandArgvSha256": "7" * 64,
        },
        "scalafix": {
            "binaryPath": "/local/scalafix",
            "binarySha256": "8" * 64,
            "version": "0.14.7",
            "commandArgvSha256": "9" * 64,
            "explicitResultTypesCommandArgvSha256": "c" * 64,
            "customRuleCommandArgvSha256": "e" * 64,
            "syntacticCommandArgvSha256": "a" * 64,
        },
        "rule": {
            "sourcePath": "/local/rule.scala",
            "sourceSha256": "0" * 64,
            "classpathSha256": "a" * 64,
        },
        "execution": {
            "startedAt": "2026-07-18T00:00:00.000000Z",
            "finishedAt": "2026-07-18T00:00:01.000000Z",
            **execution,
        },
        "negativeMatrix": negative,
        "status": "PASS",
    }


def qualification(
    plan: dict,
    scores: dict[str, float | list[float]],
) -> dict:
    policy = plan["scalaProfileQualification"]
    blocks = []
    all_effective_hashes = []
    for repetition, order in enumerate(policy["profileOrderBlocks"], start=1):
        measurements = []
        profile_evidence = []
        for profile in order:
            effective_hashes = []
            for case_id in policy["qualificationCaseOrder"]:
                score_value = scores[profile]
                score = (
                    score_value[repetition - 1]
                    if isinstance(score_value, list)
                    else score_value
                )
                sequence = len(measurements) + repetition * 100
                effective_hash = f"{sequence + 400:064x}"
                effective_hashes.append(effective_hash)
                all_effective_hashes.append(effective_hash)
                measurements.append(
                    {
                        "profileId": profile,
                        "caseId": case_id,
                        "scoreNsPerInvocation": score,
                        "rawNativeJsonSha256": f"{sequence + 200:064x}",
                        "effectiveJvmArgsSha256": effective_hash,
                        "jmhRunResultSha256": f"{sequence + 600:064x}",
                    }
                )
            profile_index = order.index(profile)
            profile_evidence.append(
                {
                    "profileId": profile,
                    "plannedCaseOrder": policy["qualificationCaseOrder"],
                    "actualCaseOrder": policy["qualificationCaseOrder"],
                    "startedAt": (
                        f"2026-07-18T00:{repetition:02d}:{profile_index * 2:02d}.000000Z"
                    ),
                    "endedAt": (
                        f"2026-07-18T00:{repetition:02d}:{profile_index * 2 + 1:02d}.000000Z"
                    ),
                    "hostValiditySha256": f"{repetition * 10 + profile_index:064x}",
                    "scalaCliBinarySha256": "a" * 64,
                    "profileOptionsSha256": canonical_sha256(
                        PROFILE_OPTIONS[profile]
                    ),
                    "sourceInputManifestSha256": "3" * 64,
                    "effectiveJvmArgsSha256": canonical_sha256(
                        effective_hashes
                    ),
                    "caseCount": len(policy["qualificationCaseOrder"]),
                }
            )
        blocks.append(
            {
                "outerRepetition": repetition,
                "plannedProfileOrder": order,
                "actualProfileOrder": order,
                "hostValiditySha256": canonical_sha256(
                    [
                        item["hostValiditySha256"]
                        for item in profile_evidence
                    ]
                ),
                "effectiveJvmArgsSha256": canonical_sha256(
                    [
                        item["effectiveJvmArgsSha256"]
                        for item in profile_evidence
                    ]
                ),
                "profileEvidence": profile_evidence,
                "measurements": measurements,
            }
        )
    return {
        "schemaVersion": "s1.4x-scala-profile-qualification-v1",
        "benchmarkPlanSha256": SHA,
        "selectorConfigSha256": canonical_sha256(policy),
        "sourceInputManifestSha256": "3" * 64,
        "profileOptionsSha256": canonical_sha256(PROFILE_OPTIONS),
        "scalaCliBinarySha256": "a" * 64,
        "jvmArgumentAllowlistSha256": "b" * 64,
        "profileRunInputPaths": [
            "benchmarks/example.scala",
            "project.scala",
            "selected-profile.scala",
            "src/main/scala/example.scala",
        ],
        "effectiveJvmArgsClosureSha256": canonical_sha256(
            all_effective_hashes
        ),
        "blocks": blocks,
        "status": "PASS",
    }


def correctness(
    *,
    compiler_profiles_sha256: str = "2" * 64,
    source_manifest_sha256: str = "3" * 64,
    profile_run_input_paths: list[str] | None = None,
    toolchain_lock_sha256: str = "4" * 64,
    scala_cli_binary_sha256: str = "a" * 64,
) -> dict:
    inputs = profile_run_input_paths or [
        "project.scala",
        "selected-profile.scala",
        "src/main/scala/example.scala",
        "src/test/scala/example.scala",
    ]
    return {
        profile: {
            "schemaVersion": "s1.4x-scala-profile-correctness-v1",
            "profileId": profile,
            "compilerProfilesSha256": compiler_profiles_sha256,
            "profileOptions": PROFILE_OPTIONS[profile],
            "profileOptionsSha256": canonical_sha256(PROFILE_OPTIONS[profile]),
            "sourceInputManifestSha256": source_manifest_sha256,
            "toolchainLockSha256": toolchain_lock_sha256,
            "scalaCliBinarySha256": scala_cli_binary_sha256,
            "profileRunInputPaths": inputs,
            "candidateSha256": f"{index + 10:064x}",
            "matrix": {
                "candidateResultSha256": f"{index + 15:064x}",
                "semanticResultSha256": f"{index + 16:064x}",
                "unitTestResultSha256": f"{index + 20:064x}",
                "unitStdoutSha256": f"{index + 21:064x}",
                "unitStderrSha256": f"{index + 22:064x}",
                "canonicalComparisonSha256": f"{index + 30:064x}",
                "semanticComparisonSha256": f"{index + 40:064x}",
                "propertyReportSha256": f"{index + 50:064x}",
                "registryReportSha256": f"{index + 60:064x}",
                "propertyExecutionEvidenceSha256": f"{index + 70:064x}",
                "propertyPlanSha256": f"{index + 80:064x}",
                "propertySeedCorpusSha256": f"{index + 90:064x}",
                "functionRegistrySha256": f"{index + 100:064x}",
                "errorRegistrySha256": f"{index + 110:064x}",
            },
            "mismatchCount": 0,
            "status": "PASS",
        }
        for index, profile in enumerate(("A", "B", "C"))
    }


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )


def selector_fixture(
    module,
    *,
    root: Path,
    plan: dict,
    scores: dict[str, float | list[float]],
) -> dict:
    """63개 qualification case의 raw byte tree를 실제 selector 입력으로 만든다."""

    root.mkdir(parents=True)
    source_manifest_path = SCALA_ROOT / "source-inputs.v1.json"
    compiler_profiles_path = SCALA_ROOT / "compiler-profiles.v1.json"
    selected_source_path = SCALA_ROOT / "selected-profile.scala"
    toolchain_lock_path = SCALA_ROOT / "toolchain-lock.v1.json"
    merged_provenance_path = S1_ROOT / "contract/toolchain-provenance.v1.json"
    capability_plan_path = S1_ROOT / "contract/capability-smoke-plan.v1.json"
    source_manifest = json.loads(source_manifest_path.read_text())
    compiler_profiles = json.loads(compiler_profiles_path.read_text())
    toolchain_lock = json.loads(toolchain_lock_path.read_text())
    source_manifest_sha = module.sha256_file(source_manifest_path)
    compiler_profiles_sha = module.sha256_file(compiler_profiles_path)
    plan_path = S1_ROOT / "benchmarks/benchmark-plan.v1.json"
    plan_sha = module.sha256_file(plan_path)
    toolchain_lock_sha = module.sha256_file(toolchain_lock_path)
    capability_plan_sha = module.sha256_file(capability_plan_path)
    java_sha = toolchain_lock["jdk"]["javaExecutableSha256"]
    javac_sha = toolchain_lock["jdk"]["javacExecutableSha256"]

    scala_cli = root / "scala-cli"
    scala_cli.write_bytes(b"selector-fixture-scala-cli\n")
    scala_cli.chmod(0o755)
    scala_cli_sha = module.sha256_file(scala_cli)

    stable_properties = dict(module.EXPECTED_STABLE_SYSTEM_PROPERTIES)
    ambient_options = dict(module.EXPECTED_AMBIENT_JVM_OPTIONS)
    effective_arguments = ["-Djmh.separateClasspathJAR=true"]

    def fork(index: int) -> dict:
        return {
            "schemaVersion": "s1.4x-scala-jvm-fork-evidence-v1",
            "forkIndex": index,
            "javaExecutablePathId": "TEMURIN_25_0_3_9_LTS/bin/java",
            "javaExecutableSha256": java_sha,
            "runtimeVersion": "25.0.3+9-LTS",
            "vendor": "Eclipse Adoptium",
            "javaHomePathId": "TEMURIN_25_0_3_9_LTS",
            "inputArguments": effective_arguments,
            "stableSystemProperties": stable_properties,
            "ambientJvmOptionVariables": ambient_options,
            "systemPropertiesSha256": module.canonical_pairs_sha256(
                stable_properties
            ),
            "environmentAllowlistSha256": module.canonical_pairs_sha256(
                module.EXPECTED_BENCHMARK_ENVIRONMENT
            ),
            "runtimeClasspathSha256": f"{index + 30:064x}",
            "evidenceSha256": f"{index + 40:064x}",
        }

    allowlist = module.assemble_jvm_argument_allowlist(
        forks=[fork(1)],
        planned_cli_arguments=[],
        benchmark_plan_sha256=plan_sha,
        capability_smoke_plan_sha256=capability_plan_sha,
        toolchain_lock_sha256=toolchain_lock_sha,
        java_executable_sha256=java_sha,
    )
    allowlist_path = root / "scala-jvm-argument-allowlist.v1.json"
    write_json(allowlist_path, allowlist)
    allowlist_sha = module.sha256_file(allowlist_path)

    qualification_value = qualification(plan, scores)
    qualification_value["benchmarkPlanSha256"] = plan_sha
    qualification_value["sourceInputManifestSha256"] = source_manifest_sha
    qualification_value["scalaCliBinarySha256"] = scala_cli_sha
    qualification_value["jvmArgumentAllowlistSha256"] = allowlist_sha
    jmh_inputs = [
        path
        for path, metadata in source_manifest["files"].items()
        if metadata["role"] in {"configuration", "main", "benchmark"}
    ]
    qualification_value["profileRunInputPaths"] = jmh_inputs

    for repetition, block in enumerate(
        qualification_value["blocks"],
        start=1,
    ):
        for measurement in block["measurements"]:
            profile = measurement["profileId"]
            case_id = measurement["caseId"]
            case_index = plan["scalaProfileQualification"][
                "qualificationCaseOrder"
            ].index(case_id) + 1
            case_root = (
                root
                / f"r{repetition}"
                / profile
                / f"case-{case_index:02d}"
            )
            case_root.mkdir(parents=True)
            score = measurement["scoreNsPerInvocation"]
            benchmark, logical_operations, include_regex = (
                module.benchmark_case_contract(plan, case_id)
            )
            forks = [
                {
                    **fork(index),
                    "runtimeClasspathSha256": (
                        f"{repetition * 1000 + case_index * 10 + index:064x}"
                    ),
                    "evidenceSha256": (
                        f"{repetition * 10000 + case_index * 10 + index:064x}"
                    ),
                }
                for index in range(1, 4)
            ]
            fork_path = case_root / "fork-evidence.normalized.json"
            write_json(fork_path, forks)
            effective = module.validate_effective_jvm_evidence(
                forks,
                expected_forks=3,
                allowlist=allowlist,
                allowlist_sha256=allowlist_sha,
            )
            effective_path = (
                case_root / "scala-effective-jvm-args-result.v1.json"
            )
            write_json(effective_path, effective)
            native = [
                {
                    "benchmark": benchmark,
                    "mode": "avgt",
                    "threads": 1,
                    "forks": 3,
                    "warmupIterations": 5,
                    "warmupTime": "500 ms",
                    "measurementIterations": 8,
                    "measurementTime": "500 ms",
                    "params": {},
                    "primaryMetric": {
                        "score": score,
                        "scoreUnit": "ns/op",
                        "rawData": [[score] * 8 for _ in range(3)],
                    },
                    "jvmArgs": effective_arguments,
                }
            ]
            native_path = case_root / "native.json"
            write_json(native_path, native)
            validation = module.validate_jmh_native_json(
                native,
                expected_benchmark=benchmark,
                expected_forks=3,
                effective_jvm_arguments=effective_arguments,
                expected_warmup_iterations=5,
                expected_warmup_time="500ms",
                expected_measurement_iterations=8,
                expected_measurement_time="500ms",
                logical_operations_per_invocation=logical_operations,
            )
            validation_path = (
                case_root / "scala-jmh-native-validation.v1.json"
            )
            write_json(validation_path, validation)
            stdout_path = case_root / "jmh.stdout"
            stderr_path = case_root / "jmh.stderr"
            stdout_path.write_bytes(b"")
            stderr_path.write_bytes(b"")
            marker = {
                "schemaVersion": "s1.4x-scala-measurement-ready-v1",
                "benchmarkPlanSha256": plan_sha,
                "caseId": case_id,
                "profileId": profile,
                "runMode": "qualification",
                "setupStatus": "PASS",
                "markerCardinality": 1,
            }
            marker_path = case_root / "measurement-ready.v1.json"
            write_json(marker_path, marker)

            precompile_stdout = (
                case_root / module.jmh_precompile.SCALA_COMPILE_STDOUT
            )
            precompile_stderr = (
                case_root / module.jmh_precompile.SCALA_COMPILE_STDERR
            )
            javac_stdout = case_root / module.jmh_precompile.JAVAC_STDOUT
            javac_stderr = case_root / module.jmh_precompile.JAVAC_STDERR
            for process_log in (
                precompile_stdout,
                precompile_stderr,
                javac_stdout,
                javac_stderr,
            ):
                process_log.write_bytes(b"")
            generated_source_paths = list(
                module.jmh_precompile.expected_generated_source_paths()
            )
            generated_sources = [
                {"path": path, "sha256": f"{index + 500:064x}"}
                for index, path in enumerate(generated_source_paths)
            ]
            generated_class_root = (
                case_root / module.jmh_precompile.GENERATED_CLASSES_NAME
            )
            generated_classes = []
            for index, source_path in enumerate(
                generated_source_paths,
                start=1,
            ):
                relative_class = (
                    f"{source_path.removesuffix('.java')}.class"
                )
                class_path = generated_class_root / relative_class
                class_path.parent.mkdir(parents=True, exist_ok=True)
                class_path.write_bytes(f"class-{index}\n".encode())
                generated_classes.append(
                    {
                        "path": relative_class,
                        "sha256": module.sha256_file(class_path),
                    }
                )
            precompile_portable = [
                "SCALA_CLI_1_15_0",
                "--power",
                "compile",
                *[f"SCALA_ROOT/{path}" for path in jmh_inputs],
                "--workspace",
                "SCALA_WORKSPACE",
                "--server=false",
                "--classpath",
                (
                    "EVIDENCE_ROOT/"
                    f"{module.jmh_precompile.GENERATED_CLASSES_NAME}"
                ),
                "--jvm",
                "system",
                "--coursier-validate-checksums",
                *module.PROFILE_CLI_ARGUMENTS[profile],
                "--jmh",
                "--jmh-version",
                "1.37",
                "--print-classpath",
            ]
            javac_portable = [
                "TEMURIN_25_0_3_9_LTS/bin/javac",
                "-encoding",
                "UTF-8",
                "-proc:none",
                "-classpath",
                "SCALA_COMPILE_CLASSPATH",
                "-d",
                (
                    "EVIDENCE_ROOT/"
                    f"{module.jmh_precompile.GENERATED_CLASSES_NAME}"
                ),
                *[
                    f"SCALA_WORKSPACE_GENERATED/{path}"
                    for path in generated_source_paths
                ],
            ]
            class_output_id = (
                "SCALA_WORKSPACE/.scala-build/"
                "selector_jmh_deadbeef00/classes/main"
            )
            generated_resource_id = (
                "SCALA_WORKSPACE/.scala-build/selector_jmh/resources"
            )
            generated_class_output_id = (
                "EVIDENCE_ROOT/"
                f"{module.jmh_precompile.GENERATED_CLASSES_NAME}"
            )
            generated_classes_sha = canonical_sha256(generated_classes)
            classpath_entries = [
                {
                    "pathId": class_output_id,
                    "kind": "directory",
                    "sha256": "a" * 64,
                },
                {
                    "pathId": generated_resource_id,
                    "kind": "directory",
                    "sha256": "e" * 64,
                },
                {
                    "pathId": (
                        "COURSIER_CACHE/https/repo.example/"
                        "org/openjdk/jmh/jmh-core/1.37/jmh-core-1.37.jar"
                    ),
                    "kind": "file",
                    "sha256": "b" * 64,
                },
                {
                    "pathId": generated_class_output_id,
                    "kind": "directory",
                    "sha256": generated_classes_sha,
                },
            ]
            precompile_receipt = {
                "schemaVersion": (
                    "s1.4x-scala-jmh-generated-java-precompile-v1"
                ),
                "profileId": profile,
                "sourceInputManifestSha256": source_manifest_sha,
                "compilerProfilesSha256": compiler_profiles_sha,
                "toolchainLockSha256": toolchain_lock_sha,
                "scalaCli": {
                    "pathId": "SCALA_CLI_1_15_0",
                    "binarySha256": scala_cli_sha,
                    "executionPathId": "PINNED_SCALA_CLI_1_15_0_FD",
                },
                "javac": {
                    "pathId": "TEMURIN_25_0_3_9_LTS/bin/javac",
                    "binarySha256": javac_sha,
                    "executionPathId": "PINNED_JAVAC_FD",
                    "jdkModulesPathId": (
                        toolchain_lock["jdk"]["jdkModulesPathId"]
                    ),
                    "jdkModulesSha256": (
                        toolchain_lock["jdk"]["jdkModulesSha256"]
                    ),
                },
                "scalaCompile": {
                    "portableArgv": precompile_portable,
                    "portableArgvSha256": canonical_sha256(
                        precompile_portable
                    ),
                    "runtimeArgvSha256": "c" * 64,
                    "stdoutSha256": module.sha256_file(
                        precompile_stdout
                    ),
                    "stderrSha256": module.sha256_file(
                        precompile_stderr
                    ),
                    "exitCode": 0,
                    "status": "PASS",
                },
                "jmhGenerator": {
                    "generatorId": "reflection",
                    "processedClassCount": 147,
                    "classInputPathId": (
                        "SCALA_WORKSPACE/.scala-build/"
                        "selector/classes/main"
                    ),
                    "generatedSourceRootPathId": (
                        "SCALA_WORKSPACE/.scala-build/"
                        "selector_jmh/sources"
                    ),
                    "generatedResourceRootPathId": generated_resource_id,
                    "classInputClosureSha256": "f" * 64,
                    "generatedResourceClosureSha256": "e" * 64,
                },
                "generatedSourceRootPathId": (
                    "SCALA_WORKSPACE/.scala-build/"
                    "selector_jmh/sources"
                ),
                "generatedSources": generated_sources,
                "generatedSourcesSha256": canonical_sha256(
                    generated_sources
                ),
                "classpathEntries": classpath_entries,
                "classpathEntriesSha256": canonical_sha256(
                    classpath_entries
                ),
                "scalaClassOutputPathId": class_output_id,
                "generatedClassOutputPathId": generated_class_output_id,
                "generatedClasses": generated_classes,
                "generatedClassesSha256": generated_classes_sha,
                "javacProcess": {
                    "portableArgv": javac_portable,
                    "portableArgvSha256": canonical_sha256(
                        javac_portable
                    ),
                    "runtimeArgvSha256": "d" * 64,
                    "stdoutSha256": module.sha256_file(javac_stdout),
                    "stderrSha256": module.sha256_file(javac_stderr),
                    "exitCode": 0,
                    "status": "PASS",
                },
                "status": "PASS",
                "aggregateStatus": "PASS",
            }
            precompile_receipt_path = (
                case_root / module.jmh_precompile.RECEIPT_NAME
            )
            write_json(precompile_receipt_path, precompile_receipt)

            common_tail = [
                "--workspace",
                "SCALA_WORKSPACE",
                "--server=false",
                "--classpath",
                (
                    "EVIDENCE_ROOT/"
                    f"{module.jmh_precompile.GENERATED_CLASSES_NAME}"
                ),
                "--jvm",
                "system",
                "--coursier-validate-checksums",
                *module.PROFILE_CLI_ARGUMENTS[profile],
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
                "-jvm",
                "PINNED_JAVA_FD",
                "-f",
                "3",
                "-wi",
                "5",
                "-i",
                "8",
                "-w",
                "500ms",
                "-r",
                "500ms",
                "-rf",
                "json",
            ]
            portable = [
                "SCALA_CLI_1_15_0",
                "--power",
                "run",
                *[f"SCALA_ROOT/{path}" for path in jmh_inputs],
                *common_tail,
                "-rff",
                "EVIDENCE_ROOT/native.json",
                include_regex,
            ]
            runtime = [
                str(scala_cli),
                "--power",
                "run",
                *[str(SCALA_ROOT / path) for path in jmh_inputs],
                "--workspace",
                str(module.isolated_scala_workspace(case_root)),
                *[
                    os.environ["S1_4X_SCALA_JAVA_PINNED_FD_PATH"]
                    if item == "PINNED_JAVA_FD"
                    else str(generated_class_root)
                    if item
                    == (
                        "EVIDENCE_ROOT/"
                        f"{module.jmh_precompile.GENERATED_CLASSES_NAME}"
                    )
                    else item
                    for item in common_tail[2:]
                ],
                "-rff",
                str(native_path),
                include_regex,
            ]
            closure_snapshot = module.SealedEvidenceSnapshot()
            command_tools = module.command_tool_closure(
                scala_root=SCALA_ROOT,
                scala_cli=scala_cli,
                java_executable=Path(os.environ["JAVA_HOME"]) / "bin/java",
                run_mode="qualification",
                snapshot=closure_snapshot,
            )
            run = {
                "schemaVersion": "s1.4x-scala-jmh-run-result-v1",
                "profileId": profile,
                "caseId": case_id,
                "logicalOperationsPerInvocation": logical_operations,
                "rawScoreNsPerInvocation": float(score),
                "normalizedScoreNsPerLogicalOperation": (
                    float(score) / logical_operations
                ),
                "runMode": "qualification",
                "benchmarkPlanSha256": plan_sha,
                "sourceInputManifestSha256": source_manifest_sha,
                "scalaCliBinarySha256": scala_cli_sha,
                "scalaCliExecutionPathId": "SCALA_CLI_1_15_0",
                "compilerProfilesSha256": compiler_profiles_sha,
                "profileOptionsSha256": canonical_sha256(
                    PROFILE_OPTIONS[profile]
                ),
                "inputPaths": jmh_inputs,
                "portableArgv": portable,
                "portableArgvSha256": canonical_sha256(portable),
                "runtimeArgvSha256": canonical_sha256(runtime),
                "commandToolClosure": command_tools,
                "commandToolClosureSha256": canonical_sha256(
                    command_tools
                ),
                "environmentValuesSha256": canonical_sha256(
                    module.SCALA_BENCHMARK_ENVIRONMENT_VALUES
                ),
                "scalaWorkspacePathId": "SCALA_WORKSPACE",
                "rawNativeJsonSha256": module.sha256_file(native_path),
                "effectiveJvmArgsSha256": module.sha256_file(
                    effective_path
                ),
                "jvmArgumentAllowlistSha256": allowlist_sha,
                "nativeValidationSha256": module.sha256_file(
                    validation_path
                ),
                "measurementReadyMarkerSha256": module.sha256_file(
                    marker_path
                ),
                "generatedJavaPrecompileReceiptSha256": (
                    module.sha256_file(precompile_receipt_path)
                ),
                "stdoutSha256": module.sha256_file(stdout_path),
                "stderrSha256": module.sha256_file(stderr_path),
                "exitCode": 0,
                "status": "PASS",
                "aggregateStatus": "PASS",
            }
            run_path = case_root / "scala-jmh-run-result.v1.json"
            write_json(run_path, run)
            measurement.update(
                {
                    "scoreNsPerInvocation": float(score),
                    "rawNativeJsonSha256": module.sha256_file(native_path),
                    "effectiveJvmArgsSha256": module.sha256_file(
                        effective_path
                    ),
                    "jmhRunResultSha256": module.sha256_file(run_path),
                }
            )

        for profile_evidence in block["profileEvidence"]:
            profile_root = (
                root
                / f"r{repetition}"
                / profile_evidence["profileId"]
            )
            host_report = {
                "schemaVersion": "s1.4x-host-validity-v1",
                "policy": {
                    "cpu_set": plan["execution"]["cpuSet"],
                    "min_home_free_bytes": 32_212_254_720,
                    "min_available_memory_bytes": (
                        plan["environmentValidity"][
                            "minAvailableMemoryGiB"
                        ]
                        * 1024**3
                    ),
                    "max_normalized_load1": plan[
                        "environmentValidity"
                    ]["maxNormalizedLoad1"],
                    "load_samples": plan["environmentValidity"][
                        "loadSampleCount"
                    ],
                    "sample_interval_seconds": plan[
                        "environmentValidity"
                    ]["loadSampleIntervalSeconds"],
                    "max_quiet_wait_seconds": plan[
                        "environmentValidity"
                    ]["maxQuietWaitSeconds"],
                    "max_running_containers": plan[
                        "environmentValidity"
                    ]["runningContainerCount"],
                    "external_process_sample_seconds": 30,
                    "max_external_process_cpu_percent": plan[
                        "environmentValidity"
                    ]["externalProcessCpuPercentThreshold"],
                    "allowed_process_root_pid": 12345,
                },
                "portableHostIdSha256": "7" * 64,
                "metadata": {
                    "cpuGovernor": "performance",
                    "temperature": "UNAVAILABLE_WSL",
                },
                "checks": [
                    {
                        "id": check_id,
                        "expected": {},
                        "actual": {},
                        "status": "PASS",
                        "evidence": {},
                    }
                    for check_id in sorted(
                        module.HOST_VALIDITY_CHECK_IDS
                    )
                ],
                "failureCount": 0,
                "status": "PASS",
            }
            host_path = profile_root / "host-validity.json"
            write_json(host_path, host_report)
            profile_evidence["hostValiditySha256"] = (
                module.sha256_file(host_path)
            )
            profile_measurements = [
                item
                for item in block["measurements"]
                if item["profileId"] == profile_evidence["profileId"]
            ]
            profile_evidence["scalaCliBinarySha256"] = scala_cli_sha
            profile_evidence[
                "sourceInputManifestSha256"
            ] = source_manifest_sha
            profile_evidence["effectiveJvmArgsSha256"] = canonical_sha256(
                [
                    item["effectiveJvmArgsSha256"]
                    for item in profile_measurements
                ]
            )
        block["effectiveJvmArgsSha256"] = canonical_sha256(
            [
                item["effectiveJvmArgsSha256"]
                for item in block["profileEvidence"]
            ]
        )
        block["hostValiditySha256"] = canonical_sha256(
            [
                item["hostValiditySha256"]
                for item in block["profileEvidence"]
            ]
        )
    qualification_value["effectiveJvmArgsClosureSha256"] = canonical_sha256(
        [
            item["effectiveJvmArgsSha256"]
            for block in qualification_value["blocks"]
            for item in block["measurements"]
        ]
    )
    qualification_value["selectorConfigSha256"] = (
        module.selector_config_sha256(
            policy=plan["scalaProfileQualification"],
            benchmark_plan_sha256=plan_sha,
            blocks=qualification_value["blocks"],
        )
    )
    qualification_path = root / "scala-profile-qualification.v1.json"
    write_json(qualification_path, qualification_value)

    correctness_inputs = [
        path
        for path, metadata in source_manifest["files"].items()
        if metadata["role"] in {"configuration", "main", "test"}
    ]
    correctness_value = correctness(
        compiler_profiles_sha256=compiler_profiles_sha,
        source_manifest_sha256=source_manifest_sha,
        profile_run_input_paths=correctness_inputs,
        toolchain_lock_sha256=toolchain_lock_sha,
        scala_cli_binary_sha256=scala_cli_sha,
    )
    correctness_sha = {}
    for profile, value in correctness_value.items():
        profile_root = root / "correctness" / profile
        profile_root.mkdir(parents=True)
        stdout_path = profile_root / "unit-test.stdout"
        stderr_path = profile_root / "unit-test.stderr"
        stdout_path.write_bytes(b"unit tests passed\n")
        stderr_path.write_bytes(b"")
        unit = {
            "schemaVersion": "s1.4x-scala-profile-unit-test-result-v1",
            "profileId": profile,
            "exitCode": 0,
            "stdoutSha256": module.sha256_file(stdout_path),
            "stderrSha256": module.sha256_file(stderr_path),
            "status": "PASS",
        }
        unit_path = (
            profile_root / "scala-profile-unit-test-result.v1.json"
        )
        write_json(unit_path, unit)
        candidate_path = profile_root / "candidate.jar"
        candidate_path.write_bytes(f"candidate-{profile}\n".encode())
        canonical_path = profile_root / "canonical-results.json"
        semantic_path = profile_root / "semantic-errors.json"
        write_json(
            canonical_path,
            {
                "implementation": "scala-3.8.4-jvm25",
                "requestId": "s1.4x-canonical-small-v1",
                "results": [],
            },
        )
        write_json(
            semantic_path,
            {
                "implementation": "scala-3.8.4-jvm25",
                "requestId": "s1.4x-semantic-errors-v1",
                "results": [],
            },
        )
        comparison = {
            "schemaVersion": "s1.4x-comparison-report-v1",
            "mismatchCount": 0,
            "status": "PASS",
        }
        canonical_comparison = profile_root / "canonical-comparison.json"
        semantic_comparison = profile_root / "semantic-comparison.json"
        write_json(canonical_comparison, comparison)
        write_json(semantic_comparison, comparison)
        property_root = profile_root / "property"
        property_root.mkdir()
        property_report = property_root / "scala-property-report.v1.json"
        registry_report = property_root / "scala-registry-report.v1.json"
        property_execution = (
            property_root
            / "scala-property-execution-evidence.v1.json"
        )
        write_json(property_report, {"status": "PASS"})
        write_json(registry_report, {"status": "PASS"})
        write_json(
            property_execution,
            {"status": "PASS", "toolchainProfile": profile},
        )
        value["candidateSha256"] = module.sha256_file(candidate_path)
        value["matrix"].update(
            {
                "candidateResultSha256": module.sha256_file(
                    canonical_path
                ),
                "semanticResultSha256": module.sha256_file(semantic_path),
                "unitTestResultSha256": module.sha256_file(unit_path),
                "unitStdoutSha256": module.sha256_file(stdout_path),
                "unitStderrSha256": module.sha256_file(stderr_path),
                "canonicalComparisonSha256": module.sha256_file(
                    canonical_comparison
                ),
                "semanticComparisonSha256": module.sha256_file(
                    semantic_comparison
                ),
                "propertyReportSha256": module.sha256_file(property_report),
                "registryReportSha256": module.sha256_file(registry_report),
                "propertyExecutionEvidenceSha256": module.sha256_file(
                    property_execution
                ),
                "propertyPlanSha256": module.sha256_file(
                    S1_ROOT / "contract/property-plan.v1.json"
                ),
                "propertySeedCorpusSha256": module.sha256_file(
                    S1_ROOT
                    / "contract/fixtures/property/property-seeds.v1.json"
                ),
                "functionRegistrySha256": module.sha256_file(
                    S1_ROOT / "contract/function-registry.v1.json"
                ),
                "errorRegistrySha256": module.sha256_file(
                    S1_ROOT / "contract/error-registry.v1.json"
                ),
            }
        )
        path = profile_root / "scala-profile-correctness-result.v1.json"
        write_json(path, value)
        correctness_sha[profile] = module.sha256_file(path)

    return {
        "plan": plan,
        "benchmark_plan_sha256": plan_sha,
        "compiler_profiles": compiler_profiles,
        "compiler_profiles_sha256": compiler_profiles_sha,
        "selected_profile_source_sha256": module.sha256_file(
            selected_source_path
        ),
        "correctness": correctness_value,
        "correctness_sha256": correctness_sha,
        "correctness_artifact_root": root / "correctness",
        "qualification": qualification_value,
        "qualification_sha256": module.sha256_file(qualification_path),
        "qualification_artifact_root": root,
        "source_manifest": source_manifest,
        "source_manifest_sha256": source_manifest_sha,
        "scala_root": SCALA_ROOT,
        "scala_cli": scala_cli,
        "pinned_scala_cli_sha256": scala_cli_sha,
        "toolchain_lock_sha256": toolchain_lock_sha,
        "merged_toolchain_provenance_sha256": module.sha256_file(
            merged_provenance_path
        ),
        "pinned_java_executable_sha256": java_sha,
        "capability_smoke_plan_sha256": capability_plan_sha,
        "jvm_allowlist": allowlist,
        "jvm_allowlist_sha256": allowlist_sha,
        "jvm_allowlist_path": allowlist_path,
    }


def capability_evidence(plan: dict) -> dict:
    smokes = plan["languages"]["scala"]["smokes"]
    return {
        item["smokeId"]: {
            "compilerStatus": "stable",
            "argv": [
                "SCALA_ROOT/tools/run-capability-smoke.sh",
                "--smoke-id",
                item["smokeId"],
            ],
            "exitCode": 0,
            "stdoutSha256": SHA,
            "stderrSha256": SHA,
            "artifactSha256": f"{index + 1:064x}",
            "status": "PASS",
            "disposition": "ADOPT",
            "provenFallback": item["provenFallback"],
            "fallbackExecuted": False,
        }
        for index, item in enumerate(smokes)
    }


def feature_evidence(planned: dict) -> dict:
    evidence = {}
    for index, item in enumerate(
        entry
        for entry in planned["entries"]
        if entry["featureId"].startswith("scala.")
    ):
        decision = item["decision"]
        if decision == "REJECT":
            effective = "REJECT"
            smoke = lint = tests = evidence_status = "NOT_APPLICABLE"
        elif decision == "PROBE_ONLY":
            effective = "PROBE_ONLY"
            smoke = lint = tests = evidence_status = "PASS"
        else:
            effective = "ADOPT"
            smoke = lint = tests = evidence_status = "PASS"
        evidence[item["featureId"]] = {
            "plannedDecision": decision,
            "effectiveDecision": effective,
            "smokeStatus": smoke,
            "lintStatus": lint,
            "testStatus": tests,
            "parityMismatchCount": 0,
            "evidenceStatus": evidence_status,
            "fallbackExecuted": False,
            "fallbackStatus": "NOT_RUN",
            "evidenceSha256": f"{index + 1:064x}",
        }
    return evidence


def main() -> int:
    module = load_module()
    os.environ.setdefault(
        "S1_4X_SCALA_JAVA_PINNED_FD_PATH",
        f"/proc/{os.getpid()}/fd/999",
    )
    temporary_root = os.environ.get("S1_4X_TEST_TMP_ROOT")
    with tempfile.TemporaryDirectory(dir=temporary_root) as directory:
        duplicate_json = Path(directory) / "duplicate.json"
        duplicate_json.write_text('{"status":"PASS","status":"FAIL"}\n')
        expect_t3_error(
            module,
            lambda: module.strict_json(duplicate_json),
            "duplicate JSON key passed",
        )
        nonfinite_json = Path(directory) / "nonfinite.json"
        nonfinite_json.write_text('{"score":NaN}\n')
        expect_t3_error(
            module,
            lambda: module.strict_json(nonfinite_json),
            "non-finite JSON number passed",
        )

    policy = json.loads((S1_ROOT / "contract/scala-source-policy.v1.json").read_text())
    matrix = json.loads(
        (TOOLS_ROOT / "fixtures/source-policy-negative.v1.json").read_text()
    )
    receipt = semantic_receipt(policy, matrix)
    module.validate_semantic_receipt(
        receipt,
        policy=policy,
        matrix=matrix,
        policy_sha256="2" * 64,
        manifest_sha256="3" * 64,
        source_tree_sha256="4" * 64,
        checked_files=receipt["checkedFiles"],
        scalafix_binary_sha256="8" * 64,
        rule_source_sha256="0" * 64,
    )
    tampered = json.loads(json.dumps(receipt))
    tampered["negativeMatrix"][0]["detectedResolvedSymbols"] = []
    try:
        module.validate_semantic_receipt(
            tampered,
            policy=policy,
            matrix=matrix,
            policy_sha256="2" * 64,
            manifest_sha256="3" * 64,
            source_tree_sha256="4" * 64,
            checked_files=receipt["checkedFiles"],
            scalafix_binary_sha256="8" * 64,
            rule_source_sha256="0" * 64,
        )
    except module.T3EvidenceError:
        pass
    else:
        raise AssertionError("resolved-symbol closure tamper passed")

    plan = json.loads((S1_ROOT / "benchmarks/benchmark-plan.v1.json").read_text())
    selector_temporary = tempfile.TemporaryDirectory(dir=temporary_root)
    selector_root = Path(selector_temporary.name)
    selector_index = 0

    def selector_inputs(
        scores: dict[str, float | list[float]],
    ) -> dict:
        nonlocal selector_index
        selector_index += 1
        return selector_fixture(
            module,
            root=selector_root / f"fixture-{selector_index:02d}",
            plan=plan,
            scores=scores,
        )

    selected_inputs = selector_inputs({"A": 100.0, "B": 95.0, "C": 96.0})
    selected = module.select_scala_profile(**selected_inputs)
    assert selected["selectedProfileId"] == "B"
    assert selected["fallbackExecuted"] is False

    case_order = plan["scalaProfileQualification"]["qualificationCaseOrder"]

    def score_matrix(
        values: dict[str, float | list[float]],
    ) -> dict[tuple[int, str, str], float]:
        return {
            (repetition, profile, case_id): float(
                value[repetition - 1]
                if isinstance(value, list)
                else value
            )
            for profile, value in values.items()
            for repetition in range(1, 4)
            for case_id in case_order
        }

    fallback_profiles, fallback_profile = module.select_profile_from_scores(
        policy=plan["scalaProfileQualification"],
        block_count=3,
        case_order=case_order,
        scores=score_matrix({"A": 100.0, "B": 106.0, "C": 107.0}),
    )
    assert fallback_profile == "A"
    assert math.isclose(fallback_profiles["B"]["maximumCaseRatio"], 1.06)

    # Frozen selector는 세 outer score의 case별 median ratio를 사용한다.
    # paired ratio의 max/전체 GM을 사용하면 이 fixture는 잘못 reject된다.
    median_profiles, median_profile = module.select_profile_from_scores(
        policy=plan["scalaProfileQualification"],
        block_count=3,
        case_order=case_order,
        scores=score_matrix(
            {
                "A": [100.0, 100.0, 100.0],
                "B": [80.0, 80.0, 200.0],
                "C": [110.0, 110.0, 110.0],
            }
        ),
    )
    assert median_profile == "B"
    assert math.isclose(
        median_profiles["B"]["maximumCaseRatio"],
        0.8,
    )
    assert math.isclose(
        median_profiles["B"]["aggregateRatioToA"],
        0.8,
    )

    bad_latin = dict(selected_inputs)
    bad_latin["qualification"] = json.loads(
        json.dumps(selected_inputs["qualification"])
    )
    bad_latin["qualification"]["blocks"][0]["actualProfileOrder"] = [
        "B",
        "A",
        "C",
    ]
    expect_t3_error(
        module,
        lambda: module.select_scala_profile(**bad_latin),
        "Latin order tamper passed",
    )

    bad_host_closure = dict(selected_inputs)
    bad_host_closure["qualification"] = json.loads(
        json.dumps(selected_inputs["qualification"])
    )
    bad_host_closure["qualification"]["blocks"][0]["profileEvidence"][0][
        "caseCount"
    ] = 6
    expect_t3_error(
        module,
        lambda: module.select_scala_profile(**bad_host_closure),
        "per-profile host/JVM closure tamper passed",
    )

    qualification_tampers = []
    base_qualification = selected_inputs["qualification"]

    def qualification_tamper() -> dict:
        value = dict(selected_inputs)
        value["qualification"] = json.loads(json.dumps(base_qualification))
        return value

    missing_measurement_sha = qualification_tamper()
    missing_measurement_sha["qualification"]["blocks"][0]["measurements"][
        0
    ].pop(
        "rawNativeJsonSha256"
    )
    qualification_tampers.append(
        (missing_measurement_sha, "measurement SHA omission passed")
    )
    extra_measurement_field = qualification_tamper()
    extra_measurement_field["qualification"]["blocks"][0]["measurements"][0][
        "forged"
    ] = True
    qualification_tampers.append(
        (extra_measurement_field, "measurement extra field passed")
    )
    profile_jvm_tamper = qualification_tamper()
    profile_jvm_tamper["qualification"]["blocks"][0]["profileEvidence"][0][
        "effectiveJvmArgsSha256"
    ] = SHA
    qualification_tampers.append(
        (profile_jvm_tamper, "profile JVM hash tamper passed")
    )
    block_jvm_tamper = qualification_tamper()
    block_jvm_tamper["qualification"]["blocks"][0][
        "effectiveJvmArgsSha256"
    ] = SHA
    qualification_tampers.append(
        (block_jvm_tamper, "block JVM hash tamper passed")
    )
    block_host_tamper = qualification_tamper()
    block_host_tamper["qualification"]["blocks"][0][
        "hostValiditySha256"
    ] = SHA
    qualification_tampers.append(
        (block_host_tamper, "block host hash tamper passed")
    )
    case_order_tamper = qualification_tamper()
    case_order_tamper["qualification"]["blocks"][0]["profileEvidence"][0][
        "actualCaseOrder"
    ] = list(
        reversed(plan["scalaProfileQualification"]["qualificationCaseOrder"])
    )
    qualification_tampers.append(
        (case_order_tamper, "actual case order tamper passed")
    )
    timestamp_tamper = qualification_tamper()
    timestamp_tamper["qualification"]["blocks"][0]["profileEvidence"][0][
        "endedAt"
    ] = "2026-07-17T00:00:00.000000Z"
    qualification_tampers.append(
        (timestamp_tamper, "reversed profile timestamp passed")
    )
    cli_identity_tamper = qualification_tamper()
    cli_identity_tamper["qualification"]["blocks"][0]["profileEvidence"][0][
        "scalaCliBinarySha256"
    ] = "b" * 64
    qualification_tampers.append(
        (cli_identity_tamper, "Scala CLI identity tamper passed")
    )
    bool_score = qualification_tamper()
    bool_score["qualification"]["blocks"][0]["measurements"][0][
        "scoreNsPerInvocation"
    ] = True
    qualification_tampers.append((bool_score, "bool selector score passed"))
    for tampered_inputs, message in qualification_tampers:
        expect_t3_error(
            module,
            lambda value=tampered_inputs: module.select_scala_profile(**value),
            message,
        )

    raw_correctness = (
        selected_inputs["correctness_artifact_root"]
        / "A/property/scala-property-report.v1.json"
    )
    raw_correctness_bytes = raw_correctness.read_bytes()
    raw_correctness_value = json.loads(raw_correctness_bytes)
    raw_correctness_value["aggregateStatus"] = "FAIL"
    write_json(raw_correctness, raw_correctness_value)
    expect_t3_error(
        module,
        lambda: module.select_scala_profile(**selected_inputs),
        "raw correctness byte tamper passed",
    )
    raw_correctness.write_bytes(raw_correctness_bytes)

    raw_tamper_inputs = selected_inputs
    raw_native = (
        raw_tamper_inputs["qualification_artifact_root"]
        / "r1/A/case-01/native.json"
    )
    raw_value = json.loads(raw_native.read_text())
    raw_value[0]["primaryMetric"]["score"] = 77.0
    write_json(raw_native, raw_value)
    expect_t3_error(
        module,
        lambda: module.select_scala_profile(**raw_tamper_inputs),
        "raw JMH byte tamper passed",
    )

    marker_path = (
        selected_inputs["qualification_artifact_root"]
        / "r1/A/case-01/measurement-ready.v1.json"
    )
    marker_sha = module.validate_measurement_ready_marker(
        marker_path,
        expected_benchmark_plan_sha256=selected_inputs[
            "benchmark_plan_sha256"
        ],
        expected_case_id=case_order[0],
        expected_profile="A",
        expected_run_mode="qualification",
    )
    assert marker_sha == module.sha256_file(marker_path)
    marker_tamper = json.loads(marker_path.read_text())
    marker_tamper["profileId"] = "C"
    write_json(marker_path, marker_tamper)
    expect_t3_error(
        module,
        lambda: module.validate_measurement_ready_marker(
            marker_path,
            expected_benchmark_plan_sha256=selected_inputs[
                "benchmark_plan_sha256"
            ],
            expected_case_id=case_order[0],
            expected_profile="A",
            expected_run_mode="qualification",
        ),
        "measurement-ready marker identity tamper passed",
    )

    capability_plan = json.loads(
        (S1_ROOT / "contract/capability-smoke-plan.v1.json").read_text()
    )
    capability = module.assemble_capability_result(
        plan=capability_plan,
        plan_sha256=SHA,
        toolchain_identity_sha256="2" * 64,
        evidence=capability_evidence(capability_plan),
    )
    assert capability["aggregateStatus"] == "PASS"
    assert [item["smokeId"] for item in capability["results"]] == [
        item["smokeId"]
        for item in capability_plan["languages"]["scala"]["smokes"]
    ]
    duplicate = capability_evidence(capability_plan)
    duplicate.pop("scala-jmh-native-json")
    try:
        module.assemble_capability_result(
            plan=capability_plan,
            plan_sha256=SHA,
            toolchain_identity_sha256="2" * 64,
            evidence=duplicate,
        )
    except module.T3EvidenceError:
        pass
    else:
        raise AssertionError("missing capability evidence passed")
    placeholder = capability_evidence(capability_plan)
    placeholder["scala-toolchain-identity"]["argv"] = [
        "SCALA_CAPABILITY_EVIDENCE",
        "scala-toolchain-identity",
    ]
    expect_t3_error(
        module,
        lambda: module.assemble_capability_result(
            plan=capability_plan,
            plan_sha256=SHA,
            toolchain_identity_sha256="2" * 64,
            evidence=placeholder,
        ),
        "placeholder capability argv passed",
    )

    source_manifest = json.loads(
        (SCALA_ROOT / "source-inputs.v1.json").read_text()
    )
    expected_paths = list(source_manifest["files"])
    input_sets = {
        name: expected_paths
        for name in ("tracked", "manifest", "format", "compile", "lint", "profileRun")
    }
    input_result = module.assemble_input_set_result(
        manifest=source_manifest,
        manifest_sha256="3" * 64,
        compiler_profile_sha256="4" * 64,
        input_sets=input_sets,
    )
    assert input_result["aggregateStatus"] == "PASS"
    assert all(item["exact"] for item in input_result["sets"].values())
    missing_compile = json.loads(json.dumps(input_sets))
    missing_compile["compile"] = expected_paths[:-1]
    try:
        module.assemble_input_set_result(
            manifest=source_manifest,
            manifest_sha256="3" * 64,
            compiler_profile_sha256="4" * 64,
            input_sets=missing_compile,
        )
    except module.T3EvidenceError:
        pass
    else:
        raise AssertionError("incomplete compiler input set passed")
    for consumer, changed, message in (
        ("format", expected_paths[1:], "omitted formatter input passed"),
        (
            "lint",
            [*expected_paths, "src/main/scala/stale.scala"],
            "extra lint input passed",
        ),
        (
            "profileRun",
            list(reversed(expected_paths)),
            "reordered profile-run input passed",
        ),
    ):
        tampered_sets = json.loads(json.dumps(input_sets))
        tampered_sets[consumer] = changed
        expect_t3_error(
            module,
            lambda value=tampered_sets: module.assemble_input_set_result(
                manifest=source_manifest,
                manifest_sha256="3" * 64,
                compiler_profile_sha256="4" * 64,
                input_sets=value,
            ),
            message,
        )

    planned = json.loads(
        (S1_ROOT / "contract/feature-decisions.v1.json").read_text()
    )
    effective = module.assemble_feature_decision_result(
        planned=planned,
        planned_sha256="5" * 64,
        capability_sha256="6" * 64,
        evidence=feature_evidence(planned),
    )
    assert len(effective["entries"]) == 6
    assert effective["entries"][-1]["effectiveDecision"] == "REJECT"
    broken_feature = feature_evidence(planned)
    broken_feature["scala.closed-enum-adt"]["parityMismatchCount"] = 1
    try:
        module.assemble_feature_decision_result(
            planned=planned,
            planned_sha256="5" * 64,
            capability_sha256="6" * 64,
            evidence=broken_feature,
        )
    except module.T3EvidenceError:
        pass
    else:
        raise AssertionError("adopted feature mismatch passed")

    dependency = module.assemble_scala_dependency_audit(
        policy_sha256="7" * 64,
        source_input_manifest_sha256="3" * 64,
        project_sha256="8" * 64,
        dependencies=[
            "com.fasterxml.jackson.core:jackson-core:2.22.1",
            "org.openjdk.jmh:jmh-core:1.37",
        ],
        forbidden_source_findings=[],
    )
    assert dependency["candidateAuthoredEdgeCount"] == 0
    assert dependency["candidateAddedNativeDependencyCount"] == 0
    try:
        module.assemble_scala_dependency_audit(
            policy_sha256="7" * 64,
            source_input_manifest_sha256="3" * 64,
            project_sha256="8" * 64,
            dependencies=["io.grpc:grpc-netty:1.0.0"],
            forbidden_source_findings=[],
        )
    except module.T3EvidenceError:
        pass
    else:
        raise AssertionError("native dependency passed")

    stable_properties = {
        "java.runtime.version": "25.0.3+9-LTS",
        "java.specification.version": "25",
        "java.vendor": "Eclipse Adoptium",
        "java.vm.name": "OpenJDK 64-Bit Server VM",
    }
    benchmark_environment = dict(module.EXPECTED_BENCHMARK_ENVIRONMENT)
    observed_jmh_arguments = ["-Djmh.separateClasspathJAR=true"]
    fork_evidence = [
        {
            "schemaVersion": "s1.4x-scala-jvm-fork-evidence-v1",
            "forkIndex": index,
            "javaExecutablePathId": "TEMURIN_25_0_3_9_LTS/bin/java",
            "javaExecutableSha256": "9" * 64,
            "runtimeVersion": "25.0.3+9-LTS",
            "vendor": "Eclipse Adoptium",
            "javaHomePathId": "TEMURIN_25_0_3_9_LTS",
            "inputArguments": observed_jmh_arguments,
            "stableSystemProperties": stable_properties,
            "ambientJvmOptionVariables": {
                "JAVA_TOOL_OPTIONS": "UNSET",
                "_JAVA_OPTIONS": "UNSET",
                "JDK_JAVA_OPTIONS": "UNSET",
            },
            "systemPropertiesSha256": module.canonical_pairs_sha256(
                stable_properties
            ),
            "environmentAllowlistSha256": module.canonical_pairs_sha256(
                benchmark_environment
            ),
            "runtimeClasspathSha256": "c" * 64,
            "evidenceSha256": f"{index + 20:064x}",
        }
        for index in range(1, 4)
    ]
    jvm_allowlist = module.assemble_jvm_argument_allowlist(
        forks=[fork_evidence[0]],
        planned_cli_arguments=[],
        benchmark_plan_sha256="d" * 64,
        capability_smoke_plan_sha256="e" * 64,
        toolchain_lock_sha256="f" * 64,
        java_executable_sha256="9" * 64,
    )
    assert jvm_allowlist["plannedCliJvmArguments"] == []
    assert jvm_allowlist["effectiveJvmArguments"] == observed_jmh_arguments
    effective_jvm = module.validate_effective_jvm_evidence(
        fork_evidence,
        expected_forks=3,
        allowlist=jvm_allowlist,
        allowlist_sha256="1" * 64,
    )
    assert effective_jvm["aggregateStatus"] == "PASS"
    assert effective_jvm["forkCount"] == 3
    unexpected_argument = json.loads(json.dumps(fork_evidence))
    unexpected_argument[0]["inputArguments"] = ["-XX:+UseWhatever"]
    expect_t3_error(
        module,
        lambda: module.validate_effective_jvm_evidence(
            unexpected_argument,
            expected_forks=3,
            allowlist=jvm_allowlist,
            allowlist_sha256="1" * 64,
        ),
        "unexpected effective JVM argument passed",
    )
    missing_property = json.loads(json.dumps(fork_evidence))
    missing_property[0]["stableSystemProperties"].pop("java.vm.name")
    expect_t3_error(
        module,
        lambda: module.validate_effective_jvm_evidence(
            missing_property,
            expected_forks=3,
            allowlist=jvm_allowlist,
            allowlist_sha256="1" * 64,
        ),
        "missing effective JVM property passed",
    )

    native = [
        {
            "benchmark": "s1_4x.benchmarks.path_transform.PathTransformBenchmark.run",
            "mode": "avgt",
            "threads": 1,
            "forks": 1,
            "warmupIterations": 1,
            "warmupTime": "200 ms",
            "measurementIterations": 1,
            "measurementTime": "200 ms",
            "params": {},
            "primaryMetric": {
                "score": 12.5,
                "scoreUnit": "ns/op",
                "rawData": [[12.5]],
            },
            "jvmArgs": [],
        }
    ]
    validated = module.validate_jmh_native_json(
        native,
        expected_benchmark=native[0]["benchmark"],
        expected_forks=1,
        effective_jvm_arguments=[],
        expected_warmup_iterations=1,
        expected_warmup_time="200ms",
        expected_measurement_iterations=1,
        expected_measurement_time="200ms",
        logical_operations_per_invocation=32,
    )
    assert validated["nativeValue"] == 12.5
    native_tampers = []
    nonfinite = json.loads(json.dumps(native))
    nonfinite[0]["primaryMetric"]["score"] = float("nan")
    native_tampers.append((nonfinite, "non-finite native score passed"))
    truncated = json.loads(json.dumps(native))
    truncated[0]["primaryMetric"]["rawData"][0] = []
    native_tampers.append((truncated, "truncated JMH rawData passed"))
    extra_sample = json.loads(json.dumps(native))
    extra_sample[0]["primaryMetric"]["rawData"][0].append(13.0)
    native_tampers.append((extra_sample, "extra JMH sample passed"))
    extra_fork = json.loads(json.dumps(native))
    extra_fork[0]["primaryMetric"]["rawData"].append([12.5])
    native_tampers.append((extra_fork, "extra JMH fork passed"))
    altered_time = json.loads(json.dumps(native))
    altered_time[0]["measurementTime"] = "201 ms"
    native_tampers.append((altered_time, "altered JMH time passed"))
    bool_raw = json.loads(json.dumps(native))
    bool_raw[0]["primaryMetric"]["rawData"][0][0] = True
    native_tampers.append((bool_raw, "bool JMH sample passed"))
    bool_iterations = json.loads(json.dumps(native))
    bool_iterations[0]["measurementIterations"] = True
    native_tampers.append(
        (bool_iterations, "bool JMH measurement iteration count passed")
    )
    for tampered_native, message in native_tampers:
        expect_t3_error(
            module,
            lambda value=tampered_native: module.validate_jmh_native_json(
                value,
                expected_benchmark=native[0]["benchmark"],
                expected_forks=1,
                effective_jvm_arguments=[],
                expected_warmup_iterations=1,
                expected_warmup_time="200ms",
                expected_measurement_iterations=1,
                expected_measurement_time="200ms",
                logical_operations_per_invocation=32,
            ),
            message,
        )
    underflow = json.loads(json.dumps(native))
    underflow[0]["primaryMetric"]["score"] = 5e-324
    underflow[0]["primaryMetric"]["rawData"] = [[5e-324]]
    expect_t3_error(
        module,
        lambda: module.validate_jmh_native_json(
            underflow,
            expected_benchmark=native[0]["benchmark"],
            expected_forks=1,
            effective_jvm_arguments=[],
            expected_warmup_iterations=1,
            expected_warmup_time="200ms",
            expected_measurement_iterations=1,
            expected_measurement_time="200ms",
            logical_operations_per_invocation=32,
        ),
        "underflowed normalized JMH score passed",
    )

    print(
        "SCALA_T3_EVIDENCE_TEST_PASS "
        "semanticNegatives=22 profiles=3 capabilities=8 features=6 "
        "inputSets=6 nativeEdges=0 jvmForks=3 native=1"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
