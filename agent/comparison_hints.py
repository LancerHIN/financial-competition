from __future__ import annotations

"""跨文档/跨证据比较 hint（纯本地，不调用模型）。

基于 fact_memory 产出的结构化事实，找出跨文档可比较的同类事实，生成短 hint 提示
judge：哪些事实可直接比较、哪些口径不一致需注意、选项是否引用了某文档没有的事实。

设计原则：
  - 只在“同一可比主题键”下比较（同指标、同阈值主题、同时限主题），主题不一致不混比；
  - 只产出“比较线索”，不替 judge 下 supported/refuted；
  - 每题 hint 数量有上限，避免 prompt 膨胀。
"""

import re

# 题干显式要求跨年份/趋势比较的信号：命中则允许跨年份比较，不再提示“年份不一致不可比”。
_CROSS_YEAR_INTENT_RE = re.compile(
    r"同比|环比|增长|下降|增加|减少|较上年|较去年|较上一?年度|上年同期|年均|复合增长|趋势|逐年|连续\s*\d+\s*年|近\s*\d+\s*年|变化"
)


def make_comparison_hints(
    question: str,
    options: dict[str, str],
    facts: list[dict],
    domain: str | None = None,
    max_hints: int = 6,
) -> list[str]:
    if not facts:
        return []
    doc_ids = {f.get("doc_id") for f in facts if f.get("doc_id")}
    # 跨文档比较只在确实涉及多文档时才有意义（单文档题靠 fact_memory 摘要辅助，不在此生成比较 hint）
    if len(doc_ids) < 2:
        return []

    # 跨年份/趋势意图须同时扫题干与选项：A 榜财报题常把“较2024年增长/下降/优于”写在选项里，
    # 题干只问“下列说法正确的有哪些”，只看 question 会漏判，导致误报“年份口径不一致不可比”。
    compare_text = str(question) + " " + " ".join(str(v) for v in (options or {}).values())
    cross_year = bool(_CROSS_YEAR_INTENT_RE.search(compare_text))
    hints: list[str] = []
    hints.extend(_compare_financial_metrics(facts, cross_year))
    hints.extend(_compare_thresholds(facts))
    hints.extend(_compare_time_limits(facts))

    deduped: list[str] = []
    seen: set[str] = set()
    for hint in hints:
        if hint and hint not in seen:
            seen.add(hint)
            deduped.append(hint)
        if len(deduped) >= max_hints:
            break
    return deduped


def _compare_financial_metrics(facts: list[dict], cross_year: bool) -> list[str]:
    """同指标跨文档比较。

    item18：题干显式要求跨年份/趋势比较时，允许跨年份比较，不再无条件禁止。
    item3/17：单位不一致时，若 value_yuan 可用，直接按 value_yuan 给换算后的大小关系，
             不再只提示“换算后再比较”。
    """
    hints: list[str] = []
    metric_facts = [f for f in facts if f.get("metric") and f.get("value_yuan") is not None]
    by_metric: dict[str, list[dict]] = {}
    for f in metric_facts:
        by_metric.setdefault(f["metric"], []).append(f)

    for metric, group in by_metric.items():
        docs = {f.get("doc_id") for f in group}
        if len(docs) < 2:
            continue
        years = {f.get("year") for f in group if f.get("year")}
        units = {f.get("unit") for f in group if f.get("unit")}

        # 年份不一致：仅当题干未要求跨年份比较时才提示口径差异；否则照常比较。
        if len(years) > 1 and not cross_year:
            hints.append(
                f"跨文档「{metric}」涉及不同年份{_fmt_set(years)}，题干未要求跨年份比较，"
                f"请按各自年份核对，勿默认同年比较。"
            )
            continue

        # 统一按 value_yuan（已换算为元）排序给大小关系，单位不同也能直接比较。
        ordered = sorted(group, key=lambda f: f.get("value_yuan", 0), reverse=True)
        parts = [f"{_label(f)}={f.get('value_raw') or _fmt_yuan(f.get('value_yuan'))}" for f in ordered[:4]]
        top, bottom = ordered[0], ordered[-1]
        note = ""
        if len(units) > 1:
            note = f"（单位不一致{_fmt_set(units)}，以下已按换算为元的金额比较）"
        year_note = f"（跨年份比较）" if (len(years) > 1 and cross_year) else ""
        hints.append(
            f"跨文档「{metric}」{note}{year_note}：{'；'.join(parts)}；"
            f"最大={top.get('doc_id')}，最小={bottom.get('doc_id')}，据此核对选项大小/排序表述，"
            f"但最终仍须引用 evidence 原文。"
        )
    return hints


