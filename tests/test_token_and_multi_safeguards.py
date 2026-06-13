from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.answer_normalizer import domain_answer_review  # noqa: E402
from agent.numeric_checker import (  # noqa: E402
    MONEY_RE,
    _aligned_ratio_pair,
    _aligned_yoy_from_evidence,
    _compute_concrete_hints,
    make_computed_hints,
)
from agent.prompts import _PROMPT_CARD_KEYS, _compact_cards_for_prompt  # noqa: E402


# ---- prompt 证据卡精简（token 优化）----

def _cards(n: int, related):
    return [
        {
            "evidence_id": f"E{i}",
            "doc_id": "d",
            "quote": f"q{i}",
            "source_text": "x" * 500,
            "related_options": related(i),
            "compression_policy": "protected",
            "score": 1.0,
            "full_source_len": 500,
        }
        for i in range(n)
    ]


def test_compact_drops_source_text_and_diagnostics():
    cards = _cards(3, lambda i: ["A"])
    compact = _compact_cards_for_prompt(cards, {"A": "a"}, limit=20)
    for card in compact:
        assert "source_text" not in card
        assert "compression_policy" not in card
        assert "full_source_len" not in card
        assert "score" not in card
        assert set(card.keys()) <= set(_PROMPT_CARD_KEYS)


def test_compact_keeps_per_option_evidence_under_truncation():
    # 选项 D 的唯一证据排在第 18 位，limit=12 时仍须保留（防多选漏选）
    cards = _cards(20, lambda i: ["A"] if i < 10 else (["D"] if i == 18 else []))
    compact = _compact_cards_for_prompt(cards, {"A": "a", "B": "b", "C": "c", "D": "d"}, limit=12)
    ids = [c["evidence_id"] for c in compact]
    assert "E18" in ids
    assert len(compact) == 12


def test_compact_default_keeps_all_cards():
    cards = _cards(20, lambda i: ["A"])
    compact = _compact_cards_for_prompt(cards, {"A": "a"})  # 默认 limit=20
    assert len(compact) == 20


# ---- P4 保守 numeric hints ----

def test_yoy_requires_year_money_alignment():
    assert _aligned_yoy_from_evidence("营业收入100亿元 净利润20亿元") is None
    aligned = _aligned_yoy_from_evidence("2024年营业收入602亿元，2025年营业收入777亿元")
    assert aligned is not None
    assert aligned[0] == "2025" and aligned[1] == "2024"


def test_compute_hints_no_blind_money_subtraction():
    # 证据有多个金额但无法对齐年份 -> 不输出"前两个金额相减"的明确同比结论
    hints = _compute_concrete_hints(
        "2025年营业收入同比是否增长", "营业收入增长",
        "营业收入602亿元 净利润20亿元 总资产5000亿元", {"money": [], "percents": [], "years": []},
    )
    joined = " ".join(hints)
    assert "请逐一核对" in joined or "勿凭金额先后相减" in joined
    # 不得给出基于随意两个金额的方向性结论
    assert "为增长（约" not in joined and "为下降（约" not in joined


def test_compute_hints_aligned_yoy_gives_direction():
    hints = _compute_concrete_hints(
        "2025年营业收入同比", "营收增长",
        "2024年营业收入602亿元；2025年营业收入777亿元", {"money": [], "percents": [], "years": []},
    )
    assert any("增长" in h for h in hints)


def test_make_computed_hints_isolates_options():
    cards = [
        {"quote": "2024年营业收入100亿元，2025年营业收入120亿元", "related_options": ["A"]},
        {"quote": "2024年营业收入200亿元，2025年营业收入180亿元", "related_options": ["B"]},
    ]
    hints = make_computed_hints("判断营收变化", {"A": "营收增长", "B": "营收下降"}, cards, max_hints=10)
    joined = "\n".join(hints)
    assert "选项A" in joined and "选项B" in joined
    assert any(h.startswith("选项A") and "增长" in h for h in hints)
    assert any(h.startswith("选项B") and "下降" in h for h in hints)


def test_threshold_hint_respects_direction_words():
    hints = _compute_concrete_hints("", "持股比例4%不超过5%", "", {})
    assert any("<=」" not in h for h in hints)  # 保持普通文本输出，不引入异常符号
    assert any("要求 <=" in h and "符合" in h for h in hints)


# ---- R7: MONEY_RE 非捕获组契约 ----

def test_money_re_uses_only_noncapturing_groups():
    # _aligned_ratio_pair 内嵌 MONEY_RE.pattern 并取 group(1)/group(2)，依赖 MONEY_RE 自身 0 捕获组
    assert MONEY_RE.groups == 0, "MONEY_RE 不得含捕获组，否则 _aligned_ratio_pair 组号错位"


def test_aligned_ratio_pair_keeps_numerator_denominator_order():
    pair = _aligned_ratio_pair("分红8亿元占净利润120亿元")
    assert pair == ("8亿元", "120亿元")
    assert _aligned_ratio_pair("营业收入100亿元，净利润20亿元") is None  # 无占比结构不计算


# ---- R1: 多选补救不把已 supported 选项卷入第二轮 ----

def test_make_computed_hints_caps_total():
    cards = [{"quote": f"2024年营业收入{i}亿元，2025年营业收入{i+10}亿元", "related_options": [chr(65 + i % 4)]} for i in range(8)]
    options = {l: "营收同比变化与占比比例条款第一条" for l in "ABCD"}
    hints = make_computed_hints("判断同比变化", options, cards, max_hints=12)
    assert len(hints) <= 12


# ---- P5 answer_review 安全阀检测 ----

def test_review_flags_full_selection():
    options = {"A": "a", "B": "b", "C": "c", "D": "d"}
    judgements = {l: {"status": "supported"} for l in "ABCD"}
    review = domain_answer_review("ABCD", "multi", options, "regulatory", judgements)
    assert any("选满" in w for w in review["warnings"])
    assert review["needs_review"]


def test_review_flags_answer_without_evidence_card():
    options = {"A": "a", "B": "b", "C": "c", "D": "d"}
    judgements = {"A": {"status": "supported"}, "C": {"status": "supported"}}
    cards = [{"related_options": ["A"]}, {"related_options": ["C"]}]
    # 答案含 B，但没有任何 related_options=B 的证据卡
    review = domain_answer_review("ABC", "multi", options, "regulatory", judgements, evidence_cards=cards)
    assert any("无相关证据卡" in w for w in review["warnings"])


def test_review_clean_answer_no_overselection_warning():
    options = {"A": "a", "B": "b", "C": "c", "D": "d"}
    judgements = {"A": {"status": "supported"}, "C": {"status": "supported"}}
    review = domain_answer_review("AC", "multi", options, "regulatory", judgements)
    assert not any("选满" in w or "偏多" in w for w in review["warnings"])
