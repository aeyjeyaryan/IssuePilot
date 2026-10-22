"""Code retrieval (RAG) for IssuePilot.

Python sources are split into *chunks* at function and class boundaries using
the standard library :mod:`ast` module.  Every chunk keeps the file path and
the line range it came from, which is what the agent shows in the UI and what
the prompt uses to point the model at the right code.

Three storage backends are supported:

``chroma``
    Persistent ChromaDB collection (the default).
``faiss``
    Local FAISS index with a persisted sidecar of chunk metadata.
``memory``
    Pure-python fallback used by the unit tests and when no vector store is
    installed.

Embeddings are computed locally with a deterministic hashing embedder, so the
whole pipeline works offline (no model download, no network access).
"""

from __future__ import annotations

import ast
import hashlib
import logging
import math
import re
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from issuepilot.config import settings

logger = logging.getLogger(__name__)

#: Directories that are never indexed.
SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    "__pycache__",
    ".venv",
    "venv",
    "env",
    "node_modules",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "build",
    "dist",
    ".tox",
    ".idea",
    ".vscode",
    "site-packages",
    ".eggs",
}

#: Files larger than this are skipped (they are usually data, not code).
MAX_FILE_BYTES = 512 * 1024

_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|\d+|[^\sA-Za-z0-9_]")


@dataclass
class Chunk:
    """A retrievable piece of source code."""

    content: str
    file_path: str
    start_line: int
    end_line: int
    name: str
    type: str
    docstring: str = ""
    parent: str = ""

    @property
    def location(self) -> str:
        """Human readable ``path:start-end`` location."""
        return f"{self.file_path}:{self.start_line}-{self.end_line}"

    def to_metadata(self) -> Dict[str, object]:
        """Return the metadata payload stored alongside the embedding."""
        return {
            "file_path": self.file_path,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "name": self.name,
            "type": self.type,
            "parent": self.parent,
            "docstring": self.docstring[:500],
        }


