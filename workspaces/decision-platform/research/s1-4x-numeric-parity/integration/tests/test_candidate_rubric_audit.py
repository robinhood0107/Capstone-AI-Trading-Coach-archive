"""후보 rubric 판정이 assembler와 독립된 raw/source 감사에서만 PASS하는지 검증한다."""

from __future__ import annotations

import hashlib
import json
import math
import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any
from unittest import TestCase
from unittest.mock import patch

INTEGRATION = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(INTEGRATION))

import candidate_rubric_audit as audit_module  # noqa: E402
from candidate_rubric_audit import (  # noqa: E402
    CANDIDATES,
    RUBRIC_IDS,
    CandidateRubricAuditError,
    generate_candidate_rubric_audit,
)

S1 = Path("workspaces/decision-platform/research/s1-4x-numeric-parity")
RESEARCH_PROJECT = "workspaces/decision-platform/research/s1-4r-jax-risk"
DESELECTED = (
    "tests/test_production_isolation.py::"
    "test_branch_diff_is_confined_to_the_research_project_and_two_workflows"
)
REPLACEMENTS = (
    "workspaces/decision-platform/research/s1-4x-numeric-parity/"
    "integration/tests/test_s1_4r_regression_boundary.py::"
    "test_s1_4x_branch_diff_is_confined_to_the_experiment_boundary",
    "workspaces/decision-platform/research/s1-4x-numeric-parity/"
    "integration/tests/test_s1_4r_regression_boundary.py::"
    "test_aggregate_deselects_only_the_inapplicable_s1_4r_branch_scope",
)
PROPERTY_IDS = (
    "production.output-finite-or-stable-error",
    "simple-returns.scale-invariant",
    "log-returns.scale-invariant",
    "cumulative-return.bankruptcy-absorbing",
    "cumulative-return.manual-product-identity",
    "volatility.translation-and-scale",
    "max-drawdown.bounds",
    "var-hf7-observation-range",
    "var-cvar.shift-and-positive-scale",
    "cvar-threshold-tail",
    "expected-shortfall.permutation-invariant",
    "realized.permutation-invariant",
    "realized.scale-laws",
    "lo.order-sensitive",
    "psr.benchmark-equality",
    "dsr.benchmark-equality",
    "dsr.provenance-count-consistency",
    "kupiec.paired-permutation-invariant",
    "backtest.strict-loss-greater-than-var",
    "christoffersen.order-sensitive",
    "backtest.positive-common-scaling",
    "likelihood.record-invariants",
    "conditional-coverage.component-identity",
    "christoffersen.unidentifiable-transition-rejected",
    "recursive-negative-zero-normalization",
)
QUALIFICATION_CASES = (
    "path-transform/log_returns/n100000/b1",
    "classical-path-risk/historical_expected_shortfall/n100000/b1",
    "intraday-realized/realized_variance/n100000/b1",
    "serial-sharpe/lo_adjusted_sharpe_ratio/n100000/q5/b1",
    "probabilistic-scalar/probabilistic_sharpe_ratio/b16384",
    "coverage-batch/kupiec_pof/n100000/b32",
    "coverage-batch/christoffersen_conditional_coverage/n100000/b32",
)
SCALA_PROFILE_ORDERS = (("A", "B", "C"), ("B", "C", "A"), ("C", "A", "B"))
HASKELL_PROFILE_ORDERS = (
    ("baseline-o0-fasm", "optimized-o2-fasm"),
    ("optimized-o2-fasm", "baseline-o0-fasm"),
    ("optimized-o2-fasm", "baseline-o0-fasm"),
    ("baseline-o0-fasm", "optimized-o2-fasm"),
)
CORRECTNESS_TRIGGER_PATHS = (
    "workspaces/decision-platform/research/s1-4x-numeric-parity/**",
    "workspaces/decision-platform/python-services/pyproject.toml",
    "workspaces/decision-platform/python-services/uv.lock",
    "workspaces/decision-platform/python-services/app/financial_engineering/**",
    "workspaces/decision-platform/python-services/tests/financial_engineering/**",
    "workspaces/decision-platform/research/s1-4r-jax-risk/README.md",
    "workspaces/decision-platform/research/s1-4r-jax-risk/pyproject.toml",
    "workspaces/decision-platform/research/s1-4r-jax-risk/uv.lock",
    "workspaces/decision-platform/research/s1-4r-jax-risk/src/s1_4r_risk_research/**",
    "workspaces/decision-platform/research/s1-4r-jax-risk/tests/**",
    "workspaces/decision-platform/research/s1-4r-jax-risk/benchmarks/**",
    "shared-docs/metrics_definitions.md",
    ".gitignore",
    ".github/workflows/s1-4x-*.yml",
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
    ).encode()


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_canonical(value))


