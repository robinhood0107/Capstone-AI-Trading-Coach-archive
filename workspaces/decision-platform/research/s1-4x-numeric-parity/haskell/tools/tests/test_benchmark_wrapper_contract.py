"""Haskell Criterion outer wrapper의 sealed-memfd 실행 계약을 고정한다."""

from __future__ import annotations

import hashlib
import os
import stat
import subprocess
import tempfile
import unittest
import venv
from pathlib import Path


HASKELL_ROOT = Path(__file__).resolve().parents[2]
WRAPPER = HASKELL_ROOT / "tools" / "run-benchmark-block.sh"
HELPER = HASKELL_ROOT / "tools" / "haskell_benchmark_block.py"
BENCHMARK_MAIN = HASKELL_ROOT / "benchmark" / "Main.hs"
EXPECTED_OPTIONS = (
    "--plan",
    "--block-dir",
    "--qualification",
    "--boundary",
    "--selector",
    "--family",
    "--rotation",
    "--outer-repetition",
    "--run-id",
    "--benchmark-subject-commit",
)


def _sha256_file(path: Path) -> str:
    """실행 회귀 fixture가 전달할 exact file bytes SHA-256을 계산한다."""

    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_executable(path: Path) -> None:
    """실행되면 실패하는 regular executable fixture를 만든다."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/usr/bin/bash\nexit 97\n", encoding="utf-8")
    path.chmod(0o755)


def _copied_benchmark_python(root: Path) -> Path:
    """외부 venv layout과 동결 dependency metadata를 가진 CPython copy를 만든다."""

    venv_root = root / "benchmark-python-venv"
    venv.EnvBuilder(with_pip=False, symlinks=False).create(venv_root)
    site_packages = venv_root / "lib/python3.12/site-packages"
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
        metadata_root = site_packages / f"{package}-{version}.dist-info"
        metadata_root.mkdir()
        (metadata_root / "METADATA").write_text(
            "Metadata-Version: 2.1\n"
            f"Name: {package}\n"
            f"Version: {version}\n",
            encoding="utf-8",
        )
    python = venv_root / "bin/python"
    if not python.is_file() or python.is_symlink():
        raise AssertionError("benchmark Python fixture must be a regular copy")
    return python


class BenchmarkWrapperContractTests(unittest.TestCase):
    """Outer wrapper가 frozen argv와 self-identity 경계를 바꾸지 못하게 한다."""

    def test_criterion_environment_consumer_uses_a_lazy_pattern(self) -> None:
        source = BENCHMARK_MAIN.read_text(encoding="utf-8")

        self.assertRegex(
            source,
            r"\(\s*\\\s*~preparedCases\s*->\s*"
            r'bgroup "" \(zipWith benchmark selectedCases preparedCases\)',
        )
        self.assertNotIn(
            '(bgroup "" . zipWith benchmark selectedCases)',
            source,
        )

    def test_outer_wrapper_is_executable_and_has_one_helper(self) -> None:
        self.assertTrue(WRAPPER.is_file(), "outer benchmark wrapper is missing")
        self.assertTrue(HELPER.is_file(), "outer benchmark helper is missing")
        mode = WRAPPER.stat().st_mode
        self.assertTrue(mode & stat.S_IXUSR, "outer benchmark wrapper is not executable")

    def test_env_i_executes_shared_runtime_before_benchmark_validation(
        self,
    ) -> None:
        """빈 환경 child에서도 retained helper가 실제 Python 실행까지 도달한다."""

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            benchmark_python = _copied_benchmark_python(root)
            install_prefix = root / "install-prefix"
            tool_paths = {
                "S1_4X_GHCUP": root / "tools/ghcup",
                "S1_4X_STACK": (
                    install_prefix / ".ghcup/stack/3.11.1/stack"
                ),
                "S1_4X_AUTHORITATIVE_GHC": (
                    install_prefix / ".ghcup/ghc/9.10.3/bin/ghc-9.10.3"
                ),
                "S1_4X_LATEST_GHC": root / "tools/ghc-9.14.1",
                "S1_4X_HLINT": root / "tools/hlint",
                "S1_4X_STYLISH": root / "tools/stylish-haskell",
            }
            for path in tool_paths.values():
                _write_executable(path)

            cache_root = root / "cache"
            large_fixture_root = root / "fixtures"
            cache_root.mkdir()
            (large_fixture_root / "large").mkdir(parents=True)
            environment = {
                "GHCUP_INSTALL_BASE_PREFIX": str(install_prefix),
                "S1_4X_BENCHMARK_PYTHON_BIN": str(benchmark_python),
                "S1_4X_BENCHMARK_PYTHON_SHA256": _sha256_file(
                    benchmark_python
                ),
                "S1_4X_CACHE_ROOT": str(cache_root),
                "S1_4X_LARGE_FIXTURE_ROOT": str(large_fixture_root),
            }
            descriptors: list[int] = []
            try:
                for prefix, path in tool_paths.items():
                    descriptor = os.open(path, os.O_RDONLY)
                    descriptors.append(descriptor)
                    environment.update(
                        {
                            f"{prefix}_BIN": str(path),
                            f"{prefix}_SHA256": _sha256_file(path),
                            f"{prefix}_PINNED_FD_PATH": (
                                f"/proc/self/fd/{descriptor}"
                            ),
                        }
                    )
                for prefix, name in (
                    ("S1_4X_HASKELL_BASELINE_CORRECTNESS", "baseline"),
                    ("S1_4X_HASKELL_OPTIMIZED_CORRECTNESS", "optimized"),
                    ("S1_4X_HASKELL_QUALIFICATION_ARTIFACT", "qualification"),
                ):
                    path = root / f"{name}.json"
                    path.write_text("{}\n", encoding="utf-8")
                    descriptor = os.open(path, os.O_RDONLY)
                    descriptors.append(descriptor)
                    environment.update(
                        {
                            prefix: f"/proc/self/fd/{descriptor}",
                            f"{prefix}_SHA256": _sha256_file(path),
                            f"{prefix}_SOURCE_PATH": str(path),
                        }
                    )

                run_id = "env-i-runtime-probe"
                rotation = "rotation-1"
                family = "probe-family"
                block_dir = (
                    root / run_id / rotation / "haskell" / family
                )
                block_dir.mkdir(parents=True)
                integration_root = HASKELL_ROOT.parent / "integration"
                created_integration_root = not integration_root.exists()
                if created_integration_root:
                    integration_root.mkdir()
                try:
                    completed = subprocess.run(
                        [
                            str(WRAPPER),
                            "--plan",
                            str(root / "missing-plan.json"),
                            "--block-dir",
                            str(block_dir),
                            "--qualification",
                            str(root / "missing-qualification.json"),
                            "--boundary",
                            "haskell",
                            "--selector",
                            f"haskell/{family}",
                            "--family",
                            family,
                            "--rotation",
                            rotation,
                            "--outer-repetition",
                            "1",
                            "--run-id",
                            run_id,
                            "--benchmark-subject-commit",
                            "a" * 40,
                        ],
                        cwd=HASKELL_ROOT.parents[4],
                        env=environment,
                        check=False,
                        capture_output=True,
                        text=True,
                        pass_fds=tuple(descriptors),
                    )
                finally:
                    if created_integration_root:
                        integration_root.rmdir()
            finally:
                for descriptor in descriptors:
                    os.close(descriptor)

        self.assertEqual(completed.returncode, 2, completed.stderr)
        self.assertIn(
            "HASKELL_BENCHMARK_BLOCK_FAIL:"
            "PLAN_NOT_CANONICAL_REGULAR_FILE",
            completed.stderr,
        )
        self.assertNotIn(
            "s1_4x_run_benchmark_python",
            completed.stderr,
        )

    def test_haskell_marker_command_preserves_venv_source_argv0(self) -> None:
        """실제 Main.hs marker route가 FD bytes와 venv argv0를 함께 보존한다."""

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            benchmark_python = _copied_benchmark_python(root)
            marker = root / "marker.py"
            marker.write_text(
                "import importlib.metadata\n"
                "import os\n"
                "import sys\n"
                "import jsonschema\n"
                "import numpy\n"
                "python_fd = os.environ['EXPECTED_PYTHON_FD']\n"
                "descriptor = int(python_fd.rsplit('/', 1)[1])\n"
                "pinned = os.fstat(descriptor)\n"
                "current = os.stat('/proc/self/exe')\n"
                "if (\n"
                "    sys.executable != os.environ['EXPECTED_PYTHON_SOURCE']\n"
                "    or sys.prefix != os.environ['EXPECTED_VENV_PREFIX']\n"
                "    or __file__ != os.environ['EXPECTED_MARKER_FD']\n"
                "    or (pinned.st_dev, pinned.st_ino, pinned.st_size)\n"
                "       != (current.st_dev, current.st_ino, current.st_size)\n"
                "    or importlib.metadata.version('jsonschema') != '4.26.0'\n"
                "    or importlib.metadata.version('numpy') != '2.5.1'\n"
                "    or numpy.__version__ != '2.5.1'\n"
                "):\n"
                "    raise SystemExit(91)\n"
                "print('{\"status\": \"MEASUREMENT_ENTERED\"}')\n",
                encoding="utf-8",
            )
            python_descriptor = os.open(benchmark_python, os.O_RDONLY)
            marker_descriptor = os.open(marker, os.O_RDONLY)
            try:
                python_fd = f"/proc/self/fd/{python_descriptor}"
                marker_fd = f"/proc/self/fd/{marker_descriptor}"
                benchmark_source = BENCHMARK_MAIN.read_text(encoding="utf-8")
                uses_source_argv0_route = all(
                    token in benchmark_source
                    for token in (
                        '"S1_4X_BENCHMARK_MARKER_PYTHON_SOURCE_PATH"',
                        'readProcessWithExitCode\n      "/usr/bin/env"',
                        '"-a"',
                        "pythonSourcePath",
                    )
                )
                if uses_source_argv0_route:
                    command = [
                        "/usr/bin/env",
                        "-a",
                        str(benchmark_python),
                        python_fd,
                        marker_fd,
                        "mark-measurement-entered",
                        "--qualification",
                        str(root / "qualification.json"),
                    ]
                else:
                    command = [
                        python_fd,
                        marker_fd,
                        "mark-measurement-entered",
                        "--qualification",
                        str(root / "qualification.json"),
                    ]
                completed = subprocess.run(
                    command,
                    env={
                        "LC_ALL": "C",
                        "PATH": "/usr/bin:/bin",
                        "EXPECTED_PYTHON_FD": python_fd,
                        "EXPECTED_PYTHON_SOURCE": str(benchmark_python),
                        "EXPECTED_VENV_PREFIX": str(benchmark_python.parent.parent),
                        "EXPECTED_MARKER_FD": marker_fd,
                    },
                    check=False,
                    capture_output=True,
                    text=True,
                    pass_fds=(python_descriptor, marker_descriptor),
                )
            finally:
                os.close(marker_descriptor)
                os.close(python_descriptor)

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(
            completed.stdout,
            '{"status": "MEASUREMENT_ENTERED"}\n',
        )
        self.assertEqual(completed.stderr, "")

    def test_outer_wrapper_uses_exact_twenty_argument_order(self) -> None:
        source = WRAPPER.read_text(encoding="utf-8")
        self.assertEqual(source.splitlines()[0], "#!/usr/bin/bash")
        self.assertIn('[[ "$#" -eq 20 ]]', source)
        self.assertEqual(source.count('LC_ALL="C.UTF-8"'), 2)
        self.assertNotIn('LC_ALL="C"', source)
        offsets = [source.index(f'"{option}"') for option in EXPECTED_OPTIONS]
        self.assertEqual(offsets, sorted(offsets))
        self.assertEqual(
            source.count('/usr/bin/git -C "$PWD" rev-parse --show-toplevel'),
            1,
        )

    def test_sealed_outer_wrapper_never_discovers_its_own_path(self) -> None:
        source = WRAPPER.read_text(encoding="utf-8")
        for forbidden in (
            "BASH_SOURCE",
            'readlink -f "$0"',
            'realpath "$0"',
            "/proc/$$/",
        ):
            self.assertNotIn(forbidden, source)
        self.assertNotIn("dirname", source)
        self.assertNotIn("source-inputs.v1.json --write", source)

    def test_wrapper_has_no_ambient_stack_escape_hatch(self) -> None:
        source = WRAPPER.read_text(encoding="utf-8")
        for variable in ("STACK_YAML", "STACK_ROOT", "STACK_OPTS", "STACK_CONFIG"):
            self.assertIn(variable, source)
        self.assertIn("haskell_benchmark_block.py", source)
        self.assertNotIn("eval ", source)
        self.assertNotIn("bash -c", source)
        self.assertNotIn("sh -c", source)
        self.assertNotIn("mark-measurement-entered", source)

    def test_outer_wrapper_scrubs_interpreter_and_git_injection(self) -> None:
        source = WRAPPER.read_text(encoding="utf-8")
        self.assertIn('export PATH="/usr/bin:/bin"', source)
        self.assertIn("compgen -e", source)
        for forbidden_environment in (
            "BASH_ENV",
            "ENV",
            "PYTHONPATH",
            "PYTHONHOME",
            "VIRTUAL_ENV",
            "JAVA_TOOL_OPTIONS",
            "JDK_JAVA_OPTIONS",
            "_JAVA_OPTIONS",
            "GIT_*",
        ):
            self.assertIn(forbidden_environment, source)
        self.assertIn("verify_pinned_object", source)

    def test_outer_wrapper_requires_shared_v3_path_sha_and_pinned_fd_triples(
        self,
    ) -> None:
        source = WRAPPER.read_text(encoding="utf-8")
        for prefix in (
            "S1_4X_BENCHMARK_PYTHON",
            "S1_4X_GHCUP",
            "S1_4X_STACK",
            "S1_4X_AUTHORITATIVE_GHC",
            "S1_4X_LATEST_GHC",
            "S1_4X_HLINT",
            "S1_4X_STYLISH",
        ):
            with self.subTest(prefix=prefix):
                for suffix in ("_BIN", "_SHA256", "_PINNED_FD_PATH"):
                    self.assertIn(f"${{{prefix}{suffix}:?", source)
                    self.assertIn(f'{prefix}{suffix}="', source)
        self.assertNotIn("export S1_4X_GHCUP_SHA256=", source)
        self.assertNotIn("export S1_4X_STACK_SHA256=", source)
        for forbidden_discovery in ("command -v", "which ", "type -P"):
            with self.subTest(forbidden_discovery=forbidden_discovery):
                self.assertNotIn(forbidden_discovery, source)

    def test_outer_wrapper_forwards_profile_evidence_fd_sha_source_triples(
        self,
    ) -> None:
        source = WRAPPER.read_text(encoding="utf-8")
        evidence = {
            "S1_4X_HASKELL_BASELINE_CORRECTNESS": "BASELINE_CORRECTNESS",
            "S1_4X_HASKELL_OPTIMIZED_CORRECTNESS": "OPTIMIZED_CORRECTNESS",
            "S1_4X_HASKELL_QUALIFICATION_ARTIFACT": "QUALIFICATION_ARTIFACT",
        }
        for prefix, local_name in evidence.items():
            with self.subTest(prefix=prefix):
                self.assertIn(f"${{{prefix}:?", source)
                self.assertIn(f"${{{prefix}_SHA256:?", source)
                self.assertIn(f"${{{prefix}_SOURCE_PATH:?", source)
                self.assertIn(f'{prefix}="${local_name}"', source)
                self.assertIn(f'{prefix}_SHA256="', source)
                self.assertIn(f'{prefix}_SOURCE_PATH="', source)

    def test_original_tool_paths_are_layout_only_and_never_reopened(self) -> None:
        source = WRAPPER.read_text(encoding="utf-8")
        self.assertIn("verify_source_path_layout", source)
        self.assertIn("verify_pinned_object", source)
        self.assertNotIn("verify_executable", source)
        self.assertNotIn("verify_benchmark_python", source)
        self.assertNotIn('/usr/bin/realpath -e -- "$executable"', source)
        self.assertNotIn('/usr/bin/sha256sum "$executable"', source)
        self.assertNotIn('/usr/bin/sha256sum "$BENCHMARK_PYTHON"', source)

    def test_marker_python_and_script_execute_only_from_pinned_fd_paths(
        self,
    ) -> None:
        helper = HELPER.read_text(encoding="utf-8")
        benchmark = BENCHMARK_MAIN.read_text(encoding="utf-8")

        self.assertIn(
            'pinned_executable_environment(\n'
            '        "S1_4X_BENCHMARK_PYTHON"',
            helper,
        )
        self.assertNotIn(
            '_verified_environment_executable(\n'
            '        "S1_4X_BENCHMARK_PYTHON_BIN"',
            helper,
        )
        self.assertIn('"markerPythonPinnedFdPath"', helper)
        self.assertIn('"markerScriptPinnedFdPath"', helper)
        self.assertIn("requiredPinnedFdPath", benchmark)
        self.assertIn(
            'verifiedMarkerInput\n'
            '      "S1_4X_BENCHMARK_MARKER_PYTHON"',
            benchmark,
        )

    def test_wrapper_mode_is_regular_not_symlink(self) -> None:
        self.assertFalse(WRAPPER.is_symlink())
        self.assertTrue(os.path.isfile(WRAPPER))

    def test_criterion_env_owns_the_single_measurement_transition(self) -> None:
        source = BENCHMARK_MAIN.read_text(encoding="utf-8")
        required_in_order = (
            "inputs <- loadFrozenInputs fixtureRoot",
            "traverse (setupPreparedCase inputs)",
            "markMeasurementEntered qualificationPath",
            "pure preparedCases",
        )
        offsets = [source.index(fragment) for fragment in required_in_order]
        self.assertEqual(offsets, sorted(offsets))
        self.assertEqual(
            source.count("markMeasurementEntered qualificationPath"),
            1,
        )
        self.assertIn('"S1_4X_BENCHMARK_QUALIFICATION"', source)
        self.assertIn("INVALID_PRE_RUN_QUALIFICATION_STATE", source)

    def test_timing_uses_only_shared_materialized_large_fixture_root(self) -> None:
        wrapper = WRAPPER.read_text(encoding="utf-8")
        helper = HELPER.read_text(encoding="utf-8")
        benchmark = BENCHMARK_MAIN.read_text(encoding="utf-8")
        for source in (wrapper, helper, benchmark):
            self.assertIn("S1_4X_LARGE_FIXTURE_ROOT", source)
            self.assertNotIn("S1_4X_BENCHMARK_FIXTURE_ROOT", source)
        self.assertIn(
            'requiredConfiguredDirectory "S1_4X_LARGE_FIXTURE_ROOT"',
            benchmark,
        )
        self.assertNotIn('"../contract/fixtures"', benchmark)
        self.assertIn(
            'S1_4X_LARGE_FIXTURE_ROOT="$LARGE_FIXTURE_ROOT"',
            wrapper,
        )
        self.assertIn(
            '"S1_4X_LARGE_FIXTURE_ROOT": str(large_fixture_root),',
            helper,
        )

    def test_benchmark_self_reports_the_exact_executed_runtime_identity(self) -> None:
        source = BENCHMARK_MAIN.read_text(encoding="utf-8")
        for token in (
            "getExecutablePath",
            "exclusiveAtomicWrite",
            '"S1_4X_BENCHMARK_RUNTIME_IDENTITY"',
            '"s1.4x-haskell-benchmark-runtime-identity-v1"',
            '"boundaryId" .= ("haskell"',
            '"selectorId" .= selectorIdText',
            '"executedBenchmarkPath" .= executablePath',
            '"executedBenchmarkSha256" .= executableSha256',
            '"status" .= ("PASS"',
        ):
            with self.subTest(token=token):
                self.assertIn(token, source)
        self.assertLess(
            source.index("publishRuntimeIdentity"),
            source.index("  defaultMain"),
        )

    def test_outer_wrapper_exports_verified_authoritative_ghc_sha(self) -> None:
        source = WRAPPER.read_text(encoding="utf-8")
        self.assertIn(
            'S1_4X_AUTHORITATIVE_GHC_SHA256="$AUTHORITATIVE_GHC_SHA256"',
            source,
        )
        self.assertIn(
            'S1_4X_AUTHORITATIVE_GHC_PINNED_FD_PATH='
            '"$AUTHORITATIVE_GHC_PINNED_FD_PATH"',
            source,
        )

    def test_wrapper_ignores_hostile_home_and_forwards_only_explicit_cache(
        self,
    ) -> None:
        source = WRAPPER.read_text(encoding="utf-8")
        self.assertIn(
            'CACHE_ROOT="${S1_4X_CACHE_ROOT:?S1_4X_CACHE_ROOT is required}"',
            source,
        )
        self.assertIn('HOME="/nonexistent"', source)
        self.assertIn('S1_4X_CACHE_ROOT="$CACHE_ROOT"', source)
        self.assertNotIn('RUNTIME_HOME="${HOME:', source)

    def test_empty_environment_preserves_only_validated_ghcup_install_prefix(
        self,
    ) -> None:
        source = WRAPPER.read_text(encoding="utf-8")

        self.assertIn(
            'GHCUP_INSTALL_BASE_PREFIX="${GHCUP_INSTALL_BASE_PREFIX:?',
            source,
        )
        self.assertIn("verify_ghcup_install_base_prefix", source)
        self.assertIn(
            'GHCUP_INSTALL_BASE_PREFIX="$GHCUP_INSTALL_BASE_PREFIX"',
            source,
        )
        self.assertLess(
            source.index("verify_ghcup_install_base_prefix"),
            source.index("exec /usr/bin/env -i"),
        )
        self.assertIn('HOME="/nonexistent"', source)

    def test_wrapper_rejects_script_alias_as_benchmark_python(self) -> None:
        def write_executable(path: Path, source: str) -> None:
            path.write_text(source, encoding="utf-8")
            path.chmod(0o755)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            marker = root / "ghc-reached.txt"
            fake_python = root / "benchmark-python"
            ghcup = root / "ghcup"
            stack = root / "stack"
            ghc = root / "ghc"
            passthrough = root / "other-tool"
            shared_prelude = (
                "#!/usr/bin/python3\n"
                "import os\n"
                "import subprocess\n"
                "import sys\n"
                "fds = tuple(int(value) for value in "
                "os.environ['TEST_PASS_FDS'].split(','))\n"
            )
            write_executable(
                ghcup,
                shared_prelude
                + "separator = sys.argv.index('--')\n"
                + "completed = subprocess.run("
                + "sys.argv[separator + 1:], check=False, pass_fds=fds)\n"
                + "raise SystemExit(completed.returncode)\n",
            )
            write_executable(
                stack,
                shared_prelude
                + "import shutil\n"
                + "from pathlib import Path\n"
                + "target = shutil.which('ghc')\n"
                + "if target != os.environ['TEST_GHC_SHIM']:\n"
                + "    raise SystemExit(91)\n"
                + "for name in ('ghc-pkg', 'runghc', 'haddock'):\n"
                + "    auxiliary = Path(target).with_name(name)\n"
                + "    if not auxiliary.exists():\n"
                + "        raise SystemExit(92)\n"
                + "    probe = subprocess.run("
                + "[str(auxiliary), '--probe'], check=False, pass_fds=fds)\n"
                + "    if probe.returncode != 0:\n"
                + "        raise SystemExit(93)\n"
                + "completed = subprocess.run("
                + "[target, '--probe'], check=False, pass_fds=fds)\n"
                + "raise SystemExit(completed.returncode)\n",
            )
            write_executable(
                ghc,
                "#!/usr/bin/python3\n"
                "import os\n"
                "from pathlib import Path\n"
                "Path(os.environ['TEST_MARKER']).write_text("
                "os.readlink(os.environ['TEST_GHC_SHIM']), encoding='utf-8')\n",
            )
            for auxiliary_name in ("ghc-pkg", "runghc", "haddock"):
                write_executable(
                    root / auxiliary_name,
                    "#!/usr/bin/python3\nraise SystemExit(0)\n",
                )
            write_executable(
                passthrough,
                "#!/usr/bin/python3\nraise SystemExit(0)\n",
            )
            write_executable(
                fake_python,
                "#!/usr/bin/python3\n"
                "import importlib.util\n"
                "import os\n"
                "import sys\n"
                "from pathlib import Path\n"
                "if sys.argv[0] != "
                "os.environ['S1_4X_BENCHMARK_PYTHON_PINNED_FD_PATH']:\n"
                "    raise SystemExit(80)\n"
                "helper_path = Path(sys.argv[1])\n"
                "spec = importlib.util.spec_from_file_location("
                "'outer_fd_probe_helper', helper_path)\n"
                "if spec is None or spec.loader is None:\n"
                "    raise SystemExit(81)\n"
                "module = importlib.util.module_from_spec(spec)\n"
                "sys.modules[spec.name] = module\n"
                "spec.loader.exec_module(module)\n"
                "pinned = tuple(module.pinned_executable_environment("
                "prefix, label=label) for prefix, label in ("
                "('S1_4X_GHCUP', 'GHCUP'),"
                "('S1_4X_STACK', 'STACK'),"
                "('S1_4X_AUTHORITATIVE_GHC', 'AUTHORITATIVE_GHC')))\n"
                "ghcup, stack, ghc = pinned\n"
                "stack_root = Path(os.environ['S1_4X_CACHE_ROOT']) / "
                "'outer-helper-probe'\n"
                "stack_root.mkdir(mode=0o700)\n"
                "tool_path, shim = module.prepare_authoritative_ghc_shim("
                "stack_root=stack_root, authoritative_ghc=ghc)\n"
                "marker = Path(sys.argv[sys.argv.index('--plan') + 1])\n"
                "environment = dict(os.environ)\n"
                "environment.update({"
                "'TEST_PASS_FDS': ','.join(str(item.descriptor) "
                "for item in pinned),"
                "'TEST_GHC_SHIM': str(shim),"
                "'TEST_MARKER': str(marker)})\n"
                "command = module.build_stack_benchmark_command("
                "ghcup_bin=ghcup.fd_path, stack_bin=stack.fd_path, "
                "tool_path=tool_path, stack_yaml=stack_root / 'stack.yaml', "
                "stack_root=stack_root, "
                "work_dir=Path(f'.stack-work-s1-4x-{stack_root.name}'), "
                "profile_options=['-O0', '-fasm'], time_limit_seconds=5, "
                "native_report=stack_root / 'raw.json', "
                "criterion_prefix='probe/')\n"
                "completed = module.run_pinned_subprocess("
                "command, cwd=stack_root, environment=environment, "
                "pinned_executables=pinned, capture_output=True)\n"
                "raise SystemExit(completed.returncode)\n",
            )
            tool_paths = (ghcup, stack, ghc, passthrough, passthrough, passthrough)
            descriptors = [os.open(path, os.O_RDONLY) for path in tool_paths]
            benchmark_python_descriptor = os.open(
                fake_python,
                os.O_RDONLY,
            )
            evidence_paths = []
            evidence_descriptors = []
            for name in ("baseline", "optimized", "qualification"):
                path = root / f"{name}.json"
                path.write_text('{"status":"PASS"}\n', encoding="utf-8")
                evidence_paths.append(path)
                evidence_descriptors.append(os.open(path, os.O_RDONLY))
            (root / "large").mkdir()
            try:
                environment = {
                    "PATH": "/usr/bin:/bin",
                    "S1_4X_BENCHMARK_PYTHON_BIN": str(fake_python),
                    "S1_4X_BENCHMARK_PYTHON_SHA256": hashlib.sha256(
                        fake_python.read_bytes()
                    ).hexdigest(),
                    "S1_4X_BENCHMARK_PYTHON_PINNED_FD_PATH": (
                        f"/proc/self/fd/{benchmark_python_descriptor}"
                    ),
                    "S1_4X_CACHE_ROOT": str(root),
                    "S1_4X_LARGE_FIXTURE_ROOT": str(root),
                }
                for prefix, path, descriptor in zip(
                    (
                        "S1_4X_GHCUP",
                        "S1_4X_STACK",
                        "S1_4X_AUTHORITATIVE_GHC",
                        "S1_4X_LATEST_GHC",
                        "S1_4X_HLINT",
                        "S1_4X_STYLISH",
                    ),
                    tool_paths,
                    descriptors,
                    strict=True,
                ):
                    environment.update(
                        {
                            f"{prefix}_BIN": str(path),
                            f"{prefix}_SHA256": hashlib.sha256(
                                path.read_bytes()
                            ).hexdigest(),
                            f"{prefix}_PINNED_FD_PATH": (
                                f"/proc/self/fd/{descriptor}"
                            ),
                        }
                    )
                for prefix, path, descriptor in zip(
                    (
                        "S1_4X_HASKELL_BASELINE_CORRECTNESS",
                        "S1_4X_HASKELL_OPTIMIZED_CORRECTNESS",
                        "S1_4X_HASKELL_QUALIFICATION_ARTIFACT",
                    ),
                    evidence_paths,
                    evidence_descriptors,
                    strict=True,
                ):
                    environment.update(
                        {
                            prefix: f"/proc/self/fd/{descriptor}",
                            f"{prefix}_SHA256": hashlib.sha256(
                                path.read_bytes()
                            ).hexdigest(),
                            f"{prefix}_SOURCE_PATH": str(path),
                        }
                    )
                completed = subprocess.run(
                    [
                        str(WRAPPER),
                        "--plan",
                        str(marker),
                        "--block-dir",
                        str(root / "block"),
                        "--qualification",
                        str(root / "timeout-qualification.json"),
                        "--boundary",
                        "haskell",
                        "--selector",
                        "haskell/probe",
                        "--family",
                        "probe",
                        "--rotation",
                        "R1",
                        "--outer-repetition",
                        "1",
                        "--run-id",
                        "fd-probe",
                        "--benchmark-subject-commit",
                        "a" * 40,
                    ],
                    cwd=HASKELL_ROOT.parents[4],
                    env=environment,
                    check=False,
                    capture_output=True,
                    pass_fds=tuple(
                        [
                            benchmark_python_descriptor,
                            *descriptors,
                            *evidence_descriptors,
                        ]
                    ),
                )
                self.assertEqual(completed.returncode, 69)
                self.assertIn(
                    "benchmark Python pinned FD execution failed",
                    completed.stderr.decode("utf-8", errors="replace"),
                )
                self.assertFalse(marker.exists())
            finally:
                os.close(benchmark_python_descriptor)
                for descriptor in descriptors + evidence_descriptors:
                    os.close(descriptor)


if __name__ == "__main__":
    unittest.main()
