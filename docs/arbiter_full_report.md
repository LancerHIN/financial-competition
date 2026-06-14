# Multi-Answer Arbitration Report

**Sources compared**: answer-0612-78.5 | answer | answer-0611-64.7 | answer-0611-65.76 | answer-0612-64.5 | answer-0611-62

**Total questions**: 100

**Disagreements**: 41


## Pattern distribution (longest vs shortest)

| Pattern | Count | Meaning |
|---------|------:|---------|
| identical | 59 | all sources agree |
| shrinkage | 28 | longest answer has extra letters vs shortest (漏选风险) |
| substitution | 13 | totally different letters (判题分歧) |


## Rule rewards (vs reference)

| Rule | Y | N | Net |
|------|--:|--:|----:|
| majority | 15 | 85 | -70 |
| final_decision | 15 | 85 | -70 |


## Disagreements (detail)

### fc_a_001  ( / multi)
- **Pattern**: `shrinkage` (top count 5/6)
- **Answers**: answer-0612-78.5=ABD | answer=ABD | answer-0611-64.7=AD | answer-0611-65.76=ABD | answer-0612-64.5=ABD | answer-0611-62=ABD
- **Evidence round0_supported**: []
- **Evidence final_supported**: []
- **Evidence answer_set**: ['A', 'B', 'D']
- **Decision**: `ABD`  via **majority**
- **Reference correct?**: YES

### fc_a_002  ( / multi)
- **Pattern**: `shrinkage` (top count 4/6)
- **Answers**: answer-0612-78.5=ABD | answer=ABD | answer-0611-64.7=AD | answer-0611-65.76=AD | answer-0612-64.5=ABD | answer-0611-62=ABD
- **Evidence round0_supported**: []
- **Evidence final_supported**: []
- **Evidence answer_set**: ['A', 'B', 'D']
- **Decision**: `ABD`  via **majority**
- **Reference correct?**: YES

### fc_a_003  ( / mcq)
- **Pattern**: `substitution` (top count 5/6)
- **Answers**: answer-0612-78.5=A | answer=B | answer-0611-64.7=B | answer-0611-65.76=B | answer-0612-64.5=B | answer-0611-62=B
- **Evidence round0_supported**: []
- **Evidence final_supported**: []
- **Evidence answer_set**: ['B']
- **Decision**: `B`  via **majority**
- **Reference correct?**: NO

### fc_a_004  ( / multi)
- **Pattern**: `shrinkage` (top count 4/6)
- **Answers**: answer-0612-78.5=AD | answer=A | answer-0611-64.7=AD | answer-0611-65.76=AD | answer-0612-64.5=AD | answer-0611-62=ABD
- **Evidence round0_supported**: []
- **Evidence final_supported**: []
- **Evidence answer_set**: ['A']
- **Decision**: `AD`  via **majority**
- **Reference correct?**: NO

### fc_a_005  ( / multi)
- **Pattern**: `shrinkage` (top count 5/6)
- **Answers**: answer-0612-78.5=ABD | answer=ABD | answer-0611-64.7=ABD | answer-0611-65.76=ABD | answer-0612-64.5=ABD | answer-0611-62=ABCD
- **Evidence round0_supported**: []
- **Evidence final_supported**: []
- **Evidence answer_set**: ['A', 'B', 'D']
- **Decision**: `ABD`  via **majority**
- **Reference correct?**: YES

### fc_a_011  ( / multi)
- **Pattern**: `shrinkage` (top count 5/6)
- **Answers**: answer-0612-78.5=AB | answer=ABD | answer-0611-64.7=ABD | answer-0611-65.76=ABD | answer-0612-64.5=ABD | answer-0611-62=ABD
- **Evidence round0_supported**: []
- **Evidence final_supported**: []
- **Evidence answer_set**: ['A', 'B', 'D']
- **Decision**: `ABD`  via **majority**
- **Reference correct?**: NO

