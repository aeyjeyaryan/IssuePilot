"""Unified diff parsing, validation and application.

The agent asks a language model for a unified diff.  Models are creative, so
this module is deliberately defensive:

* :func:`extract_diff` pulls the diff out of arbitrary model output (fenced
  code blocks, prose, ``git diff`` headers, ...).
* :func:`validate_diff` performs static checks (well formed headers, no
  absolute paths, no path traversal, at least one hunk).
* :func:`apply_unified_diff` applies the patch with ``git apply`` when it is
  available and falls back to a pure-python hunk applier otherwise.

Every failure is returned as a human readable message so the agent can feed it
back to the model instead of crashing.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

_FENCE_RE = re.compile(r"```(?:diff|patch)?\s*\n(.*?)```", re.DOTALL)
_DIFF_HEADER_RE = re.compile(r"^(diff --git |--- |\+\+\+ |@@ )", re.MULTILINE)


@dataclass
class PatchResult:
    """Outcome of a patch application attempt."""

    applied: bool
    error: str = ""
    files_changed: List[str] = field(default_factory=list)
    diff_text: str = ""
    method: str = ""

    def __bool__(self) -> bool:  # pragma: no cover - convenience
        """Truthy when the patch was applied."""
        return self.applied


def extract_diff(text: str) -> str:
    """Extract a unified diff from arbitrary model output.

    Args:
        text: Raw model output.

    Returns:
        The extracted diff (empty string when nothing diff-like was found).
    """
    if not text:
        return ""
    candidates: List[str] = []
    for match in _FENCE_RE.finditer(text):
        candidates.append(match.group(1))
    if not candidates:
        candidates.append(text)
    for candidate in candidates:
        lines = candidate.strip().splitlines()
        if not lines:
            continue
        start = None
        for index, line in enumerate(lines):
            if line.startswith("diff --git ") or line.startswith("--- "):
                start = index
                break
        if start is None:
            continue
        chunk = "\n".join(lines[start:]).strip("\n")
        if "@@" in chunk:
            return chunk + "\n"
    return ""


def validate_diff(diff_text: str) -> List[str]:
    """Statically validate a unified diff.

    Args:
        diff_text: The diff to check.

    Returns:
        A list of human readable problems; empty when the diff looks sane.
    """
    problems: List[str] = []
    if not diff_text.strip():
        return ["The patch is empty"]
    if "diff --git" not in diff_text and "--- " not in diff_text:
        problems.append("The patch does not look like a unified diff (missing `diff --git` / `---` headers)")
    if "--- " not in diff_text or "+++ " not in diff_text:
        problems.append("The patch is missing `---`/`+++` file headers")
    if "@@" not in diff_text:
        problems.append("The patch contains no hunks (`@@ ... @@` headers)")
    for line in diff_text.splitlines():
        if line.startswith("--- ") or line.startswith("+++ "):
            path = line[4:].strip().split("\t")[0]
            if path.startswith("/"):
                problems.append(f"Absolute paths are not allowed in patches: {path}")
            elif ".." in path.replace("\\", "/").split("/"):
                problems.append(f"Path traversal is not allowed in patches: {path}")
    return problems


def _hunk_lines(patchset) -> List[Tuple[str, object]]:  # pragma: no cover - helper
    """Return the hunk objects of a parsed patch set."""
    hunks = []
    for patched_file in patchset:
        hunks.extend(patched_file)
    return hunks


def apply_unified_diff(repo_path: str | Path, diff_text: str, prefer_git: bool = True) -> PatchResult:
    """Apply a unified diff to ``repo_path``.

    Args:
        repo_path: Directory the patch applies to.
        diff_text: The unified diff.
        prefer_git: Try ``git apply`` first (default).

    Returns:
        A :class:`PatchResult` describing what happened.
    """
    repo = Path(repo_path)
    result = PatchResult(applied=False, diff_text=diff_text)

    if not repo.is_dir():
        result.error = f"Repository directory does not exist: {repo}"
        return result

    problems = validate_diff(diff_text)
    if problems:
        result.error = "Invalid patch: " + "; ".join(problems)
        return result

    if prefer_git and shutil.which("git"):
        git_result = _apply_with_git(repo, diff_text)
        if git_result.applied:
            return git_result
        # Fall through to the pure python applier, it may still succeed and
        # its error message is usually clearer than git's.
        python_result = _apply_with_python(repo, diff_text)
        if python_result.applied:
            python_result.method = "python"
            python_result.error = ""
            return python_result
        result.error = python_result.error or git_result.error
        return result

    python_result = _apply_with_python(repo, diff_text)
    python_result.method = "python"
    return python_result


def _apply_with_git(repo: Path, diff_text: str) -> PatchResult:
    """Apply the patch with ``git apply``."""
    result = PatchResult(applied=False, diff_text=diff_text, method="git")
    # The patch file lives outside the repository so it never shows up in the
    # commit or in `git status`.
    handle, patch_name = tempfile.mkstemp(prefix="issuepilot-", suffix=".patch")
    os.close(handle)
    patch_file = Path(patch_name)
    try:
        patch_file.write_text(diff_text, encoding="utf-8")
        check = subprocess.run(
            ["git", "-C", str(repo), "apply", "--check", "-p1", str(patch_file)],
            capture_output=True,
            text=True,
        )
        if check.returncode != 0:
            result.error = _clean_git_error(check.stderr or check.stdout)
            return result
        apply = subprocess.run(
            ["git", "-C", str(repo), "apply", "-p1", str(patch_file)],
            capture_output=True,
            text=True,
        )
        if apply.returncode != 0:
            result.error = _clean_git_error(apply.stderr or apply.stdout)
            return result
        result.applied = True
        result.files_changed = _changed_files(repo)
        return result
    except Exception as exc:  # pragma: no cover - defensive
        result.error = f"git apply failed: {exc}"
        return result
    finally:
        try:
            patch_file.unlink()
        except OSError:
            pass


def _clean_git_error(message: str) -> str:
    """Turn git's output into a single actionable sentence."""
    text = (message or "").strip()
    lines = [line for line in text.splitlines() if line.strip()]
    interesting = [
        line
        for line in lines
        if "error:" in line.lower() or "patch failed" in line.lower() or "does not apply" in line.lower()
    ]
    if interesting:
        return interesting[0].replace("error: ", "").strip()
    return lines[-1] if lines else "git apply failed for an unknown reason"


