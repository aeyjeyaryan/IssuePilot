"""Configuration loading for IssuePilot.

All settings are read from the environment (optionally seeded from a `.env`
file).  A single :class:`Settings` instance is exposed as :data:`settings` so
that every module shares the same view of the world.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

try:  # pragma: no cover - optional dependency
    from dotenv import load_dotenv

    load_dotenv(override=False)
except Exception:  # pragma: no cover - dotenv is optional
    pass

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _env_bool(name: str, default: bool) -> bool:
    """Read a boolean flag from the environment."""
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    """Read an integer from the environment, falling back to ``default``."""
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass
class Settings:
    """Runtime configuration for IssuePilot."""

    # --- GitHub -----------------------------------------------------------
    github_token: str = field(default_factory=lambda: os.getenv("GITHUB_TOKEN", ""))
    github_user: str = field(default_factory=lambda: os.getenv("GITHUB_USER", ""))
    github_api_url: str = field(default_factory=lambda: os.getenv("GITHUB_API_URL", "https://api.github.com"))

    # --- LLM --------------------------------------------------------------
    ollama_base_url: str = field(default_factory=lambda: os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"))
    ollama_model: str = field(default_factory=lambda: os.getenv("OLLAMA_MODEL", "qwen2.5-coder:7b"))
    ollama_timeout: int = field(default_factory=lambda: _env_int("OLLAMA_TIMEOUT", 180))
    llm_temperature: float = field(default_factory=lambda: float(os.getenv("LLM_TEMPERATURE", "0.1")))

    # --- Retrieval --------------------------------------------------------
    retriever_backend: str = field(default_factory=lambda: os.getenv("RETRIEVER_BACKEND", "chroma"))
    chroma_path: str = field(default_factory=lambda: os.getenv("CHROMA_PATH", "./.issuepilot/chroma"))
    faiss_path: str = field(default_factory=lambda: os.getenv("FAISS_PATH", "./.issuepilot/faiss.index"))
    retrieval_top_k: int = field(default_factory=lambda: _env_int("RETRIEVAL_TOP_K", 12))

    # --- Sandbox ----------------------------------------------------------
    sandbox_image: str = field(default_factory=lambda: os.getenv("SANDBOX_IMAGE", "issuepilot-sandbox:latest"))
    sandbox_timeout: int = field(default_factory=lambda: _env_int("SANDBOX_TIMEOUT", 120))
    sandbox_memory: str = field(default_factory=lambda: os.getenv("SANDBOX_MEMORY", "512m"))
    sandbox_cpus: str = field(default_factory=lambda: os.getenv("SANDBOX_CPUS", "1"))
    sandbox_test_command: str = field(
        default_factory=lambda: os.getenv("SANDBOX_TEST_COMMAND", "python -m pytest -v -p no:cacheprovider")
    )

    # --- Agent ------------------------------------------------------------
    max_attempts: int = field(default_factory=lambda: _env_int("MAX_ATTEMPTS", 3))
    #: Extra model calls made *before* a malformed patch reaches the sandbox.
    #: Weak local models often emit slightly wrong snippets; asking again with
    #: the parse error attached is much cheaper than a full sandbox round trip.
    format_retries: int = field(default_factory=lambda: _env_int("FORMAT_RETRIES", 2))
    work_dir: str = field(default_factory=lambda: os.getenv("WORK_DIR", "./.issuepilot/runs"))
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO"))
    log_json: bool = field(default_factory=lambda: _env_bool("LOG_JSON", True))

    # --- Misc -------------------------------------------------------------
    project_root: Path = field(default_factory=lambda: PROJECT_ROOT)
    demo_root: Path = field(default_factory=lambda: PROJECT_ROOT / "demo")

    def run_dir(self, run_id: str) -> Path:
        """Return the working directory used for ``run_id``."""
        return Path(self.work_dir).expanduser().resolve() / run_id

    def ensure_dirs(self) -> None:
        """Create the directories IssuePilot writes to."""
        Path(self.work_dir).expanduser().mkdir(parents=True, exist_ok=True)
        Path(self.chroma_path).expanduser().mkdir(parents=True, exist_ok=True)


settings = Settings()
