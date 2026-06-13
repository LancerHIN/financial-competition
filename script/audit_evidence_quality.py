"""证据质量审计：定位分数损失发生在哪一步。

不需要标准答案。把每题在各阶段暴露的信号交叉比对，给每题打一个“最可能的失分环节”标签：

阶段链路：
    parse/table  ->  chunk  ->  retrieval  ->  compression  ->  judgement

判定逻辑（只读，不调用任何模型）：
  1. 选项里出现的关键事实（年份/金额/比例/条款号/财务指标）是否能在 *原始 chunk* 里找到？
     - 找不到  => 大概率 parse/table/chunk 阶段就丢了（或 doc 没召回）。
  2. gold doc_ids（A 榜）是否进入 candidate_doc_ids？
     - 没进  => retrieval(doc-level) 失分。
  3. 关键事实在 chunk 里有，但没进 evidence_cards 的 quote？
     - => compression 阶段剪掉了。
  4. 关键事实进了 quote，但该选项仍 unknown？
     - => judgement 阶段问题（prompt/reasoning），不是检索/压缩。
  5. quote 是否像原文（validation_issues）、是否重复占坑、每选项证据数。

用法：
    python script/audit_evidence_quality.py
    python script/audit_evidence_quality.py --evidence evidence.json --domain financial_contracts
输出：
    logs/evidence_audit.csv   每题一行的诊断
    logs/evidence_audit.json  含逐选项明细
    控制台打印阶段失分汇总
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.config import settings  # noqa: E402

YEAR_RE = re.compile(r"20[0-3]\d")
PERCENT_RE = re.compile(r"\d+(?:\.\d+)?\s*%|百分之[一二三四五六七八九十百千万零点\.\d]+")
MONEY_RE = re.compile(r"\d+(?:\.\d+)?\s*(?:亿元|万元|千元|亿|万|元)")
CLAUSE_RE = re.compile(r"第[一二三四五六七八九十百零〇0-9]+条(?:第[一二三四五六七八九十0-9]+款)?")
INDICATORS = [
    "营业收入", "营业总收入", "归属于上市公司股东的净利润", "归母净利润",
    "经营活动产生的现金流量净额", "经营现金流", "研发投入", "研发费用",
    "现金分红", "分红比例", "股份回购", "发行规模", "票面利率", "评级", "担保", "回售", "赎回",
    "现金价值", "账户价值", "身故保险金", "退保", "免责", "受托管理人",
]
NEGATION_TERMS = ["但", "除外", "不承担", "不适用", "不得", "免责", "除"]


def norm(text: str) -> str:
    return re.sub(r"\s+", "", str(text)).lower()


def extract_facts(text: str) -> dict[str, list[str]]:
    text = text or ""
    return {
        "years": sorted(set(YEAR_RE.findall(text))),
        "percents": sorted({norm(p) for p in PERCENT_RE.findall(text)}),
        "money": sorted({norm(m) for m in MONEY_RE.findall(text)}),
        "clauses": sorted({norm(c) for c in CLAUSE_RE.findall(text)}),
        "indicators": sorted({i for i in INDICATORS if i in text}),
    }


def all_facts(facts: dict[str, list[str]]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for kind, values in facts.items():
        for v in values:
            out.append((kind, v))
    return out


def load_chunks_by_doc() -> dict[str, str]:
    by_doc: dict[str, list[str]] = defaultdict(list)
    if not settings.chunks_path.exists():
        return {}
    with settings.chunks_path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                chunk = json.loads(line)
            except json.JSONDecodeError:
                continue
            did = str(chunk.get("doc_id", ""))
            if did:
                by_doc[did].append(norm(chunk.get("text", "") + " " + str(chunk.get("table_text", ""))))
    return {d: "".join(parts) for d, parts in by_doc.items()}


def load_questions() -> dict[str, dict]:
    out: dict[str, dict] = {}
    for path in sorted((settings.questions_dir / "group_a").glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        for item in data:
            out[item.get("qid", "")] = item
    return out


def classify(record: dict, question: dict, doc_text: dict[str, str]) -> dict:
    qid = record.get("qid", "")
    options = question.get("options", {}) if question else {}
    gold_docs = [str(d) for d in (question.get("doc_ids") or [])]
    candidate_docs = [str(d) for d in (record.get("candidate_doc_ids") or [])]

    # evidence quote 合并文本
    evidence = record.get("evidence_retrieval", []) or []
    quote_blob = norm(" ".join(str(e.get("quote", "")) for e in evidence))

    judgements = record.get("judgements", {}) or {}
    evidence_per_option = record.get("evidence_per_option", {}) or {}

    # gold doc 是否进入候选（A 榜）
    missing_gold_docs = [d for d in gold_docs if d not in candidate_docs] if gold_docs else []

    per_option_diag = {}
    stage_votes: Counter = Counter()
    for letter, text in options.items():
        facts = all_facts(extract_facts(text))
        status = (judgements.get(letter, {}) or {}).get("status", "unknown")
        n_evi = evidence_per_option.get(letter, 0)
        # 选项是否有可定位事实
        if not facts:
            per_option_diag[letter] = {"status": status, "stage": "no_facts", "n_evi": n_evi}
            continue
        # 事实在原文 chunk（按 gold doc 聚合文本）能否找到
        doc_pool = "".join(doc_text.get(d, "") for d in (gold_docs or candidate_docs))
        in_doc = [f for f in facts if f[1] in doc_pool] if doc_pool else []
        in_quote = [f for f in facts if f[1] in quote_blob]

        if doc_pool and not in_doc:
            stage = "parse_or_chunk"  # 事实连原文聚合文本都找不到 -> 解析/表格/切分丢失（或事实抽取口径差异）
        elif gold_docs and missing_gold_docs and not in_quote:
            stage = "retrieval_doc"   # gold doc 没召回
        elif in_doc and not in_quote:
            stage = "compression"     # 原文有，quote 没有 -> 压缩剪掉
        elif in_quote and status not in ("supported", "refuted"):
            stage = "judgement"       # 证据有，模型仍未决
        else:
            stage = "ok"
        per_option_diag[letter] = {
            "status": status,
            "n_evi": n_evi,
            "facts": facts,
            "in_doc": in_doc,
            "in_quote": in_quote,
            "stage": stage,
        }
        if stage != "ok":
            stage_votes[stage] += 1

    # quote 非原文 / 重复占坑
    issues = record.get("validation_issues", []) or []
    non_original = sum(1 for i in issues if "非原文" in str(i))
    chunk_ids = [str(e.get("chunk_id", "")) for e in evidence]
    dup_slots = len(chunk_ids) - len(set(chunk_ids))

    primary_stage = stage_votes.most_common(1)[0][0] if stage_votes else "ok"
    return {
        "qid": qid,
        "domain": record.get("domain") or (question.get("domain") if question else ""),
        "answer_format": record.get("answer_format") or (question.get("answer_format") if question else ""),
        "answer": record.get("answer", ""),
        "stop_reason": record.get("stop_reason", ""),
        "supported_count": record.get("supported_count", 0),
        "unknown_count": record.get("unknown_count", 0),
        "evidence_count": record.get("evidence_count", len(evidence)),
        "dup_evidence_slots": dup_slots,
        "non_original_quotes": non_original,
        "gold_docs": gold_docs,
        "candidate_docs": candidate_docs,
        "missing_gold_docs": missing_gold_docs,
        "retrieval_quality": (record.get("retrieval_quality", {}) or {}).get("quality", ""),
        "primary_stage": primary_stage,
        "stage_votes": dict(stage_votes),
        "per_option": per_option_diag,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", default=str(settings.evidence_path))
    parser.add_argument("--domain", default="")
    args = parser.parse_args()

    ev_path = Path(args.evidence)
    records = json.loads(ev_path.read_text(encoding="utf-8"))
    if args.domain:
        records = [r for r in records if r.get("domain") == args.domain]
    questions = load_questions()
    doc_text = load_chunks_by_doc()

    diagnostics = []
    for record in records:
        q = questions.get(record.get("qid", ""), {})
        diagnostics.append(classify(record, q, doc_text))

    # 汇总
    stage_counter: Counter = Counter()
    domain_stage: dict[str, Counter] = defaultdict(Counter)
    for d in diagnostics:
        stage_counter[d["primary_stage"]] += 1
        domain_stage[d["domain"]][d["primary_stage"]] += 1

    settings.logs_dir.mkdir(parents=True, exist_ok=True)
    json_out = settings.logs_dir / "evidence_audit.json"
    json_out.write_text(json.dumps(diagnostics, ensure_ascii=False, indent=2), encoding="utf-8")

    csv_out = settings.logs_dir / "evidence_audit.csv"
    fields = [
        "qid", "domain", "answer_format", "answer", "primary_stage", "stop_reason",
        "supported_count", "unknown_count", "evidence_count", "dup_evidence_slots",
        "non_original_quotes", "retrieval_quality", "missing_gold_docs",
    ]
    with csv_out.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for d in diagnostics:
            row = dict(d)
            row["missing_gold_docs"] = ",".join(d["missing_gold_docs"])
            writer.writerow(row)

    print(f"audited {len(diagnostics)} questions from {ev_path.name}")
    print("\n== 主要失分阶段分布 ==")
    for stage, count in stage_counter.most_common():
        print(f"  {stage:16s} {count}")
    print("\n== 分领域失分阶段 ==")
    for domain, counter in domain_stage.items():
        detail = " ".join(f"{s}={c}" for s, c in counter.most_common())
        print(f"  {domain:22s} {detail}")
    # 列出最值得人工复核的题
    print("\n== 重点复核题（非 ok 且证据少/重复/非原文）==")
    flagged = [
        d for d in diagnostics
        if d["primary_stage"] != "ok" or d["dup_evidence_slots"] > 0 or d["non_original_quotes"] > 0 or d["missing_gold_docs"]
    ]
    for d in flagged[:30]:
        print(
            f"  {d['qid']:12s} stage={d['primary_stage']:14s} "
            f"unknown={d['unknown_count']} dup={d['dup_evidence_slots']} "
            f"nonorig={d['non_original_quotes']} miss_doc={','.join(d['missing_gold_docs']) or '-'} "
            f"votes={d['stage_votes']}"
        )
    print(f"\nwrote {csv_out}")
    print(f"wrote {json_out}")


if __name__ == "__main__":
    main()