### fc_a_013  ( / mcq)
- **Pattern**: `substitution` (top count 4/6)
- **Answers**: answer-0612-78.5=A | answer=B | answer-0611-64.7=A | answer-0611-65.76=A | answer-0612-64.5=B | answer-0611-62=A
- **Evidence round0_supported**: []
- **Evidence final_supported**: []
- **Evidence answer_set**: ['B']
- **Decision**: `A`  via **majority**
- **Reference correct?**: YES

### fc_a_014  ( / multi)
- **Pattern**: `shrinkage` (top count 5/6)
- **Answers**: answer-0612-78.5=A | answer=A | answer-0611-64.7=A | answer-0611-65.76=A | answer-0612-64.5=A | answer-0611-62=AC
- **Evidence round0_supported**: []
- **Evidence final_supported**: []
- **Evidence answer_set**: ['A']
- **Decision**: `A`  via **majority**
- **Reference correct?**: NO

### fc_a_016  ( / multi)
- **Pattern**: `shrinkage` (top count 5/6)
- **Answers**: answer-0612-78.5=ABC | answer=ABC | answer-0611-64.7=ABC | answer-0611-65.76=ABCD | answer-0612-64.5=ABC | answer-0611-62=ABC
- **Evidence round0_supported**: []
- **Evidence final_supported**: []
- **Evidence answer_set**: ['A', 'B', 'C']
- **Decision**: `ABC`  via **majority**
- **Reference correct?**: YES

### fc_a_017  ( / multi)
- **Pattern**: `shrinkage` (top count 5/6)
- **Answers**: answer-0612-78.5=AD | answer=AD | answer-0611-64.7=AD | answer-0611-65.76=D | answer-0612-64.5=AD | answer-0611-62=AD
- **Evidence round0_supported**: []
- **Evidence final_supported**: []
- **Evidence answer_set**: ['A', 'D']
- **Decision**: `AD`  via **majority**
- **Reference correct?**: YES

### fc_a_019  ( / multi)
- **Pattern**: `substitution` (top count 5/6)
- **Answers**: answer-0612-78.5=AB | answer=BD | answer-0611-64.7=BD | answer-0611-65.76=BD | answer-0612-64.5=BD | answer-0611-62=BD
- **Evidence round0_supported**: []
- **Evidence final_supported**: []
- **Evidence answer_set**: ['B', 'D']
- **Decision**: `BD`  via **majority**
- **Reference correct?**: NO

### fc_a_020  ( / multi)
- **Pattern**: `shrinkage` (top count 3/6)
- **Answers**: answer-0612-78.5=ABD | answer=ABD | answer-0611-64.7=ABD | answer-0611-65.76=AB | answer-0612-64.5=AB | answer-0611-62=BD
- **Evidence round0_supported**: []
- **Evidence final_supported**: []
- **Evidence answer_set**: ['A', 'B', 'D']
- **Decision**: `ABD`  via **majority**
- **Reference correct?**: YES

### fin_a_004  ( / multi)
- **Pattern**: `shrinkage` (top count 5/6)
- **Answers**: answer-0612-78.5=ABD | answer=BD | answer-0611-64.7=BD | answer-0611-65.76=BD | answer-0612-64.5=BD | answer-0611-62=BD
- **Evidence round0_supported**: []
- **Evidence final_supported**: []
- **Evidence answer_set**: ['B', 'D']
- **Decision**: `BD`  via **majority**
- **Reference correct?**: NO

### fin_a_005  ( / multi)
- **Pattern**: `shrinkage` (top count 4/6)
- **Answers**: answer-0612-78.5=ABD | answer=AB | answer-0611-64.7=AB | answer-0611-65.76=ABD | answer-0612-64.5=AB | answer-0611-62=AB
- **Evidence round0_supported**: []
- **Evidence final_supported**: []
- **Evidence answer_set**: ['A', 'B']
- **Decision**: `AB`  via **majority**
- **Reference correct?**: NO

