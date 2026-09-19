"""Security review agent."""

from __future__ import annotations

from typing import Any

from src.agents.json_utils import safe_agent_payload
from src.agents.prompts import SECURITY_SYSTEM, user_review_payload
from src.core.llm import get_chat_llm


def run_security_agent(git_diff: str, rag_context: list[str]) -> dict[str, Any]:
    llm = get_chat_llm(json_mode=True)
    messages = [
        {"role": "system", "content": SECURITY_SYSTEM},
        {"role": "user", "content": user_review_payload(git_diff, rag_context)},
    ]
    response = llm.invoke(messages)
    content = response.content if hasattr(response, "content") else str(response)
    return safe_agent_payload("security", content)