@dataclass
class RetrievedChunk:
    """A chunk returned by the retriever together with its score."""

    chunk: Chunk
    score: float

    def format(self, width: int = 120) -> str:
        """Render the chunk for inclusion in a prompt."""
        header = f"# file: {self.chunk.file_path} (lines {self.chunk.start_line}-{self.chunk.end_line})"
        if self.chunk.parent:
            header += f" in {self.chunk.parent}"
        header += f" [{self.chunk.type} {self.chunk.name}]"
        body = "\n".join(self.chunk.content.splitlines()[:400])
        return f"{header}\n{body}"


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------
class CodeChunker:
    """Split Python sources into function/class sized chunks."""

    def chunk_file(self, file_path: str, content: str) -> List[Chunk]:
        """Chunk a single Python source file.

        Args:
            file_path: Path recorded in the chunk metadata.
            content: Source text.

        Returns:
            The chunks found in the file.  Files that cannot be parsed fall
            back to a single ``module`` chunk.
        """
        lines = content.splitlines()
        try:
            tree = ast.parse(content)
        except SyntaxError as exc:
            logger.warning("Could not parse %s (%s); indexing it as a single chunk", file_path, exc)
            return [
                Chunk(
                    content=content,
                    file_path=file_path,
                    start_line=1,
                    end_line=max(1, len(lines)),
                    name="<unparsed>",
                    type="module",
                )
            ]

        chunks: List[Chunk] = []

        header = self._module_header(tree, lines)
        if header.strip():
            chunks.append(
                Chunk(
                    content=header,
                    file_path=file_path,
                    start_line=1,
                    end_line=max(1, len(header.splitlines())),
                    name="<module>",
                    type="module",
                )
            )

        parents = self._parent_map(tree)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            start = node.lineno
            end = getattr(node, "end_lineno", None) or start
            body = "\n".join(lines[start - 1 : end])
            parent = parents.get(id(node), "")
            chunks.append(
                Chunk(
                    content=body,
                    file_path=file_path,
                    start_line=start,
                    end_line=end,
                    name=node.name,
                    type="class" if isinstance(node, ast.ClassDef) else "function",
                    docstring=ast.get_docstring(node) or "",
                    parent=parent,
                )
            )

        if len(chunks) == 1:  # only a header -> index the whole file
            chunks.append(
                Chunk(
                    content=content,
                    file_path=file_path,
                    start_line=1,
                    end_line=max(1, len(lines)),
                    name="<module>",
                    type="module",
                )
            )
        return chunks

    def chunk_directory(self, root: str | Path, extensions: Sequence[str] = (".py",)) -> List[Chunk]:
        """Chunk every matching file below ``root``.

        Args:
            root: Directory to walk.
            extensions: File extensions to index.

        Returns:
            All chunks found, ordered by file path.
        """
        root_path = Path(root).resolve()
        chunks: List[Chunk] = []
        for path in sorted(self._iter_source_files(root_path, extensions)):
            try:
                content = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError) as exc:
                logger.warning("Skipping %s: %s", path, exc)
                continue
            rel = str(path.relative_to(root_path))
            chunks.extend(self.chunk_file(rel, content))
        logger.info("Indexed %d chunks from %s", len(chunks), root_path)
        return chunks

    # -- helpers -----------------------------------------------------------
    @staticmethod
    def _iter_source_files(root: Path, extensions: Sequence[str]) -> Iterable[Path]:
        """Yield source files below ``root`` skipping noisy directories."""
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix not in extensions:
                continue
            if any(part in SKIP_DIRS for part in path.parts):
                continue
            try:
                if path.stat().st_size > MAX_FILE_BYTES:
                    logger.debug("Skipping large file %s", path)
                    continue
            except OSError:
                continue
            yield path

    @staticmethod
    def _module_header(tree: ast.Module, lines: List[str]) -> str:
        """Return the module header (imports, constants and other top level code)."""
        last = 0
        pieces: List[str] = []
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            end = getattr(node, "end_lineno", None) or node.lineno
            pieces.append("\n".join(lines[node.lineno - 1 : end]))
            last = max(last, end)
        return "\n".join(p for p in pieces if p.strip())

    @staticmethod
    def _parent_map(tree: ast.Module) -> Dict[int, str]:
        """Map the id of every nested definition to its dotted parent path."""
        parents: Dict[int, str] = {}

        def visit(node: ast.AST, prefix: str) -> None:
            for child in ast.iter_child_nodes(node):
                if isinstance(child, ast.ClassDef):
                    dotted = f"{prefix}{child.name}"
                    parents[id(child)] = prefix.rstrip(".")
                    visit(child, f"{dotted}.")
                elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    parents[id(child)] = prefix.rstrip(".")
                    visit(child, prefix)

        visit(tree, "")
        return parents


# ---------------------------------------------------------------------------
# Embeddings
# ---------------------------------------------------------------------------
class HashingEmbedder:
    """Deterministic, dependency-free text embedder.

    Tokens are hashed into a fixed size vector and the vector is
    L2-normalised.  It behaves like a very small hashing vectoriser: exact
    token matches dominate the score, which is enough to surface the right
    function for an issue that mentions its name.
    """

    def __init__(self, dim: int = 512):
        """Create an embedder with ``dim`` output dimensions."""
        self.dim = dim

    def embed(self, text: str) -> List[float]:
        """Return the embedding vector for ``text``."""
        vector = [0.0] * self.dim
        tokens = self._tokenize(text)
        if not tokens:
            return vector
        for token in tokens:
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            index = int.from_bytes(digest[:4], "big") % self.dim
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[index] += sign
        norm = math.sqrt(sum(v * v for v in vector))
        if norm == 0:
            return vector
        return [v / norm for v in vector]

    def embed_many(self, texts: Sequence[str]) -> List[List[float]]:
        """Embed a batch of texts."""
        return [self.embed(text) for text in texts]

    @staticmethod
    def _tokenize(text: str) -> List[str]:
        """Split text into lower-cased identifier-ish tokens."""
        out: List[str] = []
        for raw in _TOKEN_RE.findall(text):
            token = raw.lower()
            out.append(token)
            # Also index the camelCase / snake_case split parts.
            for part in re.split(r"_|(?<=[a-z0-9])(?=[A-Z])", raw):
                part = part.lower()
                if part and part != token:
                    out.append(part)
        return out


