"""运行独立可核验 golden 题库。

这个脚本不使用 answer.csv 作为真值；真值来自 tests/golden/verifiable_questions.json，
每题都带可核验 evidence quote。用途是验证系统在数字偷换、方向词、否定/例外条款上的真实准确率。

用法：
    python script/run_golden_verifiable.py --limit 5
    python script/run_golden_verifiable.py --domain regulatory
    python script/run_golden_verifiable.py --question-id gold_reg_01,gold_fc_03 --repeat 3
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

from agent.a_leaderboard_agent import ALeaderboardAgent  # noqa: E402
from agent.bm25_index import BM25Index  # noqa: E402
from agent.config import settings  # noqa: E402
from agent.hybrid_retriever import HybridRetriever  # noqa: E402
from agent.qwen_client import QwenClient  # noqa: E402
from agent.token_counter import TokenUsageTracker  # noqa: E402


def normalize_answer(answer: str, answer_format: str) -> str:
    letters = re.findall(r"[A-D]", (answer or "").upper())
    if answer_format == "multi":
        return "".join(sorted(set(letters)))
    return letters[0] if letters else ""


def load_questions(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("questions", [])


def to_agent_item(item: dict) -> dict:
    # 跨文档题可显式给出 doc_ids（需召回多个被比较文档）；否则回退到 evidence.doc_id 单文档。
    explicit_doc_ids = [str(d) for d in (item.get("doc_ids") or []) if str(d).strip()]
    if explicit_doc_ids:
        doc_ids = explicit_doc_ids
    else:
        doc_id = item.get("evidence", {}).get("doc_id", "")
        doc_ids = [doc_id] if doc_id else []
    return {
        "qid": item["qid"],
        "domain": item["domain"],
        "split": "GOLD",
        "question": item["question"],
        "options": item["options"],
        "answer_format": item["answer_format"],
        "type": "verifiable_golden",
        "doc_ids": doc_ids,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="运行独立可核验 golden 题库")
    parser.add_argument("--path", default=str(ROOT / "tests" / "golden" / "verifiable_questions.json"))
    parser.add_argument("--domain", default="")
    parser.add_argument("--question-id", default="")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--workers", type=int, default=1, help="并行运行的题目数（线程池），默认 1 串行")
    parser.add_argument("--out", default=str(ROOT / "logs" / "golden_verifiable_results.json"))
    args = parser.parse_args()

    questions = load_questions(Path(args.path))
    if args.domain:
        questions = [q for q in questions if q.get("domain") == args.domain]
    if args.question_id:
        wanted = {qid.strip() for qid in args.question_id.split(",") if qid.strip()}
        questions = [q for q in questions if q.get("qid") in wanted]
    if args.limit:
        questions = questions[: args.limit]
    if not questions:
        raise SystemExit("没有匹配的 golden 题。")

    if not settings.index_path.exists():
        raise FileNotFoundError(f"BM25 index not found: {settings.index_path}")

    index = BM25Index.load(settings.index_path)
    retriever = HybridRetriever(index, rrf_k=settings.rrf_k)
    if hasattr(retriever, "warmup"):
        retriever.warmup()

    tracker = TokenUsageTracker(settings.logs_dir / "golden_token_usage.jsonl")
    qwen = QwenClient()
    agent = ALeaderboardAgent(retriever, qwen, tracker)

    results: list[dict] = []
    domain_stats: dict[str, Counter] = defaultdict(Counter)
    total_tokens = 0

    repeat = max(1, args.repeat)

    def run_one(item: dict) -> dict:
        expected = normalize_answer(item.get("answer", ""), item.get("answer_format", "mcq"))
        runs = []
        answers = []
        for _ in range(repeat):
            result = agent.answer_question(to_agent_item(item))
            answer = normalize_answer(result.get("final_answer", ""), item.get("answer_format", "mcq"))
            usage = result.get("token_usage", {}) or tracker.get(item["qid"])
            tokens = int(usage.get("total_tokens", 0) or 0)
            answers.append(answer)
            runs.append(
                {
                    "answer": answer,
                    "tokens": tokens,
                    "judgements": result.get("judgements", {}),
                    "answer_review": result.get("answer_review", {}),
                    "evidence_ids": [c.get("evidence_id") for c in result.get("evidence_cards", [])],
                    "evidence_doc_ids": sorted({str(c.get("doc_id", "")) for c in result.get("evidence_cards", [])}),
                    "stop_reason": result.get("stop_reason", ""),
                    "validation_issues": result.get("validation_issues", []),
                }
            )
        stable = len(set(answers)) == 1
        correct = all(answer == expected for answer in answers)
        return {"item": item, "expected": expected, "answers": answers, "stable": stable, "correct": correct, "runs": runs}

    workers = max(1, args.workers)
    print(f"golden questions={len(questions)} repeat={repeat} workers={workers}")
    print("=" * 100)

    if workers == 1:
        completed = [run_one(item) for item in questions]
    else:
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=workers) as pool:
            # 保持与输入题序一致，便于阅读
            completed = list(pool.map(run_one, questions))

    for res in completed:
        item = res["item"]
        total_tokens += sum(r["tokens"] for r in res["runs"])
        domain_stats[item["domain"]]["total"] += 1
        domain_stats[item["domain"]]["correct"] += int(res["correct"])
        domain_stats[item["domain"]]["unstable"] += int(not res["stable"])
        mark = "PASS" if res["correct"] else "FAIL"
        print(f"{mark} {item['qid']:12s} {item['domain']:20s} expected={res['expected']:4s} answers={res['answers']} stable={res['stable']} tokens={sum(r['tokens'] for r in res['runs'])}")
        if not res["correct"]:
            print(f"     Q: {item['question']}")
            print(f"     evidence: {item.get('evidence', {})}")
            print(f"     review: {res['runs'][-1]['answer_review'].get('warnings', [])}")
        results.append(res)

    total = len(results)
    correct = sum(1 for r in results if r["correct"])
    print("=" * 100)
    print(f"accuracy={correct}/{total} = {correct / total:.2%} total_tokens={total_tokens}")
    for domain, stats in sorted(domain_stats.items()):
        acc = stats["correct"] / stats["total"] if stats["total"] else 0
        print(f"{domain:20s} {stats['correct']}/{stats['total']} acc={acc:.2%} unstable={stats['unstable']}")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
