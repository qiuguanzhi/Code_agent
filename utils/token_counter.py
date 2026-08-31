"""Dependency-free conservative token estimation utilities."""

from __future__ import annotations

import json
import math
from typing import Any


TOKEN_ESTIMATE_MARGIN = 1.20


def estimate_tokens(value: Any) -> int:
    """Estimate serialized input tokens with the project's existing 20% margin."""

    if isinstance(value, str):
        serialized = value
    else:
        serialized = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return max(1, math.ceil(len(serialized) * TOKEN_ESTIMATE_MARGIN))
