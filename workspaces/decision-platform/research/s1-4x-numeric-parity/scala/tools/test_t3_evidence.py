#!/usr/bin/env python3
"""Scala T3 portable evidence contracts and frozen selector regression tests."""

from __future__ import annotations

import importlib.util
import hashlib
import json
import math
import sys
from pathlib import Path


SCALA_ROOT = Path(__file__).resolve().parents[1]
S1_ROOT = SCALA_ROOT.parent
TOOLS_ROOT = SCALA_ROOT / "tools"
SHA = "1" * 64
PROFILE_OPTIONS = {
    "A": [],
    "B": ["-opt"],
    "C": ["-opt", "-opt-inline:ai.trading.coach.s14x.**"],
}


def canonical_sha256(value: object) -> str:
    payload = json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def load_module():
    path = TOOLS_ROOT / "t3_evidence.py"
    specification = importlib.util.spec_from_file_location("t3_evidence", path)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    sys.modules["t3_evidence"] = module
    specification.loader.exec_module(module)
    return module


def semantic_receipt(policy: dict, matrix: dict) -> dict:
    checked = ["project.scala", "src/main/scala/example.scala"]
    negative = []
    for index, fixture in enumerate(matrix["fixtures"]):
        expected = fixture["expectedSymbol"]
        disposition = fixture["expectedDisposition"]
        detected = []
        if disposition == "SEMANTIC_REJECT" and expected != "ExplicitResultTypes":
            detected = [
                {
                    "policySymbol": expected,
                    "resolvedSymbol": f"resolved/{index}.",
                }
            ]
        negative.append(
            {
                "fixtureId": fixture["fixtureId"],
                "expectedPolicySymbol": expected,
                "expectedDisposition": disposition,
                "detectedResolvedSymbols": detected,
                "commandArgvSha256": f"{index + 2:064x}",
                "exitCode": 1,
                "stdoutSha256": SHA,
                "stderrSha256": SHA,
                "evidenceSha256": f"{index + 100:064x}",
                "status": "PASS",
            }
        )
    execution = {
        "cleanSyntactic": {
            "commandArgvSha256": "a" * 64,
            "exitCode": 0,
            "stdoutSha256": SHA,
            "stderrSha256": SHA,
            "evidenceSha256": "b" * 64,
        },
        "cleanExplicitResultTypes": {
            "commandArgvSha256": "c" * 64,
            "exitCode": 0,
            "stdoutSha256": SHA,
            "stderrSha256": SHA,
            "evidenceSha256": "d" * 64,
        },
        "cleanCustomSemanticRule": {
            "commandArgvSha256": "e" * 64,
            "exitCode": 0,
            "stdoutSha256": SHA,
            "stderrSha256": SHA,
            "evidenceSha256": "f" * 64,
        },
    }
    return {
        "schemaVersion": "s1.4x-scala-semantic-policy-receipt-v1",
        "policySha256": "2" * 64,
        "sourceInputManifestSha256": "3" * 64,
        "checkedFiles": checked,
        "sourceTreeSha256": "4" * 64,
        "checkerMode": "semanticdb",
        "semanticSmokeStatus": "PASS",
        "semanticdb": {
            "rootPath": "/local/evidence/semanticdb",
            "rootSha256": "5" * 64,
            "fileCount": len(checked),
            "classpathSha256": "6" * 64,
            "compileCommandArgvSha256": "7" * 64,
        },
        "scalafix": {
            "binaryPath": "/local/scalafix",
            "binarySha256": "8" * 64,
            "version": "0.14.7",
            "commandArgvSha256": "9" * 64,
            "explicitResultTypesCommandArgvSha256": "c" * 64,
            "customRuleCommandArgvSha256": "e" * 64,
            "syntacticCommandArgvSha256": "a" * 64,
        },
        "rule": {
            "sourcePath": "/local/rule.scala",
            "sourceSha256": "0" * 64,
            "classpathSha256": "a" * 64,
        },
        "execution": {
            "startedAt": "2026-07-18T00:00:00.000000Z",
            "finishedAt": "2026-07-18T00:00:01.000000Z",
            **execution,
        },
        "negativeMatrix": negative,
        "status": "PASS",
    }


