from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[6]
WORKFLOW_ROOT = REPO_ROOT / ".github" / "workflows"
CORRECTNESS = WORKFLOW_ROOT / "s1-4x-numeric-parity-correctness.yml"
BENCHMARK = WORKFLOW_ROOT / "s1-4x-numeric-parity-benchmark.yml"
AGGREGATE = (
    REPO_ROOT
    / "workspaces/decision-platform/research/s1-4x-numeric-parity/"
    "integration/tools/run-native-oci-regression-gates.sh"
)

EXPECTED_PATHS = (
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

EXPECTED_CORRECTNESS_JOBS = (
    "contract-reference-lock",
    "scala-compiler-lint-tests",
    "scala-profile-selection",
    "haskell-authoritative-selection",
    "haskell-ghc-9-14-compatibility",
    "cross-language-comparator",
    "scala-oci-correctness",
    "haskell-oci-correctness",
    "frozen-python-regressions",
)

ACTION_PINS = {
    "actions/checkout": "34e114876b0b11c390a56381ad16ebd13914f8d5",
    "actions/download-artifact": "d3f86a106a0bac45b974a628896c90dbdf5c8093",
    "actions/setup-java": "be666c2fcd27ec809703dec50e508c2fdc7f6654",
    "actions/upload-artifact": "ea165f8d65b6e75b540449e92b4886f43607fa02",
    "astral-sh/setup-uv": "d31148d669074a8d0a63714ba94f3201e7020bc3",
    "coursier/setup-action": "039f736548afa5411c1382f40a5bd9c2d30e0383",
}

FORBIDDEN_LIVE_TOKENS = (
    "secrets.",
    "apiportal.koreainvestment.com",
    "/oauth2/tokenP",
    "opendart.fss.or.kr",
    "git push",
    "gh pr ",
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _top_level_jobs(text: str) -> dict[str, str]:
    lines = text.splitlines()
    jobs_index = lines.index("jobs:")
    starts = [
        (index, match.group(1))
        for index, line in enumerate(lines[jobs_index + 1 :], start=jobs_index + 1)
        if (match := re.fullmatch(r"  ([a-z0-9][a-z0-9-]*):", line))
    ]
    result: dict[str, str] = {}
    for position, (start, job_id) in enumerate(starts):
        end = starts[position + 1][0] if position + 1 < len(starts) else len(lines)
        result[job_id] = "\n".join(lines[start:end]) + "\n"
    return result


def _event_block(text: str, event: str) -> list[str]:
    lines = text.splitlines()
    start = lines.index(f"  {event}:")
    end = next(
        (
            index
            for index in range(start + 1, len(lines))
            if re.fullmatch(r"  [A-Za-z_][A-Za-z0-9_-]*:", lines[index])
        ),
        len(lines),
    )
    return lines[start:end]


def _event_paths(text: str, event: str) -> tuple[str, ...]:
    block = _event_block(text, event)
    path_index = block.index("    paths:")
    values: list[str] = []
    for line in block[path_index + 1 :]:
        match = re.fullmatch(r"      - (.+)", line)
        if match is None:
            break
        value = ast.literal_eval(match.group(1))
        if not isinstance(value, str):
            raise AssertionError(f"{event} path is not a string: {line}")
        values.append(value)
    return tuple(values)


def _assert_common_workflow_contract(
    case: unittest.TestCase,
    *,
    text: str,
    jobs: dict[str, str],
) -> None:
    case.assertIn("permissions:\n  contents: read\n", text)
    case.assertIn("concurrency:", text)
    case.assertIn("cancel-in-progress:", text)
    for token in FORBIDDEN_LIVE_TOKENS:
        case.assertNotIn(token, text)

    uses = re.findall(r"^\s*uses:\s+([^@\s]+)@([^\s#]+)", text, flags=re.MULTILINE)
    case.assertTrue(uses)
    for action, revision in uses:
        case.assertRegex(revision, r"\A[0-9a-f]{40}\Z")
        case.assertIn(action, ACTION_PINS)
        case.assertEqual(revision, ACTION_PINS[action])

    for job_id, block in jobs.items():
        case.assertIn("runs-on: ubuntu-24.04", block, job_id)
        timeout = re.search(r"^\s{4}timeout-minutes: ([0-9]+)$", block, re.MULTILINE)
        case.assertIsNotNone(timeout, job_id)
        assert timeout is not None
        case.assertGreater(int(timeout.group(1)), 0, job_id)
        case.assertLessEqual(int(timeout.group(1)), 360, job_id)
        case.assertIn(
            f"uses: actions/checkout@{ACTION_PINS['actions/checkout']}",
            block,
            job_id,
        )
        case.assertIn("persist-credentials: false", block, job_id)


def _assert_job_local_large_fixture(
    case: unittest.TestCase,
    *,
    block: str,
    exports_root: bool,
) -> None:
    case.assertEqual(block.count("materialize_large_fixtures.py"), 2)
    case.assertEqual(
        block.count("materialize_large_fixtures.py\" materialize"),
        1,
    )
    case.assertEqual(
        block.count("materialize_large_fixtures.py\" check"),
        1,
    )
    case.assertIn(
        'LARGE_FIXTURE_ROOT="$RUNNER_TEMP/s1-4x-large-fixtures"',
        block,
    )
    case.assertIn(
        'LARGE_FIXTURE_RECEIPT="$RUNNER_TEMP/'
        's1-4x-large-fixture-receipt.json"',
        block,
    )
    case.assertEqual(block.count('--output-root "$LARGE_FIXTURE_ROOT"'), 2)
    case.assertEqual(block.count('--receipt "$LARGE_FIXTURE_RECEIPT"'), 2)
    case.assertNotIn("contract/fixtures/large/generated", block)
    case.assertNotIn("S1_4X_LARGE_FIXTURE_RECEIPT", block)
    if exports_root:
        case.assertIn(
            "S1_4X_LARGE_FIXTURE_ROOT=$LARGE_FIXTURE_ROOT",
            block,
        )


class NumericParityCorrectnessWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = _read(CORRECTNESS)
        cls.jobs = _top_level_jobs(cls.text)

    def test_exact_events_and_paths(self) -> None:
        self.assertRegex(self.text, r"(?m)^on:\n  pull_request:\n")
        self.assertEqual(_event_paths(self.text, "pull_request"), EXPECTED_PATHS)
        self.assertEqual(_event_paths(self.text, "push"), EXPECTED_PATHS)
        self.assertEqual(
            _event_block(self.text, "push")[1:4],
            ["    branches:", "      - main", "    paths:"],
        )

    def test_exact_nine_jobs(self) -> None:
        self.assertEqual(tuple(self.jobs), EXPECTED_CORRECTNESS_JOBS)
        _assert_common_workflow_contract(self, text=self.text, jobs=self.jobs)
        self.assertIn("cancel-in-progress: true", self.text)

    def test_contract_and_reference_lock_job(self) -> None:
        block = self.jobs["contract-reference-lock"]
        for token in (
            "validate_contract.py",
            "--check-all",
            "materialize_large_fixtures.py",
            "test_workflow_contract.py",
            "referenceSourceTreeCount",
        ):
            self.assertIn(token, block)
        _assert_job_local_large_fixture(
            self,
            block=block,
            exports_root=False,
        )
        self.assertNotIn('generate_large_fixtures.py" --check', block)

    def test_scala_compiler_and_profile_jobs(self) -> None:
        compiler = self.jobs["scala-compiler-lint-tests"]
        for token in (
            "run-hard-compiler-profile.sh",
            "run-scalafmt-idempotence.sh",
            "run-scalafix.sh",
            "check-source-policy.sh",
            "test-source-input-manifest.sh",
            "run-correctness-profile.sh",
            "scala-jvm-argument-allowlist.v1.json",
        ):
            self.assertIn(token, compiler)

        selection = self.jobs["scala-profile-selection"]
        self.assertIn("needs: scala-compiler-lint-tests", selection)
        for token in (
            "run-correctness-profile.sh",
            "run-profile-qualification.sh",
            "select-proven-profile.sh",
            "A,B,C",
            "actions/download-artifact",
            "actions/upload-artifact",
        ):
            self.assertIn(token, selection)
        _assert_job_local_large_fixture(
            self,
            block=selection,
            exports_root=True,
        )

    def test_haskell_authoritative_and_compatibility_jobs(self) -> None:
        authoritative = self.jobs["haskell-authoritative-selection"]
        for token in (
            "check-format.sh",
            "check-hlint.sh",
            "run-correctness-profile.sh",
            "baseline-o0-fasm",
            "optimized-o2-fasm",
            "run-profile-qualification.sh",
            "select-proven-profile.sh",
            "test_workflow_input_closure.py",
        ):
            self.assertIn(token, authoritative)
        _assert_job_local_large_fixture(
            self,
            block=authoritative,
            exports_root=True,
        )

        compatibility = self.jobs["haskell-ghc-9-14-compatibility"]
        self.assertIn("needs: haskell-authoritative-selection", compatibility)
        for token in (
            "run-ghc-9.14.1-compatibility.sh",
            "validate-ghc-9.14.1-compatibility.sh",
            "nonScoring",
            "FAIL_FROZEN_DEPENDENCY",
        ):
            self.assertIn(token, compatibility)

    def test_comparator_oci_and_frozen_regression_jobs(self) -> None:
        comparator = self.jobs["cross-language-comparator"]
        for token in (
            "scala-profile-selection",
            "haskell-authoritative-selection",
            "haskell-ghc-9-14-compatibility",
            "run-integration-correctness.sh",
            "coverage_execution.py",
            "coverage_gate.py",
            "run-property-evidence.sh",
            "integration-coverage.json",
            "compare_results.py",
            "mismatchCount",
        ):
            self.assertIn(token, comparator)
        self.assertNotIn("materialize_large_fixtures.py", comparator)

        for job_id in ("scala-oci-correctness", "haskell-oci-correctness"):
            block = self.jobs[job_id]
            self.assertIn("run-oci-correctness.sh", block)
            self.assertIn("runtimeNetwork", block)
            self.assertIn('"none"', block)
            self.assertIn("actions/upload-artifact", block)
            self.assertNotIn("materialize_large_fixtures.py", block)
            self.assertNotIn("S1_4X_LARGE_FIXTURE_ROOT", block)

        scala_oci = self.jobs["scala-oci-correctness"]
        self.assertIn("build-oci-image.sh", scala_oci)
        self.assertRegex(scala_oci, r"docker\.io/library/eclipse-temurin@sha256:[0-9a-f]{64}")
        haskell_oci = self.jobs["haskell-oci-correctness"]
        self.assertRegex(haskell_oci, r"docker\.io/library/haskell@sha256:[0-9a-f]{64}")

        regression = self.jobs["frozen-python-regressions"]
        for token in (
            "workspaces/decision-platform/python-services",
            "workspaces/decision-platform/research/s1-4r-jax-risk",
            "ruff check .",
            "mypy app",
            "mypy src benchmarks",
            "pytest -q",
            "test_s1_4r_regression_boundary.py",
            (
                "--deselect=tests/test_production_isolation.py::"
                "test_branch_diff_is_confined_to_the_research_project_and_two_workflows"
            ),
        ):
            self.assertIn(token, regression)
        self.assertNotIn("S1_4R_EXECUTION_BOUNDARY=oci", regression)
        self.assertNotIn('generate_large_fixtures.py" --check', self.text)


class NumericParityBenchmarkWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = _read(BENCHMARK)
        cls.jobs = _top_level_jobs(cls.text)

    def test_dispatch_only_smallest_default_and_explicit_full(self) -> None:
        self.assertRegex(self.text, r"(?m)^on:\n  workflow_dispatch:\n")
        self.assertNotIn("pull_request:", self.text)
        self.assertNotIn("\n  push:", self.text)
        dispatch = "\n".join(_event_block(self.text, "workflow_dispatch"))
        self.assertIn("default: smallest", dispatch)
        self.assertRegex(dispatch, r"options:\n\s+- smallest\n\s+- full")

    def test_correctness_precedes_and_gates_timing(self) -> None:
        self.assertEqual(tuple(self.jobs), ("correctness-before-timing", "bounded-timing"))
        _assert_common_workflow_contract(self, text=self.text, jobs=self.jobs)
        self.assertIn("cancel-in-progress: false", self.text)
        correctness = self.jobs["correctness-before-timing"]
        timing = self.jobs["bounded-timing"]
        self.assertEqual(
            correctness.count("run-native-oci-regression-gates.sh"),
            1,
        )
        self.assertNotIn("run-integration-correctness.sh", correctness)
        self.assertIn("needs: correctness-before-timing", timing)
        self.assertIn("needs.correctness-before-timing.result == 'success'", timing)
        self.assertNotIn("run-ghc-9.14.1-compatibility.sh", timing)

    def test_correctness_uses_the_full_serial_native_oci_aggregate(self) -> None:
        source = _read(AGGREGATE)
        self.assertNotIn("command -v", source)
        self.assertIn('S1_4X_UV_BIN:?', source)
        for token in (
            "run-hard-compiler-profile.sh",
            "run-scalafmt-idempotence.sh",
            "run-scalafix.sh",
            "check-source-policy.sh",
            "audit-scala-dependency-edges.sh",
            "run-correctness-profile.sh",
            "run-profile-qualification.sh",
            "select-proven-profile.sh",
            "check-format.sh",
            "check-hlint.sh",
            "run-ghc-9.14.1-compatibility.sh",
            "validate-ghc-9.14.1-compatibility.sh",
            "coverage_execution.py",
            "coverage_gate.py",
            "run-integration-correctness.sh",
            "build-oci-image.sh",
            "run-oci-correctness.sh",
            "test_s1_4r_regression_boundary.py",
            "materialize_large_fixtures.py",
        ):
            self.assertIn(token, source)
        self.assertIn("--output-dir", source)
        self.assertIn("ghc-9.14.1-compatibility.v1.json", source)
        self.assertEqual(
            source.count(
                '"$SCALA/tools/run-oci-correctness.sh" \\\n'
            ),
            1,
        )
        self.assertEqual(
            source.count(
                '"$HASKELL/tools/run-oci-correctness.sh" '
            ),
            1,
        )
        self.assertEqual(source.count("materialize_large_fixtures.py"), 2)
        self.assertNotIn('generate_large_fixtures.py" --check', source)

    def test_full_mode_binds_exact_v3_runtime_and_evidence_roles(self) -> None:
        timing = self.jobs["bounded-timing"]
        self.assertNotIn("--uv ", timing)
        for role in (
            "uv",
            "docker",
            "benchmarkPython",
            "scalaCli",
            "java",
            "scalafix",
            "scalafmt",
            "ghcup",
            "stack",
            "authoritativeGhc",
            "compatibilityGhc",
            "hlint",
            "stylishHaskell",
        ):
            self.assertIn(
                f'--runtime-executable "{role}=',
                timing,
            )
        for role in (
            "scalafmtArchive",
            "selectedProfileResult",
            "profileQualificationResult",
            "jvmAllowlistResult",
            "correctnessA",
            "correctnessB",
            "correctnessC",
            "baselineCorrectness",
            "optimizedCorrectness",
            "profileQualification",
        ):
            self.assertIn(
                f'--runtime-evidence "{role}=',
                timing,
            )
        for argument in (
            "--large-fixture-root \"$S1_4X_LARGE_FIXTURE_ROOT\"",
            "--large-fixture-receipt \"$LARGE_FIXTURE_RECEIPT\"",
        ):
            self.assertIn(argument, timing)

    def test_two_bounded_modes_and_artifact_policy(self) -> None:
        timing = self.jobs["bounded-timing"]
        _assert_job_local_large_fixture(
            self,
            block=timing,
            exports_root=True,
        )
        self.assertNotIn(
            'S1_4X_LARGE_FIXTURE_ROOT="$RESULT_DIR',
            timing,
        )
        for token in (
            'case "${{ inputs.matrix }}" in',
            "smallest)",
            "full)",
            "run-python-benchmark-smoke.sh",
            "run-jmh-native-smoke.sh",
            "run-benchmark-block.sh",
            "run_rotated_blocks.py",
            'S1_4X_ARTIFACT_MAX_BYTES: "536870912"',
            'test "$artifact_bytes" -le "$S1_4X_ARTIFACT_MAX_BYTES"',
            "retention-days: 14",
            "if-no-files-found: error",
        ):
            self.assertIn(token, timing)


if __name__ == "__main__":
    unittest.main()
