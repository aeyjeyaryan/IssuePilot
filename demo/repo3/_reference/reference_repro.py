"""Reproduction test for the `chunk` bug (written by the agent)."""

from lists import chunk


def test_chunk_keeps_the_tail():
    assert chunk([1, 2, 3, 4, 5], 2) == [[1, 2], [3, 4], [5]]


def test_chunk_of_an_empty_list():
    assert chunk([], 3) == []
