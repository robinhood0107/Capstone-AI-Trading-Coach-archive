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
from unittest import mock


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
            ghcup_bin=Path("/proc/self/fd/70"),
            stack_bin=Path("/proc/self/fd/71"),
            tool_path=(
                "/cache/stack-root-benchmark-abc/tool-bin:"
                "/toolchain/ghc-9.10.3/bin:/usr/bin:/bin"
            ),
            stack_yaml=Path("/repo/haskell/stack.yaml"),
            stack_root=Path("/cache/stack-root-benchmark-abc"),
            work_dir=Path(
                ".stack-work/s1-4x/stack-root-benchmark-abc"
            ),
            profile_options=["-O2", "-fasm"],
            time_limit_seconds=5,
            native_report=Path("/out/raw/criterion-family.json"),
            criterion_prefix="path-transform/",
        )
        self.assertEqual(
            command,
            [
                "/proc/self/fd/70",
                "--offline",
                "run",
                "--quick",
                "--ghc",
                "9.10.3",
                "--stack",
                "3.11.1",
                "--",
                "/usr/bin/env",
                (
                    "PATH=/cache/stack-root-benchmark-abc/tool-bin:"
                    "/toolchain/ghc-9.10.3/bin:/usr/bin:/bin"
                ),
                "/proc/self/fd/71",
                "--stack-root",
                "/cache/stack-root-benchmark-abc",
                "--work-dir",
                ".stack-work/s1-4x/stack-root-benchmark-abc",
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
        self.assertNotIn("--offline", command[12:])

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

    def test_profile_evidence_is_parsed_and_hashed_from_the_supplied_fd(
        self,
    ) -> None:
        helper = load_helper()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            payload = b'{"candidateSourceCommit":"' + b"a" * 40 + b'"}\n'
            pinned_object = root / "opened-evidence.json"
            pinned_object.write_bytes(payload)
            descriptor = os.open(pinned_object, os.O_RDONLY)
            try:
                prefix = "S1_4X_HASKELL_BASELINE_CORRECTNESS"
                environment = {
                    prefix: f"/proc/self/fd/{descriptor}",
                    f"{prefix}_SHA256": hashlib.sha256(payload).hexdigest(),
                    f"{prefix}_SOURCE_PATH": (
                        "/source/path/that/is-never-reopened/correctness.json"
                    ),
                }
                with mock.patch.dict(os.environ, environment, clear=False):
                    snapshot = helper.pinned_json_environment_evidence(
                        prefix,
                        label="BASELINE_CORRECTNESS",
                        max_bytes=1024,
                    )
                self.assertEqual(snapshot.payload, payload)
                self.assertEqual(
                    snapshot.document,
                    {"candidateSourceCommit": "a" * 40},
                )
                self.assertEqual(
                    snapshot.source_path,
                    Path(environment[f"{prefix}_SOURCE_PATH"]),
                )
                self.assertEqual(snapshot.fd_path, Path(environment[prefix]))

                environment[f"{prefix}_SHA256"] = "0" * 64
                with mock.patch.dict(os.environ, environment, clear=False):
                    with self.assertRaisesRegex(
                        helper.BlockError,
                        "SHA256_MISMATCH",
                    ):
                        helper.pinned_json_environment_evidence(
                            prefix,
                            label="BASELINE_CORRECTNESS",
                            max_bytes=1024,
                        )
            finally:
                os.close(descriptor)

    def test_pinned_tool_fd_inheritance_reaches_nested_stack_and_ghc(
        self,
    ) -> None:
        helper = load_helper()

        def write_executable(path: Path, source: str) -> None:
            path.write_text(source, encoding="utf-8")
            path.chmod(0o755)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            marker = root / "ghc-reached.txt"
            ghcup_source = root / "ghcup"
            stack_source = root / "stack"
            ghc_source = root / "ghc"
            shared_prelude = (
                "#!/usr/bin/python3\n"
                "import os\n"
                "import subprocess\n"
                "import sys\n"
                "fds = tuple(int(value) for value in "
                "os.environ['TEST_PASS_FDS'].split(','))\n"
            )
            write_executable(
                ghcup_source,
                shared_prelude
                + "separator = sys.argv.index('--')\n"
                + "completed = subprocess.run("
                + "sys.argv[separator + 1:], check=False, pass_fds=fds)\n"
                + "raise SystemExit(completed.returncode)\n",
            )
            write_executable(
                stack_source,
                shared_prelude
                + "import shutil\n"
                + "from pathlib import Path\n"
                + "ghc = shutil.which('ghc')\n"
                + "if ghc != os.environ['TEST_GHC_SHIM']:\n"
                + "    raise SystemExit(91)\n"
                + "for name in ('ghc-pkg', 'runghc', 'haddock'):\n"
                + "    auxiliary = Path(ghc).with_name(name)\n"
                + "    if not auxiliary.exists():\n"
                + "        raise SystemExit(92)\n"
                + "    probe = subprocess.run("
                + "[str(auxiliary), '--nested-probe'], check=False, "
                + "pass_fds=fds)\n"
                + "    if probe.returncode != 0:\n"
                + "        raise SystemExit(93)\n"
                + "completed = subprocess.run("
                + "[ghc, '--nested-probe'], check=False, pass_fds=fds)\n"
                + "raise SystemExit(completed.returncode)\n",
            )
            write_executable(
                ghc_source,
                "#!/usr/bin/python3\n"
                "import os\n"
                "from pathlib import Path\n"
                "Path(os.environ['TEST_MARKER']).write_text("
                "os.readlink(os.environ['TEST_GHC_SHIM']), encoding='utf-8')\n",
            )
            for auxiliary_name in ("ghc-pkg", "runghc", "haddock"):
                write_executable(
                    root / auxiliary_name,
                    "#!/usr/bin/python3\n"
                    "raise SystemExit(0)\n",
                )
            descriptors = [
                os.open(path, os.O_RDONLY)
                for path in (ghcup_source, stack_source, ghc_source)
            ]
            try:
                pinned = []
                for label, path, descriptor in zip(
                    ("GHCUP", "STACK", "AUTHORITATIVE_GHC"),
                    (ghcup_source, stack_source, ghc_source),
                    descriptors,
                    strict=True,
                ):
                    prefix = f"S1_4X_{label}"
                    with mock.patch.dict(
                        os.environ,
                        {
                            f"{prefix}_BIN": str(path),
                            f"{prefix}_SHA256": hashlib.sha256(
                                path.read_bytes()
                            ).hexdigest(),
                            f"{prefix}_PINNED_FD_PATH": (
                                f"/proc/self/fd/{descriptor}"
                            ),
                        },
                        clear=False,
                    ):
                        pinned.append(
                            helper.pinned_executable_environment(
                                prefix,
                                label=label,
                            )
                        )
                ghcup, stack, ghc = pinned
                stack_root = root / "stack-root-benchmark-probe"
                stack_root.mkdir(mode=0o700)
                tool_path, ghc_shim = helper.prepare_authoritative_ghc_shim(
                    stack_root=stack_root,
                    authoritative_ghc=ghc,
                )
                self.assertEqual(
                    sorted(path.name for path in ghc_shim.parent.iterdir()),
                    ["ghc", "ghc-pkg", "haddock", "runghc"],
                )
                self.assertEqual(os.readlink(ghc_shim), str(ghc.fd_path))
                for auxiliary_name in ("ghc-pkg", "runghc", "haddock"):
                    auxiliary = ghc_shim.parent / auxiliary_name
                    self.assertTrue(auxiliary.is_symlink())
                    self.assertEqual(
                        auxiliary.resolve(strict=True),
                        root / auxiliary_name,
                    )
                self.assertEqual(tool_path.split(":", 1)[0], str(ghc_shim.parent))
                command = helper.build_stack_benchmark_command(
                    ghcup_bin=ghcup.fd_path,
                    stack_bin=stack.fd_path,
                    tool_path=tool_path,
                    stack_yaml=root / "stack.yaml",
                    stack_root=stack_root,
                    work_dir=Path(
                        f".stack-work/s1-4x/{stack_root.name}"
                    ),
                    profile_options=["-O0", "-fasm"],
                    time_limit_seconds=5,
                    native_report=root / "raw.json",
                    criterion_prefix="probe/",
                )
                environment = dict(os.environ)
                environment.update(
                    {
                        "TEST_PASS_FDS": ",".join(
                            str(descriptor) for descriptor in descriptors
                        ),
                        "TEST_GHC_SHIM": str(ghc_shim),
                        "TEST_MARKER": str(marker),
                    }
                )
                completed = helper.run_pinned_subprocess(
                    command,
                    cwd=root,
                    environment=environment,
                    pinned_executables=pinned,
                    capture_output=True,
                )
                self.assertEqual(
                    completed.returncode,
                    0,
                    completed.stderr.decode("utf-8", errors="replace"),
                )
                self.assertEqual(marker.read_text(encoding="utf-8"), str(ghc.fd_path))
            finally:
                for descriptor in descriptors:
                    os.close(descriptor)

    def test_final_profile_selects_same_fd_correctness_and_qualification(
        self,
    ) -> None:
        helper = load_helper()
        commit = "a" * 40
        source_tree = "b" * 64
        compiler = "c" * 64
        plan = "d" * 64
        baseline = {
            "schemaVersion": "s1.4x-haskell-full-correctness-v1",
            "status": "PASS",
            "profileId": "baseline-o0-fasm",
            "candidateSourceCommit": commit,
            "sourceTreeSha256": source_tree,
            "compilerSha256": compiler,
            "mismatchCount": 0,
        }
        optimized = {
            **baseline,
            "profileId": "optimized-o2-fasm",
        }
        qualification = {
            "schemaVersion": "s1.4x-haskell-profile-qualification-v1",
            "status": "PASS",
            "candidateSourceCommit": commit,
            "sourceTreeSha256": source_tree,
            "planSha256": plan,
            "selection": {
                "profileId": "baseline-o0-fasm",
                "selectedBy": "proven-fallback",
            },
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            descriptors: list[int] = []
            environment: dict[str, str] = {}
            for prefix, name, document in (
                (
                    "S1_4X_HASKELL_BASELINE_CORRECTNESS",
                    "baseline",
                    baseline,
                ),
                (
                    "S1_4X_HASKELL_OPTIMIZED_CORRECTNESS",
                    "optimized",
                    optimized,
                ),
                (
                    "S1_4X_HASKELL_QUALIFICATION_ARTIFACT",
                    "qualification",
                    qualification,
                ),
            ):
                payload = helper._canonical_json_bytes(document)
                path = root / f"{name}.json"
                path.write_bytes(payload)
                descriptor = os.open(path, os.O_RDONLY)
                descriptors.append(descriptor)
                environment.update(
                    {
                        prefix: f"/proc/self/fd/{descriptor}",
                        f"{prefix}_SHA256": hashlib.sha256(payload).hexdigest(),
                        f"{prefix}_SOURCE_PATH": f"/evidence/{name}.json",
                    }
                )
            try:
                with mock.patch.dict(os.environ, environment, clear=False):
                    evidence = helper.load_pinned_profile_evidence()
                profile = {
                    "schemaVersion": "s1.4x-haskell-selected-profile-v1",
                    "profileId": "baseline-o0-fasm",
                    "selectedBy": "proven-fallback",
                    "sourceTreeSha256": source_tree,
                    "compilerSha256": compiler,
                    "qualificationPlanSha256": plan,
                    "qualificationArtifactSha256": evidence[
                        "qualification"
                    ].sha256,
                    "fullCorrectnessSha256": evidence["baseline"].sha256,
                }
                closure = helper.validate_profile_evidence_closure(
                    profile=profile,
                    evidence=evidence,
                    benchmark_subject_commit=commit,
                )
                self.assertEqual(
                    closure["baselineCorrectnessSha256"],
                    evidence["baseline"].sha256,
                )
                altered = dict(profile)
                altered["fullCorrectnessSha256"] = evidence["optimized"].sha256
                with self.assertRaisesRegex(
                    helper.BlockError,
                    "SELECTED_CORRECTNESS",
                ):
                    helper.validate_profile_evidence_closure(
                        profile=altered,
                        evidence=evidence,
                        benchmark_subject_commit=commit,
                    )
            finally:
                for descriptor in descriptors:
                    os.close(descriptor)

    def test_ghc_shim_directory_must_be_fresh_and_output_bound(self) -> None:
        helper = load_helper()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            executable = root / "ghc"
            executable.write_bytes(b"fake ghc")
            executable.chmod(0o755)
            descriptor = os.open(executable, os.O_RDONLY)
            try:
                opened = os.fstat(descriptor)
                pinned = helper.PinnedExecutable(
                    label="AUTHORITATIVE_GHC",
                    source_path=Path("/toolchain/ghc-9.10.3/bin/ghc"),
                    fd_path=Path(f"/proc/self/fd/{descriptor}"),
                    descriptor=descriptor,
                    sha256=hashlib.sha256(executable.read_bytes()).hexdigest(),
                    mode=executable.stat().st_mode,
                    identity=(
                        opened.st_dev,
                        opened.st_ino,
                        opened.st_size,
                        opened.st_mtime_ns,
                        opened.st_ctime_ns,
                        opened.st_nlink,
                    ),
                )
                stack_root = root / "stack-root"
                stack_root.mkdir()
                (stack_root / "tool-bin").mkdir()
                with self.assertRaisesRegex(
                    helper.BlockError,
                    "TOOL_SHIM_ALREADY_EXISTS",
                ):
                    helper.prepare_authoritative_ghc_shim(
                        stack_root=stack_root,
                        authoritative_ghc=pinned,
                    )
            finally:
                os.close(descriptor)

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
            "authoritativeGhcPinnedFdPath",
        ):
            with self.subTest(field=field):
                self.assertIn(f'"{field}"', source)
        self.assertIn('"S1_4X_BENCHMARK_RUNTIME_IDENTITY"', source)
        self.assertIn('"S1_4X_AUTHORITATIVE_GHC"', source)


if __name__ == "__main__":
    unittest.main()
