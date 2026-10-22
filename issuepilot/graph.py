"""The IssuePilot LangGraph workflow.

The graph mirrors the steps of the issue-to-PR pipeline:

.. code-block:: text

    fetch_issue -> prepare_repo -> index_codebase -> write_failing_test
        -> draft_fix -> apply_patch -> run_tests -> open_pr -> finalize
                            ^                     |
                            |      (retry)        |  (tests failed)
                            +---------------------+

Both ``apply_patch`` and ``run_tests`` have conditional edges: when a patch
does not apply, or when the tests still fail, the agent goes back to
``draft_fix`` with the error output in the prompt.  After
:data:`issuepilot.config.settings.max_attempts` attempts the graph routes to
``finalize``, which produces the "gave up" report instead of crashing.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, TypedDict

from langgraph.graph import END, StateGraph

from issuepilot.config import settings
from issuepilot.events import emit
from issuepilot.nodes.apply_patch import apply_patch_node
from issuepilot.nodes.draft_fix import draft_fix_node
from issuepilot.nodes.fetch_issue import fetch_issue_node
from issuepilot.nodes.finalize import finalize_node
from issuepilot.nodes.index_codebase import index_codebase_node
from issuepilot.nodes.open_pr import open_pr_node
from issuepilot.nodes.prepare_repo import prepare_repo_node
from issuepilot.nodes.run_tests import run_tests_node
from issuepilot.nodes.write_failing_test import write_failing_test_node

logger = logging.getLogger(__name__)


class AgentState(TypedDict, total=False):
    """The state shared by every node of the graph."""

    # inputs
    issue_url: str
    repo_url: str
    dry_run: bool
    demo_mode: bool
    demo_repo_path: str
    demo_issue_path: str
    run_dir: str
    llm: Any

    # intermediate results
    issue: Dict[str, Any]
    repo_path: str
    retriever: Any
    store: str
    chunks_count: int
    relevant_chunks: List[Dict[str, Any]]
    failing_test: str
    failing_test_path: str
    patch: str
    patch_problems: List[str]
    patch_applied: bool
    patch_error: str
    patch_method: str
    files_changed: List[str]
    sandbox_ok: bool
    sandbox_output: str
    sandbox_backend: str
    sandbox_duration: float
    test_summary: str
    failing_tests: List[str]
    attempts: int

    # final results
    branch: str
    base_branch: str
    commit: str
    added_files: List[str]
    pr_created: bool
    pr_url: str
    pr_message: str
    pr_body: str
    root_cause: str
    outcome: str
    diff: str
    report: str
    error: str


def _max_attempts(state: Dict[str, Any]) -> int:
    """Return how many attempts the agent is allowed to make."""
    return int(state.get("max_attempts") or settings.max_attempts)


def route_after_patch(state: Dict[str, Any]) -> str:
    """Decide what to do after the patch was applied (or failed to apply)."""
    if state.get("fatal"):
        return "finalize"
    if state.get("patch_applied"):
        return "run_tests"
    if state.get("attempts", 0) < _max_attempts(state):
        logger.info("The patch did not apply, asking the model for a new one")
        emit("log", message="The patch did not apply; asking the model for a corrected diff")
        return "draft_fix"
    logger.warning("Out of attempts after a failed patch application")
    return "finalize"


def route_after_tests(state: Dict[str, Any]) -> str:
    """Decide what to do after the tests ran."""
    if state.get("fatal"):
        return "finalize"
    if state.get("sandbox_ok"):
        return "open_pr"
    if state.get("attempts", 0) < _max_attempts(state):
        logger.info("The tests failed, asking the model to revise the fix")
        emit("log", message="The tests still fail; retrying with the failure output")
        return "draft_fix"
    logger.warning("Out of attempts, giving up")
    emit("log", message="Giving up after the maximum number of attempts")
    return "finalize"


def route_after_step(state: Dict[str, Any]) -> str:
    """Continue to the next step, or stop early when the run cannot continue."""
    if state.get("fatal"):
        logger.error("Aborting the run: %s", state.get("error"))
        return "finalize"
    return "continue"


def build_graph():
    """Build and compile the IssuePilot state graph."""
    workflow = StateGraph(AgentState)

    workflow.add_node("fetch_issue", fetch_issue_node)
    workflow.add_node("prepare_repo", prepare_repo_node)
    workflow.add_node("index_codebase", index_codebase_node)
    workflow.add_node("write_failing_test", write_failing_test_node)
    workflow.add_node("draft_fix", draft_fix_node)
    workflow.add_node("apply_patch", apply_patch_node)
    workflow.add_node("run_tests", run_tests_node)
    workflow.add_node("open_pr", open_pr_node)
    workflow.add_node("finalize", finalize_node)

    workflow.set_entry_point("fetch_issue")
    workflow.add_edge("fetch_issue", "prepare_repo")
    workflow.add_edge("prepare_repo", "index_codebase")
    workflow.add_edge("index_codebase", "write_failing_test")
    workflow.add_edge("write_failing_test", "draft_fix")
    workflow.add_edge("draft_fix", "apply_patch")

    # Every step can abort the run when it hits an unrecoverable error.
    for node, following in (
        ("fetch_issue", "prepare_repo"),
        ("prepare_repo", "index_codebase"),
        ("index_codebase", "write_failing_test"),
        ("write_failing_test", "draft_fix"),
        ("open_pr", "finalize"),
    ):
        workflow.add_conditional_edges(
            node,
            route_after_step,
            {"continue": following, "finalize": "finalize"},
        )

    workflow.add_conditional_edges(
        "apply_patch",
        route_after_patch,
        {"run_tests": "run_tests", "draft_fix": "draft_fix", "finalize": "finalize"},
    )
    workflow.add_conditional_edges(
        "run_tests",
        route_after_tests,
        {"open_pr": "open_pr", "draft_fix": "draft_fix", "finalize": "finalize"},
    )
    workflow.add_edge("finalize", END)

    return workflow.compile()


graph = build_graph()


def initial_state(
    issue_url: str,
    run_dir: str,
    dry_run: bool = False,
    demo_mode: bool = False,
    demo_repo_path: str = "",
    demo_issue_path: str = "",
    llm: Any = None,
) -> Dict[str, Any]:
    """Build the initial state of a run.

    Args:
        issue_url: URL of the issue (or a ``demo://`` pseudo URL).
        run_dir: Directory the agent may write to.
        dry_run: Do everything except opening the pull request.
        demo_mode: Run against a bundled demo repository.
        demo_repo_path: Path of the demo repository (demo mode).
        demo_issue_path: Path of the demo issue description (demo mode).
        llm: The LLM client to use.

    Returns:
        The initial agent state.
    """
    return {
        "issue_url": issue_url,
        "run_dir": run_dir,
        "dry_run": dry_run,
        "demo_mode": demo_mode,
        "demo_repo_path": demo_repo_path,
        "demo_issue_path": demo_issue_path,
        "llm": llm,
        "attempts": 0,
        "max_attempts": settings.max_attempts,
    }
