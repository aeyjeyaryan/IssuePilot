"""Node 5: draft the fix as a unified diff."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

from issuepilot.config import settings
from issuepilot.edits import edits_to_diff, parse_edits
from issuepilot.events import STEP_START, emit
from issuepilot.llm import LLMClient, LLMError
from issuepilot.patch import extract_diff, reset_working_copy, validate_diff
from issuepilot.prompts import PATCH_SYSTEM_PROMPT, REVISION_SYSTEM_PROMPT, build_patch_prompt
from issuepilot.retrieval import RetrievedChunk

logger = logging.getLogger(__name__)

#: How many characters of source we are willing to send for one file.
MAX_FILE_CHARS = 8000
#: How many files we send in full.
MAX_FILES = 3


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


def _candidate_files(state: Dict[str, Any]) -> List[Tuple[str, str]]:
    """Read the files the retrieved chunks belong to, for exact context."""
    repo_path = Path(state.get("repo_path", ""))
    if not repo_path.is_dir():
        return []
    seen: List[str] = []
    for item in state.get("relevant_chunks", []):
        path = item.get("file_path", "")
        if path and path not in seen:
            seen.append(path)
    files: List[Tuple[str, str]] = []
    for path in seen[:MAX_FILES]:
        target = (repo_path / path).resolve()
        try:
            target.relative_to(repo_path.resolve())
        except ValueError:
            continue
        if not target.is_file():
            continue
        try:
            content = target.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if len(content) > MAX_FILE_CHARS:
            content = content[:MAX_FILE_CHARS] + "\n# ... (truncated)\n"
        files.append((path, content))
    return files


def _diff_from_model_output(raw: str, repo_path: str) -> Tuple[str, str]:
    """Turn model output into a diff, accepting both diffs and JSON edits."""
    diff_text = extract_diff(raw)
    if diff_text.strip():
        return diff_text, ""
    edits = parse_edits(raw)
    if not edits:
        return "", (
            "The model did not return JSON edits. Expected "
            '{"edits": [{"file": ..., "old": ..., "new": ...}]}.'
        )
    return edits_to_diff(repo_path, edits)


def draft_fix_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """Ask the model for a unified diff that fixes the bug.

    Weak local models regularly get the snippet format wrong.  Instead of
    burning a full sandbox round trip (or giving up) the node asks again with
    the parser error attached, up to :data:`settings.format_retries` times.
    """
    revision = bool(state.get("patch_error") or state.get("sandbox_output") and not state.get("sandbox_success"))
    logger.info("node=draft_fix revision=%s attempt=%s", revision, state.get("attempts", 0) + 1)
    emit(STEP_START, step="draft_fix")
    emit("log", message="Drafting the fix" if not revision else "Revising the fix after the failed attempt")

    if revision and state.get("repo_path"):
        reset, message = reset_working_copy(state["repo_path"], keep=[state.get("failing_test_path", "")])
        logger.info("node=draft_fix reset=%s (%s)", reset, message)
        emit("log", message="Reverted the working copy before the next attempt")

    issue = state.get("issue", {})
    client: LLMClient = state["llm"]
    files = _candidate_files(state)
    system = REVISION_SYSTEM_PROMPT if revision else PATCH_SYSTEM_PROMPT
    failure = state.get("sandbox_output", "") if not state.get("sandbox_success") else ""
    previous = state.get("patch", "") if revision else ""
    rounds = max(1, int(settings.format_retries) + 1)

    for round_index in range(rounds):
        try:
            prompt = build_patch_prompt(
                issue.get("title", ""),
                issue.get("body", ""),
                state.get("failing_test", ""),
                _retrieved(state),
                failure=failure,
                previous_patch=previous,
                files=files,
                repro_output=state.get("repro_output", ""),
            )
            raw = client.generate_patch(system, prompt)
        except LLMError as exc:
            state["error"] = str(exc)
            state["fatal"] = True
            logger.error("node=draft_fix error=%s", exc)
            emit("error", message=str(exc))
            return state
        except Exception as exc:  # pragma: no cover - defensive
            state["error"] = f"Could not draft the fix: {exc}"
            state["fatal"] = True
            logger.error("node=draft_fix error=%s", exc)
            emit("error", message=state["error"])
            return state

        diff_text, error = _diff_from_model_output(raw, state.get("repo_path", ""))
        if diff_text.strip() and not error:
            problems = validate_diff(diff_text)
            state["patch"] = diff_text
            state["patch_problems"] = problems
            state["attempts"] = state.get("attempts", 0) + 1
            state["fatal"] = False
            state["error"] = ""
            logger.info("node=draft_fix attempt=%s problems=%s", state["attempts"], problems)
            emit("log", message=f"Produced a patch ({len(diff_text.splitlines())} lines)")
            if problems:
                emit("log", message="Patch warnings: " + "; ".join(problems))
            return state

        error = error or "The model did not return a usable patch"
        if "does not change anything" in error:
            error += (
                ". The model repeated the buggy code instead of fixing it; "
                "a code capable model (for example `qwen2.5-coder:7b`) is needed for this issue"
            )
        logger.warning("node=draft_fix round=%s invalid output: %s", round_index + 1, error)
        emit("log", message=f"The model output was not usable ({error}); asking again")
        failure = f"{error}\n\nThe output you produced was:\n{raw.strip()[-2000:]}"
        previous = raw.strip()[-2000:]
        system = REVISION_SYSTEM_PROMPT

    state["error"] = error
    state["fatal"] = True
    logger.error("node=draft_fix error=%s", error)
    emit("error", message=error)
    return state
