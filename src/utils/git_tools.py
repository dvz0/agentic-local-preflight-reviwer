"""GitPython helpers for staged / working-tree diffs."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from git import InvalidGitRepositoryError, Repo
from git.exc import BadName, GitCommandError


class GitToolsError(RuntimeError):
    pass


def open_repo(repo_path: str | Path) -> Repo:
    root = Path(repo_path).expanduser().resolve()
    try:
        return Repo(root, search_parent_directories=True)
    except InvalidGitRepositoryError as exc:
        raise GitToolsError(f"Not a git repository: {root}") from exc


def get_diff_and_files(
    repo_path: str | Path,
    *,
    prefer_staged: bool = True,
) -> tuple[str, list[str]]:
    """
    Return (unified_diff, modified_file_paths).

    Preference order:
    1. Staged changes (index vs HEAD) if prefer_staged and anything is staged
    2. Unstaged working tree vs index
    3. Diff of current branch vs main/master (if available)
    """
    repo = open_repo(repo_path)
    root = Path(repo.working_tree_dir or repo_path)

    staged = repo.git.diff("--cached", unified=3)
    if prefer_staged and staged.strip():
        files = _diff_name_only(repo, cached=True)
        return staged, _abs_paths(root, files)

    unstaged = repo.git.diff(unified=3)
    # Cap untracked files so a brand-new repo does not dump every .py into the prompt.
    untracked = [p for p in repo.untracked_files if p.endswith(".py")][:8]
    if unstaged.strip() or untracked:
        files = _diff_name_only(repo, cached=False) if unstaged.strip() else []
        extra = ""
        for rel in untracked:
            path = root / rel
            try:
                content = path.read_text(encoding="utf-8")
            except OSError:
                continue
            # Truncate huge new files in the synthetic diff.
            if len(content) > 4000:
                content = content[:4000] + "\n# ... truncated ...\n"
            extra += _as_added_diff(rel, content)
            files.append(rel)
        return (unstaged or "") + extra, _abs_paths(root, files)

    base = _default_base_ref(repo)
    if base:
        branch_diff = repo.git.diff(f"{base}...HEAD", unified=3)
        if branch_diff.strip():
            files = repo.git.diff(f"{base}...HEAD", name_only=True).splitlines()
            return branch_diff, _abs_paths(root, files)

    return "", []


def _diff_name_only(repo: Repo, *, cached: bool) -> list[str]:
    args = ["--cached", "--name-only"] if cached else ["--name-only"]
    out = repo.git.diff(*args)
    return [line for line in out.splitlines() if line.strip()]


def _abs_paths(root: Path, rels: list[str]) -> list[str]:
    seen: list[str] = []
    for rel in rels:
        p = str((root / rel).resolve())
        if p not in seen:
            seen.append(p)
    return seen


def _commit_exists(repo: Repo, name: str) -> bool:
    try:
        repo.commit(name)
    except (BadName, GitCommandError, ValueError):
        return False
    return True


def _default_base_ref(repo: Repo) -> str | None:
    for name in ("origin/main", "main", "origin/master", "master"):
        if _commit_exists(repo, name):
            return name
    return None


def _as_added_diff(rel_path: str, content: str) -> str:
    lines = content.splitlines()
    body = "\n".join(f"+{line}" for line in lines)
    return (
        f"\ndiff --git a/{rel_path} b/{rel_path}\n"
        f"new file mode 100644\n"
        f"--- /dev/null\n"
        f"+++ b/{rel_path}\n"
        f"@@ -0,0 +1,{len(lines)} @@\n"
        f"{body}\n"
    )


def apply_unified_diff(repo_path: str | Path, unified_diff: str) -> None:
    """Apply a unified diff to the working tree via ``git apply``."""
    if not unified_diff.strip():
        return

    repo = open_repo(repo_path)
    fd, patch_path = tempfile.mkstemp(suffix=".patch", text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(unified_diff)
            if not unified_diff.endswith("\n"):
                handle.write("\n")
        repo.git.apply("--whitespace=nowarn", patch_path)
    finally:
        try:
            os.unlink(patch_path)
        except OSError:
            pass
