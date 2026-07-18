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
    FinalAuditError,
    generate_final_candidate_audit,
    validate_final_candidate_audit,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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
            for evidence_id, claims in EXPECTED_EVIDENCE_CLAIMS.items():
                source = candidate_source_root / f"{evidence_id}.json"
                source_schema = f"s1.4x-{evidence_id}-result-v1"
                source.write_text(
                    json.dumps(
                        {
                            "schemaVersion": source_schema,
                            "candidate": candidate,
                            "benchmarkSubjectCommit": self.commit,
                            "status": "PASS",
                        },
                        sort_keys=True,
                    ),
                    encoding="utf-8",
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
                            "sourceArtifacts": [
                                {
                                    "path": str(source.relative_to(self.audit_root)),
                                    "sha256": _sha256(source),
                                    "schemaVersion": source_schema,
                                    "status": "PASS",
                                }
                            ],
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
