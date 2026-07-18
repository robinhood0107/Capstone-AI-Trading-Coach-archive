"""Integration runtime이 timeout과 executable 경계를 fail-closed하는지 검증한다."""

from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

INTEGRATION = Path(__file__).resolve().parents[1]
BENCHMARKS = INTEGRATION.parent / "benchmarks"
sys.path.insert(0, str(BENCHMARKS))
sys.path.insert(0, str(INTEGRATION))

import rotated_block_runtime as runner  # noqa: E402
from benchmark_contract import ContractError, sha256_file, strict_json_load  # noqa: E402
from validate_benchmark_report import DEFAULT_PLAN, validate_plan  # noqa: E402

COMMIT = "a" * 40


@pytest.fixture(scope="module")
def plan() -> dict[str, Any]:
    return validate_plan(DEFAULT_PLAN, verify_files=False)


def _host_report(plan: dict[str, Any], *, status: str = "PASS") -> dict[str, Any]:
    failed = status != "PASS"
    checks: list[dict[str, Any]] = [
        {
            "id": check_id,
            "expected": {},
            "actual": {},
            "status": "FAIL" if failed and index == 0 else "PASS",
            "evidence": {},
        }
        for index, check_id in enumerate(sorted(runner.HOST_CHECK_IDS))
    ]
    return {
        "schemaVersion": "s1.4x-host-validity-v1",
        "policy": runner._expected_host_policy(plan, root_pid=os.getpid()),
        "portableHostIdSha256": "b" * 64,
        "metadata": {
            "cpuGovernor": "UNAVAILABLE_WSL",
            "temperature": "UNAVAILABLE_WSL",
        },
        "checks": checks,
        "failureCount": 1 if failed else 0,
        "status": status,
    }


def _command_manifest(runtime_root: Path | None = None) -> dict[str, Any]:
    executable = str(Path(sys.executable).resolve())
    identity = {"path": executable, "sha256": sha256_file(Path(executable))}
    runtime_identities: dict[str, dict[str, str]] = {}
    evidence_identities: dict[str, dict[str, str]] = {}
    if runtime_root is not None:
        payload = Path(executable).read_bytes()
        runtime_roles = {
            role
            for roles in runner.RUNTIME_DEPENDENCY_ROLES_BY_BOUNDARY.values()
            for role in roles
        }
        for role in runtime_roles:
            if role == "java":
                path = runtime_root / "toolchain/jdk/bin/java"
            elif role == "authoritativeGhc":
                path = (
                    runtime_root
                    / "prefix/.ghcup/ghc/9.10.3/bin/ghc"
                )
            else:
                path = runtime_root / "toolchain/bin" / role
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
            path.chmod(0o700)
            runtime_identities[role] = {
                "path": str(path),
                "sha256": sha256_file(path),
            }
        evidence_paths = {
            "scalafmtArchive": runtime_root / "evidence/scalafmt.zip",
            "selectedProfileResult": (
                runtime_root / "evidence/scala/selected.json"
            ),
            "profileQualificationResult": (
                runtime_root / "evidence/scala/qualification.json"
            ),
            "jvmAllowlistResult": (
                runtime_root / "evidence/scala/jvm.json"
            ),
            "correctnessA": (
                runtime_root
                / "evidence/scala/profiles/A/"
                "scala-profile-correctness-result.v1.json"
            ),
            "correctnessB": (
                runtime_root
                / "evidence/scala/profiles/B/"
                "scala-profile-correctness-result.v1.json"
            ),
            "correctnessC": (
                runtime_root
                / "evidence/scala/profiles/C/"
                "scala-profile-correctness-result.v1.json"
            ),
            "baselineCorrectness": (
                runtime_root / "evidence/haskell/baseline.json"
            ),
            "optimizedCorrectness": (
                runtime_root / "evidence/haskell/optimized.json"
            ),
            "profileQualification": (
                runtime_root / "evidence/haskell/qualification.json"
            ),
        }
        for role, path in evidence_paths.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"{}\n")
            evidence_identities[role] = {
                "path": str(path),
                "sha256": sha256_file(path),
            }
    else:
        runtime_identities = {
            role: identity
            for roles in runner.RUNTIME_DEPENDENCY_ROLES_BY_BOUNDARY.values()
            for role in roles
        }
        evidence_identities = {
            role: identity
            for roles in runner.RUNTIME_EVIDENCE_ROLES_BY_BOUNDARY.values()
            for role in roles
        }
    return {
        "schemaVersion": "s1.4x-benchmark-command-manifest-v3",
        "benchmarkSubjectCommit": COMMIT,
        "candidateSourceCommit": COMMIT,
        "hostValidatorCommand": [
            executable,
            "--output",
            "{host_report}",
            "--allowed-process-root-pid",
            "{allowed_process_root_pid}",
        ],
        "boundaryCommands": {
            boundary_id: [
                executable,
                "--plan",
                "{plan}",
                "--block-dir",
                "{block_dir}",
                "--qualification",
                "{qualification}",
                "--boundary",
                boundary_id,
                "--selector",
                "{selector_id}",
                "--family",
                "{family_id}",
                "--rotation",
                "{rotation_id}",
                "--outer-repetition",
                "{outer_repetition}",
                "--run-id",
                "{run_id}",
                "--benchmark-subject-commit",
                "{benchmark_subject_commit}",
            ]
            for boundary_id in runner.BOUNDARY_IDS
        },
        "allowedExecutables": {
            "hostValidator": identity,
            "boundaries": {
                boundary_id: identity for boundary_id in runner.BOUNDARY_IDS
            },
            "runtimeDependenciesByBoundary": {
                boundary_id: {
                    role: runtime_identities[role]
                    for role in roles
                }
                for boundary_id, roles
                in runner.RUNTIME_DEPENDENCY_ROLES_BY_BOUNDARY.items()
            },
        },
        "allowedEvidenceByBoundary": {
            boundary_id: {
                role: evidence_identities[role]
                for role in roles
            }
            for boundary_id, roles
            in runner.RUNTIME_EVIDENCE_ROLES_BY_BOUNDARY.items()
        },
    }


