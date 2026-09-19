"""Helpers for parsing JSON from Ollama responses."""

from __future__ import annotations

import json
from typing import Any


def parse_llm_json(raw: str) -> dict[str, Any]:
    """Parse JSON from model output, tolerating optional markdown fences."""
    text = (raw or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return json.loads(text)


def safe_agent_payload(agent: str, content: str) -> dict[str, Any]:
    try:
        data = parse_llm_json(content)
    except (json.JSONDecodeError, TypeError):
        return {
            "agent": agent,
            "summary": "Could not parse model response",
            "findings": [],
            "proposed_patches": [],
            "raw": content,
        }
    if not isinstance(data, dict):
        return {
            "agent": agent,
            "summary": "Unexpected JSON response",
            "findings": [],
            "proposed_patches": [],
            "raw": content,
        }
    data["agent"] = agent
    data.setdefault("findings", [])
    data.setdefault("proposed_patches", [])
    return data
