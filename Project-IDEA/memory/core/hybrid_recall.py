from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Iterable

from Memory.core.evidence_math import evidence_score
from Memory.core.recall import RecallCandidate, bm25_rank, reciprocal_rank_fusion

HYBRID_RECALL_BUDGET_EACH = 20
HYBRID_RECALL_BUDGET_TOTAL = 8
HYBRID_RECALL_RRF_K = 60

REFLECTION_DROP_STATUSES = frozenset({"superseded", "disputed", "archived", "deleted"})


@dataclass(frozen=True)
class RecallQuery:
    text: str
    layer: str | None = None
    limit: int = HYBRID_RECALL_BUDGET_TOTAL


@dataclass(frozen=True)
class RecallResult:
    memory_id: str
    content: str
    layer: str
    bm25_score: float
    cosine_score: float
    evidence_score: float
    fusion_score: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "memory_id": self.memory_id,
            "content": self.content,
            "layer": self.layer,
            "bm25_score": self.bm25_score,
            "cosine_score": self.cosine_score,
            "evidence_score": self.evidence_score,
            "fusion_score": self.fusion_score,
        }


def hard_filter(entries: Iterable[dict[str, Any]], now: datetime | None = None) -> list[dict[str, Any]]:
    """丢弃负分、suppressed 和终态 reflection，以及空白内容。"""
    now = now or datetime.now(timezone.utc)
    kept: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if entry.get("suppressed"):
            continue
        if entry.get("layer") == "reflection" and entry.get("status") in REFLECTION_DROP_STATUSES:
            continue
        if not str(entry.get("text") or "").strip():
            continue
        if evidence_score(entry, now) < 0:
            continue
        kept.append(entry)
    return kept


def hybrid_recall(
    query: str,
    entries: Iterable[dict[str, Any]],
    *,
    embed: Callable[[str], list[float] | None] | None = None,
    budget_each: int = HYBRID_RECALL_BUDGET_EACH,
    budget_total: int = HYBRID_RECALL_BUDGET_TOTAL,
    rrf_k: int = HYBRID_RECALL_RRF_K,
    layer: str | None = None,
) -> list[RecallResult]:
    """BM25 + 可选 embedding cosine 的混合召回，使用 RRF 融合。"""
    pool = hard_filter(entries)
    if layer is not None:
        pool = [entry for entry in pool if entry.get("layer") == layer]
    if not pool or not query.strip():
        return []
    documents = [(str(entry.get("id")), str(entry.get("text") or "")) for entry in pool]

    rankings: list[list[RecallCandidate]] = []
    bm25 = bm25_rank(query, documents)
    if bm25:
        rankings.append(bm25[:budget_each])
    if embed is not None:
        query_vector = embed(query)
        if query_vector:
            scored = []
            for entry in pool:
                vector = entry.get("embedding")
                if not isinstance(vector, list) or len(vector) != len(query_vector):
                    continue
                cosine = _cosine(query_vector, vector)
                if cosine > 0:
                    scored.append(RecallCandidate(str(entry.get("id")), str(entry.get("text") or ""), cosine_score=cosine))
            if scored:
                scored.sort(key=lambda item: (-item.cosine_score, item.memory_id))
                rankings.append(scored[:budget_each])

    if not rankings:
        return []
    fused = reciprocal_rank_fusion(rankings, k=rrf_k)
    fused = fused[:budget_total]

    now = datetime.now(timezone.utc)
    by_id = {str(entry.get("id")): entry for entry in pool}
    results: list[RecallResult] = []
    for candidate in fused:
        entry = by_id.get(candidate.memory_id)
        if entry is None:
            continue
        results.append(
            RecallResult(
                memory_id=candidate.memory_id,
                content=str(entry.get("text") or ""),
                layer=str(entry.get("layer") or "fact"),
                bm25_score=candidate.bm25_score,
                cosine_score=candidate.cosine_score,
                evidence_score=evidence_score(entry, now),
                fusion_score=candidate.bm25_score,
            )
        )
    return results


def _cosine(left: list[float], right: list[float]) -> float:
    from Memory.storage.dedup import cosine_similarity
    return cosine_similarity(left, right)