def _write_manifest(path: Path, manifest: dict[str, Any] | None = None) -> str:
    path.write_text(
        json.dumps(
            manifest or _command_manifest(path.parent),
            allow_nan=False,
        ),
        encoding="utf-8",
    )
    return sha256_file(path)


def test_command_manifest_requires_precommitted_digest_and_executable_identity(
    tmp_path: Path,
) -> None:
    path = tmp_path / "commands.json"
    digest = _write_manifest(path)

    manifest = runner._strict_command_manifest(
        path,
        expected_sha256=digest,
        benchmark_subject_commit=COMMIT,
        candidate_source_commit=COMMIT,
    )
    assert manifest["schemaVersion"] == "s1.4x-benchmark-command-manifest-v3"

    with pytest.raises(ContractError, match="COMMAND_MANIFEST_SHA256_MISMATCH"):
        runner._strict_command_manifest(
            path,
            expected_sha256="0" * 64,
            benchmark_subject_commit=COMMIT,
            candidate_source_commit=COMMIT,
        )

    arbitrary = _command_manifest()
    arbitrary["boundaryCommands"]["scala"][0] = "/bin/echo"
    arbitrary_path = tmp_path / "arbitrary.json"
    arbitrary_digest = _write_manifest(arbitrary_path, arbitrary)
    with pytest.raises(ContractError, match="INVALID_ALLOWED_EXECUTABLE_IDENTITY"):
        runner._strict_command_manifest(
            arbitrary_path,
            expected_sha256=arbitrary_digest,
            benchmark_subject_commit=COMMIT,
            candidate_source_commit=COMMIT,
        )


def test_runner_manifest_hash_and_parse_share_one_snapshot(
    tmp_path: Path,
) -> None:
    manifest = _command_manifest()
    path = tmp_path / "commands.json"
    digest = _write_manifest(path, manifest)
    forged = json.loads(json.dumps(manifest))
    forged["boundaryCommands"]["scala"].extend(["--override", "forged"])
    replacement = tmp_path / "replacement.json"
    replacement.write_text(json.dumps(forged), encoding="utf-8")
    real_sha256_file = sha256_file
    swapped = False

    def replace_after_hash(candidate: Path) -> str:
        nonlocal swapped
        actual = real_sha256_file(candidate)
        if candidate == path and not swapped:
            replacement.replace(candidate)
            swapped = True
        return actual

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(runner, "sha256_file", replace_after_hash)
        validated = runner._strict_command_manifest(
            path,
            expected_sha256=digest,
            benchmark_subject_commit=COMMIT,
            candidate_source_commit=COMMIT,
        )

    assert validated == manifest
    assert swapped is False


