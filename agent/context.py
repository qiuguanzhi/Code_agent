"""Framework-free context grouping, compression, and token budgeting."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from utils.token_counter import TOKEN_ESTIMATE_MARGIN, estimate_tokens

MAX_WORK_MEMORY_FACTS = 50
MAX_FACT_CHARS = 500
COMPRESSION_THRESHOLD = 0.80
MAX_INPUT_TOKENS = 320_000


class ContextBudgetError(ValueError):
    """Raised when mandatory context cannot fit inside the configured budget."""


@dataclass(frozen=True, slots=True)
class ContextUsage:
    """Observable statistics produced by one context fitting pass."""

    used_tokens: int
    budget_tokens: int
    source_tokens: int
    compressed: bool
    released_tokens: int


UsageCallback = Callable[[ContextUsage], None]


def group_protocol_units(
    messages: Sequence[Mapping[str, Any]],
) -> list[list[dict[str, Any]]]:
    """Group assistant tool calls with all immediately following tool results."""

    units: list[list[dict[str, Any]]] = []
    index = 0
    while index < len(messages):
        message = dict(messages[index])
        unit = [message]
        if message.get("role") == "assistant" and message.get("tool_calls"):
            index += 1
            while index < len(messages) and messages[index].get("role") == "tool":
                unit.append(dict(messages[index]))
                index += 1
            units.append(unit)
            continue
        units.append(unit)
        index += 1
    return units


def _clip(text: str, max_chars: int = MAX_FACT_CHARS) -> str:
    """Bound a work-memory string while preserving its beginning and end."""

    if len(text) <= max_chars:
        return text
    marker = "... [truncated] ..."
    retained = max_chars - len(marker)
    head = retained // 2
    tail = retained - head
    return text[:head] + marker + text[-tail:]


def _decode_tool_result(content: Any) -> dict[str, Any]:
    """Decode a tool message content without trusting its shape."""

    if not isinstance(content, str):
        return {"raw": _clip(str(content))}
    try:
        decoded = json.loads(content)
    except json.JSONDecodeError:
        return {"raw": _clip(content)}
    return decoded if isinstance(decoded, dict) else {"raw": _clip(content)}


def build_work_memory(
    dropped_units: Sequence[Sequence[Mapping[str, Any]]],
) -> dict[str, Any] | None:
    """Compress old protocol units into deterministic structured facts."""

    if not dropped_units:
        return None

    facts: list[dict[str, Any]] = []
    call_names: dict[str, str] = {}
    for unit in dropped_units:
        for message in unit:
            role = message.get("role")
            if role == "assistant":
                tool_calls = message.get("tool_calls")
                if isinstance(tool_calls, list):
                    for call in tool_calls:
                        if not isinstance(call, Mapping):
                            continue
                        call_id = call.get("id")
                        function = call.get("function")
                        if isinstance(call_id, str) and isinstance(function, Mapping):
                            name = function.get("name")
                            if isinstance(name, str):
                                call_names[call_id] = name
                content = message.get("content")
                if isinstance(content, str) and content.strip():
                    facts.append({"kind": "assistant_note", "text": _clip(content.strip())})
            elif role == "tool":
                call_id = message.get("tool_call_id")
                result = _decode_tool_result(message.get("content"))
                error = result.get("error")
                meta = result.get("meta") if isinstance(result.get("meta"), Mapping) else {}
                fact: dict[str, Any] = {
                    "kind": "tool_result",
                    "tool_call_id": call_id,
                    "tool": call_names.get(str(call_id), "unknown"),
                    "ok": result.get("ok"),
                }
                if isinstance(error, Mapping):
                    fact["error_code"] = error.get("code")
                for key in ("path", "exit_code", "timed_out", "sha256"):
                    if key in meta:
                        fact[key] = meta[key]
                facts.append(fact)
            elif role == "user":
                content = message.get("content")
                if isinstance(content, str) and content.strip():
                    facts.append({"kind": "user_note", "text": _clip(content.strip())})

    memory = {
        "type": "work_memory",
        "dropped_protocol_units": len(dropped_units),
        "facts": facts[-MAX_WORK_MEMORY_FACTS:],
    }
    return {
        "role": "system",
        "content": "WORK_MEMORY_JSON\n" + json.dumps(memory, ensure_ascii=False, sort_keys=True),
    }


def _assemble_context(
    units: Sequence[Sequence[dict[str, Any]]],
    selected_indices: set[int],
) -> list[dict[str, Any]]:
    """Assemble selected units chronologically and insert one work memory."""

    dropped = [unit for index, unit in enumerate(units) if index not in selected_indices]
    work_memory = build_work_memory(dropped)
    result: list[dict[str, Any]] = []
    memory_inserted = False
    for index, unit in enumerate(units):
        if index not in selected_indices:
            continue
        if not memory_inserted and work_memory is not None:
            if result and result[0].get("role") == "system":
                result.append(work_memory)
                memory_inserted = True
            elif unit[0].get("role") != "system":
                result.append(work_memory)
                memory_inserted = True
        result.extend(dict(message) for message in unit)
    if work_memory is not None and not memory_inserted:
        result.insert(0, work_memory)
    return result


def _context_cost(
    messages: Sequence[Mapping[str, Any]],
    tool_schemas: Sequence[Mapping[str, Any]],
) -> int:
    """Estimate the combined messages and tool-schema input cost."""

    return estimate_tokens(messages) + estimate_tokens(tool_schemas)


def estimate_context_tokens(
    messages: Sequence[Mapping[str, Any]],
    tool_schemas: Sequence[Mapping[str, Any]],
) -> int:
    """Publicly estimate a complete request, including tool definitions."""

    return _context_cost(messages, tool_schemas)


def compress_tool_history(
    messages: Sequence[Mapping[str, Any]],
    *,
    trigger_count: int = 30,
    keep_recent: int = 20,
) -> tuple[list[dict[str, Any]], int, int]:
    """Summarize old Assistant+Tool units after tool history grows too large."""

    if trigger_count < 1 or keep_recent < 1 or keep_recent >= trigger_count:
        raise ValueError("tool history limits must satisfy 0 < keep_recent < trigger_count")
    units = group_protocol_units(messages)
    tool_unit_indices = [
        index
        for index, unit in enumerate(units)
        if any(message.get("role") == "tool" for message in unit)
    ]
    total = len(tool_unit_indices)
    if total <= trigger_count:
        return [dict(message) for message in messages], 0, total

    drop_count = total - keep_recent
    dropped_indices = set(tool_unit_indices[:drop_count])
    dropped_units = [units[index] for index in sorted(dropped_indices)]
    memory = build_work_memory(dropped_units)
    result: list[dict[str, Any]] = []
    for index, unit in enumerate(units):
        if index in dropped_indices:
            continue
        result.extend(dict(message) for message in unit)
    if memory is not None:
        insert_at = 1 if result and result[0].get("role") == "system" else 0
        result.insert(insert_at, memory)
    return result, drop_count, keep_recent


def _shrink_longest_content(messages: list[dict[str, Any]]) -> bool:
    """Shrink the longest textual message, returning whether a change was made."""

    candidates = [
        (index, message["content"])
        for index, message in enumerate(messages)
        if isinstance(message.get("content"), str) and len(message["content"]) > 256
    ]
    if not candidates:
        return False
    index, content = max(candidates, key=lambda item: len(item[1]))
    messages[index] = {**messages[index], "content": _clip(content, max(256, len(content) // 2))}
    return True


def compress_context(
    messages: Sequence[Mapping[str, Any]],
    tool_schemas: Sequence[Mapping[str, Any]],
    target_budget: int,
) -> list[dict[str, Any]]:
    """Compress old history into WORK_MEMORY_JSON while preserving protocol units."""

    if target_budget <= 0:
        raise ValueError("target_budget must be positive")
    copied = [dict(message) for message in messages]
    if _context_cost(copied, tool_schemas) <= target_budget:
        return copied
    units = group_protocol_units(copied)
    if not units:
        raise ContextBudgetError("no messages are available for context")

    pinned: set[int] = set()
    if units[0][0].get("role") == "system":
        pinned.add(0)
    user_indices = [index for index, unit in enumerate(units) if unit[0].get("role") == "user"]
    if user_indices:
        pinned.add(user_indices[-1])

    selected = set(pinned)
    for index in range(len(units) - 1, -1, -1):
        if index in selected:
            continue
        candidate_indices = selected | {index}
        candidate = _assemble_context(units, candidate_indices)
        if _context_cost(candidate, tool_schemas) <= target_budget:
            selected = candidate_indices

    result = _assemble_context(units, selected)
    while _context_cost(result, tool_schemas) > target_budget:
        if not _shrink_longest_content(result):
            raise ContextBudgetError("mandatory context and tool schemas exceed input_budget")
    return result


def fit_context(
    messages: Sequence[Mapping[str, Any]],
    tool_schemas: Sequence[Mapping[str, Any]],
    input_budget: int,
    *,
    usage_callback: UsageCallback | None = None,
) -> list[dict[str, Any]]:
    """Fit and proactively compress context at 80% without splitting units."""

    started_at = time.perf_counter()
    if input_budget <= 0:
        raise ValueError("input_budget must be positive")
    copied = [dict(message) for message in messages]
    input_tokens = _context_cost(copied, tool_schemas)
    target_budget = max(1, int(input_budget * COMPRESSION_THRESHOLD))
    units = group_protocol_units(copied)
    pinned_count = 0
    if units and units[0][0].get("role") == "system":
        pinned_count += 1
    if any(unit[0].get("role") == "user" for unit in units):
        pinned_count += 1
    has_old_history = len(units) > pinned_count
    if input_tokens <= target_budget:
        result = copied
        compressed = False
    elif not has_old_history and input_tokens <= input_budget:
        # A system prompt plus the current user turn is mandatory context, not
        # old history. Show its real usage rather than damaging either message.
        result = copied
        compressed = False
    else:
        hard_target = target_budget if has_old_history else input_budget
        result = compress_context(copied, tool_schemas, hard_target)
        compressed = _context_cost(result, tool_schemas) < input_tokens

    duration_ms = (time.perf_counter() - started_at) * 1_000
    output_tokens = _context_cost(result, tool_schemas)
    usage = ContextUsage(
        used_tokens=output_tokens,
        budget_tokens=input_budget,
        source_tokens=input_tokens,
        compressed=compressed,
        released_tokens=max(0, input_tokens - output_tokens),
    )
    if usage_callback is not None:
        usage_callback(usage)
    print(
        "[Cerebro::Context] "
        f"messages={len(copied)} estimated_tokens={input_tokens} "
        f"budget={input_budget} output_messages={len(result)} "
        f"output_tokens={output_tokens} duration_ms={duration_ms:.1f} "
        f"compressed={str(compressed).lower()}"
    )
    return result
