"""The autonomous native-tool-calling agent loop."""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent.context import (
    MAX_INPUT_TOKENS,
    ContextBudgetError,
    ContextUsage,
    compress_tool_history,
    estimate_context_tokens,
    fit_context,
)
from agent.state import AgentState, AssistantTurn
from providers.base import ModelProvider
from skills.registry import SkillRegistry
from tools.registry import ToolRegistry, ToolStopRequested
from utils.snapshot import save_workspace_snapshot


UpdateCallback = Callable[[dict[str, Any]], None]
ConfirmWriteCallback = Callable[[str], bool]
SleepCallback = Callable[[float], None]
StopCallback = Callable[[], bool]
TokenCallback = Callable[[str], None]
ReasoningTokenCallback = Callable[[str], None]
StepExtensionCallback = Callable[[int, int, int], bool]
ClockCallback = Callable[[], float]


class ModelRetryExhausted(RuntimeError):
    """Raised after all configured transient API retries fail."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "model_api_error",
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.details = dict(details or {})


class AgentStopRequested(RuntimeError):
    """Raised at a cooperative checkpoint after a user stop request."""


@dataclass(slots=True)
class AgentConfig:
    """Runtime limits and injected dependencies for one agent run."""

    workspace: Path
    provider: ModelProvider
    max_steps: int = 200
    max_duration_seconds: float = 1_200.0
    max_wall_seconds: float | None = None
    input_token_budget: int = MAX_INPUT_TOKENS
    max_api_attempts: int = 3
    retry_base_seconds: float = 0.5
    max_empty_response_retries: int = 2
    empty_response_retry_base_seconds: float = 1.0
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
    confirm_step_extension: StepExtensionCallback | None = field(default=None, repr=False)
    enabled_skills: frozenset[str] | None = None
    skill_permissions: frozenset[str] = frozenset({"filesystem"})
    confirm_high_risk_skill: Callable[[Any], bool] | None = field(default=None, repr=False)
    skill_registry: SkillRegistry | None = field(default=None, repr=False)
    time_fn: ClockCallback = field(default=time.time, repr=False)
    system_prompt_path: Path | None = None

    def __post_init__(self) -> None:
        """Validate limits and normalize the workspace."""

        self.workspace = self.workspace.resolve(strict=True)
        if not self.workspace.is_dir():
            raise ValueError("workspace must be a directory")
        if self.max_steps < 1:
            raise ValueError("max_steps must be positive")
        if self.max_wall_seconds is not None:
            self.max_duration_seconds = self.max_wall_seconds
        if self.max_duration_seconds <= 0:
            raise ValueError("max_duration_seconds must be positive")
        if self.input_token_budget <= 0:
            raise ValueError("input_token_budget must be positive")
        if self.max_api_attempts < 1:
            raise ValueError("max_api_attempts must be positive")
        if self.retry_base_seconds < 0:
            raise ValueError("retry_base_seconds cannot be negative")
        if self.max_empty_response_retries < 0:
            raise ValueError("max_empty_response_retries cannot be negative")
        if self.empty_response_retry_base_seconds < 0:
            raise ValueError("empty_response_retry_base_seconds cannot be negative")
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


def _exception_code(exc: Exception) -> str:
    """Preserve provider protocol codes and classify transport timeouts."""

    code = getattr(exc, "code", None)
    if isinstance(code, str) and code:
        return code
    if isinstance(exc, TimeoutError) or type(exc).__name__ in {
        "APITimeoutError",
        "ReadTimeout",
    }:
        return "api_timeout"
    return "model_api_error"


def _message_diagnostics(messages: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
    """Return bounded last-message summaries and protocol-shape anomalies."""

    summaries: list[str] = []
    anomalies: list[str] = []
    for index, message in enumerate(messages):
        role = str(message.get("role", "<missing>"))
        content = message.get("content")
        if isinstance(content, str):
            preview = content.replace("\n", " ")[:50]
        elif content is None:
            preview = "<null>"
        else:
            preview = f"<{type(content).__name__}>"
            anomalies.append(f"message[{index}] content has unsupported type")
        if role == "assistant" and (content is None or content == "") and not message.get(
            "tool_calls"
        ):
            anomalies.append(f"message[{index}] empty assistant protocol message")
        if role not in {"system", "user", "assistant", "tool"}:
            anomalies.append(f"message[{index}] invalid role={role}")
        summaries.append(f"{role}: {preview}")
    return summaries[-3:], anomalies


def _friendly_model_error(code: str, *, retry_count: int = 0) -> str:
    """Map internal diagnostics to stable user-facing Chinese messages."""

    if code == "api_empty_choices":
        return "⚠️ 模型服务未返回有效响应，请检查网络连接或稍后重试。"
    if code == "api_missing_message":
        return "⚠️ 模型响应缺少消息主体，请检查兼容网关或模型配置。"
    if code == "empty_content_and_tools":
        return f"⚠️ 模型返回了空响应，可能因上下文过长或格式问题，已自动重试 {retry_count} 次。"
    if code == "tool_calls_parse_error":
        return "⚠️ 模型返回的工具调用格式异常，请检查模型配置。"
    if code == "api_timeout":
        return "⏱️ 模型响应超时，当前任务较复杂，请尝试简化问题或稍后重试。"
    return "⚠️ 模型服务请求失败，请查看事件日志中的诊断信息。"


def _record_model_error(
    state: AgentState,
    code: str,
    message: str,
    *,
    step: int,
    details: Mapping[str, Any] | None = None,
) -> None:
    """Persist a bounded, structured model diagnostic on the run state."""

    state.last_error_code = code
    state.error_history.append(
        {
            "step": step,
            "code": code,
            "message": message,
            "details": dict(details or {}),
        }
    )
    if len(state.error_history) > 50:
        del state.error_history[:-50]


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
                details = getattr(exc, "details", None)
                raise ModelRetryExhausted(
                    str(exc),
                    code=_exception_code(exc),
                    details=details if isinstance(details, Mapping) else None,
                ) from exc
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


def _duration_stopped(
    state: AgentState,
    cfg: AgentConfig,
    on_update: UpdateCallback | None,
) -> AgentRunResult:
    """Build the stable hard-duration terminal result."""

    if abs(cfg.max_duration_seconds - 1_200.0) < 0.001:
        answer = "⏱️ 执行超时（超过 20 分钟）"
    else:
        answer = f"⏱️ 执行超时（超过 {cfg.max_duration_seconds:g} 秒）"
    _emit(on_update, "run_stopped", state.step, answer, reason="max_duration")
    return _stopped("stopped", "max_duration", answer, state)


def run_agent(
    task: str,
    cfg: AgentConfig,
    on_update: UpdateCallback | None = None,
) -> AgentRunResult:
    """Run the model → local tool → observation loop until a hard stop."""

    if not task.strip():
        raise ValueError("task must be a non-empty string")

    state = AgentState(
        mode=cfg.mode,
        start_time=cfg.time_fn(),
        max_duration_seconds=cfg.max_duration_seconds,
    )
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
    skill_registry = cfg.skill_registry or SkillRegistry.discover_all(
        granted_permissions=cfg.skill_permissions,
        enabled_names=cfg.enabled_skills,
        confirm_high_risk=cfg.confirm_high_risk_skill,
    )

    def emit_skill_update(event: str, message: str, data: dict[str, Any]) -> None:
        """Bridge framework-free Skill lifecycle events to the Agent callback."""

        _emit(on_update, event, state.step, message, **data)

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
        skill_registry=skill_registry,
        on_skill_update=emit_skill_update,
    )
    registry_duration_ms = (time.perf_counter() - registry_started) * 1_000
    _emit(
        on_update,
        "registry_ready",
        0,
        "工具注册表已就绪",
        duration_ms=registry_duration_ms,
        tool_count=len(registry.schemas),
        skill_count=len(skill_registry.enabled_names()),
    )
    provider_limit = getattr(cfg.provider, "max_input_tokens", cfg.input_token_budget)
    effective_input_budget = min(
        cfg.input_token_budget,
        provider_limit if isinstance(provider_limit, int) and provider_limit > 0 else cfg.input_token_budget,
    )
    state.context_budget_tokens = effective_input_budget
    budget_label = (
        "320K"
        if effective_input_budget == MAX_INPUT_TOKENS
        else f"{effective_input_budget:,}"
    )
    _emit(
        on_update,
        "context_budget_configured",
        0,
        f"🧠 [Cerebro] 上下文预算已设置为 {budget_label} Tokens",
        used_tokens=0,
        budget_tokens=effective_input_budget,
        configured_budget_tokens=cfg.input_token_budget,
        provider_limit_tokens=provider_limit,
    )
    for stage_name, duration_ms in (
        ("system_prompt", prompt_duration_ms),
        ("tool_registry", registry_duration_ms),
    ):
        if cfg.verbose or duration_ms > 500:
            print(f"[perf] {stage_name} duration_ms={duration_ms:.1f}")
    _emit(on_update, "run_started", 0, "Agent 已启动", mode=cfg.mode)

    current_step_limit = cfg.max_steps
    step = 1
    while step <= current_step_limit:
        state.step = step
        if cfg.should_stop is not None and cfg.should_stop():
            return _user_stopped(state, on_update)
        if cfg.time_fn() - state.start_time > cfg.max_duration_seconds:
            return _duration_stopped(state, cfg, on_update)
        _emit(
            on_update,
            "step_started",
            step,
            f"步数：{step}/{current_step_limit}",
            current_step=step,
            max_steps=current_step_limit,
        )
        remaining_steps = current_step_limit - step
        if remaining_steps <= 3:
            _emit(
                on_update,
                "step_limit_warning",
                step,
                f"⚠️ 即将达到循环上限（剩余 {remaining_steps} 步）",
                current_step=step,
                max_steps=current_step_limit,
                remaining_steps=remaining_steps,
            )

        state.messages, removed_tool_units, kept_tool_units = compress_tool_history(
            state.messages
        )
        if removed_tool_units:
            state.tool_history_compressions += 1
            _emit(
                on_update,
                "tool_history_compressed",
                step,
                "🔧 工具历史已压缩，保留最近 20 条",
                removed_tool_units=removed_tool_units,
                kept_tool_units=kept_tool_units,
            )

        _emit(
            on_update,
            "context_building",
            step,
            "正在构建模型上下文",
            source_messages=len(state.messages),
        )
        context_started = time.perf_counter()
        usage_holder: list[ContextUsage] = []
        try:
            request_messages = fit_context(
                state.messages,
                registry.schemas,
                effective_input_budget,
                usage_callback=usage_holder.append,
            )
        except ContextBudgetError as exc:
            answer = str(exc)
            _emit(on_update, "run_failed", step, answer, reason="context_budget_error")
            return _stopped("failed", "context_budget_error", answer, state)

        if usage_holder:
            usage = usage_holder[-1]
            state.context_used_tokens = usage.used_tokens
            state.context_budget_tokens = usage.budget_tokens
            if usage.compressed:
                state.context_compressions += 1
            _emit(
                on_update,
                "context_usage",
                step,
                "上下文用量已更新",
                used_tokens=usage.used_tokens,
                budget_tokens=usage.budget_tokens,
                source_tokens=usage.source_tokens,
                compressed=usage.compressed,
                released_tokens=usage.released_tokens,
            )
            if usage.compressed:
                _emit(
                    on_update,
                    "context_compressed",
                    step,
                    f"📦 上下文已压缩，释放约 {usage.released_tokens} Tokens",
                    released_tokens=usage.released_tokens,
                )
                _emit(
                    on_update,
                    "context_forced_compression",
                    step,
                    f"📦 上下文 Token 估算：{usage.source_tokens} / {usage.budget_tokens}，触发压缩",
                    source_tokens=usage.source_tokens,
                    budget_tokens=usage.budget_tokens,
                    output_tokens=usage.used_tokens,
                )

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

        request_token_estimate = estimate_context_tokens(
            request_messages,
            registry.schemas,
        )
        message_summaries, message_anomalies = _message_diagnostics(request_messages)
        _emit(
            on_update,
            "model_request_diagnostics",
            step,
            "模型请求诊断已记录",
            message_count=len(request_messages),
            estimated_tokens=request_token_estimate,
            budget_tokens=effective_input_budget,
            last_messages=message_summaries,
            anomalies=message_anomalies,
        )
        print(
            "[Cerebro::ModelRequest] "
            f"messages={len(request_messages)} tokens={request_token_estimate}/"
            f"{effective_input_budget} last3={message_summaries!r} "
            f"anomalies={message_anomalies!r}"
        )
        _emit(on_update, "model_request", step, "正在请求模型", messages=len(request_messages))
        model_started = time.perf_counter()
        empty_retry_count = 0
        while True:
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
                code = exc.code
                answer = _friendly_model_error(code)
                _record_model_error(
                    state,
                    code,
                    str(exc),
                    step=step,
                    details=exc.details,
                )
                _emit(
                    on_update,
                    "run_failed",
                    step,
                    answer,
                    reason=code,
                    error_code=code,
                    technical_message=str(exc),
                    diagnostics=exc.details,
                )
                return _stopped("failed", code, answer, state)

            has_content = isinstance(turn.content, str) and bool(turn.content.strip())
            if has_content or turn.tool_calls:
                break

            code = "empty_content_and_tools"
            details = {
                "finish_reason": turn.finish_reason,
                "retry_count": empty_retry_count,
                "message_count": len(request_messages),
                "estimated_tokens": request_token_estimate,
            }
            _record_model_error(
                state,
                code,
                "provider returned neither text content nor tool calls",
                step=step,
                details=details,
            )
            if empty_retry_count >= cfg.max_empty_response_retries:
                answer = _friendly_model_error(code, retry_count=empty_retry_count)
                _emit(
                    on_update,
                    "run_stopped",
                    step,
                    answer,
                    reason="empty_response",
                    error_code=code,
                    retry_count=empty_retry_count,
                    diagnostics=details,
                )
                return _stopped("stopped", "empty_response", answer, state)

            empty_retry_count += 1
            state.empty_response_retries += 1
            delay = cfg.empty_response_retry_base_seconds * (
                2 ** (empty_retry_count - 1)
            )
            _emit(
                on_update,
                "empty_response_retry",
                step,
                f"⚠️ 检测到空响应，正在重试（第 {empty_retry_count} 次）...",
                attempt=empty_retry_count,
                delay_seconds=delay,
                finish_reason=turn.finish_reason,
            )
            # The empty assistant turn has deliberately not been appended to
            # state.messages, so the retry uses the last clean protocol state.
            if delay > 0:
                cfg.sleep_fn(delay)

        if cfg.should_stop is not None and cfg.should_stop():
            return _user_stopped(state, on_update)

        model_duration_ms = (time.perf_counter() - model_started) * 1_000
        if cfg.verbose or model_duration_ms > 500:
            print(f"[perf] model step={step} duration_ms={model_duration_ms:.1f}")

        finish_reason = turn.finish_reason
        if finish_reason == "length":
            _emit(
                on_update,
                "response_truncated",
                step,
                "⚠️ 模型响应被截断（finish_reason=length），建议增加 max_tokens 或精简上下文",
                finish_reason=finish_reason,
            )
        elif finish_reason == "content_filter":
            _emit(
                on_update,
                "response_filtered",
                step,
                "⚠️ 模型响应受到内容过滤（finish_reason=content_filter）",
                finish_reason=finish_reason,
            )

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
            finish_reason=finish_reason,
        )

        if not turn.tool_calls:
            _emit(on_update, "run_completed", step, "Agent 已完成任务")
            return _stopped("completed", "final_answer", turn.content or "", state)

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

            if cfg.time_fn() - state.start_time > cfg.max_duration_seconds:
                return _duration_stopped(state, cfg, on_update)

        _emit(on_update, "step_completed", step, f"步骤 {step} 已完成")

        if step >= current_step_limit:
            if cfg.confirm_step_extension is not None:
                _emit(
                    on_update,
                    "step_extension_requested",
                    step,
                    "循环次数已达到上限，等待用户决定",
                    current_step=step,
                    max_steps=current_step_limit,
                    extension_count=state.step_extensions,
                )
                approved = cfg.confirm_step_extension(
                    step,
                    current_step_limit,
                    state.step_extensions,
                )
                if cfg.should_stop is not None and cfg.should_stop():
                    return _user_stopped(state, on_update)
                if cfg.time_fn() - state.start_time > cfg.max_duration_seconds:
                    return _duration_stopped(state, cfg, on_update)
                if approved:
                    state.allow_continue = True
                    state.override_max_steps = True
                    state.step_extensions += 1
                    current_step_limit += 50
                    _emit(
                        on_update,
                        "step_extension_approved",
                        step,
                        "🧠 [Cerebro] 用户选择继续（步数上限 +50）",
                        max_steps=current_step_limit,
                        extension_size=50,
                    )
                else:
                    answer = "用户在循环次数上限处停止执行。"
                    _emit(
                        on_update,
                        "run_stopped",
                        step,
                        "🧠 [Cerebro] 用户停止执行",
                        reason="user_declined_step_extension",
                    )
                    return _stopped(
                        "stopped",
                        "user_declined_step_extension",
                        answer,
                        state,
                    )
            else:
                answer = f"Agent 已达到最大步骤数（{current_step_limit}），任务可能尚未完成。"
                _emit(on_update, "run_stopped", step, answer, reason="max_steps")
                return _stopped("stopped", "max_steps", answer, state)
        step += 1

    answer = f"Agent 已达到最大步骤数（{current_step_limit}），任务可能尚未完成。"
    _emit(on_update, "run_stopped", state.step, answer, reason="max_steps")
    return _stopped("stopped", "max_steps", answer, state)
