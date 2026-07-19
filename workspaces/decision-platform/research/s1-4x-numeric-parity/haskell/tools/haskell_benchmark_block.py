#!/bin/false
"""Frozen Haskell Criterion family를 실행하고 shared native evidence pipeline에 연결한다."""

from __future__ import annotations

import argparse
import ctypes
import fcntl
import hashlib
import importlib
import importlib.metadata
import json
import os
import re
import stat
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence


class BlockError(RuntimeError):
    """Benchmark block의 frozen 입력, 실행 또는 shared evidence가 유효하지 않음."""


SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
RAW_RELATIVE = "raw/criterion-family.json"
RECEIPT_RELATIVE = "receipts/criterion-family.json"
LEDGER_RELATIVE = "input-ledger.json"
NATIVE_CONTRACT_RELATIVE = "native-contract-validation.json"
NATIVE_STATISTICS_RELATIVE = "native-statistics.json"
NATIVE_RELATIVE = "native.json"
BLOCK_RESULT_RELATIVE = "block-result.json"
RUNTIME_IDENTITY_RELATIVE = "benchmark-runtime-identity.json"
GHC_INSTALL_CLOSURE_RELATIVE = "authoritative-ghc-install-closure.json"
THREAD_ENVIRONMENT = {
    "OMP_NUM_THREADS": "1",
    "OPENBLAS_NUM_THREADS": "1",
    "MKL_NUM_THREADS": "1",
    "VECLIB_MAXIMUM_THREADS": "1",
    "NUMEXPR_NUM_THREADS": "1",
    "JAX_NUM_THREADS": "1",
    "XLA_FLAGS": "--xla_cpu_multi_thread_eigen=false intra_op_parallelism_threads=1",
    "S1_4X_THREAD_COUNT": "1",
}
PINNED_TOOL_SHA256 = {
    "S1_4X_GHCUP": (
        "9ed5da5449b48043a0d17e767c05d2ef585e25a639bb934329496c6d2fad9cf8"
    ),
    "S1_4X_STACK": (
        "923dbd137756652c67b376e2447c655b87fcc373f4d104b5073bca913471ecbe"
    ),
    "S1_4X_AUTHORITATIVE_GHC": (
        "d0c0dd79a1bcc5dce3c9e73613c1be51f61b78d5ef7c0970ffe9f142a90a5e2c"
    ),
    "S1_4X_LATEST_GHC": (
        "ecfd54b4161699f574d2b163bdc817c54df08a08a310323e43b41ab5fc413ef1"
    ),
    "S1_4X_HLINT": (
        "3ff3fb4b571876d668ddf4ad0245769c19a640283fabb0c2629038aa34197f62"
    ),
    "S1_4X_STYLISH": (
        "385dc27bc2d0fb654e76ecadfb57bc0b7e1c58afe74f19923e20b696e6fe0d7b"
    ),
}
AUTHORITATIVE_GHC_VERSION = "9.10.3"
AUTHORITATIVE_GHC_COMPILER_ELF_SHA256 = (
    "560b354d05aa626c66f6d9ef04139c3746e4000acaf272667c393ce86336a45f"
)
AUTHORITATIVE_GHC_AUXILIARY_ELF_SHA256 = {
    "ghc-pkg": (
        "03792b0e7f08aed4553564271753cc290ddff649171c8f2b87822a6e32def7b7"
    ),
    "runghc": (
        "9c99cc70faf68c180d27ab2c8436e4415b13a920f0912327bda8a42e8b9bf339"
    ),
    "haddock": (
        "bbb8c400078b913fe1f13e126da6724ec06fd5dd7e7db36e761e8ee72c8f6984"
    ),
}
_LINUX_MFD_CLOEXEC = 0x0001
_LINUX_MFD_ALLOW_SEALING = 0x0002
_LINUX_F_ADD_SEALS = 1033
_LINUX_F_GET_SEALS = 1034
_LINUX_F_SEAL_SEAL = 0x0001
_LINUX_F_SEAL_SHRINK = 0x0002
_LINUX_F_SEAL_GROW = 0x0004
_LINUX_F_SEAL_WRITE = 0x0008
_LINUX_REQUIRED_MEMFD_SEALS = (
    _LINUX_F_SEAL_SEAL
    | _LINUX_F_SEAL_SHRINK
    | _LINUX_F_SEAL_GROW
    | _LINUX_F_SEAL_WRITE
)


@dataclass(frozen=True)
class RegularFileSnapshot:
    """한 secure FD에서 얻은 bytes와 그 bytes의 digest."""

    path: Path
    payload: bytes
    sha256: str
    mode: int
    identity: tuple[int, int, int, int, int, int]


@dataclass(frozen=True)
class JsonSnapshot:
    """한 secure FD의 JSON bytes, digest, strict parse 결과."""

    path: Path
    payload: bytes
    sha256: str
    document: Any


@dataclass(frozen=True)
class PinnedExecutable:
    """Shared runtime이 연 FD와 원래 provenance path를 분리해 보존한다."""

    label: str
    source_path: Path
    fd_path: Path
    descriptor: int
    sha256: str
    mode: int
    identity: tuple[int, int, int, int, int, int]


@dataclass(frozen=True)
class BenchmarkPythonRuntime:
    """Outer pin snapshot과 venv/dependency route를 benchmark 끝까지 보존한다."""

    source_path: Path
    fd_path: Path
    descriptor: int
    sha256: str
    mode: int
    identity: tuple[int, int, int, int, int, int]
    configuration_path: Path
    configuration_sha256: str
    configuration_identity: tuple[int, int, int, int, int, int, int]
    dependency_closure: tuple[str, str, str]


@dataclass(frozen=True)
class PinnedRegularFile:
    """Repo source bytes를 sealed FD로 복제해 pathname 교체와 수정을 차단한다."""

    label: str
    source_path: Path
    fd_path: Path
    descriptor: int
    sha256: str
    mode: int
    identity: tuple[int, int, int, int, int, int]


@dataclass(frozen=True)
class PinnedJsonEvidence:
    """Source pathname을 재개방하지 않고 전달된 FD bytes만 parse/hash한다."""

    label: str
    source_path: Path
    fd_path: Path
    descriptor: int
    payload: bytes
    sha256: str
    document: Any
    identity: tuple[int, int, int, int, int, int]


@dataclass(frozen=True)
class ToolchainTreeMetadataSnapshot:
    """설치 tree의 path/type/stat closure를 bytes 실행 없이 고정한다."""

    path: Path
    sha256: str
    entry_count: int
    total_file_bytes: int


@dataclass(frozen=True)
class AuthoritativeGhcClosure:
    """승인 wrapper와 실제 compiler/aux ELF, libdir, output shim을 함께 고정한다."""

    install_root: Path
    wrapper: PinnedExecutable
    compiler_elf: PinnedExecutable
    auxiliary_elves: tuple[tuple[str, PinnedExecutable], ...]
    libdir_path: Path
    distribution_bin_path: Path
    libdir_snapshot: ToolchainTreeMetadataSnapshot
    distribution_bin_snapshot: ToolchainTreeMetadataSnapshot
    tool_path: str
    ghc_shim: Path
    pinned_launchers: tuple[PinnedRegularFile, ...]

    @property
    def pinned_executables(self) -> tuple[PinnedExecutable, ...]:
        """Benchmark child에 상속해야 하는 실제 distribution ELF 집합이다."""

        return (
            self.compiler_elf,
            *(pinned for _, pinned in self.auxiliary_elves),
        )

    @property
    def pinned_objects(
        self,
    ) -> tuple[PinnedExecutable | PinnedRegularFile, ...]:
        """테스트/정리와 pre/post 검증이 공유하는 retained descriptor 집합이다."""

        return (*self.pinned_executables, *self.pinned_launchers)


def _require_linux_memfd_abi(*, label: str) -> None:
    """Python export 유무와 무관하게 사용하는 Linux memfd ABI가 일치해야 한다."""

    if (
        sys.platform != "linux"
        or os.name != "posix"
        or ctypes.sizeof(ctypes.c_int) != 4
        or ctypes.sizeof(ctypes.c_uint) != 4
        or not callable(getattr(fcntl, "fcntl", None))
    ):
        raise BlockError(f"{label}_MEMFD_ABI_UNAVAILABLE")
    expected_exports = (
        (os, "MFD_CLOEXEC", _LINUX_MFD_CLOEXEC),
        (os, "MFD_ALLOW_SEALING", _LINUX_MFD_ALLOW_SEALING),
        (fcntl, "F_ADD_SEALS", _LINUX_F_ADD_SEALS),
        (fcntl, "F_GET_SEALS", _LINUX_F_GET_SEALS),
        (fcntl, "F_SEAL_SEAL", _LINUX_F_SEAL_SEAL),
        (fcntl, "F_SEAL_SHRINK", _LINUX_F_SEAL_SHRINK),
        (fcntl, "F_SEAL_GROW", _LINUX_F_SEAL_GROW),
        (fcntl, "F_SEAL_WRITE", _LINUX_F_SEAL_WRITE),
    )
    for module, name, expected in expected_exports:
        if name not in vars(module):
            continue
        exported = getattr(module, name)
        if type(exported) is not int or exported != expected:
            raise BlockError(f"{label}_MEMFD_ABI_MISMATCH")


def _create_linux_memfd(*, name: str, label: str) -> int:
    """검증한 Linux ABI로 seal 허용 memfd를 만들고 wrapper 부재만 보완한다."""

    _require_linux_memfd_abi(label=label)
    flags = _LINUX_MFD_CLOEXEC | _LINUX_MFD_ALLOW_SEALING
    python_memfd_create = getattr(os, "memfd_create", None)
    if python_memfd_create is not None:
        if not callable(python_memfd_create):
            raise BlockError(f"{label}_MEMFD_ABI_MISMATCH")
        return python_memfd_create(name, flags=flags)
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        libc_memfd_create = libc.memfd_create
    except (AttributeError, OSError) as exc:
        raise BlockError(f"{label}_MEMFD_ABI_UNAVAILABLE") from exc
    libc_memfd_create.argtypes = [ctypes.c_char_p, ctypes.c_uint]
    libc_memfd_create.restype = ctypes.c_int
    descriptor = libc_memfd_create(name.encode("utf-8"), flags)
    if descriptor < 0:
        error_number = ctypes.get_errno()
        raise OSError(error_number, os.strerror(error_number))
    return descriptor


def _source_path_layout(value: str, *, label: str) -> Path:
    """원래 pathname은 provenance/layout만 검사하고 filesystem에는 접근하지 않는다."""

    if (
        type(value) is not str
        or not value.startswith("/")
        or "\0" in value
        or "\n" in value
        or ":" in value
        or "|" in value
        or "//" in value
        or "/./" in value
        or "/../" in value
        or value.endswith("/.")
        or value.endswith("/..")
    ):
        raise BlockError(f"{label}_SOURCE_PATH_LAYOUT_INVALID")
    path = Path(value)
    if not path.is_absolute() or any(part in {".", ".."} for part in path.parts):
        raise BlockError(f"{label}_SOURCE_PATH_LAYOUT_INVALID")
    return path


def _pinned_descriptor(path: Path, *, label: str) -> int:
    """현재 process의 inherited FD path만 허용하고 pathname open을 금지한다."""

    matched = re.fullmatch(r"/proc/self/fd/([0-9]+)", str(path))
    if matched is None:
        raise BlockError(f"{label}_PINNED_FD_PATH_INVALID")
    descriptor = int(matched.group(1))
    if descriptor < 3:
        raise BlockError(f"{label}_PINNED_FD_PATH_INVALID")
    return descriptor


def _read_pinned_fd(
    *,
    fd_path: Path,
    label: str,
    max_bytes: int,
    executable: bool,
    capture_payload: bool,
) -> tuple[
    int,
    bytes,
    str,
    int,
    tuple[int, int, int, int, int, int],
]:
    """Inherited descriptor를 pread해 parse/hash/exec identity를 같은 FD에 묶는다."""

    if type(max_bytes) is not int or max_bytes <= 0:
        raise BlockError(f"{label}_PINNED_FD_INPUT_INVALID")
    descriptor = _pinned_descriptor(fd_path, label=label)
    try:
        before = os.fstat(descriptor)
    except OSError as exc:
        raise BlockError(f"{label}_PINNED_FD_NOT_INHERITED") from exc
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_size < 0
        or before.st_size > max_bytes
        or (executable and before.st_mode & 0o111 == 0)
    ):
        raise BlockError(f"{label}_PINNED_FD_OBJECT_INVALID")
    digest = hashlib.sha256()
    chunks: list[bytes] = []
    offset = 0
    while offset < before.st_size:
        try:
            chunk = os.pread(
                descriptor,
                min(1024 * 1024, before.st_size - offset),
                offset,
            )
        except OSError as exc:
            raise BlockError(f"{label}_PINNED_FD_READ_FAILED") from exc
        if not chunk:
            raise BlockError(f"{label}_PINNED_FD_TRUNCATED")
        digest.update(chunk)
        if capture_payload:
            chunks.append(chunk)
        offset += len(chunk)
    try:
        after = os.fstat(descriptor)
    except OSError as exc:
        raise BlockError(f"{label}_PINNED_FD_NOT_INHERITED") from exc
    identity_fields = (
        "st_dev",
        "st_ino",
        "st_size",
        "st_mtime_ns",
        "st_ctime_ns",
        "st_nlink",
    )
    if any(
        getattr(before, field) != getattr(after, field)
        for field in identity_fields
    ):
        raise BlockError(f"{label}_PINNED_FD_CHANGED_DURING_READ")
    identity = tuple(getattr(before, field) for field in identity_fields)
    return (
        descriptor,
        b"".join(chunks),
        digest.hexdigest(),
        before.st_mode,
        identity,
    )


