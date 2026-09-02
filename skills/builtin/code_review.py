"""Read-only code review skill."""

from __future__ import annotations

from typing import Any

from skills.base import AgentContext, Skill, SkillResult


_PATH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "path": {"type": "string", "description": "工作区内待分析文件的相对路径。"}
    },
    "required": ["path"],
    "additionalProperties": False,
}


def _read_source(path: str, context: AgentContext) -> tuple[str | None, dict[str, Any]]:
    """Read a bounded source file through the native filesystem tool."""

    result = context.call_tool(
        "read_file",
        {"path": path, "start_line": 1, "max_lines": 500, "max_chars": 50_000},
    )
    if result.get("ok") is not True:
        return None, result
    data = result.get("data")
    content = data.get("content") if isinstance(data, dict) else None
    return (content if isinstance(content, str) else ""), result


class CodeReviewerSkill(Skill):
    """Perform a deterministic first-pass review of one source file."""

    name = "code_reviewer"
    description = "读取指定代码文件并进行安全性、可维护性和明显缺陷的快速审查。"
    parameters_schema = _PATH_SCHEMA
    required_permissions = frozenset({"filesystem"})
    built_in = True

    def execute(self, params: dict[str, Any], context: AgentContext) -> SkillResult:
        """Return source-aware findings for the model to assess further."""

        path = str(params["path"])
        content, raw = _read_source(path, context)
        if content is None:
            return SkillResult(False, error=raw.get("error"), meta={"path": path})
        findings: list[str] = []
        checks = {
            "eval(": "发现 eval 调用，需要确认输入是否可信。",
            "exec(": "发现 exec 调用，需要确认输入是否可信。",
            "except:": "发现裸 except，可能吞掉重要异常。",
            "TODO": "文件包含待办标记。",
        }
        for marker, finding in checks.items():
            if marker in content:
                findings.append(finding)
        long_lines = sum(1 for line in content.splitlines() if len(line) > 120)
        if long_lines:
            findings.append(f"有 {long_lines} 行超过 120 个字符。")
        if not findings:
            findings.append("快速静态检查未发现预设风险模式；仍需结合测试验证。")
        return SkillResult(
            True,
            {"path": path, "findings": findings, "line_count": len(content.splitlines())},
            meta={"path": path},
        )


class ExplainCodeSkill(Skill):
    """Collect bounded source context for a focused explanation."""

    name = "explain_code"
    description = "读取指定代码并返回结构化源码上下文，帮助模型准确解释职责和执行流程。"
    parameters_schema = _PATH_SCHEMA
    required_permissions = frozenset({"filesystem"})
    built_in = True

    def execute(self, params: dict[str, Any], context: AgentContext) -> SkillResult:
        """Return source text and basic metrics without modifying the file."""

        path = str(params["path"])
        content, raw = _read_source(path, context)
        if content is None:
            return SkillResult(False, error=raw.get("error"), meta={"path": path})
        return SkillResult(
            True,
            {
                "path": path,
                "line_count": len(content.splitlines()),
                "character_count": len(content),
                "source": content,
            },
            meta={"path": path},
        )


class RefactorSuggestSkill(Skill):
    """Produce local, evidence-linked refactoring candidates."""

    name = "refactor_suggest"
    description = "读取指定代码，定位长行、深缩进和重复风险，给出不直接写盘的重构候选。"
    parameters_schema = _PATH_SCHEMA
    required_permissions = frozenset({"filesystem"})
    built_in = True

    def execute(self, params: dict[str, Any], context: AgentContext) -> SkillResult:
        """Return deterministic refactoring candidates for model judgement."""

        path = str(params["path"])
        content, raw = _read_source(path, context)
        if content is None:
            return SkillResult(False, error=raw.get("error"), meta={"path": path})
        lines = content.splitlines()
        long_lines = [index for index, line in enumerate(lines, 1) if len(line) > 100]
        deep_lines = [
            index
            for index, line in enumerate(lines, 1)
            if len(line) - len(line.lstrip(" ")) >= 20
        ]
        suggestions: list[str] = []
        if long_lines:
            suggestions.append(f"拆分长行：{long_lines[:12]}")
        if deep_lines:
            suggestions.append(f"用提前返回降低嵌套：{deep_lines[:12]}")
        if not suggestions:
            suggestions.append("未发现基于行长和缩进的明显重构候选。")
        return SkillResult(
            True,
            {"path": path, "suggestions": suggestions},
            meta={"path": path},
        )
