"""최종 후보 audit가 실제 typed PASS artifact에서만 점수를 도출하는지 검증한다."""

from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import TestCase

INTEGRATION = Path(__file__).resolve().parents[1]
S1_4X = INTEGRATION.parent
REPO = S1_4X.parents[3]
sys.path.insert(0, str(INTEGRATION))

from final_candidate_audit import (  # noqa: E402
    CANDIDATES,
    EXPECTED_EVIDENCE_CLAIMS,
    EXPECTED_SOURCE_CONTRACTS,
    FROZEN_CONTRACT_MANIFEST_SHA256,
    FROZEN_GHCUP_SHA256,
    FROZEN_MERGED_TOOLCHAIN_PROVENANCE_SHA256,
    FROZEN_REFERENCE_LOCK_SHA256,
    FROZEN_SCALA_CLI_SHA256,
    FROZEN_SCALAFIX_SHA256,
    FROZEN_STACK_SHA256,
    FinalAuditError,
    generate_final_candidate_audit,
    validate_final_candidate_audit,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _coverage_candidate(candidate: str) -> dict[str, object]:
    return {
        "implementation": candidate,
        "reportedImplementation": f"{candidate}-candidate",
        "propertyPlanSha256": "1" * 64,
        "propertyCount": 25,
        "functionCount": 20,
        "errorCount": 32,
        "errorTrackCounts": {"s1.4": 19, "s1.4r": 13},
        "errorVerificationModeCounts": {
            "processDynamic": 29,
            "referenceObjectModel": 1,
            "registryStatic": 2,
        },
        "processDynamicErrorCount": 29,
        "staticErrorCount": 3,
        "propertyExecution": {
            "framework": "ScalaCheck" if candidate == "scala" else "QuickCheck",
            "toolchainProfile": "selected-profile",
            "seedCorpusSha256": "2" * 64,
            "seedCount": 24,
            "minimumSuccessfulPerSeed": 42,
            "runnerSha256": "3" * 64,
            "sourceClosureSha256": "4" * 64,
            "startedAt": "2026-07-18T00:00:00Z",
            "finishedAt": "2026-07-18T00:01:00Z",
        },
        "status": "PASS",
    }


def _source_document(
    *,
    candidate: str,
    evidence_id: str,
    role: str,
    schema: str,
    commit: str,
    reviewed_path: str,
    reviewed_sha256: str,
) -> dict[str, object]:
    if evidence_id in {"correctness-contract", "property-coverage"}:
        return {
            "schemaVersion": schema,
            "candidateCount": 2,
            "candidates": [_coverage_candidate(item) for item in CANDIDATES],
            "propertyCountPerCandidate": 25,
            "functionCountPerCandidate": 20,
            "errorTrackCountsPerCandidate": {"s1.4": 19, "s1.4r": 13},
            "errorVerificationModeCountsPerCandidate": {
                "processDynamic": 29,
                "referenceObjectModel": 1,
                "registryStatic": 2,
            },
            "status": "PASS",
        }
    if evidence_id == "cross-language-parity":
        return {
            "schemaVersion": schema,
            "requestId": f"{role}-request",
            "implementationCount": 2,
            "mismatchCount": 0,
            "mismatches": [],
            "status": "PASS",
        }
    if evidence_id == "regressions":
        production = role == "production-regression"
        return {
            "schemaVersion": schema,
            "candidate": candidate,
            "benchmarkSubjectCommit": commit,
            "project": (
                "workspaces/decision-platform/python-services"
                if production
                else "workspaces/decision-platform/research/s1-4r-jax-risk"
            ),
            "testCount": 1344 if production else 263,
            "exitCode": 0,
            "reportSha256": "5" * 64,
            "status": "PASS",
        }
    if evidence_id == "oci-correctness":
        return {
            "schemaVersion": schema,
            "candidate": candidate,
            "benchmarkSubjectCommit": commit,
            "networkMode": "none",
            "containerExitCode": 0,
            "comparisonMismatchCount": 0,
            "resultSha256": "6" * 64,
            "comparisonSha256": "7" * 64,
            "status": "PASS",
        }
    if evidence_id == "toolchain-reproducibility":
        binary_sha256 = (
            {
                "scalaCli": FROZEN_SCALA_CLI_SHA256,
                "scalafix": FROZEN_SCALAFIX_SHA256,
            }
            if candidate == "scala"
            else {
                "ghcup": FROZEN_GHCUP_SHA256,
                "stack": FROZEN_STACK_SHA256,
            }
        )
        return {
            "schemaVersion": schema,
            "candidate": candidate,
            "benchmarkSubjectCommit": commit,
            "toolchainLockSha256": "8" * 64,
            "selectedProfileId": "A" if candidate == "scala" else "baseline-o0-fasm",
            "selectedProfileSha256": "9" * 64,
            "mergedToolchainProvenanceSha256": (
                FROZEN_MERGED_TOOLCHAIN_PROVENANCE_SHA256
            ),
            "binarySha256": binary_sha256,
            "status": "PASS",
        }
    if evidence_id == "fixture-reproducibility":
        return {
            "schemaVersion": schema,
            "candidate": candidate,
            "benchmarkSubjectCommit": commit,
            "contractManifestSha256": FROZEN_CONTRACT_MANIFEST_SHA256,
            "referenceLockSha256": FROZEN_REFERENCE_LOCK_SHA256,
            "fixtureSha256": {
                "canonicalInputs": (
                    "367ca5b8589f9c5b16e8c2e5dd2cfe7cc560a7de19559551c487beeefcf62e63"
                ),
                "propertySeeds": (
                    "4502fb577ea2e2283612059f755e5e77d01d1f2a46ac4ec50dae61c8789a78fb"
                ),
                "canonicalResults": (
                    "59619337c415757a612bc923b32e2a174018b15dc67b2357b10ccb602fca1b6a"
                ),
            },
            "deterministicReplayCount": 2,
            "mismatchCount": 0,
            "status": "PASS",
        }
    if evidence_id == "offline-runtime-reproducibility":
        return {
            "schemaVersion": schema,
            "candidate": candidate,
            "benchmarkSubjectCommit": commit,
            "networkMode": "none",
            "dependencyResolveMode": "offline",
            "containerExitCode": 0,
            "resultSha256": "a" * 64,
            "toolchainLockSha256": "8" * 64,
            "status": "PASS",
        }
    return {
        "schemaVersion": schema,
        "candidate": candidate,
        "benchmarkSubjectCommit": commit,
        "rubricId": evidence_id,
        "reviewedArtifacts": [
            {
                "path": reviewed_path,
                "sha256": reviewed_sha256,
            }
        ],
        "findings": [],
        "status": "PASS",
    }


class FinalCandidateAuditTests(TestCase):
    def setUp(self) -> None:
        self.temporary = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.audit_root = self.temporary / "audit"
        self.evidence_root = self.audit_root / "evidence"
        self.source_root = self.audit_root / "sources"
        self.evidence_root.mkdir(parents=True)
        self.source_root.mkdir()
        self.commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO,
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()
        for candidate in CANDIDATES:
            candidate_root = self.evidence_root / candidate
            candidate_source_root = self.source_root / candidate
            candidate_root.mkdir()
            candidate_source_root.mkdir()
            reviewed = candidate_source_root / "reviewed-source.txt"
            reviewed.write_text(
                f"{candidate} reviewed source closure\n",
                encoding="utf-8",
            )
            for evidence_id, claims in EXPECTED_EVIDENCE_CLAIMS.items():
                source_artifacts = []
                for role, source_schema in EXPECTED_SOURCE_CONTRACTS[evidence_id]:
                    source = candidate_source_root / f"{evidence_id}-{role}.json"
                    source.write_text(
                        json.dumps(
                            _source_document(
                                candidate=candidate,
                                evidence_id=evidence_id,
                                role=role,
                                schema=source_schema,
                                commit=self.commit,
                                reviewed_path=str(reviewed.relative_to(self.audit_root)),
                                reviewed_sha256=_sha256(reviewed),
                            ),
                            sort_keys=True,
                        ),
                        encoding="utf-8",
                    )
                    source_artifacts.append(
                        {
                            "role": role,
                            "path": str(source.relative_to(self.audit_root)),
                            "sha256": _sha256(source),
                            "schemaVersion": source_schema,
                            "status": "PASS",
                        }
                    )
                envelope = candidate_root / f"{evidence_id}.json"
                envelope.write_text(
                    json.dumps(
                        {
                            "schemaVersion": ("s1.4x-final-candidate-audit-evidence-v1"),
                            "candidate": candidate,
                            "benchmarkSubjectCommit": self.commit,
                            "evidenceId": evidence_id,
                            "claims": claims,
                            "sourceArtifacts": source_artifacts,
                            "status": "PASS",
                        },
                        sort_keys=True,
                    ),
                    encoding="utf-8",
                )
        self.ledger_path = self.audit_root / "final-candidate-audit.json"

    def _generate(self) -> dict[str, object]:
        generate_final_candidate_audit(
            repository_root=REPO,
            benchmark_subject_commit=self.commit,
            evidence_root=self.evidence_root,
            output_path=self.ledger_path,
        )
        return json.loads(self.ledger_path.read_text(encoding="utf-8"))

    def _rewrite_ledger(self, document: dict[str, object]) -> None:
        self.ledger_path.write_text(
            json.dumps(document, sort_keys=True),
            encoding="utf-8",
        )

    def test_generator_derives_only_fixed_full_points_from_typed_pass(self) -> None:
        ledger = self._generate()
        validated, derived, ledger_sha256 = validate_final_candidate_audit(
            self.ledger_path,
            repository_root=REPO,
            benchmark_subject_commit=self.commit,
        )
        self.assertEqual(validated, ledger)
        self.assertEqual(ledger_sha256, _sha256(self.ledger_path))
        for candidate in CANDIDATES:
            self.assertEqual(
                derived[candidate],
                {
                    "correctnessPoints": 35.0,
                    "purityAuditabilityPoints": 20.0,
                    "reproducibilityPoints": 15.0,
                    "maintainabilityPoints": 10.0,
                    "integrationFitPoints": 5.0,
                    "evidenceSha256": [
                        item["sha256"] for item in ledger["candidates"][candidate]["evidence"]
                    ],
                },
            )
            self.assertNotIn(
                "purityAuditabilityPoints",
                ledger["candidates"][candidate],
            )

    def test_arbitrary_points_missing_evidence_and_stale_sha_fail_closed(
        self,
    ) -> None:
        baseline = self._generate()
        invalid_variants = {}
        arbitrary_points = copy.deepcopy(baseline)
        arbitrary_points["candidates"]["scala"]["purityAuditabilityPoints"] = 19
        arbitrary_points["candidates"]["scala"]["maintainabilityPoints"] = 9
        arbitrary_points["candidates"]["scala"]["integrationFitPoints"] = 4
        invalid_variants["arbitrary-points"] = arbitrary_points

        missing = copy.deepcopy(baseline)
        missing["candidates"]["scala"]["evidence"].pop()
        invalid_variants["missing-evidence"] = missing

        stale = copy.deepcopy(baseline)
        stale["candidates"]["haskell"]["evidence"][0]["sha256"] = "0" * 64
        invalid_variants["stale-sha"] = stale

        absolute = copy.deepcopy(baseline)
        absolute["candidates"]["scala"]["evidence"][0]["path"] = str(
            (
                self.evidence_root / "scala" / f"{next(iter(EXPECTED_EVIDENCE_CLAIMS))}.json"
            ).resolve()
        )
        invalid_variants["absolute-path"] = absolute

        traversal = copy.deepcopy(baseline)
        traversal["candidates"]["scala"]["evidence"][0]["path"] = "../evidence/scala/forged.json"
        invalid_variants["traversal-path"] = traversal

        for label, invalid in invalid_variants.items():
            with self.subTest(label=label):
                self._rewrite_ledger(invalid)
                with self.assertRaises(FinalAuditError):
                    validate_final_candidate_audit(
                        self.ledger_path,
                        repository_root=REPO,
                        benchmark_subject_commit=self.commit,
                    )

    def test_forged_status_symlink_and_wrong_subject_fail_closed(self) -> None:
        baseline = self._generate()
        first = baseline["candidates"]["scala"]["evidence"][0]
        envelope_path = self.audit_root / first["path"]
        envelope = json.loads(envelope_path.read_text(encoding="utf-8"))
        source_path = self.audit_root / envelope["sourceArtifacts"][0]["path"]
        source = json.loads(source_path.read_text(encoding="utf-8"))
        source["status"] = "FAIL"
        source_path.write_text(
            json.dumps(source, sort_keys=True),
            encoding="utf-8",
        )
        envelope["sourceArtifacts"][0]["sha256"] = _sha256(source_path)
        envelope_path.write_text(
            json.dumps(envelope, sort_keys=True),
            encoding="utf-8",
        )
        first["sha256"] = _sha256(envelope_path)
        self._rewrite_ledger(baseline)
        with self.assertRaises(FinalAuditError):
            validate_final_candidate_audit(
                self.ledger_path,
                repository_root=REPO,
                benchmark_subject_commit=self.commit,
            )

        source["status"] = "PASS"
        source_path.write_text(
            json.dumps(source, sort_keys=True),
            encoding="utf-8",
        )
        envelope["sourceArtifacts"][0]["sha256"] = _sha256(source_path)
        envelope_path.write_text(
            json.dumps(envelope, sort_keys=True),
            encoding="utf-8",
        )
        first["sha256"] = _sha256(envelope_path)
        symlink = self.audit_root / "forged-link.json"
        symlink.symlink_to(envelope_path)
        first["path"] = symlink.name
        self._rewrite_ledger(baseline)
        with self.assertRaises(FinalAuditError):
            validate_final_candidate_audit(
                self.ledger_path,
                repository_root=REPO,
                benchmark_subject_commit=self.commit,
            )

        with self.assertRaises(FinalAuditError):
            validate_final_candidate_audit(
                self.ledger_path,
                repository_root=REPO,
                benchmark_subject_commit="0" * 40,
            )

    def test_generic_self_signed_pass_source_cannot_score(self) -> None:
        evidence_id = "oci-correctness"
        envelope_path = self.evidence_root / "scala" / f"{evidence_id}.json"
        envelope = json.loads(envelope_path.read_text(encoding="utf-8"))
        source_entry = envelope["sourceArtifacts"][0]
        source_path = self.audit_root / source_entry["path"]
        source_path.write_text(
            json.dumps(
                {
                    "schemaVersion": "s1.4x-fake-pass-result-v1",
                    "candidate": "scala",
                    "benchmarkSubjectCommit": self.commit,
                    "status": "PASS",
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        source_entry["schemaVersion"] = "s1.4x-fake-pass-result-v1"
        source_entry["sha256"] = _sha256(source_path)
        envelope_path.write_text(
            json.dumps(envelope, sort_keys=True),
            encoding="utf-8",
        )
        with self.assertRaises(FinalAuditError):
            self._generate()

    def test_intermediate_symlink_component_fails_closed(self) -> None:
        baseline = self._generate()
        first = baseline["candidates"]["scala"]["evidence"][0]
        envelope_path = self.audit_root / first["path"]
        envelope = json.loads(envelope_path.read_text(encoding="utf-8"))
        source_entry = envelope["sourceArtifacts"][0]
        original_source = self.audit_root / source_entry["path"]
        linked_sources = self.audit_root / "linked-sources"
        linked_sources.symlink_to(self.source_root, target_is_directory=True)
        source_entry["path"] = str(
            Path(linked_sources.name)
            / original_source.relative_to(self.source_root)
        )
        envelope_path.write_text(
            json.dumps(envelope, sort_keys=True),
            encoding="utf-8",
        )
        first["sha256"] = _sha256(envelope_path)
        self._rewrite_ledger(baseline)
        with self.assertRaises(FinalAuditError):
            validate_final_candidate_audit(
                self.ledger_path,
                repository_root=REPO,
                benchmark_subject_commit=self.commit,
            )

    def test_generator_rejects_missing_required_artifact(self) -> None:
        missing = self.evidence_root / "scala" / f"{next(iter(EXPECTED_EVIDENCE_CLAIMS))}.json"
        missing.unlink()
        with self.assertRaises(FinalAuditError):
            generate_final_candidate_audit(
                repository_root=REPO,
                benchmark_subject_commit=self.commit,
                evidence_root=self.evidence_root,
                output_path=self.ledger_path,
            )
