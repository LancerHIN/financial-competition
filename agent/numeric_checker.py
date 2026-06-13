from __future__ import annotations

import re

from .domain_rules import FINANCIAL_INDICATORS


PERCENT_RE = re.compile(r"-?\d+(?:\.\d+)?\s*%")
MONEY_RE = re.compile(r"-?\d+(?:,\d{3})*(?:\.\d+)?\s*(?:亿元|万元|千元|百万元|亿|万|元)")
YEAR_RE = re.compile(r"(?:19|20)\d{2}\s*年")
CLAUSE_RE = re.compile(r"第[一二三四五六七八九十百零〇0-9]+条(?:第[一二三四五六七八九十0-9]+款)?")
PLAIN_NUM_RE = re.compile(r"-?\d+(?:,\d{3})*(?:\.\d+)?")

# 单位 -> 元换算系数
UNIT_TO_YUAN = {
    "亿元": 1e8, "亿": 1e8,
    "万元": 1e4, "万": 1e4,
    "千元": 1e3,
    "百万元": 1e6,
    "元": 1.0,
    "人民币元": 1.0, "人民币万元": 1e4, "人民币亿元": 1e8,
}

CN_NUM = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def extract_numeric_facts(text: str) -> dict[str, list[str]]:
    text = text or ""
    return {
        "percents": _dedupe(PERCENT_RE.findall(text)),
        "money": _dedupe(MONEY_RE.findall(text)),
        "years": _dedupe(YEAR_RE.findall(text)),
        "clauses": _dedupe(CLAUSE_RE.findall(text)),
        "numbers": _dedupe(PLAIN_NUM_RE.findall(text)),
        "indicators": [term for term in FINANCIAL_INDICATORS if term in text],
    }


def extract_query_facts(text: str) -> dict[str, list[str]]:
    return extract_numeric_facts(text or "")


# 定向补检索 query 的硬事实总上限：选项硬事实优先，题干硬事实补充，去重后整体限长，
# 既避免长题干把关键数值挤掉，也避免 query 过长稀释 BM25 命中。
TARGETED_QUERY_MAX_FACTS = 10


def make_targeted_query(question: str, option_text: str) -> str:
    """为某个待补检索的选项构造定向 query。

    item13：不再固定截取 years/money/percents/clauses/indicators 各前 4 个（长题干会把选项里的
    关键数值挤掉）。改为：
      1) 选项文本自身的数字/条款/年份/比例/金额/指标优先全部纳入；
      2) 题干的硬事实作为补充，仅在未超过总上限时追加；
      3) 全部去重后设一个合理的总上限，保证选项关键数值不被丢弃。
    """
    option_text = str(option_text or "")
    question = str(question or "")
    option_facts = _ordered_facts(option_text)
    question_facts = _ordered_facts(question)

    selected: list[str] = []
    seen: set[str] = set()

    def _take(values: list[str]) -> None:
        for value in values:
            if len(selected) >= TARGETED_QUERY_MAX_FACTS:
                return
            norm = re.sub(r"\s+", "", value)
            if norm and norm not in seen:
                seen.add(norm)
                selected.append(value)

    _take(option_facts)  # 选项硬事实优先占满预算
    _take(question_facts)  # 题干硬事实补充

    parts = [question, option_text, *selected]
    return " ".join(_dedupe([part for part in parts if str(part).strip()]))


def _ordered_facts(text: str) -> list[str]:
    """按“条款 > 比例 > 金额 > 年份 > 指标”的判题重要性顺序抽取硬事实。

    条款号/比例/金额是数字偷换题最关键的判别点，放在前面优先占预算。
    """
    facts = extract_query_facts(text)
    ordered: list[str] = []
    for key in ("clauses", "percents", "money", "years", "indicators"):
        ordered.extend(facts.get(key, []))
    return ordered


# ----------------------------------------------------------------------------
# 本地数值计算原语
# ----------------------------------------------------------------------------

