#!/usr/bin/env python3
"""Scalafmt runner가 explicit source set과 exact config/version을 쓰는지 검증한다."""

from __future__ import annotations

import importlib.util
import sys
import tempfile
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
    scalafmt_launcher = Path("/cache/scalafmt")
    config = SCALA_ROOT / ".scalafmt.conf"
    sources = [
        SCALA_ROOT / "project.scala",
        SCALA_ROOT / "selected-profile.scala",
    ]
    apply_command = runner.format_command(
        scala_cli=scala_cli,
        scalafmt_launcher=scalafmt_launcher,
        config=config,
        sources=sources,
        check=False,
    )
    check_command = runner.format_command(
        scala_cli=scala_cli,
        scalafmt_launcher=scalafmt_launcher,
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
        "--scalafmt-launcher",
        str(scalafmt_launcher),
        "--offline",
    ]
    assert apply_command == [*prefix, *suffix]
    assert check_command == [*prefix, *suffix, "--check"]
    assert all("src/main/scala" not in item for item in apply_command)
    assert all("src/test/scala" not in item for item in apply_command)
    assert all(item != str(SCALA_ROOT / "benchmarks") for item in apply_command)

    with tempfile.TemporaryDirectory(prefix="s1-4x-scalafmt-patch.") as directory:
        root = Path(directory)
        source_root = root / "source"
        formatted_root = root / "formatted"
        source_one = source_root / "one.scala"
        source_two = source_root / "nested/two.scala"
        formatted_one = formatted_root / "one.scala"
        formatted_two = formatted_root / "nested/two.scala"
        for path in (source_one, source_two, formatted_one, formatted_two):
            path.parent.mkdir(parents=True, exist_ok=True)
        source_one.write_text("object One{def value:Int=1}\n", encoding="utf-8")
        formatted_one.write_text("object One { def value: Int = 1 }\n", encoding="utf-8")
        source_two.write_text("object Two{def value:Int=2}\n", encoding="utf-8")
        formatted_two.write_text("object Two { def value: Int = 2 }\n", encoding="utf-8")
        patch = runner.formatted_source_patch(
            scala_root=source_root,
            sources=[source_one, source_two],
            temporary=formatted_root,
            temporary_sources=[formatted_one, formatted_two],
        )
        repeated = runner.formatted_source_patch(
            scala_root=source_root,
            sources=[source_one, source_two],
            temporary=formatted_root,
            temporary_sources=[formatted_one, formatted_two],
        )
        assert patch == repeated
        assert "--- a/one.scala\n" in patch
        assert "+++ b/one.scala\n" in patch
        assert "--- a/nested/two.scala\n" in patch
        assert "+++ b/nested/two.scala\n" in patch
        assert str(source_root) not in patch
        assert str(formatted_root) not in patch

    with tempfile.TemporaryDirectory(prefix="s1-4x-scalafmt-root.") as directory:
        temporary = runner.create_temporary_directory(directory)
        try:
            assert temporary.parent == Path(directory).resolve()
            assert temporary.name.startswith("s1-4x-scalafmt.")
        finally:
            temporary.rmdir()

    runner_source = (TOOLS_ROOT / "run_scalafmt.py").read_text(encoding="utf-8")
    for required in (
        '"firstApply"',
        '"secondApply"',
        '"nonMutatingCheck"',
        '"misformattedNegative"',
        '"firstPassSourceSha256"',
        '"secondPassSourceSha256"',
        '"sourceInputManifestSha256"',
        '"archivePathId"',
        '"executablePathId"',
        '"resolvedVersionOutput"',
        '"resolutionLogUri"',
        '"networkPolicy"',
        '"formatted-source.patch"',
        '"formattedSourcePatchSha256"',
    ):
        assert required in runner_source
    assert '"OFFLINE_PINNED_LAUNCHER"' in runner_source
    assert "open(\"x\"" in runner_source
    assert runner_source.index('"formatted-source.patch"') < runner_source.index(
        '"real-source-non-mutating-check"'
    )

    print("SCALA_SCALAFMT_RUNNER_CONTRACT_TEST_PASS commands=2")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