def qualification(plan: dict, scores: dict[str, float]) -> dict:
    policy = plan["scalaProfileQualification"]
    blocks = []
    for repetition, order in enumerate(policy["profileOrderBlocks"], start=1):
        measurements = []
        for profile in order:
            for case_id in policy["qualificationCaseOrder"]:
                measurements.append(
                    {
                        "profileId": profile,
                        "caseId": case_id,
                        "scoreNsPerInvocation": scores[profile],
                    }
                )
        blocks.append(
            {
                "outerRepetition": repetition,
                "profileOrder": order,
                "hostValiditySha256": f"{repetition:064x}",
                "effectiveJvmArgsSha256": SHA,
                "profileEvidence": [
                    {
                        "profileId": profile,
                        "hostValiditySha256": f"{repetition + index:064x}",
                        "effectiveJvmArgsSha256": SHA,
                        "caseCount": len(policy["qualificationCaseOrder"]),
                    }
                    for index, profile in enumerate(order)
                ],
                "measurements": measurements,
            }
        )
    return {
        "schemaVersion": "s1.4x-scala-profile-qualification-v1",
        "benchmarkPlanSha256": SHA,
        "selectorConfigSha256": "2" * 64,
        "sourceInputManifestSha256": "3" * 64,
        "profileOptionsSha256": canonical_sha256(PROFILE_OPTIONS),
        "blocks": blocks,
        "status": "PASS",
    }


def correctness() -> dict:
    return {
        profile: {
            "schemaVersion": "s1.4x-scala-profile-correctness-v1",
            "profileId": profile,
            "profileOptions": PROFILE_OPTIONS[profile],
            "profileOptionsSha256": canonical_sha256(PROFILE_OPTIONS[profile]),
            "sourceInputManifestSha256": "3" * 64,
            "candidateSha256": f"{index + 10:064x}",
            "mismatchCount": 0,
            "status": "PASS",
        }
        for index, profile in enumerate(("A", "B", "C"))
    }


def capability_evidence(plan: dict) -> dict:
    smokes = plan["languages"]["scala"]["smokes"]
    return {
        item["smokeId"]: {
            "compilerStatus": "stable",
            "argv": ["portable-command", item["smokeId"]],
            "exitCode": 0,
            "stdoutSha256": SHA,
            "stderrSha256": SHA,
            "artifactSha256": f"{index + 1:064x}",
            "status": "PASS",
            "disposition": "ADOPT",
            "provenFallback": item["provenFallback"],
            "fallbackExecuted": False,
        }
        for index, item in enumerate(smokes)
    }


def feature_evidence(planned: dict) -> dict:
    evidence = {}
    for index, item in enumerate(
        entry
        for entry in planned["entries"]
        if entry["featureId"].startswith("scala.")
    ):
        decision = item["decision"]
        if decision == "REJECT":
            effective = "REJECT"
            smoke = lint = tests = evidence_status = "NOT_APPLICABLE"
        elif decision == "PROBE_ONLY":
            effective = "PROBE_ONLY"
            smoke = lint = tests = evidence_status = "PASS"
        else:
            effective = "ADOPT"
            smoke = lint = tests = evidence_status = "PASS"
        evidence[item["featureId"]] = {
            "plannedDecision": decision,
            "effectiveDecision": effective,
            "smokeStatus": smoke,
            "lintStatus": lint,
            "testStatus": tests,
            "parityMismatchCount": 0,
            "evidenceStatus": evidence_status,
            "fallbackExecuted": False,
            "fallbackStatus": "NOT_RUN",
            "evidenceSha256": f"{index + 1:064x}",
        }
    return evidence


