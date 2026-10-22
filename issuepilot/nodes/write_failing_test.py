"""Node 4: write a failing pytest that reproduces the bug."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Dict, List

from issuepilot.events import emit
from issuepilot.llm import LLMClient, LLMError
from issuepilot.prompts import TEST_SYSTEM_PROMPT, build_test_prompt
from issuepilot.retrieval import RetrievedChunk

logger = logging.getLogger(__name__)

#: Name of the test file the agent writes into the repository.
REPRO_TEST_RELPATH = "tests/test_issue_repro.py"

_FENCE_RE = re.compile(r"```(?:python|py)?\s*\n(.*?)```", re.DOTALL)


def extract_code(text: str) -> str:
    """Extract the python source out of a model response."""
    if not text:
        return ""
    match = _FENCE_RE.search(text)
    if match:
        return match.group(1).strip()
    # Fall back to the first import/def in the raw text.
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line.startswith(("import ", "from ", "def ", "class ", "@")):
            return "\n".join(lines[index:]).strip()
    return text.strip()


def _ensure_repo_on_sys_path(repo_path: Path) -> None:
    """Make sure the repository root is importable from the generated test."""
    conftest = repo_path / "conftest.py"
    snippet = (
        '"""pytest bootstrap: make the repository root importable."""\n\n'
        "import os\n"
        "import sys\n\n"
        "sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\n"
    )
    if conftest.exists():
        existing = conftest.read_text(encoding="utf-8")
        if "sys.path.insert" not in existing:
            conftest.write_text(existing + "\n\n" + snippet, encoding="utf-8")
        return
    conftest.write_text(snippet, encoding="utf-8")


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


def write_failing_test_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """Ask the model for a reproduction test and store it in the repository."""
    logger.info("node=write_failing_test")
    emit("log", message="Writing the failing reproduction test")

    issue = state.get("issue", {})
    try:
        client: LLMClient = state["llm"]
        prompt = build_test_prompt(
            issue.get("title", ""),
            issue.get("body", ""),
            _retrieved(state),
        )
        raw = client.generate_test(TEST_SYSTEM_PROMPT, prompt)
        code = extract_code(raw)
        if not code.strip():
            state["error"] = "The model did not return a test file"
            state["fatal"] = True
            emit("error", message=state["error"])
            return state

        repo_path = Path(state["repo_path"])
        _ensure_repo_on_sys_path(repo_path)
        target = repo_path / REPRO_TEST_RELPATH
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(code.rstrip() + "\n", encoding="utf-8")

        state["failing_test"] = code
        state["failing_test_path"] = REPRO_TEST_RELPATH
        logger.info("node=write_failing_test path=%s", target)
        emit("log", message=f"Wrote the reproduction test to {REPRO_TEST_RELPATH}")
    except LLMError as exc:
        state["error"] = str(exc)
        state["fatal"] = True
        logger.error("node=write_failing_test error=%s", exc)
        emit("error", message=str(exc))
    except Exception as exc:  # pragma: no cover - defensive
        state["error"] = f"Could not write the reproduction test: {exc}"
        state["fatal"] = True
        logger.error("node=write_failing_test error=%s", exc)
        emit("error", message=state["error"])
    return state
