"""Workflow nodes of the IssuePilot agent.

Every node follows the same contract: it receives the agent state, performs one
step, mutates the state in place and returns it.  Nodes never raise for
expected failures - they record a user facing message in ``state["error"]``
and let the graph route to the ``give_up`` node.
"""

from issuepilot.nodes.apply_patch import apply_patch_node
from issuepilot.nodes.draft_fix import draft_fix_node
from issuepilot.nodes.fetch_issue import fetch_issue_node
from issuepilot.nodes.finalize import finalize_node
from issuepilot.nodes.index_codebase import index_codebase_node
from issuepilot.nodes.open_pr import open_pr_node
from issuepilot.nodes.prepare_repo import prepare_repo_node
from issuepilot.nodes.run_tests import run_tests_node
from issuepilot.nodes.write_failing_test import write_failing_test_node

__all__ = [
    "apply_patch_node",
    "draft_fix_node",
    "fetch_issue_node",
    "finalize_node",
    "index_codebase_node",
    "open_pr_node",
    "prepare_repo_node",
    "run_tests_node",
    "write_failing_test_node",
]
