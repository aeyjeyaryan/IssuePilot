"""Reproduction test for the `add` bug (written by the agent)."""

from calc import add


def test_add_returns_the_sum():
    assert add(2, 3) == 5


def test_add_handles_negative_numbers():
    assert add(-4, 4) == 0
