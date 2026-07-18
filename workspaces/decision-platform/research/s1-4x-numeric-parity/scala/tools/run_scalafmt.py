#!/usr/bin/env python3
"""Exact Scalafmt 3.11.4의 real-source check와 byte idempotence evidence를 만든다."""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from source_input_manifest import validated_source_files


class ScalafmtEvidenceError(ValueError):
    """Scalafmt evidence를 완전하게 만들 수 없음을 나타낸다."""


DOWNLOAD_LINE = re.compile(r"(?i)\b(?:downloading|downloaded)\s+https?://")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def source_tree_sha256(root: Path, sources: list[Path]) -> str:
    lines = [
        f"{sha256_file(path)}  {path.relative_to(root).as_posix()}\n"
        for path in sorted(
            sources,
            key=lambda item: item.relative_to(root).as_posix().encode("utf-8"),
        )
    ]
    return hashlib.sha256("".join(lines).encode("utf-8")).hexdigest()


def formatted_source_patch(
    *,
    scala_root: Path,
    sources: list[Path],
    temporary: Path,
    temporary_sources: list[Path],
) -> str:
    """원본과 formatted copy의 전체 diff를 repo-relative portable path로 만든다."""

    if len(sources) != len(temporary_sources):
        raise ScalafmtEvidenceError("FORMATTED_SOURCE_SET_SIZE_MISMATCH")
    pairs = sorted(
        zip(sources, temporary_sources, strict=True),
        key=lambda pair: pair[0].relative_to(scala_root).as_posix().encode("utf-8"),
    )
    output: list[str] = []
    for source, formatted in pairs:
        relative = source.relative_to(scala_root).as_posix()
        if formatted.relative_to(temporary).as_posix() != relative:
            raise ScalafmtEvidenceError("FORMATTED_SOURCE_SET_PATH_MISMATCH")
        output.extend(
            difflib.unified_diff(
                source.read_text(encoding="utf-8").splitlines(keepends=True),
                formatted.read_text(encoding="utf-8").splitlines(keepends=True),
                fromfile=f"a/{relative}",
                tofile=f"b/{relative}",
                lineterm="\n",
            )
        )
    return "".join(output)


def strict_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as stream:
        value = json.load(
            stream,
            parse_constant=lambda token: (_ for _ in ()).throw(
                ScalafmtEvidenceError(f"NONFINITE_JSON:{token}")
            ),
        )
    if not isinstance(value, dict):
        raise ScalafmtEvidenceError(f"JSON_OBJECT_REQUIRED:{path}")
    return value


def format_command(
    *,
    scala_cli: Path,
    scalafmt_launcher: Path,
    config: Path,
    sources: list[Path],
    check: bool,
) -> list[str]:
    command = [
        str(scala_cli),
        "--power",
        "format",
        *map(str, sources),
        "--server=false",
        "--scalafmt-version",
        "3.11.4",
        "--scalafmt-conf",
        str(config),
        "--scalafmt-launcher",
        str(scalafmt_launcher),
        "--offline",
    ]
    if check:
        command.append("--check")
    return command


def run_process(
    command: list[str],
    *,
    cwd: Path,
    environment: dict[str, str],
    log_root: Path,
    log_id: str,
) -> subprocess.CompletedProcess[bytes]:
    completed = subprocess.run(
        command,
        cwd=cwd,
        env=environment,
        check=False,
        capture_output=True,
    )
    (log_root / f"{log_id}.stdout").write_bytes(completed.stdout)
    (log_root / f"{log_id}.stderr").write_bytes(completed.stderr)
    return completed


def process_evidence(
    command: list[str],
    completed: subprocess.CompletedProcess[bytes],
) -> dict[str, Any]:
    combined = (completed.stdout + b"\n" + completed.stderr).decode(
        "utf-8", errors="replace"
    )
    value = {
        "commandArgvSha256": canonical_sha256(command),
        "exitCode": completed.returncode,
        "stdoutSha256": hashlib.sha256(completed.stdout).hexdigest(),
        "stderrSha256": hashlib.sha256(completed.stderr).hexdigest(),
        "downloadLineCount": sum(
            1 for line in combined.splitlines() if DOWNLOAD_LINE.search(line)
        ),
    }
    return {**value, "evidenceSha256": canonical_sha256(value)}


