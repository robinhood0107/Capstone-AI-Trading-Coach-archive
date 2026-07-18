"""Haskell profile이 실행 도구와 audit 입력까지 폐쇄적으로 결속하는지 검사한다."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


TOOLS_ROOT = Path(__file__).resolve().parents[1]
HASKELL_ROOT = TOOLS_ROOT.parent
MODULE_PATH = TOOLS_ROOT / "haskell_evidence.py"
SPEC = importlib.util.spec_from_file_location("haskell_evidence", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("unable to load haskell_evidence.py")
haskell_evidence = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = haskell_evidence
SPEC.loader.exec_module(haskell_evidence)

EXPECTED_WORKFLOW_INPUTS = (
    ".hlint.yaml",
    "Containerfile",
    "ghc-compatibility-solve-failure.v1.json",
    "lint-exceptions.v1.json",
    "stack-ghc-9.14.1.yaml",
    "stack-ghc-9.14.1.yaml.lock",
    "stylish-ghc2024-fallback.v1.json",
    "toolchain-lock.v1.json",
    "tools/assert-toolchain.sh",
    "tools/check-format.sh",
    "tools/check-hlint.sh",
    "tools/compatibility_evidence.py",
    "tools/fixtures/hlint-negative.v1.json",
    "tools/fixtures/hlint/aliased-from-left.hs",
    "tools/fixtures/hlint/aliased-from-right.hs",
    "tools/fixtures/hlint/core-system-io.hs",
    "tools/fixtures/hlint/debug-trace.hs",
    "tools/fixtures/hlint/forbidden-deriving.hs",
    "tools/fixtures/hlint/forbidden-extension.hs",
    "tools/fixtures/hlint/foreign-interface.hs",
    "tools/fixtures/hlint/partial-and-unsafe.hs",
    "tools/fixtures/hlint/qualified-from-just.hs",
    "tools/fixtures/hlint/qualified-throw-io.hs",
    "tools/fixtures/hlint/qualified-throw.hs",
    "tools/fixtures/hlint/unchecked-folds.hs",
    "tools/fixtures/hlint/unsafe-module.hs",
    "tools/fixtures/hlint/unsafe-modules.hs",
    "tools/fixtures/process/large/unicode-digit-path.manifest.json",
    "tools/fixtures/process/large/unicode-digit-sha.manifest.json",
    "tools/fixtures/stylish/misformatted.hs",
    "tools/haskell_benchmark_block.py",
    "tools/haskell_evidence.py",
    "tools/hlint_inventory.py",
    "tools/profile_workflow.py",
    "tools/run-benchmark-block.sh",
    "tools/run-candidate.sh",
    "tools/run-correctness-profile.sh",
    "tools/run-ghc-9.14.1-compatibility.sh",
    "tools/run-oci-correctness.sh",
    "tools/run-profile-qualification.sh",
    "tools/run-property-evidence.sh",
    "tools/select-proven-profile.sh",
    "tools/stylish_fallback.py",
    "tools/validate-ghc-9.14.1-compatibility.sh",
)


class WorkflowInputClosureTests(unittest.TestCase):
    def test_workflow_input_allowlist_is_exactly_the_tracked_non_test_tool_set(
        self,
    ) -> None:
        tracked_tools = {
            path.relative_to(HASKELL_ROOT).as_posix()
            for path in TOOLS_ROOT.rglob("*")
            if (
                path.is_file()
                and not path.is_symlink()
                and "tests" not in path.relative_to(TOOLS_ROOT).parts
                and "__pycache__" not in path.parts
            )
        }
        expected_tools = {
            path for path in EXPECTED_WORKFLOW_INPUTS if path.startswith("tools/")
        }

        self.assertEqual(
            tuple(haskell_evidence.WORKFLOW_INPUT_PATHS),
            EXPECTED_WORKFLOW_INPUTS,
        )
        self.assertEqual(tracked_tools, expected_tools)

    def test_profile_source_tree_binds_compile_toolchain_and_workflow_inputs(
        self,
    ) -> None:
        entries = haskell_evidence.benchmark_source_tree_entries(HASKELL_ROOT)
        paths = {entry["path"] for entry in entries}

        for required in (
            "package.yaml",
            "s1-4x-haskell.cabal",
            "stack.yaml",
            "stack.yaml.lock",
            *EXPECTED_WORKFLOW_INPUTS,
        ):
            with self.subTest(required=required):
                self.assertIn(required, paths)
        self.assertNotIn("selected-profile.v1.json", paths)
        self.assertNotIn("source-inputs.v1.json", paths)
        self.assertFalse(any(path.startswith("tools/tests/") for path in paths))

    def test_compile_manifest_stays_schema_scoped_while_profile_binds_tools(
        self,
    ) -> None:
        manifest = haskell_evidence.build_source_manifest(HASKELL_ROOT)
        manifest_paths = set(manifest["files"])
        source_tree_paths = {
            entry["path"]
            for entry in haskell_evidence.benchmark_source_tree_entries(HASKELL_ROOT)
        }

        self.assertTrue(
            all(
                path.endswith(".hs")
                or path in {"package.yaml", "selected-profile.v1.json"}
                for path in manifest_paths
            )
        )
        self.assertTrue(set(EXPECTED_WORKFLOW_INPUTS).isdisjoint(manifest_paths))
        self.assertTrue(set(EXPECTED_WORKFLOW_INPUTS).issubset(source_tree_paths))

    def test_module_and_dependency_audit_inputs_are_profile_bound(self) -> None:
        paths = {
            entry["path"]
            for entry in haskell_evidence.benchmark_source_tree_entries(HASKELL_ROOT)
        }

        self.assertTrue(
            {
                "package.yaml",
                "s1-4x-haskell.cabal",
                "stack.yaml",
                "stack.yaml.lock",
                ".hlint.yaml",
                "ghc-compatibility-solve-failure.v1.json",
                "lint-exceptions.v1.json",
                "stack-ghc-9.14.1.yaml",
                "stack-ghc-9.14.1.yaml.lock",
                "stylish-ghc2024-fallback.v1.json",
                "toolchain-lock.v1.json",
                "tools/check-format.sh",
                "tools/check-hlint.sh",
                "tools/haskell_evidence.py",
                "tools/hlint_inventory.py",
            }.issubset(paths)
        )


if __name__ == "__main__":
    unittest.main()
