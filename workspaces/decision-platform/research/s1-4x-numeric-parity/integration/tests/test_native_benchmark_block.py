"""Scala/Haskell native wrapper와 frozen block-result 사이의 공통 builder를 검증한다."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import statistics
import sys
import tempfile
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from typing import Any
from unittest import TestCase
from unittest.mock import patch

INTEGRATION = Path(__file__).resolve().parents[1]
BENCHMARKS = INTEGRATION.parent / "benchmarks"
FIXTURES = INTEGRATION.parent / "contract/fixtures"
sys.path.insert(0, str(INTEGRATION))
sys.path.insert(0, str(BENCHMARKS))

import native_benchmark_block as native_block_module  # noqa: E402
from executable_identity import inspect_regular_file_path  # noqa: E402
from gate import GateError, strict_json_load  # noqa: E402
from mark_benchmark_measurement import main as mark_measurement_main  # noqa: E402
from native_benchmark_block import (  # noqa: E402
    build_block_result,
    validate_native_contract_evidence,
)

PLAN = BENCHMARKS / "benchmark-plan.v1.json"
EFFECTIVE_RUNTIME_ARGUMENTS_SHA256 = "e" * 64


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


class NativeBenchmarkBlockTests(TestCase):
    def test_scala_effective_runtime_identity_binds_actual_case_receipts_in_order(
        self,
    ) -> None:
        case_ids = ["family/case-a", "family/case-b"]
        receipts = [
            {
                "caseId": "family/case-a",
                "runtimeArgvSha256": "1" * 64,
                "effectiveJvmArgsSha256": "2" * 64,
                "portableArgvSha256": "3" * 64,
            },
            {
                "caseId": "family/case-b",
                "runtimeArgvSha256": "4" * 64,
                "effectiveJvmArgsSha256": "5" * 64,
                "portableArgvSha256": "6" * 64,
            },
        ]
        expected = native_block_module._scala_effective_runtime_arguments_sha256(
            selector_id="scala/family",
            expected_case_ids=case_ids,
            profile_id="B",
            profile_options_sha256="7" * 64,
            case_receipts=receipts,
        )
        tampered_runtime = copy.deepcopy(receipts)
        tampered_runtime[0]["runtimeArgvSha256"] = "8" * 64
        self.assertNotEqual(
            native_block_module._scala_effective_runtime_arguments_sha256(
                selector_id="scala/family",
                expected_case_ids=case_ids,
                profile_id="B",
                profile_options_sha256="7" * 64,
                case_receipts=tampered_runtime,
            ),
            expected,
        )
        tampered_jvm = copy.deepcopy(receipts)
        tampered_jvm[1]["effectiveJvmArgsSha256"] = "9" * 64
        self.assertNotEqual(
            native_block_module._scala_effective_runtime_arguments_sha256(
                selector_id="scala/family",
                expected_case_ids=case_ids,
                profile_id="B",
                profile_options_sha256="7" * 64,
                case_receipts=tampered_jvm,
            ),
            expected,
        )
        with self.assertRaisesRegex(
            GateError,
            "SCALA_NATIVE_RUNTIME_RECEIPT_ORDER_INVALID",
        ):
            native_block_module._scala_effective_runtime_arguments_sha256(
                selector_id="scala/family",
                expected_case_ids=case_ids,
                profile_id="B",
                profile_options_sha256="7" * 64,
                case_receipts=list(reversed(receipts)),
            )

    def test_scala_artifact_identity_binds_source_profile_and_tool_bytes(self) -> None:
        closure = {
            "sourceTreeSha256": "1" * 64,
            "selectedProfileResultSha256": "2" * 64,
            "selectedProfileSourceSha256": "3" * 64,
            "sourceInputManifestSha256": "4" * 64,
            "compilerProfilesSha256": "5" * 64,
            "scalaCliBinarySha256": "6" * 64,
            "javaExecutableSha256": "7" * 64,
            "toolchainLockSha256": "8" * 64,
            "mergedToolchainProvenanceSha256": "9" * 64,
            "effectiveJvmArgumentsCapabilitySha256": "a" * 64,
        }
        expected = native_block_module._scala_artifact_closure_sha256(closure)
        for field in ("sourceTreeSha256", "scalaCliBinarySha256"):
            with self.subTest(field=field):
                substituted = dict(closure)
                substituted[field] = "b" * 64
                self.assertNotEqual(
                    native_block_module._scala_artifact_closure_sha256(
                        substituted
                    ),
                    expected,
                )
        invalid = dict(closure)
        invalid["unexpected"] = "c" * 64
        with self.assertRaisesRegex(
            GateError,
            "SCALA_NATIVE_ARTIFACT_CLOSURE_INVALID",
        ):
            native_block_module._scala_artifact_closure_sha256(invalid)

    def test_scala_source_manifest_and_executable_substitution_fail_closed(
        self,
    ) -> None:
        temporary = Path(self.enterContext(tempfile.TemporaryDirectory()))
        scala_root = temporary / "scala"
        files: dict[str, dict[str, str]] = {}
        for relative_path in native_block_module.SCALA_RUNTIME_SOURCE_PATHS:
            path = scala_root / relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                f"// frozen source: {relative_path}\n",
                encoding="utf-8",
            )
            role = (
                "configuration"
                if relative_path in {"project.scala", "selected-profile.scala"}
                else "benchmark"
                if relative_path.startswith("benchmarks/")
                else "main"
            )
            files[relative_path] = {
                "role": role,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        manifest = {
            "schemaVersion": "s1.4x-source-input-manifest-v1",
            "language": "scala",
            "files": files,
            "inputSets": native_block_module.SCALA_SOURCE_INPUT_SETS,
            "canonicalManifestSha256": hashlib.sha256(
                "".join(
                    f"{metadata['sha256']}  {path}\n"
                    for path, metadata in files.items()
                ).encode()
            ).hexdigest(),
        }
        native_block_module._validate_scala_source_manifest(
            manifest,
            scala_root=scala_root,
            error="SCALA_SOURCE_SUBSTITUTION",
        )
        substituted_source = (
            scala_root / native_block_module.SCALA_RUNTIME_SOURCE_PATHS[0]
        )
        substituted_source.write_text("// substituted\n", encoding="utf-8")
        with self.assertRaisesRegex(
            GateError,
            "SCALA_SOURCE_SUBSTITUTION",
        ):
            native_block_module._validate_scala_source_manifest(
                manifest,
                scala_root=scala_root,
                error="SCALA_SOURCE_SUBSTITUTION",
            )

        scala_cli = temporary / "scala-cli"
        java = temporary / "java"
        scala_cli.write_bytes(b"scala-cli frozen")
        java.write_bytes(b"java frozen")
        scala_cli.chmod(0o700)
        java.chmod(0o700)
        with (
            patch.object(
                native_block_module,
                "FROZEN_SCALA_CLI_SHA256",
                hashlib.sha256(scala_cli.read_bytes()).hexdigest(),
            ),
            patch.object(
                native_block_module,
                "FROZEN_JAVA_EXECUTABLE_SHA256",
                hashlib.sha256(java.read_bytes()).hexdigest(),
            ),
        ):
            native_block_module._validate_scala_executable_identities(
                scala_cli_path=scala_cli,
                java_executable_path=java,
            )
            scala_cli.write_bytes(b"scala-cli substituted")
            with self.assertRaisesRegex(
                GateError,
                "SCALA_NATIVE_SCALA_CLI_IDENTITY_INVALID",
            ):
                native_block_module._validate_scala_executable_identities(
                    scala_cli_path=scala_cli,
                    java_executable_path=java,
                )

    def test_scala_producer_cli_dispatches_exact_outer_options(self) -> None:
        producer_result = {
            "boundaryId": "scala",
            "selectorId": "scala/family",
            "caseCount": 2,
            "nativeContractValidationSha256": "1" * 64,
            "nativeReportSha256": "2" * 64,
            "nativeStatisticsSha256": "3" * 64,
            "status": "PASS",
        }
        argv = [
            "produce-scala-native",
            "--repo-root",
            "/repo",
            "--plan",
            "/repo/plan.json",
            "--block-dir",
            "/run/block",
            "--selector",
            "scala/family",
            "--scala-jmh-root",
            "/run/block/scala-jmh",
            "--input-ledger",
            "/run/block/input-ledger.json",
            "--fixture-root",
            "/repo/fixtures",
            "--selected-profile-result",
            "/evidence/selected.json",
            "--selected-profile-source",
            "/repo/scala/selected-profile.scala",
            "--source-input-manifest",
            "/repo/scala/source-inputs.v1.json",
            "--compiler-profiles",
            "/repo/scala/compiler-profiles.v1.json",
            "--toolchain-lock",
            "/repo/scala/toolchain-lock.v1.json",
            "--toolchain-provenance",
            "/repo/contract/toolchain-provenance.v1.json",
            "--jvm-argument-capability",
            "/evidence/jvm-allowlist.json",
            "--scala-cli",
            "/tools/scala-cli",
            "--java-executable",
            "/tools/java",
            "--started-at",
            "2026-07-18T00:00:00Z",
            "--finished-at",
            "2026-07-18T00:01:00Z",
        ]
        output = StringIO()
        with (
            patch.object(
                native_block_module,
                "produce_scala_native_evidence",
                return_value=producer_result,
            ) as producer,
            redirect_stdout(output),
        ):
            self.assertEqual(native_block_module.main(argv), 0)
        producer.assert_called_once()
        self.assertEqual(json.loads(output.getvalue()), producer_result)

    def test_scala_full_runtime_argv_uses_exact_22_source_order_and_checksums(
        self,
    ) -> None:
        scala_root = Path("/repo/numeric/scala")
        raw_path = Path("/run/block/scala-jmh/case-001/native.json")
        argv = native_block_module._scala_full_runtime_argv(
            scala_cli=Path("/tools/scala-cli"),
            scala_root=scala_root,
            source_paths=list(native_block_module.SCALA_RUNTIME_SOURCE_PATHS),
            scala_cli_arguments=["--scalac-option=-opt"],
            raw_path=raw_path,
            jmh_include_regex=r"^s1_4x\.benchmarks\.family\.Benchmark\.benchmark$",
        )
        self.assertEqual(len(native_block_module.SCALA_RUNTIME_SOURCE_PATHS), 22)
        source_start = 3
        source_end = source_start + 22
        self.assertEqual(
            argv[source_start:source_end],
            [
                str(scala_root / relative)
                for relative in native_block_module.SCALA_RUNTIME_SOURCE_PATHS
            ],
        )
        self.assertEqual(
            argv[source_end : source_end + 5],
            [
                "--server=false",
                "--jvm",
                "system",
                "--coursier-validate-checksums",
                "--scalac-option=-opt",
            ],
        )

    def test_native_json_snapshot_keeps_digest_and_payload_on_one_descriptor(
        self,
    ) -> None:
        temporary = Path(self.enterContext(tempfile.TemporaryDirectory()))
        real_inspector = inspect_regular_file_path

        for role in ("criterion-raw", "execution-receipt"):
            with self.subTest(role=role):
                source = temporary / f"{role}.json"
                replacement = temporary / f"{role}.replacement.json"
                original = {"identity": "original", "role": role}
                swapped = {"identity": "swapped", "role": role}
                source.write_text(json.dumps(original), encoding="utf-8")
                original_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()
                replacement.write_text(json.dumps(swapped), encoding="utf-8")

                def inspect_then_swap(path: Path, *, role: str) -> Any:
                    snapshot = real_inspector(path, role=role)
                    os.replace(replacement, source)
                    return snapshot

                with patch.object(
                    native_block_module,
                    "inspect_regular_file_path",
                    side_effect=inspect_then_swap,
                ):
                    snapshot, document = native_block_module._snapshot_json_file(
                        source,
                        role=role,
                        error=f"{role.upper()}_SNAPSHOT_INVALID",
                    )

                self.assertEqual(snapshot.sha256, original_sha256)
                self.assertEqual(document, original)
                self.assertEqual(strict_json_load(source), swapped)

    def test_plan_snapshot_validation_rejects_path_substitution_and_symlinks(
        self,
    ) -> None:
        temporary = Path(self.enterContext(tempfile.TemporaryDirectory()))
        plan_path = temporary / "benchmark-plan.v1.json"
        plan_path.write_bytes(PLAN.read_bytes())
        snapshot, original = native_block_module._snapshot_json_file(
            plan_path,
            role="plan-aba-regression",
            error="PLAN_SNAPSHOT_INVALID",
        )
        replacement = temporary / "replacement.json"
        replacement.write_text('{"forged":true}', encoding="utf-8")
        os.replace(replacement, plan_path)
        validated = native_block_module._validate_plan_snapshot(
            snapshot,
            error="PLAN_SNAPSHOT_INVALID",
        )
        self.assertEqual(validated, original)
        self.assertEqual(strict_json_load(plan_path), {"forged": True})

        target = temporary / "target.json"
        target.write_text('{"status":"PASS"}', encoding="utf-8")
        symlink = temporary / "symlink.json"
        symlink.symlink_to(target)
        with self.assertRaisesRegex(GateError, "SYMLINK_INPUT_INVALID"):
            native_block_module._snapshot_json_file(
                symlink,
                role="symlink-input",
                error="SYMLINK_INPUT_INVALID",
            )

    def test_haskell_native_batch_seconds_are_normalized_from_frozen_ops(self) -> None:
        plan = strict_json_load(PLAN)
        selector = next(
            item
            for item in plan["familySelectors"]
            if item["selectorId"] == "haskell/probabilistic-scalar"
        )
        case_by_id = {case["caseId"]: case for case in plan["cases"]}
        native = {
            "schemaVersion": "s1.4x-candidate-native-benchmark-v1",
            "boundaryId": "haskell",
            "selectorId": selector["selectorId"],
            "nativeBenchmarkMode": "Criterion",
            "nativeTimeUnit": "s",
            "profile": "baseline-o0-fasm",
            "artifactSha256": "1" * 64,
            "sourceTreeSha256": "2" * 64,
            "toolchainLockSha256": "3" * 64,
            "effectiveRuntimeArgumentsSha256": "4" * 64,
            "inputLedgerSha256": "9" * 64,
            "nativeContractValidationSha256": "b" * 64,
            "startedAt": "2026-07-18T00:00:00Z",
            "finishedAt": "2026-07-18T00:01:00Z",
            "cases": [
                {
                    "caseId": case_id,
                    "nativeValue": (
                        case_by_id[case_id]["logicalOperationsPerInvocation"]
                        * 1_000.0
                        / 1_000_000_000.0
                    ),
                    "samples": 100,
                    "warmupIterations": 0,
                    "measurementIterations": 100,
                }
                for case_id in selector["expectedCaseIds"]
            ],
            "status": "PASS",
        }
        qualification = {
            "schemaVersion": "s1.4x-timeout-qualification-v1",
            "phase": "MEASUREMENT",
            "measurementEntered": True,
            "subject": {"benchmarkSubjectCommit": "a" * 40},
            "run": {
                "runId": "run-001",
                "rotationId": "R1",
                "outerRepetition": 1,
            },
            "hostValidity": {
                "sha256": "5" * 64,
                "portableHostIdSha256": "6" * 64,
            },
        }
        report = build_block_result(
            plan=plan,
            native=native,
            qualification=qualification,
            family_id="probabilistic-scalar",
            rotation_id="R1",
            outer_repetition=1,
            run_id="run-001",
            benchmark_subject_commit="a" * 40,
            native_report_sha256="7" * 64,
            toolchain_provenance_sha256="8" * 64,
            actual_affinity_cpu_set=[0],
        )
        self.assertEqual(
            [case["normalizedNsPerLogicalOperation"] for case in report["cases"]],
            [1_000.0, 1_000.0],
        )
        self.assertEqual(
            report["block"]["nativeReportPath"],
            "run-001/R1/haskell/probabilistic-scalar/native.json",
        )

        invalid = copy.deepcopy(native)
        invalid["unexpected"] = True
        with self.assertRaisesRegex(GateError, "CANDIDATE_NATIVE_DOCUMENT_INVALID"):
            build_block_result(
                plan=plan,
                native=invalid,
                qualification=qualification,
                family_id="probabilistic-scalar",
                rotation_id="R1",
                outer_repetition=1,
                run_id="run-001",
                benchmark_subject_commit="a" * 40,
                native_report_sha256="7" * 64,
                toolchain_provenance_sha256="8" * 64,
                actual_affinity_cpu_set=[0],
            )

    def test_haskell_contract_evidence_binds_criterion_config_and_raw_bytes(
        self,
    ) -> None:
        temporary = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (temporary / "raw").mkdir()
        (temporary / "receipts").mkdir()
        haskell_root = temporary / "haskell"
        haskell_root.mkdir()
        contract_root = temporary / "contract"
        contract_root.mkdir()
        contract_schema_root = contract_root / "schemas"
        contract_schema_root.mkdir()
        merged_provenance = contract_root / "toolchain-provenance.v1.json"
        merged_provenance.write_bytes(
            (PLAN.parent.parent / "contract/toolchain-provenance.v1.json").read_bytes()
        )
        merged_provenance_schema = (
            contract_schema_root / "toolchain-provenance.schema.json"
        )
        merged_provenance_schema.write_bytes(
            (
                PLAN.parent.parent
                / "contract/schemas/toolchain-provenance.schema.json"
            ).read_bytes()
        )
        input_ledger = temporary / "input-ledger.json"
        input_ledger.write_text('{"status":"fixture"}', encoding="utf-8")
        benchmark_executable = (
            haskell_root
            / ".stack-work/dist/x86_64-linux/ghc-9.10.3/build/"
            "s1-4x-haskell-benchmark/s1-4x-haskell-benchmark"
        )
        benchmark_executable.parent.mkdir(parents=True)
        benchmark_executable.write_bytes(b"criterion executable fixture")
        benchmark_executable.chmod(0o700)
        ghcup = temporary / "ghcup"
        ghcup.write_bytes(b"ghcup fixture")
        stack = temporary / "stack"
        stack.write_bytes(b"stack fixture")
        authoritative_ghc = temporary / "ghc-9.10.3"
        authoritative_ghc.write_bytes(b"authoritative ghc fixture")
        authoritative_ghc.chmod(0o700)
        authoritative_ghc_sha256 = hashlib.sha256(
            authoritative_ghc.read_bytes()
        ).hexdigest()
        self.enterContext(
            patch.object(
                native_block_module,
                "FROZEN_GHC_910_SHA256",
                authoritative_ghc_sha256,
            )
        )
        for candidate_root in ("src", "app", "test", "benchmark"):
            (haskell_root / candidate_root).mkdir()
        candidate_sources = {
            "app/Main.hs": "module Main where\nmain = pure ()\n",
            "benchmark/Main.hs": "module BenchmarkMain where\nbenchmark = 1\n",
            "src/Core.hs": "module Core where\nvalue = 1\n",
            "test/Spec.hs": "module Spec where\nspec = True\n",
        }
        for relative_path, payload in candidate_sources.items():
            (haskell_root / relative_path).write_text(payload, encoding="utf-8")
        package_yaml = haskell_root / "package.yaml"
        package_yaml.write_text("name: native-evidence-fixture\n", encoding="utf-8")
        cabal_file = haskell_root / "s1-4x-haskell.cabal"
        cabal_file.write_text("name: native-evidence-fixture\n", encoding="utf-8")
        stack_yaml = haskell_root / "stack.yaml"
        stack_yaml.write_text("resolver: ghc-9.10.3\n", encoding="utf-8")
        stack_lock = haskell_root / "stack.yaml.lock"
        stack_lock.write_text("snapshots: []\npackages: []\n", encoding="utf-8")
        selected_options = ["-O0", "-fasm"]
        effective_options_sha256 = _canonical_sha256(selected_options)
        source_tree_paths = [
            *candidate_sources,
            "package.yaml",
            "s1-4x-haskell.cabal",
            "stack.yaml",
            "stack.yaml.lock",
        ]
        source_tree_sha256 = _canonical_sha256(
            [
                {
                    "path": relative_path,
                    "sha256": hashlib.sha256(
                        (haskell_root / relative_path).read_bytes()
                    ).hexdigest(),
                }
                for relative_path in sorted(source_tree_paths, key=str.encode)
            ]
        )
        selected_profile = haskell_root / "selected-profile.v1.json"
        selected_profile.write_text(
            json.dumps(
                {
                    "schemaVersion": "s1.4x-haskell-selected-profile-v1",
                    "profileId": "baseline-o0-fasm",
                    "ghcOptions": selected_options,
                    "compilerVersion": "9.10.3",
                    "compilerSha256": authoritative_ghc_sha256,
                    "sourceTreeSha256": source_tree_sha256,
                    "optionsSha256": effective_options_sha256,
                    "fullCorrectnessSha256": "6" * 64,
                    "qualificationPlanSha256": hashlib.sha256(
                        json.dumps(
                            strict_json_load(PLAN),
                            sort_keys=True,
                        ).encode()
                    ).hexdigest(),
                    "qualificationArtifactSha256": "7" * 64,
                    "selectorConfigSha256": _canonical_sha256(
                        strict_json_load(PLAN)["haskellProfileQualification"]
                    ),
                    "fallbackProfile": "baseline-o0-fasm",
                    "selectedBy": "frozen-criterion-selector",
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        source_input_manifest = haskell_root / "source-inputs.v1.json"
        source_manifest_paths = [
            *candidate_sources,
            "package.yaml",
            "selected-profile.v1.json",
        ]
        source_manifest_files = {
            relative_path: {
                "role": (
                    "configuration"
                    if relative_path in {"package.yaml", "selected-profile.v1.json"}
                    else "test"
                    if relative_path.startswith("test/")
                    else "benchmark"
                    if relative_path.startswith("benchmark/")
                    else "main"
                ),
                "sha256": hashlib.sha256(
                    (haskell_root / relative_path).read_bytes()
                ).hexdigest(),
            }
            for relative_path in sorted(source_manifest_paths, key=str.encode)
        }
        source_input_manifest.write_text(
            json.dumps(
                {
                    "schemaVersion": "s1.4x-source-input-manifest-v1",
                    "language": "haskell",
                    "files": source_manifest_files,
                    "inputSets": {
                        "tracked": "files",
                        "manifest": "files",
                        "format": "files",
                        "compile": "files",
                        "lint": "files",
                        "profileRun": "files",
                    },
                    "canonicalManifestSha256": hashlib.sha256(
                        "".join(
                            f"{source_manifest_files[path]['sha256']}  {path}\n"
                            for path in sorted(source_manifest_files, key=str.encode)
                        ).encode()
                    ).hexdigest(),
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        plan = strict_json_load(PLAN)
        plan_path = temporary / "benchmarks/benchmark-plan.v1.json"
        plan_path.parent.mkdir()
        plan_path.write_text(json.dumps(plan, sort_keys=True), encoding="utf-8")
        marker_python = Path(sys.executable).resolve(strict=True)
        marker_script = plan_path.parent / "run_rotated_blocks.py"
        marker_script.write_text("# marker fixture\n", encoding="utf-8")
        qualification_path = temporary / "timeout-qualification.json"
        qualification_path.write_text('{"phase":"PRE_RUN"}', encoding="utf-8")
        marker_argv = [
            str(marker_python),
            str(marker_script.resolve()),
            "mark-measurement-entered",
            "--qualification",
            str(qualification_path.resolve()),
        ]
        ghcup_sha256 = hashlib.sha256(ghcup.read_bytes()).hexdigest()
        stack_sha256 = hashlib.sha256(stack.read_bytes()).hexdigest()
        self.enterContext(
            patch.object(
                native_block_module,
                "FROZEN_GHCUP_SHA256",
                ghcup_sha256,
            )
        )
        self.enterContext(
            patch.object(
                native_block_module,
                "FROZEN_STACK_SHA256",
                stack_sha256,
            )
        )
        toolchain_lock = haskell_root / "toolchain-lock.v1.json"
        merged_provenance_document = strict_json_load(merged_provenance)
        contract_projection = {
            field: merged_provenance_document[field]
            for field in native_block_module.TOOLCHAIN_PROJECTION_FIELDS
        }
        toolchain_lock.write_text(
            json.dumps(
                {
                    "schemaVersion": "s1.4x-haskell-toolchain-lock-v1",
                    "snapshot": "lts-24.50",
                    "mergedToolchainProvenance": {
                        "path": "contract/toolchain-provenance.v1.json",
                        "sha256": hashlib.sha256(
                            merged_provenance.read_bytes()
                        ).hexdigest(),
                        "schemaPath": (
                            "contract/schemas/toolchain-provenance.schema.json"
                        ),
                        "schemaSha256": hashlib.sha256(
                            merged_provenance_schema.read_bytes()
                        ).hexdigest(),
                    },
                    "contractProjection": contract_projection,
                    "resolvedTools": {
                        "ghcup": {
                            "pathId": "GHCUP_0_2_6_2_LINUX_X86_64",
                            "version": "0.2.6.2",
                            "sha256": ghcup_sha256,
                        },
                        "authoritativeGhc": {
                            "pathId": "GHCUP_GHC_9_10_3",
                            "version": "9.10.3",
                            "sha256": native_block_module.FROZEN_GHC_910_SHA256,
                        },
                        "compatibilityGhc": {
                            "pathId": "GHCUP_GHC_9_14_1",
                            "version": "9.14.1",
                            "sha256": native_block_module.FROZEN_GHC_914_SHA256,
                        },
                        "stack": {
                            "pathId": "GHCUP_STACK_3_11_1",
                            "version": "3.11.1",
                            "sha256": stack_sha256,
                        },
                        "hlint": {
                            "pathId": "HLINT_3_10",
                            "version": "3.10",
                            "sha256": native_block_module.FROZEN_HLINT_SHA256,
                        },
                        "stylishHaskell": {
                            "pathId": "STYLISH_HASKELL_0_15_1_0",
                            "version": "0.15.1.0",
                            "sha256": (
                                native_block_module.FROZEN_STYLISH_HASKELL_SHA256
                            ),
                        },
                    },
                    "resolverAssertions": {
                        "authoritativeGhc": [
                            "--offline",
                            "run",
                            "--quick",
                            "--ghc",
                            "9.10.3",
                            "--stack",
                            "3.11.1",
                            "--",
                            "ghc",
                            "--numeric-version",
                        ],
                        "authoritativeStack": [
                            "--offline",
                            "run",
                            "--quick",
                            "--ghc",
                            "9.10.3",
                            "--stack",
                            "3.11.1",
                            "--",
                            "stack",
                            "--numeric-version",
                        ],
                        "compatibilityGhc": [
                            "--offline",
                            "run",
                            "--quick",
                            "--ghc",
                            "9.14.1",
                            "--stack",
                            "3.11.1",
                            "--",
                            "ghc",
                            "--numeric-version",
                        ],
                    },
                    "compatibilityPlan": {},
                    "stackConfigurations": {},
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        selector = next(
            item
            for item in plan["familySelectors"]
            if item["selectorId"] == "haskell/probabilistic-scalar"
        )
        selector_id = selector["selectorId"]
        expected_case_ids = selector["expectedCaseIds"]
        runtime_identity = temporary / "benchmark-runtime-identity.json"
        benchmark_executable_sha256 = hashlib.sha256(
            benchmark_executable.read_bytes()
        ).hexdigest()
        runtime_identity_document = {
            "schemaVersion": "s1.4x-haskell-benchmark-runtime-identity-v1",
            "boundaryId": "haskell",
            "selectorId": selector_id,
            "executedBenchmarkPath": str(benchmark_executable.resolve()),
            "executedBenchmarkSha256": benchmark_executable_sha256,
            "status": "PASS",
        }
        runtime_identity.write_text(
            json.dumps(runtime_identity_document, sort_keys=True),
            encoding="utf-8",
        )
        receipt_provenance = {
            "planPath": str(plan_path.resolve()),
            "planSha256": hashlib.sha256(plan_path.read_bytes()).hexdigest(),
            "fixtureRootPath": str(FIXTURES.resolve()),
            "fixtureFreezeIdentitySha256": _canonical_sha256(plan["fixtureFreezeIdentity"]),
            "inputLedgerPath": str(input_ledger.resolve()),
            "inputLedgerSha256": hashlib.sha256(input_ledger.read_bytes()).hexdigest(),
            "selectorId": selector_id,
            "caseIds": expected_case_ids,
            "benchmarkExecutablePath": str(benchmark_executable.resolve()),
            "benchmarkExecutableSha256": benchmark_executable_sha256,
            "effectiveRuntimeArgumentsSha256": effective_options_sha256,
            "candidateProvenance": {
                "kind": "haskell",
                "selectedProfilePath": str(selected_profile.resolve()),
                "selectedProfileSha256": hashlib.sha256(selected_profile.read_bytes()).hexdigest(),
                "selectedProfileId": "baseline-o0-fasm",
                "sourceInputManifestPath": str(source_input_manifest.resolve()),
                "sourceInputManifestSha256": hashlib.sha256(
                    source_input_manifest.read_bytes()
                ).hexdigest(),
                "effectiveCompilerFlagsSha256": effective_options_sha256,
                "markerPythonPath": str(marker_python),
                "markerPythonSha256": hashlib.sha256(
                    marker_python.read_bytes()
                ).hexdigest(),
                "markerScriptPath": str(marker_script.resolve()),
                "markerScriptSha256": hashlib.sha256(
                    marker_script.read_bytes()
                ).hexdigest(),
                "markerArgv": marker_argv,
                "markerArgvSha256": _canonical_sha256(marker_argv),
                "ghcupPath": str(ghcup.resolve()),
                "ghcupSha256": hashlib.sha256(ghcup.read_bytes()).hexdigest(),
                "stackPath": str(stack.resolve()),
                "stackSha256": hashlib.sha256(stack.read_bytes()).hexdigest(),
                "stackYamlPath": str(stack_yaml.resolve()),
                "stackYamlSha256": hashlib.sha256(stack_yaml.read_bytes()).hexdigest(),
                "runtimeIdentityPath": str(runtime_identity.resolve()),
                "runtimeIdentitySha256": hashlib.sha256(
                    runtime_identity.read_bytes()
                ).hexdigest(),
                "executedBenchmarkPath": str(benchmark_executable.resolve()),
                "executedBenchmarkSha256": benchmark_executable_sha256,
                "authoritativeGhcPath": str(authoritative_ghc.resolve()),
                "authoritativeGhcSha256": authoritative_ghc_sha256,
                "selectedGhcOptions": selected_options,
                "toolchainLockPath": str(toolchain_lock.resolve()),
                "toolchainLockSha256": hashlib.sha256(toolchain_lock.read_bytes()).hexdigest(),
                "mergedToolchainProvenancePath": str(merged_provenance.resolve()),
                "mergedToolchainProvenanceSha256": hashlib.sha256(
                    merged_provenance.read_bytes()
                ).hexdigest(),
            },
        }
        cases: list[dict[str, Any]] = [
            {
                "caseId": expected_case_ids[0],
                "nativeValue": 0.1,
                "samples": 100,
                "warmupIterations": 0,
                "measurementIterations": 100,
            },
            {
                "caseId": expected_case_ids[1],
                "nativeValue": 0.2,
                "samples": 100,
                "warmupIterations": 0,
                "measurementIterations": 100,
            },
        ]
        evidence: dict[str, Any] = {
            "schemaVersion": "s1.4x-native-contract-validation-v1",
            "boundaryId": "haskell",
            "selectorId": selector_id,
            "framework": "Criterion",
            "frameworkVersion": "1.6.4.0",
            "configuration": {
                "benchmarkMode": "Criterion",
                "nativeTimeUnit": "s",
                "threads": 1,
                "timeLimitSeconds": 5,
                "rtsArguments": ["+RTS", "-N1", "-RTS"],
            },
            "cases": [],
            "status": "PASS",
        }
        statistics_cases: list[dict[str, Any]] = []
        raw_documents: dict[str, list[Any]] = {}
        for case_index, case in enumerate(cases):
            samples = [
                case["nativeValue"] * (0.9 if index % 2 == 0 else 1.1) for index in range(100)
            ]
            mean = statistics.fmean(samples)
            standard_deviation = statistics.stdev(samples)
            iteration_counts = [float(index + 1) for index in range(100)]
            elapsed_times = [
                sample * iterations
                for sample, iterations in zip(
                    samples,
                    iteration_counts,
                    strict=True,
                )
            ]
            iteration_mean = statistics.fmean(iteration_counts)
            elapsed_mean = statistics.fmean(elapsed_times)
            regression_slope = math.fsum(
                (iterations - iteration_mean) * (elapsed - elapsed_mean)
                for iterations, elapsed in zip(
                    iteration_counts,
                    elapsed_times,
                    strict=True,
                )
            ) / math.fsum((iterations - iteration_mean) ** 2 for iterations in iteration_counts)
            case["nativeValue"] = regression_slope
            estimate = {
                "estPoint": mean,
                "estError": {
                    "confIntLDX": mean * 0.1,
                    "confIntUDX": mean * 0.2,
                    "confIntCL": 0.05,
                },
            }
            raw_document: list[Any] = [
                "criterion",
                "1.6.4.0",
                [
                    {
                        "reportNumber": case_index,
                        "reportName": case["caseId"],
                        "reportKeys": [
                            "time",
                            "cpuTime",
                            "cycles",
                            "iters",
                            "allocated",
                            "peakMbAllocated",
                            "numGcs",
                            "bytesCopied",
                            "mutatorWallSeconds",
                            "mutatorCpuSeconds",
                            "gcWallSeconds",
                            "gcCpuSeconds",
                        ],
                        "reportMeasured": [
                            [
                                elapsed_times[index],
                                sample,
                                100,
                                index + 1,
                                None,
                                None,
                                None,
                                None,
                                None,
                                None,
                                None,
                                None,
                            ]
                            for index, sample in enumerate(samples)
                        ],
                        "reportAnalysis": {
                            "anRegress": [
                                {
                                    "regResponder": "cpuTime",
                                    "regCoeffs": {
                                        "iters": estimate,
                                        "y": estimate,
                                    },
                                    "regRSquare": {
                                        "estPoint": 1.0,
                                        "estError": {
                                            "confIntLDX": 0.0,
                                            "confIntUDX": 0.0,
                                            "confIntCL": 0.05,
                                        },
                                    },
                                },
                                {
                                    "regResponder": "time",
                                    "regCoeffs": {
                                        "iters": {
                                            "estPoint": regression_slope,
                                            "estError": {
                                                "confIntLDX": (regression_slope * 0.1),
                                                "confIntUDX": (regression_slope * 0.2),
                                                "confIntCL": 0.05,
                                            },
                                        },
                                        "y": estimate,
                                    },
                                    "regRSquare": {
                                        "estPoint": 1.0,
                                        "estError": {
                                            "confIntLDX": 0.0,
                                            "confIntUDX": 0.0,
                                            "confIntCL": 0.05,
                                        },
                                    },
                                },
                            ],
                            "anMean": estimate,
                            "anStdDev": {
                                "estPoint": standard_deviation,
                                "estError": {
                                    "confIntLDX": standard_deviation * 0.1,
                                    "confIntUDX": standard_deviation * 0.2,
                                    "confIntCL": 0.05,
                                },
                            },
                            "anOutlierVar": {
                                "ovEffect": "Unaffected",
                                "ovDesc": "no",
                                "ovFraction": 0.0,
                            },
                        },
                        "reportOutliers": {
                            "samplesSeen": 100,
                            "lowSevere": 0,
                            "lowMild": 0,
                            "highMild": 0,
                            "highSevere": 0,
                        },
                        "reportKDEs": [
                            {
                                "kdeType": "time",
                                "kdeValues": [min(samples), max(samples)],
                                "kdePDF": [1.0, 1.0],
                            }
                        ],
                    }
                ],
            ]
            raw_documents[case["caseId"]] = raw_document
            statistics_cases.append(
                {
                    "caseId": case["caseId"],
                    "nativeSampleCount": 100,
                    "nativeP95": max(samples),
                    "confidenceLevel": 0.95,
                    "confidenceLow": regression_slope * 0.9,
                    "confidenceHigh": regression_slope * 1.2,
                    "dispersionMetric": (
                        "criterion-bootstrap-standard-deviation-seconds-per-invocation"
                    ),
                    "dispersionValue": standard_deviation,
                    "nativeUnit": "s",
                    "logicalOperationsPerInvocation": next(
                        frozen["logicalOperationsPerInvocation"]
                        for frozen in plan["cases"]
                        if frozen["caseId"] == case["caseId"]
                    ),
                    "normalizedP95NsPerLogicalOperation": (
                        max(samples)
                        * 1e9
                        / next(
                            frozen["logicalOperationsPerInvocation"]
                            for frozen in plan["cases"]
                            if frozen["caseId"] == case["caseId"]
                        )
                    ),
                    "normalizedConfidenceLowNsPerLogicalOperation": (
                        regression_slope
                        * 0.9
                        * 1e9
                        / next(
                            frozen["logicalOperationsPerInvocation"]
                            for frozen in plan["cases"]
                            if frozen["caseId"] == case["caseId"]
                        )
                    ),
                    "normalizedConfidenceHighNsPerLogicalOperation": (
                        regression_slope
                        * 1.2
                        * 1e9
                        / next(
                            frozen["logicalOperationsPerInvocation"]
                            for frozen in plan["cases"]
                            if frozen["caseId"] == case["caseId"]
                        )
                    ),
                    "normalizedDispersionNsPerLogicalOperation": (
                        standard_deviation
                        * 1e9
                        / next(
                            frozen["logicalOperationsPerInvocation"]
                            for frozen in plan["cases"]
                            if frozen["caseId"] == case["caseId"]
                        )
                    ),
                }
            )

        family_raw_document: list[Any] = [
            "criterion",
            "1.6.4.0",
            [raw_documents[case_id][2][0] for case_id in expected_case_ids],
        ]
        raw_path = temporary / "raw/criterion-family.json"
        receipt_path = temporary / "receipts/criterion-family.json"
        raw_relative = "raw/criterion-family.json"
        receipt_relative = "receipts/criterion-family.json"
        raw_path.write_text(json.dumps(family_raw_document), encoding="utf-8")
        raw_sha = hashlib.sha256(raw_path.read_bytes()).hexdigest()
        receipt_document: dict[str, Any] = {
            "schemaVersion": "s1.4x-native-case-execution-receipt-v1",
            "boundaryId": "haskell",
            "selectorId": selector_id,
            "caseId": None,
            "commandArgv": [
                str(ghcup.resolve()),
                "--offline",
                "run",
                "--quick",
                "--ghc",
                "9.10.3",
                "--stack",
                "3.11.1",
                "--",
                str(stack.resolve()),
                "--stack-yaml",
                str(stack_yaml.resolve()),
                "--no-terminal",
                "--color",
                "never",
                "--system-ghc",
                "--no-install-ghc",
                "bench",
                "--ghc-options=-O0 -fasm",
                (
                    "--benchmark-arguments=--time-limit 5 "
                    f"--json {raw_path} --match prefix "
                    f"{selector['criterionPrefix']} +RTS -N1 -RTS"
                ),
            ],
            "environment": {"S1_4X_BENCHMARK_SELECTOR_ID": selector_id},
            "exitCode": 0,
            "rawEvidencePath": raw_relative,
            "rawEvidenceSha256": raw_sha,
            "provenance": receipt_provenance,
            "status": "PASS",
        }
        receipt_path.write_text(
            json.dumps(receipt_document, sort_keys=True),
            encoding="utf-8",
        )
        receipt_sha = hashlib.sha256(receipt_path.read_bytes()).hexdigest()
        evidence["cases"] = [
            {
                "caseId": case["caseId"],
                "nativeSampleCount": 100,
                "rawEvidencePath": raw_relative,
                "rawEvidenceSha256": raw_sha,
                "executionReceiptPath": receipt_relative,
                "executionReceiptSha256": receipt_sha,
                "status": "PASS",
            }
            for case in cases
        ]

        def install_family_raw(document: list[Any]) -> None:
            raw_path.write_text(json.dumps(document), encoding="utf-8")
            updated_raw_sha = hashlib.sha256(raw_path.read_bytes()).hexdigest()
            updated_receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            updated_receipt["rawEvidenceSha256"] = updated_raw_sha
            receipt_path.write_text(
                json.dumps(updated_receipt, sort_keys=True),
                encoding="utf-8",
            )
            updated_receipt_sha = hashlib.sha256(receipt_path.read_bytes()).hexdigest()
            for evidence_case in evidence["cases"]:
                evidence_case["rawEvidenceSha256"] = updated_raw_sha
                evidence_case["executionReceiptSha256"] = updated_receipt_sha

        validate_native_contract_evidence(
            evidence,
            boundary_id="haskell",
            selector_id=selector_id,
            block_directory=temporary,
            native_cases=cases,
            native_statistics_cases=statistics_cases,
            plan_path=plan_path,
            fixture_root_path=FIXTURES,
            input_ledger_path=input_ledger,
            effective_runtime_arguments_sha256=effective_options_sha256,
            profile="baseline-o0-fasm",
        )

        producer_stdout = StringIO()
        with (
            patch.object(
                native_block_module,
                "validate_input_ledger",
            ) as ledger_validator,
            redirect_stdout(producer_stdout),
        ):
            producer_exit = native_block_module.main(
                [
                    "produce-haskell-native",
                    "--repo-root",
                    str(temporary),
                    "--plan",
                    str(plan_path),
                    "--block-dir",
                    str(temporary),
                    "--selector",
                    selector_id,
                    "--criterion-raw",
                    str(raw_path),
                    "--execution-receipt",
                    str(receipt_path),
                    "--input-ledger",
                    str(input_ledger),
                    "--fixture-root",
                    str(FIXTURES),
                    "--selected-profile",
                    str(selected_profile),
                    "--source-input-manifest",
                    str(source_input_manifest),
                    "--toolchain-lock",
                    str(toolchain_lock),
                    "--toolchain-provenance",
                    str(merged_provenance),
                    "--benchmark-artifact",
                    str(benchmark_executable),
                    "--started-at",
                    "2026-07-18T00:00:00Z",
                    "--finished-at",
                    "2026-07-18T00:01:00Z",
                ]
            )
        self.assertEqual(producer_exit, 0)
        produced = json.loads(producer_stdout.getvalue())
        ledger_validator.assert_called_once()
        native_document = strict_json_load(temporary / "native.json")
        statistics_document = strict_json_load(temporary / "native-statistics.json")
        contract_document = strict_json_load(
            temporary / "native-contract-validation.json"
        )
        frozen_case_by_id = {case["caseId"]: case for case in plan["cases"]}
        self.assertEqual(produced["caseCount"], 2)
        self.assertEqual(
            [case["caseId"] for case in native_document["cases"]],
            expected_case_ids,
        )
        self.assertEqual(
            [case["nativeValue"] for case in native_document["cases"]],
            [case["nativeValue"] for case in cases],
        )
        self.assertEqual(
            [
                case["logicalOperationsPerInvocation"]
                for case in statistics_document["cases"]
            ],
            [
                frozen_case_by_id[case_id]["logicalOperationsPerInvocation"]
                for case_id in expected_case_ids
            ],
        )
        for statistics_case in statistics_document["cases"]:
            logical = statistics_case["logicalOperationsPerInvocation"]
            self.assertAlmostEqual(
                statistics_case["normalizedP95NsPerLogicalOperation"],
                statistics_case["nativeP95"] * 1e9 / logical,
            )
            self.assertAlmostEqual(
                statistics_case[
                    "normalizedConfidenceLowNsPerLogicalOperation"
                ],
                statistics_case["confidenceLow"] * 1e9 / logical,
            )
            self.assertAlmostEqual(
                statistics_case[
                    "normalizedConfidenceHighNsPerLogicalOperation"
                ],
                statistics_case["confidenceHigh"] * 1e9 / logical,
            )
            self.assertAlmostEqual(
                statistics_case["normalizedDispersionNsPerLogicalOperation"],
                statistics_case["dispersionValue"] * 1e9 / logical,
            )
        self.assertTrue(
            all(
                case["rawEvidenceSha256"] == raw_sha
                and case["executionReceiptSha256"] == receipt_sha
                for case in contract_document["cases"]
            )
        )
        self.assertEqual(
            native_document["sourceTreeSha256"],
            strict_json_load(selected_profile)["sourceTreeSha256"],
        )
        self.assertEqual(
            native_document["artifactSha256"],
            hashlib.sha256(benchmark_executable.read_bytes()).hexdigest(),
        )
        self.assertFalse((temporary / "block-result.json").exists())
        with self.assertRaisesRegex(
            GateError,
            "HASKELL_NATIVE_OUTPUT_ALREADY_EXISTS",
        ):
            native_block_module.produce_haskell_native_evidence(
                repo_root=temporary,
                plan_path=plan_path,
                block_directory=temporary,
                selector_id=selector_id,
                criterion_raw_path=raw_path,
                execution_receipt_path=receipt_path,
                input_ledger_path=input_ledger,
                fixture_root_path=FIXTURES,
                selected_profile_path=selected_profile,
                source_input_manifest_path=source_input_manifest,
                toolchain_lock_path=toolchain_lock,
                merged_toolchain_provenance_path=merged_provenance,
                benchmark_artifact_path=benchmark_executable,
                started_at="2026-07-18T00:00:00Z",
                finished_at="2026-07-18T00:01:00Z",
            )

        invalid = copy.deepcopy(evidence)
        invalid["configuration"]["timeLimitSeconds"] = 4
        with self.assertRaisesRegex(GateError, "NATIVE_CONTRACT_CONFIGURATION_INVALID"):
            validate_native_contract_evidence(
                invalid,
                boundary_id="haskell",
                selector_id=selector_id,
                block_directory=temporary,
                native_cases=cases,
                native_statistics_cases=statistics_cases,
                plan_path=plan_path,
                fixture_root_path=FIXTURES,
                input_ledger_path=input_ledger,
                effective_runtime_arguments_sha256=effective_options_sha256,
                profile="baseline-o0-fasm",
            )

        wrong_case_order = copy.deepcopy(family_raw_document)
        wrong_case_order[2][0]["reportName"], wrong_case_order[2][1]["reportName"] = (
            wrong_case_order[2][1]["reportName"],
            wrong_case_order[2][0]["reportName"],
        )
        install_family_raw(wrong_case_order)
        with self.assertRaisesRegex(GateError, "CRITERION_RAW_CASE_ORDER_INVALID"):
            validate_native_contract_evidence(
                evidence,
                boundary_id="haskell",
                selector_id=selector_id,
                block_directory=temporary,
                native_cases=cases,
                native_statistics_cases=statistics_cases,
                plan_path=plan_path,
                fixture_root_path=FIXTURES,
                input_ledger_path=input_ledger,
                effective_runtime_arguments_sha256=effective_options_sha256,
                profile="baseline-o0-fasm",
            )

        missing_bootstrap = copy.deepcopy(family_raw_document)
        missing_bootstrap[2][0]["reportAnalysis"]["anRegress"] = []
        install_family_raw(missing_bootstrap)
        with self.assertRaisesRegex(GateError, "CRITERION_RAW_CONTRACT_INVALID"):
            validate_native_contract_evidence(
                evidence,
                boundary_id="haskell",
                selector_id=selector_id,
                block_directory=temporary,
                native_cases=cases,
                native_statistics_cases=statistics_cases,
                plan_path=plan_path,
                fixture_root_path=FIXTURES,
                input_ledger_path=input_ledger,
                effective_runtime_arguments_sha256=effective_options_sha256,
                profile="baseline-o0-fasm",
            )

        install_family_raw(family_raw_document)
        forged_statistics = copy.deepcopy(statistics_cases)
        forged_statistics[0]["dispersionValue"] = (
            math.nextafter(
                forged_statistics[0]["dispersionValue"],
                math.inf,
            )
            * 2
        )
        with self.assertRaisesRegex(
            GateError,
            "CRITERION_NATIVE_STATISTICS_MISMATCH",
        ):
            validate_native_contract_evidence(
                evidence,
                boundary_id="haskell",
                selector_id=selector_id,
                block_directory=temporary,
                native_cases=cases,
                native_statistics_cases=forged_statistics,
                plan_path=plan_path,
                fixture_root_path=FIXTURES,
                input_ledger_path=input_ledger,
                effective_runtime_arguments_sha256=effective_options_sha256,
                profile="baseline-o0-fasm",
            )

        def install_receipt(document: dict[str, Any]) -> None:
            receipt_path.write_text(
                json.dumps(document, sort_keys=True),
                encoding="utf-8",
            )
            updated_receipt_sha = hashlib.sha256(receipt_path.read_bytes()).hexdigest()
            for evidence_case in evidence["cases"]:
                evidence_case["executionReceiptSha256"] = updated_receipt_sha

        original_raw_bytes = raw_path.read_bytes()
        original_receipt_bytes = receipt_path.read_bytes()
        for swapped_path, role_prefix in (
            (raw_path, "native-raw:"),
            (receipt_path, "native-execution-receipt:"),
        ):
            with self.subTest(same_fd_swap=swapped_path.name):
                swapped = False

                def inspect_then_swap(path: Path, *, role: str) -> Any:
                    nonlocal swapped
                    snapshot = inspect_regular_file_path(path, role=role)
                    if not swapped and path == swapped_path and role.startswith(role_prefix):
                        swapped_path.write_text('{"forged":true}', encoding="utf-8")
                        swapped = True
                    return snapshot

                with patch.object(
                    native_block_module,
                    "inspect_regular_file_path",
                    side_effect=inspect_then_swap,
                ):
                    validate_native_contract_evidence(
                        evidence,
                        boundary_id="haskell",
                        selector_id=selector_id,
                        block_directory=temporary,
                        native_cases=cases,
                        native_statistics_cases=statistics_cases,
                        plan_path=plan_path,
                        fixture_root_path=FIXTURES,
                        input_ledger_path=input_ledger,
                        effective_runtime_arguments_sha256=effective_options_sha256,
                        profile="baseline-o0-fasm",
                    )
                self.assertTrue(swapped)
                raw_path.write_bytes(original_raw_bytes)
                receipt_path.write_bytes(original_receipt_bytes)
                install_receipt(receipt_document)

        original_manifest_bytes = source_input_manifest.read_bytes()
        omitted_manifest = json.loads(original_manifest_bytes)
        omitted_manifest["files"].pop("app/Main.hs")
        omitted_manifest["canonicalManifestSha256"] = hashlib.sha256(
            "".join(
                f"{omitted_manifest['files'][path]['sha256']}  {path}\n"
                for path in sorted(omitted_manifest["files"], key=str.encode)
            ).encode()
        ).hexdigest()
        source_input_manifest.write_text(
            json.dumps(omitted_manifest, sort_keys=True),
            encoding="utf-8",
        )
        omitted_source_receipt = copy.deepcopy(receipt_document)
        omitted_source_receipt["provenance"]["candidateProvenance"][
            "sourceInputManifestSha256"
        ] = hashlib.sha256(source_input_manifest.read_bytes()).hexdigest()
        install_receipt(omitted_source_receipt)
        with self.assertRaisesRegex(
            GateError,
            "NATIVE_EXECUTION_PROVENANCE_INVALID",
        ):
            validate_native_contract_evidence(
                evidence,
                boundary_id="haskell",
                selector_id=selector_id,
                block_directory=temporary,
                native_cases=cases,
                native_statistics_cases=statistics_cases,
                plan_path=plan_path,
                fixture_root_path=FIXTURES,
                input_ledger_path=input_ledger,
                effective_runtime_arguments_sha256=effective_options_sha256,
                profile="baseline-o0-fasm",
            )
        source_input_manifest.write_bytes(original_manifest_bytes)
        install_receipt(receipt_document)

        source_path = haskell_root / "app/Main.hs"
        original_source_bytes = source_path.read_bytes()
        source_path.write_bytes(original_source_bytes + b"-- mutated\n")
        with self.assertRaisesRegex(
            GateError,
            "NATIVE_EXECUTION_PROVENANCE_INVALID",
        ):
            validate_native_contract_evidence(
                evidence,
                boundary_id="haskell",
                selector_id=selector_id,
                block_directory=temporary,
                native_cases=cases,
                native_statistics_cases=statistics_cases,
                plan_path=plan_path,
                fixture_root_path=FIXTURES,
                input_ledger_path=input_ledger,
                effective_runtime_arguments_sha256=effective_options_sha256,
                profile="baseline-o0-fasm",
            )
        source_path.write_bytes(original_source_bytes)

        original_cabal_bytes = cabal_file.read_bytes()
        cabal_file.write_bytes(original_cabal_bytes + b"-- mutated\n")
        with self.assertRaisesRegex(
            GateError,
            "NATIVE_EXECUTION_PROVENANCE_INVALID",
        ):
            validate_native_contract_evidence(
                evidence,
                boundary_id="haskell",
                selector_id=selector_id,
                block_directory=temporary,
                native_cases=cases,
                native_statistics_cases=statistics_cases,
                plan_path=plan_path,
                fixture_root_path=FIXTURES,
                input_ledger_path=input_ledger,
                effective_runtime_arguments_sha256=effective_options_sha256,
                profile="baseline-o0-fasm",
            )
        cabal_file.write_bytes(original_cabal_bytes)

        original_ghc_bytes = authoritative_ghc.read_bytes()
        for mutation in ("missing", "mutated"):
            with self.subTest(authoritative_ghc=mutation):
                if mutation == "missing":
                    authoritative_ghc.unlink()
                else:
                    authoritative_ghc.write_bytes(b"substituted GHC")
                    authoritative_ghc.chmod(0o700)
                with self.assertRaisesRegex(
                    GateError,
                    "NATIVE_EXECUTION_PROVENANCE_INVALID",
                ):
                    validate_native_contract_evidence(
                        evidence,
                        boundary_id="haskell",
                        selector_id=selector_id,
                        block_directory=temporary,
                        native_cases=cases,
                        native_statistics_cases=statistics_cases,
                        plan_path=plan_path,
                        fixture_root_path=FIXTURES,
                        input_ledger_path=input_ledger,
                        effective_runtime_arguments_sha256=effective_options_sha256,
                        profile="baseline-o0-fasm",
                    )
                authoritative_ghc.write_bytes(original_ghc_bytes)
                authoritative_ghc.chmod(0o700)

        substitute_artifact = temporary / "substitute-criterion-benchmark"
        substitute_artifact.write_bytes(b"substitute benchmark executable")
        substitute_artifact.chmod(0o700)
        substitute_sha256 = hashlib.sha256(
            substitute_artifact.read_bytes()
        ).hexdigest()
        substituted_identity = {
            **runtime_identity_document,
            "executedBenchmarkPath": str(substitute_artifact.resolve()),
            "executedBenchmarkSha256": substitute_sha256,
        }
        runtime_identity.write_text(
            json.dumps(substituted_identity, sort_keys=True),
            encoding="utf-8",
        )
        substituted_receipt = copy.deepcopy(receipt_document)
        substituted_provenance = substituted_receipt["provenance"]
        substituted_candidate = substituted_provenance["candidateProvenance"]
        substituted_provenance["benchmarkExecutablePath"] = str(
            substitute_artifact.resolve()
        )
        substituted_provenance["benchmarkExecutableSha256"] = substitute_sha256
        substituted_candidate["executedBenchmarkPath"] = str(
            substitute_artifact.resolve()
        )
        substituted_candidate["executedBenchmarkSha256"] = substitute_sha256
        substituted_candidate["runtimeIdentitySha256"] = hashlib.sha256(
            runtime_identity.read_bytes()
        ).hexdigest()
        install_receipt(substituted_receipt)
        with self.assertRaisesRegex(
            GateError,
            "NATIVE_EXECUTION_PROVENANCE_INVALID",
        ):
            validate_native_contract_evidence(
                evidence,
                boundary_id="haskell",
                selector_id=selector_id,
                block_directory=temporary,
                native_cases=cases,
                native_statistics_cases=statistics_cases,
                plan_path=plan_path,
                fixture_root_path=FIXTURES,
                input_ledger_path=input_ledger,
                effective_runtime_arguments_sha256=effective_options_sha256,
                profile="baseline-o0-fasm",
            )
        runtime_identity.write_text(
            json.dumps(runtime_identity_document, sort_keys=True),
            encoding="utf-8",
        )
        install_receipt(receipt_document)

        direct_executable_receipt = copy.deepcopy(receipt_document)
        direct_executable_receipt["commandArgv"] = [
            str(benchmark_executable.resolve()),
            "--json",
            str(raw_path),
        ]
        install_receipt(direct_executable_receipt)
        with self.assertRaisesRegex(GateError, "NATIVE_EXECUTION_ARGV_INVALID"):
            validate_native_contract_evidence(
                evidence,
                boundary_id="haskell",
                selector_id=selector_id,
                block_directory=temporary,
                native_cases=cases,
                native_statistics_cases=statistics_cases,
                plan_path=plan_path,
                fixture_root_path=FIXTURES,
                input_ledger_path=input_ledger,
                effective_runtime_arguments_sha256=effective_options_sha256,
                profile="baseline-o0-fasm",
            )

        forged_provenance_receipt = copy.deepcopy(receipt_document)
        forged_provenance_receipt["provenance"]["candidateProvenance"]["ghcupSha256"] = "0" * 64
        install_receipt(forged_provenance_receipt)
        with self.assertRaisesRegex(
            GateError,
            "NATIVE_EXECUTION_PROVENANCE_INVALID",
        ):
            validate_native_contract_evidence(
                evidence,
                boundary_id="haskell",
                selector_id=selector_id,
                block_directory=temporary,
                native_cases=cases,
                native_statistics_cases=statistics_cases,
                plan_path=plan_path,
                fixture_root_path=FIXTURES,
                input_ledger_path=input_ledger,
                effective_runtime_arguments_sha256=effective_options_sha256,
                profile="baseline-o0-fasm",
            )

        forged_marker_receipt = copy.deepcopy(receipt_document)
        forged_marker_receipt["provenance"]["candidateProvenance"][
            "markerScriptSha256"
        ] = "0" * 64
        install_receipt(forged_marker_receipt)
        with self.assertRaisesRegex(
            GateError,
            "NATIVE_EXECUTION_PROVENANCE_INVALID",
        ):
            validate_native_contract_evidence(
                evidence,
                boundary_id="haskell",
                selector_id=selector_id,
                block_directory=temporary,
                native_cases=cases,
                native_statistics_cases=statistics_cases,
                plan_path=plan_path,
                fixture_root_path=FIXTURES,
                input_ledger_path=input_ledger,
                effective_runtime_arguments_sha256=effective_options_sha256,
                profile="baseline-o0-fasm",
            )

        install_receipt(receipt_document)
        alternate_raw = temporary / "raw/alternate.json"
        alternate_raw.write_bytes(raw_path.read_bytes())
        alternate_receipt_path = temporary / "receipts/alternate.json"
        alternate_receipt = copy.deepcopy(receipt_document)
        alternate_receipt["rawEvidencePath"] = "raw/alternate.json"
        alternate_receipt["commandArgv"][-1] = (
            "--benchmark-arguments=--time-limit 5 "
            f"--json {alternate_raw} --match prefix "
            f"{selector['criterionPrefix']} +RTS -N1 -RTS"
        )
        alternate_receipt_path.write_text(
            json.dumps(alternate_receipt, sort_keys=True),
            encoding="utf-8",
        )
        shared_violation = copy.deepcopy(evidence)
        shared_violation["cases"][1]["rawEvidencePath"] = "raw/alternate.json"
        shared_violation["cases"][1]["executionReceiptPath"] = "receipts/alternate.json"
        shared_violation["cases"][1]["executionReceiptSha256"] = hashlib.sha256(
            alternate_receipt_path.read_bytes()
        ).hexdigest()
        with self.assertRaisesRegex(
            GateError,
            "CRITERION_FAMILY_EVIDENCE_NOT_SHARED",
        ):
            validate_native_contract_evidence(
                shared_violation,
                boundary_id="haskell",
                selector_id=selector_id,
                block_directory=temporary,
                native_cases=cases,
                native_statistics_cases=statistics_cases,
                plan_path=plan_path,
                fixture_root_path=FIXTURES,
                input_ledger_path=input_ledger,
                effective_runtime_arguments_sha256=effective_options_sha256,
                profile="baseline-o0-fasm",
            )

        substitute_ghcup = temporary / "substitute-ghcup"
        substitute_ghcup.write_bytes(b"substitute ghcup")
        substitute_receipt = copy.deepcopy(receipt_document)
        substitute_candidate = substitute_receipt["provenance"]["candidateProvenance"]
        substitute_candidate["ghcupPath"] = str(substitute_ghcup.resolve())
        substitute_candidate["ghcupSha256"] = hashlib.sha256(
            substitute_ghcup.read_bytes()
        ).hexdigest()
        substitute_receipt["commandArgv"][0] = str(substitute_ghcup.resolve())
        install_receipt(substitute_receipt)
        with self.assertRaisesRegex(
            GateError,
            "NATIVE_EXECUTION_PROVENANCE_INVALID",
        ):
            validate_native_contract_evidence(
                evidence,
                boundary_id="haskell",
                selector_id=selector_id,
                block_directory=temporary,
                native_cases=cases,
                native_statistics_cases=statistics_cases,
                plan_path=plan_path,
                fixture_root_path=FIXTURES,
                input_ledger_path=input_ledger,
                effective_runtime_arguments_sha256=effective_options_sha256,
                profile="baseline-o0-fasm",
            )

        tampered_lock = json.loads(toolchain_lock.read_text(encoding="utf-8"))
        tampered_lock["resolvedTools"]["stack"]["sha256"] = "0" * 64
        toolchain_lock.write_text(
            json.dumps(tampered_lock, sort_keys=True),
            encoding="utf-8",
        )
        tampered_lock_receipt = copy.deepcopy(receipt_document)
        tampered_lock_receipt["provenance"]["candidateProvenance"]["toolchainLockSha256"] = (
            hashlib.sha256(toolchain_lock.read_bytes()).hexdigest()
        )
        install_receipt(tampered_lock_receipt)
        with self.assertRaisesRegex(
            GateError,
            "NATIVE_EXECUTION_PROVENANCE_INVALID",
        ):
            validate_native_contract_evidence(
                evidence,
                boundary_id="haskell",
                selector_id=selector_id,
                block_directory=temporary,
                native_cases=cases,
                native_statistics_cases=statistics_cases,
                plan_path=plan_path,
                fixture_root_path=FIXTURES,
                input_ledger_path=input_ledger,
                effective_runtime_arguments_sha256=effective_options_sha256,
                profile="baseline-o0-fasm",
            )

    def test_scala_contract_parses_jmh_forks_iterations_and_score(self) -> None:
        temporary = Path(self.enterContext(tempfile.TemporaryDirectory()))
        raw = temporary / "raw/000.json"
        receipt = temporary / "receipts/000.json"
        raw.parent.mkdir()
        receipt.parent.mkdir()
        scala_root = temporary / "scala"
        (scala_root / "src/main/scala").mkdir(parents=True)
        (scala_root / "benchmarks").mkdir()
        contract_root = temporary / "contract"
        contract_root.mkdir()
        merged_provenance = contract_root / "toolchain-provenance.v1.json"
        merged_provenance.write_bytes(
            (PLAN.parent.parent / "contract/toolchain-provenance.v1.json").read_bytes()
        )
        project_scala = scala_root / "project.scala"
        project_scala.write_text("//> using scala 3.8.4\n", encoding="utf-8")
        scalafmt_config = scala_root / ".scalafmt.conf"
        scalafmt_config.write_text("version = 3.11.4\n", encoding="utf-8")
        selected_profile = scala_root / "selected-profile.scala"
        selected_profile.write_text(
            "// selected profile: profile-a\n",
            encoding="utf-8",
        )
        source_input_manifest = scala_root / "source-inputs.v1.json"
        source_input_manifest.write_text(
            '{"schemaVersion":"test-source-inputs-v1"}',
            encoding="utf-8",
        )
        input_ledger = temporary / "input-ledger.json"
        input_ledger.write_text('{"status":"fixture"}', encoding="utf-8")
        benchmark_executable = temporary / "scala-cli"
        benchmark_executable.write_bytes(b"Scala CLI fixture")
        scala_cli_sha256 = hashlib.sha256(benchmark_executable.read_bytes()).hexdigest()
        self.enterContext(
            patch.object(
                native_block_module,
                "FROZEN_SCALA_CLI_SHA256",
                scala_cli_sha256,
            )
        )
        toolchain_lock = scala_root / "toolchain-lock.v1.json"
        merged_provenance_document = strict_json_load(merged_provenance)
        scala_projection_fields = tuple(
            field
            for field in native_block_module.TOOLCHAIN_PROJECTION_FIELDS
            if field
            not in {
                "ghcupReleaseUri",
                "ghcupAssetUri",
                "upstreamStandaloneAssetUri",
            }
        )
        shared_distribution_provenance = {
            field: merged_provenance_document[field]
            for field in scala_projection_fields
        }
        toolchain_lock.write_text(
            json.dumps(
                {
                    "schemaVersion": "s1.4x-scala-toolchain-lock-v1",
                    "language": "scala",
                    "mergedToolchainProvenancePath": (
                        "workspaces/decision-platform/research/"
                        "s1-4x-numeric-parity/contract/"
                        "toolchain-provenance.v1.json"
                    ),
                    "mergedToolchainProvenanceSha256": hashlib.sha256(
                        merged_provenance.read_bytes()
                    ).hexdigest(),
                    "jdk": {
                        "javaHomePathId": "TEMURIN_25_0_3_9_LTS",
                        "implementor": "Eclipse Adoptium",
                        "runtimeVersion": "25.0.3+9-LTS",
                        "vmName": "OpenJDK 64-Bit Server VM",
                        "javaExecutableSha256": (
                            native_block_module.FROZEN_JAVA_EXECUTABLE_SHA256
                        ),
                    },
                    "scalaCli": {
                        "pathId": "SCALA_CLI_1_15_0",
                        "version": "1.15.0",
                        "binarySha256": scala_cli_sha256,
                        "defaultScalaVersion": "3.8.4",
                    },
                    "scala": {
                        "version": "3.8.4",
                        "projectPath": (
                            "workspaces/decision-platform/research/"
                            "s1-4x-numeric-parity/scala/project.scala"
                        ),
                        "projectSha256": hashlib.sha256(project_scala.read_bytes()).hexdigest(),
                    },
                    "scalafmt": {
                        "version": "3.11.4",
                        "configPath": (
                            "workspaces/decision-platform/research/"
                            "s1-4x-numeric-parity/scala/.scalafmt.conf"
                        ),
                        "configSha256": hashlib.sha256(scalafmt_config.read_bytes()).hexdigest(),
                        "runnerPathId": "SCALA_CLI_1_15_0",
                        "archiveUri": (
                            "https://github.com/scalameta/scalafmt/releases/download/"
                            "v3.11.4/scalafmt-x86_64-pc-linux.zip"
                        ),
                        "archivePathId": (
                            "S1_4X_CACHE_ROOT/coursier/https/github.com/scalameta/"
                            "scalafmt/releases/download/v3.11.4/"
                            "scalafmt-x86_64-pc-linux.zip"
                        ),
                        "archiveSha256": (
                            native_block_module.FROZEN_SCALAFMT_ARCHIVE_SHA256
                        ),
                        "executablePathId": (
                            "COURSIER_ARCHIVE_CACHE/https/github.com/scalameta/"
                            "scalafmt/releases/download/v3.11.4/"
                            "scalafmt-x86_64-pc-linux.zip/scalafmt"
                        ),
                        "executableSha256": (
                            native_block_module.FROZEN_SCALAFMT_EXECUTABLE_SHA256
                        ),
                        "resolvedVersionOutput": "scalafmt 3.11.4",
                        "resolutionLogUri": (
                            "evidence://s1-4x-scala-scalafmt-evidence-9c3cb8f-01/"
                            "logs/first-apply.stderr"
                        ),
                        "resolutionLogSha256": (
                            "1cc7516d57c230f10242f43884f12f3d26cbd6d681dbaed317262148c136b781"
                        ),
                        "networkPolicy": "OFFLINE_PINNED_LAUNCHER",
                    },
                    "scalafix": {
                        "pathId": "SCALAFIX_0_14_7",
                        "version": "0.14.7",
                        "binarySha256": (
                            "9db6db7359e580de8f4b72cd7c104d70023cf32a278db0c30aefb79c939eb0f3"
                        ),
                    },
                    "sharedDistributionProvenance": (
                        shared_distribution_provenance
                    ),
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        jvm_capability = temporary / "effective-jvm-arguments.json"
        jvm_capability.write_text(
            '{"schemaVersion":"test-effective-jvm-arguments-v1"}',
            encoding="utf-8",
        )
        plan = strict_json_load(PLAN)
        selector_id = "scala/test"
        jmh_include_regex = r"^s1_4x\.benchmarks\.test\.case_a$"
        plan["familySelectors"].append(
            {
                "boundaryId": "scala",
                "familyId": "test",
                "selectorId": selector_id,
                "expectedCaseIds": ["case-a"],
                "jmhIncludeRegex": jmh_include_regex,
                "criterionMatchMode": None,
                "criterionPrefix": None,
                "pythonFamilyId": None,
            }
        )
        plan_path = temporary / "benchmarks/benchmark-plan.v1.json"
        plan_path.parent.mkdir()
        plan_path.write_text(json.dumps(plan, sort_keys=True), encoding="utf-8")
        native_cases: list[dict[str, Any]] = [
            {
                "caseId": "case-a",
                "nativeValue": 12.0,
                "samples": 30,
                "warmupIterations": 5,
                "measurementIterations": 10,
            }
        ]
        statistics_cases: list[dict[str, Any]] = [
            {
                "caseId": "case-a",
                "nativeSampleCount": 30,
                "nativeP95": 12.0,
                "confidenceLevel": None,
                "confidenceLow": 11.0,
                "confidenceHigh": 13.0,
                "dispersionMetric": "p95-minus-median-ns-per-invocation",
                "dispersionValue": 0.0,
                "nativeUnit": "ns",
                "logicalOperationsPerInvocation": 1,
                "normalizedP95NsPerLogicalOperation": 12.0,
                "normalizedConfidenceLowNsPerLogicalOperation": 11.0,
                "normalizedConfidenceHighNsPerLogicalOperation": 13.0,
                "normalizedDispersionNsPerLogicalOperation": 0.0,
            }
        ]
        raw_document: list[dict[str, Any]] = [
            {
                "jmhVersion": "1.37",
                "benchmark": "s1_4x.benchmarks.test.case_a",
                "mode": "avgt",
                "threads": 1,
                "forks": 3,
                "warmupIterations": 5,
                "warmupTime": "1 s",
                "measurementIterations": 10,
                "measurementTime": "1 s",
                "primaryMetric": {
                    "score": 12.0,
                    "scoreConfidence": [11.0, 13.0],
                    "scoreUnit": "ns/op",
                    "rawData": [[12.0] * 10 for _ in range(3)],
                },
            }
        ]

        def evidence_for(document: list[dict[str, Any]]) -> dict[str, Any]:
            raw.write_text(json.dumps(document), encoding="utf-8")
            raw_sha = hashlib.sha256(raw.read_bytes()).hexdigest()
            receipt.write_text(
                json.dumps(
                    {
                        "schemaVersion": ("s1.4x-native-case-execution-receipt-v1"),
                        "boundaryId": "scala",
                        "selectorId": selector_id,
                        "caseId": "case-a",
                        "commandArgv": [
                            str(benchmark_executable.resolve()),
                            "--power",
                            "run",
                            str(project_scala.resolve()),
                            str(selected_profile.resolve()),
                            str((scala_root / "src/main/scala").resolve()),
                            str((scala_root / "benchmarks").resolve()),
                            "--server=false",
                            "--jvm",
                            "system",
                            "--jmh",
                            "--jmh-version",
                            "1.37",
                            "--",
                            "-bm",
                            "avgt",
                            "-tu",
                            "ns",
                            "-t",
                            "1",
                            "-f",
                            "3",
                            "-wi",
                            "5",
                            "-i",
                            "10",
                            "-w",
                            "1s",
                            "-r",
                            "1s",
                            "-rf",
                            "json",
                            "-rff",
                            str(raw),
                            jmh_include_regex,
                        ],
                        "environment": {"S1_4X_BENCHMARK_CASE_ID": "case-a"},
                        "exitCode": 0,
                        "rawEvidencePath": "raw/000.json",
                        "rawEvidenceSha256": raw_sha,
                        "provenance": {
                            "planPath": str(plan_path.resolve()),
                            "planSha256": hashlib.sha256(plan_path.read_bytes()).hexdigest(),
                            "fixtureRootPath": str(FIXTURES.resolve()),
                            "fixtureFreezeIdentitySha256": _canonical_sha256(
                                plan["fixtureFreezeIdentity"]
                            ),
                            "inputLedgerPath": str(input_ledger.resolve()),
                            "inputLedgerSha256": hashlib.sha256(
                                input_ledger.read_bytes()
                            ).hexdigest(),
                            "selectorId": selector_id,
                            "caseIds": ["case-a"],
                            "benchmarkExecutablePath": str(benchmark_executable.resolve()),
                            "benchmarkExecutableSha256": hashlib.sha256(
                                benchmark_executable.read_bytes()
                            ).hexdigest(),
                            "effectiveRuntimeArgumentsSha256": (EFFECTIVE_RUNTIME_ARGUMENTS_SHA256),
                            "candidateProvenance": {
                                "kind": "scala",
                                "selectedProfilePath": str(selected_profile.resolve()),
                                "selectedProfileSha256": hashlib.sha256(
                                    selected_profile.read_bytes()
                                ).hexdigest(),
                                "selectedProfileId": "profile-a",
                                "sourceInputManifestPath": str(source_input_manifest.resolve()),
                                "sourceInputManifestSha256": hashlib.sha256(
                                    source_input_manifest.read_bytes()
                                ).hexdigest(),
                                "toolchainLockPath": str(toolchain_lock.resolve()),
                                "toolchainLockSha256": hashlib.sha256(
                                    toolchain_lock.read_bytes()
                                ).hexdigest(),
                                "mergedToolchainProvenancePath": str(merged_provenance.resolve()),
                                "mergedToolchainProvenanceSha256": hashlib.sha256(
                                    merged_provenance.read_bytes()
                                ).hexdigest(),
                                "effectiveJvmArgumentsCapabilityPath": str(
                                    jvm_capability.resolve()
                                ),
                                "effectiveJvmArgumentsCapabilitySha256": (
                                    hashlib.sha256(jvm_capability.read_bytes()).hexdigest()
                                ),
                            },
                        },
                        "status": "PASS",
                    },
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
            return {
                "schemaVersion": "s1.4x-native-contract-validation-v1",
                "boundaryId": "scala",
                "selectorId": selector_id,
                "framework": "JMH",
                "frameworkVersion": "1.37",
                "configuration": {
                    "benchmarkMode": "AverageTime",
                    "nativeTimeUnit": "ns",
                    "threads": 1,
                    "forks": 3,
                    "warmupIterations": 5,
                    "warmupSeconds": 1,
                    "measurementIterations": 10,
                    "measurementSeconds": 1,
                },
                "cases": [
                    {
                        "caseId": "case-a",
                        "nativeSampleCount": 30,
                        "rawEvidencePath": "raw/000.json",
                        "rawEvidenceSha256": raw_sha,
                        "executionReceiptPath": "receipts/000.json",
                        "executionReceiptSha256": hashlib.sha256(receipt.read_bytes()).hexdigest(),
                        "status": "PASS",
                    }
                ],
                "status": "PASS",
            }

        validate_native_contract_evidence(
            evidence_for(raw_document),
            boundary_id="scala",
            selector_id=selector_id,
            block_directory=temporary,
            native_cases=native_cases,
            native_statistics_cases=statistics_cases,
            plan_path=plan_path,
            fixture_root_path=FIXTURES,
            input_ledger_path=input_ledger,
            effective_runtime_arguments_sha256=(EFFECTIVE_RUNTIME_ARGUMENTS_SHA256),
            profile="profile-a",
        )

        forged_provenance = evidence_for(raw_document)
        receipt_document = json.loads(receipt.read_text(encoding="utf-8"))
        receipt_document["provenance"]["inputLedgerSha256"] = "0" * 64
        receipt.write_text(
            json.dumps(receipt_document, sort_keys=True),
            encoding="utf-8",
        )
        forged_provenance["cases"][0]["executionReceiptSha256"] = hashlib.sha256(
            receipt.read_bytes()
        ).hexdigest()
        with self.assertRaisesRegex(
            GateError,
            "NATIVE_EXECUTION_RECEIPT_INVALID",
        ):
            validate_native_contract_evidence(
                forged_provenance,
                boundary_id="scala",
                selector_id=selector_id,
                block_directory=temporary,
                native_cases=native_cases,
                native_statistics_cases=statistics_cases,
                plan_path=plan_path,
                fixture_root_path=FIXTURES,
                input_ledger_path=input_ledger,
                effective_runtime_arguments_sha256=(EFFECTIVE_RUNTIME_ARGUMENTS_SHA256),
                profile="profile-a",
            )

        direct_runner = evidence_for(raw_document)
        receipt_document = json.loads(receipt.read_text(encoding="utf-8"))
        receipt_document["commandArgv"] = [
            str(benchmark_executable.resolve()),
            "-bm",
            "avgt",
            "-rff",
            str(raw),
            jmh_include_regex,
        ]
        receipt.write_text(
            json.dumps(receipt_document, sort_keys=True),
            encoding="utf-8",
        )
        direct_runner["cases"][0]["executionReceiptSha256"] = hashlib.sha256(
            receipt.read_bytes()
        ).hexdigest()
        with self.assertRaisesRegex(GateError, "NATIVE_EXECUTION_ARGV_INVALID"):
            validate_native_contract_evidence(
                direct_runner,
                boundary_id="scala",
                selector_id=selector_id,
                block_directory=temporary,
                native_cases=native_cases,
                native_statistics_cases=statistics_cases,
                plan_path=plan_path,
                fixture_root_path=FIXTURES,
                input_ledger_path=input_ledger,
                effective_runtime_arguments_sha256=(EFFECTIVE_RUNTIME_ARGUMENTS_SHA256),
                profile="profile-a",
            )

        forged_profile = evidence_for(raw_document)
        receipt_document = json.loads(receipt.read_text(encoding="utf-8"))
        receipt_document["provenance"]["candidateProvenance"]["selectedProfileSha256"] = "0" * 64
        receipt.write_text(
            json.dumps(receipt_document, sort_keys=True),
            encoding="utf-8",
        )
        forged_profile["cases"][0]["executionReceiptSha256"] = hashlib.sha256(
            receipt.read_bytes()
        ).hexdigest()
        with self.assertRaisesRegex(
            GateError,
            "NATIVE_EXECUTION_PROVENANCE_INVALID",
        ):
            validate_native_contract_evidence(
                forged_profile,
                boundary_id="scala",
                selector_id=selector_id,
                block_directory=temporary,
                native_cases=native_cases,
                native_statistics_cases=statistics_cases,
                plan_path=plan_path,
                fixture_root_path=FIXTURES,
                input_ledger_path=input_ledger,
                effective_runtime_arguments_sha256=(EFFECTIVE_RUNTIME_ARGUMENTS_SHA256),
                profile="profile-a",
            )

        substitute_scala_cli = temporary / "substitute-scala-cli"
        substitute_scala_cli.write_bytes(b"substitute Scala CLI")
        substitute_executable = evidence_for(raw_document)
        receipt_document = json.loads(receipt.read_text(encoding="utf-8"))
        substitute_sha256 = hashlib.sha256(substitute_scala_cli.read_bytes()).hexdigest()
        receipt_document["commandArgv"][0] = str(substitute_scala_cli.resolve())
        receipt_document["provenance"]["benchmarkExecutablePath"] = str(
            substitute_scala_cli.resolve()
        )
        receipt_document["provenance"]["benchmarkExecutableSha256"] = substitute_sha256
        receipt.write_text(
            json.dumps(receipt_document, sort_keys=True),
            encoding="utf-8",
        )
        substitute_executable["cases"][0]["executionReceiptSha256"] = hashlib.sha256(
            receipt.read_bytes()
        ).hexdigest()
        with self.assertRaisesRegex(
            GateError,
            "NATIVE_EXECUTION_PROVENANCE_INVALID",
        ):
            validate_native_contract_evidence(
                substitute_executable,
                boundary_id="scala",
                selector_id=selector_id,
                block_directory=temporary,
                native_cases=native_cases,
                native_statistics_cases=statistics_cases,
                plan_path=plan_path,
                fixture_root_path=FIXTURES,
                input_ledger_path=input_ledger,
                effective_runtime_arguments_sha256=(EFFECTIVE_RUNTIME_ARGUMENTS_SHA256),
                profile="profile-a",
            )

        wrong_shape = copy.deepcopy(raw_document)
        wrong_shape[0]["primaryMetric"]["rawData"] = [[12.0] * 10] * 2
        with self.assertRaisesRegex(GateError, "JMH_RAW_CONTRACT_INVALID"):
            validate_native_contract_evidence(
                evidence_for(wrong_shape),
                boundary_id="scala",
                selector_id=selector_id,
                block_directory=temporary,
                native_cases=native_cases,
                native_statistics_cases=statistics_cases,
                plan_path=plan_path,
                fixture_root_path=FIXTURES,
                input_ledger_path=input_ledger,
                effective_runtime_arguments_sha256=(EFFECTIVE_RUNTIME_ARGUMENTS_SHA256),
                profile="profile-a",
            )

        wrong_score = copy.deepcopy(raw_document)
        wrong_score[0]["primaryMetric"]["score"] = 13.0
        with self.assertRaisesRegex(GateError, "JMH_RAW_CONTRACT_INVALID"):
            validate_native_contract_evidence(
                evidence_for(wrong_score),
                boundary_id="scala",
                selector_id=selector_id,
                block_directory=temporary,
                native_cases=native_cases,
                native_statistics_cases=statistics_cases,
                plan_path=plan_path,
                fixture_root_path=FIXTURES,
                input_ledger_path=input_ledger,
                effective_runtime_arguments_sha256=(EFFECTIVE_RUNTIME_ARGUMENTS_SHA256),
                profile="profile-a",
            )

        wrong_benchmark = copy.deepcopy(raw_document)
        wrong_benchmark[0]["benchmark"] = "s1_4x.benchmarks.other.case_a"
        with self.assertRaisesRegex(GateError, "JMH_RAW_CASE_SELECTION_INVALID"):
            validate_native_contract_evidence(
                evidence_for(wrong_benchmark),
                boundary_id="scala",
                selector_id=selector_id,
                block_directory=temporary,
                native_cases=native_cases,
                native_statistics_cases=statistics_cases,
                plan_path=plan_path,
                fixture_root_path=FIXTURES,
                input_ledger_path=input_ledger,
                effective_runtime_arguments_sha256=(EFFECTIVE_RUNTIME_ARGUMENTS_SHA256),
                profile="profile-a",
            )

        hidden_params = copy.deepcopy(raw_document)
        hidden_params[0]["params"] = {"caseId": "case-forged"}
        with self.assertRaisesRegex(GateError, "JMH_RAW_CASE_SELECTION_INVALID"):
            validate_native_contract_evidence(
                evidence_for(hidden_params),
                boundary_id="scala",
                selector_id=selector_id,
                block_directory=temporary,
                native_cases=native_cases,
                native_statistics_cases=statistics_cases,
                plan_path=plan_path,
                fixture_root_path=FIXTURES,
                input_ledger_path=input_ledger,
                effective_runtime_arguments_sha256=(EFFECTIVE_RUNTIME_ARGUMENTS_SHA256),
                profile="profile-a",
            )

        wrong_version = copy.deepcopy(raw_document)
        wrong_version[0]["jmhVersion"] = "1.36"
        with self.assertRaisesRegex(GateError, "JMH_RAW_CONTRACT_INVALID"):
            validate_native_contract_evidence(
                evidence_for(wrong_version),
                boundary_id="scala",
                selector_id=selector_id,
                block_directory=temporary,
                native_cases=native_cases,
                native_statistics_cases=statistics_cases,
                plan_path=plan_path,
                fixture_root_path=FIXTURES,
                input_ledger_path=input_ledger,
                effective_runtime_arguments_sha256=(EFFECTIVE_RUNTIME_ARGUMENTS_SHA256),
                profile="profile-a",
            )

        tampered_receipt_evidence = evidence_for(raw_document)
        receipt.write_text(
            receipt.read_text(encoding="utf-8") + "\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(
            GateError,
            "NATIVE_EXECUTION_RECEIPT_DIGEST_INVALID",
        ):
            validate_native_contract_evidence(
                tampered_receipt_evidence,
                boundary_id="scala",
                selector_id=selector_id,
                block_directory=temporary,
                native_cases=native_cases,
                native_statistics_cases=statistics_cases,
                plan_path=plan_path,
                fixture_root_path=FIXTURES,
                input_ledger_path=input_ledger,
                effective_runtime_arguments_sha256=(EFFECTIVE_RUNTIME_ARGUMENTS_SHA256),
                profile="profile-a",
            )

        lock_tamper_evidence = evidence_for(raw_document)
        tampered_lock = json.loads(toolchain_lock.read_text(encoding="utf-8"))
        tampered_lock["jdk"]["javaExecutableSha256"] = "0" * 64
        toolchain_lock.write_text(
            json.dumps(tampered_lock, sort_keys=True),
            encoding="utf-8",
        )
        receipt_document = json.loads(receipt.read_text(encoding="utf-8"))
        receipt_document["provenance"]["candidateProvenance"]["toolchainLockSha256"] = (
            hashlib.sha256(toolchain_lock.read_bytes()).hexdigest()
        )
        receipt.write_text(
            json.dumps(receipt_document, sort_keys=True),
            encoding="utf-8",
        )
        lock_tamper_evidence["cases"][0]["executionReceiptSha256"] = hashlib.sha256(
            receipt.read_bytes()
        ).hexdigest()
        with self.assertRaisesRegex(
            GateError,
            "NATIVE_EXECUTION_PROVENANCE_INVALID",
        ):
            validate_native_contract_evidence(
                lock_tamper_evidence,
                boundary_id="scala",
                selector_id=selector_id,
                block_directory=temporary,
                native_cases=native_cases,
                native_statistics_cases=statistics_cases,
                plan_path=plan_path,
                fixture_root_path=FIXTURES,
                input_ledger_path=input_ledger,
                effective_runtime_arguments_sha256=(EFFECTIVE_RUNTIME_ARGUMENTS_SHA256),
                profile="profile-a",
            )

    def test_marker_cli_performs_only_pre_run_to_measurement_transition(self) -> None:
        temporary = Path(self.enterContext(tempfile.TemporaryDirectory()))
        qualification = temporary / "timeout-qualification.json"
        qualification.write_text(
            json.dumps(
                {
                    "schemaVersion": "s1.4x-timeout-qualification-v1",
                    "phase": "PRE_RUN",
                    "measurementEntered": False,
                }
            ),
            encoding="utf-8",
        )
        self.assertEqual(
            mark_measurement_main(["--qualification", str(qualification)]),
            0,
        )
        marked = strict_json_load(qualification)
        self.assertEqual(marked["phase"], "MEASUREMENT")
        self.assertIs(marked["measurementEntered"], True)
        self.assertEqual(
            mark_measurement_main(["--qualification", str(qualification)]),
            2,
        )
