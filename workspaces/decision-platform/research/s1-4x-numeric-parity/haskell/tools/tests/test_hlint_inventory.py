"""HLint central ignore와 restricted-module allowance의 exact inventory 회귀 테스트."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


TOOLS_ROOT = Path(__file__).resolve().parents[1]
HASKELL_ROOT = TOOLS_ROOT.parent
MODULE_PATH = TOOLS_ROOT / "hlint_inventory.py"
SPEC = importlib.util.spec_from_file_location("hlint_inventory", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("unable to load hlint_inventory.py")
hlint_inventory = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = hlint_inventory
SPEC.loader.exec_module(hlint_inventory)


class HLintInventoryTests(unittest.TestCase):
    def test_exact_module_allowances_bind_every_import(self) -> None:
        configuration = (HASKELL_ROOT / ".hlint.yaml").read_text(encoding="utf-8")
        manifest = json.loads(
            (HASKELL_ROOT / "lint-exceptions.v1.json").read_text(encoding="utf-8")
        )

        summary = hlint_inventory.validate_module_allowances(
            HASKELL_ROOT,
            configuration,
            manifest["entries"],
        )

        self.assertEqual(summary.allowance_count, 6)
        self.assertEqual(summary.imported_symbol_count, 12)
        self.assertIn(
            "System.Environment(getExecutablePath,lookupEnv)",
            {entry["symbol"] for entry in manifest["entries"]},
        )

    def test_throw_io_is_a_global_restriction_not_a_whitelist(self) -> None:
        configuration = (HASKELL_ROOT / ".hlint.yaml").read_text(encoding="utf-8")
        hlint_inventory.validate_throw_io_restrictions(configuration)

        whitelist = """\
- functions:
    - name:
        - Control.Exception.throwIO
        - throwIO
      within:
        - Example.Allowed
"""
        with self.assertRaisesRegex(
            hlint_inventory.InventoryError,
            "throwIO restriction",
        ):
            hlint_inventory.validate_throw_io_restrictions(whitelist)

    def test_inline_yaml_ignore_is_in_the_managed_inventory(self) -> None:
        configuration = """\
