"""S1.4X branch에서 frozen S1.4R 회귀의 branch-scope 검사를 대체한다."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[6]
S1_4X_ROOT = "workspaces/decision-platform/research/s1-4x-numeric-parity/"
S1_4X_WORKFLOWS = {
    ".github/workflows/s1-4x-numeric-parity-correctness.yml",
    ".github/workflows/s1-4x-numeric-parity-benchmark.yml",
}
S1_4R_BRANCH_SCOPE_NODE = (
    "tests/test_production_isolation.py::"
    "test_branch_diff_is_confined_to_the_research_project_and_two_workflows"
)
AGGREGATE = (
    REPO_ROOT
    / "workspaces/decision-platform/research/s1-4x-numeric-parity/"
    "integration/tools/run-native-oci-regression-gates.sh"
)
REGRESSION_GATE = (
    REPO_ROOT
    / "workspaces/decision-platform/research/s1-4x-numeric-parity/"
    "integration/regression_gate.py"
)
sys.path.insert(0, str(REGRESSION_GATE.parent))

from regression_gate import (  # noqa: E402
    DESELECTED_RESEARCH_NODE as PRODUCER_DESELECTED_RESEARCH_NODE,
)


def _changed_paths() -> tuple[str, ...]:
    completed = subprocess.run(
        ["/usr/bin/git", "diff", "--name-only", "origin/main"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return tuple(line for line in completed.stdout.splitlines() if line)


def test_s1_4x_branch_diff_is_confined_to_the_experiment_boundary() -> None:
    unexpected = [
        path
        for path in _changed_paths()
        if not path.startswith(S1_4X_ROOT) and path not in S1_4X_WORKFLOWS
    ]
    assert unexpected == []


def test_aggregate_deselects_only_the_inapplicable_s1_4r_branch_scope() -> None:
    aggregate_source = AGGREGATE.read_text(encoding="utf-8")
    producer_source = REGRESSION_GATE.read_text(encoding="utf-8")

    assert aggregate_source.count('python "$INTEGRATION/regression_gate.py"') == 1
    assert producer_source.count(
        'f"--deselect={DESELECTED_RESEARCH_NODE}"'
    ) == 1
    assert PRODUCER_DESELECTED_RESEARCH_NODE == S1_4R_BRANCH_SCOPE_NODE
    assert "S1_4R_EXECUTION_BOUNDARY=oci" not in aggregate_source
    assert "test_s1_4r_regression_boundary.py" in producer_source
