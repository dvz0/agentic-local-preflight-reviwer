"""Python-aware indexing into LanceDB."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import lancedb
from langchain_core.documents import Document
from langchain_text_splitters import Language, RecursiveCharacterTextSplitter

from src.core import config
from src.core.llm import get_embeddings


def _iter_python_files(repo_path: Path) -> list[Path]:
    files: list[Path] = []
    for path in repo_path.rglob("*.py"):
        if any(part in config.SKIP_DIRS for part in path.parts):
            continue
        files.append(path)
    return sorted(files)


def _build_splitter() -> RecursiveCharacterTextSplitter:
    return RecursiveCharacterTextSplitter.from_language(
        language=Language.PYTHON,
        chunk_size=config.CHUNK_SIZE,
        chunk_overlap=config.CHUNK_OVERLAP,
    )


def _load_documents(repo_path: Path) -> list[Document]:
    docs: list[Document] = []
    for path in _iter_python_files(repo_path):
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if not text.strip():
            continue
        rel = str(path.relative_to(repo_path))
        docs.append(
            Document(
                page_content=text,
                metadata={"source": rel, "path": str(path)},
            )
        )
    return docs


def index_repository(repo_path: str | Path, *, recreate: bool = True) -> dict[str, Any]:
    """Chunk Python sources with Language.PYTHON and upsert into LanceDB."""
    root = Path(repo_path).expanduser().resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"Repo not found: {root}")

    raw_docs = _load_documents(root)
    if not raw_docs:
        raise ValueError(f"No .py files found in {root}")

    splitter = _build_splitter()
    chunks = splitter.split_documents(raw_docs)
    for i, chunk in enumerate(chunks):
        chunk.metadata["chunk_id"] = i
        chunk.metadata["repo_path"] = str(root)

    config.LANCEDB_PATH.mkdir(parents=True, exist_ok=True)
    db = lancedb.connect(str(config.LANCEDB_PATH))

    if recreate and config.LANCEDB_TABLE in db.table_names():
        db.drop_table(config.LANCEDB_TABLE)

    embeddings = get_embeddings()
    texts = [c.page_content for c in chunks]
    vectors = embeddings.embed_documents(texts)

    rows = []
    for chunk, vector in zip(chunks, vectors, strict=True):
        rows.append(
            {
                "text": chunk.page_content,
                "vector": vector,
                "source": chunk.metadata.get("source", ""),
                "path": chunk.metadata.get("path", ""),
                "chunk_id": int(chunk.metadata.get("chunk_id", 0)),
                "repo_path": chunk.metadata.get("repo_path", str(root)),
            }
        )

    db.create_table(config.LANCEDB_TABLE, data=rows, mode="overwrite")

    return {
        "repo_path": str(root),
        "files_indexed": len(raw_docs),
        "chunks": len(chunks),
        "table": config.LANCEDB_TABLE,
        "db_path": str(config.LANCEDB_PATH),
    }
