"""Haskell acceptance tooling이 host username과 implicit tool path를 누출하지 않는지 검사한다."""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import tempfile
import unittest
import venv
from pathlib import Path


TOOLS_ROOT = Path(__file__).resolve().parents[1]
HASKELL_ROOT = TOOLS_ROOT.parent
REQUIRED_TOOL_VARIABLES = (
    "S1_4X_GHCUP_BIN",
    "S1_4X_AUTHORITATIVE_GHC_BIN",
    "S1_4X_LATEST_GHC_BIN",
    "S1_4X_STACK_BIN",
    "S1_4X_HLINT_BIN",
    "S1_4X_STYLISH_BIN",
)
LEGACY_TOOL_VARIABLES = (
    "S1_4X_GHC_BIN",
    "S1_4X_GHC_914_BIN",
    "S1_4X_STYLISH_HASKELL_BIN",
)
TEXT_SUFFIXES = {
    ".cabal",
    ".hs",
    ".json",
    ".md",
    ".py",
    ".sh",
    ".yaml",
    ".yml",
}
FORBIDDEN_HOST_TOKENS = (
    "/" + "home" + "/",
    "/mnt/c/" + "Users/",
    "pjj" + "pj",
)


def _is_generated_stack_path(path: Path) -> bool:
    """기존과 output-bound 단일-component Stack 산출물 경계를 함께 제외한다."""

    return any(
        part == ".stack-work"
        or re.fullmatch(
            r"\.stack-work-s1-4x-stack-root(?:-[a-z0-9]+)*",
            part,
        )
        is not None
        for part in path.parts
    )


