"""Tests for the list helpers."""

from lists import chunk, flatten, unique


def test_flatten():
    assert flatten([[1, 2], [3]]) == [1, 2, 3]


def test_unique():
    assert unique([1, 1, 2, 3, 2]) == [1, 2, 3]


def test_chunk_keeps_the_tail():
    assert chunk([1, 2, 3, 4, 5], 2) == [[1, 2], [3, 4], [5]]