### fin_a_008  ( / mcq)
- **Pattern**: `substitution` (top count 4/6)
- **Answers**: answer-0612-78.5=D | answer=A | answer-0611-64.7=A | answer-0611-65.76=A | answer-0612-64.5=A | answer-0611-62=D
- **Evidence round0_supported**: []
- **Evidence final_supported**: []
- **Evidence answer_set**: ['A']
- **Decision**: `A`  via **majority**
- **Reference correct?**: NO

### fin_a_010  ( / mcq)
- **Pattern**: `substitution` (top count 5/6)
- **Answers**: answer-0612-78.5=A | answer=A | answer-0611-64.7=B | answer-0611-65.76=A | answer-0612-64.5=A | answer-0611-62=A
- **Evidence round0_supported**: []
- **Evidence final_supported**: []
- **Evidence answer_set**: ['A']
- **Decision**: `A`  via **majority**
- **Reference correct?**: YES

### fin_a_012  ( / multi)
- **Pattern**: `substitution` (top count 2/6)
- **Answers**: answer-0612-78.5=ABD | answer=BD | answer-0611-64.7=BCD | answer-0611-65.76=BCD | answer-0612-64.5=BD | answer-0611-62=AD
- **Evidence round0_supported**: []
- **Evidence final_supported**: []
- **Evidence answer_set**: ['B', 'D']
- **Decision**: `BD`  via **majority**
- **Reference correct?**: NO

### fin_a_014  ( / multi)
- **Pattern**: `shrinkage` (top count 5/6)
- **Answers**: answer-0612-78.5=AB | answer=B | answer-0611-64.7=B | answer-0611-65.76=B | answer-0612-64.5=B | answer-0611-62=B
- **Evidence round0_supported**: []
- **Evidence final_supported**: []
- **Evidence answer_set**: ['B']
- **Decision**: `B`  via **majority**
- **Reference correct?**: NO

### fin_a_015  ( / mcq)
- **Pattern**: `substitution` (top count 5/6)
- **Answers**: answer-0612-78.5=B | answer=B | answer-0611-64.7=B | answer-0611-65.76=C | answer-0612-64.5=B | answer-0611-62=B
- **Evidence round0_supported**: []
- **Evidence final_supported**: []
- **Evidence answer_set**: ['B']
- **Decision**: `B`  via **majority**
- **Reference correct?**: NO

### fin_a_020  ( / multi)
- **Pattern**: `shrinkage` (top count 5/6)
- **Answers**: answer-0612-78.5=AB | answer=B | answer-0611-64.7=B | answer-0611-65.76=B | answer-0612-64.5=B | answer-0611-62=B
- **Evidence round0_supported**: []
- **Evidence final_supported**: []
- **Evidence answer_set**: ['B']
- **Decision**: `B`  via **majority**
- **Reference correct?**: NO

### ins_a_003  ( / mcq)
- **Pattern**: `substitution` (top count 5/6)
- **Answers**: answer-0612-78.5=A | answer=D | answer-0611-64.7=D | answer-0611-65.76=D | answer-0612-64.5=D | answer-0611-62=D
- **Evidence round0_supported**: []
- **Evidence final_supported**: []
- **Evidence answer_set**: ['D']
- **Decision**: `D`  via **majority**
- **Reference correct?**: NO

### ins_a_007  ( / multi)
- **Pattern**: `shrinkage` (top count 5/6)
- **Answers**: answer-0612-78.5=BC | answer=BC | answer-0611-64.7=BC | answer-0611-65.76=BC | answer-0612-64.5=C | answer-0611-62=BC
- **Evidence round0_supported**: []
- **Evidence final_supported**: []
- **Evidence answer_set**: ['B', 'C']
- **Decision**: `BC`  via **majority**
- **Reference correct?**: YES