def main() -> int:
    module = load_module()
    policy = json.loads((S1_ROOT / "contract/scala-source-policy.v1.json").read_text())
    matrix = json.loads(
        (TOOLS_ROOT / "fixtures/source-policy-negative.v1.json").read_text()
    )
    receipt = semantic_receipt(policy, matrix)
    module.validate_semantic_receipt(
        receipt,
        policy=policy,
        matrix=matrix,
        policy_sha256="2" * 64,
        manifest_sha256="3" * 64,
        source_tree_sha256="4" * 64,
        checked_files=receipt["checkedFiles"],
        scalafix_binary_sha256="8" * 64,
        rule_source_sha256="0" * 64,
    )
    tampered = json.loads(json.dumps(receipt))
    tampered["negativeMatrix"][0]["detectedResolvedSymbols"] = []
    try:
        module.validate_semantic_receipt(
            tampered,
            policy=policy,
            matrix=matrix,
            policy_sha256="2" * 64,
            manifest_sha256="3" * 64,
            source_tree_sha256="4" * 64,
            checked_files=receipt["checkedFiles"],
            scalafix_binary_sha256="8" * 64,
            rule_source_sha256="0" * 64,
        )
    except module.T3EvidenceError:
        pass
    else:
        raise AssertionError("resolved-symbol closure tamper passed")

    plan = json.loads((S1_ROOT / "benchmarks/benchmark-plan.v1.json").read_text())
    compiler_profiles = json.loads(
        (SCALA_ROOT / "compiler-profiles.v1.json").read_text()
    )
    selected = module.select_scala_profile(
        plan=plan,
        compiler_profiles=compiler_profiles,
        selected_profile_source_sha256="d" * 64,
        correctness=correctness(),
        qualification=qualification(plan, {"A": 100.0, "B": 95.0, "C": 96.0}),
    )
    assert selected["selectedProfileId"] == "B"
    assert selected["fallbackExecuted"] is False
    fallback = module.select_scala_profile(
        plan=plan,
        compiler_profiles=compiler_profiles,
        selected_profile_source_sha256="d" * 64,
        correctness=correctness(),
        qualification=qualification(plan, {"A": 100.0, "B": 106.0, "C": 107.0}),
    )
    assert fallback["selectedProfileId"] == "A"
    assert fallback["fallbackExecuted"] is True
    assert math.isclose(fallback["profiles"]["B"]["maximumCaseRatio"], 1.06)
    bad_latin = qualification(plan, {"A": 100.0, "B": 95.0, "C": 96.0})
    bad_latin["blocks"][0]["profileOrder"] = ["B", "A", "C"]
    try:
        module.select_scala_profile(
            plan=plan,
            compiler_profiles=compiler_profiles,
            selected_profile_source_sha256="d" * 64,
            correctness=correctness(),
            qualification=bad_latin,
        )
    except module.T3EvidenceError:
        pass
    else:
        raise AssertionError("Latin order tamper passed")
    bad_host_closure = qualification(
        plan, {"A": 100.0, "B": 95.0, "C": 96.0}
    )
    bad_host_closure["blocks"][0]["profileEvidence"][0]["caseCount"] = 6
    try:
        module.select_scala_profile(
            plan=plan,
            compiler_profiles=compiler_profiles,
            selected_profile_source_sha256="d" * 64,
            correctness=correctness(),
            qualification=bad_host_closure,
        )
    except module.T3EvidenceError:
        pass
    else:
        raise AssertionError("per-profile host/JVM closure tamper passed")

    capability_plan = json.loads(
        (S1_ROOT / "contract/capability-smoke-plan.v1.json").read_text()
    )
    capability = module.assemble_capability_result(
        plan=capability_plan,
        plan_sha256=SHA,
        toolchain_identity_sha256="2" * 64,
        evidence=capability_evidence(capability_plan),
    )
    assert capability["aggregateStatus"] == "PASS"
    assert [item["smokeId"] for item in capability["results"]] == [
        item["smokeId"]
        for item in capability_plan["languages"]["scala"]["smokes"]
    ]
    duplicate = capability_evidence(capability_plan)
    duplicate.pop("scala-jmh-native-json")
    try:
        module.assemble_capability_result(
            plan=capability_plan,
            plan_sha256=SHA,
            toolchain_identity_sha256="2" * 64,
            evidence=duplicate,
        )
    except module.T3EvidenceError:
        pass
    else:
        raise AssertionError("missing capability evidence passed")

    source_manifest = json.loads(
        (SCALA_ROOT / "source-inputs.v1.json").read_text()
    )
    expected_paths = list(source_manifest["files"])
    input_sets = {
        name: expected_paths
        for name in ("tracked", "manifest", "format", "compile", "lint", "profileRun")
    }
    input_result = module.assemble_input_set_result(
        manifest=source_manifest,
        manifest_sha256="3" * 64,
        compiler_profile_sha256="4" * 64,
        input_sets=input_sets,
    )
    assert input_result["aggregateStatus"] == "PASS"
    assert all(item["exact"] for item in input_result["sets"].values())
    missing_compile = json.loads(json.dumps(input_sets))
    missing_compile["compile"] = expected_paths[:-1]
    try:
        module.assemble_input_set_result(
            manifest=source_manifest,
            manifest_sha256="3" * 64,
            compiler_profile_sha256="4" * 64,
            input_sets=missing_compile,
        )
    except module.T3EvidenceError:
        pass
    else:
        raise AssertionError("incomplete compiler input set passed")

    planned = json.loads(
        (S1_ROOT / "contract/feature-decisions.v1.json").read_text()
    )
    effective = module.assemble_feature_decision_result(
        planned=planned,
        planned_sha256="5" * 64,
        capability_sha256="6" * 64,
        evidence=feature_evidence(planned),
    )
    assert len(effective["entries"]) == 6
    assert effective["entries"][-1]["effectiveDecision"] == "REJECT"
    broken_feature = feature_evidence(planned)
    broken_feature["scala.closed-enum-adt"]["parityMismatchCount"] = 1
    try:
        module.assemble_feature_decision_result(
            planned=planned,
            planned_sha256="5" * 64,
            capability_sha256="6" * 64,
            evidence=broken_feature,
        )
    except module.T3EvidenceError:
        pass
    else:
        raise AssertionError("adopted feature mismatch passed")

    dependency = module.assemble_scala_dependency_audit(
        policy_sha256="7" * 64,
        source_input_manifest_sha256="3" * 64,
        project_sha256="8" * 64,
        dependencies=[
            "com.fasterxml.jackson.core:jackson-core:2.22.1",
            "org.openjdk.jmh:jmh-core:1.37",
        ],
        forbidden_source_findings=[],
    )
    assert dependency["candidateAuthoredEdgeCount"] == 0
    assert dependency["candidateAddedNativeDependencyCount"] == 0
    try:
        module.assemble_scala_dependency_audit(
            policy_sha256="7" * 64,
            source_input_manifest_sha256="3" * 64,
            project_sha256="8" * 64,
            dependencies=["io.grpc:grpc-netty:1.0.0"],
            forbidden_source_findings=[],
        )
    except module.T3EvidenceError:
        pass
    else:
        raise AssertionError("native dependency passed")

    fork_evidence = [
        {
            "schemaVersion": "s1.4x-scala-jvm-fork-evidence-v1",
            "forkIndex": index,
            "javaExecutablePathId": "TEMURIN_25_0_3_9_LTS/bin/java",
            "javaExecutableSha256": "9" * 64,
            "runtimeVersion": "25.0.3+9-LTS",
            "vendor": "Eclipse Adoptium",
            "javaHomePathId": "TEMURIN_25_0_3_9_LTS",
            "inputArguments": [],
            "systemPropertiesSha256": "a" * 64,
            "environmentAllowlistSha256": "b" * 64,
            "evidenceSha256": f"{index + 20:064x}",
        }
        for index in range(1, 4)
    ]
    effective_jvm = module.validate_effective_jvm_evidence(
        fork_evidence,
        expected_forks=3,
        allowed_arguments=[],
        java_executable_sha256="9" * 64,
        capability_smoke_sha256="c" * 64,
    )
    assert effective_jvm["aggregateStatus"] == "PASS"
    assert effective_jvm["forkCount"] == 3
    fork_evidence[0]["inputArguments"] = ["-XX:+UseWhatever"]
    try:
        module.validate_effective_jvm_evidence(
            fork_evidence,
            expected_forks=3,
            allowed_arguments=[],
            java_executable_sha256="9" * 64,
            capability_smoke_sha256="c" * 64,
        )
    except module.T3EvidenceError:
        pass
    else:
        raise AssertionError("unexpected effective JVM argument passed")

    native = [
        {
            "benchmark": "s1_4x.benchmarks.path_transform.PathTransformBenchmark.run",
            "mode": "avgt",
            "threads": 1,
            "forks": 1,
            "primaryMetric": {
                "score": 12.5,
                "scoreUnit": "ns/op",
                "rawData": [[12.5]],
            },
            "jvmArgs": [],
        }
    ]
    validated = module.validate_jmh_native_json(
        native,
        expected_benchmark=native[0]["benchmark"],
        expected_forks=1,
        effective_jvm_arguments=[],
    )
    assert validated["nativeValue"] == 12.5
    native[0]["primaryMetric"]["score"] = float("nan")
    try:
        module.validate_jmh_native_json(
            native,
            expected_benchmark=native[0]["benchmark"],
            expected_forks=1,
            effective_jvm_arguments=[],
        )
    except module.T3EvidenceError:
        pass
    else:
        raise AssertionError("non-finite native score passed")

    print(
        "SCALA_T3_EVIDENCE_TEST_PASS "
        "semanticNegatives=22 profiles=3 capabilities=8 features=6 "
        "inputSets=6 nativeEdges=0 jvmForks=3 native=1"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
