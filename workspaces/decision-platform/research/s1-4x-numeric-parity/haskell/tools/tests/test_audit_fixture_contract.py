"""Haskell format/lint/process negative audit fixture closure를 고정한다."""

from __future__ import annotations

import json
import unittest
from pathlib import Path


HASKELL_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_ROOT = HASKELL_ROOT / "tools/fixtures"


class AuditFixtureContractTests(unittest.TestCase):
    def test_hlint_manifest_covers_the_frozen_partial_unsafe_surface(self) -> None:
        manifest = json.loads(
            (FIXTURE_ROOT / "hlint-negative.v1.json").read_text(encoding="utf-8")
        )
        tokens = {
            token
            for fixture in manifest["fixtures"]
            for token in fixture["policyTokens"]
        }
        required = {
            "unsafePerformIO",
            "unsafeDupablePerformIO",
            "unsafeInterleaveIO",
            "unsafeCoerce",
            "undefined",
            "error",
            "head",
            "tail",
            "init",
            "last",
            "!!",
            "read",
            "fromJust",
            "fromLeft",
            "fromRight",
            "throw",
            "throwIO",
            "foldl1",
            "maximum",
            "minimum",
            "Debug.Trace",
            "System.IO.Unsafe",
            "GHC.IO.Unsafe",
            "Foreign.Ptr",
            "foreign import",
            "foreign export",
            "GeneralizedNewtypeDeriving",
            "DerivingVia",
            "DeriveAnyClass",
        }
        self.assertTrue(required.issubset(tokens), sorted(required - tokens))

    def test_every_manifest_entry_is_a_unique_regular_fixture(self) -> None:
        manifest = json.loads(
            (FIXTURE_ROOT / "hlint-negative.v1.json").read_text(encoding="utf-8")
        )
        paths = [fixture["path"] for fixture in manifest["fixtures"]]
        self.assertEqual(len(paths), len(set(paths)))
        for relative in paths:
            with self.subTest(relative=relative):
                path = HASKELL_ROOT / relative
                self.assertTrue(path.is_file())
                self.assertFalse(path.is_symlink())
        for fixture in manifest["fixtures"]:
            self.assertEqual(
                set(fixture),
                {
                    "fixtureId",
                    "path",
                    "expectedTokens",
                    "policyTokens",
                },
            )


if __name__ == "__main__":
    unittest.main()
