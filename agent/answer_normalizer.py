from __future__ import annotations

import re
from typing import Any


VALID_OPTIONS = "ABCD"

# tf 判断题选项文本的语义词典：用于把“正确/错误”动态映射到具体字母，而非假设 A=正确
TF_TRUE_WORDS = ("正确", "对", "是", "支持", "成立", "符合", "true", "yes", "√", "✓", "认同", "赞成")
TF_FALSE_WORDS = ("错误", "错", "否", "不对", "不正确", "不支持", "不成立", "不符合", "反对", "false", "no", "×", "✗")


def normalize_status(status: Any) -> str:
    value = str(status or "").strip().lower()
    if value in {"supported", "support", "true", "yes", "correct"}:
        return "supported"
    if value in {"refuted", "refute", "false", "no", "incorrect"}:
        return "refuted"
    return "insufficient"


def normalize_answer(answer: str | None, answer_format: str, allow_empty: bool = False) -> str:
    """把任意答案文本归一为合法字母组合。

    item8：无有效字母时【不再默认返回 "A"】。
    - allow_empty=True（默认上层显式传入）：无字母时返回 ""，由上层 fallback 逻辑处理空/unknown 状态；
    - allow_empty=False：为兼容历史直接调用（如错误兜底路径会显式传入 "A"），无字母时也返回 ""，
      不再静默猜 A。需要合法答案的最终出口由上层 legalize_answer 负责。
    """
    letters = re.findall(r"[A-D]", (answer or "").upper())
    if answer_format == "tf":
        # tf 只允许 A/B，杜绝 ABCD 兜底扩张进来（应修6）
        letters = [letter for letter in letters if letter in ("A", "B")]
    if answer_format == "multi":
        return "".join(sorted(set(letters)))
    return letters[0] if letters else ""


def synthesize_answer(judgements: dict, answer_format: str, options: dict | None = None, model_answer: str | None = None, evidence_cards: list[dict] | None = None) -> dict:
    """根据判断结果合成答案。

    核心原则（item8/9/10）：无 supported 且无可靠信号时【不静默猜 A】，而是：
    1) 优先用有 evidence_ids 的 refuted/insufficient 分布、证据覆盖、model_answer 合法性、confidence；
    2) 仍无可靠信号 -> 返回空答案 + fallback_reason + needs_review，由上层处理为合法但带强 warning 的答案。
    """
    normalized = {letter: normalize_judgement(judgements.get(letter, {})) for letter in sorted((options or {}).keys() or VALID_OPTIONS)}
    covered = _covered_letters(evidence_cards)
    insufficient_options = [letter for letter, item in normalized.items() if item["status"] == "insufficient"]

    if answer_format == "tf":
        answer = _tf_answer_from_judgements(normalized, options or {})
        if answer:
            return _result(answer, answer_format, False, "", insufficient_options)
        # tf 无明确支持/否定信号：用证据覆盖 + confidence 选较可信的一侧，仍标记 needs_review
        candidate = _signal_single_fallback(normalized, covered, options or {}, allowed=("A", "B"))
        return _result(candidate, answer_format, True, "tf_no_supported_judgement", insufficient_options, needs_review=True)

    supported = _supported_items(normalized)
    if answer_format == "multi":
        if supported:
            answer = normalize_answer("".join(letter for letter, _item in supported), "multi")
            if answer:
                return _result(answer, answer_format, False, "", insufficient_options)
            # supported 但无 evidence_ids（理论上已被上游过滤）：保守走信号兜底
            return _result(_multi_signal_fallback(normalized, covered), answer_format, True, "multi_supported_without_evidence", insufficient_options, needs_review=True)
        # item9：无 supported。保守兜底：只接受有自身证据且未被反证的选项，否则空（不靠 model_answer 猜）。
        candidate, reason = _multi_no_supported(normalized, covered)
        all_insufficient = all(item["status"] == "insufficient" for item in normalized.values())
        fallback_reason = reason or ("multi_all_insufficient" if all_insufficient else "multi_no_supported")
        return _result(candidate, answer_format, True, fallback_reason, insufficient_options, needs_review=True)

    # mcq
    if len(supported) == 1:
        return _result(supported[0][0], answer_format, False, "", insufficient_options)
    if len(supported) > 1:
        supported.sort(key=lambda pair: (-float(pair[1].get("confidence", 0) or 0), -len(pair[1].get("evidence_ids", [])), pair[0]))
        return _result(supported[0][0], answer_format, False, "", insufficient_options)

    # item10：mcq 无 supported。优先 refuted/insufficient 分布 + 证据覆盖 + model_answer + confidence；
    # 仍无可靠信号 -> 空答案 + needs_review，不静默猜 A。
    candidate = _signal_single_fallback(normalized, covered, options or {}, model_answer=model_answer)
    return _result(candidate, answer_format, True, "mcq_no_supported", insufficient_options, needs_review=True)