def parse_amount_to_yuan(text: str) -> float | None:
    """把 '8亿元'、'1,234.56万元'、'120亿' 解析为元。无法解析返回 None。"""
    if text is None:
        return None
    s = str(text).replace(",", "").replace(" ", "").replace("人民币", "")
    match = re.search(r"(-?\d+(?:\.\d+)?)\s*(百万元|人民币万元|人民币亿元|亿元|万元|千元|亿|万|元)", s)
    if not match:
        return None
    value = float(match.group(1))
    unit = match.group(2)
    return value * UNIT_TO_YUAN.get(unit, 1.0)


def parse_percent(text: str) -> float | None:
    """解析百分比/中文比例为小数（如 6.67% -> 0.0667，三分之二 -> 0.6667，过半数 -> 0.5）。

    item14：「过半数=0.5」「三分之二=2/3」只是【参考解析结果】，用于本地提示，
    绝不能覆盖证据原文中的「另有约定/另有规定/特殊章程」。调用方在生成 hint 时
    必须同时提示“若证据原文另有规定或章程另有约定，以证据原文为准”。
    """
    if text is None:
        return None
    s = str(text).strip()
    m = re.search(r"(-?\d+(?:\.\d+)?)\s*%", s)
    if m:
        return float(m.group(1)) / 100.0
    m = re.search(r"百分之([\d点零一二三四五六七八九十百]+)", s)
    if m:
        val = _cn_to_number(m.group(1))
        if val is not None:
            return val / 100.0
    # 分数：X分之Y
    m = re.search(r"([一二三四五六七八九十两\d]+)分之([一二三四五六七八九十两\d]+)", s)
    if m:
        denom = _cn_to_number(m.group(1))
        numer = _cn_to_number(m.group(2))
        if denom and numer is not None and denom != 0:
            return numer / denom
    if "过半数" in s or "半数以上" in s:
        return 0.5
    if "三分之二" in s or "2/3" in s:
        return 2.0 / 3.0
    return None


def _cn_to_number(text: str) -> float | None:
    """支持阿拉伯数字、'七十'、'七点五' 这类简单中文数字。"""
    text = str(text).strip()
    if re.fullmatch(r"-?\d+(?:\.\d+)?", text):
        return float(text)
    if "点" in text:
        left, _, right = text.partition("点")
        l = _cn_to_number(left) or 0
        digits = "".join(str(CN_NUM.get(c, "")) for c in right)
        return float(f"{int(l)}.{digits}") if digits else l
    # 十位数：七十=70, 十=10, 十五=15, 二十三=23
    if "十" in text:
        left, _, right = text.partition("十")
        tens = CN_NUM.get(left, 1 if left == "" else CN_NUM.get(left, 0))
        ones = CN_NUM.get(right, 0) if right else 0
        return float(tens * 10 + ones)
    if text in CN_NUM:
        return float(CN_NUM[text])
    return None


def yoy_change(current: str, previous: str) -> dict | None:
    """同比变化：current/previous 可为金额或纯数。返回变化率与方向。"""
    cur = parse_amount_to_yuan(current)
    prev = parse_amount_to_yuan(previous)
    if cur is None:
        cur = _plain(current)
    if prev is None:
        prev = _plain(previous)
    if cur is None or prev is None or prev == 0:
        return None
    rate = (cur - prev) / abs(prev)
    return {
        "current": cur, "previous": prev,
        "rate": rate,
        "direction": "增长" if rate > 0 else ("下降" if rate < 0 else "持平"),
        "rate_pct": round(rate * 100, 2),
    }


def ratio_of(part_text: str, whole_text: str) -> dict | None:
    """占比计算：part/whole（自动单位归一）。返回 {part, whole, ratio, ratio_pct}。"""
    part = parse_amount_to_yuan(part_text)
    whole = parse_amount_to_yuan(whole_text)
    if part is None:
        part = _plain(part_text)
    if whole is None:
        whole = _plain(whole_text)
    if part is None or whole is None or whole == 0:
        return None
    ratio = part / whole
    return {"part": part, "whole": whole, "ratio": ratio, "ratio_pct": round(ratio * 100, 2)}


