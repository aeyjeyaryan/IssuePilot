"""Driver that executes the IssuePilot graph and streams its progress."""

from __future__ import annotations

import logging
import threading
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from issuepilot.config import settings
from issuepilot.events import DONE, STEP_END, STEP_START, EventBus, current_bus
from issuepilot.graph import graph, initial_state
from issuepilot.llm import LLMClient

logger = logging.getLogger(__name__)

#: Nodes of the graph in the order they are executed.
NODE_ORDER = [
    "fetch_issue",
    "prepare_repo",
    "index_codebase",
    "write_failing_test",
    "draft_fix",
    "apply_patch",
    "run_tests",
    "open_pr",
    "finalize",
]


def make_llm(demo_mode: bool = False, reference_dir: Optional[Path] = None, backend: str = "auto") -> LLMClient:
    """Create the LLM client used by a run.

    Args:
        demo_mode: Whether the run is a demo run.
        reference_dir: Directory with the demo reference artefacts.
        backend: ``"auto"``, ``"ollama"`` or ``"reference"``.

    Returns:
        A configured :class:`LLMClient`.
    """
    if demo_mode and reference_dir is not None and backend == "auto":
        backend = "auto"
    return LLMClient(backend=backend, reference_dir=reference_dir)


def run_agent(
    issue_url: str,
    dry_run: bool = False,
    demo_mode: bool = False,
    demo_repo_path: str = "",
    demo_issue_path: str = "",
    run_dir: Optional[str] = None,
    bus: Optional[EventBus] = None,
    backend: str = "auto",
    on_event: Optional[Callable[[Any], None]] = None,
) -> Dict[str, Any]:
    """Run the IssuePilot graph until it finishes.

    Args:
        issue_url: URL of the issue to fix.
        dry_run: Everything except opening the pull request.
        demo_mode: Run against a bundled demo repository.
        demo_repo_path: Path of the demo repository.
        demo_issue_path: Path of the demo issue description.
        run_dir: Directory for this run (generated when omitted).
        bus: Event bus used for streaming (created when omitted).
        backend: LLM backend to use.
        on_event: Optional callback invoked for every emitted event.

    Returns:
        The final state of the agent.
    """
    run_id = uuid.uuid4().hex[:12]
    work_dir = Path(run_dir) if run_dir else settings.run_dir(run_id)
    work_dir.mkdir(parents=True, exist_ok=True)

    bus = bus or EventBus(run_id)
    reference_dir = Path(demo_repo_path) / "_reference" if demo_repo_path else None
    llm = make_llm(demo_mode=demo_mode, reference_dir=reference_dir, backend=backend)

    state = initial_state(
        issue_url=issue_url,
        run_dir=str(work_dir),
        dry_run=dry_run,
        demo_mode=demo_mode,
        demo_repo_path=demo_repo_path,
        demo_issue_path=demo_issue_path,
        llm=llm,
    )
    state["run_id"] = run_id

    logger.info("run_id=%s starting issue=%s", run_id, issue_url)
    bus.emit(STEP_START, step="start", run_id=run_id, llm=llm.describe())

    token = current_bus.set(bus)
    final_state: Dict[str, Any] = dict(state)
    try:
        for update in graph.stream(state, stream_mode="updates"):
            for node_name, node_state in update.items():
                if isinstance(node_state, dict):
                    final_state.update(node_state)
                bus.emit(STEP_END, step=node_name)
                if on_event is not None:
                    on_event(node_name)
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("run_id=%s crashed", run_id)
        final_state["error"] = f"The agent crashed: {exc}"
        bus.emit("error", message=final_state["error"])
    finally:
        current_bus.reset(token)
        final_state.setdefault("outcome", "error")
        bus.emit(DONE, outcome=final_state.get("outcome"))
        bus.close()

    logger.info("run_id=%s finished outcome=%s", run_id, final_state.get("outcome"))
    return final_state


def run_agent_in_thread(
    on_finish: Optional[Callable[[Dict[str, Any]], None]] = None,
    **kwargs,
) -> tuple[str, EventBus, threading.Thread]:
    """Start :func:`run_agent` in a background thread.

    Args:
        on_finish: Callback invoked with the final state when the run ends.
        **kwargs: Forwarded to :func:`run_agent`.

    Returns:
        ``(run_id, bus, thread)``.  The thread is already started.
    """
    run_id = uuid.uuid4().hex[:12]
    bus = kwargs.pop("bus", None) or EventBus(run_id)

    def _target() -> None:
        state = run_agent(bus=bus, **kwargs)
        if on_finish is not None:
            try:
                on_finish(state)
            except Exception:  # pragma: no cover - defensive
                logger.exception("The on_finish callback failed")

    thread = threading.Thread(target=_target, name=f"issuepilot-{run_id}", daemon=True)
    thread.start()
    return run_id, bus, thread
