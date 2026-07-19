"""Haskell qualification host-validator Docker trust boundary regressions."""

from __future__ import annotations

import ast
import dataclasses
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


TOOLS_ROOT = Path(__file__).resolve().parents[1]
HELPER_PATH = TOOLS_ROOT / "profile_workflow.py"


def load_helper():
    specification = importlib.util.spec_from_file_location(
        "profile_workflow_qualification_docker",
        HELPER_PATH,
    )
    if specification is None or specification.loader is None:
        raise AssertionError("unable to load profile_workflow.py")
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


def write_executable(path: Path, payload: bytes) -> str:
    path.write_bytes(payload)
    path.chmod(0o755)
    return hashlib.sha256(payload).hexdigest()


class QualificationDockerTrustTests(unittest.TestCase):
    def test_temporary_directories_defer_to_external_tmpdir(self) -> None:
        source = Path(__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        hardcoded = [
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "TemporaryDirectory"
            and any(keyword.arg == "dir" for keyword in node.keywords)
        ]

        self.assertEqual(hardcoded, [])

    def test_validator_style_grandchild_executes_retained_parent_fd(
        self,
    ) -> None:
        helper = load_helper()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            output = root / "qualification"
            output.mkdir(mode=0o700)
            trusted = root / "trusted-docker.exe"
            trusted_sha256 = write_executable(
                trusted,
                (
                    b"#!/usr/bin/bash\n"
                    b"printf 'trusted-argc=%s argv=%s|%s\\n' \"$#\" \"$1\" \"$2\"\n"
                ),
            )
            ambient_bin = root / "ambient-bin"
            ambient_bin.mkdir()
            ambient_sentinel = root / "ambient-executed"
            home = root / "home"
            ambient_config = home / ".docker"
            ambient_config.mkdir(parents=True)
            (ambient_config / "config.json").write_text(
                '{"currentContext":"remote-forged"}\n',
                encoding="utf-8",
            )
            write_executable(
                ambient_bin / "docker",
                (
                    b"#!/usr/bin/bash\n"
                    b"printf ambient > \"$AMBIENT_SENTINEL\"\n"
                    b"exit 99\n"
                ),
            )
            pinned = helper.pin_oci_docker_client(
                trusted,
                expected_sha256=trusted_sha256,
            )
            try:
                route = helper.prepare_qualification_docker_route(
                    output,
                    docker_client=pinned,
                )
                environment = helper.qualification_environment_with_docker_route(
                    {
                        "PATH": str(ambient_bin),
                        "LC_ALL": "C",
                        "HOME": str(home),
                        "AMBIENT_SENTINEL": str(ambient_sentinel),
                        "DOCKER_HOST": "tcp://forged.invalid:2376",
                        "DOCKER_CONTEXT": "remote-forged",
                    },
                    route=route,
                    docker_client=pinned,
                )
                self.assertEqual(
                    environment["PATH"],
                    str(route.host_tools_directory),
                )
                self.assertNotIn(str(ambient_bin), environment["PATH"])
                self.assertEqual(
                    environment["DOCKER_CONFIG"],
                    str(route.docker_config_directory),
                )
                self.assertFalse(any(route.docker_config_directory.iterdir()))
                self.assertNotIn("DOCKER_HOST", environment)
                self.assertEqual(environment["DOCKER_CONTEXT"], "default")
                self.assertEqual(
                    environment["WSLENV"],
                    "DOCKER_CONFIG/p:DOCKER_CONTEXT",
                )
                before = helper.snapshot_qualification_docker_route(
                    route,
                    docker_client=pinned,
                )
                validator_style = subprocess.run(
                    [
                        sys.executable,
                        "-c",
                        (
                            "import json, subprocess\n"
                            "result = subprocess.run("
                            "['docker', 'ps', '-q'], "
                            "check=False, capture_output=True, text=True)\n"
                            "print(json.dumps({"
                            "'argv': ['docker', 'ps', '-q'], "
                            "'exitCode': result.returncode, "
                            "'stdout': result.stdout, "
                            "'stderr': result.stderr"
                            "}, sort_keys=True))\n"
                            "raise SystemExit(result.returncode)\n"
                        ),
                    ],
                    check=False,
                    capture_output=True,
                    text=True,
                    env=environment,
                    pass_fds=(pinned.descriptor,),
                )
                (output / "host.stdout").write_bytes(
                    validator_style.stdout.encode("utf-8")
                )
                (output / "host.stderr").write_bytes(
                    validator_style.stderr.encode("utf-8")
                )
                after = helper.snapshot_qualification_docker_route(
                    route,
                    docker_client=pinned,
                )

                self.assertEqual(validator_style.returncode, 0)
                self.assertEqual(before, after)
                self.assertEqual(
                    json.loads(validator_style.stdout),
                    {
                        "argv": ["docker", "ps", "-q"],
                        "exitCode": 0,
                        "stderr": "",
                        "stdout": "trusted-argc=2 argv=ps|-q\n",
                    },
                )
                self.assertFalse(ambient_sentinel.exists())
            finally:
                os.close(pinned.descriptor)

    def test_output_bound_route_ignores_ambient_docker_and_is_portable(
        self,
    ) -> None:
        helper = load_helper()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            output = root / "qualification"
            output.mkdir(mode=0o700)
            trusted = root / "trusted-docker.exe"
            trusted_sha256 = write_executable(
                trusted,
                b"#!/usr/bin/bash\nexit 0\n",
            )
            ambient_bin = root / "ambient-bin"
            ambient_bin.mkdir()
            ambient = ambient_bin / "docker"
            write_executable(
                ambient,
                b"#!/usr/bin/bash\nexit 99\n",
            )
            with mock.patch.dict(
                os.environ,
                {
                    "S1_4X_DOCKER_BIN": str(trusted),
                    "S1_4X_DOCKER_SHA256": trusted_sha256,
                },
                clear=True,
            ):
                pinned = helper.pin_qualification_docker_client_from_environment()
            try:
                route = helper.prepare_qualification_docker_route(
                    output,
                    docker_client=pinned,
                )
                snapshot = helper.snapshot_qualification_docker_route(
                    route,
                    docker_client=pinned,
                )
                environment = helper.qualification_environment_with_docker_route(
                    {"PATH": str(ambient_bin), "LC_ALL": "C"},
                    route=route,
                    docker_client=pinned,
                )
                receipt = helper.build_qualification_docker_route_receipt(
                    route,
                    docker_client=pinned,
                    baseline=snapshot,
                )

                self.assertEqual(
                    os.readlink(route.docker_link),
                    f"/proc/{os.getpid()}/fd/{pinned.descriptor}",
                )
                self.assertEqual(
                    shutil.which("docker", path=environment["PATH"]),
                    str(route.docker_link),
                )
                self.assertEqual(
                    environment["PATH"],
                    str(route.host_tools_directory),
                )
                self.assertNotEqual(
                    shutil.which("docker", path=environment["PATH"]),
                    str(ambient),
                )
                self.assertEqual(
                    receipt["dockerClientPathId"],
                    pinned.path_id,
                )
                self.assertEqual(
                    receipt["snapshotSha256"],
                    snapshot["snapshotSha256"],
                )
                self.assertEqual(receipt["snapshot"], snapshot)
                self.assertEqual(
                    helper.validate_qualification_docker_route_receipt(
                        receipt,
                        require_owner_exit=False,
                    ),
                    receipt,
                )
                forged_sha256 = "f" * 64
                coherently_tampered_outer = {
                    **receipt,
                    "dockerClientSha256": forged_sha256,
                    "dockerClientPathId": (
                        "S1_4X_DOCKER_CLIENT_SHA256_"
                        f"{forged_sha256.upper()}"
                    ),
                    "snapshotSha256": "e" * 64,
                }
                with self.assertRaisesRegex(
                    helper.WorkflowError,
                    (
                        "QUALIFICATION_DOCKER_RECEIPT_"
                        "SNAPSHOT_BINDING_INVALID"
                    ),
                ):
                    helper.validate_qualification_docker_route_receipt(
                        coherently_tampered_outer,
                        require_owner_exit=False,
                    )
                helper._validate_qualification_docker_owner_binding(
                    receipt,
                    {
                        "owner": {
                            "pid": receipt["owner"]["pid"],
                            "startTicks": receipt["owner"]["startTicks"],
                        }
                    },
                )
                tampered_owner = {
                    **receipt,
                    "owner": {
                        **receipt["owner"],
                        "startTicks": receipt["owner"]["startTicks"] + 1,
                    },
                }
                with self.assertRaisesRegex(
                    helper.WorkflowError,
                    "QUALIFICATION_DOCKER_OWNER_BINDING_INVALID",
                ):
                    helper._validate_qualification_docker_owner_binding(
                        tampered_owner,
                        {
                            "owner": {
                                "pid": receipt["owner"]["pid"],
                                "startTicks": receipt["owner"][
                                    "startTicks"
                                ],
                            }
                        },
                    )
                rendered = helper.canonical_json_bytes(receipt).decode("utf-8")
                self.assertNotIn(str(root), rendered)
                self.assertNotIn("/proc/", rendered)
            finally:
                os.close(pinned.descriptor)

    def test_environment_pin_rejects_sha_mismatch_before_route_creation(
        self,
    ) -> None:
        helper = load_helper()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            trusted = root / "docker.exe"
            write_executable(trusted, b"trusted docker bytes")
            with mock.patch.dict(
                os.environ,
                {
                    "S1_4X_DOCKER_BIN": str(trusted),
                    "S1_4X_DOCKER_SHA256": "0" * 64,
                },
                clear=True,
            ), self.assertRaisesRegex(
                helper.WorkflowError,
                "DOCKER_SHA256_MISMATCH",
            ):
                helper.pin_qualification_docker_client_from_environment()

    def test_route_rejects_dead_owner_and_symlink_swap(self) -> None:
        helper = load_helper()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            output = root / "qualification"
            output.mkdir(mode=0o700)
            trusted = root / "docker.exe"
            trusted_sha256 = write_executable(
                trusted,
                b"#!/usr/bin/bash\nexit 0\n",
            )
            forged = root / "forged-docker"
            write_executable(
                forged,
                b"#!/usr/bin/bash\nexit 99\n",
            )
            pinned = helper.pin_oci_docker_client(
                trusted,
                expected_sha256=trusted_sha256,
            )
            try:
                route = helper.prepare_qualification_docker_route(
                    output,
                    docker_client=pinned,
                )
                exited = subprocess.Popen(["/usr/bin/true"])
                exited.wait(timeout=5)
                dead_owner = dataclasses.replace(
                    route,
                    owner_pid=exited.pid,
                    owner_start_ticks=1,
                )
                with self.assertRaisesRegex(
                    helper.WorkflowError,
                    "QUALIFICATION_DOCKER_OWNER_NOT_LIVE",
                ):
                    helper.snapshot_qualification_docker_route(
                        dead_owner,
                        docker_client=pinned,
                    )

                route.docker_link.unlink()
                route.docker_link.symlink_to(forged)
                with self.assertRaisesRegex(
                    helper.WorkflowError,
                    "QUALIFICATION_DOCKER_LINK_(IDENTITY_)?DRIFT",
                ):
                    helper.snapshot_qualification_docker_route(
                        route,
                        docker_client=pinned,
                    )
            finally:
                os.close(pinned.descriptor)

    def test_route_rejects_retained_fd_drift(self) -> None:
        helper = load_helper()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            output = root / "qualification"
            output.mkdir(mode=0o700)
            trusted = root / "docker.exe"
            trusted_sha256 = write_executable(
                trusted,
                b"#!/usr/bin/bash\nexit 0\n",
            )
            pinned = helper.pin_oci_docker_client(
                trusted,
                expected_sha256=trusted_sha256,
            )
            route = helper.prepare_qualification_docker_route(
                output,
                docker_client=pinned,
            )
            os.close(pinned.descriptor)

            with self.assertRaisesRegex(
                helper.WorkflowError,
                "OCI_DOCKER_CLIENT_FD_NOT_LIVE",
            ):
                helper.snapshot_qualification_docker_route(
                    route,
                    docker_client=pinned,
                )

    def test_route_rejects_output_bound_docker_config_drift(self) -> None:
        helper = load_helper()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            output = root / "qualification"
            output.mkdir(mode=0o700)
            trusted = root / "docker.exe"
            trusted_sha256 = write_executable(
                trusted,
                b"#!/usr/bin/bash\nexit 0\n",
            )
            pinned = helper.pin_oci_docker_client(
                trusted,
                expected_sha256=trusted_sha256,
            )
            try:
                route = helper.prepare_qualification_docker_route(
                    output,
                    docker_client=pinned,
                )
                helper.snapshot_qualification_docker_route(
                    route,
                    docker_client=pinned,
                )
                (route.docker_config_directory / "config.json").write_text(
                    '{"currentContext":"forged"}\n',
                    encoding="utf-8",
                )

                with self.assertRaisesRegex(
                    helper.WorkflowError,
                    "QUALIFICATION_DOCKER_CONFIG_DRIFT",
                ):
                    helper.snapshot_qualification_docker_route(
                        route,
                        docker_client=pinned,
                    )
            finally:
                os.close(pinned.descriptor)


if __name__ == "__main__":
    unittest.main()
