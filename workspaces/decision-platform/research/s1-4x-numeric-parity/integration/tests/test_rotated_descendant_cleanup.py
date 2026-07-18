"""회전 benchmark runtime이 process-group을 이탈한 자식도 정리하는지 검증한다."""

from __future__ import annotations

import os
import signal
import sys
import time
from pathlib import Path

import pytest

INTEGRATION = Path(__file__).resolve().parents[1]
BENCHMARKS = INTEGRATION.parent / "benchmarks"
sys.path.insert(0, str(BENCHMARKS))
sys.path.insert(0, str(INTEGRATION))

import rotated_block_runtime as runner  # noqa: E402
from benchmark_contract import ContractError, sha256_file  # noqa: E402


def _proc_start_time(pid: int) -> int | None:
    """PID 재사용을 구분할 수 있도록 Linux proc stat의 starttime을 읽는다."""

    try:
        payload = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
    except FileNotFoundError:
        return None
    closing_parenthesis = payload.rfind(")")
    if closing_parenthesis < 0:
        raise AssertionError(f"invalid /proc stat for pid {pid}")
    fields_after_command = payload[closing_parenthesis + 2 :].split()
    return int(fields_after_command[19])


def _wait_for_identity_exit(pid: int, start_time: int, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _proc_start_time(pid) != start_time:
            return True
        time.sleep(0.02)
    return _proc_start_time(pid) != start_time


def _cleanup_fixture_process(pid: int, start_time: int) -> None:
    """RED 단계 실패에서도 test fixture가 외부에 남지 않게 exact identity만 정리한다."""

    if _proc_start_time(pid) != start_time:
        return
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    _wait_for_identity_exit(pid, start_time)


def _run_detaching_fixture(
    tmp_path: Path,
    *,
    leader_exits: bool,
    timeout_seconds: int,
    immediate_leader_exit: bool = False,
) -> tuple[int, int, ContractError | None]:
    child_pid_path = tmp_path / "detached-child.pid"
    stdout_path = tmp_path / "stdout.log"
    stderr_path = tmp_path / "stderr.log"
    executable_path = Path(sys.executable).resolve(strict=True)
    fixture = """
import os
import pathlib
import signal
import sys
import time

pid_path = pathlib.Path(sys.argv[1])
leader_exits = sys.argv[2] == "yes"
immediate_leader_exit = sys.argv[3] == "yes"
if immediate_leader_exit:
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
child = os.fork()
if child == 0:
    os.setsid()
    stat = pathlib.Path(f"/proc/{os.getpid()}/stat").read_text(encoding="ascii")
    start_time = int(stat[stat.rfind(")") + 2:].split()[19])
    with pid_path.open("x", encoding="ascii") as stream:
        stream.write(f"{os.getpid()} {start_time}")
        stream.flush()
        os.fsync(stream.fileno())
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    while True:
        time.sleep(1)

if immediate_leader_exit:
    os._exit(0)
deadline = time.monotonic() + 2
while not pid_path.exists():
    if time.monotonic() >= deadline:
        os._exit(91)
    time.sleep(0.01)
if leader_exits:
    os._exit(0)
while True:
    time.sleep(1)
"""
    identity = {
        "path": str(executable_path),
        "sha256": sha256_file(executable_path),
    }
    caught: ContractError | None = None
    subreaper_before = runner._child_subreaper_state()
    with runner._pin_executable(identity, role="detachingFixture") as executable:
        try:
            runner._run_process(
                [
                    str(executable_path),
                    "-I",
                    "-c",
                    fixture,
                    str(child_pid_path),
                    "yes" if leader_exits else "no",
                    "yes" if immediate_leader_exit else "no",
                ],
                executable=executable,
                cwd=tmp_path,
                timeout_seconds=timeout_seconds,
                stdout_path=stdout_path,
                stderr_path=stderr_path,
                environment={
                    "HOME": str(tmp_path),
                    "LANG": "C.UTF-8",
                    "LC_ALL": "C.UTF-8",
                    "PATH": "/usr/bin:/bin",
                },
            )
        except ContractError as exc:
            caught = exc
    assert runner._child_subreaper_state() is subreaper_before
    assert child_pid_path.is_file()
    pid_text, start_time_text = child_pid_path.read_text(encoding="ascii").split()
    pid = int(pid_text)
    start_time = int(start_time_text)
    return pid, start_time, caught


def test_pid_reuse_defense_does_not_signal_replacement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = runner.ProcessIdentity(pid=43210, start_time_ticks=100)
    original = runner.ProcessRecord(identity=identity, parent_pid=os.getpid(), state="S")
    replacement = runner.ProcessRecord(
        identity=runner.ProcessIdentity(pid=43210, start_time_ticks=101),
        parent_pid=1,
        state="S",
    )
    records = iter([original, replacement])
    sent: list[tuple[int, int]] = []
    closed: list[int] = []

    monkeypatch.setattr(
        runner,
        "_read_process_record",
        lambda pid: next(records),
    )
    monkeypatch.setattr(
        os,
        "pidfd_open",
        lambda pid, flags: 91,
        raising=False,
    )
    monkeypatch.setattr(
        signal,
        "pidfd_send_signal",
        lambda descriptor, sent_signal, siginfo, flags: sent.append(
            (descriptor, sent_signal)
        ),
        raising=False,
    )
    monkeypatch.setattr(os, "close", lambda descriptor: closed.append(descriptor))

    runner._signal_process_identity(identity, signal.SIGKILL)

    assert sent == []
    assert closed == [91]


def test_baseline_direct_child_is_never_classified_as_descendant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = runner.ProcessIdentity(pid=100, start_time_ticks=10)
    baseline = runner.ProcessIdentity(pid=101, start_time_ticks=11)
    leader = runner.ProcessIdentity(pid=102, start_time_ticks=20)
    escaped = runner.ProcessIdentity(pid=103, start_time_ticks=21)
    snapshot = {
        100: runner.ProcessRecord(identity=root, parent_pid=1, state="S"),
        101: runner.ProcessRecord(identity=baseline, parent_pid=100, state="S"),
        102: runner.ProcessRecord(identity=leader, parent_pid=100, state="S"),
        103: runner.ProcessRecord(identity=escaped, parent_pid=100, state="S"),
    }
    tracker = runner.DescendantTracker(
        root=root,
        leader=leader,
        baseline_direct_children=frozenset({baseline}),
        baseline_processes=frozenset({root, baseline}),
        minimum_start_time_ticks=11,
    )
    signaled: list[runner.ProcessIdentity] = []
    monkeypatch.setattr(runner, "_process_snapshot", lambda: snapshot)
    monkeypatch.setattr(
        runner,
        "_signal_process_identity",
        lambda identity, sent_signal: signaled.append(identity),
    )

    assert runner._signal_descendants(tracker, signal.SIGTERM) == (escaped,)
    assert signaled == [escaped]


@pytest.mark.skipif(sys.platform != "linux", reason="Linux /proc containment contract")
def test_missing_reaped_leader_uses_baseline_delta_to_clean_detached_child(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_read = runner._read_process_record

    def reap_before_returning_identity(pid: int) -> None:
        deadline = time.monotonic() + 2
        while True:
            record = real_read(pid)
            if record is not None and record.state == "Z":
                os.waitpid(pid, 0)
                assert real_read(pid) is None
                return None
            if time.monotonic() >= deadline:
                raise AssertionError("fixture leader did not exit immediately")
            time.sleep(0.01)

    monkeypatch.setattr(
        runner,
        "_read_launch_leader_record",
        reap_before_returning_identity,
    )
    pid = -1
    start_time = -1
    try:
        pid, start_time, caught = _run_detaching_fixture(
            tmp_path,
            leader_exits=True,
            timeout_seconds=5,
            immediate_leader_exit=True,
        )
        assert caught is not None
        assert str(caught) == "NATIVE_PROCESS_LEADER_IDENTITY_INVALID"
        assert _wait_for_identity_exit(pid, start_time)
    finally:
        if pid > 0 and start_time >= 0:
            _cleanup_fixture_process(pid, start_time)


@pytest.mark.skipif(sys.platform != "linux", reason="Linux /proc containment contract")
def test_normal_leader_exit_rejects_and_reaps_setsid_descendant(
    tmp_path: Path,
) -> None:
    pid = -1
    start_time = -1
    try:
        pid, start_time, caught = _run_detaching_fixture(
            tmp_path,
            leader_exits=True,
            timeout_seconds=5,
        )
        assert caught is not None
        assert str(caught) == "NATIVE_DESCENDANTS_SURVIVED_EXIT"
        assert _wait_for_identity_exit(pid, start_time)
    finally:
        if pid > 0 and start_time >= 0:
            _cleanup_fixture_process(pid, start_time)


@pytest.mark.skipif(sys.platform != "linux", reason="Linux /proc containment contract")
def test_timeout_reaps_setsid_descendant_before_reporting_deadline(
    tmp_path: Path,
) -> None:
    pid = -1
    start_time = -1
    try:
        pid, start_time, caught = _run_detaching_fixture(
            tmp_path,
            leader_exits=False,
            timeout_seconds=1,
        )
        assert caught is not None
        assert str(caught) == "PERFORMANCE_DEADLINE_EXCEEDED"
        assert _wait_for_identity_exit(pid, start_time)
    finally:
        if pid > 0 and start_time >= 0:
            _cleanup_fixture_process(pid, start_time)
