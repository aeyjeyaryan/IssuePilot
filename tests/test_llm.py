"""Tests for the LLM layer (Ollama and reference backends)."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from issuepilot.llm import LLMClient, LLMError, OllamaBackend, ReferenceBackend


def test_parse_of_a_reference_directory(tmp_path: Path) -> None:
    (tmp_path / "solution.patch").write_text("--- a/x\n+++ b/x\n", encoding="utf-8")
    (tmp_path / "reference_repro.py").write_text("def test_x():\n    pass\n", encoding="utf-8")
    backend = ReferenceBackend(tmp_path)
    assert backend.available()
    assert "+++ b/x" in backend.generate("system", "prompt")
    assert "test_x" in backend.reference_test()


def test_a_missing_reference_patch_is_an_error(tmp_path: Path) -> None:
    backend = ReferenceBackend(tmp_path)
    with pytest.raises(LLMError):
        backend.generate("system", "prompt")


def test_a_missing_reference_test_is_none(tmp_path: Path) -> None:
    (tmp_path / "solution.patch").write_text("--- a/x\n+++ b/x\n", encoding="utf-8")
    assert ReferenceBackend(tmp_path).reference_test() is None


def test_the_reference_backend_is_always_available(tmp_path: Path) -> None:
    client = LLMClient(backend="reference", reference_dir=tmp_path)
    assert client.available()
    assert "reference" in client.describe()


def test_the_client_falls_back_to_the_reference_backend(tmp_path: Path) -> None:
    """With no reachable Ollama the client uses the deterministic backend."""
    (tmp_path / "solution.patch").write_text("--- a/x\n+++ b/x\n", encoding="utf-8")
    client = LLMClient(backend="auto", reference_dir=tmp_path, base_url="http://127.0.0.1:1")
    assert "reference" in client.describe()
    assert "+++ b/x" in client.generate("system", "prompt")


def test_the_ollama_backend_reports_an_unreachable_server() -> None:
    backend = OllamaBackend(base_url="http://127.0.0.1:1")
    assert not backend.available()
    with pytest.raises(LLMError):
        backend.generate("system", "prompt")


def test_the_ollama_backend_sends_the_messages(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    def fake_post(url: str, **kwargs):
        captured["url"] = url
        captured["json"] = kwargs.get("json")
        return httpx.Response(200, json={"message": {"content": "hello"}})

    monkeypatch.setattr(httpx, "post", fake_post)
    backend = OllamaBackend(model="demo-model", base_url="http://ollama.test")
    answer = backend.generate("system prompt", "user prompt", temperature=0.5)
    assert answer == "hello"
    assert captured["url"].endswith("/api/chat")
    payload = captured["json"]
    assert payload["model"] == "demo-model"
    assert payload["messages"][0] == {"role": "system", "content": "system prompt"}
    assert payload["options"]["temperature"] == 0.5


def test_the_ollama_backend_rejects_an_empty_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        httpx, "post", lambda url, **kwargs: httpx.Response(200, json={"message": {"content": "  "}})
    )
    backend = OllamaBackend(base_url="http://ollama.test")
    with pytest.raises(LLMError):
        backend.generate("system", "prompt")


def test_the_ollama_backend_reports_an_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(httpx, "post", lambda url, **kwargs: httpx.Response(500, text="boom"))
    backend = OllamaBackend(base_url="http://ollama.test")
    with pytest.raises(LLMError):
        backend.generate("system", "prompt")
