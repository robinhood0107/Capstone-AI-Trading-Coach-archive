#!/usr/bin/env python3
"""Scala OCI image를 Docker CLI/daemon/base/candidate receipt에 결속한다."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


class OciEvidenceError(ValueError):
    """OCI build/runtime identity가 receipt와 다르거나 unsafe함."""


SHA256 = re.compile(r"^[0-9a-f]{64}$")
IMAGE_ID = re.compile(r"^sha256:[0-9a-f]{64}$")
BASE_REFERENCE = re.compile(r"^[^@\s]+@sha256:[0-9a-f]{64}$")
CANDIDATE_LABEL = "org.opencontainers.image.s1-4x.candidate-sha256"
BASE_REFERENCE_LABEL = "org.opencontainers.image.s1-4x.base-reference"
BASE_IMAGE_ID_LABEL = "org.opencontainers.image.s1-4x.base-image-id"
CONTAINERFILE_LABEL = "org.opencontainers.image.s1-4x.containerfile-sha256"
FIXTURE_TREE_LABEL = "org.opencontainers.image.s1-4x.fixture-tree-sha256"
RECEIPT_FIELDS = {
    "schemaVersion",
    "baseImageReference",
    "baseImageReferenceSource",
    "baseImageId",
    "candidateSha256",
    "containerfileSha256",
    "fixtureTreeSha256",
    "imageId",
    "localTag",
    "dockerIdentity",
    "inspectedLabels",
    "buildNetwork",
    "pull",
    "buildUsedIidfile",
    "aggregateStatus",
}
DOCKER_IDENTITY_FIELDS = {
    "dockerCliPathId",
    "dockerCliSha256",
    "contextName",
    "daemonId",
    "serverVersion",
    "operatingSystem",
    "architecture",
}


def sha256_file(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise OciEvidenceError(f"UNSAFE_OR_MISSING_FILE:{path}")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


def strict_json_bytes(payload: bytes, *, label: str) -> Any:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise OciEvidenceError(f"DUPLICATE_JSON_KEY:{label}:{key}")
            result[key] = value
        return result

    try:
        return json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=unique,
            parse_constant=lambda token: (_ for _ in ()).throw(
                OciEvidenceError(f"NONFINITE_JSON:{label}:{token}")
            ),
        )
    except (UnicodeError, json.JSONDecodeError) as error:
        raise OciEvidenceError(f"INVALID_JSON:{label}") from error


def single_regular_bytes(path: Path, *, label: str) -> bytes:
    """절대 lexical path를 openat/O_NOFOLLOW로 열어 한 inode의 bytes만 읽는다."""

    if (
        not path.is_absolute()
        or len(path.parts) < 2
        or any(part in {"", ".", ".."} for part in path.parts[1:])
    ):
        raise OciEvidenceError(f"{label}_PATH_INVALID")
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    file_flags = os.O_RDONLY | os.O_NOFOLLOW
    descriptors: list[int] = []
    file_descriptor = -1
    try:
        current = os.open("/", directory_flags)
        descriptors.append(current)
        for component in path.parts[1:-1]:
            current = os.open(component, directory_flags, dir_fd=current)
            descriptors.append(current)
        file_descriptor = os.open(
            path.parts[-1],
            file_flags,
            dir_fd=current,
        )
        before = os.fstat(file_descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_size < 0
        ):
            raise OciEvidenceError(f"{label}_NOT_SINGLE_REGULAR_FILE")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(file_descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(file_descriptor)
        identity_before = (
            before.st_dev,
            before.st_ino,
            before.st_mode,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        )
        identity_after = (
            after.st_dev,
            after.st_ino,
            after.st_mode,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        )
        payload = b"".join(chunks)
        if identity_before != identity_after or len(payload) != before.st_size:
            raise OciEvidenceError(f"{label}_CHANGED_DURING_READ")
        return payload
    except OSError as error:
        raise OciEvidenceError(f"{label}_OPEN_FAILED") from error
    finally:
        if file_descriptor >= 0:
            os.close(file_descriptor)
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def strict_json_file(path: Path) -> Any:
    return strict_json_bytes(
        single_regular_bytes(path, label="OCI_RECEIPT"),
        label=str(path),
    )


def _run(
    command: list[str],
    *,
    label: str,
    timeout: int = 60,
) -> str:
    completed = subprocess.run(
        command,
        check=False,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if completed.returncode != 0:
        raise OciEvidenceError(f"{label}_FAILED:{completed.returncode}")
    return completed.stdout.strip()


def docker_identity(docker: Path, expected_sha256: str) -> dict[str, str]:
    """Absolute Docker CLI와 현재 context/daemon/server identity를 한 번에 읽는다."""

    if (
        not docker.is_absolute()
        or docker.is_symlink()
        or not docker.is_file()
        or not os.access(docker, os.X_OK)
        or SHA256.fullmatch(expected_sha256) is None
        or hashlib.sha256(
            single_regular_bytes(docker, label="DOCKER_CLI")
        ).hexdigest()
        != expected_sha256
    ):
        raise OciEvidenceError("DOCKER_CLI_IDENTITY_MISMATCH")
    context = _run([str(docker), "context", "show"], label="DOCKER_CONTEXT")
    info = _run(
        [
            str(docker),
            "info",
            "--format",
            "{{json .}}",
        ],
        label="DOCKER_INFO",
    )
    value = strict_json_bytes(info.encode("utf-8"), label="docker-info")
    if not isinstance(value, dict):
        raise OciEvidenceError("DOCKER_INFO_OBJECT_REQUIRED")
    identity = {
        "dockerCliPathId": "DOCKER_CLI",
        "dockerCliSha256": expected_sha256,
        "contextName": context,
        "daemonId": str(value.get("ID", "")),
        "serverVersion": str(value.get("ServerVersion", "")),
        "operatingSystem": str(value.get("OperatingSystem", "")),
        "architecture": str(value.get("Architecture", "")),
    }
    if any(not item for item in identity.values()):
        raise OciEvidenceError("DOCKER_IDENTITY_FIELD_MISSING")
    return identity


def inspect_image(
    docker: Path,
    reference: str,
) -> dict[str, Any]:
    value = strict_json_bytes(
        _run(
            [str(docker), "image", "inspect", reference],
            label="DOCKER_IMAGE_INSPECT",
        ).encode("utf-8"),
        label="docker-image-inspect",
    )
    if (
        not isinstance(value, list)
        or len(value) != 1
        or not isinstance(value[0], dict)
        or IMAGE_ID.fullmatch(str(value[0].get("Id"))) is None
    ):
        raise OciEvidenceError("DOCKER_IMAGE_INSPECT_INVALID")
    return value[0]


def selected_labels(image: dict[str, Any]) -> dict[str, str]:
    config = image.get("Config")
    labels = config.get("Labels") if isinstance(config, dict) else None
    if not isinstance(labels, dict):
        raise OciEvidenceError("OCI_IMAGE_LABELS_MISSING")
    result = {
        key: labels.get(key)
        for key in (
            CANDIDATE_LABEL,
            BASE_REFERENCE_LABEL,
            BASE_IMAGE_ID_LABEL,
            CONTAINERFILE_LABEL,
            FIXTURE_TREE_LABEL,
        )
    }
    if any(not isinstance(value, str) or not value for value in result.values()):
        raise OciEvidenceError("OCI_IMAGE_IDENTITY_LABEL_MISSING")
    return {key: str(value) for key, value in result.items()}


def fixture_tree_sha256(root: Path) -> str:
    if (
        not root.is_absolute()
        or root.is_symlink()
        or not root.is_dir()
        or root.resolve(strict=True) != root
    ):
        raise OciEvidenceError("FIXTURE_ROOT_INVALID")
    entries = []
    for path in sorted(root.rglob("*"), key=lambda value: str(value).encode()):
        if path.is_symlink():
            raise OciEvidenceError("FIXTURE_TREE_SYMLINK_FORBIDDEN")
        if path.is_file():
            entries.append(
                {
                    "path": path.relative_to(root).as_posix(),
                    "sha256": sha256_file(path),
                }
            )
    if not entries:
        raise OciEvidenceError("FIXTURE_TREE_EMPTY")
    return canonical_sha256(entries)


def build_receipt(
    *,
    base_image_reference: str,
    base_image_id: str,
    candidate_sha256: str,
    containerfile_sha256: str,
    fixture_tree_sha256: str,
    image_id: str,
    local_tag: str,
    docker_identity: dict[str, str],
    inspected_labels: dict[str, str],
) -> dict[str, Any]:
    receipt = {
        "schemaVersion": "s1.4x-scala-oci-build-result-v2",
        "baseImageReference": base_image_reference,
        "baseImageReferenceSource": "caller-digest-argument",
        "baseImageId": base_image_id,
        "candidateSha256": candidate_sha256,
        "containerfileSha256": containerfile_sha256,
        "fixtureTreeSha256": fixture_tree_sha256,
        "imageId": image_id,
        "localTag": local_tag,
        "dockerIdentity": docker_identity,
        "inspectedLabels": inspected_labels,
        "buildNetwork": "none",
        "pull": False,
        "buildUsedIidfile": True,
        "aggregateStatus": "PASS",
    }
    validate_build_receipt(
        receipt,
        expected_docker_identity=docker_identity,
        inspected_image_id=image_id,
        inspected_labels=inspected_labels,
    )
    return receipt


def validate_build_receipt(
    receipt: dict[str, Any],
    *,
    expected_docker_identity: dict[str, str],
    inspected_image_id: str,
    inspected_labels: dict[str, str],
) -> None:
    if (
        set(receipt) != RECEIPT_FIELDS
        or receipt.get("schemaVersion")
        != "s1.4x-scala-oci-build-result-v2"
        or receipt.get("baseImageReferenceSource")
        != "caller-digest-argument"
        or BASE_REFERENCE.fullmatch(str(receipt.get("baseImageReference")))
        is None
        or IMAGE_ID.fullmatch(str(receipt.get("baseImageId"))) is None
        or IMAGE_ID.fullmatch(str(receipt.get("imageId"))) is None
        or receipt.get("imageId") != inspected_image_id
        or any(
            SHA256.fullmatch(str(receipt.get(field))) is None
            for field in (
                "candidateSha256",
                "containerfileSha256",
                "fixtureTreeSha256",
            )
        )
        or not isinstance(receipt.get("localTag"), str)
        or not receipt["localTag"]
        or receipt.get("dockerIdentity") != expected_docker_identity
        or set(expected_docker_identity) != DOCKER_IDENTITY_FIELDS
        or receipt.get("inspectedLabels") != inspected_labels
        or receipt.get("buildNetwork") != "none"
        or receipt.get("pull") is not False
        or receipt.get("buildUsedIidfile") is not True
        or receipt.get("aggregateStatus") != "PASS"
    ):
        raise OciEvidenceError("OCI_BUILD_RECEIPT_INVALID")
    expected_labels = {
        CANDIDATE_LABEL: receipt["candidateSha256"],
        BASE_REFERENCE_LABEL: receipt["baseImageReference"],
        BASE_IMAGE_ID_LABEL: receipt["baseImageId"],
        CONTAINERFILE_LABEL: receipt["containerfileSha256"],
        FIXTURE_TREE_LABEL: receipt["fixtureTreeSha256"],
    }
    if inspected_labels != expected_labels:
        raise OciEvidenceError("OCI_IMAGE_LABEL_RECEIPT_MISMATCH")


def load_build_receipt(
    path: Path,
    *,
    expected_docker_identity: dict[str, str],
    inspected_image_id: str,
    inspected_labels: dict[str, str],
) -> dict[str, Any]:
    value = strict_json_file(path)
    if not isinstance(value, dict):
        raise OciEvidenceError("OCI_BUILD_RECEIPT_OBJECT_REQUIRED")
    validate_build_receipt(
        value,
        expected_docker_identity=expected_docker_identity,
        inspected_image_id=inspected_image_id,
        inspected_labels=inspected_labels,
    )
    return value


def _write_exclusive(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(
            value,
            stream,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        stream.write("\n")


def execute_build(arguments: argparse.Namespace) -> dict[str, Any]:
    docker = arguments.docker
    identity_before = docker_identity(docker, arguments.docker_sha256)
    if BASE_REFERENCE.fullmatch(arguments.base_image) is None:
        raise OciEvidenceError("BASE_IMAGE_DIGEST_REFERENCE_REQUIRED")
    base = inspect_image(docker, arguments.base_image)
    repo_digests = base.get("RepoDigests")
    if (
        not isinstance(repo_digests, list)
        or arguments.base_image not in repo_digests
    ):
        raise OciEvidenceError("BASE_IMAGE_SOURCE_DIGEST_NOT_LOCAL")
    base_id = str(base["Id"])
    candidate_bytes = single_regular_bytes(
        arguments.candidate,
        label="OCI_CANDIDATE",
    )
    containerfile_bytes = single_regular_bytes(
        arguments.containerfile,
        label="OCI_CONTAINERFILE",
    )
    candidate_sha256 = hashlib.sha256(candidate_bytes).hexdigest()
    containerfile_sha256 = hashlib.sha256(containerfile_bytes).hexdigest()
    fixtures_sha256 = fixture_tree_sha256(arguments.fixture_root)
    if arguments.output.exists() or arguments.output.is_symlink():
        raise OciEvidenceError("OCI_BUILD_OUTPUT_ALREADY_EXISTS")
    scratch = arguments.cache_root / "scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    context = Path(
        tempfile.mkdtemp(prefix="s1-4x-scala-oci-build.", dir=scratch)
    )
    try:
        fixture_bundle = context / "fixture-bundle"
        shutil.copytree(arguments.fixture_root, fixture_bundle)
        if fixture_tree_sha256(fixture_bundle) != fixtures_sha256:
            raise OciEvidenceError("FIXTURE_TREE_CHANGED_DURING_COPY")
        (context / "candidate.jar").write_bytes(candidate_bytes)
        (context / "Containerfile").write_bytes(containerfile_bytes)
        iidfile = context / "image.id"
        command = [
            str(docker),
            "build",
            "--network=none",
            "--pull=false",
            "--iidfile",
            str(iidfile),
            "--build-arg",
            f"S1_4X_SCALA_BASE_IMAGE={arguments.base_image}",
            "--build-arg",
            f"S1_4X_BASE_IMAGE_ID={base_id}",
            "--build-arg",
            f"S1_4X_CANDIDATE_SHA256={candidate_sha256}",
            "--build-arg",
            f"S1_4X_CONTAINERFILE_SHA256={containerfile_sha256}",
            "--build-arg",
            f"S1_4X_FIXTURE_TREE_SHA256={fixtures_sha256}",
            "--tag",
            arguments.image_tag,
            "--file",
            str(context / "Containerfile"),
            str(context),
        ]
        completed = subprocess.run(command, check=False)
        if completed.returncode != 0:
            raise OciEvidenceError(
                f"DOCKER_BUILD_FAILED:{completed.returncode}"
            )
        try:
            image_id = single_regular_bytes(
                iidfile,
                label="DOCKER_IIDFILE",
            ).decode("utf-8").strip()
        except UnicodeError as error:
            raise OciEvidenceError("DOCKER_IIDFILE_ID_INVALID") from error
        if IMAGE_ID.fullmatch(image_id) is None:
            raise OciEvidenceError("DOCKER_IIDFILE_ID_INVALID")
        built = inspect_image(docker, image_id)
        labels = selected_labels(built)
        identity_after = docker_identity(docker, arguments.docker_sha256)
        if identity_after != identity_before:
            raise OciEvidenceError("DOCKER_DAEMON_CHANGED_DURING_BUILD")
        receipt = build_receipt(
            base_image_reference=arguments.base_image,
            base_image_id=base_id,
            candidate_sha256=candidate_sha256,
            containerfile_sha256=containerfile_sha256,
            fixture_tree_sha256=fixtures_sha256,
            image_id=image_id,
            local_tag=arguments.image_tag,
            docker_identity=identity_before,
            inspected_labels=labels,
        )
        _write_exclusive(arguments.output, receipt)
        return receipt
    finally:
        if context.parent != scratch or not context.name.startswith(
            "s1-4x-scala-oci-build."
        ):
            raise OciEvidenceError("OCI_CONTEXT_CLEANUP_GUARD_FAILED")
        shutil.rmtree(context)


def runtime_binding(arguments: argparse.Namespace) -> dict[str, Any]:
    identity = docker_identity(arguments.docker, arguments.docker_sha256)
    receipt_bytes = single_regular_bytes(
        arguments.build_receipt,
        label="OCI_RECEIPT",
    )
    receipt_value = strict_json_bytes(
        receipt_bytes,
        label=str(arguments.build_receipt),
    )
    if not isinstance(receipt_value, dict):
        raise OciEvidenceError("OCI_BUILD_RECEIPT_OBJECT_REQUIRED")
    image_id = str(receipt_value.get("imageId", ""))
    image = inspect_image(arguments.docker, image_id)
    labels = selected_labels(image)
    validate_build_receipt(
        receipt_value,
        expected_docker_identity=identity,
        inspected_image_id=str(image["Id"]),
        inspected_labels=labels,
    )
    return {
        "schemaVersion": "s1.4x-scala-oci-runtime-binding-v1",
        "imageId": receipt_value["imageId"],
        "buildReceiptSha256": hashlib.sha256(receipt_bytes).hexdigest(),
        "candidateSha256": receipt_value["candidateSha256"],
        "baseImageReference": receipt_value["baseImageReference"],
        "baseImageId": receipt_value["baseImageId"],
        "dockerIdentity": identity,
        "status": "PASS",
    }


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    subcommands = value.add_subparsers(dest="command", required=True)
    build = subcommands.add_parser("build")
    build.add_argument("--docker", type=Path, required=True)
    build.add_argument("--docker-sha256", required=True)
    build.add_argument("--candidate", type=Path, required=True)
    build.add_argument("--base-image", required=True)
    build.add_argument("--image-tag", required=True)
    build.add_argument("--containerfile", type=Path, required=True)
    build.add_argument("--fixture-root", type=Path, required=True)
    build.add_argument("--cache-root", type=Path, required=True)
    build.add_argument("--output", type=Path, required=True)
    runtime = subcommands.add_parser("runtime-binding")
    runtime.add_argument("--docker", type=Path, required=True)
    runtime.add_argument("--docker-sha256", required=True)
    runtime.add_argument("--build-receipt", type=Path, required=True)
    return value


def main() -> int:
    arguments = parser().parse_args()
    try:
        result = (
            execute_build(arguments)
            if arguments.command == "build"
            else runtime_binding(arguments)
        )
    except (
        OciEvidenceError,
        OSError,
        subprocess.SubprocessError,
        TypeError,
        ValueError,
    ) as error:
        print(f"SCALA_OCI_EVIDENCE_FAIL:{error}", file=sys.stderr)
        return 2
    print(json.dumps(result, allow_nan=False, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
