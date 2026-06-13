from __future__ import annotations

import re
import time
from typing import Any

from .answer_normalizer import (
    REJUDGE_REVIEW_CODES,
    _reason_contradicts_supported,
    domain_answer_review,
    downgrade_self_refuting_judgements,
    legalize_answer,
    normalize_judgement,
    synthesize_answer,
)
from .comparison_hints import make_comparison_hints
from .config import settings
from .evidence_selector import ALeaderboardEvidenceSelector, choose_retrieval_budget
from .evidence_validator import validate_cards
from .fact_memory import build_fact_memory
from .numeric_checker import make_computed_hints
from .prompts import a_leaderboard_option_judge_prompt


class ALeaderboardAgent:
    def __init__(self, retriever, qwen_client, token_tracker):
        self.evidence_selector = ALeaderboardEvidenceSelector(retriever)
        self.qwen_client = qwen_client
        self.token_tracker = token_tracker

    def answer_question(self, question_item: dict[str, Any]) -> dict[str, Any]:
        started = time.perf_counter()
        qid = str(question_item["qid"])
        domain = question_item.get("domain")
        question = str(question_item.get("question", ""))
        options = {str(k): str(v) for k, v in (question_item.get("options") or {}).items() if str(k) in "ABCD"}
        answer_format = str(question_item.get("answer_format", "mcq"))
        doc_ids = [str(doc_id) for doc_id in (question_item.get("doc_ids") or []) if str(doc_id).strip()]
        retrieval_doc_ids = doc_ids or None
        fullcorpus_fallback_used = not bool(doc_ids)
        low_conf = float(getattr(settings, "multi_low_confidence_threshold", 0.55))

        # ---- 第一轮：检索 + 校验 + 全题判断 ----
        # 动态证据预算：决定召回深度与传给 judge 的证据卡上限（card_limit 必须与 final_top_k 对齐，
        #   否则多召回的证据会在 prompt 阶段被 _compact_cards_for_prompt 截断）。
        budget = choose_retrieval_budget(question, options, domain, retrieval_doc_ids, answer_format)
        card_limit = budget["card_limit"]
        evidence_cards, per_option = self.evidence_selector.select(question, options, domain, retrieval_doc_ids, answer_format=answer_format)
        validation = validate_cards(domain, question, options, evidence_cards, _cards_as_chunks(evidence_cards))
        judgements, model_answer = self._judge(qid, 0, question, options, answer_format, evidence_cards, domain, doc_ids=retrieval_doc_ids, card_limit=card_limit)

        # ---- 两个独立触发源（item1/item2/item11）----
        # 触发源 A：multi rescue —— 多选漏选风险最高，把 insufficient 与"无相关证据卡"且非 supported 的选项纳入。
        rescue_letters = {letter for letter, item in judgements.items() if item.get("status") == "insufficient"}
        if answer_format == "multi":
            for letter in options:
                status = judgements.get(letter, {}).get("status")
                if not per_option.get(letter) and status != "supported":
                    rescue_letters.add(letter)
        # 触发源 B：validation retry —— 与 rescue 是否存在无关，独立判断。
        retry_queries = validation.get("retry_queries", []) if isinstance(validation, dict) else []
        validation_insufficient = validation.get("status") == "insufficient" if isinstance(validation, dict) else False
        validator_block = bool(getattr(settings, "validator_can_block_stop", True))
        validation_retry_active = bool(retry_queries) or (validation_insufficient and validator_block)

        # 待重判选项集合（item5/6）：首轮 supported/refuted 且有证据且置信足够的选项默认保留，
        # 其余（insufficient/uncovered/low-confidence/review-risk）纳入。最后与 rescue 合并。
        rejudge_letters: set[str] = set()
        if validation_retry_active:
            rejudge_letters |= {letter for letter in options if not _is_solid(judgements.get(letter, {}), low_conf)}
        rejudge_letters |= rescue_letters
        # multi low-confidence / review-risk supported 也纳入（应修5）
        if answer_format == "multi":
            rejudge_letters |= {letter for letter, item in judgements.items() if not _is_solid(item, low_conf)}

        did_second_round = False
        if (rescue_letters or validation_retry_active) and rejudge_letters:
            evidence_cards, per_option, judgements = self._supplement_and_rejudge(
                qid, 1, question, options, answer_format, domain, retrieval_doc_ids,
                evidence_cards, per_option, judgements,
                rescue_letters=sorted(rescue_letters),
                retry_queries=retry_queries,
                rejudge_letters=sorted(rejudge_letters),
            )
            validation = validate_cards(domain, question, options, evidence_cards, _cards_as_chunks(evidence_cards))
            did_second_round = True

        # ---- 合成答案 ----
        answer_info = synthesize_answer(judgements, answer_format, options, model_answer=model_answer, evidence_cards=evidence_cards)
        answer_review = domain_answer_review(answer_info["answer"], answer_format, options, domain, judgements, evidence_cards)

        # ---- answer_review 驱动的保守补检索/局部重判（item7）：至少触发一次，不只写日志 ----
        review_pass_done = False
        risky_codes = set(answer_review.get("codes", [])) & REJUDGE_REVIEW_CODES
        if risky_codes:
            risk_letters = _review_risk_letters(answer_review, answer_info["answer"], options, judgements, low_conf=low_conf)
            if risk_letters:
                evidence_cards, per_option, judgements = self._supplement_and_rejudge(
                    qid, 2, question, options, answer_format, domain, retrieval_doc_ids,
                    evidence_cards, per_option, judgements,
                    rescue_letters=risk_letters,
                    retry_queries=[],
                    rejudge_letters=risk_letters,
                )
                answer_info = synthesize_answer(judgements, answer_format, options, model_answer=model_answer, evidence_cards=evidence_cards)
                answer_review = domain_answer_review(answer_info["answer"], answer_format, options, domain, judgements, evidence_cards)
                review_pass_done = True

        # ---- 合法化最终答案（item9/10/应修7）：仍为空时给合法答案 + 强 warning，不静默猜 A ----
        final_answer = answer_info["answer"]
        needs_review = bool(answer_review.get("needs_review")) or bool(answer_info.get("needs_review"))
        fallback_reason = answer_info.get("fallback_reason", "")
        if not final_answer:
            final_answer = legalize_answer(final_answer, answer_format, options)
            needs_review = True
            fallback_reason = fallback_reason or "empty_answer_legalized"
            answer_review["needs_review"] = True
            answer_review.setdefault("codes", []).append("empty_answer_legalized")
            answer_review.setdefault("warnings", []).append("最终答案为空，已合法化为占位答案，需人工复核")

        evidence_per_option = {letter: len(per_option.get(letter, [])) for letter in options}
        token_usage = self.token_tracker.get(qid) if self.token_tracker else {}
        return {
            "qid": qid,
            "domain": domain or "",
            "doc_ids": doc_ids,
            "answer_format": answer_format,
            "answer": final_answer,
            "final_answer": final_answer,
            "judgements": judgements,
            "evidence_cards": evidence_cards,
            "evidence_count": len(evidence_cards),
            "evidence_per_option": evidence_per_option,
            "insufficient_options": answer_info["insufficient_options"],
            "fallback_used": answer_info["fallback_used"] or bool(answer_info.get("needs_review")),
            "fallback_reason": fallback_reason,
            "needs_review": needs_review,
            "fullcorpus_fallback_used": fullcorpus_fallback_used,
            "validation_status": validation.get("status", ""),
            "validation_issues": validation.get("issues", []),
            "validation_retry_active": validation_retry_active,
            "rescue_letters": sorted(rescue_letters),
            "rejudge_letters": sorted(rejudge_letters),
            "review_pass_done": review_pass_done,
            "answer_review": answer_review,
            "token_usage": token_usage,
            "stop_reason": _stop_reason(did_second_round, review_pass_done),
            "elapsed_time": round(time.perf_counter() - started, 3),
        }

    def _supplement_and_rejudge(
        self,
        qid: str,
        round_id: int,
        question: str,
        options: dict[str, str],
        answer_format: str,
        domain: str | None,
        retrieval_doc_ids: list[str] | None,
        evidence_cards: list[dict],
        per_option: dict[str, list[dict]],
        judgements: dict[str, dict],
        rescue_letters: list[str],
        retry_queries: list[str],
        rejudge_letters: list[str],
    ) -> tuple[list[dict], dict[str, list[dict]], dict[str, dict]]:
        """合并两类补检索（targeted rescue + validation retry queries），局部重判，仅覆盖待重判选项。

        - evidence_id 重新编号后做 old->new 映射，保证保留下来的首轮 judgement 的 evidence_ids 不失效（应修2）；
        - 只对 rejudge_letters 调一次局部 judge，其余选项保留首轮结果（item4/应修1）；
        - 局部 prompt 只带相关证据，不塞回完整 source_text（item18）。
        """
        targeted_cards: list[dict] = []
        targeted_per_option: dict[str, list[dict]] = {letter: [] for letter in options}
        if rescue_letters:
            targeted_cards, targeted_per_option = self.evidence_selector.targeted_select(
                question, options, rescue_letters, domain, retrieval_doc_ids
            )
        if retry_queries:
            retry_cards, retry_per_option = self.evidence_selector.query_select(
                retry_queries, options, domain, retrieval_doc_ids, question=question
            )
            targeted_cards = merge_evidence_cards(targeted_cards, retry_cards)
            targeted_per_option = merge_per_option(targeted_per_option, retry_per_option)

        old_cards = evidence_cards
        merged_cards = merge_evidence_cards(evidence_cards, targeted_cards)
        merged_per_option = merge_per_option(per_option, targeted_per_option)

        # 保留首轮 judgement 的 evidence_ids 在重编号后的有效性
        remapped = remap_judgement_evidence_ids(judgements, old_cards, merged_cards)

        focus = [letter for letter in rejudge_letters if letter in options]
        if not focus:
            return merged_cards, merged_per_option, remapped

        new_judgements, _model = self._judge(
            qid, round_id, question, options, answer_format, merged_cards, domain, focus_letters=focus, doc_ids=retrieval_doc_ids
        )
        merged_judgements = merge_judgements(remapped, new_judgements, focus)
        return merged_cards, merged_per_option, merged_judgements

    def _judge(
        self,
        qid: str,
        round_id: int,
        question: str,
        options: dict[str, str],
        answer_format: str,
        evidence_cards: list[dict],
        domain: str | None = None,
        focus_letters: list[str] | None = None,
        doc_ids: list[str] | None = None,
        card_limit: int = 20,
    ) -> tuple[dict[str, dict], str]:
        hints = make_computed_hints(question, options, evidence_cards, domain=domain)
        # 跨文档比较层：仅在结构化事实启用领域（默认 financial_reports/regulatory/financial_contracts/insurance；
        #   research 不开，观点/预测易被错误结构化）。纯本地、不调模型；只产出“比较线索”，
        #   最终 supported/refuted 仍由 judge 依据证据原文判断（须引用 evidence_id）。
        comparison_hints: list[str] = []
        if domain in set(getattr(settings, "structured_fact_domains", ())):
            facts = build_fact_memory(question, options, evidence_cards, domain=domain)
            comparison_hints = make_comparison_hints(
                question, options, facts, domain=domain,
                max_hints=int(getattr(settings, "comparison_hints_max", 6)),
            )
            self._debug_hints(qid, round_id, hints, facts, comparison_hints)
        # 合并后总数限长（item10）：优先保留 computed_hints（计算/缺证），再补 comparison_hints。
        total_max = int(getattr(settings, "judge_hints_total_max", 16))
        all_hints = (hints + comparison_hints)[:total_max]
        prompt = a_leaderboard_option_judge_prompt(
            question, options, answer_format, evidence_cards, all_hints, domain,
            card_limit=card_limit, focus_letters=focus_letters, doc_ids=doc_ids,
        )
        evidence_ids = {card.get("evidence_id") for card in evidence_cards}
        target_letters = [l for l in (focus_letters or sorted(options)) if l in options]

        # ---- 自一致性投票：对同一 prompt 采样 N 次，按选项多数表决，抑制模型随机抖动 ----
        samples = max(1, int(getattr(settings, "judge_vote_samples", 1)))
        vote_on = answer_format in set(getattr(settings, "judge_vote_enabled_formats", ("mcq", "tf", "multi")))
        if samples <= 1 or not vote_on:
            data, usage = self.qwen_client.chat_json(prompt, purpose="a_leaderboard_option_judge")
            if self.token_tracker:
                self.token_tracker.record(qid, round_id, "a_leaderboard_option_judge", usage)
            judgements = self._parse_judgements(data, target_letters, evidence_ids, options)
            return judgements, str(data.get("final_answer", "") if isinstance(data, dict) else "")

        # 多次采样：首样本用确定性温度(0.0)，其余用 vote 温度产生多样性。
        vote_temp = float(getattr(settings, "judge_vote_temperature", 0.4))
        sample_judgements: list[dict[str, dict]] = []
        sample_finals: list[str] = []
        for s in range(samples):
            temp = None if s == 0 else vote_temp
            data, usage = self.qwen_client.chat_json(prompt, purpose="a_leaderboard_option_judge", temperature=temp)
            if self.token_tracker:
                self.token_tracker.record(qid, round_id, "a_leaderboard_option_judge", usage)
            sample_judgements.append(self._parse_judgements(data, target_letters, evidence_ids, options))
            sample_finals.append(str(data.get("final_answer", "") if isinstance(data, dict) else ""))
        judgements = _aggregate_votes(sample_judgements, target_letters)
        final_answer = _aggregate_final(sample_finals, answer_format, options, judgements)
        return judgements, final_answer

    def _parse_judgements(self, data: dict, target_letters: list[str], evidence_ids: set, options: dict[str, str]) -> dict[str, dict]:
        raw = data.get("judgements", {}) if isinstance(data, dict) else {}
        judgements = {}
        for letter in target_letters:
            item = normalize_judgement(raw.get(letter, {}))
            item["evidence_ids"] = [eid for eid in item.get("evidence_ids", []) if eid in evidence_ids]
            if item["status"] in {"supported", "refuted"} and not item["evidence_ids"]:
                item["status"] = "insufficient"
            judgements[letter] = item
        # Fix#2：把“写明应判错却标 supported”的判定确定性降级（高精度短语，保守、零误伤）。
        judgements = downgrade_self_refuting_judgements(judgements)
        return judgements

    def _debug_hints(self, qid: str, round_id: int, computed_hints: list[str], facts: list[dict], comparison_hints: list[str]) -> None:
        """item13：把 computed/fact/comparison hint 写入本地 debug 文件，便于错题归因
        （证据没召回 / fact 抽错 / hint 误导 / judge 判错）。默认关闭，HINTS_DEBUG_ENABLED=1 开启。"""
        if not getattr(settings, "hints_debug_enabled", False):
            return
        try:
            import json

            path = settings.run_log_path.parent / "hints_debug.jsonl"
            path.parent.mkdir(parents=True, exist_ok=True)
            record = {
                "qid": qid,
                "round": round_id,
                "computed_hints": computed_hints,
                "facts": facts,
                "comparison_hints": comparison_hints,
            }
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:  # noqa: BLE001 - debug 写入失败绝不影响主流程
            pass


