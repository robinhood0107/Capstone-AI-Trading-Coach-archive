"""Native OCI aggregate의 sealed continuation 분기 계약을 고정한다."""

from pathlib import Path

AGGREGATE = (
    Path(__file__).resolve().parents[1] / "tools/run-native-oci-regression-gates.sh"
)


def test_sealed_continuation_imports_only_then_resumes_common_tail() -> None:
    source = AGGREGATE.read_text(encoding="utf-8")

    assert "--sealed-continuation-manifest ABSOLUTE_MANIFEST" in source
    assert '[[ "$SEALED_CONTINUATION_MANIFEST" == /* \\' in source
    assert '&& -f "$SEALED_CONTINUATION_MANIFEST" \\' in source
    assert '&& ! -L "$SEALED_CONTINUATION_MANIFEST" ]]' in source
    assert 'python "$INTEGRATION/continuation_prefix.py" import \\' in source
    assert '--repo-root "$ROOT" \\' in source
    assert '--manifest "$SEALED_CONTINUATION_MANIFEST" \\' in source
    assert '--output-root "$RESULT_ROOT"' in source
    assert 'python "$ORACLE/validate_contract.py" --check-all' in source
    assert '"$RESULT_ROOT/contract-validation.json"' in source
    assert '"$RESULT_ROOT/large-fixture-receipt.json"' in source

    continuation = source.split(
        "  # Import는 sealed ancestor closure만 복원한다.", maxsplit=1
    )[1].split(
        'fi\nrun_result_command "$HASKELL/tools/run-ghc-9.14.1-compatibility.sh"', 1
    )[0]
    for prohibited in (
        "materialize_large_fixtures.py",
        "run-hard-compiler-profile.sh",
        "run-correctness-profile.sh",
        "run-profile-qualification.sh",
        "run-scalafmt-idempotence.sh",
        "run-scalafix.sh",
        "check-source-policy.sh",
        "audit-scala-dependency-edges.sh",
        "check-format.sh",
        "check-hlint.sh",
        "haskell_evidence.py",
    ):
        assert prohibited not in continuation
    assert continuation.count('"$SCALA/tools/select-proven-profile.sh"') == 2
    assert "SCALA_QUALIFICATION_SOURCE_ROOT" in continuation
    assert 'select(.sourceId == "scala-qualification")' in continuation
    assert 'select(.sourceId == "scala-jmh-smoke")' in continuation
    assert (
        '"$SCALA_QUALIFICATION_SOURCE_ROOT/qualification/'
        'scala-profile-qualification.v1.json"'
    ) in continuation
    assert (
        '"$SCALA_QUALIFICATION_SOURCE_ROOT/jmh-smoke/'
        'scala-jvm-argument-allowlist.v1.json"'
    ) in continuation
    assert '"$HASKELL/tools/select-proven-profile.sh" --check' not in continuation
    assert '"$RESULT_ROOT/continuation-import.v1.json"' in continuation
    assert "selectedProfileSha256" in continuation
    ghc_tail = 'fi\nrun_result_command "$HASKELL/tools/run-ghc-9.14.1-compatibility.sh"'
    assert source.index(ghc_tail) < source.index(
        'python "$INTEGRATION/coverage_execution.py"'
    )


def test_haskell_coverage_explicitly_prewarms_pantry_once() -> None:
    source = AGGREGATE.read_text(encoding="utf-8")
    scala = source.index("--candidate scala")
    haskell = source.index("--candidate haskell")
    coverage_gate = source.index(
        'python "$INTEGRATION/coverage_gate.py"',
        haskell,
    )

    assert "--prewarm-haskell-pantry" not in source[scala:haskell]
    assert source[haskell:coverage_gate].count("--prewarm-haskell-pantry") == 1
