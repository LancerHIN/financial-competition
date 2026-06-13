"""抗干扰评测脚本：系统性检验 A 榜问答系统在相近表述/条件偷换/否定例外/复杂选项下的稳健性。

设计原则
--------
1. 默认离线、零 token：只跑本地检索（HybridRetriever）+ 证据选择（ALeaderboardEvidenceSelector），
   把每题、每选项暴露的“抗干扰信号”交叉比对，给出失败类型标签。绝不默认调用 Qwen。
2. 调用 Qwen 的部分必须显式开启（--judge），并受 --limit / --question-id 严格约束，避免误烧 token。
3. 优先覆盖真实题与真实文档：题目来自 questions/group_a，文档证据来自已构建的 BM25 索引，
   不凭空造不可验证题。--cross-check 用现有 answer.csv 做答案合法性/题型一致性核对。

抗干扰检查维度（每题逐选项打标签）
--------------------------------
- retrieval_fail   : 题目给了 gold doc_ids，但最终证据卡里没有任何一张来自 gold 文档（检索失败）。
- option_uncovered : 某选项没有任何与之相关的证据卡（related_options 为空）——多选漏选/串用高风险。
- numeric_miss     : 选项里的关键数字（年份/金额/比例/条款号）在任何证据 quote 里都找不到（口径偷换无法核验）。
- negation_miss    : 选项含否定/例外/阈值方向词（但/除外/不得/不超过…），但证据 quote 里无任何保护词
                     （否定/例外条款漏读高风险）。
- subject_miss     : 选项里抽到的结构化主体（公司/产品/法规名）在证据里找不到（主体偷换无法识别）。

--judge 额外维度（调用 Qwen）
----------------------------
- json_error       : 模型未返回可用 judgements。
- multi_supported_gap: 多选最终答案与 supported 判断集合不一致（漏选/错选信号）。
- 输出每题 final_answer / gold(answer.csv) / judgements / evidence_ids / answer_review。

用法
----
    # 离线 smoke（默认 5 题/领域采样，零 token）
    python script/evaluate_interference.py

    # 指定领域、扩大样本
    python script/evaluate_interference.py --domain regulatory --limit 10

    # 指定题、复测 reg_a_014（离线信号）
    python script/evaluate_interference.py --question-id reg_a_014

    # 调用 Qwen 实跑判题（显式开启，受 limit 约束；会消耗 token）
    python script/evaluate_interference.py --question-id reg_a_014 --judge --repeat 5

    # 与现有 answer.csv 交叉核对答案合法性
    python script/evaluate_interference.py --cross-check
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

# Windows 控制台默认 GBK，统一切到 UTF-8，避免中文/符号打印崩溃。
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

from agent.bm25_index import BM25Index  # noqa: E402
from agent.config import settings  # noqa: E402
from agent.evidence_selector import ALeaderboardEvidenceSelector, protected_terms_hit  # noqa: E402
from agent.hybrid_retriever import HybridRetriever  # noqa: E402
from agent.numeric_checker import extract_numeric_facts  # noqa: E402

DOMAINS = ["regulatory", "insurance", "financial_reports", "financial_contracts", "research"]

NEGATION_OPTION_TERMS = [
    "但", "但是", "除外", "不承担", "不负责赔偿", "不适用", "不在此限", "不超过", "不低于",
    "不少于", "以上", "以下", "另有约定", "另有规定", "应当", "不得", "须经", "无需",
    "仅", "只需", "立即", "至少", "不会", "无须",
]


def norm(text: str) -> str:
    return re.sub(r"\s+", "", str(text)).lower()


def load_questions(domain: str | None) -> list[dict]:
    questions: list[dict] = []
    for path in sorted((settings.questions_dir / "group_a").glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        questions.extend(item for item in data if item.get("split") == "A")
    if domain:
        questions = [q for q in questions if q.get("domain") == domain]
    questions.sort(key=lambda item: item.get("qid", ""))
    return questions


def load_answer_csv() -> dict[str, str]:
    answers: dict[str, str] = {}
    if not settings.answer_path.exists():
        return answers
    with settings.answer_path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            qid = row.get("qid", "")
            if qid and qid != "summary":
                answers[qid] = row.get("answer", "")
    return answers


def sample_questions(questions: list[dict], domain: str | None, limit: int) -> list[dict]:
    """默认 smoke：每领域采样前 limit 题；指定 domain 时只对该领域取前 limit。"""
    if domain:
        return questions[:limit] if limit else questions
    by_domain: dict[str, list[dict]] = defaultdict(list)
    for q in questions:
        by_domain[q.get("domain", "")].append(q)
    sampled: list[dict] = []
    for dom in DOMAINS:
        sampled.extend(by_domain.get(dom, [])[:limit])
    return sampled


# ----------------------------------------------------------------------------
# 离线抗干扰信号
# ----------------------------------------------------------------------------

def option_facts(option_text: str) -> dict[str, list[str]]:
    facts = extract_numeric_facts(option_text)
    return {
        "years": facts["years"],
        "money": [norm(m) for m in facts["money"]],
        "percents": [norm(p) for p in facts["percents"]],
        "clauses": [norm(c) for c in facts["clauses"]],
    }


# 高置信主体：法规/产品/公司专名，过滤 extract_subjects 抽到的动词短语噪声。
_HIGH_CONF_SUBJECT_RE = re.compile(
    r"《[^》]{2,60}》|[\u4e00-\u9fffA-Za-z0-9]{2,40}(?:股份有限公司|有限公司|年金保险|人寿保险|公司债券|可转换公司债券)"
    r"|[\u4e00-\u9fff]{2,40}(?:管理办法|实施细则|条例|指引|准则)"
)
# 计算/假设题：数字来自题干假设而非文档，numeric_miss 在这类题上是噪声。
_HYPOTHETICAL_RE = re.compile(r"假设|试算|累计所交|退保所得|赔付多少|金额排序|从高到低|合计.*万")


def high_conf_subjects(text: str) -> list[str]:
    return [m.strip("《》") for m in _HIGH_CONF_SUBJECT_RE.findall(text)]


def analyze_offline(question: dict, selector: ALeaderboardEvidenceSelector) -> dict:
    qid = str(question["qid"])
    domain = question.get("domain")
    qtext = str(question.get("question", ""))
    options = {str(k): str(v) for k, v in (question.get("options") or {}).items() if str(k) in "ABCD"}
    gold_docs = [str(d) for d in (question.get("doc_ids") or [])]
    hypothetical = bool(_HYPOTHETICAL_RE.search(qtext))

    cards, per_option = selector.select(qtext, options, domain, gold_docs or None)

    card_docs = {str(c.get("doc_id", "")) for c in cards}
    quote_blob = norm(" ".join(str(c.get("quote", "")) + " " + str(c.get("source_text", "")) for c in cards))

    # 检索失败：gold doc 一个都没进证据。gold doc_id 需先经索引归一化解析
    #（题目给 csrc_0023_att1，索引内是 strict_csrc_0023_att1），否则全是假阳性。
    resolved_gold = set()
    if gold_docs:
        try:
            resolved_gold = selector.retriever.index.resolve_doc_ids(gold_docs)
        except Exception:  # noqa: BLE001
            resolved_gold = set(gold_docs)
    gold_doc_hit = bool(resolved_gold & card_docs) if gold_docs else True

    per_option_flags: dict[str, list[str]] = {}
    flag_counter: Counter = Counter()
    for letter, text in sorted(options.items()):
        flags: list[str] = []
        related = per_option.get(letter, [])
        if not related:
            flags.append("option_uncovered")

        # numeric_miss：跳过计算/假设题（数字来自题干假设，不应在文档中出现）
        if not hypothetical:
            facts = option_facts(text)
            numeric_terms = facts["years"] + facts["money"] + facts["percents"] + facts["clauses"]
            if numeric_terms and not any(term in quote_blob for term in numeric_terms):
                flags.append("numeric_miss")

        neg_in_option = [t for t in NEGATION_OPTION_TERMS if t in text]
        if neg_in_option:
            protect_hit = any(protected_terms_hit(str(c.get("quote", "")), domain) for c in cards)
            if not protect_hit:
                flags.append("negation_miss")

        # subject_miss：只看高置信专名（法规/公司/产品），避免动词短语噪声
        subjects = high_conf_subjects(text)
        if subjects and not any(norm(s) in quote_blob for s in subjects):
            flags.append("subject_miss")

        per_option_flags[letter] = flags
        for flag in flags:
            flag_counter[flag] += 1

    if not gold_doc_hit:
        flag_counter["retrieval_fail"] += 1

    return {
        "qid": qid,
        "domain": domain,
        "answer_format": question.get("answer_format", ""),
        "gold_docs": gold_docs,
        "gold_doc_hit": gold_doc_hit,
        "hypothetical": hypothetical,
        "evidence_count": len(cards),
        "evidence_doc_count": len(card_docs),
        "per_option_flags": per_option_flags,
        "flags": dict(flag_counter),
    }


def cross_check_answer(question: dict, answer: str) -> list[str]:
    """与现有 answer.csv 的答案做题型/合法性核对（不判对错，只挑高风险）。"""
    warnings: list[str] = []
    fmt = question.get("answer_format", "mcq")
    options = {str(k) for k in (question.get("options") or {}).keys() if str(k) in "ABCD"}
    letters = re.findall(r"[A-D]", (answer or "").upper())
    illegal = [c for c in letters if c not in options]
    if illegal:
        warnings.append(f"答案含非本题选项 {illegal}")
    if fmt in {"mcq", "tf"} and len(letters) != 1:
        warnings.append(f"{fmt} 题应恰好一个字母，实际 {letters}")
    if fmt == "multi" and not letters:
        warnings.append("multi 题答案为空")
    if fmt == "multi" and len(set(letters)) == len(options) and len(options) >= 4:
        warnings.append("multi 题选满全部选项（ABCD），可能错选/过度选")
    return warnings


# ----------------------------------------------------------------------------
# 在线判题（显式开启）
# ----------------------------------------------------------------------------

def analyze_judge(question: dict, retriever, repeat: int, gold_answer: str) -> dict:
    from agent.a_leaderboard_agent import ALeaderboardAgent
    from agent.qwen_client import QwenClient
    from agent.token_counter import TokenUsageTracker

    qid = str(question["qid"])
    runs: list[dict] = []
    answers: list[str] = []
    tracker = TokenUsageTracker(settings.token_log_path)
    for i in range(max(1, repeat)):
        qwen = QwenClient()
        agent = ALeaderboardAgent(retriever, qwen, tracker)
        result = agent.answer_question(question)
        judgements = result.get("judgements", {})
        supported = {l for l, v in judgements.items() if (v or {}).get("status") == "supported"}
        ans_letters = set(re.findall(r"[A-D]", (result.get("final_answer", "") or "").upper()))
        flags: list[str] = []
        if not judgements:
            flags.append("json_error")
        if question.get("answer_format") == "multi" and supported != ans_letters:
            flags.append("multi_supported_gap")
        runs.append({
            "run": i,
            "final_answer": result.get("final_answer", ""),
            "judgements": {l: {"status": (v or {}).get("status"), "confidence": (v or {}).get("confidence"), "evidence_ids": (v or {}).get("evidence_ids", [])} for l, v in judgements.items()},
            "evidence_ids": [c.get("evidence_id") for c in result.get("evidence_cards", [])],
            "answer_review": result.get("answer_review", {}),
            "validation_status": result.get("validation_status", ""),
            "fallback_reason": result.get("fallback_reason", ""),
            "flags": flags,
            "tokens": result.get("token_usage", {}).get("total_tokens", 0),
        })
        answers.append(result.get("final_answer", ""))
    stable = len(set(answers)) == 1
    return {
        "qid": qid,
        "domain": question.get("domain"),
        "gold_answer": gold_answer,
        "answers": answers,
        "stable": stable,
        "match_gold": all(a == gold_answer for a in answers) if gold_answer else None,
        "runs": runs,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="抗干扰评测（默认离线零 token）")
    parser.add_argument("--domain", default="", help="只评测某领域")
    parser.add_argument("--question-id", default="", help="只评测某题（可逗号分隔多题）")
    parser.add_argument("--limit", type=int, default=5, help="每领域采样题数（smoke 默认 5）")
    parser.add_argument("--judge", action="store_true", help="显式调用 Qwen 实跑判题（消耗 token）")
    parser.add_argument("--repeat", type=int, default=1, help="--judge 下每题重复次数（验稳定性）")
    parser.add_argument("--cross-check", action="store_true", help="与现有 answer.csv 做答案合法性核对")
    parser.add_argument("--out", default="", help="把详细结果写入 JSON 路径")
    args = parser.parse_args()

    if not settings.index_path.exists():
        raise FileNotFoundError(f"BM25 index not found: {settings.index_path}. 先运行 python script/build_index.py")

    questions = load_questions(args.domain or None)
    if args.question_id:
        wanted = {q.strip() for q in args.question_id.split(",") if q.strip()}
        questions = [q for q in questions if q.get("qid") in wanted]
    else:
        questions = sample_questions(questions, args.domain or None, args.limit)

    if not questions:
        print("没有匹配的题目。")
        return

    index = BM25Index.load(settings.index_path)
    retriever = HybridRetriever(index, rrf_k=settings.rrf_k)
    if hasattr(retriever, "warmup"):
        retriever.warmup()
    selector = ALeaderboardEvidenceSelector(retriever)
    answer_csv = load_answer_csv()

    print(f"模式={'JUDGE(消耗token)' if args.judge else '离线零token'} 题数={len(questions)} chunks={len(index.chunks)}")
    print("=" * 88)

    offline_results: list[dict] = []
    judge_results: list[dict] = []
    flag_totals: Counter = Counter()
    domain_flags: dict[str, Counter] = defaultdict(Counter)

    for q in questions:
        qid = q["qid"]
        gold = answer_csv.get(qid, "")
        diag = analyze_offline(q, selector)
        offline_results.append(diag)
        for flag, count in diag["flags"].items():
            flag_totals[flag] += count
            domain_flags[diag["domain"]][flag] += count

        flagged_opts = {l: f for l, f in diag["per_option_flags"].items() if f}
        cc = cross_check_answer(q, gold) if args.cross_check else []
        line = (
            f"{qid:12s} {diag['domain']:18s} fmt={diag['answer_format']:5s} "
            f"gold={gold or '-':5s} docs_hit={'Y' if diag['gold_doc_hit'] else 'N'} "
            f"evi={diag['evidence_count']:2d}/{diag['evidence_doc_count']}doc "
            f"flags={diag['flags'] or '-'}"
        )
        print(line)
        if flagged_opts:
            print(f"             逐选项: {flagged_opts}")
        if cc:
            print(f"             [!] 答案核对: {cc}")

        if args.judge:
            jr = analyze_judge(q, retriever, args.repeat, gold)
            judge_results.append(jr)
            stable_mark = "稳定" if jr["stable"] else "不稳定"
            match = ("命中gold" if jr["match_gold"] else "!=gold") if gold else "无gold"
            tokens = sum(r["tokens"] for r in jr["runs"])
            print(f"             >> JUDGE answers={jr['answers']} {stable_mark} {match} tokens={tokens}")
            for r in jr["runs"]:
                if r["flags"]:
                    print(f"               run{r['run']} flags={r['flags']} review={r['answer_review'].get('warnings', [])}")

    print("=" * 88)
    print("== 离线抗干扰失败类型汇总 ==")
    for flag, count in flag_totals.most_common():
        print(f"  {flag:18s} {count}")
    print("\n== 分领域 ==")
    for dom, counter in domain_flags.items():
        print(f"  {dom:18s} {dict(counter)}")

    if args.judge and judge_results:
        stable_n = sum(1 for r in judge_results if r["stable"])
        match_n = sum(1 for r in judge_results if r["match_gold"])
        print(f"\n== JUDGE 汇总 == 稳定 {stable_n}/{len(judge_results)} 命中gold {match_n}/{len(judge_results)}")

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps({"offline": offline_results, "judge": judge_results}, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nwrote {out_path}")


if __name__ == "__main__":
    main()
