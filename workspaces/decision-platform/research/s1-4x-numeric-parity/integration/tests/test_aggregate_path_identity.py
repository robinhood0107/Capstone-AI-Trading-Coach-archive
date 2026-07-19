"""Aggregate output root의 fresh-create와 pinned route 경계를 검증한다."""

from __future__ import annotations

import subprocess
from pathlib import Path

INTEGRATION = Path(__file__).resolve().parents[1]
HELPER = INTEGRATION / "tools/path-identity.sh"


def _run_bash(source: str, *arguments: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/usr/bin/bash", "-c", source, "aggregate-path-test", *map(str, arguments)],
        check=False,
        capture_output=True,
        text=True,
    )


def test_fresh_directory_pin_rejects_preexisting_symlink(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    route = tmp_path / "result"
    route.symlink_to(target, target_is_directory=True)

    completed = _run_bash(
        'set -euo pipefail; source "$1"; '
        's1_4x_pin_fresh_directory "$2" RESULT_FD',
        HELPER,
        route,
    )

    assert completed.returncode != 0
    assert "fresh output directory" in completed.stderr
    assert list(target.iterdir()) == []


def test_pinned_directory_detects_route_substitution(tmp_path: Path) -> None:
    route = tmp_path / "result"
    replacement = tmp_path / "replacement"
    replacement.mkdir()

    completed = _run_bash(
        'set -euo pipefail; source "$1"; '
        's1_4x_pin_fresh_directory "$2" RESULT_FD; '
        'rmdir -- "$2"; ln -s -- "$3" "$2"; '
        's1_4x_assert_pinned_directory "$2" "$RESULT_FD"',
        HELPER,
        route,
        replacement,
    )

    assert completed.returncode != 0
    assert "pinned directory route changed" in completed.stderr
    assert route.is_symlink()


def test_mutable_route_write_can_leave_the_pinned_directory(tmp_path: Path) -> None:
    route = tmp_path / "result"
    pinned_original = tmp_path / "pinned-original"

    completed = _run_bash(
        'set -euo pipefail; source "$1"; '
        's1_4x_pin_fresh_directory "$2" RESULT_FD; '
        'mv -- "$2" "$3"; mkdir -- "$2"; '
        'printf route >"$2/by-route"; '
        'printf pinned >"/proc/self/fd/$RESULT_FD/by-fd"',
        HELPER,
        route,
        pinned_original,
    )

    assert completed.returncode == 0, completed.stderr
    assert (route / "by-route").read_text(encoding="utf-8") == "route"
    assert not (route / "by-fd").exists()
    assert (pinned_original / "by-fd").read_text(encoding="utf-8") == "pinned"
    assert not (pinned_original / "by-route").exists()


def test_guarded_command_checks_route_before_opening_stdout(
    tmp_path: Path,
) -> None:
    route = tmp_path / "result"
    pinned_original = tmp_path / "pinned-original"

    completed = _run_bash(
        'set -euo pipefail; source "$1"; '
        's1_4x_pin_fresh_directory "$2" RESULT_FD PARENT_FD; '
        'mv -- "$2" "$3"; mkdir -- "$2"; '
        's1_4x_guarded_command "$2" "$RESULT_FD" '
        '--close-fd "$RESULT_FD" --close-fd "$PARENT_FD" '
        '--stdout-path "$2/redirected" -- /usr/bin/printf unsafe',
        HELPER,
        route,
        pinned_original,
    )

    assert completed.returncode != 0
    assert "pinned directory route changed" in completed.stderr
    assert not (route / "redirected").exists()
    assert not (pinned_original / "redirected").exists()


def test_guarded_command_closes_authority_fds_only_in_child(
    tmp_path: Path,
) -> None:
    route = tmp_path / "result"

    completed = _run_bash(
        'set -euo pipefail; source "$1"; '
        's1_4x_pin_fresh_directory "$2" RESULT_FD PARENT_FD; '
        's1_4x_guarded_command "$2" "$RESULT_FD" '
        '--close-fd "$RESULT_FD" --close-fd "$PARENT_FD" -- '
        '/usr/bin/bash -c '
        "'[[ ! -e \"/proc/self/fd/$1\" && ! -e \"/proc/self/fd/$2\" ]]' "
        'guarded-child "$RESULT_FD" "$PARENT_FD"; '
        '[[ -d "/proc/self/fd/$RESULT_FD" '
        '&& -d "/proc/self/fd/$PARENT_FD" ]]',
        HELPER,
        route,
    )

    assert completed.returncode == 0, completed.stderr


def test_guarded_command_detects_post_command_substitution(
    tmp_path: Path,
) -> None:
    route = tmp_path / "result"

    completed = _run_bash(
        'set -euo pipefail; source "$1"; '
        's1_4x_pin_fresh_directory "$2" RESULT_FD PARENT_FD; '
        's1_4x_guarded_command "$2" "$RESULT_FD" '
        '--close-fd "$RESULT_FD" --close-fd "$PARENT_FD" -- '
        '/usr/bin/bash -c '
        "'mv -- \"$1\" \"$1-original\"; mkdir -- \"$1\"' "
        'guarded-child "$2"',
        HELPER,
        route,
    )

    assert completed.returncode != 0
    assert "pinned directory route changed" in completed.stderr
    assert route.is_dir()
    assert (tmp_path / "result-original").is_dir()


def test_fresh_directory_pin_rejects_symlinked_parent(tmp_path: Path) -> None:
    real_parent = tmp_path / "real-parent"
    real_parent.mkdir()
    linked_parent = tmp_path / "linked-parent"
    linked_parent.symlink_to(real_parent, target_is_directory=True)

    completed = _run_bash(
        'set -euo pipefail; source "$1"; '
        's1_4x_pin_fresh_directory "$2" RESULT_FD',
        HELPER,
        linked_parent / "result",
    )

    assert completed.returncode != 0
    assert "canonical output parent" in completed.stderr
    assert not (real_parent / "result").exists()


def test_aggregate_reasserts_pinned_roots_before_publication() -> None:
    source = (
        INTEGRATION / "tools/run-native-oci-regression-gates.sh"
    ).read_text(encoding="utf-8")

    assert 'source "$INTEGRATION/tools/path-identity.sh"' in source
    assert (
        's1_4x_pin_fresh_directory \\\n'
        '  "$RESULT_ROOT" \\\n'
        '  RESULT_ROOT_OWNER_FD'
        in source
    )
    assert source.count(
        's1_4x_assert_pinned_directory "$RESULT_ROOT" "$RESULT_ROOT_OWNER_FD"'
    ) >= 3
    assert (
        's1_4x_assert_fresh_child_absent \\\n'
        '  "$RESULT_PARENT_OWNER_FD" "$FINAL_AUDIT_BASENAME"'
    ) in source
    assert "run_result_command()" in source
    assert "s1_4x_guarded_command" in source
    assert "--close-fd \"$RESULT_ROOT_OWNER_FD\"" in source
    assert "--close-fd \"$RESULT_PARENT_OWNER_FD\"" in source
    assert source.count("run_result_command ") >= 35
    assert (
        'VECTOR_SOURCE_ARCHIVE_PINNED="/proc/self/fd/'
        '$VECTOR_SOURCE_ARCHIVE_OWNER_FD"'
    ) in source
    assert "exec {VECTOR_SOURCE_ARCHIVE_OWNER_FD}<&-" in source
    assert 'mkdir -p \\\n  "$RESULT_ROOT/scala"' not in source