def test_runner_manifest_rejects_escaped_placeholder_and_extra_argv(
    tmp_path: Path,
) -> None:
    escaped = _command_manifest()
    escaped["boundaryCommands"]["scala"][6] = "{{qualification}}"
    escaped_path = tmp_path / "escaped.json"
    escaped_digest = _write_manifest(escaped_path, escaped)
    with pytest.raises(ContractError, match="BOUNDARY_COMMAND_TEMPLATE_MISMATCH"):
        runner._strict_command_manifest(
            escaped_path,
            expected_sha256=escaped_digest,
            benchmark_subject_commit=COMMIT,
            candidate_source_commit=COMMIT,
        )

    extra = _command_manifest()
    extra["boundaryCommands"]["haskell"].extend(["--override", "forged"])
    extra_path = tmp_path / "extra.json"
    extra_digest = _write_manifest(extra_path, extra)
    with pytest.raises(ContractError, match="BOUNDARY_COMMAND_TEMPLATE_MISMATCH"):
        runner._strict_command_manifest(
            extra_path,
            expected_sha256=extra_digest,
            benchmark_subject_commit=COMMIT,
            candidate_source_commit=COMMIT,
        )


def test_runner_executes_sealed_verified_bytes_after_supplier_path_replacement(
    tmp_path: Path,
) -> None:
    supplied = tmp_path / "wrapper"
    supplied.write_text(
        "#!/usr/bin/bash\nprintf 'A\\n'\n",
        encoding="utf-8",
    )
    supplied.chmod(0o700)
    identity = {
        "path": str(supplied),
        "sha256": sha256_file(supplied),
    }

    with runner._pin_executable(identity, role="test") as pinned:
        replacement = tmp_path / "replacement"
        replacement.write_text(
            "#!/usr/bin/bash\nprintf 'B\\n'\n",
            encoding="utf-8",
        )
        replacement.chmod(0o700)
        replacement.replace(supplied)

        for invocation in range(2):
            stdout = tmp_path / f"stdout-{invocation}"
            stderr = tmp_path / f"stderr-{invocation}"
            runner._run_process(
                [str(supplied)],
                executable=pinned,
                cwd=tmp_path,
                timeout_seconds=5,
                stdout_path=stdout,
                stderr_path=stderr,
                environment=dict(os.environ),
            )
            assert stdout.read_text(encoding="utf-8") == "A\n"
            assert stderr.read_bytes() == b""


def test_runner_executes_fd_bound_uv_dependency_after_path_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    wrapper = tmp_path / "wrapper"
    wrapper.write_text(
        "#!/usr/bin/bash\nexec \"$S1_4X_UV_BIN\"\n",
        encoding="utf-8",
    )
    wrapper.chmod(0o700)
    supplied_uv = tmp_path / "uv"
    supplied_uv.write_text(
        "#!/usr/bin/bash\nprintf 'UV_A\\n'\n",
        encoding="utf-8",
    )
    supplied_uv.chmod(0o700)
    wrapper_identity = {
        "path": str(wrapper),
        "sha256": sha256_file(wrapper),
    }
    uv_identity = {
        "path": str(supplied_uv),
        "sha256": sha256_file(supplied_uv),
    }

    with (
        runner._pin_executable(wrapper_identity, role="wrapper") as pinned_wrapper,
        runner._pin_executable(uv_identity, role="uv") as pinned_uv,
    ):
        replacement = tmp_path / "replacement-uv"
        replacement.write_text(
            "#!/usr/bin/bash\nprintf 'UV_B\\n'\n",
            encoding="utf-8",
        )
        replacement.chmod(0o700)
        replacement.replace(supplied_uv)
        stdout = tmp_path / "uv.stdout"
        stderr = tmp_path / "uv.stderr"
        environment = runner._benchmark_environment(
            {"uv": pinned_uv},
            {},
            boundary_id="hostValidator",
        )

        runner._run_process(
            [str(wrapper)],
            executable=pinned_wrapper,
            inherited_executables=(pinned_uv,),
            cwd=tmp_path,
            timeout_seconds=5,
            stdout_path=stdout,
            stderr_path=stderr,
            environment=environment,
        )

    assert stdout.read_text(encoding="utf-8") == "UV_A\n"
    assert stderr.read_bytes() == b""


