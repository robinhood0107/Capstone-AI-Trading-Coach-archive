"""S1.4X benchmark host의 disk/memory/load/container/process/affinity를 검증한다."""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import os
import platform
import re
import shutil
import stat
import subprocess
import time
from collections.abc import Mapping, Sequence
from contextlib import suppress
from pathlib import Path
from typing import Any, Protocol

from oracle_common import OracleContractError, atomic_write_json, sha256_bytes

SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
SELF_FD_PATTERN = re.compile(r"^/proc/self/fd/([3-9]|[1-9][0-9]+)$")
DOCKER_PS_TIMEOUT_SECONDS = 10
AMBIENT_ACTIVITY_OVERRIDE_ENV = "S1_4X_IGNORE_AMBIENT_HOST_ACTIVITY"
AMBIENT_ACTIVITY_OVERRIDE_REASON = "USER_APPROVED_OBSERVE_ONLY"


@dataclasses.dataclass(frozen=True)
class ProcessSample:
    """PID reuse와 CPU delta 판정에 필요한 `/proc/<pid>/stat` 최소 필드다."""

    pid: int
    ppid: int
    start_time_ticks: int
    cpu_ticks: int
    command: str


@dataclasses.dataclass(frozen=True)
class ProcessSnapshot:
    """한 시점의 monotonic time과 process identity/CPU tick 집합이다."""

    monotonic_seconds: float
    processes: Mapping[int, ProcessSample]


@dataclasses.dataclass(frozen=True)
class RunningContainer:
    """Docker 실행 컨테이너의 ID와 host-validity 분류에 필요한 라벨만 보존한다."""

    container_id: str
    mcp_server_name: str | None


@dataclasses.dataclass(frozen=True)
class EnvironmentPolicy:
    """Gate 1에서 동결하는 exact benchmark host 임계값이다."""

    cpu_set: frozenset[int]
    min_home_free_bytes: int = 32_212_254_720
    min_available_memory_bytes: int = 4_294_967_296
    max_normalized_load1: float = 0.10
    load_samples: int = 3
    sample_interval_seconds: float = 30.0
    max_quiet_wait_seconds: float = 600.0
    max_running_containers: int = 4
    external_process_sample_seconds: float = 30.0
    max_external_process_cpu_percent: float = 5.0
    allowed_process_root_pid: int = 0


class EnvironmentAdapter(Protocol):
    """실제 host와 deterministic fake가 공유하는 side-effect boundary다."""

    def home_free_bytes(self, home: Path) -> int: ...

    def available_memory_bytes(self) -> int: ...

    def logical_cpu_count(self) -> int: ...

    def normalized_affinity(self) -> frozenset[int]: ...

    def set_affinity(self, cpu_set: frozenset[int]) -> None: ...

    def load1(self) -> float: ...

    def running_containers(self) -> list[RunningContainer]: ...

    def process_snapshot(self) -> ProcessSnapshot: ...

    def clock_ticks_per_second(self) -> int: ...

    def monotonic(self) -> float: ...

    def sleep(self, seconds: float) -> None: ...

    def metadata(self) -> dict[str, Any]: ...


