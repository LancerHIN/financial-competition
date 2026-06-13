from __future__ import annotations

import re

from .domain_rules import extract_clause_numbers, extract_financial_indicators, extract_percents, extract_years


def validate_cards(
    domain: str | None,
    question: str,
    options: dict[str, str],
    cards: list[dict],
    chunks: list[dict] | None = None,
) -> dict:
    """对 evidence cards 做规则校验，返回 status + 失败原因 + 补检索建议。

    不调用模型，只做本地一致性检查；quote 必须能在原始 chunk 中找到。
    """
    chunk_text_by_id = {str(c.get("chunk_id", "")): str(c.get("text", "")) for c in (chunks or [])}
    all_chunk_text = "\n".join(chunk_text_by_id.values())

    issues: list[str] = []
    valid_cards: list[dict] = []
    for index, card in enumerate(cards):
        quote = str(card.get("quote", "")).strip()
        if not quote:
            issues.append(f"card[{index}] quote 为空")
            continue
        if chunk_text_by_id and not _quote_in_source(quote, card, chunk_text_by_id, all_chunk_text):
            issues.append(f"card[{index}] quote 可能非原文")
            card = {**card, "quote_verified": False}
        else:
            card = {**card, "quote_verified": True}
        valid_cards.append(card)

    option_text = " ".join(options.values())
    evidence_blob = " ".join(str(card.get("quote", "")) + " " + str(card.get("fact", "")) for card in valid_cards)

    domain_issues = _domain_checks(domain, question, option_text, evidence_blob)
    issues.extend(domain_issues)

    if not valid_cards:
        status = "insufficient"
    elif domain_issues:
        status = "insufficient"
    else:
        status = "sufficient"

    return {
        "status": status,
        "issues": issues,
        "valid_cards": valid_cards,
        "retry_queries": _retry_queries(domain, question, option_text, evidence_blob),
    }


