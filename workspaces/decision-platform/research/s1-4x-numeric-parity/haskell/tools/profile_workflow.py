#!/usr/bin/env python3
"""S1.4X Haskell correctness, qualification, selector evidence workflow."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence


CORRECTNESS_SCHEMA_VERSION = "s1.4x-haskell-full-correctness-v1"
CORRECTNESS_PHASES = (
    "build",
    "test",
    "canonical-process",
    "canonical-compare",
    "semantic-process",
    "semantic-compare",
)
PROFILE_OPTIONS = {
    "baseline-o0-fasm": ("-O0", "-fasm"),
    "optimized-o2-fasm": ("-O2", "-fasm"),
}
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}")
FORBIDDEN_STACK_ENVIRONMENT = (
    "STACK_YAML",
    "STACK_ROOT",
    "STACK_OPTS",
    "STACK_CONFIG",
)


class WorkflowError(RuntimeError):
    """Workflow input이나 실행 결과가 frozen contract에서 벗어났을 때 발생한다."""


def canonical_json_bytes(value: Any, *, trailing_newline: bool = False) -> bytes:
    """Non-finite 값을 거부하는 sorted compact JSON bytes를 만든다."""

    payload = json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    if trailing_newline:
        payload += "\n"
    return payload.encode("utf-8")


def canonical_sha256(value: Any) -> str:
    """Canonical JSON의 SHA-256을 계산한다."""

    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def sha256_file(path: Path) -> str:
    """Regular non-symlink file의 SHA-256을 계산한다."""

    if path.is_symlink() or not path.is_file():
        raise WorkflowError(f"REGULAR_FILE_REQUIRED:{path}")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def strict_json_load(path: Path) -> Any:
    """중복 key와 non-finite number를 거부하며 JSON을 읽는다."""

    def object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise WorkflowError(f"DUPLICATE_JSON_KEY:{path}:{key}")
            result[key] = value
        return result

    def reject_constant(token: str) -> None:
        raise WorkflowError(f"NONFINITE_JSON_TOKEN:{path}:{token}")

    try:
        return json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=object_pairs,
            parse_constant=reject_constant,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise WorkflowError(f"INVALID_JSON:{path}") from exc


def atomic_write_json_exclusive(path: Path, value: Any) -> None:
    """새 path에만 canonical JSON을 원자적으로 발행한다."""

    if path.exists() or path.is_symlink():
        raise WorkflowError(f"OUTPUT_ALREADY_EXISTS:{path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(canonical_json_bytes(value, trailing_newline=True))
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError as exc:
            raise WorkflowError(f"OUTPUT_ALREADY_EXISTS:{path}") from exc
    finally:
        temporary.unlink(missing_ok=True)


def profile_options(profile_id: object) -> tuple[str, str]:
    """두 authoritative profile ID만 exact compiler option tuple로 변환한다."""

    if type(profile_id) is not str or profile_id not in PROFILE_OPTIONS:
        raise WorkflowError("PROFILE_ID_INVALID")
    return PROFILE_OPTIONS[profile_id]


def build_stack_command(
    *,
    ghcup: Path,
    stack: Path,
    stack_yaml: Path,
    stack_root: Path,
    ghc_version: str,
    operation: Sequence[str],
) -> list[str]:
    """GHCup offline resolver와 exact Stack project command를 조립한다."""

    if ghc_version not in {"9.10.3", "9.14.1"}:
        raise WorkflowError("GHC_VERSION_INVALID")
    if not operation or any(type(argument) is not str or not argument for argument in operation):
        raise WorkflowError("STACK_OPERATION_INVALID")
    return [
        str(ghcup),
        "--offline",
        "run",
        "--quick",
        "--ghc",
        ghc_version,
        "--stack",
        "3.11.1",
        "--",
        str(stack),
        "--stack-root",
        str(stack_root),
        "--stack-yaml",
        str(stack_yaml),
        "--no-terminal",
        "--color",
        "never",
        "--system-ghc",
        "--no-install-ghc",
        "--hpack-force",
        *operation,
    ]


def _iso_now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _absolute_regular(path: Path, *, label: str, executable: bool = False) -> Path:
    if (
        not path.is_absolute()
        or path.is_symlink()
        or not path.is_file()
        or path.resolve(strict=True) != path
        or (executable and not os.access(path, os.X_OK))
    ):
        raise WorkflowError(f"{label}_IDENTITY_INVALID")
    return path


def _absolute_existing_directory(path: Path, *, label: str) -> Path:
    if (
        not path.is_absolute()
        or path.is_symlink()
        or not path.is_dir()
        or path.resolve(strict=True) != path
    ):
        raise WorkflowError(f"{label}_DIRECTORY_INVALID")
    return path


def _reserve_directory(path: Path) -> Path:
    if not path.is_absolute():
        raise WorkflowError("OUTPUT_DIRECTORY_NOT_ABSOLUTE")
    parent = _absolute_existing_directory(path.parent, label="OUTPUT_PARENT")
    normalized = parent / path.name
    if normalized != path or path.exists() or path.is_symlink():
        raise WorkflowError("OUTPUT_DIRECTORY_NOT_NEW")
    path.mkdir(mode=0o700)
    return path


def _required_environment_path(name: str, *, executable: bool = True) -> Path:
    value = os.environ.get(name)
    if value is None:
        raise WorkflowError(f"REQUIRED_ENVIRONMENT_MISSING:{name}")
    return _absolute_regular(Path(value), label=name, executable=executable)


def _load_haskell_evidence(haskell_root: Path):
    module_path = _absolute_regular(
        haskell_root / "tools/haskell_evidence.py",
        label="HASKELL_EVIDENCE",
    )
    specification = importlib.util.spec_from_file_location(
        "s1_4x_haskell_evidence",
        module_path,
    )
    if specification is None or specification.loader is None:
        raise WorkflowError("HASKELL_EVIDENCE_IMPORT_FAILED")
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


def _repo_commit(repo_root: Path) -> str:
    completed = subprocess.run(
        ["/usr/bin/git", "-C", str(repo_root), "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    commit = completed.stdout.strip()
    if completed.returncode != 0 or COMMIT_PATTERN.fullmatch(commit) is None:
        raise WorkflowError("CANDIDATE_COMMIT_INVALID")
    dirty = subprocess.run(
        ["/usr/bin/git", "-C", str(repo_root), "status", "--porcelain=v1"],
        check=False,
        capture_output=True,
        text=True,
    )
    if dirty.returncode != 0 or dirty.stdout:
        raise WorkflowError("CANDIDATE_WORKTREE_NOT_CLEAN")
    return commit


def _sealed_child_environment(*, ghc_bin: Path, stack_bin: Path) -> dict[str, str]:
    home = os.environ.get("HOME")
    if home is None:
        raise WorkflowError("HOME_MISSING")
    home_path = _absolute_existing_directory(Path(home), label="HOME")
    environment = {
        "HOME": str(home_path),
        "PATH": (
            f"{ghc_bin.parent}:{stack_bin.parent}:"
            "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
        ),
        "LC_ALL": "C",
        "TZ": "UTC",
        "OMP_NUM_THREADS": "1",
        "OPENBLAS_NUM_THREADS": "1",
        "MKL_NUM_THREADS": "1",
        "NUMEXPR_NUM_THREADS": "1",
        "VECLIB_MAXIMUM_THREADS": "1",
        "BLIS_NUM_THREADS": "1",
    }
    return environment


def _run_logged(
    command: Sequence[str],
    *,
    cwd: Path,
    environment: Mapping[str, str],
    phase: str,
    output_directory: Path,
    expected_exit_codes: frozenset[int] = frozenset({0}),
) -> dict[str, Any]:
    if phase not in CORRECTNESS_PHASES and not phase.startswith(("qualification-", "oci-")):
        raise WorkflowError(f"COMMAND_PHASE_INVALID:{phase}")
    stdout_path = output_directory / f"{phase}.stdout"
    stderr_path = output_directory / f"{phase}.stderr"
    if stdout_path.exists() or stderr_path.exists():
        raise WorkflowError(f"COMMAND_LOG_ALREADY_EXISTS:{phase}")
    started_at = _iso_now()
    with stdout_path.open("xb") as standard_output, stderr_path.open("xb") as standard_error:
        completed = subprocess.run(
            list(command),
            cwd=cwd,
            env=dict(environment),
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=standard_output,
            stderr=standard_error,
        )
    finished_at = _iso_now()
    record = {
        "phase": phase,
        "argv": list(command),
        "argvSha256": canonical_sha256(list(command)),
        "startedAt": started_at,
        "finishedAt": finished_at,
        "exitCode": completed.returncode,
        "stdoutSha256": sha256_file(stdout_path),
        "stderrSha256": sha256_file(stderr_path),
    }
    if completed.returncode not in expected_exit_codes:
        raise WorkflowError(f"COMMAND_FAILED:{phase}:{completed.returncode}")
    return record


def _find_candidate_binary(haskell_root: Path, *, ghc_version: str) -> Path:
    candidates = sorted(
        (
            path.resolve(strict=True)
            for path in (haskell_root / ".stack-work/dist").glob(
                f"*/ghc-{ghc_version}/build/s1-4x-haskell/s1-4x-haskell"
            )
            if path.is_file() and not path.is_symlink() and os.access(path, os.X_OK)
        ),
        key=lambda path: str(path).encode(),
    )
    if len(candidates) != 1:
        raise WorkflowError(f"CANDIDATE_BINARY_CARDINALITY:{len(candidates)}")
    return candidates[0]


def _comparison_status(path: Path) -> dict[str, Any]:
    report = strict_json_load(path)
    if (
        not isinstance(report, dict)
        or report.get("schemaVersion") != "s1.4x-comparison-report-v1"
        or report.get("status") != "PASS"
        or report.get("mismatchCount") != 0
        or report.get("mismatches") != []
    ):
        raise WorkflowError(f"COMPARISON_NOT_PASS:{path}")
    return report


def _correctness(arguments: argparse.Namespace) -> None:
    output = _reserve_directory(arguments.output_dir)
    haskell_root = Path(__file__).resolve(strict=True).parent.parent
    numeric_root = haskell_root.parent
    repo_root = numeric_root.parents[3]
    profile_id = arguments.profile
    options = profile_options(profile_id)
    ghcup = _required_environment_path("S1_4X_GHCUP_BIN")
    stack = _required_environment_path("S1_4X_STACK_BIN")
    ghc = _required_environment_path("S1_4X_AUTHORITATIVE_GHC_BIN")
    cache_root_value = os.environ.get("S1_4X_CACHE_ROOT")
    if cache_root_value is None:
        raise WorkflowError("REQUIRED_ENVIRONMENT_MISSING:S1_4X_CACHE_ROOT")
    cache_root = _absolute_existing_directory(
        Path(cache_root_value),
        label="CACHE_ROOT",
    )
    stack_root = cache_root / f"stack-root-correctness-{profile_id}"
    if stack_root.exists() or stack_root.is_symlink():
        raise WorkflowError("CORRECTNESS_STACK_ROOT_ALREADY_EXISTS")
    stack_root.mkdir(mode=0o700)
    candidate_commit = _repo_commit(repo_root)
    evidence = _load_haskell_evidence(haskell_root)
    source_tree_sha256 = evidence.benchmark_source_tree_sha256(haskell_root)
    environment = _sealed_child_environment(ghc_bin=ghc, stack_bin=stack)
    stack_yaml = _absolute_regular(
        haskell_root / "stack.yaml",
        label="AUTHORITATIVE_STACK_YAML",
    )
    build = build_stack_command(
        ghcup=ghcup,
        stack=stack,
        stack_yaml=stack_yaml,
        stack_root=stack_root,
        ghc_version="9.10.3",
        operation=[
            "build",
            "--test",
            "--bench",
            "--no-run-tests",
            "--no-run-benchmarks",
            "--pedantic",
            f"--ghc-options={' '.join(options)}",
        ],
    )
    test = build_stack_command(
        ghcup=ghcup,
        stack=stack,
        stack_yaml=stack_yaml,
        stack_root=stack_root,
        ghc_version="9.10.3",
        operation=["test", "--pedantic", f"--ghc-options={' '.join(options)}"],
    )
    records = [
        _run_logged(
            build,
            cwd=haskell_root,
            environment=environment,
            phase="build",
            output_directory=output,
        ),
        _run_logged(
            test,
            cwd=haskell_root,
            environment=environment,
            phase="test",
            output_directory=output,
        ),
    ]
    candidate_binary = _find_candidate_binary(haskell_root, ghc_version="9.10.3")
    fixture_root = _absolute_existing_directory(
        numeric_root / "contract/fixtures",
        label="FIXTURE_ROOT",
    )
    compare_script = _absolute_regular(
        numeric_root / "oracle/compare_results.py",
        label="COMPARE_RESULTS",
    )
    python_bin = Path("/usr/bin/python3").resolve(strict=True)
    _absolute_regular(python_bin, label="PYTHON", executable=True)
    requests = (
        (
            "canonical",
            fixture_root / "small/canonical-inputs.v1.json",
            fixture_root / "expected/canonical-results.v1.json",
        ),
        (
            "semantic",
            fixture_root / "invalid/semantic-errors.v1.json",
            fixture_root / "invalid/semantic-errors.expected.v1.json",
        ),
    )
    comparison_artifacts: list[dict[str, Any]] = []
    for label, request, expected in requests:
        _absolute_regular(request, label=f"{label.upper()}_REQUEST")
        _absolute_regular(expected, label=f"{label.upper()}_EXPECTED")
        actual = output / f"{label}.actual.json"
        comparison = output / f"{label}.comparison.json"
        process_phase = f"{label}-process"
        compare_phase = f"{label}-compare"
        records.append(
            _run_logged(
                [
                    str(candidate_binary),
                    "--request",
                    str(request),
                    "--fixture-root",
                    str(fixture_root),
                    "--output",
                    str(actual),
                ],
                cwd=haskell_root,
                environment=environment,
                phase=process_phase,
                output_directory=output,
            )
        )
        _absolute_regular(actual, label=f"{label.upper()}_ACTUAL")
        records.append(
            _run_logged(
                [
                    str(python_bin),
                    str(compare_script),
                    "--expected",
                    str(expected),
                    "--actual",
                    str(actual),
                    "--request",
                    str(request),
                    "--output",
                    str(comparison),
                ],
                cwd=repo_root,
                environment=environment,
                phase=compare_phase,
                output_directory=output,
            )
        )
        _comparison_status(comparison)
        comparison_artifacts.append(
            {
                "matrixId": label,
                "requestSha256": sha256_file(request),
                "expectedSha256": sha256_file(expected),
                "actualSha256": sha256_file(actual),
                "comparisonSha256": sha256_file(comparison),
                "mismatchCount": 0,
                "status": "PASS",
            }
        )
    if tuple(record["phase"] for record in records) != CORRECTNESS_PHASES:
        raise WorkflowError("CORRECTNESS_PHASE_SEQUENCE_DRIFT")
    if evidence.benchmark_source_tree_sha256(haskell_root) != source_tree_sha256:
        raise WorkflowError("SOURCE_TREE_CHANGED_DURING_CORRECTNESS")
    if _repo_commit(repo_root) != candidate_commit:
        raise WorkflowError("CANDIDATE_COMMIT_CHANGED_DURING_CORRECTNESS")
    receipt = {
        "schemaVersion": CORRECTNESS_SCHEMA_VERSION,
        "status": "PASS",
        "profileId": profile_id,
        "ghcOptions": list(options),
        "optionsSha256": canonical_sha256(list(options)),
        "compilerVersion": "9.10.3",
        "compilerSha256": sha256_file(ghc),
        "candidateSourceCommit": candidate_commit,
        "sourceTreeSha256": source_tree_sha256,
        "candidateBinarySha256": sha256_file(candidate_binary),
        "stackYamlSha256": sha256_file(stack_yaml),
        "commands": records,
        "comparisonArtifacts": comparison_artifacts,
        "mismatchCount": 0,
    }
    receipt_path = output / "correctness-receipt.v1.json"
    atomic_write_json_exclusive(receipt_path, receipt)
    print(
        json.dumps(
            {
                "profileId": profile_id,
                "receiptPath": str(receipt_path),
                "receiptSha256": sha256_file(receipt_path),
                "status": "PASS",
            },
            sort_keys=True,
        )
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    correctness = commands.add_parser("correctness")
    correctness.add_argument("--profile", required=True)
    correctness.add_argument("--output-dir", type=Path, required=True)
    correctness.set_defaults(handler=_correctness)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        arguments.handler(arguments)
    except (
        WorkflowError,
        OSError,
        UnicodeError,
        ValueError,
        subprocess.SubprocessError,
    ) as exc:
        print(f"HASKELL_PROFILE_WORKFLOW_FAIL:{exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
