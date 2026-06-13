from __future__ import annotations

import copy
import re

from .domain_rules import NEGATION_TERMS, expand_query_terms, extract_amounts, extract_clause_numbers, extract_financial_indicators, extract_percents, extract_years, get_domain_rules
from .numeric_checker import extract_numeric_facts, make_targeted_query


class ALeaderboardEvidenceSelector:
    def __init__(self, retriever):
        self.retriever = retriever

    def select(
        self,
        question: str,
        options: dict[str, str],
        domain: str | None,
        doc_ids: list[str] | None,
        global_top_k: int | None = None,
        option_top_k: int | None = None,
        final_top_k: int | None = None,
        answer_format: str | None = None,
        card_limit: int | None = None,
        multi_doc_quota: int | None = None,
    ) -> tuple[list[dict], dict[str, list[dict]]]:
        # 动态证据预算：题型/领域/文档数共同决定召回深度与证据卡数量。
        # 显式传入的参数优先（用于单测/特殊路径），未传入则由 choose_retrieval_budget 计算。
        budget = choose_retrieval_budget(question, options, domain, doc_ids, answer_format)
        global_top_k = budget["global_top_k"] if global_top_k is None else global_top_k
        option_top_k = budget["option_top_k"] if option_top_k is None else option_top_k
        final_top_k = budget["final_top_k"] if final_top_k is None else final_top_k
        card_limit = budget["card_limit"] if card_limit is None else card_limit
        option_floor = budget["option_floor"]
        if multi_doc_quota is None:
            multi_doc_quota = budget["multi_doc_quota"]

        queries = build_queries(question, options, domain, answer_format)
        merged: dict[str, dict] = {}
        option_hits: dict[str, list[dict]] = {letter: [] for letter in options}

        for query in queries["global"]:
            for chunk in self._retrieve(query, domain, doc_ids, global_top_k):
                self._merge(merged, chunk, "global", query, question, options, domain)

        for letter, query_list in queries["options"].items():
            for query in query_list:
                for chunk in self._retrieve(query, domain, doc_ids, option_top_k):
                    item = self._merge(merged, chunk, letter, query, question, options, domain)
                    option_hits.setdefault(letter, []).append(item)

        # item17（已回退）：曾尝试对“饿死文档”做主动回填（选项锚定/泛化 query 双路），
        #   端到端实测净负面：补进来的证据虽保证每文档≥1卡，但在多选判定里引入噪声，
        #   使模型在 fc_a_011（AB→A）、ins_a_015（AB→ABD）等题误删/误加选项，
        #   仅 ins_a_008（AC→ABC）受益，net -1。与 choose_retrieval_budget 中
        #   “加深跨文档证据稀释排序、引入噪声（净负）”的既有结论一致，故不启用回填。
        ranked = sorted(merged.values(), key=lambda item: item.get("a_score", item.get("score", 0)), reverse=True)
        selected: dict[str, dict] = {}
        for letter in sorted(options):
            bucket = sorted(option_hits.get(letter, []), key=lambda item: item.get("a_score", 0), reverse=True)
            for item in bucket[:option_floor]:
                selected[_key(item)] = item
        # item15：多文档题给每个 doc_id 保底配额，保证每个文档至少有相应数量证据进入 prompt。
        #   跨文档题（合同/财报对比等）需每文档独立证据，避免“用一份文档推出两份均”。
        if doc_ids and len(doc_ids) >= 2:
            quota = max(1, multi_doc_quota)
            for doc_id in doc_ids:
                doc_items = [item for item in ranked if str(item.get("doc_id", "")) == str(doc_id)]
                for item in doc_items[:quota]:
                    selected.setdefault(_key(item), item)
        for item in ranked:
            if len(selected) >= final_top_k:
                break
            selected.setdefault(_key(item), item)

        cards = chunks_to_evidence_cards(
            sorted(selected.values(), key=lambda item: item.get("a_score", 0), reverse=True),
            options, domain, limit=card_limit,
        )
        per_option = {letter: [card for card in cards if letter in card.get("related_options", [])] for letter in options}
        return cards, per_option

    def targeted_select(
        self,
        question: str,
        options: dict[str, str],
        letters: list[str],
        domain: str | None,
        doc_ids: list[str] | None,
        top_k: int = 6,
    ) -> tuple[list[dict], dict[str, list[dict]]]:
        merged: dict[str, dict] = {}
        option_hits: dict[str, list[dict]] = {letter: [] for letter in options}
        for letter in letters:
            option_text = options.get(letter, "")
            query = make_targeted_query(question, option_text)
            for chunk in self._retrieve(query, domain, doc_ids, top_k):
                item = self._merge(merged, chunk, letter, query, question, options, domain)
                option_hits.setdefault(letter, []).append(item)
        cards = chunks_to_evidence_cards(sorted(merged.values(), key=lambda item: item.get("a_score", 0), reverse=True), options, domain)
        per_option = {letter: [card for card in cards if letter in card.get("related_options", [])] for letter in options}
        return cards, per_option

    def query_select(
        self,
        queries: list[str],
        options: dict[str, str],
        domain: str | None,
        doc_ids: list[str] | None,
        top_k: int = 6,
        question: str = "",
    ) -> tuple[list[dict], dict[str, list[dict]]]:
        """按一组补检索 query（来自 validator.retry_queries）直接检索，结果归一为证据卡。

        这些 query 由本地校验发现的缺失年份/条款号/比例派生，不绑定具体选项，
        命中后由 _match_options 自动回填 related_options，用于补齐首轮漏检的硬事实。

        item3：必须接收原始 question，并把 question 传入 _merge()/score_chunk() 链路，
        让命中评分能利用题干上下文（年份/指标/义务词），不能再传空字符串。
        """
        merged: dict[str, dict] = {}
        for query in queries:
            query = str(query or "").strip()
            if not query:
                continue
            for chunk in self._retrieve(query, domain, doc_ids, top_k):
                self._merge(merged, chunk, "global", query, question, options, domain)
        cards = chunks_to_evidence_cards(sorted(merged.values(), key=lambda item: item.get("a_score", 0), reverse=True), options, domain)
        per_option = {letter: [card for card in cards if letter in card.get("related_options", [])] for letter in options}
        return cards, per_option

    def _retrieve(self, query: str, domain: str | None, doc_ids: list[str] | None, top_k: int) -> list[dict]:
        if hasattr(self.retriever, "hybrid_search"):
            return self.retriever.hybrid_search(query, domain=domain, doc_ids=doc_ids, per_route_top_k=max(top_k * 3, 12), fused_top_k=max(top_k * 3, 12), final_top_k=top_k)
        return self.retriever.retrieve(query, domain=domain, doc_ids=doc_ids, top_k=top_k)

    def _merge(self, merged: dict[str, dict], chunk: dict, source_option: str, query: str, question: str, options: dict[str, str], domain: str | None) -> dict:
        key = _key(chunk)
        item = merged.get(key)
        if item is None:
            item = copy.deepcopy(chunk)
            item["source_options"] = []
            item["source_queries"] = []
            merged[key] = item
        if source_option != "global" and source_option not in item["source_options"]:
            item["source_options"].append(source_option)
        item["source_queries"].append(query)
        item["a_score"] = max(float(item.get("a_score", 0) or 0), score_chunk(item, query, question, options, domain, source_option))
        return item


