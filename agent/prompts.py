from __future__ import annotations

import json
import re

from .evidence_selector import parse_tf_claims


def format_options(options: dict[str, str]) -> str:
    return "\n".join(f"{key}. {value}" for key, value in sorted(options.items()))


# 五个领域的专属判题规则。每条都针对该领域最易出错的判断点，注入到通用规则之后。
# 纯指令文本，不含任何具体年份/公司/条款号等数据集专有信息。
DOMAIN_JUDGE_RULES: dict[str, list[str]] = {
    "financial_reports": [
        "财报题：数值必须核对【年份 + 指标口径 + 单位】三者一致，"
        "区分合并/母公司报表、本期/上年同期、营业收入/营业总收入、归母净利润/净利润等口径差异。",
        "比较类（增长/下降/高于/低于/同比/占比）必须有两个可比口径一致的数值，"
        "不能用百分比列（占比/同比增减）冒充绝对金额，也不能跨口径或跨报表相减。",
        "读表格时警惕：列错位（数值与表头/年份未对齐）、负号或括号表示的负数、"
        "百分比单位（%）与绝对金额混淆、每股指标（元/股）与总额混淆；存疑时判 insufficient 而非猜测。",
        "record_type=financial_metric 的证据字段已对齐（指标/年份/单位/数值），可优先采信，但仍需与题干口径匹配。",
    ],
    "regulatory": [
        "监管题：以法规条文原文为准，核对【条文号 + 适用主体 + 义务/限制 + 时限/比例 + 例外条件】。",
        "严格区分义务情态词：应当/必须/不得/禁止/可以；以及阈值方向：以上/以下/不超过/不低于、"
        "过半数/三分之二、X日内/工作日内。这些词改变结论，不可忽略或互换。",
        "存在「另有规定/法律法规另有规定/但书/除外」时，例外条款优先于一般条款。",
        "判断某事项的表决方式或是否须审议时，以证据中对应法条的列举/阈值为准；"
        "仅当题目确实涉及修改/修订公司章程（含在章程中增设决议事项、变更表决门槛）时，"
        "才依据「章程的修改须特别决议」这一规则，不要把它套用到与章程修改无关的监管题。",
        "信息披露/备案/报告义务类题目，重点核对时限（日内/工作日内）、披露主体与触发情形；"
        "处罚/监管措施类题目，核对违法情形与对应措施（责令改正/警告/罚款/市场禁入等）的对应关系。",
        "严格区分【监管规定/法条/证监会认定的事实】与【当事人的申辩/陈述/抗辩意见】："
        "处罚决定书或裁定中常引用当事人申辩（如『当事人认为……不存在连续状态、已过时效』），"
        "这类申辩若被监管机关『不予采纳/予以驳回』，则不能作为某选项 supported 的依据；"
        "只有监管机关明确采纳或作为认定结论的表述，才可支持选项。把被驳回的申辩当成规定即属误判。",
    ],
    "insurance": [
        "保险题：核对【责任类型 + 触发条件 + 计算公式 + 免责/除外 + 期间约定 + 退保/现金价值/账户价值】。",
        "给付金额题必须按条款公式判断（如较大者/较小者、已交保费 vs 现金价值 vs 账户价值），"
        "区分身故/全残/满期/退保/医疗报销等不同给付情形。",
        "「责任免除/不承担/不负责赔偿/除外/不在此限」属免责条款，优先级高于保险责任正面描述；"
        "判断是否赔付时必须先排除免责情形。",
        "严格区分各类期间：等待期/观察期（保单生效后免责的初期）、犹豫期（可全额退保的初期，常10/15日）、"
        "宽限期（逾期缴费仍有效的期间，常60日）、保证续保期；不要混用，期间天数以条款为准。",
        "医疗/健康险核对：免赔额、免赔率、赔偿限额、报销比例、是否扣除社保已报销部分、免赔天数、续保条件与费率调整。",
    ],
    "financial_contracts": [
        "合同/债券题：核对【债券名称 + 发行规模 + 期限 + 票面利率 + 评级 + 担保/增信 + 回售/赎回 + 募集资金用途 + 违约/承诺义务】。",
        "条款阈值与方向词（不超过/不低于/不少于）、行权条件（回售/赎回触发条件）、"
        "「除本合同另有约定」等限制必须严格按原文，不可放宽。",
        "区分发行人承诺、投资者权利（回售权等）与触发条件三者，不要混为一谈。",
        "核对募集资金用途及其变更程序、偿债保障措施（偿债资金来源、专户管理）、"
        "违约事件定义与交叉违约条款、受托管理人职责与债券持有人会议的决议事项及表决比例。",
        "信用评级题区分主体评级与债项评级、评级机构与评级展望；不要把不同评级口径混为一谈。",
    ],
    "research": [
        "研报题：区分【客观数据/事实】与【分析师主观观点/预测/评级/目标价】，"
        "题目问观点时以研报表述为准，不要用常识替换研报结论。",
        "核对预测口径：预测年度、同比/环比、盈利预测、目标价、评级（买入/增持/中性/维持/首次覆盖）"
        "及其对应主体与时间范围。",
        "风险提示、预测假设、适用时间范围属观点成立的限定条件，判断选项时必须一并考虑；"
        "选项若忽略或违背研报明示的假设/风险，应判 refuted 或 insufficient。",
        "估值题核对估值方法（市盈率/市净率/PE/PB/DCF/EV-EBITDA 等）、可比公司选取与估值结论的一致性；"
        "不要用题目未给出的估值方法或可比口径反推。",
    ],
}

