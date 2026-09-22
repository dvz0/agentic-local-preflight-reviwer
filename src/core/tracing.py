"""Optional Langfuse tracing for LangGraph invokes.

When tracing is disabled or misconfigured, invokes proceed unchanged.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from src.core import config


@dataclass
class TraceSetup:
    """LangGraph ``invoke`` config plus optional UI warning."""

    config: dict[str, Any]
    warning: str | None = None
    active: bool = False
    _flush: Any = field(default=None, repr=False)


def _base_config(thread_id: str) -> dict[str, Any]:
    return {"configurable": {"thread_id": thread_id}}


def ensure_langfuse_env() -> None:
    """Push config values into os.environ for the Langfuse SDK."""
    import os

    if config.LANGFUSE_PUBLIC_KEY:
        os.environ["LANGFUSE_PUBLIC_KEY"] = config.LANGFUSE_PUBLIC_KEY
    if config.LANGFUSE_SECRET_KEY:
        os.environ["LANGFUSE_SECRET_KEY"] = config.LANGFUSE_SECRET_KEY
    if config.LANGFUSE_BASE_URL:
        os.environ["LANGFUSE_BASE_URL"] = config.LANGFUSE_BASE_URL
    if config.LANGFUSE_TRACING_ENVIRONMENT:
        os.environ["LANGFUSE_TRACING_ENVIRONMENT"] = (
            config.LANGFUSE_TRACING_ENVIRONMENT
        )


def build_trace_setup(
    *,
    thread_id: str,
    repo_path: str,
    enabled: bool,
    run_name: str,
    tags: list[str] | None = None,
) -> TraceSetup:
    """Build invoke config; soft-disables tracing on failure instead of raising."""
    base = _base_config(thread_id)
    if not enabled:
        return TraceSetup(config=base)

    if not config.langfuse_configured():
        return TraceSetup(
            config=base,
            warning=(
                "Langfuse tracing requested but LANGFUSE_PUBLIC_KEY / "
                "LANGFUSE_SECRET_KEY are missing; continuing without traces."
            ),
        )

    try:
        from langfuse import get_client
        from langfuse.langchain import CallbackHandler
    except ImportError:
        return TraceSetup(
            config=base,
            warning=(
                "Langfuse tracing requested but the `langfuse` package is not "
                "installed; continuing without traces."
            ),
        )

    ensure_langfuse_env()
    try:
        client = get_client()
        if hasattr(client, "auth_check") and not client.auth_check():
            return TraceSetup(
                config=base,
                warning=(
                    f"Langfuse unreachable at {config.LANGFUSE_BASE_URL}; "
                    "continuing without traces."
                ),
            )
    except Exception as exc:  # noqa: BLE001
        return TraceSetup(
            config=base,
            warning=(
                f"Langfuse auth failed ({exc}); continuing without traces."
            ),
        )

    tag_list = list(tags or [])
    handler = CallbackHandler()
    invoke_config: dict[str, Any] = {
        **base,
        "callbacks": [handler],
        "run_name": run_name,
        "metadata": {"repo_path": repo_path, "thread_id": thread_id},
        "tags": tag_list,
    }
    return TraceSetup(
        config=invoke_config,
        active=True,
        _flush=client.flush,
    )


@contextmanager
def traced_invoke(
    *,
    thread_id: str,
    repo_path: str,
    enabled: bool,
    run_name: str,
    tags: list[str] | None = None,
) -> Iterator[TraceSetup]:
    """Yield invoke config; flush Langfuse when the block exits."""
    setup = build_trace_setup(
        thread_id=thread_id,
        repo_path=repo_path,
        enabled=enabled,
        run_name=run_name,
        tags=tags,
    )
    if not setup.active:
        yield setup
        return

    from langfuse import propagate_attributes

    ctx = propagate_attributes(
        session_id=thread_id,
        tags=list(tags or []),
        metadata={"repo_path": repo_path, "thread_id": thread_id},
        trace_name=run_name,
        environment=config.LANGFUSE_TRACING_ENVIRONMENT,
    )
    try:
        with ctx:
            yield setup
    finally:
        flush = setup._flush
        if callable(flush):
            try:
                flush()
            except Exception:  # noqa: BLE001, S110
                pass
