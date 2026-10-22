"""Node 3: index the codebase and retrieve the relevant code (RAG)."""

from __future__ import annotations

import logging
from typing import Any, Dict

from issuepilot.config import settings
from issuepilot.events import STEP_START, emit
from issuepilot.retrieval import CodeChunker, Retriever

logger = logging.getLogger(__name__)


def index_codebase_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """Chunk the repository, store it in the vector store and query it."""
    logger.info("node=index_codebase repo=%s", state.get("repo_path"))
    emit(STEP_START, step="index_codebase")
    emit("log", message="Indexing the codebase")

    try:
        chunker = CodeChunker()
        chunks = chunker.chunk_directory(state["repo_path"])
        if not chunks:
            state["error"] = "No Python source files were found in the repository"
            emit("error", message=state["error"])
            return state

        retriever = Retriever(backend=settings.retriever_backend)
        indexed = retriever.index(chunks)
        state["retriever"] = retriever
        state["chunks_count"] = indexed
        state["store"] = retriever.store_name

        issue = state.get("issue", {})
        query = f"{issue.get('title', '')}\n{issue.get('body', '')}".strip()
        if not query:
            query = "bug fix"
        relevant = retriever.query(query, k=settings.retrieval_top_k)
        state["relevant_chunks"] = [
            {"content": item.chunk.content, "file_path": item.chunk.file_path,
             "start_line": item.chunk.start_line, "end_line": item.chunk.end_line,
             "name": item.chunk.name, "type": item.chunk.type, "score": round(item.score, 4)}
            for item in relevant
        ]
        logger.info(
            "node=index_codebase chunks=%d store=%s relevant=%d",
            indexed,
            retriever.store_name,
            len(relevant),
        )
        emit("log", message=f"Indexed {indexed} chunks ({retriever.store_name}); retrieved {len(relevant)} relevant chunks")
        for item in relevant[:5]:
            emit("log", message=f"  ↳ {item.chunk.location} {item.chunk.type} {item.chunk.name}")
    except Exception as exc:  # pragma: no cover - defensive
        # Retrieval problems are not fatal: the agent can still work with the
        # repository contents it already has.
        state["warning"] = f"Could not index the codebase: {exc}"
        logger.warning("node=index_codebase error=%s", exc)
        emit("log", message=state["warning"])
    return state