def _threshold_topic_key(fact: dict) -> tuple:
    """阈值比较的主题键：不能只按 direction 分组，否则不同主题阈值会错误混比。

    item4/5/19：加入 domain/record_type/metric/clause_type/clause_no/kind/related_options 约束。
    """
    th = fact.get("threshold", {})
    return (
        fact.get("domain", ""),
        fact.get("record_type", ""),
        fact.get("metric", ""),
        fact.get("clause_type", ""),
        fact.get("clause_no", ""),
        th.get("kind", ""),
        th.get("direction", ""),
        tuple(sorted(fact.get("related_options", []) or [])),
    )


def _compare_thresholds(facts: list[dict]) -> list[str]:
    """跨文档阈值比较：仅在【同一主题键】下比较，不同主题的阈值不混比。"""
    hints: list[str] = []
    th_facts = [f for f in facts if f.get("threshold")]
    by_topic: dict[tuple, list[dict]] = {}
    for f in th_facts:
        by_topic.setdefault(_threshold_topic_key(f), []).append(f)

    for key, group in by_topic.items():
        docs = {f.get("doc_id") for f in group}
        if len(docs) < 2:
            continue
        direction = key[6]
        topic = key[2] or key[3] or key[4] or "该阈值"
        parts = []
        for f in group[:4]:
            th = f["threshold"]
            if th.get("value_pct") is not None:
                parts.append(f"{f.get('doc_id')}:{direction}{th['value_pct']}%")
            elif th.get("value_yuan") is not None:
                parts.append(f"{f.get('doc_id')}:{direction}{_fmt_yuan(th['value_yuan'])}")
            elif th.get("value_num") is not None:
                parts.append(f"{f.get('doc_id')}:{direction}{th['value_num']}")
        if parts:
            hints.append(
                f"同主题「{topic}」的「{direction}」阈值在不同文档取值不同：{'；'.join(parts)}，"
                f"请按各自文档核对，勿跨文档混用阈值。"
            )
    return hints


def _compare_time_limits(facts: list[dict]) -> list[str]:
    """跨文档时限比较：仅在同主题（条款/义务）下，注意“工作日 vs 日”被偷换。"""
    hints: list[str] = []
    tl_facts = [f for f in facts if f.get("time_limit")]
    by_topic: dict[tuple, list[dict]] = {}
    for f in tl_facts:
        topic_key = (f.get("domain", ""), f.get("clause_type", ""), f.get("clause_no", ""))
        by_topic.setdefault(topic_key, []).append(f)

    for _key, group in by_topic.items():
        docs = {f.get("doc_id") for f in group}
        if len(docs) < 2:
            continue
        parts = []
        units = set()
        for f in group[:4]:
            tl = f["time_limit"]
            parts.append(f"{f.get('doc_id')}:{tl['amount']}{tl['unit']}")
            units.add(tl["unit"])
        note = ""
        if {"工作日"} & units and ({"日", "天"} & units):
            note = "（注意“工作日”与“日”口径不同，勿混用）"
        if parts:
            hints.append(f"不同文档的时限：{'；'.join(parts)}{note}，请按各自文档原文核对。")
    return hints


def _label(fact: dict) -> str:
    year = fact.get("year")
    return f"{fact.get('doc_id')}@{year}" if year else str(fact.get("doc_id"))


def _fmt_set(values) -> str:
    return "、".join(str(v) for v in sorted(v for v in values if v))


def _fmt_yuan(value: float | None) -> str:
    if value is None:
        return "?"
    if abs(value) >= 1e8:
        return f"{value / 1e8:.4g}亿元"
    if abs(value) >= 1e4:
        return f"{value / 1e4:.4g}万元"
    return f"{value:.4g}元"
