"""GHC 9.14 solve 실패 evidence가 portable exact object인지 검증한다."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker


TOOLS_ROOT = Path(__file__).resolve().parents[1]
HASKELL_ROOT = TOOLS_ROOT.parent
NUMERIC_ROOT = HASKELL_ROOT.parent
MODULE_PATH = TOOLS_ROOT / "compatibility_evidence.py"
EVIDENCE_PATH = HASKELL_ROOT / "ghc-compatibility-solve-failure.v1.json"
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
        self.result = compatibility_evidence.build_result(self.evidence)
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

    def test_current_configuration_ast_is_recomputed_from_frozen_stack_yaml(
        self,
    ) -> None:
        configuration = compatibility_evidence.parse_current_configuration_ast(
            HASKELL_ROOT / "stack-ghc-9.14.1.yaml"
        )

        self.assertEqual(
            configuration,
            {
                "compilerCheck": "match-exact",
                "flags": {
                    "math-functions": {
                        "system-erf": False,
                        "system-expm1": False,
                    }
                },
                "installGhc": False,
                "packages": ["."],
                "snapshot": "lts-24.50",
                "systemGhc": True,
            },
        )
        self.assertNotIn("compiler", configuration)

    def test_current_s4804_failure_leaf_is_parsed_not_copied(self) -> None:
        stderr = """\
Error: [S-4804]
       Stack failed to construct a build plan.

       In the dependencies for optparse-applicative-0.18.1.0:
         * process must match >=1.0 && <1.7, but this GHC boot package has been pruned from the
           Stack configuration. You need to add the package explicitly to extra-deps. (latest
           matching version is 1.6.30.0).
       The above is/are needed due to s1-4x-haskell-0.1.0.0 -> optparse-applicative-0.18.1.0

       In the dependencies for s1-4x-haskell-0.1.0.0:
         * directory needed, but this GHC boot package has been pruned from the Stack configuration.
           You need to add the package explicitly to extra-deps. (latest matching version is
           1.3.11.0).
       The above is/are needed since s1-4x-haskell is a build target.

         * Recommended action: try adding the following to your extra-deps in
           /tmp/stack-ghc-9.14.1.yaml

           - directory-1.3.11.0@sha256:2346c4f0af05c4ed55e77543e94b26f1b82523efd24da986bdd48a8f8a84c5a0,3113
           - process-1.6.30.0@sha256:b74eed77eb3237c4ab6a39f08bcce4712b4486b091712ee20e92c8864f1e80a0,3754
"""
        leaf = compatibility_evidence.parse_current_s4804_failure_leaf(
            stderr,
            compatibility_boot_versions={
                "directory": "1.3.10.0",
                "process": "1.6.26.1",
            },
        )

        self.assertEqual(leaf, compatibility_evidence.FAILURE_LEAF)
        with self.assertRaisesRegex(
            compatibility_evidence.CompatibilityEvidenceError,
            "exact S-4804",
        ):
            compatibility_evidence.parse_current_s4804_failure_leaf(
                stderr.replace("1.6.30.0", "1.6.29.0", 1),
                compatibility_boot_versions={
                    "directory": "1.3.10.0",
                    "process": "1.6.26.1",
                },
            )

    def test_current_direct_parents_come_from_cabal_snapshot_and_pantry(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cabal = root / "candidate.cabal"
            cabal.write_text(
                """\
library
  build-depends:
      base
    , alpha ==1.2.3
    , beta
    , candidate-core
""",
                encoding="utf-8",
            )
            pantry = root / "pantry.sqlite3"
            connection = sqlite3.connect(pantry)
            connection.executescript(
                """\
CREATE TABLE package_name(id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE);
CREATE TABLE version(id INTEGER PRIMARY KEY, version TEXT NOT NULL UNIQUE);
CREATE TABLE hackage_tarball(
  id INTEGER PRIMARY KEY,
  name INTEGER NOT NULL,
  version INTEGER NOT NULL,
  sha BLOB NOT NULL,
  size INTEGER NOT NULL
);
"""
            )
            for index, (package, version, digest) in enumerate(
                (
                    ("alpha", "1.2.3", "1" * 64),
                    ("beta", "4.5.6", "2" * 64),
                ),
                start=1,
            ):
                connection.execute(
                    "INSERT INTO package_name(id, name) VALUES (?, ?)",
                    (index, package),
                )
                connection.execute(
                    "INSERT INTO version(id, version) VALUES (?, ?)",
                    (index, version),
                )
                connection.execute(
                    """
INSERT INTO hackage_tarball(id, name, version, sha, size)
VALUES (?, ?, ?, ?, ?)
""",
                    (index, index, index, bytes.fromhex(digest), 1),
                )
            connection.commit()
            connection.close()
            snapshot = """\
flags:
  alpha:
    feature-a: true
