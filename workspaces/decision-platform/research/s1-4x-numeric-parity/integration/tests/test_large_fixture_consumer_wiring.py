"""Materialized large fixture가 shared qualification/timing 경계에만 연결되는지 검증한다."""

from __future__ import annotations

import json
import stat
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

INTEGRATION = Path(__file__).resolve().parents[1]
S1_4X_ROOT = INTEGRATION.parent
REPO_ROOT = S1_4X_ROOT.parents[3]
SOURCE_LARGE = S1_4X_ROOT / "contract" / "fixtures" / "large"
AGGREGATE = INTEGRATION / "tools" / "run-native-oci-regression-gates.sh"
CORRECTNESS_WRAPPER = INTEGRATION / "tools" / "run-integration-correctness.sh"
PYTHON_BENCHMARK_WRAPPER = INTEGRATION / "tools" / "run-python-benchmark-block.sh"
sys.path.insert(0, str(INTEGRATION))

import materialize_large_fixtures as materializer  # noqa: E402
import rotated_block_runtime as runner  # noqa: E402
from benchmark_contract import ContractError  # noqa: E402


def _source_tree_snapshot() -> tuple[tuple[str, int, int, int, int], ...]:
    snapshot = []
    for path in sorted(SOURCE_LARGE.rglob("*"), key=lambda item: item.as_posix()):
        metadata = path.lstat()
        snapshot.append(
            (
                path.relative_to(SOURCE_LARGE).as_posix(),
                stat.S_IFMT(metadata.st_mode),
                metadata.st_size,
                metadata.st_mtime_ns,
                metadata.st_ino,
            )
        )
    return tuple(snapshot)


