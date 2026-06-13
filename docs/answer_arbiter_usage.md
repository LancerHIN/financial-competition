# answer_arbiter.py Usage

## What it does

When you have multiple `answer.csv` files (different prompts, commits, or runs),
this tool:

1. **Detects disagreements** - finds questions where answers differ across sources
2. **Classifies patterns**:
   - **shrinkage** - majority deletes letters (AB -> B, ABC -> AC). High risk of漏选.
   - **expansion** - majority adds letters (AB -> ABC). High risk of过选.
   - **substitution** - letters differ but neither contains the other (AB -> CD).
3. **Validates evidence** - for each disagreement, checks if the answer's letters
   have `supported` evidence, and if any supported letters are missing from the
   answer.
4. **Arbitrates**:
   - If reference provided: reports reward for each rule (majority, longest, etc.)
   - Without reference: picks majority with shrinkage-aware tie-break.
5. **Writes per-question arbitration** to `<out_prefix>_arbited.csv` for downstream use.


## Usage

### Basic - detect disagreements

```bash
python answer_arbiter.py \
    --answer answer-0612-78.5.csv \
    --answer answer-0611-65.76.csv \
    --answer answer-0611-62.csv
```

### With reference (reports rule rewards)

```bash
python answer_arbiter.py \
    --answer a.csv --answer b.csv \
    --reference logs/reference_answers.csv
```

### With evidence (validates each answer)

```bash
python answer_arbiter.py \
    --answer a.csv --answer b.csv \
    --evidence evidence.json \
    --questions questions/group_a/*.json
```

### Output to file

```bash
python answer_arbiter.py \
    --answer a.csv --answer b.csv \
    --out docs/arbiter_report.md
```

### Emit arbited answers

```bash
python answer_arbiter.py \
    --answer a.csv --answer b.csv \
    --print-majority
# writes <out_prefix>_arbited.csv with qid,answer,decision_rule
```

## Output structure

The report contains:

1. **Header summary** - sources, total questions, disagreement count
2. **Pattern distribution** - histogram of shrinkage/expansion/substitution
3. **Rule rewards (vs reference)** - for each arbitration rule, how many right vs wrong
4. **Disagreements table** - per question:
   - pattern (shrinkage/expansion/substitution)
   - per-source answer
   - **evidence round0_supported / final_supported / answer_set**
   - **evidence_mismatch flag** - true if any source's answer diverges from evidence
5. **Sanity warnings** - questions where all sources disagree with reference
6. **Top qids with highest consensus vs reference match**

## Decision rules (in priority order)

1. **stable_majority** - ≥ 2/3 sources agree (or all agree with majority after shrinkage-aware)
2. **expand_majority** - shrinkage pattern + ≥1 source agrees with longest
3. **evidence_supported** - when no majority, use the answer whose letters are all `round0_supported`
4. **longest** - tie-break by length
5. **unsure** - cannot determine; report for manual review

## Why "shrinkage-aware"?

In our experiments (see docs/diagnostic_report_78.5_regression.md), AB→B and ABD→BD are
the **systematic**漏选 pattern. A naive majority rule on a multi-where-2-out-of-3-shrink
dataset will pick the wrong (shortened) answer. The arbiter flags shrinkage cases and
prefers the longer candidate when it has any evidence backing.

## Example output

```
# Multi-Answer Arbitration Report
**Sources compared**: a.csv | b.csv | c.csv
**Total questions**: 100
**Disagreements**: 33

## Pattern distribution
| Pattern     | Count | Meaning                          |
|-------------|------:|----------------------------------|
| identical   | 67    | all sources agree                |
| shrinkage   | 11    | majority deletes letters (risky) |
| expansion   | 8     | majority adds letters (risky)    |
| substitution| 14    | letters differ, neither contains |

## Rule rewards (vs reference)
| Rule             | Y  | N  | Net |
|------------------|---:|---:|----:|
| majority         | 56 | 33 | +23 |
| longest          | 48 | 41 | +7  |
| evidence_supported| 31| 22 | +9  |
```

## Use cases

- **Pick best answer between candidate runs** when reference is unavailable
- **Diagnose where systematic errors come from** by comparing across answer files
- **Build ensemble predictions** by majority vote
- **Audit a specific answer** against its evidence.json to find漏选/过选
