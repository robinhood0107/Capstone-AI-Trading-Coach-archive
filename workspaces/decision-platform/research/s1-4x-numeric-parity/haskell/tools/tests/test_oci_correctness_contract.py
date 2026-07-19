"""Digest-pinned offline Haskell OCI correctness wrapper contract tests."""

from __future__ import annotations

import hashlib
import importlib.util
import os
import sys
import tempfile
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
            iidfile=Path("/evidence/image.iid"),
            image_tag="local/s1-4x-haskell:abc",
            binary_sha256="1" * 64,
            provenance_labels={
                "io.s1-4x.base-image-id": f"sha256:{'2' * 64}",
                "io.s1-4x.containerfile-sha256": "3" * 64,
                "io.s1-4x.fixture-tree-sha256": "4" * 64,
            },
        )
        self.assertEqual(
            build,
            [
                "/tools/docker",
                "build",
                "--platform",
                "linux/amd64",
                "--network",
                "none",
                "--pull=false",
                "--file",
                "/cache/context/Containerfile",
                "--build-arg",
                f"S1_4X_BINARY_SHA256={'1' * 64}",
                "--label",
                f"io.s1-4x.base-image-id=sha256:{'2' * 64}",
                "--label",
                f"io.s1-4x.containerfile-sha256={'3' * 64}",
                "--label",
                f"io.s1-4x.fixture-tree-sha256={'4' * 64}",
                "--iidfile",
                "/evidence/image.iid",
                "--tag",
                "local/s1-4x-haskell:abc",
                "/cache/context",
            ],
        )
        run = helper.build_oci_run_command(
            docker=Path("/tools/docker"),
            image_id=f"sha256:{'2' * 64}",
            output_directory=Path("/evidence/runtime"),
            output_name="canonical.actual.json",
            request_path="/opt/s1-4x/fixtures/small/canonical-inputs.v1.json",
            uid=1000,
            gid=1000,
        )
        self.assertIn("--network", run)
        self.assertEqual(run[run.index("--network") + 1], "none")
        self.assertIn("--read-only", run)
        self.assertEqual(run[run.index("--platform") + 1], "linux/amd64")
        self.assertIn("--cap-drop=ALL", run)
        self.assertIn("--security-opt=no-new-privileges", run)
        self.assertIn("--user", run)
        self.assertEqual(run[run.index("--user") + 1], "1000:1000")
        self.assertEqual(
            run[run.index("--mount") + 1],
            "type=bind,src=/evidence/runtime,dst=/out",
        )
        self.assertIn(f"sha256:{'2' * 64}", run)
        self.assertNotIn("local/s1-4x-haskell:abc", run)
        rendered = " ".join(run)
        self.assertNotIn("/" + "home/", rendered)
        self.assertNotIn("/repo/", rendered)
        self.assertNotIn("docker.sock", rendered)

    def test_inspected_tag_binding_rejects_retag_or_substitution(self) -> None:
        helper = load_helper()
        image_tag = "local/s1-4x-haskell:abc"
        image_id = f"sha256:{'2' * 64}"
        inspection = {
            "Id": image_id,
            "RepoTags": [image_tag],
            "Os": "linux",
            "Architecture": "amd64",
        }

        self.assertEqual(
            helper.validate_oci_image_inspection(
                inspection,
                image_tag=image_tag,
                expected_image_id=image_id,
            ),
            image_id,
        )
        for altered in (
            {**inspection, "Id": f"sha256:{'3' * 64}"},
            {**inspection, "RepoTags": ["local/s1-4x-haskell:retagged"]},
            {**inspection, "Os": "windows"},
            {**inspection, "Architecture": "arm64"},
        ):
            with self.subTest(altered=altered):
                with self.assertRaises(helper.WorkflowError):
                    helper.validate_oci_image_inspection(
                        altered,
                        image_tag=image_tag,
                        expected_image_id=image_id,
                    )

    def test_daemon_base_and_iid_are_exact_linux_amd64_objects(self) -> None:
        helper = load_helper()
        daemon_document = {
            "ID": "daemon-id",
            "OSType": "linux",
            "Architecture": "x86_64",
            "ServerVersion": "28.3.2",
            "OperatingSystem": "Docker Desktop",
        }
        for context_name in ("default", "desktop-linux"):
            with self.subTest(context_name=context_name):
                expected_daemon = {
                    "contextName": context_name,
                    "daemonId": "daemon-id",
                    "serverVersion": "28.3.2",
                    "operatingSystem": "Docker Desktop",
                    "osType": "linux",
                    "architecture": "amd64",
                    "platform": "linux/amd64",
                }
                daemon = helper.validate_oci_daemon_identity(
                    daemon_document,
                    context_name=context_name,
                )
                self.assertEqual(daemon, expected_daemon)
                self.assertEqual(
                    helper.validate_oci_daemon_identity_pair(
                        daemon,
                        dict(daemon),
                    ),
                    helper.canonical_sha256(daemon),
                )

        accepted = helper.validate_oci_daemon_identity(
            daemon_document,
            context_name="default",
        )
        changed = helper.validate_oci_daemon_identity(
            {**daemon_document, "ID": "other-daemon"},
            context_name="default",
        )
        with self.assertRaisesRegex(
            helper.WorkflowError,
            "OCI_DAEMON_CHANGED_DURING_RUN",
        ):
            helper.validate_oci_daemon_identity_pair(accepted, changed)

        for document, context_name in (
            (
                {
                    "ID": "daemon-id",
                    "OSType": "windows",
                    "Architecture": "x86_64",
                    "ServerVersion": "28.3.2",
                    "OperatingSystem": "Docker Desktop",
                },
                "default",
            ),
            (
                daemon_document,
                "../unsafe-context",
            ),
        ):
            with self.subTest(document=document, context=context_name):
                with self.assertRaises(helper.WorkflowError):
                    helper.validate_oci_daemon_identity(
                        document,
                        context_name=context_name,
                    )

    def test_docker_context_is_safe_nonempty_runtime_identity(self) -> None:
        helper = load_helper()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            context_log = root / "context.stdout"
            for context_name in ("default", "desktop-linux"):
                with self.subTest(context_name=context_name):
                    context_log.write_bytes(f"{context_name}\n".encode("ascii"))
                    record = {
                        "stdoutPath": str(context_log),
                        "stdoutSha256": hashlib.sha256(
                            context_log.read_bytes()
                        ).hexdigest(),
                    }
                    self.assertEqual(
                        helper._oci_context_name(
                            context_log,
                            command_record=record,
                        ),
                        context_name,
                    )
            for invalid in (b"\n", b"../unsafe\n", b"default extra\n"):
                with self.subTest(invalid=invalid):
                    context_log.write_bytes(invalid)
                    record = {
                        "stdoutPath": str(context_log),
                        "stdoutSha256": hashlib.sha256(invalid).hexdigest(),
                    }
                    with self.assertRaisesRegex(
                        helper.WorkflowError,
                        "OCI_CONTEXT_NAME_INVALID",
                    ):
                        helper._oci_context_name(
                            context_log,
                            command_record=record,
                        )

    def test_arbitrary_caller_trusted_client_is_pinned_and_mismatch_rejected(
        self,
    ) -> None:
        helper = load_helper()
        with tempfile.TemporaryDirectory() as temporary:
            docker = Path(temporary).resolve() / "docker"
            docker.write_bytes(b"arbitrary caller-trusted docker client bytes")
            docker.chmod(0o755)
            expected_sha256 = helper.sha256_file(docker)
            pinned = helper.pin_oci_docker_client(
                docker,
                expected_sha256=expected_sha256,
            )
            try:
                self.assertEqual(pinned.source_path, docker)
                self.assertEqual(pinned.sha256, expected_sha256)
                self.assertEqual(
                    pinned.fd_path,
                    Path(f"/proc/self/fd/{pinned.descriptor}"),
                )
                self.assertRegex(
                    pinned.path_id,
                    rf"^S1_4X_DOCKER_CLIENT_SHA256_{expected_sha256.upper()}$",
                )
            finally:
                os.close(pinned.descriptor)

            with self.assertRaisesRegex(
                helper.WorkflowError,
                "DOCKER_SHA256_MISMATCH",
            ):
                helper.pin_oci_docker_client(
                    docker,
                    expected_sha256="0" * 64,
                )

    def test_empty_output_bound_docker_config_and_client_stage_snapshots(
        self,
    ) -> None:
        helper = load_helper()
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary).resolve() / "evidence"
            output.mkdir()
            docker_config = output / "docker-config"
            docker_config.mkdir(mode=0o700)
            docker = output / "docker.exe"
            docker.write_bytes(b"fixture docker executable")
            docker.chmod(0o755)
            docker_sha256 = helper.sha256_file(docker)
            pinned = helper.pin_oci_docker_client(
                docker,
                expected_sha256=docker_sha256,
            )

            try:
                before = helper.snapshot_oci_docker_stage(
                    stage="context-before",
                    docker_client=pinned,
                    docker_config=docker_config,
                    output_root=output,
                )
                self.assertEqual(before["dockerConfigEntryCount"], 0)
                self.assertEqual(before["dockerClientSha256"], docker_sha256)
                self.assertEqual(
                    before["dockerClientPathId"],
                    pinned.path_id,
                )

                after = helper.snapshot_oci_docker_stage(
                    stage="context-before",
                    docker_client=pinned,
                    docker_config=docker_config,
                    output_root=output,
                )
                helper.validate_oci_docker_stage_pair(before, after)

                (docker_config / "config.json").write_text(
                    '{"currentContext":"forged"}\n',
                    encoding="utf-8",
                )
                changed = helper.snapshot_oci_docker_stage(
                    stage="context-before",
                    docker_client=pinned,
                    docker_config=docker_config,
                    output_root=output,
                )
                with self.assertRaisesRegex(
                    helper.WorkflowError,
                    "OCI_DOCKER_TRUST_STAGE_CHANGED",
                ):
                    helper.validate_oci_docker_stage_pair(before, changed)
            finally:
                os.close(pinned.descriptor)

    def test_retained_docker_fd_executes_pinned_bytes_and_rejects_path_aba(
        self,
    ) -> None:
        helper = load_helper()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            output = root / "evidence"
            output.mkdir()
            docker_config = output / "docker-config"
            docker_config.mkdir(mode=0o700)
            docker = root / "docker"
            original = b"#!/usr/bin/bash\nprintf 'sealed-client\\n'\n"
            forged = b"#!/usr/bin/bash\nprintf 'forged-client\\n'\n"
            docker.write_bytes(original)
            docker.chmod(0o755)
            pinned = helper.pin_oci_docker_client(
                docker,
                expected_sha256=hashlib.sha256(original).hexdigest(),
            )
            saved = root / "docker.original"
            try:
                record = helper._run_pinned_oci_docker_logged(
                    pinned,
                    [str(pinned.fd_path)],
                    cwd=root,
                    environment={
                        "PATH": "/usr/bin:/bin",
                        "LC_ALL": "C",
                        "DOCKER_CONFIG": str(docker_config),
                    },
                    phase="oci-pinned-client",
                    output_directory=output,
                )
                self.assertEqual(
                    (output / "oci-pinned-client.stdout").read_text(
                        encoding="utf-8"
                    ),
                    "sealed-client\n",
                )
                self.assertEqual(record["argv"], [pinned.path_id])

                docker.rename(saved)
                docker.write_bytes(forged)
                docker.chmod(0o755)
                with self.assertRaisesRegex(
                    helper.WorkflowError,
                    "OCI_DOCKER_CLIENT_FD_IDENTITY_INVALID",
                ):
                    helper._run_pinned_oci_docker_logged(
                        pinned,
                        [str(pinned.fd_path)],
                        cwd=root,
                        environment={
                            "PATH": "/usr/bin:/bin",
                            "LC_ALL": "C",
                            "DOCKER_CONFIG": str(docker_config),
                        },
                        phase="oci-aba",
                        output_directory=output,
                    )
                self.assertFalse((output / "oci-aba.stdout").exists())
            finally:
                if saved.exists():
                    docker.unlink(missing_ok=True)
                    saved.rename(docker)
                os.close(pinned.descriptor)

    def test_oci_flow_uses_exact_client_sha_and_per_command_trust_snapshots(
        self,
    ) -> None:
        helper = load_helper()
        helper_source = HELPER_PATH.read_text(encoding="utf-8")
        wrapper_source = WRAPPER_PATH.read_text(encoding="utf-8")

        self.assertNotIn(
            "834d45bd30c6d08f1045f39a48fda64c",
            helper_source + wrapper_source,
        )
        self.assertNotIn("DOCKER_TRUST_ANCHOR_NOT_FROZEN", helper_source)
        self.assertNotIn('OCI_CONTEXT_NAME = "desktop-linux"', helper_source)
        self.assertIn('"dockerConfigPath"', helper_source)
        self.assertIn('"dockerTrustStageSnapshots"', helper_source)
        self.assertIn("run_docker_stage", helper_source)
        self.assertIn('docker_config = output / "docker-config"', helper_source)
        self.assertNotIn(
            'docker_config = cache_root / f"docker-config-{suffix}"',
            helper_source,
        )

        base_reference = f"docker.io/library/haskell@{BASE_DIGEST}"
        base_id = f"sha256:{'5' * 64}"
        base_inspection = {
            "Id": base_id,
            "RepoDigests": [base_reference],
            "Os": "linux",
            "Architecture": "amd64",
        }
        self.assertEqual(
            helper.validate_oci_base_image_inspection(
                base_inspection,
                expected_reference=base_reference,
            ),
            base_id,
        )
        for altered in (
            {**base_inspection, "RepoDigests": []},
            {**base_inspection, "Os": "windows"},
            {**base_inspection, "Architecture": "arm64"},
        ):
            with self.subTest(altered=altered):
                with self.assertRaises(helper.WorkflowError):
                    helper.validate_oci_base_image_inspection(
                        altered,
                        expected_reference=base_reference,
                    )

        self.assertEqual(
            helper.validate_oci_iid_bytes(
                base_id.encode("ascii"),
            ),
            base_id,
        )
        for invalid in (
            f"{base_id}\n".encode("ascii"),
            f"{base_id}\r\n".encode("ascii"),
            b"sha256:not-a-digest",
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises(helper.WorkflowError):
                    helper.validate_oci_iid_bytes(invalid)

    def test_receipt_records_the_immutable_runtime_subject_and_tag_checks(
        self,
    ) -> None:
        source = HELPER_PATH.read_text(encoding="utf-8")

        self.assertIn('"runtimeImageSubject"', source)
        self.assertIn('"referenceType": "immutable-image-id"', source)
        self.assertIn('"imageTagBindingChecks"', source)
        self.assertIn("validate_oci_image_inspection", source)

    def test_oci_logged_json_binds_parse_and_hash_to_same_fd_bytes(self) -> None:
        helper = load_helper()
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary).resolve() / "docker-inspect.json"
            payload = b'{"Architecture":"amd64","Os":"linux"}\n'
            path.write_bytes(payload)
            record = {
                "stdoutPath": str(path),
                "stdoutSha256": hashlib.sha256(payload).hexdigest(),
            }

            document, digest = helper._same_fd_logged_json_snapshot(
                path,
                label="OCI_IMAGE_INSPECTION",
                command_record=record,
            )
            self.assertEqual(
                document,
                {"Architecture": "amd64", "Os": "linux"},
            )
            self.assertEqual(digest, record["stdoutSha256"])

            path.write_bytes(
                b'{"Architecture":"arm64","Os":"linux"}\n'
            )
            with self.assertRaises(helper.WorkflowError):
                helper._same_fd_logged_json_snapshot(
                    path,
                    label="OCI_IMAGE_INSPECTION",
                    command_record=record,
                )

    def test_oci_context_provenance_uses_staged_build_inputs(self) -> None:
        helper = load_helper()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            containerfile = root / "Containerfile"
            binary = root / "candidate"
            fixtures = root / "fixture-source"
            context = root / "context"
            containerfile.write_bytes(b"FROM pinned@example\n")
            binary.write_bytes(b"candidate-binary")
            binary.chmod(0o755)
            (fixtures / "small").mkdir(parents=True)
            (fixtures / "small/input.json").write_text(
                '{"request":"frozen"}\n',
                encoding="utf-8",
            )

            snapshot = helper._copy_oci_context(
                context=context,
                containerfile=containerfile,
                binary=binary,
                fixture_root=fixtures,
            )
            self.assertEqual(
                snapshot,
                {
                    "binarySha256": helper.sha256_file(
                        context / "s1-4x-haskell"
                    ),
                    "containerfileSha256": helper.sha256_file(
                        context / "Containerfile"
                    ),
                    "fixtureTreeSha256": helper._regular_tree_sha256(
                        context / "fixtures",
                        label="OCI_FIXTURE_CONTEXT_TEST",
                    ),
                },
            )
            helper._validate_oci_context_snapshot(
                context,
                expected=snapshot,
            )

            (context / "Containerfile").write_bytes(
                b"FROM substituted@example\n"
            )
            with self.assertRaises(helper.WorkflowError):
                helper._validate_oci_context_snapshot(
                    context,
                    expected=snapshot,
                )

    def test_oci_flow_uses_same_fd_logs_and_checks_staged_context_twice(
        self,
    ) -> None:
        source = HELPER_PATH.read_text(encoding="utf-8")

        self.assertNotIn(
            'strict_json_load(output / "oci-base-before.stdout")',
            source,
        )
        self.assertNotIn(
            'strict_json_load(output / "oci-base-after.stdout")',
            source,
        )
        self.assertGreaterEqual(
            source.count("_same_fd_logged_json_snapshot("),
            6,
        )
        self.assertGreaterEqual(
            source.count("_validate_oci_context_snapshot("),
            3,
        )

    def test_wrapper_requires_only_pinned_docker_client_runtime_identity(
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
        self.assertNotIn(
            "S1_4X_DOCKER_DAEMON_IDENTITY_SHA256",
            source,
        )
        helper_source = HELPER_PATH.read_text(encoding="utf-8")
        self.assertIn("DOCKER_SHA256_MISMATCH", helper_source)
        self.assertNotIn("expectedDaemonIdentitySha256", helper_source)
        self.assertIn('"daemonIdentitySha256"', helper_source)
        for trust_token in (
            "docker context show",
            '"{{json .}}"',
            "validate_oci_daemon_identity",
            "validate_oci_base_image_inspection",
            "validate_oci_iid_bytes",
            '"daemonIdentityBefore"',
            '"daemonIdentityAfter"',
            '"baseImageId"',
            '"iidFileSha256"',
        ):
            with self.subTest(trust_token=trust_token):
                self.assertIn(trust_token, helper_source)
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
