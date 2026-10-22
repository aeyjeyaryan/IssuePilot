"""GitHub operations: issue lookup, cloning and pull request creation.

Two backends are supported:

* the ``gh`` command line client (preferred, it is what most contributors
  already have configured), and
* `PyGithub <https://pygithub.readthedocs.io>`_ using ``GITHUB_TOKEN``.

Safety rules that are enforced here:

* the agent never pushes to the default branch of the upstream repository,
* the change is pushed to a **fork** owned by the authenticated user,
* the pull request is opened against the upstream repository from that fork
  branch.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from issuepilot.config import settings

logger = logging.getLogger(__name__)

#: GitHub issue URLs, optionally with a trailing fragment or query string.
_ISSUE_URL_RE = re.compile(
    r"^https?://(?:www\.)?github\.com/(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+)/issues/(?P<number>\d+)"
    r"(?:[/?#].*)?$"
)


@dataclass
class IssueInfo:
    """The parts of a GitHub issue the agent needs."""

    number: int
    title: str
    body: str
    url: str
    owner: str = ""
    repo: str = ""
    labels: List[str] = field(default_factory=list)
    comments: List[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        """Title + body, used as the retrieval query."""
        return f"{self.title}\n\n{self.body}".strip()

    @property
    def clone_url(self) -> str:
        """HTTPS clone URL of the repository the issue belongs to."""
        return f"https://github.com/{self.owner}/{self.repo}.git"


class GitHubError(RuntimeError):
    """Raised for every user facing GitHub failure."""


def parse_issue_url(url: str) -> Tuple[str, str, int]:
    """Parse a GitHub issue URL.

    Args:
        url: The issue URL.

    Returns:
        ``(owner, repo, number)``.

    Raises:
        GitHubError: If the URL is not a GitHub issue URL.
    """
    match = _ISSUE_URL_RE.match(url.strip())
    if not match:
        raise GitHubError(
            f"'{url}' is not a valid GitHub issue URL. "
            "Expected something like https://github.com/owner/repo/issues/123"
        )
    return match.group("owner"), match.group("repo"), int(match.group("number"))


def gh_available() -> bool:
    """Return ``True`` when the ``gh`` CLI is installed."""
    return shutil.which("gh") is not None


def _run(cmd: List[str], cwd: Optional[str] = None, timeout: int = 120) -> subprocess.CompletedProcess:
    """Run a command and capture its output."""
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)


class GitHubOps:
    """High level GitHub operations used by the agent."""

    def __init__(self, token: Optional[str] = None):
        """Create the helper."""
        self.token = token if token is not None else settings.github_token
        self._github = None
        if self.token:
            try:
                from github import Github

                self._github = Github(self.token)
            except Exception as exc:  # pragma: no cover - optional dependency
                logger.warning("PyGithub is unavailable: %s", exc)

    # -- issue -------------------------------------------------------------
    def fetch_issue(self, issue_url: str) -> IssueInfo:
        """Fetch an issue from GitHub.

        Args:
            issue_url: URL of the issue.

        Returns:
            The issue information.

        Raises:
            GitHubError: If the issue cannot be retrieved.
        """
        owner, repo, number = parse_issue_url(issue_url)
        if gh_available():
            try:
                result = _run(
                    [
                        "gh",
                        "issue",
                        "view",
                        str(number),
                        "--repo",
                        f"{owner}/{repo}",
                        "--json",
                        "title,body,url,labels,comments",
                    ]
                )
                if result.returncode == 0:
                    import json

                    payload = json.loads(result.stdout or "{}")
                    labels = [label.get("name", "") for label in payload.get("labels", [])]
                    comments = [
                        comment.get("body", "")
                        for comment in payload.get("comments", [])
                        if isinstance(comment, dict)
                    ]
                    return IssueInfo(
                        number=number,
                        title=payload.get("title", ""),
                        body=payload.get("body", "") or "",
                        url=payload.get("url", issue_url),
                        owner=owner,
                        repo=repo,
                        labels=labels,
                        comments=comments,
                    )
                logger.warning("gh issue view failed: %s", (result.stderr or "").strip())
            except Exception as exc:
                logger.warning("gh issue view raised: %s", exc)

        if self._github is not None:
            try:
                repository = self._github.get_repo(f"{owner}/{repo}")
                issue = repository.get_issue(number)
                return IssueInfo(
                    number=issue.number,
                    title=issue.title,
                    body=issue.body or "",
                    url=issue.html_url,
                    owner=owner,
                    repo=repo,
                    labels=[label.name for label in issue.labels],
                )
            except Exception as exc:
                raise GitHubError(f"Could not fetch issue {issue_url}: {exc}") from exc

        raise GitHubError(
            "No GitHub access configured. Install the GitHub CLI (`gh auth login`) "
            "or set GITHUB_TOKEN in your environment to fetch issues and open pull requests."
        )

    # -- repository --------------------------------------------------------
    def clone_repository(self, clone_url: str, destination: str | Path) -> Path:
        """Clone a repository.

        Args:
            clone_url: The clone URL.
            destination: Directory the repository is cloned into.

        Returns:
            The path of the clone.

        Raises:
            GitHubError: If the clone fails.
        """
        if not shutil.which("git"):
            raise GitHubError("git is not installed, cannot clone the repository")
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        result = _run(["git", "clone", "--depth", "50", clone_url, str(destination)], timeout=600)
        if result.returncode != 0:
            raise GitHubError(
                f"Could not clone {clone_url}: {(result.stderr or result.stdout or '').strip()[:500]}"
            )
        return destination

    # -- pull request ------------------------------------------------------
    def ensure_fork(self, owner: str, repo: str, workdir: str | Path) -> Tuple[bool, str]:
        """Make sure a fork of ``owner/repo`` exists and is configured as ``fork``.

        Returns:
            ``(ok, message)``.
        """
        if gh_available():
            result = _run(["gh", "repo", "fork", f"{owner}/{repo}", "--clone=false", "--remote"], cwd=str(workdir))
            if result.returncode == 0:
                return True, (result.stdout or "").strip()
            message = (result.stderr or "").strip()
            if "already exists" in message.lower():
                return True, message
            return False, message or "Could not create the fork"

        if self._github is not None:
            try:
                source = self._github.get_repo(f"{owner}/{repo}")
                parent = source.parent
                if parent is not None:
                    return True, f"Using the existing fork {parent.full_name}"
                source.create_fork()
                return True, f"Created a fork of {owner}/{repo}"
            except Exception as exc:
                return False, f"Could not create the fork: {exc}"
        return False, "No GitHub credentials available to create a fork"

    def open_pull_request(
        self,
        workdir: str | Path,
        owner: str,
        repo: str,
        branch: str,
        title: str,
        body: str,
        base: str = "",
        dry_run: bool = False,
    ) -> Tuple[bool, str]:
        """Push the branch to a fork and open a pull request.

        Args:
            workdir: The local clone.
            owner: Upstream repository owner.
            repo: Upstream repository name.
            branch: The branch holding the fix.
            title: Pull request title.
            body: Pull request body.
            base: Base branch of the pull request (defaults to the default branch).
            dry_run: When ``True`` nothing is pushed and no PR is opened.

        Returns:
            ``(success, message)`` where the message is the PR URL on success.
        """
        workdir = Path(workdir)
        base = base or "main"
        if dry_run:
            logger.info("Dry run: not pushing the branch and not opening a pull request")
            return True, "DRY RUN: the pull request was not opened (the diff is shown instead)"

        fork_ok, fork_message = self.ensure_fork(owner, repo, workdir)
        if not fork_ok:
            return False, fork_message

        fork_remote = self._fork_remote(workdir, owner, repo)
        if not fork_remote:
            return False, "Could not determine the fork remote to push to"

        push = _run(["git", "push", "--set-upstream", fork_remote, branch], cwd=str(workdir), timeout=300)
        if push.returncode != 0:
            return False, f"Could not push the branch: {(push.stderr or push.stdout or '').strip()[:400]}"

        head = f"{self._fork_owner(owner, repo)}:{branch}"
        if gh_available():
            result = _run(
                [
                    "gh",
                    "pr",
                    "create",
                    "--repo",
                    f"{owner}/{repo}",
                    "--head",
                    head,
                    "--base",
                    base,
                    "--title",
                    title,
                    "--body",
                    body,
                ],
                cwd=str(workdir),
                timeout=180,
            )
            if result.returncode == 0:
                url = (result.stdout or "").strip().splitlines()[-1]
                return True, url
            return False, (result.stderr or result.stdout or "").strip()[:600]

        if self._github is not None:
            try:
                upstream = self._github.get_repo(f"{owner}/{repo}")
                pull = upstream.create_pull(title=title, body=body, head=head, base=base)
                return True, pull.html_url
            except Exception as exc:
                return False, f"Could not open the pull request: {exc}"
        return False, "Neither the gh CLI nor PyGithub is available to open the pull request"

    # -- helpers -----------------------------------------------------------
    def _fork_remote(self, workdir: Path, owner: str, repo: str) -> str:
        """Return the name of the remote that points at the fork."""
        remotes = _run(["git", "remote", "-v"], cwd=str(workdir)).stdout
        names = []
        for line in remotes.splitlines():
            parts = line.split()
            if len(parts) >= 2:
                names.append((parts[0], parts[1]))
        for name, url in names:
            if name in {"fork", "origin-fork"}:
                return name
        for name, url in names:
            if f"/{owner}/" in url and repo in url:
                return name
        return ""

    def _fork_owner(self, owner: str, repo: str) -> str:
        """Return the owner of the fork (the authenticated user by default)."""
        if settings.github_user:
            return settings.github_user
        if gh_available():
            result = _run(["gh", "api", "user", "--jq", ".login"])
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
        return owner
