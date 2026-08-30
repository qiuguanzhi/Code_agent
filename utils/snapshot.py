"""Content-preserving workspace snapshots with verified rollback support."""

from __future__ import annotations

import atexit
import hashlib
import json
import os
import shutil
import stat
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from tools.filesystem import sha256_file_streaming


SNAPSHOT_META_KEY = "::mini-coding-agent-snapshot-v1::"
_BACKUP_ROOTS: dict[Path, Path] = {}
SNAPSHOT_EXCLUDED_DIRECTORIES: frozenset[str] = frozenset(
    {
        ".git",
        ".idea",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".venv",
        ".vscode",
        "__pycache__",
        "node_modules",
        "venv",
    }
)


def _is_snapshot_excluded_directory(name: str) -> bool:
    """Recognize dependency, VCS, IDE, and transient test-output directories."""

    return (
        name in SNAPSHOT_EXCLUDED_DIRECTORIES
        or name.startswith(".pytest-run-")
        or name == ".pytest-local-tmp"
        or name.endswith(".egg-info")
    )


def _cleanup_backups() -> None:
    """Remove only temporary backup directories created by this process."""

    for backup_root in tuple(_BACKUP_ROOTS):
        shutil.rmtree(backup_root, ignore_errors=True)
        _BACKUP_ROOTS.pop(backup_root, None)


atexit.register(_cleanup_backups)


def _resolve_workspace(workspace_root: Path | str) -> Path:
    """Resolve and validate one snapshot workspace root."""

    try:
        root = Path(workspace_root).resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as exc:
        raise ValueError("workspace_root cannot be resolved") from exc
    if not root.is_dir():
        raise ValueError("workspace_root must be a directory")
    return root


def _relative_regular_files(root: Path) -> list[tuple[str, Path]]:
    """List source files without following links or copying dependency caches."""

    files: list[tuple[str, Path]] = []
    for current_root, directory_names, file_names in os.walk(root, followlinks=False):
        current_path = Path(current_root)
        directory_names[:] = sorted(
            name
            for name in directory_names
            if not _is_snapshot_excluded_directory(name)
            and not (current_path / name).is_symlink()
        )
        for file_name in sorted(file_names):
            candidate = current_path / file_name
            if candidate.is_symlink() or not candidate.is_file():
                continue
            try:
                relative_path = candidate.relative_to(root).as_posix()
            except ValueError as exc:
                raise ValueError("snapshot path escaped workspace") from exc
            files.append((relative_path, candidate))
    return files


def _copy_with_sha256(source: Path, destination: Path) -> str:
    """Copy and hash in one source read, then preserve file metadata."""

    digest = hashlib.sha256()
    with source.open("rb") as input_stream, destination.open("xb") as output_stream:
        while True:
            chunk = input_stream.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            output_stream.write(chunk)
    shutil.copystat(source, destination, follow_symlinks=False)
    return digest.hexdigest()


SnapshotProgressCallback = Callable[[int, int, str], None]


