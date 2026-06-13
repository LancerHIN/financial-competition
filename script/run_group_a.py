from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from tqdm import tqdm

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.bm25_index import BM25Index  # noqa: E402
from agent.answer_normalizer import normalize_answer  # noqa: E402
from agent.a_leaderboard_agent import ALeaderboardAgent  # noqa: E402
from agent.config import settings  # noqa: E402
from agent.hybrid_retriever import HybridRetriever  # noqa: E402
from agent.qwen_client import QwenClient  # noqa: E402
from agent.retriever import Retriever  # noqa: E402
from agent.token_counter import TokenUsageTracker  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="Only run first N questions for smoke test")
    parser.add_argument("--domain", default="", help="Optional domain filter")
    parser.add_argument("--workers", type=int, default=1, help="Concurrent Qwen workers")
    args = parser.parse_args()
    setup_logging()
    if not settings.index_path.exists():
        raise FileNotFoundError(f"BM25 index not found: {settings.index_path}. Run python script/build_index.py first.")
    questions = load_group_a_questions(settings.questions_dir / "group_a")
    if args.domain:
        questions = [item for item in questions if item.get("domain") == args.domain]
    if args.limit:
        questions = questions[: args.limit]
    tracker = TokenUsageTracker(settings.token_log_path)
    index = BM25Index.load(settings.index_path)
    workers = max(1, args.workers)
    retriever = HybridRetriever(index, rrf_k=settings.rrf_k) if settings.hybrid_retrieval_enabled else Retriever(index)
    # 多线程前预热共享检索语料，避免并发开局各线程重复构建大语料导致卡死
    if workers > 1 and hasattr(retriever, "warmup"):
        logging.info("warming up retriever corpus before %d workers", workers)
        retriever.warmup()
    rows: list[dict] = []
    evidence_output: list[dict] = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(answer_one, question, retriever, tracker): question for question in questions}
        for future in tqdm(as_completed(futures), total=len(futures), desc=f"answer group A x{workers}"):
            question = futures[future]
            try:
                result = future.result()
            except Exception as exc:  # noqa: BLE001
                logging.exception("question failed qid=%s: %s", question.get("qid"), exc)
                result = {"qid": question["qid"], "answer": normalize_answer("A", question.get("answer_format", "mcq")), "judgements": {}, "evidence_cards": [], "stop_reason": "error"}
            usage = tracker.get(question["qid"])
            rows.append({"qid": question["qid"], "answer": result["answer"], **usage})
            evidence_output.append(to_evidence_record(result))
            logging.info("%s answer=%s tokens=%s", question["qid"], result["answer"], usage.get("total_tokens", 0))
            write_answer_csv(sorted(rows, key=lambda row: row["qid"]), tracker.summary())
            settings.evidence_path.write_text(json.dumps(sorted(evidence_output, key=lambda row: row["qid"]), ensure_ascii=False, indent=2), encoding="utf-8")
    rows.sort(key=lambda row: row["qid"])
    evidence_output.sort(key=lambda row: row["qid"])
    write_answer_csv(rows, tracker.summary())
    settings.evidence_path.write_text(json.dumps(evidence_output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {settings.answer_path}")
    print(f"wrote {settings.evidence_path}")


def setup_logging() -> None:
    settings.logs_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=settings.run_log_path,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        encoding="utf-8",
    )


def load_group_a_questions(group_dir: Path) -> list[dict]:
    questions = []
    for path in sorted(group_dir.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        questions.extend(item for item in data if item.get("split") == "A")
    questions.sort(key=lambda item: item.get("qid", ""))
    return questions


def answer_one(question: dict, retriever, tracker: TokenUsageTracker) -> dict:
    qwen = QwenClient()
    agent = ALeaderboardAgent(retriever, qwen, tracker)
    return agent.answer_question(question)


def write_answer_csv(rows: list[dict], summary: dict[str, int]) -> None:
    with settings.answer_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["qid", "answer", "prompt_tokens", "completion_tokens", "total_tokens"])
        writer.writeheader()
        writer.writerow({"qid": "summary", "answer": "", **summary})
        for row in rows:
            writer.writerow(row)


def to_evidence_record(result: dict) -> dict:
    records = []
    for card in result.get("evidence_cards", []):
        records.append(
            {
                "doc_id": card.get("doc_id", ""),
                "chunk_id": card.get("chunk_id", ""),
                "page": card.get("page"),
                "quote": card.get("quote", ""),
                "reasoning": card.get("reason") or card.get("fact", ""),
                "support_options": card.get("related_options", []),
            }
        )
    return {
        "qid": result["qid"],
        "answer": result["answer"],
        "stop_reason": result.get("stop_reason", ""),
        "judgements": result.get("judgements", {}),
        "doc_ids": result.get("doc_ids", []),
        "validation_status": result.get("validation_status", ""),
        "validation_issues": result.get("validation_issues", []),
        "answer_review": result.get("answer_review", {}),
        "domain": result.get("domain", ""),
        "answer_format": result.get("answer_format", ""),
        "fullcorpus_fallback_used": result.get("fullcorpus_fallback_used", False),
        "evidence_count": result.get("evidence_count", 0),
        "evidence_per_option": result.get("evidence_per_option", {}),
        "insufficient_options": result.get("insufficient_options", []),
        "fallback_used": result.get("fallback_used", False),
        "fallback_reason": result.get("fallback_reason", ""),
        "token_usage": result.get("token_usage", {}),
        "elapsed_time": result.get("elapsed_time"),
        "evidence_retrieval": records,
    }


if __name__ == "__main__":
    main()
