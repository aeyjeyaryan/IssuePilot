# Bug: `chunk` drops the last partial chunk

## Description

In `lists.py`, `chunk(items, size)` should split a list into chunks of at most
`size` elements.  When the length of `items` is not a multiple of `size` the
final partial chunk is dropped.

`chunk([1, 2, 3, 4, 5], 2)` returns `[[1, 2], [3, 4]]` instead of
`[[1, 2], [3, 4], [5]]`.

## Expected behaviour

```python
from lists import chunk
assert chunk([1, 2, 3, 4, 5], 2) == [[1, 2], [3, 4], [5]]
```

## Environment

Python 3.11+, pytest.