def save_workspace_snapshot(
    workspace_root: Path | str,
    progress_callback: SnapshotProgressCallback | None = None,
) -> dict[str, str]:
    """Copy workspace files to a private temporary backup and return a manifest.

    Values remain JSON strings so the established ``dict[str, str]`` state
    contract is preserved. The backup lives outside the workspace, is not
    model-accessible through workspace tools, and is cleaned at process exit.
    """

    started_at = time.perf_counter()
    root = _resolve_workspace(workspace_root)
    backup_root = Path(tempfile.mkdtemp(prefix="mini-coding-agent-snapshot-"))
    try:
        backup_root = backup_root.resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as exc:
        shutil.rmtree(backup_root, ignore_errors=True)
        raise ValueError("snapshot backup directory cannot be resolved") from exc
    _BACKUP_ROOTS[backup_root] = root

    snapshot: dict[str, str] = {
        SNAPSHOT_META_KEY: json.dumps(
            {
                "version": 1,
                "workspace_root": str(root),
                "backup_root": str(backup_root),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    }
    try:
        scan_started_at = time.perf_counter()
        files = _relative_regular_files(root)
        scan_duration_ms = (time.perf_counter() - scan_started_at) * 1_000
        total = len(files)
        if progress_callback is not None:
            progress_callback(0, total, "")
        for completed, (relative_path, candidate) in enumerate(files, start=1):
            backup_path = backup_root / Path(relative_path)
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            source_hash = _copy_with_sha256(candidate, backup_path)
            backup_stat = backup_path.stat()
            snapshot[relative_path] = json.dumps(
                {
                    "sha256": source_hash,
                    "mtime_ns": backup_stat.st_mtime_ns,
                    "mode": stat.S_IMODE(backup_stat.st_mode),
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            if progress_callback is not None and (
                total <= 100
                or completed == total
                or completed % max(1, total // 100) == 0
            ):
                progress_callback(completed, total, relative_path)
    except (OSError, RuntimeError, ValueError):
        shutil.rmtree(backup_root, ignore_errors=True)
        _BACKUP_ROOTS.pop(backup_root, None)
        raise
    duration_ms = (time.perf_counter() - started_at) * 1_000
    print(
        "[Cerebro::Snapshot] "
        f"workspace={root} files={total} scan_ms={scan_duration_ms:.1f} "
        f"total_ms={duration_ms:.1f}"
    )
    return snapshot


def discard_workspace_snapshot(snapshot_data: dict[str, str]) -> None:
    """Delete a registered backup when its manifest is no longer needed."""

    raw_manifest = snapshot_data.get(SNAPSHOT_META_KEY)
    if not isinstance(raw_manifest, str):
        return
    try:
        manifest = json.loads(raw_manifest)
    except json.JSONDecodeError:
        return
    if not isinstance(manifest, dict):
        return
    backup_value = manifest.get("backup_root")
    if not isinstance(backup_value, str):
        return
    try:
        backup_root = Path(backup_value).resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        return
    if backup_root not in _BACKUP_ROOTS:
        return
    shutil.rmtree(backup_root, ignore_errors=True)
    _BACKUP_ROOTS.pop(backup_root, None)


def _load_manifest(snapshot_data: dict[str, str]) -> tuple[Path, Path, dict[str, dict[str, Any]]]:
    """Validate snapshot metadata and every backup before touching the workspace."""

    raw_manifest = snapshot_data.get(SNAPSHOT_META_KEY)
    if not isinstance(raw_manifest, str):
        raise ValueError("snapshot manifest is missing")
    try:
        manifest = json.loads(raw_manifest)
    except json.JSONDecodeError as exc:
        raise ValueError("snapshot manifest is invalid") from exc
    if not isinstance(manifest, dict) or manifest.get("version") != 1:
        raise ValueError("snapshot manifest version is unsupported")

    workspace_value = manifest.get("workspace_root")
    backup_value = manifest.get("backup_root")
    if not isinstance(workspace_value, str) or not isinstance(backup_value, str):
        raise ValueError("snapshot manifest paths are invalid")
    workspace = _resolve_workspace(workspace_value)
    try:
        backup_root = Path(backup_value).resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as exc:
        raise ValueError("snapshot backup is unavailable") from exc
    if not backup_root.is_dir():
        raise ValueError("snapshot backup is not a directory")
    if _BACKUP_ROOTS.get(backup_root) != workspace:
        raise ValueError("snapshot backup is not registered for this workspace")

    entries: dict[str, dict[str, Any]] = {}
    for relative_path, raw_metadata in snapshot_data.items():
        if relative_path == SNAPSHOT_META_KEY:
            continue
        if not isinstance(relative_path, str) or not relative_path or "\x00" in relative_path:
            raise ValueError("snapshot contains an invalid relative path")
        path_object = Path(relative_path)
        if path_object.is_absolute():
            raise ValueError("snapshot contains an absolute path")
        try:
            target = (workspace / path_object).resolve(strict=False)
            target.relative_to(workspace)
            backup_path = (backup_root / path_object).resolve(strict=True)
            backup_path.relative_to(backup_root)
        except (OSError, RuntimeError, ValueError) as exc:
            raise ValueError("snapshot path escaped its root") from exc
        if not backup_path.is_file():
            raise ValueError("snapshot backup file is missing")
        try:
            metadata = json.loads(raw_metadata)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError("snapshot file metadata is invalid") from exc
        if not isinstance(metadata, dict):
            raise ValueError("snapshot file metadata must be an object")
        expected_hash = metadata.get("sha256")
        if not isinstance(expected_hash, str) or sha256_file_streaming(backup_path) != expected_hash:
            raise ValueError("snapshot backup hash verification failed")
        entries[relative_path] = metadata
    return workspace, backup_root, entries


def _remove_new_regular_files(workspace: Path, initial_paths: set[str]) -> None:
    """Delete regular files created after the snapshot without following links."""

    for relative_path, candidate in reversed(_relative_regular_files(workspace)):
        if relative_path in initial_paths:
            continue
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(workspace)
        except (OSError, RuntimeError, ValueError) as exc:
            raise ValueError("rollback deletion path escaped workspace") from exc
        candidate.unlink()


def _restore_one_file(
    workspace: Path,
    backup_root: Path,
    relative_path: str,
    metadata: dict[str, Any],
) -> None:
    """Atomically restore one verified backup and recheck its final hash."""

    target = (workspace / Path(relative_path)).resolve(strict=False)
    try:
        target.relative_to(workspace)
    except ValueError as exc:
        raise ValueError("rollback target escaped workspace") from exc
    backup_path = (backup_root / Path(relative_path)).resolve(strict=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".rollback-", dir=target.parent)
    os.close(descriptor)
    temporary_path = Path(temporary_name)
    try:
        shutil.copy2(backup_path, temporary_path)
        mode = metadata.get("mode")
        if isinstance(mode, int):
            os.chmod(temporary_path, mode)
        os.replace(temporary_path, target)
        mtime_ns = metadata.get("mtime_ns")
        if isinstance(mtime_ns, int):
            os.utime(target, ns=(mtime_ns, mtime_ns))
    finally:
        if temporary_path.exists():
            temporary_path.unlink()

    expected_hash = metadata.get("sha256")
    if not isinstance(expected_hash, str) or sha256_file_streaming(target) != expected_hash:
        raise ValueError("restored file hash verification failed")


def rollback_to_snapshot(snapshot_data: dict[str, str]) -> bool:
    """Restore all snapshotted files and remove regular files created later."""

    if not isinstance(snapshot_data, dict):
        return False
    try:
        workspace, backup_root, entries = _load_manifest(snapshot_data)
        _remove_new_regular_files(workspace, set(entries))
        for relative_path in sorted(entries):
            _restore_one_file(
                workspace,
                backup_root,
                relative_path,
                entries[relative_path],
            )
    except (OSError, RuntimeError, ValueError):
        return False
    return True
