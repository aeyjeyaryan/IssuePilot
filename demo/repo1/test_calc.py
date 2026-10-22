"""Tests for the arithmetic helpers."""

from calc import add, mean, multiply


def test_add():
    assert add(2, 3) == 5


def test_multiply():
    assert multiply(2, 3) == 6


def test_mean():
    assert mean([1, 2, 3]) == 2.0