def threshold_check(value_text: str, threshold_text: str) -> dict | None:
    """阈值判断：把两侧解析为百分比或金额后比较。返回 relation 与是否触发。"""
    v = parse_percent(value_text)
    t = parse_percent(threshold_text)
    if v is None or t is None:
        v = parse_amount_to_yuan(value_text) or _plain(value_text)
        t = parse_amount_to_yuan(threshold_text) or _plain(threshold_text)
    if v is None or t is None:
        return None
    return {
        "value": v, "threshold": t,
        "exceeds": v > t,
        "relation": ">" if v > t else ("<" if v < t else "="),
    }


def resolution_threshold(question_text: str) -> str | None:
    """法规决议阈值：特别决议=三分之二以上；普通决议=过半数。"""
    if "特别决议" in question_text or "三分之二" in question_text:
        return "特别决议对应三分之二（≈66.67%）以上表决权通过"
    if "普通决议" in question_text or "过半数" in question_text:
        return "普通决议对应过半数（50%以上）表决权通过"
    return None


# 复杂保险公式信号：出现这些词说明给付不是简单 max/min(金额…)，而涉及倍数/比例/年龄分段/
# 缴费年限/领取状态等，本地无法可靠解析，必须交还原文公式核对，绝不硬算。
INSURANCE_COMPLEX_FORMULA_TERMS = (
    "倍", "比例", "百分", "%", "年龄", "周岁", "缴费年限", "缴费期", "已领取", "已领",
    "领取金额", "分段", "档次", "系数", "递增", "递减", "累计", "扣除", "减去",
)
# 明确的 max/min 触发词：只有题干/证据明确出现这些，才允许本地给出 max/min 计算 hint。
INSURANCE_MAXMIN_TERMS = ("较大者", "较高者", "孰高", "取较大", "max", "较小者", "较低者", "孰低", "取较小", "min")


def insurance_payout(values: list[str], mode: str = "max", context: str = "") -> dict | None:
    """保险给付 max/min 取数：仅作保守防误导，不做完整保险公式引擎。

    item15 保守化：
    - 仅当 context 明确出现较大者/较小者/max/min/孰高/孰低等取数词时才计算（context 为空时
      视为调用方已在外层确认过触发词，沿用旧行为以兼容直接调用与既有单测）；
    - 若 context 含倍数/比例/年龄分段/缴费年限/领取状态等复杂公式信号，说明给付不是简单取数，
      本地无法可靠解析，返回 None（由调用方提示“需按原文公式核对”），不要硬算。
    """
    ctx = str(context or "")
    if ctx and any(term in ctx.lower() for term in (t.lower() for t in INSURANCE_COMPLEX_FORMULA_TERMS)):
        return None
    if ctx and not any(term.lower() in ctx.lower() for term in INSURANCE_MAXMIN_TERMS):
        return None
    amounts = [parse_amount_to_yuan(v) for v in values]
    amounts = [a for a in amounts if a is not None]
    if len(amounts) < 2:
        # 少于两个可比金额时取数无意义，避免“只有一个金额也输出 max”制造误导
        return None
    chosen = max(amounts) if mode == "max" else min(amounts)
    return {"amounts": amounts, "mode": mode, "result": chosen}


