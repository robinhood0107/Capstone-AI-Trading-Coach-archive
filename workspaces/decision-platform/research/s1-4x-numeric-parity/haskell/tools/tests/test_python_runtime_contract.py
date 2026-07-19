"""Haskell orchestration의 exact CPython 3.12.13 pinned-FD 계약 테스트."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import venv
from pathlib import Path


TOOLS_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_HELPER = TOOLS_ROOT / "python-runtime.sh"
PROFILE_WORKFLOW = TOOLS_ROOT / "profile_workflow.py"
PYTHON_ORCHESTRATION_WRAPPERS = (
    "assert-toolchain.sh",
    "check-format.sh",
    "check-hlint.sh",
    "run-benchmark-block.sh",
    "run-candidate.sh",
    "run-correctness-profile.sh",
    "run-ghc-9.14.1-compatibility.sh",
    "run-oci-correctness.sh",
    "run-profile-qualification.sh",
    "run-property-evidence.sh",
    "select-proven-profile.sh",
    "validate-ghc-9.14.1-compatibility.sh",
)


def sha256_file(path: Path) -> str:
    """Runtime test가 전달할 exact executable bytes SHA-256을 계산한다."""

    return hashlib.sha256(path.read_bytes()).hexdigest()


def runtime_environment(python: Path) -> dict[str, str]:
    """Ambient Python 설정 없이 exact source path와 bytes만 전달한다."""

    environment = dict(os.environ)
    for name in tuple(environment):
        if name.startswith("PYTHON") or name in {"VIRTUAL_ENV", "BASH_ENV", "ENV"}:
            environment.pop(name, None)
    environment.update(
        {
            "LC_ALL": "C",
            "PATH": "/usr/bin:/bin",
            "S1_4X_BENCHMARK_PYTHON_BIN": str(python),
            "S1_4X_BENCHMARK_PYTHON_SHA256": sha256_file(python),
        }
    )
    environment.pop("S1_4X_BENCHMARK_PYTHON_PINNED_FD_PATH", None)
    return environment


def copied_test_venv(
    root: Path,
    *,
    numpy_version: str = "2.5.1",
) -> Path:
    """stdlib은 base home에서, dependency는 copied venv에서 찾는 launcher를 만든다."""

    venv_root = root / "oracle-venv"
    venv.EnvBuilder(with_pip=False, symlinks=False).create(venv_root)
    site_packages = venv_root / "lib/python3.12/site-packages"
    for package, version in (
        ("jsonschema", "4.26.0"),
        ("numpy", numpy_version),
    ):
        package_root = site_packages / package
        package_root.mkdir()
        (package_root / "__init__.py").write_text(
            f'__version__ = "{version}"\n',
            encoding="utf-8",
        )
        metadata_root = site_packages / f"{package}-{version}.dist-info"
        metadata_root.mkdir()
        (metadata_root / "METADATA").write_text(
            "Metadata-Version: 2.1\n"
            f"Name: {package}\n"
            f"Version: {version}\n",
            encoding="utf-8",
        )
    (site_packages / "s1_4x_venv_sentinel.py").write_text(
        'VALUE = "external-venv-dependency-closure"\n',
        encoding="utf-8",
    )
    python = venv_root / "bin/python"
    if not python.is_file() or python.is_symlink():
        raise AssertionError("copied test venv launcher is not a regular file")
    return python


class PythonRuntimeContractTests(unittest.TestCase):
    """모든 Haskell Python child가 accepted runtime 한 개만 소비하게 한다."""

    def test_orchestration_has_zero_system_or_path_python_fallbacks(self) -> None:
        profile_source = PROFILE_WORKFLOW.read_text(encoding="utf-8")
        self.assertNotIn("/usr/bin/python3", profile_source)
        for name in PYTHON_ORCHESTRATION_WRAPPERS:
            path = TOOLS_ROOT / name
            source = path.read_text(encoding="utf-8")
            with self.subTest(wrapper=name):
                self.assertNotIn("/usr/bin/python3", source)
                self.assertIsNone(
                    re.search(r"(?m)(?:^|[\s(])python3(?=\s)", source)
                )
        for path in sorted(TOOLS_ROOT.glob("*.py")):
            with self.subTest(python_tool=path.name):
                self.assertNotEqual(
                    path.read_text(encoding="utf-8").splitlines()[0],
                    "#!/usr/bin/env python3",
                )

    def test_every_orchestration_wrapper_uses_the_shared_runtime_pin(self) -> None:
        self.assertTrue(RUNTIME_HELPER.is_file(), "Python runtime pin helper is missing")
        for name in PYTHON_ORCHESTRATION_WRAPPERS:
            source = (TOOLS_ROOT / name).read_text(encoding="utf-8")
            with self.subTest(wrapper=name):
                self.assertIn("python-runtime.sh", source)
                self.assertIn("s1_4x_pin_benchmark_python", source)
                if name == "run-benchmark-block.sh":
                    self.assertIn(
                        "s1_4x_assert_benchmark_python_closure",
                        source,
                    )
                    self.assertIn(
                        'exec /usr/bin/env -i -a "$BENCHMARK_PYTHON"',
                        source,
                    )
                else:
                    self.assertRegex(
                        source,
                        r"s1_4x_(?:run|exec)_benchmark_python",
                    )
                if name != "run-benchmark-block.sh":
                    self.assertIsNone(
                        re.search(
                            r'(?m)^\s*(?:exec\s+)?"\$(?:S1_4X_)?'
                            r'BENCHMARK_PYTHON_PINNED_FD_PATH"',
                            source,
                        )
                    )

    def test_system_python_3_14_is_rejected(self) -> None:
        system_python = Path("/usr/bin/python3").resolve(strict=True)
        version = subprocess.run(
            [str(system_python), "--version"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        self.assertRegex(version, r"^Python 3\.14\.")
        completed = subprocess.run(
            [
                "/usr/bin/bash",
                "-c",
                'set -euo pipefail; source "$1"; s1_4x_pin_benchmark_python',
                "python-runtime-contract",
                str(RUNTIME_HELPER),
            ],
            env=runtime_environment(system_python),
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 69)
        self.assertIn(
            "benchmark Python must be CPython 3.12.13",
            completed.stderr,
        )

    def test_cpython_3_12_pinned_fd_preserves_venv_in_nested_child(self) -> None:
        self.assertEqual(sys.implementation.name, "cpython")
        self.assertEqual(sys.version_info[:3], (3, 12, 13))
        with tempfile.TemporaryDirectory() as temporary:
            temporary_root = Path(temporary).resolve()
            python = copied_test_venv(temporary_root)
            venv_root = python.parent.parent
            output_directory = temporary_root / "child-output"
            output_directory.mkdir()
            parent_script = temporary_root / "parent.py"
            parent_script.write_text(
                "import json\n"
                "import os\n"
                "import sys\n"
                "from pathlib import Path\n"
                "sys.path.insert(0, sys.argv[2])\n"
                "import profile_workflow as workflow\n"
                "fd_path = os.environ[\n"
                "    'S1_4X_BENCHMARK_PYTHON_PINNED_FD_PATH'\n"
                "]\n"
                "descriptor = int(fd_path.rsplit('/', 1)[1])\n"
                "pinned = os.fstat(descriptor)\n"
                "current = os.stat('/proc/self/exe')\n"
                "child_code = (\n"
                "    \"import json, os, sys, s1_4x_venv_sentinel; \"\n"
                "    \"p=os.environ['S1_4X_BENCHMARK_PYTHON_PINNED_FD_PATH']; \"\n"
                "    \"d=int(p.rsplit('/',1)[1]); f=os.fstat(d); \"\n"
                "    \"e=os.stat('/proc/self/exe'); \"\n"
                "    \"print(json.dumps({'implementation':sys.implementation.name,\"\n"
                "    \"'version':list(sys.version_info[:3]),\"\n"
                "    \"'prefix':sys.prefix,\"\n"
                "    \"'sentinel':s1_4x_venv_sentinel.VALUE,\"\n"
                "    \"'identityMatches':[f.st_dev,f.st_ino]==[e.st_dev,e.st_ino]}))\"\n"
                ")\n"
                "runtime = workflow._benchmark_python_runtime()\n"
                "record = workflow._run_logged(\n"
                "    [fd_path, '-I', '-c', child_code],\n"
                "    cwd=Path(sys.argv[2]),\n"
                "    environment=dict(os.environ),\n"
                "    phase='canonical-compare',\n"
                "    output_directory=Path(sys.argv[1]),\n"
                "    pass_fds=(descriptor,),\n"
                "    portable_path_ids={\n"
                "        fd_path: workflow._benchmark_python_path_id(runtime),\n"
                "    },\n"
                "    benchmark_python_runtime=runtime,\n"
                ")\n"
                "child = json.loads(\n"
                "    (Path(sys.argv[1]) / 'canonical-compare.stdout').read_text()\n"
                ")\n"
                "print(json.dumps({\n"
                "    'implementation': sys.implementation.name,\n"
                "    'version': list(sys.version_info[:3]),\n"
                "    'prefix': sys.prefix,\n"
                "    'identityMatches': (\n"
                "        (pinned.st_dev, pinned.st_ino)\n"
                "        == (current.st_dev, current.st_ino)\n"
                "    ),\n"
                "    'child': child,\n"
                "    'receiptArgv': record['argv'],\n"
                "}, sort_keys=True))\n",
                encoding="utf-8",
            )
            environment = runtime_environment(python)
            environment.update(
                {
                    "PYTHONHOME": "/hostile/python-home",
                    "PYTHONPATH": "/hostile/python-path",
                    "VIRTUAL_ENV": "/hostile/virtual-env",
                }
            )
            completed = subprocess.run(
                [
                    "/usr/bin/bash",
                    "-c",
                    (
                        'set -euo pipefail; source "$1"; '
                        "s1_4x_pin_benchmark_python; "
                        's1_4x_exec_benchmark_python -I "$2" "$3" "$4"'
                    ),
                    "python-runtime-contract",
                    str(RUNTIME_HELPER),
                    str(parent_script),
                    str(output_directory),
                    str(TOOLS_ROOT),
                ],
                env=environment,
                check=False,
                capture_output=True,
                text=True,
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        evidence = json.loads(completed.stdout)
        self.assertEqual(evidence["implementation"], "cpython")
        self.assertEqual(evidence["version"], [3, 12, 13])
        self.assertEqual(evidence["prefix"], str(venv_root))
        self.assertTrue(evidence["identityMatches"])
        self.assertEqual(evidence["child"]["implementation"], "cpython")
        self.assertEqual(evidence["child"]["version"], [3, 12, 13])
        self.assertEqual(evidence["child"]["prefix"], str(venv_root))
        self.assertEqual(
            evidence["child"]["sentinel"],
            "external-venv-dependency-closure",
        )
        self.assertTrue(evidence["child"]["identityMatches"])
        self.assertTrue(
            evidence["receiptArgv"][0].startswith(
                "S1_4X_BENCHMARK_CPYTHON_3_12_13_SHA256_"
            )
        )

    def test_python_runtime_rejects_mutable_or_ambient_venv_routes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()

            symlink_python = copied_test_venv(root / "source-symlink")
            real_python = symlink_python.with_name("python.real")
            symlink_python.rename(real_python)
            symlink_python.symlink_to(real_python.name)
            symlink_result = subprocess.run(
                [
                    "/usr/bin/bash",
                    "-c",
                    'set -euo pipefail; source "$1"; '
                    "s1_4x_pin_benchmark_python",
                    "python-runtime-contract",
                    str(RUNTIME_HELPER),
                ],
                env=runtime_environment(symlink_python),
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(symlink_result.returncode, 69)
            self.assertIn("source identity is unsafe", symlink_result.stderr)

            configuration_python = copied_test_venv(root / "config-symlink")
            configuration = configuration_python.parent.parent / "pyvenv.cfg"
            real_configuration = configuration.with_suffix(".cfg.real")
            configuration.rename(real_configuration)
            configuration.symlink_to(real_configuration.name)
            configuration_result = subprocess.run(
                [
                    "/usr/bin/bash",
                    "-c",
                    'set -euo pipefail; source "$1"; '
                    "s1_4x_pin_benchmark_python",
                    "python-runtime-contract",
                    str(RUNTIME_HELPER),
                ],
                env=runtime_environment(configuration_python),
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(configuration_result.returncode, 69)
            self.assertIn(
                "external venv layout is unsafe",
                configuration_result.stderr,
            )

            swapped_python = copied_test_venv(root / "source-swap")
            replacement = root / "source-swap/replacement-python"
            shutil.copy2(swapped_python, replacement)
            swapped_result = subprocess.run(
                [
                    "/usr/bin/bash",
                    "-c",
                    'set -euo pipefail; source "$1"; '
                    "s1_4x_pin_benchmark_python; "
                    '/usr/bin/cp --preserve=mode,timestamps '
                    '--remove-destination -- "$2" '
                    '"$S1_4X_BENCHMARK_PYTHON_BIN"; '
                    "s1_4x_run_benchmark_python -I -c "
                    "'raise SystemExit(0)'",
                    "python-runtime-contract",
                    str(RUNTIME_HELPER),
                    str(replacement),
                ],
                env=runtime_environment(swapped_python),
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(swapped_result.returncode, 69)
            self.assertIn(
                "does not match the pinned FD",
                swapped_result.stderr,
            )

            drifted_python = copied_test_venv(
                root / "dependency-drift",
                numpy_version="2.5.0",
            )
            drifted_result = subprocess.run(
                [
                    "/usr/bin/bash",
                    "-c",
                    'set -euo pipefail; source "$1"; '
                    "s1_4x_pin_benchmark_python",
                    "python-runtime-contract",
                    str(RUNTIME_HELPER),
                ],
                env=runtime_environment(drifted_python),
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(drifted_result.returncode, 69)
            self.assertIn(
                "dependency closure failed",
                drifted_result.stderr,
            )

            ambient_python = copied_test_venv(root / "ambient-shell")
            bash_environment = root / "ambient-shell/bash-env"
            bash_environment.write_text("# harmless test hook\n", encoding="utf-8")
            ambient_environment = runtime_environment(ambient_python)
            ambient_environment["BASH_ENV"] = str(bash_environment)
            ambient_result = subprocess.run(
                [
                    "/usr/bin/bash",
                    "-c",
                    'set -euo pipefail; source "$1"; '
                    "s1_4x_pin_benchmark_python",
                    "python-runtime-contract",
                    str(RUNTIME_HELPER),
                ],
                env=ambient_environment,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(ambient_result.returncode, 69)
            self.assertIn(
                "ambient shell startup injection is forbidden",
                ambient_result.stderr,
            )

            direct_python = copied_test_venv(root / "wrong-argv0")
            descriptor = os.open(direct_python, os.O_RDONLY)
            try:
                direct_result = subprocess.run(
                    [
                        f"/proc/self/fd/{descriptor}",
                        "-I",
                        "-S",
                        "-c",
                        "raise SystemExit(0)",
                    ],
                    env=runtime_environment(direct_python),
                    check=False,
                    capture_output=True,
                    text=True,
                    pass_fds=(descriptor,),
                )
            finally:
                os.close(descriptor)
            self.assertNotEqual(direct_result.returncode, 0)
            self.assertIn("No module named 'encodings'", direct_result.stderr)

    def test_python_runtime_rejects_post_pin_closure_drift(self) -> None:
        """Pin 이후 executable/config/dependency 변조도 새 기준으로 재수용하지 않는다."""

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()

            executable_python = copied_test_venv(root / "executable")
            executable_result = subprocess.run(
                [
                    "/usr/bin/bash",
                    "-c",
                    'set -euo pipefail; source "$1"; '
                    "s1_4x_pin_benchmark_python; "
                    '/usr/bin/cp -- "$2" '
                    '"$S1_4X_BENCHMARK_PYTHON_BIN"; '
                    "s1_4x_run_benchmark_python -I -c "
                    "'raise SystemExit(0)'",
                    "python-runtime-contract",
                    str(RUNTIME_HELPER),
                    "/usr/bin/true",
                ],
                env=runtime_environment(executable_python),
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(executable_result.returncode, 69)
            self.assertIn(
                "pinned FD SHA-256 mismatch",
                executable_result.stderr,
            )

            configuration_python = copied_test_venv(root / "configuration")
            configuration = configuration_python.parent.parent / "pyvenv.cfg"
            configuration_result = subprocess.run(
                [
                    "/usr/bin/bash",
                    "-c",
                    'set -euo pipefail; source "$1"; '
                    "s1_4x_pin_benchmark_python; "
                    '/usr/bin/touch -m -d "2030-01-01T00:00:00Z" "$2"; '
                    "s1_4x_run_benchmark_python -I -c "
                    "'raise SystemExit(0)'",
                    "python-runtime-contract",
                    str(RUNTIME_HELPER),
                    str(configuration),
                ],
                env=runtime_environment(configuration_python),
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(configuration_result.returncode, 69)
            self.assertIn(
                "pyvenv.cfg changed after pin",
                configuration_result.stderr,
            )

            dependency_python = copied_test_venv(root / "dependency")
            site_packages = (
                dependency_python.parent.parent
                / "lib/python3.12/site-packages"
            )
            metadata = site_packages / "numpy-2.5.1.dist-info/METADATA"
            module = site_packages / "numpy/__init__.py"
            replacement_metadata = root / "numpy-metadata-drift"
            replacement_metadata.write_text(
                "Metadata-Version: 2.1\n"
                "Name: numpy\n"
                "Version: 2.5.0\n",
                encoding="utf-8",
            )
            replacement_module = root / "numpy-module-drift"
            replacement_module.write_text(
                '__version__ = "2.5.0"\n',
                encoding="utf-8",
            )
            dependency_result = subprocess.run(
                [
                    "/usr/bin/bash",
                    "-c",
                    'set -euo pipefail; source "$1"; '
                    "s1_4x_pin_benchmark_python; "
                    '/usr/bin/cp -- "$2" "$3"; '
                    '/usr/bin/cp -- "$4" "$5"; '
                    "s1_4x_run_benchmark_python -I -c "
                    "'raise SystemExit(0)'",
                    "python-runtime-contract",
                    str(RUNTIME_HELPER),
                    str(replacement_metadata),
                    str(metadata),
                    str(replacement_module),
                    str(module),
                ],
                env=runtime_environment(dependency_python),
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(dependency_result.returncode, 69)
            self.assertIn(
                "dependency closure changed after pin",
                dependency_result.stderr,
            )

    def test_profile_nested_runtime_rejects_stored_closure_drift(self) -> None:
        """긴 workflow의 later child가 최초 Runtime closure를 다시 검증한다."""

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            controller = root / "controller.py"
            controller.write_text(
                "import os\n"
                "import shutil\n"
                "import sys\n"
                "from pathlib import Path\n"
                "sys.path.insert(0, sys.argv[2])\n"
                "import profile_workflow as workflow\n"
                "runtime = workflow._benchmark_python_runtime()\n"
                "mode = sys.argv[1]\n"
                "venv_root = runtime.source_path.parent.parent\n"
                "if mode == 'executable':\n"
                "    backup = runtime.source_path.with_name('python.opened')\n"
                "    runtime.source_path.rename(backup)\n"
                "    shutil.copy2(backup, runtime.source_path)\n"
                "    expected = 'COMMAND_RUNTIME_EXECUTABLE_CLOSURE_CHANGED'\n"
                "elif mode == 'configuration':\n"
                "    configuration = venv_root / 'pyvenv.cfg'\n"
                "    os.utime(\n"
                "        configuration,\n"
                "        ns=(1893456000000000000, 1893456000000000000),\n"
                "    )\n"
                "    expected = 'COMMAND_RUNTIME_VENV_CONFIGURATION_CHANGED'\n"
                "elif mode == 'dependency':\n"
                "    site = venv_root / 'lib/python3.12/site-packages'\n"
                "    (site / 'numpy-2.5.1.dist-info/METADATA').write_text(\n"
                "        'Metadata-Version: 2.1\\nName: numpy\\nVersion: 2.5.0\\n'\n"
                "    )\n"
                "    (site / 'numpy/__init__.py').write_text(\n"
                "        '__version__ = \"2.5.0\"\\n'\n"
                "    )\n"
                "    expected = 'COMMAND_RUNTIME_DEPENDENCY_CLOSURE_CHANGED'\n"
                "else:\n"
                "    raise SystemExit(90)\n"
                "output = Path(sys.argv[3])\n"
                "output.mkdir()\n"
                "try:\n"
                "    workflow._run_logged(\n"
                "        [str(runtime.fd_path), '-I', '-c', "
                "'raise SystemExit(0)'],\n"
                "        cwd=output,\n"
                "        environment=dict(os.environ),\n"
                "        phase='canonical-compare',\n"
                "        output_directory=output,\n"
                "        pass_fds=(runtime.descriptor,),\n"
                "        benchmark_python_runtime=runtime,\n"
                "    )\n"
                "except workflow.WorkflowError as error:\n"
                "    if expected in str(error):\n"
                "        raise SystemExit(0)\n"
                "    print(str(error), file=sys.stderr)\n"
                "    raise SystemExit(91)\n"
                "raise SystemExit(92)\n",
                encoding="utf-8",
            )
            for mode in ("executable", "configuration", "dependency"):
                with self.subTest(mode=mode):
                    python = copied_test_venv(root / mode)
                    descriptor = os.open(python, os.O_RDONLY)
                    output = root / f"{mode}-output"
                    try:
                        environment = runtime_environment(python)
                        environment[
                            "S1_4X_BENCHMARK_PYTHON_PINNED_FD_PATH"
                        ] = f"/proc/self/fd/{descriptor}"
                        environment["PYTHONDONTWRITEBYTECODE"] = "1"
                        completed = subprocess.run(
                            [
                                str(python),
                                str(controller),
                                mode,
                                str(TOOLS_ROOT),
                                str(output),
                            ],
                            executable=f"/proc/self/fd/{descriptor}",
                            env=environment,
                            check=False,
                            capture_output=True,
                            text=True,
                            pass_fds=(descriptor,),
                        )
                    finally:
                        os.close(descriptor)
                    self.assertEqual(
                        completed.returncode,
                        0,
                        completed.stderr,
                    )


if __name__ == "__main__":
    unittest.main()
