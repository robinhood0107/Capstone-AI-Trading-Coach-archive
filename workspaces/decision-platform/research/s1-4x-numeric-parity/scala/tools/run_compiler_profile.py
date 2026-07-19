#!/usr/bin/env python3
"""Pinned Scala hard compiler/profile smoke를 실행하고 portable evidence를 만든다."""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from source_input_manifest import validated_source_files
from t3_evidence import canonical_sha256
from t3_evidence import sha256_file
from t3_evidence import strict_json
from t3_evidence import write_exclusive_json


class CompilerProfileError(ValueError):
    """Frozen compiler/profile 입력이나 실행 evidence가 어긋났음을 나타낸다."""


TEST_DEPENDENCY_DIRECTIVE = re.compile(
    r"//> using test\.dep "
    r"([A-Za-z0-9_.-]+:{1,3}[A-Za-z0-9_.-]+:[A-Za-z0-9_.+-]+)"
)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def portable_argv(
    command: list[str],
    *,
    scala_root: Path,
    scala_cli: Path,
) -> list[str]:
    result = []
    root_prefix = f"{scala_root}/"
    for item in command:
        if item == str(scala_cli):
            result.append("SCALA_CLI_1_15_0")
        elif item == str(scala_root):
            result.append("SCALA_ROOT")
        elif item.startswith(root_prefix):
            result.append(f"SCALA_ROOT/{item.removeprefix(root_prefix)}")
        else:
            result.append(item)
    return result


def run_process(
    *,
    command: list[str],
    process_id: str,
    output_dir: Path,
    scala_root: Path,
    scala_cli: Path,
    environment: dict[str, str],
) -> dict[str, Any]:
    completed = subprocess.run(
        command,
        cwd=scala_root,
        env=environment,
        check=False,
        capture_output=True,
    )
    stdout_path = output_dir / "logs" / f"{process_id}.stdout"
    stderr_path = output_dir / "logs" / f"{process_id}.stderr"
    stdout_path.write_bytes(completed.stdout)
    stderr_path.write_bytes(completed.stderr)
    portable = portable_argv(
        command,
        scala_root=scala_root,
        scala_cli=scala_cli,
    )
    return {
        "processId": process_id,
        "portableArgv": portable,
        "portableArgvSha256": canonical_sha256(portable),
        "runtimeArgvSha256": canonical_sha256(command),
        "exitCode": completed.returncode,
        "stdoutSha256": sha256_bytes(completed.stdout),
        "stderrSha256": sha256_bytes(completed.stderr),
    }


def compiler_arguments(
    scala_cli: Path,
    source: Path,
    *,
    scala_version: str,
    option_groups: list[list[str]],
) -> list[str]:
    command = [
        str(scala_cli),
        "compile",
        str(source),
        "--scala",
        scala_version,
        "--server=false",
        "--jvm",
        "system",
        "--coursier-validate-checksums",
    ]
    for group in option_groups:
        command.extend(f"--scalac-option={option}" for option in group)
    return command


def project_test_dependencies(project_source: Path) -> list[str]:
    """명시 source compile에서 빠지는 test dependency 핀을 project.scala에서 읽는다."""

    dependencies: list[str] = []
    for line in project_source.read_text(encoding="utf-8").splitlines():
        if not line.startswith("//> using test.dep"):
            continue
        matched = TEST_DEPENDENCY_DIRECTIVE.fullmatch(line)
        if matched is None:
            raise CompilerProfileError("TEST_DEPENDENCY_DIRECTIVE_INVALID")
        dependency = matched.group(1)
        if dependency in dependencies:
            raise CompilerProfileError("TEST_DEPENDENCY_DIRECTIVE_DUPLICATE")
        dependencies.append(dependency)
    if not dependencies:
        raise CompilerProfileError("TEST_DEPENDENCY_DIRECTIVE_MISSING")
    return dependencies