def _changed_files(repo: Path) -> List[str]:
    """Return the files git reports as modified or new."""
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "status", "--porcelain", "-uall"],
            capture_output=True,
            text=True,
        )
        files = []
        for line in out.stdout.splitlines():
            if len(line) > 3:
                files.append(line[3:].strip())
        return files
    except Exception:  # pragma: no cover - defensive
        return []


def _apply_with_python(repo: Path, diff_text: str) -> PatchResult:
    """Apply the patch with a pure python unified diff applier."""
    result = PatchResult(applied=False, diff_text=diff_text, method="python")
    try:
        from unidiff import PatchSet
    except Exception as exc:  # pragma: no cover - unidiff is a hard dependency
        result.error = f"unidiff is not available: {exc}"
        return result

    try:
        patchset = PatchSet(diff_text)
    except Exception as exc:
        result.error = f"Could not parse the patch: {exc}"
        return result

    changed: List[str] = []
    # First pass: validate every file, so we never leave the tree half patched.
    plans = []
    for patched_file in patchset:
        target = _target_path(repo, patched_file)
        if target is None:
            result.error = f"Could not determine the target file for {patched_file.path}"
            return result
        if not target.exists():
            result.error = f"The patch targets `{patched_file.path}` which does not exist in the repository"
            return result
        try:
            original = target.read_text(encoding="utf-8").splitlines(keepends=True)
        except (UnicodeDecodeError, OSError) as exc:
            result.error = f"Could not read `{patched_file.path}`: {exc}"
            return result
        plans.append((target, patched_file, original))

    for target, patched_file, original in plans:
        new_lines, error = _apply_hunks(original, list(patched_file))
        if error:
            result.error = error
            return result
        try:
            target.write_text("".join(new_lines), encoding="utf-8")
        except OSError as exc:
            result.error = f"Could not write `{patched_file.path}`: {exc}"
            return result
        changed.append(patched_file.path)

    result.applied = True
    result.files_changed = changed
    return result


