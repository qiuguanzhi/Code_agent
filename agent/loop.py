"""The autonomous native-tool-calling agent loop."""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent.context import ContextBudgetError, fit_context
from agent.state import AgentState, AssistantTurn
from providers.base import ModelProvider
from tools.registry import ToolRegistry, ToolStopRequested
from utils.snapshot import save_workspace_snapshot


UpdateCallback = Callable[[dict[str, Any]], None]
ConfirmWriteCallback = Callable[[str], bool]
SleepCallback = Callable[[float], None]
StopCallback = Callable[[], bool]
TokenCallback = Callable[[str], None]
ReasoningTokenCallback = Callable[[str], None]


class ModelRetryExhausted(RuntimeError):
    """Raised after all configured transient API retries fail."""


class AgentStopRequested(RuntimeError):
    """Raised at a cooperative checkpoint after a user stop request."""


@dataclass(slots=True)
class AgentConfig:
    """Runtime limits and injected dependencies for one agent run."""

    workspace: Path
    provider: ModelProvider
    max_steps: int = 20
    max_wall_seconds: float = 600.0
    input_token_budget: int = 48_000
    max_api_attempts: int = 3
    retry_base_seconds: float = 0.5
    max_same_call: int = 3
    mode: str = "auto"
    interactive: bool = False
    batch_writes: bool = False
    verbose: bool = False
    confirm_write: ConfirmWriteCallback | None = None
    sleep_fn: SleepCallback = field(default=time.sleep, repr=False)
    should_stop: StopCallback | None = field(default=None, repr=False)
    on_token: TokenCallback | None = field(default=None, repr=False)
    on_reasoning_token: ReasoningTokenCallback | None = field(default=None, repr=False)
    system_prompt_path: Path | None = None

    def __post_init__(self) -> None:
        """Validate limits and normalize the workspace."""

        self.workspace = self.workspace.resolve(strict=True)
        if not self.workspace.is_dir():
            raise ValueError("workspace must be a directory")
        if self.max_steps < 1:
            raise ValueError("max_steps must be positive")
        if self.max_wall_seconds <= 0:
            raise ValueError("max_wall_seconds must be positive")
        if self.input_token_budget <= 0:
            raise ValueError("input_token_budget must be positive")
        if self.max_api_attempts < 1:
            raise ValueError("max_api_attempts must be positive")
        if self.retry_base_seconds < 0:
            raise ValueError("retry_base_seconds cannot be negative")
        if self.interactive and not self.batch_writes and self.confirm_write is None:
            raise ValueError("interactive mode requires confirm_write")


@dataclass(slots=True)
class AgentRunResult:
    """Structured terminal state returned by ``run_agent``."""

    status: str
    reason: str
    answer: str
    state: AgentState


def _emit(
    callback: UpdateCallback | None,
    event: str,
    step: int,
    message: str,
    **data: Any,
) -> None:
    """Send a non-fatal lifecycle notification to a CLI or future GUI."""

    if callback is None:
        return
    payload = {"event": event, "step": step, "message": message, "data": data}
    try:
        callback(payload)
    except Exception:
        return


def _is_retriable_exception(exc: Exception) -> bool:
    """Classify transient transport, rate-limit, and server failures."""

    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True
    status_code = getattr(exc, "status_code", None)
    if status_code == 429 or isinstance(status_code, int) and status_code >= 500:
        return True
    return type(exc).__name__ in {
        "APIConnectionError",
        "APITimeoutError",
        "InternalServerError",
        "RateLimitError",
    }


