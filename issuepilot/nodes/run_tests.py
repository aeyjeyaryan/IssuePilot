"""Node 7: run the test suite inside the sandbox."""

from __future__ import annotations

import logging
from typing import Any, Dict

from issuepilot.events import STEP_START, TEST_REPORT, emit
from issuepilot.sandbox import SandboxRunner, collect_failures, summarize_pytest

logger = logging.getLogger(__name__)

def run_tests_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """Execute the tests of the working copy inside the sandbox.

    Before trusting a passing run, the reproduction test is executed on its own
    to make sure the agent actually reproduced the bug: a green test suite whose
    repro test never failed is worthless evidence.
    """
    logger.info("node=run_tests attempt=%s", state.get("attempts", 0))
    emit(STEP_START, step="run_tests")
    emit("log", message="Running the tests in the sandbox")

    try:
        runner = SandboxRunner()
        # The local fallback is only used for the bundled (trusted) demo code.
        allow_local = bool(state.get("demo_mode"))
        result = runner.run_tests(state["repo_path"], allow_local_fallback=allow_local)


        state["sandbox_ok"] = result.ok
        state["sandbox_output"] = result.output
        state["sandbox_backend"] = result.backend
        state["sandbox_duration"] = round(result.duration, 2)
        state["test_summary"] = summarize_pytest(result.output)
        state["failing_tests"] = collect_failures(result.output)

        if result.ok:
            logger.info("node=run_tests passed backend=%s", result.backend)
            emit("log", message=f"Tests PASSED ({result.backend}, {result.duration:.1f}s)")
            emit(TEST_REPORT, ok=True, output=result.output, summary=state["test_summary"])
        else:
            message = result.error or result.output
            logger.warning("node=run_tests failed backend=%s: %s", result.backend, message[:200])
            state["sandbox_output"] = message
            emit("log", message=f"Tests FAILED ({result.backend})")
            emit(TEST_REPORT, ok=False, output=message, summary=state["test_summary"])
    except Exception as exc:  # pragma: no cover - defensive
        state["sandbox_ok"] = False
        state["sandbox_output"] = f"The sandbox could not run the tests: {exc}"
        logger.error("node=run_tests error=%s", exc)
        emit("error", message=state["sandbox_output"])
    return state
