"""Ollama chat and embedding clients with clear connection errors."""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from langchain_ollama import ChatOllama, OllamaEmbeddings

from src.core import config


class OllamaUnavailableError(RuntimeError):
    """Raised when Ollama is down or a model is missing."""


def _fetch_tags() -> dict:
    url = f"{config.OLLAMA_BASE_URL}/api/tags"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            if resp.status >= 400:
                raise OllamaUnavailableError(
                    f"Ollama returned HTTP {resp.status} at {url}. "
                    "Is the service running? (`ollama serve`)"
                )
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise OllamaUnavailableError(
            f"Cannot connect to Ollama at {config.OLLAMA_BASE_URL}. "
            "Install from https://ollama.com and run `ollama serve`. "
            f"Detail: {exc}"
        ) from exc
    except json.JSONDecodeError as exc:
        raise OllamaUnavailableError(
            f"Invalid response from Ollama at {url}."
        ) from exc


def check_ollama_reachable(*, require_models: bool = False) -> None:
    data = _fetch_tags()
    if not require_models:
        return

    names = {m.get("name", "") for m in data.get("models") or []}

    def present(wanted: str) -> bool:
        if wanted in names:
            return True
        base = wanted.split(":")[0]
        return any(
            n == wanted or n.startswith(wanted) or n.startswith(base + ":")
            for n in names
        )

    missing = [
        m
        for m in (config.OLLAMA_CHAT_MODEL, config.OLLAMA_EMBED_MODEL)
        if not present(m)
    ]
    if missing:
        raise OllamaUnavailableError(
            "Missing Ollama models: "
            + ", ".join(f"`{m}`" for m in missing)
            + ". Run `make pull-models` or `ollama pull <model>`."
        )


def get_embeddings() -> OllamaEmbeddings:
    check_ollama_reachable(require_models=True)
    return OllamaEmbeddings(
        model=config.OLLAMA_EMBED_MODEL,
        base_url=config.OLLAMA_BASE_URL,
    )


def get_chat_llm(*, json_mode: bool = False) -> ChatOllama:
    check_ollama_reachable(require_models=True)
    kwargs: dict = {
        "model": config.OLLAMA_CHAT_MODEL,
        "base_url": config.OLLAMA_BASE_URL,
        "temperature": 0.1,
        "num_ctx": config.OLLAMA_NUM_CTX,
    }
    if json_mode:
        kwargs["format"] = "json"
    return ChatOllama(**kwargs)
