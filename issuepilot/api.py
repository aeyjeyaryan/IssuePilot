"""FastAPI backend for IssuePilot.

The API exposes a very small surface:

``POST /api/runs``
    Start a run; returns a run id.
``GET /api/runs/{run_id}/events``
    Server-Sent Events stream of everything the agent does.
``GET /api/runs/{run_id}``
    Snapshot of the run state.
``GET /``
    The single page web UI.
"""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Dict, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from issuepilot.config import settings
from issuepilot.events import EventBus
from issuepilot.logging_utils import setup_logging

logger = logging.getLogger(__name__)

WEB_DIR = Path(__file__).resolve().parent.parent / "web"


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Configure logging when the server starts."""
    setup_logging(settings.log_level, json_output=True)
    yield


app = FastAPI(title="IssuePilot API", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

if WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")


class RunRequest(BaseModel):
    """Body of a ``POST /api/runs`` request."""

    issue_url: str = Field(..., description="GitHub issue URL (or demo:// pseudo URL)")
    dry_run: bool = Field(True, description="Do everything except open the pull request")
    demo: bool = Field(False, description="Run against a bundled demo repository")
    demo_repo: Optional[str] = Field(None, description="Demo repository name (repo1, repo2, repo3)")
    backend: str = Field("auto", description="LLM backend: auto, ollama or reference")


class RunRecord:
    """Bookkeeping for one run."""

    def __init__(self, run_id: str, request: RunRequest, bus: EventBus):
        """Create the record."""
        self.run_id = run_id
        self.request = request
        self.bus = bus
        self.state: Dict[str, Any] = {}
        self.finished = False


RUNS: Dict[str, RunRecord] = {}


def _demo_paths(name: str) -> tuple[Path, Path]:
    """Return the (repository, issue) paths of a demo issue."""
    root = settings.demo_root / name
    return root, root / "ISSUE.md"


@app.get("/")
async def index() -> Any:
    """Serve the single page web UI."""
    index_file = WEB_DIR / "index.html"
    if index_file.exists():
        return FileResponse(str(index_file))
    return JSONResponse({"message": "IssuePilot API", "docs": "/docs"})


@app.get("/api/health")
async def health() -> Dict[str, Any]:
    """Health check."""
    return {"status": "ok", "version": app.version, "runs": len(RUNS)}


@app.post("/api/runs")
async def create_run(request: RunRequest) -> Dict[str, Any]:
    """Start a new agent run in the background."""
    demo_mode = request.demo
    demo_repo_path = ""
    demo_issue_path = ""
    issue_url = request.issue_url

    if demo_mode:
        name = request.demo_repo or "repo1"
        root, issue = _demo_paths(name)
        if not root.is_dir():
            raise HTTPException(status_code=400, detail=f"Unknown demo repository '{name}'")
        demo_repo_path = str(root)
        demo_issue_path = str(issue)
        issue_url = issue.as_uri()

    from issuepilot.runner import run_agent_in_thread

    run_id = uuid.uuid4().hex[:12]
    bus = EventBus(run_id)
    record = RunRecord(run_id, request, bus)
    RUNS[run_id] = record

    def _on_finish(state: Dict[str, Any]) -> None:
        record.state = state
        record.finished = True

    run_agent_in_thread(
        on_finish=_on_finish,
        issue_url=issue_url,
        dry_run=request.dry_run,
        demo_mode=demo_mode,
        demo_repo_path=demo_repo_path,
        demo_issue_path=demo_issue_path,
        bus=bus,
        backend=request.backend,
    )
    return {"run_id": run_id, "issue_url": issue_url, "dry_run": request.dry_run}


@app.get("/api/runs/{run_id}")
async def get_run(run_id: str) -> Dict[str, Any]:
    """Return the snapshot of a run."""
    record = RUNS.get(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Unknown run '{run_id}'")
    return {
        "run_id": run_id,
        "finished": record.finished,
        "outcome": record.state.get("outcome"),
        "state": {
            key: value
            for key, value in record.state.items()
            if isinstance(value, (str, int, float, bool, type(None), list, dict))
        },
    }


@app.get("/api/runs/{run_id}/events")
async def stream_events(run_id: str) -> Any:
    """Stream the agent events of a run as Server-Sent Events."""
    record = RUNS.get(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Unknown run '{run_id}'")

    queue = record.bus.subscribe()
    # Events emitted before the client connected are replayed, otherwise a fast
    # run (the offline demo takes about a second) would be invisible.
    backlog = record.bus.history()

    async def generator():
        try:
            for event in backlog:
                if event.kind == "done":
                    continue
                payload = json.dumps(event.to_dict(), default=str)
                yield f"event: {event.kind}\ndata: {payload}\n\n"
            while True:
                event = await queue.get()
                if event.kind == "done":
                    payload = json.dumps({"kind": "done", "data": {"outcome": record.state.get("outcome")}})
                    yield f"event: done\ndata: {payload}\n\n"
                    break
                payload = json.dumps(event.to_dict(), default=str)
                yield f"event: {event.kind}\ndata: {payload}\n\n"
        finally:
            record.bus.unsubscribe(queue)

    return _sse_response(generator())


def _sse_response(generator) -> Any:
    """Wrap an async generator in a Server-Sent Events response."""
    from starlette.responses import StreamingResponse

    return StreamingResponse(
        generator,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/config")
async def config() -> Dict[str, Any]:
    """Expose the (non secret) configuration to the UI."""
    return {
        "max_attempts": settings.max_attempts,
        "sandbox_image": settings.sandbox_image,
        "retriever_backend": settings.retriever_backend,
        "demo_repos": ["repo1", "repo2", "repo3"],
    }
