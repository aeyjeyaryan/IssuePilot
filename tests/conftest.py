"""Shared pytest fixtures for the IssuePilot test suite."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from issuepilot.config import settings  # noqa: E402


@pytest.fixture()
def chunker():
    """Return a :class:`CodeChunker`."""
    from issuepilot.retrieval import CodeChunker

    return CodeChunker()


@pytest.fixture()
def repo_copy(tmp_path):
    """Copy a demo repository into a temporary directory."""
    import shutil

    source = settings.demo_root / "repo1"
    destination = tmp_path / "repo"
    shutil.copytree(source, destination)
    return destination


@pytest.fixture()
def isolated_settings(tmp_path, monkeypatch):
    """Point the settings at a temporary directory."""
    monkeypatch.setattr(settings, "work_dir", str(tmp_path / "runs"))
    monkeypatch.setattr(settings, "chroma_path", str(tmp_path / "chroma"))
    monkeypatch.setattr(settings, "faiss_path", str(tmp_path / "faiss.index"))
    return settings
