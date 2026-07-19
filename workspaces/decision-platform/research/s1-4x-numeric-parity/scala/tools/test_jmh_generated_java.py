#!/usr/bin/env python3
"""Scala CLI JMH generated-Java 사전 컴파일 폐쇄성을 검증한다."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import replace
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


def rotate_directory(path: Path) -> Path:
    """원본 inode를 보존한 채 같은 bytes의 새 directory tree를 materialize한다."""

    original = path.with_name(f"{path.name}-original")
    path.rename(original)
    shutil.copytree(original, path)
    return original


def restore_directory(path: Path, original: Path) -> None:
    shutil.rmtree(path)
    original.rename(path)


def rotate_file(path: Path) -> Path:
    """원본 regular inode를 보존한 채 같은 bytes의 새 file을 materialize한다."""

    original = path.with_name(f"{path.name}-original")
    path.rename(original)
    shutil.copyfile(original, path)
    return original


def restore_file(path: Path, original: Path) -> None:
    path.unlink()
    original.rename(path)


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


def verify_sitecustomize_gate_spoof_rejected(
    *,
    helper_path: Path,
    qualification_script: Path,
    gate_file: Path,
) -> None:
    """신뢰 script 실행 전 sitecustomize가 만든 owner gate를 거부한다."""

    site_root = gate_file.parent / "qualification-gate-site"
    site_root.mkdir()
    sitecustomize = site_root / "sitecustomize.py"
    sitecustomize.write_text(
        r"""
import importlib.util
import json
import os
import pathlib
import subprocess
import sys

helper_path = pathlib.Path(os.environ["S1_4X_GATE_TEST_HELPER"])
gate_file = pathlib.Path(os.environ["S1_4X_GATE_TEST_FILE"])
sys.path.insert(0, str(helper_path.parent))
specification = importlib.util.spec_from_file_location(
    "precompile_jmh_generated_java_gate_parent",
    helper_path,
)
module = importlib.util.module_from_spec(specification)
sys.modules[specification.name] = module
specification.loader.exec_module(module)
snapshot = module._snapshot_regular_file(
    gate_file,
    label="TEST_QUALIFICATION_GATE_PARENT",
    retain_payload=False,
)
gate = module._regular_file_gate_value(snapshot)
environment = os.environ.copy()
environment[module.JDK_MODULES_GATE_SNAPSHOT_VARIABLE] = json.dumps(
    gate,
    allow_nan=False,
    ensure_ascii=False,
    separators=(",", ":"),
    sort_keys=True,
)
environment["S1_4X_BENCHMARK_RUN_MODE"] = "qualification"
environment.pop("PYTHONPATH", None)
environment.pop("S1_4X_GATE_TEST_HELPER", None)
environment.pop("S1_4X_GATE_TEST_FILE", None)
child_script = r'''
import importlib.util
import pathlib
import sys

helper_path, gate_file, expected_sha256 = sys.argv[1:]
sys.path.insert(0, str(pathlib.Path(helper_path).parent))
specification = importlib.util.spec_from_file_location(
    "precompile_jmh_generated_java_gate_child",
    helper_path,
)
module = importlib.util.module_from_spec(specification)
sys.modules[specification.name] = module
specification.loader.exec_module(module)
snapshot = module._jdk_modules_snapshot(
    pathlib.Path(gate_file),
    label="TEST_QUALIFICATION_GATE_CHILD",
)
assert snapshot.sha256 == expected_sha256
module._verify_jdk_modules_snapshot(
    snapshot,
    label="TEST_QUALIFICATION_GATE_CHILD",
)
'''
completed = subprocess.run(
    [
        sys.executable,
        "-c",
        child_script,
        str(helper_path),
        str(gate_file),
        snapshot.sha256,
    ],
    check=False,
    capture_output=True,
    text=True,
    env=environment,
)
if (
    completed.returncode == 0
    or "JDK_MODULES_GATE_OWNER_INVALID" not in completed.stderr
):
    os.write(2, (completed.stdout + completed.stderr).encode("utf-8"))
    os._exit(1)
