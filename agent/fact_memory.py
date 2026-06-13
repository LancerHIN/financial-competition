from __future__ import annotations

"""单题内结构化事实记忆（纯本地，不调用模型）。

把每题召回的 evidence_cards 压缩成一组可比较的结构化事实（fact），供：
  - 跨文档比较 hint（comparison_hints.py）
  - judge prompt 的“已知结构化事实”上下文

设计原则：
  - 只复用 evidence_card 上已抽好的字段（record_type/section/contains_*）和 quote，
    不做重正则解析，避免误抽误导 judge；
  - 事实只是“线索”，最终 supported/refuted 仍由 Qwen 依据证据原文判断；
  - 每条 fact 必须带 doc_id + evidence_id，保证可回溯。
"""

import re

from .numeric_checker import parse_amount_to_yuan, parse_percent

# 阈值方向词 -> 归一方向
_THRESHOLD_DIRECTIONS = [
    ("不超过", "<=", "不超过"),
    ("不高于", "<=", "不高于"),
    ("不得超过", "<=", "不超过"),
    ("不得高于", "<=", "不高于"),
    ("不低于", ">=", "不低于"),
    ("不得低于", ">=", "不低于"),
    ("不少于", ">=", "不少于"),
    ("不得少于", ">=", "不少于"),
    ("至少", ">=", "至少"),
    ("以上", ">=", "以上"),
    ("以下", "<=", "以下"),
    ("超过", ">", "超过"),
    ("高于", ">", "高于"),
    ("低于", "<", "低于"),
]

_TIME_LIMIT_RE = re.compile(r"(\d+)\s*个?\s*(工作日|日|天|个月|月|年)(?:内|以内)?")
_UNIT_RE = re.compile(r"人民币万元|人民币亿元|百万元|亿元|万元|千元|亿|万|元")


def build_fact_memory(
    question: str,
    options: dict[str, str],
    evidence_cards: list[dict],
    domain: str | None = None,
) -> list[dict]:
    """从证据卡构造结构化事实表。每张卡最多产出一条 fact（聚合该卡的关键槽位）。"""
    facts: list[dict] = []
    for index, card in enumerate(evidence_cards or [], start=1):
        fact = _card_to_fact(card, domain, index)
        if fact is not None:
            facts.append(fact)
    return facts


def _card_to_fact(card: dict, domain: str | None, index: int) -> dict | None:
    quote = str(card.get("quote", "") or "")
    record_type = str(card.get("record_type", "") or "")
    doc_id = str(card.get("doc_id", "") or "")

    fact: dict = {
        "fact_id": f"F{index}",
        "evidence_id": card.get("evidence_id", ""),
        "doc_id": doc_id,
        "subject": doc_id,  # 用 doc_id 作为比较主体（公司/产品/法规/文档）
        "domain": domain or "",
        "record_type": record_type,
        "related_options": list(card.get("related_options", []) or []),
    }

    has_signal = False

    # 财报指标：metric + year + value + unit
    indicators = card.get("contains_indicators") or []
    section = str(card.get("section", "") or "")
    metric = ""
    if record_type == "financial_metric" and section and not section.startswith("第"):
        metric = section
    elif indicators:
        metric = indicators[0]
    if metric:
        fact["metric"] = metric
        years = card.get("contains_years") or []
        if years:
            fact["year"] = years[0]
        # item6/7：金额优先从 quote 中抽“带单位的金额”（亿元/万元/元…），不能直接取
        # contains_numbers[0]——它常是年份(2025)或其它无单位数字，会污染 value_raw/value_yuan。
        money_raw = _first_money_token(quote)
        if money_raw is not None:
            fact["value_raw"] = money_raw
            fact["value_yuan"] = parse_amount_to_yuan(money_raw)
            unit_match = _UNIT_RE.search(money_raw)
            if unit_match:
                fact["unit"] = unit_match.group(0)
        has_signal = True

    # 条款号
    clauses = card.get("contains_clauses") or []
    if clauses:
        fact["clause_no"] = clauses[0]
        has_signal = True

    # 阈值方向 + 数值
    threshold = _extract_threshold(quote)
    if threshold:
        fact["threshold"] = threshold
        has_signal = True

    # 时限
    time_limit = _extract_time_limit(quote)
    if time_limit:
        fact["time_limit"] = time_limit
        has_signal = True

    # 保险/合同条款类型与关键词
    clause_type = str(card.get("clause_type", "") or "")
    if clause_type:
        fact["clause_type"] = clause_type
        has_signal = True

    # 比例（无方向词时单独记录）
    if "threshold" not in fact:
        ratio = _first_percent(quote)
        if ratio is not None:
            fact["ratio_pct"] = round(ratio * 100, 4)
            has_signal = True

    if not has_signal:
        return None
    fact["quote_snippet"] = quote[:120]
    return fact


def _extract_threshold(text: str) -> dict | None:
    """抽取“方向词 + 数值”阈值，如 不超过80% / 不低于10亿元。"""
    for word, op, label in _THRESHOLD_DIRECTIONS:
        idx = text.find(word)
        if idx < 0:
            continue
        window = text[idx: idx + 24]
        pct = _first_percent(window)
        if pct is not None:
            return {"direction": label, "op": op, "value_pct": round(pct * 100, 4), "kind": "percent"}
        amount = _first_amount(window)
        if amount is not None:
            return {"direction": label, "op": op, "value_yuan": amount, "kind": "amount"}
        m = re.search(r"\d+(?:\.\d+)?", window)
        if m:
            return {"direction": label, "op": op, "value_num": float(m.group(0)), "kind": "number"}
    return None


def _extract_time_limit(text: str) -> dict | None:
    m = _TIME_LIMIT_RE.search(text)
    if not m:
        return None
    return {"amount": int(m.group(1)), "unit": m.group(2)}


def _first_percent(text: str) -> float | None:
    m = re.search(r"-?\d+(?:\.\d+)?\s*%", text)
    if not m:
        return None
    return parse_percent(m.group(0))


def _first_amount(text: str) -> float | None:
    token = _first_money_token(text)
    return parse_amount_to_yuan(token) if token else None


_MONEY_TOKEN_RE = re.compile(r"-?\d+(?:,\d{3})*(?:\.\d+)?\s*(?:亿元|万元|千元|百万元|亿|万|元)")


def _first_money_token(text: str) -> str | None:
    """返回 quote 中第一个【带单位】的金额原文（如 '803,964,958,000.00元'）。

    必须带金额单位，避免把年份(2025)等无单位数字误当金额。
    """
    m = _MONEY_TOKEN_RE.search(text or "")
    return m.group(0).strip() if m else None
