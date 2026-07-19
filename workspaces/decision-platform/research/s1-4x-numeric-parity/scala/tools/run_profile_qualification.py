#!/usr/bin/env python3
"""Frozen Latin-rotated Scala A/B/C JMH qualification을 순차 실행한다."""

from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from t3_evidence import SCALA_PROFILES
from t3_evidence import canonical_sha256
from t3_evidence import sha256_file
from t3_evidence import strict_json
from t3_evidence import strict_json_value
from t3_evidence import validate_correctness
from t3_evidence import require_portable_argv
from t3_evidence import selector_config_sha256
from t3_evidence import write_exclusive_json


class QualificationError(ValueError):
    """Qualification plan, host validity, or JMH block closure failure."""


def run_checked(command: list[str], *, environment: dict[str, str]) -> None:
    completed = subprocess.run(command, env=environment, check=False)
    if completed.returncode != 0:
        raise QualificationError(
            f"SUBPROCESS_FAILED:{canonical_sha256(command)}:{completed.returncode}"
        )


def utc_now() -> str:
    return (
        dt.datetime.now(dt.timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def host_command(
    *,
    uv: Path,
    oracle_root: Path,
    validator: Path,
    output: Path,
    plan: dict[str, Any],
) -> list[str]:
    policy = plan["environmentValidity"]
    cpu_set = ",".join(str(value) for value in plan["execution"]["cpuSet"])
    return [
        str(uv),
        "run",
        "--project",
        str(oracle_root),
        "--frozen",
        "python",
        str(validator),
        "--home",
        str(Path.home()),
        "--cpu-set",
        cpu_set,
        "--min-home-free-bytes",
        "32212254720",
        "--min-available-memory-bytes",
        str(int(policy["minAvailableMemoryGiB"]) * 1024**3),
        "--max-normalized-load1",
        str(policy["maxNormalizedLoad1"]),
        "--load-samples",
        str(policy["loadSampleCount"]),
        "--sample-interval-seconds",
        str(policy["loadSampleIntervalSeconds"]),
        "--max-quiet-wait-seconds",
        str(policy["maxQuietWaitSeconds"]),
        "--max-running-containers",
        str(policy["runningContainerCount"]),
        "--external-process-sample-seconds",
        "30",
        "--max-external-process-cpu-percent",
        str(policy["externalProcessCpuPercentThreshold"]),
        "--allowed-process-root-pid",
        str(os.getpid()),
        "--output",
        str(output),
    ]


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--scala-root", type=Path, required=True)
    parser.add_argument("--correctness-root", type=Path, required=True)
    parser.add_argument("--jmh-runner", type=Path, required=True)
    parser.add_argument("--host-validator", type=Path, required=True)
    parser.add_argument("--uv", type=Path, required=True)
    parser.add_argument("--jvm-allowlist", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()
    try:
        plan = strict_json(arguments.plan)
        policy = plan.get("scalaProfileQualification")
        if (
            plan.get("schemaVersion") != "s1.4x-benchmark-plan-v1"
            or not isinstance(policy, dict)
            or policy.get("profileOrderBlocks")
            != [["A", "B", "C"], ["B", "C", "A"], ["C", "A", "B"]]
            or policy.get("hostValidityBeforeEachProfileBlock") is not True
        ):
            raise QualificationError("FROZEN_LATIN_PLAN_INVALID")
        correctness = {
            profile: strict_json(
                arguments.correctness_root
                / profile
                / "scala-profile-correctness-result.v1.json"
            )
            for profile in SCALA_PROFILES
        }
        validate_correctness(correctness)
        scala_cli_value = os.environ.get("S1_4X_SCALA_CLI_BIN")
        if not scala_cli_value:
            raise QualificationError("SCALA_CLI_BINARY_REQUIRED")
        scala_cli = Path(scala_cli_value)
        if (
            not scala_cli.is_absolute()
            or not scala_cli.is_file()
            or scala_cli.is_symlink()
            or not arguments.jvm_allowlist.is_absolute()
            or not arguments.jvm_allowlist.is_file()
            or arguments.jvm_allowlist.is_symlink()
        ):
            raise QualificationError("QUALIFICATION_TOOL_PATH_INVALID")
        scala_cli_sha = sha256_file(scala_cli)
        toolchain_lock_path = arguments.scala_root / "toolchain-lock.v1.json"
        toolchain_lock = strict_json(toolchain_lock_path)
        allowlist = strict_json(arguments.jvm_allowlist)
        allowlist_sha = sha256_file(arguments.jvm_allowlist)
        if (
            toolchain_lock.get("schemaVersion")
            != "s1.4x-scala-toolchain-lock-v1"
            or toolchain_lock.get("scalaCli", {}).get("binarySha256")
            != scala_cli_sha
            or allowlist.get("schemaVersion")
            != "s1.4x-scala-jvm-argument-allowlist-v1"
            or allowlist.get("status") != "PASS"
            or allowlist.get("benchmarkPlanSha256")
            != sha256_file(arguments.plan)
            or allowlist.get("toolchainLockSha256")
            != sha256_file(toolchain_lock_path)
            or allowlist.get("javaExecutableSha256")
            != toolchain_lock.get("jdk", {}).get("javaExecutableSha256")
        ):
            raise QualificationError("QUALIFICATION_TOOLCHAIN_ALLOWLIST_DRIFT")
        manifest_path = arguments.scala_root / "source-inputs.v1.json"
        manifest_sha = sha256_file(manifest_path)
        profiles_path = arguments.scala_root / "compiler-profiles.v1.json"
        profiles = strict_json(profiles_path)
        manifest = strict_json(manifest_path)
        jmh_input_paths = sorted(
            [
                path
                for path, metadata in manifest["files"].items()
                if metadata["role"] in {"configuration", "main", "benchmark"}
            ],
            key=lambda value: value.encode("utf-8"),
        )
        output_dir = arguments.output_dir
        if output_dir.exists() or output_dir.is_symlink():
            raise QualificationError("OUTPUT_DIRECTORY_MUST_BE_NEW")
        output_dir.mkdir(parents=True)
        scala_cli_pin = os.environ.get("S1_4X_SCALA_CLI_EXEC_PATH")
        if not scala_cli_pin:
            raise QualificationError("SCALA_CLI_PINNED_FD_PATH_REQUIRED")
        self_scala_cli_pin = re.fullmatch(
            r"/proc/self/fd/([0-9]+)",
            scala_cli_pin,
        )
        if self_scala_cli_pin is not None:
            scala_cli_pin = (
                f"/proc/{os.getpid()}/fd/{self_scala_cli_pin.group(1)}"
            )
            os.environ["S1_4X_SCALA_CLI_EXEC_PATH"] = scala_cli_pin
        elif re.fullmatch(
            r"/proc/[1-9][0-9]*/fd/[0-9]+",
            scala_cli_pin,
        ) is None:
            raise QualificationError("SCALA_CLI_PINNED_FD_PATH_INVALID")
        if (
            not Path(scala_cli_pin).is_file()
            or not os.access(scala_cli_pin, os.X_OK)
            or sha256_file(Path(scala_cli_pin)) != scala_cli_sha
        ):
            raise QualificationError(
                "SCALA_CLI_PINNED_FD_IDENTITY_MISMATCH"
            )
        java_pin = os.environ.get("S1_4X_SCALA_JAVA_PINNED_FD_PATH")
        if not java_pin:
            raise QualificationError("JAVA_PINNED_FD_PATH_REQUIRED")
        self_pin = re.fullmatch(r"/proc/self/fd/([0-9]+)", java_pin)
        if self_pin is not None:
            java_pin = f"/proc/{os.getpid()}/fd/{self_pin.group(1)}"
            os.environ["S1_4X_SCALA_JAVA_PINNED_FD_PATH"] = java_pin
        elif re.fullmatch(
            r"/proc/[1-9][0-9]*/fd/[0-9]+",
            java_pin,
        ) is None:
            raise QualificationError("JAVA_PINNED_FD_PATH_INVALID")
        if (
            not Path(java_pin).is_file()
            or not os.access(java_pin, os.X_OK)
            or sha256_file(Path(java_pin))
            != allowlist["javaExecutableSha256"]
        ):
            raise QualificationError("JAVA_PINNED_FD_IDENTITY_MISMATCH")
        javac_pin = os.environ.get("S1_4X_SCALA_JAVAC_PINNED_FD_PATH")
        if not javac_pin:
            raise QualificationError("JAVAC_PINNED_FD_PATH_REQUIRED")
        self_javac_pin = re.fullmatch(r"/proc/self/fd/([0-9]+)", javac_pin)
        if self_javac_pin is not None:
            javac_pin = (
                f"/proc/{os.getpid()}/fd/{self_javac_pin.group(1)}"
            )
            os.environ["S1_4X_SCALA_JAVAC_PINNED_FD_PATH"] = javac_pin
        elif re.fullmatch(
            r"/proc/[1-9][0-9]*/fd/[0-9]+",
            javac_pin,
        ) is None:
            raise QualificationError("JAVAC_PINNED_FD_PATH_INVALID")
        if (
            not Path(javac_pin).is_file()
            or not os.access(javac_pin, os.X_OK)
            or sha256_file(Path(javac_pin))
            != toolchain_lock["jdk"]["javacExecutableSha256"]
        ):
            raise QualificationError("JAVAC_PINNED_FD_IDENTITY_MISMATCH")
        environment = os.environ.copy()
        oracle_root = arguments.scala_root.parent / "oracle"
        blocks = []
        all_effective_hashes = []
        profile_run_input_paths: set[str] = set()

        for repetition, profile_order in enumerate(
            policy["profileOrderBlocks"],
            start=1,
        ):
            measurements = []
            profile_evidence = []
            actual_profile_order = []
            for profile in profile_order:
                started_at = utc_now()
                profile_root = output_dir / f"r{repetition}" / profile
                profile_root.mkdir(parents=True)
                host_output = profile_root / "host-validity.json"
                run_checked(
                    host_command(
                        uv=arguments.uv,
                        oracle_root=oracle_root,
                        validator=arguments.host_validator,
                        output=host_output,
                        plan=plan,
                    ),
                    environment=environment,
                )
                host_result = strict_json(host_output)
                if host_result.get("status") != "PASS":
                    raise QualificationError("HOST_VALIDITY_FAILED")

                profile_effective_hashes = []
                actual_case_order = []
                for case_index, case_id in enumerate(
                    policy["qualificationCaseOrder"],
                    start=1,
                ):
                    case_root = profile_root / f"case-{case_index:02d}"
                    run_checked(
                        [
                            str(arguments.jmh_runner),
                            "--plan",
                            str(arguments.plan),
                            "--profile",
                            profile,
                            "--case-id",
                            case_id,
                            "--mode",
                            "qualification",
                            "--jvm-allowlist",
                            str(arguments.jvm_allowlist),
                            "--output-dir",
                            str(case_root),
                        ],
                        environment=environment,
                    )
                    native_path = case_root / "native.json"
                    native = strict_json_value(native_path)
                    if not isinstance(native, list) or len(native) != 1:
                        raise QualificationError("JMH_EXACT_ONE_RESULT_REQUIRED")
                    score = native[0].get("primaryMetric", {}).get("score")
                    if type(score) not in (int, float):
                        raise QualificationError("JMH_SCORE_MISSING")
                    effective_path = (
                        case_root / "scala-effective-jvm-args-result.v1.json"
                    )
                    effective_hash = sha256_file(effective_path)
                    run_result_path = (
                        case_root / "scala-jmh-run-result.v1.json"
                    )
                    run_result = strict_json(run_result_path)
                    validation_path = (
                        case_root
                        / "scala-jmh-native-validation.v1.json"
                    )
                    precompile_path = (
                        case_root
                        / "scala-jmh-generated-java-precompile.v1.json"
                    )
                    validation = strict_json(validation_path)
                    logical_operations = next(
                        item["logicalOperationsPerInvocation"]
                        for item in plan["cases"]
                        if item["caseId"] == case_id
                    )
                    if (
                        run_result.get("schemaVersion")
                        != "s1.4x-scala-jmh-run-result-v1"
                        or run_result.get("aggregateStatus") != "PASS"
                        or run_result.get("profileId") != profile
                        or run_result.get("caseId") != case_id
                        or run_result.get("runMode") != "qualification"
                        or run_result.get(
                            "logicalOperationsPerInvocation"
                        )
                        != logical_operations
                        or run_result.get("rawScoreNsPerInvocation")
                        != score
                        or run_result.get(
                            "normalizedScoreNsPerLogicalOperation"
                        )
                        != score / logical_operations
                        or run_result.get("sourceInputManifestSha256")
                        != manifest_sha
                        or run_result.get("scalaCliBinarySha256")
                        != scala_cli_sha
                        or run_result.get("profileOptionsSha256")
                        != canonical_sha256(
                            profiles["profiles"][profile]["additionalOptions"]
                        )
                        or run_result.get("inputPaths") != jmh_input_paths
                        or run_result.get("jvmArgumentAllowlistSha256")
                        != allowlist_sha
                        or run_result.get("rawNativeJsonSha256")
                        != sha256_file(native_path)
                        or run_result.get("effectiveJvmArgsSha256")
                        != effective_hash
                        or run_result.get("nativeValidationSha256")
                        != sha256_file(validation_path)
                        or run_result.get(
                            "generatedJavaPrecompileReceiptSha256"
                        )
                        != sha256_file(precompile_path)
                        or validation.get("rawScoreNsPerInvocation")
                        != score
                        or validation.get(
                            "normalizedScoreNsPerLogicalOperation"
                        )
                        != score / logical_operations
                    ):
                        raise QualificationError(
                            "JMH_RUN_IDENTITY_MISMATCH"
                        )
                    require_portable_argv(
                        run_result.get("portableArgv"),
                        f"qualification.r{repetition}.{profile}.{case_id}",
                    )
                    profile_effective_hashes.append(effective_hash)
                    all_effective_hashes.append(effective_hash)
                    profile_run_input_paths.update(run_result["inputPaths"])
                    actual_case_order.append(case_id)
                    measurements.append(
                        {
                            "profileId": profile,
                            "caseId": case_id,
                            "scoreNsPerInvocation": score,
                            "rawNativeJsonSha256": sha256_file(native_path),
                            "effectiveJvmArgsSha256": effective_hash,
                            "jmhRunResultSha256": sha256_file(run_result_path),
                        }
                    )
                ended_at = utc_now()
                actual_profile_order.append(profile)
                profile_evidence.append(
                    {
                        "profileId": profile,
                        "plannedCaseOrder": policy[
                            "qualificationCaseOrder"
                        ],
                        "actualCaseOrder": actual_case_order,
                        "startedAt": started_at,
                        "endedAt": ended_at,
                        "hostValiditySha256": sha256_file(host_output),
                        "scalaCliBinarySha256": scala_cli_sha,
                        "profileOptionsSha256": canonical_sha256(
                            profiles["profiles"][profile]["additionalOptions"]
                        ),
                        "sourceInputManifestSha256": manifest_sha,
                        "effectiveJvmArgsSha256": canonical_sha256(
                            profile_effective_hashes
                        ),
                        "caseCount": len(profile_effective_hashes),
                    }
                )
            blocks.append(
                {
                    "outerRepetition": repetition,
                    "plannedProfileOrder": profile_order,
                    "actualProfileOrder": actual_profile_order,
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

        result = {
            "schemaVersion": "s1.4x-scala-profile-qualification-v1",
            "benchmarkPlanSha256": sha256_file(arguments.plan),
            "selectorConfigSha256": selector_config_sha256(
                policy=policy,
                benchmark_plan_sha256=sha256_file(arguments.plan),
                blocks=blocks,
            ),
            "sourceInputManifestSha256": sha256_file(
                manifest_path
            ),
            "profileOptionsSha256": canonical_sha256(
                {
                    profile: profiles["profiles"][profile]["additionalOptions"]
                    for profile in SCALA_PROFILES
                }
            ),
            "effectiveJvmArgsClosureSha256": canonical_sha256(
                all_effective_hashes
            ),
            "scalaCliBinarySha256": scala_cli_sha,
            "jvmArgumentAllowlistSha256": allowlist_sha,
            "profileRunInputPaths": sorted(
                profile_run_input_paths,
                key=lambda value: value.encode("utf-8"),
            ),
            "blocks": blocks,
            "status": "PASS",
        }
        write_exclusive_json(
            output_dir / "scala-profile-qualification.v1.json",
            result,
        )
    except (OSError, UnicodeError, ValueError, QualificationError) as error:
        print(f"SCALA_PROFILE_QUALIFICATION_FAIL:{error}", file=sys.stderr)
        return 1
    print(
        "SCALA_PROFILE_QUALIFICATION_PASS "
        f"blocks={len(blocks)} measurements={sum(len(item['measurements']) for item in blocks)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
