"""LLM access layer.

IssuePilot talks to a *local* model served by `Ollama <https://ollama.ai>`_
through its HTTP API.  A second, fully deterministic backend exists for the
offline demo: it replays a reference patch shipped next to each demo issue so
the pipeline can be exercised without a model server.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import httpx

from issuepilot.config import settings

logger = logging.getLogger(__name__)


class LLMError(RuntimeError):
    """Raised when the language model cannot be reached or returns garbage."""


class OllamaBackend:
    """Chat backend that talks to a local Ollama server."""

    name = "ollama"

    def __init__(self, model: Optional[str] = None, base_url: Optional[str] = None, timeout: Optional[int] = None):
        """Configure the backend."""
        self.model = model or settings.ollama_model
        self.base_url = (base_url or settings.ollama_base_url).rstrip("/")
        self.timeout = timeout or settings.ollama_timeout

    def available(self) -> bool:
        """Return ``True`` when the Ollama server answers and serves the model."""
        try:
            response = httpx.get(f"{self.base_url}/api/tags", timeout=3.0)
            if response.status_code != 200:
                return False
            payload = response.json()
            models = [m.get("name", "") for m in payload.get("models", [])]
            if not models:
                return False
            if not any(m == self.model or m.split(":")[0] == self.model.split(":")[0] for m in models):
                logger.warning("Model %s not pulled; available models: %s", self.model, ", ".join(models))
                return False
            return True
        except Exception:
            return False

    def generate(self, system: str, prompt: str, temperature: Optional[float] = None) -> str:
        """Run a single chat completion and return the assistant message."""
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        try:
            response = httpx.post(
                f"{self.base_url}/api/chat",
                json={
                    "model": self.model,
                    "messages": messages,
                    "stream": False,
                    "options": {"temperature": settings.llm_temperature if temperature is None else temperature},
                },
                timeout=self.timeout,
            )
        except Exception as exc:  # network / timeout
            raise LLMError(
                f"Could not reach Ollama at {self.base_url}. "
                f"Start it with `ollama serve` and pull the model with `ollama pull {self.model}`. ({exc})"
            ) from exc
        if response.status_code != 200:
            raise LLMError(f"Ollama returned HTTP {response.status_code}: {response.text[:400]}")
        try:
            content = response.json()["message"]["content"]
        except Exception as exc:
            raise LLMError(f"Unexpected Ollama response: {response.text[:400]}") from exc
        if not content.strip():
            raise LLMError("Ollama returned an empty response")
        return content


class ReferenceBackend:
    """Deterministic backend used by the offline demo.

    It replays the reference solution (``solution.patch``) and the reference
    reproduction test (``reference_repro.py``) that ship with every demo
    issue, so the demo always produces the same result without a model.
    """

    name = "reference"

    def __init__(self, reference_dir: Path):
        """Point the backend at a directory holding the reference artefacts."""
        self.reference_dir = Path(reference_dir)

    def available(self) -> bool:
        """The reference backend is always available."""
        return True

    def generate(self, system: str, prompt: str, temperature: Optional[float] = None) -> str:
        """Return the reference patch (the demo only asks for patches here)."""
        patch_file = self.reference_dir / "solution.patch"
        if patch_file.exists():
            return patch_file.read_text(encoding="utf-8")
        raise LLMError(f"Reference patch not found at {patch_file}")

    def reference_test(self) -> Optional[str]:
        """Return the reference reproduction test, if present."""
        test_file = self.reference_dir / "reference_repro.py"
        if test_file.exists():
            return test_file.read_text(encoding="utf-8")
        return None


class LLMClient:
    """High-level façade over the available LLM backends."""

    def __init__(
        self,
        backend: str = "auto",
        model: Optional[str] = None,
        base_url: Optional[str] = None,
        reference_dir: Optional[Path] = None,
    ):
        """Select a backend.

        Args:
            backend: ``"auto"``, ``"ollama"`` or ``"reference"``.
            model: Ollama model name override.
            base_url: Ollama base URL override.
            reference_dir: Directory holding demo reference artefacts.
        """
        self._ollama = OllamaBackend(model=model, base_url=base_url)
        self._reference = ReferenceBackend(reference_dir) if reference_dir else None
        self.backend_name = backend

    # -- introspection -----------------------------------------------------
    def available(self) -> bool:
        """Return ``True`` when the selected backend can serve requests."""
        if self.backend_name == "reference":
            return self._reference is not None
        if self.backend_name == "ollama":
            return self._ollama.available()
        return self._ollama.available() or self._reference is not None

    def describe(self) -> str:
        """Human readable description of the active backend."""
        if self.backend_name == "reference":
            return "reference (offline demo)"
        if self._ollama.available():
            return f"ollama:{self._ollama.model} @ {self._ollama.base_url}"
        if self._reference is not None:
            return "reference (offline demo; Ollama unavailable)"
        return "unavailable"

    # -- generation --------------------------------------------------------
    def _use_reference(self) -> bool:
        """Return ``True`` when the deterministic reference backend is active."""
        if self._reference is None:
            return False
        if self.backend_name == "reference":
            return True
        if self.backend_name == "auto" and not self._ollama.available():
            return True
        return False

    def generate(self, system: str, prompt: str, temperature: Optional[float] = None) -> str:
        """Generate text using the configured backend."""
        if self._use_reference():
            logger.info("Using the deterministic reference backend")
            return self._reference.generate(system, prompt, temperature)
        return self._ollama.generate(system, prompt, temperature)

    def generate_test(self, system: str, prompt: str) -> str:
        """Generate a reproduction test."""
        if self._use_reference():
            test = self._reference.reference_test()
            if test:
                return test
            raise LLMError("The reference backend has no reference test to replay")
        return self.generate(system, prompt)

    def generate_patch(self, system: str, prompt: str) -> str:
        """Generate a unified diff patch."""
        return self.generate(system, prompt)

    def reference_test(self) -> Optional[str]:
        """Return the reference reproduction test when using the demo backend."""
        return self._reference.reference_test() if self._reference else None
