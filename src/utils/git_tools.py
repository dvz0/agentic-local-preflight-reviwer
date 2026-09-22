"""GitPython helpers for staged / working-tree diffs."""

from __future__ import annotations

import difflib
import os
import tempfile
from contextlib import suppress
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
    # Cap untracked .py so a fresh repo does not flood the prompt.
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


_APPLY_FLAGS = ("--whitespace=nowarn",)


def _patch_targets_use_crlf(repo_path: Path, unified_diff: str) -> bool:
    for rel in _paths_touched_by_fixes([{"unified_diff": unified_diff}]):
        path = repo_path / rel
        if path.is_file():
            data = path.read_bytes()
            if b"\r\n" in data:
                return True
    return False


def _normalize_patch_newlines(unified_diff: str, *, use_crlf: bool) -> str:
    text = unified_diff.replace("\r\n", "\n").replace("\r", "\n")
    if use_crlf:
        return text.replace("\n", "\r\n")
    return text


def _write_patch_file(repo_path: str | Path, unified_diff: str) -> str:
    root = Path(open_repo(repo_path).working_tree_dir or repo_path)
    use_crlf = _patch_targets_use_crlf(root, unified_diff)
    normalized = _normalize_patch_newlines(unified_diff, use_crlf=use_crlf)
    fd, patch_path = tempfile.mkstemp(suffix=".patch", text=False)
    try:
        data = normalized.encode("utf-8")
        eol = b"\r\n" if use_crlf else b"\n"
        if not data.endswith(b"\n"):
            data += eol
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        raise
    return patch_path


def check_unified_diff(repo_path: str | Path, unified_diff: str) -> None:
    """Dry-run ``git apply --check``. Raises ``GitCommandError`` if invalid."""
    if not unified_diff.strip():
        raise GitToolsError("Empty unified diff")

    repo = open_repo(repo_path)
    patch_path = _write_patch_file(repo_path, unified_diff)
    try:
        repo.git.apply("--check", *_APPLY_FLAGS, patch_path)
    finally:
        try:
            os.unlink(patch_path)
        except OSError:
            pass


def apply_unified_diff(repo_path: str | Path, unified_diff: str) -> None:
    """Apply a unified diff to the working tree via ``git apply``."""
    if not unified_diff.strip():
        return

    repo = open_repo(repo_path)
    patch_path = _write_patch_file(repo_path, unified_diff)
    try:
        repo.git.apply(*_APPLY_FLAGS, patch_path)
    finally:
        try:
            os.unlink(patch_path)
        except OSError:
            pass


def validate_proposed_fixes(
    repo_path: str | Path,
    fixes: list[dict],
) -> tuple[list[dict], list[str]]:
    """
    Normalize/repair fixes against the working tree, keep those that pass
    ``git apply --check``, then drop conflicts with earlier kept patches.
    """
    if not fixes:
        return [], []

    valid: list[dict] = []
    rejected: list[str] = []
    alone_ok: list[dict] = []

    for fix in fixes:
        label = fix.get("id") or fix.get("file") or "?"
        try:
            normalized = normalize_fix(repo_path, fix)
            check_unified_diff(repo_path, normalized["unified_diff"])
        except Exception as exc:  # noqa: BLE001
            rejected.append(f"{label}: {exc}")
            continue
        alone_ok.append(normalized)

    if not alone_ok:
        return [], rejected

    root = Path(open_repo(repo_path).working_tree_dir or repo_path)
    with tempfile.TemporaryDirectory(prefix="pr_auditor_patch_") as tmp:
        mirror = Path(tmp) / "tree"
        _mirror_working_tree(root, mirror, alone_ok)
        mirror_repo = Repo.init(mirror)
        mirror_repo.git.add(A=True)
        if mirror_repo.is_dirty(untracked_files=True) or not mirror_repo.head.is_valid():
            with suppress(GitCommandError, ValueError):
                mirror_repo.index.commit("mirror")

        for fix in alone_ok:
            diff = fix.get("unified_diff") or ""
            label = fix.get("id") or fix.get("file") or "?"
            try:
                check_unified_diff(mirror, diff)
                apply_unified_diff(mirror, diff)
            except Exception as exc:  # noqa: BLE001
                rejected.append(f"{label}: conflicts with earlier patch ({exc})")
                continue
            valid.append(fix)

    return valid, rejected


def normalize_fix(repo_path: str | Path, fix: dict) -> dict:
    """
    Return a copy of ``fix`` with an applicable ``unified_diff``.

    Preference:
    1. ``old_string`` / ``new_string`` against the working tree file
    2. existing ``unified_diff`` if ``git apply --check`` passes
    3. repair from +/- lines in a failing unified_diff
    """
    out = dict(fix)
    rel = _resolve_fix_relpath(fix)
    if rel:
        out["file"] = rel

    old_s = fix.get("old_string")
    new_s = fix.get("new_string")
    if rel and isinstance(old_s, str) and new_s is not None:
        out["unified_diff"] = build_unified_diff_from_replacement(
            repo_path, rel, old_s, str(new_s)
        )
        return out

    diff = (fix.get("unified_diff") or "").strip()
    if not diff:
        raise GitToolsError("empty unified_diff and no old_string/new_string")

    try:
        check_unified_diff(repo_path, diff)
        out["unified_diff"] = diff
        return out
    except (GitCommandError, GitToolsError):
        repaired = repair_unified_diff(repo_path, diff, file_hint=rel)
        out["unified_diff"] = repaired
        out["_repaired"] = True
        return out