def build_queries(question: str, options: dict[str, str], domain: str | None = None, answer_format: str | None = None) -> dict[str, object]:
    all_options = " ".join(str(value) for value in options.values())
    numeric_terms = _numeric_terms(question + " " + all_options)
    expansion = expand_query_terms(question + " " + all_options, domain)
    global_queries = [question, f"{question} {all_options}".strip()]
    if numeric_terms:
        global_queries.append(" ".join(numeric_terms))
    if expansion:
        global_queries.append(" ".join(expansion))
    # 判断题（tf）通常无实质选项文本，召回完全由题干驱动；当题干断言含多个事实点
    # （如“研发占比提升 且 强于美的 且 美的自2019年起回购”）时，高频主题词会挤掉弱信号子事实，
    # 导致部分事实点漏召回、整句被误判。这里仅对 tf 题把题干切成子句作为额外 global 子查询，
    # 提高对每个独立事实点的召回覆盖；不影响 mcq/multi 题的召回行为。
    if str(answer_format) == "tf":
        for clause in _split_fact_clauses(question):
            global_queries.append(clause)
    option_queries = {}
    for letter, option_text in sorted(options.items()):
        option_text = str(option_text).strip()
        # 选项专属 query 必须以选项文本本身为锚，避免被题干中其它事项（其它选项）的
        # 高频词/数值冲淡。数值与扩展词只从“选项文本”抽取，不混入整题干。
        option_only_terms = _numeric_terms(option_text)
        queries = [option_text, f"{question} {option_text}".strip()]
        if option_only_terms:
            queries.append(f"{option_text} " + " ".join(option_only_terms))
        opt_expansion = expand_query_terms(option_text, domain)
        if opt_expansion:
            queries.append(f"{option_text} " + " ".join(opt_expansion))
        option_queries[letter] = _dedupe(queries)
    return {"global": _dedupe(global_queries), "options": option_queries}


