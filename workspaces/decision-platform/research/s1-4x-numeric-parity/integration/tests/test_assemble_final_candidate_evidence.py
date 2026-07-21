"""Sealed raw closure를 strict final-candidate evidence로 투영하는 경계를 검증한다."""

from __future__ import annotations

import hashlib
import io
import json
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, cast
from unittest import TestCase
from unittest.mock import patch

INTEGRATION = Path(__file__).resolve().parents[1]
S1_4X = INTEGRATION.parent
sys.path.insert(0, str(INTEGRATION))

import assemble_final_candidate_evidence as assembler  # noqa: E402
import coverage_gate  # noqa: E402
import final_candidate_audit as final_audit  # noqa: E402
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


def _canonical_hash(value: Any) -> str:
    return hashlib.sha256(_canonical(value)[:-1]).hexdigest()


def _hash_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_canonical(value))


def _sha256(path: Path) -> str:
    return _hash_bytes(path.read_bytes())


def _git(repository: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["/usr/bin/git", *arguments],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return completed.stdout.strip()


def _object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AssertionError(f"expected object: {path}")
    return cast(dict[str, Any], value)


def _comparison(request_id: str) -> dict[str, Any]:
    return {
        "schemaVersion": "s1.4x-comparison-report-v1",
        "requestId": request_id,
        "implementationCount": 2,
        "mismatchCount": 0,
        "mismatches": [],
        "status": "PASS",
    }


def _generated_cabal_fixture(
    package_yaml: bytes,
    haskell_root: Path,
) -> bytes:
    projection = assembler._package_component_projection(  # noqa: SLF001
        package_yaml
    )
    metadata = projection["metadata"]
    github = metadata["github"]
    lines = [
        "cabal-version: 2.0",
        "",
        (
            "-- This file has been generated from package.yaml by hpack "
            "version 0.39.6."
        ),
        "--",
        "-- see: https://github.com/sol/hpack",
        "",
        f"name:           {metadata['name']}",
        f"version:        {metadata['version']}",
        f"synopsis:       {metadata['synopsis']}",
        f"description:    {metadata['description']}",
        f"category:       {metadata['category']}",
        f"homepage:       https://github.com/{github}#readme",
        f"bug-reports:    https://github.com/{github}/issues",
        f"author:         {metadata['author']}",
        f"maintainer:     {metadata['maintainer']}",
        f"license:        {metadata['license']}",
        "build-type:     Simple",
        "",
        "source-repository head",
        "  type: git",
        f"  location: https://github.com/{github}",
    ]
    for stanza, component in projection["components"].items():
        lines.extend(["", stanza])
        if component["mainIs"] is not None:
            lines.append(f"  main-is: {component['mainIs']}")
        if stanza.startswith(("test-suite ", "benchmark ")):
            lines.append("  type: exitcode-stdio-1.0")
        source_root = haskell_root / component["sourceDirs"]
        main_path = (
            source_root / component["mainIs"]
            if component["mainIs"] is not None
            else None
        )
        modules = []
        for source in sorted(source_root.rglob("*.hs")):
            if source == main_path:
                continue
            match = re.search(
                r"(?m)^module\s+([A-Z][A-Za-z0-9_.']*)\b",
                source.read_text(encoding="utf-8"),
            )
            if match is None:
                raise AssertionError(f"module declaration missing: {source}")
            modules.append(match.group(1))
        modules.sort()
        if stanza.startswith("library"):
            lines.extend(
                [
                    "  exposed-modules:",
                    *(f"      {module}" for module in modules),
                ]
            )
            other_modules = ["Paths_s1_4x_haskell"]
        else:
            other_modules = [*modules, "Paths_s1_4x_haskell"]
        lines.extend(
            [
                "  other-modules:",
                *(f"      {module}" for module in other_modules),
                "  autogen-modules:",
                "      Paths_s1_4x_haskell",
            ]
        )
        lines.extend(
            [
                "  hs-source-dirs:",
                f"      {component['sourceDirs']}",
                "  default-extensions:",
                *(
                    f"      {extension}"
                    for extension in projection["defaultExtensions"]
                ),
                "  ghc-options: "
                + " ".join(
                    [
                        *projection["ghcOptions"],
                        *component["ghcOptions"],
                    ]
                ),
                "  build-depends:",
                *(
                    ("      " if index == 0 else "    , ") + dependency
                    for index, dependency in enumerate(
                        sorted(component["dependencies"])
                    )
                ),
                f"  default-language: {projection['language']}",
            ]
        )
    return ("\n".join(lines) + "\n").encode()


class AssembleFinalCandidateEvidenceTests(TestCase):
    def setUp(self) -> None:
        self.temporary = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.repository = self.temporary / "repository"
        self.repository.mkdir()
        _git(self.repository, "init", "-b", "main")
        _git(self.repository, "config", "user.email", "audit@example.invalid")
        _git(self.repository, "config", "user.name", "S1.4X Audit Test")
        source_repository = Path(_git(S1_4X, "rev-parse", "--show-toplevel"))
        prefix = assembler.S1_4X_RELATIVE.as_posix()
        archived = subprocess.run(
            [
                "/usr/bin/git",
                "archive",
                "HEAD",
                prefix,
                ".github/workflows/s1-4x-numeric-parity-correctness.yml",
                ".github/workflows/s1-4x-numeric-parity-benchmark.yml",
            ],
            cwd=source_repository,
            check=True,
            capture_output=True,
            timeout=20,
        ).stdout
        with tarfile.open(fileobj=io.BytesIO(archived), mode="r:") as archive:
            archive.extractall(self.repository, filter="data")
        self.numeric_root = self.repository / assembler.S1_4X_RELATIVE
        self.generated_cabal = _generated_cabal_fixture(
            self._repo("haskell/package.yaml").read_bytes(),
            self._repo("haskell"),
        )
        self._materialize_final_haskell_profile()
        _git(self.repository, "add", ".")
        _git(self.repository, "commit", "-m", "test subject")
        self.commit = _git(self.repository, "rev-parse", "HEAD")

        self.large_fixtures = self._tiny_large_fixture_specs()
        self.enterContext(
            patch.object(
                assembler,
                "LARGE_FIXTURES",
                self.large_fixtures,
            )
        )
        self.enterContext(
            patch.object(
                assembler,
                "LARGE_FIXTURE_GENERATOR_SHA256",
                "7" * 64,
            )
        )
        self.raw = self.temporary / "raw-correctness"
        self.raw.mkdir()
        self._materialize_raw_closure()
        self.output = self.temporary / "audit"

    def _repo(self, relative: str) -> Path:
        return self.numeric_root / relative

    def _materialize_final_haskell_profile(self) -> None:
        manifest_path = self._repo("haskell/source-inputs.v1.json")
        manifest = _object(manifest_path)
        source_tree = self._haskell_source_tree_sha256(manifest)
        lock = _object(self._repo("haskell/toolchain-lock.v1.json"))
        options = ["-O2", "-fasm"]
        selected = {
            "schemaVersion": "s1.4x-haskell-selected-profile-v1",
            "profileId": "optimized-o2-fasm",
            "ghcOptions": options,
            "compilerVersion": "9.10.3",
            "compilerSha256": lock["resolvedTools"]["authoritativeGhc"]["sha256"],
            "sourceTreeSha256": source_tree,
            "optionsSha256": _canonical_hash(options),
            "fullCorrectnessSha256": "3" * 64,
            "qualificationPlanSha256": _sha256(
                self._repo("benchmarks/benchmark-plan.v1.json")
            ),
            "qualificationArtifactSha256": "5" * 64,
            "selectorConfigSha256": "6" * 64,
            "fallbackProfile": "baseline-o0-fasm",
            "selectedBy": "frozen-criterion-selector",
        }
        selected_path = self._repo("haskell/selected-profile.v1.json")
        _write_json(selected_path, selected)
        manifest["files"]["selected-profile.v1.json"]["sha256"] = _sha256(
            selected_path
        )
        manifest["canonicalManifestSha256"] = _hash_bytes(
            b"".join(
                (
                    f"{manifest['files'][path]['sha256']}  {path}\n"
                ).encode()
                for path in sorted(manifest["files"], key=str.encode)
            )
        )
        _write_json(manifest_path, manifest)

    def _haskell_source_tree_sha256(
        self,
        manifest: dict[str, Any] | None = None,
    ) -> str:
        source_manifest = manifest or _object(
            self._repo("haskell/source-inputs.v1.json")
        )
        source_paths = {
            path
            for path in source_manifest["files"]
            if path.endswith(".hs")
            and path.split("/", 1)[0] in assembler.HASKELL_CANDIDATE_ROOTS
        }
        entries = []
        for relative in sorted(
            source_paths | set(assembler.HASKELL_SOURCE_TREE_INPUTS),
            key=str.encode,
        ):
            payload = (
                self.generated_cabal
                if relative == "s1-4x-haskell.cabal"
                else self._repo(f"haskell/{relative}").read_bytes()
            )
            entries.append({"path": relative, "sha256": _hash_bytes(payload)})
        return _canonical_hash(entries)

    def _tiny_large_fixture_specs(
        self,
    ) -> tuple[tuple[str, int, str, str, int, str], ...]:
        values = []
        for index in range(4):
            manifest = f"manifest-{index}\n".encode()
            payload = f"payload-{index}\n".encode()
            values.append(
                (
                    f"large/fixture-{index}.manifest.json",
                    len(manifest),
                    _hash_bytes(manifest),
                    f"large/generated/fixture-{index}.f64le",
                    len(payload),
                    _hash_bytes(payload),
                )
            )
        return tuple(values)

    def _materialize_raw_closure(self) -> None:
        self._materialize_contract_and_large_fixtures()
        self._materialize_coverage()
        self._materialize_regression()
        scala = self._materialize_scala()
        haskell = self._materialize_haskell()
        self._materialize_cross_language(scala, haskell)
        self._materialize_oci(scala, haskell)
        self._materialize_rubric_inputs()
        self._materialize_rubric_audits()
        self._write_run_manifest()

    def _materialize_contract_and_large_fixtures(self) -> None:
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
        manifest_entries = []
        payload_entries = []
        for (
            manifest_path,
            manifest_size,
            manifest_sha,
            payload_path,
            payload_size,
            payload_sha,
        ) in self.large_fixtures:
            manifest_payload = (
                f"manifest-{manifest_path.split('-')[-1].split('.')[0]}\n"
            ).encode()
            payload_payload = (
                f"payload-{payload_path.split('-')[-1].split('.')[0]}\n"
            ).encode()
            materialized_manifest = self.raw / "large-fixtures" / manifest_path
            materialized_payload = self.raw / "large-fixtures" / payload_path
            materialized_manifest.parent.mkdir(parents=True, exist_ok=True)
            materialized_payload.parent.mkdir(parents=True, exist_ok=True)
            materialized_manifest.write_bytes(manifest_payload)
            materialized_payload.write_bytes(payload_payload)
            self.assertEqual(
                (manifest_size, manifest_sha),
                (len(manifest_payload), _hash_bytes(manifest_payload)),
            )
            self.assertEqual(
                (payload_size, payload_sha),
                (len(payload_payload), _hash_bytes(payload_payload)),
            )
            manifest_entries.append(
                {
                    "path": manifest_path,
                    "byteLength": manifest_size,
                    "sha256": manifest_sha,
                }
            )
            payload_entries.append(
                {
                    "path": payload_path,
                    "manifestPath": manifest_path,
                    "byteLength": payload_size,
                    "sha256": payload_sha,
                }
            )
        fixture_tree = {
            "schemaVersion": "s1.4x-large-fixture-tree-v1",
            "manifestEntries": manifest_entries,
            "payloadEntries": payload_entries,
        }
        receipt = {
            "schemaVersion": (
                "s1.4x-large-fixture-materialization-receipt-v1"
            ),
            "status": "PASS",
            "generatorSha256": "7" * 64,
            "materializedRootPathId": "S1_4X_LARGE_FIXTURE_ROOT",
            "manifestEntries": manifest_entries,
            "payloadEntries": payload_entries,
            "fixtureTreeSha256": _canonical_hash(fixture_tree),
        }
        _write_json(self.raw / "large-fixture-receipt.json", receipt)
        _write_json(self.raw / "large-fixture-check-receipt.json", receipt)

    def _candidate_reports(
        self,
        candidate: str,
        *,
        runner_sha: str,
        source_closure_sha: str,
        toolchain_profile: str,
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        plan_path = self._repo("contract/property-plan.v1.json")
        seed_path = self._repo(
            "contract/fixtures/property/property-seeds.v1.json"
        )
        plan = _object(plan_path)
        seeds = _object(seed_path)["seeds"]
        functions = _object(self._repo("contract/function-registry.v1.json"))
        errors = _object(self._repo("contract/error-registry.v1.json"))
        per_seed = (
            plan["minimumSuccessfulPerProperty"] + len(seeds) - 1
        ) // len(seeds)
        successes = per_seed * len(seeds)
        implementation = (
            "scala-3.8.4-jvm25"
            if candidate == "scala"
            else "haskell"
        )
        properties = [
            {
                "propertyId": item["propertyId"],
                "successfulTests": successes,
                "discardedTests": 0,
                "status": "PASS",
            }
            for item in plan["properties"]
        ]
        property_report = {
            "schemaVersion": "s1.4x-candidate-property-coverage-v1",
            "implementation": implementation,
            "propertyPlanSha256": _sha256(plan_path),
            "properties": properties,
            "status": "PASS",
        }
        registry_report = {
            "schemaVersion": "s1.4x-candidate-registry-coverage-v1",
            "implementation": implementation,
            "functions": [
                {"functionId": item["functionId"], "status": "PASS"}
                for item in functions["entries"]
            ],
            "errors": [
                {
                    "errorCode": item["code"],
                    "track": item["track"],
                    "verificationMode": item["verificationMode"],
                    "status": "PASS",
                }
                for item in errors["entries"]
            ],
            "status": "PASS",
        }
        execution = {
            "schemaVersion": "s1.4x-candidate-property-execution-v1",
            "implementation": implementation,
            "propertyPlanSha256": _sha256(plan_path),
            "seedCorpusSha256": _sha256(seed_path),
            "seedCount": len(seeds),
            "minimumSuccessfulPerSeed": per_seed,
            "framework": (
                "scala-check-1.19.0"
                if candidate == "scala"
                else "QuickCheck-2.15.0.1"
            ),
            "toolchainProfile": toolchain_profile,
            "commandArgvSha256": "a" * 64,
            "runnerSha256": runner_sha,
            "sourceClosureSha256": source_closure_sha,
            "startedAt": "2026-07-19T00:00:01.000000Z",
            "finishedAt": "2026-07-19T00:00:02.000000Z",
            "exitCode": 0,
            "properties": [
                {
                    "propertyId": item["propertyId"],
                    "successfulTests": successes,
                    "discardedTests": 0,
                    "attemptedTests": successes,
                    "seedCount": len(seeds),
                    "seedExecutions": [
                        {
                            "seedIndex": seed_index,
                            "originalSeed": seed,
                            "successfulTests": per_seed,
                            "discardedTests": 0,
                            "attemptedTests": per_seed,
                            "replayToken": (
                                f"{candidate}:{property_index}:{seed_index}"
                            ),
                            "shrinks": 0,
                            "status": "PASS",
                        }
                        for seed_index, seed in enumerate(seeds)
                    ],
                    "shrinks": 0,
                    "status": "PASS",
                }
                for property_index, item in enumerate(plan["properties"])
            ],
            "status": "PASS",
        }
        if candidate == "scala":
            scala_lock = _object(
                self._repo("scala/toolchain-lock.v1.json")
            )
            execution.update(
                {
                    "maximumDiscardRatio": plan["maximumDiscardRatio"],
                    "scalaCliBinarySha256": scala_lock["scalaCli"][
                        "binarySha256"
                    ],
                }
            )
        else:
            selected_path = self._repo(
                "haskell/selected-profile.v1.json"
            )
            selected = _object(selected_path)
            options = selected["ghcOptions"]
            execution.update(
                {
                    "outerCommandArgvSha256": "d" * 64,
                    "buildArgvSha256": "e" * 64,
                    "sourceInputManifestSha256": _sha256(
                        self._repo("haskell/source-inputs.v1.json")
                    ),
                    "selectedProfileSha256": _sha256(selected_path),
                    "sourceTreeSha256": selected["sourceTreeSha256"],
                    "propertyClosureSha256": source_closure_sha,
                    "profileGhcOptions": options,
                    "profileOptionsSha256": _canonical_hash(options),
                    "stackRootPathId": (
                        "S1_4X_CACHE_ROOT/stack-root-property-" + "1" * 24
                    ),
                }
            )
        return property_report, registry_report, execution

    def _source_manifest_files(self, candidate: str) -> dict[str, Any]:
        return cast(
            dict[str, Any],
            _object(self._repo(f"{candidate}/source-inputs.v1.json"))["files"],
        )

    def _materialize_generated_cabal_provenance(
        self,
        *,
        source_tree_sha: str,
        property_closure_sha: str,
    ) -> None:
        generated_path = self.raw / assembler.HASKELL_GENERATED_CABAL
        generated_path.parent.mkdir(parents=True, exist_ok=True)
        generated_path.write_bytes(self.generated_cabal)
        lock = _object(self._repo("haskell/toolchain-lock.v1.json"))
        stack_lock = lock["resolvedTools"]["stack"]
        stack = {
            "pathId": stack_lock["pathId"],
            "version": stack_lock["version"],
            "binarySha256": stack_lock["sha256"],
        }
        selected = _object(self._repo("haskell/selected-profile.v1.json"))
        argv = [
            "stack",
            "--stack-root",
            "<isolated-stack-root>",
            "--work-dir",
            "<isolated-stack-work-dir>",
            "--system-ghc",
            "--no-install-ghc",
            "--stack-yaml",
            "haskell/stack.yaml",
            "--hpack-force",
            "build",
            "--test",
            "--no-run-tests",
            "--no-terminal",
            "--ghc-options",
            " ".join(selected["ghcOptions"]),
        ]
        receipt = {
            "schemaVersion": (
                "s1.4x-haskell-generated-cabal-provenance-v1"
            ),
            "benchmarkSubjectCommit": self.commit,
            "toolchainLockSha256": _sha256(
                self._repo("haskell/toolchain-lock.v1.json")
            ),
            "packageYaml": {
                "path": (
                    assembler.S1_4X_RELATIVE / "haskell/package.yaml"
                ).as_posix(),
                "blobSha256": _sha256(self._repo("haskell/package.yaml")),
            },
            "sourceInputManifest": {
                "path": (
                    assembler.S1_4X_RELATIVE
                    / "haskell/source-inputs.v1.json"
                ).as_posix(),
                "blobSha256": _sha256(
                    self._repo("haskell/source-inputs.v1.json")
                ),
            },
            "stack": stack,
            "hpack": {
                "version": "0.39.6",
                "versionOutputSha256": _hash_bytes(b"0.39.6\n"),
            },
            "build": {
                "portableArgv": argv,
                "portableArgvSha256": _canonical_hash(argv),
                "runtimeArgvSha256": "e" * 64,
                "stackRootPathId": (
                    "S1_4X_CACHE_ROOT/stack-root-property-" + "1" * 24
                ),
                "exitCode": 0,
            },
            "generatedCabal": {
                "repositoryRelativePath": (
                    assembler.S1_4X_RELATIVE
                    / "haskell/s1-4x-haskell.cabal"
                ).as_posix(),
                "artifactPath": (
                    assembler.HASKELL_GENERATED_CABAL.as_posix()
                ),
                "sha256": _hash_bytes(self.generated_cabal),
                "sizeBytes": len(self.generated_cabal),
                "preBuildSha256": _hash_bytes(self.generated_cabal),
                "postBuildSha256": _hash_bytes(self.generated_cabal),
            },
            "sourceTreeSha256": source_tree_sha,
            "propertyClosureSha256": property_closure_sha,
            "status": "PASS",
        }
        _write_json(self.raw / assembler.HASKELL_CABAL_PROVENANCE, receipt)

    def _materialize_coverage(self) -> None:
        scala_runner = self._repo("scala/tools/run-property-evidence.sh")
        haskell_runner = self._repo("haskell/tools/run-property-evidence.sh")
        scala_source_closure = (
            assembler._scala_property_source_closure_sha256(  # noqa: SLF001
                self.repository,
                subject=self.commit,
                manifest_files=self._source_manifest_files("scala"),
            )
        )
        haskell_source_closure = (
            assembler._haskell_property_source_closure_sha256(  # noqa: SLF001
                self.repository,
                subject=self.commit,
                manifest_files=self._source_manifest_files("haskell"),
                generated_cabal_sha256=_hash_bytes(self.generated_cabal),
            )
        )
        self._materialize_generated_cabal_provenance(
            source_tree_sha=self._haskell_source_tree_sha256(),
            property_closure_sha=haskell_source_closure,
        )
        summaries = []
        for candidate, runner, source_closure, profile in (
            (
                "scala",
                scala_runner,
                scala_source_closure,
                "B",
            ),
            (
                "haskell",
                haskell_runner,
                haskell_source_closure,
                "haskell-ghc-9.10.3-optimized-o2-fasm",
            ),
        ):
            property_report, registry_report, execution = (
                self._candidate_reports(
                    candidate,
                    runner_sha=_sha256(runner),
                    source_closure_sha=source_closure,
                    toolchain_profile=profile,
                )
            )
            output = self.raw / f"coverage/{candidate}"
            property_path = output / f"{candidate}-property-report.v1.json"
            registry_path = output / f"{candidate}-registry-report.v1.json"
            execution_path = (
                output / f"{candidate}-property-execution-evidence.v1.json"
            )
            _write_json(property_path, property_report)
            _write_json(registry_path, registry_report)
            _write_json(execution_path, execution)
            summary = coverage_gate.validate_candidate_coverage(
                implementation_label=candidate,
                property_plan_path=self._repo(
                    "contract/property-plan.v1.json"
                ),
                function_registry_path=self._repo(
                    "contract/function-registry.v1.json"
                ),
                error_registry_path=self._repo(
                    "contract/error-registry.v1.json"
                ),
                property_report=property_report,
                registry_report=registry_report,
                execution_report=execution,
            )
            summaries.append(summary)
            artifacts = [
                {
                    "path": path.name,
                    "sha256": _sha256(path),
                    "sizeBytes": path.stat().st_size,
                }
                for path in (property_path, registry_path, execution_path)
            ]
            _write_json(
                self.raw / f"coverage/{candidate}-coverage-receipt.json",
                {
                    "schemaVersion": (
                        "s1.4x-property-execution-receipt-v1"
                    ),
                    "candidate": candidate,
                    "runner": {
                        "sha256": _sha256(runner),
                        "commandArgvSha256": (
                            execution["commandArgvSha256"]
                            if candidate == "scala"
                            else execution["outerCommandArgvSha256"]
                        ),
                    },
                    "process": {
                        "startedAt": "2026-07-19T00:00:00.000000Z",
                        "finishedAt": "2026-07-19T00:00:03.000000Z",
                        "exitCode": 0,
                        "stdoutSha256": "b" * 64,
                        "stderrSha256": "c" * 64,
                    },
                    "artifacts": artifacts,
                    "coverage": summary,
                    "status": "PASS",
                },
            )
        _write_json(
            self.raw / "coverage/integration-coverage.json",
            {
                "schemaVersion": "s1.4x-integration-coverage-v1",
                "candidateCount": 2,
                "candidates": summaries,
                "propertyCountPerCandidate": 25,
                "functionCountPerCandidate": 20,
                "errorTrackCountsPerCandidate": {"s1.4": 19, "s1.4r": 13},
                "errorVerificationModeCountsPerCandidate": {
                    "processDynamic": 29,
                    "referenceObjectModel": 1,
                    "registryStatic": 2,
                },
                "status": "PASS",
            },
        )

    def _rebind_haskell_coverage_as_composite_v2(self) -> Path:
        receipt_path = self.raw / "coverage/haskell-coverage-receipt.json"
        receipt = _object(receipt_path)
        execution = _object(
            self.raw
            / "coverage/haskell/haskell-property-execution-evidence.v1.json"
        )
        provenance_path = self.raw / assembler.HASKELL_CABAL_PROVENANCE
        provenance = _object(provenance_path)
        process_stdout = receipt_path.with_name(
            "haskell-coverage-receipt.process.stdout"
        )
        process_stderr = receipt_path.with_name(
            "haskell-coverage-receipt.process.stderr"
        )
        completion_stdout = receipt_path.with_name(
            "haskell-coverage-receipt.generated-cabal-completion.stdout"
        )
        completion_stderr = receipt_path.with_name(
            "haskell-coverage-receipt.generated-cabal-completion.stderr"
        )
        process_stdout.write_bytes(b"")
        process_stderr.write_bytes(
            b"usage: haskell_evidence.py generated-cabal-provenance\n"
            + assembler.HASKELL_GHC_OPTION_ARGPARSE_FAILURE
        )
        completion_stdout.write_bytes(
            (json.dumps(provenance, allow_nan=False, sort_keys=True) + "\n").encode()
        )
        completion_stderr.write_bytes(b"")
        portable_argv = assembler._haskell_completion_portable_argv(  # noqa: SLF001
            subject=self.commit,
            cabal_sha256=_hash_bytes(self.generated_cabal),
            profile_options=execution["profileGhcOptions"],
            stack_root_path_id=execution["stackRootPathId"],
            build_argv_sha256=execution["buildArgvSha256"],
        )
        receipt.update(
            {
                "schemaVersion": "s1.4x-property-execution-receipt-v2",
                "process": {
                    "startedAt": "2026-07-19T00:00:00.000000Z",
                    "finishedAt": "2026-07-19T00:00:03.000000Z",
                    "exitCode": 2,
                    "stdout": {
                        "path": process_stdout.name,
                        "sha256": _sha256(process_stdout),
                        "sizeBytes": process_stdout.stat().st_size,
                    },
                    "stderr": {
                        "path": process_stderr.name,
                        "sha256": _sha256(process_stderr),
                        "sizeBytes": process_stderr.stat().st_size,
                    },
                },
                "completion": {
                    "reason": "ARGPARSE_DASH_PREFIXED_GHC_OPTION",
                    "process": {
                        "commandArgvSha256": "d" * 64,
                        "portableArgv": portable_argv,
                        "portableArgvSha256": _canonical_hash(portable_argv),
                        "startedAt": "2026-07-19T00:00:04.000000Z",
                        "finishedAt": "2026-07-19T00:00:05.000000Z",
                        "exitCode": 0,
                        "stdout": {
                            "path": completion_stdout.name,
                            "sha256": _sha256(completion_stdout),
                            "sizeBytes": completion_stdout.stat().st_size,
                        },
                        "stderr": {
                            "path": completion_stderr.name,
                            "sha256": _sha256(completion_stderr),
                            "sizeBytes": completion_stderr.stat().st_size,
                        },
                    },
                    "artifact": {
                        "path": provenance_path.name,
                        "sha256": _sha256(provenance_path),
                        "sizeBytes": provenance_path.stat().st_size,
                    },
                    "status": "PASS",
                },
            }
        )
        _write_json(receipt_path, receipt)
        self._write_run_manifest()
        return process_stderr

    def _materialize_cross_language(
        self,
        scala: dict[str, Any],
        haskell: dict[str, Any],
    ) -> None:
        comparisons: dict[str, bytes] = {}
        for matrix, request_id, expected_path, scala_name, haskell_name in (
            (
                "canonical",
                "s1.4x-canonical-small-v1",
                "contract/fixtures/expected/canonical-results.v1.json",
                "canonical-results.json",
                "canonical.actual.json",
            ),
            (
                "semantic",
                "s1.4x-semantic-errors-v1",
                (
                    "contract/fixtures/invalid/"
                    "semantic-errors.expected.v1.json"
                ),
                "semantic-errors.json",
                "semantic.actual.json",
            ),
        ):
            root = self.raw / f"cross-language/{matrix}"
            scala_source = (
                self.raw
                / f"scala/profiles/{scala['profile']}/{scala_name}"
            )
            haskell_source = (
                self.raw
                / f"haskell/profiles/{haskell['profile']}/{haskell_name}"
            )
            scala_result = root / "scala-results.json"
            haskell_result = root / "haskell-results.json"
            scala_result.parent.mkdir(parents=True, exist_ok=True)
            scala_result.write_bytes(scala_source.read_bytes())
            haskell_result.write_bytes(haskell_source.read_bytes())
            comparison = root / "comparison-report.json"
            _write_json(comparison, _comparison(request_id))
            comparisons[matrix] = comparison.read_bytes()
            reference = root / "reference-capture.json"
            _write_json(
                reference,
                {
                    "schemaVersion": "s1.4x-reference-capture-report-v1",
                    "uvVersion": "0.11.26",
                    "processCount": 2,
                    "projects": [
                        {"projectId": "S1_4_PRODUCTION"},
                        {"projectId": "S1_4R_RESEARCH"},
                    ],
                    "resultSha256": _sha256(self._repo(expected_path)),
                    "status": "PASS",
                },
            )
            _write_json(
                root / "correctness-summary.json",
                {
                    "schemaVersion": "s1.4x-integration-correctness-v1",
                    "requestId": request_id,
                    "oracleImplementation": "python-frozen-oracle",
                    "candidateImplementations": [
                        "scala-3.8.4-jvm25",
                        "haskell-ghc-9.10.3",
                    ],
                    "caseCount": 1,
                    "mismatchCount": 0,
                    "artifacts": {
                        "reference-capture.json": _sha256(reference),
                        "scala-results.json": _sha256(scala_result),
                        "haskell-results.json": _sha256(haskell_result),
                        "comparison-report.json": _sha256(comparison),
                    },
                    "referenceCaptureStatus": "PASS",
                    "status": "PASS",
                },
            )
        (
            self.raw / "cross-language/selected-comparison.json"
        ).write_bytes(comparisons["canonical"])
        _write_json(
            self.raw / "oci/cross-language-comparison.json",
            _comparison("s1.4x-canonical-small-v1"),
        )

    def _materialize_regression(self) -> None:
        uv_sha = "d" * 64
        command_documents: dict[str, dict[str, Any]] = {}
        manifest_entries = []
        for spec in assembler._regression_specs():  # noqa: SLF001
            label = str(spec["label"])
            stdout = self.raw / f"regression/logs/{label}.stdout"
            stderr = self.raw / f"regression/logs/{label}.stderr"
            stdout.parent.mkdir(parents=True, exist_ok=True)
            if "passed" in spec:
                deselected = int(spec["deselected"])
                suffix = (
                    f", {deselected} deselected"
                    if deselected
                    else ""
                )
                stdout.write_text(
                    f"{spec['passed']} passed{suffix} in 1.00s\n",
                    encoding="utf-8",
                )
            else:
                stdout.write_text(f"{label} PASS\n", encoding="utf-8")
            stderr.write_bytes(b"")
            junit_path = spec.get("junit")
            junit_sha: str | None = None
            if isinstance(junit_path, str):
                junit = self.raw / junit_path
                junit.parent.mkdir(parents=True, exist_ok=True)
                junit.write_text(
                    (
                        '<testsuite tests="'
                        f"{spec['passed']}"
                        '" failures="0" errors="0" skipped="0"></testsuite>'
                    ),
                    encoding="utf-8",
                )
                junit_sha = _sha256(junit)
            command = {
                "schemaVersion": "s1.4x-regression-command-receipt-v1",
                "benchmarkSubjectCommit": self.commit,
                "project": spec["project"],
                "role": spec["role"],
                "uvExecutableSha256": uv_sha,
                "commandArgv": spec["argv"],
                "commandArgvSha256": _hash_bytes(_canonical(spec["argv"])),
                "exitCode": 0,
                "stdoutPath": stdout.relative_to(self.raw).as_posix(),
                "stdoutSha256": _sha256(stdout),
                "stderrPath": stderr.relative_to(self.raw).as_posix(),
                "stderrSha256": _sha256(stderr),
                "junitPath": junit_path,
                "junitSha256": junit_sha,
                "status": "PASS",
            }
            command_path = (
                self.raw
                / f"regression/commands/{label}.command.v1.json"
            )
            _write_json(command_path, command)
            command_documents[label] = command
            manifest_entries.append(
                {
                    "path": command_path.relative_to(self.raw).as_posix(),
                    "sha256": _sha256(command_path),
                }
            )
        _write_json(
            self.raw / "regression/execution-manifest.v1.json",
            {
                "schemaVersion": (
                    "s1.4x-regression-execution-manifest-v1"
                ),
                "benchmarkSubjectCommit": self.commit,
                "uvExecutableSha256": uv_sha,
                "commandReceipts": manifest_entries,
                "status": "PASS",
            },
        )
        compound_specs: dict[str, dict[str, Any]] = {
            "production": {
                "project": "workspaces/decision-platform/python-services",
                "counts": (1344, 1344, 0, 0, 1344),
                "deselected": [],
                "replacements": [],
                "labels": (
                    "production-ruff",
                    "production-mypy",
                    "production-pytest",
                ),
            },
            "research": {
                "project": (
                    "workspaces/decision-platform/research/s1-4r-jax-risk"
                ),
                "counts": (263, 262, 1, 2, 264),
                "deselected": [assembler.DESELECTED_RESEARCH_NODE],
                "replacements": list(assembler.REPLACEMENT_RESEARCH_NODES),
                "labels": (
                    "research-ruff",
                    "research-mypy",
                    "research-replacement-pytest",
                    "research-base-pytest",
                ),
            },
        }
        for name, spec in compound_specs.items():
            collected, passed, deselected, replacements, total = spec["counts"]
            labels = cast(tuple[str, ...], spec["labels"])
            commands = [
                {
                    field: command_documents[label][field]
                    for field in (
                        "role",
                        "exitCode",
                        "stdoutPath",
                        "stdoutSha256",
                        "stderrPath",
                        "stderrSha256",
                        "status",
                    )
                }
                for label in labels
            ]
            _write_json(
                self.raw
                / f"regression/{name}-compound-receipt.v1.json",
                {
                    "schemaVersion": (
                        "s1.4x-regression-compound-receipt-v1"
                    ),
                    "benchmarkSubjectCommit": self.commit,
                    "project": spec["project"],
                    "collectedCount": collected,
                    "basePassedCount": passed,
                    "deselectedCount": deselected,
                    "replacementPassedCount": replacements,
                    "totalExecutedPassedCount": total,
                    "deselectedNodeIds": spec["deselected"],
                    "replacementNodeIds": spec["replacements"],
                    "commands": commands,
                    "status": "PASS",
                },
            )

    def _materialize_scala(self) -> dict[str, Any]:
        profile = "B"
        profile_root = self.raw / f"scala/profiles/{profile}"
        candidate = profile_root / "candidate.jar"
        candidate.parent.mkdir(parents=True, exist_ok=True)
        candidate.write_bytes(b"strict scala candidate\n")
        for matrix, request_id, actual_name, comparison_name in (
            (
                "canonical",
                "s1.4x-canonical-small-v1",
                "canonical-results.json",
                "canonical-comparison.json",
            ),
            (
                "semantic",
                "s1.4x-semantic-errors-v1",
                "semantic-errors.json",
                "semantic-comparison.json",
            ),
        ):
            _write_json(
                profile_root / actual_name,
                {
                    "requestId": request_id,
                    "implementation": "scala-3.8.4-jvm25",
                    "results": [],
                },
            )
            _write_json(
                profile_root / comparison_name,
                _comparison(request_id),
            )
        _write_json(
            profile_root / "scala-profile-unit-test-result.v1.json",
            {"status": "PASS"},
        )
        (profile_root / "unit-test.stdout").write_text(
            "tests passed\n",
            encoding="utf-8",
        )
        (profile_root / "unit-test.stderr").write_bytes(b"")
        property_root = profile_root / "property"
        property_root.mkdir()
        for name in (
            "scala-property-report.v1.json",
            "scala-registry-report.v1.json",
            "scala-property-execution-evidence.v1.json",
        ):
            (property_root / name).write_bytes(
                (self.raw / "coverage/scala" / name).read_bytes()
            )
        matrix_hashes = {
            "candidateResultSha256": _sha256(
                profile_root / "canonical-results.json"
            ),
            "semanticResultSha256": _sha256(
                profile_root / "semantic-errors.json"
            ),
            "unitTestResultSha256": _sha256(
                profile_root / "scala-profile-unit-test-result.v1.json"
            ),
            "unitStdoutSha256": _sha256(profile_root / "unit-test.stdout"),
            "unitStderrSha256": _sha256(profile_root / "unit-test.stderr"),
            "canonicalComparisonSha256": _sha256(
                profile_root / "canonical-comparison.json"
            ),
            "semanticComparisonSha256": _sha256(
                profile_root / "semantic-comparison.json"
            ),
            "propertyReportSha256": _sha256(
                property_root / "scala-property-report.v1.json"
            ),
            "registryReportSha256": _sha256(
                property_root / "scala-registry-report.v1.json"
            ),
            "propertyExecutionEvidenceSha256": _sha256(
                property_root
                / "scala-property-execution-evidence.v1.json"
            ),
            "propertyPlanSha256": _sha256(
                self._repo("contract/property-plan.v1.json")
            ),
            "propertySeedCorpusSha256": _sha256(
                self._repo(
                    "contract/fixtures/property/property-seeds.v1.json"
                )
            ),
            "functionRegistrySha256": _sha256(
                self._repo("contract/function-registry.v1.json")
            ),
            "errorRegistrySha256": _sha256(
                self._repo("contract/error-registry.v1.json")
            ),
        }
        compiler_profiles = _object(
            self._repo("scala/compiler-profiles.v1.json")
        )
        options = compiler_profiles["profiles"][profile]["additionalOptions"]
        source_files = self._source_manifest_files("scala")
        correctness = {
            "schemaVersion": "s1.4x-scala-profile-correctness-v1",
            "profileId": profile,
            "compilerProfilesSha256": _sha256(
                self._repo("scala/compiler-profiles.v1.json")
            ),
            "profileOptions": options,
            "profileOptionsSha256": _canonical_hash(options),
            "sourceInputManifestSha256": _sha256(
                self._repo("scala/source-inputs.v1.json")
            ),
            "toolchainLockSha256": _sha256(
                self._repo("scala/toolchain-lock.v1.json")
            ),
            "scalaCliBinarySha256": (
                "54b93b8401e333095526da5e4853780d5bf37494baa1ba5486e9e643084253d0"
            ),
            "profileRunInputPaths": [
                path
                for path, entry in source_files.items()
                if entry["role"] != "benchmark"
            ],
            "candidateSha256": _sha256(candidate),
            "matrix": matrix_hashes,
            "mismatchCount": 0,
            "status": "PASS",
        }
        correctness_path = (
            profile_root / "scala-profile-correctness-result.v1.json"
        )
        _write_json(correctness_path, correctness)
        lock = _object(self._repo("scala/toolchain-lock.v1.json"))
        selected = {
            "schemaVersion": "s1.4x-scala-selected-profile-result-v1",
            "benchmarkPlanSha256": _sha256(
                self._repo("benchmarks/benchmark-plan.v1.json")
            ),
            "selectorConfigSha256": "1" * 64,
            "qualificationSha256": "2" * 64,
            "sourceInputManifestSha256": _sha256(
                self._repo("scala/source-inputs.v1.json")
            ),
            "compilerProfilesSha256": _sha256(
                self._repo("scala/compiler-profiles.v1.json")
            ),
            "toolchainLockSha256": _sha256(
                self._repo("scala/toolchain-lock.v1.json")
            ),
            "mergedToolchainProvenanceSha256": (
                "cd9e29a22473fba6203daa4f3a0cbaa57b8b6e5c5fc22de05ca0801c404ffa98"
            ),
            "scalaCliBinarySha256": lock["scalaCli"]["binarySha256"],
            "javaExecutableSha256": lock["jdk"]["javaExecutableSha256"],
            "jvmArgumentAllowlistSha256": "3" * 64,
            "effectiveJvmArgumentsCapabilitySha256": "4" * 64,
            "profileOptionsSha256": "5" * 64,
            "selectedProfileSourceSha256": _sha256(
                self._repo("scala/selected-profile.scala")
            ),
            "selectedProfileOptions": options,
            "selectedProfileOptionsSha256": _canonical_hash(options),
            "correctnessResultSha256": _sha256(correctness_path),
            "profiles": {
                "A": {"qualified": True},
                "B": {"qualified": True},
                "C": {"qualified": False},
            },
            "selectedProfileId": profile,
            "fallbackProfileId": "A",
            "fallbackExecuted": False,
            "selectionStatus": "PASS",
        }
        selected_path = self.raw / "scala/scala-selected-profile-result.v1.json"
        _write_json(selected_path, selected)
        return {
            "profile": profile,
            "candidate": candidate,
            "selected": selected,
        }

    def _materialize_haskell(self) -> dict[str, Any]:
        selected_path = self._repo("haskell/selected-profile.v1.json")
        selected = _object(selected_path)
        profile = selected["profileId"]
        root = self.raw / f"haskell/profiles/{profile}"
        commands = []
        for phase in (
            "build",
            "test",
            "canonical-process",
            "canonical-compare",
            "semantic-process",
            "semantic-compare",
        ):
            stdout = root / f"{phase}.stdout"
            stderr = root / f"{phase}.stderr"
            stdout.parent.mkdir(parents=True, exist_ok=True)
            stdout.write_text(f"{phase} PASS\n", encoding="utf-8")
            stderr.write_bytes(b"")
            argv = ["STACK_3_11_1", phase]
            commands.append(
                {
                    "phase": phase,
                    "argv": argv,
                    "argvSha256": _canonical_hash(argv),
                    "cwdPath": "HASKELL_ROOT",
                    "startedAt": "2026-07-19T00:00:00Z",
                    "finishedAt": "2026-07-19T00:00:01Z",
                    "exitCode": 0,
                    "stdoutPath": stdout.relative_to(self.raw).as_posix(),
                    "stdoutSha256": _sha256(stdout),
                    "stderrPath": stderr.relative_to(self.raw).as_posix(),
                    "stderrSha256": _sha256(stderr),
                }
            )
        comparison_artifacts = []
        for matrix, request_id, request_path, expected_path in (
            (
                "canonical",
                "s1.4x-canonical-small-v1",
                "contract/fixtures/small/canonical-inputs.v1.json",
                "contract/fixtures/expected/canonical-results.v1.json",
            ),
            (
                "semantic",
                "s1.4x-semantic-errors-v1",
                "contract/fixtures/invalid/semantic-errors.v1.json",
                (
                    "contract/fixtures/invalid/"
                    "semantic-errors.expected.v1.json"
                ),
            ),
        ):
            actual = root / f"{matrix}.actual.json"
            comparison = root / f"{matrix}.comparison.json"
            _write_json(
                actual,
                {
                    "requestId": request_id,
                    "implementation": "haskell-ghc-9.10.3",
                    "results": [],
                },
            )
            _write_json(comparison, _comparison(request_id))
            comparison_artifacts.append(
                {
                    "matrixId": matrix,
                    "requestPath": request_path,
                    "requestSha256": _sha256(self._repo(request_path)),
                    "expectedPath": expected_path,
                    "expectedSha256": _sha256(self._repo(expected_path)),
                    "actualPath": actual.relative_to(self.raw).as_posix(),
                    "actualSha256": _sha256(actual),
                    "comparisonPath": (
                        comparison.relative_to(self.raw).as_posix()
                    ),
                    "comparisonSha256": _sha256(comparison),
                    "mismatchCount": 0,
                    "status": "PASS",
                }
            )
        candidate_binary = "8" * 64
        correctness = {
            "schemaVersion": "s1.4x-haskell-full-correctness-v1",
            "candidateSourceCommit": self.commit,
            "profileId": profile,
            "ghcOptions": selected["ghcOptions"],
            "optionsSha256": selected["optionsSha256"],
            "compilerVersion": "9.10.3",
            "compilerSha256": selected["compilerSha256"],
            "sourceTreeSha256": selected["sourceTreeSha256"],
            "candidateBinarySha256": candidate_binary,
            "commands": commands,
            "comparisonArtifacts": comparison_artifacts,
            "mismatchCount": 0,
            "status": "PASS",
        }
        receipt = root / "correctness-receipt.v1.json"
        _write_json(receipt, correctness)
        return {
            "profile": profile,
            "selected": selected,
            "selectedPath": selected_path,
            "correctness": correctness,
            "candidateBinarySha256": candidate_binary,
        }

    def _materialize_oci(
        self,
        scala: dict[str, Any],
        haskell: dict[str, Any],
    ) -> None:
        fixture_tree_sha256 = _object(
            self.raw / "large-fixture-receipt.json"
        )["fixtureTreeSha256"]
        docker_identity = {
            "dockerCliPathId": "DOCKER_28",
            "dockerCliSha256": "9" * 64,
            "contextName": "default",
            "daemonId": "daemon",
            "serverVersion": "28.0.0",
            "operatingSystem": "linux",
            "architecture": "x86_64",
        }
        base_reference = "eclipse-temurin@sha256:" + "1" * 64
        base_id = "sha256:" + "2" * 64
        image_id = "sha256:" + "3" * 64
        build = {
            "schemaVersion": "s1.4x-scala-oci-build-result-v2",
            "baseImageReference": base_reference,
            "baseImageReferenceSource": "caller-digest-argument",
            "baseImageId": base_id,
            "candidateSha256": _sha256(scala["candidate"]),
            "containerfileSha256": _sha256(
                self._repo("scala/Containerfile")
            ),
            "fixtureTreeSha256": fixture_tree_sha256,
            "imageId": image_id,
            "localTag": "s1-4x-scala:test",
            "dockerIdentity": docker_identity,
            "inspectedLabels": {
                "org.opencontainers.image.s1-4x.candidate-sha256": (
                    _sha256(scala["candidate"])
                ),
                "org.opencontainers.image.s1-4x.base-reference": (
                    base_reference
                ),
                "org.opencontainers.image.s1-4x.base-image-id": base_id,
                "org.opencontainers.image.s1-4x.containerfile-sha256": (
                    _sha256(self._repo("scala/Containerfile"))
                ),
                "org.opencontainers.image.s1-4x.fixture-tree-sha256": (
                    fixture_tree_sha256
                ),
            },
            "buildNetwork": "none",
            "pull": False,
            "buildUsedIidfile": True,
            "aggregateStatus": "PASS",
        }
        scala_build = self.raw / "oci/scala/scala-oci-build-result.v1.json"
        _write_json(scala_build, build)
        binding = {
            "schemaVersion": "s1.4x-scala-oci-runtime-binding-v1",
            "imageId": image_id,
            "buildReceiptSha256": _sha256(scala_build),
            "candidateSha256": _sha256(scala["candidate"]),
            "baseImageReference": base_reference,
            "baseImageId": base_id,
            "dockerIdentity": docker_identity,
            "status": "PASS",
        }
        binding_before = (
            self.raw
            / "oci/scala/runtime/oci-runtime-binding-before.v1.json"
        )
        binding_after = (
            self.raw
            / "oci/scala/runtime/oci-runtime-binding-after.v1.json"
        )
        _write_json(binding_before, binding)
        _write_json(binding_after, binding)
        scala_matrix_hashes: dict[str, str] = {}
        for matrix, request_id, actual_name in (
            (
                "canonical",
                "s1.4x-canonical-small-v1",
                "canonical-results.json",
            ),
            (
                "semantic",
                "s1.4x-semantic-errors-v1",
                "semantic-errors.json",
            ),
        ):
            actual = self.raw / "oci/scala/runtime" / actual_name
            comparison = (
                self.raw / f"oci/scala/runtime/{matrix}-comparison.json"
            )
            _write_json(
                actual,
                {
                    "requestId": request_id,
                    "implementation": "scala-3.8.4-jvm25",
                    "results": [],
                },
            )
            _write_json(comparison, _comparison(request_id))
            scala_matrix_hashes[f"{matrix}ResultSha256"] = _sha256(actual)
            scala_matrix_hashes[f"{matrix}ComparisonSha256"] = _sha256(
                comparison
            )
        _write_json(
            self.raw
            / "oci/scala/runtime/scala-oci-correctness-result.v1.json",
            {
                "schemaVersion": "s1.4x-scala-oci-correctness-result-v2",
                "imageId": image_id,
                "buildReceiptSha256": _sha256(scala_build),
                "candidateSha256": _sha256(scala["candidate"]),
                "baseImageReference": base_reference,
                "baseImageId": base_id,
                "dockerIdentity": docker_identity,
                "dockerIdentitySha256": _canonical_hash(docker_identity),
                "runtimeNetwork": "none",
                "readOnlyRoot": True,
                "capabilitiesDropped": "ALL",
                "sourceTreeMounted": False,
                "userHomeMounted": False,
                "credentialMounted": False,
                **scala_matrix_hashes,
                "runtimeBindingSha256": _sha256(binding_before),
                "mismatchCount": 0,
                "aggregateStatus": "PASS",
            },
        )

        haskell_comparisons = []
        for matrix, request_id in (
            ("canonical", "s1.4x-canonical-small-v1"),
            ("semantic", "s1.4x-semantic-errors-v1"),
        ):
            actual = (
                self.raw / f"oci/haskell/runtime/{matrix}.actual.json"
            )
            comparison = (
                self.raw / f"oci/haskell/{matrix}.oci-comparison.json"
            )
            _write_json(
                actual,
                {
                    "requestId": request_id,
                    "implementation": "haskell-ghc-9.10.3",
                    "results": [],
                },
            )
            _write_json(comparison, _comparison(request_id))
            haskell_comparisons.append(
                {
                    "matrixId": matrix,
                    "actualSha256": _sha256(actual),
                    "comparisonSha256": _sha256(comparison),
                    "mismatchCount": 0,
                    "status": "PASS",
                }
            )
        stdout = self.raw / "oci/haskell/oci-run.stdout"
        stderr = self.raw / "oci/haskell/oci-run.stderr"
        stdout.parent.mkdir(parents=True, exist_ok=True)
        stdout.write_text("oci PASS\n", encoding="utf-8")
        stderr.write_bytes(b"")
        haskell_image_id = "sha256:" + "5" * 64
        _write_json(
            self.raw / "oci/haskell/oci-correctness-receipt.v1.json",
            {
                "schemaVersion": "s1.4x-haskell-oci-correctness-v1",
                "status": "PASS",
                "candidateSourceCommit": self.commit,
                "sourceTreeSha256": haskell["selected"][
                    "sourceTreeSha256"
                ],
                "selectedProfileSha256": _sha256(
                    haskell["selectedPath"]
                ),
                "profileId": haskell["profile"],
                "ghcOptions": haskell["selected"]["ghcOptions"],
                "optionsSha256": haskell["selected"]["optionsSha256"],
                "containerfileSha256": _sha256(
                    self._repo("haskell/Containerfile")
                ),
                "baseImage": "haskell@sha256:" + "6" * 64,
                "baseImageId": "sha256:" + "7" * 64,
                "baseInspectionBeforeSha256": "1" * 64,
                "baseInspectionAfterSha256": "1" * 64,
                "stackRootPath": "CACHE_ROOT/stack",
                "stackWorkDir": ".stack-work",
                "contextSnapshot": {},
                "fixtureTreeSha256": fixture_tree_sha256,
                "candidateBinarySha256": haskell[
                    "candidateBinarySha256"
                ],
                "dockerPath": "/usr/bin/docker",
                "dockerPathId": "DOCKER_28",
                "dockerSha256": "3" * 64,
                "expectedDockerSha256": "3" * 64,
                "dockerConfigPath": "DOCKER_CONFIG/config.json",
                "dockerTrustBaseline": {},
                "dockerTrustStageSnapshots": [],
                "daemonIdentitySha256": "4" * 64,
                "dockerContextName": "default",
                "daemonIdentityBefore": {},
                "daemonIdentityAfter": {},
                "imageTag": "s1-4x-haskell:test",
                "imageId": haskell_image_id,
                "iidFileSha256": "5" * 64,
                "provenanceLabels": {},
                "platform": "linux/amd64",
                "runtimeImageSubject": {
                    "referenceType": "immutable-image-id",
                    "imageId": haskell_image_id,
                },
                "imageTagBindingChecks": [],
                "buildNetwork": "none",
                "runtimeNetwork": "none",
                "runtimeMounts": ["output-only"],
                "commands": [
                    {
                        "phase": "oci-run",
                        "exitCode": 0,
                        "stdoutSha256": _sha256(stdout),
                        "stderrSha256": _sha256(stderr),
                    }
                ],
                "comparisons": haskell_comparisons,
                "mismatchCount": 0,
            },
        )

    def _materialize_rubric_inputs(self) -> None:
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
        scala_lock_path = self._repo("scala/toolchain-lock.v1.json")
        scala_lock = _object(scala_lock_path)
        scalafmt = scala_lock["scalafmt"]
        artifact_fields = (
            "archiveUri",
            "archivePathId",
            "archiveSha256",
            "executablePathId",
            "executableSha256",
            "resolvedVersionOutput",
            "resolutionLogUri",
            "resolutionLogSha256",
            "networkPolicy",
        )
        _write_json(
            self.raw
            / "scala/scalafmt/scala-scalafmt-idempotence-result.v1.json",
            {
                "schemaVersion": (
                    "s1.4x-scala-scalafmt-idempotence-result-v1"
                ),
                "scalafmtVersion": scalafmt["version"],
                "scalafmtArtifact": {
                    **{
                        field: scalafmt[field]
                        for field in artifact_fields
                    },
                    "versionOutputSha256": _hash_bytes(
                        scalafmt["resolvedVersionOutput"].encode()
                    ),
                },
                "networkPolicy": scalafmt["networkPolicy"],
                "configSha256": scalafmt["configSha256"],
                "sourceInputManifestSha256": _sha256(
                    self._repo("scala/source-inputs.v1.json")
                ),
                "toolchainLockSha256": _sha256(scala_lock_path),
                "checkedFiles": ["src/main/scala/Core.scala"],
                "copiedNonMutatingCheck": {
                    "exitCode": 0,
                    "downloadLineCount": 0,
                    "portableArgv": ["SCALAFMT", "--check"],
                    "portableArgvSha256": "8" * 64,
                    "evidenceSha256": "9" * 64,
                },
                "status": "PASS",
            },
        )
        _write_json(
            self.raw
            / "scala/scalafix/scala-semantic-policy-receipt.v1.json",
            {
                "schemaVersion": (
                    "s1.4x-scala-semantic-policy-receipt-v1"
                ),
                "sourceInputManifestSha256": _sha256(
                    self._repo("scala/source-inputs.v1.json")
                ),
                "checkerMode": "semanticdb",
                "semanticSmokeStatus": "PASS",
                "checkedFiles": ["src/main/scala/Core.scala"],
                "scalafix": {
                    "binarySha256": scala_lock["scalafix"][
                        "binarySha256"
                    ],
                    "version": scala_lock["scalafix"]["version"],
                },
                "status": "PASS",
            },
        )
        _write_json(
            self.raw
            / "scala/hard-compiler-B/scala-hard-compiler-result.v1.json",
            {
                "schemaVersion": "s1.4x-scala-hard-compiler-result-v1",
                "profileId": "B",
                "compileInputPaths": ["src/main/scala/Core.scala"],
                "aggregateStatus": "PASS",
            },
        )
        _write_json(
            self.raw
            / "haskell/module-safety/haskell-module-safety-result.v1.json",
            {
                "schemaVersion": (
                    "s1.4x-haskell-module-safety-result-v1"
                ),
                "modules": [
                    {
                        "moduleName": "S14X.Core",
                        "category": "safe-scalar",
                    }
                ],
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
        haskell_lock = _object(
            self._repo("haskell/toolchain-lock.v1.json")
        )
        stylish = haskell_lock["resolvedTools"]["stylishHaskell"]
        hlint = haskell_lock["resolvedTools"]["hlint"]
        source_manifest_sha = _sha256(
            self._repo("haskell/source-inputs.v1.json")
        )
        _write_json(
            self.raw / "haskell/format/receipt.json",
            {
                "schemaVersion": "s1.4x-haskell-format-evidence-v1",
                "formatterPathId": stylish["pathId"],
                "formatterSha256": stylish["sha256"],
                "formatterVersion": stylish["version"],
                "sourceInputManifestSha256": source_manifest_sha,
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
                "hlintPathId": hlint["pathId"],
                "hlintSha256": hlint["sha256"],
                "hlintVersion": hlint["version"],
                "sourceInputManifestSha256": source_manifest_sha,
                "sourceInputFileCount": 1,
                "negativeFixtureCount": 12,
                "status": "PASS",
            },
        )

    def _materialize_rubric_audits(self) -> None:
        self._write_run_manifest()
        closure = assembler.RawClosure(
            self.raw,
            subject=self.commit,
        )
        try:
            contracts = assembler._expected_rubric_contracts(  # noqa: SLF001
                closure,
                self.repository,
                subject=self.commit,
            )
        finally:
            closure.close()
        (self.raw / assembler.RUN_MANIFEST).unlink()
        for candidate in CANDIDATES:
            entries = [
                {
                    "rubricId": rubric_id,
                    **contracts[candidate][rubric_id],
                    "findings": [],
                    "status": "PASS",
                }
                for rubric_id in sorted(
                final_audit.RUBRIC_EVIDENCE,
                    key=str.encode,
                )
            ]
            _write_json(
                self.raw
                / f"rubric-audit/{candidate}-candidate-rubric-audit.v1.json",
                {
                    "schemaVersion": (
                        "s1.4x-candidate-rubric-audit-v1"
                    ),
                    "benchmarkSubjectCommit": self.commit,
                    "candidate": candidate,
                    "rubrics": entries,
                    "status": "PASS",
                },
            )

    def _write_run_manifest(self) -> None:
        manifest = self.raw / assembler.RUN_MANIFEST
        manifest.unlink(missing_ok=True)
        artifacts = [
            {
                "path": path.relative_to(self.raw).as_posix(),
                "sha256": _sha256(path),
                "sizeBytes": path.stat().st_size,
            }
            for path in sorted(
                (
                    item
                    for item in self.raw.rglob("*")
                    if item.is_file() and not item.is_symlink()
                ),
                key=lambda item: item.relative_to(self.raw).as_posix().encode(),
            )
        ]
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

    def _rebind_scala_coverage_profile(self, profile: str) -> None:
        execution_path = (
            self.raw
            / "coverage/scala/scala-property-execution-evidence.v1.json"
        )
        execution = _object(execution_path)
        execution["toolchainProfile"] = profile
        _write_json(execution_path, execution)
        property_path = (
            self.raw / "coverage/scala/scala-property-report.v1.json"
        )
        registry_path = (
            self.raw / "coverage/scala/scala-registry-report.v1.json"
        )
        derived = coverage_gate.validate_candidate_coverage(
            implementation_label="scala",
            property_plan_path=self._repo(
                "contract/property-plan.v1.json"
            ),
            function_registry_path=self._repo(
                "contract/function-registry.v1.json"
            ),
            error_registry_path=self._repo(
                "contract/error-registry.v1.json"
            ),
            property_report=_object(property_path),
            registry_report=_object(registry_path),
            execution_report=execution,
        )
        receipt_path = self.raw / "coverage/scala-coverage-receipt.json"
        receipt = _object(receipt_path)
        receipt["artifacts"] = [
            {
                "path": path.name,
                "sha256": _sha256(path),
                "sizeBytes": path.stat().st_size,
            }
            for path in (property_path, registry_path, execution_path)
        ]
        receipt["coverage"] = derived
        _write_json(receipt_path, receipt)
        integration_path = self.raw / "coverage/integration-coverage.json"
        integration = _object(integration_path)
        integration["candidates"][0] = derived
        _write_json(integration_path, integration)

    def _select_scala_fallback_a(self) -> None:
        self._rebind_scala_coverage_profile("A")
        source = self.raw / "scala/profiles/B"
        destination = self.raw / "scala/profiles/A"
        shutil.copytree(source, destination)
        property_execution = (
            destination
            / "property/scala-property-execution-evidence.v1.json"
        )
        property_execution.write_bytes(
            (
                self.raw
                / "coverage/scala/"
                "scala-property-execution-evidence.v1.json"
            ).read_bytes()
        )
        correctness_path = (
            destination / "scala-profile-correctness-result.v1.json"
        )
        correctness = _object(correctness_path)
        options = _object(
            self._repo("scala/compiler-profiles.v1.json")
        )["profiles"]["A"]["additionalOptions"]
        correctness.update(
            {
                "profileId": "A",
                "profileOptions": options,
                "profileOptionsSha256": _canonical_hash(options),
            }
        )
        correctness["matrix"]["propertyExecutionEvidenceSha256"] = _sha256(
            property_execution
        )
        _write_json(correctness_path, correctness)
        selected_path = self.raw / "scala/scala-selected-profile-result.v1.json"
        selected = _object(selected_path)
        selected.update(
            {
                "selectedProfileOptions": options,
                "selectedProfileOptionsSha256": _canonical_hash(options),
                "correctnessResultSha256": _sha256(correctness_path),
                "selectedProfileId": "A",
                "fallbackExecuted": True,
            }
        )
        _write_json(selected_path, selected)
        hard_b = (
            self.raw
            / "scala/hard-compiler-B/scala-hard-compiler-result.v1.json"
        )
        hard_a = (
            self.raw
            / "scala/hard-compiler-A/scala-hard-compiler-result.v1.json"
        )
        hard = _object(hard_b)
        hard["profileId"] = "A"
        _write_json(hard_a, hard)
        shutil.rmtree(self.raw / "rubric-audit")
        self._materialize_rubric_audits()
        self._write_run_manifest()

    def _assemble(self, output: Path | None = None) -> dict[str, Any]:
        return assembler.assemble_final_candidate_evidence(
            repository_root=self.repository,
            benchmark_subject_commit=self.commit,
            correctness_root=self.raw,
            production_regression_receipt=Path(
                "regression/production-compound-receipt.v1.json"
            ),
            research_regression_receipt=Path(
                "regression/research-compound-receipt.v1.json"
            ),
            candidate_rubric_audit=Path("rubric-audit"),
            output_root=output or self.output,
        )

    def test_strict_projection_is_deterministic_and_final_audit_accepts(
        self,
    ) -> None:
        first = self._assemble()
        second_root = self.temporary / "audit-second"
        second = self._assemble(second_root)
        self.assertEqual(first, second)
        self.assertEqual(
            _object(
                self.output
                / "sources/scala/toolchain-reproducibility.json"
            )["selectedProfileId"],
            "B",
        )
        for candidate in CANDIDATES:
            envelopes = sorted(
                (self.output / "evidence" / candidate).glob("*.json")
            )
            self.assertEqual(len(envelopes), 20)
            self.assertEqual(
                {path.stem for path in envelopes},
                set(EXPECTED_EVIDENCE_CLAIMS),
            )
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

    def test_haskell_composite_receipt_is_accepted_and_tamper_rejected(
        self,
    ) -> None:
        process_stderr = self._rebind_haskell_coverage_as_composite_v2()
        summary = self._assemble(self.temporary / "composite-v2")
        self.assertEqual(summary["status"], "PASS")

        process_stderr.write_bytes(
            process_stderr.read_bytes().replace(
                b"expected one argument\n",
                b"expected one argument!\n",
            )
        )
        receipt_path = self.raw / "coverage/haskell-coverage-receipt.json"
        receipt = _object(receipt_path)
        receipt["process"]["stderr"].update(
            {
                "sha256": _sha256(process_stderr),
                "sizeBytes": process_stderr.stat().st_size,
            }
        )
        _write_json(receipt_path, receipt)
        self._write_run_manifest()
        with self.assertRaisesRegex(
            assembler.EvidenceAssemblyError,
            "HASKELL_COVERAGE_RECEIPT_INVALID",
        ):
            self._assemble(self.temporary / "composite-v2-tampered")

    def test_rejects_coverage_runner_source_profile_and_receipt_drift(
        self,
    ) -> None:
        execution_path = (
            self.raw
            / "coverage/scala/scala-property-execution-evidence.v1.json"
        )
        receipt_path = self.raw / "coverage/scala-coverage-receipt.json"
        execution = _object(execution_path)
        receipt = _object(receipt_path)
        mutations = (
            (
                "source",
                execution_path,
                {**execution, "sourceClosureSha256": "0" * 64},
            ),
            (
                "profile",
                execution_path,
                {**execution, "toolchainProfile": "A"},
            ),
            (
                "runner",
                receipt_path,
                {
                    **receipt,
                    "runner": {
                        **receipt["runner"],
                        "sha256": "0" * 64,
                    },
                },
            ),
        )
        for label, path, changed in mutations:
            with self.subTest(label=label):
                baseline = path.read_bytes()
                _write_json(path, changed)
                self._write_run_manifest()
                with self.assertRaises(assembler.EvidenceAssemblyError):
                    self._assemble(self.temporary / f"coverage-{label}")
                path.write_bytes(baseline)
        self._write_run_manifest()

    def test_rejects_compiler_formatter_lint_and_generated_cabal_drift(
        self,
    ) -> None:
        scala_selected = (
            self.raw / "scala/scala-selected-profile-result.v1.json"
        )
        haskell_correctness = (
            self.raw
            / "haskell/profiles/optimized-o2-fasm/"
            "correctness-receipt.v1.json"
        )
        scala_format = (
            self.raw
            / "scala/scalafmt/scala-scalafmt-idempotence-result.v1.json"
        )
        haskell_lint = self.raw / "haskell/hlint/receipt.json"
        generated = self.raw / assembler.HASKELL_GENERATED_CABAL
        cases: tuple[
            tuple[
                str,
                Path,
                Callable[[dict[str, Any]], dict[str, Any]],
            ],
            ...,
        ] = (
            (
                "scala-java",
                scala_selected,
                lambda value: {
                    **value,
                    "javaExecutableSha256": "0" * 64,
                },
            ),
            (
                "haskell-ghc",
                haskell_correctness,
                lambda value: {
                    **value,
                    "compilerSha256": "0" * 64,
                },
            ),
            (
                "scalafmt-archive",
                scala_format,
                lambda value: {
                    **value,
                    "scalafmtArtifact": {
                        **value["scalafmtArtifact"],
                        "archiveSha256": "0" * 64,
                    },
                },
            ),
            (
                "hlint",
                haskell_lint,
                lambda value: {**value, "hlintSha256": "0" * 64},
            ),
        )
        for label, path, mutate in cases:
            with self.subTest(label=label):
                baseline = path.read_bytes()
                _write_json(path, mutate(_object(path)))
                self._write_run_manifest()
                with self.assertRaises(assembler.EvidenceAssemblyError):
                    self._assemble(self.temporary / f"tool-{label}")
                path.write_bytes(baseline)
        baseline_generated = generated.read_bytes()
        generated.write_bytes(baseline_generated + b"-- tamper\n")
        self._write_run_manifest()
        with self.assertRaises(assembler.EvidenceAssemblyError):
            self._assemble(self.temporary / "generated-cabal")
        generated.write_bytes(baseline_generated)
        self._write_run_manifest()

        provenance = self.raw / assembler.HASKELL_CABAL_PROVENANCE
        provenance_value = _object(provenance)
        cabal_mutations = (
            (
                "buildable-false",
                generated,
                baseline_generated.replace(
                    b"\nlibrary\n",
                    b"\nlibrary\n  buildable: False\n",
                    1,
                ),
            ),
            (
                "unsafe-extra-semantics",
                generated,
                baseline_generated.replace(
                    b"\nlibrary\n",
                    b"\nlibrary\n  cpp-options: -DUNSAFE\n",
                    1,
                ),
            ),
            (
                "hpack-version",
                provenance,
                _canonical(
                    {
                        **provenance_value,
                        "hpack": {
                            "version": "0.39.7",
                            "versionOutputSha256": _hash_bytes(b"0.39.7\n"),
                        },
                    }
                ),
            ),
            (
                "portable-argv",
                provenance,
                _canonical(
                    {
                        **provenance_value,
                        "build": {
                            **provenance_value["build"],
                            "portableArgv": ["stack", "build"],
                            "portableArgvSha256": _canonical_hash(
                                ["stack", "build"]
                            ),
                        },
                    }
                ),
            ),
        )
        for label, path, payload in cabal_mutations:
            with self.subTest(cabal_attack=label):
                baseline = path.read_bytes()
                path.write_bytes(payload)
                self._write_run_manifest()
                with self.assertRaises(assembler.EvidenceAssemblyError):
                    self._assemble(self.temporary / f"cabal-{label}")
                path.write_bytes(baseline)
        self._write_run_manifest()

    def test_rejects_large_payload_regression_and_rubric_drift(self) -> None:
        large = (
            self.raw
            / "large-fixtures/large/generated/fixture-0.f64le"
        )
        regression = (
            self.raw / "regression/research-compound-receipt.v1.json"
        )
        rubric = (
            self.raw
            / "rubric-audit/scala-candidate-rubric-audit.v1.json"
        )
        cases: list[tuple[str, Path, bytes]] = []
        cases.append(("large", large, large.read_bytes() + b"tamper"))
        regression_value = _object(regression)
        cases.append(
            (
                "regression",
                regression,
                _canonical(
                    {
                        **regression_value,
                        "replacementNodeIds": ["wrong::one", "wrong::two"],
                    }
                ),
            )
        )
        rubric_value = _object(rubric)
        rubric_value["rubrics"][0]["status"] = "PASS"
        rubric_value["rubrics"][0]["findings"] = ["self-awarded"]
        cases.append(("rubric", rubric, _canonical(rubric_value)))
        for label, path, payload in cases:
            with self.subTest(label=label):
                baseline = path.read_bytes()
                path.write_bytes(payload)
                self._write_run_manifest()
                with self.assertRaises(assembler.EvidenceAssemblyError):
                    self._assemble(self.temporary / f"raw-{label}")
                path.write_bytes(baseline)
        self._write_run_manifest()

    def test_rejects_cross_aggregate_oci_fixture_and_rubric_spec_drift(
        self,
    ) -> None:
        canonical_summary = (
            self.raw / "cross-language/canonical/correctness-summary.json"
        )
        selected_comparison = (
            self.raw / "cross-language/selected-comparison.json"
        )
        scala_oci = (
            self.raw / "oci/scala/scala-oci-build-result.v1.json"
        )
        haskell_oci = (
            self.raw / "oci/haskell/oci-correctness-receipt.v1.json"
        )
        rubric = (
            self.raw
            / "rubric-audit/scala-candidate-rubric-audit.v1.json"
        )
        summary_value = _object(canonical_summary)
        scala_oci_value = _object(scala_oci)
        haskell_oci_value = _object(haskell_oci)
        rubric_value = _object(rubric)
        invented_objective = json.loads(json.dumps(rubric_value))
        invented_objective["rubrics"][0]["objectiveChecks"] = [
            "invented.objective.pass"
        ]
        invented_artifact = json.loads(json.dumps(rubric_value))
        invented_artifact["rubrics"][0]["reviewedArtifacts"] = [
            {
                "path": "contract-validation.json",
                "sha256": _sha256(self.raw / "contract-validation.json"),
            }
        ]
        cases = (
            (
                "aggregate-result-binding",
                canonical_summary,
                _canonical(
                    {
                        **summary_value,
                        "artifacts": {
                            **summary_value["artifacts"],
                            "scala-results.json": "0" * 64,
                        },
                    }
                ),
            ),
            (
                "selected-comparison",
                selected_comparison,
                _canonical(_comparison("s1.4x-semantic-errors-v1")),
            ),
            (
                "scala-fixture-tree",
                scala_oci,
                _canonical(
                    {
                        **scala_oci_value,
                        "fixtureTreeSha256": "0" * 64,
                        "inspectedLabels": {
                            **scala_oci_value["inspectedLabels"],
                            (
                                "org.opencontainers.image."
                                "s1-4x.fixture-tree-sha256"
                            ): "0" * 64,
                        },
                    }
                ),
            ),
            (
                "haskell-fixture-tree",
                haskell_oci,
                _canonical(
                    {
                        **haskell_oci_value,
                        "fixtureTreeSha256": "0" * 64,
                    }
                ),
            ),
            (
                "rubric-objective",
                rubric,
                _canonical(invented_objective),
            ),
            (
                "rubric-artifact",
                rubric,
                _canonical(invented_artifact),
            ),
        )
        for label, path, payload in cases:
            with self.subTest(binding_attack=label):
                baseline = path.read_bytes()
                path.write_bytes(payload)
                self._write_run_manifest()
                with self.assertRaises(assembler.EvidenceAssemblyError):
                    self._assemble(self.temporary / f"binding-{label}")
                path.write_bytes(baseline)
        self._write_run_manifest()

    def test_coverage_uses_immutable_subject_snapshots_during_live_aba(
        self,
    ) -> None:
        live_plan = self._repo("contract/property-plan.v1.json")
        baseline = live_plan.read_bytes()
        original = coverage_gate.validate_candidate_coverage
        calls = 0

        def validate_from_snapshot(**arguments: Any) -> dict[str, Any]:
            nonlocal calls
            calls += 1
            property_plan_path = cast(Path, arguments["property_plan_path"])
            self.assertFalse(property_plan_path.is_relative_to(self.repository))
            self.assertEqual(property_plan_path.read_bytes(), baseline)
            live_plan.write_bytes(b'{"forged":"live-path"}\n')
            try:
                return original(**arguments)
            finally:
                live_plan.write_bytes(baseline)

        with patch.object(
            coverage_gate,
            "validate_candidate_coverage",
            side_effect=validate_from_snapshot,
        ):
            summary = self._assemble(self.temporary / "coverage-subject-snapshot")
        self.assertEqual(calls, 2)
        self.assertEqual(summary["status"], "PASS")
        self.assertEqual(live_plan.read_bytes(), baseline)

    def test_scala_fallback_a_is_positive_and_fallback_flag_is_exact(
        self,
    ) -> None:
        selected_path = (
            self.raw / "scala/scala-selected-profile-result.v1.json"
        )
        initial = selected_path.read_bytes()
        selected = _object(selected_path)
        selected["fallbackExecuted"] = True
        _write_json(selected_path, selected)
        self._write_run_manifest()
        with self.assertRaises(assembler.EvidenceAssemblyError):
            self._assemble(self.temporary / "forged-b-fallback")
        selected_path.write_bytes(initial)
        self._write_run_manifest()

        self._select_scala_fallback_a()
        summary = self._assemble(self.temporary / "valid-a-fallback")
        self.assertEqual(summary["status"], "PASS")
        self.assertEqual(
            _object(
                self.temporary
                / "valid-a-fallback/sources/scala/"
                "toolchain-reproducibility.json"
            )["selectedProfileId"],
            "A",
        )

        fallback_a = _object(selected_path)
        fallback_a["fallbackExecuted"] = False
        _write_json(selected_path, fallback_a)
        self._write_run_manifest()
        with self.assertRaises(assembler.EvidenceAssemblyError):
            self._assemble(self.temporary / "forged-a-no-fallback")

    def test_failure_cleanup_raw_mutation_and_symlink_swap_never_publish(
        self,
    ) -> None:
        original_write = assembler._write_exclusive  # noqa: SLF001
        writes = 0

        def fail_second(path: Path, payload: bytes) -> None:
            nonlocal writes
            writes += 1
            if writes == 2:
                raise assembler.EvidenceAssemblyError("INJECTED_WRITE_FAILURE")
            original_write(path, payload)

        partial_output = self.temporary / "partial-audit"
        with (
            patch.object(assembler, "_write_exclusive", side_effect=fail_second),
            self.assertRaises(assembler.EvidenceAssemblyError),
        ):
            self._assemble(partial_output)
        self.assertFalse(partial_output.exists())
        self.assertEqual(
            list(self.temporary.glob(".partial-audit.assembly-*")),
            [],
        )

        raw_mutation_output = self.temporary / "raw-mutation-audit"
        injected_raw = self.raw / "late-injected.bin"
        original_copy = assembler._copy_reviewed  # noqa: SLF001

        def copy_then_mutate(
            output_root: Path,
            snapshots: Sequence[assembler.Snapshot],
        ) -> dict[Path, dict[str, str]]:
            copied = original_copy(output_root, snapshots)
            injected_raw.write_bytes(b"late mutation")
            return copied

        try:
            with (
                patch.object(
                    assembler,
                    "_copy_reviewed",
                    side_effect=copy_then_mutate,
                ),
                self.assertRaises(assembler.EvidenceAssemblyError),
            ):
                self._assemble(raw_mutation_output)
        finally:
            injected_raw.unlink(missing_ok=True)
        self.assertFalse(raw_mutation_output.exists())
        self.assertEqual(
            list(self.temporary.glob(".raw-mutation-audit.assembly-*")),
            [],
        )

        sentinel = self.temporary / "sentinel"
        sentinel.mkdir()
        swapped_output = self.temporary / "swapped-audit"
        original_rename = assembler._rename_no_replace  # noqa: SLF001

        def swap_then_publish(
            source: Path,
            destination: Path,
            *,
            expected_source_identity: tuple[int, int],
        ) -> None:
            destination.symlink_to(sentinel, target_is_directory=True)
            original_rename(
                source,
                destination,
                expected_source_identity=expected_source_identity,
            )

        with (
            patch.object(
                assembler,
                "_rename_no_replace",
                side_effect=swap_then_publish,
            ),
            self.assertRaises(assembler.EvidenceAssemblyError),
        ):
            self._assemble(swapped_output)
        self.assertTrue(swapped_output.is_symlink())
        self.assertEqual(list(sentinel.iterdir()), [])
        self.assertEqual(
            list(self.temporary.glob(".swapped-audit.assembly-*")),
            [],
        )

        source_swap_output = self.temporary / "source-swapped-audit"
        source_swap_sentinel = self.temporary / "source-swap-sentinel"
        source_swap_sentinel.mkdir()
        original_rename_again = assembler._rename_no_replace  # noqa: SLF001

        def swap_source_then_publish(
            source: Path,
            destination: Path,
            *,
            expected_source_identity: tuple[int, int],
        ) -> None:
            quarantine = source.with_name(f"{source.name}.quarantine")
            source.rename(quarantine)
            source.symlink_to(source_swap_sentinel, target_is_directory=True)
            try:
                original_rename_again(
                    source,
                    destination,
                    expected_source_identity=expected_source_identity,
                )
            finally:
                source.unlink(missing_ok=True)
                quarantine.rename(source)

        with (
            patch.object(
                assembler,
                "_rename_no_replace",
                side_effect=swap_source_then_publish,
            ),
            self.assertRaises(assembler.EvidenceAssemblyError),
        ):
            self._assemble(source_swap_output)
        self.assertFalse(source_swap_output.exists())
        self.assertEqual(list(source_swap_sentinel.iterdir()), [])
        self.assertEqual(
            list(self.temporary.glob(".source-swapped-audit.assembly-*")),
            [],
        )

    def test_raw_root_clone_swap_restore_aba_is_rejected(self) -> None:
        original_copy = assembler._copy_reviewed  # noqa: SLF001

        def clone_swap_restore(
            output_root: Path,
            snapshots: Sequence[assembler.Snapshot],
        ) -> dict[Path, dict[str, str]]:
            copied = original_copy(output_root, snapshots)
            pinned = self.raw.with_name("raw-correctness-pinned")
            self.raw.rename(pinned)
            shutil.copytree(pinned, self.raw)
            shutil.rmtree(self.raw)
            pinned.rename(self.raw)
            return copied

        output = self.temporary / "raw-aba-audit"
        with (
            patch.object(
                assembler,
                "_copy_reviewed",
                side_effect=clone_swap_restore,
            ),
            self.assertRaises(assembler.EvidenceAssemblyError),
        ):
            self._assemble(output)
        self.assertFalse(output.exists())
        self.assertEqual(
            list(self.temporary.glob(".raw-aba-audit.assembly-*")),
            [],
        )