def _aggregate_votes(samples: list[dict[str, dict]], target_letters: list[str]) -> dict[str, dict]:
    """对多次采样的逐选项判定做多数表决。

    每个选项独立统计 supported/refuted/insufficient 三类票数：
      - 取票数最多的 status 为最终 status；
      - 平票时按保守优先级 insufficient > refuted > supported（宁可不选，避免多选错选/过包含）；
      - 最终 status 的 confidence 取该类样本的均值；evidence_ids 取该类样本的并集（保序去重）；
      - reason 取该类样本中 confidence 最高的一条。
    """
    PRIORITY = {"insufficient": 3, "refuted": 2, "supported": 1}  # 平票时数字大者胜（更保守）
    aggregated: dict[str, dict] = {}
    for letter in target_letters:
        items = [s.get(letter) for s in samples if isinstance(s.get(letter), dict)]
        if not items:
            aggregated[letter] = {"status": "insufficient", "confidence": 0.0, "evidence_ids": [], "reason": ""}
            continue
        counts: dict[str, int] = {}
        for it in items:
            st = it.get("status", "insufficient")
            counts[st] = counts.get(st, 0) + 1
        max_votes = max(counts.values())
        winners = [st for st, c in counts.items() if c == max_votes]
        win_status = max(winners, key=lambda st: PRIORITY.get(st, 0)) if len(winners) > 1 else winners[0]
        win_items = [it for it in items if it.get("status") == win_status]
        confidences = [float(it.get("confidence", 0) or 0) for it in win_items]
        avg_conf = round(sum(confidences) / len(confidences), 3) if confidences else 0.0
        ev_ids: list[str] = []
        for it in win_items:
            for eid in it.get("evidence_ids", []) or []:
                if eid not in ev_ids:
                    ev_ids.append(eid)
        best = max(win_items, key=lambda it: float(it.get("confidence", 0) or 0))
        aggregated[letter] = {
            "status": win_status,
            "confidence": avg_conf,
            "evidence_ids": ev_ids,
            "reason": str(best.get("reason", ""))[:300],
            "vote_counts": counts,
            "vote_samples": len(items),
        }
    return aggregated


