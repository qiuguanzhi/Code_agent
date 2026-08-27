"""CLI and optional PySide6 entry points for the lightweight coding agent."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from agent.loop import AgentConfig, AgentRunResult, UpdateCallback, run_agent
from providers.openai_compatible import ProviderConfigurationError, create_provider_from_env


ANSI_COLORS: dict[str, str] = {
    "blue": "\033[94m",
    "green": "\033[92m",
    "purple": "\033[95m",
    "red": "\033[91m",
    "yellow": "\033[93m",
    "reset": "\033[0m",
}


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser without reading secrets or contacting a provider."""

    parser = argparse.ArgumentParser(
        description="Run the framework-free local coding agent.",
    )
    parser.add_argument("task", nargs="?", help="Programming task for the agent")
    parser.add_argument(
        "--workspace",
        type=Path,
        help="Existing workspace directory accessible to local tools",
    )
    parser.add_argument("--max-steps", type=int, default=20)
    parser.add_argument("--max-wall-seconds", type=float, default=600.0)
    parser.add_argument("--input-budget", type=int, default=48_000)
    parser.add_argument("--interactive", action="store_true", help="Confirm every write_file call")
    parser.add_argument("--verbose", action="store_true", help="Show structured event details")
    parser.add_argument("--mode", choices=("auto", "goal"), default="auto")
    parser.add_argument(
        "--provider",
        choices=("deepseek", "bailian"),
        default=os.getenv("AGENT_PROVIDER", "deepseek"),
    )
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--gui",
        action="store_true",
        help="Launch the PySide6 desktop skeleton without contacting a provider",
    )
    mode_group.add_argument(
        "--cli",
        action="store_true",
        help="Explicitly select CLI mode",
    )
    return parser


def run_gui() -> int:
    """Launch the optional desktop application through lazy GUI imports."""

    from PySide6.QtWidgets import QApplication

    from gui.main_window import MainWindow
    from gui.theme import DARK_THEME

    existing_app = QApplication.instance()
    app = existing_app if existing_app is not None else QApplication([sys.argv[0]])
    app.setApplicationName("Mini Coding Agent")
    app.setStyleSheet(DARK_THEME)
    window = MainWindow()
    window.show()
    return int(app.exec())


def _confirm_write(path: str) -> bool:
    """Prompt until the user accepts or rejects one file modification."""

    while True:
        answer = input(f"允许修改 {path}？[y/n]: ").strip().lower()
        if answer in {"y", "yes"}:
            return True
        if answer in {"n", "no"}:
            return False
        print("请输入 y 或 n。")


def _supports_color() -> bool:
    """Return whether ANSI color should be emitted to the terminal."""

    return sys.stdout.isatty() and os.getenv("NO_COLOR") is None


def _color(text: str, name: str) -> str:
    """Apply an ANSI color only for an interactive color-capable terminal."""

    if not _supports_color():
        return text
    return f"{ANSI_COLORS[name]}{text}{ANSI_COLORS['reset']}"


def _event_color(event: str) -> str:
    """Map lifecycle events to a small dependency-free color palette."""

    if event in {"run_completed", "tool_result"}:
        return "green"
    if event in {"run_failed", "run_stopped"}:
        return "red"
    if event in {"api_retry"}:
        return "yellow"
    if event in {"model_request", "model_response"}:
        return "purple"
    return "blue"


def _build_update_printer(verbose: bool) -> UpdateCallback:
    """Return the lifecycle callback used by the CLI."""

    def on_update(update: dict[str, Any]) -> None:
        event = str(update.get("event", "event"))
        step = int(update.get("step", 0))
        message = str(update.get("message", ""))
        prefix = _color(f"[{step:02d}] {event}", _event_color(event))
        print(f"{prefix}: {message}")
        if verbose:
            data = update.get("data", {})
            print(json.dumps(data, ensure_ascii=False, indent=2, default=str))

    return on_update


def _print_summary(result: AgentRunResult) -> None:
    """Print a stable completion report suitable for demos and logs."""

    color = "green" if result.status == "completed" else "yellow"
    print()
    print(_color(f"状态：{result.status}", color))
    print(f"原因：{result.reason}")
    print(f"步骤：{result.state.step}")
    if result.state.changed_files:
        print("修改文件：" + ", ".join(sorted(result.state.changed_files)))
    print("最终输出：")
    print(result.answer)


def main(argv: Sequence[str] | None = None) -> int:
    """Validate configuration, run the agent, and return a process exit code."""

    args = build_parser().parse_args(argv)
    if args.gui:
        return run_gui()

    if args.workspace is None or args.task is None:
        print("CLI 模式需要 --workspace 和任务文本。", file=sys.stderr)
        return 2

    try:
        workspace = args.workspace.resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"工作区无效：{exc}", file=sys.stderr)
        return 2
    if not workspace.is_dir():
        print("工作区必须是目录。", file=sys.stderr)
        return 2

    try:
        provider = create_provider_from_env(args.provider)
        cfg = AgentConfig(
            workspace=workspace,
            provider=provider,
            max_steps=args.max_steps,
            max_wall_seconds=args.max_wall_seconds,
            input_token_budget=args.input_budget,
            mode=args.mode,
            interactive=args.interactive,
            verbose=args.verbose,
            confirm_write=_confirm_write if args.interactive else None,
        )
    except (ProviderConfigurationError, ValueError) as exc:
        print(f"配置错误：{exc}", file=sys.stderr)
        return 2

    try:
        result = run_agent(args.task, cfg, _build_update_printer(args.verbose))
    except KeyboardInterrupt:
        print("\n用户中断，Agent 已停止。", file=sys.stderr)
        return 130

    _print_summary(result)
    return 0 if result.status == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
