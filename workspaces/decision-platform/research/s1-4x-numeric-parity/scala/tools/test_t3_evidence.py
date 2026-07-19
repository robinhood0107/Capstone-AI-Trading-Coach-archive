#!/usr/bin/env python3
"""Scala T3 portable evidence contracts and frozen selector regression tests."""

from __future__ import annotations

import importlib.util
import hashlib
import json
import math
import os
import shutil
import subprocess
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


def expect_t3_error(
    module,
    operation,
    message: str,
    *,
    expected_prefix: str | None = None,
) -> None:
    try:
        operation()
    except module.T3EvidenceError as error:
        if (
            expected_prefix is not None
            and not str(error).startswith(expected_prefix)
        ):
            raise AssertionError(
                f"{message}: unexpected leaf {error}"
            ) from error
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
    scala_cli_override: Path | None = None,
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
    jdk_modules_identity = module.jmh_precompile._stat_identity(
        os.stat(
            Path(os.environ["JAVA_HOME"]) / "lib/modules",
            follow_symlinks=False,
        )
    )

    def closure_identity_sha256(
        closure_root: Path,
        values: list[dict[str, str]],
    ) -> str:
        identities = [
            {
                "path": item["path"],
                "fileIdentity": (
                    module.jmh_precompile._file_identity_value(
                        module.jmh_precompile._stat_identity(
                            os.stat(
                                closure_root / item["path"],
                                follow_symlinks=False,
                            )
                        )
                    )
                ),
            }
            for item in values
        ]
        return canonical_sha256(identities)

    scala_cli = scala_cli_override or root / "scala-cli"
    if scala_cli_override is None:
        scala_cli.write_bytes(b"selector-fixture-scala-cli\n")
        scala_cli.chmod(0o755)
    scala_cli_sha = module.sha256_file(scala_cli)

    stable_properties = dict(module.EXPECTED_STABLE_SYSTEM_PROPERTIES)
    ambient_options = dict(module.EXPECTED_AMBIENT_JVM_OPTIONS)
    reported_argument = "-Djmh.separateClasspathJAR=true"
    compile_command_sha256 = "8" * 64

    def fork(
        index: int,
        *,
        tmp_directory: Path = Path("/sealed/smoke/jmh-tmp"),
    ) -> dict:
        input_arguments = [
            reported_argument,
            f"-Djava.io.tmpdir={tmp_directory}",
            "-XX:+UnlockDiagnosticVMOptions",
            "-XX:+UnlockExperimentalVMOptions",
            "-DcompilerBlackholesEnabled=true",
            (
                "-XX:CompileCommandFile="
                f"{tmp_directory}/jmh-{index}.compilecommand"
            ),
        ]
        return {
            "schemaVersion": "s1.4x-scala-jvm-fork-evidence-v1",
            "forkIndex": index,
            "javaExecutablePathId": "TEMURIN_25_0_3_9_LTS/bin/java",
            "javaExecutableSha256": java_sha,
            "runtimeVersion": "25.0.3+9-LTS",
            "vendor": "Eclipse Adoptium",
            "javaHomePathId": "TEMURIN_25_0_3_9_LTS",
            "inputArguments": input_arguments,
            "inputArgumentFiles": [
                {
                    "argumentIndex": len(input_arguments) - 1,
                    "argumentPrefix": "-XX:CompileCommandFile=",
                    "pathId": "JMH_COMPILE_COMMAND_FILE",
                    "sha256": compile_command_sha256,
                    "fileIdentitySha256": "7" * 64,
                }
            ],
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
    effective_arguments = allowlist["effectiveJvmArguments"]

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
            case_tmp_directory = Path(
                f"/sealed/qualification/r{repetition}/{profile}/"
                f"case-{case_index:02d}/jmh-tmp"
            )
            forks = [
                {
                    **fork(index, tmp_directory=case_tmp_directory),
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
                    "params": None,
                    "primaryMetric": {
                        "score": score,
                        "scoreUnit": "ns/op",
                        "rawData": [[score] * 8 for _ in range(3)],
                    },
                    "jvmArgs": [
                        reported_argument,
                        f"-Djava.io.tmpdir={case_tmp_directory}",
                    ],
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
            generated_source_root = (
                case_root / module.jmh_precompile.GENERATED_SOURCES_NAME
            )
            generated_sources = []
            for index, source_path in enumerate(
                generated_source_paths,
                start=1,
            ):
                source = generated_source_root / source_path
                source.parent.mkdir(parents=True, exist_ok=True)
                class_name = Path(source_path).stem
                package_name = ".".join(Path(source_path).parent.parts)
                if class_name.endswith("_benchmark_jmhTest"):
                    declaration = (
                        "import org.openjdk.jmh.runner.InfraControl;\n"
                        f"public final class {class_name} {{}}\n"
                    )
                else:
                    declaration = f"public class {class_name} {{}}\n"
                source.write_text(
                    f"package {package_name};\n"
                    f"// generated fixture {index}\n"
                    f"{declaration}",
                    encoding="utf-8",
                    newline="\n",
                )
                generated_sources.append(
                    {
                        "path": source_path,
                        "sha256": module.sha256_file(source),
                    }
                )
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
                class_path.write_bytes(
                    b"\xca\xfe\xba\xbe\x00\x00\x00\x45"
                    + relative_class.removesuffix(".class").encode()
                    + f":{index}\n".encode()
                )
                generated_classes.append(
                    {
                        "path": relative_class,
                        "sha256": module.sha256_file(class_path),
                    }
                )
            generated_sources_identity_sha = closure_identity_sha256(
                generated_source_root,
                generated_sources,
            )
            generated_classes_identity_sha = closure_identity_sha256(
                generated_class_root,
                generated_classes,
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
                    f"EVIDENCE_ROOT_GENERATED/{path}"
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
                    "identitySha256": "1" * 64,
                },
                {
                    "pathId": generated_resource_id,
                    "kind": "directory",
                    "sha256": "e" * 64,
                    "identitySha256": "2" * 64,
                },
                {
                    "pathId": (
                        "COURSIER_CACHE/https/repo.example/"
                        "org/openjdk/jmh/jmh-core/1.37/jmh-core-1.37.jar"
                    ),
                    "kind": "file",
                    "sha256": "b" * 64,
                    "identitySha256": "3" * 64,
                },
                {
                    "pathId": generated_class_output_id,
                    "kind": "directory",
                    "sha256": generated_classes_sha,
                    "identitySha256": generated_classes_identity_sha,
                },
            ]
            classpath_post_entries = [
                {
                    "pathId": item["pathId"],
                    "kind": item["kind"],
                    "sha256": item["sha256"],
                    "preRunIdentitySha256": item["identitySha256"],
                    "postRunIdentitySha256": (
                        f"{index + 4:064x}"
                        if index < 2
                        else item["identitySha256"]
                    ),
                    "identityStatus": (
                        "ROTATED_SAME_BYTES"
                        if index < 2
                        else "STABLE"
                    ),
                }
                for index, item in enumerate(classpath_entries)
            ]
            classpath_post_run = {
                "schemaVersion": "s1.4x-classpath-post-run-v1",
                "allowedIdentityRotations": [
                    {
                        "role": "SCALA_CLASS_OUTPUT",
                        "pathId": class_output_id,
                    },
                    {
                        "role": "JMH_GENERATED_RESOURCES",
                        "pathId": generated_resource_id,
                    },
                ],
                "entries": classpath_post_entries,
                "entriesSha256": canonical_sha256(
                    classpath_post_entries
                ),
                "rotatedPathIds": [
                    class_output_id,
                    generated_resource_id,
                ],
                "status": "PASS",
            }
            physical_workspace = Path("/sealed/scala-workspace")
            physical_coursier = Path("/sealed/coursier")
            runtime_class_output_id = (
                "SCALA_WORKSPACE/.scala-build/"
                "selector_runtime_jmh_feedface00/classes/main"
            )
            runtime_generator_input_id = (
                "SCALA_WORKSPACE/.scala-build/"
                "selector_runtime/classes/main"
            )
            runtime_generated_source_id = (
                "SCALA_WORKSPACE/.scala-build/"
                "selector_runtime_jmh/sources"
            )
            runtime_generated_resource_id = (
                "SCALA_WORKSPACE/.scala-build/"
                "selector_runtime_jmh/resources"
            )
            runtime_classpath_entries = [
                {
                    "pathId": runtime_class_output_id,
                    "kind": "directory",
                    "sha256": classpath_entries[0]["sha256"],
                    "identitySha256": "6" * 64,
                },
                {
                    "pathId": runtime_generated_resource_id,
                    "kind": "directory",
                    "sha256": classpath_entries[1]["sha256"],
                    "identitySha256": "7" * 64,
                },
                *classpath_entries[2:],
            ]
            runtime_generator = {
                "generatorId": "reflection",
                "processedClassCount": 149,
                "classInputPathId": runtime_generator_input_id,
                "generatedSourceRootPathId": runtime_generated_source_id,
                "generatedResourceRootPathId": (
                    runtime_generated_resource_id
                ),
                "classInputClosureSha256": "f" * 64,
                "generatedResourceClosureSha256": "e" * 64,
            }
            runtime_closure = {
                "schemaVersion": "s1.4x-jmh-runtime-closure-v1",
                "generator": runtime_generator,
                "roleMappings": [
                    {
                        "role": "SCALA_CLASS_OUTPUT",
                        "precompilePathId": class_output_id,
                        "runtimePathId": runtime_class_output_id,
                        "sha256": classpath_entries[0]["sha256"],
                    },
                    {
                        "role": "JMH_GENERATED_SOURCES",
                        "precompilePathId": (
                            "SCALA_WORKSPACE/.scala-build/"
                            "selector_jmh/sources"
                        ),
                        "runtimePathId": runtime_generated_source_id,
                        "sha256": canonical_sha256(
                            generated_sources
                        ),
                    },
                    {
                        "role": "JMH_GENERATED_RESOURCES",
                        "precompilePathId": generated_resource_id,
                        "runtimePathId": runtime_generated_resource_id,
                        "sha256": classpath_entries[1]["sha256"],
                    },
                ],
                "runtimeClasspathEntries": runtime_classpath_entries,
                "runtimeClasspathEntriesSha256": canonical_sha256(
                    runtime_classpath_entries
                ),
                "runtimeClasspathSha256": "",
                "status": "PASS",
            }
            generator_input_path = (
                physical_workspace / ".scala-build/selector/classes/main"
            )
            generated_source_path = (
                physical_workspace / ".scala-build/selector_jmh/sources"
            )
            generated_resource_path = (
                physical_workspace / ".scala-build/selector_jmh/resources"
            )
            class_output_path = (
                physical_workspace
                / ".scala-build/selector_jmh_deadbeef00/classes/main"
            )
            runtime_generator_input_path = (
                physical_workspace
                / ".scala-build/selector_runtime/classes/main"
            )
            runtime_generated_source_path = (
                physical_workspace
                / ".scala-build/selector_runtime_jmh/sources"
            )
            runtime_generated_resource_path = (
                physical_workspace
                / ".scala-build/selector_runtime_jmh/resources"
            )
            runtime_class_output_path = (
                physical_workspace
                / ".scala-build/"
                "selector_runtime_jmh_feedface00/classes/main"
            )
            dependency_path = (
                physical_coursier
                / "https/repo.example/org/openjdk/jmh/"
                "jmh-core/1.37/jmh-core-1.37.jar"
            )
            generator_stdout_prefix = (
                f'Processing 149 classes from {generator_input_path} '
                'with "reflection" generator\n'
                f"Writing out Java source to {generated_source_path} "
                f"and resources to {generated_resource_path}\n"
            )
            precompile_runtime_classpath = (
                f"{class_output_path}:{generated_resource_path}:"
                f"{dependency_path}:{generated_class_root}"
            )
            generator_classpath_prefix = (
                generator_stdout_prefix
                + precompile_runtime_classpath
                + "\n"
            )
            runtime_generator_stdout_prefix = (
                f"Processing 149 classes from "
                f"{runtime_generator_input_path} "
                'with "reflection" generator\n'
                f"Writing out Java source to "
                f"{runtime_generated_source_path} "
                f"and resources to {runtime_generated_resource_path}\n"
            )
            runtime_classpath = (
                f"{runtime_class_output_path}:"
                f"{runtime_generated_resource_path}:"
                f"{dependency_path}:{generated_class_root}"
            )
            precompile_runtime_classpath_sha = hashlib.sha256(
                precompile_runtime_classpath.encode("utf-8")
            ).hexdigest()
            runtime_classpath_sha = hashlib.sha256(
                runtime_classpath.encode("utf-8")
            ).hexdigest()
            runtime_closure["runtimeClasspathSha256"] = (
                runtime_classpath_sha
            )
            for fork_value in forks:
                fork_value["runtimeClasspathSha256"] = runtime_classpath_sha
            write_json(fork_path, forks)
            precompile_stdout.write_text(
                generator_classpath_prefix,
                encoding="utf-8",
                newline="\n",
            )
            stdout_path.write_text(
                runtime_generator_stdout_prefix
                + "# JMH version: 1.37\n",
                encoding="utf-8",
                newline="\n",
            )
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
                    "jdkModulesFileIdentity": (
                        module.jmh_precompile._file_identity_value(
                            jdk_modules_identity
                        )
                    ),
                },
                "scalaCompile": {
                    "portableArgv": precompile_portable,
                    "portableArgvSha256": canonical_sha256(
                        precompile_portable
                    ),
                    "runtimeArgvSha256": canonical_sha256(
                        [
                            "PINNED_SCALA_CLI_1_15_0_FD",
                            *precompile_portable[1:],
                        ]
                    ),
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
                    "processedClassCount": 149,
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
                    "EVIDENCE_ROOT/"
                    f"{module.jmh_precompile.GENERATED_SOURCES_NAME}"
                ),
                "generatedSources": generated_sources,
                "generatedSourcesSha256": canonical_sha256(
                    generated_sources
                ),
                "generatedSourcesIdentitySha256": (
                    generated_sources_identity_sha
                ),
                "classpathEntries": classpath_entries,
                "classpathEntriesSha256": canonical_sha256(
                    classpath_entries
                ),
                "classpathPostRun": classpath_post_run,
                "classpathPostRunSha256": canonical_sha256(
                    classpath_post_run
                ),
                "jmhRuntimeClosure": runtime_closure,
                "jmhRuntimeClosureSha256": canonical_sha256(
                    runtime_closure
                ),
                "precompileRuntimeClasspathSha256": (
                    precompile_runtime_classpath_sha
                ),
                "runtimeClasspathSha256": runtime_classpath_sha,
                "scalaClassOutputPathId": class_output_id,
                "generatedClassOutputPathId": generated_class_output_id,
                "generatedClasses": generated_classes,
                "generatedClassesSha256": generated_classes_sha,
                "generatedClassesIdentitySha256": (
                    generated_classes_identity_sha
                ),
                "javacProcess": {
                    "portableArgv": javac_portable,
                    "portableArgvSha256": canonical_sha256(
                        javac_portable
                    ),
                    "runtimeArgvSha256": canonical_sha256(
                        ["PINNED_JAVAC_FD", *javac_portable[1:]]
                    ),
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
                "--java-prop",
                "java.io.tmpdir=EVIDENCE_ROOT/jmh-tmp",
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
            runtime_identity = [
                "PINNED_SCALA_CLI_1_15_0_FD",
                *portable[1:],
            ]
            runtime_execution_identities = [
                {
                    "executionPathId": "PINNED_SCALA_CLI_1_15_0_FD",
                    "binaryPathId": "SCALA_CLI_1_15_0",
                    "binarySha256": scala_cli_sha,
                },
                {
                    "executionPathId": "PINNED_JAVA_FD",
                    "binaryPathId": "TEMURIN_25_0_3_9_LTS/bin/java",
                    "binarySha256": java_sha,
                },
                {
                    "executionPathId": "PINNED_JAVAC_FD",
                    "binaryPathId": "TEMURIN_25_0_3_9_LTS/bin/javac",
                    "binarySha256": javac_sha,
                },
            ]
            live_execution_path_identity = []
            for index, (stable, binary) in enumerate(
                zip(
                    runtime_execution_identities,
                    (
                        scala_cli,
                        Path(os.environ["JAVA_HOME"]) / "bin/java",
                        Path(os.environ["JAVA_HOME"]) / "bin/javac",
                    ),
                    strict=True,
                ),
                start=10,
            ):
                metadata = os.stat(binary, follow_symlinks=False)
                live_execution_path_identity.append(
                    {
                        **stable,
                        "procOwnerPid": 12345,
                        "procOwnerStartTimeTicks": 67890,
                        "procFd": index,
                        "runtimePathSha256": f"{index:064x}",
                        "fileIdentity": {
                            "device": metadata.st_dev,
                            "inode": metadata.st_ino,
                            "mode": metadata.st_mode,
                            "linkCount": metadata.st_nlink,
                            "uid": metadata.st_uid,
                            "gid": metadata.st_gid,
                            "size": metadata.st_size,
                            "mtimeNs": metadata.st_mtime_ns,
                            "ctimeNs": metadata.st_ctime_ns,
                        },
                    }
                )
            java_index = runtime_identity.index("PINNED_JAVA_FD")
            live_runtime_argv_witness = {
                "schemaVersion": (
                    "s1.4x-scala-live-runtime-argv-witness-v1"
                ),
                "normalizedArgv": runtime_identity,
                "normalizedArgvSha256": canonical_sha256(
                    runtime_identity
                ),
                "physicalArgvSha256": "9" * 64,
                "physicalExecutionPaths": [
                    {
                        "argvIndex": 0,
                        "executionPathId": (
                            "PINNED_SCALA_CLI_1_15_0_FD"
                        ),
                        "pathSha256": live_execution_path_identity[0][
                            "runtimePathSha256"
                        ],
                    },
                    {
                        "argvIndex": java_index,
                        "executionPathId": "PINNED_JAVA_FD",
                        "pathSha256": live_execution_path_identity[1][
                            "runtimePathSha256"
                        ],
                    },
                ],
                "status": "PASS",
            }
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
                "scalaCliExecutionPathId": (
                    "PINNED_SCALA_CLI_1_15_0_FD"
                ),
                "compilerProfilesSha256": compiler_profiles_sha,
                "profileOptionsSha256": canonical_sha256(
                    PROFILE_OPTIONS[profile]
                ),
                "inputPaths": jmh_inputs,
                "portableArgv": portable,
                "portableArgvSha256": canonical_sha256(portable),
                "runtimeArgvSha256": canonical_sha256(runtime_identity),
                "liveRuntimeArgvWitness": live_runtime_argv_witness,
                "liveRuntimeArgvWitnessSha256": canonical_sha256(
                    live_runtime_argv_witness
                ),
                "runtimeExecutionPathIdentities": (
                    runtime_execution_identities
                ),
                "runtimeExecutionPathIdentitiesSha256": canonical_sha256(
                    runtime_execution_identities
                ),
                "liveExecutionPathIdentity": (
                    live_execution_path_identity
                ),
                "liveExecutionPathIdentitySha256": canonical_sha256(
                    live_execution_path_identity
                ),
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


def sequential_qualification_selector_contract(
    module,
    *,
    root: Path,
    template: dict,
) -> None:
    """두 실제 shell wrapper 사이의 pinned FD 수명 경계를 가볍게 검증한다."""

    scala_cli = Path.home() / ".local/bin/scala-cli"
    java_home = Path(os.environ["JAVA_HOME"])
    result_dir = root / "result"
    correctness_root = result_dir / "scala/profiles"
    correctness_root.mkdir(parents=True)
    output_root = root / "qualification"
    shim_root = root / "shim"
    shim_root.mkdir()
    shim = shim_root / "python3"
    shim.write_text(
        """#!/usr/bin/python3
import importlib.util
import os
import pathlib
import sys

arguments = sys.argv[1:]
if arguments[:3] == ["-E", "-s", "-S"]:
    arguments = arguments[3:]
target = pathlib.Path(arguments[0])
fd_names = (
    "S1_4X_SCALA_CLI_EXEC_PATH",
    "S1_4X_SCALA_JAVA_PINNED_FD_PATH",
    "S1_4X_SCALA_JAVAC_PINNED_FD_PATH",
)
if target.name == "t3_evidence.py":
    if any(name in os.environ for name in fd_names):
        raise SystemExit("selector inherited dead qualification FD environment")
    output = pathlib.Path(sys.argv[sys.argv.index("--output") + 1])
    output.write_text('{"selectedProfileId":"B"}\\n', encoding="utf-8")
    raise SystemExit(0)
if target.name != "run_profile_qualification.py":
    os.execv("/usr/bin/python3", ["/usr/bin/python3", *sys.argv[1:]])
if any(name not in os.environ for name in fd_names):
    raise SystemExit("qualification wrapper omitted pinned FD environment")
output = pathlib.Path(sys.argv[sys.argv.index("--output-dir") + 1])
output.mkdir(parents=True)
(output / "scala-profile-qualification.v1.json").write_text(
    '{"status":"PASS"}\\n',
    encoding="utf-8",
)
raise SystemExit(0)
""",
        encoding="utf-8",
        newline="\n",
    )
    shim.chmod(0o755)
    environment = os.environ.copy()
    for name in (
        "S1_4X_SCALA_CLI_EXEC_PATH",
        "S1_4X_SCALA_JAVA_PINNED_FD_PATH",
        "S1_4X_SCALA_JAVAC_PINNED_FD_PATH",
    ):
        environment.pop(name, None)
    environment.update(
        {
            "PATH": (
                f"{shim_root}:{Path.home()}/.local/bin:"
                f"{java_home}/bin:/usr/bin:/bin"
            ),
            "JAVA_HOME": str(java_home),
            "RESULT_DIR": str(result_dir),
            "S1_4X_CACHE_ROOT": str(root / "cache"),
            "S1_4X_UV_BIN": str(Path.home() / ".local/bin/uv"),
            "S1_4X_SCALA_CLI_BIN": str(scala_cli),
            "S1_4X_SCALAFIX_BIN": (
                str(
                    Path.home()
                    / ".local/share/s1-4x/"
                    "scalafix-0.14.7/bin/scalafix"
                )
            ),
            "S1_4X_SCALAFMT_ARCHIVE": str(
                Path.home()
                / ".cache/s1-4x/coursier/https/github.com/"
                "scalameta/scalafmt/releases/download/v3.11.4/"
                "scalafmt-x86_64-pc-linux.zip"
            ),
            "S1_4X_SCALAFMT_BIN": str(
                Path.home()
                / ".cache/coursier/arc/https/github.com/"
                "scalameta/scalafmt/releases/download/v3.11.4/"
                "scalafmt-x86_64-pc-linux.zip/scalafmt"
            ),
            "S1_4X_SCALA_JVM_ALLOWLIST_RESULT": str(
                template["jvm_allowlist_path"]
            ),
        }
    )
    (root / "cache").mkdir()
    qualification_process = subprocess.run(
        [
            str(TOOLS_ROOT / "run-profile-qualification.sh"),
            "--plan",
            str(S1_ROOT / "benchmarks/benchmark-plan.v1.json"),
            "--profiles",
            "A,B,C",
            "--enforce-order-plan",
            "--output-dir",
            str(output_root),
        ],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert qualification_process.returncode == 0, (
        qualification_process.stdout,
        qualification_process.stderr,
    )
    for name in (
        "S1_4X_SCALA_CLI_EXEC_PATH",
        "S1_4X_SCALA_JAVA_PINNED_FD_PATH",
        "S1_4X_SCALA_JAVAC_PINNED_FD_PATH",
    ):
        environment.pop(name, None)
    selected_output = root / "selected-profile.json"
    selector_process = subprocess.run(
        [
            str(TOOLS_ROOT / "select-proven-profile.sh"),
            "--plan",
            str(S1_ROOT / "benchmarks/benchmark-plan.v1.json"),
            "--qualification",
            str(output_root / "scala-profile-qualification.v1.json"),
            "--correctness-root",
            str(correctness_root),
            "--output",
            str(selected_output),
        ],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert selector_process.returncode == 0, (
        selector_process.stdout,
        selector_process.stderr,
    )
    selected = json.loads(selected_output.read_text(encoding="utf-8"))
    assert selected["selectedProfileId"] == "B"


def exact_qualification_owner_gate_contract(
    module,
    *,
    root: Path,
    template: dict,
) -> None:
    """실제 -E -s -S parent의 첫 child에서 JDK gate positive path를 검증한다."""

    root.mkdir(parents=True)
    tools_root = SCALA_ROOT / "tools"
    marker = root / "owner-gate-pass.json"
    fake_uv = root / "uv-gate-probe"
    fake_uv.write_text(
        f"""#!/usr/bin/python3
import importlib.util
import json
import os
import pathlib
import sys

tools_root = pathlib.Path({str(tools_root)!r})
sys.path.insert(0, str(tools_root))
helper_path = tools_root / "precompile_jmh_generated_java.py"
specification = importlib.util.spec_from_file_location(
    "precompile_jmh_generated_java_exact_owner_child",
    helper_path,
)
module = importlib.util.module_from_spec(specification)
sys.modules[specification.name] = module
specification.loader.exec_module(module)
snapshot = module._jdk_modules_snapshot(
    pathlib.Path(os.environ["JAVA_HOME"]) / "lib/modules",
    label="TEST_EXACT_QUALIFICATION_OWNER",
)
module._verify_jdk_modules_snapshot(
    snapshot,
    label="TEST_EXACT_QUALIFICATION_OWNER",
)
pathlib.Path({str(marker)!r}).write_text(
    json.dumps({{"sha256": snapshot.sha256}}) + "\\n",
    encoding="utf-8",
)
output = pathlib.Path(sys.argv[sys.argv.index("--output") + 1])
output.write_text('{{"status":"PASS"}}\\n', encoding="utf-8")
""",
        encoding="utf-8",
        newline="\n",
    )
    fake_uv.chmod(0o755)
    fake_jmh = root / "jmh-stop-after-owner-gate"
    fake_jmh.write_text(
        "#!/usr/bin/env bash\nexit 91\n",
        encoding="utf-8",
        newline="\n",
    )
    fake_jmh.chmod(0o755)
    output_root = root / "qualification-output"
    scala_cli = Path.home() / ".local/bin/scala-cli"
    java_home = Path(os.environ["JAVA_HOME"])
    pinned_paths = (
        scala_cli,
        java_home / "bin/java",
        java_home / "bin/javac",
    )
    descriptors = tuple(
        os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        for path in pinned_paths
    )
    try:
        environment = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith("PYTHON")
            and key
            != module.jmh_precompile.JDK_MODULES_GATE_SNAPSHOT_VARIABLE
        }
        environment.update(
            {
                "S1_4X_BENCHMARK_RUN_MODE": "qualification",
                "S1_4X_SCALA_CLI_BIN": str(scala_cli),
                "S1_4X_SCALA_CLI_EXEC_PATH": (
                    f"/proc/self/fd/{descriptors[0]}"
                ),
                "S1_4X_SCALA_JAVA_PINNED_FD_PATH": (
                    f"/proc/self/fd/{descriptors[1]}"
                ),
                "S1_4X_SCALA_JAVAC_PINNED_FD_PATH": (
                    f"/proc/self/fd/{descriptors[2]}"
                ),
            }
        )
        completed = subprocess.run(
            [
                sys.executable,
                "-E",
                "-s",
                "-S",
                str(tools_root / "run_profile_qualification.py"),
                "--plan",
                str(S1_ROOT / "benchmarks/benchmark-plan.v1.json"),
                "--scala-root",
                str(SCALA_ROOT),
                "--correctness-root",
                str(template["correctness_artifact_root"]),
                "--jmh-runner",
                str(fake_jmh),
                "--host-validator",
                str(S1_ROOT / "oracle/validate_environment.py"),
                "--uv",
                str(fake_uv),
                "--jvm-allowlist",
                str(template["jvm_allowlist_path"]),
                "--output-dir",
                str(output_root),
            ],
            check=False,
            capture_output=True,
            text=True,
            env=environment,
            pass_fds=descriptors,
        )
    finally:
        for descriptor in descriptors:
            os.close(descriptor)
    assert completed.returncode == 1, (
        completed.stdout,
        completed.stderr,
    )
    assert "SUBPROCESS_FAILED" in completed.stderr
    assert json.loads(marker.read_text(encoding="utf-8"))["sha256"] == (
        json.loads((SCALA_ROOT / "toolchain-lock.v1.json").read_text())[
            "jdk"
        ]["jdkModulesSha256"]
    )


def main() -> int:
    module = load_module()
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
        sealed_root = Path(directory) / "sealed"
        narrow_root = sealed_root / "narrow"
        narrow_root.mkdir(parents=True)
        sealed_file = narrow_root / "evidence.bin"
        sealed_file.write_bytes(b"sealed-evidence\n")
        root_snapshot = module.SealedEvidenceSnapshot()
        root_snapshot.capture(
            sealed_file,
            root=sealed_root,
            label="test.root.broad",
        )
        expect_t3_error(
            module,
            lambda: root_snapshot.capture(
                sealed_file,
                root=narrow_root,
                label="test.root.narrow",
            ),
            "cached evidence escaped its original root contract",
            expected_prefix="SEALED_EVIDENCE_ROOT_MISMATCH",
        )

        hardlink_source = narrow_root / "hardlink-source.bin"
        hardlink_target = narrow_root / "hardlink-target.bin"
        hardlink_source.write_bytes(b"hardlink\n")
        os.link(hardlink_source, hardlink_target)
        expect_t3_error(
            module,
            lambda: module.SealedEvidenceSnapshot().capture(
                hardlink_source,
                root=narrow_root,
                label="test.hardlink",
            ),
            "hardlinked sealed evidence passed",
            expected_prefix="SEALED_EVIDENCE_NOT_SINGLE_REGULAR",
        )

        symlink_target = narrow_root / "symlink-target.bin"
        symlink_target.symlink_to(sealed_file)
        expect_t3_error(
            module,
            lambda: module.SealedEvidenceSnapshot().capture(
                symlink_target,
                root=narrow_root,
                label="test.symlink",
            ),
            "symlinked sealed evidence passed",
            expected_prefix="SEALED_EVIDENCE_OPEN_FAILED",
        )

        aba_file = narrow_root / "aba.bin"
        aba_file.write_bytes(b"before\n")
        aba_snapshot = module.SealedEvidenceSnapshot()
        aba_snapshot.capture(
            aba_file,
            root=narrow_root,
            label="test.aba",
        )
        aba_file.write_bytes(b"changed")
        aba_file.write_bytes(b"before\n")
        expect_t3_error(
            module,
            aba_snapshot.verify_unchanged,
            "restored-byte ABA evidence passed",
            expected_prefix="SEALED_EVIDENCE_PATH_SUBSTITUTED",
        )

        post_open_file = narrow_root / "post-open.bin"
        post_open_file.write_bytes(b"same-bytes\n")
        replacement = narrow_root / "post-open-replacement.bin"
        replacement.write_bytes(b"same-bytes\n")
        post_open_snapshot = module.SealedEvidenceSnapshot()
        post_open_snapshot.capture(
            post_open_file,
            root=narrow_root,
            label="test.postOpen",
        )
        original_open = post_open_snapshot._open_lexical
        open_count = 0

        def replace_after_last_open(path: Path, *, root: Path, label: str):
            nonlocal open_count
            descriptor, identity = original_open(
                path,
                root=root,
                label=label,
            )
            open_count += 1
            if open_count == 2:
                os.replace(replacement, post_open_file)
            return descriptor, identity

        post_open_snapshot._open_lexical = replace_after_last_open
        expect_t3_error(
            module,
            post_open_snapshot.verify_unchanged,
            "same-byte pathname replacement after final open passed",
            expected_prefix="SEALED_EVIDENCE_PATH_SUBSTITUTED",
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
    full_generated_source_paths = (
        module.jmh_precompile.expected_generated_source_paths()
    )
    assert len(full_generated_source_paths) == 30
    # 30-file exact JMH closure는 focused helper test가 전부 검증한다. 이 테스트는
    # 63-case selector schema를 한 generated source/class pair로 반복 검증한다.
    selector_generated_source_paths = full_generated_source_paths[:1]
    module.jmh_precompile.expected_generated_source_paths = (
        lambda: selector_generated_source_paths
    )

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
    binding_case_root = Path("r1/A/case-01")
    binding_artifact_root = selected_inputs[
        "qualification_artifact_root"
    ]
    binding_receipt_path = (
        binding_artifact_root
        / binding_case_root
        / module.jmh_precompile.RECEIPT_NAME
    )
    binding_compile_stdout = (
        binding_artifact_root
        / binding_case_root
        / module.jmh_precompile.SCALA_COMPILE_STDOUT
    )
    binding_jmh_stdout = (
        binding_artifact_root / binding_case_root / "jmh.stdout"
    )
    binding_fork_path = (
        binding_artifact_root
        / binding_case_root
        / "fork-evidence.normalized.json"
    )
    binding_receipt = json.loads(
        binding_receipt_path.read_text(encoding="utf-8")
    )
    binding_forks = json.loads(
        binding_fork_path.read_text(encoding="utf-8")
    )

    def validate_binding(
        *,
        receipt_value: dict = binding_receipt,
        forks_value: list = binding_forks,
    ) -> None:
        module.validate_jmh_stdout_precompile_binding(
            compile_stdout=binding_compile_stdout,
            jmh_stdout=binding_jmh_stdout,
            receipt=receipt_value,
            fork_evidence=forks_value,
            artifact_root=binding_artifact_root,
            case_root=binding_case_root,
            snapshot=module.SealedEvidenceSnapshot(),
        )

    validate_binding()
    expect_t3_error(
        module,
        lambda: validate_binding(forks_value=[]),
        "empty JMH fork classpath evidence passed",
        expected_prefix="JMH_RUN_STDOUT_BINDING_INVALID",
    )
    mixed_forks = json.loads(json.dumps(binding_forks))
    mixed_forks.append(json.loads(json.dumps(binding_forks[0])))
    mixed_forks[-1]["runtimeClasspathSha256"] = "0" * 64
    expect_t3_error(
        module,
        lambda: validate_binding(forks_value=mixed_forks),
        "mixed JMH fork classpath evidence passed",
        expected_prefix="JMH_RUN_STDOUT_BINDING_INVALID",
    )

    binding_stdout_bytes = binding_jmh_stdout.read_bytes()
    binding_stdout_text = binding_stdout_bytes.decode("utf-8")
    for label, forged_stdout in (
        (
            "JMH generator count tamper",
            binding_stdout_text.replace(
                "Processing 149",
                "Processing 148",
                1,
            ).encode("utf-8"),
        ),
        (
            "JMH version tamper",
            binding_stdout_text.replace(
                "# JMH version: 1.37",
                "# JMH version: 1.36",
                1,
            ).encode("utf-8"),
        ),
        (
            "JMH NUL tamper",
            binding_stdout_bytes.replace(b"Processing ", b"Processing \x00", 1),
        ),
        (
            "JMH CR tamper",
            binding_stdout_bytes.replace(b"\n", b"\r\n", 1),
        ),
    ):
        binding_jmh_stdout.write_bytes(forged_stdout)
        try:
            expect_t3_error(
                module,
                validate_binding,
                f"{label} passed",
                expected_prefix="JMH_RUN_STDOUT_BINDING_INVALID",
            )
        finally:
            binding_jmh_stdout.write_bytes(binding_stdout_bytes)

    def runtime_receipt_tamper() -> dict:
        return json.loads(json.dumps(binding_receipt))

    def close_runtime_tamper(value: dict) -> dict:
        closure = value["jmhRuntimeClosure"]
        closure["runtimeClasspathEntriesSha256"] = canonical_sha256(
            closure["runtimeClasspathEntries"]
        )
        value["jmhRuntimeClosureSha256"] = canonical_sha256(closure)
        return value

    binding_receipt_tampers: list[tuple[dict, str]] = []
    swapped_runtime_mapping = runtime_receipt_tamper()
    swapped_runtime_mapping["jmhRuntimeClosure"]["roleMappings"][0][
        "runtimePathId"
    ] = swapped_runtime_mapping["jmhRuntimeClosure"]["roleMappings"][2][
        "runtimePathId"
    ]
    binding_receipt_tampers.append(
        (
            close_runtime_tamper(swapped_runtime_mapping),
            "self-consistent runtime role mapping swap passed",
        )
    )
    extra_runtime_mapping = runtime_receipt_tamper()
    extra_runtime_mapping["jmhRuntimeClosure"]["roleMappings"].append(
        {
            "role": "FORGED_EXTRA_ROLE",
            "precompilePathId": "COURSIER_CACHE/forged.jar",
            "runtimePathId": "COURSIER_CACHE/forged.jar",
            "sha256": "1" * 64,
        }
    )
    binding_receipt_tampers.append(
        (
            close_runtime_tamper(extra_runtime_mapping),
            "extra finalized runtime role mapping passed",
        )
    )
    extra_runtime_class_output = runtime_receipt_tamper()
    extra_runtime_class_output["jmhRuntimeClosure"][
        "runtimeClasspathEntries"
    ].append(
        json.loads(
            json.dumps(
                extra_runtime_class_output["jmhRuntimeClosure"][
                    "runtimeClasspathEntries"
                ][0]
            )
        )
    )
    binding_receipt_tampers.append(
        (
            close_runtime_tamper(extra_runtime_class_output),
            "extra runtime class-output entry passed",
        )
    )
    reordered_runtime_tail = runtime_receipt_tamper()
    reordered_runtime_tail["jmhRuntimeClosure"][
        "runtimeClasspathEntries"
    ][2:4] = list(
        reversed(
            reordered_runtime_tail["jmhRuntimeClosure"][
                "runtimeClasspathEntries"
            ][2:4]
        )
    )
    binding_receipt_tampers.append(
        (
            close_runtime_tamper(reordered_runtime_tail),
            "runtime dependency order tamper passed",
        )
    )
    forged_runtime_tail_identity = runtime_receipt_tamper()
    forged_runtime_tail_identity["jmhRuntimeClosure"][
        "runtimeClasspathEntries"
    ][2]["identitySha256"] = "0" * 64
    binding_receipt_tampers.append(
        (
            close_runtime_tamper(forged_runtime_tail_identity),
            "runtime dependency identity tamper passed",
        )
    )
    forged_runtime_suffix = runtime_receipt_tamper()
    forged_runtime_suffix["jmhRuntimeClosure"]["generator"][
        "generatedResourceRootPathId"
    ] = (
        "SCALA_WORKSPACE/.scala-build/"
        "selector_runtime_jmh/not-resources"
    )
    binding_receipt_tampers.append(
        (
            close_runtime_tamper(forged_runtime_suffix),
            "runtime generated-resource suffix tamper passed",
        )
    )
    for tampered_receipt, message in binding_receipt_tampers:
        expect_t3_error(
            module,
            lambda value=tampered_receipt: validate_binding(
                receipt_value=value
            ),
            message,
            expected_prefix="JMH_RUN_STDOUT_BINDING_INVALID",
        )

    exact_qualification_owner_gate_contract(
        module,
        root=selector_root / "exact-owner-gate",
        template=selected_inputs,
    )
    selector_snapshot = module.SealedEvidenceSnapshot()
    selected_inputs["evidence_snapshot"] = selector_snapshot
    original_regular_snapshot = (
        module.jmh_precompile._snapshot_regular_file
    )
    jdk_modules_path = Path(os.environ["JAVA_HOME"]) / "lib/modules"
    jdk_modules_full_reads = 0

    def counted_regular_snapshot(
        path: Path,
        *,
        label: str,
        retain_payload: bool = True,
    ):
        nonlocal jdk_modules_full_reads
        if path == jdk_modules_path:
            jdk_modules_full_reads += 1
        return original_regular_snapshot(
            path,
            label=label,
            retain_payload=retain_payload,
        )

    module.jmh_precompile._snapshot_regular_file = (
        counted_regular_snapshot
    )
    selected = module.select_scala_profile(**selected_inputs)
    assert selected["selectedProfileId"] == "B"
    assert selected["fallbackExecuted"] is False
    selected_inputs.pop("evidence_snapshot")
    del selector_snapshot
    witness_run = json.loads(
        (
            selected_inputs["qualification_artifact_root"]
            / "r1/A/case-01/scala-jmh-run-result.v1.json"
        ).read_text(encoding="utf-8")
    )
    witness_snapshot = module.SealedEvidenceSnapshot()
    expected_witness_identities = module.runtime_execution_path_identities(
        scala_cli=selected_inputs["scala_cli"],
        java_executable=Path(os.environ["JAVA_HOME"]) / "bin/java",
        scala_cli_execution_path_id="PINNED_SCALA_CLI_1_15_0_FD",
        snapshot=witness_snapshot,
    )
    module.validate_live_runtime_execution_witness(
        run=witness_run,
        expected_normalized_argv=witness_run["liveRuntimeArgvWitness"][
            "normalizedArgv"
        ],
        expected_execution_path_identities=expected_witness_identities,
        scala_cli=selected_inputs["scala_cli"],
        java_executable=Path(os.environ["JAVA_HOME"]) / "bin/java",
        snapshot=witness_snapshot,
    )
    witness_tamper = json.loads(json.dumps(witness_run))
    witness_tamper["liveExecutionPathIdentity"][0]["fileIdentity"][
        "inode"
    ] += 1
    witness_tamper["liveExecutionPathIdentitySha256"] = canonical_sha256(
        witness_tamper["liveExecutionPathIdentity"]
    )
    expect_t3_error(
        module,
        lambda: module.validate_live_runtime_execution_witness(
            run=witness_tamper,
            expected_normalized_argv=witness_run[
                "liveRuntimeArgvWitness"
            ]["normalizedArgv"],
            expected_execution_path_identities=(
                expected_witness_identities
            ),
            scala_cli=selected_inputs["scala_cli"],
            java_executable=Path(os.environ["JAVA_HOME"]) / "bin/java",
            snapshot=witness_snapshot,
        ),
        "self-consistent fabricated live fstat witness passed",
    )
    del witness_snapshot
    sequential_qualification_selector_contract(
        module,
        root=selector_root / "sequential",
        template=selected_inputs,
    )

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

    qualification_path = (
        selected_inputs["qualification_artifact_root"]
        / "scala-profile-qualification.v1.json"
    )
    qualification_bytes = qualification_path.read_bytes()

    def expect_qualification_error(
        value: dict,
        message: str,
        *,
        expected_prefix: str,
    ) -> None:
        tampered = value["qualification"]
        tampered["selectorConfigSha256"] = module.selector_config_sha256(
            policy=plan["scalaProfileQualification"],
            benchmark_plan_sha256=value["benchmark_plan_sha256"],
            blocks=tampered["blocks"],
        )
        write_json(qualification_path, tampered)
        value["qualification_sha256"] = module.sha256_file(
            qualification_path
        )
        call_value = dict(value)
        call_value["evidence_snapshot"] = module.SealedEvidenceSnapshot()
        try:
            expect_t3_error(
                module,
                lambda: module.select_scala_profile(**call_value),
                message,
                expected_prefix=expected_prefix,
            )
        finally:
            call_value.pop("evidence_snapshot")
            qualification_path.write_bytes(qualification_bytes)

    bad_latin = dict(selected_inputs)
    bad_latin["qualification"] = json.loads(
        json.dumps(selected_inputs["qualification"])
    )
    bad_latin["qualification"]["blocks"][0]["actualProfileOrder"] = [
        "B",
        "A",
        "C",
    ]
    expect_qualification_error(
        bad_latin,
        "Latin order tamper passed",
        expected_prefix="PROFILE_LATIN_ORDER_MISMATCH:1",
    )

    bad_host_closure = dict(selected_inputs)
    bad_host_closure["qualification"] = json.loads(
        json.dumps(selected_inputs["qualification"])
    )
    bad_host_closure["qualification"]["blocks"][0]["profileEvidence"][0][
        "caseCount"
    ] = 6
    expect_qualification_error(
        bad_host_closure,
        "per-profile host/JVM closure tamper passed",
        expected_prefix="PROFILE_HOST_JVM_CASE_CLOSURE_MISMATCH:1:A",
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
        (
            missing_measurement_sha,
            "measurement SHA omission passed",
            "PROFILE_MEASUREMENTS_MISSING:1",
        )
    )
    extra_measurement_field = qualification_tamper()
    extra_measurement_field["qualification"]["blocks"][0]["measurements"][0][
        "forged"
    ] = True
    qualification_tampers.append(
        (
            extra_measurement_field,
            "measurement extra field passed",
            "PROFILE_MEASUREMENTS_MISSING:1",
        )
    )
    profile_jvm_tamper = qualification_tamper()
    profile_jvm_tamper["qualification"]["blocks"][0]["profileEvidence"][0][
        "effectiveJvmArgsSha256"
    ] = SHA
    qualification_tampers.append(
        (
            profile_jvm_tamper,
            "profile JVM hash tamper passed",
            "PROFILE_HOST_JVM_CASE_CLOSURE_MISMATCH:1:A",
        )
    )
    block_jvm_tamper = qualification_tamper()
    block_jvm_tamper["qualification"]["blocks"][0][
        "effectiveJvmArgsSha256"
    ] = SHA
    qualification_tampers.append(
        (
            block_jvm_tamper,
            "block JVM hash tamper passed",
            "PROFILE_BLOCK_HASH_CLOSURE_MISMATCH:1",
        )
    )
    block_host_tamper = qualification_tamper()
    block_host_tamper["qualification"]["blocks"][0][
        "hostValiditySha256"
    ] = SHA
    qualification_tampers.append(
        (
            block_host_tamper,
            "block host hash tamper passed",
            "PROFILE_BLOCK_HASH_CLOSURE_MISMATCH:1",
        )
    )
    case_order_tamper = qualification_tamper()
    case_order_tamper["qualification"]["blocks"][0]["profileEvidence"][0][
        "actualCaseOrder"
    ] = list(
        reversed(plan["scalaProfileQualification"]["qualificationCaseOrder"])
    )
    qualification_tampers.append(
        (
            case_order_tamper,
            "actual case order tamper passed",
            "PROFILE_HOST_JVM_CASE_CLOSURE_MISMATCH:1:A",
        )
    )
    timestamp_tamper = qualification_tamper()
    timestamp_tamper["qualification"]["blocks"][0]["profileEvidence"][0][
        "endedAt"
    ] = "2026-07-17T00:00:00.000000Z"
    qualification_tampers.append(
        (
            timestamp_tamper,
            "reversed profile timestamp passed",
            "PROFILE_HOST_JVM_CASE_CLOSURE_MISMATCH:1:A",
        )
    )
    cli_identity_tamper = qualification_tamper()
    cli_identity_tamper["qualification"]["blocks"][0]["profileEvidence"][0][
        "scalaCliBinarySha256"
    ] = "b" * 64
    qualification_tampers.append(
        (
            cli_identity_tamper,
            "Scala CLI identity tamper passed",
            "PROFILE_HOST_JVM_CASE_CLOSURE_MISMATCH:1:A",
        )
    )
    bool_score = qualification_tamper()
    bool_score["qualification"]["blocks"][0]["measurements"][0][
        "scoreNsPerInvocation"
    ] = True
    qualification_tampers.append(
        (
            bool_score,
            "bool selector score passed",
            "PROFILE_SCORE_INVALID",
        )
    )
    for tampered_inputs, message, expected_prefix in qualification_tampers:
        expect_qualification_error(
            tampered_inputs,
            message,
            expected_prefix=expected_prefix,
        )
    assert jdk_modules_full_reads == 2, (
        "63-case selector와 synthetic metadata negatives가 JDK modules를 "
        f"gate 전후 1회씩만 읽어야 한다: {jdk_modules_full_reads}"
    )
    module.jmh_precompile._snapshot_regular_file = (
        original_regular_snapshot
    )

    raw_correctness = (
        selected_inputs["correctness_artifact_root"]
        / "A/property/scala-property-report.v1.json"
    )
    raw_correctness_bytes = raw_correctness.read_bytes()
    raw_correctness_value = json.loads(raw_correctness_bytes)
    raw_correctness_value["aggregateStatus"] = "FAIL"
    write_json(raw_correctness, raw_correctness_value)
    raw_correctness_inputs = dict(selected_inputs)
    raw_correctness_inputs["evidence_snapshot"] = (
        module.SealedEvidenceSnapshot()
    )
    try:
        expect_t3_error(
            module,
            lambda: module.select_scala_profile(
                **raw_correctness_inputs
            ),
            "raw correctness byte tamper passed",
            expected_prefix=(
                "PROFILE_CORRECTNESS_RAW_HASH_DRIFT:"
                "A:propertyReportSha256"
            ),
        )
    finally:
        raw_correctness.write_bytes(raw_correctness_bytes)
        raw_correctness_inputs.pop("evidence_snapshot")

    raw_tamper_inputs = dict(selected_inputs)
    raw_tamper_inputs["evidence_snapshot"] = (
        module.SealedEvidenceSnapshot()
    )
    raw_native = (
        raw_tamper_inputs["qualification_artifact_root"]
        / "r1/A/case-01/native.json"
    )
    raw_native_bytes = raw_native.read_bytes()
    raw_value = json.loads(raw_native.read_text())
    raw_value[0]["primaryMetric"]["score"] = 77.0
    write_json(raw_native, raw_value)
    try:
        expect_t3_error(
            module,
            lambda: module.select_scala_profile(**raw_tamper_inputs),
            "raw JMH byte tamper passed",
            expected_prefix=(
                "QUALIFICATION_NATIVE_VALIDATION_DRIFT:"
                f"1:A:{case_order[0]}"
            ),
        )
    finally:
        raw_native.write_bytes(raw_native_bytes)
        raw_tamper_inputs.pop("evidence_snapshot")

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
    module.jmh_precompile.expected_generated_source_paths = (
        lambda: full_generated_source_paths
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
    reported_jmh_arguments = [
        "-Dscala.sources=/sealed/source.scala",
        "-Dscala.source.names=source.scala",
        "-Djava.io.tmpdir=/sealed/smoke/jmh-tmp",
    ]
    compile_command_sha256 = "8" * 64
    observed_jmh_arguments = [
        *reported_jmh_arguments,
        "-XX:+UnlockDiagnosticVMOptions",
        "-XX:+UnlockExperimentalVMOptions",
        "-DcompilerBlackholesEnabled=true",
        (
            "-XX:CompileCommandFile="
            "/sealed/smoke/jmh-tmp/jmh-smoke-123.compilecommand"
        ),
    ]
    fork_evidence = [
        {
            "schemaVersion": "s1.4x-scala-jvm-fork-evidence-v1",
            "forkIndex": index,
            "javaExecutablePathId": "TEMURIN_25_0_3_9_LTS/bin/java",
            "javaExecutableSha256": "9" * 64,
            "runtimeVersion": "25.0.3+9-LTS",
            "vendor": "Eclipse Adoptium",
            "javaHomePathId": "TEMURIN_25_0_3_9_LTS",
            "inputArguments": list(observed_jmh_arguments),
            "inputArgumentFiles": [
                {
                    "argumentIndex": len(observed_jmh_arguments) - 1,
                    "argumentPrefix": "-XX:CompileCommandFile=",
                    "pathId": "JMH_COMPILE_COMMAND_FILE",
                    "sha256": compile_command_sha256,
                    "fileIdentitySha256": "7" * 64,
                }
            ],
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
    assert jvm_allowlist["effectiveJvmArguments"] == [
        *reported_jmh_arguments[:-1],
        module.JMH_TMPDIR_PORTABLE_ARGUMENT,
        *observed_jmh_arguments[len(reported_jmh_arguments) : -1],
        (
            "-XX:CompileCommandFile=JMH_COMPILE_COMMAND_FILE"
            f"#sha256={compile_command_sha256}"
        ),
    ]
    for index, fork_value in enumerate(fork_evidence, start=1):
        fork_tmp = f"/sealed/qualification-{index}/jmh-tmp"
        fork_value["inputArguments"][len(reported_jmh_arguments) - 1] = (
            f"-Djava.io.tmpdir={fork_tmp}"
        )
        fork_value["inputArguments"][-1] = (
            f"-XX:CompileCommandFile={fork_tmp}/"
            f"jmh-qualification-{index}.compilecommand"
        )
    effective_jvm = module.validate_effective_jvm_evidence(
        fork_evidence,
        expected_forks=3,
        allowlist=jvm_allowlist,
        allowlist_sha256="1" * 64,
    )
    assert effective_jvm["aggregateStatus"] == "PASS"
    assert effective_jvm["forkCount"] == 3
    assert effective_jvm["effectiveJvmArguments"] == (
        jvm_allowlist["effectiveJvmArguments"]
    )
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
    changed_compile_command = json.loads(json.dumps(fork_evidence))
    changed_compile_command[0]["inputArgumentFiles"][0]["sha256"] = "7" * 64
    expect_t3_error(
        module,
        lambda: module.validate_effective_jvm_evidence(
            changed_compile_command,
            expected_forks=3,
            allowlist=jvm_allowlist,
            allowlist_sha256="1" * 64,
        ),
        "changed JMH CompileCommandFile bytes passed",
    )
    forged_compile_command_index = json.loads(json.dumps(fork_evidence))
    forged_compile_command_index[0]["inputArgumentFiles"][0][
        "argumentIndex"
    ] = 0
    expect_t3_error(
        module,
        lambda: module.validate_effective_jvm_evidence(
            forged_compile_command_index,
            expected_forks=3,
            allowlist=jvm_allowlist,
            allowlist_sha256="1" * 64,
        ),
        "forged JMH CompileCommandFile argv binding passed",
    )
    forbidden_tmp = json.loads(json.dumps(fork_evidence))
    forbidden_tmp[0]["inputArguments"][len(reported_jmh_arguments) - 1] = (
        "-Djava.io.tmpdir=/tmp/jmh-tmp"
    )
    forbidden_tmp[0]["inputArguments"][-1] = (
        "-XX:CompileCommandFile=/tmp/jmh-tmp/jmh.compilecommand"
    )
    expect_t3_error(
        module,
        lambda: module.validate_effective_jvm_evidence(
            forbidden_tmp,
            expected_forks=3,
            allowlist=jvm_allowlist,
            allowlist_sha256="1" * 64,
        ),
        "forbidden global tmp JMH argument file passed",
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
            "params": None,
            "primaryMetric": {
                "score": 12.5,
                "scoreUnit": "ns/op",
                "rawData": [[12.5]],
            },
            "jvmArgs": reported_jmh_arguments,
        }
    ]
    validated = module.validate_jmh_native_json(
        native,
        expected_benchmark=native[0]["benchmark"],
        expected_forks=1,
        effective_jvm_arguments=jvm_allowlist["effectiveJvmArguments"],
        expected_warmup_iterations=1,
        expected_warmup_time="200ms",
        expected_measurement_iterations=1,
        expected_measurement_time="200ms",
        logical_operations_per_invocation=32,
    )
    assert validated["nativeValue"] == 12.5
    assert validated["reportedJvmArguments"] == [
        *reported_jmh_arguments[:-1],
        module.JMH_TMPDIR_PORTABLE_ARGUMENT,
    ]
    assert validated["effectiveJvmArguments"] == (
        jvm_allowlist["effectiveJvmArguments"]
    )
    forged_reported_arguments = json.loads(json.dumps(native))
    forged_reported_arguments[0]["jvmArgs"].append(
        "-DcompilerBlackholesEnabled=true"
    )
    expect_t3_error(
        module,
        lambda: module.validate_jmh_native_json(
            forged_reported_arguments,
            expected_benchmark=native[0]["benchmark"],
            expected_forks=1,
            effective_jvm_arguments=jvm_allowlist[
                "effectiveJvmArguments"
            ],
            expected_warmup_iterations=1,
            expected_warmup_time="200ms",
            expected_measurement_iterations=1,
            expected_measurement_time="200ms",
            logical_operations_per_invocation=32,
        ),
        "JMH reported JVM arguments accepted launcher-only flags",
    )
    native_tampers = []
    fabricated_empty_params = json.loads(json.dumps(native))
    fabricated_empty_params[0]["params"] = {}
    native_tampers.append(
        (
            fabricated_empty_params,
            "fabricated empty JMH params object passed",
        )
    )
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
                effective_jvm_arguments=jvm_allowlist[
                    "effectiveJvmArguments"
                ],
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
            effective_jvm_arguments=jvm_allowlist[
                "effectiveJvmArguments"
            ],
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
