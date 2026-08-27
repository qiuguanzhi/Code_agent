"""Strict parsing, validation, and deduplication for native tool calls."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

from agent.state import AgentState, ToolCall
from tools.schemas import TOOL_SCHEMAS


class ToolArgumentsError(ValueError):
    """Raised when model-generated tool arguments violate the local contract."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.details = details or {}


class RepeatedToolCallError(ValueError):
    """Raised when an identical normalized call exceeds its repetition limit."""

    def __init__(self, signature: str, count: int, max_repeats: int) -> None:
        super().__init__(f"identical tool call repeated {count} times; limit is {max_repeats}")
        self.signature = signature
        self.count = count
        self.max_repeats = max_repeats


def _schema_by_name(
    tool_schemas: Sequence[Mapping[str, Any]],
) -> dict[str, Mapping[str, Any]]:
    """Index OpenAI-compatible tool schemas by function name."""

    indexed: dict[str, Mapping[str, Any]] = {}
    for schema in tool_schemas:
        function = schema.get("function")
        if not isinstance(function, Mapping):
            continue
        name = function.get("name")
        if isinstance(name, str):
            indexed[name] = function
    return indexed


def _matches_json_type(value: Any, expected_type: str) -> bool:
    """Return whether a Python value matches a supported JSON Schema type."""

    if expected_type == "object":
        return isinstance(value, dict)
    if expected_type == "array":
        return isinstance(value, list)
    if expected_type == "string":
        return isinstance(value, str)
    if expected_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected_type == "boolean":
        return isinstance(value, bool)
    return True


def _validate_value(name: str, value: Any, schema: Mapping[str, Any]) -> None:
    """Validate one value against the JSON Schema subset used by local tools."""

    expected_type = schema.get("type")
    if isinstance(expected_type, str) and not _matches_json_type(value, expected_type):
        raise ToolArgumentsError(
            "invalid_argument_type",
            f"argument '{name}' must be of type {expected_type}",
            details={"argument": name, "expected_type": expected_type},
        )

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        if isinstance(minimum, (int, float)) and value < minimum:
            raise ToolArgumentsError(
                "argument_out_of_range",
                f"argument '{name}' must be at least {minimum}",
                details={"argument": name, "minimum": minimum},
            )
        if isinstance(maximum, (int, float)) and value > maximum:
            raise ToolArgumentsError(
                "argument_out_of_range",
                f"argument '{name}' must be at most {maximum}",
                details={"argument": name, "maximum": maximum},
            )

    if isinstance(value, list):
        minimum_items = schema.get("minItems")
        maximum_items = schema.get("maxItems")
        if isinstance(minimum_items, int) and len(value) < minimum_items:
            raise ToolArgumentsError(
                "argument_out_of_range",
                f"argument '{name}' must contain at least {minimum_items} items",
            )
        if isinstance(maximum_items, int) and len(value) > maximum_items:
            raise ToolArgumentsError(
                "argument_out_of_range",
                f"argument '{name}' must contain at most {maximum_items} items",
            )
        item_schema = schema.get("items")
        if isinstance(item_schema, Mapping):
            for index, item in enumerate(value):
                _validate_value(f"{name}[{index}]", item, item_schema)


def parse_tool_arguments(
    call: ToolCall,
    tool_schemas: Sequence[Mapping[str, Any]] = TOOL_SCHEMAS,
) -> dict[str, Any]:
    """Parse a tool call as a strict JSON object and validate its schema."""

    available = _schema_by_name(tool_schemas)
    if call.name not in available:
        raise ToolArgumentsError(
            "unknown_tool",
            f"unknown tool: {call.name}",
            details={"allowed": sorted(available)},
        )

    try:
        arguments = json.loads(call.arguments_json)
    except json.JSONDecodeError as exc:
        raise ToolArgumentsError(
            "invalid_json",
            f"tool arguments are not valid JSON: {exc.msg}",
            details={"line": exc.lineno, "column": exc.colno},
        ) from exc

    if not isinstance(arguments, dict):
        raise ToolArgumentsError(
            "arguments_must_be_object",
            "tool arguments must decode to a JSON object",
        )

    parameters = available[call.name].get("parameters", {})
    if not isinstance(parameters, Mapping):
        raise ToolArgumentsError("invalid_schema", "tool parameters schema is invalid")
    properties = parameters.get("properties", {})
    if not isinstance(properties, Mapping):
        raise ToolArgumentsError("invalid_schema", "tool properties schema is invalid")

    required_value = parameters.get("required", [])
    required = set(required_value) if isinstance(required_value, list) else set()
    missing = sorted(name for name in required if name not in arguments)
    if missing:
        raise ToolArgumentsError(
            "missing_arguments",
            f"missing required arguments: {', '.join(missing)}",
            details={"missing": missing},
        )

    if parameters.get("additionalProperties") is False:
        unexpected = sorted(name for name in arguments if name not in properties)
        if unexpected:
            raise ToolArgumentsError(
                "unexpected_arguments",
                f"unexpected arguments: {', '.join(unexpected)}",
                details={"unexpected": unexpected},
            )

    for name, value in arguments.items():
        value_schema = properties.get(name)
        if isinstance(value_schema, Mapping):
            _validate_value(name, value, value_schema)
    return arguments


def canonical_tool_signature(name: str, arguments: Mapping[str, Any]) -> str:
    """Hash a tool name and canonical JSON arguments for repeat detection."""

    canonical = json.dumps(
        {"name": name, "arguments": arguments},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def register_call_signature(
    state: AgentState,
    name: str,
    arguments: Mapping[str, Any],
    *,
    max_repeats: int = 3,
) -> str:
    """Record one normalized call and reject calls beyond ``max_repeats``."""

    if max_repeats < 1:
        raise ValueError("max_repeats must be positive")
    signature = canonical_tool_signature(name, arguments)
    count = state.repeated_signatures.get(signature, 0) + 1
    state.repeated_signatures[signature] = count
    if count > max_repeats:
        raise RepeatedToolCallError(signature, count, max_repeats)
    return signature


def tool_error(
    code: str,
    message: str | None = None,
    *,
    details: dict[str, Any] | None = None,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create the shared structured error shape used for tool feedback."""

    return {
        "ok": False,
        "data": None,
        "error": {"code": code, "message": message or code, "details": details or {}},
        "meta": meta or {},
    }


def extract_goal_plan(response_text: str) -> list[str]:
    """Extension hook for future goal-plan parsing.

    Phase 2 deliberately leaves goal-plan semantics undefined. Phase 3 or a
    later goal-oriented mode can replace this stub without changing provider
    response parsing.
    """

    _ = response_text
    return []