### ins_a_009  ( / mcq)
- **Pattern**: `substitution` (top count 5/6)
- **Answers**: answer-0612-78.5=C | answer=C | answer-0611-64.7=A | answer-0611-65.76=C | answer-0612-64.5=C | answer-0611-62=C
- **Evidence round0_supported**: []
- **Evidence final_supported**: []
- **Evidence answer_set**: ['C']
- **Decision**: `C`  via **majority**
- **Reference correct?**: YES

### ins_a_010  ( / multi)
- **Pattern**: `shrinkage` (top count 3/6)
- **Answers**: answer-0612-78.5=AB | answer=AB | answer-0611-64.7=B | answer-0611-65.76=AB | answer-0612-64.5=B | answer-0611-62=ABCD
- **Evidence round0_supported**: []
- **Evidence final_supported**: []
- **Evidence answer_set**: ['A', 'B']
- **Decision**: `AB`  via **majority**
- **Reference correct?**: YES

### ins_a_014  ( / multi)
- **Pattern**: `shrinkage` (top count 5/6)
- **Answers**: answer-0612-78.5=AB | answer=AB | answer-0611-64.7=AB | answer-0611-65.76=AB | answer-0612-64.5=AB | answer-0611-62=ABD
- **Evidence round0_supported**: []
- **Evidence final_supported**: []
- **Evidence answer_set**: ['A', 'B']
- **Decision**: `AB`  via **majority**
- **Reference correct?**: NO

### ins_a_015  ( / multi)
- **Pattern**: `substitution` (top count 5/6)
- **Answers**: answer-0612-78.5=AB | answer=AB | answer-0611-64.7=AB | answer-0611-65.76=AB | answer-0612-64.5=AB | answer-0611-62=BD
- **Evidence round0_supported**: []
- **Evidence final_supported**: []
- **Evidence answer_set**: ['A', 'B']
- **Decision**: `AB`  via **majority**
- **Reference correct?**: YES

### ins_a_017  ( / multi)
- **Pattern**: `shrinkage` (top count 5/6)
- **Answers**: answer-0612-78.5=ABCD | answer=ABCD | answer-0611-64.7=ABCD | answer-0611-65.76=ABC | answer-0612-64.5=ABCD | answer-0611-62=ABCD
- **Evidence round0_supported**: []
- **Evidence final_supported**: []
- **Evidence answer_set**: ['A', 'B', 'C', 'D']
- **Decision**: `ABCD`  via **majority**
- **Reference correct?**: YES

### ins_a_018  ( / multi)
- **Pattern**: `shrinkage` (top count 5/6)
- **Answers**: answer-0612-78.5=BCD | answer=BCD | answer-0611-64.7=BCD | answer-0611-65.76=BCD | answer-0612-64.5=BCD | answer-0611-62=BD
- **Evidence round0_supported**: []
- **Evidence final_supported**: []
- **Evidence answer_set**: ['B', 'C', 'D']
- **Decision**: `BCD`  via **majority**
- **Reference correct?**: YES

### ins_a_019  ( / multi)
- **Pattern**: `shrinkage` (top count 4/6)
- **Answers**: answer-0612-78.5=ABD | answer=AB | answer-0611-64.7=AB | answer-0611-65.76=AB | answer-0612-64.5=AB | answer-0611-62=ABD
- **Evidence round0_supported**: []
- **Evidence final_supported**: []
- **Evidence answer_set**: ['A', 'B']
- **Decision**: `AB`  via **majority**
- **Reference correct?**: NO

### ins_a_020  ( / mcq)
- **Pattern**: `substitution` (top count 4/6)
- **Answers**: answer-0612-78.5=D | answer=D | answer-0611-64.7=D | answer-0611-65.76=B | answer-0612-64.5=B | answer-0611-62=D
- **Evidence round0_supported**: []
- **Evidence final_supported**: []
- **Evidence answer_set**: ['D']
- **Decision**: `D`  via **majority**
- **Reference correct?**: YES

