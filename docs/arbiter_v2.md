# Multi-Answer Arbitration Report

**Sources compared**: answer-0612-78.5 | answer

**Total questions**: 100

**Disagreements**: 23


## Pattern distribution (longest vs shortest)

| Pattern | Count | Meaning |
|---------|------:|---------|
| identical | 77 | all sources agree |
| shrinkage | 16 | longest answer has extra letters vs shortest (漏选风险) |
| substitution | 7 | totally different letters (判题分歧) |


## Rule rewards (vs reference)

| Rule | Y | N | Net |
|------|--:|--:|----:|
| majority | 17 | 83 | -66 |
| final_decision | 20 | 80 | -60 |


## Disagreements (detail)

### fc_a_003  ( / mcq)
- **Pattern**: `substitution` (top count 1.0/2.0)
- **Answers**: answer-0612-78.5=A | answer=B
- **Decision**: `A`  via **majority**
- **Reference correct?**: YES

### fc_a_004  ( / multi)
- **Pattern**: `shrinkage` (top count 1.0/2.0)
- **Answers**: answer-0612-78.5=AD | answer=A
- **Decision**: `AD`  via **longest_shrinkage_fallback**
  - note: shrinkage without evidence: longest='AD' > majority='A'; preferring longer to defend against漏选
- **Reference correct?**: NO

### fc_a_011  ( / multi)
- **Pattern**: `shrinkage` (top count 1.0/2.0)
- **Answers**: answer-0612-78.5=AB | answer=ABD
- **Decision**: `ABD`  via **longest_shrinkage_fallback**
  - note: shrinkage without evidence: longest='ABD' > majority='AB'; preferring longer to defend against漏选
- **Reference correct?**: NO

### fc_a_013  ( / mcq)
- **Pattern**: `substitution` (top count 1.0/2.0)
- **Answers**: answer-0612-78.5=A | answer=B
- **Decision**: `A`  via **majority**
- **Reference correct?**: YES

### fc_a_019  ( / multi)
- **Pattern**: `substitution` (top count 1.0/2.0)
- **Answers**: answer-0612-78.5=AB | answer=BD
- **Decision**: `AB`  via **majority**
- **Reference correct?**: YES

### fin_a_004  ( / multi)
- **Pattern**: `shrinkage` (top count 1.0/2.0)
- **Answers**: answer-0612-78.5=ABD | answer=BD
- **Decision**: `ABD`  via **majority**
- **Reference correct?**: YES

### fin_a_005  ( / multi)
- **Pattern**: `shrinkage` (top count 1.0/2.0)
- **Answers**: answer-0612-78.5=ABD | answer=AB
- **Decision**: `ABD`  via **longest_shrinkage_fallback**
  - note: shrinkage without evidence: longest='ABD' > majority='AB'; preferring longer to defend against漏选
- **Reference correct?**: YES

### fin_a_008  ( / mcq)
- **Pattern**: `substitution` (top count 1.0/2.0)
- **Answers**: answer-0612-78.5=D | answer=A
- **Decision**: `A`  via **majority**
- **Reference correct?**: NO

### fin_a_012  ( / multi)
- **Pattern**: `shrinkage` (top count 1.0/2.0)
- **Answers**: answer-0612-78.5=ABD | answer=BD
- **Decision**: `ABD`  via **majority**
- **Reference correct?**: YES

### fin_a_014  ( / multi)
- **Pattern**: `shrinkage` (top count 1.0/2.0)
- **Answers**: answer-0612-78.5=AB | answer=B
- **Decision**: `AB`  via **majority**
- **Reference correct?**: YES

### fin_a_020  ( / multi)
- **Pattern**: `shrinkage` (top count 1.0/2.0)
- **Answers**: answer-0612-78.5=AB | answer=B
- **Decision**: `AB`  via **majority**
- **Reference correct?**: YES

### ins_a_003  ( / mcq)
- **Pattern**: `substitution` (top count 1.0/2.0)
- **Answers**: answer-0612-78.5=A | answer=D
- **Decision**: `A`  via **majority**
- **Reference correct?**: YES

### ins_a_019  ( / multi)
- **Pattern**: `shrinkage` (top count 1.0/2.0)
- **Answers**: answer-0612-78.5=ABD | answer=AB
- **Decision**: `ABD`  via **longest_shrinkage_fallback**
  - note: shrinkage without evidence: longest='ABD' > majority='AB'; preferring longer to defend against漏选
- **Reference correct?**: NO

### reg_a_001  ( / multi)
- **Pattern**: `shrinkage` (top count 1.0/2.0)
- **Answers**: answer-0612-78.5=ACD | answer=AC
- **Decision**: `ACD`  via **longest_shrinkage_fallback**
  - note: shrinkage without evidence: longest='ACD' > majority='AC'; preferring longer to defend against漏选
- **Reference correct?**: YES

### reg_a_011  ( / multi)
- **Pattern**: `shrinkage` (top count 1.0/2.0)
- **Answers**: answer-0612-78.5=ACD | answer=AC
- **Decision**: `ACD`  via **longest_shrinkage_fallback**
  - note: shrinkage without evidence: longest='ACD' > majority='AC'; preferring longer to defend against漏选