class LocalEnvironmentAdapter:
    """Linux/WSL host를 stdlib와 bounded Docker subprocess만으로 읽는다."""

    def __init__(
        self,
        *,
        docker_bin: Path | str = "docker",
        docker_sha256: str | None = None,
    ) -> None:
        self._docker_command = str(docker_bin)
        self._docker_sha256: str | None = None
        self._docker_pass_fds: tuple[int, ...] = ()
        self._owned_docker_fd: int | None = None
        if self._docker_command == "docker" and docker_sha256 is None:
            return
        if (
            not Path(self._docker_command).is_absolute()
            or docker_sha256 is None
            or SHA256_PATTERN.fullmatch(docker_sha256) is None
        ):
            raise OracleContractError(
                "explicit Docker executable requires an absolute path and SHA-256"
            )
        descriptor_match = SELF_FD_PATTERN.fullmatch(self._docker_command)
        if descriptor_match is not None:
            descriptor = int(descriptor_match.group(1))
            try:
                descriptor_stat = os.fstat(descriptor)
            except OSError as exc:
                raise OracleContractError("Docker executable FD is not live") from exc
            if (
                not stat.S_ISREG(descriptor_stat.st_mode)
                or descriptor_stat.st_mode & 0o111 == 0
            ):
                raise OracleContractError(
                    "Docker executable FD is not an executable regular file"
                )
            actual_sha256 = self._sha256_descriptor(
                descriptor,
                expected_size=descriptor_stat.st_size,
            )
            self._docker_pass_fds = (descriptor,)
        else:
            flags = os.O_RDONLY | os.O_CLOEXEC
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            try:
                descriptor = os.open(self._docker_command, flags)
                descriptor_stat = os.fstat(descriptor)
            except OSError as exc:
                raise OracleContractError(
                    "Docker executable path is unavailable or unsafe"
                ) from exc
            if (
                not stat.S_ISREG(descriptor_stat.st_mode)
                or descriptor_stat.st_mode & 0o111 == 0
            ):
                os.close(descriptor)
                raise OracleContractError(
                    "Docker executable path is not an executable regular file"
                )
            try:
                actual_sha256 = self._sha256_descriptor(
                    descriptor,
                    expected_size=descriptor_stat.st_size,
                )
            except BaseException:
                os.close(descriptor)
                raise
            self._owned_docker_fd = descriptor
            self._docker_command = f"/proc/self/fd/{descriptor}"
            self._docker_pass_fds = (descriptor,)
        if actual_sha256 != docker_sha256:
            self.close()
            raise OracleContractError("Docker executable SHA-256 mismatch")
        self._docker_sha256 = actual_sha256

    @staticmethod
    def _sha256_descriptor(descriptor: int, *, expected_size: int) -> str:
        digest = hashlib.sha256()
        offset = 0
        while offset < expected_size:
            try:
                chunk = os.pread(
                    descriptor,
                    min(1024 * 1024, expected_size - offset),
                    offset,
                )
            except OSError as exc:
                raise OracleContractError(
                    "Docker executable could not be hashed"
                ) from exc
            if not chunk:
                raise OracleContractError("Docker executable changed during hashing")
            digest.update(chunk)
            offset += len(chunk)
        return digest.hexdigest()

    def close(self) -> None:
        """Adapter가 직접 연 Docker descriptor만 닫는다."""

        if self._owned_docker_fd is not None:
            os.close(self._owned_docker_fd)
            self._owned_docker_fd = None
            self._docker_pass_fds = ()

    def __del__(self) -> None:
        with suppress(OSError):
            self.close()

    def home_free_bytes(self, home: Path) -> int:
        return shutil.disk_usage(home).free

    def available_memory_bytes(self) -> int:
        for line in Path("/proc/meminfo").read_text(encoding="ascii").splitlines():
            if line.startswith("MemAvailable:"):
                fields = line.split()
                if len(fields) == 3 and fields[2] == "kB":
                    return int(fields[1]) * 1024
        raise OracleContractError("/proc/meminfo has no MemAvailable")

    def logical_cpu_count(self) -> int:
        # Parent runner가 먼저 CPU 0에 pin되어도 host 분모는 pin 이전 값이어야 한다.
        logical_cpu_count = os.cpu_count()
        if logical_cpu_count is None or logical_cpu_count <= 0:
            raise OracleContractError("logical CPU count is unavailable")
        return logical_cpu_count

    def normalized_affinity(self) -> frozenset[int]:
        return frozenset(os.sched_getaffinity(0))

    def set_affinity(self, cpu_set: frozenset[int]) -> None:
        os.sched_setaffinity(0, cpu_set)

    def load1(self) -> float:
        return os.getloadavg()[0]

    def running_containers(self) -> list[RunningContainer]:
        try:
            completed = subprocess.run(
                [
                    self._docker_command,
                    "ps",
                    "--format",
                    '{{.ID}}\\t{{.Label "io.modelcontextprotocol.server.name"}}',
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=DOCKER_PS_TIMEOUT_SECONDS,
                pass_fds=self._docker_pass_fds,
            )
        except subprocess.TimeoutExpired as exc:
            raise OracleContractError(
                f"docker ps timed out after {DOCKER_PS_TIMEOUT_SECONDS} seconds"
            ) from exc
        if completed.returncode != 0:
            raise OracleContractError(
                f"docker ps failed with exit {completed.returncode}"
            )
        containers: list[RunningContainer] = []
        for line in completed.stdout.splitlines():
            if not line:
                continue
            container_id, separator, mcp_server_name = line.partition("\t")
            if not container_id or not separator:
                raise OracleContractError("docker ps returned malformed container row")
            containers.append(
                RunningContainer(
                    container_id=container_id,
                    mcp_server_name=mcp_server_name or None,
                )
            )
        return containers

    def process_snapshot(self) -> ProcessSnapshot:
        processes: dict[int, ProcessSample] = {}
        for directory in Path("/proc").iterdir():
            if not directory.name.isdecimal():
                continue
            try:
                raw = (directory / "stat").read_text(encoding="utf-8")
                sample = _parse_proc_stat(raw)
            except (FileNotFoundError, PermissionError, ProcessLookupError, ValueError):
                continue
            processes[sample.pid] = sample
        return ProcessSnapshot(self.monotonic(), processes)

    def clock_ticks_per_second(self) -> int:
        return int(os.sysconf("SC_CLK_TCK"))

    def monotonic(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)

    def metadata(self) -> dict[str, Any]:
        kernel = platform.release()
        is_wsl = "microsoft" in kernel.lower()
        fields: dict[str, Any] = {
            "kernelRelease": kernel,
            "platform": platform.system(),
            "machine": platform.machine(),
            "wsl": is_wsl,
        }
        visibility = {
            "cpuGovernor": Path(
                "/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor"
            ),
            "cpuFrequency": Path(
                "/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq"
            ),
            "temperature": Path("/sys/class/thermal/thermal_zone0/temp"),
        }
        for field, path in visibility.items():
            try:
                fields[field] = path.read_text(encoding="ascii").strip()
            except OSError:
                fields[field] = "UNAVAILABLE_WSL" if is_wsl else "UNAVAILABLE"
        return fields


def _parse_proc_stat(raw: str) -> ProcessSample:
    left_parenthesis = raw.find("(")
    right_parenthesis = raw.rfind(")")
    if left_parenthesis <= 0 or right_parenthesis <= left_parenthesis:
        raise ValueError("malformed /proc stat")
    pid = int(raw[:left_parenthesis].strip())
    command = raw[left_parenthesis + 1 : right_parenthesis]
    remainder = raw[right_parenthesis + 1 :].strip().split()
    if len(remainder) < 20:
        raise ValueError("short /proc stat")
    # remainder[0]은 field 3(state)다.
    ppid = int(remainder[1])
    user_ticks = int(remainder[11])
    system_ticks = int(remainder[12])
    start_time_ticks = int(remainder[19])
    return ProcessSample(
        pid=pid,
        ppid=ppid,
        start_time_ticks=start_time_ticks,
        cpu_ticks=user_ticks + system_ticks,
        command=command,
    )


def _record(
    checks: list[dict[str, Any]],
    *,
    check_id: str,
    expected: Any,
    actual: Any,
    passed: bool,
    evidence: Any | None = None,
) -> None:
    checks.append(
        {
            "id": check_id,
            "expected": expected,
            "actual": actual,
            "status": "PASS" if passed else "FAIL",
            "evidence": evidence,
        }
    )


def _allowed_processes(
    snapshot: ProcessSnapshot,
    *,
    root_pid: int,
) -> tuple[set[int], tuple[int, int] | None]:
    root = snapshot.processes.get(root_pid)
    if root is None:
        return set(), None
    allowed = {root_pid}
    changed = True
    while changed:
        changed = False
        for process in snapshot.processes.values():
            if process.ppid in allowed and process.pid not in allowed:
                allowed.add(process.pid)
                changed = True
    ancestor_pid = root.ppid
    visited: set[int] = set()
    while ancestor_pid > 0 and ancestor_pid not in visited:
        visited.add(ancestor_pid)
        ancestor = snapshot.processes.get(ancestor_pid)
        if ancestor is None:
            break
        # ancestor 자체만 허용하며 그 ancestor의 다른 descendant/sibling은 허용하지 않는다.
        allowed.add(ancestor.pid)
        ancestor_pid = ancestor.ppid
    return allowed, (root.pid, root.start_time_ticks)


def _process_cpu_result(
    first: ProcessSnapshot,
    second: ProcessSnapshot,
    *,
    root_pid: int,
    clock_ticks_per_second: int,
    threshold_percent: float,
) -> dict[str, Any]:
    first_allowed, first_root_identity = _allowed_processes(first, root_pid=root_pid)
    second_allowed, second_root_identity = _allowed_processes(second, root_pid=root_pid)
    elapsed = second.monotonic_seconds - first.monotonic_seconds
    if elapsed <= 0.0 or clock_ticks_per_second <= 0:
        return {
            "passed": False,
            "elapsedSeconds": elapsed,
            "pidReuse": [],
            "external": [],
            "reason": "invalid process sample interval or clock tick rate",
        }
    pid_reuse: list[dict[str, int]] = []
    external: list[dict[str, Any]] = []
    for pid, current in second.processes.items():
        previous = first.processes.get(pid)
        if previous is not None and previous.start_time_ticks != current.start_time_ticks:
            pid_reuse.append(
                {
                    "pid": pid,
                    "firstStartTimeTicks": previous.start_time_ticks,
                    "secondStartTimeTicks": current.start_time_ticks,
                }
            )
            continue
        if pid in second_allowed or pid in first_allowed:
            continue
        delta_ticks = (
            current.cpu_ticks
            if previous is None
            else max(0, current.cpu_ticks - previous.cpu_ticks)
        )
        cpu_percent = delta_ticks / clock_ticks_per_second / elapsed * 100.0
        if cpu_percent >= threshold_percent:
            external.append(
                {
                    "pid": pid,
                    "startTimeTicks": current.start_time_ticks,
                    "command": current.command,
                    "cpuPercent": cpu_percent,
                }
            )
    root_stable = first_root_identity is not None and first_root_identity == second_root_identity
    return {
        "passed": root_stable and not pid_reuse and not external,
        "elapsedSeconds": elapsed,
        "rootIdentityStable": root_stable,
        "pidReuse": pid_reuse,
        "external": external,
    }


def _sample_quiet_load(
    adapter: EnvironmentAdapter,
    *,
    policy: EnvironmentPolicy,
    logical_cpu_count: int,
) -> dict[str, Any]:
    if logical_cpu_count <= 0:
        return {
            "passed": False,
            "windows": [],
            "reason": "logical CPU count is not positive",
        }
    started = adapter.monotonic()
    deadline = started + policy.max_quiet_wait_seconds
    windows: list[list[float]] = []
    while True:
        samples: list[float] = []
        complete = True
        for index in range(policy.load_samples):
            if index:
                if adapter.monotonic() + policy.sample_interval_seconds > deadline:
                    complete = False
                    break
                adapter.sleep(policy.sample_interval_seconds)
            try:
                normalized = adapter.load1() / logical_cpu_count
            except (OSError, OracleContractError):
                normalized = float("inf")
            samples.append(normalized)
        windows.append(samples)
        if (
            complete
            and len(samples) == policy.load_samples
            and all(sample <= policy.max_normalized_load1 for sample in samples)
        ):
            return {
                "passed": True,
                "windows": windows,
                "elapsedSeconds": adapter.monotonic() - started,
            }
        if not complete or adapter.monotonic() >= deadline:
            return {
                "passed": False,
                "windows": windows,
                "elapsedSeconds": adapter.monotonic() - started,
            }


def _sample_running_containers(
    adapter: EnvironmentAdapter,
    *,
    policy: EnvironmentPolicy,
    ignore_limit: bool = False,
) -> dict[str, Any]:
    """Docker 조회 실패와 일시적 초과를 기존 quiet-window 안에서만 재시도한다."""

    started = adapter.monotonic()
    deadline = started + policy.max_quiet_wait_seconds
    attempts: list[dict[str, Any]] = []
    while True:
        try:
            containers = adapter.running_containers()
        except (OSError, OracleContractError) as exc:
            actual: int | str = f"UNAVAILABLE:{type(exc).__name__}"
            attempt_status = "UNAVAILABLE"
            container_ids: list[str] = []
            excluded_container_ids: list[str] = []
            total_count: int | str = actual
        else:
            excluded = [
                container
                for container in containers
                if container.mcp_server_name is not None
            ]
            eligible = [
                container
                for container in containers
                if container.mcp_server_name is None
            ]
            actual = len(eligible)
            total_count = len(containers)
            container_ids = [container.container_id for container in eligible]
            excluded_container_ids = [container.container_id for container in excluded]
            attempt_status = (
                "PASS"
                if ignore_limit or actual <= policy.max_running_containers
                else "TOO_MANY"
            )
        attempts.append(
            {
                "elapsedSeconds": adapter.monotonic() - started,
                "actual": actual,
                "containerIds": container_ids,
                "totalCount": total_count,
                "excludedContainerCount": len(excluded_container_ids),
                "excludedContainerIds": excluded_container_ids,
                "exclusionReason": (
                    "MCP_INFRASTRUCTURE_LABEL:io.modelcontextprotocol.server.name"
                    if excluded_container_ids
                    else None
                ),
                "limitEnforced": not ignore_limit,
                "overrideReason": (
                    AMBIENT_ACTIVITY_OVERRIDE_REASON if ignore_limit else None
                ),
                "status": attempt_status,
            }
        )
        if attempt_status == "PASS":
            return {
                "passed": True,
                "actual": actual,
                "attempts": attempts,
                "elapsedSeconds": adapter.monotonic() - started,
            }
        now = adapter.monotonic()
        if now >= deadline:
            return {
                "passed": False,
                "actual": actual,
                "attempts": attempts,
                "elapsedSeconds": now - started,
            }
        adapter.sleep(min(policy.sample_interval_seconds, deadline - now))


def validate_environment(
    home: Path,
    *,
    policy: EnvironmentPolicy,
    adapter: EnvironmentAdapter | None = None,
    ignore_ambient_host_activity: bool = False,
) -> dict[str, Any]:
    """모든 host check를 끝까지 수집해 typed aggregate result를 반환한다."""

    source = adapter or LocalEnvironmentAdapter()
    checks: list[dict[str, Any]] = []
    try:
        home_free = source.home_free_bytes(home)
        _record(
            checks,
            check_id="disk.home-free-bytes",
            expected=f">={policy.min_home_free_bytes}",
            actual=home_free,
            passed=home_free >= policy.min_home_free_bytes,
        )
    except (OSError, OracleContractError) as exc:
        _record(
            checks,
            check_id="disk.home-free-bytes",
            expected=f">={policy.min_home_free_bytes}",
            actual=f"UNAVAILABLE:{type(exc).__name__}",
            passed=False,
        )
    try:
        available_memory = source.available_memory_bytes()
        _record(
            checks,
            check_id="memory.available-bytes",
            expected=f">={policy.min_available_memory_bytes}",
            actual=available_memory,
            passed=available_memory >= policy.min_available_memory_bytes,
        )
    except (OSError, OracleContractError) as exc:
        _record(
            checks,
            check_id="memory.available-bytes",
            expected=f">={policy.min_available_memory_bytes}",
            actual=f"UNAVAILABLE:{type(exc).__name__}",
            passed=False,
        )

    # 부모가 이미 CPU 0에 고정됐어도 system logical CPU 수를 host load 분모로 사용한다.
    try:
        logical_cpu_count = source.logical_cpu_count()
        _record(
            checks,
            check_id="cpu.logical-count",
            expected=">=1",
            actual=logical_cpu_count,
            passed=logical_cpu_count >= 1,
            evidence={"countSource": "SYSTEM_LOGICAL_CPU_COUNT"},
        )
    except (OSError, OracleContractError, ValueError):
        logical_cpu_count = 0
        _record(
            checks,
            check_id="cpu.logical-count",
            expected=">=1",
            actual="UNAVAILABLE",
            passed=False,
        )

    try:
        source.set_affinity(policy.cpu_set)
        affinity = source.normalized_affinity()
        _record(
            checks,
            check_id="cpu.affinity-round-trip",
            expected=sorted(policy.cpu_set),
            actual=sorted(affinity),
            passed=affinity == policy.cpu_set,
        )
    except (OSError, OracleContractError, ValueError) as exc:
        _record(
            checks,
            check_id="cpu.affinity-round-trip",
            expected=sorted(policy.cpu_set),
            actual=f"UNAVAILABLE:{type(exc).__name__}",
            passed=False,
        )

    container_result = _sample_running_containers(
        source,
        policy=policy,
        ignore_limit=ignore_ambient_host_activity,
    )
    _record(
        checks,
        check_id="docker.running-containers",
        expected={
            "max": policy.max_running_containers,
            "retryIntervalSeconds": policy.sample_interval_seconds,
            "maxQuietWaitSeconds": policy.max_quiet_wait_seconds,
        },
        actual=container_result["actual"],
        passed=bool(container_result["passed"]),
        evidence={
            "attempts": container_result["attempts"],
            "elapsedSeconds": container_result["elapsedSeconds"],
            "limitEnforced": not ignore_ambient_host_activity,
            "overrideReason": (
                AMBIENT_ACTIVITY_OVERRIDE_REASON
                if ignore_ambient_host_activity
                else None
            ),
        },
    )

    load_result = _sample_quiet_load(
        source,
        policy=policy,
        logical_cpu_count=logical_cpu_count,
    )
    _record(
        checks,
        check_id="load.normalized-load1-window",
        expected={
            "max": policy.max_normalized_load1,
            "samples": policy.load_samples,
            "intervalSeconds": policy.sample_interval_seconds,
            "maxQuietWaitSeconds": policy.max_quiet_wait_seconds,
            "logicalCpuCountSource": "SYSTEM_LOGICAL_CPU_COUNT",
        },
        actual=load_result.get("windows"),
        passed=bool(load_result["passed"]),
        evidence={
            "elapsedSeconds": load_result.get("elapsedSeconds"),
            "normalizationLogicalCpuCount": logical_cpu_count,
        },
    )

    if ignore_ambient_host_activity:
        _record(
            checks,
            check_id="process.external-cpu",
            expected={
                "rootPid": policy.allowed_process_root_pid,
                "sampleSeconds": policy.external_process_sample_seconds,
                "externalCpuPercent": (
                    f"<{policy.max_external_process_cpu_percent}"
                ),
                "pidReuse": 0,
                "enforced": False,
            },
            actual={
                "externalCount": "NOT_MEASURED",
                "pidReuseCount": "NOT_MEASURED",
                "rootIdentityStable": "NOT_MEASURED",
            },
            passed=True,
            evidence={
                "limitEnforced": False,
                "overrideReason": AMBIENT_ACTIVITY_OVERRIDE_REASON,
            },
        )
    else:
        try:
            first_process_snapshot = source.process_snapshot()
            source.sleep(policy.external_process_sample_seconds)
            second_process_snapshot = source.process_snapshot()
            process_result = _process_cpu_result(
                first_process_snapshot,
                second_process_snapshot,
                root_pid=policy.allowed_process_root_pid,
                clock_ticks_per_second=source.clock_ticks_per_second(),
                threshold_percent=policy.max_external_process_cpu_percent,
            )
            _record(
                checks,
                check_id="process.external-cpu",
                expected={
                    "rootPid": policy.allowed_process_root_pid,
                    "sampleSeconds": policy.external_process_sample_seconds,
                    "externalCpuPercent": (
                        f"<{policy.max_external_process_cpu_percent}"
                    ),
                    "pidReuse": 0,
                },
                actual={
                    "externalCount": len(process_result.get("external", [])),
                    "pidReuseCount": len(process_result.get("pidReuse", [])),
                    "rootIdentityStable": process_result.get("rootIdentityStable"),
                },
                passed=bool(process_result["passed"]),
                evidence=process_result,
            )
        except (OSError, OracleContractError, ValueError) as exc:
            _record(
                checks,
                check_id="process.external-cpu",
                expected="valid two-snapshot process audit",
                actual=f"UNAVAILABLE:{type(exc).__name__}",
                passed=False,
            )

    try:
        metadata = source.metadata()
    except (OSError, OracleContractError):
        metadata = {"status": "UNAVAILABLE"}
    policy_json = {
        **dataclasses.asdict(policy),
        "cpu_set": sorted(policy.cpu_set),
    }
    host_id_payload = (
        f"{metadata.get('kernelRelease')}|{metadata.get('machine')}|{logical_cpu_count}"
    ).encode()
    failures = [check for check in checks if check["status"] != "PASS"]
    return {
        "schemaVersion": "s1.4x-host-validity-v1",
        "policy": policy_json,
        "portableHostIdSha256": sha256_bytes(host_id_payload),
        "metadata": metadata,
        "checks": checks,
        "failureCount": len(failures),
        "status": "PASS" if not failures else "FAIL",
    }


def _parse_cpu_set(value: str) -> frozenset[int]:
    cpus: set[int] = set()
    for component in value.split(","):
        component = component.strip()
        if not component:
            raise argparse.ArgumentTypeError("CPU set contains an empty component")
        if "-" in component:
            start_text, end_text = component.split("-", maxsplit=1)
            start, end = int(start_text), int(end_text)
            if start < 0 or end < start:
                raise argparse.ArgumentTypeError("CPU range is invalid")
            cpus.update(range(start, end + 1))
        else:
            cpu = int(component)
            if cpu < 0:
                raise argparse.ArgumentTypeError("CPU index is negative")
            cpus.add(cpu)
    if not cpus:
        raise argparse.ArgumentTypeError("CPU set is empty")
    return frozenset(cpus)


def _parse_arguments(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Collect all S1.4X host validity checks into typed JSON. "
            "The validator never stops user processes or containers."
        )
    )
    parser.add_argument("--home", required=True, type=Path)
    parser.add_argument("--docker-bin", default="docker")
    parser.add_argument("--docker-sha256")
    parser.add_argument("--cpu-set", required=True, type=_parse_cpu_set)
    parser.add_argument("--min-home-free-bytes", type=int, default=32_212_254_720)
    parser.add_argument("--min-available-memory-bytes", type=int, default=4_294_967_296)
    parser.add_argument("--max-normalized-load1", type=float, default=0.10)
    parser.add_argument("--load-samples", type=int, default=3)
    parser.add_argument("--sample-interval-seconds", type=float, default=30.0)
    parser.add_argument("--max-quiet-wait-seconds", type=float, default=600.0)
    parser.add_argument("--max-running-containers", type=int, default=4)
    parser.add_argument("--external-process-sample-seconds", type=float, default=30.0)
    parser.add_argument("--max-external-process-cpu-percent", type=float, default=5.0)
    parser.add_argument("--allowed-process-root-pid", type=int, required=True)
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args(argv)