os._exit(0)
""",
        encoding="utf-8",
        newline="\n",
    )
    environment = os.environ.copy()
    environment.update(
        {
            "PYTHONPATH": str(site_root),
            "S1_4X_GATE_TEST_HELPER": str(helper_path),
            "S1_4X_GATE_TEST_FILE": str(gate_file),
        }
    )
    # 겉보기 script argv와 interpreter identity만 맞춘 pre-main spoof다.
    completed = subprocess.run(
        [
            sys.executable,
            str(qualification_script),
            str(helper_path),
            str(gate_file),
        ],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert completed.returncode == 0, (
        completed.stdout,
        completed.stderr,
    )


def verify_isolated_python_ignores_sitecustomize(
    *,
    qualification_script: Path,
    root: Path,
) -> None:
    """Production의 -E -s -S argv가 ambient Python code injection을 막는다."""

    site_root = root / "python-isolation-site"
    site_root.mkdir()
    marker = root / "sitecustomize-executed"
    (site_root / "sitecustomize.py").write_text(
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('executed')\n",
        encoding="utf-8",
        newline="\n",
    )
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(site_root)
    control = subprocess.run(
        [sys.executable, "-c", "pass"],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert control.returncode == 0, (control.stdout, control.stderr)
    assert marker.is_file(), "control Python did not load sitecustomize"
    marker.unlink()
    completed = subprocess.run(
        [
            sys.executable,
            "-E",
            "-s",
            "-S",
            str(qualification_script),
            "--help",
        ],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert completed.returncode == 0, (
        completed.stdout,
        completed.stderr,
    )
    assert not marker.exists(), "isolated qualification Python loaded sitecustomize"


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
            f'Processing 149 classes from {generator_input} '
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
        assert classpath.processed_class_count == 149
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
                "identitySha256": item.identity_sha256,
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
            "\n".join(generator_stdout.splitlines()[:2])
            + "\n# JMH version: 1.37\n"
        )
        runtime_classpath_sha256 = helper.require_jmh_stdout_binding(
            generator_stdout,
            post_run_stdout,
        )
        assert runtime_classpath_sha256 == classpath.runtime_classpath_sha256
        helper.require_runtime_classpath_evidence(
            runtime_classpath_sha256,
            [{"runtimeClasspathSha256": runtime_classpath_sha256}],
        )
        expect_error(
            helper,
            "JMH_RUN_STDOUT_BINDING_INVALID",
            lambda: helper.require_jmh_stdout_binding(
                generator_stdout,
                post_run_stdout.replace("Processing 149", "Processing 148"),
            ),
        )
        expect_error(
            helper,
            "JMH_GENERATOR_STDOUT_INVALID",
            lambda: helper.classpath_closure(
                generator_stdout.replace(
                    "Processing 149",
                    "Processing 150",
                ),
                workspace=workspace,
                coursier_cache=coursier,
                evidence_dir=generated_classes.parent,
            ),
        )
        expect_error(
            helper,
            "JMH_RUN_CLASSPATH_DRIFT",
            lambda: helper.require_runtime_classpath_evidence(
                runtime_classpath_sha256,
                [{"runtimeClasspathSha256": "0" * 64}],
            ),
        )
        alternate_dependency = dependency.with_name("jmh-core-copy.jar")
        alternate_dependency.write_bytes(b"other-jar-bytes\n")
        forged_run = helper.classpath_closure(
            generator_stdout.replace(
                f":{dependency}\n",
                f":{dependency}:{alternate_dependency}\n",
            ),
            workspace=workspace,
            coursier_cache=coursier,
            evidence_dir=generated_classes.parent,
        )
        alternate_dependency.unlink()
        expect_error(
            helper,
            "JMH_RUN_CLASSPATH_DRIFT",
            lambda: helper.require_matching_classpath(classpath, forged_run),
        )
        forged_directory_identity = replace(
            classpath,
            entries=(
                replace(
                    classpath.entries[0],
                    identity_sha256="0" * 64,
                ),
                *classpath.entries[1:],
            ),
        )
        expect_error(
            helper,
            "JMH_RUN_CLASSPATH_DRIFT",
            lambda: helper.require_matching_classpath(
                classpath,
                forged_directory_identity,
            ),
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

        class_output_id = classpath.entries[0].path_id
        generated_resource_id = classpath.entries[1].path_id
        class_output_original = rotate_directory(class_output)
        resources_original = rotate_directory(generated_resources)
        try:
            post_run = helper.capture_classpath_post_run(
                entry_values,
                scala_class_output_path_id=class_output_id,
                generated_resource_path_id=generated_resource_id,
                workspace=workspace,
                coursier_cache=coursier,
                evidence_dir=generated_classes.parent,
            )
            assert post_run["rotatedPathIds"] == [
                class_output_id,
                generated_resource_id,
            ]
            assert [
                item["identityStatus"] for item in post_run["entries"]
            ] == [
                "ROTATED_SAME_BYTES",
                "ROTATED_SAME_BYTES",
                "STABLE",
                "STABLE",
            ]
            helper.validate_classpath_post_run_evidence(
                post_run,
                classpath_entries=entry_values,
                scala_class_output_path_id=class_output_id,
                generated_resource_path_id=generated_resource_id,
            )
            swapped_roles = json.loads(json.dumps(post_run))
            swapped_roles["allowedIdentityRotations"][0]["pathId"] = (
                generated_resource_id
            )
            swapped_roles["allowedIdentityRotations"][1]["pathId"] = (
                class_output_id
            )
            expect_error(
                helper,
                "CLASSPATH_POST_RUN_EVIDENCE_INVALID",
                lambda: helper.validate_classpath_post_run_evidence(
                    swapped_roles,
                    classpath_entries=entry_values,
                    scala_class_output_path_id=class_output_id,
                    generated_resource_path_id=generated_resource_id,
                ),
            )
            extra_allowed = json.loads(json.dumps(post_run))
            extra_allowed["allowedIdentityRotations"].append(
                {
                    "role": "FORGED_EXTRA_DIRECTORY",
                    "pathId": classpath.entries[2].path_id,
                }
            )
            expect_error(
                helper,
                "CLASSPATH_POST_RUN_EVIDENCE_INVALID",
                lambda: helper.validate_classpath_post_run_evidence(
                    extra_allowed,
                    classpath_entries=entry_values,
                    scala_class_output_path_id=class_output_id,
                    generated_resource_path_id=generated_resource_id,
                ),
            )
        finally:
            restore_directory(class_output, class_output_original)
            restore_directory(generated_resources, resources_original)

        class_output_original = rotate_directory(class_output)
        try:
            benchmark_list.write_bytes(b"changed-class-output\n")
            expect_error(
                helper,
                "CLASSPATH_POST_RUN_DRIFT",
                lambda: helper.capture_classpath_post_run(
                    entry_values,
                    scala_class_output_path_id=class_output_id,
                    generated_resource_path_id=generated_resource_id,
                    workspace=workspace,
                    coursier_cache=coursier,
                    evidence_dir=generated_classes.parent,
                ),
            )
        finally:
            restore_directory(class_output, class_output_original)

        evidence_original = rotate_directory(generated_classes)
        try:
            expect_error(
                helper,
                "CLASSPATH_POST_RUN_IDENTITY_DRIFT",
                lambda: helper.capture_classpath_post_run(
                    entry_values,
                    scala_class_output_path_id=class_output_id,
                    generated_resource_path_id=generated_resource_id,
                    workspace=workspace,
                    coursier_cache=coursier,
                    evidence_dir=generated_classes.parent,
                ),
            )
        finally:
            restore_directory(generated_classes, evidence_original)

        dependency_original = rotate_file(dependency)
        try:
            expect_error(
                helper,
                "CLASSPATH_POST_RUN_IDENTITY_DRIFT",
                lambda: helper.capture_classpath_post_run(
                    entry_values,
                    scala_class_output_path_id=class_output_id,
                    generated_resource_path_id=generated_resource_id,
                    workspace=workspace,
                    coursier_cache=coursier,
                    evidence_dir=generated_classes.parent,
                ),
            )
        finally:
            restore_file(dependency, dependency_original)

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
        gate_file = root / "jdk-modules-gate.bin"
        gate_file.write_bytes(b"parent-gate-bytes\n")
        gate_snapshot = helper._snapshot_regular_file(
            gate_file,
            label="TEST_GATE",
            retain_payload=False,
        )
        gate_variable = helper.JDK_MODULES_GATE_SNAPSHOT_VARIABLE
        previous_gate = os.environ.get(gate_variable)
        previous_run_mode = os.environ.get(
            "S1_4X_BENCHMARK_RUN_MODE"
        )
        parent_gate = helper._regular_file_gate_value(gate_snapshot)
        assert parent_gate["ownerProcess"]["pid"] == os.getpid()
        assert parent_gate["ownerProcess"]["uid"] == os.getuid()
        verify_sitecustomize_gate_spoof_rejected(
            helper_path=TOOLS_ROOT
            / "precompile_jmh_generated_java.py",
            qualification_script=TOOLS_ROOT
            / "run_profile_qualification.py",
            gate_file=gate_file,
        )
        verify_isolated_python_ignores_sitecustomize(
            qualification_script=TOOLS_ROOT
            / "run_profile_qualification.py",
            root=root,
        )
        os.environ[gate_variable] = json.dumps(
            parent_gate,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        standalone_file = root / "standalone-forged-gate.bin"
        standalone_file.write_bytes(b"not-the-pinned-jdk-modules\n")
        standalone_snapshot = helper._snapshot_regular_file(
            standalone_file,
            label="TEST_STANDALONE_GATE",
            retain_payload=False,
        )
        forged_gate = helper._regular_file_gate_value(
            standalone_snapshot
        )
        forged_gate["sha256"] = "0" * 64
        os.environ[gate_variable] = json.dumps(
            forged_gate,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        os.environ["S1_4X_BENCHMARK_RUN_MODE"] = "smoke"
        expect_error(
            helper,
            "JDK_MODULES_GATE_CONTEXT_INVALID",
            lambda: helper._jdk_modules_snapshot(
                standalone_file,
                label="TEST_STANDALONE_GATE",
            ),
        )
        if previous_gate is None:
            os.environ.pop(gate_variable, None)
        else:
            os.environ[gate_variable] = previous_gate
        if previous_run_mode is None:
            os.environ.pop("S1_4X_BENCHMARK_RUN_MODE", None)
        else:
            os.environ[
                "S1_4X_BENCHMARK_RUN_MODE"
            ] = previous_run_mode
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