def _result(answer: str, answer_format: str, fallback_used: bool, fallback_reason: str, insufficient_options: list[str], needs_review: bool = False) -> dict:
    normalized_answer = normalize_answer(answer, answer_format)
    return {
        "answer": normalized_answer,
        "fallback_used": fallback_used,
        "fallback_reason": fallback_reason,
        "insufficient_options": insufficient_options,
        "needs_review": needs_review or (fallback_used and not normalized_answer),
    }


def _covered_letters(evidence_cards: list[dict] | None) -> set[str]:
    covered: set[str] = set()
    for card in evidence_cards or []:
        covered.update(card.get("related_options", []) or [])
    return covered


def _multi_no_supported(normalized: dict, covered: set[str]) -> tuple[str, str]:
    """multi 无 supported 时的【保守】兜底（point 6）。

    关键修正：不再用 model_answer 反推，也不再凭 confidence 把 insufficient 选项选入。
    原因：judge 对 insufficient 给出的 confidence 表示“对‘证据不足’这一判断的确信度”，
    并非“该选项成立的可能性”。旧逻辑把高 confidence 的 insufficient 当成候选，会选中
    reason 明确写着“无证据”的选项（如 ins_a_009 把自证不存在的 D 选了进去）。

    现在只接受“有直接证据支持、且未被反证”的选项；没有这样的选项就返回空，交上层 legalize，
    宁可空也不猜。
    """
    candidate = _multi_signal_fallback(normalized, covered)
    if candidate:
        return candidate, "multi_signal_fallback"
    return "", "multi_no_signal_empty"


def _multi_signal_fallback(normalized: dict, covered: set[str]) -> str:
    """保守候选：只保留【有自身 evidence_ids、未被 refuted、且证据卡覆盖该选项】的选项。

    不再使用 confidence 阈值（insufficient 的高 confidence 会误导），也不使用 model_answer。
    """
    confident = [
        letter
        for letter, item in normalized.items()
        if item.get("status") != "refuted"
        and item.get("evidence_ids")  # 必须有该选项自身的引用证据
        and (not covered or letter in covered)  # 且证据卡确实覆盖该选项
    ]
    if confident:
        return normalize_answer("".join(confident), "multi")
    return ""


def _signal_single_fallback(normalized: dict, covered: set[str], options: dict, allowed: tuple[str, ...] | None = None, model_answer: str | None = None) -> str:
    """单选/判断题无 supported 时的信号兜底：综合 refuted 惩罚、证据覆盖、confidence、model_answer。

    返回最佳候选字母；若完全无信号返回 ""（由上层 legalize 处理，不静默猜 A）。
    """
    letters = [l for l in (options.keys() if options else VALID_OPTIONS) if (allowed is None or l in allowed)]
    if not letters:
        letters = list(allowed or VALID_OPTIONS)
    model_letter = ""
    if model_answer:
        ml = re.findall(r"[A-D]", (model_answer or "").upper())
        model_letter = ml[0] if ml else ""

    scored = []
    has_signal = False
    for letter in letters:
        item = normalized.get(letter, {})
        status = item.get("status", "insufficient")
        confidence = float(item.get("confidence", 0) or 0)
        evidence_count = len(item.get("evidence_ids", []) or [])
        refute_penalty = 1 if status == "refuted" else 0
        cover_bonus = 1 if letter in covered else 0
        model_bonus = 1 if letter == model_letter else 0
        if confidence > 0 or evidence_count > 0 or cover_bonus or model_bonus or refute_penalty:
            has_signal = True
        # 排序键：refuted 最差；其余按 (model命中, 覆盖, confidence, 证据数) 降序
        scored.append((refute_penalty, -model_bonus, -cover_bonus, -confidence, -evidence_count, letter))
    if not has_signal:
        return ""
    scored.sort()
    return scored[0][5]


