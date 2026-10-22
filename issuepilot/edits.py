"""Search/replace edits: a model friendly alternative to unified diffs.

Small local models are notoriously bad at counting lines and writing correct
``@@`` hunk headers.  They are, however, quite good at copying a snippet from a
file and saying what it should become.  This module turns such edits into a
proper unified diff, which is what the rest of the pipeline consumes.

The expected model output is a JSON object::

    {"edits": [{"file": "calc.py", "old": "return a - b", "new": "return a + b"}]}

A bare list, a single edit object and fenced code blocks are all accepted, and
the keys are matched leniently (``path``/``filename``/``file``, ...).
"""

from __future__ import annotations

import ast
import difflib
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

_FENCE_RE = re.compile(r"```(?:json)?\s*\n(.*?)```", re.DOTALL)
_FILE_KEYS = ("file", "path", "filename", "file_path", "filepath")
_OLD_KEYS = ("old", "original", "search", "find", "old_string", "before")
_NEW_KEYS = ("new", "replacement", "replace", "new_string", "after", "fixed", "code")
_FUNC_KEYS = ("function", "function_name", "name")


@dataclass
class Edit:
    """A single edit: either a snippet replacement or a whole function."""

    file: str
    old: str
    new: str
    function: str = ""


def _first(payload: Dict[str, Any], keys: Sequence[str]) -> Optional[str]:
    """Return the first present, non-empty value among ``keys``."""
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def _looks_like_edit(payload: Any) -> bool:
    """Return ``True`` when ``payload`` seems to describe an edit."""
    if not isinstance(payload, dict):
        return False
    return bool(_first(payload, _OLD_KEYS) is not None and _first(payload, _FILE_KEYS) is not None)


def _collect(payload: Any, out: List[Edit]) -> None:
    """Recursively collect edit objects from an arbitrary JSON payload."""
    if isinstance(payload, list):
        for item in payload:
            _collect(item, out)
        return
    if not isinstance(payload, dict):
        return
    if _looks_like_edit(payload):
        file_name = _first(payload, _FILE_KEYS) or ""
        old = _first(payload, _OLD_KEYS) or ""
        new = _first(payload, _NEW_KEYS)
        if new is None:
            return
        out.append(Edit(file=file_name.strip(), old=old, new=new))
        return
    if _first(payload, _FUNC_KEYS) and _first(payload, _NEW_KEYS) and _first(payload, _FILE_KEYS):
        out.append(
            Edit(
                file=(_first(payload, _FILE_KEYS) or "").strip(),
                old="",
                new=_first(payload, _NEW_KEYS) or "",
                function=(_first(payload, _FUNC_KEYS) or "").strip(),
            )
        )
        return
    for key in ("edits", "changes", "patches", "replacements", "files"):
        if key in payload:
            _collect(payload[key], out)


def _loads(text: str) -> Any:
    """Parse JSON, tolerating trailing commas and single quoted keys."""
    try:
        return json.loads(text)
    except Exception:
        pass
    cleaned = re.sub(r",\s*([}\]])", r"\1", text)
    try:
        return json.loads(cleaned)
    except Exception:
        return None


def parse_edits(text: str) -> List[Edit]:
    """Extract search/replace edits from model output.

    Args:
        text: Raw model output.

    Returns:
        The edits that could be parsed (empty when the output is not JSON).
    """
    if not text or not text.strip():
        return []
    candidates: List[str] = [match.group(1) for match in _FENCE_RE.finditer(text)]
    candidates.append(text)
    for candidate in candidates:
        payload = _loads(candidate.strip())
        if payload is None:
            start = min(
                (index for index in (candidate.find("["), candidate.find("{")) if index >= 0),
                default=-1,
            )
            if start < 0:
                continue
            end = max(candidate.rfind("]"), candidate.rfind("}"))
            if end <= start:
                continue
            payload = _loads(candidate[start : end + 1])
        if payload is None:
            continue
        edits: List[Edit] = []
        _collect(payload, edits)
        if edits:
            return edits
    return []


