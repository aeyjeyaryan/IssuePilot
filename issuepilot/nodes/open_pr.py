"""Node 8: open the pull request (or describe what would happen in dry runs)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict

from issuepilot.events import STEP_START, emit
from issuepilot.github_ops import GitHubOps
from issuepilot.patch import (
    create_branch,
    current_branch,
    default_branch,
    stage_and_commit,
    untracked_files,
)

logger = logging.getLogger(__name__)


def _branch_name(state: Dict[str, Any]) -> str:
    """Return the name of the branch the fix is pushed to."""
    issue = state.get("issue", {})
    number = issue.get("number", 0)
    slug = "-".join(
        word
        for word in str(issue.get("title", "fix")).lower().split()
        if word.isalnum()
    )[:40].strip("-")
    return f"issuepilot/issue-{number}-{slug or 'fix'}"


def open_pr_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """Commit the fix on a new branch and open the pull request.

    In dry-run mode (and in demo mode) nothing is pushed and no pull request
    is opened; the diff is reported instead.
    """
    dry_run = bool(state.get("dry_run"))
    logger.info("node=open_pr dry_run=%s", dry_run)
    emit(STEP_START, step="open_pr")
    emit("log", message="Preparing the pull request" if not dry_run else "Dry run: skipping the pull request")

    repo_path = Path(state["repo_path"])
    issue = state.get("issue", {})
    branch = _branch_name(state)

    try:
        base = default_branch(repo_path)
        if branch == base:
            branch = f"issuepilot/issue-{issue.get('number', 0)}-fix"
        state["branch"] = branch
        state["base_branch"] = base

        if dry_run or state.get("demo_mode"):
            state["pr_created"] = False
            state["pr_url"] = ""
            state["pr_message"] = (
                "DRY RUN: the branch and the pull request were not created."
                if dry_run
                else "DEMO: the pull request was not created (demo mode never touches GitHub)."
            )
            emit("log", message=state["pr_message"])
            return state

        created, message = create_branch(repo_path, branch)
        if not created:
            state["error"] = f"Could not create the branch: {message}"
            emit("error", message=state["error"])
            return state
        if current_branch(repo_path) == base:
            state["error"] = f"Refusing to push to the default branch {base}"
            emit("error", message=state["error"])
            return state

        untracked = untracked_files(repo_path)
        committed, commit_message = stage_and_commit(
            repo_path, f"Fix: {issue.get('title', 'bug reported in the issue')}"
        )
        if not committed:
            state["error"] = f"Could not commit the changes: {commit_message}"
            emit("error", message=state["error"])
            return state
        state["commit"] = commit_message
        state["added_files"] = untracked
        emit("log", message=f"Committed the fix on {branch}")

        gh = GitHubOps()
        ok, pr_message = gh.open_pull_request(
            workdir=repo_path,
            owner=issue.get("owner", ""),
            repo=issue.get("repo", ""),
            branch=branch,
            title=f"Fix: {issue.get('title', 'bug reported in the issue')}",
            body=state.get("pr_body", ""),
            base=base,
            dry_run=False,
        )
        state["pr_created"] = ok
        state["pr_url"] = pr_message if ok else ""
        state["pr_message"] = pr_message
        if ok:
            logger.info("node=open_pr pr=%s", pr_message)
            emit("log", message=f"Pull request opened: {pr_message}")
        else:
            state["error"] = f"The pull request could not be opened: {pr_message}"
            logger.error("node=open_pr error=%s", pr_message)
            emit("error", message=state["error"])
    except Exception as exc:  # pragma: no cover - defensive
        state["error"] = f"Could not open the pull request: {exc}"
        logger.error("node=open_pr error=%s", exc)
        emit("error", message=state["error"])
    return state
