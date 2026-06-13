"""回归验证：对指定 qid 集跑真实 pipeline，对比核实真值，打印逐题 judgement。
用法：
  python script/_validate_fixes.py            # 跑13道真错题
  python script/_validate_fixes.py all         # 跑全量100题
  python script/_validate_fixes.py reg_a_017 fc_a_004   # 跑指定题
"""
from __future__ import annotations
import sys, json, glob, io
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.bm25_index import BM25Index
from agent.config import settings
from agent.hybrid_retriever import HybridRetriever
from agent.retriever import Retriever
from agent.qwen_client import QwenClient
from agent.token_counter import TokenUsageTracker
from agent.a_leaderboard_agent import ALeaderboardAgent

# 逐题核实真值（见 logs/_verified_wrong_questions.md）
TRUTH = {
    "fc_a_004": "A", "fc_a_014": "AB", "fin_a_008": "B", "fin_a_015": "A",
    "ins_a_006": "A", "ins_a_008": "ABC", "ins_a_014": "ABD", "ins_a_019": "ABCD",
    "res_a_002": "AC", "res_a_004": "AB", "res_a_020": "ABD",
    "reg_a_011": "AC", "reg_a_017": "AB",
}
WRONG_13 = list(TRUTH.keys())


def load_questions():
    qm = {}
    for f in glob.glob(str(ROOT / "questions" / "group_a" / "*.json")):
        for it in json.load(open(f, encoding="utf-8")):
            qm[it["qid"]] = it
    return qm


def main():
    args = sys.argv[1:]
    qm = load_questions()
    if not args:
        qids = WRONG_13
    elif args == ["all"]:
        qids = sorted(qm.keys())
    else:
        qids = args

    idx = BM25Index.load(settings.index_path)
    retr = HybridRetriever(idx, rrf_k=settings.rrf_k) if settings.hybrid_retrieval_enabled else Retriever(idx)
    if hasattr(retr, "warmup"):
        retr.warmup()
    tracker = TokenUsageTracker(settings.token_log_path)

    out = io.open(ROOT / "logs" / "_validate_out.txt", "w", encoding="utf-8")
    n_correct = 0
    n_truth_known = 0
    for qid in qids:
        it = qm.get(qid)
        if not it:
            out.write(f"{qid}: NOT FOUND\n"); continue
        agent = ALeaderboardAgent(retr, QwenClient(), tracker)
        try:
            res = agent.answer_question(it)
        except Exception as exc:  # noqa: BLE001
            line = f"{qid} [{it.get('answer_format')}] -> ERROR {exc}"
            out.write(line + "\n"); print(line)
            continue
        ans = res["answer"]
        truth = TRUTH.get(qid)
        mark = ""
        if truth is not None:
            n_truth_known += 1
            ok = ans == truth
            n_correct += int(ok)
            mark = "  OK" if ok else f"  XX (truth={truth})"
        line = f"{qid} [{it.get('answer_format')}] -> {ans}{mark}"
        out.write(line + "\n")
        print(line)
        for L in sorted(it.get("options", {}).keys()):
            j = res["judgements"].get(L, {})
            if j:
                out.write("    %s: %-12s conf=%.2f ev#=%s %s\n" % (
                    L, j.get("status"), float(j.get("confidence", 0) or 0),
                    res.get("evidence_per_option", {}).get(L),
                    str(j.get("reason", ""))[:80]))
        out.write("\n")
    summary = f"\n=== {n_correct}/{n_truth_known} correct on truth-known qids ==="
    out.write(summary + "\n")
    print(summary)
    out.close()


if __name__ == "__main__":
    main()