def _split_lines(value: str, keepends: bool = True) -> List[str]:
    """Split a snippet into lines; ``keepends`` keeps the newlines."""
    lines = value.splitlines()
    if keepends:
        return [line + "\n" for line in lines]
    return lines


def _indent_of(line: str) -> str:
    """Return the leading whitespace of a line."""
    return line[: len(line) - len(line.lstrip())]


def _reindent(lines: Sequence[str], delta: int) -> List[str]:
    """Shift ``lines`` by ``delta`` spaces (only where it makes sense)."""
    if delta == 0:
        return list(lines)
    out = []
    for line in lines:
        if not line.strip():
            out.append(line)
            continue
        if delta > 0:
            out.append(" " * delta + line)
        else:
            stripped = line.lstrip(" ")
            out.append(stripped)
    return out


def _similarity(left: Sequence[str], right: Sequence[str]) -> float:
    """Return how similar two line blocks are (0.0 - 1.0).

    The comparison happens character by character: models usually only get a
    few characters wrong (an escaped quote, a stray space), which is invisible
    when whole lines are compared as single tokens.
    """
    return difflib.SequenceMatcher(
        None,
        "\n".join(line.strip() for line in left),
        "\n".join(line.strip() for line in right),
    ).ratio()


def _find_window(content: Sequence[str], old: Sequence[str]) -> Optional[int]:
    """Locate ``old`` inside ``content``.

    Models copy snippets imperfectly (lost escapes, re-wrapped docstrings, ...)
    so the lookup goes from strict to lenient: exact, whitespace insensitive and
    finally a similarity search that must clearly win.
    """
    size = len(old)
    if size == 0:
        return None
    for index in range(len(content) - size + 1):
        if list(content[index : index + size]) == list(old):
            return index
    stripped_old = [line.strip() for line in old]
    for index in range(len(content) - size + 1):
        if [line.strip() for line in content[index : index + size]] == stripped_old:
            return index
    best_score = 0.0
    best_index: Optional[int] = None
    second = 0.0
    for index in range(len(content) - size + 1):
        score = _similarity(content[index : index + size], old)
        if score > best_score:
            best_score, second = score, best_score
            best_index = index
        elif score > second:
            second = score
    if best_index is not None and best_score >= 0.85 and best_score - second >= 0.02:
        logger.info("Located an imperfect snippet with similarity %.2f", best_score)
        return best_index
    return None


_NOISE_RE = re.compile(r"[\"'`\\\s]+")


def _same_ignoring_quotes(left: str, right: str) -> bool:
    """Return ``True`` when two lines differ only in quotes/backslashes/space.

    Local models constantly escape backticks or drop a triple quote.  Such a
    "difference" carries no meaning and must never be written back to the file.
    """
    return _NOISE_RE.sub("", left) == _NOISE_RE.sub("", right)


def _choose_line(file_line: str, old_line: str, new_line: str, delta: int) -> str:
    """Pick the file's line when the edit did not really change it."""
    if _same_ignoring_quotes(old_line, new_line):
        return file_line
    return _reindent([new_line], delta)[0] + "\n"


def _build_replacement(
    matched: Sequence[str],
    old_lines: Sequence[str],
    new_lines: Sequence[str],
    delta: int,
) -> List[str]:
    """Compute the new content of the matched window, line by line."""
    if len(old_lines) == len(new_lines):
        return [
            _choose_line(matched[index], old_lines[index], new_lines[index], delta)
            for index in range(len(old_lines))
        ]
    matcher = difflib.SequenceMatcher(
        None, [line.strip() for line in old_lines], [line.strip() for line in new_lines]
    )
    out: List[str] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            out.extend(matched[i1:i2])
            continue
        block = new_lines[j1:j2]
        pairs = min(i2 - i1, j2 - j1)
        for offset in range(pairs):
            out.append(_choose_line(matched[i1 + offset], old_lines[i1 + offset], block[offset], delta))
        for line in block[pairs:]:
            out.append(_reindent([line], delta)[0] + "\n")
    return out