### reg_a_001  ( / multi)
- **Pattern**: `shrinkage` (top count 5/6)
- **Answers**: answer-0612-78.5=ACD | answer=AC | answer-0611-64.7=AC | answer-0611-65.76=AC | answer-0612-64.5=AC | answer-0611-62=AC
- **Evidence round0_supported**: []
- **Evidence final_supported**: []
- **Evidence answer_set**: ['A', 'C']
- **Decision**: `AC`  via **majority**
- **Reference correct?**: NO

### reg_a_011  ( / multi)
- **Pattern**: `shrinkage` (top count 5/6)
- **Answers**: answer-0612-78.5=ACD | answer=AC | answer-0611-64.7=ACD | answer-0611-65.76=ACD | answer-0612-64.5=ACD | answer-0611-62=ACD
- **Evidence round0_supported**: []
- **Evidence final_supported**: []
- **Evidence answer_set**: ['A', 'C']
- **Decision**: `ACD`  via **majority**
- **Reference correct?**: NO

### reg_a_013  ( / mcq)
- **Pattern**: `substitution` (top count 4/6)
- **Answers**: answer-0612-78.5=A | answer=B | answer-0611-64.7=A | answer-0611-65.76=A | answer-0612-64.5=B | answer-0611-62=A
- **Evidence round0_supported**: []
- **Evidence final_supported**: []
- **Evidence answer_set**: ['B']
- **Decision**: `A`  via **majority**
- **Reference correct?**: YES

### reg_a_017  ( / multi)
- **Pattern**: `shrinkage` (top count 4/6)
- **Answers**: answer-0612-78.5=ABC | answer=AB | answer-0611-64.7=ABC | answer-0611-65.76=ABC | answer-0612-64.5=ABC | answer-0611-62=AB
- **Evidence round0_supported**: []
- **Evidence final_supported**: []
- **Evidence answer_set**: ['A', 'B']
- **Decision**: `ABC`  via **majority**
- **Reference correct?**: NO

### res_a_002  ( / multi)
- **Pattern**: `shrinkage` (top count 4/6)
- **Answers**: answer-0612-78.5=ABC | answer=AC | answer-0611-64.7=AC | answer-0611-65.76=AC | answer-0612-64.5=ABC | answer-0611-62=AC
- **Evidence round0_supported**: []
- **Evidence final_supported**: []
- **Evidence answer_set**: ['A', 'C']
- **Decision**: `AC`  via **majority**
- **Reference correct?**: YES

### res_a_004  ( / multi)
- **Pattern**: `shrinkage` (top count 5/6)
- **Answers**: answer-0612-78.5=ABC | answer=AB | answer-0611-64.7=ABC | answer-0611-65.76=ABC | answer-0612-64.5=ABC | answer-0611-62=ABC
- **Evidence round0_supported**: []
- **Evidence final_supported**: []
- **Evidence answer_set**: ['A', 'B']
- **Decision**: `ABC`  via **majority**
- **Reference correct?**: NO

### res_a_007  ( / multi)
- **Pattern**: `shrinkage` (top count 5/6)
- **Answers**: answer-0612-78.5=ABC | answer=AB | answer-0611-64.7=AB | answer-0611-65.76=AB | answer-0612-64.5=AB | answer-0611-62=AB
- **Evidence round0_supported**: []
- **Evidence final_supported**: []
- **Evidence answer_set**: ['A', 'B']
- **Decision**: `AB`  via **majority**
- **Reference correct?**: NO

### res_a_017  ( / multi)
- **Pattern**: `shrinkage` (top count 4/6)
- **Answers**: answer-0612-78.5=ABD | answer=AD | answer-0611-64.7=AD | answer-0611-65.76=AD | answer-0612-64.5=AD | answer-0611-62=ABD
- **Evidence round0_supported**: []
- **Evidence final_supported**: []
- **Evidence answer_set**: ['A', 'D']
- **Decision**: `AD`  via **majority**
- **Reference correct?**: NO