def call_model_with_retry(
    cfg: AgentConfig,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    on_update: UpdateCallback | None = None,
    *,
    step: int = 0,
) -> AssistantTurn:
    """Call a provider with bounded exponential backoff."""

    for attempt in range(1, cfg.max_api_attempts + 1):
        if cfg.should_stop is not None and cfg.should_stop():
            raise AgentStopRequested("user requested stop")
        try:
            if cfg.on_token is not None or cfg.on_reasoning_token is not None:
                content_callback = cfg.on_token or (lambda _delta: None)
                return cfg.provider.complete_stream(
                    messages,
                    tools,
                    content_callback,
                    cfg.on_reasoning_token,
                )
            return cfg.provider.complete(messages, tools)
        except Exception as exc:
            if cfg.should_stop is not None and cfg.should_stop():
                raise AgentStopRequested("user requested stop") from exc
            if not _is_retriable_exception(exc) or attempt >= cfg.max_api_attempts:
                raise ModelRetryExhausted(str(exc)) from exc
            delay = cfg.retry_base_seconds * (2 ** (attempt - 1))
            _emit(
                on_update,
                "api_retry",
                step,
                f"模型请求失败，{delay:.2f} 秒后重试",
                attempt=attempt,
                error_type=type(exc).__name__,
            )
            cfg.sleep_fn(delay)
    raise ModelRetryExhausted("model retry loop ended unexpectedly")


def _load_system_prompt(cfg: AgentConfig) -> str:
    """Load the prompt and activate the reserved goal-mode block when needed."""

    prompt_path = cfg.system_prompt_path or Path(__file__).resolve().parents[1] / "prompts" / "system.md"
    prompt = prompt_path.read_text(encoding="utf-8")
    start_marker = "<!-- GOAL_MODE_PROMPT_START"
    end_marker = "GOAL_MODE_PROMPT_END -->"
    if start_marker not in prompt or end_marker not in prompt:
        return prompt
    before, remainder = prompt.split(start_marker, maxsplit=1)
    goal_block, after = remainder.split(end_marker, maxsplit=1)
    if cfg.mode == "goal":
        return before + goal_block.strip() + after
    return before + after


def _forbidden_tools_from_task(task: str) -> set[str]:
    """Extract only explicit tool prohibitions from the user's own wording.

    This intentionally avoids guessing scope from vague requests. It provides
    a deterministic backstop when a user explicitly says not to inspect/read
    existing files, while the system prompt handles broader plan adherence.
    """

    chinese_no_read = re.compile(
        r"(?:不要|禁止|无需|不需要|请勿|别)\s*"
        r"(?:读取|查看|检查|打开|扫描)\s*"
        r"(?:任何|全部|所有)?\s*(?:现有|已有|工作区(?:中|内)?)?\s*文件"
    )
    english_no_read = re.compile(
        r"\b(?:do\s+not|don't|never)\s+"
        r"(?:read|inspect|open|scan)\s+(?:any\s+)?(?:existing\s+)?files?\b",
        re.IGNORECASE,
    )
    if chinese_no_read.search(task) or english_no_read.search(task):
        return {"read_file"}
    return set()


def _decode_result(encoded: str) -> dict[str, Any]:
    """Decode an internally generated tool result for state updates."""

    try:
        result = json.loads(encoded)
    except json.JSONDecodeError:
        return {"ok": False, "error": {"code": "invalid_internal_tool_result"}}
    return result if isinstance(result, dict) else {"ok": False}


def _stopped(
    status: str,
    reason: str,
    answer: str,
    state: AgentState,
) -> AgentRunResult:
    """Build a terminal run result."""

    return AgentRunResult(status=status, reason=reason, answer=answer, state=state)


def _user_stopped(state: AgentState, on_update: UpdateCallback | None) -> AgentRunResult:
    """Build and emit the common cooperative user-stop result."""

    answer = "Agent 已按用户请求停止。"
    _emit(on_update, "run_stopped", state.step, answer, reason="user_stopped")
    return _stopped("stopped", "user_stopped", answer, state)