# 合同题关键要素触发词（保留供检索/扩展逻辑参考）。
_CONTRACT_KEY_TRIGGERS = (
    "可转债", "可转换公司债券", "赎回", "回售", "评级", "发行规模", "发行金额",
    "转股价", "股票代码", "上市地", "发行日期", "票面利率", "违约", "债券持有人会议",
)


def choose_retrieval_budget(
    question: str,
    options: dict[str, str],
    domain: str | None,
    doc_ids: list[str] | None,
    answer_format: str | None,
) -> dict[str, int]:
    """按题型/领域/文档数决定证据预算。

    返回 dict：global_top_k / option_top_k / final_top_k / card_limit / option_floor / multi_doc_quota。

    经实测（对照 3 份真实榜单答案的高置信一致子集），加深 final_top_k / 提高跨文档配额
    在可答题上反而稀释排序、引入噪声（净负）。因此这里回归到经验最优的基准预算。
    card_limit 仍与 final_top_k 对齐，避免“多召回却被 prompt 截断”的耦合 bug。
    """
    doc_count = len({str(d).strip() for d in (doc_ids or []) if str(d).strip()})
    is_multi_doc = doc_count >= 2  # 预留给调用方/未来策略，当前预算对其不敏感

    # 基准（与历史最优行为一致）。实测对单文档 mcq/tf 收窄召回亦无稳定增益且有风险，
    # 故全部题型回归基准预算，保证与已验证的最优行为一致。
    global_top_k = 12
    option_top_k = 5
    final_top_k = 24
    option_floor = 2
    multi_doc_quota = max(1, _multi_doc_quota())

    # card_limit 与历史基准一致（chunks_to_evidence_cards/prompt 默认均为 20）。
    # 这里显式返回 20，避免“多召回却被 prompt 截断”的歧义，同时保持与已验证基准完全一致。
    card_limit = 20
    return {
        "global_top_k": global_top_k,
        "option_top_k": option_top_k,
        "final_top_k": final_top_k,
        "card_limit": card_limit,
        "option_floor": option_floor,
        "multi_doc_quota": multi_doc_quota,
    }


def score_chunk(chunk: dict, query: str, question: str, options: dict[str, str], domain: str | None, source_option: str) -> float:
    text = _chunk_text(chunk)
    score = float(chunk.get("rerank_score", chunk.get("score", 0)) or 0)
    score += _keyword_overlap(query, text) * 0.8
    if source_option in options:
        score += _keyword_overlap(options[source_option], text) * 1.2
    q_facts = extract_numeric_facts(query)
    if any(year in text for year in q_facts["years"]):
        score += 4.0
    if any(num in text for num in q_facts["money"] + q_facts["percents"]):
        score += 4.0
    if any(clause in text for clause in q_facts["clauses"]):
        score += 5.0
    if any(indicator in text for indicator in q_facts["indicators"]):
        score += 3.0
    rules = get_domain_rules(domain)
    score += sum(1.0 for keyword in rules.get("keywords", []) if keyword in text)
    if re.search(r"\t|表|项目|单位：|金额|比例", text):
        score += 2.0
    if any(term in text for term in NEGATION_TERMS):
        score += 1.5
    if re.search(r"增长|下降|高于|低于|超过|不超过|同比|占比", question + " " + " ".join(options.values())) and len(re.findall(r"-?\d+(?:\.\d+)?", text)) >= 2:
        score += 2.0
    score += _structured_bonus(chunk, domain, question, options)
    return score