# ---------------------------------------------------------------------------
# Vector stores
# ---------------------------------------------------------------------------
class BaseVectorStore:
    """Common interface for the vector stores used by IssuePilot.

    Every store keeps the *index id* of the last indexing operation so that
    concurrent runs (the API can run several agents at the same time) never
    see each other's chunks.
    """

    name = "base"

    def add(
        self,
        chunks: Sequence[Chunk],
        embeddings: Sequence[Sequence[float]],
        index_id: str = "",
    ) -> None:
        """Store chunks and their embeddings."""
        raise NotImplementedError

    def query(self, embedding: Sequence[float], k: int, index_id: str = "") -> List[Tuple[int, float]]:
        """Return ``(chunk_index, score)`` pairs sorted by descending score."""
        raise NotImplementedError


class MemoryVectorStore(BaseVectorStore):
    """Pure-python cosine similarity store (tests and fallback)."""

    name = "memory"

    def __init__(self) -> None:
        """Create an empty store."""
        self._vectors: List[List[float]] = []

    def add(
        self,
        chunks: Sequence[Chunk],
        embeddings: Sequence[Sequence[float]],
        index_id: str = "",
    ) -> None:
        """Store chunks and their embeddings."""
        self._vectors = [list(vec) for vec in embeddings]

    def query(self, embedding: Sequence[float], k: int, index_id: str = "") -> List[Tuple[int, float]]:
        """Return the ``k`` most similar chunks."""
        scores: List[Tuple[int, float]] = []
        query = list(embedding)
        qnorm = math.sqrt(sum(v * v for v in query)) or 1.0
        for index, vector in enumerate(self._vectors):
            vnorm = math.sqrt(sum(v * v for v in vector)) or 1.0
            dot = sum(a * b for a, b in zip(query, vector))
            scores.append((index, dot / (qnorm * vnorm)))
        scores.sort(key=lambda item: item[1], reverse=True)
        return scores[:k]


