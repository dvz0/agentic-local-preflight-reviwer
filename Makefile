# Prefer 3.12/3.11 — Streamlit/wheels often fail on 3.14
PYTHON ?= $(shell command -v python3.12 || command -v python3.11 || command -v python3)
VENV ?= .venv
BIN := $(VENV)/bin
PIP := $(BIN)/pip
STREAMLIT := $(BIN)/streamlit
PYTEST := $(BIN)/pytest

.PHONY: setup pull-models run index test lint clean

setup:
	@echo "Using: $$($(PYTHON) --version 2>&1)"
	@case "$$($(PYTHON) --version 2>&1)" in \
	  *3.14*) echo "WARNING: Python 3.14 may lack wheels (streamlit/lancedb). Prefer 3.12 or 3.11.";; \
	esac
	$(PYTHON) -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt
	@test -f .env || cp .env.example .env
	@mkdir -p data
	@echo "OK: venv ready. Next: make pull-models && make run"

pull-models:
	@command -v ollama >/dev/null || (echo "ERROR: Ollama is not installed. https://ollama.com" && exit 1)
	ollama pull $${OLLAMA_CHAT_MODEL:-qwen2.5-coder:7b}
	ollama pull $${OLLAMA_EMBED_MODEL:-nomic-embed-text}

run:
	$(STREAMLIT) run app.py --server.headless true --server.fileWatcherType none

index:
	$(BIN)/python -c "from src.rag.indexer import index_repository; import os; from dotenv import load_dotenv; load_dotenv(); p=os.getenv('DEFAULT_REPO_PATH') or '.'; print(index_repository(p))"

test:
	PYTHONPATH=. $(PYTEST) -q

lint:
	$(BIN)/ruff check src app.py

clean:
	rm -rf $(VENV) .pytest_cache .ruff_cache
	find . -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true