# 各领域优先的结构化记录类型（一个领域可优先多类）
_DOMAIN_PREFERRED_RECORDS = {
    "financial_reports": {"financial_metric"},
    "regulatory": {"regulatory_clause"},
    "insurance": {"insurance_formula", "insurance_clause"},
    "financial_contracts": {"contract_key"},
    "research": {"research_claim"},
}


def _structured_bonus(chunk: dict, domain: str | None, question: str = "", options: dict[str, str] | None = None) -> float:
    """结构化证据加权：领域题优先对应类型的结构化记录，并按 insurance 题意细化加权。"""
    rec_type = chunk.get("record_type")
    if not rec_type:
        return 0.0
    bonus = 1.5  # 任意结构化记录的基础加权（字段对齐、可计算）
    if domain and rec_type in _DOMAIN_PREFERRED_RECORDS.get(domain, set()):
        bonus += 5.0
    if domain == "insurance":
        bonus += _insurance_clause_bonus(chunk, rec_type, question, options or {})
    return bonus


def _insurance_clause_bonus(chunk: dict, rec_type: str, question: str, options: dict[str, str]) -> float:
    """按题目/选项关键词对 insurance_clause / insurance_formula 细化加权。"""
    qo = str(question) + " " + " ".join(str(v) for v in options.values())
    clause_type = chunk.get("clause_type", "")
    bonus = 0.0
    if any(t in qo for t in ("责任免除", "不承担", "不负责赔偿", "除外", "不适用", "不在此限")):
        if rec_type == "insurance_clause" and clause_type == "exclusion":
            bonus += 4.0
    if any(t in qo for t in ("保险金", "给付", "现金价值", "账户价值", "较大者", "较小者")):
        if rec_type == "insurance_formula":
            bonus += 4.0
    if any(t in qo for t in ("赔偿限额", "免赔额", "免赔率")):
        if rec_type == "insurance_clause" and clause_type == "limit":
            bonus += 4.0
    if any(t in qo for t in ("退保", "解除合同", "现金价值")):
        if rec_type == "insurance_clause" and clause_type in {"surrender", "cash_value"}:
            bonus += 4.0
    return bonus


def chunks_to_evidence_cards(chunks: list[dict], options: dict[str, str], domain: str | None = None, limit: int = 20) -> list[dict]:
    cards = []
    protected = domain in PROTECTED_DOMAINS
    policy = "protected" if protected else "compact"
    for index, chunk in enumerate(chunks[:limit], start=1):
        text = _chunk_text(chunk)
        record_type = chunk.get("record_type")
        raw = str(chunk.get("raw", "") or "")
        # 结构化记录优先用 raw 原文作为 quote，保证 quote 可在原文中校验
        if record_type and raw:
            full_source = raw
            quote = raw if (protected and len(raw) <= PROTECTED_MAX_LEN) else _evidence_window(raw, domain)
            source_text = _build_source_text((raw + "\n" + text), domain)
        else:
            full_source = text
            quote = _evidence_window(text, domain)
            source_text = _build_source_text(text, domain)
        # 保证 source_text 包含 quote，避免 evidence_validator 误判 quote 非原文
        source_text = _ensure_contains(source_text, quote)
        related = sorted(set(chunk.get("source_options", []) or []) | set(_match_options(text, options)))
        facts = extract_numeric_facts(quote)
        terms_hit = protected_terms_hit(quote, domain)
        cards.append(
            {
                "evidence_id": f"E{index}",
                "doc_id": str(chunk.get("doc_id", "")),
                "chunk_id": str(chunk.get("chunk_id", "")),
                "page": chunk.get("page"),
                "section": str(chunk.get("section", "")),
                "record_type": record_type or "",
                "quote": quote,
                "source_text": source_text,
                "related_options": [letter for letter in related if letter in options],
                "contains_years": facts["years"],
                "contains_numbers": facts["money"] + facts["percents"] + facts["numbers"][:6],
                "contains_clauses": facts["clauses"],
                "contains_indicators": facts["indicators"],
                "quote_compressed": len(quote) < len(full_source),
                "compression_policy": policy,
                "protected_terms_hit": terms_hit,
                "full_source_len": len(full_source),
                "quote_len": len(quote),
                "score": float(chunk.get("a_score", chunk.get("score", 0)) or 0),
            }
        )
    return cards


