"""Tests for the retrieval layer (chunking + vector stores + querying)."""

from __future__ import annotations

import pytest

from issuepilot.retrieval import Chunk, CodeChunker, HashingEmbedder, Retriever


def _chunk(name: str, content: str, path: str = "mod.py") -> Chunk:
    return Chunk(
        content=content,
        file_path=path,
        start_line=1,
        end_line=len(content.splitlines()),
        name=name,
        type="function",
    )


def test_index_returns_the_number_of_chunks() -> None:
    retriever = Retriever(backend="memory")
    assert retriever.index([_chunk("a", "def a():\n    pass")]) == 1


def test_query_returns_the_best_match_first() -> None:
    retriever = Retriever(backend="memory")
    retriever.index(
        [
            _chunk("add", "def add(a, b):\n    return a + b"),
            _chunk("subtract", "def subtract(a, b):\n    return a - b"),
            _chunk("multiply", "def multiply(a, b):\n    return a * b"),
        ]
    )
    results = retriever.query("add two numbers together", k=3)

    assert results
    assert results[0].chunk.name == "add"
    assert results[0].score > 0


def test_query_on_an_empty_index_returns_nothing() -> None:
    assert Retriever(backend="memory").query("anything") == []


def test_results_carry_the_source_location() -> None:
    retriever = Retriever(backend="memory")
    retriever.index([_chunk("add", "def add(a, b):\n    return a + b", "pkg/math.py")])
    result = retriever.query("add", k=1)[0]

    assert result.chunk.file_path == "pkg/math.py"
    assert result.chunk.start_line == 1
    assert "pkg/math.py" in result.chunk.location


def test_embedder_is_deterministic_and_normalised() -> None:
    embedder = HashingEmbedder(dim=64)
    first = embedder.embed("def add(a, b): return a + b")
    second = embedder.embed("def add(a, b): return a + b")

    assert first == second
    assert len(first) == 64
    assert abs(sum(value * value for value in first) - 1.0) < 1e-6


def test_embedder_handles_empty_input() -> None:
    assert HashingEmbedder(dim=32).embed("") == [0.0] * 32


@pytest.mark.parametrize("backend", ["memory", "faiss"])
def test_retriever_works_with_multiple_backends(backend: str, tmp_path, monkeypatch) -> None:
    from issuepilot.config import settings

    monkeypatch.setattr(settings, "faiss_path", str(tmp_path / "faiss.index"))
    retriever = Retriever(backend=backend)
    retriever.index([_chunk("add", "def add(a, b):\n    return a + b")])

    results = retriever.query("add", k=1)

    assert results and results[0].chunk.name == "add"


def test_chunker_and_retriever_round_trip(tmp_path) -> None:
    (tmp_path / "m.py").write_text("class A:\n    def go(self):\n        return 1\n")
    chunks = CodeChunker().chunk_directory(tmp_path)
    retriever = Retriever(backend="memory")
    retriever.index(chunks)

    assert retriever.chunks
    assert all(c.file_path == "m.py" for c in retriever.chunks)
    assert retriever.query("class A go", k=2)
