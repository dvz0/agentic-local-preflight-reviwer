"""LangGraph StateGraph: diff → RAG → parallel agents → consolidate → HITL → apply."""

from __future__ import annotations

import json
import uuid
from typing import Any

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from src.agents.json_utils import parse_llm_json
from src.agents.prompts import CONSOLIDATOR_SYSTEM
from src.agents.quality import run_quality_agent
from src.agents.security import run_security_agent
from src.agents.testing import run_test_agent
from src.core.llm import get_chat_llm
from src.core.state import AuditState
from src.rag.retriever import retrieve_context
from src.utils.git_tools import apply_unified_diff, get_diff_and_files

_checkpointer = MemorySaver()
_compiled = None


def extract_diff_node(state: AuditState) -> dict[str, Any]:
    from src.core import config

    repo_path = state["repo_path"]
    diff, files = get_diff_and_files(repo_path)
    if not diff.strip():
        return {
            "git_diff": "",
            "modified_files": [],
            "errors": ["No local changes (staged/unstaged/branch) to audit."],
        }
    errors: list[str] = []
    if len(diff) > config.MAX_DIFF_CHARS:
        errors.append(
            f"Diff truncated from {len(diff)} to {config.MAX_DIFF_CHARS} chars "
            "(too large for the model context). Prefer a smaller change set."
        )
        diff = diff[: config.MAX_DIFF_CHARS] + "\n... [truncated] ...\n"
    return {"git_diff": diff, "modified_files": files, "errors": errors}


def retrieve_context_node(state: AuditState) -> dict[str, Any]:
    diff = state.get("git_diff") or ""
    if not diff.strip():
        return {"rag_context": []}
    try:
        snippets = retrieve_context(diff)
    except FileNotFoundError as exc:
        return {"rag_context": [], "errors": [str(exc)]}
    return {"rag_context": snippets}


def security_agent_node(state: AuditState) -> dict[str, Any]:
    if not (state.get("git_diff") or "").strip():
        return {"reviews": {"security": {"agent": "security", "findings": [], "summary": "No diff"}}}
    result = run_security_agent(state["git_diff"], state.get("rag_context") or [])
    return {"reviews": {"security": result}}


def quality_agent_node(state: AuditState) -> dict[str, Any]:
    if not (state.get("git_diff") or "").strip():
        return {"reviews": {"quality": {"agent": "quality", "findings": [], "summary": "No diff"}}}
    result = run_quality_agent(state["git_diff"], state.get("rag_context") or [])
    return {"reviews": {"quality": result}}


def test_agent_node(state: AuditState) -> dict[str, Any]:
    if not (state.get("git_diff") or "").strip():
        return {"reviews": {"testing": {"agent": "testing", "findings": [], "summary": "No diff"}}}
    result = run_test_agent(state["git_diff"], state.get("rag_context") or [])
    return {"reviews": {"testing": result}}


def _fallback_consolidate(reviews: dict[str, Any]) -> dict[str, Any]:
    findings = []
    patches = []
    for name, report in (reviews or {}).items():
        for f in report.get("findings") or []:
            findings.append({**f, "source_agent": name})
        for p in report.get("proposed_patches") or []:
            patches.append(
                {
                    "id": f"FIX-{len(patches)+1:03d}",
                    "severity": "medium",
                    "file": p.get("file", ""),
                    "rationale": p.get("rationale", ""),
                    "unified_diff": p.get("unified_diff", ""),
                }
            )
    lines = ["## Consolidated report (fallback)", ""]
    for f in findings:
        lines.append(
            f"- **[{f.get('severity', '?')}]** {f.get('id', '')} "
            f"{f.get('title', '')} (`{f.get('file', '')}`)"
        )
    if not findings:
        lines.append("_No findings._")
    return {
        "report": "\n".join(lines),
        "proposed_fixes": [p for p in patches if (p.get("unified_diff") or "").strip()],
        "finding_count": len(findings),
    }


def consolidator_node(state: AuditState) -> dict[str, Any]:
    reviews = state.get("reviews") or {}
    llm = get_chat_llm(json_mode=True)
    from src.core import config

    payload = json.dumps(reviews, ensure_ascii=False, indent=2)[
        : config.MAX_CONSOLIDATOR_CHARS
    ]
    messages = [
        {"role": "system", "content": CONSOLIDATOR_SYSTEM},
        {"role": "user", "content": f"Agent reports:\n{payload}"},
    ]
    try:
        response = llm.invoke(messages)
        content = response.content if hasattr(response, "content") else str(response)
        data = parse_llm_json(content)
        if not isinstance(data, dict):
            raise TypeError("consolidator did not return a JSON object")
    except (json.JSONDecodeError, TypeError, OSError, RuntimeError, ValueError):
        data = _fallback_consolidate(reviews)

    return {
        "consolidated_report": data.get("report", ""),
        "proposed_fixes": data.get("proposed_fixes") or [],
        "user_approved": False,
    }