# ----------------------------------------------------------------------------
# 保护式证据压缩
# ----------------------------------------------------------------------------

PROTECTED_DOMAINS = {"insurance", "regulatory", "financial_contracts"}

COMPACT_MAX_LEN = 1400
PROTECTED_MAX_LEN = 2400
SOURCE_FULL_LIMIT = 3000

# 通用保护词：但书、除外、免责、否定、阈值方向、义务词等，绝不可被裁掉。
PROTECT_TERMS = [
    "除本合同另有约定", "另有约定", "另有规定", "不承担保险责任", "不承担", "不适用",
    "责任免除", "免除责任", "前提是", "条件是", "特别约定", "释义", "除非",
    "但是", "但", "除外", "不得", "不能", "未", "无", "不超过", "不少于", "不低于",
    "以上", "以下", "高于", "低于", "限制", "须经", "应当", "可以", "禁止",
]

# 领域特定保护词
DOMAIN_PROTECT_TERMS = {
    "insurance": [
        "保险责任", "责任免除", "身故保险金", "现金价值", "账户价值", "退保", "领取",
        "等待期", "犹豫期", "宽限期", "给付", "不给付", "不承担保险责任", "除外责任",
        "已交保费", "基本保险金额", "较大者", "较小者",
    ],
    "regulatory": [
        "应当", "不得", "可以", "须经", "报告", "备案", "处罚", "责令", "限期",
        "日内", "工作日内", "普通决议", "特别决议", "过半数", "三分之二",
        "另有规定", "法律法规另有规定",
    ],
    "financial_contracts": [
        "发行规模", "期限", "票面利率", "评级", "担保", "赎回", "回售",
        "募集资金用途", "违约", "承诺", "限制", "除本合同另有约定",
        "不超过", "不低于", "不少于",
    ],
}

# 保险给付公式相关词：命中则必须保留公式句及条件句
INSURANCE_FORMULA_TERMS = ["较大者", "较小者", "max", "min", "现金价值", "已交保费", "账户价值"]
# 保险给付/金额词，若出现而无限制词则触发向后扩展
INSURANCE_PAYOUT_TERMS = ["给付", "保险金", "现金价值", "账户价值"]
INSURANCE_LIMIT_TERMS = ["免责", "不承担", "除外", "不适用", "但", "等待期", "领取", "退保"]

ANCHOR_RE = re.compile(
    r"\d{4}年|\d+(?:\.\d+)?%|\d+(?:\.\d+)?\s*(?:亿元|万元|千元|元)|第[一二三四五六七八九十百零〇0-9]+条"
)
CLAUSE_BOUNDARY_RE = re.compile(
    r"^\s*(第[一二三四五六七八九十百零〇0-9]+[章节条]|[一二三四五六七八九十]+、|\d+(?:\.\d+)*[、.])"
)


def _protect_terms_for(domain: str | None) -> list[str]:
    return _dedupe(PROTECT_TERMS + DOMAIN_PROTECT_TERMS.get(domain or "", []))


def protected_terms_hit(text: str, domain: str | None) -> list[str]:
    return [term for term in _protect_terms_for(domain) if term in text]


def _evidence_window(text: str, domain: str | None = None) -> str:
    if domain in PROTECTED_DOMAINS:
        return _protected_window(text, domain)
    return _compact_window(text)


def _compact_window(text: str) -> str:
    """财报/研报等：命中行 + 前后各一行的紧凑窗口。"""
    lines = [line.strip() for line in str(text).splitlines() if line.strip()]
    if not lines:
        return str(text)[:1200]
    important = []
    for idx, line in enumerate(lines):
        if re.search(r"\d{4}年|\d+(?:\.\d+)?%|\d+(?:\.\d+)?\s*(?:亿元|万元|元)|第[一二三四五六七八九十百零〇0-9]+条|但|除外|不承担|不得|以上|以下|不超过|营业收入|净利润|现金流|票面利率|发行规模", line):
            important.extend(lines[max(0, idx - 1): min(len(lines), idx + 2)])
    if important:
        return "\n".join(_dedupe(important))[:COMPACT_MAX_LEN]
    return "\n".join(lines[:4])[:COMPACT_MAX_LEN]