def pinned_executable_environment(
    prefix: str,
    *,
    label: str,
    required_sha256: str | None = None,
) -> PinnedExecutable:
    """Shared v3 executable triple을 검증하되 원래 pathname은 재개방하지 않는다."""

    if re.fullmatch(r"S1_4X_[A-Z0-9_]+", prefix) is None:
        raise BlockError(f"{label}_ENVIRONMENT_PREFIX_INVALID")
    source_path = _source_path_layout(
        _required_environment(f"{prefix}_BIN"),
        label=label,
    )
    fd_path = Path(_required_environment(f"{prefix}_PINNED_FD_PATH"))
    expected_sha256 = _required_environment(f"{prefix}_SHA256")
    if (
        SHA256_PATTERN.fullmatch(expected_sha256) is None
        or (
            required_sha256 is not None
            and expected_sha256 != required_sha256
        )
    ):
        raise BlockError(f"{label}_EXPECTED_SHA256_INVALID")
    descriptor, _, actual_sha256, mode, identity = _read_pinned_fd(
        fd_path=fd_path,
        label=label,
        max_bytes=1024 * 1024 * 1024,
        executable=True,
        capture_payload=False,
    )
    if actual_sha256 != expected_sha256:
        raise BlockError(f"{label}_SHA256_MISMATCH")
    return PinnedExecutable(
        label=label,
        source_path=source_path,
        fd_path=fd_path,
        descriptor=descriptor,
        sha256=actual_sha256,
        mode=mode,
        identity=identity,
    )


def _full_stat_identity(
    value: os.stat_result,
) -> tuple[int, int, int, int, int, int, int]:
    """Source/config route continuity에 쓰는 full stat identity다."""

    return (
        value.st_dev,
        value.st_ino,
        value.st_size,
        value.st_mode,
        value.st_mtime_ns,
        value.st_ctime_ns,
        value.st_nlink,
    )


def _shell_stat_identity(path: Path) -> str:
    """Outer Bash pin과 동일한 GNU stat 표현으로 initial snapshot을 비교한다."""

    matched = re.fullmatch(r"/proc/self/fd/([0-9]+)", str(path))
    pass_fds = () if matched is None else (int(matched.group(1)),)
    stat_environment = {"LC_ALL": "C", "PATH": "/usr/bin:/bin"}
    if "TZ" in os.environ:
        stat_environment["TZ"] = os.environ["TZ"]
    try:
        completed = subprocess.run(
            [
                "/usr/bin/stat",
                "-Lc",
                "%d:%i:%s:%f:%y:%z:%h",
                "--",
                str(path),
            ],
            env=stat_environment,
            check=False,
            capture_output=True,
            text=True,
            pass_fds=pass_fds,
        )
    except OSError as exc:
        raise BlockError("BENCHMARK_PYTHON_INITIAL_CLOSURE_STAT_FAILED") from exc
    if completed.returncode != 0 or completed.stderr or not completed.stdout:
        raise BlockError("BENCHMARK_PYTHON_INITIAL_CLOSURE_STAT_FAILED")
    return completed.stdout.rstrip("\n")


def _snapshot_benchmark_python_runtime(
    pinned: PinnedExecutable,
    *,
    require_current_process: bool,
) -> BenchmarkPythonRuntime:
    """Source/FD/config/dependency를 한 accepted venv route snapshot으로 묶는다."""

    source = pinned.source_path
    configuration = source.parent.parent / "pyvenv.cfg"
    try:
        source_before = os.lstat(source)
        configuration_before = os.lstat(configuration)
        if (
            source.resolve(strict=True) != source
            or configuration.resolve(strict=True) != configuration
        ):
            raise BlockError("BENCHMARK_PYTHON_ROUTE_NONCANONICAL")
        (
            descriptor,
            _,
            executable_sha256,
            executable_mode,
            executable_identity,
        ) = _read_pinned_fd(
            fd_path=pinned.fd_path,
            label="BENCHMARK_PYTHON",
            max_bytes=1024 * 1024 * 1024,
            executable=True,
            capture_payload=False,
        )
        configuration_payload = configuration.read_bytes()
        probe_code = (
            "import importlib.metadata, json, os, sys\n"
            "import jsonschema, numpy\n"
            "p=os.environ['S1_4X_BENCHMARK_PYTHON_PINNED_FD_PATH']\n"
            "d=int(p.rsplit('/',1)[1]); f=os.fstat(d)\n"
            "e=os.stat('/proc/self/exe')\n"
            "print(json.dumps({"
            "'implementation':sys.implementation.name,"
            "'version':list(sys.version_info[:3]),"
            "'executable':sys.executable,"
            "'prefix':sys.prefix,"
            "'basePrefix':sys.base_prefix,"
            "'jsonschema':importlib.metadata.version('jsonschema'),"
            "'numpy':importlib.metadata.version('numpy'),"
            "'numpyModule':numpy.__version__,"
            "'identityMatches':"
            "[f.st_dev,f.st_ino,f.st_size]=="
            "[e.st_dev,e.st_ino,e.st_size]"
            "},sort_keys=True))\n"
        )
        completed = subprocess.run(
            [str(source), "-B", "-I", "-c", probe_code],
            executable=str(pinned.fd_path),
            cwd=source.parent.parent,
            env={
                "LC_ALL": "C",
                "PATH": "/usr/bin:/bin",
                "S1_4X_BENCHMARK_PYTHON_BIN": str(source),
                "S1_4X_BENCHMARK_PYTHON_PINNED_FD_PATH": str(
                    pinned.fd_path
                ),
            },
            check=False,
            capture_output=True,
            text=True,
            pass_fds=(pinned.descriptor,),
        )
        source_after = os.lstat(source)
        configuration_after = os.lstat(configuration)
    except OSError as exc:
        raise BlockError("BENCHMARK_PYTHON_ROUTE_STAT_FAILED") from exc
    source_identity = _full_stat_identity(source_before)
    configuration_identity = _full_stat_identity(configuration_before)
    expected_executable_identity = (
        pinned.identity[0],
        pinned.identity[1],
        pinned.identity[2],
        pinned.mode,
        pinned.identity[3],
        pinned.identity[4],
        pinned.identity[5],
    )
    if (
        source.parent.name != "bin"
        or not stat.S_ISREG(source_before.st_mode)
        or source_before.st_mode & 0o111 == 0
        or not stat.S_ISREG(configuration_before.st_mode)
        or source_identity != expected_executable_identity
        or source_identity != _full_stat_identity(source_after)
        or configuration_identity
        != _full_stat_identity(configuration_after)
        or descriptor != pinned.descriptor
        or executable_mode != pinned.mode
        or executable_identity != pinned.identity
        or executable_sha256 != pinned.sha256
    ):
        raise BlockError("BENCHMARK_PYTHON_SOURCE_FD_MISMATCH")
    if completed.returncode != 0 or completed.stderr:
        raise BlockError("BENCHMARK_PYTHON_DEPENDENCY_CLOSURE_MISMATCH")
    try:
        probe = json.loads(completed.stdout)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise BlockError(
            "BENCHMARK_PYTHON_DEPENDENCY_CLOSURE_MISMATCH"
        ) from exc
    dependency_closure = (
        probe.get("jsonschema"),
        probe.get("numpy"),
        probe.get("numpyModule"),
    )
    if (
        set(probe)
        != {
            "implementation",
            "version",
            "executable",
            "prefix",
            "basePrefix",
            "jsonschema",
            "numpy",
            "numpyModule",
            "identityMatches",
        }
        or probe.get("implementation") != "cpython"
        or probe.get("version") != [3, 12, 13]
        or probe.get("executable") != str(source)
        or probe.get("prefix") != str(source.parent.parent)
        or probe.get("basePrefix") == probe.get("prefix")
        or dependency_closure != ("4.26.0", "2.5.1", "2.5.1")
        or probe.get("identityMatches") is not True
    ):
        raise BlockError("BENCHMARK_PYTHON_DEPENDENCY_CLOSURE_MISMATCH")
    if require_current_process:
        try:
            current = os.stat("/proc/self/exe")
            current_dependencies = (
                importlib.metadata.version("jsonschema"),
                importlib.metadata.version("numpy"),
                str(
                    getattr(
                        importlib.import_module("numpy"),
                        "__version__",
                        "",
                    )
                ),
            )
        except (
            ImportError,
            importlib.metadata.PackageNotFoundError,
            OSError,
        ) as exc:
            raise BlockError(
                "BENCHMARK_PYTHON_CURRENT_PROCESS_MISMATCH"
            ) from exc
        if (
            sys.implementation.name != "cpython"
            or sys.version_info[:3] != (3, 12, 13)
            or sys.executable != str(source)
            or Path(sys.prefix) != source.parent.parent
            or sys.prefix == sys.base_prefix
            or _full_stat_identity(current) != expected_executable_identity
            or current_dependencies != dependency_closure
        ):
            raise BlockError("BENCHMARK_PYTHON_CURRENT_PROCESS_MISMATCH")
    return BenchmarkPythonRuntime(
        source_path=source,
        fd_path=pinned.fd_path,
        descriptor=pinned.descriptor,
        sha256=pinned.sha256,
        mode=pinned.mode,
        identity=pinned.identity,
        configuration_path=configuration,
        configuration_sha256=hashlib.sha256(
            configuration_payload
        ).hexdigest(),
        configuration_identity=configuration_identity,
        dependency_closure=dependency_closure,
    )


def _benchmark_python_runtime() -> BenchmarkPythonRuntime:
    """Outer pin initial closure와 현재 helper process를 exact 비교한다."""

    pinned = pinned_executable_environment(
        "S1_4X_BENCHMARK_PYTHON",
        label="MARKER_PYTHON",
    )
    runtime = _snapshot_benchmark_python_runtime(
        pinned,
        require_current_process=True,
    )
    initial = {
        "route": os.environ.get(
            "S1_4X_BENCHMARK_PYTHON_INITIAL_ROUTE_IDENTITY"
        ),
        "configurationIdentity": os.environ.get(
            "S1_4X_BENCHMARK_PYTHON_INITIAL_VENV_IDENTITY"
        ),
        "configurationSha256": os.environ.get(
            "S1_4X_BENCHMARK_PYTHON_INITIAL_VENV_SHA256"
        ),
        "dependencies": os.environ.get(
            "S1_4X_BENCHMARK_PYTHON_INITIAL_DEPENDENCIES"
        ),
    }
    present = {name for name, value in initial.items() if value is not None}
    if present and present != set(initial):
        raise BlockError("BENCHMARK_PYTHON_INITIAL_CLOSURE_INCOMPLETE")
    if present and (
        initial["route"] != _shell_stat_identity(runtime.source_path)
        or initial["route"] != _shell_stat_identity(runtime.fd_path)
        or initial["configurationIdentity"]
        != _shell_stat_identity(runtime.configuration_path)
        or initial["configurationSha256"] != runtime.configuration_sha256
        or initial["dependencies"] != "|".join(runtime.dependency_closure)
    ):
        raise BlockError("BENCHMARK_PYTHON_INITIAL_CLOSURE_CHANGED")
    return runtime


def load_pinned_toolchain() -> tuple[PinnedExecutable, ...]:
    """Shared v3의 여섯 tool FD를 repo lock SHA와 함께 검증한다."""

    return tuple(
        pinned_executable_environment(
            prefix,
            label=label,
            required_sha256=PINNED_TOOL_SHA256[prefix],
        )
        for prefix, label in (
            ("S1_4X_GHCUP", "GHCUP"),
            ("S1_4X_STACK", "STACK"),
            ("S1_4X_AUTHORITATIVE_GHC", "AUTHORITATIVE_GHC"),
            ("S1_4X_LATEST_GHC", "LATEST_GHC"),
            ("S1_4X_HLINT", "HLINT"),
            ("S1_4X_STYLISH", "STYLISH"),
        )
    )


def pinned_json_environment_evidence(
    prefix: str,
    *,
    label: str,
    max_bytes: int,
) -> PinnedJsonEvidence:
    """Shared v3 evidence triple의 전달 FD bytes를 strict parse/hash한다."""

    fd_path = Path(_required_environment(prefix))
    source_path = _source_path_layout(
        _required_environment(f"{prefix}_SOURCE_PATH"),
        label=label,
    )
    expected_sha256 = _required_environment(f"{prefix}_SHA256")
    if SHA256_PATTERN.fullmatch(expected_sha256) is None:
        raise BlockError(f"{label}_EXPECTED_SHA256_INVALID")
    descriptor, payload, actual_sha256, _, identity = _read_pinned_fd(
        fd_path=fd_path,
        label=label,
        max_bytes=max_bytes,
        executable=False,
        capture_payload=True,
    )
    if actual_sha256 != expected_sha256:
        raise BlockError(f"{label}_SHA256_MISMATCH")
    try:
        text = payload.decode("utf-8")
    except UnicodeError as exc:
        raise BlockError(f"INVALID_JSON:{label}") from exc
    return PinnedJsonEvidence(
        label=label,
        source_path=source_path,
        fd_path=fd_path,
        descriptor=descriptor,
        payload=payload,
        sha256=actual_sha256,
        document=_strict_json_decode(text, label=label),
        identity=identity,
    )


