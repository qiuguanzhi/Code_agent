"""Tests for CLI argument parsing and fail-fast environment validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from main import build_parser, main


def test_cli_parser_accepts_required_phase3_flags(tmp_path: Path) -> None:
    args = build_parser().parse_args(
        [
            "--workspace",
            str(tmp_path),
            "--max-steps",
            "12",
            "--interactive",
            "--verbose",
            "--mode",
            "goal",
            "repair tests",
        ]
    )

    assert args.workspace == tmp_path
    assert args.max_steps == 12
    assert args.interactive is True
    assert args.verbose is True
    assert args.mode == "goal"


def test_cli_reports_missing_api_key_without_network(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("AGENT_MODEL", "fake-model")

    exit_code = main(["--workspace", str(tmp_path), "task"])

    assert exit_code == 2
    assert "DEEPSEEK_API_KEY" in capsys.readouterr().err


def test_cli_reports_missing_workspace_and_task(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Keep GUI-compatible optional parser fields mandatory at CLI runtime."""

    exit_code = main(["--cli"])

    assert exit_code == 2
    assert "--workspace" in capsys.readouterr().err
