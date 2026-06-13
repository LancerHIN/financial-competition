"""检索耗时基准脚本：只跑本地检索（HybridRetriever），不调用任何 Qwen API、不计 token。

用途：验证候选池/BM25 复用等优化的速度收益，输出每道题的 retrieve_option_wise 耗时。
不改变检索结果，仅做计时与统计。

用法：
    python script/benchmark_retrieval.py --limit 20
    python script/benchmark_retrieval.py --limit 20 --domain financial_reports
    CANDIDATE_ONLY_SCORING=0 python script/benchmark_retrieval.py --limit 20   # ablation
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.bm25_index import BM25Index  # noqa: E402
from agent.config import settings  # noqa: E402
from agent.hybrid_retriever import HybridRetriever  # noqa: E402


def load_group_a_questions(group_dir: Path) -> list[dict]:
    questions = []
    for path in sorted(group_dir.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        questions.extend(item for item in data if item.get("split") == "A")
    questions.sort(key=lambda item: item.get("qid", ""))
    return questions


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=20, help="只跑前 N 道题")
    parser.add_argument("--domain", default="", help="可选 domain 过滤")
    args = parser.parse_args()

    if not settings.index_path.exists():
        raise FileNotFoundError(f"BM25 index not found: {settings.index_path}. 先运行 python script/build_index.py")

    questions = load_group_a_questions(settings.questions_dir / "group_a")
    if args.domain:
        questions = [q for q in questions if q.get("domain") == args.domain]
    if args.limit:
        questions = questions[: args.limit]

    index = BM25Index.load(settings.index_path)
    retriever = HybridRetriever(index, rrf_k=settings.rrf_k)
    print(f"chunks={len(index.chunks)} candidate_only={settings.candidate_only_scoring} pool={settings.candidate_pool_size}")

    warmup_start = time.perf_counter()
    retriever.warmup()
    print(f"warmup elapsed={time.perf_counter() - warmup_start:.3f}s")

    timings: list[float] = []
    for q in questions:
        options = q.get("options", {})
        start = time.perf_counter()
        chunks = retriever.retrieve_option_wise(
            question=q["question"],
            options=options,
            extra_queries=[],
            domain=q.get("domain"),
            doc_ids=q.get("doc_ids") or [],
            global_top_k=min(settings.retrieve_top_k, 10),
            option_top_k=4,
            final_top_k=settings.retrieve_top_k + len(options) * 4,
            min_per_option=settings.min_per_option,
        )
        elapsed = time.perf_counter() - start
        timings.append(elapsed)
        print(f"{q['qid']} domain={q.get('domain')} options={len(options)} chunks={len(chunks)} elapsed={elapsed:.3f}s")

    if timings:
        print("---")
        print(f"questions={len(timings)} total={sum(timings):.3f}s mean={statistics.mean(timings):.3f}s "
              f"median={statistics.median(timings):.3f}s max={max(timings):.3f}s")


if __name__ == "__main__":
    main()
