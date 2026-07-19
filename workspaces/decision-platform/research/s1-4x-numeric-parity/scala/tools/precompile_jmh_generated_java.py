#!/usr/bin/env python3
"""Scala CLI serverless JMH generated Java를 pinned javac로 사전 컴파일한다."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
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
GENERATED_SOURCES_NAME = "generated-java-sources"
GENERATED_CLASSES_NAME = "generated-java-classes"
SCALA_COMPILE_STDOUT = "scala-jmh-precompile.stdout"
SCALA_COMPILE_STDERR = "scala-jmh-precompile.stderr"
JAVAC_STDOUT = "scala-javac.stdout"
JAVAC_STDERR = "scala-javac.stderr"
# 현재 manifest-고정 configuration/main/benchmark source closure의 Scala 3.8.4
# serverless class output은 실제 reflection generator 입력 149개와 exact 결속한다.
EXPECTED_JMH_PROCESSED_CLASS_COUNT = 149
JDK_MODULES_GATE_SNAPSHOT_VARIABLE = (
    "S1_4X_JDK_MODULES_GATE_SNAPSHOT"
)


@dataclass(frozen=True)
class FileDigest:
    """한 번 연 regular inode의 상대 경로·bytes·metadata snapshot."""

    relative_path: str
    sha256: str
    file_identity: tuple[int, int, int, int, int, int, int, int, int]
    payload: bytes


@dataclass(frozen=True)
class RegularFileSnapshot:
    """O_NOFOLLOW로 연 한 regular file의 bytes와 ctime 포함 identity."""

    path: Path
    sha256: str
    file_identity: tuple[int, int, int, int, int, int, int, int, int]
    payload: bytes | None


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
    identity_sha256: str


@dataclass(frozen=True)
class ClasspathClosure:
    """Printed classpath 순서와 Scala CLI JMH class output."""

    entries: tuple[ClasspathEntry, ...]
    class_output: Path
    runtime_classpath_sha256: str
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


@dataclass(frozen=True)
class ExecutableIdentity:
    """실행 pathname/parent proc FD의 pre/post 비교용 immutable identity."""

    path: Path
    expected_sha256: str
    file_identity: tuple[int, int, int, int, int, int, int, int, int]
    proc_owner_pid: int | None
    proc_owner_start_time: int | None
    proc_owner_uid: int | None


def sha256_file(path: Path) -> str:
    """단일-link regular inode를 open/fstat한 같은 bytes에서 해시한다."""

    return _snapshot_regular_file(path, label=str(path)).sha256


def _file_identity_value(
    identity: tuple[int, int, int, int, int, int, int, int, int],
) -> dict[str, int]:
    return {
        "device": identity[0],
        "inode": identity[1],
        "mode": identity[2],
        "linkCount": identity[3],
        "uid": identity[4],
        "gid": identity[5],
        "size": identity[6],
        "mtimeNs": identity[7],
        "ctimeNs": identity[8],
    }


def _snapshot_regular_file(
    path: Path,
    *,
    label: str,
    retain_payload: bool = True,
) -> RegularFileSnapshot:
    """Path race 없이 한 inode의 metadata와 bytes를 함께 capture한다."""

    if (
        not path.is_absolute()
        or path.is_symlink()
        or not path.is_file()
    ):
        raise PrecompileError(f"UNSAFE_OR_MISSING_FILE:{path}")
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
        )
    except OSError as error:
        raise PrecompileError(f"UNSAFE_OR_MISSING_FILE:{path}") from error
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise PrecompileError(f"UNSAFE_OR_MISSING_FILE:{path}")
        chunks: list[bytes] | None = [] if retain_payload else None
        digest = hashlib.sha256()
        payload_size = 0
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            payload_size += len(block)
            if chunks is not None:
                chunks.append(block)
            digest.update(block)
        after = os.fstat(descriptor)
        try:
            path_metadata = os.stat(path, follow_symlinks=False)
        except OSError as error:
            raise PrecompileError(f"{label}_IDENTITY_DRIFT") from error
        if (
            _stat_identity(before) != _stat_identity(after)
            or _stat_identity(after) != _stat_identity(path_metadata)
        ):
            raise PrecompileError(f"{label}_IDENTITY_DRIFT")
    finally:
        os.close(descriptor)
    payload = b"".join(chunks) if chunks is not None else None
    if payload_size != after.st_size:
        raise PrecompileError(f"{label}_IDENTITY_DRIFT")
    return RegularFileSnapshot(
        path=path,
        sha256=digest.hexdigest(),
        file_identity=_stat_identity(after),
        payload=payload,
    )


def _verify_regular_file_snapshot(
    snapshot: RegularFileSnapshot,
    *,
    label: str,
) -> None:
    """Bytes를 복원한 ABA도 ctime/inode 비교로 post-step에서 거부한다."""

    try:
        current = _snapshot_regular_file(
            snapshot.path,
            label=label,
            retain_payload=snapshot.payload is not None,
        )
    except PrecompileError as error:
        raise PrecompileError(f"{label}_IDENTITY_DRIFT") from error
    if (
        current.sha256 != snapshot.sha256
        or current.file_identity != snapshot.file_identity
        or (
            snapshot.payload is not None
            and current.payload != snapshot.payload
        )
    ):
        raise PrecompileError(f"{label}_IDENTITY_DRIFT")


def _verify_regular_file_identity(
    snapshot: RegularFileSnapshot,
    *,
    label: str,
) -> None:
    """이미 전후 hash된 inode를 재사용할 때 ctime 포함 identity만 재확인한다."""

    path = snapshot.path
    if not path.is_absolute() or path.is_symlink():
        raise PrecompileError(f"{label}_IDENTITY_DRIFT")
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
        )
    except OSError as error:
        raise PrecompileError(f"{label}_IDENTITY_DRIFT") from error
    try:
        before = os.fstat(descriptor)
        path_metadata = os.stat(path, follow_symlinks=False)
        after = os.fstat(descriptor)
    except OSError as error:
        raise PrecompileError(f"{label}_IDENTITY_DRIFT") from error
    finally:
        os.close(descriptor)
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_nlink != 1
        or _stat_identity(before) != snapshot.file_identity
        or _stat_identity(after) != snapshot.file_identity
        or _stat_identity(path_metadata) != snapshot.file_identity
    ):
        raise PrecompileError(f"{label}_IDENTITY_DRIFT")


def _regular_file_gate_value(
    snapshot: RegularFileSnapshot,
) -> dict[str, Any]:
    """상위 qualification gate가 child에 넘길 immutable file snapshot이다."""

    owner_pid = os.getpid()
    owner_start_time, _ = _proc_identity(owner_pid)
    owner_script = Path(__file__).with_name(
        "run_profile_qualification.py"
    ).resolve(strict=True)
    return {
        "schemaVersion": "s1.4x-regular-file-gate-snapshot-v1",
        "path": str(snapshot.path),
        "sha256": snapshot.sha256,
        "fileIdentity": _file_identity_value(snapshot.file_identity),
        "ownerProcess": {
            "pid": owner_pid,
            "startTimeTicks": owner_start_time,
            "uid": os.getuid(),
            "scriptPath": str(owner_script),
            "scriptSha256": sha256_file(owner_script),
        },
    }


def _proc_identity(pid: int) -> tuple[int, int]:
    """Linux proc stat에서 process start tick과 parent PID를 읽는다."""

    try:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        fields = raw[raw.rindex(")") + 2 :].split()
        start_time = int(fields[19])
        parent_pid = int(fields[1])
    except (OSError, UnicodeError, ValueError, IndexError) as error:
        raise PrecompileError("JDK_MODULES_GATE_OWNER_INVALID") from error
    if start_time <= 0 or parent_pid < 0:
        raise PrecompileError("JDK_MODULES_GATE_OWNER_INVALID")
    return start_time, parent_pid


def _require_gate_owner(owner: Any) -> None:
    """Gate owner가 현재 child의 실제 qualification ancestor인지 검증한다."""

    expected_script = Path(__file__).with_name(
        "run_profile_qualification.py"
    ).resolve(strict=True)
    if (
        not isinstance(owner, dict)
        or set(owner)
        != {
            "pid",
            "startTimeTicks",
            "uid",
            "scriptPath",
            "scriptSha256",
        }
        or type(owner.get("pid")) is not int
        or owner["pid"] <= 1
        or type(owner.get("startTimeTicks")) is not int
        or type(owner.get("uid")) is not int
        or owner["uid"] != os.getuid()
        or owner.get("scriptPath") != str(expected_script)
        or SHA256_PATTERN.fullmatch(
            str(owner.get("scriptSha256"))
        )
        is None
        or sha256_file(expected_script) != owner["scriptSha256"]
    ):
        raise PrecompileError("JDK_MODULES_GATE_OWNER_INVALID")
    current = os.getppid()
    ancestors: set[int] = set()
    while current > 1 and current not in ancestors:
        ancestors.add(current)
        start_time, parent = _proc_identity(current)
        if current == owner["pid"]:
            try:
                command_line = Path(
                    f"/proc/{current}/cmdline"
                ).read_bytes().split(b"\x00")
                owner_executable = os.stat(
                    f"/proc/{current}/exe",
                    follow_symlinks=True,
                )
                current_executable = os.stat(
                    "/proc/self/exe",
                    follow_symlinks=True,
                )
            except OSError as error:
                raise PrecompileError(
                    "JDK_MODULES_GATE_OWNER_INVALID"
                ) from error
            if (
                start_time != owner["startTimeTicks"]
                or len(command_line) < 5
                or command_line[1:5]
                != [
                    b"-E",
                    b"-s",
                    b"-S",
                    str(expected_script).encode("utf-8"),
                ]
                or (
                    owner_executable.st_dev,
                    owner_executable.st_ino,
                    owner_executable.st_mode,
                    owner_executable.st_uid,
                    owner_executable.st_gid,
                )
                != (
                    current_executable.st_dev,
                    current_executable.st_ino,
                    current_executable.st_mode,
                    current_executable.st_uid,
                    current_executable.st_gid,
                )
            ):
                raise PrecompileError(
                    "JDK_MODULES_GATE_OWNER_INVALID"
                )
            return
        current = parent
    raise PrecompileError("JDK_MODULES_GATE_OWNER_INVALID")


def _jdk_modules_snapshot(
    path: Path,
    *,
    label: str,
) -> RegularFileSnapshot:
    """Qualification parent snapshot이 있으면 content 재해시 없이 identity만 검증한다."""

    raw_gate = os.environ.get(JDK_MODULES_GATE_SNAPSHOT_VARIABLE)
    if raw_gate is None:
        return _snapshot_regular_file(
            path,
            label=label,
            retain_payload=False,
        )
    if os.environ.get("S1_4X_BENCHMARK_RUN_MODE") != "qualification":
        raise PrecompileError("JDK_MODULES_GATE_CONTEXT_INVALID")
    try:
        gate = json.loads(raw_gate)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise PrecompileError("JDK_MODULES_GATE_SNAPSHOT_INVALID") from error
    identity_value = gate.get("fileIdentity") if isinstance(gate, dict) else None
    owner_value = gate.get("ownerProcess") if isinstance(gate, dict) else None
    identity_keys = {
        "device",
        "inode",
        "mode",
        "linkCount",
        "uid",
        "gid",
        "size",
        "mtimeNs",
        "ctimeNs",
    }
    if (
        not isinstance(gate, dict)
        or set(gate)
        != {
            "schemaVersion",
            "path",
            "sha256",
            "fileIdentity",
            "ownerProcess",
        }
        or gate.get("schemaVersion")
        != "s1.4x-regular-file-gate-snapshot-v1"
        or gate.get("path") != str(path)
        or SHA256_PATTERN.fullmatch(str(gate.get("sha256"))) is None
        or not isinstance(identity_value, dict)
        or set(identity_value) != identity_keys
        or any(type(identity_value[key]) is not int for key in identity_keys)
        or identity_value["linkCount"] != 1
    ):
        raise PrecompileError("JDK_MODULES_GATE_SNAPSHOT_INVALID")
    _require_gate_owner(owner_value)
    snapshot = RegularFileSnapshot(
        path=path,
        sha256=gate["sha256"],
        file_identity=(
            identity_value["device"],
            identity_value["inode"],
            identity_value["mode"],
            identity_value["linkCount"],
            identity_value["uid"],
            identity_value["gid"],
            identity_value["size"],
            identity_value["mtimeNs"],
            identity_value["ctimeNs"],
        ),
        payload=None,
    )
    _verify_regular_file_identity(snapshot, label=label)
    return snapshot


def _verify_jdk_modules_snapshot(
    snapshot: RegularFileSnapshot,
    *,
    label: str,
) -> None:
    """상위 gate가 content를 소유하면 child는 동일 inode identity만 재확인한다."""

    if os.environ.get(JDK_MODULES_GATE_SNAPSHOT_VARIABLE) is None:
        _verify_regular_file_snapshot(snapshot, label=label)
    else:
        _verify_regular_file_identity(snapshot, label=label)


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


def _directory_files(root: Path) -> tuple[FileDigest, ...]:
    try:
        root_before = _stat_identity(os.stat(root, follow_symlinks=False))
    except OSError as error:
        raise PrecompileError(f"DIRECTORY_CLOSURE_INVALID:{root}") from error
    values: list[FileDigest] = []
    for path in root.rglob("*"):
        # pathlib rglob은 기본적으로 directory symlink를 재귀하지 않는다.
        # 발견된 각 component 자체를 거부하면 같은 ancestor를 매 파일마다
        # 반복 stat하지 않으면서 closure 밖 우회를 막을 수 있다.
        if path.is_symlink():
            raise PrecompileError(f"SYMLINK_IN_CLOSURE:{path}")
        if path.is_dir():
            continue
        if not path.is_file():
            raise PrecompileError(f"NON_REGULAR_IN_CLOSURE:{path}")
        snapshot = _snapshot_regular_file(
            path,
            label=f"DIRECTORY_FILE:{path.relative_to(root).as_posix()}",
        )
        if snapshot.payload is None:
            raise PrecompileError(f"DIRECTORY_FILE_PAYLOAD_MISSING:{path}")
        values.append(
            FileDigest(
                relative_path=path.relative_to(root).as_posix(),
                sha256=snapshot.sha256,
                file_identity=snapshot.file_identity,
                payload=snapshot.payload,
            )
        )
    result = tuple(
        sorted(values, key=lambda item: item.relative_path.encode("utf-8"))
    )
    for item in result:
        path = root / item.relative_path
        try:
            current = os.stat(path, follow_symlinks=False)
        except OSError as error:
            raise PrecompileError(
                f"DIRECTORY_FILE:{item.relative_path}_IDENTITY_DRIFT"
            ) from error
        # Capture 때의 content hash와 open/fstat identity에 ctime을 포함했으므로,
        # scan 말미의 exact stat 비교가 bytes 복원형 ABA도 거부한다.
        if (
            path.is_symlink()
            or _stat_identity(current) != item.file_identity
        ):
            raise PrecompileError(
                f"DIRECTORY_FILE:{item.relative_path}_IDENTITY_DRIFT"
            )
    try:
        root_after = _stat_identity(os.stat(root, follow_symlinks=False))
    except OSError as error:
        raise PrecompileError(f"DIRECTORY_CLOSURE_INVALID:{root}") from error
    if root_before != root_after:
        raise PrecompileError(f"DIRECTORY_CLOSURE_IDENTITY_DRIFT:{root}")
    return result


def _file_digest_values(
    values: Sequence[FileDigest],
) -> list[dict[str, str]]:
    return [
        {"path": item.relative_path, "sha256": item.sha256}
        for item in values
    ]


def _file_identity_values(
    values: Sequence[FileDigest],
) -> list[dict[str, Any]]:
    return [
        {
            "path": item.relative_path,
            "fileIdentity": _file_identity_value(item.file_identity),
        }
        for item in values
    ]


def _file_identity_sha256(values: Sequence[FileDigest]) -> str:
    return canonical_sha256(_file_identity_values(values))


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
    workspace: Path | None = None,
    evidence_dir: Path | None = None,
) -> GeneratedSourceClosure:
    """Receipt가 지목한 한 generated-Java root의 exact set을 검증한다."""

    root = _require_absolute_directory(
        root,
        label="GENERATED_SOURCE_ROOT",
    )
    workspace_root: Path | None = None
    if workspace is not None:
        workspace_root = _require_absolute_directory(
            workspace,
            label="SCALA_WORKSPACE",
        )
    evidence_root: Path | None = None
    if evidence_dir is not None:
        evidence_root = _require_absolute_directory(
            evidence_dir,
            label="EVIDENCE_ROOT",
        )
    if (workspace_root is None) == (evidence_root is None):
        raise PrecompileError("GENERATED_SOURCE_ROOT_CLOSURE_MISMATCH")
    if workspace_root is not None and (
        not root.is_relative_to(workspace_root / ".scala-build")
        or root.name != "sources"
        or not root.parent.name.endswith("_jmh")
    ):
        raise PrecompileError("GENERATED_SOURCE_ROOT_CLOSURE_MISMATCH")
    if (
        evidence_root is not None
        and root != evidence_root / GENERATED_SOURCES_NAME
    ):
        raise PrecompileError("GENERATED_SOURCE_ROOT_CLOSURE_MISMATCH")
    files = _directory_files(root)
    actual = tuple(item.relative_path for item in files)
    if actual != expected_generated_source_paths():
        raise PrecompileError("GENERATED_SOURCE_CLOSURE_MISMATCH")
    _validate_generated_source_contents(files)
    return GeneratedSourceClosure(root=root, files=files)


def _validate_generated_source_contents(
    files: Sequence[FileDigest],
) -> None:
    """Filename-only fixture가 아니라 JMH generator Java shape인지 확인한다."""

    for item in files:
        try:
            text = item.payload.decode("utf-8")
        except UnicodeError as error:
            raise PrecompileError(
                "GENERATED_SOURCE_CONTENT_INVALID"
            ) from error
        relative = Path(item.relative_path)
        package_name = ".".join(relative.parent.parts)
        class_name = relative.stem
        declaration = re.search(
            rf"\bpublic\s+(?:final\s+)?class\s+{re.escape(class_name)}\b",
            text,
        )
        if (
            "\x00" in text
            or "\r" in text
            or not text.startswith(f"package {package_name};\n")
            or declaration is None
            or (
                class_name.endswith("_benchmark_jmhTest")
                and (
                    "org.openjdk.jmh" not in text
                    or f"public final class {class_name}" not in text
                )
            )
        ):
            raise PrecompileError("GENERATED_SOURCE_CONTENT_INVALID")


def _copy_generated_sources(
    source: GeneratedSourceClosure,
    *,
    evidence_dir: Path,
) -> GeneratedSourceClosure:
    """Generator bytes를 exclusive evidence tree로 복제해 javac 입력을 보존한다."""

    destination = evidence_dir / GENERATED_SOURCES_NAME
    if destination.exists() or destination.is_symlink():
        raise PrecompileError("GENERATED_SOURCE_OUTPUT_ALREADY_EXISTS")
    destination.mkdir()
    for item in source.files:
        output = destination / item.relative_path
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("xb") as stream:
            stream.write(item.payload)
    copied = generated_source_closure_at(
        destination,
        evidence_dir=evidence_dir,
    )
    if _file_digest_values(copied.files) != _file_digest_values(source.files):
        raise PrecompileError("GENERATED_SOURCE_COPY_DRIFT")
    return copied


def _portable_path_id(
    path: Path,
    *,
    workspace: Path,
    coursier_cache: Path,
    evidence_dir: Path,
) -> str:
    if path == evidence_dir / GENERATED_SOURCES_NAME:
        return f"EVIDENCE_ROOT/{GENERATED_SOURCES_NAME}"
    if path == evidence_dir / GENERATED_CLASSES_NAME:
        return f"EVIDENCE_ROOT/{GENERATED_CLASSES_NAME}"
    if path.is_relative_to(workspace):
        return f"SCALA_WORKSPACE/{path.relative_to(workspace).as_posix()}"
    if path.is_relative_to(coursier_cache):
        return f"COURSIER_CACHE/{path.relative_to(coursier_cache).as_posix()}"
    raise PrecompileError(f"CLASSPATH_ENTRY_OUTSIDE_SEALED_ROOTS:{path}")


def _entry_digest(path: Path) -> tuple[str, str, str]:
    if path.is_file() and not path.is_symlink():
        snapshot = _snapshot_regular_file(path, label=f"CLASSPATH_FILE:{path}")
        return (
            "file",
            snapshot.sha256,
            canonical_sha256(_file_identity_value(snapshot.file_identity)),
        )
    if path.is_dir() and not path.is_symlink():
        closure = _directory_files(path)
        files = _file_digest_values(closure)
        identity = _file_identity_values(closure)
        return (
            "directory",
            canonical_sha256(files),
            canonical_sha256(identity),
        )
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
        processed_class_count != EXPECTED_JMH_PROCESSED_CLASS_COUNT
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
    allow_trailing: bool = False,
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
    if len(lines) < 3 or (not allow_trailing and len(lines) != 3):
        raise PrecompileError("JMH_GENERATOR_STDOUT_INVALID")
    generator = generator_output_closure(
        "\n".join(lines[:2]) + "\n",
        workspace=workspace,
    )

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
        kind, digest, identity_sha256 = _entry_digest(path)
        entries.append(
            ClasspathEntry(
                path=path,
                path_id=path_id,
                kind=kind,
                sha256=digest,
                identity_sha256=identity_sha256,
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
        runtime_classpath_sha256=hashlib.sha256(
            lines[2].encode("utf-8")
        ).hexdigest(),
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


def require_matching_classpath(
    expected: ClasspathClosure,
    actual: ClasspathClosure,
) -> None:
    """Actual JMH stdout의 ordered entries와 class output을 compile과 결속한다."""

    expected_values = [
        (
            item.path_id,
            item.kind,
            item.sha256,
            item.identity_sha256,
        )
        for item in expected.entries
    ]
    actual_values = [
        (
            item.path_id,
            item.kind,
            item.sha256,
            item.identity_sha256,
        )
        for item in actual.entries
    ]
    if (
        actual_values != expected_values
        or actual.class_output != expected.class_output
        or actual.runtime_classpath_sha256
        != expected.runtime_classpath_sha256
        or actual.processed_class_count != expected.processed_class_count
        or actual.generator_class_input != expected.generator_class_input
        or actual.generated_source_root != expected.generated_source_root
        or actual.generated_resource_root != expected.generated_resource_root
        or actual.generator_class_input_sha256
        != expected.generator_class_input_sha256
        or actual.generated_resource_root_sha256
        != expected.generated_resource_root_sha256
    ):
        raise PrecompileError("JMH_RUN_CLASSPATH_DRIFT")


def _runtime_class_output(
    generator: GeneratorOutputClosure,
) -> Path:
    """Actual run build에서 exact 10-hex JMH class-output 하나만 선택한다."""

    generated_build = generator.generated_source_root.parent
    build_parent = generated_build.parent
    pattern = re.compile(
        rf"{re.escape(generated_build.name)}_[0-9a-f]{{10}}"
    )
    candidates: list[Path] = []
    try:
        children = list(build_parent.iterdir())
    except OSError as error:
        raise PrecompileError(
            "JMH_RUN_CLASS_OUTPUT_CARDINALITY_INVALID"
        ) from error
    for child in children:
        if pattern.fullmatch(child.name) is None:
            continue
        class_output = child / "classes/main"
        if (
            child.is_symlink()
            or not child.is_dir()
            or child.resolve(strict=True) != child
            or class_output.is_symlink()
            or not class_output.is_dir()
            or class_output.resolve(strict=True) != class_output
        ):
            raise PrecompileError(
                "JMH_RUN_CLASS_OUTPUT_CARDINALITY_INVALID"
            )
        candidates.append(class_output)
    if len(candidates) != 1:
        raise PrecompileError(
            "JMH_RUN_CLASS_OUTPUT_CARDINALITY_INVALID"
        )
    return candidates[0]


def _classpath_entry_index(
    closure: ClasspathClosure,
    path: Path,
) -> int:
    indexes = [
        index
        for index, item in enumerate(closure.entries)
        if item.path == path
    ]
    if len(indexes) != 1:
        raise PrecompileError("JMH_RUN_CLASSPATH_DRIFT")
    return indexes[0]


def require_jmh_stdout_binding(
    compile_raw: str,
    jmh_raw: str,
    *,
    workspace: Path,
    coursier_cache: Path,
    evidence_dir: Path,
) -> ClasspathClosure:
    """Compile/run build path만 exact role로 remap하고 actual fork closure를 만든다."""

    if (
        "\x00" in compile_raw
        or "\r" in compile_raw
        or "\x00" in jmh_raw
        or "\r" in jmh_raw
    ):
        raise PrecompileError("JMH_RUN_STDOUT_BINDING_INVALID")
    compile_lines = compile_raw.splitlines()
    jmh_lines = jmh_raw.splitlines()
    if (
        len(compile_lines) != 3
        or not compile_raw.endswith("\n")
        or len(jmh_lines) < 3
        or jmh_lines[2] != "# JMH version: 1.37"
    ):
        raise PrecompileError("JMH_RUN_STDOUT_BINDING_INVALID")
    precompile_classpath = classpath_closure(
        compile_raw,
        workspace=workspace,
        coursier_cache=coursier_cache,
        evidence_dir=evidence_dir,
    )
    runtime_prefix = "\n".join(jmh_lines[:2]) + "\n"
    try:
        runtime_generator = generator_output_closure(
            runtime_prefix,
            workspace=workspace,
        )
    except PrecompileError as error:
        raise PrecompileError(
            "JMH_RUN_STDOUT_BINDING_INVALID"
        ) from error
    if (
        runtime_generator.processed_class_count
        != precompile_classpath.processed_class_count
        or runtime_generator.generator_class_input_sha256
        != precompile_classpath.generator_class_input_sha256
        or runtime_generator.generated_resource_root_sha256
        != precompile_classpath.generated_resource_root_sha256
    ):
        raise PrecompileError("JMH_RUN_CLASSPATH_DRIFT")

    class_output_index = _classpath_entry_index(
        precompile_classpath,
        precompile_classpath.class_output,
    )
    resource_index = _classpath_entry_index(
        precompile_classpath,
        precompile_classpath.generated_resource_root,
    )
    if class_output_index == resource_index:
        raise PrecompileError("JMH_RUN_CLASSPATH_DRIFT")
    runtime_paths = [
        item.path for item in precompile_classpath.entries
    ]
    runtime_paths[class_output_index] = _runtime_class_output(
        runtime_generator
    )
    runtime_paths[resource_index] = (
        runtime_generator.generated_resource_root
    )
    runtime_raw = (
        runtime_prefix
        + os.pathsep.join(str(path) for path in runtime_paths)
        + "\n"
    )
    runtime_classpath = classpath_closure(
        runtime_raw,
        workspace=workspace,
        coursier_cache=coursier_cache,
        evidence_dir=evidence_dir,
    )
    for index, (precompile_item, runtime_item) in enumerate(
        zip(
            precompile_classpath.entries,
            runtime_classpath.entries,
            strict=True,
        )
    ):
        if index in {class_output_index, resource_index}:
            if (
                precompile_item.kind != "directory"
                or runtime_item.kind != "directory"
                or precompile_item.sha256 != runtime_item.sha256
            ):
                raise PrecompileError("JMH_RUN_CLASSPATH_DRIFT")
        elif (
            precompile_item.path_id,
            precompile_item.kind,
            precompile_item.sha256,
            precompile_item.identity_sha256,
        ) != (
            runtime_item.path_id,
            runtime_item.kind,
            runtime_item.sha256,
            runtime_item.identity_sha256,
        ):
            raise PrecompileError("JMH_RUN_CLASSPATH_DRIFT")
    return runtime_classpath


def require_runtime_classpath_evidence(
    expected_sha256: str,
    forks: Any,
) -> None:
    """모든 실제 fork의 java.class.path hash를 compile classpath line에 결속한다."""

    if (
        SHA256_PATTERN.fullmatch(expected_sha256) is None
        or not isinstance(forks, list)
        or not forks
        or any(
            not isinstance(fork, dict)
            or fork.get("runtimeClasspathSha256") != expected_sha256
            for fork in forks
        )
    ):
        raise PrecompileError("JMH_RUN_CLASSPATH_DRIFT")


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
                and relative
                not in {
                    Path(GENERATED_SOURCES_NAME),
                    Path(GENERATED_CLASSES_NAME),
                }
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
            or set(item)
            != {"pathId", "kind", "sha256", "identitySha256"}
            or item.get("kind") not in {"file", "directory"}
            or not isinstance(item.get("pathId"), str)
            or item["pathId"] in seen
            or SHA256_PATTERN.fullmatch(
                str(item.get("identitySha256"))
            )
            is None
        ):
            raise PrecompileError("CLASSPATH_POST_RUN_DRIFT")
        seen.add(item["pathId"])
        path = _path_from_id(
            item["pathId"],
            workspace=workspace,
            coursier_cache=coursier_cache,
            evidence_dir=evidence_dir,
        )
        kind, digest, identity_sha256 = _entry_digest(path)
        if kind != item["kind"] or digest != item.get("sha256"):
            raise PrecompileError("CLASSPATH_POST_RUN_DRIFT")
        if identity_sha256 != item["identitySha256"]:
            raise PrecompileError("CLASSPATH_POST_RUN_IDENTITY_DRIFT")


def _classpath_identity_rotation_policy(
    classpath_entries: Any,
    *,
    scala_class_output_path_id: Any,
    generated_resource_path_id: Any,
) -> tuple[list[dict[str, str]], set[str]]:
    """Scala CLI가 합법적으로 rematerialize하는 정확한 두 workspace role을 고정한다."""

    class_output_match = re.fullmatch(
        r"SCALA_WORKSPACE/\.scala-build/"
        r"(?P<build>[A-Za-z0-9._-]+)_jmh_[0-9a-f]{10}/classes/main",
        str(scala_class_output_path_id),
    )
    resource_match = re.fullmatch(
        r"SCALA_WORKSPACE/\.scala-build/"
        r"(?P<build>[A-Za-z0-9._-]+)_jmh/resources",
        str(generated_resource_path_id),
    )
    if (
        not isinstance(classpath_entries, list)
        or not classpath_entries
        or class_output_match is None
        or resource_match is None
        or class_output_match.group("build")
        != resource_match.group("build")
        or scala_class_output_path_id == generated_resource_path_id
    ):
        raise PrecompileError("CLASSPATH_POST_RUN_EVIDENCE_INVALID")
    seen: set[str] = set()
    for item in classpath_entries:
        if (
            not isinstance(item, dict)
            or set(item)
            != {"pathId", "kind", "sha256", "identitySha256"}
            or item.get("kind") not in {"file", "directory"}
            or not isinstance(item.get("pathId"), str)
            or item["pathId"] in seen
            or SHA256_PATTERN.fullmatch(str(item.get("sha256"))) is None
            or SHA256_PATTERN.fullmatch(
                str(item.get("identitySha256"))
            )
            is None
        ):
            raise PrecompileError("CLASSPATH_POST_RUN_EVIDENCE_INVALID")
        seen.add(item["pathId"])
    allowed = [
        {
            "role": "SCALA_CLASS_OUTPUT",
            "pathId": str(scala_class_output_path_id),
        },
        {
            "role": "JMH_GENERATED_RESOURCES",
            "pathId": str(generated_resource_path_id),
        },
    ]
    allowed_ids = {item["pathId"] for item in allowed}
    if (
        sum(item["pathId"] == allowed[0]["pathId"] for item in classpath_entries)
        != 1
        or sum(
            item["pathId"] == allowed[1]["pathId"]
            for item in classpath_entries
        )
        != 1
        or any(
            item["kind"] != "directory"
            for item in classpath_entries
            if item["pathId"] in allowed_ids
        )
    ):
        raise PrecompileError("CLASSPATH_POST_RUN_EVIDENCE_INVALID")
    return allowed, allowed_ids


def validate_classpath_post_run_evidence(
    value: Any,
    *,
    classpath_entries: Any,
    scala_class_output_path_id: Any,
    generated_resource_path_id: Any,
) -> None:
    """Pre/post identity evidence가 exact role과 동일 byte closure만 허용하는지 검증한다."""

    allowed, allowed_ids = _classpath_identity_rotation_policy(
        classpath_entries,
        scala_class_output_path_id=scala_class_output_path_id,
        generated_resource_path_id=generated_resource_path_id,
    )
    entries = value.get("entries") if isinstance(value, dict) else None
    if (
        not isinstance(value, dict)
        or set(value)
        != {
            "schemaVersion",
            "allowedIdentityRotations",
            "entries",
            "entriesSha256",
            "rotatedPathIds",
            "status",
        }
        or value.get("schemaVersion") != "s1.4x-classpath-post-run-v1"
        or value.get("allowedIdentityRotations") != allowed
        or not isinstance(entries, list)
        or len(entries) != len(classpath_entries)
        or value.get("entriesSha256") != canonical_sha256(entries)
        or value.get("status") != "PASS"
    ):
        raise PrecompileError("CLASSPATH_POST_RUN_EVIDENCE_INVALID")
    rotated_path_ids: list[str] = []
    for expected, actual in zip(classpath_entries, entries, strict=True):
        if (
            not isinstance(actual, dict)
            or set(actual)
            != {
                "pathId",
                "kind",
                "sha256",
                "preRunIdentitySha256",
                "postRunIdentitySha256",
                "identityStatus",
            }
            or actual.get("pathId") != expected["pathId"]
            or actual.get("kind") != expected["kind"]
            or actual.get("sha256") != expected["sha256"]
            or actual.get("preRunIdentitySha256")
            != expected["identitySha256"]
            or SHA256_PATTERN.fullmatch(
                str(actual.get("postRunIdentitySha256"))
            )
            is None
        ):
            raise PrecompileError("CLASSPATH_POST_RUN_EVIDENCE_INVALID")
        rotated = (
            actual["postRunIdentitySha256"]
            != actual["preRunIdentitySha256"]
        )
        expected_status = "ROTATED_SAME_BYTES" if rotated else "STABLE"
        if (
            actual.get("identityStatus") != expected_status
            or (rotated and actual["pathId"] not in allowed_ids)
        ):
            raise PrecompileError("CLASSPATH_POST_RUN_EVIDENCE_INVALID")
        if rotated:
            rotated_path_ids.append(actual["pathId"])
    if value.get("rotatedPathIds") != rotated_path_ids:
        raise PrecompileError("CLASSPATH_POST_RUN_EVIDENCE_INVALID")


def capture_classpath_post_run(
    values: Any,
    *,
    scala_class_output_path_id: Any,
    generated_resource_path_id: Any,
    workspace: Path,
    coursier_cache: Path,
    evidence_dir: Path,
) -> dict[str, Any]:
    """Post-run bytes를 다시 닫고 exact 두 workspace directory만 inode 회전을 허용한다."""

    workspace = _require_absolute_directory(workspace, label="SCALA_WORKSPACE")
    coursier_cache = _require_absolute_directory(
        coursier_cache,
        label="COURSIER_CACHE",
    )
    evidence_dir = _require_absolute_directory(
        evidence_dir,
        label="EVIDENCE_ROOT",
    )
    allowed, allowed_ids = _classpath_identity_rotation_policy(
        values,
        scala_class_output_path_id=scala_class_output_path_id,
        generated_resource_path_id=generated_resource_path_id,
    )
    post_entries: list[dict[str, str]] = []
    rotated_path_ids: list[str] = []
    for item in values:
        path = _path_from_id(
            item["pathId"],
            workspace=workspace,
            coursier_cache=coursier_cache,
            evidence_dir=evidence_dir,
        )
        kind, digest, identity_sha256 = _entry_digest(path)
        if kind != item["kind"] or digest != item["sha256"]:
            raise PrecompileError("CLASSPATH_POST_RUN_DRIFT")
        rotated = identity_sha256 != item["identitySha256"]
        if rotated and item["pathId"] not in allowed_ids:
            raise PrecompileError("CLASSPATH_POST_RUN_IDENTITY_DRIFT")
        if rotated:
            rotated_path_ids.append(item["pathId"])
        post_entries.append(
            {
                "pathId": item["pathId"],
                "kind": kind,
                "sha256": digest,
                "preRunIdentitySha256": item["identitySha256"],
                "postRunIdentitySha256": identity_sha256,
                "identityStatus": (
                    "ROTATED_SAME_BYTES" if rotated else "STABLE"
                ),
            }
        )
    result = {
        "schemaVersion": "s1.4x-classpath-post-run-v1",
        "allowedIdentityRotations": allowed,
        "entries": post_entries,
        "entriesSha256": canonical_sha256(post_entries),
        "rotatedPathIds": rotated_path_ids,
        "status": "PASS",
    }
    validate_classpath_post_run_evidence(
        result,
        classpath_entries=values,
        scala_class_output_path_id=scala_class_output_path_id,
        generated_resource_path_id=generated_resource_path_id,
    )
    return result


def _generator_evidence_value(
    closure: ClasspathClosure,
    *,
    workspace: Path,
    coursier_cache: Path,
    evidence_dir: Path,
) -> dict[str, Any]:
    """Generator의 path role과 byte closure를 portable evidence로 만든다."""

    return {
        "generatorId": "reflection",
        "processedClassCount": closure.processed_class_count,
        "classInputPathId": _portable_path_id(
            closure.generator_class_input,
            workspace=workspace,
            coursier_cache=coursier_cache,
            evidence_dir=evidence_dir,
        ),
        "generatedSourceRootPathId": _portable_path_id(
            closure.generated_source_root,
            workspace=workspace,
            coursier_cache=coursier_cache,
            evidence_dir=evidence_dir,
        ),
        "generatedResourceRootPathId": _portable_path_id(
            closure.generated_resource_root,
            workspace=workspace,
            coursier_cache=coursier_cache,
            evidence_dir=evidence_dir,
        ),
        "classInputClosureSha256": (
            closure.generator_class_input_sha256
        ),
        "generatedResourceClosureSha256": (
            closure.generated_resource_root_sha256
        ),
    }


def _classpath_evidence_values(
    closure: ClasspathClosure,
) -> list[dict[str, str]]:
    return [
        {
            "pathId": item.path_id,
            "kind": item.kind,
            "sha256": item.sha256,
            "identitySha256": item.identity_sha256,
        }
        for item in closure.entries
    ]


def create_jmh_runtime_closure_evidence(
    *,
    precompile_classpath: ClasspathClosure,
    runtime_classpath: ClasspathClosure,
    generated_sources_sha256: str,
    workspace: Path,
    coursier_cache: Path,
    evidence_dir: Path,
) -> dict[str, Any]:
    """Actual run의 remapped 3개 role과 ordered fork classpath를 기록한다."""

    if SHA256_PATTERN.fullmatch(generated_sources_sha256) is None:
        raise PrecompileError("JMH_RUNTIME_CLOSURE_EVIDENCE_INVALID")
    precompile_class_index = _classpath_entry_index(
        precompile_classpath,
        precompile_classpath.class_output,
    )
    precompile_resource_index = _classpath_entry_index(
        precompile_classpath,
        precompile_classpath.generated_resource_root,
    )
    runtime_class_index = _classpath_entry_index(
        runtime_classpath,
        runtime_classpath.class_output,
    )
    runtime_resource_index = _classpath_entry_index(
        runtime_classpath,
        runtime_classpath.generated_resource_root,
    )
    if (
        precompile_class_index != runtime_class_index
        or precompile_resource_index != runtime_resource_index
    ):
        raise PrecompileError("JMH_RUNTIME_CLOSURE_EVIDENCE_INVALID")
    precompile_class = precompile_classpath.entries[
        precompile_class_index
    ]
    precompile_resource = precompile_classpath.entries[
        precompile_resource_index
    ]
    runtime_class = runtime_classpath.entries[runtime_class_index]
    runtime_resource = runtime_classpath.entries[
        runtime_resource_index
    ]
    runtime_entries = _classpath_evidence_values(runtime_classpath)
    result = {
        "schemaVersion": "s1.4x-jmh-runtime-closure-v1",
        "generator": _generator_evidence_value(
            runtime_classpath,
            workspace=workspace,
            coursier_cache=coursier_cache,
            evidence_dir=evidence_dir,
        ),
        "roleMappings": [
            {
                "role": "SCALA_CLASS_OUTPUT",
                "precompilePathId": precompile_class.path_id,
                "runtimePathId": runtime_class.path_id,
                "sha256": runtime_class.sha256,
            },
            {
                "role": "JMH_GENERATED_SOURCES",
                "precompilePathId": _portable_path_id(
                    precompile_classpath.generated_source_root,
                    workspace=workspace,
                    coursier_cache=coursier_cache,
                    evidence_dir=evidence_dir,
                ),
                "runtimePathId": _portable_path_id(
                    runtime_classpath.generated_source_root,
                    workspace=workspace,
                    coursier_cache=coursier_cache,
                    evidence_dir=evidence_dir,
                ),
                "sha256": generated_sources_sha256,
            },
            {
                "role": "JMH_GENERATED_RESOURCES",
                "precompilePathId": precompile_resource.path_id,
                "runtimePathId": runtime_resource.path_id,
                "sha256": runtime_resource.sha256,
            },
        ],
        "runtimeClasspathEntries": runtime_entries,
        "runtimeClasspathEntriesSha256": canonical_sha256(
            runtime_entries
        ),
        "runtimeClasspathSha256": (
            runtime_classpath.runtime_classpath_sha256
        ),
        "status": "PASS",
    }
    return result


def validate_jmh_runtime_closure_evidence(
    value: Any,
    *,
    classpath_entries: Any,
    classpath_post_run: Any,
    scala_class_output_path_id: Any,
    jmh_generator: Any,
    generated_sources_sha256: Any,
) -> None:
    """Final runtime mapping이 exact 3개 role 외 path/order/identity를 못 바꾸게 한다."""

    invalid = "JMH_RUNTIME_CLOSURE_EVIDENCE_INVALID"
    if (
        not isinstance(jmh_generator, dict)
        or set(jmh_generator)
        != {
            "generatorId",
            "processedClassCount",
            "classInputPathId",
            "generatedSourceRootPathId",
            "generatedResourceRootPathId",
            "classInputClosureSha256",
            "generatedResourceClosureSha256",
        }
        or jmh_generator.get("generatorId") != "reflection"
        or jmh_generator.get("processedClassCount")
        != EXPECTED_JMH_PROCESSED_CLASS_COUNT
        or SHA256_PATTERN.fullmatch(
            str(jmh_generator.get("classInputClosureSha256"))
        )
        is None
        or SHA256_PATTERN.fullmatch(
            str(jmh_generator.get("generatedResourceClosureSha256"))
        )
        is None
        or SHA256_PATTERN.fullmatch(str(generated_sources_sha256))
        is None
    ):
        raise PrecompileError(invalid)
    generated_resource_path_id = jmh_generator[
        "generatedResourceRootPathId"
    ]
    try:
        validate_classpath_post_run_evidence(
            classpath_post_run,
            classpath_entries=classpath_entries,
            scala_class_output_path_id=scala_class_output_path_id,
            generated_resource_path_id=generated_resource_path_id,
        )
    except PrecompileError as error:
        raise PrecompileError(invalid) from error

    runtime_generator = (
        value.get("generator") if isinstance(value, dict) else None
    )
    runtime_entries = (
        value.get("runtimeClasspathEntries")
        if isinstance(value, dict)
        else None
    )
    if (
        not isinstance(value, dict)
        or set(value)
        != {
            "schemaVersion",
            "generator",
            "roleMappings",
            "runtimeClasspathEntries",
            "runtimeClasspathEntriesSha256",
            "runtimeClasspathSha256",
            "status",
        }
        or value.get("schemaVersion")
        != "s1.4x-jmh-runtime-closure-v1"
        or value.get("status") != "PASS"
        or not isinstance(runtime_generator, dict)
        or set(runtime_generator) != set(jmh_generator)
        or runtime_generator.get("generatorId") != "reflection"
        or runtime_generator.get("processedClassCount")
        != jmh_generator["processedClassCount"]
        or runtime_generator.get("classInputClosureSha256")
        != jmh_generator["classInputClosureSha256"]
        or runtime_generator.get("generatedResourceClosureSha256")
        != jmh_generator["generatedResourceClosureSha256"]
        or not isinstance(runtime_entries, list)
        or len(runtime_entries) != len(classpath_entries)
        or value.get("runtimeClasspathEntriesSha256")
        != canonical_sha256(runtime_entries)
        or SHA256_PATTERN.fullmatch(
            str(value.get("runtimeClasspathSha256"))
        )
        is None
    ):
        raise PrecompileError(invalid)

    runtime_input_match = re.fullmatch(
        r"SCALA_WORKSPACE/\.scala-build/"
        r"(?P<build>[A-Za-z0-9._-]+)/classes/main",
        str(runtime_generator.get("classInputPathId")),
    )
    if runtime_input_match is None:
        raise PrecompileError(invalid)
    runtime_build = runtime_input_match.group("build")
    if runtime_build in {".", ".."}:
        raise PrecompileError(invalid)
    runtime_source_id = (
        f"SCALA_WORKSPACE/.scala-build/{runtime_build}_jmh/sources"
    )
    runtime_resource_id = (
        f"SCALA_WORKSPACE/.scala-build/{runtime_build}_jmh/resources"
    )
    if (
        runtime_generator.get("generatedSourceRootPathId")
        != runtime_source_id
        or runtime_generator.get("generatedResourceRootPathId")
        != runtime_resource_id
    ):
        raise PrecompileError(invalid)
    runtime_class_pattern = re.compile(
        r"SCALA_WORKSPACE/\.scala-build/"
        rf"{re.escape(runtime_build)}_jmh_[0-9a-f]{{10}}/classes/main"
    )
    runtime_class_ids = [
        item.get("pathId")
        for item in runtime_entries
        if isinstance(item, dict)
        and runtime_class_pattern.fullmatch(
            str(item.get("pathId"))
        )
        is not None
    ]
    if len(runtime_class_ids) != 1:
        raise PrecompileError(invalid)
    runtime_class_id = runtime_class_ids[0]

    seen: set[str] = set()
    for item in runtime_entries:
        if (
            not isinstance(item, dict)
            or set(item)
            != {"pathId", "kind", "sha256", "identitySha256"}
            or not isinstance(item.get("pathId"), str)
            or item["pathId"] in seen
            or item.get("kind") not in {"file", "directory"}
            or SHA256_PATTERN.fullmatch(str(item.get("sha256"))) is None
            or SHA256_PATTERN.fullmatch(
                str(item.get("identitySha256"))
            )
            is None
        ):
            raise PrecompileError(invalid)
        seen.add(item["pathId"])

    precompile_entries_by_id = {
        item.get("pathId"): item
        for item in classpath_entries
        if isinstance(item, dict)
    }
    precompile_class = precompile_entries_by_id.get(
        scala_class_output_path_id
    )
    precompile_resource = precompile_entries_by_id.get(
        generated_resource_path_id
    )
    if (
        not isinstance(precompile_class, dict)
        or not isinstance(precompile_resource, dict)
    ):
        raise PrecompileError(invalid)
    expected_mappings = [
        {
            "role": "SCALA_CLASS_OUTPUT",
            "precompilePathId": scala_class_output_path_id,
            "runtimePathId": runtime_class_id,
            "sha256": precompile_class["sha256"],
        },
        {
            "role": "JMH_GENERATED_SOURCES",
            "precompilePathId": jmh_generator[
                "generatedSourceRootPathId"
            ],
            "runtimePathId": runtime_source_id,
            "sha256": generated_sources_sha256,
        },
        {
            "role": "JMH_GENERATED_RESOURCES",
            "precompilePathId": generated_resource_path_id,
            "runtimePathId": runtime_resource_id,
            "sha256": precompile_resource["sha256"],
        },
    ]
    if value.get("roleMappings") != expected_mappings:
        raise PrecompileError(invalid)

    post_entries = classpath_post_run["entries"]
    for precompile_item, post_item, runtime_item in zip(
        classpath_entries,
        post_entries,
        runtime_entries,
        strict=True,
    ):
        if precompile_item["pathId"] == scala_class_output_path_id:
            expected_path_id = runtime_class_id
            expected_identity = runtime_item.get("identitySha256")
        elif precompile_item["pathId"] == generated_resource_path_id:
            expected_path_id = runtime_resource_id
            expected_identity = runtime_item.get("identitySha256")
        else:
            expected_path_id = precompile_item["pathId"]
            expected_identity = post_item["postRunIdentitySha256"]
        if (
            runtime_item.get("pathId") != expected_path_id
            or runtime_item.get("kind") != precompile_item["kind"]
            or runtime_item.get("sha256") != precompile_item["sha256"]
            or runtime_item.get("identitySha256") != expected_identity
        ):
            raise PrecompileError(invalid)


def _strict_json_payload(payload: bytes) -> Any:
    """한 번 snapshot한 bytes만 duplicate-key/finite JSON 규칙으로 해석한다."""

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise PrecompileError(f"DUPLICATE_JSON_KEY:{key}")
            value[key] = item
        return value

    return json.loads(
        payload.decode("utf-8"),
        object_pairs_hook=unique_object,
        parse_constant=lambda value: (_ for _ in ()).throw(
            PrecompileError(f"NONFINITE_JSON:{value}")
        ),
    )


def _strict_json_value(path: Path) -> Any:
    return _strict_json_payload(path.read_bytes())


def _strict_json(path: Path) -> dict[str, Any]:
    parsed = _strict_json_value(path)
    if not isinstance(parsed, dict):
        raise PrecompileError(f"JSON_OBJECT_REQUIRED:{path}")
    return parsed


def _replace_json_object_atomically(
    path: Path,
    value: dict[str, Any],
    *,
    expected_snapshot: RegularFileSnapshot,
) -> RegularFileSnapshot:
    """검증한 receipt inode를 exact canonical JSON으로 원자 교체하고 다시 봉인한다."""

    parent = _require_absolute_directory(
        path.parent,
        label="PRECOMPILE_RECEIPT_PARENT",
    )
    if (
        expected_snapshot.path != path
        or expected_snapshot.payload is None
        or path.parent != parent
    ):
        raise PrecompileError("PRECOMPILE_RECEIPT_FINALIZATION_FAILED")
    try:
        payload = (
            json.dumps(
                value,
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise PrecompileError(
            "PRECOMPILE_RECEIPT_FINALIZATION_FAILED"
        ) from error

    temporary = parent / (
        f".{path.name}.tmp-{os.getpid()}-{os.urandom(16).hex()}"
    )
    descriptor: int | None = None
    directory_descriptor: int | None = None
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | os.O_NOFOLLOW
            | os.O_CLOEXEC,
            0o600,
        )
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                raise PrecompileError(
                    "PRECOMPILE_RECEIPT_FINALIZATION_FAILED"
                )
            offset += written
        os.fchmod(
            descriptor,
            stat.S_IMODE(expected_snapshot.file_identity[2]),
        )
        os.fsync(descriptor)
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_size != len(payload)
        ):
            raise PrecompileError(
                "PRECOMPILE_RECEIPT_FINALIZATION_FAILED"
            )
        os.close(descriptor)
        descriptor = None

        _verify_regular_file_snapshot(
            expected_snapshot,
            label="PRECOMPILE_RECEIPT",
        )
        os.replace(temporary, path)
        directory_descriptor = os.open(
            parent,
            os.O_RDONLY
            | os.O_DIRECTORY
            | os.O_NOFOLLOW
            | os.O_CLOEXEC,
        )
        os.fsync(directory_descriptor)
        finalized = _snapshot_regular_file(
            path,
            label="PRECOMPILE_RECEIPT_FINAL",
        )
        if (
            finalized.payload != payload
            or finalized.sha256
            != hashlib.sha256(payload).hexdigest()
            or stat.S_IMODE(finalized.file_identity[2])
            != stat.S_IMODE(expected_snapshot.file_identity[2])
        ):
            raise PrecompileError(
                "PRECOMPILE_RECEIPT_FINALIZATION_FAILED"
            )
        return finalized
    except OSError as error:
        raise PrecompileError(
            "PRECOMPILE_RECEIPT_FINALIZATION_FAILED"
        ) from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if directory_descriptor is not None:
            os.close(directory_descriptor)
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _normalized_exec_path(path: Path) -> Path:
    match = PROC_FD_PATTERN.fullmatch(str(path))
    if match is not None and match.group("pid") == "self":
        return Path(f"/proc/{os.getpid()}/fd/{match.group('fd')}")
    return path


def _stat_identity(
    metadata: os.stat_result,
) -> tuple[int, int, int, int, int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_nlink,
        metadata.st_uid,
        metadata.st_gid,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _sha256_fd(fd: int) -> str:
    digest = hashlib.sha256()
    offset = 0
    while True:
        block = os.pread(fd, 1024 * 1024, offset)
        if not block:
            break
        digest.update(block)
        offset += len(block)
    return digest.hexdigest()


def _process_identity(pid: int) -> tuple[int, int, int]:
    """proc owner의 parent/start-time/uid를 PID 재사용과 함께 고정한다."""

    try:
        proc_root = Path(f"/proc/{pid}")
        proc_metadata = os.stat(proc_root, follow_symlinks=False)
        raw_stat = (proc_root / "stat").read_text(encoding="utf-8")
        closing_parenthesis = raw_stat.rfind(")")
        if (
            closing_parenthesis < 0
            or not raw_stat.startswith(f"{pid} (")
        ):
            raise ValueError("invalid proc stat")
        fields = raw_stat[closing_parenthesis + 2 :].split()
        parent_pid = int(fields[1])
        start_time = int(fields[19])
    except (IndexError, OSError, ValueError) as error:
        raise PrecompileError("PROC_FD_OWNER_IDENTITY_INVALID") from error
    return parent_pid, start_time, proc_metadata.st_uid


def _ancestor_process_identities() -> dict[int, tuple[int, int]]:
    """현재 helper와 실제 ancestor만 parent-sealed FD owner로 허용한다."""

    ancestors: dict[int, tuple[int, int]] = {}
    pid = os.getpid()
    for _ in range(256):
        if pid <= 0 or pid in ancestors:
            break
        parent_pid, start_time, uid = _process_identity(pid)
        ancestors[pid] = (start_time, uid)
        if parent_pid == pid:
            break
        pid = parent_pid
    return ancestors


def _executable_identity(
    path: Path,
    *,
    expected_sha256: str,
) -> ExecutableIdentity:
    """Regular pathname 또는 exact ancestor proc FD를 fstat/hash로 검증한다."""

    if not path.is_absolute():
        raise PrecompileError("EXECUTION_PATH_NOT_ABSOLUTE")
    proc_match = PROC_FD_PATTERN.fullmatch(str(path))
    proc_owner_pid: int | None = None
    proc_owner_start_time: int | None = None
    proc_owner_uid: int | None = None
    if proc_match is not None:
        if proc_match.group("pid") == "self":
            raise PrecompileError("PROC_FD_PATH_NOT_NORMALIZED")
        proc_owner_pid = int(proc_match.group("pid"))
        ancestor_identities = _ancestor_process_identities()
        owner_identity = ancestor_identities.get(proc_owner_pid)
        if owner_identity is None or owner_identity[1] != os.geteuid():
            raise PrecompileError("PROC_FD_OWNER_IDENTITY_INVALID")
        proc_owner_start_time, proc_owner_uid = owner_identity
        owner_before = _process_identity(proc_owner_pid)
        if owner_before[1:] != (proc_owner_start_time, proc_owner_uid):
            raise PrecompileError("PROC_FD_OWNER_IDENTITY_INVALID")
        open_flags = os.O_RDONLY | os.O_CLOEXEC
    else:
        if (
            path.is_symlink()
            or not path.is_file()
            or path.resolve(strict=True) != path
        ):
            raise PrecompileError("REGULAR_EXECUTION_PATH_INVALID")
        open_flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC

    try:
        fd = os.open(path, open_flags)
    except OSError as error:
        raise PrecompileError("EXECUTION_PATH_OPEN_FAILED") from error
    try:
        before = os.fstat(fd)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_mode & 0o111 == 0
            or not os.access(path, os.X_OK)
        ):
            raise PrecompileError("EXECUTION_FILE_INVALID")
        digest = _sha256_fd(fd)
        after = os.fstat(fd)
        try:
            path_metadata = os.stat(path, follow_symlinks=True)
        except OSError as error:
            raise PrecompileError("EXECUTION_PATH_STAT_FAILED") from error
        if (
            digest != expected_sha256
            or _stat_identity(before) != _stat_identity(after)
            or _stat_identity(after) != _stat_identity(path_metadata)
        ):
            raise PrecompileError("EXECUTION_FILE_IDENTITY_MISMATCH")
    finally:
        os.close(fd)

    if proc_owner_pid is not None:
        owner_after = _process_identity(proc_owner_pid)
        if owner_after[1:] != (proc_owner_start_time, proc_owner_uid):
            raise PrecompileError("PROC_FD_OWNER_IDENTITY_INVALID")
    return ExecutableIdentity(
        path=path,
        expected_sha256=expected_sha256,
        file_identity=_stat_identity(after),
        proc_owner_pid=proc_owner_pid,
        proc_owner_start_time=proc_owner_start_time,
        proc_owner_uid=proc_owner_uid,
    )


def _verify_executable_stability(
    identity: ExecutableIdentity,
    *,
    label: str,
) -> None:
    """실행 전 snapshot과 실행 후 proc owner/inode/bytes가 동일함을 확인한다."""

    try:
        current = _executable_identity(
            identity.path,
            expected_sha256=identity.expected_sha256,
        )
    except PrecompileError as error:
        raise PrecompileError(
            f"{label}_EXECUTION_POST_EXEC_IDENTITY_MISMATCH"
        ) from error
    if current != identity:
        raise PrecompileError(
            f"{label}_EXECUTION_POST_EXEC_IDENTITY_MISMATCH"
        )


def _verified_executable(
    *,
    binary: Path,
    execution_path: Path,
    expected_sha256: str,
    label: str,
) -> tuple[Path, str, ExecutableIdentity]:
    binary = _require_absolute_regular(binary, label=f"{label}_BINARY")
    if (
        not os.access(binary, os.X_OK)
        or SHA256_PATTERN.fullmatch(expected_sha256) is None
        or sha256_file(binary) != expected_sha256
    ):
        raise PrecompileError(f"{label}_BINARY_IDENTITY_MISMATCH")
    execution_path = _normalized_exec_path(execution_path)
    try:
        identity = _executable_identity(
            execution_path,
            expected_sha256=expected_sha256,
        )
    except PrecompileError as error:
        raise PrecompileError(
            f"{label}_EXECUTION_IDENTITY_MISMATCH"
        ) from error
    if identity.file_identity != _stat_identity(
        os.stat(binary, follow_symlinks=False)
    ):
        raise PrecompileError(f"{label}_EXECUTION_IDENTITY_MISMATCH")
    execution_id = (
        f"PINNED_{label}_FD"
        if PROC_FD_PATTERN.fullmatch(str(execution_path)) is not None
        else label
    )
    return execution_path, execution_id, identity


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
                f"EVIDENCE_ROOT_GENERATED/"
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
    for item in files:
        internal_name = item.relative_path.removesuffix(".class").encode(
            "utf-8"
        )
        if (
            len(item.payload) < 8
            or item.payload[:8] != b"\xca\xfe\xba\xbe\x00\x00\x00\x45"
            or internal_name not in item.payload
        ):
            raise PrecompileError("GENERATED_CLASS_MAGIC_INVALID")
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
    (
        scala_cli_exec,
        scala_cli_execution_id,
        scala_cli_identity,
    ) = _verified_executable(
        binary=arguments.scala_cli_binary,
        execution_path=arguments.scala_cli_exec,
        expected_sha256=scala_cli_sha256,
        label="SCALA_CLI_1_15_0",
    )
    javac_exec, javac_execution_id, javac_identity = _verified_executable(
        binary=arguments.javac_binary,
        execution_path=arguments.javac_exec,
        expected_sha256=javac_sha256,
        label="JAVAC",
    )
    jdk_modules = arguments.javac_binary.parent.parent / "lib/modules"
    jdk_modules_snapshot = _jdk_modules_snapshot(
        jdk_modules,
        label="JDK_MODULES",
    )
    if (
        jdk_modules_path_id != "TEMURIN_25_0_3_9_LTS/lib/modules"
        or not jdk_modules.is_absolute()
        or jdk_modules.is_symlink()
        or not jdk_modules.is_file()
        or jdk_modules.resolve(strict=True) != jdk_modules
        or jdk_modules_snapshot.sha256 != jdk_modules_sha256
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
    _verify_executable_stability(
        scala_cli_identity,
        label="SCALA_CLI_1_15_0",
    )
    _verify_executable_stability(javac_identity, label="JAVAC")
    _verify_regular_file_identity(
        jdk_modules_snapshot,
        label="JDK_MODULES",
    )
    classpath = classpath_closure(
        stdout_path.read_text(encoding="utf-8"),
        workspace=workspace,
        coursier_cache=coursier_cache,
        evidence_dir=evidence_dir,
    )
    workspace_generated_sources = generated_source_closure_at(
        classpath.generated_source_root,
        workspace=workspace,
    )
    generated_sources = _copy_generated_sources(
        workspace_generated_sources,
        evidence_dir=evidence_dir,
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
    _verify_executable_stability(javac_identity, label="JAVAC")
    _verify_regular_file_identity(
        jdk_modules_snapshot,
        label="JDK_MODULES",
    )
    if (
        generated_source_closure_at(
            workspace_generated_sources.root,
            workspace=workspace,
        )
        != workspace_generated_sources
        or generated_source_closure_at(
            generated_sources.root,
            evidence_dir=evidence_dir,
        )
        != generated_sources
    ):
        raise PrecompileError("GENERATED_SOURCE_DURING_JAVAC_DRIFT")
    generated_classes = _generated_class_closure(destination)
    if sha256_file(arguments.javac_binary) != javac_sha256:
        raise PrecompileError("JAVAC_BINARY_POST_EXEC_IDENTITY_MISMATCH")
    try:
        _verify_jdk_modules_snapshot(
            jdk_modules_snapshot,
            label="JDK_MODULES",
        )
    except PrecompileError as error:
        raise PrecompileError(
            "JDK_MODULES_POST_EXEC_IDENTITY_MISMATCH"
        ) from error
    # javac이 외부 directory를 채운 뒤의 최종 bytes를 receipt classpath에
    # 기록해야 post-run 검증이 의도된 변경을 drift로 오인하지 않는다.
    refreshed_entries: list[ClasspathEntry] = []
    for item in classpath.entries:
        entry_kind, entry_sha256, identity_sha256 = _entry_digest(item.path)
        if (
            entry_kind != item.kind
            or (
                item.path != destination
                and (
                    entry_sha256 != item.sha256
                    or identity_sha256 != item.identity_sha256
                )
            )
        ):
            raise PrecompileError("CLASSPATH_DURING_JAVAC_DRIFT")
        refreshed_entries.append(
            ClasspathEntry(
                path=item.path,
                path_id=item.path_id,
                kind=entry_kind,
                sha256=entry_sha256,
                identity_sha256=identity_sha256,
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
            "identitySha256": item.identity_sha256,
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
            "jdkModulesFileIdentity": _file_identity_value(
                jdk_modules_snapshot.file_identity
            ),
        },
        "scalaCompile": {
            "portableArgv": compile_portable,
            "portableArgvSha256": canonical_sha256(compile_portable),
            "runtimeArgvSha256": canonical_sha256(
                [scala_cli_execution_id, *compile_portable[1:]]
            ),
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
            f"EVIDENCE_ROOT/{GENERATED_SOURCES_NAME}"
        ),
        "generatedSources": source_values,
        "generatedSourcesSha256": canonical_sha256(source_values),
        "generatedSourcesIdentitySha256": _file_identity_sha256(
            generated_sources.files
        ),
        "classpathEntries": classpath_values,
        "classpathEntriesSha256": canonical_sha256(classpath_values),
        "runtimeClasspathSha256": classpath.runtime_classpath_sha256,
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
        "generatedClassesIdentitySha256": _file_identity_sha256(
            generated_classes
        ),
        "javacProcess": {
            "portableArgv": javac_portable,
            "portableArgvSha256": canonical_sha256(javac_portable),
            "runtimeArgvSha256": canonical_sha256(
                [javac_execution_id, *javac_portable[1:]]
            ),
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
    compile_stdout = _require_absolute_regular(
        evidence_dir / SCALA_COMPILE_STDOUT,
        label="SCALA_COMPILE_STDOUT",
    )
    fork_evidence_path = _require_absolute_regular(
        arguments.fork_evidence,
        label="JVM_FORK_EVIDENCE",
    )
    receipt_path = _require_absolute_regular(
        evidence_dir / RECEIPT_NAME,
        label="PRECOMPILE_RECEIPT",
    )
    receipt_snapshot = _snapshot_regular_file(
        receipt_path,
        label="PRECOMPILE_RECEIPT",
    )
    receipt_value = _strict_json_payload(receipt_snapshot.payload or b"")
    if not isinstance(receipt_value, dict):
        raise PrecompileError("PRECOMPILE_RECEIPT_INVALID")
    receipt = receipt_value
    if (
        receipt.get("schemaVersion")
        != "s1.4x-scala-jmh-generated-java-precompile-v1"
        or receipt.get("status") != "PASS"
        or receipt.get("aggregateStatus") != "PASS"
        or "classpathPostRun" in receipt
        or "classpathPostRunSha256" in receipt
        or "jmhRuntimeClosure" in receipt
        or "jmhRuntimeClosureSha256" in receipt
        or "precompileRuntimeClasspathSha256" in receipt
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
            "jdkModulesFileIdentity",
        }
        or javac.get("pathId") != "TEMURIN_25_0_3_9_LTS/bin/javac"
        or javac.get("jdkModulesPathId")
        != "TEMURIN_25_0_3_9_LTS/lib/modules"
        or SHA256_PATTERN.fullmatch(str(javac.get("binarySha256"))) is None
        or SHA256_PATTERN.fullmatch(str(javac.get("jdkModulesSha256")))
        is None
    ):
        raise PrecompileError("PRECOMPILE_JAVAC_RECEIPT_INVALID")
    javac_exec, javac_execution_id, javac_identity = _verified_executable(
        binary=arguments.javac_binary,
        execution_path=arguments.javac_exec,
        expected_sha256=javac["binarySha256"],
        label="JAVAC",
    )
    modules = arguments.javac_binary.parent.parent / "lib/modules"
    modules_snapshot = _jdk_modules_snapshot(
        modules,
        label="JDK_MODULES",
    )
    if (
        javac.get("executionPathId") != javac_execution_id
        or modules.is_symlink()
        or not modules.is_file()
        or modules.resolve(strict=True) != modules
        or modules_snapshot.sha256 != javac["jdkModulesSha256"]
        or _file_identity_value(modules_snapshot.file_identity)
        != javac.get("jdkModulesFileIdentity")
    ):
        raise PrecompileError("JDK_COMPILER_POST_RUN_DRIFT")
    try:
        _verify_executable_stability(javac_identity, label="JAVAC")
    except PrecompileError as error:
        raise PrecompileError("JDK_COMPILER_POST_RUN_DRIFT") from error

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
        evidence_dir=evidence_dir,
    )
    source_values = _file_digest_values(sources.files)
    if (
        receipt.get("generatedSources") != source_values
        or receipt.get("generatedSourcesSha256")
        != canonical_sha256(source_values)
        or receipt.get("generatedSourcesIdentitySha256")
        != _file_identity_sha256(sources.files)
    ):
        raise PrecompileError("GENERATED_SOURCE_POST_RUN_DRIFT")

    destination = _require_absolute_directory(
        evidence_dir / GENERATED_CLASSES_NAME,
        label="GENERATED_CLASS_OUTPUT",
    )
    generated_class_closure = _generated_class_closure(destination)
    class_values = _file_digest_values(generated_class_closure)
    if (
        receipt.get("generatedClassOutputPathId")
        != f"EVIDENCE_ROOT/{GENERATED_CLASSES_NAME}"
        or receipt.get("generatedClasses") != class_values
        or receipt.get("generatedClassesSha256")
        != canonical_sha256(class_values)
        or receipt.get("generatedClassesIdentitySha256")
        != _file_identity_sha256(generated_class_closure)
    ):
        raise PrecompileError("GENERATED_CLASS_POST_RUN_DRIFT")

    classpath_values = receipt.get("classpathEntries")
    if (
        not isinstance(classpath_values, list)
        or not classpath_values
        or receipt.get("classpathEntriesSha256")
        != canonical_sha256(classpath_values)
    ):
        raise PrecompileError("CLASSPATH_POST_RUN_DRIFT")
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
        or generator.get("processedClassCount")
        != EXPECTED_JMH_PROCESSED_CLASS_COUNT
        or not str(generator.get("generatedSourceRootPathId", "")).startswith(
            "SCALA_WORKSPACE/.scala-build/"
        )
        or generated_source_root_id
        != f"EVIDENCE_ROOT/{GENERATED_SOURCES_NAME}"
    ):
        raise PrecompileError("JMH_GENERATOR_POST_RUN_DRIFT")
    classpath_post_run = capture_classpath_post_run(
        classpath_values,
        scala_class_output_path_id=class_output_id,
        generated_resource_path_id=generator[
            "generatedResourceRootPathId"
        ],
        workspace=workspace,
        coursier_cache=coursier_cache,
        evidence_dir=evidence_dir,
    )
    compile_raw = compile_stdout.read_text(encoding="utf-8")
    jmh_raw = jmh_stdout.read_text(encoding="utf-8")
    runtime_classpath = require_jmh_stdout_binding(
        compile_raw,
        jmh_raw,
        workspace=workspace,
        coursier_cache=coursier_cache,
        evidence_dir=evidence_dir,
    )
    runtime_generator_values = _generator_evidence_value(
        runtime_classpath,
        workspace=workspace,
        coursier_cache=coursier_cache,
        evidence_dir=evidence_dir,
    )
    if (
        runtime_generator_values["generatorId"]
        != generator["generatorId"]
        or runtime_generator_values["processedClassCount"]
        != generator["processedClassCount"]
        or runtime_generator_values["classInputClosureSha256"]
        != generator["classInputClosureSha256"]
        or runtime_generator_values[
            "generatedResourceClosureSha256"
        ]
        != generator["generatedResourceClosureSha256"]
    ):
        raise PrecompileError("JMH_GENERATOR_POST_RUN_DRIFT")
    actual_sources = generated_source_closure_at(
        runtime_classpath.generated_source_root,
        workspace=workspace,
    )
    if _file_digest_values(actual_sources.files) != source_values:
        raise PrecompileError("GENERATED_SOURCE_POST_RUN_DRIFT")

    precompile_classpath = classpath_closure(
        compile_raw,
        workspace=workspace,
        coursier_cache=coursier_cache,
        evidence_dir=evidence_dir,
    )
    precompile_generator_values = _generator_evidence_value(
        precompile_classpath,
        workspace=workspace,
        coursier_cache=coursier_cache,
        evidence_dir=evidence_dir,
    )
    if precompile_generator_values != generator:
        raise PrecompileError("JMH_GENERATOR_POST_RUN_DRIFT")
    actual_classpath_values = _classpath_evidence_values(
        precompile_classpath
    )
    expected_classpath_values = classpath_post_run["entries"]
    if len(actual_classpath_values) != len(expected_classpath_values):
        raise PrecompileError("JMH_RUN_CLASSPATH_DRIFT")
    for expected_item, actual_item in zip(
        expected_classpath_values,
        actual_classpath_values,
        strict=True,
    ):
        if (
            expected_item["pathId"] != actual_item["pathId"]
            or expected_item["kind"] != actual_item["kind"]
            or expected_item["sha256"] != actual_item["sha256"]
            or expected_item["postRunIdentitySha256"]
            != actual_item["identitySha256"]
        ):
            raise PrecompileError("JMH_RUN_CLASSPATH_DRIFT")
    actual_class_output_id = _portable_path_id(
        precompile_classpath.class_output,
        workspace=workspace,
        coursier_cache=coursier_cache,
        evidence_dir=evidence_dir,
    )
    if (
        actual_class_output_id != receipt.get("scalaClassOutputPathId")
        or receipt.get("runtimeClasspathSha256")
        != precompile_classpath.runtime_classpath_sha256
    ):
        raise PrecompileError("JMH_RUN_CLASSPATH_DRIFT")

    runtime_evidence = create_jmh_runtime_closure_evidence(
        precompile_classpath=precompile_classpath,
        runtime_classpath=runtime_classpath,
        generated_sources_sha256=receipt["generatedSourcesSha256"],
        workspace=workspace,
        coursier_cache=coursier_cache,
        evidence_dir=evidence_dir,
    )
    validate_jmh_runtime_closure_evidence(
        runtime_evidence,
        classpath_entries=classpath_values,
        classpath_post_run=classpath_post_run,
        scala_class_output_path_id=class_output_id,
        jmh_generator=generator,
        generated_sources_sha256=receipt["generatedSourcesSha256"],
    )
    forks = _strict_json_value(fork_evidence_path)
    require_runtime_classpath_evidence(
        runtime_classpath.runtime_classpath_sha256,
        forks,
    )
    try:
        _verify_jdk_modules_snapshot(
            modules_snapshot,
            label="JDK_MODULES",
        )
    except PrecompileError as error:
        raise PrecompileError("JDK_COMPILER_POST_RUN_DRIFT") from error

    receipt["precompileRuntimeClasspathSha256"] = receipt[
        "runtimeClasspathSha256"
    ]
    receipt["runtimeClasspathSha256"] = (
        runtime_classpath.runtime_classpath_sha256
    )
    receipt["jmhRuntimeClosure"] = runtime_evidence
    receipt["jmhRuntimeClosureSha256"] = canonical_sha256(
        runtime_evidence
    )
    receipt["classpathPostRun"] = classpath_post_run
    receipt["classpathPostRunSha256"] = canonical_sha256(
        classpath_post_run
    )
    finalized_receipt = _replace_json_object_atomically(
        receipt_path,
        receipt,
        expected_snapshot=receipt_snapshot,
    )
    return {
        "status": "PASS",
        "receiptSha256": finalized_receipt.sha256,
        "generatedSourcesSha256": receipt["generatedSourcesSha256"],
        "generatedClassesSha256": receipt["generatedClassesSha256"],
        "classpathEntriesSha256": receipt["classpathEntriesSha256"],
        "runtimeClasspathSha256": receipt["runtimeClasspathSha256"],
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
        "--fork-evidence",
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
