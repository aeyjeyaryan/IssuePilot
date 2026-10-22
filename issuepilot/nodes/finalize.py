"""Node 9: finalise the run - build the report and decide the outcome."""

from __future__ import annotations

import logging
from typing import Any, Dict

from issuepilot.events import REPORT, emit
from issuepilot.report import build_pr_body, build_report, working_tree_diff

logger = logging.getLogger(__name__)


def finalize_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """Produce the final report for the run.

    This node is the graceful end state of the graph: it is reached both after
    a successful fix and when the agent gave up after exhausting its attempts.
    """
    logger.info("node=finalize attempts=%s ok=%s", state.get("attempts", 0), state.get("sandbox_ok"))
    emit("log", message="Building the final report")

    if state.get("sandbox_ok"):
        state["outcome"] = "fixed"
    elif state.get("error"):
        state["outcome"] = "error"
    else:
        state["outcome"] = "gave-up"

    state.setdefault("root_cause", "")
    state["diff"] = working_tree_diff(state)
    state["pr_body"] = build_pr_body(state)
    state["report"] = build_report(state)

    if state.get("error"):
        emit("error", message=state["error"])
    emit(REPORT, report=state["report"], outcome=state["outcome"], diff=state["diff"])
    emit("log", message=f"Run finished with outcome '{state['outcome']}'")
    return state