packages:
- hackage: alpha-1.2.3@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa,10
- hackage: beta-4.5.6@sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb,11
"""

            parents = compatibility_evidence.derive_current_direct_non_boot_parents(
                cabal_path=cabal,
                snapshot_text=snapshot,
                pantry_db=pantry,
                boot_package_names={"base"},
                local_package_names={"candidate-core"},
                approved_flags={"alpha": {"feature-a": True}},
            )

        self.assertEqual(
            parents,
            [
                {
                    "effectiveFlags": {"feature-a": True},
                    "package": "alpha",
                    "sourceSha256": "1" * 64,
                    "sourceUri": (
                        "https://hackage.haskell.org/package/alpha-1.2.3/"
                        "alpha-1.2.3.tar.gz"
                    ),
                    "version": "1.2.3",
                },
                {
                    "effectiveFlags": {},
                    "package": "beta",
                    "sourceSha256": "2" * 64,
                    "sourceUri": (
                        "https://hackage.haskell.org/package/beta-4.5.6/"
                        "beta-4.5.6.tar.gz"
                    ),
                    "version": "4.5.6",
                },
            ],
        )

    def test_current_snapshot_accepts_hash_addressed_pantry_blob_without_url_row(
        self,
    ) -> None:
        payload = b"flags: {}\npackages: []\n"
        digest = hashlib.sha256(payload).hexdigest()
        with tempfile.TemporaryDirectory() as temporary:
            pantry = Path(temporary) / "pantry.sqlite3"
            connection = sqlite3.connect(pantry)
            connection.executescript(
                """\
CREATE TABLE blob(
  id INTEGER PRIMARY KEY,
  sha BLOB NOT NULL UNIQUE,
  size INTEGER NOT NULL,
  contents BLOB NOT NULL
);
CREATE TABLE url_blob(
  id INTEGER PRIMARY KEY,
  url TEXT NOT NULL,
  blob INTEGER NOT NULL,
  time TEXT NOT NULL
);
"""
            )
            connection.execute(
                "INSERT INTO blob(id, sha, size, contents) VALUES (1, ?, ?, ?)",
                (bytes.fromhex(digest), len(payload), payload),
            )
            connection.commit()
            connection.close()

            snapshot = compatibility_evidence.read_current_snapshot_from_pantry(
                pantry,
                snapshot_url="https://example.invalid/lts.yaml",
                expected_sha256=digest,
                expected_size=len(payload),
            )

        self.assertEqual(snapshot, payload.decode("utf-8"))

    def test_current_snapshot_accepts_duplicate_exact_url_rows(self) -> None:
        payload = b"flags: {}\npackages: []\n"
        digest = hashlib.sha256(payload).hexdigest()
        with tempfile.TemporaryDirectory() as temporary:
            pantry = Path(temporary) / "pantry.sqlite3"
            connection = sqlite3.connect(pantry)
            connection.executescript(
                """\
CREATE TABLE blob(
  id INTEGER PRIMARY KEY,
  sha BLOB NOT NULL UNIQUE,
  size INTEGER NOT NULL,
  contents BLOB NOT NULL
);
CREATE TABLE url_blob(
  id INTEGER PRIMARY KEY,
  url TEXT NOT NULL,
  blob INTEGER NOT NULL,
  time TEXT NOT NULL
);
"""
            )
            connection.execute(
                "INSERT INTO blob(id, sha, size, contents) VALUES (1, ?, ?, ?)",
                (bytes.fromhex(digest), len(payload), payload),
            )
            for row_id, timestamp in (
                (1, "2026-07-19T00:00:00Z"),
                (2, "2026-07-19T00:00:01Z"),
            ):
                connection.execute(
                    """
INSERT INTO url_blob(id, url, blob, time)
VALUES (?, 'https://example.invalid/lts.yaml', 1, ?)
""",
                    (row_id, timestamp),
                )
            connection.commit()
            connection.close()

            snapshot = compatibility_evidence.read_current_snapshot_from_pantry(
                pantry,
                snapshot_url="https://example.invalid/lts.yaml",
                expected_sha256=digest,
                expected_size=len(payload),
            )

        self.assertEqual(snapshot, payload.decode("utf-8"))

    def test_current_snapshot_rejects_drifted_url_row_before_hash_fallback(
        self,
    ) -> None:
        payload = b"flags: {}\npackages: []\n"
        drifted_payload = b"flags:\n  drifted: true\npackages: []\n"
        digest = hashlib.sha256(payload).hexdigest()
        with tempfile.TemporaryDirectory() as temporary:
            pantry = Path(temporary) / "pantry.sqlite3"
            connection = sqlite3.connect(pantry)
            connection.executescript(
                """\
CREATE TABLE blob(
  id INTEGER PRIMARY KEY,
  sha BLOB NOT NULL UNIQUE,
  size INTEGER NOT NULL,
  contents BLOB NOT NULL
);
CREATE TABLE url_blob(
  id INTEGER PRIMARY KEY,
  url TEXT NOT NULL,
  blob INTEGER NOT NULL,
  time TEXT NOT NULL
);
"""
            )
            connection.execute(
                "INSERT INTO blob(id, sha, size, contents) VALUES (1, ?, ?, ?)",
                (bytes.fromhex(digest), len(payload), payload),
            )
            connection.execute(
                "INSERT INTO blob(id, sha, size, contents) VALUES (2, ?, ?, ?)",
                (
                    hashlib.sha256(drifted_payload).digest(),
                    len(drifted_payload),
                    drifted_payload,
                ),
            )
            connection.execute(
                """
INSERT INTO url_blob(id, url, blob, time)
VALUES (1, 'https://example.invalid/lts.yaml', 2, '2026-07-19T00:00:00Z')
"""
            )
            connection.commit()
            connection.close()

            with self.assertRaisesRegex(
                compatibility_evidence.CompatibilityEvidenceError,
                "current frozen snapshot bytes drift",
            ):
                compatibility_evidence.read_current_snapshot_from_pantry(
                    pantry,
                    snapshot_url="https://example.invalid/lts.yaml",
                    expected_sha256=digest,
                    expected_size=len(payload),
                )


if __name__ == "__main__":
    unittest.main()
