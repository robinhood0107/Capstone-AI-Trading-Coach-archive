"""Integration이 호출하는 Haskell candidate process wrapper 계약 테스트."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


TOOLS_ROOT = Path(__file__).resolve().parents[1]
HASKELL_ROOT = TOOLS_ROOT.parent
WRAPPER = TOOLS_ROOT / "run-candidate.sh"
HELPER_PATH = TOOLS_ROOT / "profile_workflow.py"


def load_helper():
    specification = importlib.util.spec_from_file_location(
        "candidate_profile_workflow",
        HELPER_PATH,
    )
    if specification is None or specification.loader is None:
        raise AssertionError("unable to load profile_workflow.py")
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


class CandidateWrapperContractTests(unittest.TestCase):
    def test_wrapper_exposes_exact_protocol_without_bootstrap_escape(self) -> None:
        self.assertTrue(WRAPPER.is_file(), "run-candidate.sh is missing")
        self.assertTrue(WRAPPER.stat().st_mode & 0o111)
        source = WRAPPER.read_text(encoding="utf-8")
        for required in (
            "--request",
            "--fixture-root",
            "--output",
            "assert-toolchain.sh",
            "selected-profile.v1.json",
            "source-inputs.v1.json",
            "--stack-root",
            "--stack-yaml",
            "--system-ghc",
            "--no-install-ghc",
            "--hpack-force",
            "--silent",
            "S1_4X_CACHE_ROOT",
            "STACK_YAML",
            "STACK_ROOT",
            "STACK_OPTS",
            "STACK_CONFIG",
        ):
            with self.subTest(required=required):
                self.assertIn(required, source)
        for forbidden in (
            "eval ",
            "bash -c",
            "sh -c",
            "${S1_4X_HASKELL_CANDIDATE_BIN",
            "command -v",
            "/" + "home" + "/",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)

    def test_runtime_profile_reader_rejects_pending_and_unknown_fields(self) -> None:
        helper = load_helper()
        pending = {
            "schemaVersion": "s1.4x-haskell-selected-profile-pending-v1",
        }
        with self.assertRaisesRegex(
            helper.WorkflowError,
            "RUNTIME_SELECTED_PROFILE_NOT_FINAL",
        ):
            helper.runtime_selected_profile(pending)

        final = {
            "schemaVersion": "s1.4x-haskell-selected-profile-v1",
            "profileId": "baseline-o0-fasm",
            "ghcOptions": ["-O0", "-fasm"],
            "compilerVersion": "9.10.3",
            "compilerSha256": "1" * 64,
            "sourceTreeSha256": "2" * 64,
            "optionsSha256": helper.canonical_sha256(["-O0", "-fasm"]),
            "fullCorrectnessSha256": "3" * 64,
            "qualificationPlanSha256": "4" * 64,
            "qualificationArtifactSha256": "5" * 64,
            "selectorConfigSha256": "6" * 64,
            "fallbackProfile": "baseline-o0-fasm",
            "selectedBy": "proven-fallback",
        }
        self.assertEqual(
            helper.runtime_selected_profile(final),
            ("baseline-o0-fasm", ("-O0", "-fasm")),
        )
        final["unknown"] = True
        with self.assertRaisesRegex(
            helper.WorkflowError,
            "RUNTIME_SELECTED_PROFILE_INVALID",
        ):
            helper.runtime_selected_profile(final)

    def test_candidate_stack_root_is_output_bound_and_nonportable_free(
        self,
    ) -> None:
        helper = load_helper()
        first = helper.candidate_stack_root(
            Path("/cache/s1-4x"),
            Path("/evidence/run-a/result.json"),
        )
        second = helper.candidate_stack_root(
            Path("/cache/s1-4x"),
            Path("/evidence/run-b/result.json"),
        )
        self.assertNotEqual(first, second)
        self.assertEqual(first.parent, Path("/cache/s1-4x"))
        self.assertRegex(
            first.name,
            r"^stack-root-candidate-[0-9a-f]{24}$",
        )

    def test_profile_options_are_applied_by_build_before_stack_exec(self) -> None:
        """Stack 3.11 run parser가 받지 않는 build option을 run 뒤에 두지 않는다."""

        source = WRAPPER.read_text(encoding="utf-8")
        self.assertIn('"${STACK_COMMAND[@]}" build \\', source)
        self.assertIn("s1-4x-haskell:exe:s1-4x-haskell", source)
        self.assertIn('--ghc-options "$PROFILE_GHC_OPTIONS"', source)
        self.assertIn('"${STACK_COMMAND[@]}" exec s1-4x-haskell -- \\', source)
        self.assertNotRegex(source, r"\brun\s+\\\n\s+--ghc-options\b")

    def test_wrapper_is_bound_into_profile_source_closure(self) -> None:
        helper = load_helper()
        evidence = helper._load_haskell_evidence(HASKELL_ROOT)
        paths = {
            entry["path"]
            for entry in evidence.benchmark_source_tree_entries(HASKELL_ROOT)
        }
        self.assertIn("tools/run-candidate.sh", paths)


if __name__ == "__main__":
    unittest.main()
