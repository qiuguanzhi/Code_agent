"""Diff generation helpers for reviewable file modifications."""

from __future__ import annotations

import difflib


DEFAULT_MAX_DIFF_CHARS = 12_000


def generate_unified_diff(
    original_content: str,
    new_content: str,
    file_path: str,
) -> str:
    """Return a standard-library unified diff for one text file."""

    original_lines = original_content.splitlines()
    new_lines = new_content.splitlines()
    diff_lines = difflib.unified_diff(
        original_lines,
        new_lines,
        fromfile=f"a/{file_path}",
        tofile=f"b/{file_path}",
        lineterm="",
    )
    rendered = "\n".join(diff_lines)
    return f"{rendered}\n" if rendered else ""


def truncate_diff(diff_text: str, max_chars: int = DEFAULT_MAX_DIFF_CHARS) -> str:
    """Bound a diff by retaining its beginning and end."""

    if max_chars < 100:
        raise ValueError("max_chars must be at least 100")
    if len(diff_text) <= max_chars:
        return diff_text
    marker = "\n... [diff truncated] ...\n"
    retained_chars = max_chars - len(marker)
    head_size = retained_chars // 2
    tail_size = retained_chars - head_size
    return diff_text[:head_size] + marker + diff_text[-tail_size:]