def _canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        + b"\n"
    )


def load_pinned_profile_evidence() -> dict[str, PinnedJsonEvidence]:
    """Correctness/qualification 세 object를 shared runtime FD에서만 읽는다."""

    return {
        "baseline": pinned_json_environment_evidence(
            "S1_4X_HASKELL_BASELINE_CORRECTNESS",
            label="BASELINE_CORRECTNESS",
            max_bytes=4 * 1024 * 1024,
        ),
        "optimized": pinned_json_environment_evidence(
            "S1_4X_HASKELL_OPTIMIZED_CORRECTNESS",
            label="OPTIMIZED_CORRECTNESS",
            max_bytes=4 * 1024 * 1024,
        ),
        "qualification": pinned_json_environment_evidence(
            "S1_4X_HASKELL_QUALIFICATION_ARTIFACT",
            label="PROFILE_QUALIFICATION_ARTIFACT",
            max_bytes=64 * 1024 * 1024,
        ),
    }


def validate_profile_evidence_closure(
    *,
    profile: Mapping[str, Any],
    evidence: Mapping[str, PinnedJsonEvidence],
    benchmark_subject_commit: str,
) -> dict[str, str]:
    """Final profile이 same-FD correctness/qualification bytes를 선택했음을 고정한다."""

    if (
        set(evidence) != {"baseline", "optimized", "qualification"}
        or COMMIT_PATTERN.fullmatch(benchmark_subject_commit) is None
        or profile.get("schemaVersion")
        != "s1.4x-haskell-selected-profile-v1"
    ):
        raise BlockError("PINNED_PROFILE_EVIDENCE_CLOSURE_INVALID")
    for snapshot in evidence.values():
        if snapshot.payload != _canonical_json_bytes(snapshot.document):
            raise BlockError("PINNED_PROFILE_EVIDENCE_NOT_CANONICAL")
    expected_profiles = {
        "baseline": "baseline-o0-fasm",
        "optimized": "optimized-o2-fasm",
    }
    for name, expected_profile_id in expected_profiles.items():
        document = evidence[name].document
        if (
            not isinstance(document, dict)
            or document.get("schemaVersion")
            != "s1.4x-haskell-full-correctness-v1"
            or document.get("status") != "PASS"
            or document.get("profileId") != expected_profile_id
            or document.get("candidateSourceCommit")
            != benchmark_subject_commit
            or document.get("sourceTreeSha256")
            != profile.get("sourceTreeSha256")
            or document.get("compilerSha256")
            != profile.get("compilerSha256")
            or document.get("mismatchCount") != 0
        ):
            raise BlockError(
                f"PINNED_{name.upper()}_CORRECTNESS_INVALID"
            )
    qualification = evidence["qualification"].document
    selection = (
        qualification.get("selection")
        if isinstance(qualification, dict)
        else None
    )
    if (
        not isinstance(qualification, dict)
        or qualification.get("schemaVersion")
        != "s1.4x-haskell-profile-qualification-v1"
        or qualification.get("status") != "PASS"
        or qualification.get("candidateSourceCommit")
        != benchmark_subject_commit
        or qualification.get("sourceTreeSha256")
        != profile.get("sourceTreeSha256")
        or qualification.get("planSha256")
        != profile.get("qualificationPlanSha256")
        or not isinstance(selection, dict)
        or selection.get("profileId") != profile.get("profileId")
        or selection.get("selectedBy") != profile.get("selectedBy")
        or evidence["qualification"].sha256
        != profile.get("qualificationArtifactSha256")
    ):
        raise BlockError("PINNED_PROFILE_QUALIFICATION_INVALID")
    selected_name = (
        "optimized"
        if profile.get("profileId") == "optimized-o2-fasm"
        else "baseline"
    )
    if (
        profile.get("profileId") not in expected_profiles.values()
        or evidence[selected_name].sha256
        != profile.get("fullCorrectnessSha256")
    ):
        raise BlockError("PINNED_SELECTED_CORRECTNESS_INVALID")
    return {
        "baselineCorrectnessSha256": evidence["baseline"].sha256,
        "baselineCorrectnessSourcePath": str(
            evidence["baseline"].source_path
        ),
        "optimizedCorrectnessSha256": evidence["optimized"].sha256,
        "optimizedCorrectnessSourcePath": str(
            evidence["optimized"].source_path
        ),
        "qualificationArtifactSha256": evidence["qualification"].sha256,
        "qualificationArtifactSourcePath": str(
            evidence["qualification"].source_path
        ),
    }


def read_regular_file_snapshot(
    path: Path,
    *,
    label: str,
    max_bytes: int,
    executable: bool = False,
) -> RegularFileSnapshot:
    """모든 parent를 openat/O_NOFOLLOW로 통과해 한 FD snapshot만 읽는다."""

    if (
        not path.is_absolute()
        or type(label) is not str
        or re.fullmatch(r"[A-Z][A-Z0-9_]*", label) is None
        or type(max_bytes) is not int
        or max_bytes <= 0
    ):
        raise BlockError(f"{label}_SNAPSHOT_INPUT_INVALID")
    components = path.parts[1:]
    if not components or any(component in {"", ".", ".."} for component in components):
        raise BlockError(f"{label}_PATH_COMPONENT_INVALID")
    directory_flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    directory_descriptor = os.open("/", directory_flags)
    try:
        for component in components[:-1]:
            try:
                next_descriptor = os.open(
                    component,
                    directory_flags,
                    dir_fd=directory_descriptor,
                )
            except OSError as exc:
                raise BlockError(f"{label}_PATH_COMPONENT_INVALID") from exc
            os.close(directory_descriptor)
            directory_descriptor = next_descriptor
        try:
            descriptor = os.open(
                components[-1],
                os.O_RDONLY
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=directory_descriptor,
            )
        except OSError as exc:
            raise BlockError(f"{label}_FILE_INVALID") from exc
        try:
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode) or before.st_size > max_bytes:
                raise BlockError(f"{label}_FILE_INVALID")
            if before.st_nlink != 1:
                raise BlockError(f"{label}_HARDLINK_FORBIDDEN")
            if executable and before.st_mode & 0o111 == 0:
                raise BlockError(f"{label}_NOT_EXECUTABLE")
            chunks: list[bytes] = []
            while chunk := os.read(descriptor, 1024 * 1024):
                chunks.append(chunk)
            after = os.fstat(descriptor)
            current = os.stat(
                components[-1],
                dir_fd=directory_descriptor,
                follow_symlinks=False,
            )
            identity = (
                "st_dev",
                "st_ino",
                "st_size",
                "st_mtime_ns",
                "st_ctime_ns",
                "st_nlink",
            )
            if any(
                getattr(before, field) != getattr(after, field)
                or getattr(before, field) != getattr(current, field)
                for field in identity
            ):
                raise BlockError(f"{label}_CHANGED_DURING_READ")
        finally:
            os.close(descriptor)
    finally:
        os.close(directory_descriptor)
    payload = b"".join(chunks)
    return RegularFileSnapshot(
        path=path,
        payload=payload,
        sha256=hashlib.sha256(payload).hexdigest(),
        mode=before.st_mode,
        identity=tuple(
            getattr(before, field)
            for field in (
                "st_dev",
                "st_ino",
                "st_size",
                "st_mtime_ns",
                "st_ctime_ns",
                "st_nlink",
            )
        ),
    )


def pin_regular_file(
    path: Path,
    *,
    label: str,
    max_bytes: int,
) -> PinnedRegularFile:
    """Secure source snapshot을 immutable memfd로 복제하고 실행 종료까지 연다."""

    snapshot = read_regular_file_snapshot(
        path,
        label=label,
        max_bytes=max_bytes,
    )
    descriptor = _create_linux_memfd(
        name=f"s1-4x-{label.lower()}",
        label=label,
    )
    try:
        offset = 0
        while offset < len(snapshot.payload):
            written = os.write(descriptor, snapshot.payload[offset:])
            if written <= 0:
                raise BlockError(f"{label}_MEMFD_WRITE_FAILED")
            offset += written
        os.fchmod(descriptor, stat.S_IMODE(snapshot.mode))
        fcntl.fcntl(
            descriptor,
            _LINUX_F_ADD_SEALS,
            _LINUX_REQUIRED_MEMFD_SEALS,
        )
        if (
            fcntl.fcntl(descriptor, _LINUX_F_GET_SEALS)
            != _LINUX_REQUIRED_MEMFD_SEALS
        ):
            raise BlockError(f"{label}_MEMFD_SEAL_FAILED")
        fd_path = Path(f"/proc/self/fd/{descriptor}")
        (
            pinned_descriptor,
            payload,
            sha256,
            mode,
            identity,
        ) = _read_pinned_fd(
            fd_path=fd_path,
            label=label,
            max_bytes=max_bytes,
            executable=False,
            capture_payload=True,
        )
        if payload != snapshot.payload or sha256 != snapshot.sha256:
            raise BlockError(f"{label}_MEMFD_SNAPSHOT_MISMATCH")
        return PinnedRegularFile(
            label=label,
            source_path=path,
            fd_path=fd_path,
            descriptor=pinned_descriptor,
            sha256=sha256,
            mode=mode,
            identity=identity,
        )
    except BaseException:
        os.close(descriptor)
        raise


def read_json_snapshot(
    path: Path,
    *,
    label: str,
    max_bytes: int,
) -> JsonSnapshot:
    """Strict JSON parse와 SHA가 반드시 같은 secure FD bytes를 소비하게 한다."""

    snapshot = read_regular_file_snapshot(
        path,
        label=label,
        max_bytes=max_bytes,
    )
    try:
        text = snapshot.payload.decode("utf-8")
    except UnicodeError as exc:
        raise BlockError(f"INVALID_JSON:{label}") from exc
    return JsonSnapshot(
        path=path,
        payload=snapshot.payload,
        sha256=snapshot.sha256,
        document=_strict_json_decode(text, label=label),
    )


def sha256_file(path: Path) -> str:
    """Regular non-symlink file의 bytes를 SHA-256으로 고정한다."""

    return read_regular_file_snapshot(
        path,
        label="HASH_INPUT",
        max_bytes=1024 * 1024 * 1024,
    ).sha256


def canonical_sha256(value: Any) -> str:
    """중첩 evidence object의 canonical JSON SHA-256을 계산한다."""

    try:
        payload = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    except (TypeError, ValueError) as exc:
        raise BlockError("NON_CANONICAL_HASH_INPUT") from exc
    return hashlib.sha256(payload).hexdigest()


def _strict_json_decode(payload: str, *, label: str) -> Any:
    """Duplicate key와 비유한 숫자를 거부하며 JSON text를 읽는다."""

    def object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise BlockError(f"DUPLICATE_JSON_KEY:{label}:{key}")
            result[key] = value
        return result

    try:
        return json.loads(
            payload,
            object_pairs_hook=object_pairs,
            parse_constant=lambda token: (_ for _ in ()).throw(
                BlockError(f"NONFINITE_JSON_TOKEN:{label}:{token}")
            ),
        )
    except json.JSONDecodeError as exc:
        raise BlockError(f"INVALID_JSON:{label}") from exc


def strict_json_load(path: Path) -> Any:
    """Regular JSON file을 strict decoder로 읽는다."""

    return read_json_snapshot(
        path,
        label="JSON_EVIDENCE",
        max_bytes=512 * 1024 * 1024,
    ).document


def build_stack_benchmark_command(
    *,
    ghcup_bin: Path,
    stack_bin: Path,
    tool_path: str,
    stack_yaml: Path,
    stack_root: Path,
    work_dir: Path,
    profile_options: Sequence[str],
    time_limit_seconds: int,
    native_report: Path,
    criterion_prefix: str,
) -> list[str]:
    """GHCup offline resolver와 exact Stack/Criterion argv를 구성한다."""

    if list(profile_options) not in (["-O0", "-fasm"], ["-O2", "-fasm"]):
        raise BlockError("INVALID_SELECTED_PROFILE_OPTIONS")
    if (
        not isinstance(time_limit_seconds, int)
        or isinstance(time_limit_seconds, bool)
        or time_limit_seconds != 5
        or not criterion_prefix
        or any(character.isspace() for character in criterion_prefix)
        or not stack_root.is_absolute()
        or work_dir.is_absolute()
        or work_dir
        != Path(f".stack-work-s1-4x-{stack_root.name}")
        or type(tool_path) is not str
        or not tool_path.startswith(f"{stack_root}/tool-bin:")
        or tool_path.endswith(":")
        or any(not entry.startswith("/") for entry in tool_path.split(":"))
    ):
        raise BlockError("INVALID_CRITERION_COMMAND_INPUT")
    criterion_arguments = (
        f"--time-limit {time_limit_seconds} --json {native_report} "
        f"--match prefix {criterion_prefix} +RTS -N1 -RTS"
    )
    return [
        str(ghcup_bin),
        "--offline",
        "run",
        "--quick",
        "--ghc",
        "9.10.3",
        "--stack",
        "3.11.1",
        "--",
        "/usr/bin/env",
        f"PATH={tool_path}",
        str(stack_bin),
        "--stack-root",
        str(stack_root),
        "--work-dir",
        str(work_dir),
        "--stack-yaml",
        str(stack_yaml),
        "--no-terminal",
        "--color",
        "never",
        "--system-ghc",
        "--no-install-ghc",
        "--hpack-force",
        "bench",
        f"--ghc-options={' '.join(profile_options)}",
        f"--benchmark-arguments={criterion_arguments}",
    ]


