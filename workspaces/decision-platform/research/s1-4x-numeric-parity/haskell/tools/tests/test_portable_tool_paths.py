"""Haskell acceptance tooling이 host username과 implicit tool path를 누출하지 않는지 검사한다."""

from __future__ import annotations

import unittest
from pathlib import Path


TOOLS_ROOT = Path(__file__).resolve().parents[1]
HASKELL_ROOT = TOOLS_ROOT.parent
REQUIRED_TOOL_VARIABLES = (
    "S1_4X_GHCUP_BIN",
    "S1_4X_GHC_BIN",
    "S1_4X_GHC_914_BIN",
    "S1_4X_STACK_BIN",
    "S1_4X_HLINT_BIN",
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
    "/home/",
    "/mnt/c/Users/",
    "pjj" + "pj",
)


class PortableToolPathTests(unittest.TestCase):
    def test_haskell_subtree_has_no_frozen_username_or_host_path(self) -> None:
        violations: list[str] = []
        for path in sorted(HASKELL_ROOT.rglob("*"), key=lambda item: str(item).encode()):
            if (
                not path.is_file()
                or path.is_symlink()
                or ".stack-work" in path.parts
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

    def test_format_and_lint_gates_reuse_canonical_readiness_variables(self) -> None:
        formatter = (TOOLS_ROOT / "check-format.sh").read_text(encoding="utf-8")
        lint = (TOOLS_ROOT / "check-hlint.sh").read_text(encoding="utf-8")

        self.assertIn('${S1_4X_STYLISH_HASKELL_BIN:?', formatter)
        self.assertNotIn("S1_4X_STYLISH_BIN", formatter)
        self.assertIn('${S1_4X_HLINT_BIN:?', lint)

    def test_provisional_test_inherits_explicit_paths_without_defaults(self) -> None:
        script = (TOOLS_ROOT / "tests/assert-provisional-toolchain.sh").read_text(
            encoding="utf-8"
        )
        for variable in REQUIRED_TOOL_VARIABLES:
            with self.subTest(variable=variable):
                self.assertIn(f'${{{variable}:?', script)
                self.assertNotIn(f'${{{variable}:-', script)


if __name__ == "__main__":
    unittest.main()