def per_share_dividend(text: str) -> dict | None:
    """把现金分红表述归一为【每股】口径，避免「每10股派X元」与「每股Y元」直接比较的单位陷阱。

    支持："每10股派43.0元"、"每10股派发现金红利43元"、"10派4.3"、"每股0.43元"。
    返回 {per10: 每10股金额 or None, per_share: 每股金额, raw: 原始串}。
    只解析【明确的每股/每N股】结构，无法识别返回 None，绝不臆造。
    """
    if text is None:
        return None
    s = str(text).replace(",", "").replace(" ", "")
    # 每N股派/派发X元（含“现金红利/股息”等修饰，X 可带元）
    m = re.search(r"每(\d+)股(?:派(?:发)?(?:现金(?:红利|股利|分红))?)?(-?\d+(?:\.\d+)?)元?", s)
    if m:
        base = float(m.group(1))
        amount = float(m.group(2))
        if base > 0:
            return {"per10": amount if base == 10 else None, "per_share": round(amount / base, 6), "base": base, "raw": m.group(0)}
    # “10派4.3” / “10转增…派4.3”这类简记：N 派 X
    m = re.search(r"(\d+)\s*派(?:发)?\s*(-?\d+(?:\.\d+)?)", s)
    if m:
        base = float(m.group(1))
        amount = float(m.group(2))
        if base > 0:
            return {"per10": amount if base == 10 else None, "per_share": round(amount / base, 6), "base": base, "raw": m.group(0)}
    # 每股X元（已是每股口径）
    m = re.search(r"每股(-?\d+(?:\.\d+)?)元", s)
    if m:
        return {"per10": None, "per_share": float(m.group(1)), "base": 1.0, "raw": m.group(0)}
    return None


def _plain(text) -> float | None:
    if text is None:
        return None
    m = re.search(r"-?\d+(?:\.\d+)?", str(text).replace(",", ""))
    return float(m.group(0)) if m else None


def _fmt_yuan(value: float) -> str:
    """把元值格式化为可读字符串（自动选亿/万元）。"""
    if abs(value) >= 1e8:
        return f"{value / 1e8:.4g}亿元"
    if abs(value) >= 1e4:
        return f"{value / 1e4:.4g}万元"
    return f"{value:.4g}元"


# ----------------------------------------------------------------------------
# 提示生成
# ----------------------------------------------------------------------------

def make_computed_hints(question: str, options: dict[str, str], evidence_cards: list[dict], max_hints: int = 12, domain: str | None = None) -> list[str]:
    """按选项生成本地计算提示，避免全题 evidence 数字混用。

    旧逻辑把所有 evidence_cards 合成一个 evidence_blob，再给全题共用 hint；多选题中 A/B/C/D 的金额、比例、年份
    会互相污染。这里改为：每个选项只使用 related_options 命中的证据卡，算出的提示也加上“选项X”前缀。
    无直接证据的选项不做明确计算，只提示证据不足。

    R2 token 防护：优先保留"明确计算结论/缺证提示"（_compute_concrete_hints 与缺证提示），
    数值清单类提示（年份/比例/金额罗列）只在还有 hint 预算时补充，避免逐项罗列把 prompt 撑大。
    """
    priority_hints: list[str] = []  # 计算结论 / 缺证提示（高价值，优先）
    listing_hints: list[str] = []   # 数值罗列（低价值，预算够才加）
    for letter, option_text in sorted(options.items()):
        option_text = str(option_text or "")
        cards = [card for card in (evidence_cards or []) if letter in (card.get("related_options") or [])]
        evidence_blob = _cards_text(cards)
        combined = question + "\n" + option_text

        if not cards:
            if _needs_numeric_or_clause_check(combined):
                priority_hints.extend(_prefix_option_hints(letter, ["缺少该选项的直接证据卡，不能用其他选项证据替代计算或判断。"]))
            continue

        facts = _facts_from_text(evidence_blob)
        computed = _compute_concrete_hints(question, option_text, evidence_blob, facts, domain)
        res = resolution_threshold(combined)
        if res:
            computed.append(res + "；若证据原文另有规定或章程另有约定，以证据原文为准。")
        priority_hints.extend(_prefix_option_hints(letter, computed[:2]))

        # 数值罗列：每个选项最多 1 条（合并到一行），仅在该选项确有计算/条款语义时生成
        if _needs_numeric_or_clause_check(combined):
            parts: list[str] = []
            if facts["years"]:
                parts.append("年份" + "、".join(facts["years"][:3]))
            if facts["percents"]:
                parts.append("比例" + "、".join(facts["percents"][:4]))
            if facts["money"]:
                parts.append("金额" + "、".join(facts["money"][:4]))
            if facts["clauses"]:
                parts.append("条款" + "、".join(facts["clauses"][:4]))
            if parts:
                listing_hints.extend(_prefix_option_hints(letter, ["仅核对本选项相关证据(" + "；".join(parts) + ")，勿混用其他选项数字。"]))

    hints = _dedupe_hints(priority_hints)
    for hint in _dedupe_hints(listing_hints):
        if len(hints) >= max_hints:
            break
        hints.append(hint)
    return hints[:max_hints]


