"""Report how the configured Ollama model behaves on the demo repos.

This is a diagnostic helper, not part of the pipeline: it runs each demo with
the `ollama` backend and prints the outcome so you can see whether the local
model is good enough for the demo without any hand-holding.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from issuepilot.config import settings  # noqa: E402
from issuepilot.runner import run_agent  # noqa: E402


def main() -> int:
    """Run every demo repository with the ollama backend."""
    from issuepilot.llm import LLMClient

    client = LLMClient(backend="ollama")
    print(f"backend: {client.describe()}")
    if not client.available():
        print("ollama is not available; start it with `ollama serve`")
        return 1

    failures = 0
    for name in ("repo1", "repo2", "repo3"):
        repo = settings.demo_root / name
        with tempfile.TemporaryDirectory() as tmp:
            state = run_agent(
                issue_url=(repo / "ISSUE.md").as_uri(),
                dry_run=True,
                demo_mode=True,
                demo_repo_path=str(repo),
                demo_issue_path=str(repo / "ISSUE.md"),
                run_dir=tmp,
                backend="ollama",
            )
        outcome = state.get("outcome")
        print(f"{name}: {outcome} (attempts={state.get('attempts')})")
        if outcome != "fixed":
            failures += 1
            print(f"  error: {state.get('error')}")
    print(f"{3 - failures}/3 fixed by the local model")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
