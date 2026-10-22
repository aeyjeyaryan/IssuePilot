"""Tests for the search/replace edit format used to talk to the model."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from issuepilot.edits import Edit, apply_edit, edits_to_diff, parse_edits

SAMPLE = '''"""A small arithmetic helper module."""


def add(a, b):
    """Return the sum of ``a`` and ``b``."""
    return a - b


def multiply(a, b):
    """Return the product of ``a`` and ``b``."""
    return a * b
'''


def test_parse_edits_reads_a_fenced_json_object() -> None:
    payload = {"edits": [{"file": "calc.py", "old": "return a - b", "new": "return a + b"}]}
    raw = "Here you go:\n```json\n" + json.dumps(payload) + "\n```\n"
    edits = parse_edits(raw)
    assert edits == [Edit(file="calc.py", old="return a - b", new="return a + b")]


def test_parse_edits_accepts_a_bare_list() -> None:
    payload = [{"file": "calc.py", "old": "a", "new": "b"}]
    edits = parse_edits(json.dumps(payload))
    assert len(edits) == 1
    assert edits[0].file == "calc.py"


def test_parse_edits_accepts_lenient_keys() -> None:
    raw = json.dumps({"edits": [{"path": "calc.py", "search": "a", "replace": "b"}]})
    edits = parse_edits(raw)
    assert edits[0].file == "calc.py"
    assert edits[0].old == "a"
    assert edits[0].new == "b"


def test_parse_edits_supports_the_function_format() -> None:
    raw = json.dumps({"edits": [{"file": "calc.py", "function": "add", "new": "def add(a, b):\n    return a + b"}]})
    edits = parse_edits(raw)
    assert edits[0].function == "add"
    assert edits[0].old == ""


def test_parse_edits_ignores_garbage() -> None:
    assert parse_edits("no json at all") == []
    assert parse_edits("") == []


def test_apply_edit_replaces_an_exact_snippet() -> None:
    updated, error = apply_edit(SAMPLE, Edit("calc.py", "    return a - b", "    return a + b"))
    assert not error
    assert "return a + b" in updated
    assert "return a - b" not in updated


def test_apply_edit_ignores_the_indentation_of_the_snippet() -> None:
    updated, error = apply_edit(SAMPLE, Edit("calc.py", "return a - b", "return a + b"))
    assert not error
    assert "    return a + b" in updated


def test_apply_edit_keeps_lines_the_model_only_re_quoted() -> None:
    broken = 'def add(a, b):\n    ""Return the sum of `a` and `b`."" \n    return a - b'
    fixed = 'def add(a, b):\n    ""Return the sum of `a` and `b`."" \n    return a + b'
    updated, error = apply_edit(SAMPLE, Edit("calc.py", broken, fixed))
    assert not error
    # The docstring of the file is untouched, only the buggy line changed.
    assert '"""Return the sum of ``a`` and ``b``."""' in updated
    assert "    return a + b" in updated


def test_apply_edit_reports_a_missing_snippet() -> None:
    updated, error = apply_edit(SAMPLE, Edit("calc.py", 'raise ValueError("boom")', "x"))
    assert "not found" in error
    assert updated == SAMPLE


def test_apply_edit_reports_a_no_op() -> None:
    _, error = apply_edit(SAMPLE, Edit("calc.py", "    return a - b", "    return a - b"))
    assert "does not change anything" in error


def test_edits_to_diff_produces_a_unified_diff(tmp_path: Path) -> None:
    (tmp_path / "calc.py").write_text(SAMPLE, encoding="utf-8")
    diff, error = edits_to_diff(tmp_path, [Edit("calc.py", "    return a - b", "    return a + b")])
    assert not error
    assert diff.startswith("diff --git a/calc.py b/calc.py")
    assert "-    return a - b" in diff
    assert "+    return a + b" in diff


def test_edits_to_diff_rejects_path_traversal(tmp_path: Path) -> None:
    diff, error = edits_to_diff(tmp_path, [Edit("../evil.py", "a", "b")])
    assert "traversal" in error.lower()
    assert diff == ""


def test_edits_to_diff_reports_a_missing_file(tmp_path: Path) -> None:
    diff, error = edits_to_diff(tmp_path, [Edit("nope.py", "a", "b")])
    assert "does not exist" in error
    assert diff == ""


def test_edits_to_diff_can_rewrite_a_whole_function(tmp_path: Path) -> None:
    (tmp_path / "calc.py").write_text(SAMPLE, encoding="utf-8")
    new_function = "def add(a, b):\n    return a + b"
    diff, error = edits_to_diff(
        tmp_path, [Edit("calc.py", "", new_function, function="add")]
    )
    assert not error
    assert "-    return a - b" in diff
    assert "+    return a + b" in diff


def test_edits_to_diff_reports_an_unknown_function(tmp_path: Path) -> None:
    (tmp_path / "calc.py").write_text(SAMPLE, encoding="utf-8")
    diff, error = edits_to_diff(tmp_path, [Edit("calc.py", "", "def nope():\n    pass", function="nope")])
    assert "Could not find the function" in error
    assert diff == ""


@pytest.mark.parametrize("bad", ["", "   "])
def test_apply_edit_rejects_an_empty_snippet(bad: str) -> None:
    _, error = apply_edit(SAMPLE, Edit("calc.py", bad, "x"))
    assert "empty" in error