def _cards_text(cards: list[dict]) -> str:
    return "\n".join(f"{card.get('quote', '')}\n{card.get('source_text', '')}\n{card.get('fact', '')}" for card in cards)


def _facts_from_text(text: str) -> dict[str, list[str]]:
    extracted = extract_numeric_facts(text)
    return {
        "percents": _dedupe(extracted["percents"]),
        "money": _dedupe(extracted["money"]),
        "years": _dedupe(extracted["years"]),
        "clauses": _dedupe(extracted["clauses"]),
        "indicators": _dedupe(extracted["indicators"]),
    }


def _needs_numeric_or_clause_check(text: str) -> bool:
    return bool(re.search(r"同比|环比|增长|下降|增加|减少|占比|比例|优于|劣于|高于|低于|超过|不超过|不得|应当|期限|不少于|不多于|计算|合计|差异|第[一二三四五六七八九十百零〇0-9]+条", text))


def _prefix_option_hints(letter: str, hints: list[str]) -> list[str]:
    return [f"选项{letter}：{hint}" for hint in hints if hint]


def _compute_concrete_hints(question: str, option_text: str, evidence_blob: str, facts: dict, domain: str | None = None) -> list[str]:
    """从题干/选项中的金额、百分比、阈值出发，给出明确的本地计算结论。

    保守原则：只有当可比项口径明确对齐时才输出"明确结论"，否则只提示需人工核对，不主动给可能误导的数字。
    - 占比/阈值：两个比例/金额来自同一表述（题干/选项内并列），可计算。
    - 同比：证据里必须能对齐两个【不同年份】，否则不臆造"前两个金额相减"。
    """
    hints: list[str] = []
    combined = question + "\n" + option_text

    # 1) 占比题：只在文本有明确“分子/分母”结构时计算，避免拿前两个金额倒置相除。
    ratio_pair = _aligned_ratio_pair(combined)
    if ratio_pair:
        numerator, denominator = ratio_pair
        r = ratio_of(numerator, denominator)
        if r and r["whole"]:
            hints.append(f"{numerator} / {denominator} = {r['ratio_pct']}%（仅在该分子/分母口径成立时采用）")

    # 2) 阈值题：结合方向词给出应核对的比较关系，不把“value > threshold”泛化成真假结论。
    q_percents = _dedupe(PERCENT_RE.findall(combined))
    if len(q_percents) >= 2 and re.search(r"超过|高于|低于|不超过|不得|达到|是否", combined):
        chk = threshold_check(q_percents[0], q_percents[1])
        if chk:
            direction = _threshold_direction(combined)
            # 应修9：regulatory/financial_contracts 的阈值方向（不超过/不低于/不少于/以上/以下）只提示
            # “应按证据原文方向核对”，不替 judge 下符合/不符合的结论；其它领域（如财报数值核对）
            # 仍可给参考结论但同样标注以原文为准。
            advisory_only = domain in {"regulatory", "financial_contracts"}
            if direction and not advisory_only:
                ok = _relation_satisfies(float(chk["value"]), float(chk["threshold"]), direction)
                hints.append(f"阈值方向词要求 {direction}；{q_percents[0]} {chk['relation']} {q_percents[1]}，按该方向为{'符合' if ok else '不符合'}（参考，以证据原文方向为准）。")
            elif direction and advisory_only:
                hints.append(f"涉及阈值方向（{direction}）：{q_percents[0]} 与 {q_percents[1]} 的大小关系为 {chk['relation']}，请严格按证据原文的方向词核对，不要据此直接下结论。")
            else:
                hints.append(f"{q_percents[0]} {chk['relation']} {q_percents[1]}，但未识别明确方向词，请按证据原文判断阈值语义。")

    # 3) 同比题：保守化——只有当证据能对齐【两个不同年份】且各自附带金额时才给方向结论，
    #    否则不再用"证据里前两个金额"硬算（旧逻辑会把不同指标/口径的金额相减，产生误导 hint）。
    if re.search(r"同比|增长|下降|增加|减少", combined):
        target_indicators = [term for term in FINANCIAL_INDICATORS if term in combined]
        yoy = _aligned_yoy_from_evidence(evidence_blob, target_indicators)
        if yoy:
            cur_year, prev_year, change = yoy
            hints.append(
                f"证据中 {cur_year} 较 {prev_year} 为{change['direction']}（约{change['rate_pct']}%），"
                f"请确认两数为同一指标同一口径后再判断增长/下降表述真伪"
            )
        else:
            ev_moneys = _dedupe(MONEY_RE.findall(evidence_blob))
            if len(ev_moneys) >= 2:
                hints.append("题目涉及同比/增减，证据中存在多个金额但未能可靠对齐年份与指标口径，请逐一核对后再判断，勿凭金额先后相减。")

    # 3.5) 现金分红：题目比较"每股/每10股派息"时，把两边归一到【每股】口径再比，避免单位陷阱
    #      （每10股派43元 = 每股4.3元，不能直接和"每股X元"比大小）。
    if re.search(r"分红|股利|派息|派发|每\d*股|每股", combined):
        ps = [d for d in (per_share_dividend(t) for t in (option_text, evidence_blob)) if d]
        # 证据里可能出现多个分红表述，逐一抽取
        for m in re.finditer(r"每\d+股[^，。；\n]{0,12}?\d+(?:\.\d+)?元?|每股\d+(?:\.\d+)?元|\d+派\d+(?:\.\d+)?", evidence_blob):
            d = per_share_dividend(m.group(0))
            if d:
                ps.append(d)
        uniq = {round(d["per_share"], 6): d for d in ps}
        if uniq:
            parts = [f"{d['raw']}→每股{d['per_share']:g}元" for d in list(uniq.values())[:3]]
            hints.append("现金分红已归一到每股口径：" + "；".join(parts) + "（比较大小请用每股口径，勿混用每10股原值）")

    # 4) 保险给付：仅当题干/选项【明确】含 max/min/较大者/较小者 且证据含多个金额才取数；
    #    若题意含倍数/比例/年龄分段/缴费年限/领取状态等复杂公式，insurance_payout 会返回 None，
    #    此时提示“需按原文给付公式核对”，不硬算（item15）。
    max_ctx = re.search(r"较大者|较高者|孰高|取较大|max", combined, re.I)
    min_ctx = re.search(r"较小者|较低者|孰低|取较小|min", combined, re.I)
    if max_ctx or min_ctx:
        ev_moneys = _dedupe(MONEY_RE.findall(evidence_blob + " " + option_text))
        mode = "max" if max_ctx else "min"
        label = "较大者 max" if mode == "max" else "较小者 min"
        payout = insurance_payout(ev_moneys, mode, context=combined)
        if payout:
            hints.append(f"给付取{label}(" + "、".join(ev_moneys[:4]) + f") = {_fmt_yuan(payout['result'])}（参考，以证据原文给付公式为准）")
        elif _hits_complex_formula(combined):
            hints.append("题目涉及取较大者/较小者但给付公式含倍数/比例/年龄/缴费年限/领取状态等复杂条件，本地无法可靠计算，请严格按证据原文给付公式核对。")

    return hints