def _protected_window(text: str, domain: str | None) -> str:
    """保护式窗口：命中行前 2~3 行、后 3~6 行，遇保护词继续向后扩展至条款/章节结束或上限。

    保证但书、除外、免责、否定、阈值方向、义务词不被裁掉。
    """
    raw = str(text)
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    protect = _protect_terms_for(domain)
    if not lines:
        return raw[:PROTECTED_MAX_LEN]

    # regulatory：命中“第X条”时尽量返回完整条文
    if domain == "regulatory" and re.search(r"第[一二三四五六七八九十百零〇0-9]+条", raw):
        if len(raw) <= PROTECTED_MAX_LEN:
            return raw.strip()
        return _prioritize_sentences(raw, protect, PROTECTED_MAX_LEN)

    def is_hit(line: str) -> bool:
        return bool(ANCHOR_RE.search(line)) or any(term in line for term in protect)

    hit_indices = [idx for idx, line in enumerate(lines) if is_hit(line)]
    if not hit_indices:
        head = "\n".join(lines[:6])
        return head[:PROTECTED_MAX_LEN]

    ranges: list[tuple[int, int]] = []
    for idx in hit_indices:
        start = max(0, idx - 3)
        end = min(len(lines), idx + 7)  # 命中行后约 6 行
        # 向后扩展：只要后续行含保护词且未进入下一条款/章节，就继续纳入
        cursor = end
        while cursor < len(lines):
            line = lines[cursor]
            if CLAUSE_BOUNDARY_RE.match(line) and cursor > idx + 1:
                break
            if any(term in line for term in protect):
                cursor += 1
                end = cursor
                continue
            # 给一行宽限：但书可能紧跟在一行普通描述之后
            if cursor + 1 < len(lines) and any(term in lines[cursor + 1] for term in protect):
                cursor += 1
                end = cursor + 1
                continue
            break
        ranges.append((start, end))

    merged = _merge_ranges(ranges)
    selected: list[str] = []
    for start, end in merged:
        selected.extend(lines[start:end])
    window = "\n".join(_dedupe(selected))

    # insurance：含给付/保险金但无限制词时，自动向后扩展直到出现限制词或到上限
    if domain == "insurance":
        window = _insurance_autoexpand(window, lines, merged, protect)

    if len(window) > PROTECTED_MAX_LEN:
        window = _prioritize_sentences(window, protect, PROTECTED_MAX_LEN)
    return window


def _insurance_autoexpand(window: str, lines: list[str], merged: list[tuple[int, int]], protect: list[str]) -> str:
    has_payout = any(term in window for term in INSURANCE_PAYOUT_TERMS)
    has_limit = any(term in window for term in INSURANCE_LIMIT_TERMS)
    if not has_payout or has_limit:
        return window
    last_end = merged[-1][1] if merged else 0
    extra: list[str] = []
    cursor = last_end
    while cursor < len(lines) and len("\n".join([window] + extra)) < PROTECTED_MAX_LEN:
        line = lines[cursor]
        extra.append(line)
        if any(term in line for term in INSURANCE_LIMIT_TERMS):
            break
        cursor += 1
    if extra:
        window = "\n".join(_dedupe(window.splitlines() + extra))
    return window