# 各领域裁判 persona（system message），强化领域视角但不放松“只看证据”的硬约束。
DOMAIN_SYSTEM_PERSONA: dict[str, str] = {
    "financial_reports": "你是严格的财报数据核查裁判，精于年份/口径/单位对齐与表格读数，只基于给定 evidence 输出 JSON。",
    "regulatory": "你是严格的金融监管法规裁判，精于条文号/义务词/例外条款，只基于给定 evidence 输出 JSON。",
    "insurance": "你是严格的保险条款裁判，精于责任/免责/给付公式/各类期间/退保，只基于给定 evidence 输出 JSON。",
    "financial_contracts": "你是严格的债券/合同条款裁判，精于发行要素/回售赎回/偿债保障/违约条款/阈值方向，只基于给定 evidence 输出 JSON。",
    "research": "你是严格的研报观点核查裁判，精于区分事实与分析师观点/预测/估值，只基于给定 evidence 输出 JSON。",
}

_DEFAULT_SYSTEM = "你是严格的金融长文档证据裁判，只基于给定 evidence 输出 JSON。"


def _domain_rules_block(domain: str | None) -> str:
    rules = DOMAIN_JUDGE_RULES.get(domain or "", [])
    if not rules:
        return ""
    lines = "\n".join(f"- {rule}" for rule in rules)
    return f"\n本领域（{domain}）专项判断要点：\n{lines}\n"


def _judgements_template(letters: list[str]) -> str:
    """按实际选项字母生成 judgements 模板，避免判断题（仅 A/B）诱导模型臆造 C/D。"""
    items = []
    for i, letter in enumerate(letters):
        items.append(
            f'    "{letter}": {{"status": "supported|refuted|insufficient", '
            f'"confidence": 0.0, "evidence_ids": ["E{i + 1}"], "reason": "一句话"}}'
        )
    return ",\n".join(items)


def _final_answer_hint(answer_format: str, letters: list[str]) -> str:
    joined = "".join(letters)
    if answer_format == "tf":
        return f"{'|'.join(letters)}（判断题只输出一个字母，且只能在 {joined} 中选）"
    if answer_format == "multi":
        return f"{joined} 中的一个或多个字母组合，按字母排序，如 {''.join(letters[:2]) if len(letters) >= 2 else joined}"
    return f"{'|'.join(letters)}（单选题只输出一个字母）"


# 传给模型的证据卡只保留判题必需字段，丢弃 source_text（最大 token 占用，约占整卡 50%+）
# 与纯诊断/计量字段（compression_policy/score/quote_len 等）。quote 已是保护式窗口，足够判题。
_PROMPT_CARD_KEYS = (
    "evidence_id", "doc_id", "section", "record_type", "quote", "related_options",
    "contains_years", "contains_numbers", "contains_clauses", "contains_indicators",
)


def _doc_ordinal_map(doc_ids: list[str] | None) -> dict[str, int]:
    """把 doc_ids 列表顺序映射为序号（第几份文档）。题干常以“第一份/第二份文档”指代，
    而题面 doc_ids 的顺序即为金标准序号；该映射必须显式告诉模型，否则模型只能猜，易猜反
    （doc_ordinal_unmapped bug）。"""
    mapping: dict[str, int] = {}
    for index, doc_id in enumerate(doc_ids or []):
        key = str(doc_id).strip()
        if key and key not in mapping:
            mapping[key] = len(mapping) + 1
    return mapping


_ORDINAL_CN = ["", "第一份", "第二份", "第三份", "第四份", "第五份", "第六份"]

