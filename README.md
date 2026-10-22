# IssuePilot

IssuePilot is an open-source issue-to-PR agent that takes a GitHub issue URL for a Python repository, reproduces the bug, writes a fix, validates it in a sandboxed environment, and opens a pull request with full context. Built for hackathon/demo use with local LLMs (Ollama), deterministic workflows (LangGraph), and safe-by-default execution (Docker isolation, no network, dry-run mode).

## Architecture (ASCII)

```
┌─────────────────────────────────────────────────────────────────────────┐
│                                User                                     │
│                            (CLI / Web UI)                               │
└───────────────────────────────┬─────────────────────────────────────────┘
                                │ issue URL / --demo
                                ▼
                        ┌───────────────┐
                        │   API/CLI     │ (FastAPI + Typer)
                        └───────┬───────┘
                                │ invokes
                                ▼
                        ┌───────────────────────────────────────────┐
                        │           LangGraph Workflow              │
                        │  ┌─────────────────────────────────────┐ │
                        │  │ fetch_issue → clone_repo → index   │ │
                        │  │ → write_failing_test → draft_fix   │ │
                        │  │ → apply_patch → run_in_sandbox     │ │
                        │  │     ↙ (on failure, retry ≤ 3)      │ │
                        │  │   revise_fix → apply_patch → ...    │ │
                        │  │         ↓ (success or give up)      │ │
                        │  │        open_pr (or dry-run)         │ │
                        │  └─────────────────────────────────────┘ │
                        └─────┬─────────┬─────────┬─────────┬─────┘
                              │         │         │         │
                            RAG      Sandbox   GitHub    LLM
                          (ChromaDB  Docker   (PyGithub/ Ollama
                            /FAISS)   no net)   gh CLI)
```

## Setup

### Prerequisites
- Python 3.11+
- Docker (for sandboxed test runs)
- [Ollama](https://ollama.ai/) (optional for real LLM calls; agent works with stubs in tests/demo)
- Git

### Install
```bash
# Using pip
pip install -r requirements.txt

# Or with uv
uv pip install -r requirements.txt
```

### Environment
```bash
cp .env.example .env
# Edit .env with your tokens if you want real GitHub operations
```

## Usage

### CLI
```bash
# Dry run on a GitHub issue (safe, no PR)
python -m issuepilot.cli run https://github.com/owner/repo/issues/123 --dry-run

# Real run (will create branch/PR if tests pass and not dry-run)
python -m issuepilot.cli run https://github.com/owner/repo/issues/123

# Demo mode - no GitHub access needed
python -m issuepilot.cli demo

# Check status
python -m issuepilot.cli status <run_id>
```

### Web UI
```bash
# Start API server
uvicorn issuepilot.api:app --reload --port 8000
# Open web/index.html or navigate to http://localhost:8000/
```

The UI streams each agent step live via Server-Sent Events, shows logs, diffs, test reports, and includes a dry-run toggle.

## How it works (60 seconds)
1. **Fetch & clone**: Get issue details and clone the target repo locally (or use demo repos).
2. **Index**: Parse Python files with `ast`, chunk by function/class, store in ChromaDB/FAISS with file/line metadata for RAG.
3. **Reproduce**: Generate a failing pytest that demonstrates the reported bug using retrieved context.
4. **Fix**: Draft a unified diff patch targeting the root cause.
5. **Validate**: Apply patch (with validation/retry feedback if apply fails), run tests in Docker sandbox (no network, limits, 120s timeout). On failure, feed logs to LLM and retry up to 3 times.
6. **Report/PR**: On success, open PR from a new branch on a fork (never default branch). In dry-run/demo, just produce diff + test report.

## Safety model
- **Branch safety**: Never push to default branch. Always create a new branch on a fork.
- **Sandboxing**: All cloned-repo code executes only inside Docker with `--network=none`, CPU/memory limits, 120s timeout.
- **Dry-run**: `--dry-run` flag and UI toggle skip PR creation and print diff.
- **Secrets**: Never log/commit secrets. `.env.example` provided, `.gitignore` excludes secrets/env.
- **Scoped execution**: Python repos, pytest only, bug-fix issues, one issue per run.

## Known limitations
- Only Python + pytest
- Bug fixes only (feature requests may not reproduce cleanly)
- Local LLMs may produce imperfect patches; retry logic helps but not guaranteed
- GitHub operations require tokens/fork setup for non-dry runs
- Demo uses synthetic repos for reliability offline

## Design decisions
- **LangGraph**: Explicit node/edge graph makes retry loop and "give up with report" state auditable.
- **AST chunking**: More precise than naive line splits; preserves function/class boundaries and line numbers.
- **Docker sandbox**: Strong isolation vs running untrusted repo code on host.
- **SSE streaming**: Keeps UI responsive with stepwise visibility for debugging/demo.
- **Validation-first patching**: Validate unified diff structure and feed apply errors back to LLM (robust).
- **Demo mode**: Deterministic offline path ensures hackathon demo reliability.