def project_compiler_arguments(
    scala_cli: Path,
    sources: list[Path],
    *,
    profile_arguments: list[str],
    test_dependencies: list[str],
) -> list[str]:
    """전체 source closure를 test scope 오인 없이 한 compile 입력으로 만든다."""

    dependency_arguments = [
        argument
        for dependency in test_dependencies
        for argument in ("--dependency", dependency)
    ]
    return [
        str(scala_cli),
        "--power",
        "compile",
        *map(str, sources),
        "--server=false",
        "--jvm",
        "system",
        "--coursier-validate-checksums",
        *dependency_arguments,
        *profile_arguments,
    ]


def project_directive_options(project: Path) -> list[str]:
    prefix = "//> using option "
    return [
        line.removeprefix(prefix)
        for line in project.read_text(encoding="utf-8").splitlines()
        if line.startswith(prefix)
    ]


def diagnostic_disposition(
    stderr: bytes,
    *,
    expected_pattern: str,
    forbidden_pattern: str,
) -> dict[str, Any]:
    text = stderr.decode("utf-8", errors="replace")
    expected = re.search(expected_pattern, text)
    forbidden = re.search(forbidden_pattern, text)
    matched = expected is not None and forbidden is None
    value = {
        "expectedDiagnosticPattern": expected_pattern,
        "forbiddenDiagnosticPattern": forbidden_pattern,
        "expectedDiagnosticMatched": expected is not None,
        "forbiddenDiagnosticMatched": forbidden is not None,
        "status": "PASS" if matched else "FAIL",
    }
    return {
        **value,
        "diagnosticDispositionSha256": canonical_sha256(value),
    }


