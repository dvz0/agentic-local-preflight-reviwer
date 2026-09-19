# Developer guide

This note is for developers who know Python/Git but are not deep into AI.
It explains the folders and how `src/` hangs together.

For setup and usage, see [../README.md](../README.md). For a UI smoke test, see [change_example.md](change_example.md).

## Mental model

Think of the app as a **pipeline**, not a chatbot:

1. Read the local Git diff (what changed).
2. Look up related code in the rest of the repo (search, not “memory”).
3. Ask a local LLM three times with different instructions (security / quality / tests).
4. Merge those answers into one report + optional patches.
5. **Stop** and wait for a human.
6. Only then optionally apply patches to disk.

The LLM runs in **Ollama** on your machine. This repo is the glue: Git + search + orchestration + UI.

Useful terms in one line:

| Term | Plain meaning |
| --- | --- |
| **LLM** | Model that turns a prompt into text (here: code review JSON). |
| **Ollama** | Local server that loads models and answers HTTP calls. |
| **Embedding** | Turns text into a vector so “similar code” can be found. |
| **RAG** | “Search the repo first, then put those snippets in the prompt.” |
| **LanceDB** | On-disk vector store (no separate DB server). |
| **LangGraph** | Library to define a graph of steps (nodes) that pass a shared state. |
| **HITL** | Human-in-the-loop: pause before doing something irreversible. |
| **Agent (here)** | Not autonomous software — a **prompt + LLM call** with a specialty. |

---

## What’s in each top-level folder / file

```text
local_pr_auditor/
├── app.py              # Streamlit UI (buttons call into src/)
├── src/                # All business logic (see below)
├── data/               # LanceDB files after “Index Repo” (gitignored)
├── tests/              # Unit tests that do NOT need Ollama/GPU
├── docs/               # Dev docs + screenshots
│   ├── change_example.md
│   ├── dev.md          # this file
│   └── examples/       # UI screenshots
├── .streamlit/         # Streamlit config (file watcher off on external disks)
├── .env / .env.example # Models, paths, context size limits
├── Makefile            # setup / pull-models / run / test
├── requirements.txt
└── README.md
```

| Path | Role |
| --- | --- |
| `app.py` | Thin UI. Index button → `rag.indexer`. Audit button → `core.graph`. Approve/Reject → resume graph. |
| `data/` | Created at runtime. Do not commit. Safe to delete and re-index. |
| `tests/` | Git/state helpers, JSON parsing — no live LLM. |
| `.env` | Local overrides (`OLLAMA_*`, `MAX_DIFF_CHARS`, etc.). |

---

## How `src/` works

```text
src/
├── core/       # Config, Ollama clients, shared state, LangGraph
├── rag/        # Index repo + similarity search
├── agents/     # Prompts + three “reviewer” LLM calls
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

Everything the graph needs lives in **`AuditState`** (`core/state.py`): a typed dict that nodes update (diff, RAG snippets, reviews, report, `user_approved`, errors).

---

### `src/core/` — orchestration

| File | What it does |
| --- | --- |
| `config.py` | Reads `.env`: Ollama URL/models, LanceDB path, chunk sizes, max prompt sizes. |
| `llm.py` | Builds Ollama chat + embedding clients. Checks the server is up and models exist. Clear errors if not. |
| `state.py` | `AuditState` definition. `reviews` merges dicts from parallel agents; `errors` appends. |
| `graph.py` | Defines nodes and edges. Compiles with `MemorySaver` and `interrupt_before=["apply_fixes_node"]`. Exposes `run_until_approval` / `resume_with_approval` for the UI. |

If you change the pipeline order, you almost always edit **`graph.py`**.

---

### `src/rag/` — “search the codebase”

| File | What it does |
| --- | --- |
| `indexer.py` | Walks `*.py`, splits with LangChain `Language.PYTHON` (tries not to cut functions mid-body), embeds chunks, writes LanceDB. |
| `retriever.py` | Embeds the query (usually the diff), returns top-k similar chunks as text for the prompts. |

Indexing is **manual** (UI button). Auditing assumes an index already exists.

---

### `src/agents/` — the three reviewers

| File | What it does |
| --- | --- |
| `prompts.py` | System prompts (English) + how to pack diff + RAG into the user message. Asks for **JSON only**. |
| `security.py` / `quality.py` / `testing.py` | Same pattern: get LLM → send prompts → parse JSON. |
| `json_utils.py` | Tolerant JSON parse (strips \`\`\` fences, safe fallback). |

There is no separate “agent runtime.” Each file is: **prompt + one Ollama call**.

The **consolidator** lives in `graph.py` (fourth LLM call) and merges the three JSON reports.

---

### `src/utils/` — Git

| File | What it does |
| --- | --- |
| `git_tools.py` | Prefer staged diff → unstaged → a few untracked `.py` → branch vs main/master. Apply patches via `git apply`. Caps huge untracked dumps so prompts fit. |

Classic GitPython / git CLI wrapping — no AI here.

---

## Where to change what

| You want to… | Touch… |
| --- | --- |
| UI labels / buttons / flow display | `app.py` |
| Model name, context size, prompt caps | `.env` / `config.py` |
| Review criteria or JSON shape | `agents/prompts.py` |
| Add a fourth reviewer | new file under `agents/` + wire it in `graph.py` |
| Index other languages | `rag/indexer.py` (+ prompts) |
| Diff selection / apply behavior | `utils/git_tools.py` |
| Pause / resume / node order | `graph.py` |

---

## Runtime dependencies

- **Ollama** must be running (`ollama serve`).
- Models from `.env` must be pulled (`make pull-models`).
- Without GPU, Ollama still runs on **CPU** (slower). This app does not special-case that.

---

## Tests

```bash
make test
```
