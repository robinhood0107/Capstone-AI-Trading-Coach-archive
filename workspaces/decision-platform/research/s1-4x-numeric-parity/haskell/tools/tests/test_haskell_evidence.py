"""Haskell evidence tooling의 deterministic contract 회귀 테스트."""

from __future__ import annotations

import importlib.util
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


TOOLS_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = TOOLS_ROOT / "haskell_evidence.py"
SPEC = importlib.util.spec_from_file_location("haskell_evidence", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("unable to load haskell_evidence.py")
haskell_evidence = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = haskell_evidence
SPEC.loader.exec_module(haskell_evidence)


class HaskellEvidenceTests(unittest.TestCase):
    def test_parser_resolves_module_aliases_without_losing_import_identity(self) -> None:
        parsed = haskell_evidence.parse_haskell_module(
            """
{-# LANGUAGE Safe #-}
module Risk.Example (value) where

import qualified Data.Maybe as Maybe
import Risk.Home (Thing)

value :: Maybe a -> a
value = Maybe.fromJust
""".encode()
        )

        self.assertEqual(parsed.module_name, "Risk.Example")
        self.assertEqual(parsed.extensions, ("Safe",))
        self.assertEqual(parsed.imports, ("Data.Maybe", "Risk.Home"))

    def test_source_manifest_uses_one_exact_file_object_for_every_consumer(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = {
                "src/core/Risk/Core.hs": b"module Risk.Core (value) where\nvalue = 1\n",
                "src/contract/Risk/Shell.hs": b"module Risk.Shell (value) where\nvalue = 1\n",
                "app/Main.hs": b"module Main (main) where\nmain = pure ()\n",
                "test/RiskSpec.hs": b"module RiskSpec (tests) where\ntests = ()\n",
                "benchmark/Main.hs": b"module Main (main) where\nmain = pure ()\n",
                "package.yaml": b"name: sample\n",
                "selected-profile.v1.json": b"{}\n",
            }
            for relative, payload in paths.items():
                destination = root / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(payload)

            manifest = haskell_evidence.build_source_manifest(
                root,
                tracked_paths=set(paths),
            )

        self.assertEqual(manifest["language"], "haskell")
        self.assertEqual(
            manifest["inputSets"],
            {
                "tracked": "files",
                "manifest": "files",
                "format": "files",
                "compile": "files",
                "lint": "files",
                "profileRun": "files",
            },
        )
        self.assertEqual(manifest["files"]["package.yaml"]["role"], "configuration")
        self.assertEqual(
            manifest["files"]["selected-profile.v1.json"]["role"],
            "configuration",
        )
        self.assertEqual(
            manifest["canonicalManifestSha256"],
            haskell_evidence.canonical_source_manifest_sha256(manifest["files"]),
        )

    def test_source_manifest_hash_uses_byte_sorted_sha256sum_lines(self) -> None:
        files = {
            "src/Zeta.hs": {"role": "main", "sha256": "b" * 64},
            "app/Alpha.hs": {"role": "main", "sha256": "a" * 64},
        }
        expected = hashlib.sha256(
            (
                f"{'a' * 64}  app/Alpha.hs\n"
                f"{'b' * 64}  src/Zeta.hs\n"
            ).encode()
        ).hexdigest()
        self.assertEqual(
            haskell_evidence.canonical_source_manifest_sha256(files),
            expected,
        )

    def test_source_manifest_rejects_untracked_or_non_hs_candidate_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "src").mkdir()
            (root / "src" / "Risk.hs").write_text(
                "module Risk (value) where\nvalue = 1\n",
                encoding="utf-8",
            )
            (root / "src" / "Escape.hsc").write_text(
                "module Escape (value) where\nvalue = 1\n",
                encoding="utf-8",
            )
            (root / "package.yaml").write_text("name: sample\n", encoding="utf-8")
            (root / "selected-profile.v1.json").write_text("{}\n", encoding="utf-8")

            with self.assertRaisesRegex(
                haskell_evidence.EvidenceError,
                "forbidden compilable suffix",
            ):
                haskell_evidence.build_source_manifest(
                    root,
                    tracked_paths={
                        "src/Risk.hs",
                        "src/Escape.hsc",
                        "package.yaml",
                        "selected-profile.v1.json",
                    },
                )

    def test_git_enumerator_rejects_untracked_candidate_source_escape(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            (root / "src").mkdir()
            (root / "app").mkdir()
            (root / "test").mkdir()
            (root / "benchmark").mkdir()
            tracked = {
                "src/Risk.hs": "module Risk (value) where\nvalue = 1\n",
                "app/Main.hs": "module Main (main) where\nmain = pure ()\n",
                "test/TestMain.hs": "module TestMain (main) where\nmain = pure ()\n",
                "benchmark/BenchMain.hs": "module BenchMain (main) where\nmain = pure ()\n",
                "package.yaml": "name: sample\n",
                "selected-profile.v1.json": "{}\n",
            }
            for relative, content in tracked.items():
                destination = root / relative
                destination.write_text(content, encoding="utf-8")
            subprocess.run(["git", "-C", str(root), "add", *tracked], check=True)
            (root / "src" / "Untracked.hs").write_text(
                "module Untracked (value) where\nvalue = 2\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                haskell_evidence.EvidenceError,
                "untracked candidate input",
            ):
                haskell_evidence.build_source_manifest(root)

    def test_profile_selector_uses_all_twenty_eight_paired_ratios(self) -> None:
        cases = [f"case-{index}" for index in range(7)]
        qualifying = [
            {
                "orderBlock": index,
                "ratios": {case_id: 0.95 for case_id in cases},
            }
            for index in range(4)
        ]
        selected = haskell_evidence.select_haskell_profile(
            qualifying,
            qualification_case_order=cases,
        )
        self.assertEqual(selected.profile_id, "optimized-o2-fasm")
        self.assertEqual(selected.selected_by, "frozen-criterion-selector")
        self.assertEqual(len(selected.paired_ratios), 28)

        regressed = json.loads(json.dumps(qualifying))
        regressed[0]["ratios"][cases[0]] = 1.051
        fallback = haskell_evidence.select_haskell_profile(
            regressed,
            qualification_case_order=cases,
        )
        self.assertEqual(fallback.profile_id, "baseline-o0-fasm")
        self.assertEqual(fallback.selected_by, "proven-fallback")

    def test_cabal_projection_rejects_the_stale_single_library_shape(self) -> None:
        stale = """
library
  hs-source-dirs: src
  build-depends:
      statistics ==0.16.5.0
"""
        with self.assertRaisesRegex(
            haskell_evidence.EvidenceError,
            "generated Cabal projection",
        ):
            haskell_evidence.validate_cabal_projection(stale)

        generated = """
library
  hs-source-dirs:
      src/contract
  build-depends:
      s1-4x-core

library s1-4x-core
  hs-source-dirs:
      src/core
  build-depends:
      deepseq
    , math-functions ==0.3.4.4
    , vector ==0.13.2.0

benchmark s1-4x-haskell-benchmark
  other-modules:
      S14X.Contract.BenchmarkValidation

test-suite s1-4x-haskell-test
  other-modules:
      S14X.BenchmarkStaticSpec
"""
        haskell_evidence.validate_cabal_projection(generated)


if __name__ == "__main__":
    unittest.main()
