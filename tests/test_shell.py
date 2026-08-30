"""Tests for bounded, timeout-aware command execution."""

import os
import sys
import time
from pathlib import Path

from tools.shell import BoundedHeadTailBuffer, build_sanitized_env, run_command


def test_bounded_buffer_preserves_head_and_tail() -> None:
    buffer = BoundedHeadTailBuffer(10)
    buffer.feed("abcdefghij")
    buffer.feed("klmnop")

    rendered = buffer.render()

    assert buffer.truncated is True
    assert rendered.startswith("abcde")
    assert rendered.endswith("lmnop")
    assert "truncated 6 characters" in rendered


def test_sanitized_environment_removes_likely_secrets() -> None:
    result = build_sanitized_env(
        {
            "PATH": "safe-path",
            "DEEPSEEK_API_KEY": "secret",
            "CUSTOM_TOKEN": "secret",
            "NORMAL_SETTING": "visible",
        }
    )

    assert result["PATH"] == "safe-path"
    assert result["NORMAL_SETTING"] == "visible"
    assert "DEEPSEEK_API_KEY" not in result
    assert "CUSTOM_TOKEN" not in result


def test_sanitized_environment_override_keeps_process_path() -> None:
    result = build_sanitized_env({"NORMAL_SETTING": "override"})

    assert result.get("PATH") or result.get("Path")
    assert result["NORMAL_SETTING"] == "override"


def test_first_ls_call_lists_workspace_without_shell(tmp_path: Path) -> None:
    """The first listing must work on Windows even though ls is not an executable."""

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "alpha.txt").write_text("a", encoding="utf-8")
    (workspace / "folder").mkdir()

    result = run_command(
        workspace,
        ["ls"],
        timeout_seconds=5,
        max_output_chars=1_000,
    )

    assert result["ok"] is True
    assert "alpha.txt" in result["data"]
    assert "folder" in result["data"]
    if os.name == "nt":
        assert result["meta"]["portable_alias"] is True


def test_run_command_captures_successful_output(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    result = run_command(
        workspace,
        [sys.executable, "-c", "print('hello from child')"],
        timeout_seconds=5,
        max_output_chars=1_000,
    )

    assert result["ok"] is True
    assert result["meta"]["exit_code"] == 0
    assert result["data"] == "hello from child\n"


def test_run_command_truncates_output_and_keeps_tail(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    result = run_command(
        workspace,
        [sys.executable, "-c", "print('A' * 3000 + 'THE_END')"],
        timeout_seconds=5,
        max_output_chars=1_000,
    )

    assert result["ok"] is True
    assert result["meta"]["output_truncated"] is True
    assert "truncated" in result["data"]
    assert result["data"].endswith("THE_END\n")


def test_run_command_terminates_after_timeout(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    result = run_command(
        workspace,
        [sys.executable, "-c", "import time; time.sleep(10)"],
        timeout_seconds=1,
        max_output_chars=1_000,
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "timeout"
    assert result["meta"]["timed_out"] is True
    assert result["meta"]["duration_ms"] < 8_000


def test_run_command_rejects_cwd_escape(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    result = run_command(
        workspace,
        [sys.executable, "-c", "print('must not run')"],
        cwd="..",
        timeout_seconds=5,
        max_output_chars=1_000,
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "path_violation"


def test_run_command_rejects_shell_executable(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    result = run_command(
        workspace,
        ["cmd.exe", "/c", "echo unsafe"],
        timeout_seconds=5,
        max_output_chars=1_000,
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "invalid_command"


def test_run_command_cancels_long_process_within_two_seconds(tmp_path: Path) -> None:
    """Poll the stop flag and terminate the entire child process group promptly."""

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    started = time.monotonic()

    result = run_command(
        workspace,
        [sys.executable, "-c", "import time; time.sleep(10)"],
        timeout_seconds=20,
        max_output_chars=1_000,
        should_stop=lambda: time.monotonic() - started >= 0.2,
    )

    duration = time.monotonic() - started
    assert result["ok"] is False
    assert result["error"]["code"] == "cancelled"
    assert result["meta"]["cancelled"] is True
    assert duration < 2.0
