"""Tests for paginated reads and optimistic atomic writes."""

from pathlib import Path

from tools.filesystem import read_file, sha256_file_streaming, write_file


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