def _toolchain_tree_records(
    root: Path,
    *,
    label: str,
    allow_relative_symlinks: bool,
) -> tuple[list[dict[str, Any]], int]:
    """한 시점의 tree metadata를 path 순서로 수집하며 hardlink/escape를 거부한다."""

    records: list[dict[str, Any]] = []
    total_file_bytes = 0
    pending = [root]
    while pending:
        directory = pending.pop()
        relative_directory = (
            "."
            if directory == root
            else directory.relative_to(root).as_posix()
        )
        directory_stat = os.lstat(directory)
        if not stat.S_ISDIR(directory_stat.st_mode):
            raise BlockError(f"{label}_DIRECTORY_INVALID:{relative_directory}")
        records.append(
            {
                "path": relative_directory,
                "type": "directory",
                "device": directory_stat.st_dev,
                "inode": directory_stat.st_ino,
                "mode": directory_stat.st_mode,
                "size": directory_stat.st_size,
                "mtimeNs": directory_stat.st_mtime_ns,
                "ctimeNs": directory_stat.st_ctime_ns,
                "linkCount": directory_stat.st_nlink,
            }
        )
        try:
            entries = sorted(
                os.scandir(directory),
                key=lambda entry: os.fsencode(entry.name),
                reverse=True,
            )
        except OSError as exc:
            raise BlockError(
                f"{label}_DIRECTORY_SCAN_FAILED:{relative_directory}"
            ) from exc
        for entry in entries:
            path = Path(entry.path)
            relative = path.relative_to(root).as_posix()
            entry_stat = entry.stat(follow_symlinks=False)
            common = {
                "path": relative,
                "device": entry_stat.st_dev,
                "inode": entry_stat.st_ino,
                "mode": entry_stat.st_mode,
                "size": entry_stat.st_size,
                "mtimeNs": entry_stat.st_mtime_ns,
                "ctimeNs": entry_stat.st_ctime_ns,
                "linkCount": entry_stat.st_nlink,
            }
            if stat.S_ISLNK(entry_stat.st_mode):
                if not allow_relative_symlinks:
                    raise BlockError(f"{label}_SYMLINK_FORBIDDEN:{relative}")
                target = os.readlink(path)
                target_path = Path(target)
                try:
                    resolved = (path.parent / target_path).resolve(strict=True)
                    resolved.relative_to(root)
                except (OSError, ValueError) as exc:
                    raise BlockError(
                        f"{label}_SYMLINK_ESCAPE:{relative}"
                    ) from exc
                if target_path.is_absolute() or ".." in target_path.parts:
                    raise BlockError(f"{label}_SYMLINK_ESCAPE:{relative}")
                records.append({**common, "type": "symlink", "target": target})
            elif stat.S_ISDIR(entry_stat.st_mode):
                pending.append(path)
            elif stat.S_ISREG(entry_stat.st_mode):
                if entry_stat.st_nlink != 1:
                    raise BlockError(f"{label}_HARDLINK_FORBIDDEN:{relative}")
                total_file_bytes += entry_stat.st_size
                records.append({**common, "type": "regular"})
            else:
                raise BlockError(f"{label}_ENTRY_INVALID:{relative}")
    records.sort(key=lambda record: os.fsencode(record["path"]))
    return records, total_file_bytes


def snapshot_toolchain_tree_metadata(
    root: Path,
    *,
    label: str,
    allow_relative_symlinks: bool = False,
) -> ToolchainTreeMetadataSnapshot:
    """Tree 전체 stat closure를 연속 두 번 읽어 path ABA와 hardlink를 탐지한다."""

    if (
        type(label) is not str
        or re.fullmatch(r"[A-Z][A-Z0-9_]*", label) is None
        or not root.is_absolute()
        or root.is_symlink()
        or not root.is_dir()
        or root.resolve(strict=True) != root
    ):
        raise BlockError(f"{label}_ROOT_INVALID")
    first_records, first_bytes = _toolchain_tree_records(
        root,
        label=label,
        allow_relative_symlinks=allow_relative_symlinks,
    )
    second_records, second_bytes = _toolchain_tree_records(
        root,
        label=label,
        allow_relative_symlinks=allow_relative_symlinks,
    )
    if first_records != second_records or first_bytes != second_bytes:
        raise BlockError(f"{label}_CHANGED_DURING_SNAPSHOT")
    return ToolchainTreeMetadataSnapshot(
        path=root,
        sha256=canonical_sha256(first_records),
        entry_count=len(first_records),
        total_file_bytes=first_bytes,
    )


def _pin_source_executable(
    path: Path,
    *,
    label: str,
    required_sha256: str | None = None,
) -> PinnedExecutable:
    """Secure source snapshot과 같은 inode를 retained executable FD로 고정한다."""

    snapshot = read_regular_file_snapshot(
        path,
        label=label,
        max_bytes=1024 * 1024 * 1024,
        executable=True,
    )
    if required_sha256 is not None and snapshot.sha256 != required_sha256:
        raise BlockError(f"{label}_SHA256_MISMATCH")
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
    except OSError as exc:
        raise BlockError(f"{label}_PIN_OPEN_FAILED") from exc
    try:
        (
            pinned_descriptor,
            _,
            sha256,
            mode,
            identity,
        ) = _read_pinned_fd(
            fd_path=Path(f"/proc/self/fd/{descriptor}"),
            label=label,
            max_bytes=1024 * 1024 * 1024,
            executable=True,
            capture_payload=False,
        )
        if (
            identity != snapshot.identity
            or sha256 != snapshot.sha256
            or mode != snapshot.mode
            or identity[-1] != 1
        ):
            raise BlockError(f"{label}_PIN_SOURCE_IDENTITY_MISMATCH")
        return PinnedExecutable(
            label=label,
            source_path=path,
            fd_path=Path(f"/proc/self/fd/{descriptor}"),
            descriptor=pinned_descriptor,
            sha256=sha256,
            mode=mode,
            identity=identity,
        )
    except BaseException:
        os.close(descriptor)
        raise


def _authoritative_ghc_layout(
    wrapper: PinnedExecutable,
) -> tuple[Path, Path, Path, bytes]:
    """승인 wrapper bytes에서 고정된 install root, distribution bin, libdir를 복원한다."""

    (
        _,
        payload,
        sha256,
        mode,
        identity,
    ) = _read_pinned_fd(
        fd_path=wrapper.fd_path,
        label=wrapper.label,
        max_bytes=64 * 1024,
        executable=True,
        capture_payload=True,
    )
    if (
        wrapper.label != "AUTHORITATIVE_GHC"
        or sha256 != wrapper.sha256
        or mode != wrapper.mode
        or identity != wrapper.identity
        or wrapper.source_path.name != f"ghc-{AUTHORITATIVE_GHC_VERSION}"
        or wrapper.source_path.parent.name != "bin"
    ):
        raise BlockError("AUTHORITATIVE_GHC_WRAPPER_IDENTITY_INVALID")
    install_root = wrapper.source_path.parent.parent
    distribution_root = (
        install_root / "lib" / f"ghc-{AUTHORITATIVE_GHC_VERSION}"
    )
    distribution_bin = distribution_root / "bin"
    libdir = distribution_root / "lib"
    expected = (
        "#!/bin/bash\n"
        f'exedir="{distribution_bin}"\n'
        f'exeprog="./ghc-{AUTHORITATIVE_GHC_VERSION}"\n'
        f'executablename="{distribution_bin}/./ghc-{AUTHORITATIVE_GHC_VERSION}"\n'
        f'bindir="{install_root / "bin"}"\n'
        f'libdir="{libdir}"\n'
        f'docdir="{install_root / "share" / "doc" / f"ghc-{AUTHORITATIVE_GHC_VERSION}"}"\n'
        f'includedir="{install_root / "include"}"\n'
        "\n"
        'exec "$executablename" -B"$libdir" ${1+"$@"}\n'
    ).encode("utf-8")
    if payload != expected:
        raise BlockError("AUTHORITATIVE_GHC_WRAPPER_LAYOUT_INVALID")
    return install_root, distribution_bin, libdir, payload


def _write_pinned_launcher(
    *,
    tool_bin: Path,
    name: str,
    payload: bytes,
) -> PinnedRegularFile:
    """Output-bound launcher를 배타 생성한 뒤 sealed memfd symlink로 즉시 교체한다."""

    source = tool_bin / f".{name}.launcher"
    try:
        descriptor = os.open(
            source,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o500,
        )
    except OSError as exc:
        raise BlockError(f"AUTHORITATIVE_GHC_LAUNCHER_CREATE_FAILED:{name}") from exc
    try:
        # Ambient umask가 launcher 실행 bit를 약화하지 못하도록 FD에서 exact mode를 고정한다.
        os.fchmod(descriptor, 0o500)
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                raise BlockError(
                    f"AUTHORITATIVE_GHC_LAUNCHER_WRITE_FAILED:{name}"
                )
            offset += written
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    pinned = pin_regular_file(
        source,
        label=f"AUTHORITATIVE_GHC_{name.upper().replace('-', '_')}_LAUNCHER",
        max_bytes=64 * 1024,
    )
    source.unlink()
    command_path = tool_bin / name
    command_path.symlink_to(str(pinned.fd_path))
    return pinned


def prepare_authoritative_ghc_closure(
    *,
    stack_root: Path,
    authoritative_ghc: PinnedExecutable,
) -> AuthoritativeGhcClosure:
    """실제 GHC/aux ELF FD와 distribution metadata를 output-bound shim에 결속한다."""

    if (
        not stack_root.is_absolute()
        or not stack_root.is_dir()
        or stack_root.is_symlink()
    ):
        raise BlockError("AUTHORITATIVE_GHC_TOOL_SHIM_INPUT_INVALID")
    tool_bin = stack_root / "tool-bin"
    if tool_bin.exists() or tool_bin.is_symlink():
        raise BlockError("AUTHORITATIVE_GHC_TOOL_SHIM_ALREADY_EXISTS")
    (
        install_root,
        distribution_bin,
        libdir,
        _,
    ) = _authoritative_ghc_layout(authoritative_ghc)
    frozen_install = (
        authoritative_ghc.sha256
        == PINNED_TOOL_SHA256["S1_4X_AUTHORITATIVE_GHC"]
    )
    compiler_elf = _pin_source_executable(
        distribution_bin / f"ghc-{AUTHORITATIVE_GHC_VERSION}",
        label="AUTHORITATIVE_GHC_COMPILER_ELF",
        required_sha256=(
            AUTHORITATIVE_GHC_COMPILER_ELF_SHA256
            if frozen_install
            else None
        ),
    )
    auxiliary_binary_names = {
        "ghc-pkg": f"ghc-pkg-{AUTHORITATIVE_GHC_VERSION}",
        "runghc": f"runghc-{AUTHORITATIVE_GHC_VERSION}",
        "haddock": f"haddock-ghc-{AUTHORITATIVE_GHC_VERSION}",
    }
    auxiliary_elves: list[tuple[str, PinnedExecutable]] = []
    pinned_launchers: list[PinnedRegularFile] = []
    try:
        for name, binary_name in auxiliary_binary_names.items():
            auxiliary_elves.append(
                (
                    name,
                    _pin_source_executable(
                        distribution_bin / binary_name,
                        label=(
                            "AUTHORITATIVE_GHC_AUX_"
                            f"{name.upper().replace('-', '_')}_ELF"
                        ),
                        required_sha256=(
                            AUTHORITATIVE_GHC_AUXILIARY_ELF_SHA256[name]
                            if frozen_install
                            else None
                        ),
                    ),
                )
            )
        libdir_snapshot = snapshot_toolchain_tree_metadata(
            libdir,
            label="AUTHORITATIVE_GHC_LIBDIR",
        )
        distribution_bin_snapshot = snapshot_toolchain_tree_metadata(
            distribution_bin,
            label="AUTHORITATIVE_GHC_DISTRIBUTION_BIN",
            allow_relative_symlinks=True,
        )
        tool_bin.mkdir(mode=0o700)
        auxiliary_map = dict(auxiliary_elves)
        launcher_payloads = {
            "ghc": (
                "#!/usr/bin/bash\n"
                "set -euo pipefail\n"
                f'exec "{compiler_elf.fd_path}" "-B{libdir}" "$@"\n'
            ),
            "ghc-pkg": (
                "#!/usr/bin/bash\n"
                "set -euo pipefail\n"
                f'exec "{auxiliary_map["ghc-pkg"].fd_path}" '
                f'"--global-package-db" "{libdir / "package.conf.d"}" "$@"\n'
            ),
            "runghc": (
                "#!/usr/bin/bash\n"
                "set -euo pipefail\n"
                f'exec "{auxiliary_map["runghc"].fd_path}" '
                f'"-f" "{tool_bin / "ghc"}" "$@"\n'
            ),
            "haddock": (
                "#!/usr/bin/bash\n"
                "set -euo pipefail\n"
                f'exec "{auxiliary_map["haddock"].fd_path}" '
                f'"-B{libdir}" "-l{libdir}" "$@"\n'
            ),
        }
        for name, payload in launcher_payloads.items():
            pinned_launchers.append(
                _write_pinned_launcher(
                    tool_bin=tool_bin,
                    name=name,
                    payload=payload.encode("utf-8"),
                )
            )
        closure = AuthoritativeGhcClosure(
            install_root=install_root,
            wrapper=authoritative_ghc,
            compiler_elf=compiler_elf,
            auxiliary_elves=tuple(auxiliary_elves),
            libdir_path=libdir,
            distribution_bin_path=distribution_bin,
            libdir_snapshot=libdir_snapshot,
            distribution_bin_snapshot=distribution_bin_snapshot,
            tool_path=f"{tool_bin}:/usr/bin:/bin",
            ghc_shim=tool_bin / "ghc",
            pinned_launchers=tuple(pinned_launchers),
        )
        validate_authoritative_ghc_closure(closure)
        return closure
    except BaseException:
        for pinned in (*pinned_launchers, *(item for _, item in auxiliary_elves)):
            os.close(pinned.descriptor)
        os.close(compiler_elf.descriptor)
        raise


