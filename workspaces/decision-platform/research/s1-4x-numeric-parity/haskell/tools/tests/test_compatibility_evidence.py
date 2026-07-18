"""GHC 9.14 solve 실패 evidence가 portable exact object인지 검증한다."""

from __future__ import annotations

import copy
import importlib.util
import sys
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker


TOOLS_ROOT = Path(__file__).resolve().parents[1]
HASKELL_ROOT = TOOLS_ROOT.parent
NUMERIC_ROOT = HASKELL_ROOT.parent
MODULE_PATH = TOOLS_ROOT / "compatibility_evidence.py"
EVIDENCE_PATH = HASKELL_ROOT / "ghc-compatibility-solve-failure.v1.json"
RESULT_PATH = NUMERIC_ROOT / "reports/ghc-compatibility-result.v1.json"
RESULT_SCHEMA_PATH = (
    NUMERIC_ROOT / "contract/schemas/ghc-compatibility-result.schema.json"
)
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
        self.result = compatibility_evidence.strict_json_load(RESULT_PATH)
        compatibility_evidence.validate_result_binding(self.result, self.evidence)

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
        altered["rawEvidence"]["baseUri"] = "/" + "home" + "/example/private/stderr"

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

    def test_boot_set_identity_is_hash_bound(self) -> None:
        altered = copy.deepcopy(self.evidence)
        altered["bootSets"]["compatibility"]["packages"][0]["unitId"] = "tampered"

        with self.assertRaisesRegex(
            compatibility_evidence.CompatibilityEvidenceError,
            "compatibility boot set SHA-256",
        ):
            compatibility_evidence.validate_failure_evidence(altered)

    def test_raw_receipts_bind_only_sha_size_and_portable_uri(self) -> None:
        self.assertEqual(
            self.evidence["rawEvidence"],
            {
                "baseUri": (
                    "cache://s1-4x/haskell-evidence/"
                    "ghc914-solve-20260718T174629Z"
                ),
                "stderr": {
                    "pathId": "STDERR",
                    "sha256": (
                        "22c3939bedcd8861c0fe1f987ca500c0ebf3f89ded229a2fe8f7107722019e0a"
                    ),
                    "size": 17544,
                },
                "stdout": {
                    "pathId": "STDOUT",
                    "sha256": (
                        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
                    ),
                    "size": 0,
                },
            },
        )

    def test_result_is_schema_valid_and_hash_binds_the_companion(self) -> None:
        schema = compatibility_evidence.strict_json_load(RESULT_SCHEMA_PATH)
        errors = list(
            Draft202012Validator(
                schema,
                format_checker=FormatChecker(),
            ).iter_errors(self.result)
        )
        self.assertEqual(errors, [])
        self.assertEqual(self.result["result"], "FAIL_FROZEN_DEPENDENCY")
        self.assertTrue(self.result["nonBootPlanEquivalent"])
        self.assertTrue(self.result["expectedBootSetDifferenceOnly"])
        self.assertEqual(
            self.result["minimalReproducerSha256"],
            compatibility_evidence.sha256_file(EVIDENCE_PATH),
        )

    def test_result_with_altered_companion_hash_is_rejected(self) -> None:
        altered = copy.deepcopy(self.result)
        altered["minimalReproducerSha256"] = "0" * 64

        with self.assertRaisesRegex(
            compatibility_evidence.CompatibilityEvidenceError,
            "minimal reproducer SHA-256",
        ):
            compatibility_evidence.validate_result_binding(altered, self.evidence)

    def test_historical_and_canonical_command_contracts_stay_separate(self) -> None:
        execution = self.evidence["execution"]
        historical = execution["historicalCommand"]
        canonical = execution["canonicalReproducer"]
        self.assertEqual(execution["rolloutCallId"], "call_0AtmO5VLjaiRGKBkS7iRyb0H")
        self.assertEqual(execution["timeoutMs"], 900000)
        self.assertEqual(
            historical["shellSetup"],
            {
                "directoryMode": "0700",
                "outputPathTemplate": "CACHE_ROOT/haskell-evidence/$RUN_ID",
                "runIdExpression": (
                    "ghc914-solve-$(date -u +%Y%m%dT%H%M%SZ)"
                ),
                "stackRootPathId": "CACHE_ROOT/stack-root-ghc914",
            },
        )
        self.assertIn("S1_4X_GHC_914_BIN", historical["legacyEnvironment"])
        self.assertIn(
            "S1_4X_LATEST_GHC_BIN",
            canonical["requiredEnvironment"],
        )
        self.assertNotIn(
            "S1_4X_GHC_914_BIN",
            canonical["requiredEnvironment"],
        )
        self.assertEqual(historical["argv"], canonical["argv"])


if __name__ == "__main__":
    unittest.main()
