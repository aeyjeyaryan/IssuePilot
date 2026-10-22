# Bug: `add` returns the difference instead of the sum

## Description

In `calc.py`, the function `add(a, b)` is documented to return the sum of the
two numbers, but it returns `a - b` instead.

`add(2, 3)` therefore returns `-1` instead of `5`.

## Expected behaviour

```python
from calc import add
assert add(2, 3) == 5
```

## Environment

Python 3.11+, pytest.
