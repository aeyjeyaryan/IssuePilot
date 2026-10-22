"""List helpers."""


def flatten(nested):
    """Flatten a list of lists into a single list."""
    out = []
    for item in nested:
        out.extend(item)
    return out


def unique(items):
    """Return ``items`` without duplicates, preserving the original order."""
    seen = set()
    out = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def chunk(items, size):
    """Split ``items`` into chunks of at most ``size`` elements."""
    return [items[i : i + size] for i in range(0, len(items) - size + 1, size)]
