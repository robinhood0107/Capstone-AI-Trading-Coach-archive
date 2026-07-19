#!/usr/bin/env python3
"""Scala OCI build receipt와 immutable runtime binding forgery를 검증한다."""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
import tempfile
from pathlib import Path


SCALA_ROOT = Path(__file__).resolve().parents[1]
TOOLS_ROOT = SCALA_ROOT / "tools"
SHA = "1" * 64


def load_module():
    path = TOOLS_ROOT / "oci_evidence.py"
    specification = importlib.util.spec_from_file_location("oci_evidence", path)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    sys.modules["oci_evidence"] = module
    specification.loader.exec_module(module)
    return module


def expect_error(module, operation, message: str) -> None:
    try:
        operation()
    except module.OciEvidenceError:
        pass
    else:
        raise AssertionError(message)


def main() -> int:
    module = load_module()
    base_ref = f"eclipse-temurin@sha256:{'2' * 64}"
    base_id = f"sha256:{'3' * 64}"
    image_id = f"sha256:{'4' * 64}"
    identity = {
        "dockerCliPathId": "DOCKER_CLI",
        "dockerCliSha256": "5" * 64,
        "contextName": "desktop-linux",
        "daemonId": "daemon-immutable-id",
        "serverVersion": "29.6.1",
        "operatingSystem": "Docker Desktop",
        "architecture": "x86_64",
    }
    labels = {
        module.CANDIDATE_LABEL: "6" * 64,
        module.BASE_REFERENCE_LABEL: base_ref,
        module.BASE_IMAGE_ID_LABEL: base_id,
        module.CONTAINERFILE_LABEL: "7" * 64,
        module.FIXTURE_TREE_LABEL: "8" * 64,
    }
    receipt = module.build_receipt(
        base_image_reference=base_ref,
        base_image_id=base_id,
        candidate_sha256="6" * 64,
        containerfile_sha256="7" * 64,
        fixture_tree_sha256="8" * 64,
        image_id=image_id,
        local_tag="s1-4x/scala:test",
        docker_identity=identity,
        inspected_labels=labels,
    )
    assert receipt["imageId"] == image_id
    assert receipt["buildUsedIidfile"] is True
    module.validate_build_receipt(
        receipt,
        expected_docker_identity=identity,
        inspected_image_id=image_id,
        inspected_labels=labels,
    )

    for field, forged_value in (
        ("imageId", f"sha256:{'9' * 64}"),
        ("candidateSha256", "a" * 64),
        ("baseImageReference", f"forged@sha256:{'b' * 64}"),
        ("baseImageId", f"sha256:{'c' * 64}"),
    ):
        forged = copy.deepcopy(receipt)
        forged[field] = forged_value
        expect_error(
            module,
            lambda value=forged: module.validate_build_receipt(
                value,
                expected_docker_identity=identity,
                inspected_image_id=image_id,
                inspected_labels=labels,
            ),
            f"forged build receipt field passed: {field}",
        )

    forged_identity = dict(identity)
    forged_identity["daemonId"] = "different-daemon"
    expect_error(
        module,
        lambda: module.validate_build_receipt(
            receipt,
            expected_docker_identity=forged_identity,
            inspected_image_id=image_id,
            inspected_labels=labels,
        ),
        "Docker daemon/context substitution passed",
    )
    forged_labels = dict(labels)
    forged_labels[module.CANDIDATE_LABEL] = "d" * 64
    expect_error(
        module,
        lambda: module.validate_build_receipt(
            receipt,
            expected_docker_identity=identity,
            inspected_image_id=image_id,
            inspected_labels=forged_labels,
        ),
        "candidate label substitution passed",
    )

    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "build-result.json"
        path.write_text(
            json.dumps(receipt, separators=(",", ":"), sort_keys=True) + "\n",
            encoding="utf-8",
        )
        loaded = module.load_build_receipt(
            path,
            expected_docker_identity=identity,
            inspected_image_id=image_id,
            inspected_labels=labels,
        )
        assert loaded == receipt
        link = Path(directory) / "link.json"
        link.symlink_to(path.name)
        expect_error(
            module,
            lambda: module.load_build_receipt(
                link,
                expected_docker_identity=identity,
                inspected_image_id=image_id,
                inspected_labels=labels,
            ),
            "symlink build receipt passed",
        )
        parent_link = Path(directory) / "parent-link"
        parent_link.symlink_to(Path(directory), target_is_directory=True)
        expect_error(
            module,
            lambda: module.load_build_receipt(
                parent_link / path.name,
                expected_docker_identity=identity,
                inspected_image_id=image_id,
                inspected_labels=labels,
            ),
            "symlink-parent build receipt passed",
        )

    print(
        "SCALA_OCI_EVIDENCE_TEST_PASS "
        "iidfile=BOUND candidate=BOUND base=BOUND dockerDaemon=BOUND"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