def apply_edit(content: str, edit: Edit) -> Tuple[str, str]:
    """Apply one edit to ``content`` at the line level.

    Only the lines the model actually changed are taken from ``new``; every
    other line is kept verbatim from the file.  That makes the edit robust
    against the small copying mistakes local models make (escaped quotes,
    changed indentation inside docstrings, ...).

    Args:
        content: The current file content.
        edit: The edit to apply.

    Returns:
        ``(new_content, error)``; ``error`` is empty on success.
    """
    old_lines = edit.old.splitlines()
    if not old_lines or not edit.old.strip():
        return content, "The edit has an empty `old` snippet"
    content_lines = content.splitlines(keepends=True)
    index = _find_window(content_lines, old_lines)
    if index is None:
        return content, (
            f"The `old` snippet was not found in `{edit.file}`. Copy it exactly from the file, "
            "including indentation."
        )
    matched = content_lines[index : index + len(old_lines)]
    new_lines = edit.new.splitlines()

    old_indent = _indent_of(next((line for line in old_lines if line.strip()), ""))
    found_indent = _indent_of(next((line for line in matched if line.strip()), ""))
    delta = len(found_indent) - len(old_indent)

    replacement = _build_replacement(matched, old_lines, new_lines, delta)
    if replacement == matched:
        return content, f"The edit for `{edit.file}` does not change anything"
    rebuilt = content_lines[:index] + replacement + content_lines[index + len(old_lines) :]
    return "".join(rebuilt), ""


def _safe_path(repo: Path, relative: str) -> Tuple[Optional[Path], str]:
    """Resolve ``relative`` inside ``repo``, rejecting escapes."""
    cleaned = str(relative).replace("\\", "/").strip()
    if cleaned.startswith(("a/", "b/")):
        cleaned = cleaned[2:]
    if cleaned.startswith("/"):
        return None, f"Absolute paths are not allowed: {relative}"
    target = (repo / cleaned).resolve()
    try:
        target.relative_to(repo.resolve())
    except ValueError:
        return None, f"Path traversal is not allowed: {relative}"
    return target, ""


def function_source(content: str, name: str) -> Optional[str]:
    """Return the source of the function ``name`` (``Class.method`` works too)."""
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return None
    wanted = name.split(".")[-1]
    lines = content.splitlines()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name != wanted:
            continue
        start = min([node.lineno] + [decorator.lineno for decorator in node.decorator_list]) - 1
        end = node.end_lineno or node.lineno
        return "\n".join(lines[start:end])
    return None


def edits_to_diff(repo_path: str | Path, edits: Sequence[Edit]) -> Tuple[str, str]:
    """Turn search/replace edits into a unified diff.

    Args:
        repo_path: Root of the working copy.
        edits: The edits to apply.

    Returns:
        ``(diff_text, error)``; ``error`` is empty on success.
    """
    repo = Path(repo_path)
    results: List[str] = []
    for edit in edits:
        target, error = _safe_path(repo, edit.file)
        if target is None:
            return "", error
        if not target.exists():
            return "", f"The edit targets `{edit.file}` which does not exist in the repository"
        try:
            original = target.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError) as exc:
            return "", f"Could not read `{edit.file}`: {exc}"
        if not edit.old and edit.function:
            snippet = function_source(original, edit.function)
            if not snippet:
                return "", f"Could not find the function `{edit.function}` in `{edit.file}`"
            edit = Edit(file=edit.file, old=snippet, new=edit.new, function=edit.function)
        updated, error = apply_edit(original, edit)
        if error:
            return "", error
        if updated == original:
            logger.info("The edit for %s did not change the file", edit.file)
            continue
        relative = str(target.relative_to(repo.resolve())).replace("\\", "/")
        diff = difflib.unified_diff(
            original.splitlines(keepends=True),
            updated.splitlines(keepends=True),
            fromfile=f"a/{relative}",
            tofile=f"b/{relative}",
            n=3,
        )
        body = "".join(diff)
        if not body:
            continue
        results.append(f"diff --git a/{relative} b/{relative}\n{body}")
    if not results:
        return "", "The edits did not change anything in the repository"
    diff_text = "".join(results)
    if not diff_text.endswith("\n"):
        diff_text += "\n"
    return diff_text, ""