def run_agent(
    task: str,
    cfg: AgentConfig,
    on_update: UpdateCallback | None = None,
) -> AgentRunResult:
    """Run the model → local tool → observation loop until a hard stop."""

    if not task.strip():
        raise ValueError("task must be a non-empty string")

    state = AgentState(mode=cfg.mode)
    _emit(on_update, "run_preparing", 0, "正在初始化 Agent 环境")
    _emit(on_update, "snapshot_started", 0, "正在保存工作区快照")
    snapshot_started = time.perf_counter()

    def report_snapshot_progress(completed: int, total: int, path: str) -> None:
        """Forward bounded snapshot progress without exposing backup paths."""

        _emit(
            on_update,
            "snapshot_progress",
            0,
            "正在扫描工作区文件",
            completed=completed,
            total=total,
            path=path,
        )

    try:
        state.initial_snapshot = save_workspace_snapshot(
            cfg.workspace,
            progress_callback=report_snapshot_progress,
        )
    except (OSError, ValueError) as exc:
        answer = str(exc)
        _emit(on_update, "run_failed", 0, answer, reason="snapshot_error")
        return _stopped("failed", "snapshot_error", answer, state)
    snapshot_duration_ms = (time.perf_counter() - snapshot_started) * 1_000
    snapshot_file_count = max(0, len(state.initial_snapshot) - 1)
    _emit(
        on_update,
        "snapshot_completed",
        0,
        "工作区快照已保存",
        duration_ms=snapshot_duration_ms,
        file_count=snapshot_file_count,
    )
    if cfg.verbose or snapshot_duration_ms > 500:
        print(
            f"[perf] snapshot files={snapshot_file_count} "
            f"duration_ms={snapshot_duration_ms:.1f}"
        )

    prompt_started = time.perf_counter()
    state.messages = [
        {"role": "system", "content": _load_system_prompt(cfg)},
        {"role": "user", "content": task},
    ]
    prompt_duration_ms = (time.perf_counter() - prompt_started) * 1_000
    registry_started = time.perf_counter()
    registry = ToolRegistry(
        cfg.workspace,
        write_policy={
            "require_confirmation": cfg.interactive and not cfg.batch_writes
        },
        max_same_call=cfg.max_same_call,
        confirm_write=cfg.confirm_write,
        should_stop=cfg.should_stop,
        defer_writes=cfg.batch_writes,
        forbidden_tools=_forbidden_tools_from_task(task),
    )
    registry_duration_ms = (time.perf_counter() - registry_started) * 1_000
    _emit(
        on_update,
        "registry_ready",
        0,
        "工具注册表已就绪",
        duration_ms=registry_duration_ms,
        tool_count=len(registry.schemas),
    )
    for stage_name, duration_ms in (
        ("system_prompt", prompt_duration_ms),
        ("tool_registry", registry_duration_ms),
    ):
        if cfg.verbose or duration_ms > 500:
            print(f"[perf] {stage_name} duration_ms={duration_ms:.1f}")
    _emit(on_update, "run_started", 0, "Agent 已启动", mode=cfg.mode)

    for step in range(1, cfg.max_steps + 1):
        state.step = step
        if cfg.should_stop is not None and cfg.should_stop():
            return _user_stopped(state, on_update)
        elapsed = time.monotonic() - state.started_at
        if elapsed > cfg.max_wall_seconds:
            answer = f"Agent 已达到墙钟时间上限（{cfg.max_wall_seconds:.0f} 秒）。"
            _emit(on_update, "run_stopped", step, answer, reason="wall_time_limit")
            return _stopped("stopped", "wall_time_limit", answer, state)

        _emit(
            on_update,
            "context_building",
            step,
            "正在构建模型上下文",
            source_messages=len(state.messages),
        )
        context_started = time.perf_counter()
        try:
            request_messages = fit_context(
                state.messages,
                registry.schemas,
                cfg.input_token_budget,
            )
        except ContextBudgetError as exc:
            answer = str(exc)
            _emit(on_update, "run_failed", step, answer, reason="context_budget_error")
            return _stopped("failed", "context_budget_error", answer, state)

        context_duration_ms = (time.perf_counter() - context_started) * 1_000
        _emit(
            on_update,
            "context_ready",
            step,
            "模型上下文已就绪，准备连接服务",
            duration_ms=context_duration_ms,
            messages=len(request_messages),
        )
        if cfg.verbose or context_duration_ms > 500:
            print(
                f"[perf] context step={step} messages={len(request_messages)} "
                f"duration_ms={context_duration_ms:.1f}"
            )

        _emit(on_update, "model_request", step, "正在请求模型", messages=len(request_messages))
        model_started = time.perf_counter()
        try:
            turn = call_model_with_retry(
                cfg,
                request_messages,
                registry.schemas,
                on_update,
                step=step,
            )
        except AgentStopRequested:
            return _user_stopped(state, on_update)
        except ModelRetryExhausted as exc:
            answer = f"模型 API 请求失败：{exc}"
            _emit(on_update, "run_failed", step, answer, reason="model_api_error")
            return _stopped("failed", "model_api_error", answer, state)

        if cfg.should_stop is not None and cfg.should_stop():
            return _user_stopped(state, on_update)

        model_duration_ms = (time.perf_counter() - model_started) * 1_000
        if cfg.verbose or model_duration_ms > 500:
            print(f"[perf] model step={step} duration_ms={model_duration_ms:.1f}")

        state.messages.append(turn.protocol_message)
        reasoning = turn.protocol_message.get("reasoning_content")
        if cfg.mode == "goal" and isinstance(reasoning, str) and reasoning.strip():
            separator = "\n\n" if state.reasoning else ""
            state.reasoning += separator + reasoning.strip()
        _emit(
            on_update,
            "model_response",
            step,
            "模型响应已接收",
            tool_call_count=len(turn.tool_calls),
            reasoning=reasoning if isinstance(reasoning, str) else None,
            duration_ms=model_duration_ms,
        )

        if not turn.tool_calls:
            if turn.content and turn.content.strip():
                _emit(on_update, "run_completed", step, "Agent 已完成任务")
                return _stopped("completed", "final_answer", turn.content, state)
            answer = "模型未返回文本或工具调用，Agent 已停止。"
            _emit(on_update, "run_stopped", step, answer, reason="empty_response")
            return _stopped("stopped", "empty_response", answer, state)

        for call in turn.tool_calls:
            if cfg.should_stop is not None and cfg.should_stop():
                return _user_stopped(state, on_update)
            _emit(
                on_update,
                "tool_call",
                step,
                f"调用工具：{call.name}",
                tool=call.name,
                tool_call_id=call.id,
                arguments_json=call.arguments_json,
            )
            tool_started = time.perf_counter()
            try:
                encoded_result = registry.execute_one_call(call, state)
            except ToolStopRequested:
                return _user_stopped(state, on_update)
            state.messages.append(
                {"role": "tool", "tool_call_id": call.id, "content": encoded_result}
            )
            result = _decode_result(encoded_result)
            tool_duration_ms = (time.perf_counter() - tool_started) * 1_000
            if cfg.verbose or tool_duration_ms > 500:
                print(
                    f"[perf] tool={call.name} step={step} "
                    f"duration_ms={tool_duration_ms:.1f}"
                )
            if call.name == "run_command":
                state.last_test_result = encoded_result
            _emit(
                on_update,
                "tool_result",
                step,
                f"工具完成：{call.name}",
                tool=call.name,
                tool_call_id=call.id,
                ok=result.get("ok"),
                result=result,
                duration_ms=tool_duration_ms,
            )

            if cfg.should_stop is not None and cfg.should_stop():
                return _user_stopped(state, on_update)

            error = result.get("error")
            error_code = error.get("code") if isinstance(error, Mapping) else None
            if error_code == "repeated_call_limit":
                answer = "检测到重复工具调用，Agent 已停止以避免死循环。"
                _emit(on_update, "run_stopped", step, answer, reason=error_code)
                return _stopped("stopped", error_code, answer, state)

            if time.monotonic() - state.started_at > cfg.max_wall_seconds:
                answer = f"Agent 已达到墙钟时间上限（{cfg.max_wall_seconds:.0f} 秒）。"
                _emit(on_update, "run_stopped", step, answer, reason="wall_time_limit")
                return _stopped("stopped", "wall_time_limit", answer, state)

        _emit(on_update, "step_completed", step, f"步骤 {step} 已完成")

    answer = f"Agent 已达到最大步骤数（{cfg.max_steps}），任务可能尚未完成。"
    _emit(on_update, "run_stopped", cfg.max_steps, answer, reason="max_steps")
    return _stopped("stopped", "max_steps", answer, state)
