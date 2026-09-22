"""Streamlit UI for Agentic Local Pre-flight Reviewer."""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core import config
from src.core.graph import new_thread_id, resume_with_approval, run_until_approval
from src.core.llm import OllamaUnavailableError, check_ollama_reachable
from src.rag.indexer import index_repository

st.set_page_config(
    page_title="Agentic Local Pre-flight Reviewer",
    page_icon=":mag:",
    layout="wide",
)

st.title("Agentic Local Pre-flight Reviewer")
st.caption(
    f"Ollama · {config.OLLAMA_CHAT_MODEL} · embeddings {config.OLLAMA_EMBED_MODEL}"
)

with st.sidebar:
    st.header("Settings")
    default_repo = config.DEFAULT_REPO_PATH or str(ROOT)
    repo_path = st.text_input("Repository path to audit", value=default_repo)
    st.markdown(
        f"- Chat: `{config.OLLAMA_CHAT_MODEL}`\n"
        f"- Embed: `{config.OLLAMA_EMBED_MODEL}`\n"
        f"- LanceDB: `{config.LANCEDB_PATH}`"
    )
    if st.button("Check Ollama", use_container_width=True):
        try:
            check_ollama_reachable(require_models=True)
            st.success(
                f"Ollama OK at {config.OLLAMA_BASE_URL} "
                f"(chat `{config.OLLAMA_CHAT_MODEL}`, embed `{config.OLLAMA_EMBED_MODEL}`)"
            )
        except OllamaUnavailableError as exc:
            st.error(str(exc))

    st.divider()
    st.subheader("Observability")
    langfuse_ready = config.langfuse_configured()
    if "langfuse_toggle" not in st.session_state:
        st.session_state.langfuse_toggle = bool(
            config.LANGFUSE_ENABLED and langfuse_ready
        )
    st.checkbox(
        "Trace with Langfuse",
        key="langfuse_toggle",
        disabled=not langfuse_ready,
        help=(
            "Send LangGraph / LLM spans to Langfuse "
            "(typically Docker on localhost). Applies to Approve/Reject of the same audit."
        ),
    )
    if not langfuse_ready:
        st.caption(
            "Set `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` in `.env` "
            f"(host: `{config.LANGFUSE_BASE_URL}`). See docs/dev.md."
        )
    else:
        st.caption(f"Host: `{config.LANGFUSE_BASE_URL}`")
        st.link_button(
            "Open Langfuse",
            config.LANGFUSE_BASE_URL,
            use_container_width=True,
        )

for key, default in (
    ("thread_id", None),
    ("audit_state", None),
    ("awaiting_approval", False),
    ("last_index", None),
    ("langfuse_trace_run", False),
):
    if key not in st.session_state:
        st.session_state[key] = default

col_index, col_run = st.columns(2)

with col_index:
    st.subheader("1. Index RAG")
    if st.button("Index Repo", type="secondary", use_container_width=True):
        if not repo_path or not Path(repo_path).is_dir():
            st.error("Invalid repository path.")
        else:
            try:
                with st.status("Indexing repository…", expanded=True) as status:
                    st.write("Chunking Python (`Language.PYTHON`)…")
                    result = index_repository(repo_path, recreate=True)
                    st.session_state.last_index = result
                    status.update(
                        label=(
                            f"Indexed: {result['chunks']} chunks / "
                            f"{result['files_indexed']} files"
                        ),
                        state="complete",
                    )
                st.success(
                    f"{result['files_indexed']} files → {result['chunks']} chunks "
                    f"in `{result['db_path']}`"
                )
            except OllamaUnavailableError as exc:
                st.error(str(exc))
            except Exception as exc:  # noqa: BLE001
                st.error(f"Indexing error: {exc}")

    if st.session_state.last_index:
        st.json(st.session_state.last_index)