def _aggregate_final(finals: list[str], answer_format: str, options: dict[str, str], judgements: dict[str, dict]) -> str:
    """聚合多次采样的 final_answer。

    多选题不直接用 final_answer（由 supported 选项在 synthesize_answer 合成），返回空串即可。
    单选/判断题按多数表决选最高票的 final_answer。
    """
    if answer_format == "multi":
        return ""
    valid = [re.sub(r"[^A-D]", "", (f or "").upper()) for f in finals]
    valid = [f for f in valid if f and all(c in options for c in f)]
    if not valid:
        return ""
    counts: dict[str, int] = {}
    for f in valid:
        counts[f] = counts.get(f, 0) + 1
    max_votes = max(counts.values())
    winners = sorted([f for f, c in counts.items() if c == max_votes])
    return winners[0]


def _is_solid(item: dict, low_conf: float) -> bool:
    """首轮判断是否"确定"：有证据、状态明确(supported/refuted)、置信不低于阈值。

    确定的选项第二轮默认保留，不纳入待重判集合（item5/6）。
    """
    if not isinstance(item, dict):
        return False
    if item.get("status") not in {"supported", "refuted"}:
        return False
    if not item.get("evidence_ids"):
        return False
    return float(item.get("confidence", 0) or 0) >= low_conf