def _validate_retained_pinned_object(
    pinned: PinnedExecutable | PinnedRegularFile,
) -> None:
    """Retained FD의 bytes/stat/seal identity가 준비 시점과 같은지 재검증한다."""

    (
        descriptor,
        _,
        sha256,
        mode,
        identity,
    ) = _read_pinned_fd(
        fd_path=pinned.fd_path,
        label=pinned.label,
        max_bytes=1024 * 1024 * 1024,
        executable=bool(pinned.mode & 0o111),
        capture_payload=False,
    )
    if (
        descriptor != pinned.descriptor
        or sha256 != pinned.sha256
        or mode != pinned.mode
        or identity != pinned.identity
    ):
        raise BlockError(f"{pinned.label}_PINNED_FD_OBJECT_CHANGED")
    if isinstance(pinned, PinnedRegularFile) and (
        fcntl.fcntl(descriptor, _LINUX_F_GET_SEALS)
        != _LINUX_REQUIRED_MEMFD_SEALS
    ):
        raise BlockError(f"{pinned.label}_PINNED_FD_NOT_SEALED")


def validate_authoritative_ghc_closure(
    closure: AuthoritativeGhcClosure,
) -> None:
    """Compiler 실행 전후에 retained FD, output shim, install metadata를 모두 재검증한다."""

    if not isinstance(closure, AuthoritativeGhcClosure):
        raise BlockError("AUTHORITATIVE_GHC_INSTALL_CLOSURE_INVALID")
    _validate_retained_pinned_object(closure.wrapper)
    for pinned in closure.pinned_objects:
        _validate_retained_pinned_object(pinned)
    tool_bin = closure.ghc_shim.parent
    expected_names = ["ghc", "ghc-pkg", "haddock", "runghc"]
    launcher_by_name = {
        pinned.source_path.name.removeprefix(".").removesuffix(".launcher"): pinned
        for pinned in closure.pinned_launchers
    }
    try:
        actual_names = sorted(path.name for path in tool_bin.iterdir())
        links = {
            name: os.readlink(tool_bin / name)
            for name in expected_names
        }
    except OSError as exc:
        raise BlockError("AUTHORITATIVE_GHC_TOOL_SHIM_INVALID") from exc
    if (
        actual_names != expected_names
        or set(launcher_by_name) != set(expected_names)
        or any(
            links[name] != str(launcher_by_name[name].fd_path)
            for name in expected_names
        )
    ):
        raise BlockError("AUTHORITATIVE_GHC_TOOL_SHIM_INVALID")
    current_libdir = snapshot_toolchain_tree_metadata(
        closure.libdir_path,
        label="AUTHORITATIVE_GHC_LIBDIR",
    )
    current_bin = snapshot_toolchain_tree_metadata(
        closure.distribution_bin_path,
        label="AUTHORITATIVE_GHC_DISTRIBUTION_BIN",
        allow_relative_symlinks=True,
    )
    if (
        current_libdir != closure.libdir_snapshot
        or current_bin != closure.distribution_bin_snapshot
    ):
        raise BlockError("AUTHORITATIVE_GHC_INSTALL_CLOSURE_CHANGED")


def authoritative_ghc_closure_receipt(
    closure: AuthoritativeGhcClosure,
) -> dict[str, Any]:
    """승인 wrapper와 실제 실행 ELF/metadata closure를 구분한 receipt projection이다."""

    auxiliary = dict(closure.auxiliary_elves)
    launcher_by_name = {
        pinned.source_path.name.removeprefix(".").removesuffix(".launcher"): pinned
        for pinned in closure.pinned_launchers
    }
    return {
        "approvedWrapperPath": str(closure.wrapper.source_path),
        "approvedWrapperPinnedFdPath": str(closure.wrapper.fd_path),
        "approvedWrapperSha256": closure.wrapper.sha256,
        "actualCompilerElfPath": str(closure.compiler_elf.source_path),
        "actualCompilerElfPinnedFdPath": str(closure.compiler_elf.fd_path),
        "actualCompilerElfSha256": closure.compiler_elf.sha256,
        "actualCompilerLibdirPath": str(closure.libdir_path),
        "libdirMetadataSha256": closure.libdir_snapshot.sha256,
        "libdirMetadataEntryCount": closure.libdir_snapshot.entry_count,
        "libdirMetadataTotalFileBytes": (
            closure.libdir_snapshot.total_file_bytes
        ),
        "distributionBinPath": str(closure.distribution_bin_path),
        "distributionBinMetadataSha256": (
            closure.distribution_bin_snapshot.sha256
        ),
        "distributionBinMetadataEntryCount": (
            closure.distribution_bin_snapshot.entry_count
        ),
        "auxiliaryElfSha256": {
            name: auxiliary[name].sha256
            for name in sorted(auxiliary)
        },
        "auxiliaryElfPinnedFdPath": {
            name: str(auxiliary[name].fd_path)
            for name in sorted(auxiliary)
        },
        "outputLauncherSha256": {
            name: launcher_by_name[name].sha256
            for name in sorted(launcher_by_name)
        },
        "scoringCompilerExecutionBinding": (
            "sealed-elf-fd-with-validated-install-closure"
        ),
    }


def prepare_authoritative_ghc_shim(
    *,
    stack_root: Path,
    authoritative_ghc: PinnedExecutable,
) -> tuple[str, Path]:
    """기존 내부 호출자를 actual-ELF closure 구현으로 연결한다."""

    closure = prepare_authoritative_ghc_closure(
        stack_root=stack_root,
        authoritative_ghc=authoritative_ghc,
    )
    return closure.tool_path, closure.ghc_shim


def run_pinned_subprocess(
    command: Sequence[str],
    *,
    cwd: Path,
    environment: Mapping[str, str],
    pinned_executables: Sequence[PinnedExecutable],
    capture_output: bool,
    pinned_files: Sequence[PinnedRegularFile] = (),
    timeout: int | None = None,
    benchmark_python_runtime: BenchmarkPythonRuntime | None = None,
) -> subprocess.CompletedProcess[bytes]:
    """검증한 executable과 sealed source FD를 모든 nested process에 상속한다."""

    pinned_objects = (*pinned_executables, *pinned_files)
    descriptors = tuple(
        sorted({pinned.descriptor for pinned in pinned_objects})
    )
    if (
        not command
        or not cwd.is_absolute()
        or not cwd.is_dir()
        or not descriptors
        or str(command[0])
        not in {str(executable.fd_path) for executable in pinned_executables}
        or any(
            _pinned_descriptor(pinned.fd_path, label=pinned.label)
            != pinned.descriptor
            for pinned in pinned_objects
        )
    ):
        raise BlockError("PINNED_SUBPROCESS_INPUT_INVALID")
    for pinned in pinned_objects:
        try:
            current = os.fstat(pinned.descriptor)
        except OSError as exc:
            raise BlockError(
                f"{pinned.label}_PINNED_FD_NOT_INHERITED"
            ) from exc
        if (
            not stat.S_ISREG(current.st_mode)
            or current.st_mode != pinned.mode
            or (
                current.st_dev,
                current.st_ino,
                current.st_size,
                current.st_mtime_ns,
                current.st_ctime_ns,
                current.st_nlink,
            )
            != pinned.identity
        ):
            raise BlockError(
                f"{pinned.label}_PINNED_FD_OBJECT_CHANGED"
            )
    if pinned_files:
        _require_linux_memfd_abi(label="PINNED_SUBPROCESS")
    for pinned_file in pinned_files:
        if (
            fcntl.fcntl(
                pinned_file.descriptor,
                _LINUX_F_GET_SEALS,
            )
            != _LINUX_REQUIRED_MEMFD_SEALS
        ):
            raise BlockError(f"{pinned_file.label}_PINNED_FD_NOT_SEALED")
    execution_target = next(
        executable
        for executable in pinned_executables
        if str(command[0]) == str(executable.fd_path)
    )
    before_python_runtime: BenchmarkPythonRuntime | None = None
    execution_command = list(command)
    executable_path: str | None = None
    if benchmark_python_runtime is not None:
        expected_python = PinnedExecutable(
            label="MARKER_PYTHON",
            source_path=benchmark_python_runtime.source_path,
            fd_path=benchmark_python_runtime.fd_path,
            descriptor=benchmark_python_runtime.descriptor,
            sha256=benchmark_python_runtime.sha256,
            mode=benchmark_python_runtime.mode,
            identity=benchmark_python_runtime.identity,
        )
        before_python_runtime = _snapshot_benchmark_python_runtime(
            expected_python,
            require_current_process=False,
        )
        if before_python_runtime != benchmark_python_runtime:
            raise BlockError("BENCHMARK_PYTHON_CLOSURE_CHANGED_BEFORE_CHILD")
    if execution_target.label == "MARKER_PYTHON":
        if (
            benchmark_python_runtime is None
            or execution_target.source_path
            != benchmark_python_runtime.source_path
            or execution_target.fd_path != benchmark_python_runtime.fd_path
        ):
            raise BlockError("BENCHMARK_PYTHON_EXECUTION_BINDING_MISSING")
        execution_command[0] = str(benchmark_python_runtime.source_path)
        executable_path = str(benchmark_python_runtime.fd_path)
    completed = subprocess.run(
        execution_command,
        executable=executable_path,
        cwd=cwd,
        env=dict(environment),
        check=False,
        stdin=subprocess.DEVNULL,
        capture_output=capture_output,
        pass_fds=descriptors,
        timeout=timeout,
    )
    if benchmark_python_runtime is not None:
        expected_python = PinnedExecutable(
            label="MARKER_PYTHON",
            source_path=benchmark_python_runtime.source_path,
            fd_path=benchmark_python_runtime.fd_path,
            descriptor=benchmark_python_runtime.descriptor,
            sha256=benchmark_python_runtime.sha256,
            mode=benchmark_python_runtime.mode,
            identity=benchmark_python_runtime.identity,
        )
        after_python_runtime = _snapshot_benchmark_python_runtime(
            expected_python,
            require_current_process=False,
        )
        if (
            after_python_runtime != before_python_runtime
            or after_python_runtime != benchmark_python_runtime
        ):
            raise BlockError("BENCHMARK_PYTHON_CLOSURE_CHANGED_AFTER_CHILD")
    return completed


def _require_absolute_regular(path: Path, *, label: str) -> Path:
    if (
        not path.is_absolute()
        or path.is_symlink()
        or not path.is_file()
        or path.resolve(strict=True) != path
    ):
        raise BlockError(f"{label}_NOT_CANONICAL_REGULAR_FILE")
    return path


def _require_absolute_directory(path: Path, *, label: str) -> Path:
    if (
        not path.is_absolute()
        or path.is_symlink()
        or not path.is_dir()
        or path.resolve(strict=True) != path
    ):
        raise BlockError(f"{label}_NOT_CANONICAL_DIRECTORY")
    return path


def _required_environment(name: str) -> str:
    value = os.environ.get(name)
    if value is None or not value:
        raise BlockError(f"REQUIRED_ENVIRONMENT_MISSING:{name}")
    return value


