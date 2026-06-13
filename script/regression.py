"""本地回归：把当前 answer.csv 与参考答案逐题比对，输出分领域/题型/漏选-多选明细。

不调用任何模型，只读 answer.csv + 参考答案 + group_a 题面。

参考答案来源（按可信度）：
  1) --consensus：给定多份【真实榜单评分】的提交（已知分数），取它们【完全一致】的题作为
     高置信 ground truth（最可信，但只覆盖一致子集）；
  2) --ref：单份参考答案 CSV（reference_answers.csv，启发式生成，可信度较低，仅作补充）。

评测规则对齐：
  - multi：答案字母去重排序后完全匹配（漏选/多选/错选均判错，无部分分）；
  - mcq/tf：取首个有效字母比对。

用法：
    python script/regression.py
    python script/regression.py --consensus answer-0611-62.csv answer-0611-64.7.csv answer-0611-65.76.csv
    python script/regression.py --answer answer.csv --ref logs/reference_answers.csv
    python script/regression.py --domain financial_contracts
输出：
    控制台：总准确率、分领域、分题型、multi 漏选/多选/混合明细、错题清单
    logs/regression_report.csv：每题一行（含 reference 来源与置信度）
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.config import settings  # noqa: E402


def norm_answer(answer: str, answer_format: str) -> str:
    letters = re.findall(r"[A-D]", (answer or "").upper())
    if answer_format == "tf":
        letters = [l for l in letters if l in ("A", "B")]
    if answer_format == "multi":
        return "".join(sorted(set(letters)))
    return letters[0] if letters else ""


def load_answers(path: Path, col: str) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            qid = row.get("qid", "")
            if qid and qid != "summary":
                out[qid] = row.get(col, "") or ""
    return out


def load_reference(path: Path) -> dict[str, dict]:
    out: dict[str, dict] = {}
    if not path.exists():
        return out
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            qid = row.get("qid", "")
            if qid and qid != "summary":
                out[qid] = {
                    "answer": row.get("reference_answer", "") or row.get("answer", ""),
                    "confidence": row.get("confidence", ""),
                }
    return out


def load_question_meta() -> dict[str, dict]:
    meta: dict[str, dict] = {}
    group_dir = settings.questions_dir / "group_a"
    for path in sorted(group_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        for item in data:
            meta[item.get("qid", "")] = {
                "domain": item.get("domain", ""),
                "answer_format": item.get("answer_format", ""),
            }
    return meta


def build_consensus(paths: list[str], meta: dict[str, dict]) -> dict[str, dict]:
    """从多份真实榜单提交里取【完全一致】的题作为高置信 ground truth。"""
    versions = [load_answers(Path(p), "answer") for p in paths]
    versions = [v for v in versions if v]
    if len(versions) < 2:
        return {}
    qids = set.intersection(*[set(v.keys()) for v in versions])
    consensus: dict[str, dict] = {}
    for qid in qids:
        fmt = meta.get(qid, {}).get("answer_format", "")
        norms = {norm_answer(v[qid], fmt) for v in versions}
        if len(norms) == 1:
            consensus[qid] = {"answer": next(iter(norms)), "confidence": "consensus"}
    return consensus


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--answer", default=str(settings.answer_path))
    parser.add_argument("--ref", default=str(settings.logs_dir / "reference_answers.csv"))
    parser.add_argument(
        "--consensus",
        nargs="*",
        default=[],
        help="多份真实榜单提交 CSV：取完全一致的题作为高置信 ground truth（优先于 --ref）",
    )
    parser.add_argument("--domain", default="", help="Optional domain filter")
    parser.add_argument("--out", default=str(settings.logs_dir / "regression_report.csv"))
    args = parser.parse_args()

    cur = load_answers(Path(args.answer), "answer")
    meta = load_question_meta()
    if args.consensus:
        ref = build_consensus(args.consensus, meta)
        print(f"[consensus] {len(ref)} high-confidence questions from {len(args.consensus)} scored submissions")
    else:
        ref = load_reference(Path(args.ref))

    rows: list[dict] = []
    total = correct = 0
    by_domain: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    by_format: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    # multi 错误细分：漏选/多选/混合（按领域）
    multi_under: dict[str, int] = defaultdict(int)
    multi_over: dict[str, int] = defaultdict(int)
    multi_mixed: dict[str, int] = defaultdict(int)
    # 按 reference confidence 的错误分布
    wrong_by_conf: dict[str, int] = defaultdict(int)
    wrong_rows: list[dict] = []

    for qid, info in ref.items():
        if qid not in cur:
            continue
        domain = meta.get(qid, {}).get("domain", "?")
        if args.domain and domain != args.domain:
            continue
        fmt = meta.get(qid, {}).get("answer_format", "?")
        r_norm = norm_answer(info["answer"], fmt)
        c_norm = norm_answer(cur[qid], fmt)
        is_correct = bool(r_norm) and r_norm == c_norm
        total += 1
        by_domain[domain][1] += 1
        by_format[fmt][1] += 1
        if is_correct:
            correct += 1
            by_domain[domain][0] += 1
            by_format[fmt][0] += 1
        else:
            wrong_by_conf[info["confidence"] or "?"] += 1
            if fmt == "multi":
                missing = set(r_norm) - set(c_norm)
                extra = set(c_norm) - set(r_norm)
                if missing and extra:
                    multi_mixed[domain] += 1
                elif missing:
                    multi_under[domain] += 1
                elif extra:
                    multi_over[domain] += 1
            wrong_rows.append(
                {"qid": qid, "domain": domain, "format": fmt, "current": c_norm, "reference": r_norm, "confidence": info["confidence"]}
            )
        rows.append(
            {
                "qid": qid,
                "domain": domain,
                "format": fmt,
                "current": c_norm,
                "reference": r_norm,
                "confidence": info["confidence"],
                "correct": "correct" if is_correct else "wrong",
            }
        )

    _write_report(Path(args.out), rows)

    print(f"TOTAL={total} CORRECT={correct} ACC={100 * correct / total:.1f}%" if total else "no overlapping qids")
    print("\n== BY DOMAIN (correct/total) ==")
    for domain in sorted(by_domain):
        c, t = by_domain[domain]
        print(f"  {domain:22s} {c}/{t}")
    print("\n== BY FORMAT (correct/total) ==")
    for fmt in sorted(by_format):
        c, t = by_format[fmt]
        print(f"  {fmt:8s} {c}/{t}")
    print("\n== MULTI ERROR SUBTYPES (by domain) ==")
    print(f"  under(漏选): {dict(multi_under)}")
    print(f"  over (多选): {dict(multi_over)}")
    print(f"  mixed(混合): {dict(multi_mixed)}")
    print(f"\n== WRONG BY REFERENCE CONFIDENCE ==\n  {dict(wrong_by_conf)}")
    print("\n== WRONG LIST ==")
    for w in sorted(wrong_rows, key=lambda r: r["qid"]):
        print(f"  {w['qid']:12s} {w['domain']:20s} {w['format']:6s} cur={w['current']:6s} ref={w['reference']:6s} [{w['confidence']}]")
    print(f"\nwrote {args.out}")


def _write_report(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = ["qid", "domain", "format", "current", "reference", "confidence", "correct"]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in sorted(rows, key=lambda r: r["qid"]):
            writer.writerow(row)


if __name__ == "__main__":
    main()
