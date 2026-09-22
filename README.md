# Agentic Local Pre-flight Reviewer

![Python](https://img.shields.io/badge/Python-3.11%2B-blue?logo=python&logoColor=white)
![Streamlit](https://img.shields.io/badge/UI-Streamlit-FF4B4B?logo=streamlit&logoColor=white)
![Ollama](https://img.shields.io/badge/LLM-Ollama-000000?logo=ollama&logoColor=white)
![LangChain](https://img.shields.io/badge/LLM-LangChain-1C3C3C)
![LangGraph](https://img.shields.io/badge/Agents-LangGraph-1C3C3C)
![Langfuse](https://img.shields.io/badge/tracing-Langfuse-4B5EFF)
![LanceDB](https://img.shields.io/badge/RAG-LanceDB-22B8CF)
![Ruff](https://img.shields.io/badge/lint-Ruff-261230?logo=ruff&logoColor=white)

Local pre-flight review of Git changes with RAG (LanceDB), LangGraph agents, and human approval in Streamlit. LLMs run via Ollama on the host.

## Requirements

- Python **3.11 or 3.12** (`make setup` prefers these; avoid 3.14)
- [Ollama](https://ollama.com) installed and running
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
3. Make or stage changes in that repo.
4. Click **Run audit**.
5. Review the report and **Approve Fixes** / **Reject**.

## Configuration

Copy `.env.example` → `.env` (`make setup` does this):

| Variable | Default | Notes |
| --- | --- | --- |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | |
| `OLLAMA_CHAT_MODEL` | `qwen2.5-coder:7b` | Larger option: `qwen2.5-coder:14b` |
| `OLLAMA_EMBED_MODEL` | `nomic-embed-text` | Alternative: `mxbai-embed-large` |
| `LANCEDB_PATH` | `./data/lancedb` | Gitignored |
| `DEFAULT_REPO_PATH` | empty | Prefills the UI |
| `LANGFUSE_*` | off / localhost:3000 | Optional tracing; see [docs/dev.md](docs/dev.md#optional-langfuse-tracing-dev) |

## Architecture

```
extract_diff → retrieve_context → (security ‖ quality ‖ test) → consolidator
    → human_approval → [INTERRUPT] → apply_fixes
```

| Stage | What it does |
| --- | --- |
| **extract_diff** | Reads local Git changes: staged, then unstaged, then limited untracked `.py`, then branch vs main. Caps size for the model context. |
| **retrieve_context** | Embeds the diff and searches LanceDB for related Python chunks from the indexed repo. |
| **security / quality / test** | Three specialist agents. Each gets diff + RAG context and returns structured JSON via Ollama. On one GPU they usually run sequentially. |
| **consolidator** | Merges the three reports, dedupes, ranks severity, and produces a markdown report plus `proposed_fixes`. |
| **human_approval** | Pause before apply. Streamlit shows the report and waits. |
| **apply_fixes** | Runs after **Approve** or **Reject**. Applies unified diffs with `git apply` when approved. Broken LLM diffs are repaired from the working tree when possible; invalid or conflicting patches are dropped. |

**HITL:** the graph uses `MemorySaver` and `interrupt_before=["apply_fixes_node"]`. Streamlit resumes the same thread with `user_approved=True/False`.

**Indexing** is separate from the graph. **Index Repo** walks `*.py`, splits with `Language.PYTHON`, embeds with Ollama, and stores vectors in LanceDB under `data/`.

## Make targets

| Target | Action |
| --- | --- |
| `make setup` | venv + deps + `.env` |
| `make pull-models` | `ollama pull` for chat and embeddings |
| `make run` | Streamlit |
| `make test` | pytest |
| `make lint` | ruff |
