"""HybridRetriever 并发安全 + 缓存预热行为测试。

目标：修复 10 并发开局卡死，但绝不改变检索排序结果（不影响分数）。
这些测试描述行为契约：
- 预热后与冷启动的 top 排序完全一致（warmup 只是提前算，不改结果）。
- 多线程并发检索不重复构建语料、不抛错、结果与单线程一致。
"""
from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.bm25_index import BM25Index  # noqa: E402
from agent.config import settings  # noqa: E402
from agent.hybrid_retriever import HybridRetriever  # noqa: E402


pytestmark = pytest.mark.skipif(
    not settings.index_path.exists(),
    reason="BM25 index 未构建，跳过需要真实索引的并发测试",
)


QUESTION = "某公司年度报告营业收入与净利润变化"
OPTIONS = {"A": "营业收入增长", "B": "净利润下滑"}


def _top_ids(chunks, n=8):
    return [str(c.get("chunk_id", "")) for c in chunks[:n]]


def test_warmup_matches_cold_start_ranking():
    """预热缓存后，检索 top 排序必须和冷启动完全一致——warmup 不改变分数。"""
    index = BM25Index.load(settings.index_path)

    cold = HybridRetriever(index, rrf_k=settings.rrf_k)
    cold_chunks = cold.retrieve_option_wise(QUESTION, OPTIONS, domain="financial_reports", final_top_k=15)

    warm = HybridRetriever(index, rrf_k=settings.rrf_k)
    warm.warmup()
    warm_chunks = warm.retrieve_option_wise(QUESTION, OPTIONS, domain="financial_reports", final_top_k=15)

    assert _top_ids(cold_chunks) == _top_ids(warm_chunks)


def test_concurrent_retrieval_is_consistent_and_safe():
    """多线程并发检索：不抛错，且结果与单线程一致（共享语料只构建一次）。"""
    index = BM25Index.load(settings.index_path)
    retriever = HybridRetriever(index, rrf_k=settings.rrf_k)
    retriever.warmup()

    baseline = _top_ids(
        retriever.retrieve_option_wise(QUESTION, OPTIONS, domain="financial_reports", final_top_k=15)
    )

    def run(_):
        return _top_ids(
            retriever.retrieve_option_wise(QUESTION, OPTIONS, domain="financial_reports", final_top_k=15)
        )

    with ThreadPoolExecutor(max_workers=10) as executor:
        results = list(executor.map(run, range(10)))

    for result in results:
        assert result == baseline