def git_scalafmt_configs(scala_root: Path) -> list[str]:
    completed = subprocess.run(
        [
            "git",
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
            "--",
            ".scalafmt.conf",
            "**/.scalafmt.conf",
        ],
        cwd=scala_root,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise ScalafmtEvidenceError("SCALAFMT_CONFIG_ENUMERATION_FAILED")
    return sorted(set(completed.stdout.splitlines()), key=lambda value: value.encode("utf-8"))


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--scala-root", type=Path, required=True)
    value.add_argument("--policy", type=Path, required=True)
    value.add_argument("--manifest", type=Path, required=True)
    value.add_argument("--scala-cli", type=Path, required=True)
    value.add_argument("--scalafmt-archive", type=Path, required=True)
    value.add_argument("--scalafmt-launcher", type=Path, required=True)
    value.add_argument("--toolchain-lock", type=Path, required=True)
    value.add_argument("--output-dir", type=Path, required=True)
    return value


def run(arguments: argparse.Namespace) -> Path:
    for name in (
        "scala_root",
        "policy",
        "manifest",
        "scala_cli",
        "scalafmt_archive",
        "scalafmt_launcher",
        "toolchain_lock",
    ):
        path = getattr(arguments, name)
        if not path.is_absolute() or path.is_symlink():
            raise ScalafmtEvidenceError(f"UNSAFE_INPUT_PATH:{name}")
    if (
        not arguments.scala_root.is_dir()
        or not arguments.policy.is_file()
        or not arguments.manifest.is_file()
        or not arguments.scala_cli.is_file()
        or not arguments.scalafmt_archive.is_file()
        or not arguments.scalafmt_launcher.is_file()
        or not os.access(arguments.scalafmt_launcher, os.X_OK)
        or not arguments.toolchain_lock.is_file()
    ):
        raise ScalafmtEvidenceError("INPUT_PATH_MISSING")
    if (
        not arguments.output_dir.is_absolute()
        or arguments.output_dir.exists()
        or arguments.output_dir.is_symlink()
    ):
        raise ScalafmtEvidenceError("OUTPUT_DIRECTORY_MUST_BE_NEW")

    scala_root = arguments.scala_root.resolve(strict=True)
    policy = strict_json(arguments.policy)
    manifest = strict_json(arguments.manifest)
    toolchain_lock = strict_json(arguments.toolchain_lock)
    scalafmt_lock = toolchain_lock.get("scalafmt")
    if not isinstance(scalafmt_lock, dict):
        raise ScalafmtEvidenceError("SCALAFMT_ARTIFACT_LOCK_MISSING")
    expected_artifact = {
        "archiveUri": (
            "https://github.com/scalameta/scalafmt/releases/download/"
            "v3.11.4/scalafmt-x86_64-pc-linux.zip"
        ),
        "archivePathId": (
            "S1_4X_CACHE_ROOT/coursier/https/github.com/scalameta/scalafmt/"
            "releases/download/v3.11.4/scalafmt-x86_64-pc-linux.zip"
        ),
        "archiveSha256": (
            "e7d43a5621074a63a46d5b287d0b0bb0650033deeb836af2b27515b2127476f2"
        ),
        "executablePathId": (
            "COURSIER_ARCHIVE_CACHE/https/github.com/scalameta/scalafmt/"
            "releases/download/v3.11.4/"
            "scalafmt-x86_64-pc-linux.zip/scalafmt"
        ),
        "executableSha256": (
            "88526f9f4d64c2fb023d54578812419f49e2ec09e30e4fb77443a05f1a59cac0"
        ),
        "resolvedVersionOutput": "scalafmt 3.11.4",
        "resolutionLogUri": (
            "evidence://s1-4x-scala-scalafmt-evidence-9c3cb8f-01/"
            "logs/first-apply.stderr"
        ),
        "resolutionLogSha256": (
            "1cc7516d57c230f10242f43884f12f3d26cbd6d681dbaed317262148c136b781"
        ),
        "networkPolicy": "OFFLINE_PINNED_LAUNCHER",
    }
    if any(scalafmt_lock.get(key) != value for key, value in expected_artifact.items()):
        raise ScalafmtEvidenceError("SCALAFMT_ARTIFACT_LOCK_MISMATCH")
    if (
        sha256_file(arguments.scalafmt_archive)
        != expected_artifact["archiveSha256"]
        or sha256_file(arguments.scalafmt_launcher)
        != expected_artifact["executableSha256"]
    ):
        raise ScalafmtEvidenceError("SCALAFMT_RESOLVED_ARTIFACT_SHA_MISMATCH")
    version = subprocess.run(
        [str(arguments.scalafmt_launcher), "--version"],
        check=False,
        capture_output=True,
        text=True,
    )
    resolved_version_output = (version.stdout + version.stderr).strip()
    if version.returncode != 0 or resolved_version_output != "scalafmt 3.11.4":
        raise ScalafmtEvidenceError("SCALAFMT_RESOLVED_VERSION_MISMATCH")
    sources = validated_source_files(
        scala_root,
        manifest,
        policy=policy,
        require_git_source_equality=True,
    )
    config = scala_root / ".scalafmt.conf"
    if (
        config.is_symlink()
        or not config.is_file()
        or git_scalafmt_configs(scala_root) != [".scalafmt.conf"]
    ):
        raise ScalafmtEvidenceError("SCALAFMT_CONFIG_CLOSURE_MISMATCH")
    config_text = config.read_text(encoding="utf-8")
    if (
        "version = 3.11.4\n" not in config_text
        or "runner.dialect = scala3\n" not in config_text
        or "lineEndings = unix\n" not in config_text
    ):
        raise ScalafmtEvidenceError("SCALAFMT_CONFIG_INVALID")

    output_root = arguments.output_dir
    output_root.mkdir()
    log_root = output_root / "logs"
    log_root.mkdir()
    environment = os.environ.copy()
    environment["NO_COLOR"] = "1"
    source_before = source_tree_sha256(scala_root, sources)

    temporary = Path(tempfile.mkdtemp(prefix="s1-4x-scalafmt."))
    if temporary.parent != Path("/tmp") and not str(temporary).startswith(
        str(Path(os.environ.get("TMPDIR", "/tmp")).resolve()) + os.sep
    ):
        raise ScalafmtEvidenceError("UNSAFE_TEMPORARY_DIRECTORY")
    try:
        temporary_config = temporary / ".scalafmt.conf"
        shutil.copy2(config, temporary_config)
        temporary_sources: list[Path] = []
        for source in sources:
            destination = temporary / source.relative_to(scala_root)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            temporary_sources.append(destination)

        first_command = format_command(
            scala_cli=arguments.scala_cli,
            scalafmt_launcher=arguments.scalafmt_launcher,
            config=temporary_config,
            sources=temporary_sources,
            check=False,
        )
        first = run_process(
            first_command,
            cwd=temporary,
            environment=environment,
            log_root=log_root,
            log_id="first-apply",
        )
        if first.returncode != 0:
            raise ScalafmtEvidenceError("FIRST_SCALAFMT_APPLY_FAILED")
        if process_evidence(first_command, first)["downloadLineCount"] != 0:
            raise ScalafmtEvidenceError("FIRST_SCALAFMT_NETWORK_ACCESS_DETECTED")
        first_hash = source_tree_sha256(temporary, temporary_sources)

        second_command = format_command(
            scala_cli=arguments.scala_cli,
            scalafmt_launcher=arguments.scalafmt_launcher,
            config=temporary_config,
            sources=temporary_sources,
            check=False,
        )
        second = run_process(
            second_command,
            cwd=temporary,
            environment=environment,
            log_root=log_root,
            log_id="second-apply",
        )
        if second.returncode != 0:
            raise ScalafmtEvidenceError("SECOND_SCALAFMT_APPLY_FAILED")
        if process_evidence(second_command, second)["downloadLineCount"] != 0:
            raise ScalafmtEvidenceError("SECOND_SCALAFMT_NETWORK_ACCESS_DETECTED")
        second_hash = source_tree_sha256(temporary, temporary_sources)
        if first_hash != second_hash:
            raise ScalafmtEvidenceError("SCALAFMT_NOT_BYTE_IDEMPOTENT")

        formatted_patch = formatted_source_patch(
            scala_root=scala_root,
            sources=sources,
            temporary=temporary,
            temporary_sources=temporary_sources,
        )
        formatted_patch_path = output_root / "formatted-source.patch"
        with formatted_patch_path.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(formatted_patch)
        formatted_patch_sha256 = sha256_file(formatted_patch_path)
        if source_tree_sha256(scala_root, sources) != source_before:
            raise ScalafmtEvidenceError("REAL_SOURCE_MUTATED_WHILE_BUILDING_PATCH")

        copied_check_command = format_command(
            scala_cli=arguments.scala_cli,
            scalafmt_launcher=arguments.scalafmt_launcher,
            config=temporary_config,
            sources=temporary_sources,
            check=True,
        )
        copied_check = run_process(
            copied_check_command,
            cwd=temporary,
            environment=environment,
            log_root=log_root,
            log_id="copied-non-mutating-check",
        )
        if copied_check.returncode != 0:
            raise ScalafmtEvidenceError("FORMATTED_COPY_CHECK_FAILED")
        if process_evidence(copied_check_command, copied_check)["downloadLineCount"] != 0:
            raise ScalafmtEvidenceError("COPIED_CHECK_NETWORK_ACCESS_DETECTED")

        real_check_command = format_command(
            scala_cli=arguments.scala_cli,
            scalafmt_launcher=arguments.scalafmt_launcher,
            config=config,
            sources=sources,
            check=True,
        )
        real_check = run_process(
            real_check_command,
            cwd=scala_root,
            environment=environment,
            log_root=log_root,
            log_id="real-source-non-mutating-check",
        )
        if real_check.returncode != 0:
            raise ScalafmtEvidenceError("REAL_SOURCE_NOT_FORMATTED")
        if process_evidence(real_check_command, real_check)["downloadLineCount"] != 0:
            raise ScalafmtEvidenceError("REAL_CHECK_NETWORK_ACCESS_DETECTED")
        if source_tree_sha256(scala_root, sources) != source_before:
            raise ScalafmtEvidenceError("REAL_SOURCE_MUTATED_BY_CHECK")

        negative = temporary / "Misformatted.scala"
        negative.write_text(
            "package s1_4x.scalafmt_negative\nobject Misformatted{def value:Int=1}\n",
            encoding="utf-8",
            newline="\n",
        )
        negative_command = format_command(
            scala_cli=arguments.scala_cli,
            scalafmt_launcher=arguments.scalafmt_launcher,
            config=temporary_config,
            sources=[negative],
            check=True,
        )
        negative_result = run_process(
            negative_command,
            cwd=temporary,
            environment=environment,
            log_root=log_root,
            log_id="misformatted-negative",
        )
        if negative_result.returncode == 0:
            raise ScalafmtEvidenceError("MISFORMATTED_NEGATIVE_UNEXPECTEDLY_PASSED")
        if process_evidence(negative_command, negative_result)["downloadLineCount"] != 0:
            raise ScalafmtEvidenceError("NEGATIVE_CHECK_NETWORK_ACCESS_DETECTED")

        result = {
            "schemaVersion": "s1.4x-scala-scalafmt-idempotence-result-v1",
            "scalafmtVersion": "3.11.4",
            "scalafmtArtifact": {
                **expected_artifact,
                "resolvedVersionOutput": resolved_version_output,
                "versionOutputSha256": hashlib.sha256(
                    resolved_version_output.encode("utf-8")
                ).hexdigest(),
            },
            "networkPolicy": "OFFLINE_PINNED_LAUNCHER",
            "configPath": ".scalafmt.conf",
            "configSha256": sha256_file(config),
            "sourceInputManifestSha256": sha256_file(arguments.manifest),
            "toolchainLockSha256": sha256_file(arguments.toolchain_lock),
            "checkedFiles": [
                path.relative_to(scala_root).as_posix() for path in sources
            ],
            "sourceBeforeSha256": source_before,
            "firstPassSourceSha256": first_hash,
            "secondPassSourceSha256": second_hash,
            "formattedSourcePatchSha256": formatted_patch_sha256,
            "firstApply": process_evidence(first_command, first),
            "secondApply": process_evidence(second_command, second),
            "copiedNonMutatingCheck": process_evidence(
                copied_check_command,
                copied_check,
            ),
            "nonMutatingCheck": process_evidence(real_check_command, real_check),
            "misformattedNegative": process_evidence(
                negative_command,
                negative_result,
            ),
            "status": "PASS",
        }
        result_path = output_root / "scala-scalafmt-idempotence-result.v1.json"
        with result_path.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(
                result,
                stream,
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            stream.write("\n")
        return result_path
    finally:
        if temporary.is_dir():
            shutil.rmtree(temporary)


def main() -> int:
    arguments = parser().parse_args()
    try:
        result = run(arguments)
        print(f"SCALA_SCALAFMT_IDEMPOTENCE_PASS result={result}")
    except (OSError, ScalafmtEvidenceError, UnicodeError, ValueError) as error:
        print(f"SCALA_SCALAFMT_IDEMPOTENCE_FAIL:{error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
