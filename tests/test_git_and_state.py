"""Unit tests that do not require Ollama or GPU."""

from __future__ import annotations

from pathlib import Path

from git import Repo

from src.agents.json_utils import parse_llm_json, safe_agent_payload
from src.core.state import _merge_reviews
from src.utils.git_tools import get_diff_and_files


def test_merge_reviews():
    left = {"security": {"findings": [1]}}
    right = {"quality": {"findings": [2]}}
    assert _merge_reviews(left, right) == {
        "security": {"findings": [1]},
        "quality": {"findings": [2]},
    }


def test_parse_llm_json_with_fence():
    raw = '```json\n{"agent": "security", "findings": []}\n```'
    assert parse_llm_json(raw)["agent"] == "security"


def test_safe_agent_payload_bad_json():
    data = safe_agent_payload("quality", "not-json")
    assert data["agent"] == "quality"
    assert data["findings"] == []
    assert "raw" in data


def test_get_diff_unstaged(tmp_path: Path):
    repo = Repo.init(tmp_path)
    (tmp_path / "hello.py").write_text("print('hi')\n", encoding="utf-8")
    repo.index.add(["hello.py"])
    repo.index.commit("init")
    (tmp_path / "hello.py").write_text("print('bye')\n", encoding="utf-8")

    diff, files = get_diff_and_files(tmp_path, prefer_staged=True)
    assert "hello.py" in diff or any("hello.py" in f for f in files)
    assert diff.strip()


def test_get_diff_untracked_only(tmp_path: Path):
    repo = Repo.init(tmp_path)
    (tmp_path / "seed.py").write_text("x = 1\n", encoding="utf-8")
    repo.index.add(["seed.py"])
    repo.index.commit("init")
    (tmp_path / "new_mod.py").write_text("y = 2\n", encoding="utf-8")

    diff, files = get_diff_and_files(tmp_path)
    assert "new_mod.py" in diff
    assert any(f.endswith("new_mod.py") for f in files)