# 题干是否以“第N份/两份/这两份…文档”等序号措辞指代文档。只有此时 doc_ids 顺序才承担
# “第一份/第二份”的语义，才需要把序号映射喂给模型；否则（题干用文档名/编号指代）注入序号
# 反而是噪声，会干扰判题（doc_ordinal_unmapped 修复必须收敛到真正用序号指代的题）。
_ORDINAL_QUESTION_RE = re.compile(r"第[一二三四五六七八九十1-9]+份|两份文档|这两份|各份文档|两个文档|前一份|后一份")


def _question_uses_ordinal(question: str | None) -> bool:
    return bool(_ORDINAL_QUESTION_RE.search(str(question or "")))


def _doc_ordinal_block(doc_ids: list[str] | None) -> str:
    """生成“第N份文档=doc_id”的映射说明块，仅在 2+ 文档时给出。"""
    mapping = _doc_ordinal_map(doc_ids)
    if len(mapping) < 2:
        return ""
    lines = []
    for doc_id, ordinal in mapping.items():
        label = _ORDINAL_CN[ordinal] if ordinal < len(_ORDINAL_CN) else f"第{ordinal}份"
        lines.append(f"{label}文档 = doc_id「{doc_id}」")
    joined = "；".join(lines)
    return (
        "\n文档序号映射（题干中“第一份/第二份…文档”严格按此对应，不得猜测或颠倒）：\n"
        f"{joined}\n"
        "判断涉及“第N份文档”的选项时，必须用上面映射先确定对应 doc_id，再只用该 doc_id 的证据 quote 判断。\n"
    )


def _tf_claims_block(question: str, answer_format: str) -> str:
    """tf 复合命题逐句核验块：把“P 且 Q / P 或 Q”拆开，要求 judge 逐子命题对照证据。

    只在 tf 题且能拆出 2+ 子命题时给出（单一命题不注入，避免画蛇添足）。
    强调：仅“未检索到证据”不等于“被反证”，避免把信息缺失误判为命题错误（这正是当前 tf 错题的根因）。
    """
    if str(answer_format) != "tf":
        return ""
    parsed = parse_tf_claims(question)
    claims = parsed.get("claims", [])
    operator = parsed.get("operator", "SINGLE")
    if operator == "SINGLE" or len(claims) < 2:
        return ""
    claim_lines = "\n".join(f"  ({i + 1}) {c}" for i, c in enumerate(claims))
    if operator == "AND":
        rule = (
            "这是【联言命题（且）】：必须每个子命题都能在证据 quote 中找到直接支持，整句才成立（选“正确”）；\n"
            "  只要有任一子命题被证据【明确反证】（证据给出相反的事实/数值/口径），整句即不成立（选“错误”）。\n"
            "  注意：某子命题在证据中“未提及/没检索到”属于证据不足，不能据此直接判“错误”——\n"
            "  只有证据【正面否定】该子命题时才算反证。请先逐句核对每个子命题，再合成最终判断。"
        )
    else:  # OR
        rule = (
            "这是【选言命题（或）】：只要任一子命题被证据直接支持，整句即成立（选“正确”）；\n"
            "  只有所有子命题都被证据反证或均无支持时，整句才不成立。请先逐句核对每个子命题，再合成最终判断。"
        )
    return (
        "\n复合命题逐句核验（先拆解、逐句对照证据，再合成）：\n"
        f"{claim_lines}\n"
        f"{rule}\n"
    )


def _compact_cards_for_prompt(evidence_cards: list[dict], options: dict[str, str], limit: int = 20, doc_ordinal: dict[str, int] | None = None) -> list[dict]:
    """精简证据卡用于 prompt：去掉 source_text 与诊断字段，并在保证每个选项至少有相关证据的前提下截断。

    截断策略：先把每个选项的相关证据（related_options 命中）各保底纳入，再按原顺序（已按分数降序）
    补满至 limit。这样既省 token，又不会因截断而漏掉某选项的唯一证据（多选漏选风险）。

    注意：默认 limit 与 selector 产出的卡数一致（20），即默认只做"字段精简"而不删证据——
    字段精简单独已带来 50~66% 的 prompt token 下降，且不改变证据集合，不影响准确率。
    如需进一步压缩可调小 limit，但会有删掉关键证据导致多选漏选/单选改判的风险。
    """
    if not evidence_cards:
        return []
    by_id = {card.get("evidence_id"): card for card in evidence_cards}
    kept_ids: list[str] = []

    # 1) 每个选项保底：取该选项相关证据中排序最高的一张
    for letter in sorted(options):
        for card in evidence_cards:
            if letter in (card.get("related_options") or []):
                eid = card.get("evidence_id")
                if eid not in kept_ids:
                    kept_ids.append(eid)
                break
    # 2) 按原顺序补满
    for card in evidence_cards:
        if len(kept_ids) >= limit:
            break
        eid = card.get("evidence_id")
        if eid not in kept_ids:
            kept_ids.append(eid)

    compact: list[dict] = []
    for eid in kept_ids:
        card = by_id.get(eid, {})
        slim = {key: card[key] for key in _PROMPT_CARD_KEYS if key in card and card[key] not in (None, "", [])}
        # 注入文档序号（第几份），让模型把证据 doc_id 与题干“第N份文档”对齐，避免序号猜反。
        if doc_ordinal:
            ordinal = doc_ordinal.get(str(card.get("doc_id", "")).strip())
            if ordinal:
                slim["doc_ordinal"] = ordinal
        compact.append(slim)
    return compact


