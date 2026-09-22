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
from src.core.tracing import traced_invoke
from src.rag.retriever import retrieve_context
from src.utils.git_tools import (
    apply_unified_diff,
    file_excerpts_for_prompt,
    get_diff_and_files,
    paths_from_git_diff,
    validate_proposed_fixes,
)

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

    git_diff = state.get("git_diff") or ""
    repo_path = state.get("repo_path") or ""
    paths = paths_from_git_diff(git_diff)
    excerpts = ""
    if repo_path and paths:
        try:
            excerpts = file_excerpts_for_prompt(
                repo_path, paths, max_chars=max(1500, config.MAX_CONSOLIDATOR_CHARS // 4)
            )
        except Exception:  # noqa: BLE001 — consolidator still works without excerpts
            excerpts = ""

    # Reserve room for the real diff + file contents so fixes can match the tree.
    excerpt_budget = min(len(excerpts), max(1000, config.MAX_CONSOLIDATOR_CHARS // 4))
    diff_budget = min(
        len(git_diff), max(1500, (config.MAX_CONSOLIDATOR_CHARS - excerpt_budget) // 3)
    )
    reports_budget = max(
        800, config.MAX_CONSOLIDATOR_CHARS - diff_budget - excerpt_budget - 300
    )
    payload = json.dumps(reviews, ensure_ascii=False, indent=2)[:reports_budget]
    diff_excerpt = git_diff[:diff_budget]
    if len(git_diff) > diff_budget:
        diff_excerpt += "\n... [diff truncated] ..."
    file_excerpt = excerpts[:excerpt_budget]
    if len(excerpts) > excerpt_budget:
        file_excerpt += "\n... [file contents truncated] ..."

    user_parts = [
        f"## Original git diff\n```diff\n{diff_excerpt}\n```",
    ]
    if file_excerpt.strip():
        user_parts.append(
            "## CURRENT FILE CONTENTS (copy old_string from here)\n" + file_excerpt
        )
    user_parts.append(f"## Agent reports\n{payload}")

    messages = [
        {"role": "system", "content": CONSOLIDATOR_SYSTEM},
        {"role": "user", "content": "\n\n".join(user_parts)},
    ]
    try:
        response = llm.invoke(messages)
        content = response.content if hasattr(response, "content") else str(response)
        data = parse_llm_json(content)
        if not isinstance(data, dict):
            raise TypeError("consolidator did not return a JSON object")
    except (json.JSONDecodeError, TypeError, OSError, RuntimeError, ValueError):
        data = _fallback_consolidate(reviews)

    raw_fixes = data.get("proposed_fixes") or []
    report = data.get("report", "") or ""
    errors: list[str] = []
    if raw_fixes and repo_path:
        valid, rejected = validate_proposed_fixes(repo_path, raw_fixes)
        repaired_n = sum(1 for f in valid if f.get("_repaired"))
        if rejected or repaired_n:
            kept_msg = (
                f"Kept {len(valid)}/{len(raw_fixes)} patch(es) "
                f"(repaired {repaired_n}) after validation.\n"
            )
            report = report.rstrip() + "\n\n### Patch validation\n" + kept_msg
        if rejected:
            errors.append(
                "Dropped "
                f"{len(rejected)}/{len(raw_fixes)} proposed patch(es) "
                "(failed validation or conflicted): "
                + "; ".join(rejected)
            )
        for fix in valid:
            fix.pop("_repaired", None)
        raw_fixes = valid
    else:
        raw_fixes = [
            p
            for p in raw_fixes
            if (p.get("unified_diff") or "").strip()
            or (isinstance(p.get("old_string"), str) and p.get("new_string") is not None)
        ]

    return {
        "consolidated_report": report,
        "proposed_fixes": raw_fixes,
        "user_approved": False,
        "errors": errors,
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
    repo_path: str,
    thread_id: str | None = None,
    *,
    trace: bool = False,
) -> tuple[str, dict, bool, str | None]:
    """
    Invoke graph until HITL interrupt (or END if no diff).

    Returns (thread_id, state_values, awaiting_approval, trace_warning).
    ``awaiting_approval`` is True only when next node is ``apply_fixes_node``.
    ``trace_warning`` is set when Langfuse tracing was requested but skipped.
    """
    graph = get_compiled_graph()
    tid = thread_id or new_thread_id()
    with traced_invoke(
        thread_id=tid,
        repo_path=repo_path,
        enabled=trace,
        run_name="pr-audit",
        tags=["local-pr-auditor", "audit"],
    ) as setup:
        graph.invoke(
            {
                "repo_path": repo_path,
                "reviews": {},
                "rag_context": [],
                "proposed_fixes": [],
                "user_approved": False,
                "errors": [],
            },
            config=setup.config,
        )
        snapshot = graph.get_state(setup.config)
    awaiting = "apply_fixes_node" in (snapshot.next or ())
    return tid, dict(snapshot.values), awaiting, setup.warning


def resume_with_approval(
    thread_id: str,
    approved: bool,
    *,
    trace: bool = False,
) -> tuple[dict, str | None]:
    """Set user_approved and continue past the interrupt.

    Returns (state_values, trace_warning).
    """
    graph = get_compiled_graph()
    base = {"configurable": {"thread_id": thread_id}}
    prior = graph.get_state(base)
    repo_path = str((prior.values or {}).get("repo_path") or "")
    tag = "approval" if approved else "reject"
    with traced_invoke(
        thread_id=thread_id,
        repo_path=repo_path,
        enabled=trace,
        run_name=f"pr-audit-{tag}",
        tags=["local-pr-auditor", tag],
    ) as setup:
        graph.update_state(setup.config, {"user_approved": approved})
        graph.invoke(None, config=setup.config)
        snapshot = graph.get_state(setup.config)
    return dict(snapshot.values), setup.warning