def _supported_items(judgements: dict) -> list[tuple[str, dict]]:
    return [(letter, item) for letter, item in judgements.items() if item.get("status") == "supported" and item.get("evidence_ids")]


def tf_letter_roles(options: dict) -> tuple[str | None, str | None]:
    """根据 tf 选项文本动态判断哪个字母代表“正确/是”，哪个代表“错误/否”。

    不假设 A=正确；返回 (true_letter, false_letter)，无法判定的位置为 None。
    """
    true_letter: str | None = None
    false_letter: str | None = None
    for letter, text in options.items():
        lowered = str(text or "").strip().lower()
        if not lowered:
            continue
        if any(word in lowered for word in TF_FALSE_WORDS):
            false_letter = false_letter or letter
        elif any(word in lowered for word in TF_TRUE_WORDS):
            true_letter = true_letter or letter
    return true_letter, false_letter


def _tf_answer_from_judgements(judgements: dict, options: dict) -> str:
    true_letter, false_letter = tf_letter_roles(options)
    if not true_letter and not false_letter:
        return ""
    statuses = {letter: normalize_status(value.get("status") if isinstance(value, dict) else value) for letter, value in judgements.items()}
    # 命题被支持 → 选“正确”；被否定 → 选“错误”
    if true_letter and (statuses.get(true_letter) == "supported" or statuses.get(false_letter) == "refuted"):
        return true_letter
    if false_letter and (statuses.get(false_letter) == "supported" or statuses.get(true_letter) == "refuted"):
        return false_letter
    return ""


def normalize_judgement(value: Any) -> dict:
    if not isinstance(value, dict):
        return {"status": normalize_status(value), "confidence": 0.0, "evidence_ids": [], "reason": ""}
    result = dict(value)
    result["status"] = normalize_status(result.get("status"))
    try:
        result["confidence"] = float(result.get("confidence", 0) or 0)
    except (TypeError, ValueError):
        result["confidence"] = 0.0
    evidence_ids = result.get("evidence_ids", [])
    result["evidence_ids"] = evidence_ids if isinstance(evidence_ids, list) else []
    result["reason"] = str(result.get("reason", ""))[:300]
    return result


# 判题理由中表明“该选项其实不成立”的否定/纠错措辞。若 status=supported 却出现这些措辞，
# 说明模型推理过程已自我否定，判定不可靠（judge_reason_conflict）。
# 用较强的、明确指向“选项错/不该选”的短语，避免误伤正常论证（如“虽未直接…但…”这类仍判对的表述）。
_SUPPORTED_CONTRADICTION_TERMS = (
    "张冠李戴", "偷换", "主体混淆", "张冠",
    "该选项错", "选项错误", "选项c错", "选项d错", "选项a错", "选项b错",
    "此选项错", "该选项不成立", "选项不成立", "故不成立", "因此不成立",
    "应为refuted", "应判refuted", "应为 refuted", "实为错误", "表述错误",
    "与证据矛盾", "与证据不符", "证据不支持", "并不支持", "无法支持",
    "故该选项应", "应判为错", "实际上错误",
)