def diagnostic_probe_disposition(
    stdout: bytes,
    stderr: bytes,
    *,
    option: str,
    expected_pattern: str,
    exit_code: int,
) -> dict[str, Any]:
    """Advisory probe의 clean/warning-Werror와 unrelated failure를 분리한다."""

    text = (stdout + b"\n" + stderr).decode("utf-8", errors="replace")
    match_count = len(re.findall(expected_pattern, text))
    unrelated = re.search(
        r"(?i)(internal compiler error|exception|out ?of ?memory|"
        r"killed|fatal error|stack overflow|syntax error|not found:)",
        text,
    )
    if exit_code == 0 and unrelated is None:
        disposition = "CLEAN_NO_BLOCKING_DIAGNOSTIC"
        status = "PASS"
    elif exit_code == 1 and match_count > 0 and unrelated is None:
        disposition = "RECORDED_DIAGNOSTIC_WERROR"
        status = "PASS"
    else:
        disposition = "UNRELATED_FAILURE"
        status = "FAIL"
    value = {
        "option": option,
        "expectedDiagnosticPattern": expected_pattern,
        "expectedDiagnosticMatchCount": match_count,
        "unrelatedFailureMatched": unrelated is not None,
        "exitDisposition": disposition,
        "status": status,
    }
    return {
        **value,
        "diagnosticDispositionSha256": canonical_sha256(value),
    }


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scala-root", type=Path, required=True)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--compiler-profiles", type=Path, required=True)
    parser.add_argument("--toolchain-lock", type=Path, required=True)
    parser.add_argument("--scala-cli", type=Path, required=True)
    parser.add_argument("--profile", choices=("A", "B", "C"), required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()
    try:
        scala_root = arguments.scala_root.resolve(strict=True)
        scala_cli = arguments.scala_cli.resolve(strict=True)
        if (
            arguments.output_dir.exists()
            or not scala_cli.is_file()
            or scala_cli.is_symlink()
        ):
            raise CompilerProfileError("UNSAFE_COMPILER_PROFILE_PATH")
        policy = strict_json(arguments.policy)
        manifest = strict_json(arguments.manifest)
        profiles = strict_json(arguments.compiler_profiles)
        if (
            profiles.get("schemaVersion")
            != "s1.4x-scala-compiler-profiles-v1"
            or profiles.get("scalaVersion") != "3.8.4"
            or profiles.get("jdkRelease") != "25"
            or list(profiles.get("profiles", {})) != ["A", "B", "C"]
        ):
            raise CompilerProfileError("COMPILER_PROFILE_CONTRACT_INVALID")
        if profiles["profiles"] != {
            "A": {
                "profileName": "baseline",
                "additionalOptions": [],
                "scalaCliArguments": [],
            },
            "B": {
                "profileName": "opt",
                "additionalOptions": ["-opt"],
                "scalaCliArguments": ["--scalac-option=-opt"],
            },
            "C": {
                "profileName": "opt-own-source-inline",
                "additionalOptions": [
                    "-opt",
                    "-opt-inline:ai.trading.coach.s14x.**",
                ],
                "scalaCliArguments": [
                    "--scalac-option=-opt",
                    (
                        "--scalac-option="
                        "-opt-inline:ai.trading.coach.s14x.**"
                    ),
                ],
            },
        }:
            raise CompilerProfileError("COMPILER_PROFILE_OPTION_MAPPING_INVALID")
        option_groups = profiles.get("baseOptionGroups")
        if (
            not isinstance(option_groups, list)
            or len(option_groups) != 20
            or any(
                not isinstance(group, list)
                or not group
                or any(not isinstance(option, str) for option in group)
                for group in option_groups
            )
        ):
            raise CompilerProfileError("BASE_COMPILER_OPTIONS_INVALID")
        flattened_options = [option for group in option_groups for option in group]
        if project_directive_options(scala_root / "project.scala") != flattened_options:
            raise CompilerProfileError("PROJECT_COMPILER_DIRECTIVE_DRIFT")

        sources = validated_source_files(
            scala_root,
            manifest,
            policy=policy,
            require_git_source_equality=True,
        )
        relative_sources = [
            path.relative_to(scala_root).as_posix() for path in sources
        ]
        output_dir = arguments.output_dir
        (output_dir / "logs").mkdir(parents=True)
        environment = os.environ.copy()
        environment["NO_COLOR"] = "1"

        positive = []
        clean = scala_root / "tools/fixtures/compiler-clean.scala"
        for index, group in enumerate(option_groups, start=1):
            result = run_process(
                command=compiler_arguments(
                    scala_cli,
                    clean,
                    scala_version=profiles["scalaVersion"],
                    option_groups=[group],
                ),
                process_id=f"positive-{index:02d}",
                output_dir=output_dir,
                scala_root=scala_root,
                scala_cli=scala_cli,
                environment=environment,
            )
            result["optionGroup"] = group
            result["status"] = "PASS" if result["exitCode"] == 0 else "FAIL"
            positive.append(result)

        negative = []
        for index, fixture in enumerate(
            profiles.get("warningNegativeFixtures", []),
            start=1,
        ):
            process_id = f"negative-{index:02d}-{fixture['fixtureId']}"
            fixture_path = scala_root / fixture["path"]
            required = fixture.get("requiredOptions")
            expected_exit_code = fixture.get("expectedExitCode")
            expected_pattern = fixture.get("expectedDiagnosticPattern")
            forbidden_pattern = fixture.get("forbiddenDiagnosticPattern")
            if (
                not fixture_path.is_file()
                or not isinstance(required, list)
                or required[-1:] != ["-Werror"]
                or expected_exit_code != 1
                or not isinstance(expected_pattern, str)
                or not expected_pattern
                or not isinstance(forbidden_pattern, str)
                or not forbidden_pattern
            ):
                raise CompilerProfileError("WARNING_NEGATIVE_FIXTURE_INVALID")
            result = run_process(
                command=compiler_arguments(
                    scala_cli,
                    fixture_path,
                    scala_version=profiles["scalaVersion"],
                    option_groups=[[option] for option in required],
                ),
                process_id=process_id,
                output_dir=output_dir,
                scala_root=scala_root,
                scala_cli=scala_cli,
                environment=environment,
            )
            result["fixtureId"] = fixture["fixtureId"]
            result["requiredOptions"] = required
            disposition = diagnostic_disposition(
                (output_dir / "logs" / f"{process_id}.stderr").read_bytes(),
                expected_pattern=expected_pattern,
                forbidden_pattern=forbidden_pattern,
            )
            result["diagnosticDisposition"] = disposition
            result["status"] = (
                "PASS"
                if result["exitCode"] == expected_exit_code
                and disposition["status"] == "PASS"
                else "FAIL"
            )
            negative.append(result)

        profile = profiles["profiles"][arguments.profile]
        full_command = project_compiler_arguments(
            scala_cli,
            sources,
            profile_arguments=profile["scalaCliArguments"],
            test_dependencies=project_test_dependencies(
                scala_root / "project.scala"
            ),
        )
        full_compile = run_process(
            command=full_command,
            process_id=f"profile-{arguments.profile}-full-compile",
            output_dir=output_dir,
            scala_root=scala_root,
            scala_cli=scala_cli,
            environment=environment,
        )
        full_compile["status"] = (
            "PASS" if full_compile["exitCode"] == 0 else "FAIL"
        )
        diagnostic_only = []
        for index, diagnostic_config in enumerate(
            profiles.get("diagnosticOnlyOptions", []),
            start=1,
        ):
            if (
                not isinstance(diagnostic_config, dict)
                or set(diagnostic_config)
                != {"option", "expectedDiagnosticPattern"}
                or not isinstance(diagnostic_config.get("option"), str)
                or not isinstance(
                    diagnostic_config.get("expectedDiagnosticPattern"),
                    str,
                )
            ):
                raise CompilerProfileError("DIAGNOSTIC_ONLY_CONFIG_INVALID")
            diagnostic_option = diagnostic_config["option"]
            diagnostic = run_process(
                command=[
                    *full_command,
                    f"--scalac-option={diagnostic_option}",
                ],
                process_id=f"profile-{arguments.profile}-diagnostic-{index:02d}",
                output_dir=output_dir,
                scala_root=scala_root,
                scala_cli=scala_cli,
                environment=environment,
            )
            diagnostic["option"] = diagnostic_option
            diagnostic["disposition"] = "RECORDED_NON_SCORING"
            diagnostic["diagnosticDisposition"] = (
                diagnostic_probe_disposition(
                    (output_dir / "logs" / (
                        f"profile-{arguments.profile}-diagnostic-{index:02d}.stdout"
                    )).read_bytes(),
                    (output_dir / "logs" / (
                        f"profile-{arguments.profile}-diagnostic-{index:02d}.stderr"
                    )).read_bytes(),
                    option=diagnostic_option,
                    expected_pattern=diagnostic_config[
                        "expectedDiagnosticPattern"
                    ],
                    exit_code=diagnostic["exitCode"],
                )
            )
            diagnostic_only.append(diagnostic)

        status = (
            "PASS"
            if all(item["status"] == "PASS" for item in positive)
            and all(item["status"] == "PASS" for item in negative)
            and full_compile["status"] == "PASS"
            else "FAIL"
        )
        result = {
            "schemaVersion": "s1.4x-scala-hard-compiler-result-v1",
            "profileId": arguments.profile,
            "scalaVersion": profiles["scalaVersion"],
            "jdkRelease": profiles["jdkRelease"],
            "toolPathId": "SCALA_CLI_1_15_0",
            "resolvedBinarySha256": sha256_file(scala_cli),
            "toolchainLockSha256": sha256_file(arguments.toolchain_lock),
            "compilerProfilesSha256": sha256_file(arguments.compiler_profiles),
            "profileOptionsSha256": canonical_sha256(
                profile["additionalOptions"]
            ),
            "sourceInputManifestSha256": sha256_file(arguments.manifest),
            "compileInputPaths": relative_sources,
            "positiveFlags": positive,
            "negativeWarnings": negative,
            "fullCompile": full_compile,
            "diagnosticOnly": diagnostic_only,
            "aggregateStatus": status,
        }
        write_exclusive_json(
            output_dir / "scala-hard-compiler-result.v1.json",
            result,
        )
    except (OSError, UnicodeError, ValueError, CompilerProfileError) as error:
        print(f"SCALA_COMPILER_PROFILE_FAIL:{error}", file=sys.stderr)
        return 1
    if result["aggregateStatus"] != "PASS":
        print("SCALA_COMPILER_PROFILE_FAIL:PROCESS_MATRIX", file=sys.stderr)
        return 1
    print(
        "SCALA_COMPILER_PROFILE_PASS "
        f"profile={arguments.profile} positiveFlags={len(positive)} "
        f"negativeWarnings={len(negative)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
