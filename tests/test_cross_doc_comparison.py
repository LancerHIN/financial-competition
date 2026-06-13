"""单测：跨文档比较层（fact_memory + comparison_hints）。

纯本地，不调用模型。验证：
- 财报指标事实抽取 + 同口径可比 / 年份或单位不一致不可比
- 阈值方向 + 数值抽取与跨文档比较
- 时限抽取与 工作日 vs 日 口径提示
- 单文档不产出跨文档比较 hint
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.comparison_hints import make_comparison_hints  # noqa: E402
from agent.fact_memory import build_fact_memory  # noqa: E402


def _card(eid, doc_id, quote, **kw):
    card = {
        "evidence_id": eid,
        "doc_id": doc_id,
        "quote": quote,
        "related_options": kw.pop("related_options", []),
        "contains_years": kw.pop("contains_years", []),
        "contains_numbers": kw.pop("contains_numbers", []),
        "contains_clauses": kw.pop("contains_clauses", []),
        "contains_indicators": kw.pop("contains_indicators", []),
        "record_type": kw.pop("record_type", ""),
        "section": kw.pop("section", ""),
    }
    card.update(kw)
    return card


# ---- fact_memory ----

def test_fact_memory_financial_metric():
    cards = [
        _card("E1", "doc1", "营业收入 803,964,958,000.00 元",
              record_type="financial_metric", section="营业收入",
              contains_years=["2025年"], contains_numbers=["803,964,958,000.00元"],
              contains_indicators=["营业收入"]),
    ]
    facts = build_fact_memory("比较营业收入", {"A": "x"}, cards, domain="financial_reports")
    assert len(facts) == 1
    f = facts[0]
    assert f["metric"] == "营业收入"
    assert f["year"] == "2025年"
    assert f["value_yuan"] is not None and f["value_yuan"] > 8e11
    # item6/7：value_raw 必须是带单位金额，而非 contains_numbers[0]（可能是年份）
    assert "元" in f["value_raw"]
    assert f["doc_id"] == "doc1"


def test_fact_memory_value_raw_not_year():
    # quote 里第一个数字是年份；value_raw 不能取成年份，必须取带单位金额
    cards = [
        _card("E1", "doc1", "2025年 营业收入为 100亿元",
              record_type="financial_metric", section="营业收入",
              contains_years=["2025年"], contains_numbers=["2025", "100亿元"],
              contains_indicators=["营业收入"]),
    ]
    facts = build_fact_memory("q", {"A": "x"}, cards, domain="financial_reports")
    f = facts[0]
    assert f["value_raw"] == "100亿元"
    assert abs(f["value_yuan"] - 1e10) < 1
    assert f["unit"] == "亿元"


def test_fact_memory_threshold_and_clause():
    cards = [
        _card("E1", "doc1", "第十二条 发行规模不超过80%的比例",
              contains_clauses=["第十二条"]),
    ]
    facts = build_fact_memory("阈值题", {"A": "x"}, cards, domain="financial_contracts")
    f = facts[0]
    assert f["clause_no"] == "第十二条"
    assert f["threshold"]["direction"] == "不超过"
    assert f["threshold"]["value_pct"] == 80.0


def test_fact_memory_time_limit():
    cards = [_card("E1", "doc1", "应当在5个工作日内向派出机构报告")]
    facts = build_fact_memory("时限题", {"A": "x"}, cards, domain="regulatory")
    assert facts[0]["time_limit"] == {"amount": 5, "unit": "工作日"}


def test_fact_memory_no_signal_dropped():
    cards = [_card("E1", "doc1", "本段为纯描述性文字，无任何结构化事实信号内容。")]
    facts = build_fact_memory("q", {"A": "x"}, cards)
    assert facts == []


# ---- comparison_hints ----

def test_compare_metrics_same_caliber():
    cards = [
        _card("E1", "doc1", "营业收入 100亿元", record_type="financial_metric",
              section="营业收入", contains_years=["2025年"], contains_numbers=["100亿元"],
              contains_indicators=["营业收入"]),
        _card("E2", "doc2", "营业收入 80亿元", record_type="financial_metric",
              section="营业收入", contains_years=["2025年"], contains_numbers=["80亿元"],
              contains_indicators=["营业收入"]),
    ]
    facts = build_fact_memory("比较两家营业收入", {"A": "x"}, cards, domain="financial_reports")
    hints = make_comparison_hints("比较两家营业收入", {"A": "x"}, facts, domain="financial_reports")
    joined = " ".join(hints)
    assert "营业收入" in joined
    assert "最大=doc1" in joined
    assert "doc1" in joined and "doc2" in joined


def test_compare_metrics_year_mismatch_no_cross_year_intent():
    cards = [
        _card("E1", "doc1", "营业收入 100亿元", record_type="financial_metric",
              section="营业收入", contains_years=["2025年"], contains_numbers=["100亿元"],
              contains_indicators=["营业收入"]),
        _card("E2", "doc2", "营业收入 80亿元", record_type="financial_metric",
              section="营业收入", contains_years=["2024年"], contains_numbers=["80亿元"],
              contains_indicators=["营业收入"]),
    ]
    facts = build_fact_memory("比较两家营业收入", {"A": "x"}, cards, domain="financial_reports")
    # 题干无跨年份意图 -> 提示年份口径差异，不直接比较
    hints = make_comparison_hints("比较两家营业收入", {"A": "x"}, facts, domain="financial_reports")
    joined = " ".join(hints)
    assert "不同年份" in joined and "未要求跨年份" in joined


def test_compare_metrics_cross_year_allowed_when_intent():
    cards = [
        _card("E1", "doc1", "营业收入 100亿元", record_type="financial_metric",
              section="营业收入", contains_years=["2025年"], contains_numbers=["100亿元"],
              contains_indicators=["营业收入"]),
        _card("E2", "doc2", "营业收入 80亿元", record_type="financial_metric",
              section="营业收入", contains_years=["2024年"], contains_numbers=["80亿元"],
              contains_indicators=["营业收入"]),
    ]
    q = "营业收入同比增长情况如何"
    facts = build_fact_memory(q, {"A": "x"}, cards, domain="financial_reports")
    hints = make_comparison_hints(q, {"A": "x"}, facts, domain="financial_reports")
    joined = " ".join(hints)
    # item18：题干要求跨年份比较时，允许比较并给数值
    assert "跨年份比较" in joined and "doc1" in joined and "doc2" in joined


def test_compare_metrics_cross_year_intent_in_options():
    # A 榜财报题常态：题干中性，“较2024年增长/下降”写在选项里。须扫选项才能识别跨年意图。
    cards = [
        _card("E1", "doc1", "营业收入 100亿元", record_type="financial_metric",
              section="营业收入", contains_years=["2025年"], contains_numbers=["100亿元"],
              contains_indicators=["营业收入"]),
        _card("E2", "doc2", "营业收入 80亿元", record_type="financial_metric",
              section="营业收入", contains_years=["2024年"], contains_numbers=["80亿元"],
              contains_indicators=["营业收入"]),
    ]
    q = "根据年度报告，下列关于经营业绩的说法正确的有哪些？"
    options = {
        "A": "2025年营业收入较2024年增长",
        "B": "2025年营业收入较2024年下滑",
        "C": "营业收入规模优于行业",
        "D": "营业收入持续下降",
    }
    facts = build_fact_memory(q, options, cards, domain="financial_reports")
    hints = make_comparison_hints(q, options, facts, domain="financial_reports")
    joined = " ".join(hints)
    # 选项里有“较2024年增长/下滑/下降” -> 视为跨年意图，允许比较，不再误报“年份口径不一致”
    assert "跨年份比较" in joined
    assert "未要求跨年份" not in joined


def test_compare_metrics_unit_mismatch_converts():
    # item3/17：单位不同但 value_yuan 可用 -> 直接按换算金额给大小关系
    cards = [
        _card("E1", "doc1", "营业收入 10亿元", record_type="financial_metric",
              section="营业收入", contains_years=["2025年"], contains_numbers=["10亿元"],
              contains_indicators=["营业收入"]),
        _card("E2", "doc2", "营业收入 50000万元", record_type="financial_metric",
              section="营业收入", contains_years=["2025年"], contains_numbers=["50000万元"],
              contains_indicators=["营业收入"]),
    ]
    facts = build_fact_memory("比较营业收入", {"A": "x"}, cards, domain="financial_reports")
    hints = make_comparison_hints("比较营业收入", {"A": "x"}, facts, domain="financial_reports")
    joined = " ".join(hints)
    assert "单位不一致" in joined
    assert "最大=doc1" in joined  # 10亿 > 5亿(50000万)


def test_compare_thresholds_cross_doc():
    cards = [
        _card("E1", "doc1", "发行规模不超过10亿元"),
        _card("E2", "doc2", "发行规模不超过5亿元"),
    ]
    facts = build_fact_memory("q", {"A": "x"}, cards, domain="financial_contracts")
    hints = make_comparison_hints("q", {"A": "x"}, facts, domain="financial_contracts")
    joined = " ".join(hints)
    assert "不超过" in joined and "doc1" in joined and "doc2" in joined


def test_thresholds_different_topic_not_mixed():
    # item4/5：不同条款号的同方向阈值不能混比
    cards = [
        _card("E1", "doc1", "第十二条 发行规模不超过10亿元", contains_clauses=["第十二条"]),
        _card("E2", "doc2", "第二十条 单一客户授信不超过5亿元", contains_clauses=["第二十条"]),
    ]
    facts = build_fact_memory("q", {"A": "x"}, cards, domain="financial_contracts")
    hints = make_comparison_hints("q", {"A": "x"}, facts, domain="financial_contracts")
    # 主题键（clause_no）不同 -> 不应产生“同主题阈值不同”的混比 hint
    assert all("同主题" not in h for h in hints)


def test_compare_time_limit_workday_vs_day():
    cards = [
        _card("E1", "doc1", "应当在5个工作日内报告"),
        _card("E2", "doc2", "应当在5日内备案"),
    ]
    facts = build_fact_memory("q", {"A": "x"}, cards, domain="regulatory")
    hints = make_comparison_hints("q", {"A": "x"}, facts, domain="regulatory")
    joined = " ".join(hints)
    assert "工作日" in joined and "口径不同" in joined


def test_single_doc_no_cross_doc_hint():
    cards = [
        _card("E1", "doc1", "营业收入 100亿元", record_type="financial_metric",
              section="营业收入", contains_years=["2025年"], contains_numbers=["100亿元"],
              contains_indicators=["营业收入"]),
        _card("E2", "doc1", "净利润 20亿元", record_type="financial_metric",
              section="净利润", contains_years=["2025年"], contains_numbers=["20亿元"],
              contains_indicators=["净利润"]),
    ]
    facts = build_fact_memory("q", {"A": "x"}, cards, domain="financial_reports")
    hints = make_comparison_hints("q", {"A": "x"}, facts, domain="financial_reports")
    assert hints == []


def test_comparison_hints_capped():
    cards = []
    for i in range(20):
        cards.append(_card(f"E{i}", f"doc{i%2}", f"指标{i} 不超过{i+1}%"))
    facts = build_fact_memory("q", {"A": "x"}, cards, domain="financial_contracts")
    hints = make_comparison_hints("q", {"A": "x"}, facts, domain="financial_contracts", max_hints=8)
    assert len(hints) <= 8
