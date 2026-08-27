"""Failing test that the demo agent is expected to repair."""

import pytest

from calc import divide


def test_divide_regular_numbers() -> None:
    assert divide(8, 2) == 4


def test_divide_by_zero() -> None:
    with pytest.raises(ValueError, match="denominator cannot be zero"):
        divide(8, 0)

