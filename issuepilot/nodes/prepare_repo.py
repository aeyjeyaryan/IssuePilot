"""Node 2: prepare the working copy of the repository.

For a real issue the repository is cloned from GitHub.  In demo mode the
bundled demo repository is copied into the run directory so the demo never
touches the checked-in sources.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any, Dict

from issuepilot.events import emit
from issuepilot.github_ops import GitHubError, GitHubOps
from issuepilot.patch import ensure_git_repo

logger = logging.getLogger(__name__)

#: Files that are not part of the working copy of a demo repository.
DEMO_EXCLUDES = {"_reference", "__pycache__", ".git", ".pytest_cache"}


def _copy_demo_repo(source: Path, destination: Path) -> Path:
    """Copy a demo repository into the run directory."""
    shutil.copytree(
        source,
        destination,
        ignore=shutil.ignore_patterns(*DEMO_EXCLUDES),
        dirs_exist_ok=True,
    )
    return destination


def prepare_repo_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """Clone (or copy) the repository the agent has to work on."""
    logger.info("node=prepare_repo demo=%s", state.get("demo_mode"))
    emit("log", message="Preparing the repository checkout")

    run_dir = Path(state["run_dir"])
    repo_path = run_dir / "repo"
    try:
        if state.get("demo_mode"):
            source = Path(state["demo_repo_path"])
            if not source.is_dir():
                state["error"] = f"The demo repository {source} does not exist"
                state["fatal"] = True
                emit("error", message=state["error"])
                return state
            _copy_demo_repo(source, repo_path)
        else:
            repo_url = state.get("repo_url") or ""
            if not repo_url:
                state["error"] = "No repository URL could be derived from the issue"
                state["fatal"] = True
                emit("error", message=state["error"])
                return state
            GitHubOps().clone_repository(repo_url, repo_path)

        if not ensure_git_repo(repo_path):
            logger.warning("Could not initialise a git repository in %s", repo_path)

        state["repo_path"] = str(repo_path)
        logger.info("node=prepare_repo path=%s", repo_path)
        emit("log", message=f"Repository ready at {repo_path}")
    except GitHubError as exc:
        state["error"] = str(exc)
        state["fatal"] = True
        logger.error("node=prepare_repo error=%s", exc)
        emit("error", message=str(exc))
    except Exception as exc:  # pragma: no cover - defensive
        state["error"] = f"Could not prepare the repository: {exc}"
        state["fatal"] = True
        logger.error("node=prepare_repo error=%s", exc)
        emit("error", message=state["error"])
    return state