def _policy_from_arguments(arguments: argparse.Namespace) -> EnvironmentPolicy:
    integer_nonnegative = (
        "min_home_free_bytes",
        "min_available_memory_bytes",
        "max_running_containers",
    )
    for field in integer_nonnegative:
        if getattr(arguments, field) < 0:
            raise OracleContractError(f"{field} must be non-negative")
    if arguments.load_samples < 1:
        raise OracleContractError("load_samples must be positive")
    for field in (
        "sample_interval_seconds",
        "max_quiet_wait_seconds",
        "external_process_sample_seconds",
        "max_external_process_cpu_percent",
    ):
        if getattr(arguments, field) <= 0:
            raise OracleContractError(f"{field} must be positive")
    if arguments.max_normalized_load1 < 0.0:
        raise OracleContractError("max_normalized_load1 must be non-negative")
    if arguments.allowed_process_root_pid <= 0:
        raise OracleContractError("allowed_process_root_pid must be positive")
    return EnvironmentPolicy(
        cpu_set=arguments.cpu_set,
        min_home_free_bytes=arguments.min_home_free_bytes,
        min_available_memory_bytes=arguments.min_available_memory_bytes,
        max_normalized_load1=arguments.max_normalized_load1,
        load_samples=arguments.load_samples,
        sample_interval_seconds=arguments.sample_interval_seconds,
        max_quiet_wait_seconds=arguments.max_quiet_wait_seconds,
        max_running_containers=arguments.max_running_containers,
        external_process_sample_seconds=arguments.external_process_sample_seconds,
        max_external_process_cpu_percent=arguments.max_external_process_cpu_percent,
        allowed_process_root_pid=arguments.allowed_process_root_pid,
    )


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint이며 앞 check 실패 뒤 마지막 성공도 aggregate 실패로 보존한다."""

    arguments = _parse_arguments(argv)
    adapter: LocalEnvironmentAdapter | None = None
    try:
        policy = _policy_from_arguments(arguments)
        adapter = LocalEnvironmentAdapter(
            docker_bin=arguments.docker_bin,
            docker_sha256=arguments.docker_sha256,
        )
        report = validate_environment(
            arguments.home.resolve(),
            policy=policy,
            adapter=adapter,
            ignore_ambient_host_activity=(
                os.environ.get(AMBIENT_ACTIVITY_OVERRIDE_ENV) == "1"
            ),
        )
    except OracleContractError as exc:
        report = {
            "schemaVersion": "s1.4x-host-validity-v1",
            "policy": None,
            "portableHostIdSha256": None,
            "metadata": {},
            "checks": [
                {
                    "id": "arguments",
                    "expected": "valid frozen environment policy",
                    "actual": str(exc),
                    "status": "FAIL",
                    "evidence": None,
                }
            ],
            "failureCount": 1,
            "status": "FAIL",
        }
    finally:
        if adapter is not None:
            adapter.close()
    atomic_write_json(arguments.output, report)
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
