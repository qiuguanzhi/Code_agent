"""OpenAI-compatible JSON schemas for locally implemented tools."""

from copy import deepcopy
from typing import Any


READ_FILE_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "read_file",
        "description": (
            "Read a UTF-8 text file inside the configured workspace. "
            "Use it only when reading that file directly advances the user's "
            "stated task or plan; never inspect unrelated files speculatively. "
            "Use next_line from the result to continue reading a truncated file."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path relative to the workspace root.",
                },
                "start_line": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "One-based first line to return.",
                },
                "max_lines": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 500,
                    "description": "Maximum number of lines to return.",
                },
                "max_chars": {
                    "type": "integer",
                    "minimum": 100,
                    "maximum": 50_000,
                    "description": "Maximum number of file-content characters to return.",
                },
            },
            "required": ["path", "start_line", "max_lines", "max_chars"],
            "additionalProperties": False,
        },
    },
}


WRITE_FILE_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "write_file",
        "description": (
            "Atomically replace a UTF-8 text file inside the configured workspace. "
            "Pass the SHA-256 returned by read_file, or an empty string for a new file."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path relative to the workspace root.",
                },
                "content": {
                    "type": "string",
                    "description": "Complete UTF-8 text content for the file.",
                },
                "expected_sha256": {
                    "type": "string",
                    "description": (
                        "Current file SHA-256 from read_file. Use an empty string only "
                        "when creating a file that does not exist."
                    ),
                },
            },
            "required": ["path", "content", "expected_sha256"],
            "additionalProperties": False,
        },
    },
}


DELETE_FILE_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "delete_file",
        "description": (
            "Delete one regular file inside the workspace after verifying the "
            "SHA-256 returned by read_file. Directories cannot be deleted."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path relative to the workspace root.",
                },
                "expected_sha256": {
                    "type": "string",
                    "description": "Current file SHA-256 returned by read_file.",
                },
            },
            "required": ["path", "expected_sha256"],
            "additionalProperties": False,
        },
    },
}


RUN_COMMAND_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "run_command",
        "description": (
            "Run one allow-listed executable with arguments inside the workspace. "
            "Shell operators, pipelines, and redirections are not supported."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "argv": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "maxItems": 64,
                    "description": (
                        "Executable and arguments, for example "
                        "[\"python\", \"-m\", \"pytest\", \"-q\"]."
                    ),
                },
                "cwd": {
                    "type": "string",
                    "description": "Working directory relative to the workspace root.",
                },
                "timeout_seconds": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 120,
                },
                "max_output_chars": {
                    "type": "integer",
                    "minimum": 1_000,
                    "maximum": 50_000,
                },
            },
            "required": ["argv", "cwd", "timeout_seconds", "max_output_chars"],
            "additionalProperties": False,
        },
    },
}


TOOL_SCHEMAS: list[dict[str, Any]] = [
    READ_FILE_SCHEMA,
    WRITE_FILE_SCHEMA,
    DELETE_FILE_SCHEMA,
    RUN_COMMAND_SCHEMA,
]


def get_tool_schemas() -> list[dict[str, Any]]:
    """Return an isolated copy so callers cannot mutate global schemas."""

    return deepcopy(TOOL_SCHEMAS)