class ChromaVectorStore(BaseVectorStore):
    """Persistent ChromaDB store.

    ChromaDB's Rust backend is not safe to use from several threads at once,
    so every access is serialised behind a process wide lock.
    """

    name = "chroma"

    #: ChromaDB's Rust backend cannot be instantiated twice for the same
    #: directory inside one process, so the client is cached globally.
    _clients: Dict[str, object] = {}
    _lock = threading.RLock()

    def __init__(self, path: str, collection_name: str = "issuepilot_code"):
        """Open (or create) the persistent collection."""
        from chromadb.config import Settings as ChromaSettings

        self.collection_name = collection_name
        self._client = self._get_client(path, ChromaSettings)
        with self._lock:
            self._collection = self._client.get_or_create_collection(
                name=collection_name,
                metadata={"hnsw:space": "cosine"},
            )

    @classmethod
    def _get_client(cls, path: str, chroma_settings) -> object:
        """Return the (cached) Chroma client for ``path``."""
        import chromadb

        key = str(Path(path).expanduser().resolve())
        with cls._lock:
            client = cls._clients.get(key)
            if client is None:
                client = chromadb.PersistentClient(
                    path=key,
                    settings=chroma_settings(anonymized_telemetry=False, allow_reset=True),
                )
                cls._clients[key] = client
            return client

    def add(
        self,
        chunks: Sequence[Chunk],
        embeddings: Sequence[Sequence[float]],
        index_id: str = "",
    ) -> None:
        """Store chunks and their embeddings."""
        if not chunks:
            return
        metadatas = []
        for chunk in chunks:
            metadata = chunk.to_metadata()
            metadata["index_id"] = index_id
            metadatas.append(metadata)
        with self._lock:
            self._collection.add(
                ids=[f"{index_id}-{i}" for i in range(len(chunks))],
                embeddings=[list(vec) for vec in embeddings],
                documents=[chunk.content for chunk in chunks],
                metadatas=metadatas,
            )

    def query(self, embedding: Sequence[float], k: int, index_id: str = "") -> List[Tuple[int, float]]:
        """Return the ``k`` most similar chunks of the given index."""
        with self._lock:
            where = {"index_id": index_id} if index_id else None
            count = self._collection.count()
            if count == 0:
                return []
            try:
                result = self._collection.query(
                    query_embeddings=[list(embedding)],
                    n_results=min(k, count),
                    where=where,
                    include=["distances"],
                )
            except Exception:
                if where is None:
                    raise
                # The index id is unknown to this collection (fresh store).
                result = self._collection.query(
                    query_embeddings=[list(embedding)],
                    n_results=min(k, count),
                    include=["distances"],
                )
        ids = (result.get("ids") or [[]])[0]
        distances = (result.get("distances") or [[]])[0]
        out: List[Tuple[int, float]] = []
        for identifier, distance in zip(ids, distances):
            parts = str(identifier).split("-")
            try:
                index = int(parts[-1])
            except ValueError:
                continue
            out.append((index, 1.0 - float(distance)))
        return out


class FaissVectorStore(BaseVectorStore):
    """Local FAISS index with a JSON sidecar for the chunk metadata."""

    name = "faiss"

    def __init__(self, path: str):
        """Open (or create) the FAISS index at ``path``."""
        import faiss
        import numpy as np

        self._faiss = faiss
        self._np = np
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.meta_path = self.path.with_suffix(self.path.suffix + ".meta.json")
        self._index = None
        self._metadata: List[Dict[str, object]] = []
        self._dim = 0
        self._lock = threading.RLock()

    def reset(self) -> None:
        """Drop the index and its metadata."""
        with self._lock:
            self._index = None
            self._metadata = []
            if self.meta_path.exists():
                self.meta_path.unlink()

    def add(
        self,
        chunks: Sequence[Chunk],
        embeddings: Sequence[Sequence[float]],
        index_id: str = "",
    ) -> None:
        """Store chunks and their embeddings."""
        import json

        if not chunks:
            return
        with self._lock:
            matrix = self._np.asarray([list(vec) for vec in embeddings], dtype="float32")
            self._dim = matrix.shape[1]
            index = self._faiss.IndexFlatIP(self._dim)
            index.add(matrix)
            self._index = index
            self._metadata = [
                {**chunk.to_metadata(), "index_id": index_id, "position": i}
                for i, chunk in enumerate(chunks)
            ]
            self._faiss.write_index(index, str(self.path))
            self.meta_path.write_text(json.dumps(self._metadata, indent=2), encoding="utf-8")

    def query(self, embedding: Sequence[float], k: int, index_id: str = "") -> List[Tuple[int, float]]:
        """Return the ``k`` most similar chunks of the given index."""
        import json

        with self._lock:
            if self._index is None and self.path.exists():
                self._index = self._faiss.read_index(str(self.path))
                if self.meta_path.exists():
                    self._metadata = json.loads(self.meta_path.read_text(encoding="utf-8"))
            if self._index is None:
                return []
            vector = self._np.asarray([list(embedding)], dtype="float32")
            total = self._index.ntotal
            scores, indices = self._index.search(vector, min(total, max(k * 4, k)))
            out: List[Tuple[int, float]] = []
            for score, index in zip(scores[0], indices[0]):
                if index < 0:
                    continue
                if index >= len(self._metadata):
                    continue
                metadata = self._metadata[index]
                if index_id and metadata.get("index_id") != index_id:
                    continue
                out.append((int(metadata.get("position", index)), float(score)))
        return out


