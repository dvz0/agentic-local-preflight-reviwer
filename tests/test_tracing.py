"""Unit tests for optional Langfuse tracing helpers (no live Langfuse)."""

from __future__ import annotations

from src.core.tracing import build_trace_setup, traced_invoke


def test_build_trace_setup_disabled():
    setup = build_trace_setup(
        thread_id="t1",
        repo_path="/tmp/repo",
        enabled=False,
        run_name="pr-audit",
    )
    assert setup.active is False
    assert setup.warning is None
    assert setup.config == {"configurable": {"thread_id": "t1"}}
    assert "callbacks" not in setup.config


def test_build_trace_setup_missing_keys(monkeypatch):
    monkeypatch.setattr("src.core.config.LANGFUSE_PUBLIC_KEY", "")
    monkeypatch.setattr("src.core.config.LANGFUSE_SECRET_KEY", "")
    setup = build_trace_setup(
        thread_id="t2",
        repo_path="/tmp/repo",
        enabled=True,
        run_name="pr-audit",
    )
    assert setup.active is False
    assert setup.warning is not None
    assert "LANGFUSE_PUBLIC_KEY" in setup.warning
    assert "callbacks" not in setup.config


def test_traced_invoke_disabled_context():
    with traced_invoke(
        thread_id="t3",
        repo_path="/tmp/repo",
        enabled=False,
        run_name="pr-audit",
    ) as setup:
        assert setup.active is False
        assert setup.config["configurable"]["thread_id"] == "t3"
