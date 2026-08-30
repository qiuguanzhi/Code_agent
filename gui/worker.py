"""QThread adapter between the framework-free Agent loop and PySide6."""

from __future__ import annotations

import json
import os
import threading
import time
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import QThread, Signal

from agent.loop import AgentConfig, AgentRunResult, run_agent
from providers.base import ModelProvider
from providers.openai_compatible import create_provider_from_env
from tools.filesystem import apply_staged_writes


ConfirmationCallback = Callable[[list[dict[str, Any]]], bool]


class AgentWorker(QThread):
    """Run one real Agent session and translate lifecycle events into signals."""

    log_signal = Signal(int, str, str, str, str)
    code_signal = Signal(str, str)
    diff_signal = Signal(str, str, int, int)
    status_signal = Signal(str, str)
    progress_signal = Signal(int, str)
    reasoning_signal = Signal(str, str)
    tool_status_signal = Signal(str, str, str)
    batch_confirmation_signal = Signal(object)
    snapshot_signal = Signal(object, str)
    stream_signal = Signal(str, str)
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
        self._stop_event = threading.Event()
        self.current_step = 0
        self._active_provider: ModelProvider | None = None
        self._reasoning_output_started = False
        self._reasoning_interruption_marked = False
        self._current_reasoning_text = ""
        self._reasoning_pending = ""
        self._last_reasoning_emit = 0.0
        self._reasoning_wait_timer: threading.Timer | None = None
        self._session_id = ""
        self._stream_pending = ""
        self._last_stream_emit = 0.0
        self._diagnostic_stage = ""
        self._diagnostic_started_at = 0.0
        self._diagnostic_generation = 0
        self._diagnostic_timer: threading.Timer | None = None

    def start_agent(
        self,
        task: str,
        workspace: Path | str,
        max_steps: int,
        interactive: bool,
        session_id: str = "",
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
        # Events are deliberately replaced, not merely cleared. A previous run
        # may still own a waiter reference while its queued Qt signals drain.
        self._confirmation_event = threading.Event()
        self._confirmation_result = False
        self._stop_event = threading.Event()
        self.current_step = 0
        self._reasoning_output_started = False
        self._reasoning_interruption_marked = False
        self._current_reasoning_text = ""
        self._reasoning_pending = ""
        self._last_reasoning_emit = 0.0
        self._cancel_reasoning_wait_hint()
        self._session_id = session_id
        self._stream_pending = ""
        self._last_stream_emit = 0.0
        self._end_diagnostic_stage()
        self._diagnostic(
            "阶段0: 重置Worker状态",
            workspace=str(resolved_workspace),
            conversation_id=session_id or "<cli>",
        )
        self.start()

    def stop(self) -> None:
        """Request cooperative cancellation and release any confirmation wait."""

        self._stop_event.set()
        self.requestInterruption()
        self._confirmation_result = False
        self._confirmation_event.set()
        cancel = getattr(self._active_provider, "cancel", None)
        if callable(cancel):
            try:
                cancel()
            except Exception:
                pass

    def is_stop_requested(self) -> bool:
        """Return whether the GUI requested this run to stop."""

        return self._stop_event.is_set() or self.isInterruptionRequested()

    def resolve_write_confirmation(self, confirmed: bool) -> None:
        """Release a worker waiting for a GUI write-confirmation decision."""

        self._confirmation_result = confirmed
        self._confirmation_event.set()

    def run(self) -> None:
        """Build the Provider/configuration and execute the Agent loop."""

        if self._workspace is None:
            self._finish_with_error("Agent Worker 尚未配置工作区。")
            return

        self.status_signal.emit("running", "阶段 1/5：⏳ 初始化模型连接...")
        try:
            self._begin_diagnostic_stage("阶段1: 创建 Provider")
            provider_started = time.perf_counter()
            provider = self.provider or create_provider_from_env(
                self.provider_name,
                mode=self.mode,
            )
            provider_duration_ms = (time.perf_counter() - provider_started) * 1_000
            self._report_slow_stage("Provider 初始化", provider_duration_ms)
            self._end_diagnostic_stage("阶段1: 创建 Provider")
            self._active_provider = provider
            set_stop_callback = getattr(provider, "set_stop_callback", None)
            if callable(set_stop_callback):
                set_stop_callback(self.is_stop_requested)
            self._begin_diagnostic_stage("阶段2: 构建配置")
            cfg = AgentConfig(
                workspace=self._workspace,
                provider=provider,
                max_steps=self._max_steps,
                mode=self.mode,
                interactive=self._interactive,
                batch_writes=self._interactive,
                confirm_write=None,
                should_stop=self.is_stop_requested,
                on_token=self._handle_stream_token,
                on_reasoning_token=(
                    self._handle_reasoning_token if self.mode == "goal" else None
                ),
            )
            self._end_diagnostic_stage("阶段2: 构建配置")
            snapshot_timestamp = datetime.now().astimezone().strftime(
                "%Y-%m-%d %H:%M:%S"
            )
            result = run_agent(self._task, cfg, self._handle_update)
        except Exception as exc:
            self._end_diagnostic_stage()
            self._flush_stream_tokens()
            self._mark_reasoning_interrupted()
            self._flush_reasoning_tokens()
            self._finish_with_error(f"{type(exc).__name__}: {exc}")
            return

        success = result.status == "completed"
        stopped_by_user = result.reason == "user_stopped"
        if self._interactive and result.state.pending_writes and not stopped_by_user:
            approved = self._await_batch_confirmation(result.state.pending_writes)
            if approved and not self.is_stop_requested():
                batch_result = apply_staged_writes(
                    self._workspace,
                    result.state.pending_writes,
                )
                if batch_result.get("ok") is True:
                    self._emit_applied_batch(result, batch_result)
                    self.tool_status_signal.emit("success", "批量修改", "")
                    result.answer = f"{result.answer}\n\n已批量应用 {len(result.state.pending_writes)} 个文件。"
                else:
                    result.status = "failed"
                    result.reason = "batch_apply_failed"
                    error = batch_result.get("error")
                    detail = error.get("message") if isinstance(error, Mapping) else "批量写入失败"
                    result.answer = str(detail)
            else:
                result.status = "stopped"
                result.reason = (
                    "user_stopped" if self.is_stop_requested() else "user_rejected_batch"
                )
                result.answer = (
                    "Agent 已按用户请求停止，暂存修改已丢弃。"
                    if self.is_stop_requested()
                    else "用户已拒绝全部暂存修改，工作区未改变。"
                )
                self.tool_status_signal.emit(
                    "cancelled",
                    "批量修改",
                    "用户已拒绝全部修改",
                )
            result.state.pending_writes.clear()
            success = result.status == "completed"
            stopped_by_user = result.reason == "user_stopped"
        elif stopped_by_user:
            result.state.pending_writes.clear()
        if (
            self.mode == "goal"
            and result.state.reasoning
            and not self._reasoning_output_started
        ):
            self._handle_reasoning_token(result.state.reasoning)
        summary = self._format_summary(result)
        self._flush_stream_tokens()
        self._flush_reasoning_tokens()
        self.snapshot_signal.emit(result.state.initial_snapshot, snapshot_timestamp)
        rejected_by_user = result.reason == "user_rejected_batch"
        self.status_signal.emit(
            "ready" if success or stopped_by_user or rejected_by_user else "error",
            "已停止"
            if stopped_by_user
            else "已拒绝全部修改"
            if rejected_by_user
            else result.answer,
        )
        self.finished_signal.emit(success, summary)
        self._active_provider = None
        self._cancel_reasoning_wait_hint()
        self._end_diagnostic_stage()

    def _await_batch_confirmation(
        self,
        pending_writes: list[dict[str, Any]],
    ) -> bool:
        """Publish one complete Diff batch and block for exactly one decision."""

        payload = [dict(item) for item in pending_writes]
        self._confirmation_result = False
        self._confirmation_event.clear()
        self.batch_confirmation_signal.emit(payload)
        self.status_signal.emit(
            "running",
            f"等待批量审批：{len(payload)} 个文件",
        )
        if self.confirmation_callback is not None:
            return bool(self.confirmation_callback(payload))
        while not self._confirmation_event.wait(timeout=0.1):
            if self.is_stop_requested():
                return False
        return self._confirmation_result

    def _emit_applied_batch(
        self,
        result: AgentRunResult,
        batch_result: Mapping[str, Any],
    ) -> None:
        """Reflect committed staged files in core state and GUI signals."""

        raw_results = batch_result.get("data")
        applied_results = raw_results if isinstance(raw_results, list) else []
        for index, entry in enumerate(result.state.pending_writes):
            path = str(entry.get("path", ""))
            content = str(entry.get("content", ""))
            diff_text = str(entry.get("diff", ""))
            applied = applied_results[index] if index < len(applied_results) else {}
            meta_value = applied.get("meta") if isinstance(applied, Mapping) else {}
            meta = meta_value if isinstance(meta_value, Mapping) else {}
            new_hash = meta.get("sha256")
            if isinstance(new_hash, str):
                result.state.changed_file_hashes[path] = new_hash
            result.state.changed_files[path] = diff_text
            self.code_signal.emit(path, content)
            additions, deletions = self._count_diff_changes(diff_text)
            raw_step = entry.get("step", result.state.step)
            step = raw_step if isinstance(raw_step, int) else result.state.step
            if bool(entry.get("created", False)):
                self.log_signal.emit(
                    step,
                    "📄",
                    "filesystem_create",
                    "success",
                    path,
                )
            else:
                self.log_signal.emit(
                    step,
                    "📝",
                    f"modified {Path(path).name} (+{additions} -{deletions})",
                    "success",
                    "",
                )

    def _handle_stream_token(self, delta: str) -> None:
        """Coalesce very small transport chunks before crossing Qt threads."""

        if not delta or self.is_stop_requested():
            return
        self._stream_pending += delta
        now = time.perf_counter()
        if now - self._last_stream_emit >= 0.04 or len(self._stream_pending) >= 256:
            self._flush_stream_tokens(now)

    def _handle_reasoning_token(self, delta: str) -> None:
        """Coalesce one provider-native reasoning delta for the owning session."""

        if self.mode != "goal" or not delta or self.is_stop_requested():
            return
        self._cancel_reasoning_wait_hint()
        if not self._current_reasoning_text and self._reasoning_output_started:
            self._reasoning_pending += "\n\n"
        self._current_reasoning_text += delta
        self._reasoning_pending += delta
        self._reasoning_output_started = True
        now = time.perf_counter()
        if now - self._last_reasoning_emit >= 0.04 or len(self._reasoning_pending) >= 256:
            self._flush_reasoning_tokens(now)

    def _flush_stream_tokens(self, now: float | None = None) -> None:
        """Emit all buffered text as one GUI update."""

        if not self._stream_pending:
            return
        delta = self._stream_pending
        self._stream_pending = ""
        self._last_stream_emit = now if now is not None else time.perf_counter()
        self.stream_signal.emit(self._session_id, delta)

    def _flush_reasoning_tokens(self, now: float | None = None) -> None:
        """Emit buffered reasoning at most about 25 times per second."""

        if not self._reasoning_pending:
            return
        delta = self._reasoning_pending
        self._reasoning_pending = ""
        self._last_reasoning_emit = now if now is not None else time.perf_counter()
        self.reasoning_signal.emit(self._session_id, delta)

    def _deliver_final_reasoning(self, reasoning: str) -> None:
        """Deliver only text not already seen through native streaming."""

        if self.mode != "goal" or not reasoning:
            return
        if not self._current_reasoning_text:
            self._handle_reasoning_token(reasoning)
            return
        if reasoning.startswith(self._current_reasoning_text):
            missing = reasoning[len(self._current_reasoning_text) :]
            if missing:
                self._handle_reasoning_token(missing)

    def _mark_reasoning_interrupted(self, *, retrying: bool = False) -> None:
        """Keep partial reasoning visible and append one explicit interruption marker."""

        if (
            self.mode != "goal"
            or not self._current_reasoning_text
            or self._reasoning_interruption_marked
        ):
            return
        marker = (
            "\n\n[推理流中断，正在重试…]\n\n"
            if retrying
            else "\n\n[推理流中断，已保留以上内容。]"
        )
        self._reasoning_pending += marker
        self._reasoning_interruption_marked = True
        self._flush_reasoning_tokens()

    def _start_reasoning_wait_hint(self) -> None:
        """Warn gently when a gateway has not produced native reasoning chunks."""

        self._cancel_reasoning_wait_hint()
        if self.mode != "goal":
            return
        try:
            delay = float(os.getenv("CEREBRO_REASONING_HINT_SECONDS", "2"))
        except ValueError:
            delay = 2.0
        delay = max(0.05, delay)

        def show_hint() -> None:
            if self._current_reasoning_text or self.is_stop_requested():
                return
            self.progress_signal.emit(
                0,
                "思考中，模型暂未提供推理片段，正在等待完整推理…",
            )

        self._reasoning_wait_timer = threading.Timer(delay, show_hint)
        self._reasoning_wait_timer.daemon = True
        self._reasoning_wait_timer.start()

    def _cancel_reasoning_wait_hint(self) -> None:
        """Cancel the no-stream hint after a chunk or terminal event arrives."""

        timer = self._reasoning_wait_timer
        self._reasoning_wait_timer = None
        if timer is not None:
            timer.cancel()

    def _report_slow_stage(self, stage: str, duration_ms: float) -> None:
        """Surface initialization bottlenecks that exceed the UX threshold."""

        if duration_ms <= 500:
            return
        message = f"{stage}耗时 {duration_ms / 1_000:.2f}s"
        self.log_signal.emit(0, "⏱", "性能", "warning", message)
        print(f"[Cerebro::Perf] {stage} duration_ms={duration_ms:.1f}")

    def _diagnostic(self, stage: str, **details: object) -> None:
        """Print one timestamped, secret-free lifecycle diagnostic line."""

        timestamp = datetime.now().astimezone().isoformat(timespec="milliseconds")
        session_id = self._session_id or "<cli>"
        rendered = " ".join(f"{key}={value}" for key, value in details.items())
        suffix = f" {rendered}" if rendered else ""
        print(
            f"[{timestamp}] [Cerebro::Worker] session={session_id} "
            f"{stage}{suffix}"
        )

    def _begin_diagnostic_stage(self, stage: str) -> None:
        """Start one measured stage and warn if it remains blocked too long."""

        if self._diagnostic_stage == stage:
            return
        self._end_diagnostic_stage()
        self._diagnostic_generation += 1
        generation = self._diagnostic_generation
        self._diagnostic_stage = stage
        self._diagnostic_started_at = time.perf_counter()
        self._diagnostic(stage, event="started")
        try:
            warning_seconds = float(os.getenv("CEREBRO_STAGE_WARN_SECONDS", "10"))
        except ValueError:
            warning_seconds = 10.0
        warning_seconds = max(1.0, warning_seconds)

        def warn_if_current() -> None:
            if (
                generation != self._diagnostic_generation
                or self._diagnostic_stage != stage
            ):
                return
            self._diagnostic(stage, event="slow", seconds=f"{warning_seconds:.1f}")
            self.log_signal.emit(
                self.current_step,
                "⚠",
                "诊断",
                "warning",
                f"{stage} 已耗时超过 {warning_seconds:.0f} 秒，仍在等待；可检查工作区规模或网络连接。",
            )

        self._diagnostic_timer = threading.Timer(warning_seconds, warn_if_current)
        self._diagnostic_timer.daemon = True
        self._diagnostic_timer.start()

    def _end_diagnostic_stage(self, expected_stage: str | None = None) -> None:
        """Finish the current measured stage and cancel its watchdog."""

        if expected_stage is not None and self._diagnostic_stage != expected_stage:
            return
        timer = self._diagnostic_timer
        self._diagnostic_timer = None
        if timer is not None:
            timer.cancel()
        if self._diagnostic_stage:
            duration_ms = (time.perf_counter() - self._diagnostic_started_at) * 1_000
            self._diagnostic(
                self._diagnostic_stage,
                event="finished",
                duration_ms=f"{duration_ms:.1f}",
            )
        self._diagnostic_stage = ""
        self._diagnostic_started_at = 0.0

    def _handle_update(self, update: dict[str, Any]) -> None:
        """Translate a core lifecycle dictionary into strongly shaped Qt signals."""

        event = str(update.get("event", "event"))
        step = self._safe_step(update.get("step"))
        self.current_step = step
        message = str(update.get("message", ""))
        data = update.get("data")
        event_data = data if isinstance(data, Mapping) else {}
        if self.mode == "goal":
            self._emit_safe_progress(event, step, event_data)
        else:
            self._emit_quick_progress(event, step, event_data)

        if event in {"run_preparing", "snapshot_started"}:
            self._begin_diagnostic_stage("阶段3: 保存快照")
            self.status_signal.emit("running", "阶段 2/5：⏳ 保存工作区快照...")
        elif event == "snapshot_progress":
            completed = event_data.get("completed", 0)
            total = event_data.get("total", 0)
            if isinstance(completed, int) and isinstance(total, int) and total > 0:
                self.status_signal.emit(
                    "running",
                    f"阶段 2/5：⏳ 保存工作区快照... {completed}/{total}",
                )
        elif event == "snapshot_completed":
            self._end_diagnostic_stage("阶段3: 保存快照")
            duration = event_data.get("duration_ms")
            if isinstance(duration, (int, float)):
                self._report_slow_stage("工作区快照", float(duration))
        elif event == "context_building":
            self._begin_diagnostic_stage("阶段4: 构建上下文")
            self.status_signal.emit("running", "阶段 3/5：⏳ 构建上下文...")
        elif event == "context_ready":
            self._end_diagnostic_stage("阶段4: 构建上下文")
            self.status_signal.emit("running", "阶段 4/5：⏳ 连接模型服务...")
            duration = event_data.get("duration_ms")
            if isinstance(duration, (int, float)):
                self._report_slow_stage("上下文构建", float(duration))
        elif event == "model_request":
            self._flush_reasoning_tokens()
            self._current_reasoning_text = ""
            self._reasoning_interruption_marked = False
            self._begin_diagnostic_stage("阶段5: 调用模型")
            self.status_signal.emit("running", "阶段 5/5：🧠 模型思考中...")
            if self.mode == "goal":
                self.progress_signal.emit(0, "思考中，等待模型返回实时推理…")
                self._start_reasoning_wait_hint()
        elif event == "model_response":
            self._cancel_reasoning_wait_hint()
            self._end_diagnostic_stage("阶段5: 调用模型")
            self._flush_stream_tokens()
            reasoning = event_data.get("reasoning")
            if self.mode == "goal" and isinstance(reasoning, str) and reasoning.strip():
                self._deliver_final_reasoning(reasoning)
                self._flush_reasoning_tokens()
        elif event == "api_retry":
            self._mark_reasoning_interrupted(retrying=True)
        elif event == "run_failed":
            self._cancel_reasoning_wait_hint()
            self._end_diagnostic_stage("阶段5: 调用模型")
            self._mark_reasoning_interrupted()
        elif event == "tool_call":
            self._end_diagnostic_stage("阶段5: 调用模型")
            tool_name = str(event_data.get("tool", "工具"))
            self.status_signal.emit("running", f"第 {step} 步：执行 {tool_name}")
            self.tool_status_signal.emit("running", tool_name, "")
        elif event == "tool_result":
            self._emit_tool_preview(event_data)
            self._emit_compact_tool_log(step, event_data)
            tool_name = str(event_data.get("tool", "unknown_tool"))
            result = event_data.get("result")
            error_code = self._result_error_code(result)
            if tool_name == "write_file" and error_code == "user_aborted":
                self.tool_status_signal.emit(
                    "cancelled",
                    tool_name,
                    "用户已拒绝修改",
                )
            elif isinstance(result, Mapping) and result.get("ok") is True:
                self.tool_status_signal.emit("success", tool_name, "")
            else:
                detail = json.dumps(
                    result,
                    ensure_ascii=False,
                    indent=2,
                    default=str,
                )
                self.tool_status_signal.emit("error", tool_name, detail)

    def _emit_safe_progress(
        self,
        event: str,
        step: int,
        event_data: Mapping[str, Any],
    ) -> None:
        """Expose high-level lifecycle summaries without private model reasoning."""

        summary = ""
        level = 0
        if event == "run_started":
            summary = "正在分析任务目标和工作区约束。"
        elif event == "model_request":
            summary = f"第 {step} 轮：检查当前上下文并选择下一步操作。"
        elif event == "model_response":
            count = event_data.get("tool_call_count", 0)
            summary = (
                f"第 {step} 轮：已形成操作方案，准备执行 {count} 个工具。"
                if isinstance(count, int) and count > 0
                else f"第 {step} 轮：已整理最终答复。"
            )
        elif event == "tool_call":
            tool_name = str(event_data.get("tool", "工具"))
            summary = f"第 {step} 轮：正在使用 {tool_name} 获取可验证结果。"
            level = 1
        elif event == "tool_result":
            tool_name = str(event_data.get("tool", "工具"))
            result = event_data.get("result")
            succeeded = isinstance(result, Mapping) and result.get("ok") is True
            outcome = "成功" if succeeded else "失败"
            summary = f"第 {step} 轮：{tool_name} 执行{outcome}，正在评估结果。"
            level = 2
        elif event == "api_retry":
            summary = f"第 {step} 轮：模型服务暂时不可用，正在按策略重试。"
            level = 1
        elif event == "step_completed":
            summary = f"第 {step} 轮：本轮操作和结果检查已完成。"
        if summary:
            self.progress_signal.emit(level, summary)

    def _emit_quick_progress(
        self,
        event: str,
        step: int,
        event_data: Mapping[str, Any],
    ) -> None:
        """Expose only a concise current-stage sentence in quick mode."""

        summary = ""
        level = 0
        if event == "run_started":
            summary = "正在分析问题…"
        elif event == "model_request":
            summary = "正在判断下一步操作…"
        elif event == "model_response":
            tool_count = event_data.get("tool_call_count", 0)
            summary = (
                "正在准备工具调用…"
                if isinstance(tool_count, int) and tool_count > 0
                else "正在整理回答…"
            )
        elif event == "tool_call":
            tool_name = str(event_data.get("tool", "工具"))
            summary = f"正在执行 {tool_name}…"
            level = 1
        elif event == "tool_result":
            tool_name = str(event_data.get("tool", "工具"))
            result = event_data.get("result")
            succeeded = isinstance(result, Mapping) and result.get("ok") is True
            summary = f"{tool_name} {'执行完成' if succeeded else '执行失败'}，正在检查结果…"
            level = 2
        elif event == "api_retry":
            summary = "模型服务暂时繁忙，正在重试…"
            level = 1
        elif event == "step_completed":
            summary = "本轮处理完成，正在继续分析…"
        elif event == "run_completed":
            summary = "任务已完成，正在生成结果…"
        if summary:
            self.progress_signal.emit(level, summary)

    def _emit_compact_tool_log(
        self,
        step: int,
        event_data: Mapping[str, Any],
    ) -> None:
        """Emit exactly one compact line for one completed tool call."""

        tool_name = str(event_data.get("tool", "unknown_tool"))
        result = event_data.get("result")
        if (
            tool_name == "write_file"
            and self._result_error_code(result) == "user_aborted"
        ):
            self.log_signal.emit(
                step,
                "↩",
                tool_name,
                "muted",
                "用户已拒绝修改",
            )
            return
        if isinstance(result, Mapping) and result.get("ok") is True:
            meta_value = result.get("meta")
            meta = meta_value if isinstance(meta_value, Mapping) else {}
            if meta.get("staged") is True:
                return
            file_path = str(meta.get("path", ""))
            if tool_name == "write_file" and meta.get("created") is True:
                self.log_signal.emit(
                    step,
                    "📄",
                    "filesystem_create",
                    "success",
                    file_path,
                )
                return
            if tool_name == "delete_file":
                self.log_signal.emit(
                    step,
                    "🗑️",
                    "filesystem_delete",
                    "warning",
                    file_path,
                )
                return
            self.log_signal.emit(step, "🔧", tool_name, "tool_success", "")
            return

        reason = "工具执行失败"
        if isinstance(result, Mapping):
            error = result.get("error")
            if isinstance(error, Mapping):
                raw_reason = error.get("message") or error.get("code")
                if isinstance(raw_reason, str) and raw_reason.strip():
                    reason = raw_reason.strip()
        if len(reason) > 120:
            reason = reason[:117] + "..."
        self.log_signal.emit(step, "❌", tool_name, "error", reason)

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
            # Agent reads are context-only. Opening tabs is reserved for user
            # actions and actual/new file writes.
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
    def _result_error_code(result: object) -> str | None:
        """Extract a structured tool error code without trusting result shape."""

        if not isinstance(result, Mapping):
            return None
        error = result.get("error")
        if not isinstance(error, Mapping):
            return None
        code = error.get("code")
        return code if isinstance(code, str) else None

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

        self._cancel_reasoning_wait_hint()
        self._end_diagnostic_stage()
        self.status_signal.emit("error", message)
        self.finished_signal.emit(False, message)
        self._active_provider = None
