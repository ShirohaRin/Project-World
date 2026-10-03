from __future__ import annotations

import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from math import log
from typing import Iterable


_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_LATIN_RE = re.compile(r"[A-Za-z0-9_]+")


# 第一版使用常见的一对一繁简折叠；后续可替换为生成的完整映射表。
_TRADITIONAL_TO_SIMPLIFIED = str.maketrans({
    "記": "记", "憶": "忆", "學": "学", "習": "习", "與": "与",
    "長": "长", "時": "时", "間": "间", "關": "关", "係": "系",
    "個": "个", "們": "们", "這": "这", "樣": "样", "過": "过",
    "會": "会", "說": "说", "話": "话", "對": "对", "於": "于",
    "進": "进", "行": "行", "發": "发", "現": "现", "後": "后",
    "應": "应", "為": "为", "來": "来", "從": "从", "專": "专",
    "業": "业", "實": "实", "體": "体", "驗": "验", "種": "种",
    "類": "类", "與": "与", "國": "国", "語": "语", "開": "开",
    "門": "门", "問": "问", "題": "题", "處": "处", "理": "理",
    "資": "资", "訊": "讯", "設": "设", "計": "计", "術": "术",
})


def fold_script(text: str) -> str:
    return text.translate(_TRADITIONAL_TO_SIMPLIFIED)


def tokenize(text: str) -> list[str]:
    text = fold_script(text)
    tokens: list[str] = []
    cjk_chars = [char for char in text if _CJK_RE.fullmatch(char)]
    if len(cjk_chars) / max(1, len(text)) >= 0.2:
        for size in (2, 3):
            tokens.extend("".join(cjk_chars[index:index + size]) for index in range(len(cjk_chars) - size + 1))
    tokens.extend(match.group(0).lower() for match in _LATIN_RE.finditer(text))
    return tokens


@dataclass(frozen=True)
class RecallCandidate:
    memory_id: str
    content: str
    bm25_score: float = 0.0
    cosine_score: float = 0.0
    evidence_score: float = 0.0


def bm25_rank(query: str, documents: Iterable[tuple[str, str]], k1: float = 1.5, b: float = 0.75) -> list[RecallCandidate]:
    docs = [(memory_id, tokenize(content), content) for memory_id, content in documents]
    if not docs:
        return []
    query_terms = tokenize(query)
    document_frequency: Counter[str] = Counter()
    for _, terms, _ in docs:
        document_frequency.update(set(terms))
    average_length = sum(len(terms) for _, terms, _ in docs) / len(docs)
    ranked: list[RecallCandidate] = []
    for memory_id, terms, content in docs:
        term_frequency = Counter(terms)
        score = 0.0
        for term in query_terms:
            if not term_frequency[term]:
                continue
            df = document_frequency[term]
            idf = log(1 + (len(docs) - df + 0.5) / (df + 0.5))
            denominator = term_frequency[term] + k1 * (1 - b + b * len(terms) / max(1.0, average_length))
            score += idf * term_frequency[term] * (k1 + 1) / denominator
        if score > 0:
            ranked.append(RecallCandidate(memory_id, content, bm25_score=score))
    return sorted(ranked, key=lambda item: (-item.bm25_score, item.memory_id))


def reciprocal_rank_fusion(rankings: Iterable[Iterable[RecallCandidate]], k: int = 60) -> list[RecallCandidate]:
    merged: dict[str, RecallCandidate] = {}
    scores: defaultdict[str, float] = defaultdict(float)
    for ranking in rankings:
        for rank, candidate in enumerate(ranking, start=1):
            scores[candidate.memory_id] += 1 / (k + rank)
            merged.setdefault(candidate.memory_id, candidate)
    return sorted(
        (RecallCandidate(**{**candidate.__dict__, "bm25_score": scores[candidate.memory_id]}) for candidate in merged.values()),
        key=lambda item: (-item.bm25_score, item.memory_id),
    )


__all__ = ["RecallCandidate", "bm25_rank", "fold_script", "reciprocal_rank_fusion", "tokenize"]