def _quote_in_source(quote: str, card: dict, by_id: dict[str, str], all_text: str) -> bool:
    chunk_id = str(card.get("chunk_id", ""))
    source = by_id.get(chunk_id, "") or all_text
    norm_quote = _norm(quote)
    norm_source = _norm(source)
    if not norm_quote:
        return False
    if norm_quote in norm_source:
        return True
    # 容忍压缩时的轻微改写：取较长子片段匹配
    head = norm_quote[: max(10, len(norm_quote) // 2)]
    return head in norm_source


def _domain_checks(domain: str | None, question: str, option_text: str, evidence: str) -> list[str]:
    issues: list[str] = []
    combined = question + " " + option_text
    if domain == "financial_reports":
        option_years = extract_years(combined)
        option_indicators = extract_financial_indicators(combined)
        evidence_years = extract_years(evidence)
        evidence_indicators = extract_financial_indicators(evidence)
        for year in option_years:
            if year not in evidence_years:
                issues.append(f"缺少年份 {year} 的财报证据")
        if option_indicators and not evidence_indicators:
            issues.append("缺少财务指标证据")
    elif domain == "regulatory":
        option_clauses = extract_clause_numbers(combined)
        if option_clauses and not extract_clause_numbers(evidence):
            issues.append("缺少条文号/法规原文证据")
        # 题目/选项含义务词/限制词，但证据中缺失对应义务词 -> insufficient
        if _hits(combined, _REGULATORY_TRIGGERS) and not _hits(evidence, _REGULATORY_OBLIGATIONS):
            issues.append("题目涉及义务/限制（应当/不得/须经/决议/阈值/时限），但证据缺少对应义务词或限制词")
    elif domain == "insurance":
        if re.search(r"金额|保险金|现金价值|账户价值|给付|免责|不承担|领取|退保", combined):
            if not re.search(r"保险金|现金价值|账户价值|给付|免责|不承担|除外|领取|退保|触发|条件|公式|=", evidence):
                issues.append("缺少触发条件/计算公式/免责证据")
        # 题目/选项含免责/限制语义，但证据中无任何保护词 -> insufficient
        if _hits(combined, _INSURANCE_TRIGGERS) and not _hits(evidence, _INSURANCE_PROTECT):
            issues.append("题目涉及免责/除外/等待期/退保/给付，但证据缺少任何限制/条件词")
    elif domain == "financial_contracts":
        if re.search(r"发行规模|利率|期限|评级|担保|回售|赎回", combined):
            if not re.search(r"发行规模|利率|期限|评级|担保|回售|赎回|亿元|%", evidence):
                issues.append("缺少合同条款关键证据")
        # 题目/选项含合同关键条款，但证据缺少条件词 -> insufficient
        if _hits(combined, _CONTRACT_TRIGGERS) and not _hits(evidence, _CONTRACT_CONDITIONS):
            issues.append("题目涉及回售/赎回/担保/评级/期限/利率/违约/阈值，但证据缺少条件词")
    elif domain == "research":
        # 研报观点题：题目涉及观点/预测/评级，证据须含研报观点类词
        if _hits(combined, _RESEARCH_TRIGGERS) and not _hits(evidence, _RESEARCH_CLAIM_WORDS):
            issues.append("题目涉及观点/预测/评级/目标价，但证据缺少研报观点/预测类表述")
        # 题目带预测年份，证据须覆盖对应年份（口径一致）
        combined_years = extract_years(combined)
        if combined_years and not (set(combined_years) & set(extract_years(evidence))):
            issues.append("题目涉及特定预测/报告年份，但证据缺少对应年份")
    return issues


_REGULATORY_TRIGGERS = ["必须", "应当", "不得", "可以", "须经", "特别决议", "普通决议", "以上", "以下", "不超过", "日内"]
_REGULATORY_OBLIGATIONS = ["应当", "不得", "可以", "须经", "禁止", "必须", "以上", "以下", "不超过", "不少于", "日内", "工作日内", "过半数", "三分之二", "另有规定"]

_INSURANCE_TRIGGERS = ["免责", "不承担", "除外", "不适用", "等待期", "退保", "领取", "保险金", "现金价值", "账户价值"]
_INSURANCE_PROTECT = ["免责", "不承担", "除外", "不适用", "但", "等待期", "犹豫期", "宽限期", "领取", "退保", "现金价值", "账户价值", "给付", "较大者", "较小者", "已交保费"]

_CONTRACT_TRIGGERS = ["回售", "赎回", "担保", "评级", "期限", "利率", "违约", "不超过", "不低于", "另有约定"]
_CONTRACT_CONDITIONS = ["回售", "赎回", "担保", "评级", "期限", "利率", "违约", "不超过", "不低于", "不少于", "另有约定", "募集资金用途", "承诺", "限制"]

_RESEARCH_TRIGGERS = ["预测", "预计", "盈利预测", "目标价", "评级", "投资建议", "买入", "增持", "中性", "维持", "首次覆盖", "核心观点", "风险提示", "趋势"]
_RESEARCH_CLAIM_WORDS = ["预测", "预计", "盈利预测", "目标价", "评级", "投资建议", "买入", "增持", "减持", "中性", "维持", "首次覆盖", "核心观点", "风险提示", "同比", "环比", "趋势", "预期"]


def _hits(text: str, terms: list[str]) -> bool:
    return any(term in text for term in terms)


def _retry_queries(domain: str | None, question: str, option_text: str, evidence: str) -> list[str]:
    """根据本地校验发现的缺失硬事实派生补检索 query。

    应修4：补充更多缺失类型——缺年份、缺条文号、缺比例、缺金额、缺指标、缺保护词。
    这些 query 不绑定具体选项，命中后由 _match_options 回填，用于补齐首轮漏检的硬事实。
    即使模型首轮错误地 supported/refuted，这些 query 仍能在 validation retry 触发源里独立生效。
    """
    combined = question + " " + option_text
    queries: list[str] = []

    # 缺年份（财报口径对齐）
    evidence_years = extract_years(evidence)
    for year in extract_years(combined):
        if year not in evidence_years:
            queries.append(f"{year} {' '.join(extract_financial_indicators(combined)[:2])}".strip())

    # 缺条文号
    evidence_clauses = extract_clause_numbers(evidence)
    for clause in extract_clause_numbers(combined):
        if clause not in evidence_clauses:
            queries.append(clause)

    # 缺比例
    for percent in extract_percents(combined):
        if percent not in evidence:
            queries.append(f"{percent} {question[:20]}")

    # 缺金额（题干/选项出现金额但证据未覆盖）
    combined_money = _MONEY_RE.findall(combined)
    for money in _dedupe(combined_money):
        if money not in re.sub(r"\s+", "", evidence):
            queries.append(f"{money} {question[:16]}".strip())

    # 缺指标（题干指标在证据中缺失）
    evidence_indicators = extract_financial_indicators(evidence)
    for indicator in extract_financial_indicators(combined):
        if indicator not in evidence_indicators:
            queries.append(f"{indicator} {' '.join(extract_years(combined)[:1])}".strip())

    # 缺保护词/条件词（题目涉及义务/限制/免责，但证据无对应保护词）
    hit_protect = [term for term in _PROTECT_WORDS if term in combined]
    if hit_protect and not any(term in evidence for term in _PROTECT_WORDS):
        queries.append(" ".join(hit_protect[:3]) + " " + question[:16])

    if domain == "research" and _hits(combined, _RESEARCH_TRIGGERS) and not _hits(evidence, _RESEARCH_CLAIM_WORDS):
        hit_terms = [t for t in _RESEARCH_TRIGGERS if t in combined][:3]
        if hit_terms:
            queries.append(" ".join(hit_terms))
    return [q for q in dict.fromkeys(queries) if q.strip()][:8]


# 保护词/条件词：但书、除外、免责、否定、阈值方向、义务词等。证据缺这些时大概率漏检关键条件。
_PROTECT_WORDS = [
    "但", "但是", "除外", "不承担", "不负责赔偿", "不适用", "不在此限",
    "不超过", "不低于", "不少于", "以上", "以下", "另有约定", "另有规定",
    "应当", "不得", "须经",
]

_MONEY_RE = re.compile(r"-?\d+(?:,\d{3})*(?:\.\d+)?\s*(?:亿元|万元|千元|百万元|亿|万|元)")


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        item = re.sub(r"\s+", "", str(value))
        if item and item not in seen:
            seen.add(item)
            result.append(value)
    return result


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", str(text)).lower()