- ignore: {name: Use isDigit, within: [Example.Module]}
"""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "src/Example/Module.hs"
            focused = root / "test/ExampleSpec.hs"
            source.parent.mkdir(parents=True)
            focused.parent.mkdir(parents=True)
            source.write_text(
                "module Example.Module (digit) where\n"
                "digit value = value >= '0' && value <= '9'\n",
                encoding="utf-8",
            )
            focused.write_text(
                'testCase "ASCII digit contract" testBody\n',
                encoding="utf-8",
            )
            entries = [
                {
                    "language": "haskell",
                    "file": "src/Example/Module.hs",
                    "rule": "Use isDigit",
                    "symbol": "digit",
                    "reason": "ASCII only",
                    "focusedTest": "test/ExampleSpec.hs: ASCII digit contract",
                    "owner": "S1.4X",
                    "expiresWhen": "An exact ASCII predicate is frozen.",
                }
            ]
            diagnostics = [
                {
                    "file": str(source),
                    "severity": "Ignore",
                    "hint": "Use isDigit",
                    "module": ["Example.Module"],
                    "decl": ["digit"],
                    "from": "value >= '0' && value <= '9'",
                }
            ]

            summary = hlint_inventory.validate_managed_ignored_diagnostics(
                root,
                configuration,
                entries,
                diagnostics,
            )

            self.assertEqual(summary.configured_pair_count, 1)
            self.assertEqual(summary.managed_diagnostic_count, 1)

    def test_source_local_hlint_suppression_is_rejected_in_every_source_root(
        self,
    ) -> None:
        for source_root in ("src", "app", "test", "benchmark"):
            with self.subTest(source_root=source_root):
                with tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    source = root / source_root / "Example.hs"
                    source.parent.mkdir(parents=True)
                    source.write_text(
                        '{-# ANN module ("HLint: ignore Use head" :: String) #-}\n'
                        "module Example (value) where\n"
                        "value = 1\n",
                        encoding="utf-8",
                    )
                    with self.assertRaisesRegex(
                        hlint_inventory.InventoryError,
                        "source-local HLint suppression",
                    ):
                        hlint_inventory.validate_no_source_local_suppressions(root)

    def test_unknown_ignored_diagnostic_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "src/Example.hs"
            source.parent.mkdir(parents=True)
            source.write_text(
                "module Example (value) where\nvalue = 1\n",
                encoding="utf-8",
            )
            diagnostics = [
                {
                    "file": str(source),
                    "severity": "Ignore",
                    "hint": "Unexpected hidden rule",
                    "module": ["Example"],
                    "decl": ["value"],
                    "from": "value = 1",
                }
            ]

            with self.assertRaisesRegex(
                hlint_inventory.InventoryError,
                "unknown ignored HLint diagnostic",
            ):
                hlint_inventory.validate_managed_ignored_diagnostics(
                    root,
                    "",
                    [],
                    diagnostics,
                )

    def test_qualified_throw_io_fixture_is_bound_to_both_global_names(self) -> None:
        configuration = (HASKELL_ROOT / ".hlint.yaml").read_text(encoding="utf-8")
        fixture = (
            HASKELL_ROOT / "tools/fixtures/hlint/qualified-throw-io.hs"
        ).read_text(encoding="utf-8")

        self.assertIn("Exception.throwIO", fixture)
        hlint_inventory.validate_throw_io_restrictions(configuration)

    def test_base_is_digit_replaces_the_stale_managed_exception(self) -> None:
        configuration = (HASKELL_ROOT / ".hlint.yaml").read_text(encoding="utf-8")
        manifest = (HASKELL_ROOT / "lint-exceptions.v1.json").read_text(
            encoding="utf-8"
        )
        process = (
            HASKELL_ROOT / "src/contract/S14X/Contract/Process.hs"
        ).read_text(encoding="utf-8")

        self.assertNotIn("Use isDigit", configuration)
        self.assertNotIn('"rule": "Use isDigit"', manifest)
        self.assertNotIn("isDigitAscii", process)
        self.assertIn("Data.Char (isDigit)", process)
        self.assertIn("isDigit character", process)
        self.assertEqual(len(hlint_inventory._ignore_pairs(configuration)), 4)

    def test_extra_managed_ignored_diagnostic_is_rejected(self) -> None:
        configuration = """\
- ignore:
    name: Use isDigit
    within:
      - Example.Module
"""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "src/Example/Module.hs"
            focused = root / "test/ExampleSpec.hs"
            source.parent.mkdir(parents=True)
            focused.parent.mkdir(parents=True)
            source.write_text(
                "module Example.Module (digit) where\n"
                "digit value = value >= '0' && value <= '9'\n",
                encoding="utf-8",
            )
            focused.write_text(
                'testCase "ASCII digit contract" testBody\n',
                encoding="utf-8",
            )
            entries = [
                {
                    "language": "haskell",
                    "file": "src/Example/Module.hs",
                    "rule": "Use isDigit",
                    "symbol": "digit",
                    "reason": "ASCII only",
                    "focusedTest": "test/ExampleSpec.hs: ASCII digit contract",
                    "owner": "S1.4X",
                    "expiresWhen": "An exact ASCII predicate is frozen.",
                }
            ]
            diagnostics = [
                {
                    "file": str(source),
                    "severity": "Ignore",
                    "hint": "Use isDigit",
                    "module": ["Example.Module"],
                    "decl": ["digit"],
                    "from": "value >= '0' && value <= '9'",
                }
            ]
            summary = hlint_inventory.validate_managed_ignored_diagnostics(
                root,
                configuration,
                entries,
                diagnostics,
            )
            self.assertEqual(summary.managed_diagnostic_count, 1)

            diagnostics.append(
                {
                    "file": str(source),
                    "severity": "Ignore",
                    "hint": "Use isDigit",
                    "module": ["Example.Module"],
                    "decl": ["newHiddenDigit"],
                    "from": "other >= '0' && other <= '9'",
                }
            )
            with self.assertRaisesRegex(
                hlint_inventory.InventoryError,
                "unbound managed ignored diagnostics",
            ):
                hlint_inventory.validate_managed_ignored_diagnostics(
                    root,
                    configuration,
                    entries,
                    diagnostics,
                )


if __name__ == "__main__":
    unittest.main()