# ---------------------------------------------------------------------------
# Retriever
# ---------------------------------------------------------------------------
class Retriever:
    """Chunk a repository and retrieve the pieces relevant to an issue."""

    def __init__(self, backend: str = "memory", embedder: Optional[HashingEmbedder] = None):
        """Create a retriever using the requested backend."""
        self.backend_name = backend
        self.embedder = embedder or HashingEmbedder()
        self.chunks: List[Chunk] = []
        self.index_id: str = ""
        self._store: BaseVectorStore = MemoryVectorStore()
        self._backend_error: Optional[str] = None
        self._init_store()

    def _init_store(self) -> None:
        """Instantiate the vector store, falling back to memory on failure."""
        backend = self.backend_name
        try:
            if backend == "chroma":
                self._store = ChromaVectorStore(settings.chroma_path)
            elif backend == "faiss":
                self._store = FaissVectorStore(settings.faiss_path)
            else:
                self._store = MemoryVectorStore()
        except Exception as exc:
            logger.warning("Vector store %s unavailable (%s); using the in-memory store", backend, exc)
            self._backend_error = str(exc)
            self._store = MemoryVectorStore()

    @property
    def store_name(self) -> str:
        """Name of the active vector store."""
        return self._store.name

    def index(self, chunks: Sequence[Chunk]) -> int:
        """Index ``chunks`` in the active store.

        The chunks are tagged with a fresh *index id* so that concurrent runs
        (the API can run several agents at once) never mix up their results.

        Args:
            chunks: Chunks to store.

        Returns:
            The number of chunks indexed.
        """
        self.chunks = list(chunks)
        if not self.chunks:
            return 0
        self.index_id = uuid.uuid4().hex[:12]
        embeddings = self.embedder.embed_many([chunk.content for chunk in self.chunks])
        self._store.add(self.chunks, embeddings, self.index_id)
        logger.info(
            "Indexed %d chunks into the %s store (index %s)",
            len(self.chunks),
            self._store.name,
            self.index_id,
        )
        return len(self.chunks)

    def query(self, query: str, k: Optional[int] = None) -> List[RetrievedChunk]:
        """Return the chunks most relevant to ``query``.

        Args:
            query: Natural language query (usually the issue body).
            k: Number of chunks to return.

        Returns:
            Retrieved chunks with their similarity scores, best first.
        """
        if not self.chunks:
            return []
        k = k or settings.retrieval_top_k
        embedding = self.embedder.embed(query)
        try:
            pairs = self._store.query(embedding, k, self.index_id)
        except Exception as exc:
            logger.warning("The %s store failed to answer the query (%s); using a local scan", self._store.name, exc)
            pairs = self._local_query(embedding, k)
        results: List[RetrievedChunk] = []
        for index, score in pairs:
            if 0 <= index < len(self.chunks):
                results.append(RetrievedChunk(chunk=self.chunks[index], score=float(score)))
        return results

    def _local_query(self, embedding: Sequence[float], k: int) -> List[Tuple[int, float]]:
        """Score the chunks locally (fallback when the store misbehaves)."""
        query = list(embedding)
        qnorm = math.sqrt(sum(v * v for v in query)) or 1.0
        scored: List[Tuple[int, float]] = []
        for index, chunk in enumerate(self.chunks):
            vector = self.embedder.embed(chunk.content)
            vnorm = math.sqrt(sum(v * v for v in vector)) or 1.0
            dot = sum(a * b for a, b in zip(query, vector))
            scored.append((index, dot / (qnorm * vnorm)))
        scored.sort(key=lambda item: item[1], reverse=True)
        return scored[:k]
