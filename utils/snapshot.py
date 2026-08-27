"""Workspace snapshot extension points for future rollback support."""

from __future__ import annotations

import json
import os
from pathlib import Path

from tools.filesystem import sha256_file_streaming


def save_workspace_snapshot(workspace_root: Path | str) -> dict[str, str]:
    """Record SHA-256 and modification time for every regular workspace file.

    Snapshot values are JSON strings so ``AgentState.initial_snapshot`` keeps
    the requested ``dict[str, str]`` type while retaining both data fields.
    Symbolic links are not followed, preventing a snapshot from reading outside
    the workspace through a link.
    """

    try:
        root = Path(workspace_root).resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as exc:
        raise ValueError("workspace_root cannot be resolved") from exc
    if not root.is_dir():
        raise ValueError("workspace_root must be a directory")

    snapshot: dict[str, str] = {}
    for current_root, directory_names, file_names in os.walk(root, followlinks=False):
        current_path = Path(current_root)
        directory_names[:] = sorted(
            name for name in directory_names if not (current_path / name).is_symlink()
        )
        for file_name in sorted(file_names):
            candidate = current_path / file_name
            if candidate.is_symlink() or not candidate.is_file():
                continue
            try:
                relative_path = candidate.relative_to(root).as_posix()
            except ValueError as exc:
                raise ValueError("snapshot path escaped workspace") from exc
            stat_result = candidate.stat()
            snapshot[relative_path] = json.dumps(
                {
                    "sha256": sha256_file_streaming(candidate),
                    "mtime_ns": stat_result.st_mtime_ns,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
    return snapshot


def rollback_to_snapshot(snapshot_data: dict[str, str]) -> bool:
    """Placeholder for a future content-preserving rollback implementation."""

    _ = snapshot_data
    print("Rollback not fully implemented yet")
    return False

