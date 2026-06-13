"""Multi-answer arbiter for financial long-doc QA.

Detects disagreements across multiple answer.csv files, classifies patterns
(shrinkage/expansion/substitution), validates against evidence.json, and
emits arbitration suggestions and (when a reference is provided) reward
estimates for each rule.

Usage:
    python answer_arbiter.py \\
        --answer a.csv b.csv \\
        --evidence evidence.json \\
        [--reference reference_answers.csv] \\
        [--questions questions/group_a/*.json] \\
        [--out report.md]
"""
from __future__ import annotations
import argparse, csv, json, re, sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

# ---- Constants ----
YEAR_RE = re.compile(r"(?:19|20)\d{2}\s*年?")
PERCENT_RE = re.compile(r"\d+(?:\.\d+)?\s*%")
MONEY_RE = re.compile(r"\d+(?:\.\d+)?\s*(?:亿元|万元|千元|亿|万)")

VALID_ANSWER_TYPES = {"multi", "mcq", "tf"}


def normalize_answer(s: str) -> str:
    """Normalize an answer string to sorted uppercase letters (A-Z)."""
    if not s:
        return ""
    return "".join(sorted(c.upper() for c in s if c.isalpha()))


def answer_set(s: str) -> set[str]:
    return set(normalize_answer(s))