@pytest.fixture(scope="module")
def materialized_input(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[tuple[Path, Path]]:
    root = tmp_path_factory.mktemp("large-fixture-consumer")
    output_root = root / "large-fixture-root"
    receipt = root / "large-fixture-receipt.json"
    source_before = _source_tree_snapshot()
    materializer.materialize(
        s1_4x_root=S1_4X_ROOT,
        output_root=output_root,
        receipt=receipt,
    )
    assert _source_tree_snapshot() == source_before
    yield output_root, receipt


def test_native_aggregate_materializes_and_checks_once_under_result_root() -> None:
    source = AGGREGATE.read_text(encoding="utf-8")

    assert source.count("materialize_large_fixtures.py") == 2
    assert (
        source.count('python "$INTEGRATION/materialize_large_fixtures.py" materialize')
        == 1
    )
    assert (
        source.count('python "$INTEGRATION/materialize_large_fixtures.py" check') == 1
    )
    assert 'LARGE_FIXTURE_ROOT="$RESULT_ROOT/large-fixtures"' in source
    assert 'LARGE_FIXTURE_RECEIPT="$RESULT_ROOT/large-fixture-receipt.json"' in source
    assert (
        'run_result_command_to_file \\\n'
        '  "$RESULT_ROOT/large-fixture-check-receipt.json"'
        in source
    )
    assert 'export S1_4X_LARGE_FIXTURE_ROOT="$LARGE_FIXTURE_ROOT"' in source
    assert "contract/fixtures/large/generated" not in source
    assert 'generate_large_fixtures.py" --check' not in source


def test_small_invalid_and_oci_correctness_keep_the_tracked_fixture_root() -> None:
    source = CORRECTNESS_WRAPPER.read_text(encoding="utf-8")

    assert "materialize_large_fixtures.py" not in source
    assert "S1_4X_LARGE_FIXTURE_ROOT" not in source
    assert source.count('--fixture-root "$S1_4X/contract/fixtures"') == 4


def test_rotated_input_validator_checks_exactly_once(
    materialized_input: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_root, receipt = materialized_input
    calls: list[dict[str, Path]] = []
    original_check = runner.check_materialization

    def recording_check(**arguments: Path) -> dict[str, Any]:
        calls.append(arguments)
        return original_check(**arguments)

    monkeypatch.setattr(runner, "check_materialization", recording_check)
    validated = runner._validate_large_fixture_input(
        large_fixture_root=output_root,
        large_fixture_receipt=receipt,
    )

    assert validated.root == output_root
    assert calls == [
        {
            "s1_4x_root": S1_4X_ROOT,
            "output_root": output_root,
            "receipt": receipt,
        }
    ]


def test_rotated_input_validator_rejects_missing_tamper_and_symlink(
    materialized_input: tuple[Path, Path],
    tmp_path: Path,
) -> None:
    output_root, receipt = materialized_input

    with pytest.raises(ContractError, match="LARGE_FIXTURE_INPUT_INVALID"):
        runner._validate_large_fixture_input(
            large_fixture_root=tmp_path / "missing-root",
            large_fixture_receipt=receipt,
        )

    tampered_receipt = tmp_path / "tampered-receipt.json"
    receipt_value = json.loads(receipt.read_bytes())
    receipt_value["unexpected"] = True
    tampered_receipt.write_text(
        json.dumps(
            receipt_value,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    with pytest.raises(ContractError, match="LARGE_FIXTURE_INPUT_INVALID"):
        runner._validate_large_fixture_input(
            large_fixture_root=output_root,
            large_fixture_receipt=tampered_receipt,
        )

    symlink_root = tmp_path / "symlink-root"
    symlink_root.symlink_to(output_root, target_is_directory=True)
    with pytest.raises(ContractError, match="LARGE_FIXTURE_INPUT_INVALID"):
        runner._validate_large_fixture_input(
            large_fixture_root=symlink_root,
            large_fixture_receipt=receipt,
        )


def test_rotated_run_parser_requires_explicit_root_and_receipt(tmp_path: Path) -> None:
    arguments = [
        "run",
        "--commands",
        str(tmp_path / "commands.json"),
        "--commands-sha256",
        "a" * 64,
        "--benchmark-subject-commit",
        "b" * 40,
        "--candidate-source-commit",
        "b" * 40,
        "--output-root",
        str(tmp_path / "output"),
        "--run-id",
        "fixture-input-contract",
        "--repo-root",
        str(REPO_ROOT),
    ]
    with pytest.raises(SystemExit):
        runner._parser().parse_args(arguments)

    parsed = runner._parser().parse_args(
        [
            *arguments,
            "--large-fixture-root",
            str(tmp_path / "large-fixture-root"),
            "--large-fixture-receipt",
            str(tmp_path / "large-fixture-receipt.json"),
        ]
    )
    assert parsed.large_fixture_root == tmp_path / "large-fixture-root"
    assert parsed.large_fixture_receipt == tmp_path / "large-fixture-receipt.json"


def _pinned(
    path: Path,
    descriptor: int,
) -> runner.PinnedExecutable:
    return runner.PinnedExecutable(
        binding={
            "path": str(path),
            "resolvedPath": str(path),
            "sha256": f"{descriptor:064x}",
        },
        descriptor=descriptor,
        required_seals=runner.F_SEAL_SEAL,
    )


def _lane_inputs(
    boundary_id: str,
    tmp_path: Path,
) -> tuple[
    dict[str, runner.PinnedExecutable],
    dict[str, runner.PinnedExecutable],
]:
    dependencies = {}
    for descriptor, role in enumerate(
        runner.RUNTIME_DEPENDENCY_ROLES_BY_BOUNDARY[boundary_id],
        start=100,
    ):
        if role == "java":
            path = tmp_path / "jdk/bin/java"
        elif role == "authoritativeGhc":
            path = tmp_path / "prefix/.ghcup/ghc/9.10.3/bin/ghc"
        else:
            path = tmp_path / "tools" / role
        dependencies[role] = _pinned(path, descriptor)

    evidence = {}
    for descriptor, role in enumerate(
        runner.RUNTIME_EVIDENCE_ROLES_BY_BOUNDARY[boundary_id],
        start=200,
    ):
        if role.startswith("correctness"):
            profile = role.removeprefix("correctness")
            path = (
                tmp_path
                / "evidence/scala/profiles"
                / profile
                / "scala-profile-correctness-result.v1.json"
            )
        else:
            path = tmp_path / "evidence" / f"{role}.json"
        evidence[role] = _pinned(path, descriptor)
    return dependencies, evidence


@pytest.mark.parametrize("boundary_id", runner.BOUNDARY_IDS)
def test_every_benchmark_boundary_receives_the_validated_root(
    boundary_id: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    large_root = tmp_path / "large-fixture-root"
    large_root.mkdir()
    validated = runner.ValidatedLargeFixtureInput(root=large_root)
    dependencies, evidence = _lane_inputs(boundary_id, tmp_path)

    environment = runner._benchmark_environment(
        dependencies,
        evidence,
        boundary_id=boundary_id,
        large_fixture_input=validated,
    )

    assert environment["S1_4X_LARGE_FIXTURE_ROOT"] == str(large_root)
    assert all("RECEIPT" not in key for key in environment)


@pytest.mark.parametrize("boundary_id", runner.BOUNDARY_IDS)
def test_benchmark_boundary_environment_requires_the_validated_marker(
    boundary_id: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    dependencies, evidence = _lane_inputs(boundary_id, tmp_path)

    with pytest.raises(ContractError, match="LARGE_FIXTURE_INPUT_NOT_VALIDATED"):
        runner._benchmark_environment(
            dependencies,
            evidence,
            boundary_id=boundary_id,
            large_fixture_input=None,
        )


def test_python_benchmark_wrapper_passes_explicit_materialized_root() -> None:
    source = PYTHON_BENCHMARK_WRAPPER.read_text(encoding="utf-8")

    assert (
        'readonly LARGE_FIXTURE_ROOT="${S1_4X_LARGE_FIXTURE_ROOT:'
        '?S1_4X_LARGE_FIXTURE_ROOT is required}"' in source
    )
    assert '--large-fixture-root "$LARGE_FIXTURE_ROOT"' in source
