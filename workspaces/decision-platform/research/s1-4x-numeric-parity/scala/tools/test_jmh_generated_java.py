#!/usr/bin/env python3
"""Scala CLI JMH generated-Java 사전 컴파일 폐쇄성을 검증한다."""

from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path


TOOLS_ROOT = Path(__file__).resolve().parent


def load_helper():
    helper_path = TOOLS_ROOT / "precompile_jmh_generated_java.py"
    specification = importlib.util.spec_from_file_location(
        "precompile_jmh_generated_java",
        helper_path,
    )
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


def expect_error(helper, expected: str, action) -> None:
    try:
        action()
    except helper.PrecompileError as error:
        assert str(error) == expected, error
    else:
        raise AssertionError(f"expected PrecompileError: {expected}")


def main() -> int:
    helper = load_helper()
    expected = helper.expected_generated_source_paths()
    assert len(expected) == 30
    assert expected == tuple(sorted(expected, key=lambda value: value.encode()))
    assert len(set(expected)) == len(expected)
    assert all("/jmh_generated/" in item for item in expected)

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        workspace = root / "workspace"
        coursier = root / "coursier"
        generated_root = (
            workspace
            / ".scala-build/project_jmh/sources"
        )
        class_output = (
            workspace
            / ".scala-build/project_jmh_deadbeef00/classes/main"
        )
        benchmark_list = class_output / "META-INF/BenchmarkList"
        generated_resources = (
            workspace
            / ".scala-build/project_jmh/resources"
        )
        resource_benchmark_list = (
            generated_resources / "META-INF/BenchmarkList"
        )
        generated_classes = root / "evidence/generated-java-classes"
        dependency = coursier / "https/repo.example/jmh-core.jar"
        benchmark_list.parent.mkdir(parents=True)
        benchmark_list.write_bytes(b"benchmark-list\n")
        resource_benchmark_list.parent.mkdir(parents=True)
        resource_benchmark_list.write_bytes(b"benchmark-list\n")
        (resource_benchmark_list.parent / "CompilerHints").write_bytes(
            b"compiler-hints\n"
        )
        generated_classes.mkdir(parents=True)
        dependency.parent.mkdir(parents=True)
        dependency.write_bytes(b"jar-bytes\n")
        for index, relative in enumerate(expected, start=1):
            source = generated_root / relative
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_text(
                f"// generated {index}\n",
                encoding="utf-8",
                newline="\n",
            )

        sources = helper.generated_source_closure(workspace)
        assert sources.root == generated_root
        assert tuple(item.relative_path for item in sources.files) == expected
        assert len({item.sha256 for item in sources.files}) == len(expected)

        generator_input = (
            workspace
            / ".scala-build/project/classes/main"
        )
        generator_input.mkdir(parents=True)
        (generator_input / "BenchmarkInvocation.class").write_bytes(
            b"scala-class\n"
        )
        generator_output = (
            workspace
            / ".scala-build/project_jmh"
        )
        generator_stdout = (
            f'Processing 147 classes from {generator_input} '
            'with "reflection" generator\n'
            f"Writing out Java source to {generator_output / 'sources'} "
            f"and resources to {generator_output / 'resources'}\n"
            f"{class_output}:{generated_resources}:"
            f"{generated_classes}:{dependency}\n"
        )
        classpath = helper.classpath_closure(
            generator_stdout,
            workspace=workspace,
            coursier_cache=coursier,
            evidence_dir=generated_classes.parent,
        )
        assert classpath.class_output == class_output
        assert classpath.processed_class_count == 147
        assert classpath.generator_class_input == generator_input
        assert classpath.generated_source_root == generated_root
        assert classpath.generated_resource_root == generator_output / "resources"
        assert classpath.generator_class_input_sha256
        assert classpath.generated_resource_root_sha256
        assert [item.path_id for item in classpath.entries] == [
            (
                "SCALA_WORKSPACE/"
                ".scala-build/project_jmh_deadbeef00/classes/main"
            ),
            "SCALA_WORKSPACE/.scala-build/project_jmh/resources",
            "EVIDENCE_ROOT/generated-java-classes",
            "COURSIER_CACHE/https/repo.example/jmh-core.jar",
        ]
        entry_values = [
            {
                "pathId": item.path_id,
                "kind": item.kind,
                "sha256": item.sha256,
            }
            for item in classpath.entries
        ]
        helper.verify_classpath_entries(
            entry_values,
            workspace=workspace,
            coursier_cache=coursier,
            evidence_dir=generated_classes.parent,
        )
        post_run_stdout = (
            f'Processing 147 classes from {generator_input} '
            'with "reflection" generator\n'
            f"Writing out Java source to {generator_output / 'sources'} "
            f"and resources to {generator_output / 'resources'}"
            + "\n# JMH version: 1.37\n"
        )
        post_run = helper.generator_output_closure(
            post_run_stdout,
            workspace=workspace,
        )
        assert (
            post_run.generator_class_input_sha256
            == classpath.generator_class_input_sha256
        )
        assert (
            post_run.generated_resource_root_sha256
            == classpath.generated_resource_root_sha256
        )
        dependency.write_bytes(b"tampered\n")
        expect_error(
            helper,
            "CLASSPATH_POST_RUN_DRIFT",
            lambda: helper.verify_classpath_entries(
                entry_values,
                workspace=workspace,
                coursier_cache=coursier,
                evidence_dir=generated_classes.parent,
            ),
        )
        dependency.write_bytes(b"jar-bytes\n")

        missing = generated_root / expected[0]
        missing.unlink()
        expect_error(
            helper,
            "GENERATED_SOURCE_CLOSURE_MISMATCH",
            lambda: helper.generated_source_closure(workspace),
        )
        missing.write_text("// restored\n", encoding="utf-8", newline="\n")
        extra = (
            generated_root
            / "s1_4x/benchmarks/path_transform/jmh_generated/Extra.java"
        )
        extra.write_text("// extra\n", encoding="utf-8", newline="\n")
        expect_error(
            helper,
            "GENERATED_SOURCE_CLOSURE_MISMATCH",
            lambda: helper.generated_source_closure(workspace),
        )
        extra.unlink()

        second_output = (
            workspace
            / ".scala-build/project_jmh_other/classes/main"
            / "META-INF/BenchmarkList"
        )
        second_output.parent.mkdir(parents=True)
        second_output.write_bytes(b"other\n")
        expect_error(
            helper,
            "JMH_CLASS_OUTPUT_CLOSURE_MISMATCH",
            lambda: helper.classpath_closure(
                generator_stdout.replace(
                    f":{dependency}\n",
                    f":{second_output.parent.parent}:{dependency}\n",
                ),
                workspace=workspace,
                coursier_cache=coursier,
                evidence_dir=generated_classes.parent,
            ),
        )
        expect_error(
            helper,
            "JMH_GENERATOR_STDOUT_INVALID",
            lambda: helper.classpath_closure(
                generator_stdout.split("\n", maxsplit=1)[1],
                workspace=workspace,
                coursier_cache=coursier,
                evidence_dir=generated_classes.parent,
            ),
        )

    print("SCALA_JMH_GENERATED_JAVA_TEST_PASS expectedSources=30")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
