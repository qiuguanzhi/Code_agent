"""Safe, workspace-scoped filesystem tools."""

from __future__ import annotations

import hashlib
import io
import os
import stat
import tempfile
from pathlib import Path
from typing import Any, BinaryIO

from utils.diff import generate_unified_diff, truncate_diff


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
    original_content = ""
    if target.exists():
        try:
            actual_sha256 = sha256_file_streaming(target)
            previous_mode = stat.S_IMODE(target.stat().st_mode)
            with target.open("r", encoding="utf-8", errors="strict", newline="") as stream:
                original_content = stream.read()
        except UnicodeDecodeError:
            return _error(
                "not_utf8",
                "existing file is not valid UTF-8 text",
                meta={"path": path},
            )
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

    diff_text = truncate_diff(generate_unified_diff(original_content, content, path))
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
        return _error("write_failed", str(exc), meta={"path": path, "diff": diff_text})
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
            "diff": diff_text,
        },
    }


def read_staged_file(
    path: str,
    content: str,
    start_line: int = 1,
    max_lines: int = 200,
    max_chars: int = 20_000,
) -> dict[str, Any]:
    """Read one in-memory staged UTF-8 document with normal paging metadata."""

    if start_line < 1:
        return _error("invalid_start_line", "start_line must be at least 1")
    if not 1 <= max_lines <= 500:
        return _error("invalid_max_lines", "max_lines must be between 1 and 500")
    if not 1 <= max_chars <= 50_000:
        return _error("invalid_max_chars", "max_chars must be between 1 and 50000")
    selected: list[str] = []
    selected_chars = 0
    next_line: int | None = None
    line_truncated = False
    for line_number, line in enumerate(io.StringIO(content), start=1):
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
    encoded = content.encode("utf-8")
    return {
        "ok": True,
        "data": "".join(selected),
        "error": None,
        "meta": {
            "path": path,
            "sha256": hashlib.sha256(encoded).hexdigest(),
            "start_line": start_line,
            "returned_lines": len(selected),
            "returned_chars": selected_chars,
            "next_line": next_line,
            "truncated": next_line is not None,
            "line_truncated": line_truncated,
            "size_bytes": len(encoded),
            "staged": True,
        },
    }


def stage_write_file(
    workspace: Path,
    path: str,
    content: str,
    expected_sha256: str,
    *,
    pending_writes: list[dict[str, Any]],
    max_write_bytes: int = DEFAULT_MAX_WRITE_BYTES,
) -> dict[str, Any]:
    """Validate and stage a complete file replacement without touching disk."""

    if not isinstance(content, str):
        return _error("invalid_content", "content must be a string")
    if not isinstance(expected_sha256, str):
        return _error("invalid_hash", "expected_sha256 must be a string")
    encoded = content.encode("utf-8")
    if len(encoded) > max_write_bytes:
        return _error(
            "content_too_large",
            f"content exceeds the {max_write_bytes}-byte write limit",
            meta={"path": path, "size_bytes": len(encoded)},
        )
    try:
        root = workspace.resolve(strict=True)
        target = resolve_in_workspace(root, path, must_exist=False)
        normalized_path = target.relative_to(root).as_posix()
    except (OSError, RuntimeError, PathViolation, ValueError) as exc:
        return _error("path_violation", str(exc), meta={"path": path})
    if target.exists() and not target.is_file():
        return _error("not_a_file", "target path is not a regular file", meta={"path": path})

    existing_entry = next(
        (
            item
            for item in reversed(pending_writes)
            if str(item.get("path", "")) == normalized_path
        ),
        None,
    )
    if existing_entry is not None:
        current_content = str(existing_entry.get("content", ""))
        current_sha256 = str(existing_entry.get("staged_sha256", ""))
        original_content = str(existing_entry.get("original_content", ""))
        base_sha256 = str(existing_entry.get("base_sha256", ""))
        created = bool(existing_entry.get("created", False))
    elif target.exists():
        try:
            current_content = target.read_text(encoding="utf-8")
            current_sha256 = sha256_file_streaming(target)
        except UnicodeDecodeError:
            return _error("not_utf8", "existing file is not valid UTF-8 text", meta={"path": path})
        except OSError as exc:
            return _error("read_before_write_failed", str(exc), meta={"path": path})
        original_content = current_content
        base_sha256 = current_sha256
        created = False
    else:
        current_content = ""
        current_sha256 = ""
        original_content = ""
        base_sha256 = ""
        created = True

    if current_sha256 != expected_sha256:
        code = "expected_existing_file" if created and expected_sha256 else "hash_conflict"
        return _error(
            code,
            "staged or on-disk file changed; read it again before writing",
            meta={"path": normalized_path, "actual_sha256": current_sha256},
        )

    staged_sha256 = hashlib.sha256(encoded).hexdigest()
    if content == original_content:
        if existing_entry is not None:
            pending_writes.remove(existing_entry)
        return {
            "ok": True,
            "data": None,
            "error": None,
            "meta": {
                "path": normalized_path,
                "sha256": staged_sha256,
                "size_bytes": len(encoded),
                "created": created,
                "diff": "",
                "staged": True,
                "no_change": True,
                "pending_count": len(pending_writes),
            },
        }
    diff_text = truncate_diff(
        generate_unified_diff(original_content, content, normalized_path)
    )
    entry: dict[str, Any] = {
        "path": normalized_path,
        "content": content,
        "original_content": original_content,
        "base_sha256": base_sha256,
        "staged_sha256": staged_sha256,
        "created": created,
        "diff": diff_text,
    }
    if existing_entry is None:
        pending_writes.append(entry)
    else:
        existing_entry.clear()
        existing_entry.update(entry)
    return {
        "ok": True,
        "data": None,
        "error": None,
        "meta": {
            "path": normalized_path,
            "sha256": staged_sha256,
            "size_bytes": len(encoded),
            "created": created,
            "diff": diff_text,
            "staged": True,
            "pending_count": len(pending_writes),
        },
    }


