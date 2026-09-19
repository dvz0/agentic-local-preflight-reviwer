"""LangGraph shared state."""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict


def _merge_reviews(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    merged = dict(left or {})
    merged.update(right or {})
    return merged


class AuditState(TypedDict, total=False):
    repo_path: str
    git_diff: str
    modified_files: list[str]
    rag_context: list[str]
    reviews: Annotated[dict[str, Any], _merge_reviews]
    consolidated_report: str
    proposed_fixes: list[dict[str, Any]]
    user_approved: bool
    apply_result: str
    errors: Annotated[list[str], operator.add]
