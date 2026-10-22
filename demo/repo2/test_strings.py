"""Tests for the string helpers."""

from strings import count_words, is_palindrome, truncate


def test_is_palindrome():
    assert is_palindrome("A man a plan a canal Panama")


def test_truncate_respects_the_maximum_length():
    assert len(truncate("hello world", 8)) == 8


def test_truncate_short_text_is_unchanged():
    assert truncate("hi", 8) == "hi"


def test_count_words():
    assert count_words("one two three") == 3
