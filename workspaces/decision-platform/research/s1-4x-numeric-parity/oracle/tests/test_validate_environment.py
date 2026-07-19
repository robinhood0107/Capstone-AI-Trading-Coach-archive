from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

import pytest

from oracle_common import OracleContractError
from validate_environment import (
    EnvironmentPolicy,
    LocalEnvironmentAdapter,
    ProcessSample,
    ProcessSnapshot,
    _parse_proc_stat,
    _sample_quiet_load,
    validate_environment,
)

GIB = 1024**3


class FakeEnvironment:
    """시간과 `/proc` snapshot을 외부 상태 없이 재현하는 host adapter다."""

    def __init__(self) -> None:
        self.free_bytes = 30 * GIB
        self.memory_bytes = 4 * GIB
        self.affinity = frozenset({0, 1})
        self.logical_cpus = 2
        self.loads = [0.2, 0.2, 0.2]
        self.containers = [f"container-{index}" for index in range(1, 5)]
        self.now = 0.0
        self.external_delta_ticks = 149
        self.reuse_pid = False
        self.snapshot_index = 0

    def home_free_bytes(self, home: Path) -> int:
        del home
        return self.free_bytes

    def available_memory_bytes(self) -> int:
        return self.memory_bytes

    def logical_cpu_count(self) -> int:
        return self.logical_cpus

    def normalized_affinity(self) -> frozenset[int]:
        return self.affinity

    def set_affinity(self, cpu_set: frozenset[int]) -> None:
        del cpu_set

    def load1(self) -> float:
        return self.loads.pop(0)

    def running_containers(self) -> list[str]:
        return self.containers

    def process_snapshot(self) -> ProcessSnapshot:
        second = self.snapshot_index == 1
        self.snapshot_index += 1
        processes = {
            1: ProcessSample(1, 0, 1, 10, "parent"),
            10: ProcessSample(10, 1, 100, 100 + (10 if second else 0), "root"),
            11: ProcessSample(11, 10, 110, 20 + (10 if second else 0), "child"),
            20: ProcessSample(
                20,
                1,
                201 if second and self.reuse_pid else 200,
                self.external_delta_ticks if second else 0,
                "sibling",
            ),
        }
        return ProcessSnapshot(self.now, processes)

    def clock_ticks_per_second(self) -> int:
        return 100

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds

    def metadata(self) -> dict[str, Any]:
        return {
            "kernelRelease": "test-kernel",
            "machine": "x86_64",
            "wsl": True,
        }


def _policy() -> EnvironmentPolicy:
    return EnvironmentPolicy(
        cpu_set=frozenset({0, 1}),
        min_home_free_bytes=30 * GIB,
        min_available_memory_bytes=4 * GIB,
        max_normalized_load1=0.10,
        load_samples=3,
        sample_interval_seconds=30.0,
        max_quiet_wait_seconds=60.0,
        max_running_containers=4,
        external_process_sample_seconds=30.0,
        max_external_process_cpu_percent=5.0,
        allowed_process_root_pid=10,
    )


