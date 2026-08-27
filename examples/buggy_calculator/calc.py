"""A deliberately incomplete calculator used by the coding-agent demo."""


def divide(a: float, b: float) -> float:
    """Divide ``a`` by ``b``, rejecting a zero denominator."""

    if b == 0:
        raise ValueError("denominator cannot be zero")

    return a / b
