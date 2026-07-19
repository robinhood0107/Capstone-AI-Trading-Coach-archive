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

    def test_cpython_3_12_pinned_fd_survives_nested_child(self) -> None:
        self.assertEqual(sys.implementation.name, "cpython")
        self.assertEqual(sys.version_info[:3], (3, 12, 13))
        python = Path(sys.executable).resolve(strict=True)
        with tempfile.TemporaryDirectory() as temporary:
            parent_script = Path(temporary).resolve() / "parent.py"
            parent_script.write_text(
                "import json\n"
                "import os\n"
                "import subprocess\n"
                "import sys\n"
                "fd_path = os.environ[\n"
                "    'S1_4X_BENCHMARK_PYTHON_PINNED_FD_PATH'\n"
                "]\n"
                "descriptor = int(fd_path.rsplit('/', 1)[1])\n"
                "pinned = os.fstat(descriptor)\n"
                "current = os.stat('/proc/self/exe')\n"
                "child_code = (\n"
                "    \"import json, os, sys; \"\n"
                "    \"p=os.environ['S1_4X_BENCHMARK_PYTHON_PINNED_FD_PATH']; \"\n"
                "    \"d=int(p.rsplit('/',1)[1]); f=os.fstat(d); \"\n"
                "    \"e=os.stat('/proc/self/exe'); \"\n"
                "    \"print(json.dumps({'implementation':sys.implementation.name,\"\n"
                "    \"'version':list(sys.version_info[:3]),\"\n"
                "    \"'identityMatches':[f.st_dev,f.st_ino]==[e.st_dev,e.st_ino]}))\"\n"
                ")\n"
                "child = subprocess.run(\n"
                "    [fd_path, '-I', '-S', '-c', child_code],\n"
                "    check=True,\n"
                "    capture_output=True,\n"
                "    text=True,\n"
                "    pass_fds=(descriptor,),\n"
                ")\n"
                "print(json.dumps({\n"
                "    'implementation': sys.implementation.name,\n"
                "    'version': list(sys.version_info[:3]),\n"
                "    'identityMatches': (\n"
                "        (pinned.st_dev, pinned.st_ino)\n"
                "        == (current.st_dev, current.st_ino)\n"
                "    ),\n"
                "    'child': json.loads(child.stdout),\n"
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
                        'exec "$S1_4X_BENCHMARK_PYTHON_PINNED_FD_PATH" '
                        '-I -S "$2"'
                    ),
                    "python-runtime-contract",
                    str(RUNTIME_HELPER),
                    str(parent_script),
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
        self.assertTrue(evidence["identityMatches"])
        self.assertEqual(evidence["child"]["implementation"], "cpython")
        self.assertEqual(evidence["child"]["version"], [3, 12, 13])
        self.assertTrue(evidence["child"]["identityMatches"])


if __name__ == "__main__":
    unittest.main()