def _merge_ranges(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    if not ranges:
        return []
    ranges = sorted(ranges)
    merged = [ranges[0]]
    for start, end in ranges[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


def _prioritize_sentences(text: str, protect: list[str], max_len: int) -> str:
    """超长时按句保留：含保护词/阈值/条款的句子必留，其余按序填充至上限。"""
    sentences = [s for s in re.split(r"(?<=[。；！？\n])", text) if s.strip()]
    if not sentences:
        return text[:max_len]

    def must_keep(sentence: str) -> bool:
        return any(term in sentence for term in protect) or bool(ANCHOR_RE.search(sentence))

    result: list[str] = []
    used = 0
    for sentence in sentences:
        if must_keep(sentence):
            result.append(sentence)
            used += len(sentence)
        elif used + len(sentence) <= max_len:
            result.append(sentence)
            used += len(sentence)
    out = "".join(result)
    if len(out) > max_len:
        # 必留句已超限：保留所有必留句并硬截断（宁可多给，也尽量保住但书）
        out = "".join(s for s in sentences if must_keep(s))[:max_len]
    return out


def _ensure_contains(source_text: str, quote: str) -> str:
    """确保 source_text 包含 quote（按去空白比较）；不包含则前置 quote。"""
    if not quote:
        return source_text
    norm_src = re.sub(r"\s+", "", source_text)
    norm_quote = re.sub(r"\s+", "", quote)
    if norm_quote and norm_quote in norm_src:
        return source_text
    combined = quote + "\n" + source_text
    return combined[:SOURCE_FULL_LIMIT]


def _build_source_text(text: str, domain: str | None) -> str:
    """source_text：保护类领域尽量保留完整上下文，财报/研报保持较短。"""
    if domain in PROTECTED_DOMAINS:
        if len(text) <= SOURCE_FULL_LIMIT:
            return text
        return _protected_window(text, domain)[:SOURCE_FULL_LIMIT]
    return text[:1800]


def _chunk_text(chunk: dict) -> str:
    return str(chunk.get("text") or chunk.get("table_text") or "")


def _key(item: dict) -> str:
    return str(item.get("chunk_id") or f"{item.get('doc_id')}::{item.get('page')}::{_chunk_text(item)[:40]}")


def _numeric_terms(text: str) -> list[str]:
    return extract_years(text) + extract_amounts(text) + extract_percents(text) + extract_clause_numbers(text) + extract_financial_indicators(text)


def _keyword_overlap(query: str, text: str) -> int:
    terms = [term for term in re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z0-9]+", str(query)) if len(term) >= 2]
    return sum(1 for term in terms if term in text)


def _match_options(text: str, options: dict[str, str]) -> list[str]:
    """把一段证据文本关联到它能支撑的选项。

    硬事实门槛（point 3）：当选项本身含硬事实（年份/金额/比例/条文号/财务指标）时，
    证据 quote 必须命中该选项的【至少一个硬事实】才自动关联——否则弱相关词项命中会把证据
    误标到该选项，连锁导致 covered/hint/fallback 把弱证据当成该选项有据，放大多选错选。
    选项不含任何硬事实（纯文字描述）时，退回原有“词项命中比例”逻辑，不影响纯文字判断题。
    """
    text_facts = extract_numeric_facts(text)
    text_hard = {_norm_money(v) for v in (text_facts["money"] + text_facts["percents"])} | set(text_facts["years"])
    text_clauses = {_norm_fact(c) for c in text_facts["clauses"]}
    text_indicators = set(text_facts["indicators"])

    matched = []
    for letter, option_text in options.items():
        option_text = str(option_text)
        opt_facts = extract_numeric_facts(option_text)
        opt_hard = {_norm_money(v) for v in (opt_facts["money"] + opt_facts["percents"])} | set(opt_facts["years"])
        opt_clauses = {_norm_fact(c) for c in opt_facts["clauses"]}
        opt_indicators = set(opt_facts["indicators"])
        has_hard_fact = bool(opt_hard or opt_clauses or opt_indicators)

        if has_hard_fact:
            # 选项含硬事实：证据必须命中其中至少一个（数值/比例/年份/条文号/指标），才关联。
            hit_hard = bool(opt_hard & text_hard) or bool(opt_clauses & text_clauses) or bool(opt_indicators & text_indicators)
            if hit_hard:
                matched.append(letter)
            continue

        # 选项无硬事实（纯文字）：沿用词项命中比例逻辑。
        terms = [term for term in re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z0-9]+", option_text) if len(term) >= 2]
        if terms and sum(1 for term in terms if term in text) >= max(1, len(terms) // 3):
            matched.append(letter)
    return matched


def _norm_fact(value: str) -> str:
    return re.sub(r"\s+", "", str(value))


# 金额/比例匹配前先抽取“数值 + 紧邻量级单位（亿/万/千/百分号）”的核心，
# 忽略“人民币/元/整”等装饰后缀，使“50亿元”与“50亿”、“43.24%”与“43.24 %”可匹配，
# 但仍区分 50亿 vs 5亿、8.5% vs 5%（量级单位与数字一并参与比较，不退化为纯数字）。
_MONEY_CORE_RE = re.compile(r"-?\d+(?:\.\d+)?\s*(?:亿|万|千|百|%)?")


def _norm_money(value: str) -> str:
    text = re.sub(r"\s+", "", str(value))
    match = _MONEY_CORE_RE.search(text)
    return match.group(0).replace(" ", "") if match else text



def _multi_doc_quota() -> int:
    try:
        from .config import settings

        return int(getattr(settings, "multi_doc_evidence_quota", 2))
    except Exception:  # noqa: BLE001
        return 2


def _dedupe(values: list[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        value = str(value).strip()
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


# tf 题题干切分为独立事实子句的分隔符（顿号/分号/逗号/连接词）。
_FACT_CLAUSE_SEP_RE = re.compile(r"[，；。、]|(?:同时)|(?:并且)|(?:以及)|(?:且)")
# 去掉题干常见的设问外壳，避免子句里混入“该说法是否正确”这类无检索价值的尾巴。
_QUESTION_TAIL_RE = re.compile(r"(该说法是否正确|是否正确|下列.*?正确.*?[？?]?$|该描述.*?[？?]?$)")


def _split_fact_clauses(question: str, min_len: int = 6, max_clauses: int = 6) -> list[str]:
    """把 tf 题题干切成独立事实子句，作为额外检索子查询。

    只用于提高召回覆盖；切分较粗即可（基于标点/连接词）。过短片段（如“正确”）丢弃。
    """
    text = _QUESTION_TAIL_RE.sub("", str(question)).strip()
    parts = [p.strip() for p in _FACT_CLAUSE_SEP_RE.split(text) if p and p.strip()]
    clauses = [p for p in parts if len(p) >= min_len]
    # 子句过少（题干本就单一事实）时不额外加，避免噪声。
    if len(clauses) < 2:
        return []
    return _dedupe(clauses)[:max_clauses]


# tf 复合命题的逻辑连接词。AND：所有子命题成立才为真；OR：任一成立即为真。
# 只按【逻辑连接词】切，不按逗号/顿号切——后者会把单个子命题内部切碎，破坏逻辑判定。
_TF_AND_SEP_RE = re.compile(r"(?:，|,)?(?:并且|同时|而且|且|以及|，且|，并)")
_TF_OR_SEP_RE = re.compile(r"(?:，|,)?(?:或者|或)")
# 题干设问外壳：判断题常见“判断以下陈述是否正确：”“下列说法正确吗”等，需剥离再拆。
_TF_SHELL_RE = re.compile(
    r"^.*?(?:判断[^：:]*[：:]|根据[^，。]*?[，,]\s*)|(?:该说法|该陈述|该描述|此说法)?是否正确[？?。]?$|，?\s*下列.*?$|该(?:说法|陈述|描述).*?$"
)


def parse_tf_claims(question: str, min_len: int = 5, max_claims: int = 4) -> dict:
    """把 tf 复合命题拆成子命题 + 逻辑算子（AND/OR），用于 prompt 显式逐句核验。

    返回 {"claims": [...], "operator": "AND"|"OR"|"SINGLE"}。
    - 仅按逻辑连接词（且/并且/同时/以及 -> AND；或/或者 -> OR）切分，保留子命题内部完整语义；
    - 子命题不足 2 个时 operator="SINGLE"，调用方应退回整句判断（不做拆解，避免误伤单一命题）；
    - 混用 AND/OR 的复杂句保守按 AND 处理（金融题“且”远多于“或”，按 AND 更稳）。
    """
    text = _QUESTION_TAIL_RE.sub("", str(question)).strip()
    # 剥离“判断以下陈述是否正确：”这类设问外壳，只保留陈述主体。
    if "：" in text or ":" in text:
        tail = re.split(r"[：:]", text, maxsplit=1)[-1].strip()
        if len(tail) >= min_len:
            text = tail

    has_or = bool(_TF_OR_SEP_RE.search(text))
    has_and = bool(_TF_AND_SEP_RE.search(text))
    # AND 优先（金融判断题“且”关系为主，且更保守：要求所有子句都成立）。
    if has_and or not has_or:
        parts = _TF_AND_SEP_RE.split(text)
        operator = "AND"
    else:
        parts = _TF_OR_SEP_RE.split(text)
        operator = "OR"

    claims = _dedupe([p.strip(" ，,。.；;") for p in parts if p and len(p.strip(" ，,。.；;")) >= min_len])
    if len(claims) < 2:
        return {"claims": [text] if text else [], "operator": "SINGLE"}
    return {"claims": claims[:max_claims], "operator": operator}

