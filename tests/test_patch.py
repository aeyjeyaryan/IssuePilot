"""Tests for the unified diff parser, validator and applier."""

from __future__ import annotations

import subprocess
from pathlib import Path

from issuepilot.patch import (
    apply_unified_diff,
    create_branch,
    current_branch,
    default_branch,
    extract_diff,
    stage_and_commit,
    validate_diff,
)

GOOD_PATCH = """diff --git a/mod.py b/mod.py
index 1111111..2222222 100644
--- a/mod.py
+++ b/mod.py
@@ -1,2 +1,2 @@
 def f(x):
-    return x - 1
+    return x + 1
"""


def _make_repo(tmp_path: Path) -> Path:
    (tmp_path / "mod.py").write_text("def f(x):\n    return x - 1\n")
    subprocess.run(["git", "-C", str(tmp_path), "init", "-q"], capture_output=True)
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], capture_output=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(tmp_path),
            "-c",
            "user.email=t@example.invalid",
            "-c",
            "user.name=t",
            "commit",
            "-q",
            "-m",
            "init",
        ],
        capture_output=True,
    )
    return tmp_path


# -- extract_diff ------------------------------------------------------------
def test_extract_diff_from_a_fenced_block() -> None:
    text = "Sure!\n```diff\ndiff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-a\n+b\n```\nHope that helps."
    diff = extract_diff(text)

    assert diff.startswith("diff --git")
    assert "+b" in diff


def test_extract_diff_from_raw_output() -> None:
    diff = extract_diff(GOOD_PATCH)
    assert "diff --git" in diff


def test_extract_diff_returns_empty_for_prose() -> None:
    assert extract_diff("I am not able to help with that.") == ""


# -- validate_diff -----------------------------------------------------------
def test_validate_accepts_a_good_diff() -> None:
    assert validate_diff(GOOD_PATCH) == []


def test_validate_rejects_an_empty_patch() -> None:
    assert validate_diff("") == ["The patch is empty"]


def test_validate_rejects_a_patch_without_hunks() -> None:
    problems = validate_diff("diff --git a/x b/x\n--- a/x\n+++ b/x\n")
    assert any("hunks" in problem for problem in problems)


def test_validate_rejects_absolute_paths() -> None:
    problems = validate_diff("--- /etc/passwd\n+++ /etc/passwd\n@@ -1 +1 @@\n-a\n+b\n")
    assert any("Absolute paths" in problem for problem in problems)


def test_validate_rejects_path_traversal() -> None:
    problems = validate_diff("--- a/../../etc/passwd\n+++ b/../../etc/passwd\n@@ -1 +1 @@\n-a\n+b\n")
    assert any("traversal" in problem.lower() for problem in problems)


# -- apply_unified_diff ------------------------------------------------------
def test_apply_changes_the_file(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    result = apply_unified_diff(repo, GOOD_PATCH)

    assert result.applied, result.error
    assert (repo / "mod.py").read_text() == "def f(x):\n    return x + 1\n"
    assert result.files_changed


def test_apply_reports_a_context_mismatch(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    broken = GOOD_PATCH.replace("return x - 1", "return zzz + 1")
    result = apply_unified_diff(repo, broken)

    assert not result.applied
    assert result.error
    assert (repo / "mod.py").read_text() == "def f(x):\n    return x - 1\n"


def test_apply_reports_a_missing_target_file(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    patch = GOOD_PATCH.replace("a/mod.py", "a/nope.py").replace("b/mod.py", "b/nope.py")
    result = apply_unified_diff(repo, patch)

    assert not result.applied
    assert "does not exist" in result.error


def test_apply_rejects_an_invalid_diff(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    result = apply_unified_diff(repo, "this is not a diff")

    assert not result.applied
    assert "Invalid patch" in result.error


def test_apply_reports_a_missing_repository() -> None:
    result = apply_unified_diff("/definitely/not/here", GOOD_PATCH)

    assert not result.applied
    assert "does not exist" in result.error


def test_apply_leaves_no_temporary_files(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    apply_unified_diff(repo, GOOD_PATCH)

    assert not (repo / ".issuepilot.patch").exists()


def test_python_applier_handles_a_wrong_line_number(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    patch = GOOD_PATCH.replace("@@ -1,2 +1,2 @@", "@@ -7,2 +7,2 @@")
    result = apply_unified_diff(repo, patch, prefer_git=False)

    assert result.applied, result.error
    assert (repo / "mod.py").read_text() == "def f(x):\n    return x + 1\n"


# -- git helpers -------------------------------------------------------------
def test_create_branch_never_touches_the_current_branch(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    before = current_branch(repo)

    ok, message = create_branch(repo, "issuepilot/issue-1-fix")

    assert ok, message
    assert current_branch(repo) == "issuepilot/issue-1-fix"
    assert before != current_branch(repo)


def test_default_branch_is_detected(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    assert default_branch(repo)


def test_stage_and_commit_reports_when_there_is_nothing_to_commit(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    ok, message = stage_and_commit(repo, "no changes")

    assert not ok
    assert "nothing to commit" in message.lower()