def _git(repository: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["/usr/bin/git", "-c", "core.fsmonitor=false", *arguments],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return completed.stdout.strip()


def _geometric_mean(values: list[float]) -> float:
    return math.exp(math.fsum(math.log(value) for value in values) / len(values))


def _scala_profiles_from_scores(
    policy: dict[str, Any],
    scores: dict[tuple[int, str, str], float],
) -> tuple[dict[str, dict[str, Any]], str]:
    profiles: dict[str, dict[str, Any]] = {
        "A": {
            "aggregateRatioToA": 1.0,
            "maximumCaseRatio": 1.0,
            "improvingOuterRepetitions": 3,
            "caseMedianRatiosToA": {
                case_id: 1.0 for case_id in QUALIFICATION_CASES
            },
            "outerAggregateRatiosToA": [1.0, 1.0, 1.0],
            "qualified": True,
        }
    }
    for profile in ("B", "C"):
        case_ratios = {
            case_id: sorted(
                scores[(repetition, profile, case_id)]
                / scores[(repetition, "A", case_id)]
                for repetition in range(1, 4)
            )[1]
            for case_id in QUALIFICATION_CASES
        }
        outer = [
            _geometric_mean(
                [
                    scores[(repetition, profile, case_id)]
                    / scores[(repetition, "A", case_id)]
                    for case_id in QUALIFICATION_CASES
                ]
            )
            for repetition in range(1, 4)
        ]
        aggregate = _geometric_mean(list(case_ratios.values()))
        maximum = max(case_ratios.values())
        improving = sum(value < 1.0 for value in outer)
        profiles[profile] = {
            "aggregateRatioToA": aggregate,
            "maximumCaseRatio": maximum,
            "improvingOuterRepetitions": improving,
            "caseMedianRatiosToA": case_ratios,
            "outerAggregateRatiosToA": outer,
            "qualified": (
                maximum <= policy["perCaseMaxRegressionRatio"]
                and aggregate <= policy["aggregateMaxRatio"]
                and improving >= policy["minimumImprovingOuterRepetitions"]
            ),
        }
    c_over_b = (
        profiles["C"]["aggregateRatioToA"]
        / profiles["B"]["aggregateRatioToA"]
    )
    profiles["C"]["aggregateRatioToB"] = c_over_b
    if profiles["C"]["qualified"] and (
        not profiles["B"]["qualified"]
        or c_over_b <= 1.0 - policy["cOverBMinimumImprovement"]
    ):
        selected = "C"
    elif profiles["B"]["qualified"]:
        selected = "B"
    elif profiles["C"]["qualified"]:
        selected = "C"
    else:
        selected = "A"
    return profiles, selected


def _scala_selector_sha(
    *,
    policy: dict[str, Any],
    benchmark_plan_sha256: str,
    blocks: list[dict[str, Any]],
) -> str:
    observed = []
    for block in blocks:
        observed.append(
            {
                "outerRepetition": block["outerRepetition"],
                "plannedProfileOrder": block["plannedProfileOrder"],
                "actualProfileOrder": block["actualProfileOrder"],
                "profileCaseOrder": [
                    {
                        "profileId": item["profileId"],
                        "plannedCaseOrder": item["plannedCaseOrder"],
                        "actualCaseOrder": item["actualCaseOrder"],
                        "caseCount": item["caseCount"],
                    }
                    for item in block["profileEvidence"]
                ],
                "measurementOrder": [
                    {
                        "profileId": item["profileId"],
                        "caseId": item["caseId"],
                        "rawNativeJsonSha256": item["rawNativeJsonSha256"],
                        "effectiveJvmArgsSha256": item[
                            "effectiveJvmArgsSha256"
                        ],
                        "jmhRunResultSha256": item["jmhRunResultSha256"],
                    }
                    for item in block["measurements"]
                ],
            }
        )
    return hashlib.sha256(
        _canonical(
            {
                "benchmarkPlanSha256": benchmark_plan_sha256,
                "policy": policy,
                "observedLatinProfileCaseClosure": observed,
            }
        )[:-1]
    ).hexdigest()


class _Fixture:
    def __init__(self, root: Path) -> None:
        self.repository = root / "repository"
        self.correctness = root / "correctness"
        self.output = self.correctness / "rubric-audit"
        self.repository.mkdir()
        self.correctness.mkdir()
        _git(self.repository, "init", "-b", "main")
        _git(self.repository, "config", "user.email", "audit@example.invalid")
        _git(self.repository, "config", "user.name", "Rubric Audit Test")
        _git(self.repository, "config", "core.filemode", "true")
        self._write_subject_sources()
        self.subject = self.commit()
        self._write_raw_evidence()

    def _subject_path(self, relative: str) -> Path:
        path = self.repository / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def _write_subject_sources(self) -> None:
        scala_core = S1 / "scala/src/main/scala/ai/trading/coach/s14x/core"
        scala_shell = S1 / "scala/src/main/scala/ai/trading/coach/s14x/shell"
        scala_tests = S1 / "scala/src/test/scala/ai/trading/coach/s14x"
        haskell_core = S1 / "haskell/src/core/S14X/Core"
        haskell_contract = S1 / "haskell/src/contract/S14X/Contract"
        haskell_tests = S1 / "haskell/test/S14X"

        self._subject_path(str(scala_core / "Math.scala")).write_text(
            """package ai.trading.coach.s14x.core

object Math:
  /** 두 유한 입력을 더해 stable numeric 결과를 반환한다. */
  def add(left: Double, right: Double): Double =
    val first = left
    val second = right
    val total = first + second
    total
""",
            encoding="utf-8",
        )
        self._subject_path(str(scala_core / "Validation.scala")).write_text(
            """package ai.trading.coach.s14x.core

object Validation:
  /** numeric 입력의 유한성을 명시적으로 검증한다. */
  def finite(value: Double): Either[String, Double] =
    if value.isFinite then Right(value)
    else Left("non_finite")
""",
            encoding="utf-8",
        )
        self._subject_path(str(scala_shell / "Main.scala")).write_text(
            """package ai.trading.coach.s14x.shell

import ai.trading.coach.s14x.core.Math

object Main:
  def main(arguments: Array[String]): Unit =
    val _ = Math.add(arguments.length.toDouble, 0.0)
""",
            encoding="utf-8",
        )
        scala_test_values = {
            "core/CoreSuite.scala": (
                'class CoreSuite:\n  test("adds two finite values deterministically"):\n'
                "    assertEquals(Math.add(1.0, 2.0), 3.0)\n"
            ),
            "shell/ContractShellSuite.scala": (
                'class ContractShellSuite:\n  test("process contract preserves request identity"):\n'
                '    assertEquals("req-1", "req-1")\n'
            ),
            "core/NumericPropertiesSuite.scala": (
                "class NumericPropertiesSuite:\n"
                '  property("addition remains commutative for finite inputs"):\n'
                "    assert(1 + 1 == 2)\n"
            ),
        }
        for relative, payload in scala_test_values.items():
            self._subject_path(str(scala_tests / relative)).write_text(
                payload,
                encoding="utf-8",
            )

        self._subject_path(str(haskell_core / "Math.hs")).write_text(
            """{-# LANGUAGE Safe #-}
module S14X.Core.Math (add) where

-- | 두 유한 입력을 더해 순수 numeric 결과를 반환한다.
add :: Double -> Double -> Double
add left right =
  let first = left
      second = right
      total = first + second
   in total
""",
            encoding="utf-8",
        )
        self._subject_path(str(haskell_core / "Validation.hs")).write_text(
            """{-# LANGUAGE Safe #-}
module S14X.Core.Validation (finite) where

-- | 비유한 입력을 명시적 Either 오류로 닫는다.
finite :: Double -> Either String Double
finite value
  | isNaN value || isInfinite value = Left "non_finite"
  | otherwise = Right value
""",
            encoding="utf-8",
        )
        self._subject_path(str(haskell_contract / "Process.hs")).write_text(
            """module S14X.Contract.Process (runRequest) where

import S14X.Core.Math (add)

runRequest :: Double -> IO Double
runRequest value = pure (add value 0.0)
""",
            encoding="utf-8",
        )
        haskell_test_values = {
            "CoreSpec.hs": (
                'tests = testGroup "pure-core" '
                '[testCase "adds two finite values deterministically" '
                '(assertBool "sum" (1 + 1 == 2))]\n'
            ),
            "ContractSpec.hs": (
                'tests = testGroup "process-contract" '
                '[testCase "request identity survives transport" '
                '(assertEqual "identity" "req-1" "req-1")]\n'
            ),
            "PropertySpec.hs": (
                'tests = testGroup "properties" '
                '[testProperty "addition is commutative for finite values" property]\n'
            ),
        }
        for relative, payload in haskell_test_values.items():
            self._subject_path(str(haskell_tests / relative)).write_text(
                payload,
                encoding="utf-8",
            )

        _write_json(
            self._subject_path(str(S1 / "contract/scala-source-policy.v1.json")),
            {
                "schemaVersion": "s1.4x-scala-source-policy-v1",
                "productionRoots": ["scala/src/main/scala"],
                "inputSetEqualityRequiredAcross": ["tracked", "compile"],
            },
        )
        _write_json(
            self._subject_path(
                str(S1 / "contract/haskell-module-safety-policy.v1.json")
            ),
            {
                "schemaVersion": "s1.4x-haskell-module-safety-policy-v1",
                "everyModuleExactlyOneCategory": True,
                "candidateGraphInvariants": {"candidateHomeCoreToShellEdgeCount": 0},
            },
        )
        boundary = self._subject_path(
            str(S1 / "integration/tests/test_s1_4r_regression_boundary.py")
        )
        boundary.write_text(
            '''from regression_gate import (
    DESELECTED_RESEARCH_NODE as PRODUCER_DESELECTED_RESEARCH_NODE,
)

S1_4X_ROOT = "workspaces/decision-platform/research/s1-4x-numeric-parity/"
S1_4X_WORKFLOWS = {
    ".github/workflows/s1-4x-numeric-parity-correctness.yml",
    ".github/workflows/s1-4x-numeric-parity-benchmark.yml",
}
S1_4R_BRANCH_SCOPE_NODE = "tests/test_production_isolation.py::test_branch_diff_is_confined_to_the_research_project_and_two_workflows"

def _changed_paths():
    subprocess.run(
        ["/usr/bin/git", "diff", "--name-only", "origin/main"],
        check=True,
    )

def test_s1_4x_branch_diff_is_confined_to_the_experiment_boundary() -> None:
    unexpected = [
        path for path in _changed_paths()
        if not path.startswith(S1_4X_ROOT) and path not in S1_4X_WORKFLOWS
    ]
    assert unexpected == []

def test_aggregate_deselects_only_the_inapplicable_s1_4r_branch_scope() -> None:
    aggregate_source = AGGREGATE.read_text()
    producer_source = REGRESSION_GATE.read_text()
    assert aggregate_source.count('python "$INTEGRATION/regression_gate.py"') == 1
    assert PRODUCER_DESELECTED_RESEARCH_NODE == S1_4R_BRANCH_SCOPE_NODE
    assert "S1_4R_EXECUTION_BOUNDARY=oci" not in aggregate_source
    assert "test_s1_4r_regression_boundary.py" in producer_source
''',
            encoding="utf-8",
        )
        correctness_workflow = self._subject_path(
            ".github/workflows/s1-4x-numeric-parity-correctness.yml"
        )
        trigger_paths = "\n".join(
            f'      - "{path}"' for path in CORRECTNESS_TRIGGER_PATHS
        )
        correctness_workflow.write_text(
            f"""name: S1.4X correctness
on:
  pull_request:
    paths:
{trigger_paths}
  push:
    branches:
      - main
    paths:
{trigger_paths}
permissions:
  contents: read
concurrency:
  group: s1-4x-correctness-${{{{ github.ref }}}}
  cancel-in-progress: true
jobs:
  correctness:
    timeout-minutes: 240
    steps:
      - run: python integration/regression_gate.py
""",
            encoding="utf-8",
        )
        self._write_subject_contract_inputs()
        self.refresh_haskell_selected()
        self._refresh_source_manifests()
        benchmark_workflow = self._subject_path(
            ".github/workflows/s1-4x-numeric-parity-benchmark.yml"
        )
        benchmark_workflow.write_text(
            """name: S1.4X benchmark
on:
  workflow_dispatch:
    inputs:
      matrix:
        options:
          - smallest
          - full
permissions:
  contents: read
concurrency:
  group: s1-4x-benchmark-${{ github.ref }}
  cancel-in-progress: false
jobs:
  correctness-before-timing:
    timeout-minutes: 360
    steps:
      - run: |
          docker run --network none docker.io/library/eclipse-temurin@sha256:5742cdb98ef117621ad75f57475ab127db04f344d9c523307cc60b9955bdd676
          docker run --network none docker.io/library/haskell@sha256:417d4bc30ac7d8d5ff04ec97937f86eb508b0c76bfd1a39b5ec225688531aa9d
""",
            encoding="utf-8",
        )
        for path in self.repository.rglob("*"):
            if path.is_file() and not path.is_symlink():
                path.chmod(0o644)
        for executable in audit_module.EXECUTABLE_SUBJECT_PATHS:
            (self.repository / executable).chmod(0o755)

    def _write_subject_contract_inputs(self) -> None:
        scala = self.repository / S1 / "scala"
        haskell = self.repository / S1 / "haskell"
        subject_json = {
            S1 / "scala/compiler-profiles.v1.json": {
                "profiles": {
                    "A": {"additionalOptions": []},
                    "B": {"additionalOptions": ["-opt"]},
                    "C": {"additionalOptions": ["-opt", "-experimental"]},
                }
            },
            S1 / "scala/toolchain-lock.v1.json": {
                "scalaCli": {"binarySha256": "1" * 64},
                "mergedToolchainProvenanceSha256": "2" * 64,
            },
            S1 / "benchmarks/benchmark-plan.v1.json": {
                "scalaProfileQualification": {
                    "qualificationCaseIds": list(QUALIFICATION_CASES),
                    "qualificationCaseOrder": list(QUALIFICATION_CASES),
                    "profileOrderBlocks": [
                        list(order) for order in SCALA_PROFILE_ORDERS
                    ],
                    "hostValidityBeforeEachProfileBlock": True,
                    "outerQualificationRepetitions": 3,
                    "perCaseMaxRegressionRatio": 1.05,
                    "aggregateMaxRatio": 0.97,
                    "minimumImprovingOuterRepetitions": 2,
                    "cOverBMinimumImprovement": 0.01,
                    "tieBreakOrder": ["B", "C", "A"],
                    "fallbackProfile": "A",
                },
                "haskellProfileQualification": {
                    "qualificationCaseIds": list(QUALIFICATION_CASES),
                    "qualificationCaseOrder": list(QUALIFICATION_CASES),
                    "profileOrderBlocks": [
                        list(order) for order in HASKELL_PROFILE_ORDERS
                    ],
                    "outerQualificationRepetitions": 4,
                    "perCaseMaxRegressionRatio": 1.05,
                    "aggregateMaxRatio": 0.97,
                    "minimumImprovingOuterRepetitions": 3,
                    "optimizedProfile": "optimized-o2-fasm",
                    "fallbackProfile": "baseline-o0-fasm",
                },
            },
            S1 / "contract/property-plan.v1.json": {
                "schemaVersion": "s1.4x-property-plan-v1",
                "seedCorpusFile": (
                    "contract/fixtures/property/property-seeds.v1.json"
                ),
                "seedCount": 24,
                "minimumSuccessfulPerProperty": 1000,
                "maximumDiscardedPerProperty": 100,
                "maximumDiscardRatio": 0.1,
                "properties": [
                    {
                        "propertyId": property_id,
                        "functionIds": ["fixture"],
                        "invariant": "fixture invariant",
                        "generatorPolicy": "valid-by-construction",
                    }
                    for property_id in PROPERTY_IDS
                ],
            },
            S1 / "contract/fixtures/property/property-seeds.v1.json": {
                "schemaVersion": "s1.4x-property-seeds-v1",
                "seeds": [
                    17,
                    29,
                    43,
                    71,
                    113,
                    181,
                    293,
                    467,
                    743,
                    1181,
                    1871,
                    2969,
                    4703,
                    7451,
                    11801,
                    18691,
                    29569,
                    46771,
                    73973,
                    117037,
                    185033,
                    292817,
                    463249,
                    732911,
                ],
            },
            S1 / "contract/function-registry.v1.json": {"count": 20},
            S1 / "contract/error-registry.v1.json": {"count": 32},
            S1 / "contract/fixtures/small/canonical-inputs.v1.json": {
                "requestId": "s1.4x-canonical-small-v1"
            },
            S1 / "contract/fixtures/expected/canonical-results.v1.json": {
                "requestId": "s1.4x-canonical-small-v1"
            },
            S1 / "contract/fixtures/invalid/semantic-errors.v1.json": {
                "requestId": "s1.4x-semantic-errors-v1"
            },
            S1
            / "contract/fixtures/invalid/semantic-errors.expected.v1.json": {
                "requestId": "s1.4x-semantic-errors-v1"
            },
        }
        for relative, document in subject_json.items():
            _write_json(self._subject_path(str(relative)), document)
        for path, payload in (
            (scala / "project.scala", "//> using scala 3.8.4\n"),
            (scala / "selected-profile.scala", "// selected profile\n"),
            (scala / "Containerfile", "FROM scala-base\n"),
            (scala / "tools/run-property-evidence.sh", "#!/bin/sh\n"),
            (haskell / "package.yaml", "name: s1-4x-haskell\n"),
            (haskell / "Containerfile", "FROM haskell-base\n"),
            (haskell / "tools/run-property-evidence.sh", "#!/bin/sh\n"),
        ):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(payload, encoding="utf-8")
        for executable in audit_module.EXECUTABLE_SUBJECT_PATHS:
            path = self.repository / executable
            if not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("#!/bin/sh\n", encoding="utf-8")
            path.chmod(0o755)
        for haskell_relative in audit_module.HASKELL_SOURCE_TREE_INPUTS:
            path = haskell / haskell_relative
            if not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(
                    f"fixture:{haskell_relative}\n",
                    encoding="utf-8",
                )
        (haskell / ".gitignore").write_text("/*.cabal\n", encoding="utf-8")
        (haskell / "s1-4x-haskell.cabal").write_text(
            "cabal-version: 2.4\nname: s1-4x-haskell\nversion: 0.1.0.0\n",
            encoding="utf-8",
        )
        _write_json(
            haskell / "toolchain-lock.v1.json",
            {
                "resolvedTools": {
                    "stack": {
                        "pathId": "GHCUP_STACK_3_11_1",
                        "version": "3.11.1",
                        "sha256": "f" * 64,
                    }
                }
            },
        )

    def _candidate_haskell_paths(self) -> list[str]:
        root = self.repository / S1 / "haskell"
        paths: list[str] = []
        for candidate_root in audit_module.HASKELL_CANDIDATE_ROOTS:
            prefix = root / candidate_root
            if prefix.exists():
                paths.extend(
                    path.relative_to(root).as_posix()
                    for path in prefix.rglob("*.hs")
                    if path.is_file()
                )
        return sorted(set(paths))

    def refresh_haskell_selected(self) -> None:
        root = self.repository / S1 / "haskell"
        tree_paths = sorted(
            set(self._candidate_haskell_paths())
            | set(audit_module.HASKELL_SOURCE_TREE_INPUTS)
            | {"s1-4x-haskell.cabal"}
        )
        entries = [
            {
                "path": relative,
                "sha256": hashlib.sha256((root / relative).read_bytes()).hexdigest(),
            }
            for relative in tree_paths
        ]
        source_tree_sha256 = hashlib.sha256(_canonical(entries)[:-1]).hexdigest()
        plan = self.repository / S1 / "benchmarks/benchmark-plan.v1.json"
        selector = json.loads(plan.read_text())["haskellProfileQualification"]
        options = ["-O0", "-fasm"]
        _write_json(
            root / "selected-profile.v1.json",
            {
                "schemaVersion": "s1.4x-haskell-selected-profile-v1",
                "profileId": "baseline-o0-fasm",
                "ghcOptions": options,
                "compilerVersion": "9.10.3",
                "compilerSha256": audit_module.AUTHORITATIVE_GHC_SHA256,
                "sourceTreeSha256": source_tree_sha256,
                "optionsSha256": hashlib.sha256(_canonical(options)[:-1]).hexdigest(),
                "fullCorrectnessSha256": "3" * 64,
                "qualificationPlanSha256": hashlib.sha256(
                    plan.read_bytes()
                ).hexdigest(),
                "qualificationArtifactSha256": "5" * 64,
                "selectorConfigSha256": hashlib.sha256(
                    _canonical(selector)[:-1]
                ).hexdigest(),
                "fallbackProfile": "baseline-o0-fasm",
                "selectedBy": "proven-fallback",
            },
        )

    def _refresh_source_manifests(self) -> None:
        for language in ("scala", "haskell"):
            root = self.repository / S1 / language
            if language == "scala":
                paths = [
                    "project.scala",
                    "selected-profile.scala",
                    *(
                        path.relative_to(root).as_posix()
                        for path in (root / "src/main/scala").rglob("*.scala")
                    ),
                    *(
                        path.relative_to(root).as_posix()
                        for path in (root / "src/test/scala").rglob("*.scala")
                    ),
                ]
            else:
                paths = [
                    "package.yaml",
                    "selected-profile.v1.json",
                    *self._candidate_haskell_paths(),
                ]
            files = {
                path: {
                    "role": audit_module._source_role(language, path),
                    "sha256": hashlib.sha256((root / path).read_bytes()).hexdigest(),
                }
                for path in sorted(set(paths))
            }
            canonical_lines = b"".join(
                f"{entry['sha256']}  {path}\n".encode()
                for path, entry in files.items()
            )
            _write_json(
                root / "source-inputs.v1.json",
                {
                    "schemaVersion": "s1.4x-source-input-manifest-v1",
                    "language": language,
                    "files": files,
                    "inputSets": audit_module.SOURCE_INPUT_SETS,
                    "canonicalManifestSha256": hashlib.sha256(
                        canonical_lines
                    ).hexdigest(),
                },
            )

    def commit(self) -> str:
        self._refresh_source_manifests()
        _git(self.repository, "add", "--all")
        _git(self.repository, "commit", "-m", "fixture subject")
        return _git(self.repository, "rev-parse", "HEAD")

    def _repo_sha(self, relative: str) -> str:
        return hashlib.sha256((self.repository / relative).read_bytes()).hexdigest()

    def _write_raw_blob(self, relative: str, payload: bytes = b"fixture\n") -> str:
        path = self.correctness / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        return hashlib.sha256(payload).hexdigest()

    def _comparison(self, matrix: str) -> dict[str, Any]:
        return {
            "schemaVersion": "s1.4x-comparison-report-v1",
            "requestId": {
                "canonical": "s1.4x-canonical-small-v1",
                "semantic": "s1.4x-semantic-errors-v1",
            }[matrix],
            "implementationCount": 2,
            "mismatchCount": 0,
            "mismatches": [],
            "status": "PASS",
        }

    def _actual(self, matrix: str, implementation: str) -> dict[str, Any]:
        return {
            "requestId": {
                "canonical": "s1.4x-canonical-small-v1",
                "semantic": "s1.4x-semantic-errors-v1",
            }[matrix],
            "implementation": implementation,
            "results": [],
        }

    def refresh_subject_bindings(self, *, refresh_scala: bool = True) -> None:
        stale_semantic: bytes | None = None
        semantic = (
            self.correctness
            / "scala/scalafix/scala-semantic-policy-receipt.v1.json"
        )
        if not refresh_scala and semantic.exists():
            stale_semantic = semantic.read_bytes()
        self._write_raw_evidence()
        if stale_semantic is not None:
            semantic.write_bytes(stale_semantic)

    def _write_regression(self, role: str) -> None:
        roles: tuple[str, ...]
        if role == "production":
            project = audit_module.PRODUCTION_PROJECT
            counts = (1344, 1344, 0, 0, 1344)
            deselected: list[str] = []
            replacements: list[str] = []
            roles = ("ruff", "mypy", "pytest")
        else:
            project = RESEARCH_PROJECT
            counts = (263, 262, 1, 2, 264)
            deselected = [DESELECTED]
            replacements = list(REPLACEMENTS)
            roles = ("ruff", "mypy", "replacement-pytest", "base-pytest")
        commands = []
        for command_role in roles:
            label = f"{role}-{command_role}"
            stdout_path = f"regression/logs/{label}.stdout"
            stderr_path = f"regression/logs/{label}.stderr"
            commands.append(
                {
                    "role": command_role,
                    "exitCode": 0,
                    "stdoutPath": stdout_path,
                    "stdoutSha256": self._write_raw_blob(stdout_path),
                    "stderrPath": stderr_path,
                    "stderrSha256": self._write_raw_blob(stderr_path, b""),
                    "status": "PASS",
                }
            )
        self.write_raw(
            f"regression/{role}-compound-receipt.v1.json",
            {
                "schemaVersion": "s1.4x-regression-compound-receipt-v1",
                "benchmarkSubjectCommit": self.subject,
                "project": project,
                "collectedCount": counts[0],
                "basePassedCount": counts[1],
                "deselectedCount": counts[2],
                "replacementPassedCount": counts[3],
                "totalExecutedPassedCount": counts[4],
                "deselectedNodeIds": deselected,
                "replacementNodeIds": replacements,
                "commands": commands,
                "status": "PASS",
            },
        )

    def _write_scala_source_evidence(self) -> None:
        manifest_path = self.repository / S1 / "scala/source-inputs.v1.json"
        manifest = json.loads(manifest_path.read_text())
        files = manifest["files"]
        checked = list(files)
        policy_sha = self._repo_sha(str(S1 / "contract/scala-source-policy.v1.json"))
        manifest_sha = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
        source_tree = [
            {"path": path, "sha256": entry["sha256"]}
            for path, entry in files.items()
        ]
        semantic_path = (
            "scala/scalafix/scala-semantic-policy-receipt.v1.json"
        )
        self.write_raw(
            semantic_path,
            {
                "schemaVersion": "s1.4x-scala-semantic-policy-receipt-v1",
                "policySha256": policy_sha,
                "sourceInputManifestSha256": manifest_sha,
                "checkedFiles": checked,
                "sourceTreeSha256": hashlib.sha256(
                    _canonical(source_tree)[:-1]
                ).hexdigest(),
                "checkerMode": "semanticdb",
                "semanticSmokeStatus": "PASS",
                "semanticdb": {"status": "PASS"},
                "scalafix": {"status": "PASS"},
                "rule": {"status": "PASS"},
                "execution": {"status": "PASS"},
                "negativeMatrix": [{"status": "PASS"}],
                "status": "PASS",
            },
        )
        semantic_sha = hashlib.sha256(
            (self.correctness / semantic_path).read_bytes()
        ).hexdigest()
        base = {
            "schemaVersion": "s1.4x-scala-source-policy-result-v1",
            "policySha256": policy_sha,
            "sourceInputManifestSha256": manifest_sha,
            "semanticReceiptSha256": semantic_sha,
            "checkerMode": "semanticdb",
            "semanticSmokeStatus": "PASS",
            "checkedFiles": checked,
            "violations": [],
            "usedAllowlistEntries": [],
            "staleAllowlistEntries": [],
            "sourceSetExact": True,
            "aggregateStatus": "PASS",
        }
        wrapper_path = "scala/scala-source-policy-result.v1.json"
        core_sha = self._write_raw_blob(
            wrapper_path + ".core",
            _canonical(base),
        )
        stdout_sha = self._write_raw_blob(wrapper_path + ".stdout")
        stderr_sha = self._write_raw_blob(wrapper_path + ".stderr", b"")
        argv = ["python", "scala-policy"]
        self.write_raw(
            wrapper_path,
            {
                **base,
                "coreResultSha256": core_sha,
                "process": {
                    "portableArgv": argv,
                    "portableArgvSha256": hashlib.sha256(
                        _canonical(argv)[:-1]
                    ).hexdigest(),
                    "runtimeArgvSha256": "6" * 64,
                    "exitCode": 0,
                    "stdoutSha256": stdout_sha,
                    "stderrSha256": stderr_sha,
                    "status": "PASS",
                },
            },
        )
        project = self.repository / S1 / "scala/project.scala"
        coordinate = "org.scala-lang:scala3-library_3:3.8.4"
        self.write_raw(
            "scala/scala-dependency-edge-result.v1.json",
            {
                "schemaVersion": "s1.4x-scala-dependency-native-edge-result-v1",
                "policySha256": policy_sha,
                "sourceInputManifestSha256": manifest_sha,
                "projectSha256": hashlib.sha256(project.read_bytes()).hexdigest(),
                "dependencies": [
                    {
                        "coordinate": coordinate,
                        "coordinateSha256": hashlib.sha256(
                            coordinate.encode()
                        ).hexdigest(),
                        "nativeInterop": False,
                    }
                ],
                "forbiddenSourceFindings": [],
                "candidateAuthoredEdgeCount": 0,
                "candidateAddedNativeDependencyCount": 0,
                "candidateCoreDirectNativeBindingImportCount": 0,
                "candidateCoreDirectNativeBindingCallCount": 0,
                "timedKernelExplicitCandidateNativeInteropCallCount": 0,
                "unknownEdgeCount": 0,
                "aggregateStatus": "PASS",
            },
        )
        copied = {"exitCode": 0, "downloadLineCount": 0}
        source_sha = "7" * 64
        self.write_raw(
            "scala/scalafmt/scala-scalafmt-idempotence-result.v1.json",
            {
                "schemaVersion": "s1.4x-scala-scalafmt-idempotence-result-v1",
                "scalafmtVersion": "3.11.4",
                "scalafmtArtifact": "pinned",
                "networkPolicy": "OFFLINE_PINNED_LAUNCHER",
                "configPath": ".scalafmt.conf",
                "configSha256": "8" * 64,
                "sourceInputManifestSha256": manifest_sha,
                "toolchainLockSha256": self._repo_sha(
                    str(S1 / "scala/toolchain-lock.v1.json")
                ),
                "checkedFiles": checked,
                "sourceBeforeSha256": source_sha,
                "firstPassSourceSha256": source_sha,
                "secondPassSourceSha256": source_sha,
                "formattedSourcePatchSha256": "9" * 64,
                "firstApply": {"status": "PASS"},
                "secondApply": {"status": "PASS"},
                "copiedNonMutatingCheck": copied,
                "nonMutatingCheck": {"status": "PASS"},
                "misformattedNegative": {"status": "PASS"},
                "status": "PASS",
            },
        )

    def select_scala_profile(self, profile: str) -> None:
        compiler_profiles_path = (
            self.repository / S1 / "scala/compiler-profiles.v1.json"
        )
        compiler_profiles = json.loads(compiler_profiles_path.read_text())
        options = compiler_profiles["profiles"][profile]["additionalOptions"]
        manifest_path = self.repository / S1 / "scala/source-inputs.v1.json"
        manifest = json.loads(manifest_path.read_text())
        manifest_sha = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
        toolchain_path = self.repository / S1 / "scala/toolchain-lock.v1.json"
        toolchain = json.loads(toolchain_path.read_text())
        toolchain_sha = hashlib.sha256(toolchain_path.read_bytes()).hexdigest()
        plan_path = self.repository / S1 / "benchmarks/benchmark-plan.v1.json"
        plan_sha = self._repo_sha(str(S1 / "benchmarks/benchmark-plan.v1.json"))
        profiles_sha = hashlib.sha256(compiler_profiles_path.read_bytes()).hexdigest()
        option_sha = hashlib.sha256(_canonical(options)[:-1]).hexdigest()
        qualification_inputs = [
            path
            for path, entry in manifest["files"].items()
            if entry["role"] in {"configuration", "main", "benchmark"}
        ]
        plan_document = json.loads(plan_path.read_text())
        policy = plan_document["scalaProfileQualification"]
        requested_ratios = {
            "A": {"A": 1.0, "B": 1.06, "C": 1.06},
            "B": {"A": 1.0, "B": 0.90, "C": 0.92},
            "C": {"A": 1.0, "B": 0.90, "C": 0.88},
        }[profile]
        scores: dict[tuple[int, str, str], float] = {}
        blocks = []
        effective_closure: list[str] = []
        for repetition, order in enumerate(SCALA_PROFILE_ORDERS, start=1):
            measurements = []
            profile_evidence = []
            for profile_index, candidate_profile in enumerate(order):
                effective_hashes = []
                for case_index, case_id in enumerate(QUALIFICATION_CASES):
                    score = requested_ratios[candidate_profile]
                    scores[(repetition, candidate_profile, case_id)] = score
                    sequence = repetition * 100 + profile_index * 10 + case_index
                    effective_sha = f"{sequence + 1000:064x}"
                    effective_hashes.append(effective_sha)
                    effective_closure.append(effective_sha)
                    measurements.append(
                        {
                            "profileId": candidate_profile,
                            "caseId": case_id,
                            "scoreNsPerInvocation": score,
                            "rawNativeJsonSha256": f"{sequence + 2000:064x}",
                            "effectiveJvmArgsSha256": effective_sha,
                            "jmhRunResultSha256": f"{sequence + 3000:064x}",
                        }
                    )
                candidate_options = compiler_profiles["profiles"][
                    candidate_profile
                ]["additionalOptions"]
                profile_evidence.append(
                    {
                        "profileId": candidate_profile,
                        "plannedCaseOrder": list(QUALIFICATION_CASES),
                        "actualCaseOrder": list(QUALIFICATION_CASES),
                        "startedAt": (
                            f"2026-01-01T00:{repetition:02d}:"
                            f"{profile_index * 2:02d}Z"
                        ),
                        "endedAt": (
                            f"2026-01-01T00:{repetition:02d}:"
                            f"{profile_index * 2 + 1:02d}Z"
                        ),
                        "hostValiditySha256": f"{repetition * 10 + profile_index:064x}",
                        "scalaCliBinarySha256": toolchain["scalaCli"][
                            "binarySha256"
                        ],
                        "profileOptionsSha256": hashlib.sha256(
                            _canonical(candidate_options)[:-1]
                        ).hexdigest(),
                        "sourceInputManifestSha256": manifest_sha,
                        "effectiveJvmArgsSha256": hashlib.sha256(
                            _canonical(effective_hashes)[:-1]
                        ).hexdigest(),
                        "caseCount": 7,
                    }
                )
            blocks.append(
                {
                    "outerRepetition": repetition,
                    "plannedProfileOrder": list(order),
                    "actualProfileOrder": list(order),
                    "hostValiditySha256": hashlib.sha256(
                        _canonical(
                            [
                                item["hostValiditySha256"]
                                for item in profile_evidence
                            ]
                        )[:-1]
                    ).hexdigest(),
                    "effectiveJvmArgsSha256": hashlib.sha256(
                        _canonical(
                            [
                                item["effectiveJvmArgsSha256"]
                                for item in profile_evidence
                            ]
                        )[:-1]
                    ).hexdigest(),
                    "profileEvidence": profile_evidence,
                    "measurements": measurements,
                }
            )
        profile_results, computed_profile = _scala_profiles_from_scores(
            policy,
            scores,
        )
        assert computed_profile == profile
        selector_sha = _scala_selector_sha(
            policy=policy,
            benchmark_plan_sha256=plan_sha,
            blocks=blocks,
        )
        qualification_path = (
            "scala/qualification/scala-profile-qualification.v1.json"
        )
        self.write_raw(
            qualification_path,
            {
                "schemaVersion": "s1.4x-scala-profile-qualification-v1",
                "benchmarkPlanSha256": plan_sha,
                "selectorConfigSha256": selector_sha,
                "sourceInputManifestSha256": manifest_sha,
                "profileOptionsSha256": hashlib.sha256(
                    _canonical(
                        {
                            candidate_profile: compiler_profiles["profiles"][
                                candidate_profile
                            ]["additionalOptions"]
                            for candidate_profile in ("A", "B", "C")
                        }
                    )[:-1]
                ).hexdigest(),
                "scalaCliBinarySha256": toolchain["scalaCli"]["binarySha256"],
                "jvmArgumentAllowlistSha256": "b" * 64,
                "profileRunInputPaths": qualification_inputs,
                "effectiveJvmArgsClosureSha256": hashlib.sha256(
                    _canonical(effective_closure)[:-1]
                ).hexdigest(),
                "blocks": blocks,
                "status": "PASS",
            },
        )
        qualification_sha = hashlib.sha256(
            (self.correctness / qualification_path).read_bytes()
        ).hexdigest()
        root = f"scala/profiles/{profile}"
        candidate_sha = self._write_raw_blob(
            f"{root}/candidate.jar",
            f"candidate-{profile}\n".encode(),
        )
        matrix_paths = {
            "candidateResultSha256": "canonical-results.json",
            "semanticResultSha256": "semantic-errors.json",
            "unitTestResultSha256": "scala-profile-unit-test-result.v1.json",
            "unitStdoutSha256": "unit-test.stdout",
            "unitStderrSha256": "unit-test.stderr",
            "canonicalComparisonSha256": "canonical-comparison.json",
            "semanticComparisonSha256": "semantic-comparison.json",
            "propertyReportSha256": "property/scala-property-report.v1.json",
            "registryReportSha256": "property/scala-registry-report.v1.json",
            "propertyExecutionEvidenceSha256": (
                "property/scala-property-execution-evidence.v1.json"
            ),
        }
        matrix: dict[str, str] = {}
        for field, suffix in matrix_paths.items():
            relative = f"{root}/{suffix}"
            if suffix == "canonical-results.json":
                payload = _canonical(self._actual("canonical", "scala-3.8.4-jvm25"))
            elif suffix == "semantic-errors.json":
                payload = _canonical(self._actual("semantic", "scala-3.8.4-jvm25"))
            elif suffix == "canonical-comparison.json":
                payload = _canonical(self._comparison("canonical"))
            elif suffix == "semantic-comparison.json":
                payload = _canonical(self._comparison("semantic"))
            else:
                payload = b"fixture\n"
            matrix[field] = self._write_raw_blob(relative, payload)
        matrix.update(
            {
                "propertyPlanSha256": self._repo_sha(
                    str(S1 / "contract/property-plan.v1.json")
                ),
                "propertySeedCorpusSha256": self._repo_sha(
                    str(S1 / "contract/fixtures/property/property-seeds.v1.json")
                ),
                "functionRegistrySha256": self._repo_sha(
                    str(S1 / "contract/function-registry.v1.json")
                ),
                "errorRegistrySha256": self._repo_sha(
                    str(S1 / "contract/error-registry.v1.json")
                ),
            }
        )
        profile_inputs = [
            path
            for path, entry in manifest["files"].items()
            if entry["role"] != "benchmark"
        ]
        correctness_path = (
            f"{root}/scala-profile-correctness-result.v1.json"
        )
        self.write_raw(
            correctness_path,
            {
                "schemaVersion": "s1.4x-scala-profile-correctness-v1",
                "profileId": profile,
                "compilerProfilesSha256": profiles_sha,
                "profileOptions": options,
                "profileOptionsSha256": option_sha,
                "sourceInputManifestSha256": manifest_sha,
                "toolchainLockSha256": toolchain_sha,
                "scalaCliBinarySha256": toolchain["scalaCli"]["binarySha256"],
                "profileRunInputPaths": profile_inputs,
                "candidateSha256": candidate_sha,
                "matrix": matrix,
                "mismatchCount": 0,
                "status": "PASS",
            },
        )
        correctness_sha = hashlib.sha256(
            (self.correctness / correctness_path).read_bytes()
        ).hexdigest()
        self.write_raw(
            f"scala/hard-compiler-{profile}/scala-hard-compiler-result.v1.json",
            {
                "schemaVersion": "s1.4x-scala-hard-compiler-result-v1",
                "profileId": profile,
                "scalaVersion": "3.8.4",
                "jdkRelease": "25",
                "toolPathId": "SCALA_CLI_1_15_0",
                "resolvedBinarySha256": toolchain["scalaCli"]["binarySha256"],
                "toolchainLockSha256": toolchain_sha,
                "compilerProfilesSha256": profiles_sha,
                "profileOptionsSha256": option_sha,
                "sourceInputManifestSha256": manifest_sha,
                "compileInputPaths": profile_inputs,
                "positiveFlags": [
                    {"optionGroup": ["-Werror"], "exitCode": 0, "status": "PASS"}
                ],
                "negativeWarnings": [
                    {"exitCode": 1, "status": "PASS"} for _ in range(4)
                ],
                "fullCompile": {"exitCode": 0, "status": "PASS"},
                "diagnosticOnly": [],
                "aggregateStatus": "PASS",
            },
        )
        self.write_raw(
            "scala/scala-selected-profile-result.v1.json",
            {
                "schemaVersion": "s1.4x-scala-selected-profile-result-v1",
                "benchmarkPlanSha256": plan_sha,
                "selectorConfigSha256": selector_sha,
                "qualificationSha256": qualification_sha,
                "sourceInputManifestSha256": manifest_sha,
                "compilerProfilesSha256": profiles_sha,
                "toolchainLockSha256": toolchain_sha,
                "mergedToolchainProvenanceSha256": toolchain[
                    "mergedToolchainProvenanceSha256"
                ],
                "scalaCliBinarySha256": toolchain["scalaCli"]["binarySha256"],
                "javaExecutableSha256": "d" * 64,
                "jvmArgumentAllowlistSha256": "b" * 64,
                "effectiveJvmArgumentsCapabilitySha256": "b" * 64,
                "profileOptionsSha256": hashlib.sha256(
                    _canonical(
                        {
                            candidate_profile: compiler_profiles["profiles"][
                                candidate_profile
                            ]["additionalOptions"]
                            for candidate_profile in ("A", "B", "C")
                        }
                    )[:-1]
                ).hexdigest(),
                "selectedProfileSourceSha256": self._repo_sha(
                    str(S1 / "scala/selected-profile.scala")
                ),
                "selectedProfileOptions": options,
                "selectedProfileOptionsSha256": option_sha,
                "correctnessResultSha256": correctness_sha,
                "profiles": profile_results,
                "selectedProfileId": profile,
                "fallbackProfileId": "A",
                "fallbackExecuted": profile == "A",
                "selectionStatus": "PASS",
            },
        )
        coverage = self.correctness / "coverage/integration-coverage.json"
        if coverage.exists():
            selected_haskell = json.loads(
                (
                    self.repository / S1 / "haskell/selected-profile.v1.json"
                ).read_text()
            )
            self._write_coverage(profile, selected_haskell["profileId"])
            binaries = {
                candidate_profile: self.read_raw(
                    f"haskell/profiles/{candidate_profile}/"
                    "correctness-receipt.v1.json"
                )["candidateBinarySha256"]
                for candidate_profile in (
                    "baseline-o0-fasm",
                    "optimized-o2-fasm",
                )
            }
            self._write_oci_evidence(binaries)

    def _write_haskell_evidence(self) -> dict[str, str]:
        manifest_path = self.repository / S1 / "haskell/source-inputs.v1.json"
        manifest = json.loads(manifest_path.read_text())
        manifest_sha = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
        policy_sha = self._repo_sha(
            str(S1 / "contract/haskell-module-safety-policy.v1.json")
        )
        modules = []
        for path, entry in manifest["files"].items():
            if not path.endswith(".hs"):
                continue
            category = (
                "test"
                if path.startswith("test/")
                else "io-shell"
                if path.startswith(("src/contract/", "app/"))
                else "safe-scalar"
            )
            modules.append(
                {
                    "moduleName": path.removesuffix(".hs").replace("/", "."),
                    "path": path,
                    "category": category,
                    "compileMode": "safe",
                    "extensions": [],
                    "sourceSha256": entry["sha256"],
                }
            )
        self.write_raw(
            "haskell/module-safety/haskell-module-safety-result.v1.json",
            {
                "schemaVersion": "s1.4x-haskell-module-safety-result-v1",
                "policySha256": policy_sha,
                "sourceInputManifestSha256": manifest_sha,
                "modules": modules,
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
        paths_sha = hashlib.sha256(
            "".join(f"{path}\n" for path in manifest["files"]).encode()
        ).hexdigest()
        lint_logs = {
            "positive.stdout": self._write_raw_blob(
                "haskell/hlint/positive.stdout"
            ),
            "positive.stderr": self._write_raw_blob(
                "haskell/hlint/positive.stderr", b""
            ),
        }
        self.write_raw(
            "haskell/hlint/receipt.json",
            {
                "schemaVersion": "s1.4x-haskell-hlint-evidence-v1",
                "hlintPathId": "HLINT_3_10",
                "hlintPath": "/tool/hlint",
                "hlintSha256": "1" * 64,
                "hlintVersion": "3.10",
                "configurationSha256": "2" * 64,
                "sourceInputManifestSha256": manifest_sha,
                "sourceInputCanonicalManifestSha256": manifest[
                    "canonicalManifestSha256"
                ],
                "sourceInputFileCount": len(manifest["files"]),
                "sourceInputPathsSha256": paths_sha,
                "exceptionManifestSha256": "3" * 64,
                "exceptionSchemaSha256": "4" * 64,
                "fixtureManifestSha256": "5" * 64,
                "positiveArgv": ["hlint"],
                "ignoredInventoryArgv": ["hlint", "--json"],
                "ignoredInventoryExitCode": 0,
                "negativeFixtureCount": 14,
                "logs": lint_logs,
                "status": "PASS",
            },
        )
        format_logs = {
            "positive.stdout": self._write_raw_blob(
                "haskell/format/positive.stdout"
            ),
            "positive.stderr": self._write_raw_blob(
                "haskell/format/positive.stderr", b""
            ),
        }
        self.write_raw(
            "haskell/format/receipt.json",
            {
                "schemaVersion": "s1.4x-haskell-format-evidence-v1",
                "formatterPathId": "STYLISH_HASKELL_0_15_1_0",
                "formatterPath": "/tool/stylish-haskell",
                "formatterSha256": "6" * 64,
                "formatterVersion": "0.15.1.0",
                "mandatedConfigurationPath": ".stylish-haskell.yaml",
                "mandatedConfigurationSha256": "7" * 64,
                "derivedConfigurationPath": "derived.yaml",
                "derivedConfigurationSha256": "8" * 64,
                "fallbackContractPath": "fallback.json",
                "fallbackContractSha256": "9" * 64,
                "parserCapabilityReceiptSha256": "a" * 64,
                "parserCapabilityStatus": (
                    "PINNED_PARSER_COMPATIBILITY_FALLBACK"
                ),
                "sourceInputManifestSha256": manifest_sha,
                "sourceInputCanonicalManifestSha256Before": manifest[
                    "canonicalManifestSha256"
                ],
                "sourceInputCanonicalManifestSha256After": manifest[
                    "canonicalManifestSha256"
                ],
                "sourceInputFileCount": len(manifest["files"]),
                "sourceInputPathsSha256": paths_sha,
                "positiveArgv": ["stylish-haskell"],
                "positiveExitCode": 0,
                "negativeArgv": ["stylish-haskell", "-i"],
                "negativeFixturePath": "tools/fixtures/stylish/misformatted.hs",
                "negativeFixtureSha256Before": "b" * 64,
                "negativeFixtureSha256After": "b" * 64,
                "misformattedExitCode": 1,
                "sourceInputNegativeTests": [
                    "untracked-rogue-source",
                    "stale-manifest-entry",
                    "intermediate-directory-symlink",
                ],
                "logs": format_logs,
                "fallbackLimitation": "fixture parser compatibility fallback",
                "status": "PASS",
            },
        )
        selected = json.loads(
            (self.repository / S1 / "haskell/selected-profile.v1.json").read_text()
        )
        source_tree = selected["sourceTreeSha256"]
        binaries: dict[str, str] = {}
        for profile, options in (
            ("baseline-o0-fasm", ["-O0", "-fasm"]),
            ("optimized-o2-fasm", ["-O2", "-fasm"]),
        ):
            root = f"haskell/profiles/{profile}"
            commands = []
            for phase in (
                "build",
                "test",
                "canonical-process",
                "canonical-compare",
                "semantic-process",
                "semantic-compare",
            ):
                argv = (
                    ["stack", "--pedantic", f"--ghc-options={' '.join(options)}"]
                    if phase in {"build", "test"}
                    else ["tool", phase]
                )
                stdout_path = f"{root}/{phase}.stdout"
                stderr_path = f"{root}/{phase}.stderr"
                commands.append(
                    {
                        "phase": phase,
                        "argv": argv,
                        "argvSha256": hashlib.sha256(
                            _canonical(argv)[:-1]
                        ).hexdigest(),
                        "cwdPath": "/candidate",
                        "startedAt": "2026-01-01T00:00:00Z",
                        "finishedAt": "2026-01-01T00:00:01Z",
                        "exitCode": 0,
                        "stdoutPath": stdout_path,
                        "stdoutSha256": self._write_raw_blob(stdout_path),
                        "stderrPath": stderr_path,
                        "stderrSha256": self._write_raw_blob(stderr_path, b""),
                    }
                )
            comparisons = []
            for matrix in ("canonical", "semantic"):
                actual_path = f"{root}/{matrix}.actual.json"
                comparison_path = f"{root}/{matrix}.comparison.json"
                actual_sha = self._write_raw_blob(
                    actual_path,
                    _canonical(
                        self._actual(matrix, "haskell-ghc-9.10.3")
                    ),
                )
                comparison_sha = self._write_raw_blob(
                    comparison_path,
                    _canonical(self._comparison(matrix)),
                )
                comparisons.append(
                    {
                        "matrixId": matrix,
                        "requestPath": f"{matrix}-inputs.json",
                        "requestSha256": self._repo_sha(
                            str(
                                S1
                                / (
                                    "contract/fixtures/small/canonical-inputs.v1.json"
                                    if matrix == "canonical"
                                    else "contract/fixtures/invalid/semantic-errors.v1.json"
                                )
                            )
                        ),
                        "expectedPath": f"{matrix}-expected.json",
                        "expectedSha256": self._repo_sha(
                            str(
                                S1
                                / (
                                    "contract/fixtures/expected/canonical-results.v1.json"
                                    if matrix == "canonical"
                                    else "contract/fixtures/invalid/semantic-errors.expected.v1.json"
                                )
                            )
                        ),
                        "actualPath": actual_path,
                        "actualSha256": actual_sha,
                        "comparisonPath": comparison_path,
                        "comparisonSha256": comparison_sha,
                        "mismatchCount": 0,
                        "status": "PASS",
                    }
                )
            binary_sha = hashlib.sha256(profile.encode()).hexdigest()
            binaries[profile] = binary_sha
            self.write_raw(
                f"{root}/correctness-receipt.v1.json",
                {
                    "schemaVersion": "s1.4x-haskell-full-correctness-v1",
                    "status": "PASS",
                    "profileId": profile,
                    "ghcOptions": options,
                    "optionsSha256": hashlib.sha256(
                        _canonical(options)[:-1]
                    ).hexdigest(),
                    "compilerVersion": "9.10.3",
                    "compilerPath": "/tool/ghc",
                    "compilerSha256": audit_module.AUTHORITATIVE_GHC_SHA256,
                    "candidateSourceCommit": self.subject,
                    "sourceTreeSha256": source_tree,
                    "candidateBinaryPath": "/candidate/bin",
                    "candidateBinarySha256": binary_sha,
                    "stackRootPath": "/stack-root",
                    "stackWorkDir": ".stack-work-fixture",
                    "stackYamlPath": "stack.yaml",
                    "stackYamlSha256": self._repo_sha(
                        str(S1 / "haskell/stack.yaml")
                    ),
                    "commands": commands,
                    "comparisonArtifacts": comparisons,
                    "mismatchCount": 0,
                },
            )
        plan_path = self.repository / S1 / "benchmarks/benchmark-plan.v1.json"
        config = json.loads(plan_path.read_text())["haskellProfileQualification"]
        blocks = []
        for block_index, order in enumerate(HASKELL_PROFILE_ORDERS):
            profiles = []
            for profile_index, profile_id in enumerate(order):
                options = {
                    "baseline-o0-fasm": ["-O0", "-fasm"],
                    "optimized-o2-fasm": ["-O2", "-fasm"],
                }[profile_id]
                marker_argv = ["python", "marker", profile_id]
                profiles.append(
                    {
                        "profileId": profile_id,
                        "ghcOptions": options,
                        "optionsSha256": hashlib.sha256(
                            _canonical(options)[:-1]
                        ).hexdigest(),
                        "startedAt": (
                            f"2026-01-01T00:{block_index:02d}:"
                            f"{profile_index * 2:02d}Z"
                        ),
                        "finishedAt": (
                            f"2026-01-01T00:{block_index:02d}:"
                            f"{profile_index * 2 + 1:02d}Z"
                        ),
                        "hostValidityPath": "/evidence/host.json",
                        "hostValiditySha256": "1" * 64,
                        "hostDockerRouteBeforeSha256": "2" * 64,
                        "hostDockerRouteAfterSha256": "2" * 64,
                        "hostCommand": {"argv": ["python", "host"]},
                        "rawCriterionPath": "/evidence/criterion.json",
                        "rawCriterionSha256": "3" * 64,
                        "criterionCommand": {"argv": ["criterion"]},
                        "caseSecondsPerBatch": {
                            case_id: 1.0 for case_id in QUALIFICATION_CASES
                        },
                        "marker": {
                            "path": "/evidence/marker.json",
                            "preRunSha256": "4" * 64,
                            "measurementSha256": "5" * 64,
                            "pythonPath": "/usr/bin/python3",
                            "pythonPinnedFdPath": "/proc/self/fd/10",
                            "pythonSha256": "6" * 64,
                            "scriptPath": "/candidate/profile_workflow.py",
                            "scriptPinnedFdPath": "/proc/self/fd/11",
                            "scriptSha256": "7" * 64,
                            "argv": marker_argv,
                            "argvSha256": hashlib.sha256(
                                _canonical(marker_argv)[:-1]
                            ).hexdigest(),
                            "portableWitness": {
                                "profileId": profile_id,
                            },
                        },
                    }
                )
            blocks.append(
                {
                    "orderBlock": block_index,
                    "plannedProfileOrder": list(order),
                    "actualProfileOrder": list(order),
                    "profiles": profiles,
                    "ratios": {
                        case_id: 1.0 for case_id in QUALIFICATION_CASES
                    },
                }
            )
        self.write_raw(
            "haskell/qualification/qualification-artifact.v1.json",
            {
                "schemaVersion": "s1.4x-haskell-profile-qualification-v1",
                "status": "PASS",
                "candidateSourceCommit": self.subject,
                "planPathId": "S1_4X_BENCHMARK_PLAN",
                "planSha256": hashlib.sha256(plan_path.read_bytes()).hexdigest(),
                "selectorConfigSha256": hashlib.sha256(
                    _canonical(config)[:-1]
                ).hexdigest(),
                "sourceTreeSha256": source_tree,
                "stackWorkDir": ".stack-work-fixture",
                "qualificationCaseOrder": list(QUALIFICATION_CASES),
                "plannedProfileOrderBlocks": [
                    list(order) for order in HASKELL_PROFILE_ORDERS
                ],
                "dockerRoute": {"status": "PASS"},
                "blocks": blocks,
                "selection": {
                    "profileId": selected["profileId"],
                    "selectedBy": selected["selectedBy"],
                    "pairedRatios": [1.0] * 28,
                    "perCaseMaxima": {
                        case_id: 1.0 for case_id in QUALIFICATION_CASES
                    },
                    "aggregateRatio": 1.0,
                    "improvingOuterRepetitions": 0,
                },
            },
        )
        return binaries

    def _write_haskell_generated_cabal(self) -> None:
        haskell_root = self.repository / S1 / "haskell"
        cabal = haskell_root / "s1-4x-haskell.cabal"
        artifact_path = (
            self.correctness
            / "coverage/haskell/generated/s1-4x-haskell.cabal"
        )
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_bytes(cabal.read_bytes())
        artifact_sha = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
        selected = json.loads(
            (haskell_root / "selected-profile.v1.json").read_text()
        )
        output = (
            self.correctness / "coverage/haskell"
        ).resolve(strict=True)
        stack_root_id = (
            "S1_4X_CACHE_ROOT/stack-root-property-"
            + hashlib.sha256(
                b"property\0" + os.fsencode(str(output))
            ).hexdigest()[:24]
        )
        toolchain = json.loads(
            (haskell_root / "toolchain-lock.v1.json").read_text()
        )
        stack = toolchain["resolvedTools"]["stack"]
        portable_argv = [
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
        manifest = haskell_root / "source-inputs.v1.json"
        package = haskell_root / "package.yaml"
        self.write_raw(
            "coverage/haskell/haskell-generated-cabal-provenance.v1.json",
            {
                "schemaVersion": (
                    "s1.4x-haskell-generated-cabal-provenance-v1"
                ),
                "benchmarkSubjectCommit": self.subject,
                "toolchainLockSha256": hashlib.sha256(
                    (haskell_root / "toolchain-lock.v1.json").read_bytes()
                ).hexdigest(),
                "packageYaml": {
                    "path": (S1 / "haskell/package.yaml").as_posix(),
                    "blobSha256": hashlib.sha256(package.read_bytes()).hexdigest(),
                },
                "sourceInputManifest": {
                    "path": (S1 / "haskell/source-inputs.v1.json").as_posix(),
                    "blobSha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
                },
                "stack": {
                    "pathId": stack["pathId"],
                    "version": stack["version"],
                    "binarySha256": stack["sha256"],
                },
                "hpack": {
                    "version": "0.39.6",
                    "versionOutputSha256": hashlib.sha256(
                        b"0.39.6\n"
                    ).hexdigest(),
                },
                "build": {
                    "portableArgv": portable_argv,
                    "portableArgvSha256": hashlib.sha256(
                        _canonical(portable_argv)[:-1]
                    ).hexdigest(),
                    "runtimeArgvSha256": "e" * 64,
                    "stackRootPathId": stack_root_id,
                    "exitCode": 0,
                },
                "generatedCabal": {
                    "repositoryRelativePath": (
                        S1 / "haskell/s1-4x-haskell.cabal"
                    ).as_posix(),
                    "artifactPath": (
                        "coverage/haskell/generated/s1-4x-haskell.cabal"
                    ),
                    "sha256": artifact_sha,
                    "sizeBytes": len(artifact_path.read_bytes()),
                    "preBuildSha256": artifact_sha,
                    "postBuildSha256": artifact_sha,
                },
                "sourceTreeSha256": selected["sourceTreeSha256"],
                "propertyClosureSha256": self._haskell_property_closure(),
                "status": "PASS",
            },
        )

    def _haskell_property_closure(self) -> str:
        haskell_root = self.repository / S1 / "haskell"
        paths = (
            set(self._candidate_haskell_paths())
            | set(audit_module.HASKELL_PROPERTY_CLOSURE_INPUTS)
            | {"s1-4x-haskell.cabal"}
        )
        digest = hashlib.sha256()
        for path in sorted(paths):
            digest.update(path.encode())
            digest.update(b"\0")
            digest.update(
                hashlib.sha256((haskell_root / path).read_bytes())
                .hexdigest()
                .encode()
            )
            digest.update(b"\n")
        return digest.hexdigest()

    def _write_oci_evidence(self, haskell_binaries: dict[str, str]) -> None:
        selected_scala = self.read_raw(
            "scala/scala-selected-profile-result.v1.json"
        )
        profile = selected_scala["selectedProfileId"]
        candidate_path = self.correctness / f"scala/profiles/{profile}/candidate.jar"
        candidate_sha = hashlib.sha256(candidate_path.read_bytes()).hexdigest()
        containerfile_sha = self._repo_sha(str(S1 / "scala/Containerfile"))
        docker_identity = {
            "dockerCliPathId": "DOCKER",
            "dockerCliSha256": "1" * 64,
            "contextName": "default",
            "daemonId": "daemon",
            "serverVersion": "1",
            "operatingSystem": "linux",
            "architecture": "amd64",
        }
        base_id = "sha256:" + "2" * 64
        image_id = "sha256:" + "3" * 64
        fixture_sha = "4" * 64
        labels = {
            "org.opencontainers.image.s1-4x.candidate-sha256": candidate_sha,
            "org.opencontainers.image.s1-4x.base-reference": (
                audit_module.SCALA_BASE_IMAGE
            ),
            "org.opencontainers.image.s1-4x.base-image-id": base_id,
            "org.opencontainers.image.s1-4x.containerfile-sha256": (
                containerfile_sha
            ),
            "org.opencontainers.image.s1-4x.fixture-tree-sha256": fixture_sha,
        }
        build_path = "oci/scala/scala-oci-build-result.v1.json"
        self.write_raw(
            build_path,
            {
                "schemaVersion": "s1.4x-scala-oci-build-result-v2",
                "baseImageReference": audit_module.SCALA_BASE_IMAGE,
                "baseImageReferenceSource": "caller-digest-argument",
                "baseImageId": base_id,
                "candidateSha256": candidate_sha,
                "containerfileSha256": containerfile_sha,
                "fixtureTreeSha256": fixture_sha,
                "imageId": image_id,
                "localTag": "s1-4x-scala:test",
                "dockerIdentity": docker_identity,
                "inspectedLabels": labels,
                "buildNetwork": "none",
                "pull": False,
                "buildUsedIidfile": True,
                "aggregateStatus": "PASS",
            },
        )
        build_sha = hashlib.sha256(
            (self.correctness / build_path).read_bytes()
        ).hexdigest()
        binding = {
            "schemaVersion": "s1.4x-scala-oci-runtime-binding-v1",
            "imageId": image_id,
            "buildReceiptSha256": build_sha,
            "candidateSha256": candidate_sha,
            "baseImageReference": audit_module.SCALA_BASE_IMAGE,
            "baseImageId": base_id,
            "dockerIdentity": docker_identity,
            "status": "PASS",
        }
        binding_bytes = _canonical(binding)
        binding_sha = self._write_raw_blob(
            "oci/scala/runtime/oci-runtime-binding-before.v1.json",
            binding_bytes,
        )
        self._write_raw_blob(
            "oci/scala/runtime/oci-runtime-binding-after.v1.json",
            binding_bytes,
        )
        scala_runtime_hashes: dict[str, str] = {}
        for matrix in ("canonical", "semantic"):
            scala_runtime_hashes[f"{matrix}ResultSha256"] = self._write_raw_blob(
                f"oci/scala/runtime/{matrix}-"
                + ("results.json" if matrix == "canonical" else "errors.json"),
                _canonical(self._actual(matrix, "scala-3.8.4-jvm25")),
            )
            scala_runtime_hashes[
                f"{matrix}ComparisonSha256"
            ] = self._write_raw_blob(
                f"oci/scala/runtime/{matrix}-comparison.json",
                _canonical(self._comparison(matrix)),
            )
        self.write_raw(
            "oci/scala/runtime/scala-oci-correctness-result.v1.json",
            {
                "schemaVersion": "s1.4x-scala-oci-correctness-result-v2",
                "imageId": image_id,
                "buildReceiptSha256": build_sha,
                "candidateSha256": candidate_sha,
                "baseImageReference": audit_module.SCALA_BASE_IMAGE,
                "baseImageId": base_id,
                "dockerIdentity": docker_identity,
                "dockerIdentitySha256": hashlib.sha256(
                    _canonical(docker_identity)[:-1]
                ).hexdigest(),
                "runtimeNetwork": "none",
                "readOnlyRoot": True,
                "capabilitiesDropped": "ALL",
                "sourceTreeMounted": False,
                "userHomeMounted": False,
                "credentialMounted": False,
                **scala_runtime_hashes,
                "runtimeBindingSha256": binding_sha,
                "mismatchCount": 0,
                "aggregateStatus": "PASS",
            },
        )

        selected_path = self.repository / S1 / "haskell/selected-profile.v1.json"
        selected = json.loads(selected_path.read_text())
        haskell_profile = selected["profileId"]
        haskell_container_sha = self._repo_sha(str(S1 / "haskell/Containerfile"))
        phases = (
            "oci-stack-build",
            "oci-context-before",
            "oci-daemon-before",
            "oci-base-before",
            "oci-image-build",
            "oci-image-id-inspect",
            "oci-image-inspect",
            "oci-canonical-run",
            "oci-canonical-tag-check",
            "oci-canonical-compare",
            "oci-semantic-run",
            "oci-semantic-tag-check",
            "oci-semantic-compare",
            "oci-base-after",
            "oci-context-after",
            "oci-daemon-after",
        )
        image_tag = "s1-4x-haskell:test"
        haskell_image_id = "sha256:" + "5" * 64
        haskell_base_id = "sha256:" + "6" * 64
        commands = []
        phase_stdout: dict[str, str] = {}
        for phase in phases:
            if phase == "oci-image-build":
                argv = [
                    "/usr/bin/docker",
                    "build",
                    "--platform",
                    "linux/amd64",
                    "--network",
                    "none",
                    "--pull=false",
                    "--iidfile",
                    "/evidence/image.iid",
                    "--tag",
                    image_tag,
                    "/context",
                ]
            elif phase in {"oci-canonical-run", "oci-semantic-run"}:
                argv = [
                    "/usr/bin/docker",
                    "run",
                    "--platform",
                    "linux/amd64",
                    "--rm",
                    "--network",
                    "none",
                    "--read-only",
                    "--cap-drop=ALL",
                    "--security-opt=no-new-privileges",
                    "--mount",
                    "type=bind,src=/evidence/runtime,dst=/out",
                    haskell_image_id,
                ]
            else:
                argv = ["/usr/bin/docker", phase]
            stdout_path = f"oci/haskell/{phase}.stdout"
            stderr_path = f"oci/haskell/{phase}.stderr"
            stdout_sha = self._write_raw_blob(stdout_path)
            phase_stdout[phase] = stdout_sha
            commands.append(
                {
                    "phase": phase,
                    "argv": argv,
                    "argvSha256": hashlib.sha256(
                        _canonical(argv)[:-1]
                    ).hexdigest(),
                    "cwdPath": "/candidate",
                    "startedAt": "2026-01-01T00:00:00Z",
                    "finishedAt": "2026-01-01T00:00:01Z",
                    "exitCode": 0,
                    "stdoutPath": stdout_path,
                    "stdoutSha256": stdout_sha,
                    "stderrPath": stderr_path,
                    "stderrSha256": self._write_raw_blob(stderr_path, b""),
                }
            )
        comparisons = []
        for matrix in ("canonical", "semantic"):
            actual_sha = self._write_raw_blob(
                f"oci/haskell/runtime/{matrix}.actual.json",
                _canonical(self._actual(matrix, "haskell-ghc-9.10.3")),
            )
            comparison_sha = self._write_raw_blob(
                f"oci/haskell/{matrix}.oci-comparison.json",
                _canonical(self._comparison(matrix)),
            )
            comparisons.append(
                {
                    "matrixId": matrix,
                    "actualSha256": actual_sha,
                    "comparisonSha256": comparison_sha,
                    "mismatchCount": 0,
                    "status": "PASS",
                }
            )
        daemon = {"id": "daemon", "version": "1"}
        context = {
            "binarySha256": haskell_binaries[haskell_profile],
            "containerfileSha256": haskell_container_sha,
            "fixtureTreeSha256": "7" * 64,
        }
        self.write_raw(
            "oci/haskell/oci-correctness-receipt.v1.json",
            {
                "schemaVersion": "s1.4x-haskell-oci-correctness-v1",
                "status": "PASS",
                "candidateSourceCommit": self.subject,
                "sourceTreeSha256": selected["sourceTreeSha256"],
                "selectedProfileSha256": hashlib.sha256(
                    selected_path.read_bytes()
                ).hexdigest(),
                "profileId": haskell_profile,
                "ghcOptions": selected["ghcOptions"],
                "optionsSha256": selected["optionsSha256"],
                "containerfileSha256": haskell_container_sha,
                "baseImage": audit_module.HASKELL_BASE_IMAGE,
                "baseImageId": haskell_base_id,
                "baseInspectionBeforeSha256": phase_stdout["oci-base-before"],
                "baseInspectionAfterSha256": phase_stdout["oci-base-after"],
                "stackRootPath": "/stack-root",
                "stackWorkDir": ".stack-work-fixture",
                "contextSnapshot": context,
                "fixtureTreeSha256": context["fixtureTreeSha256"],
                "candidateBinarySha256": haskell_binaries[haskell_profile],
                "dockerPath": "/usr/bin/docker",
                "dockerPathId": "DOCKER",
                "dockerSha256": "8" * 64,
                "expectedDockerSha256": "8" * 64,
                "dockerConfigPath": "/docker-config",
                "dockerTrustBaseline": {"status": "PASS"},
                "dockerTrustStageSnapshots": [{"status": "PASS"}],
                "daemonIdentitySha256": hashlib.sha256(
                    _canonical(daemon)[:-1]
                ).hexdigest(),
                "dockerContextName": "default",
                "daemonIdentityBefore": daemon,
                "daemonIdentityAfter": daemon,
                "imageTag": image_tag,
                "imageId": haskell_image_id,
                "iidFileSha256": "9" * 64,
                "provenanceLabels": {
                    "io.s1-4x.base-image-id": haskell_base_id,
                    "io.s1-4x.containerfile-sha256": haskell_container_sha,
                    "io.s1-4x.fixture-tree-sha256": context[
                        "fixtureTreeSha256"
                    ],
                },
                "platform": "linux/amd64",
                "runtimeImageSubject": {
                    "referenceType": "immutable-image-id",
                    "imageId": haskell_image_id,
                },
                "imageTagBindingChecks": [
                    {
                        "phase": phase,
                        "imageTag": image_tag,
                        "imageId": haskell_image_id,
                        "inspectionSha256": phase_stdout[phase],
                        "status": "PASS",
                    }
                    for phase in (
                        "oci-image-inspect",
                        "oci-canonical-tag-check",
                        "oci-semantic-tag-check",
                    )
                ],
                "buildNetwork": "none",
                "runtimeNetwork": "none",
                "runtimeMounts": ["output-only"],
                "commands": commands,
                "comparisons": comparisons,
                "mismatchCount": 0,
            },
        )
        self.write_raw(
            "oci/cross-language-comparison.json",
            self._comparison("canonical"),
        )

    def _write_coverage(self, scala_profile: str, haskell_profile: str) -> None:
        scala_root = self.repository / S1 / "scala"
        scala_manifest = json.loads(
            (scala_root / "source-inputs.v1.json").read_text()
        )
        scala_digest = hashlib.sha256()
        for path in sorted(scala_manifest["files"]):
            if (
                path in {"project.scala", "selected-profile.scala"}
                or path.startswith(("src/main/scala/", "src/test/scala/"))
            ):
                scala_digest.update(path.encode())
                scala_digest.update(b"\0")
                scala_digest.update((scala_root / path).read_bytes())
                scala_digest.update(b"\0")
        haskell_root = self.repository / S1 / "haskell"
        haskell_closure = self._haskell_property_closure()
        property_plan_path = (
            self.repository / S1 / "contract/property-plan.v1.json"
        )
        property_plan = json.loads(property_plan_path.read_text())
        property_plan_sha = hashlib.sha256(
            property_plan_path.read_bytes()
        ).hexdigest()
        seed_sha = self._repo_sha(
            str(S1 / "contract/fixtures/property/property-seeds.v1.json")
        )
        property_seeds = json.loads(
            (
                self.repository
                / S1
                / "contract/fixtures/property/property-seeds.v1.json"
            ).read_text()
        )["seeds"]
        execution_entries = []
        for property_id in PROPERTY_IDS:
            seed_executions = [
                {
                    "seedIndex": seed_index,
                    "originalSeed": seed,
                    "successfulTests": 42,
                    "discardedTests": 0,
                    "attemptedTests": 42,
                    "replayToken": f"seed-v1:{seed}",
                    "shrinks": 0,
                    "status": "PASS",
                }
                for seed_index, seed in enumerate(property_seeds)
            ]
            execution_entries.append(
                {
                    "propertyId": property_id,
                    "successfulTests": 1008,
                    "discardedTests": 0,
                    "attemptedTests": 1008,
                    "shrinks": 0,
                    "seedCount": 24,
                    "seedExecutions": seed_executions,
                    "status": "PASS",
                }
            )
        scala_toolchain = json.loads(
            (scala_root / "toolchain-lock.v1.json").read_text()
        )
        scala_runner_sha = self._repo_sha(
            str(S1 / "scala/tools/run-property-evidence.sh")
        )
        common_execution = {
            "schemaVersion": "s1.4x-candidate-property-execution-v1",
            "propertyPlanSha256": property_plan_sha,
            "seedCorpusSha256": seed_sha,
            "seedCount": 24,
            "minimumSuccessfulPerSeed": 42,
            "commandArgvSha256": "c" * 64,
            "startedAt": "2026-01-01T00:00:00Z",
            "finishedAt": "2026-01-01T00:00:01Z",
            "exitCode": 0,
            "properties": execution_entries,
            "status": "PASS",
        }
        self.write_raw(
            "coverage/scala/scala-property-execution-evidence.v1.json",
            {
                **common_execution,
                "implementation": "scala-3.8.4-jvm25",
                "maximumDiscardRatio": property_plan["maximumDiscardRatio"],
                "framework": "scala-check-1.19.0",
                "toolchainProfile": scala_profile,
                "scalaCliBinarySha256": scala_toolchain["scalaCli"][
                    "binarySha256"
                ],
                "runnerSha256": scala_runner_sha,
                "sourceClosureSha256": scala_digest.hexdigest(),
            },
        )
        haskell_selected_path = haskell_root / "selected-profile.v1.json"
        haskell_selected = json.loads(haskell_selected_path.read_text())
        haskell_runner_sha = self._repo_sha(
            str(S1 / "haskell/tools/run-property-evidence.sh")
        )
        self.write_raw(
            "coverage/haskell/haskell-property-execution-evidence.v1.json",
            {
                **common_execution,
                "implementation": "haskell",
                "framework": "QuickCheck-2.15.0.1",
                "toolchainProfile": (
                    f"haskell-ghc-9.10.3-{haskell_profile}"
                ),
                "outerCommandArgvSha256": "d" * 64,
                "buildArgvSha256": "e" * 64,
                "runnerSha256": haskell_runner_sha,
                "sourceClosureSha256": haskell_closure,
                "sourceInputManifestSha256": self._repo_sha(
                    str(S1 / "haskell/source-inputs.v1.json")
                ),
                "selectedProfileSha256": hashlib.sha256(
                    haskell_selected_path.read_bytes()
                ).hexdigest(),
                "sourceTreeSha256": haskell_selected["sourceTreeSha256"],
                "propertyClosureSha256": haskell_closure,
                "profileGhcOptions": haskell_selected["ghcOptions"],
                "profileOptionsSha256": haskell_selected["optionsSha256"],
                "stackRootPathId": (
                    "S1_4X_CACHE_ROOT/stack-root-property-"
                    + hashlib.sha256(
                        b"property\0"
                        + os.fsencode(
                            str(
                                (
                                    self.correctness / "coverage/haskell"
                                ).resolve(strict=True)
                            )
                        )
                    ).hexdigest()[:24]
                ),
            },
        )
        error_modes = {
            "processDynamic": 29,
            "referenceObjectModel": 1,
            "registryStatic": 2,
        }
        candidates = []
        for candidate in CANDIDATES:
            is_scala = candidate == "scala"
            candidates.append(
                {
                    "implementation": candidate,
                    "reportedImplementation": (
                        "scala-3.8.4-jvm25" if is_scala else "haskell"
                    ),
                    "propertyPlanSha256": self._repo_sha(
                        str(S1 / "contract/property-plan.v1.json")
                    ),
                    "propertyCount": 25,
                    "functionCount": 20,
                    "errorCount": 32,
                    "errorTrackCounts": {"s1.4": 19, "s1.4r": 13},
                    "errorVerificationModeCounts": error_modes,
                    "processDynamicErrorCount": 29,
                    "staticErrorCount": 3,
                    "propertyExecution": {
                        "framework": (
                            "scala-check-1.19.0"
                            if is_scala
                            else "QuickCheck-2.15.0.1"
                        ),
                        "toolchainProfile": (
                            scala_profile
                            if is_scala
                            else f"haskell-ghc-9.10.3-{haskell_profile}"
                        ),
                        "seedCorpusSha256": self._repo_sha(
                            str(
                                S1
                                / "contract/fixtures/property/property-seeds.v1.json"
                            )
                        ),
                        "seedCount": 24,
                        "minimumSuccessfulPerSeed": 42,
                        "runnerSha256": self._repo_sha(
                            str(
                                S1
                                / (
                                    "scala/tools/run-property-evidence.sh"
                                    if is_scala
                                    else "haskell/tools/run-property-evidence.sh"
                                )
                            )
                        ),
                        "sourceClosureSha256": (
                            scala_digest.hexdigest()
                            if is_scala
                            else haskell_closure
                        ),
                        "startedAt": "2026-01-01T00:00:00Z",
                        "finishedAt": "2026-01-01T00:00:01Z",
                    },
                    "status": "PASS",
                }
            )
        self.write_raw(
            "coverage/integration-coverage.json",
            {
                "schemaVersion": "s1.4x-integration-coverage-v1",
                "candidateCount": 2,
                "candidates": candidates,
                "propertyCountPerCandidate": 25,
                "functionCountPerCandidate": 20,
                "errorTrackCountsPerCandidate": {"s1.4": 19, "s1.4r": 13},
                "errorVerificationModeCountsPerCandidate": error_modes,
                "status": "PASS",
            },
        )

    def _write_raw_evidence(self) -> None:
        for matrix in ("canonical", "semantic"):
            root = f"cross-language/{matrix}"
            self.write_raw(
                f"{root}/comparison-report.json",
                self._comparison(matrix),
            )
            expected_path = (
                S1 / "contract/fixtures/expected/canonical-results.v1.json"
                if matrix == "canonical"
                else S1
                / "contract/fixtures/invalid/semantic-errors.expected.v1.json"
            )
            self.write_raw(
                f"{root}/reference-capture.json",
                {
                    "schemaVersion": "s1.4x-reference-capture-report-v1",
                    "uvVersion": "0.11.26",
                    "processCount": 2,
                    "projects": ["production", "research"],
                    "resultSha256": self._repo_sha(str(expected_path)),
                    "status": "PASS",
                },
            )
            self.write_raw(
                f"{root}/scala-results.json",
                self._actual(matrix, "scala-3.8.4-jvm25"),
            )
            self.write_raw(
                f"{root}/haskell-results.json",
                self._actual(matrix, "haskell-ghc-9.10.3"),
            )
            artifacts = {
                name: hashlib.sha256(
                    (self.correctness / root / name).read_bytes()
                ).hexdigest()
                for name in (
                    "reference-capture.json",
                    "scala-results.json",
                    "haskell-results.json",
                    "comparison-report.json",
                )
            }
            self.write_raw(
                f"{root}/correctness-summary.json",
                {
                    "schemaVersion": "s1.4x-integration-correctness-v1",
                    "requestId": self._comparison(matrix)["requestId"],
                    "oracleImplementation": "python-frozen-oracle",
                    "candidateImplementations": [
                        "scala-3.8.4-jvm25",
                        "haskell-ghc-9.10.3",
                    ],
                    "caseCount": 0,
                    "mismatchCount": 0,
                    "artifacts": artifacts,
                    "referenceCaptureStatus": "PASS",
                    "status": "PASS",
                },
            )
        self._write_regression("production")
        self._write_regression("research")
        self._write_scala_source_evidence()
        self.select_scala_profile("A")
        haskell_binaries = self._write_haskell_evidence()
        self._write_haskell_generated_cabal()
        selected = json.loads(
            (
                self.repository / S1 / "haskell/selected-profile.v1.json"
            ).read_text()
        )
        self._write_coverage("A", selected["profileId"])
        self._write_oci_evidence(haskell_binaries)

    def write_raw(self, relative: str, value: Any) -> None:
        _write_json(self.correctness / relative, value)

    def read_raw(self, relative: str) -> dict[str, Any]:
        value = json.loads((self.correctness / relative).read_text())
        assert isinstance(value, dict)
        return value

    def run(self) -> dict[str, dict[str, Any]]:
        return generate_candidate_rubric_audit(
            repository_root=self.repository,
            benchmark_subject_commit=self.subject,
            correctness_root=self.correctness,
            output_root=self.output,
        )


class CandidateRubricAuditTests(TestCase):
    def fixture(self) -> _Fixture:
        temporary = Path(self.enterContext(tempfile.TemporaryDirectory()))
        return _Fixture(temporary)

    def test_happy_path_writes_exact_two_candidate_twelve_rubric_closure(
        self,
    ) -> None:
        fixture = self.fixture()
        result = fixture.run()

        self.assertEqual(tuple(result), CANDIDATES)
        for candidate in CANDIDATES:
            path = fixture.output / f"{candidate}-candidate-rubric-audit.v1.json"
            payload = path.read_bytes()
            document = json.loads(payload)
            self.assertEqual(payload, _canonical(document))
            self.assertEqual(
                document["schemaVersion"], "s1.4x-candidate-rubric-audit-v1"
            )
            self.assertEqual(document["benchmarkSubjectCommit"], fixture.subject)
            self.assertEqual(document["candidate"], candidate)
            self.assertEqual(
                tuple(entry["rubricId"] for entry in document["rubrics"]),
                RUBRIC_IDS,
            )
            self.assertEqual(len(document["rubrics"]), 12)
            for entry in document["rubrics"]:
                self.assertTrue(entry["objectiveChecks"])
                self.assertTrue(entry["reviewedArtifacts"])
                self.assertTrue(entry["repositoryArtifacts"])
                self.assertEqual(entry["findings"], [])
                self.assertEqual(entry["status"], "PASS")
            self.assertEqual(document["status"], "PASS")

    def test_selected_scala_hard_compiler_routes_to_b_and_c(self) -> None:
        for profile in ("B", "C"):
            with self.subTest(profile=profile):
                temporary = Path(self.enterContext(tempfile.TemporaryDirectory()))
                fixture = _Fixture(temporary)
                fixture.select_scala_profile(profile)
                result = fixture.run()["scala"]
                warning = next(
                    entry
                    for entry in result["rubrics"]
                    if entry["rubricId"] == "maintainability-warning-free"
                )
                paths = {item["path"] for item in warning["reviewedArtifacts"]}
                self.assertIn(
                    f"scala/hard-compiler-{profile}/scala-hard-compiler-result.v1.json",
                    paths,
                )
                self.assertNotIn(
                    "scala/hard-compiler-A/scala-hard-compiler-result.v1.json",
                    paths,
                )

    def test_pending_haskell_profile_cannot_receive_a_rubric_pass(self) -> None:
        fixture = self.fixture()
        selected_path = fixture.repository / S1 / "haskell/selected-profile.v1.json"
        selected = json.loads(selected_path.read_text())
        selected["schemaVersion"] = "s1.4x-haskell-selected-profile-pending-v1"
        selected_path.write_bytes(_canonical(selected))
        fixture.subject = fixture.commit()
        fixture.refresh_subject_bindings()
        with self.assertRaisesRegex(
            CandidateRubricAuditError,
            "HASKELL_SELECTED_PROFILE",
        ):
            fixture.run()

    def test_missing_and_tampered_typed_evidence_fail_closed(self) -> None:
        fixture = self.fixture()
        (fixture.correctness / "scala/scala-source-policy-result.v1.json").unlink()
        with self.assertRaisesRegex(CandidateRubricAuditError, "RAW_EVIDENCE"):
            fixture.run()

        fixture = self.fixture()
        selected = fixture.read_raw("scala/scala-selected-profile-result.v1.json")
        selected["correctnessResultSha256"] = "0" * 64
        fixture.write_raw(
            "scala/scala-selected-profile-result.v1.json",
            selected,
        )
        with self.assertRaisesRegex(
            CandidateRubricAuditError,
            "SCALA_SELECTED_PROFILE",
        ):
            fixture.run()

    def test_swapped_semantic_and_canonical_comparisons_fail(self) -> None:
        fixture = self.fixture()
        canonical = fixture.read_raw("cross-language/canonical/comparison-report.json")
        semantic = fixture.read_raw("cross-language/semantic/comparison-report.json")
        fixture.write_raw(
            "cross-language/canonical/comparison-report.json",
            semantic,
        )
        fixture.write_raw(
            "cross-language/semantic/comparison-report.json",
            canonical,
        )
        with self.assertRaisesRegex(
            CandidateRubricAuditError,
            "COMPARISON_CANONICAL",
        ):
            fixture.run()

    def test_undocumented_public_core_boundary_fails(self) -> None:
        fixture = self.fixture()
        source = (
            fixture.repository
            / S1
            / "scala/src/main/scala/ai/trading/coach/s14x/core/Math.scala"
        )
        source.write_text(
            source.read_text().replace(
                "  /** 두 유한 입력을 더해 stable numeric 결과를 반환한다. */\n",
                "",
            ),
            encoding="utf-8",
        )
        fixture.subject = fixture.commit()
        fixture.refresh_subject_bindings()
        with self.assertRaisesRegex(
            CandidateRubricAuditError,
            "UNDOCUMENTED_PUBLIC_BOUNDARY",
        ):
            fixture.run()

    def test_duplicate_normalized_production_block_fails(self) -> None:
        fixture = self.fixture()
        duplicate = """object CopiedBlock:
  private def copied(value: Double): Double =
    val first = value + 1.0
    val second = first * 2.0
    val third = second - 3.0
    val fourth = third / 4.0
    val fifth = fourth + 5.0
    val sixth = fifth * 6.0
    sixth
"""
        for name in ("CopiedOne.scala", "CopiedTwo.scala"):
            path = (
                fixture.repository
                / S1
                / "scala/src/main/scala/ai/trading/coach/s14x/core"
                / name
            )
            path.write_text(
                "package ai.trading.coach.s14x.core\n\n" + duplicate,
                encoding="utf-8",
            )
        fixture.subject = fixture.commit()
        fixture.refresh_subject_bindings()
        with self.assertRaisesRegex(
            CandidateRubricAuditError,
            "DUPLICATE_PRODUCTION_BLOCK",
        ):
            fixture.run()

    def test_identifier_only_constructor_binder_sequence_is_not_duplication(
        self,
    ) -> None:
        fixture = self.fixture()
        core = fixture.repository / S1 / "haskell/src/core/S14X/Core"
        for suffix in ("One", "Two"):
            (core / f"Binder{suffix}.hs").write_text(
                f"""module S14X.Core.Binder{suffix} (Binder{suffix} (..)) where

data Binder{suffix}
  = ConditionalCoverageResult
      Double
      Double
      Bool
      Int
      Int
      Int
      Double

consume{suffix} ::
  Binder{suffix} ->
  Double
consume{suffix}
  ( ConditionalCoverageResult
      statistic
      pValue
      reject
      observations
      exceptions
      dof
      significance
    ) =
    statistic + pValue + fromIntegral observations + fromIntegral exceptions
      + fromIntegral dof + significance + if reject then 1.0 else 0.0
""",
                encoding="utf-8",
            )
        fixture.refresh_haskell_selected()
        fixture.subject = fixture.commit()
        fixture.refresh_subject_bindings()
        result = fixture.run()
        self.assertEqual(result["haskell"]["status"], "PASS")

    def test_haskell_operational_duplicate_block_still_fails(self) -> None:
        fixture = self.fixture()
        core = fixture.repository / S1 / "haskell/src/core/S14X/Core"
        operation = """copied value =
  let first = value + 1.0
      second = first * 2.0
      third = second - 3.0
      fourth = third / 4.0
      fifth = fourth + 5.0
      sixth = fifth * 6.0
   in sixth
"""
        for suffix in ("One", "Two"):
            (core / f"Copied{suffix}.hs").write_text(
                f"module S14X.Core.Copied{suffix} () where\n\n" + operation,
                encoding="utf-8",
            )
        fixture.subject = fixture.commit()
        fixture.refresh_subject_bindings()
        with self.assertRaisesRegex(
            CandidateRubricAuditError,
            "DUPLICATE_PRODUCTION_BLOCK",
        ):
            fixture.run()

    def test_weak_test_structure_fails(self) -> None:
        fixture = self.fixture()
        test_root = (
            fixture.repository / S1 / "scala/src/test/scala/ai/trading/coach/s14x"
        )
        for path in test_root.rglob("*.scala"):
            path.unlink()
        (test_root / "core").mkdir(parents=True, exist_ok=True)
        (test_root / "core/OnlySuite.scala").write_text(
            'class OnlySuite:\n  test("x"):\n    assert(true)\n',
            encoding="utf-8",
        )
        fixture.subject = fixture.commit()
        fixture.refresh_subject_bindings()
        with self.assertRaisesRegex(
            CandidateRubricAuditError,
            "TEST_STRUCTURE_WEAK",
        ):
            fixture.run()

    def test_dirty_and_wrong_subject_fail(self) -> None:
        fixture = self.fixture()
        dirty = fixture.repository / "untracked.txt"
        dirty.write_text("dirty\n", encoding="utf-8")
        with self.assertRaisesRegex(
            CandidateRubricAuditError,
            "SUBJECT_INVALID",
        ):
            fixture.run()
        dirty.unlink()
        fixture.subject = "0" * 40
        with self.assertRaisesRegex(
            CandidateRubricAuditError,
            "SUBJECT_INVALID",
        ):
            fixture.run()

    def test_symlink_and_output_path_escape_fail(self) -> None:
        fixture = self.fixture()
        policy = fixture.correctness / "scala/scala-source-policy-result.v1.json"
        target = fixture.correctness / "outside.json"
        target.write_bytes(policy.read_bytes())
        policy.unlink()
        policy.symlink_to(target)
        with self.assertRaisesRegex(
            CandidateRubricAuditError,
            "RAW_EVIDENCE",
        ):
            fixture.run()

        fixture = self.fixture()
        with self.assertRaisesRegex(
            CandidateRubricAuditError,
            "OUTPUT_ROOT_INVALID",
        ):
            generate_candidate_rubric_audit(
                repository_root=fixture.repository,
                benchmark_subject_commit=fixture.subject,
                correctness_root=fixture.correctness,
                output_root=fixture.correctness.parent / "rubric-audit",
            )

    def test_raw_receipt_toctou_is_detected_before_output(self) -> None:
        fixture = self.fixture()
        original = audit_module._verify_raw_snapshots_unchanged

        def tamper_then_verify(
            correctness_fd: int,
            snapshots: tuple[audit_module.RawSnapshot, ...],
        ) -> None:
            path = (
                fixture.correctness
                / "cross-language/canonical/comparison-report.json"
            )
            path.write_bytes(path.read_bytes() + b" ")
            original(correctness_fd, snapshots)

        with (
            patch.object(
                audit_module,
                "_verify_raw_snapshots_unchanged",
                side_effect=tamper_then_verify,
            ),
            self.assertRaisesRegex(
                CandidateRubricAuditError,
                "RAW_EVIDENCE_CHANGED",
            ),
        ):
            fixture.run()
        self.assertFalse(fixture.output.exists())

    def test_missing_production_regression_receipt_fails_closed(self) -> None:
        fixture = self.fixture()
        (
            fixture.correctness
            / "regression/production-compound-receipt.v1.json"
        ).unlink()

        with self.assertRaisesRegex(
            CandidateRubricAuditError,
            "PRODUCTION_REGRESSION|RAW_EVIDENCE",
        ):
            fixture.run()

    def test_haskell_property_execution_uses_real_producer_identity(self) -> None:
        fixture = self.fixture()
        path = (
            "coverage/haskell/"
            "haskell-property-execution-evidence.v1.json"
        )
        execution = fixture.read_raw(path)
        execution["implementation"] = "haskell-ghc-9.10.3"
        execution["toolchainProfile"] = "haskell-baseline-o0-fasm"
        fixture.write_raw(path, execution)

        with self.assertRaisesRegex(
            CandidateRubricAuditError,
            "HASKELL_PROPERTY_EXECUTION",
        ):
            fixture.run()

    def test_property_execution_schema_rejects_missing_and_extra_fields(
        self,
    ) -> None:
        fixture = self.fixture()
        path = "coverage/scala/scala-property-execution-evidence.v1.json"
        execution = fixture.read_raw(path)
        execution.pop("maximumDiscardRatio")
        execution["unproducedField"] = True
        fixture.write_raw(path, execution)

        with self.assertRaisesRegex(
            CandidateRubricAuditError,
            "SCALA_PROPERTY_EXECUTION",
        ):
            fixture.run()

    def test_stale_scala_policy_cannot_hide_subject_side_effect(self) -> None:
        fixture = self.fixture()
        source = (
            fixture.repository
            / S1
            / "scala/src/main/scala/ai/trading/coach/s14x/core/Math.scala"
        )
        source.write_text(
            source.read_text().replace(
                "    val first = left\n",
                '    println("stale policy must not hide this side effect")\n'
                "    val first = left\n",
            ),
            encoding="utf-8",
        )
        fixture.subject = fixture.commit()
        fixture.refresh_subject_bindings(refresh_scala=False)

        with self.assertRaisesRegex(
            CandidateRubricAuditError,
            "SCALA_SOURCE|SOURCE_INPUT",
        ):
            fixture.run()

    def test_workflow_comment_cannot_forge_pull_request_trigger(self) -> None:
        fixture = self.fixture()
        workflow = (
            fixture.repository
            / ".github/workflows/s1-4x-numeric-parity-correctness.yml"
        )
        workflow.write_text(
            workflow.read_text().replace(
                "  pull_request:\n",
                "  push:\n    # pull_request:\n",
            ),
            encoding="utf-8",
        )
        fixture.subject = fixture.commit()
        fixture.refresh_subject_bindings()

        with self.assertRaisesRegex(
            CandidateRubricAuditError,
            "CI_CORRECTNESS_STRUCTURE",
        ):
            fixture.run()

    def test_workflow_comments_cannot_forge_required_path_filters(self) -> None:
        fixture = self.fixture()
        workflow = (
            fixture.repository
            / ".github/workflows/s1-4x-numeric-parity-correctness.yml"
        )
        text = workflow.read_text()
        for required in CORRECTNESS_TRIGGER_PATHS:
            text = text.replace(f'      - "{required}"\n', "")
        text += "\n# paths:\n" + "\n".join(
            f'#   - "{required}"' for required in CORRECTNESS_TRIGGER_PATHS
        )
        workflow.write_text(text, encoding="utf-8")
        fixture.subject = fixture.commit()
        fixture.refresh_subject_bindings()

        with self.assertRaisesRegex(
            CandidateRubricAuditError,
            "CI_CORRECTNESS_STRUCTURE",
        ):
            fixture.run()

    def test_subject_blob_modes_are_exact_for_runners_and_regular_files(
        self,
    ) -> None:
        fixture = self.fixture()
        scala_runner = (
            fixture.repository / S1 / "scala/tools/run-property-evidence.sh"
        )
        haskell_runner = (
            fixture.repository / S1 / "haskell/tools/run-property-evidence.sh"
        )
        self.assertEqual(stat.S_IMODE(scala_runner.stat().st_mode), 0o755)
        self.assertEqual(stat.S_IMODE(haskell_runner.stat().st_mode), 0o755)
        audit_module._git_blob(
            fixture.repository,
            fixture.subject,
            audit_module.SCALA_PROPERTY_RUNNER,
        )
        audit_module._git_blob(
            fixture.repository,
            fixture.subject,
            audit_module.HASKELL_PROPERTY_RUNNER,
        )

        scala_runner.chmod(0o644)
        fixture.subject = fixture.commit()
        fixture.refresh_subject_bindings()
        with self.assertRaisesRegex(
            CandidateRubricAuditError,
            "REPOSITORY_BLOB_INVALID",
        ):
            fixture.run()

        fixture = self.fixture()
        ordinary = (
            fixture.repository
            / S1
            / "scala/src/main/scala/ai/trading/coach/s14x/core/Math.scala"
        )
        ordinary.chmod(0o755)
        fixture.subject = fixture.commit()
        fixture.refresh_subject_bindings()
        with self.assertRaisesRegex(
            CandidateRubricAuditError,
            "REPOSITORY_BLOB_INVALID",
        ):
            fixture.run()

    def test_property_ids_seed_order_and_accounting_are_exact(self) -> None:
        mutations = (
            lambda document: document["properties"][0].__setitem__(
                "propertyId",
                "unplanned.property",
            ),
            lambda document: document["properties"][0]["seedExecutions"][0].__setitem__(
                "originalSeed",
                -999,
            ),
            lambda document: document["properties"][0]["seedExecutions"][0].__setitem__(
                "successfulTests",
                -1,
            ),
            lambda document: document["properties"][0]["seedExecutions"][0].__setitem__(
                "attemptedTests",
                43,
            ),
            lambda document: document["properties"][0].__setitem__(
                "successfulTests",
                1007,
            ),
        )
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                fixture = self.fixture()
                for language in ("scala", "haskell"):
                    path = (
                        f"coverage/{language}/"
                        f"{language}-property-execution-evidence.v1.json"
                    )
                    document = fixture.read_raw(path)
                    mutate(document)
                    fixture.write_raw(path, document)
                with self.assertRaisesRegex(
                    CandidateRubricAuditError,
                    "PROPERTY_EXECUTION",
                ):
                    fixture.run()

    def test_generated_cabal_is_raw_not_git_and_provenance_bound(self) -> None:
        fixture = self.fixture()
        self.assertEqual(
            _git(
                fixture.repository,
                "ls-files",
                str(S1 / "haskell/s1-4x-haskell.cabal"),
            ),
            "",
        )
        self.assertEqual(fixture.run()["haskell"]["status"], "PASS")

        fixture = self.fixture()
        artifact = (
            fixture.correctness
            / "coverage/haskell/generated/s1-4x-haskell.cabal"
        )
        artifact.write_bytes(artifact.read_bytes() + b"-- tampered\n")
        with self.assertRaisesRegex(
            CandidateRubricAuditError,
            "GENERATED_CABAL",
        ):
            fixture.run()

    def test_scala_selector_profiles_and_fallback_are_recomputed(self) -> None:
        fixture = self.fixture()
        selected_path = "scala/scala-selected-profile-result.v1.json"
        selected = fixture.read_raw(selected_path)
        selected["profiles"]["B"]["qualified"] = True
        fixture.write_raw(selected_path, selected)
        with self.assertRaisesRegex(
            CandidateRubricAuditError,
            "SCALA_(SELECTED_PROFILE|QUALIFICATION)",
        ):
            fixture.run()

        fixture = self.fixture()
        selected = fixture.read_raw(selected_path)
        self.assertEqual(selected["selectedProfileId"], "A")
        self.assertTrue(selected["fallbackExecuted"])
        self.assertEqual(fixture.run()["scala"]["status"], "PASS")

    def test_haskell_qualification_ratios_and_selection_are_recomputed(self) -> None:
        fixture = self.fixture()
        path = "haskell/qualification/qualification-artifact.v1.json"
        qualification = fixture.read_raw(path)
        qualification["blocks"][0]["ratios"][QUALIFICATION_CASES[0]] = 0.5
        fixture.write_raw(path, qualification)
        with self.assertRaisesRegex(
            CandidateRubricAuditError,
            "HASKELL_QUALIFICATION",
        ):
            fixture.run()

    def test_cross_actual_and_summary_bind_selected_outputs(self) -> None:
        fixture = self.fixture()
        path = "cross-language/canonical/scala-results.json"
        actual = fixture.read_raw(path)
        actual["results"] = [{"forged": True}]
        fixture.write_raw(path, actual)
        with self.assertRaisesRegex(
            CandidateRubricAuditError,
            "CROSS_CANONICAL_INVALID",
        ):
            fixture.run()

    def test_oci_runtime_security_flags_are_required(self) -> None:
        fixture = self.fixture()
        path = "oci/haskell/oci-correctness-receipt.v1.json"
        receipt = fixture.read_raw(path)
        command = next(
            item
            for item in receipt["commands"]
            if item["phase"] == "oci-canonical-run"
        )
        command["argv"].remove("--read-only")
        command["argvSha256"] = hashlib.sha256(
            _canonical(command["argv"])[:-1]
        ).hexdigest()
        fixture.write_raw(path, receipt)
        with self.assertRaisesRegex(
            CandidateRubricAuditError,
            "HASKELL_OCI_COMMAND_INVALID",
        ):
            fixture.run()

    def test_current_real_subject_passes_structural_source_audits(self) -> None:
        repository = INTEGRATION.parents[4]

        def blobs(root: Path, suffix: str) -> tuple[audit_module.RepositoryBlob, ...]:
            return tuple(
                audit_module.RepositoryBlob(
                    path=audit_module.PurePosixPath(
                        path.relative_to(repository).as_posix()
                    ),
                    payload=path.read_bytes(),
                    sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                )
                for path in sorted(root.rglob(f"*{suffix}"))
            )

        scala_production = blobs(
            repository / audit_module.SCALA_CORE.parent,
            ".scala",
        )
        haskell_production = (
            *blobs(repository / audit_module.HASKELL_CORE, ".hs"),
            *blobs(repository / audit_module.HASKELL_CONTRACT, ".hs"),
        )
        haskell_tests = blobs(repository / audit_module.HASKELL_TEST, ".hs")
        audit_module._audit_duplicate_blocks(
            scala_production,
            language="scala",
        )
        audit_module._audit_duplicate_blocks(
            haskell_production,
            language="haskell",
        )
        audit_module._audit_haskell_tests(haskell_tests)

    def test_publish_race_cannot_replace_precreated_destination(self) -> None:
        for kind in ("directory", "file"):
            with self.subTest(kind=kind):
                fixture = self.fixture()
                original = audit_module._rename_noreplace

                def race_then_publish(
                    source_fd: int,
                    source_name: str,
                    destination_fd: int,
                    destination_name: str,
                ) -> None:
                    destination = fixture.correctness / destination_name
                    if kind == "directory":
                        destination.mkdir()
                    else:
                        destination.write_bytes(b"attacker\n")
                    original(
                        source_fd,
                        source_name,
                        destination_fd,
                        destination_name,
                    )

                with (
                    patch.object(
                        audit_module,
                        "_rename_noreplace",
                        side_effect=race_then_publish,
                    ),
                    self.assertRaisesRegex(
                        CandidateRubricAuditError,
                        "OUTPUT_WRITE_FAILED",
                    ),
                ):
                    fixture.run()
                destination = fixture.correctness / "rubric-audit"
                if kind == "directory":
                    self.assertTrue(destination.is_dir())
                    self.assertEqual(list(destination.iterdir()), [])
                else:
                    self.assertEqual(destination.read_bytes(), b"attacker\n")
                self.assertEqual(
                    list(fixture.correctness.glob(".rubric-audit.stage-*")),
                    [],
                )

    def test_same_file_operational_duplicate_block_fails(self) -> None:
        fixture = self.fixture()
        duplicate = """  private def copied(value: Double): Double =
    val first = value + 1.0
    val second = first * 2.0
    val third = second - 3.0
    val fourth = third / 4.0
    val fifth = fourth + 5.0
    val sixth = fifth * 6.0
    sixth
"""
        path = (
            fixture.repository
            / S1
            / "scala/src/main/scala/ai/trading/coach/s14x/core/SameFile.scala"
        )
        path.write_text(
            "package ai.trading.coach.s14x.core\n\n"
            "object SameFile:\n"
            + duplicate
            + "\n"
            + duplicate.replace("copied", "copiedAgain"),
            encoding="utf-8",
        )
        fixture.subject = fixture.commit()
        fixture.refresh_subject_bindings()

        with self.assertRaisesRegex(
            CandidateRubricAuditError,
            "DUPLICATE_PRODUCTION_BLOCK",
        ):
            fixture.run()

    def test_trivial_test_bodies_do_not_receive_readability_pass(self) -> None:
        fixture = self.fixture()
        scala_test = (
            fixture.repository
            / S1
            / "scala/src/test/scala/ai/trading/coach/s14x/core/CoreSuite.scala"
        )
        scala_test.write_text(
            'class CoreSuite:\n'
            '  test("adds two finite values deterministically"):\n'
            "    assert(true)\n",
            encoding="utf-8",
        )
        fixture.subject = fixture.commit()
        fixture.refresh_subject_bindings()

        with self.assertRaisesRegex(
            CandidateRubricAuditError,
            "TEST_STRUCTURE_WEAK",
        ):
            fixture.run()

    def test_correctness_root_swap_cannot_redirect_publication(self) -> None:
        fixture = self.fixture()
        original_write = audit_module._write_exclusive
        pinned = fixture.correctness.with_name("correctness-pinned")
        outside = fixture.correctness.with_name("attacker-output")
        outside.mkdir()
        swapped = False

        def swap_after_first_write(*arguments: Any, **keywords: Any) -> None:
            nonlocal swapped
            original_write(*arguments, **keywords)
            if not swapped:
                fixture.correctness.rename(pinned)
                fixture.correctness.symlink_to(outside, target_is_directory=True)
                swapped = True

        try:
            with (
                patch.object(
                    audit_module,
                    "_write_exclusive",
                    side_effect=swap_after_first_write,
                ),
                self.assertRaisesRegex(
                    CandidateRubricAuditError,
                    "CORRECTNESS_ROOT_CHANGED|OUTPUT",
                ),
            ):
                fixture.run()
            self.assertFalse((outside / "rubric-audit").exists())
            self.assertFalse((pinned / "rubric-audit").exists())
        finally:
            if fixture.correctness.is_symlink():
                fixture.correctness.unlink()
                pinned.rename(fixture.correctness)

    def test_second_write_failure_leaves_no_partial_output_and_retry_succeeds(
        self,
    ) -> None:
        fixture = self.fixture()
        original_write = audit_module._write_exclusive
        write_count = 0

        def fail_second_write(*arguments: Any, **keywords: Any) -> None:
            nonlocal write_count
            write_count += 1
            if write_count == 2:
                raise CandidateRubricAuditError("INJECTED_SECOND_WRITE_FAILURE")
            original_write(*arguments, **keywords)

        with (
            patch.object(
                audit_module,
                "_write_exclusive",
                side_effect=fail_second_write,
            ),
            self.assertRaisesRegex(
                CandidateRubricAuditError,
                "INJECTED_SECOND_WRITE_FAILURE",
            ),
        ):
            fixture.run()

        self.assertFalse(fixture.output.exists())
        self.assertEqual(
            list(fixture.correctness.glob(".rubric-audit.stage-*")),
            [],
        )
        self.assertEqual(fixture.run()["scala"]["status"], "PASS")