# ---- 1. Loaders ----
def load_answers(paths: list[Path]) -> dict[str, dict[str, str]]:
    """Load multiple answer.csv files.

    Each csv has at least (qid, answer). Optional (answer_format).
    Returns {name: {qid: sorted_letters_string}}."""
    out: dict[str, dict[str, str]] = {}
    for p in paths:
        name = p.stem
        rows: dict[str, str] = {}
        fmts: dict[str, str] = {}
        with p.open(encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                if r.get("qid") == "summary":
                    continue
                qid = r.get("qid", "").strip()
                ans = "".join(sorted((r.get("answer") or "").strip()))
                rows[qid] = ans
                if r.get("answer_format"):
                    fmts[qid] = r["answer_format"].strip()
        out[name] = rows
        if fmts:
            out[f"{name}__fmt"] = fmts
    return out


def load_evidence(path: Path) -> dict[str, dict]:
    """Load evidence.json. Returns {qid: evidence_dict}."""
    if not path or not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return {x.get("qid", ""): x for x in data}
    if isinstance(data, dict) and "qid" in data:
        return {data["qid"]: data}
    return data


def load_reference(path: Path) -> dict[str, str]:
    if not path or not path.exists():
        return {}
    out: dict[str, str] = {}
    with path.open(encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            qid = r.get("qid", "").strip()
            if qid and qid != "summary":
                out[qid] = "".join(sorted((r.get("reference_answer") or r.get("answer") or "").strip()))
    return out


def load_questions(paths: list[Path]) -> dict[str, dict]:
    """Load question metadata (format, options)."""
    import glob as _g
    out: dict[str, dict] = {}
    pat = list(paths) if paths else [Path(p) for p in _g.glob("questions/group_a/*.json")]
    for p in pat:
        if not p.exists():
            continue
        try:
            items = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        for it in items:
            qid = it.get("qid", "")
            if qid:
                out[qid] = it
    return out


# ---- 2. Disagreement detection & classification ----
def classify_pattern(short: str, long: str) -> str:
    """Classify how 'short' differs from 'long' (each a sorted-letters string)."""
    s = set(short); l = set(long)
    if not s and not l:
        return "identical_empty"
    if s == l:
        return "identical"
    if s.issubset(l):
        return "shrinkage"   # long contains short's letters, plus more
    if l.issubset(s):
        return "expansion"   # short contains long's letters, plus more
    return "substitution"   # letters differ, neither contains the other


# ---- 3. Evidence validation ----
def extract_evidence_signals(ev_record: dict) -> dict[str, Any]:
    """Pull out the useful signals from a per-question evidence record.

    Returns a dict with: round0_supported, final_supported, judgements, and
    an overall 'evidence_mismatch' flag if the supported set does not equal
    the final answer.
    """
    if not ev_record:
        return {"present": False}
    judgements = ev_record.get("judgements", {}) or {}
    r0 = set(ev_record.get("round0_supported", []) or [])
    final = set(ev_record.get("final_supported", []) or [])
    answer = ev_record.get("answer", "")
    answer_set = set(answer) if answer else set()
    mismatch = bool(r0 or final) and (r0 != answer_set or (final and final != answer_set))
    supported = {
        L: (judgements.get(L, {}) or {}).get("status", "")
        for L in (set(judgements) | r0 | final)
    }
    return {
        "present": True,
        "round0_supported": sorted(r0),
        "final_supported": sorted(final),
        "answer_set": sorted(answer_set),
        "evidence_mismatch": mismatch,
        "judgements": {
            L: {
                "status": j.get("status"),
                "confidence": j.get("confidence"),
                "evidence_ids": j.get("evidence_ids"),
                "reason": (j.get("reason") or "")[:160],
            }
            for L, j in judgements.items()
        },
    }


# ---- 4. Main arbitration ----
def arbitrate(
    answers_by_name: dict[str, dict[str, str]],
    questions: dict[str, dict] | None = None,
    evidence: dict[str, dict] | None = None,
    reference: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Build a per-question arbitration report.

    Args:
        answers_by_name: {label: {qid: normalized_answer_str}}.
        questions: optional {qid: {answer_format, options, ...}} from group_a/*.json.
        evidence: optional {qid: evidence_record} from evidence.json.
        reference: optional {qid: gold_answer_str} from reference_answers.csv.

    Returns a dict with keys:
        per_qid: {qid: {answers, normalized, agreement, pattern, ...}}
        disagreements: list[qid] where answers differ
        pattern_counts: Counter of classify_pattern across (long, short) pairs
        rule_rewards (if reference): {rule_name: {"matches": n, "correct": n}}
        majority_answer: {qid: answer} best-effort decision
    """
    all_qids: set[str] = set()
    for d in answers_by_name.values():
        all_qids.update(d.keys())
    if reference:
        all_qids.update(reference.keys())
    all_qids = sorted(all_qids)

    pattern_counter: Counter = Counter()
    per_qid: dict[str, dict] = {}
    disagreements: list[str] = []
    majority_answer: dict[str, str] = {}

    for qid in all_qids:
        # Filter out __fmt entries; only consider actual answer names
        names = [n for n in answers_by_name if not n.endswith("__fmt")]
        raw = {name: answers_by_name.get(name, {}).get(qid, "") for name in names}
        norm = {name: normalize_answer(v) for name, v in raw.items()}
        unique = set(norm.values())
        # normalize reference too (if any)
        gold = normalize_answer(reference[qid]) if reference and qid in reference else None
        qmeta = (questions or {}).get(qid, {})
        ans_fmt = qmeta.get("answer_format") or ("multi" if any(len(v) > 1 for v in norm.values()) else ("mcq" if any(len(v) == 1 for v in norm.values()) else "tf"))
        ev_signals = extract_evidence_signals((evidence or {}).get(qid, {}) or {})

        # Determine pattern by comparing the longest vs shortest
        if len(unique) == 1:
            pattern = "identical"
        else:
            sorted_by_len = sorted(norm.values(), key=lambda s: (len(s), s))
            shortest = sorted_by_len[0]
            longest = sorted_by_len[-1]
            pattern = classify_pattern(shortest, longest)

        pattern_counter[pattern] += 1
        is_disagreement = len(unique) > 1
        if is_disagreement:
            disagreements.append(qid)

        # ---- Arbitration decision ----
        # Counter over normalized answers, tie-break by evidence then by alphabetical
        cnt = Counter(norm.values())
        top_ans, top_count = cnt.most_common(1)[0]
        total = sum(cnt.values())

        # Decide candidate answers (in order of decreasing count, then alpha)
        ordered = sorted(cnt.items(), key=lambda kv: (-kv[1], kv[0]))

        decision = top_ans  # default: majority
        decision_rule = "majority"
        notes: list[str] = []

        if is_disagreement and ans_fmt == "multi" and pattern in ("shrinkage", "empty"):
            # 警惕：多选删减型。系统性的"漏选"bug 风险
            # 如果 evidence 显示 round0 支持了被删的字母，则可能漏选
            if ev_signals.get("present"):
                r0 = set(ev_signals.get("round0_supported", []))
                final = set(ev_signals.get("final_supported", []))
                missing_from_majority = (r0 | final) - set(top_ans) if top_ans else ((r0 | final) - set())
                if missing_from_majority:
                    # union with majority; trust round0 supported
                    merged = "".join(sorted(set(top_ans) | missing_from_majority))
                    notes.append(
                        f"shrinkage with evidence: round0/final supported {sorted(missing_from_majority)} "
                        f"but majority = {top_ans!r}; merging supported letters"
                    )
                    decision = merged
                    decision_rule = "evidence_restore_shrinkage"
        elif is_disagreement and pattern == "expansion":
            # 扩张型：多数可能更保守。如果是 multi 且 evidence 显示扩张的字符无 supported，倾向多数
            if ev_signals.get("present") and ans_fmt == "multi":
                r0 = set(ev_signals.get("round0_supported", []))
                if r0 and r0 != set(top_ans) and r0.issubset(set(top_ans)):
                    # 多数答案的字符 ⊇ round0 supported；扩张的多余字符可能过选
                    notes.append(
                        f"expansion: round0 supported = {sorted(r0)}; majority {top_ans!r} contains them; "
                        f"keeping majority"
                    )
                    decision = top_ans
                    decision_rule = "majority_no_over_trust"

        if is_disagreement and top_count == total // 2 and total >= 2:
            # 平票：优先信任 evidence
            if ev_signals.get("present"):
                r0 = set(ev_signals.get("round0_supported", []))
                if r0 and len(r0) > 0:
                    merged = "".join(sorted(r0))
                    notes.append(f"tied, evidence says round0 supported = {sorted(r0)}")
                    decision = merged
                    decision_rule = "evidence_tie_break"

        majority_answer[qid] = decision

        # ---- Reference evaluation ----
        ref_correct = None
        rule_rewards_for_q: dict[str, str] = {}
        if gold is not None:
            def mark(ans: str) -> bool:
                return ans == gold
            # Rules to evaluate
            rule_rewards_for_q["majority"] = "Y" if mark(top_ans) else "N"
            if decision_rule != "majority":
                rule_rewards_for_q[decision_rule] = "Y" if mark(decision) else "N"
            ref_correct = mark(decision)

        per_qid[qid] = {
            "qid": qid,
            "domain": qmeta.get("domain", ""),
            "answer_format": ans_fmt,
            "answers": norm,
            "raw_answers": raw,
            "agreement": "full" if not is_disagreement else "partial",
            "top_count": top_count,
            "total_answers": total,
            "pattern": pattern,
            "evidence": ev_signals,
            "decision_rule": decision_rule,
            "decision": decision,
            "majority": top_ans,
            "majority_count": top_count,
            "ordered_answers": [a for a, _ in ordered],
            "notes": notes,
            "ref_correct": ref_correct,
        }

    # ---- Aggregate rule rewards ----
    rule_rewards: dict[str, dict[str, int]] = {}
    if reference:
        agg: dict[str, Counter] = defaultdict(Counter)
        for qid, rec in per_qid.items():
            if rec["ref_correct"] is None:
                continue
            for rule, hit in (rec.get("rule_rewards") or {}).items() if False else []:
                agg[rule][hit] += 1
            # above: rule_rewards_for_q is on rec itself; recompute
        for qid, rec in per_qid.items():
            if gold is None:
                continue
            maj = rec["majority"]
            decision = rec["decision"]
            agg["majority"]["Y" if maj == gold else "N"] += 1
            agg["final_decision"]["Y" if decision == gold else "N"] += 1
        rule_rewards = {k: dict(v) for k, v in agg.items()}

    return {
        "per_qid": per_qid,
        "disagreements": disagreements,
        "pattern_counts": dict(pattern_counter),
        "rule_rewards": rule_rewards,
        "majority_answer": majority_answer,
    }


# ---- 5. Markdown report ----
def render_report(arb: dict, source_labels: list[str]) -> str:
    per_qid = arb["per_qid"]
    disagreements = arb["disagreements"]
    pattern_counts = arb["pattern_counts"]
    rule_rewards = arb.get("rule_rewards", {})

    out: list[str] = []
    out.append("# Multi-Answer Arbitration Report\n")
    out.append(f"**Sources compared**: {' | '.join(source_labels)}\n")
    out.append(f"**Total questions**: {len(per_qid)}\n")
    out.append(f"**Disagreements**: {len(disagreements)}\n")
    out.append("\n## Pattern distribution (longest vs shortest)\n")
    out.append("| Pattern | Count | Meaning |")
    out.append("|---------|------:|---------|")
    for p, c in sorted(pattern_counts.items(), key=lambda kv: -kv[1]):
        meaning = {
            "identical": "all sources agree",
            "shrinkage": "longest answer has extra letters vs shortest (漏选风险)",
            "expansion": "shortest answer is missing letters (过选风险)",
            "substitution": "totally different letters (判题分歧)",
            "empty": "one source has empty answer",
        }.get(p, p)
        out.append(f"| {p} | {c} | {meaning} |")
    out.append("")

    if rule_rewards:
        out.append("\n## Rule rewards (vs reference)\n")
        out.append("| Rule | Y | N | Net |")
        out.append("|------|--:|--:|----:|")
        for rule, hits in rule_rewards.items():
            y = hits.get("Y", 0)
            n = hits.get("N", 0)
            out.append(f"| {rule} | {y} | {n} | {y - n:+d} |")
        out.append("")

    out.append("\n## Disagreements (detail)\n")
    for qid in disagreements:
        rec = per_qid[qid]
        out.append(f"### {qid}  ({rec['domain']} / {rec['answer_format']})")
        out.append(f"- **Pattern**: `{rec['pattern']}` (top count {rec['top_count']}/{rec['total_answers']})")
        out.append(f"- **Answers**: " + " | ".join(
            f"{name}={a or '∅'}" for name, a in rec["answers"].items()
        ))
        ev = rec["evidence"]
        if ev.get("present"):
            out.append(f"- **Evidence round0_supported**: {ev['round0_supported']}")
            out.append(f"- **Evidence final_supported**: {ev['final_supported']}")
            out.append(f"- **Evidence answer_set**: {ev['answer_set']}")
            if ev.get("evidence_mismatch"):
                out.append("- **Evidence mismatch**: supported set differs from final answer")
        out.append(f"- **Decision**: `{rec['decision']}`  via **{rec['decision_rule']}**")
        for n in rec["notes"]:
            out.append(f"  - note: {n}")
        if rec.get("ref_correct") is not None:
            out.append(f"- **Reference correct?**: {'YES' if rec['ref_correct'] else 'NO'}")
        out.append("")

    out.append("\n## Top 30 questions needing attention (sorted by disagreement + evidence mismatch)\n")
    ranked = sorted(
        per_qid.items(),
        key=lambda kv: (kv[1]["agreement"] == "full", -kv[1]["total_answers"], kv[0]),
    )
    out.append("| QID | Domain | Format | Pattern | Decision | Rule |")
    out.append("|-----|--------|--------|---------|----------|------|")
    for qid, rec in ranked[:30]:
        out.append(
            f"| {qid} | {rec['domain']} | {rec['answer_format']} | "
            f"{rec['pattern']} | `{rec['decision']}` | {rec['decision_rule']} |"
        )

    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Multi-answer arbiter for financial QA")
    p.add_argument("--answer", action="append", required=True, help="Path to answer.csv (repeatable)")
    p.add_argument("--label", action="append", default=[], help="Label for each --answer (in order)")
    p.add_argument("--evidence", help="Path to evidence.json (optional)")
    p.add_argument("--reference", help="Path to reference_answers.csv (optional)")
    p.add_argument("--questions", action="append", default=[], help="Path to questions/group_a/*.json (optional)")
    p.add_argument("--out", help="Write Markdown report to this path (optional)")
    p.add_argument("--print-majority", action="store_true", help="Print final majority answer.csv")
    args = p.parse_args(argv)

    answer_paths = [Path(a) for a in args.answer]
    labels = args.label or [p.stem for p in answer_paths]

    answers_by_name: dict[str, dict[str, str]] = {}
    for label, path in zip(labels, answer_paths):
        d = load_answers([path])
        if d:
            # load_answers returns {stem: {qid: ans}}; flatten to {label: {qid: ans}}
            answers_by_name[label] = next(iter(d.values()), {})

    questions = load_questions([Path(p) for p in args.questions]) if args.questions else {}
    evidence = None
    if args.evidence:
        evidence = load_evidence(Path(args.evidence))
    reference = None
    if args.reference:
        reference = load_reference(Path(args.reference))

    arb = arbitrate(answers_by_name, questions, evidence, reference)

    report = render_report(arb, labels)

    if args.out:
        Path(args.out).write_text(report, encoding="utf-8")
        print(f"wrote {args.out} ({len(report)} bytes)")
    else:
        print(report)

    if args.print_majority:
        out_csv = Path("majority_arbited.csv")
        with out_csv.open("w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["qid", "answer", "decision_rule"])
            for qid, ans in arb["majority_answer"].items():
                rule = arb["per_qid"][qid]["decision_rule"]
                w.writerow([qid, ans, rule])
        print(f"wrote {out_csv}")

    return 0


if __name__ == "__main__":
    sys.exit(main())