def _reason_contradicts_supported(reason: str) -> bool:
    """检测 supported 判定的 reason 是否含明确否定/纠错措辞（自相矛盾）。"""
    text = str(reason or "").lower().replace(" ", "")
    return any(term.replace(" ", "") in text for term in _SUPPORTED_CONTRADICTION_TERMS)


# 高精度“自我否定”短语：reason 出现这些短语时，模型已明确写出“该选项其实应判错/张冠李戴”，
# 却把 status 标成 supported（写对了分析、贴错了标签）。这类只做确定性降级为 refuted，
# 比依赖重判更稳。短语经全量审计：仅出现在确实应否定的判定里，对正确 supported 零误伤。
_HARD_SELF_REFUTE_TERMS = (
    "张冠李戴", "应判refuted", "应为refuted", "应判为错", "应判为refuted", "实为错误",
)


def _reason_hard_self_refutes(reason: str) -> bool:
    text = str(reason or "").lower().replace(" ", "")
    return any(term.replace(" ", "") in text for term in _HARD_SELF_REFUTE_TERMS)


def downgrade_self_refuting_judgements(judgements: dict) -> dict:
    """把“写明应判错却标 supported”的判定确定性降级为 refuted（高精度短语，保守）。

    只动 supported→refuted，不动其它状态；保留原 evidence_ids 与 reason，附 downgrade 标记便于审计。
    返回新 dict，不就地修改入参。
    """
    result: dict = {}
    for letter, item in (judgements or {}).items():
        if not isinstance(item, dict):
            result[letter] = item
            continue
        new_item = dict(item)
        if normalize_status(new_item.get("status")) == "supported" and _reason_hard_self_refutes(new_item.get("reason", "")):
            new_item["status"] = "refuted"
            new_item["downgraded_self_refute"] = True
        result[letter] = new_item
    return result


