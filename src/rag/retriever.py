"""Similarity search over indexed Python chunks."""

from __future__ import annotations

from typing import Any

import lancedb

from src.core import config
from src.core.llm import get_embeddings


def _open_table():
    if not config.LANCEDB_PATH.exists():
        raise FileNotFoundError(
            f"LanceDB not found at {config.LANCEDB_PATH}. "
            "Use the «Index Repo» button before auditing."
        )
    db = lancedb.connect(str(config.LANCEDB_PATH))
    if config.LANCEDB_TABLE not in db.table_names():
        raise FileNotFoundError(
            f"Table «{config.LANCEDB_TABLE}» not found. Index the repository first."
        )
    return db.open_table(config.LANCEDB_TABLE)


def retrieve_context(query: str, *, top_k: int | None = None) -> list[str]:
    """Return formatted code snippets most similar to ``query``."""
    if not query or not query.strip():
        return []

    k = top_k or config.RAG_TOP_K
    table = _open_table()
    embeddings = get_embeddings()
    vector = embeddings.embed_query(query[: min(4000, config.MAX_DIFF_CHARS)])

    results = table.search(vector).limit(k).to_list()
    snippets: list[str] = []
    for row in results:
        source = row.get("source") or row.get("path") or "unknown"
        text = row.get("text") or ""
        snippets.append(f"# source: {source}\n{text}")
    return snippets


def retrieve_raw(query: str, *, top_k: int | None = None) -> list[dict[str, Any]]:
    """Same as retrieve_context but returns raw LanceDB rows."""
    if not query or not query.strip():
        return []
    k = top_k or config.RAG_TOP_K
    table = _open_table()
    embeddings = get_embeddings()
    vector = embeddings.embed_query(query[: min(4000, config.MAX_DIFF_CHARS)])
    return table.search(vector).limit(k).to_list()