def test_runner_rejects_path_resolved_script_interpreter(
    tmp_path: Path,
) -> None:
    supplied = tmp_path / "wrapper"
    supplied.write_text(
        "#!/usr/bin/env bash\nprintf 'untrusted interpreter\\n'\n",
        encoding="utf-8",
    )
    supplied.chmod(0o700)

    with pytest.raises(
        ContractError,
        match="COMMAND_SCRIPT_INTERPRETER_MISMATCH",
    ), runner._pin_executable(
        {
            "path": str(supplied),
            "sha256": sha256_file(supplied),
        },
        role="test",
    ):
        pass


def test_source_commit_binding_rejects_tracked_and_untracked_worktree_drift(
    tmp_path: Path,
) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "config", "user.name", "S1.4X Test"],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "s1.4x-test@example.invalid"],
        cwd=tmp_path,
        check=True,
    )
    tracked = tmp_path / "tracked.txt"
    tracked.write_text("frozen\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "frozen"], cwd=tmp_path, check=True)
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    runner._verify_source_commit_binding(
        tmp_path,
        benchmark_subject_commit=commit,
        candidate_source_commit=commit,
    )
    tracked.write_text("mutated\n", encoding="utf-8")
    with pytest.raises(ContractError, match="CURRENT_SOURCE_WORKTREE_DIRTY"):
        runner._verify_source_commit_binding(
            tmp_path,
            benchmark_subject_commit=commit,
            candidate_source_commit=commit,
        )
    tracked.write_text("frozen\n", encoding="utf-8")
    (tmp_path / "untracked.txt").write_text("untracked\n", encoding="utf-8")
    with pytest.raises(ContractError, match="CURRENT_SOURCE_WORKTREE_DIRTY"):
        runner._verify_source_commit_binding(
            tmp_path,
            benchmark_subject_commit=commit,
            candidate_source_commit=commit,
        )


def test_source_binding_cannot_be_bypassed_by_path_git(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(["/usr/bin/git", "init", "-q"], cwd=repository, check=True)
    subprocess.run(
        ["/usr/bin/git", "config", "user.name", "S1.4X Test"],
        cwd=repository,
        check=True,
    )
    subprocess.run(
        ["/usr/bin/git", "config", "user.email", "s1.4x-test@example.invalid"],
        cwd=repository,
        check=True,
    )
    tracked = repository / "tracked.txt"
    tracked.write_text("frozen\n", encoding="utf-8")
    subprocess.run(
        ["/usr/bin/git", "add", "tracked.txt"],
        cwd=repository,
        check=True,
    )
    subprocess.run(
        ["/usr/bin/git", "commit", "-qm", "frozen"],
        cwd=repository,
        check=True,
    )
    commit = subprocess.run(
        ["/usr/bin/git", "rev-parse", "HEAD"],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    tracked.write_text("dirty\n", encoding="utf-8")

    malicious = tmp_path / "malicious"
    malicious.mkdir()
    fake_git = malicious / "git"
    fake_git.write_text(
        "#!/bin/sh\n"
        "case \"$*\" in\n"
        "  *--show-toplevel*) printf '%s\\n%s\\n' \"$FAKE_ROOT\" \"$FAKE_HEAD\" ;;\n"
        "  *status*) : ;;\n"
        "  *) exit 91 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    fake_git.chmod(0o700)
    monkeypatch.setenv("PATH", str(malicious))
    monkeypatch.setenv("FAKE_ROOT", str(repository))
    monkeypatch.setenv("FAKE_HEAD", commit)

    with pytest.raises(ContractError, match="CURRENT_SOURCE_WORKTREE_DIRTY"):
        runner._verify_source_commit_binding(
            repository,
            benchmark_subject_commit=commit,
            candidate_source_commit=commit,
        )


def test_benchmark_environment_drops_ambient_code_and_tool_overrides(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    cache_root = tmp_path / ".cache/s1-4x"
    for directory in ("tmp", "uv", "coursier", "stack-root"):
        (cache_root / directory).mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("BASH_ENV", str(tmp_path / "inject.sh"))
    monkeypatch.setenv("ENV", str(tmp_path / "inject-posix.sh"))
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "python"))
    monkeypatch.setenv("JAVA_TOOL_OPTIONS", "-javaagent:/tmp/inject.jar")
    monkeypatch.setenv("S1_4X_UV_BIN", str(tmp_path / "uv"))
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.fsmonitor")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", str(tmp_path / "hook"))

    runtime = runner.PinnedExecutable(
        binding={"path": "/opt/s1-4x/uv", "sha256": "a" * 64},
        descriptor=42,
        required_seals=runner.F_SEAL_SEAL,
    )
    environment = runner._benchmark_environment(
        {"uv": runtime},
        {},
        boundary_id="hostValidator",
    )

    assert environment == {
        "HOME": str(tmp_path),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": "/usr/bin:/bin",
        "TMP": str(cache_root / "tmp"),
        "TMPDIR": str(cache_root / "tmp"),
        "TEMP": str(cache_root / "tmp"),
        "UV_CACHE_DIR": str(cache_root / "uv"),
        "COURSIER_CACHE": str(cache_root / "coursier"),
        **runner.THREAD_ENVIRONMENT,
        "S1_4X_THREAD_COUNT": "1",
        "S1_4X_UV_BIN": "/proc/self/fd/42",
        "S1_4X_UV_SHA256": "a" * 64,
    }


def test_benchmark_environment_is_boundary_least_privilege_and_haskell_clean(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))

    def pinned(role: str, descriptor: int) -> runner.PinnedExecutable:
        path = (
            tmp_path / ".ghcup/ghc/9.10.3/bin/ghc"
            if role == "authoritativeGhc"
            else Path(f"/opt/s1-4x/{role}")
        )
        return runner.PinnedExecutable(
            binding={
                "path": str(path),
                "resolvedPath": str(path),
                "sha256": f"{descriptor:064x}",
            },
            descriptor=descriptor,
            required_seals=runner.F_SEAL_SEAL,
        )

    haskell_dependencies = {
        role: pinned(role, index)
        for index, role in enumerate(
            runner.RUNTIME_DEPENDENCY_ROLES_BY_BOUNDARY["haskell"],
            start=100,
        )
    }
    haskell_evidence = {
        role: pinned(role, index)
        for index, role in enumerate(
            runner.RUNTIME_EVIDENCE_ROLES_BY_BOUNDARY["haskell"],
            start=200,
        )
    }

    environment = runner._benchmark_environment(
        haskell_dependencies,
        haskell_evidence,
        boundary_id="haskell",
    )

    self_forbidden = {
        "S1_4X_UV_BIN",
        "S1_4X_SCALA_CLI_BIN",
        "S1_4X_SCALAFIX_BIN",
        "S1_4X_SCALAFMT_BIN",
        "S1_4X_SCALA_JAVA_BIN",
        "STACK_ROOT",
    }
    assert self_forbidden.isdisjoint(environment)
    assert environment["S1_4X_GHCUP_PINNED_FD_PATH"].startswith(
        "/proc/self/fd/"
    )
    assert environment["S1_4X_STACK_PINNED_FD_PATH"].startswith(
        "/proc/self/fd/"
    )
    assert environment["S1_4X_HASKELL_BASELINE_CORRECTNESS"].startswith(
        "/proc/self/fd/"
    )


def test_timeout_termination_reaps_leader_and_remaining_process_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signals: list[int] = []
    process_group_checks = iter([False, True])

    class FinishedLeader:
        pid = 12345

        def wait(self, timeout: float | None = None) -> int:
            assert timeout is not None
            assert 0.0 < timeout <= 5.0
            return -signal.SIGTERM

    monkeypatch.setattr(
        runner,
        "_signal_process_group",
        lambda process_group_id, sent_signal: signals.append(sent_signal),
    )
    monkeypatch.setattr(
        runner,
        "_wait_for_process_group_exit",
        lambda process_group_id, timeout_seconds: next(process_group_checks),
    )

    runner._terminate_process_group(FinishedLeader())

    assert signals == [signal.SIGTERM, signal.SIGKILL]


def test_timeout_termination_has_stable_leaf_when_group_survives_sigkill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FinishedLeader:
        pid = 12345

        def wait(self, timeout: float | None = None) -> int:
            return -signal.SIGKILL

    monkeypatch.setattr(runner, "_signal_process_group", lambda *args: None)
    monkeypatch.setattr(
        runner,
        "_wait_for_process_group_exit",
        lambda process_group_id, timeout_seconds: False,
    )

    with pytest.raises(
        ContractError,
        match="TIMEOUT_PROCESS_GROUP_SURVIVED_SIGKILL",
    ):
        runner._terminate_process_group(FinishedLeader())


def test_sigkill_is_sent_within_one_shared_five_second_term_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [100.0]
    signals: list[tuple[int, float]] = []

    class StuckLeader:
        pid = 12345

        def wait(self, timeout: float | None = None) -> int:
            raise AssertionError("patched wait helper owns the simulated clock")

    def wait_for_leader(
        process: runner.WaitableProcess,
        *,
        timeout_seconds: float,
    ) -> bool:
        clock[0] += timeout_seconds
        return False

    def wait_for_group(process_group_id: int, timeout_seconds: float) -> bool:
        clock[0] += timeout_seconds
        return False

    monkeypatch.setattr(runner.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(runner, "_wait_for_leader", wait_for_leader)
    monkeypatch.setattr(runner, "_wait_for_process_group_exit", wait_for_group)
    monkeypatch.setattr(
        runner,
        "_signal_process_group",
        lambda process_group_id, sent_signal: signals.append(
            (sent_signal, clock[0])
        ),
    )

    with pytest.raises(
        ContractError,
        match="TIMEOUT_PROCESS_GROUP_SURVIVED_SIGKILL",
    ):
        runner._terminate_process_group(StuckLeader())

    assert signals[0] == (signal.SIGTERM, 100.0)
    assert signals[1][0] == signal.SIGKILL
    assert signals[1][1] <= 105.0


def test_process_group_probe_treats_esrch_as_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing(process_group_id: int, sent_signal: int) -> None:
        raise ProcessLookupError

    monkeypatch.setattr(os, "killpg", missing)

    assert runner._process_group_exists(12345) is False


def test_completed_native_process_rejects_and_cleans_surviving_descendants(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    terminated: list[int] = []

    class FinishedLeader:
        pid = 12345

        def wait(self, timeout: float | None = None) -> int:
            return 0

    monkeypatch.setattr(runner, "_process_group_exists", lambda process_group_id: True)
    monkeypatch.setattr(
        runner,
        "_terminate_process_group",
        lambda process: terminated.append(process.pid),
    )

    with pytest.raises(
        ContractError,
        match="NATIVE_PROCESS_GROUP_SURVIVED_EXIT",
    ):
        runner._reject_surviving_process_group(FinishedLeader())

    assert terminated == [12345]


def test_command_renderer_rejects_unbound_or_formatted_placeholders() -> None:
    with pytest.raises(ContractError, match="UNKNOWN_COMMAND_PLACEHOLDER"):
        runner._render_command(["tool", "{unbound}"], {"bound": "value"})
    with pytest.raises(ContractError, match="UNKNOWN_COMMAND_PLACEHOLDER"):
        runner._render_command(["tool", "{bound!r}"], {"bound": "value"})


def test_host_validity_verifies_bytes_pass_state_and_frozen_policy(
    tmp_path: Path,
    plan: dict[str, Any],
) -> None:
    report_path = tmp_path / "host-validity.json"
    report_path.write_text(
        json.dumps(_host_report(plan), allow_nan=False),
        encoding="utf-8",
    )

    binding = runner._verify_host_validity_report(
        report_path,
        plan=plan,
        root_pid=os.getpid(),
    )
    assert binding["status"] == "PASS"
    assert binding["sha256"] == sha256_file(report_path)

    failed = _host_report(plan, status="FAIL")
    report_path.write_text(json.dumps(failed, allow_nan=False), encoding="utf-8")
    with pytest.raises(ContractError, match="INVALID_HOST_VALIDITY_ARTIFACT"):
        runner._verify_host_validity_report(
            report_path,
            plan=plan,
            root_pid=os.getpid(),
        )

    wrong_policy = _host_report(plan)
    wrong_policy["policy"]["max_normalized_load1"] = 0.2
    report_path.write_text(
        json.dumps(wrong_policy, allow_nan=False),
        encoding="utf-8",
    )
    with pytest.raises(ContractError, match="INVALID_HOST_VALIDITY_ARTIFACT"):
        runner._verify_host_validity_report(
            report_path,
            plan=plan,
            root_pid=os.getpid(),
        )


def test_host_validity_hash_and_parse_share_one_snapshot(
    tmp_path: Path,
    plan: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report_path = tmp_path / "host-validity.json"
    report_a = _host_report(plan)
    report_b = _host_report(plan)
    report_a["portableHostIdSha256"] = "a" * 64
    report_b["portableHostIdSha256"] = "b" * 64
    report_a_bytes = json.dumps(report_a, allow_nan=False).encode("utf-8")
    report_b_bytes = json.dumps(report_b, allow_nan=False).encode("utf-8")
    report_path.write_bytes(report_a_bytes)
    original_strict_json_load = runner.strict_json_load
    swap_attempted = False

    def aba_parse(candidate: Path) -> Any:
        nonlocal swap_attempted
        if candidate == report_path:
            swap_attempted = True
            report_path.write_bytes(report_b_bytes)
            parsed = original_strict_json_load(candidate)
            report_path.write_bytes(report_a_bytes)
            return parsed
        return original_strict_json_load(candidate)

    monkeypatch.setattr(runner, "strict_json_load", aba_parse)
    binding = runner._verify_host_validity_report(
        report_path,
        plan=plan,
        root_pid=os.getpid(),
    )

    assert swap_attempted is False
    assert binding["sha256"] == hashlib.sha256(report_a_bytes).hexdigest()
    assert binding["portableHostIdSha256"] == "a" * 64


def test_measurement_transition_is_exact_and_tamper_evident(
    tmp_path: Path,
    plan: dict[str, Any],
) -> None:
    block = runner.build_schedule(plan)[0]
    executable_path = Path(sys.executable).absolute()
    expected = runner._qualification_document(
        plan=plan,
        plan_sha256="1" * 64,
        command_manifest_sha256="2" * 64,
        benchmark_subject_commit=COMMIT,
        candidate_source_commit=COMMIT,
        run_id="run-001",
        block=block,
        host_validity={
            "artifactPath": "host-validity.json",
            "sha256": "3" * 64,
            "status": "PASS",
            "policySha256": "4" * 64,
            "portableHostIdSha256": "5" * 64,
        },
        executable={
            "path": str(executable_path),
            "resolvedPath": str(executable_path.resolve()),
            "sha256": sha256_file(executable_path),
        },
        command=[str(executable_path), "--selector", block.selector_id],
        measurement_entered=False,
    )
    path = tmp_path / "timeout-qualification.json"
    runner._write_json_exclusive(path, expected)

    with pytest.raises(ContractError, match="INVALID_MEASUREMENT_QUALIFICATION"):
        runner._verify_measurement_qualification(path, expected=expected)

    runner.mark_measurement_entered(path)
    assert runner._verify_measurement_qualification(path, expected=expected) == (
        sha256_file(path)
    )

    tampered = strict_json_load(path)
    tampered["selectorInputClosure"]["expectedCaseIds"] = []
    path.write_text(json.dumps(tampered, allow_nan=False), encoding="utf-8")
    with pytest.raises(ContractError, match="INVALID_MEASUREMENT_QUALIFICATION"):
        runner._verify_measurement_qualification(path, expected=expected)


def _install_execute_fakes(
    monkeypatch: pytest.MonkeyPatch,
    plan: dict[str, Any],
    *,
    native_marks_measurement: bool,
    host_status: str = "PASS",
    host_times_out: bool = False,
) -> list[runner.ScheduledBlock]:
    schedule = runner.build_schedule(plan)[:2]

    monkeypatch.setattr(runner, "validate_plan", lambda _path: plan)
    monkeypatch.setattr(runner, "build_schedule", lambda _plan: schedule)
    monkeypatch.setattr(runner, "_pin_current_process", lambda _cpu_set: None)
    monkeypatch.setattr(
        runner,
        "_verify_source_commit_binding",
        lambda *_args, **_kwargs: None,
    )

    def fake_process(
        command: list[str],
        *,
        executable: runner.PinnedExecutable,
        inherited_executables: tuple[runner.PinnedExecutable, ...] = (),
        cwd: Path,
        timeout_seconds: int,
        stdout_path: Path,
        stderr_path: Path,
        environment: dict[str, str],
    ) -> None:
        del (
            executable,
            inherited_executables,
            cwd,
            timeout_seconds,
            stdout_path,
            stderr_path,
            environment,
        )
        if "--allowed-process-root-pid" in command:
            if host_times_out:
                raise ContractError("PERFORMANCE_DEADLINE_EXCEEDED")
            output_index = command.index("--output") + 1
            Path(command[output_index]).write_text(
                json.dumps(_host_report(plan, status=host_status), allow_nan=False),
                encoding="utf-8",
            )
            return
        if native_marks_measurement:
            qualification_index = command.index("--qualification") + 1
            runner.mark_measurement_entered(Path(command[qualification_index]))
        raise ContractError("PERFORMANCE_DEADLINE_EXCEEDED")

    monkeypatch.setattr(runner, "_run_process", fake_process)
    return schedule


def _execute(
    tmp_path: Path,
    manifest_path: Path,
    manifest_sha256: str,
) -> dict[str, Any]:
    return runner.execute_schedule(
        plan_path=DEFAULT_PLAN,
        command_manifest_path=manifest_path,
        expected_command_manifest_sha256=manifest_sha256,
        benchmark_subject_commit=COMMIT,
        candidate_source_commit=COMMIT,
        output_root=tmp_path / "outputs",
        run_id="run-timeout",
        repo_root=tmp_path,
    )


def test_setup_timeout_cannot_be_recorded_as_performance_zero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    plan: dict[str, Any],
) -> None:
    schedule = _install_execute_fakes(
        monkeypatch,
        plan,
        native_marks_measurement=False,
    )
    manifest_path = tmp_path / "commands.json"
    manifest_sha256 = _write_manifest(manifest_path)

    with pytest.raises(ContractError, match="INVALID_MEASUREMENT_QUALIFICATION"):
        _execute(tmp_path, manifest_path, manifest_sha256)

    first_block = runner.block_directory(
        tmp_path / "outputs" / "run-timeout",
        schedule[0],
    )
    assert not (first_block / "valid-performance-timeout.json").exists()


def test_valid_measurement_timeout_binds_qualification_and_continues(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    plan: dict[str, Any],
) -> None:
    schedule = _install_execute_fakes(
        monkeypatch,
        plan,
        native_marks_measurement=True,
    )
    manifest_path = tmp_path / "commands.json"
    manifest_sha256 = _write_manifest(manifest_path)

    summary = _execute(tmp_path, manifest_path, manifest_sha256)

    assert summary["status"] == "PASS_WITH_VALID_PERFORMANCE_TIMEOUTS"
    assert summary["validPerformanceTimeoutCount"] == 2
    for block in schedule:
        block_path = runner.block_directory(
            tmp_path / "outputs" / "run-timeout",
            block,
        )
        evidence = strict_json_load(
            block_path / "valid-performance-timeout.json"
        )
        qualification = block_path / "timeout-qualification.json"
        assert evidence["measurementEntered"] is True
        assert evidence["timeoutQualificationSha256"] == sha256_file(qualification)
        assert strict_json_load(qualification)["measurementEntered"] is True


def test_execute_rechecks_source_binding_around_every_native_block(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    plan: dict[str, Any],
) -> None:
    schedule = _install_execute_fakes(
        monkeypatch,
        plan,
        native_marks_measurement=True,
    )
    checks: list[tuple[str, str]] = []

    def record_source_check(
        _repo_root: Path,
        *,
        benchmark_subject_commit: str,
        candidate_source_commit: str,
    ) -> None:
        checks.append((benchmark_subject_commit, candidate_source_commit))

    monkeypatch.setattr(
        runner,
        "_verify_source_commit_binding",
        record_source_check,
    )
    manifest_path = tmp_path / "commands.json"
    manifest_sha256 = _write_manifest(manifest_path)

    summary = _execute(tmp_path, manifest_path, manifest_sha256)

    assert summary["validPerformanceTimeoutCount"] == len(schedule)
    assert checks == [(COMMIT, COMMIT)] * (1 + 2 * len(schedule))


@pytest.mark.parametrize(
    ("host_status", "host_times_out", "error"),
    [
        ("FAIL", False, "INVALID_HOST_VALIDITY_ARTIFACT"),
        ("PASS", True, "HOST_PREFLIGHT_DEADLINE_EXCEEDED"),
    ],
)
def test_failed_or_hung_host_preflight_is_a_hard_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    plan: dict[str, Any],
    host_status: str,
    host_times_out: bool,
    error: str,
) -> None:
    _install_execute_fakes(
        monkeypatch,
        plan,
        native_marks_measurement=True,
        host_status=host_status,
        host_times_out=host_times_out,
    )
    manifest_path = tmp_path / "commands.json"
    manifest_sha256 = _write_manifest(manifest_path)

    with pytest.raises(ContractError, match=error):
        _execute(tmp_path, manifest_path, manifest_sha256)
