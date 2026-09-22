"""Shared settings loaded from environment / .env."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(_ROOT / ".env")


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


def _bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
OLLAMA_CHAT_MODEL = os.getenv("OLLAMA_CHAT_MODEL", "qwen2.5-coder:7b")
OLLAMA_EMBED_MODEL = os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text")

LANCEDB_PATH = Path(os.getenv("LANCEDB_PATH", str(_ROOT / "data" / "lancedb"))).expanduser()
if not LANCEDB_PATH.is_absolute():
    LANCEDB_PATH = (_ROOT / LANCEDB_PATH).resolve()

LANCEDB_TABLE = os.getenv("LANCEDB_TABLE", "code_chunks")
DEFAULT_REPO_PATH = os.getenv("DEFAULT_REPO_PATH", "")

CHUNK_SIZE = _int("CHUNK_SIZE", 1500)
CHUNK_OVERLAP = _int("CHUNK_OVERLAP", 150)
RAG_TOP_K = _int("RAG_TOP_K", 4)

# Keep prompts inside the model context window (7B defaults are often ~8k–32k tokens).
OLLAMA_NUM_CTX = _int("OLLAMA_NUM_CTX", 16384)
MAX_DIFF_CHARS = _int("MAX_DIFF_CHARS", 6000)
MAX_RAG_CHARS = _int("MAX_RAG_CHARS", 8000)
MAX_CONSOLIDATOR_CHARS = _int("MAX_CONSOLIDATOR_CHARS", 12000)

# Optional Langfuse tracing (self-hosted or cloud). Off by default.
LANGFUSE_ENABLED = _bool("LANGFUSE_ENABLED", False)
LANGFUSE_PUBLIC_KEY = os.getenv("LANGFUSE_PUBLIC_KEY", "").strip()
LANGFUSE_SECRET_KEY = os.getenv("LANGFUSE_SECRET_KEY", "").strip()
LANGFUSE_BASE_URL = os.getenv("LANGFUSE_BASE_URL", "http://localhost:3000").rstrip("/")
LANGFUSE_TRACING_ENVIRONMENT = os.getenv(
    "LANGFUSE_TRACING_ENVIRONMENT", "development"
).strip() or "development"


def langfuse_configured() -> bool:
    """True when API keys are present (host may still be down)."""
    return bool(LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY)


SKIP_DIRS = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "node_modules",
    "dist",
    "build",
    ".tox",
    "data",
    ".eggs",
}
