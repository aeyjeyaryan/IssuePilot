# Bug: `truncate` returns a string that is too long

## Description

In `strings.py`, `truncate(text, max_length)` is supposed to return a string
of at most `max_length` characters.  It returns `max_length + 3` characters
instead, because the ellipsis is appended without taking its length into
account.

`truncate("hello world", 8)` returns `"hello wor..."` which is 11 characters
long instead of 8.

## Expected behaviour

```python
from strings import truncate
assert len(truncate("hello world", 8)) == 8
```

## Environment

Python 3.11+, pytest.
