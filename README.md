# Local Agentic PR Pre-flight

**Local** PR review tool with RAG (LanceDB), LangGraph agents, and human approval in Streamlit. LLMs run via **Ollama** on the host 

## Requirements

- Python **3.11 or 3.12** (recommended; `make setup` prefers these; avoid 3.14)
- [Ollama](https://ollama.com) installed and running
- NVIDIA GPU with ~8–12 GB VRAM for `qwen2.5-coder:7b`
- A git repository with local changes to audit

## Quick start

```bash
cd local_pr_auditor
make setup
make pull-models   # qwen2.5-coder:7b + nomic-embed-text
make run           # Streamlit
```

1. In the UI, set the path to the repo under audit.
2. Click **Index Repo**.
3. Make (or stage) changes in that repo.
4. Click **Run audit**.
5. Review the report and **Approve Fixes** / **Reject**.

## Configuration

Copy `.env.example` → `.env` (`make setup` does this):

| Variable | Default | Notes |
| --- | --- | --- |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | |
| `OLLAMA_CHAT_MODEL` | `qwen2.5-coder:7b` | If VRAM allows: `qwen2.5-coder:14b` |
| `OLLAMA_EMBED_MODEL` | `nomic-embed-text` | Alternative: `mxbai-embed-large` |
| `LANCEDB_PATH` | `./data/lancedb` | Gitignored |
| `DEFAULT_REPO_PATH` | (empty) | Prefills the UI |

## Architecture

```
extract_diff → retrieve_context → (security ‖ quality ‖ test) → consolidator
    → human_approval → [INTERRUPT] → apply_fixes
```

| Stage | What it does |
| --- | --- |
| **extract_diff** | Reads local Git changes (staged → unstaged → limited untracked `.py` → branch vs main). Caps size so prompts fit the model context. |
| **retrieve_context** | Embeds the diff, searches LanceDB for related Python chunks from the indexed repo (RAG). |
| **security / quality / test** | Three specialist agents (fan-out). Each gets diff + RAG context and returns structured JSON findings / optional patches via Ollama. On one GPU they usually run back-to-back, not truly in parallel. |
| **consolidator** | Fan-in: merges the three reports, dedupes, severity, unified markdown report + `proposed_fixes`. |
| **human_approval** | Marker before apply. Streamlit shows the report and waits. |
| **apply_fixes** | Runs only after you **Approve** or **Reject**. Applies unified diffs with `git apply` when approved. |

**HITL:** the graph is compiled with `MemorySaver` and `interrupt_before=["apply_fixes_node"]`. Streamlit resumes the same thread with `user_approved=True/False`.

**Indexing (separate from the graph):** **Index Repo** walks `*.py`, splits with `Language.PYTHON`, embeds with Ollama, and stores vectors in embedded LanceDB under `data/`.
## Make targets

| Target | Action |
| --- | --- |
| `make setup` | venv + deps + `.env` |
| `make pull-models` | `ollama pull` for chat and embeddings |
| `make run` | Streamlit |
| `make test` | pytest |
| `make lint` | ruff |