def _target_path(repo: Path, patched_file) -> Optional[Path]:
    """Resolve the file a patched file refers to inside ``repo``."""
    source = patched_file.source_file or patched_file.path
    target = patched_file.target_file or patched_file.path
    for candidate in (target, source):
        cleaned = str(candidate).replace("\\", "/")
        if cleaned.startswith("a/") or cleaned.startswith("b/"):
            cleaned = cleaned[2:]
        if cleaned in ("/dev/null", "dev/null"):
            continue
        cleaned = cleaned.split("\t")[0].strip()
        if not cleaned:
            continue
        path = (repo / cleaned).resolve()
        try:
            path.relative_to(repo.resolve())
        except ValueError:
            logger.warning("Patch path escapes the repository: %s", cleaned)
            continue
        return path
    return None


def _apply_hunks(original: Sequence[str], hunks: Sequence) -> Tuple[List[str], str]:
    """Apply ``hunks`` to ``original`` lines.

    Args:
        original: The original file content (with line endings).
        hunks: :class:`unidiff` hunk objects.

    Returns:
        ``(new_lines, error)``; ``error`` is empty on success.
    """
    result: List[str] = []
    cursor = 0
    for hunk in hunks:
        source_start = hunk.source_start - 1
        if source_start < cursor:
            source_start = cursor
        match_index = _locate(original, hunk, source_start)
        if match_index is None:
            return original, (
                f"Could not apply hunk @@ -{hunk.source_start},{hunk.source_length} "
                f"+{hunk.target_start},{hunk.target_length} @@: the context does not match the file"
            )
        result.extend(original[cursor:match_index])
        for line in hunk:
            if line.is_context:
                if match_index >= len(original) or original[match_index].rstrip("\n") != line.value.rstrip("\n"):
                    return original, (
                        f"Could not apply hunk @@ -{hunk.source_start} @@: "
                        f"context line {line.value.strip()!r} does not match the file"
                    )
                result.append(original[match_index])
                match_index += 1
            elif line.is_removed:
                if match_index >= len(original) or original[match_index].rstrip("\n") != line.value.rstrip("\n"):
                    return original, (
                        f"Could not apply hunk @@ -{hunk.source_start} @@: "
                        f"line to remove {line.value.strip()!r} does not match the file"
                    )
                match_index += 1
            elif line.is_added:
                result.append(line.value if line.value.endswith("\n") else line.value + "\n")
        cursor = match_index
    result.extend(original[cursor:])
    return result, ""


def _locate(original: Sequence[str], hunk, start: int) -> Optional[int]:
    """Find where a hunk's source lines start inside ``original``."""
    expected = [line.value.rstrip("\n") for line in hunk if line.is_context or line.is_removed]
    if not expected:
        return min(start, len(original))
    limit = len(original) - len(expected)
    for offset in range(0, max(0, limit) + 1):
        index = start + offset
        if index > limit:
            break
        if [line.rstrip("\n") for line in original[index : index + len(expected)]] == expected:
            return index
    # Also try from the beginning of the file (the model may have guessed the
    # line numbers wrong).
    for index in range(0, max(0, limit) + 1):
        if [line.rstrip("\n") for line in original[index : index + len(expected)]] == expected:
            return index
    return None


def diff_of_files(repo_path: str | Path, files: Sequence[str]) -> str:
    """Return a ``git diff`` for the given files (best effort)."""
    repo = Path(repo_path)
    if not shutil.which("git"):
        return ""
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "diff", "--"] + list(files),
            capture_output=True,
            text=True,
        )
        return out.stdout
    except Exception:  # pragma: no cover - defensive
        return ""


def ensure_git_repo(repo_path: str | Path) -> bool:
    """Initialise ``repo_path`` as a git repository when it is not one yet."""
    repo = Path(repo_path)
    if (repo / ".git").exists():
        return True
    if not shutil.which("git"):
        return False
    try:
        subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(repo),
                "-c",
                "user.email=issuepilot@example.invalid",
                "-c",
                "user.name=IssuePilot",
                "commit",
                "-q",
                "-m",
                "Snapshot before agent changes",
            ],
            check=True,
            capture_output=True,
        )
        return True
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Could not initialise a git repository in %s: %s", repo, exc)
        return False


