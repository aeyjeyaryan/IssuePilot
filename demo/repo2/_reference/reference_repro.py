"""Reproduction test for the `truncate` bug (written by the agent)."""

from strings import truncate


def test_truncate_respects_the_maximum_length():
    assert len(truncate("hello world", 8)) == 8


def test_truncate_result_keeps_the_beginning():
    assert truncate("hello world", 8).startswith("hello")
