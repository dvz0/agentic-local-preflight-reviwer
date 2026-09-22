# Developer guide

Layout of the repo and how `src/` fits together. For setup and usage, see [../README.md](../README.md).

## Mental model

The app is a pipeline:

1. Read the local Git diff.
2. Look up related code in the rest of the repo via embeddings.
3. Call a local LLM three times with different instructions (security / quality / tests).
4. Merge those answers into one report plus optional patches.
5. Stop and wait for a human.
6. Optionally apply patches to disk.

Ollama runs the LLM on your machine. This repo is the glue: Git, search, orchestration, and UI.

| Term | Meaning |
| --- | --- |
| **LLM** | Model that turns a prompt into text; here, code-review JSON. |
| **Ollama** | Local server that loads models and answers HTTP calls. |
| **Embedding** | Vector representation of text used for similarity search. |
| **RAG** | Search the repo first, then put those snippets in the prompt. |
| **LanceDB** | On-disk vector store; no separate DB server. |
| **LangGraph** | Library for a graph of steps that share state. |
| **HITL** | Human-in-the-loop: pause before irreversible writes. |
| **Agent** | In this project: a prompt plus one LLM call with a specialty. |

## Top-level layout

```text
local_pr_auditor/
├── app.py              # Streamlit UI
├── src/                # Business logic
├── data/               # LanceDB after Index Repo (gitignored)
├── tests/              # Unit tests; no Ollama/GPU required
├── docs/               # Developer docs
│   └── dev.md          # this file
├── .streamlit/         # Streamlit config
├── .env / .env.example # Models, paths, context limits
├── Makefile            # setup / pull-models / run / test
├── requirements.txt
└── README.md
```

| Path | Role |
| --- | --- |
| `app.py` | Thin UI. Index → `rag.indexer`. Audit → `core.graph`. Approve/Reject → resume graph. Optional Langfuse toggle in the sidebar. |
| `data/` | Created at runtime. Do not commit. Safe to delete and re-index. |
| `tests/` | Git/state helpers, JSON parsing; no live LLM. |
| `.env` | Local overrides (`OLLAMA_*`, `MAX_DIFF_CHARS`, `LANGFUSE_*`, etc.). |

## How `src/` works

```text
src/
├── core/       # Config, Ollama clients, shared state, LangGraph
├── rag/        # Index repo + similarity search
├── agents/     # Prompts + three reviewer LLM calls
└── utils/      # Git diff / apply patch helpers
```

### Data flow

```text
app.py
  │
  ├─ Index Repo ──► rag/indexer.py ──► data/lancedb (vectors)
  │
  └─ Run audit ──► core/graph.py
                      │
                      ├─ utils/git_tools.py     → git_diff, files
                      ├─ rag/retriever.py       → rag_context snippets
                      ├─ agents/security.py     ┐
                      ├─ agents/quality.py      ├→ reviews{} (JSON)
                      ├─ agents/testing.py      ┘
                      ├─ consolidator (in graph)→ report + proposed_fixes
                      ├─ [INTERRUPT]            → wait for Streamlit
                      └─ apply_fixes            → git apply (if approved)
```

Shared state lives in **`AuditState`** (`core/state.py`): a typed dict that nodes update (diff, RAG snippets, reviews, report, `user_approved`, errors).

### `src/core/`: orchestration

| File | What it does |
| --- | --- |
| `config.py` | Reads `.env`: Ollama URL/models, LanceDB path, chunk sizes, max prompt sizes, optional Langfuse keys. |
| `llm.py` | Builds Ollama chat and embedding clients. Checks the server is up and models exist. |
| `state.py` | `AuditState` definition. `reviews` merges dicts from parallel agents; `errors` appends. |
| `tracing.py` | Optional Langfuse `CallbackHandler`; soft-fails when keys or host are missing. |
| `graph.py` | Nodes and edges. Compiles with `MemorySaver` and `interrupt_before=["apply_fixes_node"]`. Exposes `run_until_approval` / `resume_with_approval` for the UI. |

Pipeline order changes go in **`graph.py`**.

### `src/rag/`: codebase search

| File | What it does |
| --- | --- |
| `indexer.py` | Walks `*.py`, splits with LangChain `Language.PYTHON`, embeds chunks, writes LanceDB. |
| `retriever.py` | Embeds the query (usually the diff), returns top-k similar chunks for the prompts. |

Indexing is manual via the UI button. Auditing assumes an index already exists.

### `src/agents/`: the three reviewers

| File | What it does |
| --- | --- |
| `prompts.py` | System prompts plus packing of diff + RAG into the user message. Asks for JSON only. |
| `security.py` / `quality.py` / `testing.py` | Same pattern: get LLM → send prompts → parse JSON. |
| `json_utils.py` | Tolerant JSON parse: strips fences, safe fallback. |

There is no separate agent runtime. Each file is a prompt plus one Ollama call.

The **consolidator** lives in `graph.py` and merges the three JSON reports with a fourth LLM call.

### `src/utils/`: Git

| File | What it does |
| --- | --- |
| `git_tools.py` | Prefer staged diff → unstaged → a few untracked `.py` → branch vs main/master. Apply patches via `git apply`. Caps huge untracked dumps so prompts fit. |

GitPython / git CLI wrapping only.

## Where to change what

| You want to… | Touch… |
| --- | --- |
| UI labels / buttons / flow display | `app.py` |
| Model name, context size, prompt caps | `.env` / `config.py` |
| Langfuse host / keys / default toggle | `.env` / `config.py` / `core/tracing.py` |
| Review criteria or JSON shape | `agents/prompts.py` |
| Add a fourth reviewer | new file under `agents/` + wire it in `graph.py` |
| Index other languages | `rag/indexer.py` and prompts |
| Diff selection / apply behavior | `utils/git_tools.py` |
| Pause / resume / node order | `graph.py` |

## Runtime dependencies

- **Ollama** must be running (`ollama serve`).
- Models from `.env` must be pulled (`make pull-models`).
- Without GPU, Ollama still runs on CPU, just slower.

## Optional: Langfuse tracing (dev)

Langfuse is not bundled. Run it yourself, then point this app at it.

### 1. Start Langfuse

Use the official Compose stack (v3+; older v2 breaks current Python SDKs):

```bash
git clone --depth=1 https://github.com/langfuse/langfuse.git
cd langfuse
docker compose up -d
```

UI: [http://localhost:3000](http://localhost:3000). Create a project and copy Public / Secret API keys.

### 2. Configure this app

In `.env` (see `.env.example`):

```bash
LANGFUSE_ENABLED=false          # Streamlit checkbox default only
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
LANGFUSE_BASE_URL=http://localhost:3000
LANGFUSE_TRACING_ENVIRONMENT=development
```

Reinstall deps if needed: `pip install -r requirements.txt` (includes `langfuse`).

### 3. Use the Streamlit toggle

Sidebar → **Trace with Langfuse**. When on, **Run audit** and the matching **Approve** / **Reject** send LangGraph + Ollama spans to Langfuse. The choice sticks for that audit’s HITL step (`session_id` = LangGraph `thread_id`).

If keys are missing or Langfuse is down, the audit still runs; Streamlit shows a warning and skips tracing.

Code entry points: `src/core/tracing.py`, wired from `run_until_approval` / `resume_with_approval` in `graph.py`.

## Tests

```bash
make test
```
