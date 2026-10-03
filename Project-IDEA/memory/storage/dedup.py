from __future__ import annotations

from collections import Counter
from typing import Any

from Memory.core.recall import fold_script, tokenize

FACT_DEDUP_COSINE_THRESHOLD = 0.85
FACT_NEAR_DUP_ARBITRATE_OVERLAP = 0.25
FACT_DEDUP_PAIRS_PER_NEW = 3


def normalized_fact_text(text: str) -> str:
    return " ".join(fold_script(str(text or "")).lower().split())


def exact_fact_key(text: str) -> str:
    return normalized_fact_text(text)


def token_overlap(left: str, right: str) -> float:
    left_tokens = set(tokenize(left))
    right_tokens = set(tokenize(right))
    if not left_tokens or not right_tokens:
        return 0.0
    return 2 * len(left_tokens & right_tokens) / (len(left_tokens) + len(right_tokens))


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    numerator = sum(a * b for a, b in zip(left, right))
    left_norm = sum(a * a for a in left) ** 0.5
    right_norm = sum(b * b for b in right) ** 0.5
    if not left_norm or not right_norm:
        return 0.0
    return numerator / (left_norm * right_norm)


def find_exact_duplicate(fact: dict[str, Any], existing: list[dict[str, Any]]) -> dict[str, Any] | None:
    key = exact_fact_key(str(fact.get("text", "")))
    if not key:
        return None
    return next((item for item in existing if exact_fact_key(str(item.get("text", ""))) == key), None)


def near_duplicate_candidates(
    fact: dict[str, Any], existing: list[dict[str, Any]], *, limit: int = FACT_DEDUP_PAIRS_PER_NEW,
) -> list[dict[str, Any]]:
    scored = []
    text = str(fact.get("text", ""))
    for item in existing:
        if item.get("id") == fact.get("id"):
            continue
        overlap = token_overlap(text, str(item.get("text", "")))
        if overlap >= FACT_NEAR_DUP_ARBITRATE_OVERLAP:
            scored.append((overlap, item))
    scored.sort(key=lambda pair: (-pair[0], str(pair[1].get("id", ""))))
    return [item for _, item in scored[:max(0, limit)]]


def embedding_candidates(
    fact: dict[str, Any], existing: list[dict[str, Any]], *, threshold: float = FACT_DEDUP_COSINE_THRESHOLD,
    limit: int = FACT_DEDUP_PAIRS_PER_NEW,
) -> list[dict[str, Any]]:
    vector = fact.get("embedding")
    if not isinstance(vector, list):
        return []
    scored = []
    for item in existing:
        other = item.get("embedding")
        if not isinstance(other, list):
            continue
        score = cosine_similarity(vector, other)
        if score >= threshold:
            scored.append((score, item))
    scored.sort(key=lambda pair: (-pair[0], str(pair[1].get("id", ""))))
    return [item for _, item in scored[:max(0, limit)]]


def build_dedup_pairs(fact: dict[str, Any], existing: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if find_exact_duplicate(fact, existing) is not None:
        return []
    candidates = {str(item.get("id")): item for item in near_duplicate_candidates(fact, existing)}
    candidates.update({str(item.get("id")): item for item in embedding_candidates(fact, existing)})
    return [
        {"candidate_id": fact.get("id"), "existing_id": item.get("id"), "candidate_text": str(fact.get("text", "")), "existing_text": str(item.get("text", ""))}
        for item in candidates.values() if item.get("id") is not None
    ]
