from __future__ import annotations

from pathlib import Path

from Memory.core import (
    MemoryLayer,
    MemoryStatus,
    bm25_rank,
    fold_script,
    hybrid_recall,
    reciprocal_rank_fusion,
    render_recall_block,
)


def test_script_fold_and_bm25():
    assert fold_script("記憶與學習") == "记忆与学习"
    ranked = bm25_rank("长期记忆", [("a", "这是长期记忆"), ("b", "短期任务")])
    assert ranked[0].memory_id == "a"


def test_rrf_merges_rankings():
    first = bm25_rank("长期记忆", [("a", "长期记忆系统"), ("b", "长期任务规划")])
    second = bm25_rank("记忆系统", [("b", "长期任务规划"), ("a", "长期记忆系统")])
    fused = reciprocal_rank_fusion([first, second])
    assert fused[0].memory_id == "a"
    assert {candidate.memory_id for candidate in fused} == {"a", "b"}


def test_domain_layers_and_statuses():
    assert MemoryLayer.PERSONA.value == "persona"
    assert MemoryStatus.ARCHIVED.value == "archived"


def test_hybrid_recall_and_render():
    entries = [
        {"id": "f1", "layer": "fact", "text": "用户喜欢猫", "score": 1.0},
        {"id": "f2", "layer": "fact", "text": "用户喜欢科研", "score": 0.8},
        {"id": "r1", "layer": "reflection", "text": "用户偏好稳定协作", "status": "confirmed", "score": 1.2},
    ]
    results = hybrid_recall("用户喜欢", entries)
    assert results and results[0].content
    block = render_recall_block([item.as_dict() for item in results])
    assert block.strip()
