"""String utility helpers."""


def is_palindrome(text):
    """Return ``True`` when ``text`` reads the same forwards and backwards."""
    cleaned = text.lower().replace(" ", "")
    return cleaned == cleaned[::-1]


def truncate(text, max_length):
    """Truncate ``text`` to ``max_length`` characters, adding an ellipsis."""
    if len(text) <= max_length:
        return text
    return text[:max_length] + "..."


def count_words(text):
    """Count the whitespace separated words in ``text``."""
    return len(text.split())
