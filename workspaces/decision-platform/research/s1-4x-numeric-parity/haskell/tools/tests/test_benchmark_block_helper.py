"""Haskell Criterion raw report와 frozen inner command receipt 계약 테스트."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


TOOLS_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = TOOLS_ROOT / "haskell_benchmark_block.py"


def load_helper():
    """구현 전 RED도 collection error 대신 명시적 assertion으로 남긴다."""

    if not MODULE_PATH.is_file():
        raise AssertionError("Haskell benchmark block helper is missing")
    specification = importlib.util.spec_from_file_location(
        "haskell_benchmark_block",
        MODULE_PATH,
    )
    if specification is None or specification.loader is None:
        raise AssertionError("unable to load Haskell benchmark block helper")
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


class BenchmarkBlockHelperTests(unittest.TestCase):
    """Haskell lane이 raw 실행만 소유하고 shared evidence 투영을 재구현하지 않게 한다."""

    def test_inner_command_is_exact_network_free_frozen_argv(self) -> None:
        helper = load_helper()
        command = helper.build_stack_benchmark_command(
            ghcup_bin=Path("/tools/ghcup"),
            stack_bin=Path("/tools/stack"),
            stack_yaml=Path("/repo/haskell/stack.yaml"),
            stack_root=Path("/cache/stack-root-benchmark-abc"),
            work_dir=Path("/cache/stack-root-benchmark-abc/work"),
            profile_options=["-O2", "-fasm"],
            time_limit_seconds=5,
            native_report=Path("/out/raw/criterion-family.json"),
            criterion_prefix="path-transform/",
        )
        self.assertEqual(
            command,
            [
                "/tools/ghcup",
                "--offline",
                "run",
                "--quick",
                "--ghc",
                "9.10.3",
                "--stack",
                "3.11.1",
                "--",
                "/tools/stack",
                "--stack-root",
                "/cache/stack-root-benchmark-abc",
                "--work-dir",
                "/cache/stack-root-benchmark-abc/work",
                "--stack-yaml",
                "/repo/haskell/stack.yaml",
                "--no-terminal",
                "--color",
                "never",
                "--system-ghc",
                "--no-install-ghc",
                "--hpack-force",
                "bench",
                "--ghc-options=-O2 -fasm",
                (
                    "--benchmark-arguments=--time-limit 5 "
                    "--json /out/raw/criterion-family.json "
                    "--match prefix path-transform/ +RTS -N1 -RTS"
                ),
            ],
        )
        self.assertNotIn("--offline", command[10:])

    def test_shared_pipeline_owns_ledger_native_projection_and_block_result(
        self,
    ) -> None:
        source = MODULE_PATH.read_text(encoding="utf-8")
        for shared_tool in (
            "benchmark_input_ledger.py",
            "native_benchmark_block.py",
        ):
            with self.subTest(shared_tool=shared_tool):
                self.assertIn(shared_tool, source)
        for output in (
            "raw/criterion-family.json",
            "receipts/criterion-family.json",
            "input-ledger.json",
            "native-contract-validation.json",
            "native-statistics.json",
            "native.json",
            "block-result.json",
        ):
            with self.subTest(output=output):
                self.assertIn(output, source)

    def test_haskell_helper_does_not_duplicate_shared_statistics_or_report_math(
        self,
    ) -> None:
        source = MODULE_PATH.read_text(encoding="utf-8")
        for forbidden in (
            "def _build_block_result",
            "def parse_criterion_reports",
            "statistics.median",
            "reportMeasured",
            "reportAnalysis",
            "normalizedNsPerLogicalOperation",
            "nativeP95",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)

    def test_runtime_identity_is_an_exact_self_reported_executable_object(
        self,
    ) -> None:
        helper = load_helper()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            executable = root / "criterion-benchmark"
            executable.write_bytes(b"verified benchmark executable")
            executable.chmod(0o755)
            digest = hashlib.sha256(executable.read_bytes()).hexdigest()
            identity = root / "benchmark-runtime-identity.json"
            document = {
                "schemaVersion": "s1.4x-haskell-benchmark-runtime-identity-v1",
                "boundaryId": "haskell",
                "selectorId": "haskell/path-transform",
                "executedBenchmarkPath": str(executable),
                "executedBenchmarkSha256": digest,
                "status": "PASS",
            }
            identity.write_text(
                json.dumps(document, separators=(",", ":"), sort_keys=True),
                encoding="utf-8",
            )

            (
                executed_path,
                executed_sha256,
                identity_sha256,
            ) = helper.validate_runtime_identity(
                identity,
                selector_id="haskell/path-transform",
            )
            self.assertEqual(executed_path, executable)
            self.assertEqual(executed_sha256, digest)
            self.assertEqual(
                identity_sha256,
                hashlib.sha256(identity.read_bytes()).hexdigest(),
            )

            for altered in (
                {**document, "unknown": True},
                {**document, "selectorId": "haskell/advanced-risk"},
                {**document, "executedBenchmarkSha256": "0" * 64},
            ):
                with self.subTest(altered=altered):
                    identity.write_text(
                        json.dumps(altered, separators=(",", ":"), sort_keys=True),
                        encoding="utf-8",
                    )
                    with self.assertRaises(helper.BlockError):
                        helper.validate_runtime_identity(
                            identity,
                            selector_id="haskell/path-transform",
                        )

    def test_same_fd_snapshot_rejects_symlink_parent_and_hardlink(self) -> None:
        helper = load_helper()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            real = root / "real"
            real.mkdir()
            evidence = real / "evidence.json"
            evidence.write_text('{"status":"PASS"}\n', encoding="utf-8")

            snapshot = helper.read_json_snapshot(
                evidence,
                label="TEST_EVIDENCE",
                max_bytes=1024,
            )
            self.assertEqual(snapshot.document, {"status": "PASS"})
            self.assertEqual(
                snapshot.sha256,
                hashlib.sha256(snapshot.payload).hexdigest(),
            )

            linked = real / "linked.json"
            os.link(evidence, linked)
            with self.assertRaisesRegex(helper.BlockError, "HARDLINK"):
                helper.read_json_snapshot(
                    evidence,
                    label="TEST_EVIDENCE",
                    max_bytes=1024,
                )
            linked.unlink()

            alias = root / "alias"
            alias.symlink_to(real, target_is_directory=True)
            with self.assertRaisesRegex(helper.BlockError, "PATH_COMPONENT"):
                helper.read_json_snapshot(
                    alias / "evidence.json",
                    label="TEST_EVIDENCE",
                    max_bytes=1024,
                )

    def test_benchmark_subject_requires_clean_exact_head(self) -> None:
        helper = load_helper()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            tracked = root / "tracked.txt"
            tracked.write_text("clean\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(root), "add", "tracked.txt"], check=True)
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(root),
                    "-c",
                    "user.name=S1.4X Test",
                    "-c",
                    "user.email=s1.4x@example.invalid",
                    "commit",
                    "-qm",
                    "fixture",
                ],
                check=True,
            )
            commit = subprocess.run(
                ["git", "-C", str(root), "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            helper._verify_subject_commit(root, commit)

            tracked.write_text("dirty\n", encoding="utf-8")
            with self.assertRaisesRegex(helper.BlockError, "WORKTREE_NOT_CLEAN"):
                helper._verify_subject_commit(root, commit)
            tracked.write_text("clean\n", encoding="utf-8")
            (root / "untracked.txt").write_text("untracked\n", encoding="utf-8")
            with self.assertRaisesRegex(helper.BlockError, "WORKTREE_NOT_CLEAN"):
                helper._verify_subject_commit(root, commit)

    def test_receipt_binds_runtime_executable_and_authoritative_ghc_identity(
        self,
    ) -> None:
        source = MODULE_PATH.read_text(encoding="utf-8")
        for field in (
            "runtimeIdentityPath",
            "runtimeIdentitySha256",
            "executedBenchmarkPath",
            "executedBenchmarkSha256",
            "authoritativeGhcPath",
            "authoritativeGhcSha256",
        ):
            with self.subTest(field=field):
                self.assertIn(f'"{field}"', source)
        self.assertIn('"S1_4X_BENCHMARK_RUNTIME_IDENTITY"', source)
        self.assertIn('"S1_4X_AUTHORITATIVE_GHC_SHA256"', source)


if __name__ == "__main__":
    unittest.main()
