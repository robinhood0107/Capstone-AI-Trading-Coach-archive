"""Haskell orchestration의 exact CPython 3.12.13 pinned-FD 계약 테스트."""

from __future__ import annotations

import hashlib
import json
import os
import re
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
                self.assertRegex(
                    source,
                    r"s1_4x_(?:run|exec)_benchmark_python",
                )
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
            venv_root = temporary_root / "oracle-venv"
            venv.EnvBuilder(with_pip=False, symlinks=False).create(venv_root)
            python = venv_root / "bin/python"
            self.assertTrue(python.is_file())
            self.assertFalse(python.is_symlink())
            site_packages = (
                venv_root
                / "lib"
                / "python3.12"
                / "site-packages"
            )
            site_packages.mkdir(parents=True, exist_ok=True)
            for package, version in (
                ("jsonschema", "4.26.0"),
                ("numpy", "2.5.1"),
            ):
                package_root = site_packages / package
                package_root.mkdir()
                (package_root / "__init__.py").write_text(
                    f'__version__ = "{version}"\n',
                    encoding="utf-8",
                )
                metadata_root = (
                    site_packages / f"{package}-{version}.dist-info"
                )
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
                env=runtime_environment(python),
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


if __name__ == "__main__":
    unittest.main()
