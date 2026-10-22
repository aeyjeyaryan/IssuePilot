"""Tests for the AST based code chunker."""

from __future__ import annotations

from issuepilot.retrieval import CodeChunker

SAMPLE = '''"""Module docstring."""

import os

CONSTANT = 3


def top_level(x):
    """Return x."""
    return x


class Widget:
    """A widget."""

    def method(self):
        return 42

    async def amethod(self):
        return 43


class Other:
    pass
'''


def test_finds_functions_and_classes(chunker: CodeChunker) -> None:
    chunks = chunker.chunk_file("sample.py", SAMPLE)
    names = {chunk.name for chunk in chunks}
    assert {"top_level", "Widget", "Other", "method", "amethod"} <= names


def test_classifies_chunk_types(chunker: CodeChunker) -> None:
    chunks = {chunk.name: chunk for chunk in chunker.chunk_file("sample.py", SAMPLE)}
    assert chunks["top_level"].type == "function"
    assert chunks["Widget"].type == "class"
    assert chunks["method"].type == "function"


def test_methods_know_their_parent_class(chunker: CodeChunker) -> None:
    chunks = {chunk.name: chunk for chunk in chunker.chunk_file("sample.py", SAMPLE)}
    assert chunks["method"].parent == "Widget"


def test_line_numbers_are_within_the_file(chunker: CodeChunker) -> None:
    total = len(SAMPLE.splitlines())
    for chunk in chunker.chunk_file("sample.py", SAMPLE):
        assert 1 <= chunk.start_line <= chunk.end_line <= total


def test_chunk_content_contains_the_definition(chunker: CodeChunker) -> None:
    chunks = {chunk.name: chunk for chunk in chunker.chunk_file("sample.py", SAMPLE)}
    assert "def top_level" in chunks["top_level"].content


def test_module_header_holds_the_imports(chunker: CodeChunker) -> None:
    chunks = chunker.chunk_file("sample.py", SAMPLE)
    header = next(chunk for chunk in chunks if chunk.name == "<module>")
    assert "import os" in header.content


def test_syntax_error_falls_back_to_a_single_chunk(chunker: CodeChunker) -> None:
    broken = "def broken(:\n    pass\n"
    chunks = chunker.chunk_file("broken.py", broken)
    assert len(chunks) == 1
    assert chunks[0].type == "module"
    assert chunks[0].content == broken


def test_chunk_directory_skips_pycache_and_venvs(tmp_path) -> None:
    (tmp_path / "a.py").write_text("def f():\n    return 1\n")
    cache = tmp_path / "__pycache__"
    cache.mkdir()
    (cache / "b.py").write_text("def g():\n    return 2\n")
    venv = tmp_path / ".venv"
    venv.mkdir()
    (venv / "c.py").write_text("def h():\n    return 3\n")

    chunks = CodeChunker().chunk_directory(tmp_path)

    assert chunks
    assert all("__pycache__" not in c.file_path for c in chunks)
    assert all(".venv" not in c.file_path for c in chunks)


def test_chunk_directory_indexes_a_whole_project(tmp_path) -> None:
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "mod.py").write_text("class Thing:\n    def go(self):\n        return 1\n")
    (tmp_path / "root.py").write_text("def helper():\n    return 2\n")

    chunks = CodeChunker().chunk_directory(tmp_path)
    names = {c.name for c in chunks}

    assert "Thing" in names
    assert "helper" in names
    assert any(c.file_path == "root.py" for c in chunks)
