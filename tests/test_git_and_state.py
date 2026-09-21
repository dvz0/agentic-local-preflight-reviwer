"""Unit tests that do not require Ollama or GPU."""

from __future__ import annotations

from pathlib import Path

from git import Repo

from src.agents.json_utils import parse_llm_json, safe_agent_payload
from src.core.state import _merge_reviews
from src.utils.git_tools import (
    apply_unified_diff,
    check_unified_diff,
    get_diff_and_files,
    validate_proposed_fixes,
)


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


def _init_py_repo(tmp_path: Path, body: str) -> None:
    repo = Repo.init(tmp_path)
    (tmp_path / "mod.py").write_text(body, encoding="utf-8")
    repo.index.add(["mod.py"])
    repo.index.commit("init")


def test_check_and_apply_valid_patch(tmp_path: Path):
    _init_py_repo(tmp_path, "a = 1\nb = 2\n")
    patch = (
        "diff --git a/mod.py b/mod.py\n"
        "--- a/mod.py\n"
        "+++ b/mod.py\n"
        "@@ -1,2 +1,2 @@\n"
        " a = 1\n"
        "-b = 2\n"
        "+b = 3\n"
    )
    check_unified_diff(tmp_path, patch)
    apply_unified_diff(tmp_path, patch)
    assert (tmp_path / "mod.py").read_text(encoding="utf-8") == "a = 1\nb = 3\n"


def test_validate_drops_corrupt_and_conflicting(tmp_path: Path):
    _init_py_repo(
        tmp_path,
        "line1\n"
        "secret = 'abc'\n"
        "line3\n",
    )
    good = {
        "id": "FIX-001",
        "file": "mod.py",
        "unified_diff": (
            "diff --git a/mod.py b/mod.py\n"
            "--- a/mod.py\n"
            "+++ b/mod.py\n"
            "@@ -1,3 +1,2 @@\n"
            " line1\n"
            "-secret = 'abc'\n"
            " line3\n"
        ),
    }
    corrupt = {
        "id": "FIX-002",
        "file": "mod.py",
        "unified_diff": (
            "diff --git a/mod.py b/mod.py\n"
            "--- a/mod.py\n"
            "+++ b/mod.py\n"
            "@@ -1,2 +1 @@\n"
            "-not a real hunk\n"
        ),
    }
    conflict = {
        "id": "FIX-003",
        "file": "mod.py",
        "unified_diff": (
            "diff --git a/mod.py b/mod.py\n"
            "--- a/mod.py\n"
            "+++ b/mod.py\n"
            "@@ -1,3 +1,3 @@\n"
            " line1\n"
            "-secret = 'abc'\n"
            "+secret = os.getenv('S')\n"
            " line3\n"
        ),
    }
    valid, rejected = validate_proposed_fixes(tmp_path, [good, corrupt, conflict])
    assert [f["id"] for f in valid] == ["FIX-001"]
    assert any("FIX-002" in r for r in rejected)
    assert any("FIX-003" in r for r in rejected)


def test_validate_repairs_wrong_context(tmp_path: Path):
    _init_py_repo(
        tmp_path,
        "AWS_REGION = 'us-east-1'\n"
        "# just a comment2\n"
        "secret = 'abc'\n",
    )
    bad_context = {
        "id": "FIX-001",
        "file": "mod.py",
        "unified_diff": (
            "diff --git a/mod.py b/mod.py\n"
            "--- a/mod.py\n"
            "+++ b/mod.py\n"
            "@@ -1,3 +1,2 @@\n"
            " AWS_REGION = 'us-east-1'\n"
            "-secret = 'abc'\n"
            "\n"
        ),
    }
    valid, rejected = validate_proposed_fixes(tmp_path, [bad_context])
    assert [f["id"] for f in valid] == ["FIX-001"]
    assert rejected == []
    apply_unified_diff(tmp_path, valid[0]["unified_diff"])
    assert (tmp_path / "mod.py").read_text(encoding="utf-8") == (
        "AWS_REGION = 'us-east-1'\n# just a comment2\n"
    )


def test_validate_old_string_new_string(tmp_path: Path):
    _init_py_repo(
        tmp_path,
        "AWS_REGION = 'us-east-1'\n"
        "# just a comment2\n"
        "temporal_prod_password_tests= \"abc123.\"\n",
    )
    fix = {
        "id": "FIX-001",
        "file": "mod.py",
        "old_string": 'temporal_prod_password_tests= "abc123."\n',
        "new_string": "",
    }
    valid, rejected = validate_proposed_fixes(tmp_path, [fix])
    assert rejected == []
    assert len(valid) == 1
    apply_unified_diff(tmp_path, valid[0]["unified_diff"])
    text = (tmp_path / "mod.py").read_text(encoding="utf-8")
    assert "abc123" not in text
    assert "# just a comment2" in text