- **Reference correct?**: NO

### reg_a_013  ( / mcq)
- **Pattern**: `substitution` (top count 1.0/2.0)
- **Answers**: answer-0612-78.5=A | answer=B
- **Decision**: `A`  via **majority**
- **Reference correct?**: YES

### reg_a_017  ( / multi)
- **Pattern**: `shrinkage` (top count 1.0/2.0)
- **Answers**: answer-0612-78.5=ABC | answer=AB
- **Decision**: `ABC`  via **longest_shrinkage_fallback**
  - note: shrinkage without evidence: longest='ABC' > majority='AB'; preferring longer to defend against漏选
- **Reference correct?**: NO

### res_a_002  ( / multi)
- **Pattern**: `shrinkage` (top count 1.0/2.0)
- **Answers**: answer-0612-78.5=ABC | answer=AC
- **Decision**: `ABC`  via **majority**
- **Reference correct?**: NO

### res_a_004  ( / multi)
- **Pattern**: `shrinkage` (top count 1.0/2.0)
- **Answers**: answer-0612-78.5=ABC | answer=AB
- **Decision**: `ABC`  via **longest_shrinkage_fallback**
  - note: shrinkage without evidence: longest='ABC' > majority='AB'; preferring longer to defend against漏选
- **Reference correct?**: NO

### res_a_007  ( / multi)
- **Pattern**: `shrinkage` (top count 1.0/2.0)
- **Answers**: answer-0612-78.5=ABC | answer=AB
- **Decision**: `ABC`  via **longest_shrinkage_fallback**
  - note: shrinkage without evidence: longest='ABC' > majority='AB'; preferring longer to defend against漏选
- **Reference correct?**: YES

### res_a_017  ( / multi)
- **Pattern**: `shrinkage` (top count 1.0/2.0)
- **Answers**: answer-0612-78.5=ABD | answer=AD
- **Decision**: `ABD`  via **majority**
- **Reference correct?**: YES

### res_a_018  ( / mcq)
- **Pattern**: `substitution` (top count 1.0/2.0)
- **Answers**: answer-0612-78.5=B | answer=A
- **Decision**: `A`  via **majority**
- **Reference correct?**: NO

### res_a_020  ( / multi)
- **Pattern**: `shrinkage` (top count 1.0/2.0)
- **Answers**: answer-0612-78.5=AD | answer=A
- **Decision**: `AD`  via **longest_shrinkage_fallback**
  - note: shrinkage without evidence: longest='AD' > majority='A'; preferring longer to defend against漏选
- **Reference correct?**: NO


## Top 30 questions needing attention (sorted by disagreement + evidence mismatch)

| QID | Domain | Format | Pattern | Decision | Rule |
|-----|--------|--------|---------|----------|------|
| fc_a_003 |  | mcq | substitution | `A` | majority |
| fc_a_004 |  | multi | shrinkage | `AD` | longest_shrinkage_fallback |
| fc_a_011 |  | multi | shrinkage | `ABD` | longest_shrinkage_fallback |
| fc_a_013 |  | mcq | substitution | `A` | majority |
| fc_a_019 |  | multi | substitution | `AB` | majority |
| fin_a_004 |  | multi | shrinkage | `ABD` | majority |
| fin_a_005 |  | multi | shrinkage | `ABD` | longest_shrinkage_fallback |
| fin_a_008 |  | mcq | substitution | `A` | majority |
| fin_a_012 |  | multi | shrinkage | `ABD` | majority |
| fin_a_014 |  | multi | shrinkage | `AB` | majority |
| fin_a_020 |  | multi | shrinkage | `AB` | majority |
| ins_a_003 |  | mcq | substitution | `A` | majority |
| ins_a_019 |  | multi | shrinkage | `ABD` | longest_shrinkage_fallback |
| reg_a_001 |  | multi | shrinkage | `ACD` | longest_shrinkage_fallback |
| reg_a_011 |  | multi | shrinkage | `ACD` | longest_shrinkage_fallback |
| reg_a_013 |  | mcq | substitution | `A` | majority |
| reg_a_017 |  | multi | shrinkage | `ABC` | longest_shrinkage_fallback |
| res_a_002 |  | multi | shrinkage | `ABC` | majority |
| res_a_004 |  | multi | shrinkage | `ABC` | longest_shrinkage_fallback |
| res_a_007 |  | multi | shrinkage | `ABC` | longest_shrinkage_fallback |
| res_a_017 |  | multi | shrinkage | `ABD` | majority |
| res_a_018 |  | mcq | substitution | `A` | majority |
| res_a_020 |  | multi | shrinkage | `AD` | longest_shrinkage_fallback |
| fc_a_001 |  | multi | identical | `ABD` | majority |
| fc_a_002 |  | multi | identical | `ABD` | majority |
| fc_a_005 |  | multi | identical | `ABD` | majority |
| fc_a_006 |  | mcq | identical | `A` | majority |
| fc_a_007 |  | multi | identical | `BD` | majority |
| fc_a_008 |  | mcq | identical | `B` | majority |
| fc_a_009 |  | multi | identical | `ABD` | majority |