#!/usr/bin/env python3
"""Scala CLI JMH generated-Java 사전 컴파일 폐쇄성을 검증한다."""

from __future__ import annotations

import hashlib
import importlib.util
import os
import shutil
import subprocess
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


def verify_parent_owned_proc_fd(
    *,
    helper_path: Path,
    binary: Path,
    expected_sha256: str,
    fd: int,
) -> None:
    """실제 child에서 parent-owned proc FD가 sealed 실행 경계로 유지되는지 검증한다."""

    script = """
import importlib.util
import os
import pathlib
import sys

helper_path, binary, expected_sha256, owner_pid, fd = sys.argv[1:]
sys.path.insert(0, str(pathlib.Path(helper_path).parent))
specification = importlib.util.spec_from_file_location(
    "precompile_jmh_generated_java_child",
    helper_path,
)
module = importlib.util.module_from_spec(specification)
sys.modules[specification.name] = module
specification.loader.exec_module(module)
verified_path, execution_id, identity = module._verified_executable(
    binary=pathlib.Path(binary),
    execution_path=pathlib.Path(f"/proc/{owner_pid}/fd/{fd}"),
    expected_sha256=expected_sha256,
    label="PARENT_TEST",
)
assert verified_path == pathlib.Path(f"/proc/{owner_pid}/fd/{fd}")
assert execution_id == "PINNED_PARENT_TEST_FD"
assert identity.proc_owner_pid == int(owner_pid)
assert identity.proc_owner_start_time is not None
module._verify_executable_stability(identity, label="PARENT_TEST")
"""
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            str(helper_path),
            str(binary),
            expected_sha256,
            str(os.getpid()),
            str(fd),
        ],
        check=False,
        capture_output=True,
        text=True,
        pass_fds=(fd,),
    )
    assert completed.returncode == 0, (
        completed.stdout,
        completed.stderr,
    )