def _checks(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {check["id"]: check for check in report["checks"]}


def test_default_policy_freezes_every_gate1_host_threshold() -> None:
    policy = EnvironmentPolicy(cpu_set=frozenset({0}), allowed_process_root_pid=10)

    assert policy.min_home_free_bytes == 30 * GIB
    assert policy.min_available_memory_bytes == 4 * GIB
    assert policy.max_normalized_load1 == 0.10
    assert policy.load_samples == 3
    assert policy.sample_interval_seconds == 30.0
    assert policy.max_quiet_wait_seconds == 600.0
    assert policy.max_running_containers == 4
    assert policy.external_process_sample_seconds == 30.0
    assert policy.max_external_process_cpu_percent == 5.0


def test_quiet_load_retries_until_exact_600_second_deadline() -> None:
    adapter = FakeEnvironment()
    adapter.loads = [0.200001] * 100
    policy = EnvironmentPolicy(
        cpu_set=frozenset({0, 1}),
        allowed_process_root_pid=10,
    )

    result = _sample_quiet_load(adapter, policy=policy, logical_cpu_count=2)

    assert result["passed"] is False
    assert result["elapsedSeconds"] == 600.0
    assert adapter.now == 600.0
    assert all(
        sample > policy.max_normalized_load1
        for window in result["windows"]
        for sample in window
    )


def test_exact_host_boundaries_pass() -> None:
    report = validate_environment(Path("/unused"), policy=_policy(), adapter=FakeEnvironment())

    assert report["status"] == "PASS"
    assert report["failureCount"] == 0
    assert all(check["status"] == "PASS" for check in report["checks"])


def test_normalized_load_uses_effective_logical_cpu_count_before_affinity_pin() -> None:
    class AffinityShrinksVisibleCpuCount(FakeEnvironment):
        def __init__(self) -> None:
            super().__init__()
            self.logical_cpus = 8
            self.loads = [0.8, 0.8, 0.8]

        def set_affinity(self, cpu_set: frozenset[int]) -> None:
            self.affinity = cpu_set
            self.logical_cpus = len(cpu_set)

    report = validate_environment(
        Path("/unused"),
        policy=_policy(),
        adapter=AffinityShrinksVisibleCpuCount(),
    )
    checks = _checks(report)

    assert checks["cpu.logical-count"]["actual"] == 8
    assert checks["cpu.logical-count"]["evidence"]["samplePhase"] == "PRE_AFFINITY_PIN"
    assert checks["cpu.affinity-round-trip"]["status"] == "PASS"
    assert checks["load.normalized-load1-window"]["actual"] == [
        [pytest.approx(0.10), pytest.approx(0.10), pytest.approx(0.10)]
    ]
    assert (
        checks["load.normalized-load1-window"]["evidence"][
            "normalizationLogicalCpuCount"
        ]
        == 8
    )
    assert checks["load.normalized-load1-window"]["status"] == "PASS"


def test_early_failure_is_not_erased_by_last_success() -> None:
    adapter = FakeEnvironment()
    adapter.free_bytes -= 1

    report = validate_environment(Path("/unused"), policy=_policy(), adapter=adapter)
    checks = _checks(report)

    assert checks["disk.home-free-bytes"]["status"] == "FAIL"
    assert checks["process.external-cpu"]["status"] == "PASS"
    assert report["status"] == "FAIL"
    assert report["failureCount"] == 1


@pytest.mark.parametrize(
    "mutation",
    ["memory", "load", "container", "affinity", "external-threshold", "pid-reuse"],
)
def test_each_frozen_negative_boundary_fails(mutation: str) -> None:
    adapter = FakeEnvironment()
    if mutation == "memory":
        adapter.memory_bytes -= 1
    elif mutation == "load":
        adapter.loads = [0.200001, 0.200001, 0.200001]
    elif mutation == "container":
        adapter.containers.append("container-5")
    elif mutation == "affinity":
        adapter.affinity = frozenset({0})
    elif mutation == "external-threshold":
        # 150 / 100 ticks/s / 30 s * 100 == exact 5%, which is rejected.
        adapter.external_delta_ticks = 150
    elif mutation == "pid-reuse":
        adapter.reuse_pid = True

    report = validate_environment(Path("/unused"), policy=_policy(), adapter=adapter)

    assert report["status"] == "FAIL"
    assert report["failureCount"] >= 1


def test_container_count_policy_accepts_four_and_rejects_five() -> None:
    accepted = FakeEnvironment()
    accepted_report = validate_environment(
        Path("/unused"),
        policy=_policy(),
        adapter=accepted,
    )

    rejected = FakeEnvironment()
    rejected.containers.append("container-5")
    rejected_report = validate_environment(
        Path("/unused"),
        policy=_policy(),
        adapter=rejected,
    )

    assert _checks(accepted_report)["docker.running-containers"]["status"] == "PASS"
    assert _checks(rejected_report)["docker.running-containers"]["status"] == "FAIL"


def test_local_adapter_uses_the_injected_docker_identity_not_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    trusted = tmp_path / "trusted-docker"
    trusted.write_text(
        "#!/usr/bin/bash\n"
        "test \"$1\" = ps\n"
        "test \"$2\" = -q\n"
        "printf 'container-a\\ncontainer-b\\n'\n",
        encoding="utf-8",
    )
    trusted.chmod(0o700)
    trusted_sha256 = hashlib.sha256(trusted.read_bytes()).hexdigest()
    ambient_bin = tmp_path / "ambient-bin"
    ambient_bin.mkdir()
    sentinel = tmp_path / "ambient-executed"
    ambient = ambient_bin / "docker"
    ambient.write_text(
        "#!/usr/bin/bash\n"
        f"printf ambient > {sentinel}\n"
        "exit 99\n",
        encoding="utf-8",
    )
    ambient.chmod(0o700)
    monkeypatch.setenv("PATH", str(ambient_bin))

    adapter = LocalEnvironmentAdapter(
        docker_bin=trusted,
        docker_sha256=trusted_sha256,
    )

    assert adapter.running_containers() == ["container-a", "container-b"]
    assert not sentinel.exists()


@pytest.mark.parametrize("mutation", ["sha", "symlink", "non-executable"])
def test_local_adapter_rejects_unsafe_explicit_docker_identity(
    tmp_path: Path,
    mutation: str,
) -> None:
    trusted = tmp_path / "trusted-docker"
    trusted.write_text("#!/usr/bin/bash\nexit 0\n", encoding="utf-8")
    trusted.chmod(0o700)
    expected_sha256 = hashlib.sha256(trusted.read_bytes()).hexdigest()
    candidate = trusted
    if mutation == "sha":
        expected_sha256 = "0" * 64
    elif mutation == "symlink":
        candidate = tmp_path / "docker-link"
        candidate.symlink_to(trusted)
    else:
        trusted.chmod(0o600)

    with pytest.raises(OracleContractError):
        LocalEnvironmentAdapter(
            docker_bin=candidate,
            docker_sha256=expected_sha256,
        )


def test_local_logical_cpu_count_is_stable_under_inherited_affinity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(os, "cpu_count", lambda: 8)
    monkeypatch.setattr(os, "sched_getaffinity", lambda _pid: {0})

    assert LocalEnvironmentAdapter().logical_cpu_count() == 8


def test_proc_stat_parser_handles_spaces_inside_command() -> None:
    fields = ["S", "1", "0", "0", "0", "0", "0", "0", "0", "0", "0", "7", "8"]
    fields.extend(["0"] * 6)
    fields.append("1234")
    raw = f"42 (worker with spaces) {' '.join(fields)}"

    sample = _parse_proc_stat(raw)

    assert sample.pid == 42
    assert sample.ppid == 1
    assert sample.cpu_ticks == 15
    assert sample.start_time_ticks == 1234
