"""HLint central ignore와 restricted-module allowance의 exact inventory 회귀 테스트."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path


TOOLS_ROOT = Path(__file__).resolve().parents[1]
HASKELL_ROOT = TOOLS_ROOT.parent
MODULE_PATH = TOOLS_ROOT / "hlint_inventory.py"
WRAPPER_PATH = TOOLS_ROOT / "check-hlint.sh"
SCHEMA_PATH = (
    HASKELL_ROOT.parent
    / "contract/schemas/suppression-exception.schema.json"
)
MANIFEST_PATH = HASKELL_ROOT / "lint-exceptions.v1.json"
SPEC = importlib.util.spec_from_file_location("hlint_inventory", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("unable to load hlint_inventory.py")
hlint_inventory = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = hlint_inventory
SPEC.loader.exec_module(hlint_inventory)


class HLintInventoryTests(unittest.TestCase):
    def test_stdlib_suppression_contract_accepts_the_frozen_schema_and_manifest(
        self,
    ) -> None:
        schema = hlint_inventory.strict_json_load(SCHEMA_PATH)
        manifest = hlint_inventory.strict_json_load(MANIFEST_PATH)

        entries = hlint_inventory.validate_suppression_contract(
            HASKELL_ROOT,
            schema,
            manifest,
        )

        self.assertEqual(len(entries), 11)
        self.assertTrue(all(entry["language"] == "haskell" for entry in entries))

    def test_suppression_schema_drift_is_fail_closed(self) -> None:
        schema = hlint_inventory.strict_json_load(SCHEMA_PATH)
        manifest = hlint_inventory.strict_json_load(MANIFEST_PATH)
        mutations = []

        extra_outer = deepcopy(schema)
        extra_outer["unknown"] = True
        mutations.append(("extra-outer", extra_outer))

        wrong_enum = deepcopy(schema)
        wrong_enum["$defs"]["entry"]["properties"]["language"]["enum"] = [
            "haskell"
        ]
        mutations.append(("language-enum", wrong_enum))

        wrong_length = deepcopy(schema)
        wrong_length["$defs"]["entry"]["properties"]["reason"]["maxLength"] = 2048
        mutations.append(("reason-max-length", wrong_length))

        wrong_pattern = deepcopy(schema)
        wrong_pattern["$defs"]["entry"]["properties"]["file"]["pattern"] = ".*"
        mutations.append(("file-pattern", wrong_pattern))

        missing_composite = deepcopy(schema)
        del missing_composite["properties"]["entries"][
            "x-s1-4x-unique-by-composite"
        ]
        mutations.append(("unique-composite", missing_composite))

        stale_allowed = deepcopy(schema)
        stale_allowed["properties"]["entries"][
            "x-s1-4x-stale-or-unused-entry-is-error"
        ] = False
        mutations.append(("stale-unused-rule", stale_allowed))

        for label, altered in mutations:
            with self.subTest(label=label):
                with self.assertRaisesRegex(
                    hlint_inventory.InventoryError,
                    "suppression schema drift",
                ):
                    hlint_inventory.validate_suppression_contract(
                        HASKELL_ROOT,
                        altered,
                        manifest,
                    )

    def test_suppression_manifest_rejects_shape_length_path_and_duplicate_drift(
        self,
    ) -> None:
        schema = hlint_inventory.strict_json_load(SCHEMA_PATH)
        manifest = hlint_inventory.strict_json_load(MANIFEST_PATH)
        mutations = []

        extra_outer = deepcopy(manifest)
        extra_outer["unknown"] = True
        mutations.append(("extra-outer", extra_outer))

        wrong_language = deepcopy(manifest)
        wrong_language["entries"][0]["language"] = "scala"
        mutations.append(("wrong-language", wrong_language))

        empty_reason = deepcopy(manifest)
        empty_reason["entries"][0]["reason"] = ""
        mutations.append(("empty-reason", empty_reason))

        oversized_reason = deepcopy(manifest)
        oversized_reason["entries"][0]["reason"] = "x" * 1025
        mutations.append(("oversized-reason", oversized_reason))

        absolute_path = deepcopy(manifest)
        absolute_path["entries"][0]["file"] = "/tmp/escape.hs"
        mutations.append(("absolute-path", absolute_path))

        traversal_path = deepcopy(manifest)
        traversal_path["entries"][0]["file"] = "src/../escape.hs"
        mutations.append(("traversal-path", traversal_path))

        duplicate = deepcopy(manifest)
        duplicate["entries"].append(deepcopy(duplicate["entries"][0]))
        mutations.append(("duplicate-composite", duplicate))

        stale_source = deepcopy(manifest)
        stale_source["entries"][0]["file"] = "src/missing/Removed.hs"
        mutations.append(("stale-source", stale_source))

        for label, altered in mutations:
            with self.subTest(label=label):
                with self.assertRaises(hlint_inventory.InventoryError):
                    hlint_inventory.validate_suppression_contract(
                        HASKELL_ROOT,
                        schema,
                        altered,
                    )

    def test_hlint_wrapper_uses_no_external_jsonschema_runtime(self) -> None:
        source = WRAPPER_PATH.read_text(encoding="utf-8")

        self.assertNotIn("from jsonschema", source)
        self.assertIn(
            '--schema "$EXCEPTION_SCHEMA"',
            source,
        )
        self.assertIn(
            '"$HASKELL_ROOT/tools/hlint_inventory.py"',
            source,
        )

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
