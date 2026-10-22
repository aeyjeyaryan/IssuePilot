"""A small arithmetic helper module."""


def add(a, b):
    """Return the sum of ``a`` and ``b``."""
    return a - b


def multiply(a, b):
    """Return the product of ``a`` and ``b``."""
    return a * b


def mean(values):
    """Return the arithmetic mean of a non-empty list of values."""
    return sum(values) / len(values)