def apply_staged_writes(
    workspace: Path,
    pending_writes: list[dict[str, Any]],
) -> dict[str, Any]:
    """Preflight and apply all staged files, rolling back on a partial failure."""

    if not pending_writes:
        return {"ok": True, "data": [], "error": None, "meta": {"count": 0}}
    validated: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    for raw_entry in pending_writes:
        path = raw_entry.get("path")
        content = raw_entry.get("content")
        base_sha256 = raw_entry.get("base_sha256")
        if not isinstance(path, str) or not isinstance(content, str) or not isinstance(base_sha256, str):
            return _error("invalid_staged_write", "staged write data is malformed")
        if path in seen_paths:
            return _error("duplicate_staged_path", f"duplicate staged path: {path}")
        seen_paths.add(path)
        try:
            target = resolve_in_workspace(workspace, path, must_exist=False)
        except PathViolation as exc:
            return _error("path_violation", str(exc), meta={"path": path})
        if target.exists():
            if not target.is_file():
                return _error("not_a_file", "target path is not a regular file", meta={"path": path})
            try:
                actual_sha256 = sha256_file_streaming(target)
            except OSError as exc:
                return _error("batch_preflight_failed", str(exc), meta={"path": path})
        else:
            actual_sha256 = ""
        if actual_sha256 != base_sha256:
            return _error(
                "hash_conflict",
                "file changed after the Diff was staged",
                meta={"path": path, "actual_sha256": actual_sha256},
            )
        validated.append(raw_entry)

    applied: list[dict[str, Any]] = []
    for entry in validated:
        path = str(entry["path"])
        result = write_file(
            workspace,
            path,
            str(entry["content"]),
            str(entry["base_sha256"]),
        )
        if result.get("ok") is not True:
            _rollback_staged_subset(workspace, applied)
            return _error(
                "batch_apply_failed",
                f"failed to apply staged file: {path}",
                meta={"path": path, "cause": result, "rollback_performed": True},
            )
        applied.append({"entry": entry, "result": result})
    return {
        "ok": True,
        "data": [item["result"] for item in applied],
        "error": None,
        "meta": {"count": len(applied)},
    }


def _rollback_staged_subset(
    workspace: Path,
    applied: list[dict[str, Any]],
) -> None:
    """Best-effort rollback of files already written by one failed batch."""

    for item in reversed(applied):
        entry = item["entry"]
        result = item["result"]
        path = str(entry["path"])
        meta = result.get("meta", {})
        current_sha256 = str(meta.get("sha256", "")) if isinstance(meta, dict) else ""
        if bool(entry.get("created", False)):
            delete_file(workspace, path, current_sha256)
        else:
            write_file(
                workspace,
                path,
                str(entry.get("original_content", "")),
                current_sha256,
            )


def delete_file(
    workspace: Path,
    path: str,
    expected_sha256: str,
) -> dict[str, Any]:
    """Delete one regular workspace file after an optimistic-lock check."""

    if not isinstance(expected_sha256, str):
        return _error("invalid_hash", "expected_sha256 must be a string")
    try:
        target = resolve_in_workspace(workspace, path, must_exist=True)
    except PathViolation as exc:
        return _error("path_violation", str(exc), meta={"path": path})
    try:
        unresolved_target = workspace.resolve(strict=True) / Path(path)
    except (OSError, RuntimeError, ValueError) as exc:
        return _error("path_violation", str(exc), meta={"path": path})
    if unresolved_target.is_symlink() or not target.is_file():
        return _error(
            "not_a_file",
            "target path is not a regular file",
            meta={"path": path},
        )
    try:
        actual_sha256 = sha256_file_streaming(target)
    except OSError as exc:
        return _error("read_before_delete_failed", str(exc), meta={"path": path})
    if actual_sha256 != expected_sha256:
        return _error(
            "hash_conflict",
            "file changed since it was read; read it again before deleting",
            meta={"path": path, "actual_sha256": actual_sha256},
        )
    try:
        target.unlink()
    except OSError as exc:
        return _error("delete_failed", str(exc), meta={"path": path})
    return {
        "ok": True,
        "data": None,
        "error": None,
        "meta": {"path": path, "deleted_sha256": actual_sha256},
    }
