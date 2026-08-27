"""Safe, workspace-scoped filesystem tools."""

from __future__ import annotations

import hashlib
import io
import os
import stat
import tempfile
from pathlib import Path
from typing import Any, BinaryIO


DEFAULT_HASH_CHUNK_SIZE = 64 * 1024
DEFAULT_MAX_WRITE_BYTES = 2 * 1024 * 1024


class PathViolation(ValueError):
    """Raised when a requested path is invalid or escapes the workspace."""


def _error(
    code: str,
    message: str,
    *,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a stable tool-error payload."""

    return {
        "ok": False,
        "data": None,
        "error": {"code": code, "message": message},
        "meta": meta or {},
    }


def resolve_in_workspace(
    workspace: Path,
    user_path: str,
    *,
    must_exist: bool = False,
) -> Path:
    """Resolve a relative path and reject paths outside ``workspace``.

    ``Path.resolve`` follows existing symbolic links. Therefore a symlink that
    points outside the workspace is rejected by the subsequent ``relative_to``
    check. The check is path-based protection, not an OS sandbox.
    """

    if not isinstance(user_path, str) or not user_path.strip():
        raise PathViolation("path must be a non-empty string")
    if "\x00" in user_path:
        raise PathViolation("path contains a NUL byte")

    try:
        root = workspace.resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as exc:
        raise PathViolation("workspace cannot be resolved") from exc
    if not root.is_dir():
        raise PathViolation("workspace is not a directory")

    raw_path = Path(user_path)
    if raw_path.is_absolute():
        raise PathViolation("absolute paths are not allowed")

    try:
        candidate = (root / raw_path).resolve(strict=must_exist)
    except (OSError, RuntimeError, ValueError) as exc:
        raise PathViolation("path cannot be resolved") from exc

    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise PathViolation("path escapes workspace") from exc

    return candidate


def _sha256_stream(stream: BinaryIO, chunk_size: int) -> str:
    """Hash an already opened binary stream in bounded chunks."""

    digest = hashlib.sha256()
    while True:
        chunk = stream.read(chunk_size)
        if not chunk:
            break
        digest.update(chunk)
    return digest.hexdigest()


def sha256_file_streaming(
    path: Path,
    *,
    chunk_size: int = DEFAULT_HASH_CHUNK_SIZE,
) -> str:
    """Return a file SHA-256 without loading the complete file into memory."""

    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    with path.open("rb") as stream:
        return _sha256_stream(stream, chunk_size)


def read_file(
    workspace: Path,
    path: str,
    start_line: int = 1,
    max_lines: int = 200,
    max_chars: int = 20_000,
) -> dict[str, Any]:
    """Read a UTF-8 file page and return continuation metadata.

    ``next_line`` is ``None`` at EOF. If a single line exceeds ``max_chars``,
    the returned page contains the beginning of that line and marks
    ``line_truncated``; the next page starts at the following line.
    """

    if start_line < 1:
        return _error("invalid_start_line", "start_line must be at least 1")
    if not 1 <= max_lines <= 500:
        return _error("invalid_max_lines", "max_lines must be between 1 and 500")
    if not 1 <= max_chars <= 50_000:
        return _error("invalid_max_chars", "max_chars must be between 1 and 50000")

    try:
        target = resolve_in_workspace(workspace, path, must_exist=True)
    except PathViolation as exc:
        return _error("path_violation", str(exc), meta={"path": path})

    if not target.is_file():
        return _error("not_a_file", "requested path is not a regular file", meta={"path": path})

    selected: list[str] = []
    selected_chars = 0
    next_line: int | None = None
    line_truncated = False

    try:
        with target.open("rb") as raw_stream:
            digest = _sha256_stream(raw_stream, DEFAULT_HASH_CHUNK_SIZE)
            raw_stream.seek(0)
            with io.TextIOWrapper(raw_stream, encoding="utf-8", errors="strict", newline="") as stream:
                for line_number, line in enumerate(stream, start=1):
                    if line_number < start_line:
                        continue
                    if len(selected) >= max_lines:
                        next_line = line_number
                        break

                    remaining = max_chars - selected_chars
                    if remaining <= 0:
                        next_line = line_number
                        break
                    if len(line) > remaining:
                        if not selected:
                            selected.append(line[:remaining])
                            selected_chars += remaining
                            next_line = line_number + 1
                            line_truncated = True
                        else:
                            next_line = line_number
                        break

                    selected.append(line)
                    selected_chars += len(line)
    except UnicodeDecodeError:
        return _error("not_utf8", "file is not valid UTF-8 text", meta={"path": path})
    except OSError as exc:
        return _error("read_failed", str(exc), meta={"path": path})

    return {
        "ok": True,
        "data": "".join(selected),
        "error": None,
        "meta": {
            "path": path,
            "sha256": digest,
            "start_line": start_line,
            "returned_lines": len(selected),
            "returned_chars": selected_chars,
            "next_line": next_line,
            "truncated": next_line is not None,
            "line_truncated": line_truncated,
            "size_bytes": target.stat().st_size,
        },
    }


def write_file(
    workspace: Path,
    path: str,
    content: str,
    expected_sha256: str,
    *,
    max_write_bytes: int = DEFAULT_MAX_WRITE_BYTES,
) -> dict[str, Any]:
    """Atomically replace a UTF-8 file after an optimistic-lock check."""

    if not isinstance(content, str):
        return _error("invalid_content", "content must be a string")
    if not isinstance(expected_sha256, str):
        return _error("invalid_hash", "expected_sha256 must be a string")
    if max_write_bytes <= 0:
        return _error("invalid_write_limit", "max_write_bytes must be positive")

    encoded = content.encode("utf-8")
    if len(encoded) > max_write_bytes:
        return _error(
            "content_too_large",
            f"content exceeds the {max_write_bytes}-byte write limit",
            meta={"path": path, "size_bytes": len(encoded)},
        )

    try:
        target = resolve_in_workspace(workspace, path, must_exist=False)
    except PathViolation as exc:
        return _error("path_violation", str(exc), meta={"path": path})

    if target.exists() and not target.is_file():
        return _error("not_a_file", "target path is not a regular file", meta={"path": path})

    previous_mode: int | None = None
    if target.exists():
        try:
            actual_sha256 = sha256_file_streaming(target)
            previous_mode = stat.S_IMODE(target.stat().st_mode)
        except OSError as exc:
            return _error("read_before_write_failed", str(exc), meta={"path": path})
        if actual_sha256 != expected_sha256:
            return _error(
                "hash_conflict",
                "file changed since it was read; read it again before writing",
                meta={"path": path, "actual_sha256": actual_sha256},
            )
    elif expected_sha256:
        return _error(
            "expected_existing_file",
            "target does not exist but expected_sha256 is not empty",
            meta={"path": path},
        )

    temp_name: str | None = None
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        file_descriptor, temp_name = tempfile.mkstemp(prefix=".agent-", dir=target.parent)
        with os.fdopen(file_descriptor, "w", encoding="utf-8", newline="") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if previous_mode is not None:
            os.chmod(temp_name, previous_mode)
        os.replace(temp_name, target)
        temp_name = None
    except OSError as exc:
        return _error("write_failed", str(exc), meta={"path": path})
    finally:
        if temp_name is not None:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass

    return {
        "ok": True,
        "data": None,
        "error": None,
        "meta": {
            "path": path,
            "sha256": sha256_file_streaming(target),
            "size_bytes": len(encoded),
            "created": previous_mode is None,
        },
    }

