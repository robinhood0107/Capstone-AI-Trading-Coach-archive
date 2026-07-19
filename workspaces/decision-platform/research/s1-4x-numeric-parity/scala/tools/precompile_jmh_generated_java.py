#!/usr/bin/env python3
"""Scala CLI serverless JMH generated Java를 pinned javac로 사전 컴파일한다."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import source_input_manifest


class PrecompileError(RuntimeError):
    """Generated Java 또는 pinned compiler 폐쇄성이 유효하지 않음."""


SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
PROC_FD_PATTERN = re.compile(
    r"^/proc/(?P<pid>self|[1-9][0-9]*)/fd/(?P<fd>[0-9]+)$"
)
BENCHMARK_TYPES = (
    ("classical_path_risk", "ClassicalPathRiskBenchmark"),
    ("coverage_batch", "CoverageBatchBenchmark"),
    ("intraday_realized", "IntradayRealizedBenchmark"),
    ("path_transform", "PathTransformBenchmark"),
    ("probabilistic_scalar", "ProbabilisticScalarBenchmark"),
    ("serial_sharpe", "SerialSharpeBenchmark"),
)
GENERATED_SUFFIXES = (
    "_benchmark_jmhTest.java",
    "_jmhType.java",
    "_jmhType_B1.java",
    "_jmhType_B2.java",
    "_jmhType_B3.java",
)
RECEIPT_NAME = "scala-jmh-generated-java-precompile.v1.json"
GENERATED_CLASSES_NAME = "generated-java-classes"
SCALA_COMPILE_STDOUT = "scala-jmh-precompile.stdout"
SCALA_COMPILE_STDERR = "scala-jmh-precompile.stderr"
JAVAC_STDOUT = "scala-javac.stdout"
JAVAC_STDERR = "scala-javac.stderr"


@dataclass(frozen=True)
class FileDigest:
    """폐쇄성 검증에 사용하는 상대 경로와 SHA-256."""

    relative_path: str
    sha256: str


@dataclass(frozen=True)
class GeneratedSourceClosure:
    """Scala CLI가 생성한 정확한 Java source root와 파일 집합."""

    root: Path
    files: tuple[FileDigest, ...]


@dataclass(frozen=True)
class ClasspathEntry:
    """Printed classpath 한 항목의 portable identity."""

    path: Path
    path_id: str
    kind: str
    sha256: str


@dataclass(frozen=True)
class ClasspathClosure:
    """Printed classpath 순서와 Scala CLI JMH class output."""

    entries: tuple[ClasspathEntry, ...]
    class_output: Path
    processed_class_count: int
    generator_class_input: Path
    generated_source_root: Path
    generated_resource_root: Path
    generator_class_input_sha256: str
    generated_resource_root_sha256: str


@dataclass(frozen=True)
class GeneratorOutputClosure:
    """Scala CLI reflection generator stdout가 가리키는 입력·출력 폐쇄성."""

    processed_class_count: int
    generator_class_input: Path
    generated_source_root: Path
    generated_resource_root: Path
    generator_class_input_sha256: str
    generated_resource_root_sha256: str


def sha256_file(path: Path) -> str:
    """Regular non-symlink file bytes를 SHA-256으로 고정한다."""

    if path.is_symlink() or not path.is_file():
        raise PrecompileError(f"UNSAFE_OR_MISSING_FILE:{path}")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_sha256(value: Any) -> str:
    """JSON evidence value를 canonical UTF-8 bytes로 해시한다."""

    try:
        payload = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise PrecompileError("NON_CANONICAL_HASH_INPUT") from error
    return hashlib.sha256(payload).hexdigest()


def expected_generated_source_paths() -> tuple[str, ...]:
    """동결된 6개 benchmark가 생성해야 하는 30개 Java 상대 경로를 반환한다."""

    values = [
        (
            f"s1_4x/benchmarks/{family}/jmh_generated/"
            f"{benchmark}{suffix}"
        )
        for family, benchmark in BENCHMARK_TYPES
        for suffix in GENERATED_SUFFIXES
    ]
    return tuple(sorted(values, key=lambda value: value.encode("utf-8")))


def _require_absolute_directory(path: Path, *, label: str) -> Path:
    if (
        not path.is_absolute()
        or path.is_symlink()
        or not path.is_dir()
        or path.resolve(strict=True) != path
    ):
        raise PrecompileError(f"{label}_INVALID")
    return path


def _require_absolute_regular(path: Path, *, label: str) -> Path:
    if (
        not path.is_absolute()
        or path.is_symlink()
        or not path.is_file()
        or path.resolve(strict=True) != path
    ):
        raise PrecompileError(f"{label}_INVALID")
    return path


def _path_has_symlink(root: Path, path: Path) -> bool:
    relative = path.relative_to(root)
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            return True
    return False


def _directory_files(root: Path) -> tuple[FileDigest, ...]:
    values: list[FileDigest] = []
    for path in root.rglob("*"):
        if _path_has_symlink(root, path):
            raise PrecompileError(f"SYMLINK_IN_CLOSURE:{path}")
        if path.is_dir():
            continue
        if not path.is_file():
            raise PrecompileError(f"NON_REGULAR_IN_CLOSURE:{path}")
        values.append(
            FileDigest(
                relative_path=path.relative_to(root).as_posix(),
                sha256=sha256_file(path),
            )
        )
    return tuple(
        sorted(values, key=lambda item: item.relative_path.encode("utf-8"))
    )


def _file_digest_values(
    values: Sequence[FileDigest],
) -> list[dict[str, str]]:
    return [
        {"path": item.relative_path, "sha256": item.sha256}
        for item in values
    ]


def generated_source_closure(workspace: Path) -> GeneratedSourceClosure:
    """Workspace의 generated-Java root와 30-file exact set을 검증한다."""

    workspace = _require_absolute_directory(workspace, label="SCALA_WORKSPACE")
    build_root = _require_absolute_directory(
        workspace / ".scala-build",
        label="SCALA_BUILD_ROOT",
    )
    roots = sorted(
        (
            item / "sources"
            for item in build_root.iterdir()
            if item.is_dir()
            and not item.is_symlink()
            and item.name.endswith("_jmh")
            and (item / "sources").is_dir()
        ),
        key=lambda item: item.as_posix().encode("utf-8"),
    )
    if len(roots) != 1:
        raise PrecompileError("GENERATED_SOURCE_ROOT_CLOSURE_MISMATCH")
    return generated_source_closure_at(roots[0], workspace=workspace)


def generated_source_closure_at(
    root: Path,
    *,
    workspace: Path,
) -> GeneratedSourceClosure:
    """Receipt가 지목한 한 generated-Java root의 exact set을 검증한다."""

    workspace = _require_absolute_directory(workspace, label="SCALA_WORKSPACE")
    root = _require_absolute_directory(
        root,
        label="GENERATED_SOURCE_ROOT",
    )
    if (
        not root.is_relative_to(workspace / ".scala-build")
        or root.name != "sources"
        or not root.parent.name.endswith("_jmh")
    ):
        raise PrecompileError("GENERATED_SOURCE_ROOT_CLOSURE_MISMATCH")
    files = _directory_files(root)
    actual = tuple(item.relative_path for item in files)
    if actual != expected_generated_source_paths():
        raise PrecompileError("GENERATED_SOURCE_CLOSURE_MISMATCH")
    return GeneratedSourceClosure(root=root, files=files)


def _portable_path_id(
    path: Path,
    *,
    workspace: Path,
    coursier_cache: Path,
    evidence_dir: Path,
) -> str:
    if path == evidence_dir / GENERATED_CLASSES_NAME:
        return f"EVIDENCE_ROOT/{GENERATED_CLASSES_NAME}"
    if path.is_relative_to(workspace):
        return f"SCALA_WORKSPACE/{path.relative_to(workspace).as_posix()}"
    if path.is_relative_to(coursier_cache):
        return f"COURSIER_CACHE/{path.relative_to(coursier_cache).as_posix()}"
    raise PrecompileError(f"CLASSPATH_ENTRY_OUTSIDE_SEALED_ROOTS:{path}")


def _entry_digest(path: Path) -> tuple[str, str]:
    if path.is_file() and not path.is_symlink():
        return "file", sha256_file(path)
    if path.is_dir() and not path.is_symlink():
        files = _file_digest_values(_directory_files(path))
        return "directory", canonical_sha256(files)
    raise PrecompileError(f"CLASSPATH_ENTRY_INVALID:{path}")


def generator_output_closure(
    raw: str,
    *,
    workspace: Path,
) -> GeneratorOutputClosure:
    """Scala CLI reflection generator의 선두 두 stdout 행을 exact 검증한다."""

    workspace = _require_absolute_directory(workspace, label="SCALA_WORKSPACE")
    if "\x00" in raw or "\r" in raw:
        raise PrecompileError("JMH_GENERATOR_STDOUT_INVALID")
    lines = raw.splitlines()
    if len(lines) < 2 or any(not line for line in lines[:2]):
        raise PrecompileError("JMH_GENERATOR_STDOUT_INVALID")
    processing = re.fullmatch(
        r'Processing ([1-9][0-9]*) classes from (.+) '
        r'with "reflection" generator',
        lines[0],
    )
    generated = re.fullmatch(
        r"Writing out Java source to (.+) and resources to (.+)",
        lines[1],
    )
    if processing is None or generated is None:
        raise PrecompileError("JMH_GENERATOR_STDOUT_INVALID")
    processed_class_count = int(processing.group(1))
    generator_class_input = Path(processing.group(2))
    generated_source_root = Path(generated.group(1))
    generated_resource_root = Path(generated.group(2))
    expected_build_root = workspace / ".scala-build"
    if (
        processed_class_count != 147
        or not generator_class_input.is_absolute()
        or not generated_source_root.is_absolute()
        or not generated_resource_root.is_absolute()
        or generator_class_input.is_symlink()
        or generated_source_root.is_symlink()
        or generated_resource_root.is_symlink()
        or not generator_class_input.is_dir()
        or not generated_source_root.is_dir()
        or not generated_resource_root.is_dir()
        or generator_class_input.resolve(strict=True)
        != generator_class_input
        or generated_source_root.resolve(strict=True)
        != generated_source_root
        or generated_resource_root.resolve(strict=True)
        != generated_resource_root
        or not generator_class_input.is_relative_to(expected_build_root)
        or not generated_source_root.is_relative_to(expected_build_root)
        or not generated_resource_root.is_relative_to(expected_build_root)
        or generator_class_input.parts[-2:] != ("classes", "main")
    ):
        raise PrecompileError("JMH_GENERATOR_STDOUT_INVALID")
    generator_build = generator_class_input.parents[1]
    expected_generated_build = generator_build.with_name(
        f"{generator_build.name}_jmh"
    )
    if (
        generated_source_root != expected_generated_build / "sources"
        or generated_resource_root != expected_generated_build / "resources"
    ):
        raise PrecompileError("JMH_GENERATOR_STDOUT_INVALID")
    class_input_values = _file_digest_values(
        _directory_files(generator_class_input)
    )
    resource_values = _file_digest_values(
        _directory_files(generated_resource_root)
    )
    return GeneratorOutputClosure(
        processed_class_count=processed_class_count,
        generator_class_input=generator_class_input,
        generated_source_root=generated_source_root,
        generated_resource_root=generated_resource_root,
        generator_class_input_sha256=canonical_sha256(class_input_values),
        generated_resource_root_sha256=canonical_sha256(resource_values),
    )


def classpath_closure(
    raw: str,
    *,
    workspace: Path,
    coursier_cache: Path,
    evidence_dir: Path,
) -> ClasspathClosure:
    """Scala CLI generator 2행과 printed classpath의 exact closure를 고정한다."""

    workspace = _require_absolute_directory(workspace, label="SCALA_WORKSPACE")
    coursier_cache = _require_absolute_directory(
        coursier_cache,
        label="COURSIER_CACHE",
    )
    evidence_dir = _require_absolute_directory(
        evidence_dir,
        label="EVIDENCE_ROOT",
    )
    lines = raw.splitlines()
    if len(lines) != 3:
        raise PrecompileError("JMH_GENERATOR_STDOUT_INVALID")
    generator = generator_output_closure(raw, workspace=workspace)

    raw_paths = lines[2].split(os.pathsep)
    if not raw_paths or any(not item for item in raw_paths):
        raise PrecompileError("PRINTED_CLASSPATH_ENTRY_INVALID")

    entries: list[ClasspathEntry] = []
    seen: set[Path] = set()
    class_outputs: list[Path] = []
    for raw_path in raw_paths:
        path = Path(raw_path)
        if not path.is_absolute() or path.resolve(strict=True) != path:
            raise PrecompileError(f"CLASSPATH_ENTRY_INVALID:{path}")
        if path in seen:
            raise PrecompileError("PRINTED_CLASSPATH_DUPLICATE")
        seen.add(path)
        path_id = _portable_path_id(
            path,
            workspace=workspace,
            coursier_cache=coursier_cache,
            evidence_dir=evidence_dir,
        )
        kind, digest = _entry_digest(path)
        entries.append(
            ClasspathEntry(
                path=path,
                path_id=path_id,
                kind=kind,
                sha256=digest,
            )
        )
        if (
            kind == "directory"
            and path.is_relative_to(workspace / ".scala-build")
            and path.parts[-2:] == ("classes", "main")
            and (path / "META-INF/BenchmarkList").is_file()
            and not (path / "META-INF/BenchmarkList").is_symlink()
        ):
            class_outputs.append(path)
    if len(class_outputs) != 1:
        raise PrecompileError("JMH_CLASS_OUTPUT_CLOSURE_MISMATCH")
    generated_build = generator.generated_source_root.parent
    class_output_build = class_outputs[0].parents[1]
    external_classes = evidence_dir / GENERATED_CLASSES_NAME
    if (
        re.fullmatch(
            rf"{re.escape(generated_build.name)}_[0-9a-f]{{10}}",
            class_output_build.name,
        )
        is None
        or class_output_build.parent != generated_build.parent
        or sum(
            item.path == generator.generated_resource_root
            for item in entries
        )
        != 1
        or sum(item.path == external_classes for item in entries) != 1
    ):
        raise PrecompileError("JMH_CLASSPATH_IDENTITY_MISMATCH")
    return ClasspathClosure(
        entries=tuple(entries),
        class_output=class_outputs[0],
        processed_class_count=generator.processed_class_count,
        generator_class_input=generator.generator_class_input,
        generated_source_root=generator.generated_source_root,
        generated_resource_root=generator.generated_resource_root,
        generator_class_input_sha256=(
            generator.generator_class_input_sha256
        ),
        generated_resource_root_sha256=(
            generator.generated_resource_root_sha256
        ),
    )


def _path_from_id(
    path_id: str,
    *,
    workspace: Path,
    coursier_cache: Path,
    evidence_dir: Path,
) -> Path:
    roots = (
        ("SCALA_WORKSPACE/", workspace),
        ("COURSIER_CACHE/", coursier_cache),
        ("EVIDENCE_ROOT/", evidence_dir),
    )
    for prefix, root in roots:
        if path_id.startswith(prefix):
            relative = Path(path_id.removeprefix(prefix))
            if relative.is_absolute() or ".." in relative.parts:
                raise PrecompileError("CLASSPATH_POST_RUN_DRIFT")
            path = root / relative
            if (
                prefix == "EVIDENCE_ROOT/"
                and relative != Path(GENERATED_CLASSES_NAME)
            ):
                raise PrecompileError("CLASSPATH_POST_RUN_DRIFT")
            if (
                not path.exists()
                or path.resolve(strict=True) != path
                or not path.is_relative_to(root)
            ):
                raise PrecompileError("CLASSPATH_POST_RUN_DRIFT")
            return path
    raise PrecompileError("CLASSPATH_POST_RUN_DRIFT")


def verify_classpath_entries(
    values: Any,
    *,
    workspace: Path,
    coursier_cache: Path,
    evidence_dir: Path,
) -> None:
    """Receipt의 Scala class output·dependency JAR bytes를 post-run 재검증한다."""

    workspace = _require_absolute_directory(workspace, label="SCALA_WORKSPACE")
    coursier_cache = _require_absolute_directory(
        coursier_cache,
        label="COURSIER_CACHE",
    )
    evidence_dir = _require_absolute_directory(
        evidence_dir,
        label="EVIDENCE_ROOT",
    )
    if not isinstance(values, list) or not values:
        raise PrecompileError("CLASSPATH_POST_RUN_DRIFT")
    seen: set[str] = set()
    for item in values:
        if (
            not isinstance(item, dict)
            or set(item) != {"pathId", "kind", "sha256"}
            or item.get("kind") not in {"file", "directory"}
            or not isinstance(item.get("pathId"), str)
            or item["pathId"] in seen
        ):
            raise PrecompileError("CLASSPATH_POST_RUN_DRIFT")
        seen.add(item["pathId"])
        path = _path_from_id(
            item["pathId"],
            workspace=workspace,
            coursier_cache=coursier_cache,
            evidence_dir=evidence_dir,
        )
        kind, digest = _entry_digest(path)
        if kind != item["kind"] or digest != item.get("sha256"):
            raise PrecompileError("CLASSPATH_POST_RUN_DRIFT")


def _strict_json(path: Path) -> dict[str, Any]:
    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise PrecompileError(f"DUPLICATE_JSON_KEY:{key}")
            value[key] = item
        return value

    parsed = json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=unique_object,
        parse_constant=lambda value: (_ for _ in ()).throw(
            PrecompileError(f"NONFINITE_JSON:{value}")
        ),
    )
    if not isinstance(parsed, dict):
        raise PrecompileError(f"JSON_OBJECT_REQUIRED:{path}")
    return parsed


def _normalized_exec_path(path: Path) -> Path:
    match = PROC_FD_PATTERN.fullmatch(str(path))
    if match is not None and match.group("pid") == "self":
        return Path(f"/proc/{os.getpid()}/fd/{match.group('fd')}")
    return path


def _verified_executable(
    *,
    binary: Path,
    execution_path: Path,
    expected_sha256: str,
    label: str,
) -> tuple[Path, str]:
    binary = _require_absolute_regular(binary, label=f"{label}_BINARY")
    if (
        not os.access(binary, os.X_OK)
        or SHA256_PATTERN.fullmatch(expected_sha256) is None
        or sha256_file(binary) != expected_sha256
    ):
        raise PrecompileError(f"{label}_BINARY_IDENTITY_MISMATCH")
    execution_path = _normalized_exec_path(execution_path)
    if (
        not execution_path.is_absolute()
        or not execution_path.is_file()
        or not os.access(execution_path, os.X_OK)
        or sha256_file(execution_path) != expected_sha256
    ):
        raise PrecompileError(f"{label}_EXECUTION_IDENTITY_MISMATCH")
    execution_id = (
        f"PINNED_{label}_FD"
        if PROC_FD_PATTERN.fullmatch(str(execution_path)) is not None
        else label
    )
    return execution_path, execution_id


def _write_process_logs(
    command: Sequence[str],
    *,
    stdout_path: Path,
    stderr_path: Path,
) -> int:
    with stdout_path.open("xb") as stdout, stderr_path.open("xb") as stderr:
        completed = subprocess.run(
            list(command),
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
        )
    return completed.returncode


def _portable_scala_compile_argv(
    command: Sequence[str],
    *,
    scala_root: Path,
    workspace: Path,
    generated_classes: Path,
) -> list[str]:
    portable: list[str] = []
    for index, item in enumerate(command):
        if index == 0:
            portable.append("SCALA_CLI_1_15_0")
        elif item == str(workspace):
            portable.append("SCALA_WORKSPACE")
        elif item == str(generated_classes):
            portable.append(f"EVIDENCE_ROOT/{GENERATED_CLASSES_NAME}")
        elif item.startswith(f"{scala_root}/"):
            portable.append(f"SCALA_ROOT/{item.removeprefix(f'{scala_root}/')}")
        else:
            portable.append(item)
    return portable


def _portable_javac_argv(
    command: Sequence[str],
    *,
    source_root: Path,
    destination: Path,
) -> list[str]:
    portable: list[str] = []
    classpath_index = command.index("-classpath") + 1
    for index, item in enumerate(command):
        if index == 0:
            portable.append("TEMURIN_25_0_3_9_LTS/bin/javac")
        elif item == str(destination):
            portable.append(f"EVIDENCE_ROOT/{GENERATED_CLASSES_NAME}")
        elif index == classpath_index:
            portable.append("SCALA_COMPILE_CLASSPATH")
        elif item.startswith(f"{source_root}/"):
            portable.append(
                f"SCALA_WORKSPACE_GENERATED/"
                f"{item.removeprefix(f'{source_root}/')}"
            )
        else:
            portable.append(item)
    return portable


def _load_sources(
    *,
    scala_root: Path,
    source_manifest: Path,
    source_policy: Path,
) -> list[Path]:
    manifest = source_input_manifest.strict_json(source_manifest)
    policy = source_input_manifest.strict_json(source_policy)
    values = source_input_manifest.validated_source_files(
        scala_root,
        manifest,
        policy=policy,
        require_git_source_equality=True,
    )
    return [
        path
        for path in values
        if manifest["files"][path.relative_to(scala_root).as_posix()]["role"]
        in {"configuration", "main", "benchmark"}
    ]


def _profile_arguments(
    compiler_profiles: Path,
    profile: str,
) -> list[str]:
    profiles = _strict_json(compiler_profiles)
    try:
        arguments = profiles["profiles"][profile]["scalaCliArguments"]
    except (KeyError, TypeError) as error:
        raise PrecompileError("COMPILER_PROFILE_INVALID") from error
    if (
        profiles.get("schemaVersion")
        != "s1.4x-scala-compiler-profiles-v1"
        or profile not in {"A", "B", "C"}
        or not isinstance(arguments, list)
        or any(not isinstance(item, str) for item in arguments)
    ):
        raise PrecompileError("COMPILER_PROFILE_INVALID")
    return arguments


def _generated_class_closure(destination: Path) -> tuple[FileDigest, ...]:
    files = _directory_files(destination)
    expected_prefixes = tuple(
        f"s1_4x/benchmarks/{family}/jmh_generated/"
        for family, _ in BENCHMARK_TYPES
    )
    if (
        not files
        or any(not item.relative_path.endswith(".class") for item in files)
        or any(
            not item.relative_path.startswith(expected_prefixes)
            for item in files
        )
    ):
        raise PrecompileError("GENERATED_CLASS_CLOSURE_INVALID")
    relative_paths = {item.relative_path for item in files}
    for source in expected_generated_source_paths():
        expected_class = f"{source.removesuffix('.java')}.class"
        if expected_class not in relative_paths:
            raise PrecompileError("GENERATED_CLASS_CLOSURE_INVALID")
    return files


def precompile(arguments: argparse.Namespace) -> dict[str, Any]:
    """Scala compile → source closure → pinned javac를 실행하고 typed receipt를 만든다."""

    scala_root = _require_absolute_directory(
        arguments.scala_root,
        label="SCALA_ROOT",
    )
    workspace = _require_absolute_directory(
        arguments.workspace,
        label="SCALA_WORKSPACE",
    )
    coursier_cache = _require_absolute_directory(
        arguments.coursier_cache,
        label="COURSIER_CACHE",
    )
    evidence_dir = _require_absolute_directory(
        arguments.evidence_dir,
        label="EVIDENCE_ROOT",
    )
    source_manifest = _require_absolute_regular(
        arguments.source_manifest,
        label="SOURCE_MANIFEST",
    )
    source_policy = _require_absolute_regular(
        arguments.source_policy,
        label="SOURCE_POLICY",
    )
    compiler_profiles = _require_absolute_regular(
        arguments.compiler_profiles,
        label="COMPILER_PROFILES",
    )
    toolchain_lock = _require_absolute_regular(
        arguments.toolchain_lock,
        label="TOOLCHAIN_LOCK",
    )
    lock = _strict_json(toolchain_lock)
    try:
        scala_cli_sha256 = lock["scalaCli"]["binarySha256"]
        javac_sha256 = lock["jdk"]["javacExecutableSha256"]
        jdk_modules_path_id = lock["jdk"]["jdkModulesPathId"]
        jdk_modules_sha256 = lock["jdk"]["jdkModulesSha256"]
    except (KeyError, TypeError) as error:
        raise PrecompileError("TOOLCHAIN_JAVAC_CLOSURE_MISSING") from error
    scala_cli_exec, scala_cli_execution_id = _verified_executable(
        binary=arguments.scala_cli_binary,
        execution_path=arguments.scala_cli_exec,
        expected_sha256=scala_cli_sha256,
        label="SCALA_CLI_1_15_0",
    )
    javac_exec, javac_execution_id = _verified_executable(
        binary=arguments.javac_binary,
        execution_path=arguments.javac_exec,
        expected_sha256=javac_sha256,
        label="JAVAC",
    )
    jdk_modules = arguments.javac_binary.parent.parent / "lib/modules"
    if (
        jdk_modules_path_id != "TEMURIN_25_0_3_9_LTS/lib/modules"
        or not jdk_modules.is_absolute()
        or jdk_modules.is_symlink()
        or not jdk_modules.is_file()
        or jdk_modules.resolve(strict=True) != jdk_modules
        or sha256_file(jdk_modules) != jdk_modules_sha256
    ):
        raise PrecompileError("JDK_MODULES_IDENTITY_MISMATCH")
    sources = _load_sources(
        scala_root=scala_root,
        source_manifest=source_manifest,
        source_policy=source_policy,
    )
    profile_arguments = _profile_arguments(
        compiler_profiles,
        arguments.profile,
    )
    stdout_path = evidence_dir / SCALA_COMPILE_STDOUT
    stderr_path = evidence_dir / SCALA_COMPILE_STDERR
    javac_stdout = evidence_dir / JAVAC_STDOUT
    javac_stderr = evidence_dir / JAVAC_STDERR
    receipt_path = evidence_dir / RECEIPT_NAME
    destination = evidence_dir / GENERATED_CLASSES_NAME
    reserved = (
        stdout_path,
        stderr_path,
        javac_stdout,
        javac_stderr,
        receipt_path,
    )
    if any(path.exists() or path.is_symlink() for path in reserved):
        raise PrecompileError("PRECOMPILE_OUTPUT_ALREADY_EXISTS")
    destination = _require_absolute_directory(
        destination,
        label="GENERATED_CLASS_OUTPUT",
    )
    if destination.parent != evidence_dir or _directory_files(destination):
        raise PrecompileError("GENERATED_CLASS_OUTPUT_NOT_EMPTY")

    compile_command = [
        str(scala_cli_exec),
        "--power",
        "compile",
        *(str(path) for path in sources),
        "--workspace",
        str(workspace),
        "--server=false",
        "--classpath",
        str(destination),
        "--jvm",
        "system",
        "--coursier-validate-checksums",
        *profile_arguments,
        "--jmh",
        "--jmh-version",
        "1.37",
        "--print-classpath",
    ]
    compile_exit = _write_process_logs(
        compile_command,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
    )
    if compile_exit != 0:
        raise PrecompileError(f"SCALA_JMH_PRECOMPILE_FAILED:{compile_exit}")
    classpath = classpath_closure(
        stdout_path.read_text(encoding="utf-8"),
        workspace=workspace,
        coursier_cache=coursier_cache,
        evidence_dir=evidence_dir,
    )
    generated_sources = generated_source_closure_at(
        classpath.generated_source_root,
        workspace=workspace,
    )
    if _directory_files(destination):
        raise PrecompileError("GENERATED_CLASS_OUTPUT_PRE_JAVAC_DRIFT")
    raw_classpath = os.pathsep.join(str(item.path) for item in classpath.entries)
    javac_command = [
        str(javac_exec),
        "-encoding",
        "UTF-8",
        "-proc:none",
        "-classpath",
        raw_classpath,
        "-d",
        str(destination),
        *(str(generated_sources.root / item.relative_path)
          for item in generated_sources.files),
    ]
    javac_exit = _write_process_logs(
        javac_command,
        stdout_path=javac_stdout,
        stderr_path=javac_stderr,
    )
    if javac_exit != 0:
        raise PrecompileError(f"PINNED_JAVAC_FAILED:{javac_exit}")
    generated_classes = _generated_class_closure(destination)
    if sha256_file(arguments.javac_binary) != javac_sha256:
        raise PrecompileError("JAVAC_BINARY_POST_EXEC_IDENTITY_MISMATCH")
    if sha256_file(javac_exec) != javac_sha256:
        raise PrecompileError("JAVAC_EXECUTION_POST_EXEC_IDENTITY_MISMATCH")
    if sha256_file(jdk_modules) != jdk_modules_sha256:
        raise PrecompileError("JDK_MODULES_POST_EXEC_IDENTITY_MISMATCH")
    # javac이 외부 directory를 채운 뒤의 최종 bytes를 receipt classpath에
    # 기록해야 post-run 검증이 의도된 변경을 drift로 오인하지 않는다.
    refreshed_entries: list[ClasspathEntry] = []
    for item in classpath.entries:
        entry_kind, entry_sha256 = _entry_digest(item.path)
        if (
            entry_kind != item.kind
            or (
                item.path != destination
                and entry_sha256 != item.sha256
            )
        ):
            raise PrecompileError("CLASSPATH_DURING_JAVAC_DRIFT")
        refreshed_entries.append(
            ClasspathEntry(
                path=item.path,
                path_id=item.path_id,
                kind=entry_kind,
                sha256=entry_sha256,
            )
        )
    final_classpath_entries = tuple(refreshed_entries)

    compile_portable = _portable_scala_compile_argv(
        compile_command,
        scala_root=scala_root,
        workspace=workspace,
        generated_classes=destination,
    )
    javac_portable = _portable_javac_argv(
        javac_command,
        source_root=generated_sources.root,
        destination=destination,
    )
    source_values = _file_digest_values(generated_sources.files)
    classpath_values = [
        {
            "pathId": item.path_id,
            "kind": item.kind,
            "sha256": item.sha256,
        }
        for item in final_classpath_entries
    ]
    class_values = _file_digest_values(generated_classes)
    result = {
        "schemaVersion": "s1.4x-scala-jmh-generated-java-precompile-v1",
        "profileId": arguments.profile,
        "sourceInputManifestSha256": sha256_file(source_manifest),
        "compilerProfilesSha256": sha256_file(compiler_profiles),
        "toolchainLockSha256": sha256_file(toolchain_lock),
        "scalaCli": {
            "pathId": "SCALA_CLI_1_15_0",
            "binarySha256": scala_cli_sha256,
            "executionPathId": scala_cli_execution_id,
        },
        "javac": {
            "pathId": "TEMURIN_25_0_3_9_LTS/bin/javac",
            "binarySha256": javac_sha256,
            "executionPathId": javac_execution_id,
            "jdkModulesPathId": jdk_modules_path_id,
            "jdkModulesSha256": jdk_modules_sha256,
        },
        "scalaCompile": {
            "portableArgv": compile_portable,
            "portableArgvSha256": canonical_sha256(compile_portable),
            "runtimeArgvSha256": canonical_sha256(compile_command),
            "stdoutSha256": sha256_file(stdout_path),
            "stderrSha256": sha256_file(stderr_path),
            "exitCode": compile_exit,
            "status": "PASS",
        },
        "jmhGenerator": {
            "generatorId": "reflection",
            "processedClassCount": classpath.processed_class_count,
            "classInputPathId": _portable_path_id(
                classpath.generator_class_input,
                workspace=workspace,
                coursier_cache=coursier_cache,
                evidence_dir=evidence_dir,
            ),
            "generatedSourceRootPathId": _portable_path_id(
                classpath.generated_source_root,
                workspace=workspace,
                coursier_cache=coursier_cache,
                evidence_dir=evidence_dir,
            ),
            "generatedResourceRootPathId": _portable_path_id(
                classpath.generated_resource_root,
                workspace=workspace,
                coursier_cache=coursier_cache,
                evidence_dir=evidence_dir,
            ),
            "classInputClosureSha256": (
                classpath.generator_class_input_sha256
            ),
            "generatedResourceClosureSha256": (
                classpath.generated_resource_root_sha256
            ),
        },
        "generatedSourceRootPathId": (
            f"SCALA_WORKSPACE/"
            f"{generated_sources.root.relative_to(workspace).as_posix()}"
        ),
        "generatedSources": source_values,
        "generatedSourcesSha256": canonical_sha256(source_values),
        "classpathEntries": classpath_values,
        "classpathEntriesSha256": canonical_sha256(classpath_values),
        "scalaClassOutputPathId": _portable_path_id(
            classpath.class_output,
            workspace=workspace,
            coursier_cache=coursier_cache,
            evidence_dir=evidence_dir,
        ),
        "generatedClassOutputPathId": (
            f"EVIDENCE_ROOT/{GENERATED_CLASSES_NAME}"
        ),
        "generatedClasses": class_values,
        "generatedClassesSha256": canonical_sha256(class_values),
        "javacProcess": {
            "portableArgv": javac_portable,
            "portableArgvSha256": canonical_sha256(javac_portable),
            "runtimeArgvSha256": canonical_sha256(javac_command),
            "stdoutSha256": sha256_file(javac_stdout),
            "stderrSha256": sha256_file(javac_stderr),
            "exitCode": javac_exit,
            "status": "PASS",
        },
        "status": "PASS",
        "aggregateStatus": "PASS",
    }
    with receipt_path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(
            json.dumps(
                result,
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
        )
    return result


def verify(arguments: argparse.Namespace) -> dict[str, Any]:
    """Actual serverless run 뒤 compiler·classpath·generator 폐쇄성을 재검증한다."""

    workspace = _require_absolute_directory(
        arguments.workspace,
        label="SCALA_WORKSPACE",
    )
    coursier_cache = _require_absolute_directory(
        arguments.coursier_cache,
        label="COURSIER_CACHE",
    )
    evidence_dir = _require_absolute_directory(
        arguments.evidence_dir,
        label="EVIDENCE_ROOT",
    )
    jmh_stdout = _require_absolute_regular(
        arguments.jmh_stdout,
        label="JMH_STDOUT",
    )
    receipt_path = _require_absolute_regular(
        evidence_dir / RECEIPT_NAME,
        label="PRECOMPILE_RECEIPT",
    )
    receipt = _strict_json(receipt_path)
    if (
        receipt.get("schemaVersion")
        != "s1.4x-scala-jmh-generated-java-precompile-v1"
        or receipt.get("status") != "PASS"
        or receipt.get("aggregateStatus") != "PASS"
    ):
        raise PrecompileError("PRECOMPILE_RECEIPT_INVALID")

    javac = receipt.get("javac")
    if (
        not isinstance(javac, dict)
        or set(javac)
        != {
            "pathId",
            "binarySha256",
            "executionPathId",
            "jdkModulesPathId",
            "jdkModulesSha256",
        }
        or javac.get("pathId") != "TEMURIN_25_0_3_9_LTS/bin/javac"
        or javac.get("jdkModulesPathId")
        != "TEMURIN_25_0_3_9_LTS/lib/modules"
        or SHA256_PATTERN.fullmatch(str(javac.get("binarySha256"))) is None
        or SHA256_PATTERN.fullmatch(str(javac.get("jdkModulesSha256")))
        is None
    ):
        raise PrecompileError("PRECOMPILE_JAVAC_RECEIPT_INVALID")
    javac_exec, javac_execution_id = _verified_executable(
        binary=arguments.javac_binary,
        execution_path=arguments.javac_exec,
        expected_sha256=javac["binarySha256"],
        label="JAVAC",
    )
    modules = arguments.javac_binary.parent.parent / "lib/modules"
    if (
        javac.get("executionPathId") != javac_execution_id
        or modules.is_symlink()
        or not modules.is_file()
        or modules.resolve(strict=True) != modules
        or sha256_file(modules) != javac["jdkModulesSha256"]
        or sha256_file(javac_exec) != javac["binarySha256"]
    ):
        raise PrecompileError("JDK_COMPILER_POST_RUN_DRIFT")

    generated_source_root_id = receipt.get("generatedSourceRootPathId")
    if not isinstance(generated_source_root_id, str):
        raise PrecompileError("PRECOMPILE_RECEIPT_INVALID")
    generated_source_root = _path_from_id(
        generated_source_root_id,
        workspace=workspace,
        coursier_cache=coursier_cache,
        evidence_dir=evidence_dir,
    )
    sources = generated_source_closure_at(
        generated_source_root,
        workspace=workspace,
    )
    source_values = _file_digest_values(sources.files)
    if (
        receipt.get("generatedSources") != source_values
        or receipt.get("generatedSourcesSha256")
        != canonical_sha256(source_values)
    ):
        raise PrecompileError("GENERATED_SOURCE_POST_RUN_DRIFT")

    destination = _require_absolute_directory(
        evidence_dir / GENERATED_CLASSES_NAME,
        label="GENERATED_CLASS_OUTPUT",
    )
    class_values = _file_digest_values(_generated_class_closure(destination))
    if (
        receipt.get("generatedClassOutputPathId")
        != f"EVIDENCE_ROOT/{GENERATED_CLASSES_NAME}"
        or receipt.get("generatedClasses") != class_values
        or receipt.get("generatedClassesSha256")
        != canonical_sha256(class_values)
    ):
        raise PrecompileError("GENERATED_CLASS_POST_RUN_DRIFT")

    classpath_values = receipt.get("classpathEntries")
    if (
        receipt.get("classpathEntriesSha256")
        != canonical_sha256(classpath_values)
    ):
        raise PrecompileError("CLASSPATH_POST_RUN_DRIFT")
    verify_classpath_entries(
        classpath_values,
        workspace=workspace,
        coursier_cache=coursier_cache,
        evidence_dir=evidence_dir,
    )
    external_entries = [
        item
        for item in classpath_values
        if isinstance(item, dict)
        and item.get("pathId")
        == f"EVIDENCE_ROOT/{GENERATED_CLASSES_NAME}"
    ]
    class_output_id = receipt.get("scalaClassOutputPathId")
    if (
        len(external_entries) != 1
        or external_entries[0].get("kind") != "directory"
        or external_entries[0].get("sha256")
        != receipt.get("generatedClassesSha256")
        or not isinstance(class_output_id, str)
        or sum(
            isinstance(item, dict) and item.get("pathId") == class_output_id
            for item in classpath_values
        )
        != 1
    ):
        raise PrecompileError("CLASSPATH_POST_RUN_DRIFT")

    generator = receipt.get("jmhGenerator")
    if (
        not isinstance(generator, dict)
        or set(generator)
        != {
            "generatorId",
            "processedClassCount",
            "classInputPathId",
            "generatedSourceRootPathId",
            "generatedResourceRootPathId",
            "classInputClosureSha256",
            "generatedResourceClosureSha256",
        }
        or generator.get("generatorId") != "reflection"
        or generator.get("processedClassCount") != 147
        or generator.get("generatedSourceRootPathId")
        != generated_source_root_id
    ):
        raise PrecompileError("JMH_GENERATOR_POST_RUN_DRIFT")
    actual_generator = generator_output_closure(
        jmh_stdout.read_text(encoding="utf-8"),
        workspace=workspace,
    )
    actual_generator_values = {
        "generatorId": "reflection",
        "processedClassCount": actual_generator.processed_class_count,
        "classInputPathId": _portable_path_id(
            actual_generator.generator_class_input,
            workspace=workspace,
            coursier_cache=coursier_cache,
            evidence_dir=evidence_dir,
        ),
        "generatedSourceRootPathId": _portable_path_id(
            actual_generator.generated_source_root,
            workspace=workspace,
            coursier_cache=coursier_cache,
            evidence_dir=evidence_dir,
        ),
        "generatedResourceRootPathId": _portable_path_id(
            actual_generator.generated_resource_root,
            workspace=workspace,
            coursier_cache=coursier_cache,
            evidence_dir=evidence_dir,
        ),
        "classInputClosureSha256": (
            actual_generator.generator_class_input_sha256
        ),
        "generatedResourceClosureSha256": (
            actual_generator.generated_resource_root_sha256
        ),
    }
    if generator != actual_generator_values:
        raise PrecompileError("JMH_GENERATOR_POST_RUN_DRIFT")
    actual_sources = generated_source_closure_at(
        actual_generator.generated_source_root,
        workspace=workspace,
    )
    if _file_digest_values(actual_sources.files) != source_values:
        raise PrecompileError("GENERATED_SOURCE_POST_RUN_DRIFT")

    return {
        "status": "PASS",
        "receiptSha256": sha256_file(receipt_path),
        "generatedSourcesSha256": receipt["generatedSourcesSha256"],
        "generatedClassesSha256": receipt["generatedClassesSha256"],
        "classpathEntriesSha256": receipt["classpathEntriesSha256"],
        "classInputClosureSha256": (
            generator["classInputClosureSha256"]
        ),
        "generatedResourceClosureSha256": (
            generator["generatedResourceClosureSha256"]
        ),
        "jdkModulesSha256": javac["jdkModulesSha256"],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    precompile_parser = subcommands.add_parser("precompile")
    precompile_parser.add_argument("--scala-root", type=Path, required=True)
    precompile_parser.add_argument("--workspace", type=Path, required=True)
    precompile_parser.add_argument(
        "--coursier-cache",
        type=Path,
        required=True,
    )
    precompile_parser.add_argument(
        "--evidence-dir",
        type=Path,
        required=True,
    )
    precompile_parser.add_argument(
        "--source-manifest",
        type=Path,
        required=True,
    )
    precompile_parser.add_argument(
        "--source-policy",
        type=Path,
        required=True,
    )
    precompile_parser.add_argument(
        "--compiler-profiles",
        type=Path,
        required=True,
    )
    precompile_parser.add_argument(
        "--toolchain-lock",
        type=Path,
        required=True,
    )
    precompile_parser.add_argument(
        "--scala-cli-binary",
        type=Path,
        required=True,
    )
    precompile_parser.add_argument(
        "--scala-cli-exec",
        type=Path,
        required=True,
    )
    precompile_parser.add_argument(
        "--javac-binary",
        type=Path,
        required=True,
    )
    precompile_parser.add_argument(
        "--javac-exec",
        type=Path,
        required=True,
    )
    precompile_parser.add_argument(
        "--profile",
        choices=("A", "B", "C"),
        required=True,
    )
    verify_parser = subcommands.add_parser("verify")
    verify_parser.add_argument("--workspace", type=Path, required=True)
    verify_parser.add_argument(
        "--coursier-cache",
        type=Path,
        required=True,
    )
    verify_parser.add_argument(
        "--evidence-dir",
        type=Path,
        required=True,
    )
    verify_parser.add_argument(
        "--jmh-stdout",
        type=Path,
        required=True,
    )
    verify_parser.add_argument(
        "--javac-binary",
        type=Path,
        required=True,
    )
    verify_parser.add_argument(
        "--javac-exec",
        type=Path,
        required=True,
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        result = (
            precompile(arguments)
            if arguments.command == "precompile"
            else verify(arguments)
        )
    except (
        OSError,
        UnicodeError,
        ValueError,
        KeyError,
        TypeError,
        subprocess.SubprocessError,
        PrecompileError,
        source_input_manifest.SourceInputManifestError,
    ) as error:
        print(f"SCALA_JMH_GENERATED_JAVA_FAIL:{error}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            result,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
