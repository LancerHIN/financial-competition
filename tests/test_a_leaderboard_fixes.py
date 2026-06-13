"""单测覆盖本轮 A 榜主流程修复点。

覆盖：
- validator retry 独立触发（不依赖 multi rescue）
- 局部重判不覆盖首轮 supported 选项
- 多选无 supported 不回退 A（返回空/信号兜底 + needs_review）
- query_select 传 question
- make_targeted_query 不截掉后部关键数字
- evidence_id 重映射、judgement merge
- normalize_answer 无字母不返回 A
- tf 只允许 A/B
- answer_review 结构化 codes
- insurance_payout 保守化
- config flag 接入/标注
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent import a_leaderboard_agent as agent_mod  # noqa: E402
from agent.a_leaderboard_agent import (  # noqa: E402
    ALeaderboardAgent,
    merge_judgements,
    remap_judgement_evidence_ids,
)
from agent.answer_normalizer import (  # noqa: E402
    domain_answer_review,
    legalize_answer,
    normalize_answer,
    synthesize_answer,
)
from agent.evidence_validator import _retry_queries, validate_cards  # noqa: E402
from agent.evidence_selector import build_queries, _split_fact_clauses  # noqa: E402
from agent.numeric_checker import insurance_payout, make_targeted_query  # noqa: E402
from agent.prompts import a_leaderboard_option_judge_prompt, _doc_ordinal_map  # noqa: E402
from agent.answer_normalizer import _reason_contradicts_supported  # noqa: E402


# ---- normalize_answer：无有效字母不返回 A（item8）----

def test_normalize_answer_no_default_a():
    assert normalize_answer("", "mcq") == ""
    assert normalize_answer("无", "mcq") == ""
    assert normalize_answer("", "multi") == ""
    assert normalize_answer(None, "tf") == ""


def test_tf_question_adds_subclause_queries():
    # tf 题题干含多个独立事实点时，应把题干切成子句作为额外 global 子查询，
    # 提高每个事实点（尤其弱信号子事实，如“2019年起回购”）的召回覆盖。
    q = "比亚迪 2025 年研发投入占营业收入比例较 2024 年有所提升，且美的集团自 2019 年起连续实施了股份回购方案。该说法是否正确？"
    options = {"A": "正确", "B": "错误"}
    tf_queries = build_queries(q, options, domain="financial_reports", answer_format="tf")["global"]
    mcq_queries = build_queries(q, options, domain="financial_reports", answer_format="mcq")["global"]
    # tf 应产生针对“美的回购”子句的独立 query
    assert any("回购" in s and "2019" in s for s in tf_queries)
    # 非 tf 题不应注入子句（query 数不增加）
    assert len(tf_queries) > len(mcq_queries)


def test_split_fact_clauses_single_fact_returns_empty():
    # 单一事实的 tf 题不切分，避免引入噪声子查询。
    assert _split_fact_clauses("研发投入金额为 100 亿元。该说法是否正确？") == []


# ---- Fix#1：doc 序号映射（doc_ordinal_unmapped）----

def test_doc_ordinal_map_follows_doc_ids_order():
    # doc_ids 列表顺序即“第一份/第二份…”的金标准序号。
    assert _doc_ordinal_map(["text01", "text14"]) == {"text01": 1, "text14": 2}
    assert _doc_ordinal_map(["text04", "text09", "text11"]) == {"text04": 1, "text09": 2, "text11": 3}
    assert _doc_ordinal_map(None) == {}
    assert _doc_ordinal_map(["only_one"]) == {"only_one": 1}  # 映射照建，但单文档不在 prompt 给出序号块


def test_judge_prompt_injects_doc_ordinal_block_and_card_field():
    # 多文档时，judge prompt 必须显式给出“第N份文档=doc_id”映射，且证据卡带 doc_ordinal。
    cards = [
        {"evidence_id": "E1", "doc_id": "text01", "quote": "发行规模不超过10亿元", "related_options": ["A"]},
        {"evidence_id": "E2", "doc_id": "text14", "quote": "本期发行金额不超过20亿元", "related_options": ["B"]},
    ]
    messages = a_leaderboard_option_judge_prompt(
        "对比两份文档发行金额", {"A": "a", "B": "b"}, "multi", cards,
        doc_ids=["text01", "text14"],
    )
    user = messages[-1]["content"]
    assert "第一份" in user and "text01" in user
    assert "第二份" in user and "text14" in user
    assert "文档序号映射" in user
    # 卡片内应注入 doc_ordinal
    assert '"doc_ordinal": 1' in user or '"doc_ordinal":1' in user


def test_judge_prompt_no_ordinal_block_for_single_doc():
    cards = [{"evidence_id": "E1", "doc_id": "d1", "quote": "x", "related_options": ["A"]}]
    messages = a_leaderboard_option_judge_prompt(
        "单文档题", {"A": "a", "B": "b"}, "mcq", cards, doc_ids=["d1"],
    )
    assert "文档序号映射" not in messages[-1]["content"]


def test_judge_prompt_no_ordinal_block_when_docs_referenced_by_name():
    # 多文档但题干用产品名/文档名指代（非“第N份”），不应注入序号块，避免噪声（ins_a_010 教训）。
    cards = [
        {"evidence_id": "E1", "doc_id": "1", "quote": "现金价值公式A", "related_options": ["A"]},
        {"evidence_id": "E2", "doc_id": "2", "quote": "现金价值公式B", "related_options": ["B"]},
    ]
    messages = a_leaderboard_option_judge_prompt(
        "关于现金价值的表述，下列哪些产品的条款中明确给出了具体的计算方法或公式？",
        {"A": "平安智盈金生", "B": "国寿增益宝"}, "multi", cards, doc_ids=["1", "2"],
    )
    user = messages[-1]["content"]
    assert "文档序号映射" not in user
    assert '"doc_ordinal"' not in user


def test_judge_prompt_injects_ordinal_when_only_options_use_ordinal():
    # 序号指代出现在【选项】而非题干时（fc_a_020 类），仍必须注入文档序号映射，
    # 否则 judge 只能猜文档归属（doc_ordinal_in_options bug）。
    cards = [
        {"evidence_id": "E1", "doc_id": "text04", "quote": "股票代码300866", "related_options": ["C"]},
        {"evidence_id": "E2", "doc_id": "text11", "quote": "初始转股价格为18.26元/股", "related_options": ["A"]},
    ]
    messages = a_leaderboard_option_judge_prompt(
        "依据文档内容，关于可转换债券的初始转股价格及发行人信息，正确的陈述有？",
        {
            "A": "第二份文档披露的初始转股价格为18.26元/股",
            "B": "第一份文档显示的发行人股票简称是安克创新",
            "C": "第二份文档显示的发行人股票代码为300866",
            "D": "第二份文档声明初始转股价格不低于募集说明书公告之日的市场价格",
        },
        "multi", cards, doc_ids=["text04", "text11"],
    )
    user = messages[-1]["content"]
    assert "文档序号映射" in user
    assert "第一份" in user and "text04" in user
    assert "第二份" in user and "text11" in user


# ---- Fix#2：判题理由↔结论一致性（judge_reason_conflict）----

def test_reason_contradicts_supported_detects_conflict():
    # supported 但 reason 自我否定（张冠李戴/选项错/与证据矛盾）应被识别为冲突。
    assert _reason_contradicts_supported("证据原文是博通预测，这是张冠李戴，因此选项C错")
    assert _reason_contradicts_supported("该选项与证据矛盾，应判refuted")
    assert _reason_contradicts_supported("证据不支持该表述")
    # 正常论证不应误判为冲突。
    assert not _reason_contradicts_supported("证据E1明确记载该数值，与选项一致，成立")
    assert not _reason_contradicts_supported("虽未直接点名，但结合上下文可确认，支持该选项")


def test_domain_review_flags_judge_reason_conflict():
    # final_answer 含某 supported 选项，但其 reason 自相矛盾 → 触发 judge_reason_conflict。
    judgements = {
        "A": {"status": "supported", "evidence_ids": ["E1"], "reason": "证据一致，成立"},
        "C": {"status": "supported", "evidence_ids": ["E5"], "reason": "这是张冠李戴，选项C错"},
    }
    review = domain_answer_review("AC", "multi", {"A": "a", "B": "b", "C": "c", "D": "d"}, "research", judgements)
    assert "judge_reason_conflict" in review["codes"]


def test_downgrade_self_refuting_supported_to_refuted():
    from agent.answer_normalizer import downgrade_self_refuting_judgements
    judgements = {
        "A": {"status": "supported", "evidence_ids": ["E1"], "reason": "证据明确支持，与选项一致"},
        "C": {"status": "supported", "evidence_ids": ["E5"], "reason": "证据是博通预测，安到芯原是张冠李戴"},
        "B": {"status": "supported", "evidence_ids": ["E2"], "reason": "8.99%>3.6%，应判refuted"},
    }
    out = downgrade_self_refuting_judgements(judgements)
    assert out["A"]["status"] == "supported"          # 正常 supported 不动
    assert out["C"]["status"] == "refuted" and out["C"]["downgraded_self_refute"]
    assert out["B"]["status"] == "refuted" and out["B"]["downgraded_self_refute"]
    # 保留证据与原 reason
    assert out["C"]["evidence_ids"] == ["E5"]


def test_normalize_answer_tf_only_ab():
    # tf 不允许 C/D 兜底扩张进来（应修6）
    assert normalize_answer("C", "tf") == ""
    assert normalize_answer("A", "tf") == "A"
    assert normalize_answer("B", "tf") == "B"


def test_legalize_answer_gives_legal_placeholder():
    assert legalize_answer("", "mcq", {"A": "a", "B": "b"}) == "A"
    assert legalize_answer("", "tf", {"A": "对", "B": "错"}) == "A"
    assert legalize_answer("B", "mcq", {"A": "a", "B": "b"}) == "B"


# ---- 多选无 supported 不回退 A（item9/item10）----

def test_multi_no_supported_no_evidence_returns_empty_needs_review():
    judgements = {l: {"status": "insufficient", "confidence": 0.0, "evidence_ids": []} for l in "ABCD"}
    result = synthesize_answer(judgements, "multi", {l: l for l in "ABCD"}, model_answer="", evidence_cards=[])
    assert result["answer"] == ""  # 不静默猜 A
    assert result["needs_review"] is True
    assert result["fallback_used"] is True


def test_multi_no_supported_does_not_guess_from_model_answer():
    # 全 insufficient 且各选项无自身 evidence_ids：即使 model_answer 与证据覆盖存在，
    # 也不靠 model_answer 反推（point 6 保守化，修复 ins_a_009 类“高置信 insufficient 被选中”）。
    judgements = {l: {"status": "insufficient", "confidence": 0.9, "evidence_ids": []} for l in "ABCD"}
    cards = [{"related_options": ["A"]}, {"related_options": ["C"]}]
    result = synthesize_answer(judgements, "multi", {l: l for l in "ABCD"}, model_answer="AC", evidence_cards=cards)
    assert result["answer"] == ""  # 不猜，交 legalize
    assert result["needs_review"] is True


def test_multi_no_supported_keeps_option_with_own_evidence():
    # insufficient 但该选项【有自身引用证据且被证据卡覆盖、未被反证】：可保守保留为候选。
    judgements = {
        "A": {"status": "insufficient", "confidence": 0.5, "evidence_ids": ["E1"]},
        "B": {"status": "refuted", "confidence": 0.9, "evidence_ids": ["E2"]},
        "C": {"status": "insufficient", "confidence": 0.9, "evidence_ids": []},  # 无自身证据 -> 不入选
        "D": {"status": "insufficient", "confidence": 0.9, "evidence_ids": []},
    }
    cards = [{"related_options": ["A"]}]
    result = synthesize_answer(judgements, "multi", {l: l for l in "ABCD"}, model_answer="ACD", evidence_cards=cards)
    # 只有 A 满足“有自身证据 + 覆盖 + 非refuted”；C/D 无证据被排除，B 被反证
    assert result["answer"] == "A"
    assert result["needs_review"] is True


def test_mcq_no_supported_no_signal_returns_empty():
    judgements = {l: {"status": "insufficient", "confidence": 0.0, "evidence_ids": []} for l in "ABCD"}
    result = synthesize_answer(judgements, "mcq", {l: l for l in "ABCD"}, evidence_cards=[])
    assert result["answer"] == ""
    assert result["needs_review"] is True


def test_mcq_no_supported_uses_refuted_distribution():
    # A/B/C 被 refuted，D insufficient 且有证据覆盖 -> 选 D（不猜 A）
    judgements = {
        "A": {"status": "refuted", "confidence": 0.8, "evidence_ids": ["E1"]},
        "B": {"status": "refuted", "confidence": 0.8, "evidence_ids": ["E2"]},
        "C": {"status": "refuted", "confidence": 0.8, "evidence_ids": ["E3"]},
        "D": {"status": "insufficient", "confidence": 0.3, "evidence_ids": ["E4"]},
    }
    cards = [{"related_options": ["D"]}]
    result = synthesize_answer(judgements, "mcq", {l: l for l in "ABCD"}, evidence_cards=cards)
    assert result["answer"] == "D"


# ---- make_targeted_query 不截掉后部关键数字（item13）----

def test_make_targeted_query_keeps_option_numbers():
    question = (
        "根据该办法关于登记报告时限的较长题干，涉及 2020 年 2021 年 2022 年 2023 年 5 个工作日 "
        "第一条 第二条 第三条 第四条 营业收入 净利润 等众多事项"
    )
    option = "应当在知悉之日起 30 个工作日内向派出机构报告"
    query = make_targeted_query(question, option)
    # 选项里的关键数字 30 必须出现在 query（不能被长题干前 4 个挤掉）
    assert "30" in query


def test_make_targeted_query_option_facts_prioritized():
    question = "一段很长的题干 第一条 第二条 第三条 第四条 第五条"
    option = "比例不低于 70% 第九十九条"
    query = make_targeted_query(question, option)
    assert "70%" in query
    assert "第九十九条" in query


# ---- insurance_payout 保守化（item15）----

def test_insurance_payout_requires_maxmin_context():
    # 无 max/min 触发词的 context -> None
    assert insurance_payout(["10万元", "8万元"], "max", context="给付身故保险金") is None
    # 有较大者 -> 计算
    payout = insurance_payout(["10万元", "8万元"], "max", context="取较大者给付")
    assert payout and payout["result"] == 100000.0


def test_insurance_payout_complex_formula_returns_none():
    # 含倍数/比例/年龄等复杂公式信号 -> None，不硬算
    assert insurance_payout(["10万元", "8万元"], "max", context="较大者 按基本保额的2倍给付") is None
    assert insurance_payout(["10万元", "8万元"], "min", context="较小者 按缴费年限分段") is None


def test_insurance_payout_single_amount_returns_none():
    assert insurance_payout(["10万元"], "max", context="取较大者") is None


# ---- validator retry 扩展类型（item14/应修4）----

def test_retry_queries_covers_protect_words_and_money():
    # 题目含金额与保护词，证据缺失 -> 派生补检索 query
    queries = _retry_queries(
        "financial_contracts",
        "本期债券发行金额不超过 10 亿元",
        "发行规模不超过 10 亿元",
        "本产品为公司债券。",
    )
    joined = " ".join(queries)
    assert any("不超过" in q for q in queries) or "10亿元" in joined.replace(" ", "")


def test_retry_queries_independent_of_domain_research():
    # 缺年份独立派生
    queries = _retry_queries("financial_reports", "2025 年营业收入", "营业收入增长", "本报告期数据。")
    assert any("2025" in q for q in queries)


# ---- query_select 传 question（item3）----

def test_query_select_passes_question_to_merge():
    captured = {}

    class FakeRetriever:
        def hybrid_search(self, query, **kwargs):
            return [{"chunk_id": "c1", "doc_id": "d1", "text": "2025年营业收入777亿元", "score": 1.0}]

    from agent.evidence_selector import ALeaderboardEvidenceSelector

    selector = ALeaderboardEvidenceSelector(FakeRetriever())
    orig_merge = selector._merge

    def spy_merge(merged, chunk, source_option, query, question, options, domain):
        captured["question"] = question
        return orig_merge(merged, chunk, source_option, query, question, options, domain)

    selector._merge = spy_merge
    selector.query_select(["2025 营业收入"], {"A": "营收增长"}, "financial_reports", ["d1"], question="2025年营收是否增长")
    assert captured.get("question") == "2025年营收是否增长"


# ---- evidence_id 重映射 + judgement merge（应修1/2）----

def test_remap_evidence_ids_after_reindex():
    old_cards = [
        {"evidence_id": "E1", "doc_id": "d1", "chunk_id": "c1", "quote": "q1"},
        {"evidence_id": "E2", "doc_id": "d1", "chunk_id": "c2", "quote": "q2"},
    ]
    # 合并后顺序变化，c2 变成 E1
    merged_cards = [
        {"evidence_id": "E1", "doc_id": "d1", "chunk_id": "c2", "quote": "q2"},
        {"evidence_id": "E2", "doc_id": "d1", "chunk_id": "c1", "quote": "q1"},
    ]
    judgements = {"A": {"status": "supported", "confidence": 0.9, "evidence_ids": ["E2"]}}
    remapped = remap_judgement_evidence_ids(judgements, old_cards, merged_cards)
    # 原 E2(c2) 现在是 E1
    assert remapped["A"]["evidence_ids"] == ["E1"]
    assert remapped["A"]["status"] == "supported"


def test_remap_drops_missing_evidence_and_demotes():
    old_cards = [{"evidence_id": "E1", "doc_id": "d1", "chunk_id": "c1", "quote": "q1"}]
    merged_cards = [{"evidence_id": "E1", "doc_id": "d2", "chunk_id": "cX", "quote": "qX"}]
    judgements = {"A": {"status": "supported", "confidence": 0.9, "evidence_ids": ["E1"]}}
    remapped = remap_judgement_evidence_ids(judgements, old_cards, merged_cards)
    assert remapped["A"]["evidence_ids"] == []
    assert remapped["A"]["status"] == "insufficient"  # 失去证据退回 insufficient


def test_merge_judgements_only_overrides_focus():
    base = {
        "A": {"status": "supported", "confidence": 0.9, "evidence_ids": ["E1"]},
        "B": {"status": "insufficient", "confidence": 0.0, "evidence_ids": []},
    }
    updated = {
        "A": {"status": "refuted", "confidence": 0.5, "evidence_ids": ["E2"]},
        "B": {"status": "supported", "confidence": 0.8, "evidence_ids": ["E3"]},
    }
    merged = merge_judgements(base, updated, focus_letters=["B"])
    # A 不在 focus，保留首轮 supported
    assert merged["A"]["status"] == "supported"
    assert merged["A"]["evidence_ids"] == ["E1"]
    # B 在 focus，被更新
    assert merged["B"]["status"] == "supported"


# ---- answer_review 结构化 codes（应修3）----

def test_answer_review_machine_codes_over_select():
    options = {l: l for l in "ABCD"}
    judgements = {l: {"status": "supported"} for l in "ABCD"}
    review = domain_answer_review("ABCD", "multi", options, "regulatory", judgements)
    assert "over_select" in review["codes"]
    assert review["needs_review"]


def test_answer_review_codes_uncovered_and_missing():
    options = {l: l for l in "ABCD"}
    judgements = {"A": {"status": "supported"}, "C": {"status": "supported"}}
    cards = [{"related_options": ["A"]}, {"related_options": ["C"]}]
    # 答案选 AB，B 无证据卡且非 supported；C supported 但漏选
    review = domain_answer_review("AB", "multi", options, "regulatory", judgements, evidence_cards=cards)
    assert "uncovered_answer_letter" in review["codes"]
    assert "unsupported_answer_letter" in review["codes"]
    assert "missing_supported" in review["codes"]


def test_answer_review_regulatory_no_clause_code():
    options = {"A": "a", "B": "b"}
    judgements = {"A": {"status": "supported"}}
    review = domain_answer_review("A", "tf", options, "regulatory", judgements, evidence_cards=[{"quote": "无条文号文字"}])
    assert "no_clause_for_regulatory" in review["codes"]


# ---- 端到端：validator retry 独立触发 + 局部重判保留 supported（item1/2/4/5/6/11）----

class _StubTracker:
    def record(self, *a, **k):
        pass

    def get(self, qid):
        return {}


class _StubRetriever:
    def hybrid_search(self, query, **kwargs):
        return []


def _make_agent(judge_responses):
    """judge_responses: list of (judgements_dict, final_answer) consumed per _judge call."""
    calls = {"i": 0, "focus": []}

    class StubQwen:
        def chat_json(self, messages, purpose="json"):
            idx = calls["i"]
            calls["i"] += 1
            # 记录本次 judge 的 focus（从 prompt user 内容粗略判断）
            data = judge_responses[min(idx, len(judge_responses) - 1)]
            return {"judgements": data[0], "final_answer": data[1]}, {"total_tokens": 10}

    agent = ALeaderboardAgent(_StubRetriever(), StubQwen(), _StubTracker())
    return agent, calls


def test_validator_retry_triggers_supplement_independently(monkeypatch):
    """模型首轮把所有选项都判 supported，但 validator 缺条文号 -> retry_queries 非空，
    必须独立触发补检索 + 局部重判（不依赖 multi rescue）。"""

    # 第一轮 select：给一张无条文号证据；retry/targeted 检索给带条文号的证据
    select_cards = [{"evidence_id": "E1", "doc_id": "d1", "chunk_id": "c1", "quote": "相关说明无条文号", "related_options": ["A"], "source_text": "相关说明无条文号"}]
    targeted_cards = [{"evidence_id": "E1", "doc_id": "d1", "chunk_id": "c2", "quote": "第十二条 应当在5个工作日内报告", "related_options": ["A", "B"], "source_text": "第十二条 应当在5个工作日内报告"}]

    def fake_select(self, question, options, domain, doc_ids, **kw):
        return ([dict(c) for c in select_cards], {"A": [dict(select_cards[0])], "B": []})

    def fake_targeted(self, question, options, letters, domain, doc_ids, **kw):
        return ([dict(c) for c in targeted_cards], {l: [] for l in options})

    def fake_query_select(self, queries, options, domain, doc_ids, top_k=6, question=""):
        assert question, "query_select 必须收到非空 question"
        return ([dict(c) for c in targeted_cards], {l: [] for l in options})

    from agent.evidence_selector import ALeaderboardEvidenceSelector

    monkeypatch.setattr(ALeaderboardEvidenceSelector, "select", fake_select)
    monkeypatch.setattr(ALeaderboardEvidenceSelector, "targeted_select", fake_targeted)
    monkeypatch.setattr(ALeaderboardEvidenceSelector, "query_select", fake_query_select)

    # round0: A/B 都 supported（错误地，缺条文号）；round1 局部重判：B 退回 insufficient
    round0 = ({
        "A": {"status": "supported", "confidence": 0.9, "evidence_ids": ["E1"]},
        "B": {"status": "supported", "confidence": 0.4, "evidence_ids": ["E1"]},
    }, "AB")
    round1 = ({
        "B": {"status": "insufficient", "confidence": 0.2, "evidence_ids": []},
    }, "")
    agent, calls = _make_agent([round0, round1])

    item = {
        "qid": "t1", "domain": "regulatory", "answer_format": "multi",
        "question": "根据第十二条规定，下列时限说法正确的有哪些？",
        "options": {"A": "应当在5个工作日内报告", "B": "应当在30个工作日内报告"},
        "doc_ids": ["d1"],
    }
    result = agent.answer_question(item)
    # 触发了第二轮（validator retry 独立触发）
    assert calls["i"] >= 2
    assert result["validation_retry_active"] is True
    # 首轮高置信 supported 的 A 被保留
    assert result["judgements"]["A"]["status"] == "supported"


def test_make_computed_hints_accepts_domain():
    from agent.numeric_checker import make_computed_hints

    hints = make_computed_hints("阈值题 不超过 5%", {"A": "比例为4%不超过5%"}, [{"quote": "比例4% 5%", "related_options": ["A"]}], domain="regulatory")
    # regulatory 阈值方向只提示按原文核对，不直接下"符合/不符合"结论
    joined = " ".join(hints)
    assert "符合" not in joined or "按证据原文" in joined


def test_choose_budget_returns_baseline_values():
    from agent.evidence_selector import choose_retrieval_budget

    single = choose_retrieval_budget("某事项表决方式", {"A": "x", "B": "y", "C": "z", "D": "w"}, "regulatory", ["d1"], "mcq")
    cross = choose_retrieval_budget("对比两份可转债条款 赎回 回售", {"A": "赎回", "B": "回售", "C": "评级", "D": "转股价"}, "financial_contracts", ["t1", "t2"], "multi")
    # 回归到经验最优基准：所有题型同一预算，card_limit 与历史默认（20）一致
    for b in (single, cross):
        assert b["final_top_k"] == 24
        assert b["card_limit"] == 20
        assert b["option_floor"] == 2


def test_choose_budget_multi_uses_baseline_depth():
    from agent.evidence_selector import choose_retrieval_budget

    b = choose_retrieval_budget("对比两份文档营业收入 净利润", {"A": "a", "B": "b", "C": "c", "D": "d"}, "financial_reports", ["b1", "b2"], "multi")
    # 多选回归基准召回深度 24（实测加深反伤多选）
    assert b["final_top_k"] == 24
    assert b["option_floor"] == 2


def test_match_options_requires_hard_fact_hit():
    from agent.evidence_selector import _match_options

    options = {
        "A": "本期债券发行金额上限为 10 亿元",
        "B": "本期债券发行金额上限为 5 亿元",
    }
    # 证据只提到 10 亿元：只应关联 A，不应因“本期/债券/发行/金额”等词命中而误关联 B
    text = "本期债券发行金额上限为人民币10亿元，由公司董事会审议通过。"
    assert _match_options(text, options) == ["A"]


def test_match_options_clause_and_indicator_gate():
    from agent.evidence_selector import _match_options

    options = {
        "A": "第十二条规定应在5个工作日内报告",
        "B": "营业收入同比增长",
    }
    text_clause = "第十二条 相关主体应当在五个工作日内向监管机构报告。"
    # 含条文号的选项：证据命中第十二条 -> 关联 A；未提营业收入 -> 不关联 B
    assert _match_options(text_clause, options) == ["A"]
    text_indicator = "报告期内公司营业收入为120亿元。"
    # 含指标名的选项 B：证据命中“营业收入” -> 关联 B；无第十二条 -> 不关联 A
    assert _match_options(text_indicator, options) == ["B"]


def test_match_options_plain_text_keeps_term_ratio():
    from agent.evidence_selector import _match_options

    # 纯文字选项（无硬事实），选项含可分词的词项；证据命中其中多数词项 -> 关联（沿用词项比例逻辑）
    options = {"A": "发行人承诺及时、公平地履行信息披露义务"}
    text = "发行人承诺将及时、公平地履行信息披露义务。"
    assert _match_options(text, options) == ["A"]


def test_match_options_money_normalization_tolerates_suffix():
    from agent.evidence_selector import _match_options, _norm_money

    # 金额核心归一：忽略 人民币/元/整 等装饰，但保留量级单位以区分 50亿 vs 5亿
    assert _norm_money("人民币50亿元") == _norm_money("50亿") == "50亿"
    assert _norm_money("5亿") != _norm_money("50亿")
    options = {"A": "本期债券发行规模为50亿元", "B": "本期债券发行规模为30亿元"}
    # 证据省略“元”仍应关联 A，且不误关联 B
    assert _match_options("发行规模为50亿。", options) == ["A"]
    assert _match_options("本期发行规模合计30亿元整。", options) == ["B"]


def test_parse_tf_claims_splits_and_operator():
    from agent.evidence_selector import parse_tf_claims

    q = "上市公司应当在股东会召开前披露董事候选人，且半年度报告内容应当经上市公司董事会审议通过后方可披露。"
    parsed = parse_tf_claims(q)
    assert parsed["operator"] == "AND"
    assert len(parsed["claims"]) == 2
    assert "股东会召开前披露董事候选人" in parsed["claims"][0]
    assert "董事会审议通过" in parsed["claims"][1]


def test_parse_tf_claims_strips_question_shell():
    from agent.evidence_selector import parse_tf_claims

    q = "根据提供的文档片段，判断以下陈述是否正确：两份文档均提及董事对真实性负责，且其中一份披露资产负债率为43.24%。"
    parsed = parse_tf_claims(q)
    assert parsed["operator"] == "AND"
    assert len(parsed["claims"]) == 2
    # 设问外壳被剥离，子命题不含“判断以下陈述是否正确”
    assert all("判断" not in c for c in parsed["claims"])


def test_parse_tf_claims_single_returns_single():
    from agent.evidence_selector import parse_tf_claims

    parsed = parse_tf_claims("甲公司2024年营业收入同比增长。")
    assert parsed["operator"] == "SINGLE"


def test_tf_compound_block_injected_only_for_compound_tf():
    from agent.prompts import a_leaderboard_option_judge_prompt

    compound = "上市公司应当在股东会召开前披露董事候选人，且半年度报告应当经董事会审议通过。"
    cards = [{"evidence_id": "E1", "quote": "第十九条 上市公司应当在股东会召开前披露董事候选人", "related_options": ["A"]}]
    msgs = a_leaderboard_option_judge_prompt(compound, {"A": "正确", "B": "错误"}, "tf", cards, [], "regulatory")
    assert "复合命题逐句核验" in msgs[1]["content"]
    assert "联言命题" in msgs[1]["content"]
    assert "未提及/没检索到" in msgs[1]["content"]

    # 单一命题 tf 不注入复合块
    single = a_leaderboard_option_judge_prompt("营业收入同比增长。", {"A": "正确", "B": "错误"}, "tf", cards, [], "financial_reports")
    assert "复合命题逐句核验" not in single[1]["content"]

    # 非 tf 题不注入
    mcq = a_leaderboard_option_judge_prompt("下列哪项正确？", {"A": "x", "B": "y"}, "mcq", cards, [], "regulatory")
    assert "复合命题逐句核验" not in mcq[1]["content"]
