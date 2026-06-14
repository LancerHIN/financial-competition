# 13-Top-Wrong Diagnostic: Failure Modes & Repair Hints

**Context**: All 5 historical answer.csv (incl. bak answer-0612-78.5 = 87/100) miss the same 13 questions. Oracle upper bound is 93/100, hybrid-with-reference is 92/100. This report identifies what's failing and what could be repaired.

**Sources analyzed**:
- bak (answ...ach repair) to the score. If any of these could be closed, the ceiling moves.

## Mode 1: Wrong-Company Evidence (2 questions: fin_a_008, fin_a_015)

Both questions ask about specific companies (海尔, 智研), but the retrieved evidence is from different companies (BYD, CATL, 中国移动).

- **fin_a_008**: question about 海尔 2024 R&D ratio; evidence is from BYD/CATL 2024
- **fin_a_015**: question about 智研 dividend; evidence is from CATL 2024 + 中国移动 2025 (no 智研 docs)

**Judge says "A supported" because E4 has 6.63% number**. But E4 is from the wrong company.

**Repair**: doc_id filtering by question's company. When question explicitly mentions a company name and the evidence is from a different company, **discard that evidence**. The question is the natural filtering signal.

**Potential gain**: 2/100 (if both are the only wrong-company cases; could be more across the 100 we don't see).

## Mode 2: Missing Evidence for One Option (1 question: ins_a_019)

For option C, judge has **0 evidence_ids**. Retrieval failed to surface any clause about 平安附加险 specifically.

**Repair**: when 0 eids + option mentioned in question, do a second-pass targeted retrieval with the option's text as the query (not the global query).

**Potential gain**: 1/100 (per question, plus 0-1 from similar patterns).

## Mode 3: TOC/Crowding (1-3 questions: ins_a_019 and possibly more)

21 evidence cards provided, but most are table of contents, headers, or generic product intros. Specific clause text is crowded out.

**Repair**: filter evidence cards by `record_type` or `is_specific_clause` to prefer actual clause text over TOC. Currently the retrieval has no filter for TOC vs clause.

**Potential gain**: 0-2/100 (hard to estimate without further diagnostic).

## Mode 4: Judge Actively Rejects Correct Options (4 questions: fc_a_014, ins_a_006, ins_a_008, ins_a_014)

For these, judge says `refuted` or `insufficient` with full confidence, but the option is in gold. Evidence exists for the option.

- **fc_a_014**: B has 5 eids, marked insufficient, conf 0.9
- **ins_a_006**: A has 6 eids, marked refuted, conf 1.0
- **ins_a_008**: B has 2 eids, marked refuted, conf 1.0
- **ins_a_014**: D has 2 eids, marked insufficient, conf 0.6

**Repair**: difficult — the judge is making a defensible decision based on its evidence. The evidence *might* not actually fully support the option (e.g., evidence has a different subject or different numeric claim). Without re-reading each evidence card, can't tell if judge is right or wrong.

**Potential gain**: 0-4/100 (depends on whether the evidence actually does support the correct option, or whether the judge is right to reject).

## Mode 5: Multi-doc Synthesis Failure (1 question: res_a_020)

For res_a_020 (gold=ABD, cur=A), the question asks about EV + IC industry. Evidence is from pack2_text04 and pack2_text09. Judge supports A but refutes B and D.

- A (芯原 IP market share): supported, correct
- B (碳酸锂价格/PE): refuted, but gold has B
- D (2026Q1 国内 EV 销量 -3.6%): refuted, but gold has D

**Repair**: same as Mode 4. Judge evaluation of B and D might be too strict, or evidence doesn't actually contain the right numbers.

**Potential gain**: 0-2/100 (per question).

## Summary of Repair Vectors

| Mode | Count | Repair cost | Potential gain | Risk |
|------|------:|-------------|---------------:|------|
| 1. Wrong-company | 2 | Low (filter by company name) | 2 | Low |
| 2. Missing evidence | 1 | Low (targeted retrieval) | 1 | Low |
| 3. TOC crowding | 1+ | Medium (filter by record_type) | 0-2 | Medium |
| 4. Judge over-rejects | 4 | High (rewrite judge) | 0-4 | High |
| 5. Multi-doc synthesis | 1 | High (rewrite synthesis) | 0-2 | High |

**Low-risk, high-certainty repairs**: Modes 1+2 (3 questions, +3/100, low risk).
**Medium-risk repairs**: Mode 3 (1-3 questions, +0-2/100, medium risk).
**High-risk repairs**: Modes 4+5 (5 questions, +0-6/100, high risk).

**Realistic ceiling**: 87 + 3 (modes 1+2) + 1 (mode 3) = **~91/100**, if low+medium repairs succeed.

## Update: Mode 1 实体一致性 post-filter 的实证结论

按用户建议，**最窄的 entity-consistency post-filter 已实施并测试，结论：不能修 fin_a_008/015 这两题**。

**实施细节**（已回退）：
- 触发条件：`domain ∈ {financial_reports, research}` + `answer_format ∈ {multi, mcq}` + 题干含明确公司实体（海