class PortableToolPathTests(unittest.TestCase):
    def test_generated_stack_work_paths_cover_flat_output_bound_layout(
        self,
    ) -> None:
        for path in (
            Path("/repo/haskell/.stack-work/dist/generated.hs"),
            Path(
                "/repo/haskell/"
                ".stack-work-s1-4x-stack-root-candidate-deadbeef/"
                "dist/generated.hs"
            ),
        ):
            with self.subTest(path=path):
                self.assertTrue(_is_generated_stack_path(path))

        self.assertFalse(
            _is_generated_stack_path(
                Path("/repo/haskell/.stack-work-s1-4x-forged/source.hs")
            )
        )

    def test_haskell_subtree_has_no_frozen_username_or_host_path(self) -> None:
        violations: list[str] = []
        for path in sorted(HASKELL_ROOT.rglob("*"), key=lambda item: str(item).encode()):
            if (
                not path.is_file()
                or path.is_symlink()
                or _is_generated_stack_path(path)
                or "__pycache__" in path.parts
                or path.suffix not in TEXT_SUFFIXES
            ):
                continue
            text = path.read_text(encoding="utf-8")
            for token in FORBIDDEN_HOST_TOKENS:
                if token in text:
                    violations.append(f"{path.relative_to(HASKELL_ROOT)}:{token}")
        self.assertEqual(violations, [])

    def test_toolchain_assertion_requires_every_exact_readiness_path(self) -> None:
        script = (TOOLS_ROOT / "assert-toolchain.sh").read_text(encoding="utf-8")
        for variable in REQUIRED_TOOL_VARIABLES:
            with self.subTest(variable=variable):
                self.assertIn(f'${{{variable}:?', script)
                self.assertNotIn(f'${{{variable}:-', script)
        for variable in LEGACY_TOOL_VARIABLES:
            with self.subTest(legacy_variable=variable):
                self.assertNotIn(variable, script)

    def test_format_and_lint_gates_reuse_canonical_readiness_variables(self) -> None:
        formatter = (TOOLS_ROOT / "check-format.sh").read_text(encoding="utf-8")
        lint = (TOOLS_ROOT / "check-hlint.sh").read_text(encoding="utf-8")

        self.assertIn('${S1_4X_STYLISH_BIN:?', formatter)
        self.assertNotIn("S1_4X_STYLISH_HASKELL_BIN", formatter)
        self.assertIn('${S1_4X_HLINT_BIN:?', lint)

    def test_legacy_aliases_do_not_satisfy_the_readiness_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            venv_root = Path(temporary) / "oracle-venv"
            venv.EnvBuilder(with_pip=False, symlinks=False).create(venv_root)
            python = venv_root / "bin/python"
            site_packages = (
                venv_root / "lib/python3.12/site-packages"
            )
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
            descriptor = os.open(python, os.O_RDONLY)
            try:
                result = subprocess.run(
                    ["bash", str(TOOLS_ROOT / "assert-toolchain.sh")],
                    check=False,
                    capture_output=True,
                    text=True,
                    env={
                        "PATH": "/usr/bin:/bin",
                        "S1_4X_BENCHMARK_PYTHON_BIN": str(python),
                        "S1_4X_BENCHMARK_PYTHON_SHA256": hashlib.sha256(
                            python.read_bytes()
                        ).hexdigest(),
                        "S1_4X_BENCHMARK_PYTHON_PINNED_FD_PATH": (
                            f"/proc/self/fd/{descriptor}"
                        ),
                        "S1_4X_GHCUP_BIN": "/legacy-must-not-run/ghcup",
                        "S1_4X_GHC_BIN": "/legacy-must-not-run/ghc",
                        "S1_4X_GHC_914_BIN": "/legacy-must-not-run/ghc-9.14.1",
                        "S1_4X_STACK_BIN": "/legacy-must-not-run/stack",
                        "S1_4X_HLINT_BIN": "/legacy-must-not-run/hlint",
                        "S1_4X_STYLISH_HASKELL_BIN": (
                            "/legacy-must-not-run/stylish-haskell"
                        ),
                    },
                    pass_fds=(descriptor,),
                )
            finally:
                os.close(descriptor)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("S1_4X_AUTHORITATIVE_GHC_BIN", result.stderr)

    def test_format_gate_scopes_python_module_discovery_to_haskell_root(self) -> None:
        formatter = (TOOLS_ROOT / "check-format.sh").read_text(encoding="utf-8")
        self.assertIn(
            'cd "$HASKELL_ROOT"\n'
            "  s1_4x_run_benchmark_python -m unittest -v",
            formatter,
        )
        self.assertNotIn("PYTHONPATH=", formatter)

    def test_arbitrary_cwd_runner_covers_all_three_acceptance_wrappers(self) -> None:
        runner = (
            TOOLS_ROOT / "tests/assert-wrappers-arbitrary-cwd.sh"
        ).read_text(encoding="utf-8")
        for wrapper in (
            "assert-toolchain.sh",
            "check-format.sh",
            "check-hlint.sh",
        ):
            with self.subTest(wrapper=wrapper):
                self.assertIn(f'"$HASKELL_ROOT/tools/{wrapper}"', runner)
        self.assertIn('cd "$temporary_cwd"', runner)
        self.assertIn('${S1_4X_EVIDENCE_ROOT:?', runner)

    def test_provisional_test_inherits_explicit_paths_without_defaults(self) -> None:
        script = (TOOLS_ROOT / "tests/assert-provisional-toolchain.sh").read_text(
            encoding="utf-8"
        )
        for variable in REQUIRED_TOOL_VARIABLES:
            with self.subTest(variable=variable):
                self.assertIn(f'${{{variable}:?', script)
                self.assertNotIn(f'${{{variable}:-', script)

    def test_toolchain_requires_live_replay_without_a_tracked_result(
        self,
    ) -> None:
        script = (TOOLS_ROOT / "assert-toolchain.sh").read_text(encoding="utf-8")
        for token in (
            "CURRENT_REPLAY_REQUIRED",
            "ghc-compatibility-solve-failure.v1.json",
        ):
            with self.subTest(token=token):
                self.assertIn(token, script)
        self.assertNotIn("ghc-compatibility-result.v1.json", script)
        self.assertNotIn("failureResultPath", script)
        self.assertNotIn("ALLOW_PENDING_COMPATIBILITY", script)


if __name__ == "__main__":
    unittest.main()