# 领域级 final_answer 后处理校验：产出结构化告警 code（machine-readable）+ 人读说明，
# 不直接改写答案，便于上层据 code 触发保守补检索/局部重判/保守修正与复核。
# 设计原则：宁可少改也不误改——这里只做“答案合法性 + 领域常见风险”的非破坏性检查。
def domain_answer_review(
    answer: str,
    answer_format: str,
    options: dict | None,
    domain: str | None,
    judgements: dict,
    evidence_cards: list[dict] | None = None,
) -> dict:
    options = options or {}
    valid_letters = set(options.keys()) or set(VALID_OPTIONS)
    answer_letters = list(dict.fromkeys(re.findall(r"[A-D]", (answer or "").upper())))
    warnings: list[str] = []
    codes: list[str] = []  # machine-readable warning codes

    def _flag(code: str, message: str) -> None:
        codes.append(code)
        warnings.append(message)

    # 1) 通用：答案字母必须都属于本题实际选项
    illegal = [letter for letter in answer_letters if letter not in valid_letters]
    if illegal:
        _flag("illegal_letter", f"答案含非本题选项字母 {illegal}")

    # 2) 题型基本约束
    if answer_format in {"mcq", "tf"} and len(answer_letters) != 1:
        _flag("wrong_letter_count", f"{answer_format} 题应恰好一个字母，实际 {answer_letters}")
    if answer_format == "multi" and not answer_letters:
        _flag("empty_multi_answer", "multi 题答案为空")
    if answer_format == "tf" and any(letter not in ("A", "B") for letter in answer_letters):
        _flag("tf_letter_out_of_range", "tf 题只能为 A/B")

    # 3) 答案选项是否有对应 supported 判断（multi 漏选/错选的早期信号）
    supported = {
        letter
        for letter, value in judgements.items()
        if normalize_status(value.get("status") if isinstance(value, dict) else value) == "supported"
    }
    if answer_format == "multi":
        non_supported = [letter for letter in answer_letters if letter not in supported]
        missing_supported = [letter for letter in supported if letter not in answer_letters]
        if non_supported:
            _flag("unsupported_answer_letter", f"multi 答案含非 supported 选项 {non_supported}")
        if missing_supported:
            _flag("missing_supported", f"存在 supported 但未选入 final_answer 的选项 {missing_supported}")
        # 过度选择信号：多选无部分分，全选/接近全选大概率错选
        if len(answer_letters) == len(valid_letters) and len(valid_letters) >= 4:
            _flag("over_select", "multi 答案选满全部选项（疑似过度选择，多选无部分分需复核）")
        elif valid_letters and len(answer_letters) >= max(3, len(valid_letters) - 1):
            _flag("over_select", "multi 答案选项偏多，存在过度选择风险，请确认每个选项均有直接证据")
        # 缺证支持却选入：选项无任何相关证据卡却被选，漏选/错选高风险
        if evidence_cards is not None:
            covered = set()
            for card in evidence_cards:
                covered.update(card.get("related_options", []) or [])
            uncovered_in_answer = [letter for letter in answer_letters if letter not in covered]
            if uncovered_in_answer:
                _flag("uncovered_answer_letter", f"final_answer 含无相关证据卡的选项 {uncovered_in_answer}")
    else:
        # 单选/判断：答案字母无 supported 判断也是复核信号
        if answer_letters and not any(letter in supported for letter in answer_letters):
            _flag("unsupported_answer_letter", f"{answer_format} 答案选项 {answer_letters} 无对应 supported 判断")

    # 4) 领域专属风险提示（仅告警，不改答案）
    blob = " ".join(str(c.get("quote", "")) for c in (evidence_cards or []))
    if domain == "regulatory" and not re.search(r"第[一二三四五六七八九十百零〇0-9]+条", blob):
        _flag("no_clause_for_regulatory", "监管题最终证据缺少明确条文号，结论可追溯性弱")
    if domain == "financial_reports" and answer_format != "tf" and not re.search(r"\d", blob):
        _flag("no_number_for_financial", "财报题最终证据缺少数值，数值类判断可追溯性弱")

    # 5) 判题理由↔结论一致性：模型常“嘴上说错、手上判 supported”（reason 含明确否定/纠错措辞却给 supported），
    #    或反之。这类自相矛盾的判定可靠性低，标记触发局部重判（judge_reason_conflict）。
    for letter in answer_letters:
        item = judgements.get(letter)
        if not isinstance(item, dict):
            continue
        if normalize_status(item.get("status")) != "supported":
            continue
        reason = str(item.get("reason", ""))
        if _reason_contradicts_supported(reason):
            _flag(
                "judge_reason_conflict",
                f"选项 {letter} 判为 supported 但理由含否定/纠错措辞，存在判定自相矛盾，需复核",
            )

    return {
        "domain": domain or "",
        "answer": answer,
        "answer_letters": answer_letters,
        "supported_letters": sorted(supported),
        "warnings": warnings,
        "codes": sorted(set(codes)),
        "needs_review": bool(warnings),
    }


# 触发保守补检索/局部重判的 review code 子集：这些 code 表明答案存在“漏选/错选/无证据”风险，
# 上层应据此对相关选项做一次保守的补检索 + 局部重判（不整题重判，保护 token）。
REJUDGE_REVIEW_CODES = {
    "over_select",
    "unsupported_answer_letter",
    "uncovered_answer_letter",
    "missing_supported",
    "judge_reason_conflict",
}


def legalize_answer(answer: str, answer_format: str, options: dict | None = None) -> str:
    """把最终答案归一为【合法非空】的提交答案：A 榜要求每题都要有合法答案。

    仅在 synthesize/review 已无可靠信号、答案为空时作为最后出口使用；
    选取本题首个合法选项（tf 取 A/B 的首个），保证 answer.csv 合法。
    调用方应同时带上 fallback_reason / needs_review 强 warning。
    """
    normalized = normalize_answer(answer, answer_format)
    if normalized:
        return normalized
    letters = sorted((options or {}).keys()) if options else list(VALID_OPTIONS)
    if answer_format == "tf":
        letters = [l for l in letters if l in ("A", "B")] or ["A"]
    return letters[0] if letters else "A"
