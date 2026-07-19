"""Raw correctness closure에서 최종 후보 감사 evidence를 조립하는 경계를 검증한다."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any
from unittest import TestCase

INTEGRATION = Path(__file__).resolve().parents[1]
S1_4X = INTEGRATION.parent
sys.path.insert(0, str(INTEGRATION))

from assemble_final_candidate_evidence import (  # noqa: E402
    DESELECTED_RESEARCH_NODE,
    REPLACEMENT_RESEARCH_NODES,
    EvidenceAssemblyError,
    assemble_final_candidate_evidence,
)
from final_candidate_audit import (  # noqa: E402
    CANDIDATES,
    EXPECTED_EVIDENCE_CLAIMS,
    generate_final_candidate_audit,
    validate_final_candidate_audit,
)


def _canonical(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_canonical(value))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git(repository: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return completed.stdout.strip()


def _coverage_candidate(candidate: str) -> dict[str, Any]:
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
            "startedAt": "2026-07-19T00:00:00Z",
            "finishedAt": "2026-07-19T00:01:00Z",
        },
        "status": "PASS",
    }


class AssembleFinalCandidateEvidenceTests(TestCase):
    def setUp(self) -> None:
        self.temporary = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.repository = self.temporary / "repository"
        self.repository.mkdir()
        _git(self.repository, "init", "-b", "main")
        _git(self.repository, "config", "user.email", "audit@example.invalid")
        _git(self.repository, "config", "user.name", "S1.4X Audit Test")
        destination = (
            self.repository
            / "workspaces/decision-platform/research/s1-4x-numeric-parity"
        )
        copied = (
            "contract/contract-manifest.v1.json",
            "contract/reference-lock.v1.json",
            "contract/toolchain-provenance.v1.json",
            "contract/fixtures/small/canonical-inputs.v1.json",
            "contract/fixtures/property/property-seeds.v1.json",
            "contract/fixtures/expected/canonical-results.v1.json",
            "scala/toolchain-lock.v1.json",
            "haskell/toolchain-lock.v1.json",
        )
        for relative in copied:
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(S1_4X / relative, target)
        _write_json(
            destination / "haskell/selected-profile.v1.json",
            {
                "schemaVersion": "s1.4x-haskell-selected-profile-v1",
                "profileId": "baseline-o0-fasm",
                "ghcOptions": ["-O0", "-fasm"],
                "compilerVersion": "9.10.3",
                "compilerSha256": "d" * 64,
                "sourceTreeSha256": "1" * 64,
                "optionsSha256": "2" * 64,
                "fullCorrectnessSha256": "3" * 64,
                "qualificationPlanSha256": "4" * 64,
                "qualificationArtifactSha256": "5" * 64,
                "selectorConfigSha256": "6" * 64,
                "fallbackProfile": "baseline-o0-fasm",
                "selectedBy": "proven-fallback",
            },
        )
        _git(self.repository, "add", ".")
        _git(self.repository, "commit", "-m", "test subject")
        self.commit = _git(self.repository, "rev-parse", "HEAD")
        self.raw = self.temporary / "raw-correctness"
        self.raw.mkdir()
        self._materialize_raw_closure()
        self.output = self.temporary / "audit"

    @property
    def numeric_root(self) -> Path:
        return (
            self.repository
            / "workspaces/decision-platform/research/s1-4x-numeric-parity"
        )

    def _materialize_raw_closure(self) -> None:
        coverage = {
            "schemaVersion": "s1.4x-integration-coverage-v1",
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
        _write_json(self.raw / "coverage/integration-coverage.json", coverage)
        for matrix, request_id in (
            ("canonical", "s1.4x-canonical-small-v1"),
            ("semantic", "s1.4x-semantic-errors-v1"),
        ):
            _write_json(
                self.raw
                / f"cross-language/{matrix}/comparison-report.json",
                {
                    "schemaVersion": "s1.4x-comparison-report-v1",
                    "requestId": request_id,
                    "implementationCount": 2,
                    "mismatchCount": 0,
                    "mismatches": [],
                    "status": "PASS",
                },
            )
        _write_json(
            self.raw / "oci/cross-language-comparison.json",
            {
                "schemaVersion": "s1.4x-comparison-report-v1",
                "requestId": "s1.4x-oci-canonical-v1",
                "implementationCount": 2,
                "mismatchCount": 0,
                "mismatches": [],
                "status": "PASS",
            },
        )
        _write_json(
            self.raw / "contract-validation.json",
            {
                "schemaVersion": "s1.4x-contract-validation-v1",
                "status": "PASS",
                "checkAll": True,
                "functionCount": 20,
                "errorCodeCount": 32,
                "propertyCount": 25,
                "binaryManifestCount": 4,
                "referenceSourceCount": 1,
                "referenceSourceTreeCount": 4,
                "contractManifestFileCount": 96,
            },
        )
        _write_json(
            self.raw / "large-fixture-receipt.json",
            {
                "schemaVersion": (
                    "s1.4x-large-fixture-materialization-receipt-v1"
                ),
                "generatorSha256": "7" * 64,
                "materializedRootPathId": "S1_4X_LARGE_FIXTURE_ROOT",
                "manifestEntries": [
                    {
                        "path": f"large/fixture-{index}.manifest.json",
                        "sha256": f"{index + 1:x}" * 64,
                        "byteLength": 100 + index,
                    }
                    for index in range(4)
                ],
                "payloadEntries": [
                    {
                        "manifestPath": f"large/fixture-{index}.manifest.json",
                        "path": f"large/generated/fixture-{index}.f64le",
                        "sha256": f"{index + 5:x}" * 64,
                        "byteLength": 800 + index,
                    }
                    for index in range(4)
                ],
                "fixtureTreeSha256": "b" * 64,
                "status": "PASS",
            },
        )
        self._materialize_regression_receipts()
        self._materialize_candidate_raw()
        self._write_run_manifest()

    def _materialize_regression_receipts(self) -> None:
        specifications = {
            "production": {
                "project": "workspaces/decision-platform/python-services",
                "counts": (1344, 1344, 0, 0, 1344),
                "deselected": [],
                "replacements": [],
                "roles": ("ruff", "mypy", "pytest"),
            },
            "research": {
                "project": (
                    "workspaces/decision-platform/research/s1-4r-jax-risk"
                ),
                "counts": (263, 262, 1, 2, 264),
                "deselected": [DESELECTED_RESEARCH_NODE],
                "replacements": list(REPLACEMENT_RESEARCH_NODES),
                "roles": (
                    "ruff",
                    "mypy",
                    "replacement-pytest",
                    "base-pytest",
                ),
            },
        }
        for label, specification in specifications.items():
            commands = []
            for role in specification["roles"]:
                stdout = self.raw / f"regression/logs/{label}-{role}.stdout"
                stderr = self.raw / f"regression/logs/{label}-{role}.stderr"
                stdout.parent.mkdir(parents=True, exist_ok=True)
                stdout.write_text(f"{label} {role} PASS\n", encoding="utf-8")
                stderr.write_bytes(b"")
                commands.append(
                    {
                        "role": role,
                        "exitCode": 0,
                        "stdoutPath": stdout.relative_to(self.raw).as_posix(),
                        "stdoutSha256": _sha256(stdout),
                        "stderrPath": stderr.relative_to(self.raw).as_posix(),
                        "stderrSha256": _sha256(stderr),
                        "status": "PASS",
                    }
                )
            (
                collected,
                base_passed,
                deselected,
                replacement_passed,
                total_executed,
            ) = specification["counts"]
            _write_json(
                self.raw
                / f"regression/{label}-compound-receipt.v1.json",
                {
                    "schemaVersion": (
                        "s1.4x-regression-compound-receipt-v1"
                    ),
                    "benchmarkSubjectCommit": self.commit,
                    "project": specification["project"],
                    "collectedCount": collected,
                    "basePassedCount": base_passed,
                    "deselectedCount": deselected,
                    "replacementPassedCount": replacement_passed,
                    "totalExecutedPassedCount": total_executed,
                    "deselectedNodeIds": specification["deselected"],
                    "replacementNodeIds": specification["replacements"],
                    "commands": commands,
                    "status": "PASS",
                },
            )

    def _materialize_candidate_raw(self) -> None:
        scala_lock = self.numeric_root / "scala/toolchain-lock.v1.json"
        scala_correctness = self.raw / (
            "scala/profiles/A/scala-profile-correctness-result.v1.json"
        )
        _write_json(
            scala_correctness,
            {
                "schemaVersion": "s1.4x-scala-profile-correctness-v1",
                "profileId": "A",
                "toolchainLockSha256": _sha256(scala_lock),
                "scalaCliBinarySha256": (
                    "54b93b8401e333095526da5e4853780d5bf37494baa1ba5486e9e643084253d0"
                ),
                "mismatchCount": 0,
                "status": "PASS",
            },
        )
        _write_json(
            self.raw / "scala/scala-selected-profile-result.v1.json",
            {
                "schemaVersion": "s1.4x-scala-selected-profile-result-v1",
                "selectedProfileId": "A",
                "selectionStatus": "PASS",
                "toolchainLockSha256": _sha256(scala_lock),
                "mergedToolchainProvenanceSha256": (
                    "cd9e29a22473fba6203daa4f3a0cbaa57b8b6e5c5fc22de05ca0801c404ffa98"
                ),
                "scalaCliBinarySha256": (
                    "54b93b8401e333095526da5e4853780d5bf37494baa1ba5486e9e643084253d0"
                ),
                "correctnessResultSha256": _sha256(scala_correctness),
            },
        )
        _write_json(
            self.raw / "scala/scala-source-policy-result.v1.json",
            {
                "schemaVersion": "s1.4x-scala-source-policy-result-v1",
                "checkerMode": "semanticdb",
                "semanticSmokeStatus": "PASS",
                "checkedFiles": ["src/main/scala/Core.scala"],
                "violations": [],
                "staleAllowlistEntries": [],
                "sourceSetExact": True,
                "aggregateStatus": "PASS",
            },
        )
        _write_json(
            self.raw / "scala/scala-dependency-edge-result.v1.json",
            {
                "schemaVersion": (
                    "s1.4x-scala-dependency-native-edge-result-v1"
                ),
                "candidateAddedNativeDependencyCount": 0,
                "candidateAuthoredEdgeCount": 0,
                "candidateCoreDirectNativeBindingCallCount": 0,
                "candidateCoreDirectNativeBindingImportCount": 0,
                "timedKernelExplicitCandidateNativeInteropCallCount": 0,
                "unknownEdgeCount": 0,
                "forbiddenSourceFindings": [],
                "aggregateStatus": "PASS",
            },
        )
        _write_json(
            self.raw / "scala/scalafmt/scala-scalafmt-idempotence-result.v1.json",
            {
                "schemaVersion": (
                    "s1.4x-scala-scalafmt-idempotence-result-v1"
                ),
                "checkedFiles": ["src/main/scala/Core.scala"],
                "copiedNonMutatingCheck": {
                    "exitCode": 0,
                    "downloadLineCount": 0,
                    "portableArgv": ["SCALA_CLI_1_15_0", "--offline", "--check"],
                    "portableArgvSha256": "8" * 64,
                    "evidenceSha256": "9" * 64,
                },
                "status": "PASS",
            },
        )
        _write_json(
            self.raw / "scala/scalafix/scala-semantic-policy-receipt.v1.json",
            {
                "schemaVersion": "s1.4x-scala-semantic-policy-receipt-v1",
                "checkerMode": "semanticdb",
                "semanticSmokeStatus": "PASS",
                "checkedFiles": ["src/main/scala/Core.scala"],
                "status": "PASS",
            },
        )
        _write_json(
            self.raw / "scala/hard-compiler-A/scala-hard-compiler-result.v1.json",
            {
                "schemaVersion": "s1.4x-scala-hard-compiler-result-v1",
                "profileId": "A",
                "compileInputPaths": ["src/main/scala/Core.scala"],
                "aggregateStatus": "PASS",
            },
        )
        _write_json(
            self.raw / "oci/scala/scala-oci-build-result.v1.json",
            {
                "schemaVersion": "s1.4x-scala-oci-build-result-v2",
                "baseImageReference": (
                    "docker.io/library/eclipse-temurin@sha256:" + "1" * 64
                ),
                "candidateSha256": "2" * 64,
                "buildNetwork": "none",
                "pull": False,
                "aggregateStatus": "PASS",
            },
        )
        _write_json(
            self.raw
            / "oci/scala/runtime/scala-oci-correctness-result.v1.json",
            {
                "schemaVersion": "s1.4x-scala-oci-correctness-result-v2",
                "runtimeNetwork": "none",
                "readOnlyRoot": True,
                "capabilitiesDropped": "ALL",
                "sourceTreeMounted": False,
                "userHomeMounted": False,
                "credentialMounted": False,
                "mismatchCount": 0,
                "aggregateStatus": "PASS",
            },
        )

        haskell_profile = self.numeric_root / "haskell/selected-profile.v1.json"
        _write_json(
            self.raw
            / "haskell/profiles/baseline-o0-fasm/correctness-receipt.v1.json",
            {
                "schemaVersion": "s1.4x-haskell-full-correctness-v1",
                "candidateSourceCommit": self.commit,
                "profileId": "baseline-o0-fasm",
                "mismatchCount": 0,
                "status": "PASS",
            },
        )
        _write_json(
            self.raw
            / "haskell/module-safety/haskell-module-safety-result.v1.json",
            {
                "schemaVersion": "s1.4x-haskell-module-safety-result-v1",
                "modules": [{"moduleName": "S14X.Core", "category": "safe-scalar"}],
                "candidateDirectImports": [],
                "candidateHomeModuleEdges": [],
                "upstreamTransitiveEdges": [{"allowlisted": True}],
                "unclassifiedModuleCount": 0,
                "candidateTrustworthyUnsafeDeclarationCount": 0,
                "candidateDirectUnsafeIoForeignImportCount": 0,
                "coreToShellEdgeCount": 0,
                "unknownTransitiveEdgeCount": 0,
                "staleAllowlistCount": 0,
                "aggregateStatus": "PASS",
            },
        )
        _write_json(
            self.raw / "haskell/format/receipt.json",
            {
                "schemaVersion": "s1.4x-haskell-format-evidence-v1",
                "sourceInputFileCount": 1,
                "positiveExitCode": 0,
                "misformattedExitCode": 1,
                "parserCapabilityStatus": (
                    "PINNED_PARSER_COMPATIBILITY_FALLBACK"
                ),
                "status": "PASS",
            },
        )
        _write_json(
            self.raw / "haskell/hlint/receipt.json",
            {
                "schemaVersion": "s1.4x-haskell-hlint-evidence-v1",
                "sourceInputFileCount": 1,
                "negativeFixtureCount": 12,
                "status": "PASS",
            },
        )
        _write_json(
            self.raw / "oci/haskell/oci-correctness-receipt.v1.json",
            {
                "schemaVersion": "s1.4x-haskell-oci-correctness-v1",
                "candidateSourceCommit": self.commit,
                "selectedProfileSha256": _sha256(haskell_profile),
                "profileId": "baseline-o0-fasm",
                "buildNetwork": "none",
                "runtimeNetwork": "none",
                "runtimeMounts": ["output-only"],
                "comparisons": [
                    {
                        "matrixId": matrix,
                        "mismatchCount": 0,
                        "status": "PASS",
                    }
                    for matrix in ("canonical", "semantic")
                ],
                "mismatchCount": 0,
                "status": "PASS",
            },
        )

    def _write_run_manifest(self) -> None:
        manifest = self.raw / "correctness-run-manifest.v1.json"
        manifest.unlink(missing_ok=True)
        artifacts = []
        for path in sorted(
            (item for item in self.raw.rglob("*") if item.is_file()),
            key=lambda item: item.relative_to(self.raw).as_posix().encode(),
        ):
            artifacts.append(
                {
                    "path": path.relative_to(self.raw).as_posix(),
                    "sha256": _sha256(path),
                    "sizeBytes": path.stat().st_size,
                }
            )
        _write_json(
            manifest,
            {
                "schemaVersion": "s1.4x-correctness-run-manifest-v1",
                "benchmarkSubjectCommit": self.commit,
                "artifactCount": len(artifacts),
                "artifacts": artifacts,
                "status": "PASS",
            },
        )

    def _assemble(self, output: Path | None = None) -> dict[str, Any]:
        return assemble_final_candidate_evidence(
            repository_root=self.repository,
            benchmark_subject_commit=self.commit,
            correctness_root=self.raw,
            production_regression_receipt=Path(
                "regression/production-compound-receipt.v1.json"
            ),
            research_regression_receipt=Path(
                "regression/research-compound-receipt.v1.json"
            ),
            output_root=output or self.output,
        )

    def test_assembles_exact_deterministic_closure_and_existing_audit_accepts(
        self,
    ) -> None:
        first = self._assemble()
        second_root = self.temporary / "audit-second"
        second = self._assemble(second_root)
        self.assertEqual(first, second)
        for candidate in CANDIDATES:
            envelopes = sorted((self.output / "evidence" / candidate).glob("*.json"))
            self.assertEqual(len(envelopes), 20)
            self.assertEqual(
                {path.stem for path in envelopes},
                set(EXPECTED_EVIDENCE_CLAIMS),
            )
            reference_count = sum(
                len(json.loads(path.read_text(encoding="utf-8"))["sourceArtifacts"])
                for path in envelopes
            )
            self.assertEqual(reference_count, 22)
        first_tree = {
            path.relative_to(self.output): path.read_bytes()
            for path in self.output.rglob("*")
            if path.is_file()
        }
        second_tree = {
            path.relative_to(second_root): path.read_bytes()
            for path in second_root.rglob("*")
            if path.is_file()
        }
        self.assertEqual(first_tree, second_tree)

        ledger = self.output / "final-candidate-audit.json"
        generate_final_candidate_audit(
            repository_root=self.repository,
            benchmark_subject_commit=self.commit,
            evidence_root=self.output / "evidence",
            output_path=ledger,
        )
        document, derived, _ = validate_final_candidate_audit(
            ledger,
            repository_root=self.repository,
            benchmark_subject_commit=self.commit,
        )
        self.assertEqual(document["status"], "PASS")
        self.assertEqual(set(derived), set(CANDIDATES))

    def test_rejects_research_count_and_exact_node_drift(self) -> None:
        receipt = self.raw / "regression/research-compound-receipt.v1.json"
        baseline = json.loads(receipt.read_text(encoding="utf-8"))
        mutations = {
            "collected": ("collectedCount", 262),
            "base-passed": ("basePassedCount", 263),
            "deselected": ("deselectedNodeIds", ["wrong::node"]),
            "replacement": ("replacementNodeIds", ["wrong::one", "wrong::two"]),
            "total": ("totalExecutedPassedCount", 263),
        }
        for label, (field, value) in mutations.items():
            with self.subTest(label=label):
                changed = dict(baseline)
                changed[field] = value
                _write_json(receipt, changed)
                self._write_run_manifest()
                with self.assertRaises(EvidenceAssemblyError):
                    self._assemble(self.temporary / f"audit-{label}")
        _write_json(receipt, baseline)
        self._write_run_manifest()

    def test_rejects_subject_manifest_path_and_symlink_drift(self) -> None:
        with self.assertRaises(EvidenceAssemblyError):
            assemble_final_candidate_evidence(
                repository_root=self.repository,
                benchmark_subject_commit="0" * 40,
                correctness_root=self.raw,
                production_regression_receipt=Path(
                    "regression/production-compound-receipt.v1.json"
                ),
                research_regression_receipt=Path(
                    "regression/research-compound-receipt.v1.json"
                ),
                output_root=self.temporary / "wrong-subject",
            )
        for label, invalid in (
            ("absolute", self.raw / "regression/production-compound-receipt.v1.json"),
            ("traversal", Path("../production-compound-receipt.v1.json")),
        ):
            with self.subTest(label=label), self.assertRaises(
                EvidenceAssemblyError
            ):
                assemble_final_candidate_evidence(
                    repository_root=self.repository,
                    benchmark_subject_commit=self.commit,
                    correctness_root=self.raw,
                    production_regression_receipt=invalid,
                    research_regression_receipt=Path(
                        "regression/research-compound-receipt.v1.json"
                    ),
                    output_root=self.temporary / f"bad-{label}",
                )
        linked = self.raw / "coverage/linked-coverage.json"
        linked.symlink_to(self.raw / "coverage/integration-coverage.json")
        self._write_run_manifest()
        with self.assertRaises(EvidenceAssemblyError):
            self._assemble(self.temporary / "symlink")

    def test_rejects_existing_output_and_generic_self_signed_rubric_pass(
        self,
    ) -> None:
        self.output.mkdir()
        with self.assertRaises(EvidenceAssemblyError):
            self._assemble()
        self.output.rmdir()

        raw = self.raw / "haskell/module-safety/haskell-module-safety-result.v1.json"
        _write_json(
            raw,
            {
                "schemaVersion": "s1.4x-generic-pass-v1",
                "status": "PASS",
            },
        )
        self._write_run_manifest()
        with self.assertRaises(EvidenceAssemblyError):
            self._assemble()

    def test_rejects_formatter_execution_contract_drift(self) -> None:
        scala_receipt = (
            self.raw
            / "scala/scalafmt/scala-scalafmt-idempotence-result.v1.json"
        )
        haskell_receipt = self.raw / "haskell/format/receipt.json"
        scala_baseline = json.loads(scala_receipt.read_text(encoding="utf-8"))
        haskell_baseline = json.loads(haskell_receipt.read_text(encoding="utf-8"))
        mutations = (
            (
                "scala-exit",
                scala_receipt,
                {
                    **scala_baseline,
                    "copiedNonMutatingCheck": {
                        **scala_baseline["copiedNonMutatingCheck"],
                        "exitCode": 1,
                    },
                },
            ),
            (
                "scala-download",
                scala_receipt,
                {
                    **scala_baseline,
                    "copiedNonMutatingCheck": {
                        **scala_baseline["copiedNonMutatingCheck"],
                        "downloadLineCount": 1,
                    },
                },
            ),
            (
                "haskell-positive",
                haskell_receipt,
                {**haskell_baseline, "positiveExitCode": 1},
            ),
            (
                "haskell-negative",
                haskell_receipt,
                {**haskell_baseline, "misformattedExitCode": 0},
            ),
            (
                "haskell-parser",
                haskell_receipt,
                {**haskell_baseline, "parserCapabilityStatus": "UNVERIFIED"},
            ),
        )
        for label, receipt, changed in mutations:
            with self.subTest(label=label):
                _write_json(receipt, changed)
                self._write_run_manifest()
                with self.assertRaises(EvidenceAssemblyError):
                    self._assemble(self.temporary / f"formatter-{label}")
                _write_json(
                    receipt,
                    scala_baseline
                    if receipt == scala_receipt
                    else haskell_baseline,
                )
        self._write_run_manifest()
