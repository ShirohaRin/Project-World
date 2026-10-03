from __future__ import annotations

from datetime import datetime
from typing import Any

from .evidence_math import (
    compute_evidence_snapshot,
    derive_status,
    effective_disputation,
    effective_reinforcement,
    evidence_score,
    initial_reinforcement_from_importance,
    maybe_mark_sub_zero,
)


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _age_days(value: Any, now: datetime) -> float:
    if not value:
        return 0.0
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return 0.0
    return max(0.0, (now - parsed).total_seconds() / 86400)


def normalize_evidence(entry: dict[str, Any]) -> dict[str, Any]:
    """将 NEKO 证据字段整理成可持久化的稳定形状。"""
    normalized = dict(entry)
    normalized["reinforcement"] = _as_float(entry.get("reinforcement"))
    normalized["disputation"] = max(0.0, _as_float(entry.get("disputation")))
    normalized["user_fact_reinforce_count"] = max(0, int(entry.get("user_fact_reinforce_count", 0) or 0))
    normalized["sub_zero_days"] = max(0, int(entry.get("sub_zero_days", 0) or 0))
    return normalized


__all__ = [
    "compute_evidence_snapshot",
    "derive_status",
    "effective_disputation",
    "effective_reinforcement",
    "evidence_score",
    "initial_reinforcement_from_importance",
    "maybe_mark_sub_zero",
    "normalize_evidence",
]
