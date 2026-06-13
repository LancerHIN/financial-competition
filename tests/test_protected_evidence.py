from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.evidence_selector import (  # noqa: E402
    _evidence_window,
    chunks_to_evidence_cards,
)
from agent.evidence_validator import validate_cards  # noqa: E402


def _card_chunk(text, domain, chunk_id="c1"):
    return {"chunk_id": chunk_id, "doc_id": "d1", "page": 1, "text": text, "domain": domain}


def test_regulatory_keeps_but_condition():
    text = (
        "第十二条 私募基金管理人应当向投资者披露信息。\n"
        "披露内容包括基金净值、投资比例等。\n"
        "但是基金合同另有约定的，从其约定。\n"
        "第十三条 其他事项另行规定。"
    )
    quote = _evidence_window(text, "regulatory")
    assert "但是" in quote
    assert "另有约定" in quote


def test_insurance_keeps_no_liability():
    text = (
        "第六条 保险责任。\n"
        "被保险人身故的，我们按基本保险金额给付身故保险金。\n"
        "第七条 责任免除。\n"
        "因下列情形之一导致被保险人身故的，我们不承担保险责任：\n"
        "（一）投保人故意造成被保险人死亡；\n"
        "（二）被保险人主动吸食毒品。"
    )
    quote = _evidence_window(text, "insurance")
    assert "不承担保险责任" in quote


def test_contract_keeps_otherwise_agreed():
    text = (
        "第五条 回售条款。\n"
        "债券存续期内，发行人可选择赎回本期债券。\n"
        "除本合同另有约定外，回售价格为面值的100%加应计利息。\n"
        "回售比例不超过本期债券发行规模的50%。\n"
        "募集资金用途为偿还有息债务。"
    )
    quote = _evidence_window(text, "financial_contracts")
    assert "除本合同另有约定" in quote
    assert "不超过" in quote


def test_regulatory_keeps_obligation_words():
    text = (
        "第八条 相关主体应当在十日内报告，不得隐瞒，须经董事会审议通过。\n"
        "未按规定报告的，依法予以处罚。"
    )
    quote = _evidence_window(text, "regulatory")
    assert "应当" in quote
    assert "不得" in quote
    assert "须经" in quote


def test_keeps_threshold_direction():
    text = (
        "第二十条 表决事项。\n"
        "普通决议须经出席会议的股东所持表决权过半数以上通过。\n"
        "特别决议须经三分之二以上通过。\n"
        "持股比例不超过5%的股东无需披露。"
    )
    quote = _evidence_window(text, "regulatory")
    assert "过半数" in quote
    assert "三分之二" in quote
    assert "不超过" in quote


def test_insurance_autoexpand_to_limit():
    # 命中给付但无限制词的行在前，限制词在后；保护窗口应向后扩展纳入限制词
    text = (
        "我们按合同约定给付保险金。\n"
        "给付金额根据基本保险金额确定。\n"
        "保险金按现金价值计算。\n"
        "普通描述行一。\n"
        "普通描述行二。\n"
        "普通描述行三。\n"
        "但被保险人在等待期内身故的，我们不承担保险责任，退还现金价值。"
    )
    quote = _evidence_window(text, "insurance")
    assert "不承担保险责任" in quote or "等待期" in quote


def test_card_fields_present():
    text = "第六条 责任免除。\n我们不承担保险责任的情形如下。\n（一）故意行为。"
    cards = chunks_to_evidence_cards([_card_chunk(text, "insurance")], {"A": "免责情形"}, domain="insurance")
    card = cards[0]
    assert card["compression_policy"] == "protected"
    assert "quote_compressed" in card
    assert card["full_source_len"] == len(text)
    assert card["quote_len"] == len(card["quote"])
    assert "不承担" in card["protected_terms_hit"]


def test_financial_reports_uses_compact():
    text = "2024年营业收入为777亿元。\n2023年营业收入为602亿元。\n同比增长29%。"
    cards = chunks_to_evidence_cards([_card_chunk(text, "financial_reports")], {"A": "营收增长"}, domain="financial_reports")
    assert cards[0]["compression_policy"] == "compact"


def test_validator_insurance_insufficient_without_protect():
    # 选项涉及免责，但证据无任何限制词 -> insufficient
    cards = [{"quote": "本产品保障身故风险。", "chunk_id": "c1", "fact": ""}]
    chunks = [{"chunk_id": "c1", "text": "本产品保障身故风险。"}]
    result = validate_cards("insurance", "下列关于责任免除的说法", {"A": "属于免责情形"}, cards, chunks)
    assert result["status"] == "insufficient"


def test_validator_insurance_sufficient_with_protect():
    cards = [{"quote": "下列情形我们不承担保险责任：故意行为、等待期内出险。", "chunk_id": "c1", "fact": ""}]
    chunks = [{"chunk_id": "c1", "text": "下列情形我们不承担保险责任：故意行为、等待期内出险。"}]
    result = validate_cards("insurance", "下列关于责任免除的说法", {"A": "属于免责情形"}, cards, chunks)
    assert result["status"] == "sufficient"