def build_unified_diff_from_replacement(
    repo_path: str | Path,
    rel_path: str,
    old_string: str,
    new_string: str,
) -> str:
    root = Path(open_repo(repo_path).working_tree_dir or repo_path)
    path = root / rel_path
    if not path.is_file():
        raise GitToolsError(f"file not found for replacement: {rel_path}")

    before = path.read_text(encoding="utf-8")
    after = replace_unique(before, old_string, new_string)
    return make_unified_diff(rel_path, before, after)


def repair_unified_diff(
    repo_path: str | Path,
    unified_diff: str,
    *,
    file_hint: str | None = None,
) -> str:
    """Rebuild an applicable patch from +/- lines matched in the working tree."""
    rel = file_hint or _first_path_from_diff(unified_diff)
    if not rel:
        raise GitToolsError("cannot repair patch: no target file path")

    old_block, new_block = _extract_change_blocks(unified_diff)
    if not old_block and not new_block:
        raise GitToolsError("cannot repair patch: no +/- change lines")

    return build_unified_diff_from_replacement(
        repo_path, rel, old_block, new_block
    )


def replace_unique(content: str, old: str, new: str) -> str:
    """Replace ``old`` with ``new`` exactly once (newline-style tolerant)."""
    nl = "\r\n" if "\r\n" in content else "\n"
    old_n = _to_newline(old, nl)
    new_n = _to_newline(new, nl)

    candidates = [old_n]
    if old_n.endswith(nl):
        candidates.append(old_n[: -len(nl)])
    elif old_n:
        candidates.append(old_n + nl)

    for candidate in candidates:
        if not candidate:
            continue
        count = content.count(candidate)
        if count == 1:
            return content.replace(candidate, new_n, 1)
        if count > 1:
            raise GitToolsError(
                f"old_string matches {count} times; need a unique snippet"
            )

    raise GitToolsError("old_string not found uniquely in working tree file")


def make_unified_diff(rel_path: str, before: str, after: str) -> str:
    before_lines = before.splitlines(keepends=True)
    after_lines = after.splitlines(keepends=True)
    hunks = list(
        difflib.unified_diff(
            before_lines,
            after_lines,
            fromfile=f"a/{rel_path}",
            tofile=f"b/{rel_path}",
            n=3,
        )
    )
    if not hunks:
        raise GitToolsError("replacement produced no changes")
    body = "".join(hunks)
    if not body.startswith("diff --git"):
        body = f"diff --git a/{rel_path} b/{rel_path}\n{body}"
    return body


def file_excerpts_for_prompt(
    repo_path: str | Path,
    rel_paths: list[str],
    *,
    max_chars: int = 4000,
) -> str:
    """Read current working-tree files for consolidator context."""
    root = Path(open_repo(repo_path).working_tree_dir or repo_path)
    parts: list[str] = []
    used = 0
    for rel in rel_paths:
        rel = rel.replace("\\", "/").lstrip("./")
        path = root / rel
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        chunk = f"### {rel}\n```\n{text}\n```\n"
        if used + len(chunk) > max_chars and parts:
            break
        parts.append(chunk)
        used += len(chunk)
    return "\n".join(parts)


def paths_from_git_diff(git_diff: str) -> list[str]:
    found: list[str] = []
    for line in git_diff.splitlines():
        if line.startswith("diff --git "):
            parts = line.split()
            if len(parts) >= 4:
                rel = parts[3].removeprefix("b/")
                if rel not in found:
                    found.append(rel)
        elif line.startswith("+++ b/"):
            rel = line[6:].strip()
            if rel not in found and rel != "/dev/null":
                found.append(rel)
    return found


def _to_newline(text: str, nl: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", nl)


def _resolve_fix_relpath(fix: dict) -> str | None:
    file_hint = (fix.get("file") or "").strip().replace("\\", "/")
    if file_hint:
        return file_hint.lstrip("./")
    return _first_path_from_diff(fix.get("unified_diff") or "")


def _first_path_from_diff(unified_diff: str) -> str | None:
    paths = _paths_touched_by_fixes([{"unified_diff": unified_diff}])
    return min(paths) if paths else None


def _extract_change_blocks(unified_diff: str) -> tuple[str, str]:
    """Collect removed (-) and added (+) body lines from a unified diff."""
    removed: list[str] = []
    added: list[str] = []
    for line in unified_diff.splitlines():
        if line.startswith(("--- ", "+++ ")):
            continue
        if line.startswith("-"):
            removed.append(line[1:])
        elif line.startswith("+"):
            added.append(line[1:])
    old_block = "\n".join(removed)
    new_block = "\n".join(added)
    if removed:
        old_block += "\n"
    if added:
        new_block += "\n"
    return old_block, new_block


def _paths_touched_by_fixes(fixes: list[dict]) -> set[str]:
    paths: set[str] = set()
    for fix in fixes:
        file_hint = (fix.get("file") or "").strip()
        if file_hint:
            paths.add(file_hint.replace("\\", "/").lstrip("./"))
        diff = fix.get("unified_diff") or ""
        for line in diff.splitlines():
            if line.startswith("diff --git "):
                parts = line.split()
                if len(parts) >= 4:
                    paths.add(parts[2].removeprefix("a/"))
            elif line.startswith(("+++ b/", "--- a/")):
                paths.add(line[6:].strip())
    paths.discard("/dev/null")
    paths.discard("dev/null")
    return {p for p in paths if p and p != "/dev/null"}


def _mirror_working_tree(root: Path, dest: Path, fixes: list[dict]) -> None:
    """Copy files touched by patches (plus parents) into dest for dry-run apply."""
    dest.mkdir(parents=True, exist_ok=True)
    touched = _paths_touched_by_fixes(fixes)
    if not touched:
        return
    for rel in touched:
        src = root / rel
        if not src.is_file():
            continue
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(src.read_bytes())