### res_a_018  ( / mcq)
- **Pattern**: `substitution` (top count 5/6)
- **Answers**: answer-0612-78.5=B | answer=A | answer-0611-64.7=A | answer-0611-65.76=A | answer-0612-64.5=A | answer-0611-62=A
- **Evidence round0_supported**: []
- **Evidence final_supported**: []
- **Evidence answer_set**: ['A']
- **Decision**: `A`  via **majority**
- **Reference correct?**: NO

### res_a_019  ( / multi)
- **Pattern**: `shrinkage` (top count 4/6)
- **Answers**: answer-0612-78.5=AB | answer=AB | answer-0611-64.7=A | answer-0611-65.76=A | answer-0612-64.5=A | answer-0611-62=A
- **Evidence round0_supported**: []
- **Evidence final_supported**: []
- **Evidence answer_set**: ['A', 'B']
- **Decision**: `A`  via **majority**
- **Reference correct?**: NO

### res_a_020  ( / multi)
- **Pattern**: `shrinkage` (top count 2/6)
- **Answers**: answer-0612-78.5=AD | answer=A | answer-0611-64.7=AC | answer-0611-65.76=AC | answer-0612-64.5=A | answer-0611-62=AD
- **Evidence round0_supported**: []
- **Evidence final_supported**: []
- **Evidence answer_set**: ['A']
- **Decision**: `AD`  via **majority**
- **Reference correct?**: NO


## Top 30 questions needing attention (sorted by disagreement + evidence mismatch)

| QID | Domain | Format | Pattern | Decision | Rule |
|-----|--------|--------|---------|----------|------|
| fc_a_001 |  | multi | shrinkage | `ABD` | majority |
| fc_a_002 |  | multi | shrinkage | `ABD` | majority |
| fc_a_003 |  | mcq | substitution | `B` | majority |
| fc_a_004 |  | multi | shrinkage | `AD` | majority |
| fc_a_005 |  | multi | shrinkage | `ABD` | majority |
| fc_a_011 |  | multi | shrinkage | `ABD` | majority |
| fc_a_013 |  | mcq | substitution | `A` | majority |
| fc_a_014 |  | multi | shrinkage | `A` | majority |
| fc_a_016 |  | multi | shrinkage | `ABC` | majority |
| fc_a_017 |  | multi | shrinkage | `AD` | majority |
| fc_a_019 |  | multi | substitution | `BD` | majority |
| fc_a_020 |  | multi | shrinkage | `ABD` | majority |
| fin_a_004 |  | multi | shrinkage | `BD` | majority |
| fin_a_005 |  | multi | shrinkage | `AB` | majority |
| fin_a_008 |  | mcq | substitution | `A` | majority |
| fin_a_010 |  | mcq | substitution | `A` | majority |
| fin_a_012 |  | multi | substitution | `BD` | majority |
| fin_a_014 |  | multi | shrinkage | `B` | majority |
| fin_a_015 |  | mcq | substitution | `B` | majority |
| fin_a_020 |  | multi | shrinkage | `B` | majority |
| ins_a_003 |  | mcq | substitution | `D` | majority |
| ins_a_007 |  | multi | shrinkage | `BC` | majority |
| ins_a_009 |  | mcq | substitution | `C` | majority |
| ins_a_010 |  | multi | shrinkage | `AB` | majority |
| ins_a_014 |  | multi | shrinkage | `AB` | majority |
| ins_a_015 |  | multi | substitution | `AB` | majority |
| ins_a_017 |  | multi | shrinkage | `ABCD` | majority |
| ins_a_018 |  | multi | shrinkage | `BCD` | majority |
| ins_a_019 |  | multi | shrinkage | `AB` | majority |
| ins_a_020 |  | mcq | substitution | `D` | majority |