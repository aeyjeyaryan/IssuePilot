# IssuePilot

**Give IssuePilot a GitHub issue URL. It gives you back a pull request.**

IssuePilot is an open-source issue-to-PR agent for Python repositories. It reads
an issue, finds the relevant code with a local retrieval stack, writes a
reproduction test, drafts the fix, runs the test suite inside an isolated
sandbox, and opens a pull request on a branch with the full context attached.
Everything runs locally: a local model through Ollama, a local vector store,
Docker for the untrusted test run, and no cloud service in between.

```
issue URL ──▶ fetch ──▶ prepare ──▶ index ──▶ repro test ──▶ draft fix
                                                                │
        pull request ◀── open PR ◀── tests pass ◀── apply ◀────┘
                                 ▲                  │
                                 └──── retry ◀──────┘ (max 3 attempts)
```

---

## Table of contents

- [What you get](#what-you-get)
- [Quick start](#quick-start)
- [How it works](#how-it-works)
- [Architecture](#architecture)
- [Safety model](#safety-model)
- [The demo repositories](#the-demo-repositories)
- [Configuration](#configuration)
- [CLI reference](#cli-reference)
- [HTTP API](#http-api)
- [Project layout](#project-layout)
- [Testing](#testing)
- [Limitations (read this)](#limitations-read-this)
- [Design decisions](#design-decisions)

---

## What you get

| | |
|---|---|
| **Issue → PR** | Fetches a GitHub issue, clones the repository, and opens a pull request from a dedicated branch. |
| **Real reproduction** | Writes a pytest that fails on the current code. The test is *executed* before the fix: a test that passes is rejected and the model is asked again. |
| **Grounded fixes** | The model sees the exact content of the candidate files plus the failure output, and answers with search/replace edits that are turned into a real diff. |
| **Sandboxed tests** | Untrusted code runs in Docker with no network, a read-only filesystem, memory/CPU/PID limits and a hard timeout. |
| **Safe GitHub behaviour** | Never pushes to the default branch: fork → branch → commit → pull request. `--dry-run` stops before anything is pushed. |
| **Live web UI** | A single-page app that streams every step of the run over SSE: logs, the diff, the test report. |

---

## Quick start

### 1. The offline demo (no model, no Docker, no network)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python -m issuepilot.cli demo
```

This runs the complete pipeline on three bundled repositories and prints the
diff, the test report and the outcome of every run. It is fully deterministic,
so it is the fastest way to see the agent work end to end.

### 2. With a local model

```bash
ollama serve
ollama pull qwen2.5-coder:7b      # any code-capable model works

python -m issuepilot.cli demo --backend ollama
python -m issuepilot.cli run https://github.com/owner/repo/issues/42 --dry-run
```

### 3. The web UI

```bash
python -m issuepilot.cli serve      # http://127.0.0.1:8000
```

Pick a demo repository (or paste an issue URL), press **Run** and watch the
agent work: step pills, live log, generated diff and the test report.

### 4. Check your environment

```bash
python -m issuepilot.cli doctor
```

It reports Docker, the sandbox image, the LLM backend, the `gh` CLI and the
GitHub token.

---

## How it works

Nine steps, orchestrated by a LangGraph state machine. Every step emits events
that the CLI prints and the web UI streams.

| # | Step | What happens |
|---|------|--------------|
| 1 | `fetch_issue` | Parse the issue URL, fetch title/body/labels through the `gh` CLI or PyGithub. |
| 2 | `prepare_repo` | Clone the repository (or copy the demo repo) into the run directory and take a git snapshot. |
| 3 | `index_codebase` | Chunk the Python files, embed them with a hashing embedder and store them in ChromaDB (or FAISS/memory). |
| 4 | `write_failing_test` | Ask the model for a pytest, write it to `tests/test_issue_repro.py` and **run it**: the test has to fail, otherwise it is rejected. |
| 5 | `draft_fix` | Ask the model for search/replace edits, with the exact file content, the repro test and its failure output. Malformed answers are re-requested with the parser error attached. |
| 6 | `apply_patch` | Turn the edits into a unified diff and apply it with `git apply` (with a pure-python fallback). |
| 7 | `run_tests` | Run the whole suite in the sandbox. If it fails, the failure output goes back into step 5. |
| 8 | `open_pr` | Create a branch, commit, push and open the pull request (skipped in dry-run mode). |
| 9 | `finalize` | Ask the model for a root cause analysis, then write `report.md`: what was broken, why, what changed, which tests ran, and what the agent could not solve. |

The graph retries steps 5–7 up to `MAX_ATTEMPTS` times. Every retry starts from
the original snapshot, so a failed attempt never leaks into the next one.

```
fetch_issue → prepare_repo → index_codebase → write_failing_test
      → draft_fix → apply_patch → run_tests → open_pr → finalize
                     ▲            │           │
                     └───────────┘◀──────────┘  (retry, max 3 attempts)
```

---

## Architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│                           User (CLI / Web UI)                        │
└────────────────────────────────┬─────────────────────────────────────┘
                                 │ issue URL  /  demo repository
                                 ▼
                    ┌────────────────────────┐
                    │   CLI (Typer) / API    │  FastAPI + SSE
                    └───────────┬────────────┘
                                │
                                ▼
      ┌───────────────────────────────────────────────────────────┐
      │                    LangGraph workflow                    │
      │  fetch_issue → prepare_repo → index_codebase →           │
      │  write_failing_test → draft_fix ⇄ apply_patch →          │
      │  run_tests → open_pr → finalize                          │
      └───────┬──────────────┬──────────────┬───────────────┬────┘
              │              │              │               │
          Retrieval       Sandbox        GitHub            LLM
          ChromaDB /     Docker, no     gh CLI /        Ollama
          FAISS / memory network,       PyGithub      (local model)
```

| Module | Responsibility |
|--------|----------------|
| `issuepilot/graph.py` | The state machine: nodes, edges, retry routing. |
| `issuepilot/nodes/` | One module per step (`fetch_issue.py`, `prepare_repo.py`, …). |
| `issuepilot/retrieval.py` | Chunking, embeddings, Chroma/FAISS/memory stores, the retriever. |
| `issuepilot/edits.py` | Search/replace edits → unified diff (model friendly format). |
| `issuepilot/patch.py` | Diff extraction, validation, application, git helpers. |
| `issuepilot/sandbox.py` | Docker command construction, local fallback, pytest parsing. |
| `issuepilot/github_ops.py` | Issue fetching, cloning, forking, pull requests. |
| `issuepilot/llm.py` | Ollama backend (HTTP) and the deterministic reference backend. |
| `issuepilot/api.py` | FastAPI app, run registry, SSE event stream. |
| `issuepilot/events.py` | The event bus: every step publishes events the UI consumes. |

---

## Safety model

The agent clones and executes code it did not write, so the execution is the
part that needs the most care.

**Untrusted repositories (cloned from GitHub) are only ever executed inside
Docker:**

```bash
docker run --rm \
  --network=none \                 # no network at all
  --read-only \                    # read-only root filesystem
  --tmpfs /tmp:rw,noexec,nosuid \  # writable scratch space only
  --memory=512m --cpus=1 --pids-limit=256 \
  --network=none \
  -v "$REPO:/workspace:ro" \       # the repository is mounted read-only
  -w /workspace \
  issuepilot-sandbox:latest python -m pytest -v
```

* If Docker is missing, the agent **refuses to run the code** and explains why.
* The one exception is the **bundled demo**: it is first-party, trusted code,
  so it may fall back to a restricted local subprocess. This fallback is only
  reachable when `demo_mode` is on, it is logged as a warning, and the backend
  used is shown in the report.

**GitHub safety:**

* The agent never pushes to the default branch. It creates a branch
  (`issuepilot/fix-<issue-number>`), commits there and opens a pull request.
* If no fork exists, one is created first (requires `GITHUB_TOKEN` and
  `GITHUB_USER`).
* `--dry-run` (or the *Dry run* switch in the UI) stops right before the
  push: the branch, the commit and the diff are still produced locally.
* Tokens are never written to the repository, never logged and never passed to
  the sandbox (`issuepilot.patch.env_without_secrets` strips them from the
  environment of the test run).

---

## The demo repositories

`demo/repo1`, `demo/repo2` and `demo/repo3` are tiny projects, each with a
deliberate bug, an issue describing it and a test that catches it:

| Demo | Bug | Expected behaviour |
|------|-----|--------------------|
| `repo1` | `calc.add` subtracts instead of adding | `add(2, 3) == 5` |
| `repo2` | `strings.truncate` ignores the ellipsis length | `len(truncate("hello world", 8)) == 8` |
| `repo3` | `lists.chunk` drops the last partial chunk | `chunk([1,2,3,4,5], 2) == [[1,2],[3,4],[5]]` |

Each demo ships a `_reference/` directory with the expected patch, the
reference reproduction test and a short root cause analysis. The `reference`
LLM backend replays them, which is what makes the offline demo deterministic.
`_reference` is excluded from the working copy, so the agent never sees the
answer.

---

## Configuration

Everything is configured through environment variables (see `.env.example`).

| Variable | Default | Meaning |
|----------|---------|---------|
| `GITHUB_TOKEN` | – | Token used for forking and opening pull requests. |
| `GITHUB_USER` | – | Login of the user the fork belongs to. |
| `GITHUB_FORK_REMOTE` | `origin` | Remote used as the base of the fork. |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama server. |
| `OLLAMA_MODEL` | `qwen2.5-coder:7b` | Model used for the test, the fix and the root cause. |
| `OLLAMA_TIMEOUT` | `180` | Seconds before a model call is aborted. |
| `LLM_TEMPERATURE` | `0.1` | Sampling temperature. |
| `RETRIEVER_BACKEND` | `chroma` | `chroma`, `faiss` or `memory`. |
| `CHROMA_PATH` / `FAISS_PATH` | `./.issuepilot/...` | Where the vector stores live. |
| `RETRIEVAL_TOP_K` | `12` | Chunks handed to the model. |
| `SANDBOX_IMAGE` | `issuepilot-sandbox:latest` | Image used to run the tests. |
| `SANDBOX_TIMEOUT` | `120` | Wall-clock limit of a test run. |
| `SANDBOX_MEMORY` / `SANDBOX_CPUS` | `512m` / `1` | Container limits. |
| `SANDBOX_TEST_COMMAND` | `python -m pytest -v -p no:cacheprovider` | Command executed in the container. |
| `MAX_ATTEMPTS` | `3` | Draft/apply/test attempts per run. |
| `FORMAT_RETRIES` | `2` | Extra model calls used to fix a malformed answer. |
| `WORK_DIR` | `./.issuepilot/runs` | Where the runs and their reports are written. |
| `LOG_LEVEL` | `INFO` | Logging verbosity. |

---

## CLI reference

```bash
python -m issuepilot.cli run <issue-url> [--dry-run] [--backend auto|ollama|reference] [--verbose]
python -m issuepilot.cli demo [--repo repo1] [--backend reference|ollama] [--keep]
python -m issuepilot.cli doctor
python -m issuepilot.cli sandbox-build
python -m issuepilot.cli serve [--host 127.0.0.1] [--port 8000] [--reload]
python -m issuepilot.cli report <run-dir>
```

`run` exits with code `1` when the issue could not be fixed, so it can be used
in CI.

---

## HTTP API

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/` | The web UI. |
| `GET` | `/api/health` | Liveness plus the active configuration. |
| `GET` | `/api/config` | Demo repositories and default settings. |
| `POST` | `/api/runs` | Start a run (`{"issue_url": ..., "demo": "repo1", "dry_run": true}`). |
| `GET` | `/api/runs/{id}` | The state of a run. |
| `GET` | `/api/runs/{id}/events` | Server-sent events: `step_start`, `step_end`, `log`, `diff`, `test_report`, `report`, `error`, `done`. |

---

## Project layout

```
issuepilot/            the agent
  graph.py             LangGraph workflow
  nodes/               one module per step
  retrieval.py         chunking + vector stores
  edits.py             search/replace edits -> unified diff
  patch.py             diff handling and git helpers
  sandbox.py           Docker sandbox and pytest parsing
  github_ops.py        GitHub operations
  llm.py               Ollama and reference backends
  api.py               FastAPI app + SSE
  cli.py               Typer CLI
web/index.html         the single page UI
demo/repo{1,2,3}/      buggy projects with issues and tests
scripts/               helper scripts (patch generation, model check)
tests/                 pytest suite
```

---

## Testing

```bash
pytest tests/ -q
```

The suite covers the chunker, the three vector stores, the diff machinery, the
edit format, the sandbox output parser, the graph (including a full offline
demo run) and the HTTP API (including the SSE stream).

---

## Limitations (read this)

* **Model quality decides the outcome.** The pipeline is robust, but a small
  model is the bottleneck. Measured on the bundled demo with `gemma3:4b` on an
  Apple Silicon laptop: `repo1` is fixed, `repo2` and `repo3` fail because the
  model repeats the buggy code instead of correcting it. The same run with the
  deterministic reference backend fixes all three. For real issues use a
  code-capable model (for example `qwen2.5-coder:7b`, `codellama` or a
  hosted model) and raise `MAX_ATTEMPTS` if needed.
* **The agent cannot fix what it cannot see.** Retrieval is lexical (a hashing
  embedder plus BM25-style scoring), not semantic. Very large repositories may
  need a better embedder; `RETRIEVER_BACKEND` and `RETRIEVAL_TOP_K` are the
  knobs.
* **Only Python is supported.** The chunker, the test runner and the sandbox
  image assume Python and pytest.
* **The sandbox image has to be built once** (`make sandbox-image`) and needs
  the repository's own dependencies to be installable inside the container.
  The current image ships pytest only.
* **Docker was not available in the development environment** used to write
  this, so the Docker path is implemented and reviewed but was exercised
  through the local fallback of the demo.

---

## Design decisions

**The demo replays a reference patch.** A deterministic demo needs a
deterministic model. Each demo repository ships the expected patch and the
reference test in `_reference/`, and the `reference` backend replays them. The
alternative — hoping a local model reproduces the demo every time — makes the
demo flaky and hides the interesting part (the pipeline). The `_reference`
directory is stripped from the working copy, so the agent never sees it.

**The model answers with search/replace edits, not with a diff.** Small models
cannot count lines, so their `@@` headers are wrong and the patch never
applies. Asking for `{"file": ..., "old": ..., "new": ...}` is much easier:
the agent locates the snippet itself, tolerates small copying mistakes
(different quoting, different indentation) and only writes back the lines that
really changed. See `issuepilot/edits.py`.

**The reproduction test has to fail.** A test that passes on the buggy code
proves nothing, and a fix that satisfies such a test is not a fix. The agent
runs the generated test against the unfixed code and rejects it (with the
reason) when it passes.

**The local fallback is limited to the demo.** Falling back to a local
subprocess is a security decision, not a convenience: it is only allowed for
the bundled, trusted demo code. For anything cloned from the internet, the
agent refuses to execute code without Docker.

**ChromaDB is shared behind a lock.** Its Rust backend cannot be opened twice
for the same directory in one process and does not like concurrent access, so
the client is cached globally and every access is serialised. Each indexing
run is tagged with an index id so parallel runs never see each other's
chunks.

**LangGraph nodes return the whole state.** Because of that, a node must not
have both a static and a conditional edge — LangGraph would run two nodes in
the same superstep. Every linear step therefore uses a conditional edge.

---

## License

MIT.