with col_run:
    st.subheader("2. Audit changes")
    if st.button("Run audit", type="primary", use_container_width=True):
        if not repo_path or not Path(repo_path).is_dir():
            st.error("Invalid repository path.")
        else:
            try:
                thread_id = new_thread_id()
                # Keep the same Langfuse choice for Approve/Reject of this audit.
                st.session_state.langfuse_trace_run = bool(
                    st.session_state.langfuse_toggle and langfuse_ready
                )
                with st.status("Running LangGraph…", expanded=True) as status:
                    st.write("extract_diff_node…")
                    st.write("retrieve_context_node…")
                    st.write("security / quality / test agents (parallel)…")
                    st.write("consolidator_node…")
                    st.write("human_approval_node → interrupt before apply…")
                    tid, values, awaiting, trace_warn = run_until_approval(
                        repo_path,
                        thread_id=thread_id,
                        trace=st.session_state.langfuse_trace_run,
                    )
                    st.session_state.thread_id = tid
                    st.session_state.audit_state = values
                    st.session_state.awaiting_approval = awaiting
                    if trace_warn:
                        st.warning(trace_warn)
                    elif st.session_state.langfuse_trace_run:
                        st.caption(
                            f"Trace sent to Langfuse · session `{tid}` · "
                            f"[open]({config.LANGFUSE_BASE_URL})"
                        )
                    if awaiting:
                        status.update(
                            label="Audit ready: awaiting approval",
                            state="complete",
                        )
                    else:
                        status.update(
                            label="Audit finished (no HITL interrupt)",
                            state="complete",
                        )
            except OllamaUnavailableError as exc:
                st.error(str(exc))
                st.session_state.awaiting_approval = False
            except Exception as exc:  # noqa: BLE001
                st.error(f"Graph error: {exc}")
                st.session_state.awaiting_approval = False

state = st.session_state.audit_state
if state:
    st.divider()
    st.subheader("Results")

    for err in state.get("errors") or []:
        st.warning(err)

    tabs = st.tabs(["Report", "Agent reviews", "Proposed patches", "Diff"])

    with tabs[0]:
        st.markdown(state.get("consolidated_report") or "_No report._")

    with tabs[1]:
        reviews = state.get("reviews") or {}
        if not reviews:
            st.info("No reviews (was there a diff?).")
        for name, payload in reviews.items():
            with st.expander(name, expanded=False):
                st.json(payload)

    with tabs[2]:
        fixes = state.get("proposed_fixes") or []
        if not fixes:
            st.info("No proposed patches.")
        for fix in fixes:
            st.markdown(
                f"**{fix.get('id', 'FIX')}** · `{fix.get('severity', '?')}` · "
                f"`{fix.get('file', '')}`"
            )
            st.caption(fix.get("rationale", ""))
            diff = fix.get("unified_diff") or ""
            if diff:
                st.code(diff, language="diff")

    with tabs[3]:
        st.code(state.get("git_diff") or "(empty)", language="diff")
        modified = state.get("modified_files") or []
        st.caption(
            "Files: " + (", ".join(modified) if modified else "(none)")
        )

    if st.session_state.awaiting_approval and st.session_state.thread_id:
        st.divider()
        st.subheader("3. Human-in-the-loop")
        st.info(
            "The graph is paused (`interrupt_before=apply_fixes_node`). "
            "Approve or reject the patches to continue."
        )
        c1, c2 = st.columns(2)
        with c1:
            if st.button("Approve Fixes", type="primary", use_container_width=True):
                try:
                    with st.spinner("Applying patches…"):
                        values, trace_warn = resume_with_approval(
                            st.session_state.thread_id,
                            approved=True,
                            trace=bool(st.session_state.langfuse_trace_run),
                        )
                    st.session_state.audit_state = values
                    st.session_state.awaiting_approval = False
                    if trace_warn:
                        st.warning(trace_warn)
                    st.success(values.get("apply_result") or "Done.")
                    st.rerun()
                except Exception as exc:  # noqa: BLE001
                    st.error(f"Apply error: {exc}")
        with c2:
            if st.button("Reject", use_container_width=True):
                try:
                    values, trace_warn = resume_with_approval(
                        st.session_state.thread_id,
                        approved=False,
                        trace=bool(st.session_state.langfuse_trace_run),
                    )
                    st.session_state.audit_state = values
                    st.session_state.awaiting_approval = False
                    if trace_warn:
                        st.warning(trace_warn)
                    st.warning(values.get("apply_result") or "Rejected.")
                    st.rerun()
                except Exception as exc:  # noqa: BLE001
                    st.error(f"Reject error: {exc}")

    apply_result = state.get("apply_result")
    if apply_result and not st.session_state.awaiting_approval:
        st.success(apply_result)