def main() -> int:
    helper = load_helper()
    expected = helper.expected_generated_source_paths()
    assert len(expected) == 30
    assert expected == tuple(sorted(expected, key=lambda value: value.encode()))
    assert len(set(expected)) == len(expected)
    assert all("/jmh_generated/" in item for item in expected)

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        executable = root / "executable"
        executable.write_bytes(Path("/usr/bin/dash").read_bytes())
        executable.chmod(0o755)
        executable_sha256 = hashlib.sha256(
            executable.read_bytes()
        ).hexdigest()
        executable_fd = os.open(executable, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            regular_path, regular_id, regular_identity = (
                helper._verified_executable(
                    binary=executable,
                    execution_path=executable,
                    expected_sha256=executable_sha256,
                    label="REGULAR_TEST",
                )
            )
            assert regular_path == executable
            assert regular_id == "REGULAR_TEST"
            assert regular_identity.proc_owner_pid is None
            helper._verify_executable_stability(
                regular_identity,
                label="REGULAR_TEST",
            )

            self_path = Path(f"/proc/self/fd/{executable_fd}")
            verified_path, execution_id, identity = (
                helper._verified_executable(
                    binary=executable,
                    execution_path=self_path,
                    expected_sha256=executable_sha256,
                    label="SELF_TEST",
                )
            )
            assert verified_path == Path(
                f"/proc/{os.getpid()}/fd/{executable_fd}"
            )
            assert execution_id == "PINNED_SELF_TEST_FD"
            assert identity.proc_owner_pid == os.getpid()
            assert identity.proc_owner_start_time is not None
            helper._verify_executable_stability(
                identity,
                label="SELF_TEST",
            )
            verify_parent_owned_proc_fd(
                helper_path=TOOLS_ROOT
                / "precompile_jmh_generated_java.py",
                binary=executable,
                expected_sha256=executable_sha256,
                fd=executable_fd,
            )

            executable_link = root / "executable-link"
            executable_link.symlink_to(executable)
            expect_error(
                helper,
                "LINK_TEST_EXECUTION_IDENTITY_MISMATCH",
                lambda: helper._verified_executable(
                    binary=executable,
                    execution_path=executable_link,
                    expected_sha256=executable_sha256,
                    label="LINK_TEST",
                ),
            )
        finally:
            os.close(executable_fd)
        expect_error(
            helper,
            "SELF_TEST_EXECUTION_POST_EXEC_IDENTITY_MISMATCH",
            lambda: helper._verify_executable_stability(
                identity,
                label="SELF_TEST",
            ),
        )

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
            package_name = ".".join(Path(relative).parent.parts)
            class_name = Path(relative).stem
            if class_name.endswith("_benchmark_jmhTest"):
                declaration = (
                    "import org.openjdk.jmh.runner.InfraControl;\n"
                    f"public final class {class_name} {{}}\n"
                )
            elif class_name.endswith("_jmhType"):
                declaration = (
                    f"public class {class_name} extends "
                    f"{class_name}_B3 {{}}\n"
                )
            elif class_name.endswith("_jmhType_B1"):
                benchmark_name = class_name.removesuffix("_jmhType_B1")
                declaration = (
                    f"public class {class_name} extends "
                    f"s1_4x.benchmarks.{Path(relative).parts[2]}."
                    f"{benchmark_name} {{}}\n"
                )
            elif class_name.endswith("_jmhType_B2"):
                declaration = (
                    f"public class {class_name} extends "
                    f"{class_name.removesuffix('_B2')}_B1 {{}}\n"
                )
            else:
                declaration = (
                    f"public class {class_name} extends "
                    f"{class_name.removesuffix('_B3')}_B2 {{}}\n"
                )
            source.write_text(
                f"package {package_name};\n"
                f"// generated {index}\n"
                f"{declaration}",
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
            generator_stdout
            + "# JMH version: 1.37\n"
        )
        post_run = helper.classpath_closure(
            post_run_stdout,
            workspace=workspace,
            coursier_cache=coursier,
            evidence_dir=generated_classes.parent,
            allow_trailing=True,
        )
        helper.require_matching_classpath(classpath, post_run)
        assert (
            post_run.generator_class_input_sha256
            == classpath.generator_class_input_sha256
        )
        assert (
            post_run.generated_resource_root_sha256
            == classpath.generated_resource_root_sha256
        )
        alternate_dependency = dependency.with_name("jmh-core-copy.jar")
        alternate_dependency.write_bytes(b"other-jar-bytes\n")
        forged_run = helper.classpath_closure(
            post_run_stdout.replace(
                f":{dependency}\n",
                f":{dependency}:{alternate_dependency}\n",
            ),
            workspace=workspace,
            coursier_cache=coursier,
            evidence_dir=generated_classes.parent,
            allow_trailing=True,
        )
        alternate_dependency.unlink()
        expect_error(
            helper,
            "JMH_RUN_CLASSPATH_DRIFT",
            lambda: helper.require_matching_classpath(classpath, forged_run),
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

        dependency_snapshot = helper._snapshot_regular_file(
            dependency,
            label="TEST_DEPENDENCY",
        )
        dependency.write_bytes(b"temporary tamper\n")
        dependency.write_bytes(b"jar-bytes\n")
        expect_error(
            helper,
            "TEST_DEPENDENCY_IDENTITY_DRIFT",
            lambda: helper._verify_regular_file_snapshot(
                dependency_snapshot,
                label="TEST_DEPENDENCY",
            ),
        )
        hardlink = dependency.with_name("jmh-core-hardlink.jar")
        os.link(dependency, hardlink)
        expect_error(
            helper,
            f"UNSAFE_OR_MISSING_FILE:{hardlink}",
            lambda: helper.sha256_file(hardlink),
        )
        hardlink.unlink()

        missing = generated_root / expected[0]
        missing_bytes = missing.read_bytes()
        missing.unlink()
        expect_error(
            helper,
            "GENERATED_SOURCE_CLOSURE_MISMATCH",
            lambda: helper.generated_source_closure(workspace),
        )
        missing.write_bytes(missing_bytes)
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

        original_source = generated_root / expected[1]
        source_copy = root / "source-copy.java"
        shutil.copyfile(original_source, source_copy)
        original_source.unlink()
        os.link(source_copy, original_source)
        expect_error(
            helper,
            f"UNSAFE_OR_MISSING_FILE:{original_source}",
            lambda: helper.generated_source_closure(workspace),
        )
        original_source.unlink()
        shutil.copyfile(source_copy, original_source)
        source_copy.unlink()

        forged_source = generated_root / expected[2]
        original_bytes = forged_source.read_bytes()
        forged_source.write_text(
            "// filenames alone are not generated Java evidence\n",
            encoding="utf-8",
            newline="\n",
        )
        expect_error(
            helper,
            "GENERATED_SOURCE_CONTENT_INVALID",
            lambda: helper.generated_source_closure(workspace),
        )
        forged_source.write_bytes(original_bytes)

        class_destination = root / "class-output"
        for relative in expected:
            class_relative = f"{relative.removesuffix('.java')}.class"
            class_file = class_destination / class_relative
            class_file.parent.mkdir(parents=True, exist_ok=True)
            internal_name = class_relative.removesuffix(".class").encode()
            class_file.write_bytes(
                b"\xca\xfe\xba\xbe\x00\x00\x00\x45" + internal_name
            )
        assert len(helper._generated_class_closure(class_destination)) == 30
        forged_class = class_destination / (
            f"{expected[0].removesuffix('.java')}.class"
        )
        forged_class.write_bytes(b"not-a-real-class\n")
        expect_error(
            helper,
            "GENERATED_CLASS_MAGIC_INVALID",
            lambda: helper._generated_class_closure(class_destination),
        )

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
