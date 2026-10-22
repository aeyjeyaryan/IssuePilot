"""Node 9: finalise the run - build the report and decide the outcome."""

from __future__ import annotations

import logging
from typing import Any, Dict

from issuepilot.events import REPORT, STEP_START, emit
from issuepilot.llm import LLMError
from issuepilot.prompts import ROOT_CAUSE_SYSTEM_PROMPT, build_root_cause_prompt
from issuepilot.report import build_pr_body, build_report, working_tree_diff

logger = logging.getLogger(__name__)


def _analyse_root_cause(state: Dict[str, Any]) -> str:
    """Ask the model to explain why the bug happened.

    The analysis is a nice-to-have: when the model is unavailable (or the
    reference backend is used) the report falls back to a mechanical summary.
    """
    client = state.get("llm")
    if client is None or not state.get("patch"):
        return ""
    issue = state.get("issue", {}) or {}
    prompt = build_root_cause_prompt(
        issue.get("title", ""),
        issue.get("body", ""),
        state.get("failing_test", ""),
        state.get("patch", ""),
    )
    try:
        answer = client.generate_root_cause(ROOT_CAUSE_SYSTEM_PROMPT, prompt).strip()
    except LLMError as exc:
        logger.info("No root cause analysis: %s", exc)
        return ""
    except Exception as exc:  # pragma: no cover - defensive
        logger.info("No root cause analysis: %s", exc)
        return ""
    if answer.startswith("```"):
        answer = answer.strip("`").split("\n", 1)[-1].strip()
    return answer


def finalize_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """Produce the final report for the run.

    This node is the graceful end state of the graph: it is reached both after
    a successful fix and when the agent gave up after exhausting its attempts.
    """
    logger.info("node=finalize attempts=%s ok=%s", state.get("attempts", 0), state.get("sandbox_ok"))
    emit(STEP_START, step="finalize")
    emit("log", message="Building the final report")

    if state.get("sandbox_ok"):
        state["outcome"] = "fixed"
    elif state.get("error"):
        state["outcome"] = "error"
    else:
        state["outcome"] = "gave-up"

    state["diff"] = working_tree_diff(state)
    state["root_cause"] = _analyse_root_cause(state)
    state["pr_body"] = build_pr_body(state)
    state["report"] = build_report(state)

    if state.get("error"):
        emit("error", message=state["error"])
    emit(REPORT, report=state["report"], outcome=state["outcome"], diff=state["diff"])
    emit("log", message=f"Run finished with outcome '{state['outcome']}'")
    return state
