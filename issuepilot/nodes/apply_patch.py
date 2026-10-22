"""Node 6: apply the generated patch to the working copy."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict

from issuepilot.events import DIFF, emit
from issuepilot.patch import apply_unified_diff

logger = logging.getLogger(__name__)


def apply_patch_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """Apply the patch, recording a readable error when it does not apply."""
    logger.info("node=apply_patch attempt=%s", state.get("attempts", 0))
    emit("log", message="Applying the patch")

    diff_text = state.get("patch", "")
    if not diff_text.strip():
        state["patch_applied"] = False
        state["patch_error"] = "There is no patch to apply"
        emit("error", message=state["patch_error"])
        return state

    try:
        result = apply_unified_diff(state["repo_path"], diff_text)
        state["patch_applied"] = result.applied
        state["patch_error"] = result.error
        state["files_changed"] = result.files_changed
        state["patch_method"] = result.method
        if result.applied:
            logger.info("node=apply_patch applied files=%s", result.files_changed)
            emit("log", message=f"Patch applied to {', '.join(result.files_changed) or 'the repository'}")
            emit(DIFF, diff=diff_text, files=result.files_changed)
        else:
            logger.warning("node=apply_patch failed: %s", result.error)
            emit("error", message=f"The patch did not apply: {result.error}")
    except Exception as exc:  # pragma: no cover - defensive
        state["patch_applied"] = False
        state["patch_error"] = f"Could not apply the patch: {exc}"
        logger.error("node=apply_patch error=%s", exc)
        emit("error", message=state["patch_error"])
    return state