def human_approval_node(state: AuditState) -> dict[str, Any]:
    """Marker node before interrupt; Streamlit sets user_approved on resume."""
    return {
        "proposed_fixes": state.get("proposed_fixes") or [],
        "consolidated_report": state.get("consolidated_report") or "",
    }


def apply_fixes_node(state: AuditState) -> dict[str, Any]:
    if not state.get("user_approved"):
        return {"apply_result": "Rejected by user; no patches applied."}

    fixes = state.get("proposed_fixes") or []
    if not fixes:
        return {"apply_result": "No patches to apply."}

    applied = 0
    errors: list[str] = []
    for fix in fixes:
        diff = (fix.get("unified_diff") or "").strip()
        if not diff:
            continue
        try:
            apply_unified_diff(state["repo_path"], diff)
            applied += 1
        except Exception as exc:  # noqa: BLE001 — surface to UI
            errors.append(f"{fix.get('file', '?')}: {exc}")

    if errors:
        return {
            "apply_result": f"Applied {applied}/{len(fixes)}. Errors: " + "; ".join(errors),
            "errors": errors,
        }
    return {"apply_result": f"Applied {applied} patch(es) successfully."}


def _route_after_diff(state: AuditState) -> str:
    if not (state.get("git_diff") or "").strip():
        return "end"
    return "retrieve"


def build_graph():
    graph = StateGraph(AuditState)

    graph.add_node("extract_diff_node", extract_diff_node)
    graph.add_node("retrieve_context_node", retrieve_context_node)
    graph.add_node("security_agent", security_agent_node)
    graph.add_node("quality_agent", quality_agent_node)
    graph.add_node("test_agent", test_agent_node)
    graph.add_node("consolidator_node", consolidator_node)
    graph.add_node("human_approval_node", human_approval_node)
    graph.add_node("apply_fixes_node", apply_fixes_node)

    graph.add_edge(START, "extract_diff_node")
    graph.add_conditional_edges(
        "extract_diff_node",
        _route_after_diff,
        {"retrieve": "retrieve_context_node", "end": END},
    )
    # Fan-out
    graph.add_edge("retrieve_context_node", "security_agent")
    graph.add_edge("retrieve_context_node", "quality_agent")
    graph.add_edge("retrieve_context_node", "test_agent")
    # Fan-in
    graph.add_edge("security_agent", "consolidator_node")
    graph.add_edge("quality_agent", "consolidator_node")
    graph.add_edge("test_agent", "consolidator_node")
    graph.add_edge("consolidator_node", "human_approval_node")
    graph.add_edge("human_approval_node", "apply_fixes_node")
    graph.add_edge("apply_fixes_node", END)

    return graph.compile(
        checkpointer=_checkpointer,
        interrupt_before=["apply_fixes_node"],
    )


def get_compiled_graph():
    global _compiled
    if _compiled is None:
        _compiled = build_graph()
    return _compiled


def new_thread_id() -> str:
    return str(uuid.uuid4())


def run_until_approval(
    repo_path: str, thread_id: str | None = None
) -> tuple[str, dict, bool]:
    """
    Invoke graph until HITL interrupt (or END if no diff).

    Returns (thread_id, state_values, awaiting_approval).
    ``awaiting_approval`` is True only when next node is ``apply_fixes_node``.
    """
    graph = get_compiled_graph()
    tid = thread_id or new_thread_id()
    config = {"configurable": {"thread_id": tid}}
    graph.invoke(
        {
            "repo_path": repo_path,
            "reviews": {},
            "rag_context": [],
            "proposed_fixes": [],
            "user_approved": False,
            "errors": [],
        },
        config=config,
    )
    snapshot = graph.get_state(config)
    awaiting = "apply_fixes_node" in (snapshot.next or ())
    return tid, dict(snapshot.values), awaiting


def resume_with_approval(thread_id: str, approved: bool) -> dict:
    """Set user_approved and continue past the interrupt."""
    graph = get_compiled_graph()
    config = {"configurable": {"thread_id": thread_id}}
    graph.update_state(config, {"user_approved": approved})
    graph.invoke(None, config=config)
    snapshot = graph.get_state(config)
    return dict(snapshot.values)
