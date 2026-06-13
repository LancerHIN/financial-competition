from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.answer_normalizer import normalize_status  # noqa: E402
from agent.config import settings  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gold", default="", help="Optional gold answer CSV: qid,answer")
    parser.add_argument("--out", default=str(ROOT / "logs" / "analysis_report.csv"))
    args = parser.parse_args()

    evidence = load_json(settings.evidence_path)
    answers = load_answers(settings.answer_path)
    questions = load_questions()
    gold = load_gold(args.gold) if args.gold else {}

    rows = []
    for record in evidence:
        qid = record.get("qid")
        meta = questions.get(qid, {})
        rows.append(build_row(qid, record, answers.get(qid, {}), meta, gold))

    write_report(Path(args.out), rows, bool(gold))
    print(f"wrote {args.out} ({len(rows)} rows)")


def load_json(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def load_answers(path: Path) -> dict[str, dict]:
    answers: dict[str, dict] = {}
    if not path.exists():
        return answers
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            answers[row.get("qid", "")] = row
    return answers


def load_questions() -> dict[str, dict]:
    questions: dict[str, dict] = {}
    group_dir = settings.questions_dir / "group_a"
    if not group_dir.exists():
        return questions
    for path in sorted(group_dir.glob("*.json")):
        for item in json.loads(path.read_text(encoding="utf-8")):
            questions[item.get("qid", "")] = item
    return questions


def load_gold(path_str: str) -> dict[str, str]:
    gold: dict[str, str] = {}
    path = Path(path_str)
    if not path.exists():
        return gold
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            answer = row.get("answer", "") or row.get("reference_answer", "")
            gold[row.get("qid", "")] = answer.strip().upper()
    return gold


def build_row(qid: str, record: dict, answer_row: dict, meta: dict, gold: dict) -> dict:
    cards = record.get("evidence_retrieval", [])
    supported = refuted = unknown = 0
    for card in cards:
        pass
    judgements = record.get("judgements", {})
    for value in judgements.values() if isinstance(judgements, dict) else []:
        status = normalize_status(value.get("status") if isinstance(value, dict) else value)
        supported += status == "supported"
        refuted += status == "refuted"
        unknown += status == "unknown"
    answer = record.get("answer", "")
    row = {
        "qid": qid,
        "domain": meta.get("domain", ""),
        "type": meta.get("answer_format", ""),
        "answer": answer,
        "num_cards": len(cards),
        "num_supported": supported,
        "num_refuted": refuted,
        "num_unknown": unknown,
        "stop_reason": record.get("stop_reason", ""),
        "retrieval_quality": (record.get("retrieval_quality") or {}).get("quality", ""),
        "total_tokens": answer_row.get("total_tokens", ""),
    }
    if gold:
        gold_answer = gold.get(qid, "")
        row["gold"] = gold_answer
        # 多选题/任意题型一律做“去重+排序”后再比对，避免 multi 顺序/重复导致误判
        # （评测规则：multi 去重排序后完全匹配；mcq/tf 取首字母）。
        fmt = meta.get("answer_format", "")
        row["correct"] = "correct" if gold_answer and _norm_answer(gold_answer, fmt) == _norm_answer(answer, fmt) else "wrong"
        row["wrong_reason_guess"] = guess_wrong_reason(record, answer, len(cards))
    return row


def _norm_answer(answer: str, answer_format: str) -> str:
    """归一答案字母用于比对：multi 去重排序；mcq/tf 取首个有效字母。"""
    import re

    letters = re.findall(r"[A-D]", (answer or "").upper())
    if answer_format == "tf":
        letters = [l for l in letters if l in ("A", "B")]
    if answer_format == "multi":
        return "".join(sorted(set(letters)))
    return letters[0] if letters else ""


def guess_wrong_reason(record: dict, answer: str, num_cards: int) -> str:
    quality = (record.get("retrieval_quality") or {}).get("quality", "")
    if num_cards == 0:
        return "no_evidence"
    if quality == "bad":
        return "retrieval_bad"
    if record.get("stop_reason") == "no_new_evidence":
        return "all_unknown"
    if answer == "A":
        return "answer_fallback"
    return "possible_reasoning_error"


def write_report(path: Path, rows: list[dict], has_gold: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = ["qid", "domain", "type", "answer", "num_cards", "num_supported", "num_refuted", "num_unknown", "stop_reason", "retrieval_quality", "total_tokens"]
    if has_gold:
        fields += ["gold", "correct", "wrong_reason_guess"]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fields})


if __name__ == "__main__":
    main()
