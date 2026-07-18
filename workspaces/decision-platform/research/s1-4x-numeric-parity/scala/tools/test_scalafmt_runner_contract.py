#!/usr/bin/env python3
"""Scalafmt runner가 explicit source set과 exact config/version을 쓰는지 검증한다."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


SCALA_ROOT = Path(__file__).resolve().parents[1]
TOOLS_ROOT = SCALA_ROOT / "tools"


def load_runner():
    path = TOOLS_ROOT / "run_scalafmt.py"
    specification = importlib.util.spec_from_file_location("run_scalafmt", path)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    sys.modules["run_scalafmt"] = module
    specification.loader.exec_module(module)
    return module


def main() -> int:
    runner = load_runner()
    scala_cli = Path("/tool/scala-cli")
    config = SCALA_ROOT / ".scalafmt.conf"
    sources = [
        SCALA_ROOT / "project.scala",
        SCALA_ROOT / "selected-profile.scala",
    ]
    apply_command = runner.format_command(
        scala_cli=scala_cli,
        config=config,
        sources=sources,
        check=False,
    )
    check_command = runner.format_command(
        scala_cli=scala_cli,
        config=config,
        sources=sources,
        check=True,
    )

    prefix = [str(scala_cli), "--power", "format", *map(str, sources)]
    suffix = [
        "--server=false",
        "--scalafmt-version",
        "3.11.4",
        "--scalafmt-conf",
        str(config),
    ]
    assert apply_command == [*prefix, *suffix]
    assert check_command == [*prefix, *suffix, "--check"]
    assert all("src/main/scala" not in item for item in apply_command)
    assert all("src/test/scala" not in item for item in apply_command)
    assert all(item != str(SCALA_ROOT / "benchmarks") for item in apply_command)

    runner_source = (TOOLS_ROOT / "run_scalafmt.py").read_text(encoding="utf-8")
    for required in (
        '"firstApply"',
        '"secondApply"',
        '"nonMutatingCheck"',
        '"misformattedNegative"',
        '"firstPassSourceSha256"',
        '"secondPassSourceSha256"',
        '"sourceInputManifestSha256"',
    ):
        assert required in runner_source
    assert "open(\"x\"" in runner_source

    print("SCALA_SCALAFMT_RUNNER_CONTRACT_TEST_PASS commands=2")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
