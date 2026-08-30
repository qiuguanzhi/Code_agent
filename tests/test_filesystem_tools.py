"""Tests for paginated reads and optimistic atomic writes."""

from pathlib import Path

from tools.filesystem import (
    apply_staged_writes,
    delete_file,
    read_file,
    read_staged_file,
    sha256_file_streaming,
    stage_write_file,
    write_file,
)


def test_read_file_returns_next_line_for_pagination(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "many-lines.txt"
    target.write_text(
        "".join(f"line-{index}\n" for index in range(1, 11)),
        encoding="utf-8",
        newline="\n",
    )

    first_page = read_file(
        workspace,
        "many-lines.txt",
        start_line=1,
        max_lines=3,
        max_chars=1_000,
    )
    second_page = read_file(
        workspace,
        "many-lines.txt",
        start_line=first_page["meta"]["next_line"],
        max_lines=3,
        max_chars=1_000,
    )

    assert first_page["ok"] is True
    assert first_page["data"] == "line-1\nline-2\nline-3\n"
    assert first_page["meta"]["next_line"] == 4
    assert first_page["meta"]["truncated"] is True
    assert second_page["data"].startswith("line-4\n")


def test_read_file_truncates_large_content(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "large.txt"
    target.write_text("".join(f"record-{index:04d}-payload\n" for index in range(2_000)), encoding="utf-8")

    result = read_file(
        workspace,
        "large.txt",
        start_line=1,
        max_lines=500,
        max_chars=250,
    )

    assert result["ok"] is True
    assert len(result["data"]) <= 250
    assert result["meta"]["truncated"] is True
    assert isinstance(result["meta"]["next_line"], int)
    assert result["meta"]["next_line"] > 1
    assert result["meta"]["size_bytes"] > len(result["data"].encode("utf-8"))


def test_write_file_rejects_hash_conflict(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "module.py"
    target.write_text("value = 1\n", encoding="utf-8")
    stale_hash = sha256_file_streaming(target)
    target.write_text("value = 2\n", encoding="utf-8")

    result = write_file(
        workspace,
        "module.py",
        "value = 3\n",
        stale_hash,
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "hash_conflict"
    assert target.read_text(encoding="utf-8") == "value = 2\n"


def test_write_file_atomically_replaces_matching_file(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "module.py"
    target.write_text("value = 1\n", encoding="utf-8")
    expected_hash = sha256_file_streaming(target)

    result = write_file(
        workspace,
        "module.py",
        "value = 2\n",
        expected_hash,
    )

    assert result["ok"] is True
    assert target.read_text(encoding="utf-8") == "value = 2\n"
    assert result["meta"]["sha256"] == sha256_file_streaming(target)
    assert "-value = 1" in result["meta"]["diff"]
    assert "+value = 2" in result["meta"]["diff"]
    assert list(workspace.glob(".agent-*")) == []


def test_write_file_creates_new_file_only_with_empty_hash(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    rejected = write_file(workspace, "new.txt", "new\n", "not-empty")
    created = write_file(workspace, "nested/new.txt", "new\n", "")

    assert rejected["ok"] is False
    assert rejected["error"]["code"] == "expected_existing_file"
    assert created["ok"] is True
    assert (workspace / "nested" / "new.txt").read_text(encoding="utf-8") == "new\n"


def test_file_tools_reject_parent_directory_escape(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    secret = tmp_path / "secret.txt"
    secret.write_text("do not change", encoding="utf-8")

    read_result = read_file(workspace, "../secret.txt")
    write_result = write_file(workspace, "../secret.txt", "changed", "")

    assert read_result["ok"] is False
    assert read_result["error"]["code"] == "path_violation"
    assert write_result["ok"] is False
    assert write_result["error"]["code"] == "path_violation"
    assert secret.read_text(encoding="utf-8") == "do not change"


def test_delete_file_requires_current_hash_and_reports_deleted_path(tmp_path: Path) -> None:
    """Delete only the exact file version the Agent previously inspected."""

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "verify.py"
    target.write_text("assert True\n", encoding="utf-8")
    expected_hash = sha256_file_streaming(target)

    conflict = delete_file(workspace, "verify.py", "stale")
    deleted = delete_file(workspace, "verify.py", expected_hash)

    assert conflict["error"]["code"] == "hash_conflict"
    assert deleted["ok"] is True
    assert deleted["meta"]["path"] == "verify.py"
    assert deleted["meta"]["deleted_sha256"] == expected_hash
    assert target.exists() is False


def test_delete_file_rejects_workspace_escape(tmp_path: Path) -> None:
    """Deletion applies the same resolved workspace boundary as other tools."""

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    secret = tmp_path / "secret.txt"
    secret.write_text("keep", encoding="utf-8")

    result = delete_file(workspace, "../secret.txt", sha256_file_streaming(secret))

    assert result["error"]["code"] == "path_violation"
    assert secret.read_text(encoding="utf-8") == "keep"


def test_staged_writes_are_readable_but_do_not_touch_disk_until_batch_apply(
    tmp_path: Path,
) -> None:
    """Provide read-your-writes semantics while preserving approval isolation."""

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    first = workspace / "first.py"
    second = workspace / "second.py"
    first.write_text("value = 1\n", encoding="utf-8")
    second.write_text("value = 2\n", encoding="utf-8")
    pending: list[dict[str, object]] = []

    first_result = stage_write_file(
        workspace,
        "first.py",
        "value = 10\n",
        sha256_file_streaming(first),
        pending_writes=pending,
    )
    second_result = stage_write_file(
        workspace,
        "second.py",
        "value = 20\n",
        sha256_file_streaming(second),
        pending_writes=pending,
    )
    staged_page = read_staged_file("first.py", str(pending[0]["content"]))

    assert first_result["meta"]["staged"] is True
    assert second_result["meta"]["pending_count"] == 2
    assert staged_page["data"] == "value = 10\n"
    assert first.read_text(encoding="utf-8") == "value = 1\n"
    assert second.read_text(encoding="utf-8") == "value = 2\n"

    applied = apply_staged_writes(workspace, pending)

    assert applied["ok"] is True
    assert first.read_text(encoding="utf-8") == "value = 10\n"
    assert second.read_text(encoding="utf-8") == "value = 20\n"


def test_batch_preflight_conflict_prevents_every_write(tmp_path: Path) -> None:
    """Reject a conflicting batch before applying even its conflict-free files."""

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    first = workspace / "first.py"
    second = workspace / "second.py"
    first.write_text("first = 1\n", encoding="utf-8")
    second.write_text("second = 1\n", encoding="utf-8")
    pending: list[dict[str, object]] = []
    stage_write_file(
        workspace,
        "first.py",
        "first = 2\n",
        sha256_file_streaming(first),
        pending_writes=pending,
    )
    stage_write_file(
        workspace,
        "second.py",
        "second = 2\n",
        sha256_file_streaming(second),
        pending_writes=pending,
    )
    second.write_text("external = True\n", encoding="utf-8")

    result = apply_staged_writes(workspace, pending)

    assert result["error"]["code"] == "hash_conflict"
    assert first.read_text(encoding="utf-8") == "first = 1\n"
    assert second.read_text(encoding="utf-8") == "external = True\n"