def _hits_complex_formula(text: str) -> bool:
    lowered = str(text or "").lower()
    return any(term.lower() in lowered for term in INSURANCE_COMPLEX_FORMULA_TERMS)


def _aligned_ratio_pair(text: str) -> tuple[str, str] | None:
    """识别明确占比结构，返回 (分子金额, 分母金额)。无法识别则不计算。

    支持："8亿元 / 120亿元"、"8亿元占120亿元"、"8亿元占...120亿元"。
    不支持仅出现两个金额但无结构关系的文本，避免倒置相除。

    R7 注意：此处把 MONEY_RE.pattern 内嵌进更大的正则并取 group(1)/group(2)，
    依赖 MONEY_RE 内部全部使用【非捕获组】(?:...)。若日后给 MONEY_RE 添加捕获组，
    这里的组号会错位——修改 MONEY_RE 时必须同步检查本函数（已有单测锁定该契约）。
    """
    text = str(text or "")
    m = re.search(rf"({MONEY_RE.pattern})\s*/\s*({MONEY_RE.pattern})", text)
    if m:
        return m.group(1), m.group(2)
    m = re.search(rf"({MONEY_RE.pattern}).{{0,20}}?占.{{0,30}}?({MONEY_RE.pattern})", text)
    if m:
        return m.group(1), m.group(2)
    return None


