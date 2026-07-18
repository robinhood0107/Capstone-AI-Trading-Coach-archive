"""Digest-pinned offline Haskell OCI correctness wrapper contract tests."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


TOOLS_ROOT = Path(__file__).resolve().parents[1]
HASKELL_ROOT = TOOLS_ROOT.parent
HELPER_PATH = TOOLS_ROOT / "profile_workflow.py"
WRAPPER_PATH = TOOLS_ROOT / "run-oci-correctness.sh"
CONTAINERFILE_PATH = HASKELL_ROOT / "Containerfile"
BASE_DIGEST = (
    "sha256:417d4bc30ac7d8d5ff04ec97937f86eb508b0c76bfd1a39b5ec225688531aa9d"
)


def load_helper():
    specification = importlib.util.spec_from_file_location(
        "profile_workflow",
        HELPER_PATH,
    )
    if specification is None or specification.loader is None:
        raise AssertionError("unable to load profile_workflow.py")
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


class OciCorrectnessContractTests(unittest.TestCase):
    def test_containerfile_is_digest_pinned_and_contains_no_dependency_fetch(
        self,
    ) -> None:
        self.assertTrue(CONTAINERFILE_PATH.is_file(), "Containerfile is missing")
        source = CONTAINERFILE_PATH.read_text(encoding="utf-8")
        self.assertIn(
            f"FROM docker.io/library/haskell@{BASE_DIGEST}",
            source,
        )
        self.assertIn("ARG S1_4X_BINARY_SHA256", source)
        self.assertIn("COPY --chmod=0555 s1-4x-haskell", source)
        self.assertIn("COPY --chown=65532:65532 fixtures", source)
        self.assertIn("USER 65532:65532", source)
        self.assertIn('ENTRYPOINT ["/usr/local/bin/s1-4x-haskell"]', source)
        for forbidden in (
            "FROM haskell:",
            "apt-get",
            "apk add",
            "curl ",
            "wget ",
            "COPY . ",
            "ADD ",
            "stack build",
            "cabal update",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)

    def test_docker_commands_force_offline_build_and_runtime_isolation(self) -> None:
        helper = load_helper()
        build = helper.build_oci_build_command(
            docker=Path("/tools/docker"),
            containerfile=Path("/cache/context/Containerfile"),
            context=Path("/cache/context"),
            image_tag="local/s1-4x-haskell:abc",
            binary_sha256="1" * 64,
        )
        self.assertEqual(
            build,
            [
                "/tools/docker",
                "build",
                "--network",
                "none",
                "--pull=false",
                "--file",
                "/cache/context/Containerfile",
                "--build-arg",
                f"S1_4X_BINARY_SHA256={'1' * 64}",
                "--tag",
                "local/s1-4x-haskell:abc",
                "/cache/context",
            ],
        )
        run = helper.build_oci_run_command(
            docker=Path("/tools/docker"),
            image_tag="local/s1-4x-haskell:abc",
            output_directory=Path("/evidence/runtime"),
            output_name="canonical.actual.json",
            request_path="/opt/s1-4x/fixtures/small/canonical-inputs.v1.json",
            uid=1000,
            gid=1000,
        )
        self.assertIn("--network", run)
        self.assertEqual(run[run.index("--network") + 1], "none")
        self.assertIn("--read-only", run)
        self.assertIn("--cap-drop=ALL", run)
        self.assertIn("--security-opt=no-new-privileges", run)
        self.assertIn("--user", run)
        self.assertEqual(run[run.index("--user") + 1], "1000:1000")
        self.assertEqual(
            run[run.index("--mount") + 1],
            "type=bind,src=/evidence/runtime,dst=/out",
        )
        rendered = " ".join(run)
        self.assertNotIn("/home/", rendered)
        self.assertNotIn("/repo/", rendered)
        self.assertNotIn("docker.sock", rendered)

    def test_wrapper_requires_selected_profile_and_pinned_docker_identity(
        self,
    ) -> None:
        self.assertTrue(WRAPPER_PATH.is_file(), "OCI wrapper is missing")
        source = WRAPPER_PATH.read_text(encoding="utf-8")
        for required in (
            "--output-dir",
            "ABSOLUTE_NEW_DIRECTORY",
            "select-proven-profile.sh",
            "--check",
            "S1_4X_DOCKER_BIN",
            "S1_4X_DOCKER_SHA256",
            "profile_workflow.py",
            "oci-correctness",
            "--network",
            "none",
        ):
            with self.subTest(required=required):
                self.assertIn(required, source)
        for forbidden in (
            "eval ",
            "bash -c",
            "sh -c",
            "/var/run/docker.sock",
            "--privileged",
            "--network host",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