def _review_risk_letters(answer_review: dict, answer: str, options: dict[str, str], judgements: dict[str, dict], low_conf: float = 0.55) -> list[str]:
    """从 answer_review 推导需保守复核的选项：答案里无证据/非 supported 的字母 + supported 漏选的字母
    + 判题理由自相矛盾（supported 但 reason 否定）的字母。"""
    answer_letters = set(re.findall(r"[A-D]", (answer or "").upper()))
    supported = {l for l, v in judgements.items() if isinstance(v, dict) and v.get("status") == "supported"}
    risk = set()
    # 答案里非 supported 或无证据卡的字母
    for letter in answer_letters:
        if letter in options and letter not in supported:
            risk.add(letter)
    # supported 但漏选的字母
    risk |= {l for l in supported if l not in answer_letters and l in options}
    # supported 但判题理由含否定/纠错措辞（judge_reason_conflict）的字母
    for letter, item in judgements.items():
        if letter in options and isinstance(item, dict) and item.get("status") == "supported":
            if _reason_contradicts_supported(str(item.get("reason", ""))):
                risk.add(letter)
    return sorted(risk)


def _stop_reason(did_second_round: bool, review_pass_done: bool) -> str:
    if review_pass_done:
        return "review_local_rejudge_a_leaderboard"
    if did_second_round:
        return "two_round_a_leaderboard"
    return "round1_resolved"


