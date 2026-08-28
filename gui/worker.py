"""QThread adapter between the framework-free Agent loop and PySide6."""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from PySide6.QtCore import QThread, Signal

from agent.loop import AgentConfig, AgentRunResult, run_agent
from providers.base import ModelProvider
from providers.openai_compatible import create_provider_from_env
from tools.filesystem import resolve_in_workspace
from utils.diff import generate_unified_diff, truncate_diff


ConfirmationCallback = Callable[[str], bool]


EVENT_PRESENTATION: dict[str, tuple[str, str, str]] = {
    "run_started": ("🚀", "开始", "#89b4fa"),
    "model_request": ("🧠", "思考", "#cba6f7"),
    "model_response": ("💬", "响应", "#cba6f7"),
    "api_retry": ("↻", "重试", "#f9e2af"),
    "tool_call": ("🔧", "工具", "#f9e2af"),
    "tool_result": ("✓", "结果", "#a6e3a1"),
    "step_completed": ("✓", "步骤", "#89b4fa"),
    "run_completed": ("✅", "完成", "#a6e3a1"),
    "run_stopped": ("■", "停止", "#f9e2af"),
    "run_failed": ("✕", "错误", "#f38ba8"),
}


class AgentWorker(QThread):
    """Run one real Agent session and translate lifecycle events into signals."""

    log_signal = Signal(int, str, str, str, str)
    code_signal = Signal(str, str)
    diff_signal = Signal(str, str, int, int)
    status_signal = Signal(str, str)
    confirmation_signal = Signal(str)
    snapshot_signal = Signal(object)
    finished_signal = Signal(bool, str)

    def __init__(
        self,
        *,
        provider: ModelProvider | None = None,
        provider_name: str | None = None,
        mode: str = "auto",
        confirmation_callback: ConfirmationCallback | None = None,
    ) -> None:
        """Create a worker with optional network-free test dependencies."""

        super().__init__()
        self.provider = provider
        self.provider_name = provider_name or os.getenv("AGENT_PROVIDER", "deepseek")
        self.mode = mode
        self.confirmation_callback = confirmation_callback
        self._task = ""
        self._workspace: Path | None = None
        self._max_steps = 20
        self._interactive = False
        self._confirmation_event = threading.Event()
        self._confirmation_result = False
        self._prepared_confirmation_path: str | None = None

    def start_agent(
        self,
        task: str,
        workspace: Path | str,
        max_steps: int,
        interactive: bool,
    ) -> None:
        """Validate and start one configured Agent run in this QThread."""

        if self.isRunning():
            raise RuntimeError("Agent worker is already running")
        if not task.strip():
            raise ValueError("task must be a non-empty string")
        if max_steps < 1:
            raise ValueError("max_steps must be positive")
        try:
            resolved_workspace = Path(workspace).resolve(strict=True)
        except (OSError, RuntimeError, ValueError) as exc:
            raise ValueError("workspace cannot be resolved") from exc
        if not resolved_workspace.is_dir():
            raise ValueError("workspace must be a directory")

        self._task = task
        self._workspace = resolved_workspace
        self._max_steps = max_steps
        self._interactive = interactive
        self._confirmation_event.clear()
        self._confirmation_result = False
        self.start()

    def resolve_write_confirmation(self, confirmed: bool) -> None:
        """Release a worker waiting for a GUI write-confirmation decision."""

        self._confirmation_result = confirmed
        self._confirmation_event.set()

    def run(self) -> None:
        """Build the Provider/configuration and execute the Agent loop."""

        if self._workspace is None:
            self._finish_with_error("Agent Worker 尚未配置工作区。")
            return

        self.status_signal.emit("running", "Agent 正在运行")
        try:
            provider = self.provider or create_provider_from_env(self.provider_name)
            cfg = AgentConfig(
                workspace=self._workspace,
                provider=provider,
                max_steps=self._max_steps,
                mode=self.mode,
                interactive=self._interactive,
                confirm_write=self._confirm_write if self._interactive else None,
            )
            result = run_agent(self._task, cfg, self._handle_update)
        except Exception as exc:
            self._finish_with_error(f"{type(exc).__name__}: {exc}")
            return

        success = result.status == "completed"
        summary = self._format_summary(result)
        self.snapshot_signal.emit(result.state.initial_snapshot)
        self.status_signal.emit("ready" if success else "error", result.answer)
        self.finished_signal.emit(success, summary)

    def _confirm_write(self, path: str) -> bool:
        """Obtain a write decision from an injected callback or the GUI thread."""

        if self._prepared_confirmation_path == path:
            confirmed = self._confirmation_result
            self._prepared_confirmation_path = None
            return confirmed

        if self.confirmation_callback is not None:
            return bool(self.confirmation_callback(path))

        self._confirmation_result = False
        self._confirmation_event.clear()
        self.confirmation_signal.emit(path)
        while not self._confirmation_event.wait(timeout=0.1):
            if self.isInterruptionRequested():
                return False
        return self._confirmation_result

    def _handle_update(self, update: dict[str, Any]) -> None:
        """Translate a core lifecycle dictionary into strongly shaped Qt signals."""

        event = str(update.get("event", "event"))
        step = self._safe_step(update.get("step"))
        message = str(update.get("message", ""))
        data = update.get("data")
        event_data = data if isinstance(data, Mapping) else {}

        icon, label, color = EVENT_PRESENTATION.get(
            event,
            ("•", event, "#89b4fa"),
        )
        if event == "tool_result" and event_data.get("ok") is False:
            icon, color = "✕", "#f38ba8"
        self.log_signal.emit(step, icon, label, color, message)

        if event == "model_request":
            self.status_signal.emit("running", f"第 {step} 步：模型思考中")
        elif event == "tool_call":
            tool_name = str(event_data.get("tool", "工具"))
            self.status_signal.emit("running", f"第 {step} 步：执行 {tool_name}")
            if tool_name == "write_file" and self._interactive:
                self._prepare_interactive_write(event_data, step)
        elif event == "tool_result":
            self._emit_tool_preview(event_data)

    def _prepare_interactive_write(
        self,
        event_data: Mapping[str, Any],
        step: int,
    ) -> None:
        """Generate a pre-write Diff and block until the user decides."""

        if self._workspace is None:
            return
        raw_arguments = event_data.get("arguments_json")
        if not isinstance(raw_arguments, str):
            return
        try:
            arguments = json.loads(raw_arguments)
        except json.JSONDecodeError:
            return
        if not isinstance(arguments, dict):
            return
        path = arguments.get("path")
        new_content = arguments.get("content")
        if not isinstance(path, str) or not isinstance(new_content, str):
            return

        try:
            target = resolve_in_workspace(self._workspace, path, must_exist=False)
            original_content = (
                target.read_text(encoding="utf-8") if target.exists() else ""
            )
            diff_text = truncate_diff(
                generate_unified_diff(original_content, new_content, path)
            )
        except (OSError, RuntimeError, UnicodeError, ValueError) as exc:
            self.log_signal.emit(
                step,
                "✕",
                "Diff",
                "#f38ba8",
                f"无法生成写入预览，已拒绝修改：{exc}",
            )
            self._confirmation_result = False
            self._prepared_confirmation_path = path
            return

        additions, deletions = self._count_diff_changes(diff_text)
        self._confirmation_result = False
        self._confirmation_event.clear()
        self.diff_signal.emit(path, diff_text, additions, deletions)
        self.confirmation_signal.emit(path)
        self.status_signal.emit("running", f"等待确认修改：{path}")

        if self.confirmation_callback is not None:
            self._confirmation_result = bool(self.confirmation_callback(path))
        else:
            while not self._confirmation_event.wait(timeout=0.1):
                if self.isInterruptionRequested():
                    self._confirmation_result = False
                    break
        self._prepared_confirmation_path = path

    def _emit_tool_preview(self, event_data: Mapping[str, Any]) -> None:
        """Emit code or Diff previews from one successful tool-result payload."""

        result = event_data.get("result")
        if not isinstance(result, Mapping) or result.get("ok") is not True:
            return
        tool_name = event_data.get("tool")
        meta_value = result.get("meta")
        meta = meta_value if isinstance(meta_value, Mapping) else {}
        file_path = str(meta.get("path", ""))

        if tool_name == "read_file":
            content = result.get("data")
            if file_path and isinstance(content, str):
                self.code_signal.emit(file_path, content)
            return

        if tool_name == "write_file":
            if self._interactive:
                return
            diff_text = meta.get("diff")
            if file_path and isinstance(diff_text, str):
                additions, deletions = self._count_diff_changes(diff_text)
                self.diff_signal.emit(file_path, diff_text, additions, deletions)

    @staticmethod
    def _safe_step(value: Any) -> int:
        """Normalize an untrusted callback step without raising in a Qt slot."""

        return value if isinstance(value, int) and not isinstance(value, bool) else 0

    @staticmethod
    def _count_diff_changes(diff_text: str) -> tuple[int, int]:
        """Count added and removed content lines, excluding Diff headers."""

        additions = 0
        deletions = 0
        for line in diff_text.splitlines():
            if line.startswith("+++") or line.startswith("---"):
                continue
            if line.startswith("+"):
                additions += 1
            elif line.startswith("-"):
                deletions += 1
        return additions, deletions

    @staticmethod
    def _format_summary(result: AgentRunResult) -> str:
        """Create a compact completion summary for the GUI."""

        changed = ", ".join(sorted(result.state.changed_files)) or "无"
        return (
            f"{result.answer}\n\n"
            f"状态：{result.status}\n"
            f"原因：{result.reason}\n"
            f"步骤：{result.state.step}\n"
            f"修改文件：{changed}"
        )

    def _finish_with_error(self, message: str) -> None:
        """Emit a stable terminal error without leaking an exception from QThread."""

        self.log_signal.emit(0, "✕", "错误", "#f38ba8", message)
        self.status_signal.emit("error", message)
        self.finished_signal.emit(False, message)