def validate_runtime_identity(
    path: Path,
    *,
    selector_id: str,
) -> tuple[Path, str, str]:
    """Benchmark process가 self-report한 executable exact-object를 검증한다."""

    identity_snapshot = read_json_snapshot(
        path,
        label="BENCHMARK_RUNTIME_IDENTITY",
        max_bytes=1024 * 1024,
    )
    document = identity_snapshot.document
    expected_fields = {
        "schemaVersion",
        "boundaryId",
        "selectorId",
        "executedBenchmarkPath",
        "executedBenchmarkSha256",
        "status",
    }
    if (
        not isinstance(document, dict)
        or set(document) != expected_fields
        or document.get("schemaVersion")
        != "s1.4x-haskell-benchmark-runtime-identity-v1"
        or document.get("boundaryId") != "haskell"
        or document.get("selectorId") != selector_id
        or document.get("status") != "PASS"
    ):
        raise BlockError("BENCHMARK_RUNTIME_IDENTITY_INVALID")
    executed = Path(str(document["executedBenchmarkPath"]))
    executable_snapshot = read_regular_file_snapshot(
        executed,
        label="EXECUTED_BENCHMARK",
        max_bytes=512 * 1024 * 1024,
        executable=True,
    )
    expected_sha256 = document["executedBenchmarkSha256"]
    if (
        not isinstance(expected_sha256, str)
        or SHA256_PATTERN.fullmatch(expected_sha256) is None
        or executable_snapshot.sha256 != expected_sha256
    ):
        raise BlockError("EXECUTED_BENCHMARK_SHA256_MISMATCH")
    return executed, expected_sha256, identity_snapshot.sha256


def _import_repo_modules(
    *,
    haskell_root: Path,
    numeric_root: Path,
) -> tuple[Any, Any, Any]:
    for directory in (
        haskell_root / "tools",
        numeric_root / "benchmarks",
        numeric_root / "integration",
    ):
        value = str(directory)
        if value not in sys.path:
            sys.path.insert(0, value)
    try:
        import gate  # type: ignore[import-not-found]
        import haskell_evidence  # type: ignore[import-not-found]
        import validate_benchmark_report  # type: ignore[import-not-found]
    except ImportError as exc:
        raise BlockError("BENCHMARK_VALIDATOR_IMPORT_FAILED") from exc
    return haskell_evidence, validate_benchmark_report, gate


