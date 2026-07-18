#!/usr/bin/env python3
"""Scalafix custom rule compiler와 clean semantic command 분리를 회귀 검증한다."""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path


SCALA_ROOT = Path(__file__).resolve().parents[1]
TOOLS_ROOT = SCALA_ROOT / "tools"
RULE_SOURCE = TOOLS_ROOT / "scalafix" / "S1_4XForbiddenSymbols.scala"


def load_runner():
    path = TOOLS_ROOT / "run_scalafix.py"
    specification = importlib.util.spec_from_file_location("run_scalafix", path)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    sys.modules["run_scalafix"] = module
    specification.loader.exec_module(module)
    return module


def selected_rules(command: list[str]) -> list[str]:
    start = command.index("--rules") + 1
    end = command.index("--files")
    return command[start:end]


def main() -> int:
    source = RULE_SOURCE.read_text(encoding="utf-8")
    forbidden_scala3_syntax = {
        "then-keyword": r"\b(?:if|while)\b[^\n]*\bthen\b",
        "given-definition": r"(?m)^\s*given\b",
        "using-clause": r"\busing\s*(?:\(|[A-Za-z_])",
        "significant-indentation-declaration": (
            r"(?m)^\s*(?:object|class|trait|enum)\s+[A-Za-z_][^\n{]*:\s*$"
        ),
    }
    for label, pattern in forbidden_scala3_syntax.items():
        assert re.search(pattern, source) is None, (
            f"Scalafix source rule must compile as Scala 2.13: {label}"
        )

    runner = load_runner()
    sources = [SCALA_ROOT / "project.scala", SCALA_ROOT / "selected-profile.scala"]
    test_dependencies = runner.project_test_dependencies(
        SCALA_ROOT / "project.scala"
    )
    assert test_dependencies == [
        "org.scalameta::munit:1.3.0",
        "org.scalameta::munit-scalacheck:1.3.0",
        "org.scalacheck::scalacheck:1.19.0",
    ]
    compile_command = runner.clean_semanticdb_compile_command(
        scala_cli=Path("/tool/scala-cli"),
        scala_root=SCALA_ROOT,
        semanticdb_root=Path("/evidence/semanticdb"),
        sources=sources,
        test_dependencies=test_dependencies,
    )
    assert "--test" in compile_command
    assert compile_command.count("--dependency") == len(test_dependencies)
    for dependency in test_dependencies:
        offset = compile_command.index(dependency)
        assert compile_command[offset - 1 : offset + 1] == [
            "--dependency",
            dependency,
        ]
    assert all(str(source_path) in compile_command for source_path in sources)

    commands = runner.clean_semantic_commands(
        scalafix=Path("/tool/scalafix"),
        scala_root=SCALA_ROOT,
        scalafix_config=SCALA_ROOT / ".scalafix.conf",
        rule_source=RULE_SOURCE,
        classpath="/classpath/one:/classpath/two",
        semanticdb_root=Path("/evidence/semanticdb"),
        sources=sources,
    )
    assert set(commands) == {"explicit-result-types", "custom-rule"}
    explicit = commands["explicit-result-types"]
    custom = commands["custom-rule"]
    assert explicit != custom
    assert selected_rules(explicit) == ["ExplicitResultTypes"]
    assert selected_rules(custom) == [f"file:{RULE_SOURCE}"]
    assert all(str(source_path) in explicit for source_path in sources)
    assert all(str(source_path) in custom for source_path in sources)

    runner_source = (TOOLS_ROOT / "run_scalafix.py").read_text(encoding="utf-8")
    for required_receipt_field in (
        '"explicitResultTypesCommandArgvSha256"',
        '"customRuleCommandArgvSha256"',
        '"cleanExplicitResultTypes"',
        '"cleanCustomSemanticRule"',
    ):
        assert required_receipt_field in runner_source

    print(
        "SCALA_SCALAFIX_RUNNER_CONTRACT_TEST_PASS "
        "cleanSemanticCommands=2 testDependencies=3"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
