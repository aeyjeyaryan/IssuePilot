"""Node 1: fetch the issue from GitHub (or load the local demo issue)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict

from issuepilot.events import STEP_START, emit
from issuepilot.github_ops import GitHubError, IssueInfo

logger = logging.getLogger(__name__)


def _load_demo_issue(issue_path: Path) -> IssueInfo:
    """Load an issue description from a local markdown file."""
    if not issue_path.exists():
        raise FileNotFoundError(f"The demo issue {issue_path} does not exist")
    text = issue_path.read_text(encoding="utf-8")
    title = issue_path.stem
    body = text
    for line in text.splitlines():
        if line.startswith("# "):
            title = line[2:].strip()
            break
    repo_name = issue_path.parent.name
    return IssueInfo(
        number=1,
        title=title,
        body=body,
        url=issue_path.as_uri(),
        owner="demo",
        repo=repo_name,
    )


def fetch_issue_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """Fetch the issue the agent has to fix.

    In demo mode the issue is read from the local ``ISSUE.md`` file; otherwise
    it is fetched from GitHub.
    """
    logger.info("node=fetch_issue url=%s", state.get("issue_url"))
    emit(STEP_START, step="fetch_issue")
    emit("log", message=f"Fetching issue {state.get('issue_url')}")

    try:
        if state.get("demo_mode"):
            issue_path = Path(state["demo_issue_path"])
            issue = _load_demo_issue(issue_path)
        else:
            from issuepilot.github_ops import GitHubOps

            issue = GitHubOps().fetch_issue(state["issue_url"])
        state["issue"] = {
            "number": issue.number,
            "title": issue.title,
            "body": issue.body,
            "url": issue.url,
            "owner": issue.owner,
            "repo": issue.repo,
            "labels": issue.labels,
            "comments": issue.comments,
        }
        state["repo_url"] = issue.clone_url
        logger.info("node=fetch_issue title=%r", issue.title)
        emit("log", message=f"Issue #{issue.number}: {issue.title}")
    except GitHubError as exc:
        state["error"] = str(exc)
        state["fatal"] = True
        logger.error("node=fetch_issue error=%s", exc)
        emit("error", message=str(exc))
    except Exception as exc:  # pragma: no cover - defensive
        state["error"] = f"Could not fetch the issue: {exc}"
        state["fatal"] = True
        logger.error("node=fetch_issue error=%s", exc)
        emit("error", message=state["error"])
    return state
