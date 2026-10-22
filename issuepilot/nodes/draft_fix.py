"""Node 5: draft the fix as a unified diff."""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from issuepilot.events import STEP_START, emit
from issuepilot.llm import LLMClient, LLMError
from issuepilot.patch import extract_diff, validate_diff
from issuepilot.prompts import PATCH_SYSTEM_PROMPT, REVISION_SYSTEM_PROMPT, build_patch_prompt
from issuepilot.retrieval import RetrievedChunk

logger = logging.getLogger(__name__)


def _retrieved(state: Dict[str, Any]) -> List[RetrievedChunk]:
    """Rebuild :class:`RetrievedChunk` objects from the state."""
    from issuepilot.retrieval import Chunk

    chunks: List[RetrievedChunk] = []
    for item in state.get("relevant_chunks", []):
        chunks.append(
            RetrievedChunk(
                chunk=Chunk(
                    content=item["content"],
                    file_path=item["file_path"],
                    start_line=item["start_line"],
                    end_line=item["end_line"],
                    name=item.get("name", ""),
                    type=item.get("type", ""),
                ),
                score=item.get("score", 0.0),
            )
        )
    return chunks


def draft_fix_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """Ask the model for a unified diff that fixes the bug."""
    revision = bool(state.get("patch_error") or state.get("sandbox_output") and not state.get("sandbox_success"))
    logger.info("node=draft_fix revision=%s attempt=%s", revision, state.get("attempts", 0) + 1)
    emit(STEP_START, step="draft_fix")
    emit("log", message="Drafting the fix" if not revision else "Revising the fix after the failed attempt")

    issue = state.get("issue", {})
    try:
        client: LLMClient = state["llm"]
        prompt = build_patch_prompt(
            issue.get("title", ""),
            issue.get("body", ""),
            state.get("failing_test", ""),
            _retrieved(state),
            failure=state.get("sandbox_output", "") if not state.get("sandbox_success") else "",
            previous_patch=state.get("patch", "") if revision else "",
        )
        system = REVISION_SYSTEM_PROMPT if revision else PATCH_SYSTEM_PROMPT
        raw = client.generate_patch(system, prompt)
        diff_text = extract_diff(raw)
        if not diff_text.strip():
            state["error"] = "The model did not return a unified diff"
            state["fatal"] = True
            logger.error("node=draft_fix error=%s", state["error"])
            emit("error", message=state["error"])
            return state

        problems = validate_diff(diff_text)
        state["patch"] = diff_text
        state["patch_problems"] = problems
        state["attempts"] = state.get("attempts", 0) + 1
        logger.info("node=draft_fix attempt=%s problems=%s", state["attempts"], problems)
        emit("log", message=f"Produced a patch ({len(diff_text.splitlines())} lines)")
        if problems:
            emit("log", message="Patch warnings: " + "; ".join(problems))
    except LLMError as exc:
        state["error"] = str(exc)
        state["fatal"] = True
        logger.error("node=draft_fix error=%s", exc)
        emit("error", message=str(exc))
    except Exception as exc:  # pragma: no cover - defensive
        state["error"] = f"Could not draft the fix: {exc}"
        state["fatal"] = True
        logger.error("node=draft_fix error=%s", exc)
        emit("error", message=state["error"])
    return state
