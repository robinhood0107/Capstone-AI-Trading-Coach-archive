"""Gate 2 wrapper path와 실제 실행 바이트를 fail-closed로 결속한다."""

from __future__ import annotations

import hashlib
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ExecutableIdentityError(ValueError):
    """실행 파일 path, 권한, type, digest closure 위반을 나타낸다."""


@dataclass(frozen=True)
class InspectedExecutable:
    """Symlink-free path에서 읽어 검증한 정확한 실행 파일 바이트다."""

    path: str
    resolved_path: str
    sha256: str
    payload: bytes


def _identity_error(leaf: str, role: str) -> ExecutableIdentityError:
    return ExecutableIdentityError(f"{leaf}:{role}")


def _open_regular_without_symlinks(path: Path, *, role: str) -> int:
    """각 경로 component를 dirfd로 고정해 중간·최종 symlink를 모두 거부한다."""

    parts = path.parts
    if (
        not path.is_absolute()
        or not parts
        or parts[0] != os.sep
        or len(parts) < 2
        or any(part in {"", ".", ".."} for part in parts[1:])
    ):
        raise _identity_error("COMMAND_EXECUTABLE_MISMATCH", role)
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
    no_follow = getattr(os, "O_NOFOLLOW", None)
    if no_follow is None:
        raise _identity_error("COMMAND_EXECUTABLE_NOFOLLOW_UNSUPPORTED", role)
    try:
        directory_fd = os.open(os.sep, directory_flags | no_follow)
    except OSError as exc:
        raise _identity_error("COMMAND_EXECUTABLE_UNAVAILABLE", role) from exc
    try:
        for component in parts[1:-1]:
            try:
                before = os.stat(
                    component,
                    dir_fd=directory_fd,
                    follow_symlinks=False,
                )
            except OSError as exc:
                raise _identity_error(
                    "COMMAND_EXECUTABLE_UNAVAILABLE",
                    role,
                ) from exc
            if stat.S_ISLNK(before.st_mode):
                raise _identity_error(
                    "COMMAND_EXECUTABLE_SYMLINK_COMPONENT",
                    role,
                )
            if not stat.S_ISDIR(before.st_mode):
                raise _identity_error("COMMAND_EXECUTABLE_UNAVAILABLE", role)
            try:
                next_fd = os.open(
                    component,
                    directory_flags | no_follow,
                    dir_fd=directory_fd,
                )
            except OSError as exc:
                raise _identity_error(
                    "COMMAND_EXECUTABLE_CHANGED_DURING_VALIDATION",
                    role,
                ) from exc
            try:
                opened = os.fstat(next_fd)
                if (
                    not stat.S_ISDIR(opened.st_mode)
                    or (before.st_dev, before.st_ino)
                    != (opened.st_dev, opened.st_ino)
                ):
                    raise _identity_error(
                        "COMMAND_EXECUTABLE_CHANGED_DURING_VALIDATION",
                        role,
                    )
            except BaseException:
                os.close(next_fd)
                raise
            os.close(directory_fd)
            directory_fd = next_fd

        leaf = parts[-1]
        try:
            before = os.stat(
                leaf,
                dir_fd=directory_fd,
                follow_symlinks=False,
            )
        except OSError as exc:
            raise _identity_error("COMMAND_EXECUTABLE_UNAVAILABLE", role) from exc
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
            raise _identity_error("COMMAND_EXECUTABLE_NOT_REGULAR", role)
        try:
            descriptor = os.open(
                leaf,
                os.O_RDONLY | os.O_CLOEXEC | no_follow,
                dir_fd=directory_fd,
            )
        except OSError as exc:
            raise _identity_error(
                "COMMAND_EXECUTABLE_CHANGED_DURING_VALIDATION",
                role,
            ) from exc
        try:
            opened = os.fstat(descriptor)
            if (
                not stat.S_ISREG(opened.st_mode)
                or (before.st_dev, before.st_ino)
                != (opened.st_dev, opened.st_ino)
            ):
                raise _identity_error(
                    "COMMAND_EXECUTABLE_CHANGED_DURING_VALIDATION",
                    role,
                )
        except BaseException:
            os.close(descriptor)
            raise
        return descriptor
    except ExecutableIdentityError:
        raise
    except OSError as exc:
        raise _identity_error(
            "COMMAND_EXECUTABLE_CHANGED_DURING_VALIDATION",
            role,
        ) from exc
    finally:
        os.close(directory_fd)


def _inspect_regular_path(
    path: Path,
    *,
    role: str,
    require_executable: bool,
) -> InspectedExecutable:
    absolute = Path(os.path.abspath(path))
    descriptor = _open_regular_without_symlinks(absolute, role=role)
    try:
        if require_executable:
            access_options: dict[str, bool] = {}
            if os.access in os.supports_effective_ids:
                access_options["effective_ids"] = True
            if not os.access(
                f"/proc/self/fd/{descriptor}",
                os.X_OK,
                **access_options,
            ):
                raise _identity_error("COMMAND_EXECUTABLE_NOT_EXECUTABLE", role)
        chunks: list[bytes] = []
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            chunks.append(block)
        payload = b"".join(chunks)
    except ExecutableIdentityError:
        raise
    except OSError as exc:
        raise _identity_error(
            "COMMAND_EXECUTABLE_CHANGED_DURING_VALIDATION",
            role,
        ) from exc
    finally:
        os.close(descriptor)
    digest = hashlib.sha256(payload).hexdigest()
    return InspectedExecutable(
        path=str(absolute),
        resolved_path=str(absolute),
        sha256=digest,
        payload=payload,
    )


def inspect_regular_file_path(path: Path, *, role: str) -> InspectedExecutable:
    """Symlink-free regular file의 단일 FD snapshot을 읽어 hash와 bytes를 함께 반환한다."""

    return _inspect_regular_path(
        path,
        role=role,
        require_executable=False,
    )


def inspect_executable_path(path: Path, *, role: str) -> InspectedExecutable:
    """현재 사용자에게 실행 가능한 symlink-free regular file의 바이트를 읽는다."""

    return _inspect_regular_path(
        path,
        role=role,
        require_executable=True,
    )


def inspect_executable_identity(
    identity: Any,
    *,
    role: str,
) -> InspectedExecutable:
    """Manifest의 절대 경로와 SHA-256에 맞는 정확한 실행 바이트를 반환한다."""

    if (
        not isinstance(identity, dict)
        or set(identity) != {"path", "sha256"}
        or not isinstance(identity["path"], str)
        or not Path(identity["path"]).is_absolute()
        or SHA256.fullmatch(str(identity["sha256"])) is None
    ):
        raise _identity_error("COMMAND_EXECUTABLE_MISMATCH", role)
    inspected = inspect_executable_path(Path(identity["path"]), role=role)
    if inspected.path != identity["path"]:
        raise _identity_error("COMMAND_EXECUTABLE_MISMATCH", role)
    if inspected.sha256 != identity["sha256"]:
        raise _identity_error("COMMAND_EXECUTABLE_SHA256_MISMATCH", role)
    return inspected
