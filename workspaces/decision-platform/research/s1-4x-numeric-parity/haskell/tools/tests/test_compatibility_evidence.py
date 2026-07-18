"""GHC 9.14 solve 실패 evidence가 portable exact object인지 검증한다."""

from __future__ import annotations

import copy
import importlib.util
import sys
import unittest
from pathlib import Path


TOOLS_ROOT = Path(__file__).resolve().parents[1]
HASKELL_ROOT = TOOLS_ROOT.parent
MODULE_PATH = TOOLS_ROOT / "compatibility_evidence.py"
EVIDENCE_PATH = HASKELL_ROOT / "ghc-compatibility-solve-failure.v1.json"
SPEC = importlib.util.spec_from_file_location("compatibility_evidence", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("unable to load compatibility_evidence.py")
compatibility_evidence = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = compatibility_evidence
SPEC.loader.exec_module(compatibility_evidence)


class CompatibilityEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.evidence = compatibility_evidence.strict_json_load(EVIDENCE_PATH)
        compatibility_evidence.validate_failure_evidence(self.evidence)

    def test_unknown_field_is_rejected(self) -> None:
        altered = copy.deepcopy(self.evidence)
        altered["unknown"] = True

        with self.assertRaisesRegex(
            compatibility_evidence.CompatibilityEvidenceError,
            "outer field set",
        ):
            compatibility_evidence.validate_failure_evidence(altered)

    def test_altered_pruned_boot_package_is_rejected(self) -> None:
        altered = copy.deepcopy(self.evidence)
        altered["failureLeaf"]["prunedBootPackages"][0]["package"] = "filepath"

        with self.assertRaisesRegex(
            compatibility_evidence.CompatibilityEvidenceError,
            "pruned boot package set",
        ):
            compatibility_evidence.validate_failure_evidence(altered)

    def test_altered_suggested_extra_dep_is_rejected(self) -> None:
        altered = copy.deepcopy(self.evidence)
        altered["failureLeaf"]["suggestedExtraDeps"][0]["version"] = "0.0.0"

        with self.assertRaisesRegex(
            compatibility_evidence.CompatibilityEvidenceError,
            "suggested extra-dep set",
        ):
            compatibility_evidence.validate_failure_evidence(altered)

    def test_raw_absolute_path_is_rejected(self) -> None:
        altered = copy.deepcopy(self.evidence)
        altered["rawEvidence"]["baseUri"] = "/home/example/private/stderr"

        with self.assertRaisesRegex(
            compatibility_evidence.CompatibilityEvidenceError,
            "portable",
        ):
            compatibility_evidence.validate_failure_evidence(altered)

    def test_downstream_not_run_closure_is_exact(self) -> None:
        altered = copy.deepcopy(self.evidence)
        altered["downstream"]["candidateCompile"] = "PASS"

        with self.assertRaisesRegex(
            compatibility_evidence.CompatibilityEvidenceError,
            "downstream NOT_RUN closure",
        ):
            compatibility_evidence.validate_failure_evidence(altered)

    def test_fallback_does_not_claim_a_full_compatibility_plan(self) -> None:
        altered = copy.deepcopy(self.evidence)
        altered["fallbackProof"]["fullCompatibilityPlanAvailable"] = True

        with self.assertRaisesRegex(
            compatibility_evidence.CompatibilityEvidenceError,
            "full compatibility plan",
        ):
            compatibility_evidence.validate_failure_evidence(altered)

    def test_direct_parent_manifest_equality_is_hash_bound(self) -> None:
        altered = copy.deepcopy(self.evidence)
        altered["fallbackProof"]["compatibilityDirectNonBootParents"][0][
            "constraint"
        ] = ">=0"

        with self.assertRaisesRegex(
            compatibility_evidence.CompatibilityEvidenceError,
            "direct non-boot parent manifest",
        ):
            compatibility_evidence.validate_failure_evidence(altered)

    def test_failed_partial_plan_hash_is_separate_and_exact(self) -> None:
        altered = copy.deepcopy(self.evidence)
        altered["fallbackProof"]["failedPartialPlanSha256"] = "0" * 64

        with self.assertRaisesRegex(
            compatibility_evidence.CompatibilityEvidenceError,
            "failed partial plan SHA-256",
        ):
            compatibility_evidence.validate_failure_evidence(altered)


if __name__ == "__main__":
    unittest.main()