def a_leaderboard_option_judge_prompt(
    question: str,
    options: dict[str, str],
    answer_format: str,
    evidence_cards: list[dict],
    computed_hints: list[str] | None = None,
    domain: str | None = None,
    card_limit: int = 20,
    focus_letters: list[str] | None = None,
    doc_ids: list[str] | None = None,
) -> list[dict[str, str]]:
    """构造判题 prompt。

    item18 局部重判：focus_letters 非空时，只判这些待重判选项，且只传与这些选项相关的证据卡
    （related_options 命中 focus_letters，外加少量未归属任何选项的通用证据作为上下文），
    不重新塞回完整 source_text，保持 token 精简。其余选项沿用第一轮结果（由上层 merge 保留）。
    """
    hints_block = "\n".join(f"- {hint}" for hint in (computed_hints or [])) or "无"
    domain_block = _domain_rules_block(domain)
    # 仅当题干或【选项】真正用“第N份/两份文档”指代时才启用文档序号映射，避免给按名/编号指代的题注入噪声。
    # 注意：序号指代常出现在选项里（如“第二份文档披露的初始转股价格为…”），题干本身可能不含序号词，
    #   因此必须同时检查选项文本，否则 doc_ordinal 漏注入，judge 只能猜文档归属（doc_ordinal_in_options bug）。
    options_text = " ".join(str(v) for v in (options or {}).values())
    use_ordinal = _question_uses_ordinal(question) or _question_uses_ordinal(options_text)
    doc_ordinal = _doc_ordinal_map(doc_ids) if use_ordinal else {}
    doc_ordinal_block = _doc_ordinal_block(doc_ids) if use_ordinal else ""
    all_letters = sorted(options.keys()) or ["A", "B", "C", "D"]
    focus = [l for l in (focus_letters or []) if l in options]
    judge_letters = focus or all_letters
    letters_str = "/".join(all_letters)
    judgements_tpl = _judgements_template(judge_letters)
    final_hint = _final_answer_hint(answer_format, all_letters)
    # tf 复合命题逐句核验块（point 4）：把“P 且 Q / P 或 Q”显式拆开，要求逐子命题对照证据，
    #   只有在某子命题被证据【明确反证】时才据此判错误，仅“未检索到”不等于反证。
    tf_claims_block = _tf_claims_block(question, answer_format)
    if focus:
        # 只保留与待重判选项相关的证据 + 少量未归属选项的通用证据（上下文），其余不传，省 token。
        relevant = [
            card for card in evidence_cards
            if (set(card.get("related_options", []) or []) & set(focus)) or not card.get("related_options")
        ]
        prompt_cards = _compact_cards_for_prompt(relevant, {l: options[l] for l in focus}, limit=card_limit, doc_ordinal=doc_ordinal)
        focus_note = (
            f"\n本次为局部复核：只对以下选项重新判断：{'/'.join(judge_letters)}。"
            "其余选项已在上一轮判定，无需输出。只依据下方与这些选项相关的证据判断。\n"
        )
    else:
        prompt_cards = _compact_cards_for_prompt(evidence_cards, options, limit=card_limit, doc_ordinal=doc_ordinal)
        focus_note = ""
    user = f"""题型：{answer_format}
题干：{question}
选项：
{format_options(options)}
本地数值/条款提示：
{hints_block}
{doc_ordinal_block}{tf_claims_block}证据 JSON：
{json.dumps(prompt_cards, ensure_ascii=False)}
{focus_note}
你是 A 榜金融长文档问答裁判。只能根据 evidence 判断，不允许使用常识、训练记忆或外部信息。
本题仅有以下选项：{letters_str}。只对这些选项作判断，不要臆造其它选项。
判断规则：
1. 每个选项独立判断 supported/refuted/insufficient。
2. 没有证据就是 insufficient，不要猜。
3. supported 必须有 evidence_ids；refuted 也必须有 evidence_ids。
4. 每个选项的 evidence_ids 只能引用与该选项直接相关的证据（证据 related_options 含该选项，或内容明确对应该选项所述事项），不要跨选项串用证据。
5. 必须高度重视保护词与条件词，它们直接改变结论，绝不可忽略或与近义词互换：
   「但、但是、除外、不承担、不负责赔偿、不适用、不在此限、不超过、不低于、不少于、以上、以下、另有约定、另有规定、应当、不得、须经」。
   存在例外/否定/免责/阈值方向/义务情态条件时，优先以这些条件为准判断。
6. 金融数值比较必须核对单位、年份、口径；增长/下降/高于/低于必须有可比较数值。
6b. 区间/范围类表述（如"数值在 X 至 Y 之间""介于 X 到 Y""不低于 X 且不高于 Y"）：必须把证据中【涉及的每一个数值】逐一与区间边界比对，只要有任一数值越过边界（如声称"63% 至 66% 之间"但实际存在 66.38%，已超过 66% 上界），即判 refuted；不可只看部分数值落在区间内就 supported。
7. 表格证据必须核对表头、行名、单位和相邻数据。record_type 为 financial_metric/regulatory_clause/insurance_formula/insurance_clause/contract_key/research_claim 的证据是离线结构化抽取结果，字段已对齐，可优先采信，但仍需与题干口径一致。
8. 「本地数值/条款提示」（含占比、同比、阈值、给付、跨文档比较等）只是本地预计算的【辅助核对线索】，不能替代证据，也不能作为 supported/refuted 的依据。其中“跨文档/跨证据比较”提示会指出哪些事实可比、哪些口径（年份/单位/工作日 vs 日）需注意，帮助你避免跨文档混用数据；但任何 supported/refuted 结论都必须能在 evidence quote 原文中找到直接支持，并在 evidence_ids 中引用对应证据；提示与证据原文冲突时，一律以证据原文为准，绝不能仅凭提示判定。
9. 多选题按选项独立判断，final_answer 只由 supported 选项组成并按字母排序。多选无部分分（漏选/错选/多选均判全错），因此每个判为 supported 的选项都必须能在证据 quote 中找到直接、明确支持其完整表述的原文；只要该选项含有未被证据支持的部分（数值、条件、主体、口径、时限任一处偷换或缺证），即判 insufficient，不要因"大致相关/看起来合理"就选入。不要为了凑答案而把存疑选项标 supported，也不要无依据地全选。
9b. 题干设问限定：若题干限定了范围（如"哪些情形需要满足特定金额门槛/审批程序"、"哪些属于X类"、"符合Y要求的有哪些"），则某选项即使其陈述本身为真，但若不落入题干限定的范围，仍不应选入。务必逐选项回答"它是否属于题干问的那一类"：例如题干问"需要履行特定内部审批程序（如须经董事会/股东会审议）或满足特定金额门槛（如金额达到某数额才触发义务）的情形"，则"按时限保存记录""保存至调查结束"这类【时限/保存义务】既不是审批程序也不是金额门槛，即使表述属实也不得选入。先用题干设问筛掉"虽真但不切题"的选项，再判其余。
9c. 主体/口径一致性（防偷换）：选项与证据必须是【同一主体、同一指标口径】才可 supported。常见偷换：把甲公司的数据安到乙公司（如把 A 公司预测说成 B 公司预测）、把"贡献率/占比"与"复合增速"互换、把"支撑业务增长"偷换为"完善管理体系"、把绝对值与比率互换。证据主体或口径与选项不完全一致时判 insufficient/refuted，不可仅因主题相近就 supported。
10. 单选/判断题只能输出一个字母；多选可输出多个字母组合。final_answer 只能由本题实际存在的选项（{letters_str}）组成。
11. 不允许输出解释性长文，只输出 JSON，且 JSON 之外不要附加任何文字。
{domain_block}
只输出 JSON，格式必须为：
{{
  "judgements": {{
{judgements_tpl}
  }},
  "final_answer": "{final_hint}"
}}"""
    system = DOMAIN_SYSTEM_PERSONA.get(domain or "", _DEFAULT_SYSTEM)
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