def write_file(repo_path: str | Path, relative_path: str, content: str) -> Path:
    """Write ``content`` to ``relative_path`` inside ``repo_path``.

    Returns:
        The path of the written file.
    """
    repo = Path(repo_path)
    target = repo / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return target


def default_branch(repo_path: str | Path) -> str:
    """Return the default branch of the repository (best effort)."""
    repo = Path(repo_path)
    if not shutil.which("git"):
        return "main"
    for command in (
        ["git", "-C", str(repo), "symbolic-ref", "--short", "refs/remotes/origin/HEAD"],
        ["git", "-C", str(repo), "rev-parse", "--abbrev-ref", "origin/HEAD"],
    ):
        try:
            out = subprocess.run(command, capture_output=True, text=True)
            value = out.stdout.strip()
            if value and value != "HEAD":
                return value.split("/")[-1]
        except Exception:  # pragma: no cover - defensive
            continue
    try:
        out = subprocess.run(["git", "-C", str(repo), "branch", "--show-current"], capture_output=True, text=True)
        value = out.stdout.strip()
        if value:
            return value
    except Exception:  # pragma: no cover - defensive
        pass
    return "main"


def branch_exists(repo_path: str | Path, branch: str) -> bool:
    """Return ``True`` when ``branch`` exists locally."""
    if not shutil.which("git"):
        return False
    out = subprocess.run(
        ["git", "-C", str(Path(repo_path)), "rev-parse", "--verify", branch],
        capture_output=True,
        text=True,
    )
    return out.returncode == 0


def create_branch(repo_path: str | Path, branch: str) -> Tuple[bool, str]:
    """Create and check out a new branch."""
    repo = Path(repo_path)
    if not shutil.which("git"):
        return False, "git is not installed"
    if branch_exists(repo, branch):
        out = subprocess.run(
            ["git", "-C", str(repo), "checkout", branch], capture_output=True, text=True
        )
        if out.returncode != 0:
            return False, out.stderr.strip()
        return True, f"Switched to existing branch {branch}"
    out = subprocess.run(
        ["git", "-C", str(repo), "checkout", "-b", branch], capture_output=True, text=True
    )
    if out.returncode != 0:
        return False, out.stderr.strip() or "Could not create the branch"
    return True, f"Created branch {branch}"


def current_branch(repo_path: str | Path) -> str:
    """Return the currently checked out branch (empty string on failure)."""
    if not shutil.which("git"):
        return ""
    out = subprocess.run(
        ["git", "-C", str(Path(repo_path)), "branch", "--show-current"], capture_output=True, text=True
    )
    return out.stdout.strip()


def stage_and_commit(repo_path: str | Path, message: str) -> Tuple[bool, str]:
    """Stage every change and create a commit."""
    repo = Path(repo_path)
    if not shutil.which("git"):
        return False, "git is not installed"
    add = subprocess.run(["git", "-C", str(repo), "add", "-A"], capture_output=True, text=True)
    if add.returncode != 0:
        return False, add.stderr.strip()
    status = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain"], capture_output=True, text=True
    )
    if not status.stdout.strip():
        return False, "There is nothing to commit"
    commit = subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.email=issuepilot@example.invalid",
            "-c",
            "user.name=IssuePilot",
            "commit",
            "-m",
            message,
        ],
        capture_output=True,
        text=True,
    )
    if commit.returncode != 0:
        return False, commit.stderr.strip() or "git commit failed"
    return True, commit.stdout.strip().splitlines()[-1] if commit.stdout.strip() else "committed"


def untracked_files(repo_path: str | Path) -> List[str]:
    """Return the untracked files of the repository."""
    if not shutil.which("git"):
        return []
    out = subprocess.run(
        ["git", "-C", str(Path(repo_path)), "ls-files", "--others", "--exclude-standard"],
        capture_output=True,
        text=True,
    )
    return [line for line in out.stdout.splitlines() if line.strip()]


def env_without_secrets() -> dict:
    """Return a copy of the environment with obvious secrets removed."""
    safe = dict(os.environ)
    for key in list(safe):
        if "TOKEN" in key.upper() or "SECRET" in key.upper() or "PASSWORD" in key.upper():
            safe.pop(key, None)
    return safe
