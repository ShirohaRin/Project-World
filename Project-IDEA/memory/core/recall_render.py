from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping, Sequence

RECALL_RENDER_ENTRY_MAX_TOKENS = 60
RECALL_RENDER_LINE_OVERHEAD_TOKENS = 12
RECALL_RENDER_TOTAL_MAX_TOKENS = 400


def _time_suffix(entry: Mapping[str, Any]) -> str:
    anchor = None
    for candidate in (entry.get("event_end_at"), entry.get("event_start_at"), entry.get("created_at")):
        if not isinstance(candidate, str):
            continue
        try:
            anchor = datetime.fromisoformat(candidate)
            break
        except ValueError:
            continue
    if anchor is None:
        return ""
    return f"  ({anchor.date().isoformat()})"


def render_recall_block(
    results: Sequence[Mapping[str, Any]] | None,
    *,
    header_template: str | None = None,
    entry_max_tokens: int = RECALL_RENDER_ENTRY_MAX_TOKENS,
    line_overhead_tokens: int = RECALL_RENDER_LINE_OVERHEAD_TOKENS,
    total_max_tokens: int = RECALL_RENDER_TOTAL_MAX_TOKENS,
) -> str:
    """把召回结果渲染成模型可见的编号块，带硬 token 上限。"""
    entries = [item for item in (results or []) if isinstance(item, Mapping)]
    lines: list[str] = []
    for item in entries:
        text = str(item.get("text") or item.get("content") or "").strip()
        if not text:
            continue
        tag = item.get("layer") or item.get("tier") or "fact"
        lines.append(f"{len(lines) + 1}. [{tag}] {text[:entry_max_tokens]}{_time_suffix(item)}")

    if header_template is not None:
        total_max_tokens -= len(header_template.format(n=len(lines))) + 1

    budget = max(0, total_max_tokens)
    kept: list[str] = []
    used = 0
    for line in lines:
        line = line[: entry_max_tokens + line_overhead_tokens]
        cost = len(line) + 1
        if used + cost > budget and kept:
            break
        kept.append(line)
        used += cost
    if header_template is not None:
        kept.insert(0, header_template.format(n=len(kept)))
    return "\n".join(kept)