def _threshold_direction(text: str) -> str | None:
    """把阈值方向词转成比较关系；只是参考，不覆盖证据原文。"""
    text = str(text or "")
    if any(term in text for term in ("不超过", "不高于", "低于", "以下", "以内")):
        return "<="
    if any(term in text for term in ("不少于", "不低于", "至少", "以上", "达到")):
        return ">="
    if any(term in text for term in ("超过", "高于")):
        return ">"
    return None


def _relation_satisfies(value: float, threshold: float, direction: str) -> bool:
    if direction == "<=":
        return value <= threshold
    if direction == ">=":
        return value >= threshold
    if direction == ">":
        return value > threshold
    if direction == "<":
        return value < threshold
    return False


_YEAR_MONEY_RE = re.compile(
    r"((?:19|20)\d{2})\s*年[^0-9]{0,12}?(-?\d+(?:,\d{3})*(?:\.\d+)?\s*(?:亿元|万元|千元|百万元|亿|万|元))"
)


def _aligned_yoy_from_evidence(evidence_blob: str, target_indicators: list[str] | None = None) -> tuple[str, str, dict] | None:
    """只在证据中能抽到【两个不同年份各自紧邻的金额】时，才返回同比方向，避免跨指标乱算。

    如果题目/选项给出指标，则年份-金额附近必须命中同一目标指标；取年份最大者为 current、次大者为 previous。
    无法可靠对齐返回 None。
    """
    text = evidence_blob or ""
    pairs: list[tuple[str, str]] = []
    for match in _YEAR_MONEY_RE.finditer(text):
        year, money = match.group(1), match.group(2)
        if target_indicators:
            start = max(0, match.start() - 80)
            end = min(len(text), match.end() + 80)
            window = text[start:end]
            if not any(indicator in window for indicator in target_indicators):
                continue
        pairs.append((year, money))
    if len(pairs) < 2:
        return None
    # year -> 第一个出现的金额（保留出现顺序，年份唯一化）
    year_money: dict[str, str] = {}
    for year, money in pairs:
        year_money.setdefault(year, money)
    if len(year_money) < 2:
        return None
    years_sorted = sorted(year_money.keys(), reverse=True)
    cur_year, prev_year = years_sorted[0], years_sorted[1]
    change = yoy_change(year_money[cur_year], year_money[prev_year])
    if not change:
        return None
    return cur_year, prev_year, change


def _dedupe(values: list[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        item = re.sub(r"\s+", "", str(value))
        if not item or item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _dedupe_hints(values: list[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        v = str(value).strip()
        if v and v not in seen:
            seen.add(v)
            result.append(v)
    return result