def merge_evidence_cards(existing: list[dict], new_cards: list[dict]) -> list[dict]:
    merged = []
    seen = set()
    for card in existing + new_cards:
        key = (card.get("doc_id"), card.get("chunk_id"), card.get("quote"))
        if key in seen:
            continue
        seen.add(key)
        merged.append(dict(card))
    for index, card in enumerate(merged, start=1):
        card["evidence_id"] = f"E{index}"
    return merged


def _card_key(card: dict) -> tuple:
    return (card.get("doc_id"), card.get("chunk_id"), card.get("quote"))


def remap_judgement_evidence_ids(judgements: dict[str, dict], old_cards: list[dict], merged_cards: list[dict]) -> dict[str, dict]:
    """合并重编号后，把首轮 judgement 的旧 evidence_id 映射到新 evidence_id（应修2）。

    通过 (doc_id, chunk_id, quote) 身份键建立 old_id -> new_id 映射；映射不到的 id 丢弃，
    避免引用失效的证据。
    """
    key_to_new = {_card_key(card): card.get("evidence_id") for card in merged_cards}
    old_id_to_key = {card.get("evidence_id"): _card_key(card) for card in old_cards}
    remapped: dict[str, dict] = {}
    for letter, item in judgements.items():
        if not isinstance(item, dict):
            remapped[letter] = item
            continue
        new_ids = []
        for eid in item.get("evidence_ids", []) or []:
            key = old_id_to_key.get(eid)
            new_id = key_to_new.get(key) if key else None
            if new_id and new_id not in new_ids:
                new_ids.append(new_id)
        new_item = {**item, "evidence_ids": new_ids}
        # 重映射后丢失全部证据的 supported/refuted 退回 insufficient，避免悬空结论
        if new_item.get("status") in {"supported", "refuted"} and not new_ids:
            new_item["status"] = "insufficient"
        remapped[letter] = new_item
    return remapped


def merge_judgements(base: dict[str, dict], updated: dict[str, dict], focus_letters: list[str]) -> dict[str, dict]:
    """局部重判 merge（应修1）：只用 updated 覆盖 focus_letters 选项，其余保留 base 的首轮结果。"""
    result = {letter: (dict(item) if isinstance(item, dict) else item) for letter, item in base.items()}
    for letter in focus_letters:
        if letter in updated:
            result[letter] = updated[letter]
    return result


def merge_per_option(left: dict[str, list[dict]], right: dict[str, list[dict]]) -> dict[str, list[dict]]:
    result = {letter: list(cards) for letter, cards in left.items()}
    for letter, cards in right.items():
        result.setdefault(letter, []).extend(cards)
    return result


def _cards_as_chunks(cards: list[dict]) -> list[dict]:
    return [{"chunk_id": card.get("chunk_id"), "text": card.get("source_text") or card.get("quote", "")} for card in cards]
