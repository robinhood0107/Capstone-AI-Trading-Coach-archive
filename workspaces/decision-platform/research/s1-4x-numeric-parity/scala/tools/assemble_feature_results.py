#!/usr/bin/env python3
"""Scala planned feature 여섯 개를 accepted evidence 기반 effective result로 조립한다."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from t3_evidence import SCALA_PROFILES
from t3_evidence import assemble_feature_decision_result
from t3_evidence import canonical_sha256
from t3_evidence import require_sha
from t3_evidence import sha256_file
from t3_evidence import strict_json
from t3_evidence import validate_correctness
from t3_evidence import write_exclusive_json


class FeatureAssemblyError(ValueError):
    """Feature evidence inputs are incomplete or contradictory."""


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--planned", type=Path, required=True)
    parser.add_argument("--capability", type=Path, required=True)
    parser.add_argument("--selected", type=Path, required=True)
    parser.add_argument("--compiler", type=Path, required=True)
    parser.add_argument("--source-policy", type=Path, required=True)
    parser.add_argument("--dependency-audit", type=Path, required=True)
    parser.add_argument("--lint-exceptions", type=Path, required=True)
    parser.add_argument("--correctness-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()
    try:
        capability = strict_json(arguments.capability)
        selected = strict_json(arguments.selected)
        compiler = strict_json(arguments.compiler)
        source_policy = strict_json(arguments.source_policy)
        dependency = strict_json(arguments.dependency_audit)
        lint_exceptions = strict_json(arguments.lint_exceptions)
        correctness = {
            profile: strict_json(
                arguments.correctness_root
                / profile
                / "scala-profile-correctness-result.v1.json"
            )
            for profile in SCALA_PROFILES
        }
        validate_correctness(correctness)
        if (
            arguments.output.exists()
            or capability.get("aggregateStatus") != "PASS"
            or selected.get("selectionStatus") != "PASS"
            or compiler.get("aggregateStatus") != "PASS"
            or source_policy.get("aggregateStatus") != "PASS"
            or dependency.get("aggregateStatus") != "PASS"
            or any(
                lint_exceptions.get(name) != []
                for name in (
                    "mutationAllowlist",
                    "partialApiAllowlist",
                    "suppressionAllowlist",
                )
            )
        ):
            raise FeatureAssemblyError("FEATURE_INPUT_FAILED")
        diagnostic_only = compiler.get("diagnosticOnly")
        if not isinstance(diagnostic_only, list):
            raise FeatureAssemblyError("DIAGNOSTIC_ONLY_RESULT_INVALID")
        safe_init_matches = [
            item
            for item in diagnostic_only
            if isinstance(item, dict) and item.get("option") == "-Wsafe-init"
        ]
        if len(safe_init_matches) != 1:
            raise FeatureAssemblyError("SAFE_INIT_DIAGNOSTIC_MISSING")
        safe_init = safe_init_matches[0]
        disposition = safe_init.get("diagnosticDisposition")
        if (
            safe_init.get("disposition") != "RECORDED_NON_SCORING"
            or safe_init.get("exitCode") not in {0, 1}
            or not isinstance(disposition, dict)
            or disposition.get("option") != "-Wsafe-init"
            or disposition.get("status") != "PASS"
            or disposition.get("exitDisposition")
            not in {
                "CLEAN_NO_BLOCKING_DIAGNOSTIC",
                "RECORDED_DIAGNOSTIC_WERROR",
            }
        ):
            raise FeatureAssemblyError("SAFE_INIT_DIAGNOSTIC_INVALID")
        for key in (
            "portableArgvSha256",
            "runtimeArgvSha256",
            "stdoutSha256",
            "stderrSha256",
        ):
            require_sha(safe_init.get(key), f"safeInit.{key}")
        require_sha(
            disposition.get("diagnosticDispositionSha256"),
            "safeInit.diagnosticDispositionSha256",
        )

        common_evidence = [
            sha256_file(arguments.capability),
            *[
                sha256_file(
                    arguments.correctness_root
                    / profile
                    / "scala-profile-correctness-result.v1.json"
                )
                for profile in SCALA_PROFILES
            ],
        ]

        def adopted_evidence(*extra: str) -> dict[str, object]:
            return {
                "plannedDecision": "ADOPT",
                "effectiveDecision": "ADOPT",
                "smokeStatus": "PASS",
                "lintStatus": "PASS",
                "testStatus": "PASS",
                "parityMismatchCount": 0,
                "evidenceStatus": "PASS",
                "fallbackExecuted": False,
                "fallbackStatus": "NOT_RUN",
                "evidenceSha256": canonical_sha256(
                    [*common_evidence, *extra]
                ),
            }

        selected_profile = selected["selectedProfileId"]
        if selected_profile == "A":
            optimizer = {
                "plannedDecision": "CONDITIONAL",
                "effectiveDecision": "FALLBACK",
                "smokeStatus": "PASS",
                "lintStatus": "PASS",
                "testStatus": "PASS",
                "parityMismatchCount": 0,
                "evidenceStatus": "FAIL",
                "fallbackExecuted": True,
                "fallbackStatus": "PASS",
                "evidenceSha256": canonical_sha256(
                    [
                        sha256_file(arguments.selected),
                        correctness["A"]["candidateSha256"],
                    ]
                ),
            }
        else:
            optimizer = {
                "plannedDecision": "CONDITIONAL",
                "effectiveDecision": "ADOPT",
                "smokeStatus": "PASS",
                "lintStatus": "PASS",
                "testStatus": "PASS",
                "parityMismatchCount": 0,
                "evidenceStatus": "PASS",
                "fallbackExecuted": False,
                "fallbackStatus": "NOT_RUN",
                "evidenceSha256": canonical_sha256(
                    [sha256_file(arguments.selected), selected_profile]
                ),
            }
        evidence = {
            "scala.closed-enum-adt": adopted_evidence(
                correctness["A"]["matrix"]["registryReportSha256"]
            ),
            "scala.opaque-validation-boundaries": adopted_evidence(
                correctness["A"]["matrix"]["propertyReportSha256"]
            ),
            "scala.optimizer-profile-b-c": optimizer,
            "scala.local-mutation-hot-kernel": {
                "plannedDecision": "CONDITIONAL",
                "effectiveDecision": "FALLBACK",
                "smokeStatus": "PASS",
                "lintStatus": "PASS",
                "testStatus": "PASS",
                "parityMismatchCount": 0,
                "evidenceStatus": "FAIL",
                "fallbackExecuted": True,
                "fallbackStatus": "PASS",
                "evidenceSha256": canonical_sha256(
                    [
                        sha256_file(arguments.source_policy),
                        sha256_file(arguments.lint_exceptions),
                        "empty-ledger-immutable-fallback",
                    ]
                ),
            },
            "scala.safe-init-warning": {
                "plannedDecision": "PROBE_ONLY",
                "effectiveDecision": "PROBE_ONLY",
                "smokeStatus": "PASS",
                "lintStatus": "NOT_APPLICABLE",
                "testStatus": "PASS",
                "parityMismatchCount": 0,
                "evidenceStatus": "PASS",
                "fallbackExecuted": False,
                "fallbackStatus": "NOT_RUN",
                "evidenceSha256": canonical_sha256(
                    [
                        sha256_file(arguments.compiler),
                        safe_init,
                    ]
                ),
            },
            "scala.experimental-and-native-features": {
                "plannedDecision": "REJECT",
                "effectiveDecision": "REJECT",
                "smokeStatus": "NOT_APPLICABLE",
                "lintStatus": "PASS",
                "testStatus": "PASS",
                "parityMismatchCount": 0,
                "evidenceStatus": "PASS",
                "fallbackExecuted": False,
                "fallbackStatus": "NOT_RUN",
                "evidenceSha256": canonical_sha256(
                    [
                        sha256_file(arguments.source_policy),
                        sha256_file(arguments.dependency_audit),
                    ]
                ),
            },
        }
        result = assemble_feature_decision_result(
            planned=strict_json(arguments.planned),
            planned_sha256=sha256_file(arguments.planned),
            capability_sha256=sha256_file(arguments.capability),
            evidence=evidence,
        )
        write_exclusive_json(arguments.output, result)
    except (OSError, UnicodeError, ValueError, FeatureAssemblyError) as error:
        print(f"SCALA_FEATURE_ASSEMBLY_FAIL:{error}", file=sys.stderr)
        return 1
    print(
        "SCALA_FEATURE_ASSEMBLY_PASS "
        f"features={len(result['entries'])} selectedProfile={selected_profile}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
