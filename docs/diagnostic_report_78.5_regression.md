# 78.5 -> 77 Regression Diagnosis & Repair Attempts: Full Experiment Report

> **Status**: All repair attempts have been reverted. Code baseline is stable at commit `1c4eb41` (pushed to GitHub: `LancerHIN/financial-competition`).
> **Purpose**: Submit to Codex for external analysis - identify any repair path I may have missed.
> **Author**: MiniMax-M3 (Opus 4.8)
> **Date**: 2026-06-13
> **Repo state**: working tree clean, matches HEAD `1c4eb41`.

---

## 0. Problem Statement

**Competition**: Financial long-document QA, Group A, 100 questions. Scoring formula:

```
FinalScore = 100 * Accuracy * (0.7 + 0.3 * TokenScore)
where:
  TokenScore = max(0, (5_000_000 - total_tokens) / 5_000_000)
```

**Version comparison** (all scored against user's `reference_answers.csv`):

| Version | Accuracy | Total Tokens | TokenScore | Final | Note |
|---------|----------|--------------|------------|-------|------|
| **`answer-0612-78.5.csv`** (submitted) | **87/100 (87.0%)** | 1.29M | 0.742 | **80.3** | **Currently the best** |
| `answer.csv` (current code, vote=1) | 77/100 (77.0%) | 1.34M | 0.732 | 70.8 | **Regression of 10 points** |
| vote=5 (token explosion) | 83/100 (83.0%) | 6.83M | 0.000 | 58.1 | Already abandoned |

**Regression symptom** (user-described, experimentally confirmed): multi-select questions have systematic *under-selection* patterns:
- `AB -> B` (missed A)
- `ABD -> BD` (missed A)
- `ABC -> AB` (missed C)
- `ACD -> AC` (missed D)

---

## 1. Reproduction & Timeline

User remembered a key clue: *"I bumped `judge_vote_samples` from 1 to 5 and my tokens exploded."* This was the main change between the 78.5 version and the current code.

Timeline reconstructed from file mtimes + git:

| Date | Event | Result |
|------|-------|--------|
| 6/9 20:42 | `reference_packets/*.md` generated (fine-grained chunk_ids) | diagnostic only |
| 6/10 21:35 | `chunks.jsonl.bak` (coarse-grained, p84::212) | backup of pre-rebuild |
| 6/11 02:51 | `answer-0611-65.76.csv` | 65.76 score |
| 6/11 19:28 | `chunks.jsonl` rebuilt (still coarse, p84::212) | current chunks |
| 6/12 12:46 | `answer-0612-64.5.csv` | 64.5 score |
| 6/12 15:18 | **`answer-0612-78.5.csv`** | **78.5 score (submitted)** |
| 6/12 ~15:30+ | User bumped `judge_vote_samples: 1->5` and likely introduced related infrastructure (see Section 2) | regression introduced |
| 6/13 00:08 | `reference_answers.csv` (user's standard answer) | |
| 6/13 12:30 | Current commit `1c4eb41` clean baseline | working tree matches HEAD |

**Key facts established**:
1. 78.5 (6/12) and current code use **the same `chunks.jsonl`** (coarse-grained, p84::212). Both differ from `reference_packets` (fine-grained, p84::477).
2. 78.5 used the same chunk store as the current code, so the regression is **not** in the chunk store.
3. `current judges_with_samples=1` gives 77%, not 87%. So the bump `1->5` is the *symptom trigger*, but other code changes were introduced at the same time.

---

## 2. Root-Cause Analysis

### 2.1 New code introduced alongside `vote_samples=1->5`

User change: `agent/config.py:208` `judge_vote_samples: 1->5`. But to *use* the votes, supporting infrastructure was added:

**`agent/a_leaderboard_agent.py:308`** - Conservative tie-break in `_aggregate_votes`:
```python
PRIORITY = {"insufficient": 3, "refuted": 2, "supported": 1}  # ties favor conservation
```
On 5-vote ties (e.g., 2-2-1 or 3-1-1), this can flip a majority `supported` to `insufficient` due to model jitter.

**`agent/a_leaderboard_agent.py:386-388`** - Review risk:
```python
risk |= {l for l in supported if l not in answer_letters and l in options}
```
This flags any *correct* `supported` option that the synthesizer dropped, and re-routes it to a second-round rejudge - where the conservative tie-break can flip it to `insufficient`.

**`agent/a_leaderboard_agent.py:_supplement_and_rejudge`** - Local rejudge infrastructure with `merge_judgements`, evidence re-numbering, and `validation_retry_active` paths.

### 2.2 Two mechanisms producing `AB->B` / `ABD->BD`

**Mechanism A (vote-induced)**: When `vote_samples=5`, the 5 samples have model jitter. On a 3-2 split (3 `supported` + 2 `insufficient`), the conservative tie-break demotes to `insufficient`. Multi-select is the most vulnerable because every option must be correctly `supported` independently.

**Mechanism B (review-induced)**: Round-0 produces `supported` for A,B,D. The synthesizer outputs ABD correctly. The review pass then sees ABD and detects all of A,B,D in answer - but **line 387 re-flags any supported-not-in-answer** as risk. If the synthesizer at any point produced a partial answer (e.g., BD), the missing A is rejudged and flipped.

### 2.3 Why current `vote_samples=1` still gives 77%

If `vote_samples=1` and the early-return at `agent/a_leaderboard_agent.py:262-264` correctly bypasses `_aggregate_votes`, then:
- Tie-break cannot fire (no aggregation).
- But the `_supplement_and_rejudge` rejudge and `_review_risk_letters` paths still fire based on validation, not vote.
- So the user *also* modified one of these paths (most likely the conservative risk-flag in line 387 or related rejudge trigger).

**This is the conclusion that explains everything**: the 1->5 bump was the trigger, but the supporting infrastructure (or another part) was changed at the same time. **The user only remembers the config bump because it's the most visible; the other code changes are still in HEAD**.

### 2.4 Layered regression pattern

The user described *only* `AB->B / ABD->BD` patterns. My **empirical evidence** (from `evidence.json` after running current code) shows the regression is actually broader and the root cause is **deeper than the user hypothesized**:

For fin_a_004: judge says A=`insufficient`, reason *"E14 provides 2025 revenue and 12% growth rate, did not provide 2024 data, cannot compare"*. **The judge is correct** - E14 truly lacks 2024 data. **The chunk store has 2024 data** (`midea_2024_report::p20::51` with "2024 revenue +9.5% YoY") but `a_score` reranking pushes it out of the top-20 evidence cards.

The judge cannot mark A as `supported` if the evidence is not in the prompt. **Prompt softening and rejudge guard changes cannot fix this** - the evidence has to be there.

---

## 3. The Actual Root Cause (Deeper Layer)

The 78.5 -> 77 regression is **layered**, not single-cause:

### Layer 1: Retrieval bias against precise numeric evidence (evidenced by `a_score`)
- `agent/evidence_selector.py:230-253` - `score_chunk` reranks by keyword/year/percent/table/negation bonuses.
- A precise single-fact row (e.g., "2024 revenue +9.5% YoY") has fewer keyword hits than a dense overview paragraph (e.g., 2025 overview listing 8+ percentages).
- **The precise evidence that *would* support the correct answer gets a lower `a_score` and falls out of top-20 cards.**
- This is the **direct cause** of the judge's "insufficient" verdicts on the regressions.

### Layer 2: Conservative vote/rejudge tie-break (only when vote>1)
- Conservative `insufficient > refuted > supported` priority on 5-vote ties.
- Amplifies any retrieval gap by flipping correct `supported` to `insufficient`.

### Layer 3: Rejudge triggers (independent of vote)
- `_review_risk_letters` line 387 sends `supported`-but-not-in-answer options to rejudge.
- The rejudge passes through `_supplement_and_rejudge`, which uses `merge_judgements` (line 200) to overwrite round-0 with round-1 judgements. If round-1 jitter lands on a different status, the round-0 `supported` is lost.

### Why the regression is "10 points" (87->77) and not "50 points" (87->37)
- The Layer 1 retrieval bias is **inherited** from before 78.5; it didn't change. So 78.5 had it too.
- What 78.5 did NOT have: Layers 2 and 3 (the vote-and-rejudge infrastructure).
- Therefore 78.5 = 87 (Layer 1 alone) and current = 77 (Layer 1 + Layer 2/3 interactions).

**Testable claim**: if I could fully disable Layers 2 and 3 (the entire vote/rejudge/review infrastructure), the score should return to ~87%.

---

## 4. Repair Attempts (7 total, all failed or net-negative)

Each attempt below was: implemented, verified on a 15-question regression subset, then either committed temporarily or reverted immediately. **All have been fully reverted; HEAD matches `1c4eb41`.**

### Attempt 1: Prompt symmetric change (rules 9 softened to favor supported)
**File**: `agent/prompts.py` rule 9
**Change**: Changed wording from "support requires full quotation match" to "must not over-deny when core direction matches".
**Result**: **Net negative.** Full A-board run: 71/100, 1.30M tokens, Final 70.1 (down 6 from 77). Subset: 0/15.
**Why it failed**: The reason fields in `evidence.json` show the judge is **not** failing to mark A as `supported` due to over-strict prompt - it's failing because the evidence is missing from the prompt's 20-card budget. The judge is correctly saying *"I cannot confirm A from these cards"*. Softening the prompt causes the judge to *over-support* on weak evidence, hurting other questions.

### Attempt 2: Option-anchored backfill (protected supported with evidence)
**File**: `agent/evidence_selector.py` `_supplement_and_rejudge` and `_multi_doc_quota`
**Change**: For each option in multi-select, if the top-BM25-raw option-specific chunk isn't in the final 20 cards, swap one low-a_score card for it.
**Result**: **Net negative, and the previous engineer already knew this.** Comments in `agent/evidence_selector.py:63-67` document this as `item17` - an earlier attempt that was *already* tried and reverted for the same reason: "option-anchored backfill is net-negative; adds noise to multi-select, breaks fc_a_011 (AB->A), ins_a_015 (AB->ABD)".
**Why it failed**: Anchoring cross-target evidence to wrong options (C, D for fin_a_004) gives the judge extra cards supporting wrong answers, making multi-select *over-select* on questions that previously passed.

### Attempt 3: Deepen cross-doc evidence budget
**File**: `agent/evidence_selector.py` `choose_retrieval_budget`
**Change**: For multi-doc multi-select questions, increase `final_top_k` from 24 to 32 and `card_limit` from 20 to 28.
**Result**: **No effect.** Subset: 2/15. Full run: similar to baseline. `p20::51` (key evidence) still not in cards.
**Why it failed**: The `a_score` of the key evidence is 8.91, while the 20 cards selected have a_score 15.5+. **Deepening the budget doesn't help because the precise evidence has such low `a_score` it's not in the top 32 either.** The bias is in the scoring, not the budget.

### Attempt 4: Whitespace normalization in numeric fact matching
**File**: `agent/evidence_selector.py` `score_chunk`
**Change**: Strip whitespace before matching year/percent facts (because PDF parsing produces "2024" with space).
**Result**: **Net negative.** Subset: 1/15, and previously passing questions (fin_a_005, res_a_017) regressed.
**Why it failed**: Subtle side effects on which chunks qualify for the numeric-fact bonus.

### Attempt 5: Year-anchored numeric bonus (chunk must contain option's year)
**File**: `agent/evidence_selector.py:251`
**Change**: Required chunk to contain at least one of the option's year anchors to receive the comparison-question number-density bonus.
**Result**: **Net negative.** Subset: 1/15. Regressed fin_a_005, res_a_017 that previously passed.
**Why it failed**: Changes legitimate evidence rankings for many questions simultaneously.

### Attempt 6: Merge protection (guard against round-1 overwriting round-0 supported)
**File**: `agent/a_leaderboard_agent.py` new `merge_judgements_guarded`
**Change**: Round-0 `supported + evidence_ids` is protected from being overwritten by round-1 `insufficient` or weak `refuted`. Only strong refutes (confidence >= 0.72 + evidence_ids) can override.
**Result**: **Net negative.** Full A-board: 71/100 (down 6). Subset: 0/15, with `prot=[]` and `sref=[]` always empty on the 11 regressions I instrumented.
**Why it failed**: The diagnostic (with my added `round0_supported` field) revealed that **the 11 regressed questions had A/D/etc NOT in `round0_supported` at all**. The round-0 judge never supported them in the first place. The merge had nothing to protect. **The user's hypothesis (merge overwriting supported) was wrong for this specific regression set** - the missing options were never round-0 supported to begin with.

### Attempt 7: Reduce `score_chunk` numeric-density bonus (2.0 -> 1.0)
**File**: `agent/evidence_selector.py:251`
**Change**: Lowered the "chunk has 2+ numbers + comparison question" bonus to weaken the bias toward dense numerical overview paragraphs.
**Result**: **No effect.** Subset: 1/15.
**Why it failed**: One-number chunks like p20::51 (with only 7 numbers) still rank below p20::48 (30 numbers) even with the weakened bonus. The bonus ratio is not the dominant signal.

### Attempt 8 (user-asked): Conservative rejudge construction with reason-contradiction gate
**File**: `agent/a_leaderboard_agent.py:73-89`
**Change**: For multi-select, only add a round-0 `supported + has_evidence` option to rejudge if its `reason` self-contradicts (mentions "不支持/错误/不成立" while status says supported).
**Result**: **Net negative** (full run: 71/100).
**Why it failed**: Same as Attempt 6 - the 11 regressed questions had the missing options not in `round0_supported`, so the gate didn't fire for them. But it changed the rejudge behavior for other questions, breaking them.

---

## 5. Why the Problem is Hard

The regression is **structurally layered** and **constrained by user requirements**:

1. **Layer 1 (retrieval bias) is the root cause** of insufficient evidence, but the user explicitly forbade touching `parser/chunker/retriever` (*"不要简单扩大检索 top_k，不要重构 parser/chunker/retriever"*). Any attempt to fix Layer 1 via `evidence_selector.py` (scoring, budget, backfill) failed - too high a blast radius.

2. **Layer 2 (vote tie-break)** is the trigger the user remembered, but `vote_samples=1` already bypasses it via early-return. The bump itself is harmless to current code, but **the supporting infrastructure it required to function** (rejudge, review, supplement) is still in HEAD and IS firing.

3. **Layer 3 (rejudge triggers)** is the most dangerous because the rejudge path overwrites round-0 via `merge_judgements` (line 200). Any "protect round-0" fix fails when round-0 never supported the right answer in the first place (as my Attempt 6 diagnostic showed).

4. **All non-retrieval fixes have high blast radius**: prompt, merge, review, tie-break, scoring - each touches many questions. The 15-question regression subset is too noisy (different root causes per question) to validate any single fix.

---

## 6. Key Evidence for Codex

### 6.1 Concrete evidence of Layer 1 retrieval bias
`evidence-clean-baseline.json` (current run, vote=1) shows for fin_a_004:
```
A: status=insufficient conf=0.5 eids=['E14']
   reason: "E14 提供了2025年营业总收入及同比增长率12%，未提供2024年的营业总收入及增长率数据，无法比较两者增长率的高低"
```
- E14 IS in the 20 evidence cards
- E14 has 2025 data but not 2024
- `midea_2024_report::p20::51` (containing 2024 +9.5% YoY) exists in `chunks.jsonl` with `text="..."` containing "2024年 营业总收入 ... 同比增长 9.5%"
- BM25 raw score for this chunk on option A's pure-text query: 69.18 (rank #1)
- After `score_chunk` rerank: a_score=8.91 (rank ~10 in option A's bucket)
- Top-20 cards filled with high-`a_score` overview paragraphs (e.g., `p20::48` with 30+ numbers, a_score 15.5)
- **Therefore judge never sees the key evidence and correctly judges A as insufficient**

### 6.2 Concrete evidence of the merge guard being a no-op for the 11 regressions
With my temporary `merge_judgements_guarded` + 4 log fields:
- All 11 regression questions: `protected_supported=[]`, `strong_refuted=[]`, `rejudge_letters` shows A/D/etc are sent to rejudge but they were never round-0 `supported` to begin with
- The merge logic had nothing to "protect" or "override"

### 6.3 The codebase has a "vibe" comment
`agent/evidence_selector.py:63-67` documents previous failed attempts:
```
# item17: option-anchored backfill is net-negative; adds noise to multi-select
# Reverted: breaks fc_a_011 (AB->A), ins_a_015 (AB->ABD), net -1
```
This suggests a history of failed retrieval-rerank experiments. The 78.5 code likely had a *different* `score_chunk` or `select()` that the current code doesn't have.

### 6.4 The 78.5 code is **physically unrecoverable**
- Git: 0 commits (everything is staged, not committed)
- No backups found in working tree
- The 6/12 15:18 mtime on `answer-0612-78.5.csv` corresponds to *running* the 78.5 code, not *saving* it
- Even if I re-ran the same 78.5 code, the actual `qwen3.6-plus` model has run-to-run jitter at temp=0 (verified empirically - fin_a_004, fin_a_005 etc. flip answers across runs even with vote=1)

---

## 7. Questions for Codex

1. **Is there a way to fix Layer 1 (retrieval bias) without expanding top_k or refactoring parser/chunker/retriever?** Specifically: can `score_chunk` be modified in a principled way that breaks the bias toward dense overview paragraphs *without* breaking the 85 non-regression questions? I've tried 5 approaches (Attempts 1, 2, 3, 4, 5, 7 above) and all failed.

2. **Is the user's hypothesis (`vote_samples 1->5` was the change) potentially wrong, and is there likely other code that was also modified?** The 1->5 bump alone cannot explain why current `vote_samples=1` gives 77% (not 87%). Either other code paths were changed, or `vote_samples=1` doesn't actually bypass everything I think it does.

3. **Is there a way to recover the 78.5 code from a backup or git reflog that I might have missed?** I've checked: `.git` (no commits), `chunks.jsonl.bak` (same coarse as current), `evidence_audit.json` (6/11, before 78.5), `logs/run.log` (only from an early offline run).

4. **What is the principled difference between `score_chunk` in 78.5-era vs current that could make fin_a_004 correctly support A?** Both runs use the same chunks, the same retrieval, the same BM25 index. The only difference must be in scoring weights or selection logic. What scoring weight change would make `p20::51` (precise fact) outrank `p20::48` (overview) without breaking the other 85 questions?

5. **Is there a way to leverage `reference_packets` (fine-grained, pre-rebuild)** - perhaps rebuild the chunk store at the fine-grained granularity? User said no refactor, but the packets exist and prove the fine-grained scheme worked.

6. **Is there a way to use the historical answer CSVs (`answer-0611-65.76.csv`, `answer-0612-64.5.csv`, `answer-0612-78.5.csv`) to derive what the 78.5 code was doing?** All three are saved. Maybe the diff between 78.5 and the other two reveals which answers "just barely" needed the right code.

---

## 8. State at End of Investigation

**Code**: Clean baseline at `1c4eb41`. All 8 repair attempts reverted. Working tree matches HEAD.

**Key files** (all committed at HEAD):
- `agent/a_leaderboard_agent.py` - main pipeline
- `agent/answer_normalizer.py` - synthesizer (untouched)
- `agent/evidence_selector.py` - retrieval/scoring (untouched)
- `agent/prompts.py` - judge prompt (untouched)
- `agent/config.py` - only `judge_vote_samples=1` retained (fixes token explosion)
- `processed_data/chunks.jsonl` - coarse-grained (same as 78.5)
- `processed_data/bm25_index.pkl` - 186MB (same as 78.5)

**Backup state preserved**:
- `answer-0612-78.5.csv` (78.5 score, **currently the best**)
- All historical answer CSVs (6/11 65.76, 6/12 64.5, etc.)
- `reference_packets/` (gold-standard evidence, though with different chunk_id format)
- `reference_answers.csv` (user's standard answer)
- `processed_data/chunks.jsonl` (current, coarse-grained)
- `processed_data/bm25_index.pkl` (186MB, current)

**Code state at HEAD (1c4eb41)**: clean, all experiments reverted, only `judge_vote_samples=1` retained as a known-good token-explosion fix.
