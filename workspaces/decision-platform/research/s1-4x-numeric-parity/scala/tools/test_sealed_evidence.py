#!/usr/bin/env python3
"""Selector evidence가 path 재개방 없이 같은 sealed bytes를 소비하는지 검증한다."""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from pathlib import Path


SCALA_ROOT = Path(__file__).resolve().parents[1]
TOOLS_ROOT = SCALA_ROOT / "tools"


def load_module():
    path = TOOLS_ROOT / "t3_evidence.py"
    specification = importlib.util.spec_from_file_location("t3_evidence", path)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    sys.modules["t3_evidence"] = module
    specification.loader.exec_module(module)
    return module


def expect_error(module, operation, message: str) -> None:
    try:
        operation()
    except module.T3EvidenceError:
        pass
    else:
        raise AssertionError(message)


def main() -> int:
    module = load_module()
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory).resolve()
        evidence = root / "evidence.json"
        evidence.write_text('{"status":"PASS","value":1}\n', encoding="utf-8")

        snapshot = module.SealedEvidenceSnapshot()
        sealed = snapshot.capture(evidence, root=root, label="unit")
        assert sealed.json_value() == {"status": "PASS", "value": 1}
        assert sealed.sha256 == module.sha256_bytes(sealed.payload)

        # 첫 capture 뒤 같은 pathname을 다른 inode/bytes로 바꿔도 기존 snapshot은
        # 재개방하지 않으며, 최종 path-identity 검사가 substitution을 거부해야 한다.
        original = root / "original.json"
        evidence.rename(original)
        evidence.write_text('{"status":"PASS","value":999}\n', encoding="utf-8")
        assert snapshot.capture(evidence, root=root, label="unit") is sealed
        assert sealed.json_value()["value"] == 1
        expect_error(
            module,
            snapshot.verify_unchanged,
            "path substitution after sealed capture passed",
        )

        # 원래 inode를 pathname에 되돌리는 ABA는 snapshot bytes와 pathname identity가
        # 다시 정확히 같을 때만 허용된다. 중간 B bytes는 selector 입력이 될 수 없다.
        evidence.unlink()
        original.rename(evidence)
        snapshot.verify_unchanged()
        assert snapshot.capture(evidence, root=root, label="unit").json_value()[
            "value"
        ] == 1

        link = root / "link.json"
        link.symlink_to(evidence.name)
        expect_error(
            module,
            lambda: module.SealedEvidenceSnapshot().capture(
                link,
                root=root,
                label="symlink",
            ),
            "symlink evidence passed",
        )

        nested = root / "nested"
        nested.mkdir()
        nested_evidence = nested / "value.json"
        nested_evidence.write_text('{"status":"PASS"}\n', encoding="utf-8")
        nested_link = root / "nested-link"
        nested_link.symlink_to(nested, target_is_directory=True)
        expect_error(
            module,
            lambda: module.SealedEvidenceSnapshot().capture(
                nested_link / "value.json",
                root=root,
                label="parent-symlink",
            ),
            "symlink parent evidence passed",
        )

        hardlink = root / "hardlink.json"
        os.link(evidence, hardlink)
        expect_error(
            module,
            lambda: module.SealedEvidenceSnapshot().capture(
                hardlink,
                root=root,
                label="hardlink",
            ),
            "multiply-linked evidence passed",
        )

    print(
        "SCALA_SEALED_EVIDENCE_TEST_PASS "
        "sameBytes=PASS substitution=REJECT symlink=REJECT hardlink=REJECT"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
