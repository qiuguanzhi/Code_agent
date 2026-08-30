"""Tests for the four Phase 2 extension hooks."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.state import AgentState, ToolCall
from tools.filesystem import sha256_file_streaming
from tools.registry import WRITE_POLICY, ToolRegistry, ToolStopRequested
from utils.diff import generate_unified_diff, truncate_diff
from utils.snapshot import rollback_to_snapshot, save_workspace_snapshot


class RejectingToolRegistry(ToolRegistry):
    """Exercise the future user-rejection branch without a real UI."""

    def _confirm_write(self, path: str) -> bool:
        """Reject every write for this test."""

        _ = path
        return False


def _write_call(path: str, content: str, expected_sha256: str) -> ToolCall:
    """Build a valid write_file call."""

    return ToolCall(
        id="write-call",
        name="write_file",
        arguments_json=json.dumps(
            {
                "path": path,
                "content": content,
                "expected_sha256": expected_sha256,
            }
        ),
    )


def test_workspace_snapshot_records_hash_and_modification_time(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "src" / "main.py"
    target.parent.mkdir()
    target.write_text("print('hello')\n", encoding="utf-8")

    snapshot = save_workspace_snapshot(workspace)
    metadata = json.loads(snapshot["src/main.py"])

    assert metadata["sha256"] == sha256_file_streaming(target)
    assert isinstance(metadata["mtime_ns"], int)


def test_snapshot_reports_bounded_file_copy_progress(tmp_path: Path) -> None:
    """Expose deterministic progress without changing the snapshot contract."""

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    for index in range(3):
        (workspace / f"file-{index}.txt").write_text(str(index), encoding="utf-8")
    updates: list[tuple[int, int, str]] = []

    snapshot = save_workspace_snapshot(
        workspace,
        lambda completed, total, path: updates.append((completed, total, path)),
    )

    assert len(snapshot) == 4
    assert updates[0] == (0, 3, "")
    assert updates[-1] == (3, 3, "file-2.txt")


def test_snapshot_skips_dependencies_and_rollback_preserves_them(tmp_path: Path) -> None:
    """Avoid copying or deleting heavyweight environment/cache directories."""

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = workspace / "main.py"
    source.write_text("original\n", encoding="utf-8")
    dependency = workspace / ".venv" / "Lib" / "package.py"
    dependency.parent.mkdir(parents=True)
    dependency.write_text("dependency\n", encoding="utf-8")
    transient = workspace / ".pytest-run-123" / "captured.txt"
    transient.parent.mkdir()
    transient.write_text("temporary\n", encoding="utf-8")

    snapshot = save_workspace_snapshot(workspace)

    assert "main.py" in snapshot
    assert ".venv/Lib/package.py" not in snapshot
    assert ".pytest-run-123/captured.txt" not in snapshot
    source.write_text("changed\n", encoding="utf-8")
    dependency.write_text("still-here\n", encoding="utf-8")
    assert rollback_to_snapshot(snapshot) is True
    assert source.read_text(encoding="utf-8") == "original\n"
    assert dependency.read_text(encoding="utf-8") == "still-here\n"


def test_rollback_restores_original_files_and_removes_new_files(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "main.py"
    target.write_text("original\n", encoding="utf-8", newline="\n")
    original_hash = sha256_file_streaming(target)
    snapshot = save_workspace_snapshot(workspace)

    target.write_text("changed\n", encoding="utf-8", newline="\n")
    added = workspace / "added.txt"
    added.write_text("new\n", encoding="utf-8", newline="\n")

    assert rollback_to_snapshot(snapshot) is True
    assert sha256_file_streaming(target) == original_hash
    assert target.read_text(encoding="utf-8") == "original\n"
    assert added.exists() is False


def test_generate_unified_diff_and_truncate() -> None:
    diff = generate_unified_diff("value = 1\n", "value = 2\n", "src/main.py")
    long_diff = generate_unified_diff(
        "".join(f"old-{index}\n" for index in range(2_000)),
        "".join(f"new-{index}\n" for index in range(2_000)),
        "large.txt",
    )
    truncated = truncate_diff(long_diff, max_chars=1_000)

    assert diff.startswith("--- a/src/main.py\n+++ b/src/main.py\n")
    assert "-value = 1" in diff
    assert "+value = 2" in diff
    assert "diff truncated" in truncated
    assert len(truncated) <= 1_000


def test_default_write_confirmation_hook_and_diff_metadata(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "main.py"
    target.write_text("value = 1\n", encoding="utf-8", newline="\n")
    state = AgentState(initial_snapshot=save_workspace_snapshot(workspace))
    registry = ToolRegistry(workspace)
    call = _write_call("main.py", "value = 2\n", sha256_file_streaming(target))

    encoded = registry.execute_one_call(call, state)
    result = json.loads(encoded)

    assert WRITE_POLICY == {"require_confirmation": True}
    assert result["ok"] is True
    assert result["meta"]["confirmed_by_user"] is True
    assert "-value = 1" in result["meta"]["diff"]
    assert "+value = 2" in result["meta"]["diff"]
    assert state.changed_files["main.py"] == result["meta"]["diff"]
    assert state.changed_file_hashes["main.py"] == result["meta"]["sha256"]
    assert registry.execute_one_call(call, state) == encoded


def test_deferred_registry_reads_staged_content_without_writing_disk(tmp_path: Path) -> None:
    """Let later model steps inspect a staged version before batch approval."""

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "main.py"
    target.write_text("value = 1\n", encoding="utf-8")
    state = AgentState()
    registry = ToolRegistry(
        workspace,
        write_policy={"require_confirmation": False},
        defer_writes=True,
    )
    write_call = _write_call(
        "main.py",
        "value = 2\n",
        sha256_file_streaming(target),
    )
    read_call = ToolCall(
        id="read-staged",
        name="read_file",
        arguments_json=json.dumps(
            {
                "path": "main.py",
                "start_line": 1,
                "max_lines": 20,
                "max_chars": 2_000,
            }
        ),
    )

    write_result = json.loads(registry.execute_one_call(write_call, state))
    read_result = json.loads(registry.execute_one_call(read_call, state))

    assert write_result["meta"]["staged"] is True
    assert read_result["meta"]["staged"] is True
    assert read_result["data"] == "value = 2\n"
    assert target.read_text(encoding="utf-8") == "value = 1\n"
    assert len(state.pending_writes) == 1


def test_rejected_write_does_not_touch_disk(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "main.py"
    target.write_text("original\n", encoding="utf-8", newline="\n")
    registry = RejectingToolRegistry(workspace)
    call = _write_call("main.py", "changed\n", sha256_file_streaming(target))

    result = json.loads(registry.execute_one_call(call, AgentState()))

    assert result["ok"] is False
    assert result["error"]["code"] == "user_aborted"
    assert result["meta"]["confirmed_by_user"] is False
    assert target.read_text(encoding="utf-8") == "original\n"


def test_tool_registry_checks_stop_before_dispatch(tmp_path: Path) -> None:
    """Never enter a local tool after cancellation was requested."""

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "main.py"
    target.write_text("original\n", encoding="utf-8")
    registry = ToolRegistry(workspace, should_stop=lambda: True)
    call = _write_call("main.py", "changed\n", sha256_file_streaming(target))

    with pytest.raises(ToolStopRequested):
        registry.execute_one_call(call, AgentState())

    assert target.read_text(encoding="utf-8") == "original\n"
