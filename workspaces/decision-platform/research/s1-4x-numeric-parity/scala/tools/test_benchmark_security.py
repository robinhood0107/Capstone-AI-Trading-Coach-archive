#!/usr/bin/env python3
"""Scala timed wrapper의 clean subject, tool FD, cache 격리를 검증한다."""

from __future__ import annotations

import hashlib
import importlib.util
import subprocess
import sys
import tempfile
from pathlib import Path


SCALA_ROOT = Path(__file__).resolve().parents[1]
TOOLS_ROOT = SCALA_ROOT / "tools"


def load_helper():
    path = TOOLS_ROOT / "scala_benchmark_block.py"
    specification = importlib.util.spec_from_file_location(
        "scala_benchmark_block",
        path,
    )
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    sys.modules["scala_benchmark_block"] = module
    specification.loader.exec_module(module)
    return module


def expect_error(module, operation, message: str) -> None:
    try:
        operation()
    except module.BlockError:
        pass
    else:
        raise AssertionError(message)


def git(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["/usr/bin/git", "-C", str(root), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def main() -> int:
    module = load_helper()
    helper_source = (TOOLS_ROOT / "scala_benchmark_block.py").read_text(
        encoding="utf-8"
    )
    for marker in (
        "benchmark_python_exec = benchmark_python_pin.proc_path",
        'environment["S1_4X_BENCHMARK_PYTHON_PINNED_FD_PATH"]',
        "python=benchmark_python_exec",
        "str(benchmark_python_exec)",
    ):
        assert marker in helper_source

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory).resolve()
        git(root, "init", "-q")
        git(root, "config", "user.name", "S1.4X Test")
        git(root, "config", "user.email", "s1-4x@example.invalid")
        scala = (
            root
            / "workspaces/decision-platform/research/"
            "s1-4x-numeric-parity/scala"
        )
        scala.mkdir(parents=True)
        (root / ".gitignore").write_text(
            "**/.scala-build/\n**/.bsp/\n",
            encoding="utf-8",
        )
        source = scala / "project.scala"
        source.write_text("// frozen\n", encoding="utf-8")
        git(root, "add", ".")
        git(root, "commit", "-qm", "fixture")
        head = git(root, "rev-parse", "HEAD")

        module._verify_subject_commit(root, head, scala_root=scala)
        source.write_text("// dirty\n", encoding="utf-8")
        expect_error(
            module,
            lambda: module._verify_subject_commit(
                root,
                head,
                scala_root=scala,
            ),
            "dirty tracked subject passed",
        )
        source.write_text("// frozen\n", encoding="utf-8")

        ignored = scala / ".scala-build"
        ignored.mkdir()
        (ignored / "cache.bin").write_bytes(b"ambient")
        expect_error(
            module,
            lambda: module._verify_subject_commit(
                root,
                head,
                scala_root=scala,
            ),
            "ignored repo-local Scala build output passed",
        )

        tool = root / "tool"
        tool.write_bytes(b"#!/bin/sh\nexit 0\n")
        tool.chmod(0o755)
        expected_sha = hashlib.sha256(tool.read_bytes()).hexdigest()
        with module.PinnedExecutable(
            tool,
            expected_sha256=expected_sha,
            label="TEST_TOOL",
        ) as pinned:
            assert pinned.proc_path == Path(f"/proc/self/fd/{pinned.fd}")
            assert pinned.fd in pinned.pass_fds
            pinned.verify_path_identity()
            replacement = root / "replacement"
            replacement.write_bytes(b"#!/bin/sh\nexit 1\n")
            replacement.chmod(0o755)
            tool.unlink()
            replacement.rename(tool)
            pinned_execution = subprocess.run(
                [str(pinned.proc_path)],
                check=False,
                pass_fds=pinned.pass_fds,
            )
            pathname_execution = subprocess.run(
                [str(tool)],
                check=False,
            )
            assert pinned_execution.returncode == 0
            assert pathname_execution.returncode == 1
            expect_error(
                module,
                pinned.verify_path_identity,
                "tool pathname substitution passed",
            )

        cache_root = root / "cache"
        cache_root.mkdir()
        block = root / "block"
        block.mkdir()
        expect_error(
            module,
            lambda: module.deterministic_scala_environment(
                cache_root=cache_root,
                block_directory=block,
                java_home=Path("/opt/jdk"),
                base_environment={
                    "COURSIER_CACHE": "/ambient/cache",
                    "COURSIER_REPOSITORIES": "https://example.invalid",
                    "SCALA_CLI_HOME": "/ambient/scala-cli",
                },
            ),
            "ambient Scala/Coursier configuration passed",
        )
        environment, closure = module.deterministic_scala_environment(
            cache_root=cache_root,
            block_directory=block,
            java_home=Path("/opt/jdk"),
            base_environment={"LANG": "C.UTF-8"},
        )
        assert environment["COURSIER_CACHE"] == str(
            cache_root / "coursier"
        )
        assert environment["SCALA_CLI_HOME"] == str(
            block / "scala-cli-home"
        )
        assert environment["COURSIER_CONFIG_DIR"] == str(
            block / "coursier-config"
        )
        assert environment["SCALA_CLI_CONFIG"] == str(
            block / "scala-cli-home/config.json"
        )
        assert closure["coursierCachePathId"] == "CACHE_ROOT/coursier"
        assert closure["scalaWorkspacePathId"].startswith(
            "CACHE_ROOT/scala-workspaces/"
        )

    print(
        "SCALA_BENCHMARK_SECURITY_TEST_PASS "
        "dirty=REJECT ignoredBuild=REJECT fdPin=PASS abaExecution=PASS "
        "ambient=REJECT cacheIsolation=PASS"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