def _selector_and_cases(
    plan: Mapping[str, Any],
    *,
    selector_id: str,
    family_id: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    selector = next(
        (
            item
            for item in plan["familySelectors"]
            if item["selectorId"] == selector_id
        ),
        None,
    )
    if (
        not isinstance(selector, dict)
        or selector.get("boundaryId") != "haskell"
        or selector.get("familyId") != family_id
        or selector.get("criterionMatchMode") != "prefix"
        or selector.get("criterionPrefix") != f"{family_id}/"
    ):
        raise BlockError("SELECTOR_IDENTITY_MISMATCH")
    by_id = {item["caseId"]: item for item in plan["cases"]}
    try:
        cases = [by_id[case_id] for case_id in selector["expectedCaseIds"]]
    except (KeyError, TypeError) as exc:
        raise BlockError("SELECTOR_CASE_CLOSURE_INVALID") from exc
    if (
        not 2 <= len(cases) <= 45
        or [case["familyId"] for case in cases] != [family_id] * len(cases)
    ):
        raise BlockError("SELECTOR_FAMILY_CLOSURE_INVALID")
    return selector, cases


def _selector_input_closure(
    plan: Mapping[str, Any],
    selector: Mapping[str, Any],
    cases: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    return {
        "boundaryId": "haskell",
        "familyId": selector["familyId"],
        "selectorId": selector["selectorId"],
        "expectedCaseIds": selector["expectedCaseIds"],
        "expectedCaseCount": len(cases),
        "inputClosureSha256": canonical_sha256(
            {
                "fixtureFreezeIdentity": plan["fixtureFreezeIdentity"],
                "selector": selector,
                "cases": list(cases),
            }
        ),
    }


def _validate_qualification(
    *,
    path: Path,
    plan: Mapping[str, Any],
    plan_sha256: str,
    selector: Mapping[str, Any],
    cases: Sequence[Mapping[str, Any]],
    rotation_id: str,
    outer_repetition: int,
    run_id: str,
    benchmark_subject_commit: str,
    block_dir: Path,
) -> dict[str, Any]:
    _require_absolute_regular(path, label="QUALIFICATION")
    if path.parent != block_dir or path.name != "timeout-qualification.json":
        raise BlockError("QUALIFICATION_PATH_MISMATCH")
    qualification = strict_json_load(path)
    exact_fields = {
        "schemaVersion",
        "phase",
        "measurementEntered",
        "plan",
        "subject",
        "run",
        "hostValidity",
        "selectorInputClosure",
        "command",
    }
    if (
        not isinstance(qualification, dict)
        or set(qualification) != exact_fields
        or qualification["schemaVersion"] != "s1.4x-timeout-qualification-v1"
        or qualification["phase"] != "PRE_RUN"
        or qualification["measurementEntered"] is not False
    ):
        raise BlockError("INVALID_PRE_RUN_QUALIFICATION_STATE")
    if qualification["plan"] != {
        "planId": plan["planId"],
        "sha256": plan_sha256,
    }:
        raise BlockError("QUALIFICATION_PLAN_MISMATCH")
    if qualification["subject"] != {
        "benchmarkSubjectCommit": benchmark_subject_commit,
        "candidateSourceCommit": benchmark_subject_commit,
    }:
        raise BlockError("QUALIFICATION_SUBJECT_MISMATCH")
    timeout = plan["execution"]["familyBlockTimeoutSeconds"][selector["selectorId"]]
    if qualification["run"] != {
        "runId": run_id,
        "rotationId": rotation_id,
        "outerRepetition": outer_repetition,
        "timeoutSeconds": timeout,
    }:
        raise BlockError("QUALIFICATION_RUN_MISMATCH")
    if qualification["selectorInputClosure"] != _selector_input_closure(
        plan,
        selector,
        cases,
    ):
        raise BlockError("QUALIFICATION_SELECTOR_CLOSURE_MISMATCH")
    host_validity = qualification["hostValidity"]
    if (
        not isinstance(host_validity, dict)
        or set(host_validity)
        != {
            "artifactPath",
            "sha256",
            "status",
            "policySha256",
            "portableHostIdSha256",
        }
        or host_validity["artifactPath"] != "host-validity.json"
        or host_validity["status"] != "PASS"
        or any(
            SHA256_PATTERN.fullmatch(str(host_validity[field])) is None
            for field in ("sha256", "policySha256", "portableHostIdSha256")
        )
    ):
        raise BlockError("QUALIFICATION_HOST_VALIDITY_INVALID")
    if sha256_file(block_dir / host_validity["artifactPath"]) != host_validity["sha256"]:
        raise BlockError("QUALIFICATION_HOST_VALIDITY_SHA256_MISMATCH")
    command = qualification["command"]
    if (
        not isinstance(command, dict)
        or set(command)
        != {
            "commandManifestSha256",
            "allowedExecutable",
            "renderedArgvSha256",
        }
        or SHA256_PATTERN.fullmatch(str(command["commandManifestSha256"])) is None
        or SHA256_PATTERN.fullmatch(str(command["renderedArgvSha256"])) is None
        or not isinstance(command["allowedExecutable"], dict)
        or set(command["allowedExecutable"]) != {"path", "resolvedPath", "sha256"}
        or SHA256_PATTERN.fullmatch(
            str(command["allowedExecutable"]["sha256"])
        )
        is None
    ):
        raise BlockError("QUALIFICATION_COMMAND_INVALID")
    return qualification


def _validate_measurement_qualification(
    *,
    path: Path,
    pre_run: Mapping[str, Any],
) -> None:
    actual = strict_json_load(path)
    expected = dict(pre_run)
    expected["phase"] = "MEASUREMENT"
    expected["measurementEntered"] = True
    if actual != expected:
        raise BlockError("INVALID_MEASUREMENT_QUALIFICATION")


def _profile_and_source_evidence(
    *,
    haskell_root: Path,
    plan_path: Path,
    haskell_evidence: Any,
    pinned_profile_evidence: Mapping[str, PinnedJsonEvidence],
    benchmark_subject_commit: str,
) -> tuple[dict[str, Any], Path, Path, dict[str, str]]:
    profile_path = _require_absolute_regular(
        haskell_root / "selected-profile.v1.json",
        label="SELECTED_PROFILE",
    )
    manifest_path = _require_absolute_regular(
        haskell_root / "source-inputs.v1.json",
        label="SOURCE_INPUT_MANIFEST",
    )
    plan_snapshot = read_json_snapshot(
        plan_path,
        label="BENCHMARK_PLAN",
        max_bytes=16 * 1024 * 1024,
    )
    profile_snapshot = read_json_snapshot(
        profile_path,
        label="SELECTED_PROFILE",
        max_bytes=4 * 1024 * 1024,
    )
    manifest_snapshot = read_json_snapshot(
        manifest_path,
        label="SOURCE_INPUT_MANIFEST",
        max_bytes=16 * 1024 * 1024,
    )
    plan = plan_snapshot.document
    profile = profile_snapshot.document
    if profile.get("schemaVersion") != "s1.4x-haskell-selected-profile-v1":
        raise BlockError("FINAL_SELECTED_PROFILE_REQUIRED")
    source_tree_sha256 = haskell_evidence.benchmark_source_tree_sha256(
        haskell_root
    )
    validated_profile = haskell_evidence.validate_selected_profile_document(
        profile,
        expected_compiler_sha256=haskell_evidence.AUTHORITATIVE_GHC_SHA256,
        expected_source_tree_sha256=source_tree_sha256,
        expected_qualification_plan_sha256=plan_snapshot.sha256,
        expected_selector_config_sha256=canonical_sha256(
            plan["haskellProfileQualification"]
        ),
    )
    validated_manifest = haskell_evidence.validate_source_manifest(
        haskell_root,
        manifest_path,
    )
    if (
        profile_snapshot.payload
        != haskell_evidence.canonical_json_bytes(
            validated_profile,
            trailing_newline=True,
        )
        or manifest_snapshot.document != validated_manifest
        or manifest_snapshot.payload
        != haskell_evidence.canonical_json_bytes(
            validated_manifest,
            trailing_newline=True,
        )
    ):
        raise BlockError("PROFILE_SOURCE_EVIDENCE_NOT_CANONICAL")
    pinned_closure = validate_profile_evidence_closure(
        profile=profile,
        evidence=pinned_profile_evidence,
        benchmark_subject_commit=benchmark_subject_commit,
    )
    return (
        profile,
        profile_path,
        manifest_path,
        {
            "selectedProfileSha256": profile_snapshot.sha256,
            "sourceInputManifestSha256": manifest_snapshot.sha256,
            "qualificationPlanSha256": plan_snapshot.sha256,
            "sourceTreeSha256": source_tree_sha256,
            **pinned_closure,
        },
    )


def _verify_subject_commit(repo_root: Path, expected: str) -> None:
    if COMMIT_PATTERN.fullmatch(expected) is None:
        raise BlockError("BENCHMARK_SUBJECT_COMMIT_INVALID")
    completed = subprocess.run(
        ["/usr/bin/git", "-C", str(repo_root), "rev-parse", "--verify", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if completed.returncode != 0 or completed.stdout.strip() != expected:
        raise BlockError("BENCHMARK_SUBJECT_COMMIT_MISMATCH")
    status = subprocess.run(
        [
            "/usr/bin/git",
            "-C",
            str(repo_root),
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
        ],
        check=False,
        capture_output=True,
        timeout=10,
    )
    if status.returncode != 0 or status.stdout:
        raise BlockError("BENCHMARK_SUBJECT_WORKTREE_NOT_CLEAN")


def _find_benchmark_artifact(work_dir: Path) -> Path:
    candidates = sorted(
        (
            path
            for path in (work_dir / "dist").glob(
                "*/ghc-9.10.3/build/"
                "s1-4x-haskell-benchmark/s1-4x-haskell-benchmark"
            )
            if path.is_file() and not path.is_symlink() and os.access(path, os.X_OK)
        ),
        key=lambda path: str(path).encode(),
    )
    if len(candidates) != 1:
        raise BlockError(f"BENCHMARK_ARTIFACT_COUNT_INVALID:{len(candidates)}")
    return candidates[0].resolve(strict=True)


def _benchmark_stack_root(cache_root: Path, block_dir: Path) -> Path:
    """Block output identity에만 결속된 일회용 benchmark Stack root를 만든다."""

    if not cache_root.is_absolute() or not block_dir.is_absolute():
        raise BlockError("BENCHMARK_STACK_ROOT_INPUT_INVALID")
    suffix = hashlib.sha256(os.fsencode(str(block_dir))).hexdigest()[:24]
    return cache_root / f"stack-root-benchmark-{suffix}"


def _iso_now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _validate_rotation(
    *,
    rotation_id: str,
    outer_repetition_text: str,
) -> int:
    if (
        not outer_repetition_text.isascii()
        or not outer_repetition_text.isdigit()
        or outer_repetition_text.startswith("0")
    ):
        raise BlockError("OUTER_REPETITION_INVALID")
    outer_repetition = int(outer_repetition_text)
    if outer_repetition not in (1, 2, 3) or rotation_id != f"R{outer_repetition}":
        raise BlockError("ROTATION_REPETITION_MISMATCH")
    return outer_repetition


def _run_shared_json_command(
    command: Sequence[str],
    *,
    label: str,
    cwd: Path,
    environment: Mapping[str, str],
    pinned_executables: Sequence[PinnedExecutable],
    pinned_files: Sequence[PinnedRegularFile],
    benchmark_python_runtime: BenchmarkPythonRuntime,
    timeout: int = 300,
) -> dict[str, Any]:
    if (
        len(command) < 2
        or str(command[1])
        not in {str(pinned.fd_path) for pinned in pinned_files}
    ):
        raise BlockError(f"{label}_PINNED_ARGV_INVALID")
    completed = run_pinned_subprocess(
        command,
        cwd=cwd,
        environment=environment,
        pinned_executables=pinned_executables,
        capture_output=True,
        pinned_files=pinned_files,
        timeout=timeout,
        benchmark_python_runtime=benchmark_python_runtime,
    )
    if completed.returncode != 0:
        raise BlockError(f"{label}_FAILED:{completed.returncode}")
    if completed.stderr:
        raise BlockError(f"{label}_UNEXPECTED_STDERR")
    try:
        standard_output = completed.stdout.decode("utf-8")
    except UnicodeError as exc:
        raise BlockError(f"{label}_OUTPUT_NOT_UTF8") from exc
    document = _strict_json_decode(standard_output, label=label)
    if not isinstance(document, dict):
        raise BlockError(f"{label}_OUTPUT_INVALID")
    return document


def _require_sha_fields(document: Mapping[str, Any], fields: Sequence[str]) -> None:
    if any(SHA256_PATTERN.fullmatch(str(document.get(field))) is None for field in fields):
        raise BlockError("SHARED_OUTPUT_SHA256_INVALID")


def run_block(arguments: argparse.Namespace) -> dict[str, Any]:
    """한 family의 preflight, Criterion 실행, shared evidence 발행을 수행한다."""

    benchmark_python_runtime = _benchmark_python_runtime()
    repo_root = _require_absolute_directory(arguments.repo_root, label="REPO_ROOT")
    numeric_root = (
        repo_root
        / "workspaces"
        / "decision-platform"
        / "research"
        / "s1-4x-numeric-parity"
    )
    haskell_root = _require_absolute_directory(
        numeric_root / "haskell",
        label="HASKELL_ROOT",
    )
    # Full timing은 orchestration이 check한 materialized large root만 소비한다.
    large_fixture_root = _require_absolute_directory(
        Path(_required_environment("S1_4X_LARGE_FIXTURE_ROOT")),
        label="LARGE_FIXTURE_ROOT",
    )
    _require_absolute_directory(
        large_fixture_root / "large",
        label="LARGE_FIXTURE_DIRECTORY",
    )
    integration_root = _require_absolute_directory(
        numeric_root / "integration",
        label="INTEGRATION_ROOT",
    )
    expected_plan = _require_absolute_regular(
        numeric_root / "benchmarks/benchmark-plan.v1.json",
        label="FROZEN_PLAN",
    )
    plan_path = _require_absolute_regular(arguments.plan, label="PLAN")
    if plan_path != expected_plan:
        raise BlockError("PLAN_PATH_MISMATCH")
    block_dir = _require_absolute_directory(arguments.block_dir, label="BLOCK_DIR")
    qualification_path = _require_absolute_regular(
        arguments.qualification,
        label="QUALIFICATION",
    )
    if arguments.boundary != "haskell":
        raise BlockError("BOUNDARY_MISMATCH")
    if arguments.selector != f"haskell/{arguments.family}":
        raise BlockError("SELECTOR_FAMILY_MISMATCH")
    if RUN_ID_PATTERN.fullmatch(arguments.run_id) is None:
        raise BlockError("RUN_ID_INVALID")
    outer_repetition = _validate_rotation(
        rotation_id=arguments.rotation,
        outer_repetition_text=arguments.outer_repetition,
    )
    expected_tail = (
        Path(arguments.run_id)
        / arguments.rotation
        / "haskell"
        / arguments.family
    )
    if tuple(block_dir.parts[-len(expected_tail.parts) :]) != expected_tail.parts:
        raise BlockError("BLOCK_DIRECTORY_LAYOUT_MISMATCH")

    raw_path = block_dir / RAW_RELATIVE
    receipt_path = block_dir / RECEIPT_RELATIVE
    input_ledger_path = block_dir / LEDGER_RELATIVE
    native_contract_path = block_dir / NATIVE_CONTRACT_RELATIVE
    native_statistics_path = block_dir / NATIVE_STATISTICS_RELATIVE
    native_path = block_dir / NATIVE_RELATIVE
    result_path = block_dir / BLOCK_RESULT_RELATIVE
    runtime_identity_path = block_dir / RUNTIME_IDENTITY_RELATIVE
    ghc_install_closure_path = block_dir / GHC_INSTALL_CLOSURE_RELATIVE
    output_paths = (
        raw_path,
        receipt_path,
        input_ledger_path,
        native_contract_path,
        native_statistics_path,
        native_path,
        result_path,
        runtime_identity_path,
        ghc_install_closure_path,
        raw_path.parent,
        receipt_path.parent,
    )
    if any(path.exists() or path.is_symlink() for path in output_paths):
        raise BlockError("BENCHMARK_OUTPUT_ALREADY_EXISTS")
    cache_root = _require_absolute_directory(
        Path(_required_environment("S1_4X_CACHE_ROOT")),
        label="CACHE_ROOT",
    )
    stack_root = _benchmark_stack_root(cache_root, block_dir)
    if stack_root.exists() or stack_root.is_symlink():
        raise BlockError("BENCHMARK_STACK_ROOT_ALREADY_EXISTS")
    stack_root.mkdir(mode=0o700)
    # Stack이 dependency source에도 만들 수 있는 단일-component 상대 경로를 쓴다.
    work_dir = Path(f".stack-work-s1-4x-{stack_root.name}")
    work_directory = haskell_root / work_dir
    if work_directory.exists() or work_directory.is_symlink():
        raise BlockError("BENCHMARK_STACK_WORK_DIRECTORY_ALREADY_EXISTS")
    pinned_executables = load_pinned_toolchain()
    (
        ghcup,
        stack,
        authoritative_ghc,
        latest_ghc,
        hlint,
        stylish,
    ) = pinned_executables
    ghc_closure = prepare_authoritative_ghc_closure(
        stack_root=stack_root,
        authoritative_ghc=authoritative_ghc,
    )
    tool_path = ghc_closure.tool_path
    ghc_shim = ghc_closure.ghc_shim
    pinned_profile_evidence = load_pinned_profile_evidence()

    _verify_subject_commit(repo_root, arguments.benchmark_subject_commit)
    haskell_evidence, report_validator, gate = _import_repo_modules(
        haskell_root=haskell_root,
        numeric_root=numeric_root,
    )
    plan = report_validator.validate_plan(plan_path)
    if not isinstance(plan, dict):
        raise BlockError("PLAN_VALIDATOR_RETURNED_NON_OBJECT")
    expected_affinity = plan["execution"]["cpuSet"]
    actual_affinity = sorted(os.sched_getaffinity(0))
    if actual_affinity != expected_affinity:
        raise BlockError(f"ACTUAL_CPU_AFFINITY_MISMATCH:{actual_affinity}")
    selector, cases = _selector_and_cases(
        plan,
        selector_id=arguments.selector,
        family_id=arguments.family,
    )
    qualification = _validate_qualification(
        path=qualification_path,
        plan=plan,
        plan_sha256=sha256_file(plan_path),
        selector=selector,
        cases=cases,
        rotation_id=arguments.rotation,
        outer_repetition=outer_repetition,
        run_id=arguments.run_id,
        benchmark_subject_commit=arguments.benchmark_subject_commit,
        block_dir=block_dir,
    )
    (
        profile,
        profile_path,
        source_manifest_path,
        profile_source_closure,
    ) = _profile_and_source_evidence(
        haskell_root=haskell_root,
        plan_path=plan_path,
        haskell_evidence=haskell_evidence,
        pinned_profile_evidence=pinned_profile_evidence,
        benchmark_subject_commit=arguments.benchmark_subject_commit,
    )
    toolchain_lock_path = _require_absolute_regular(
        haskell_root / "toolchain-lock.v1.json",
        label="TOOLCHAIN_LOCK",
    )
    merged_provenance_path = _require_absolute_regular(
        numeric_root / "contract/toolchain-provenance.v1.json",
        label="TOOLCHAIN_PROVENANCE",
    )
    stack_yaml_path = _require_absolute_regular(
        haskell_root / "stack.yaml",
        label="STACK_YAML",
    )

    marker_python = PinnedExecutable(
        label="MARKER_PYTHON",
        source_path=benchmark_python_runtime.source_path,
        fd_path=benchmark_python_runtime.fd_path,
        descriptor=benchmark_python_runtime.descriptor,
        sha256=benchmark_python_runtime.sha256,
        mode=benchmark_python_runtime.mode,
        identity=benchmark_python_runtime.identity,
    )
    marker_script = pin_regular_file(
        numeric_root / "benchmarks/run_rotated_blocks.py",
        label="MARKER_SCRIPT",
        max_bytes=16 * 1024 * 1024,
    )
    marker_argv = [
        "/usr/bin/env",
        "-a",
        str(marker_python.source_path),
        str(marker_python.fd_path),
        str(marker_script.fd_path),
        "mark-measurement-entered",
        "--qualification",
        str(qualification_path),
    ]
    ledger_script = pin_regular_file(
        integration_root / "benchmark_input_ledger.py",
        label="BENCHMARK_INPUT_LEDGER_SCRIPT",
        max_bytes=16 * 1024 * 1024,
    )
    native_script = pin_regular_file(
        integration_root / "native_benchmark_block.py",
        label="NATIVE_BENCHMARK_BLOCK_SCRIPT",
        max_bytes=16 * 1024 * 1024,
    )
    runtime_executables = (
        *pinned_executables,
        *ghc_closure.pinned_executables,
        marker_python,
    )
    environment = dict(os.environ)
    environment.update(THREAD_ENVIRONMENT)
    environment.update(
        {
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONHASHSEED": "0",
            "PYTHONNOUSERSITE": "1",
            "PYTHONPATH": f"{numeric_root / 'benchmarks'}:{integration_root}",
        }
    )

    ledger_result = _run_shared_json_command(
        [
            str(marker_python.fd_path),
            str(ledger_script.fd_path),
            "--repo-root",
            str(repo_root),
            "--plan",
            str(plan_path),
            "--boundary",
            "haskell",
            "--selector",
            arguments.selector,
            "--output",
            str(input_ledger_path),
        ],
        label="BENCHMARK_INPUT_LEDGER",
        cwd=repo_root,
        environment=environment,
        pinned_executables=runtime_executables,
        pinned_files=(ledger_script,),
        benchmark_python_runtime=benchmark_python_runtime,
    )
    if ledger_result != {
        "boundaryId": "haskell",
        "selectorId": arguments.selector,
        "status": "PASS",
    }:
        raise BlockError("BENCHMARK_INPUT_LEDGER_OUTPUT_INVALID")
    raw_path.parent.mkdir(mode=0o700)
    receipt_path.parent.mkdir(mode=0o700)

    command = build_stack_benchmark_command(
        ghcup_bin=ghcup.fd_path,
        stack_bin=stack.fd_path,
        tool_path=tool_path,
        stack_yaml=stack_yaml_path,
        stack_root=stack_root,
        work_dir=work_dir,
        profile_options=profile["ghcOptions"],
        time_limit_seconds=plan["execution"]["criterionTimeLimitSeconds"],
        native_report=raw_path,
        criterion_prefix=selector["criterionPrefix"],
    )
    environment.update(
        {
            "S1_4X_BENCHMARK_PLAN": str(plan_path),
            "S1_4X_LARGE_FIXTURE_ROOT": str(large_fixture_root),
            "S1_4X_BENCHMARK_QUALIFICATION": str(qualification_path),
            "S1_4X_BENCHMARK_SELECTOR_ID": arguments.selector,
            "S1_4X_BENCHMARK_RUNTIME_IDENTITY": str(runtime_identity_path),
            "S1_4X_BENCHMARK_MARKER_PYTHON": str(
                marker_python.fd_path
            ),
            "S1_4X_BENCHMARK_MARKER_PYTHON_SOURCE_PATH": str(
                marker_python.source_path
            ),
            "S1_4X_BENCHMARK_MARKER_PYTHON_SHA256": marker_python.sha256,
            "S1_4X_BENCHMARK_MARKER_SCRIPT": str(marker_script.fd_path),
            "S1_4X_BENCHMARK_MARKER_SCRIPT_SHA256": marker_script.sha256,
        }
    )
    validate_authoritative_ghc_closure(ghc_closure)
    started_at = _iso_now()
    completed = run_pinned_subprocess(
        command,
        cwd=haskell_root,
        environment=environment,
        pinned_executables=runtime_executables,
        capture_output=False,
        pinned_files=(marker_script, *ghc_closure.pinned_launchers),
        benchmark_python_runtime=benchmark_python_runtime,
    )
    finished_at = _iso_now()
    if completed.returncode != 0:
        raise BlockError(f"INNER_BENCHMARK_FAILED:{completed.returncode}")
    validate_authoritative_ghc_closure(ghc_closure)
    _validate_measurement_qualification(
        path=qualification_path,
        pre_run=qualification,
    )
    if _benchmark_python_runtime() != benchmark_python_runtime:
        raise BlockError("MARKER_IDENTITY_CHANGED_DURING_RUN")
    _require_absolute_regular(raw_path, label="CRITERION_FAMILY_RAW")
    (
        executed_benchmark,
        executed_benchmark_sha256,
        runtime_identity_sha256,
    ) = validate_runtime_identity(
        runtime_identity_path,
        selector_id=arguments.selector,
    )
    artifact = _find_benchmark_artifact(work_directory)
    if (
        artifact != executed_benchmark
        or sha256_file(artifact) != executed_benchmark_sha256
    ):
        raise BlockError("BENCHMARK_ARTIFACT_RUNTIME_IDENTITY_MISMATCH")
    ghc_install_closure = {
        "schemaVersion": "s1.4x-haskell-ghc-install-closure-v1",
        "status": "PASS",
        **authoritative_ghc_closure_receipt(ghc_closure),
    }
    ghc_install_closure["closureSha256"] = canonical_sha256(
        ghc_install_closure
    )
    gate.exclusive_json_write(
        ghc_install_closure_path,
        ghc_install_closure,
    )
    ghc_install_closure_file_sha256 = sha256_file(
        ghc_install_closure_path
    )
    fixture_root = large_fixture_root
    receipt = {
        "schemaVersion": "s1.4x-native-case-execution-receipt-v1",
        "boundaryId": "haskell",
        "selectorId": arguments.selector,
        "caseId": None,
        "commandArgv": command,
        "environment": {"S1_4X_BENCHMARK_SELECTOR_ID": arguments.selector},
        "exitCode": 0,
        "rawEvidencePath": RAW_RELATIVE,
        "rawEvidenceSha256": sha256_file(raw_path),
        "provenance": {
            "planPath": str(plan_path),
            "planSha256": sha256_file(plan_path),
            "fixtureRootPath": str(fixture_root),
            "fixtureFreezeIdentitySha256": canonical_sha256(
                plan["fixtureFreezeIdentity"]
            ),
            "inputLedgerPath": str(input_ledger_path),
            "inputLedgerSha256": sha256_file(input_ledger_path),
            "selectorId": arguments.selector,
            "caseIds": selector["expectedCaseIds"],
            "benchmarkExecutablePath": str(executed_benchmark),
            "benchmarkExecutableSha256": executed_benchmark_sha256,
            "effectiveRuntimeArgumentsSha256": profile["optionsSha256"],
            "candidateProvenance": {
                "kind": "haskell",
                "selectedProfilePath": str(profile_path),
                "selectedProfileSha256": profile_source_closure[
                    "selectedProfileSha256"
                ],
                "selectedProfileId": profile["profileId"],
                "sourceInputManifestPath": str(source_manifest_path),
                "sourceInputManifestSha256": profile_source_closure[
                    "sourceInputManifestSha256"
                ],
                "effectiveCompilerFlagsSha256": profile["optionsSha256"],
                "runtimeIdentityPath": str(runtime_identity_path),
                "runtimeIdentitySha256": runtime_identity_sha256,
                "executedBenchmarkPath": str(executed_benchmark),
                "executedBenchmarkSha256": executed_benchmark_sha256,
                "authoritativeGhcPath": str(
                    authoritative_ghc.source_path
                ),
                "authoritativeGhcPinnedFdPath": str(
                    authoritative_ghc.fd_path
                ),
                "authoritativeGhcSha256": authoritative_ghc.sha256,
                "authoritativeGhcShimPath": str(ghc_shim),
                "scoringCompilerExecutionBinding": (
                    "sealed-elf-fd-with-validated-install-closure"
                ),
                **authoritative_ghc_closure_receipt(ghc_closure),
                "toolchainInstallClosurePath": str(
                    ghc_install_closure_path
                ),
                "toolchainInstallClosurePolicy": (
                    "sealed-critical-elf-fds-and-stable-distribution-metadata"
                ),
                "markerPythonPath": str(marker_python.source_path),
                "markerPythonPinnedFdPath": str(marker_python.fd_path),
                "markerPythonSha256": marker_python.sha256,
                "markerScriptPath": str(marker_script.source_path),
                "markerScriptPinnedFdPath": str(marker_script.fd_path),
                "markerScriptSha256": marker_script.sha256,
                "markerArgv": marker_argv,
                "markerArgvSha256": canonical_sha256(marker_argv),
                "inputLedgerScriptPath": str(ledger_script.source_path),
                "inputLedgerScriptPinnedFdPath": str(
                    ledger_script.fd_path
                ),
                "inputLedgerScriptSha256": ledger_script.sha256,
                "nativeBlockScriptPath": str(native_script.source_path),
                "nativeBlockScriptPinnedFdPath": str(
                    native_script.fd_path
                ),
                "nativeBlockScriptSha256": native_script.sha256,
                "ghcupPath": str(ghcup.source_path),
                "ghcupPinnedFdPath": str(ghcup.fd_path),
                "ghcupSha256": ghcup.sha256,
                "stackPath": str(stack.source_path),
                "stackPinnedFdPath": str(stack.fd_path),
                "stackSha256": stack.sha256,
                "latestGhcPath": str(latest_ghc.source_path),
                "latestGhcPinnedFdPath": str(latest_ghc.fd_path),
                "latestGhcSha256": latest_ghc.sha256,
                "hlintPath": str(hlint.source_path),
                "hlintPinnedFdPath": str(hlint.fd_path),
                "hlintSha256": hlint.sha256,
                "stylishPath": str(stylish.source_path),
                "stylishPinnedFdPath": str(stylish.fd_path),
                "stylishSha256": stylish.sha256,
                "stackYamlPath": str(stack_yaml_path),
                "stackYamlSha256": sha256_file(stack_yaml_path),
                "stackRootPath": str(stack_root),
                "stackWorkDirectory": str(work_dir),
                "stackWorkDirectoryAbsolute": str(work_directory),
                "toolPath": tool_path,
                "selectedGhcOptions": profile["ghcOptions"],
                "baselineCorrectnessSourcePath": profile_source_closure[
                    "baselineCorrectnessSourcePath"
                ],
                "baselineCorrectnessSha256": profile_source_closure[
                    "baselineCorrectnessSha256"
                ],
                "optimizedCorrectnessSourcePath": profile_source_closure[
                    "optimizedCorrectnessSourcePath"
                ],
                "optimizedCorrectnessSha256": profile_source_closure[
                    "optimizedCorrectnessSha256"
                ],
                "qualificationArtifactSourcePath": profile_source_closure[
                    "qualificationArtifactSourcePath"
                ],
                "qualificationArtifactSha256": profile_source_closure[
                    "qualificationArtifactSha256"
                ],
                "toolchainLockPath": str(toolchain_lock_path),
                "toolchainLockSha256": sha256_file(toolchain_lock_path),
                "mergedToolchainProvenancePath": str(merged_provenance_path),
                "mergedToolchainProvenanceSha256": sha256_file(
                    merged_provenance_path
                ),
            },
        },
        "status": "PASS",
    }
    gate.exclusive_json_write(receipt_path, receipt)

    producer_result = _run_shared_json_command(
        [
            str(marker_python.fd_path),
            str(native_script.fd_path),
            "produce-haskell-native",
            "--repo-root",
            str(repo_root),
            "--plan",
            str(plan_path),
            "--block-dir",
            str(block_dir),
            "--selector",
            arguments.selector,
            "--criterion-raw",
            str(raw_path),
            "--execution-receipt",
            str(receipt_path),
            "--input-ledger",
            str(input_ledger_path),
            "--fixture-root",
            str(fixture_root),
            "--selected-profile",
            str(profile_path),
            "--source-input-manifest",
            str(source_manifest_path),
            "--toolchain-lock",
            str(toolchain_lock_path),
            "--toolchain-provenance",
            str(merged_provenance_path),
            "--benchmark-artifact",
            str(artifact),
            "--started-at",
            started_at,
            "--finished-at",
            finished_at,
        ],
        label="HASKELL_NATIVE_PRODUCER",
        cwd=repo_root,
        environment=environment,
        pinned_executables=runtime_executables,
        pinned_files=(
            native_script,
            marker_script,
            ledger_script,
            *ghc_closure.pinned_launchers,
        ),
        benchmark_python_runtime=benchmark_python_runtime,
    )
    if (
        set(producer_result)
        != {
            "boundaryId",
            "selectorId",
            "caseCount",
            "nativeContractValidationSha256",
            "nativeReportSha256",
            "nativeStatisticsSha256",
            "status",
        }
        or producer_result["boundaryId"] != "haskell"
        or producer_result["selectorId"] != arguments.selector
        or producer_result["caseCount"] != len(cases)
        or producer_result["status"] != "PASS"
    ):
        raise BlockError("HASKELL_NATIVE_PRODUCER_OUTPUT_INVALID")
    _require_sha_fields(
        producer_result,
        (
            "nativeContractValidationSha256",
            "nativeReportSha256",
            "nativeStatisticsSha256",
        ),
    )

    block_result = _run_shared_json_command(
        [
            str(marker_python.fd_path),
            str(native_script.fd_path),
            "--repo-root",
            str(repo_root),
            "--plan",
            str(plan_path),
            "--block-dir",
            str(block_dir),
            "--qualification",
            str(qualification_path),
            "--boundary",
            "haskell",
            "--selector",
            arguments.selector,
            "--family",
            arguments.family,
            "--rotation",
            arguments.rotation,
            "--outer-repetition",
            str(outer_repetition),
            "--run-id",
            arguments.run_id,
            "--benchmark-subject-commit",
            arguments.benchmark_subject_commit,
        ],
        label="NATIVE_BENCHMARK_BLOCK",
        cwd=repo_root,
        environment=environment,
        pinned_executables=runtime_executables,
        pinned_files=(
            native_script,
            marker_script,
            ledger_script,
            *ghc_closure.pinned_launchers,
        ),
        benchmark_python_runtime=benchmark_python_runtime,
    )
    if (
        set(block_result)
        != {"boundaryId", "selectorId", "blockResultSha256", "status"}
        or block_result["boundaryId"] != "haskell"
        or block_result["selectorId"] != arguments.selector
        or block_result["status"] != "PASS"
    ):
        raise BlockError("NATIVE_BENCHMARK_BLOCK_OUTPUT_INVALID")
    _require_sha_fields(block_result, ("blockResultSha256",))
    report_validator.validate_block_result(
        result_path,
        plan_path=plan_path,
        native_report_path=native_path,
        expected_boundary_id="haskell",
        expected_selector_id=arguments.selector,
    )
    final_pinned_profile_evidence = load_pinned_profile_evidence()
    if final_pinned_profile_evidence != pinned_profile_evidence:
        raise BlockError("PINNED_PROFILE_EVIDENCE_CHANGED_DURING_BLOCK")
    _, _, _, final_profile_source_closure = _profile_and_source_evidence(
        haskell_root=haskell_root,
        plan_path=plan_path,
        haskell_evidence=haskell_evidence,
        pinned_profile_evidence=final_pinned_profile_evidence,
        benchmark_subject_commit=arguments.benchmark_subject_commit,
    )
    if final_profile_source_closure != profile_source_closure:
        raise BlockError("PROFILE_SOURCE_CLOSURE_CHANGED_DURING_BLOCK")
    if load_pinned_toolchain() != pinned_executables:
        raise BlockError("PINNED_TOOLCHAIN_CHANGED_DURING_BLOCK")
    if _benchmark_python_runtime() != benchmark_python_runtime:
        raise BlockError("BENCHMARK_PYTHON_CHANGED_DURING_BLOCK")
    validate_authoritative_ghc_closure(ghc_closure)
    if (
        sha256_file(ghc_install_closure_path)
        != ghc_install_closure_file_sha256
    ):
        raise BlockError("AUTHORITATIVE_GHC_CLOSURE_EVIDENCE_CHANGED")
    _verify_subject_commit(repo_root, arguments.benchmark_subject_commit)
    return {
        "status": "PASS",
        "selectorId": arguments.selector,
        "caseCount": len(cases),
        "receiptSha256": sha256_file(receipt_path),
        "nativeReportSha256": sha256_file(native_path),
        "blockResultSha256": sha256_file(result_path),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--block-dir", type=Path, required=True)
    parser.add_argument("--qualification", type=Path, required=True)
    parser.add_argument("--boundary", required=True)
    parser.add_argument("--selector", required=True)
    parser.add_argument("--family", required=True)
    parser.add_argument("--rotation", required=True)
    parser.add_argument("--outer-repetition", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--benchmark-subject-commit", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        result = run_block(arguments)
    except (
        BlockError,
        OSError,
        KeyError,
        TypeError,
        ValueError,
        subprocess.SubprocessError,
    ) as exc:
        print(f"HASKELL_BENCHMARK_BLOCK_FAIL:{exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, allow_nan=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
