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
HASKELL_ROOT = TOOLS_ROOT.parent
POLICY_PATH = HASKELL_ROOT.parent / "contract/haskell-module-safety-policy.v1.json"
MODULE_PATH = TOOLS_ROOT / "haskell_evidence.py"
SPEC = importlib.util.spec_from_file_location("haskell_evidence", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("unable to load haskell_evidence.py")
haskell_evidence = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = haskell_evidence
SPEC.loader.exec_module(haskell_evidence)


class HaskellEvidenceTests(unittest.TestCase):
    def _module_safety_policy(self) -> dict[str, object]:
        return json.loads(POLICY_PATH.read_text(encoding="utf-8"))

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

    def test_source_manifest_rejects_a_stale_tracked_entry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = {
                "src/Risk.hs": "module Risk (value) where\nvalue = 1\n",
                "app/Main.hs": "module Main (main) where\nmain = pure ()\n",
                "test/TestMain.hs": "module TestMain (main) where\nmain = pure ()\n",
                "benchmark/BenchMain.hs": "module BenchMain (main) where\nmain = pure ()\n",
                "package.yaml": "name: sample\n",
                "selected-profile.v1.json": "{}\n",
            }
            for relative, content in paths.items():
                destination = root / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(content, encoding="utf-8")

            with self.assertRaisesRegex(
                haskell_evidence.EvidenceError,
                "tracked source input set mismatch",
            ):
                haskell_evidence.build_source_manifest(
                    root,
                    tracked_paths={*paths, "src/Stale.hs"},
                )

    def test_source_manifest_rejects_an_intermediate_directory_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "candidate"
            outside = Path(temporary) / "outside"
            for relative in ("src", "app", "test", "benchmark"):
                (root / relative).mkdir(parents=True, exist_ok=True)
            outside.mkdir()
            (outside / "Escape.hs").write_text(
                "module Escape (value) where\nvalue = 1\n",
                encoding="utf-8",
            )
            (root / "src" / "Nested").symlink_to(outside, target_is_directory=True)
            (root / "app" / "Main.hs").write_text(
                "module Main (main) where\nmain = pure ()\n",
                encoding="utf-8",
            )
            (root / "test" / "TestMain.hs").write_text(
                "module TestMain (main) where\nmain = pure ()\n",
                encoding="utf-8",
            )
            (root / "benchmark" / "BenchMain.hs").write_text(
                "module BenchMain (main) where\nmain = pure ()\n",
                encoding="utf-8",
            )
            (root / "package.yaml").write_text("name: sample\n", encoding="utf-8")
            (root / "selected-profile.v1.json").write_text("{}\n", encoding="utf-8")

            with self.assertRaisesRegex(
                haskell_evidence.EvidenceError,
                "candidate source symlink is forbidden",
            ):
                haskell_evidence.build_source_manifest(
                    root,
                    tracked_paths={
                        "app/Main.hs",
                        "test/TestMain.hs",
                        "benchmark/BenchMain.hs",
                        "package.yaml",
                        "selected-profile.v1.json",
                    },
                )

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

    def test_show_iface_parser_uses_direct_home_dependency_section_only(self) -> None:
        output = """
interface S14X.Core.AdvancedRisk 9103
direct module dependencies: pkg:S14X.Core.Error
                            pkg:S14X.Core.Models
boot module dependencies:
direct package dependencies: base-4.20.2.0
import  -/  Data.Vector.Unboxed deadbeef
"""
        self.assertEqual(
            haskell_evidence.parse_show_iface_home_imports(
                output,
                candidate_modules={
                    "S14X.Core.AdvancedRisk",
                    "S14X.Core.Error",
                    "S14X.Core.Models",
                },
            ),
            ("S14X.Core.Error", "S14X.Core.Models"),
        )

    def test_module_safety_policy_requires_the_exact_frozen_field_set(self) -> None:
        policy = self._module_safety_policy()
        haskell_evidence.validate_module_safety_policy(policy)

        for altered in (
            {**policy, "unknownPolicyControl": True},
            {key: value for key, value in policy.items() if key != "forbiddenCoreTypesAndUses"},
        ):
            with self.subTest(fields=sorted(altered)):
                with self.assertRaisesRegex(
                    haskell_evidence.EvidenceError,
                    "module-safety policy field set",
                ):
                    haskell_evidence.validate_module_safety_policy(altered)

    def test_core_cannot_reenable_a_mandatory_negative_extension(self) -> None:
        policy = self._module_safety_policy()
        source = b"""\
{-# LANGUAGE Safe, CPP #-}
module Risk.Core (value) where
value = 1
"""
        parsed = haskell_evidence.parse_haskell_module(source)

        with self.assertRaisesRegex(
            haskell_evidence.EvidenceError,
            "forbidden core positive extension",
        ):
            haskell_evidence.audit_candidate_source(
                relative="src/core/Risk/Core.hs",
                parsed=parsed,
                payload=source,
                category="safe-scalar",
                default_extensions=tuple(policy["mandatoryCoreExtensions"]),
                policy=policy,
            )

    def test_source_local_controls_are_rejected_across_all_candidate_roots(self) -> None:
        policy = self._module_safety_policy()
        fixtures = {
            "src/contract/Risk/Shell.hs": b"""\
{-# OPTIONS_GHC -Wno-everything #-}
module Risk.Shell (value) where
value = 1
""",
            "app/Main.hs": b"""\
{-# ANN module ("HLint: ignore Use head" :: String) #-}
module Main (main) where
main = pure ()
""",
            "test/RiskSpec.hs": b"""\
{-# LANGUAGE CPP #-}
module RiskSpec (tests) where
tests = ()
""",
            "benchmark/Main.hs": b"""\
{-# LANGUAGE MagicHash #-}
module BenchMain (main) where
main = pure ()
""",
        }
        for relative, source in fixtures.items():
            with self.subTest(relative=relative):
                parsed = haskell_evidence.parse_haskell_module(source)
                category = haskell_evidence.module_category(
                    relative,
                    parsed.module_name,
                )
                with self.assertRaisesRegex(
                    haskell_evidence.EvidenceError,
                    "forbidden source-local control",
                ):
                    haskell_evidence.audit_candidate_source(
                        relative=relative,
                        parsed=parsed,
                        payload=source,
                        category=category,
                        default_extensions=tuple(policy["mandatoryCoreExtensions"]),
                        policy=policy,
                    )

    def test_core_environment_clock_random_and_network_imports_are_rejected(
        self,
    ) -> None:
        policy = self._module_safety_policy()
        for imported in (
            "System.Environment",
            "Data.Time.Clock",
            "System.Random",
            "Network.Socket",
        ):
            with self.subTest(imported=imported):
                source = f"""\
{{-# LANGUAGE Safe #-}}
module Risk.Core (value) where
import {imported}
value = 1
""".encode()
                parsed = haskell_evidence.parse_haskell_module(source)
                with self.assertRaisesRegex(
                    haskell_evidence.EvidenceError,
                    "forbidden core capability",
                ):
                    haskell_evidence.audit_candidate_source(
                        relative="src/core/Risk/Core.hs",
                        parsed=parsed,
                        payload=source,
                        category="safe-scalar",
                        default_extensions=tuple(policy["mandatoryCoreExtensions"]),
                        policy=policy,
                    )

    def test_non_stock_deriving_policy_applies_only_to_core_modules(self) -> None:
        policy = self._module_safety_policy()
        core_source = b"""\
{-# LANGUAGE Safe #-}
module Risk.Core (Wrapped (..)) where
newtype Wrapped = Wrapped Int
  deriving (Eq)
"""
        with self.assertRaisesRegex(
            haskell_evidence.EvidenceError,
            "candidate core deriving must be stock",
        ):
            haskell_evidence.audit_candidate_source(
                relative="src/core/Risk/Core.hs",
                parsed=haskell_evidence.parse_haskell_module(core_source),
                payload=core_source,
                category="safe-scalar",
                default_extensions=tuple(policy["mandatoryCoreExtensions"]),
                policy=policy,
            )

        shell_source = b"""\
module Risk.Shell (Wrapped (..)) where
newtype Wrapped = Wrapped Int
  deriving (Eq)
"""
        haskell_evidence.audit_candidate_source(
            relative="src/contract/Risk/Shell.hs",
            parsed=haskell_evidence.parse_haskell_module(shell_source),
            payload=shell_source,
            category="io-shell",
            default_extensions=tuple(policy["mandatoryCoreExtensions"]),
            policy=policy,
        )

    def test_vector_edges_are_derived_from_actual_source_graph(self) -> None:
        source_sha256 = "1" * 64
        provenance = "verified archive provenance"
        sources = {
            "Data.Vector.Unboxed": b"""
module Data.Vector.Unboxed (value) where
import Data.Vector.Unboxed.Base
import Data.Vector.Generic
value = 1
""",
            "Data.Vector.Unboxed.Base": b"""
module Data.Vector.Unboxed.Base (value) where
import Data.Vector.Primitive
value = 1
""",
            "Data.Vector.Primitive": b"""
module Data.Vector.Primitive (value) where
import Data.Vector.Primitive.Mutable
import Unsafe.Coerce
value = 1
""",
            "Data.Vector.Primitive.Mutable": b"""
module Data.Vector.Primitive.Mutable (value) where
import Unsafe.Coerce
value = 1
""",
            "Data.Vector.Generic": b"""
module Data.Vector.Generic (value) where
import Data.Vector.Internal.Check
value = 1
""",
            "Data.Vector.Internal.Check": b"""
{-# LANGUAGE MagicHash #-}
module Data.Vector.Internal.Check (value) where
import GHC.Exts (Int#)
value = 1
""",
        }
        expected = [
            {
                "package": "vector",
                "version": "0.13.2.0",
                "sourceSha256": source_sha256,
                "importPath": (
                    "Data.Vector.Unboxed -> Data.Vector.Unboxed.Base -> "
                    "Data.Vector.Primitive -> Unsafe.Coerce"
                ),
                "provenance": provenance,
                "edgeKind": "unsafe-import",
            },
            {
                "package": "vector",
                "version": "0.13.2.0",
                "sourceSha256": source_sha256,
                "importPath": (
                    "Data.Vector.Unboxed -> Data.Vector.Unboxed.Base -> "
                    "Data.Vector.Primitive -> Data.Vector.Primitive.Mutable -> "
                    "Unsafe.Coerce"
                ),
                "provenance": provenance,
                "edgeKind": "unsafe-import",
            },
            {
                "package": "vector",
                "version": "0.13.2.0",
                "sourceSha256": source_sha256,
                "importPath": (
                    "Data.Vector.Unboxed -> Data.Vector.Generic -> "
                    "Data.Vector.Internal.Check -> GHC.Exts(Int#)"
                ),
                "provenance": provenance,
                "edgeKind": "compiler-primop",
            },
        ]
        self.assertEqual(
            haskell_evidence.derive_vector_transitive_edges(
                sources,
                source_sha256=source_sha256,
                provenance=provenance,
            ),
            expected,
        )

        drifted = dict(sources)
        drifted["Data.Vector.Primitive"] = drifted["Data.Vector.Primitive"].replace(
            b"import Unsafe.Coerce\n",
            b"",
        )
        with self.assertRaisesRegex(
            haskell_evidence.EvidenceError,
            "unsafe target set drift",
        ):
            haskell_evidence.derive_vector_transitive_edges(
                drifted,
                source_sha256=source_sha256,
                provenance=provenance,
            )


if __name__ == "__main__":
    unittest.main()
