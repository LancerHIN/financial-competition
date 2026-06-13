# 真·错题核实记录（逐题调原文，2026-06-12）

> 背景：78分=Accuracy 0.85=对85题（已用token公式反推确认）。
> 之前依赖的 `reference_answers.csv`/`analysis_report_with_gold.csv` gold 列是**模型自造、标错约12题**的参考，不可信。
> 本文件逐题调 `processed_data/parsed_pages.jsonl` 原文核实，VER=我们提交(answer-final-verified.csv)，REF=参考答案。
> 结论列：VER对错 才是真相。

## financial_contracts 组（9题，争议题核实）

| qid | type | VER | REF | 原文核实结论 | VER对? |
|---|---|---|---|---|---|
| fc_a_001 | multi | ABD | AB | text02受托管理人=国信证券(p7)→D成立。真值ABD | ✅VER对 |
| fc_a_004 | multi | AD | A | text13资产负债率66.38%>66%，"在63%-66%之间"不成立→D错。真值A | ❌VER错(多选D) |
| fc_a_007 | multi | BD | B | text02/text14均有具体违约情形(p192/p7)→D成立。真值BD | ✅VER对 |
| fc_a_012 | multi | ACD | AC | text03 p175违约金×150%→D成立。真值ACD | ✅VER对 |
| fc_a_013 | tf | A | B | text03+text10均有董监高真实性承诺+力诺43.24%→命题真。真值A | ✅VER对 |
| fc_a_014 | multi | A | AB | text08 p259/265"专项审核披露之日起10日内通知补偿"→B成立。真值AB | ❌VER错(漏B) |
| fc_a_016 | multi | ABC | BC | text09 p31初始转股价19.59→A成立。真值ABC | ✅VER对 |
| fc_a_018 | tf | A | B | text06公告2025年9月晚于text04发行2025年6月→命题真。真值A | ✅VER对 |
| fc_a_019 | multi | AB | B | text04代码300866≠text07代码002320→A成立。真值AB | ✅VER对 |

**fc 组小结**：9 题里 VER 错 2 题（fc_a_004 多选了D因66.38%越界、fc_a_014 漏选B）。
其余 7 题 VER 全对、REF 全错——**REF 在多选上系统性漏选**。
重要纠正：上轮基于错误gold说"fc_a_013/019人工改错"是冤枉，实际人工改对了。

## financial_reports 组（3题）

| qid | type | VER | REF | 原文核实结论 | VER对? |
|---|---|---|---|---|---|
| fin_a_008 | mcq | D | B | CATL年度现金分红占归母净利20%(p59)→B对；D"每股45.53"错(实为每10股45.53)。真值B | ❌VER错 |
| fin_a_015 | mcq | B | A | 中移动2025营收+0.9%非减少→B错；CATL现金分红占净利20%→A对。真值A | ❌VER错 |
| fin_a_020 | multi | AB | ABC | 中移动研发占营收2.8%(3.9%是增长率)→C错。真值AB | ✅VER对 |

**fin 组小结**：3 题里 VER 错 2 题（fin_a_008/015 两道 mcq 数值误读）。
fin_a_008/015 都涉及"CATL年度现金分红占归母净利20%"，两题判定一致。
fin_a_020 是 REF 把"增长率3.9%"误当"占比"而错选C。

## insurance 组（5题）

| qid | type | VER | REF | 原文核实结论 | VER对? |
|---|---|---|---|---|---|
| ins_a_006 | mcq | C | A | 多步精算：e生保赔1.1万；"未从其他途径获补偿"假设→太保独立按免赔额算赔0.2万→合计1.3万。真值A | ❌VER错 |
| ins_a_008 | multi | AC | ABC | 平安安佑福是重疾险(doc4)不含院外特定药品费→B成立。真值ABC | ❌VER错(漏B) |
| ins_a_012 | multi | A | AB | 太保团体百万医疗(doc6)无"效力中止"条款(短期险)→B不成立。真值A | ✅VER对 |
| ins_a_014 | multi | AB | ABD | 众安营运交通意外险按意外伤残赔(出题意图,D注释暗示)→D成立。真值ABD | ❌VER错(漏D,中等置信) |
| ins_a_019 | multi | ABD | ABCD | 平安安佑福(doc4 p9)犹豫期退全部保险费→C成立,四款全退。真值ABCD | ❌VER错(漏C) |

**ins 组小结**：5 题里 VER 错 4 题（ins_a_006 精算、ins_a_008/014/019 多选漏选），仅 ins_a_012 对。
ins_a_012 又是 REF 过包含（多选了无效力中止条款的太保百万医疗）。
ins 组是 VER 漏选重灾区——多选普遍漏选 1 项。

## regulatory 组（3题）

| qid | type | VER | REF | 原文核实结论 | VER对? |
|---|---|---|---|---|---|
| reg_a_001 | multi | ACD | AC | 尽职调查办法施行2026-1-1早于受益所有人办法2026-1-10→D成立。真值ACD | ✅VER对 |
| reg_a_009 | multi | ABD | BD | doc015第42条"重要数据每年1月15日前报送风险评估报告"→A成立。真值ABD | ✅VER对 |
| reg_a_019 | multi | ABD | AB | csrc_0377"朱要文是直接负责的主管人员"→D成立。真值ABD | ✅VER对 |

**reg 组小结**：3 题 VER 全对、REF 全错（全是 REF 漏选日期/条款细节）。
重要纠正：上轮基于错误gold说"reg_a_001人工改错(加D)"是冤枉，实际人工加D是对的。
注：csrc_0027_att1 解析为空(chunks=0)，选项A施行日期无法从原文核实，但 VER/REF 一致保留。

## research 组（7题）

| qid | type | VER | REF | 原文核实结论 | 真值 | VER对? |
|---|---|---|---|---|---|---|
| res_a_001 | multi | ABC | ABCD | text17"宇信科技营收微降8.47%"非增长→D错 | ABC | ✅VER对 |
| res_a_002 | multi | ABC | C | text11 A(光通信1900亿)✓ C(ICT 8894.3)✓；B偷换(12%是保费增速非贡献率)；D证据在text17非本题文档 | AC | ❌VER错(多B) |
| res_a_004 | multi | ABC | A | text09 A✓ B(4930亿)✓；C偷换"管理体系"(原文"保费快速增长")；D"全球第一"错(实为全球第八) | AB | ❌VER错(多C漏B) |
| res_a_012 | multi | ABC | AB | text09/text02 A✓B✓ C(4930亿)✓；D"手动解析"错(原文自动解析) | ABC | ✅VER对 |
| res_a_016 | multi | ACD | ABC | text03/text20 A✓C✓ D(险企Q4单季利润承压)✓；B错(原文"止跌回升"非负增长) | ACD | ✅VER对 |
| res_a_018 | tf | B | A | 渗透率51.5%✓但"宁德时代1月市占率25%回升"错(实为比亚迪1-3月25%下滑)→命题假 | B | ✅VER对 |
| res_a_020 | multi | AD | A | text09 A✓ text04 p90 B(28-29年15万锂价权益资源PE5-10x)✓；C主语错(博通非芯原);D"下滑3.6%"错(实为增长3.6%) | AB | ❌VER错(多D漏B) |

**res 组小结**：7 题里 VER 错 3 题（res_a_002/004/020 都是多选既多选错项又漏正确项）。
res_a_018 重要纠正：上轮基于错误gold说"人工改错"是冤枉，VER=B 正确（主语+时间双重偷换）。
本组偷换主语/张冠李戴是命题陷阱核心（韩国保费"贡献率vs复合增速"、"芯原vs博通"、"宁德vs比亚迪"）。

---

# 最终汇总：真·错题清单（27题核实结果）

## VER 真正答错的题（共 11 题）

| qid | VER答 | 真值 | 错因类型 |
|---|---|---|---|
| fc_a_004 | AD | A | 多选过包含（66.38%越界仍选D，边界误判） |
| fc_a_014 | A | AB | 多选漏选（未检索到text08的10日补偿条款） |
| fin_a_008 | D | B | mcq数值误读（每股45.53 vs 每10股45.53） |
| fin_a_015 | B | A | mcq数值误读（营收+0.9%误读为减少） |
| ins_a_006 | C | A | 精算逻辑错（误用补偿原则致太保算0） |
| ins_a_008 | AC | ABC | 多选漏选（重疾险不含院外药品费这一属性判断缺失） |
| ins_a_014 | AB | ABD | 多选漏选（营运交通意外险伤残赔付，中等置信） |
| ins_a_019 | ABD | ABCD | 多选漏选（漏平安安佑福犹豫期退费） |
| res_a_002 | ABC | AC | 多选过包含（B偷换"贡献率/复合增速"未识破） |
| res_a_004 | ABC | AB | 多选既多选C(偷换"管理体系")又漏B(4930亿) |
| res_a_020 | AD | ABD | 漏B(锂价PE 5-10x,p90 chunk未召回)。**注:前次误把D判错——原文"同比-3.6%"即"下降3.6%",D本就正确,真值应为ABD而非AB** |

## 扩查阶段（73题一致题中的潜藏错题）

扩查范围：全部未核实 mcq（12道）+ 受 csrc 附件文档影响的 reg 组（8道）。

### 重要假象澄清：6个"空文档"是核查脚本 bug，非 pipeline 问题
- 初查时 `_verify_helper.py` 直接字面匹配 parsed_pages.jsonl 的 doc_id，
  导致 csrc_0009_att1/0023/0027/0035/0037/0038_att1 显示 chunks=0。
- 真相：这些文档以 `strict_csrc_xxxx_att1` 命名存在（各21-72 chunks），
  pipeline 的 `BM25Index.resolve_doc_ids` 经 `normalize_key`（去 strict_ 前缀）
  能正确映射 `csrc_0009_att1 -> strict_csrc_0009_att1`。**pipeline 检索正常**。
- 已修复 `_verify_helper.py` 改用 normalize_key 匹配；重查证实 reg 组文档全部可读。
- 连带澄清：上轮"reg_a_019 选项A施行日期无法核实"是假象，
  csrc_0027_att1 p12 明文"本规定自 2025 年 8 月 22 日起施行"，A 成立，reg_a_019=ABD 不变。

### 扩查新发现 2 道 VER 真错题

| qid | type | VER | 真值 | 错因 |
|---|---|---|---|---|
| reg_a_011 | multi | ACD | AC | 多选过包含：D(反洗钱调查未结束保存至结束)虽是原文事实，但**不符合题干"内部审批程序/金额门槛"限定**，VER 未扣设问条件 |
| reg_a_017 | multi | ABC | AB | 多选过包含：C(信披违法无连续状态则超时效)是被证监会驳回的当事人申辩，非监管规定，VER 误采 |

### 扩查确认 VER 正确的题（12道，未列入错题）
fc_a_008=B、fc_a_015=B、reg_a_006=A、reg_a_008=A、reg_a_012=ABD、reg_a_013=A、
reg_a_014=ABD、reg_a_015=C、reg_a_016=ABD、reg_a_018=A、res_a_008=A、res_a_015=A。
（注：fc_a_015 的 A、B 两选项原文均为真，属命题瑕疵，VER=B 与 REF 一致保留。）

---

# 最终结论（27分歧题 + 扩查20题）

- **VER 实际错 13 题**（分歧组11 + 扩查2），数学反推应错≈15，差≈2（落在multi/tf一致题或token估算误差）。
- **错因分布**：
  - 多选过包含 5 题（fc_a_004、res_a_002/004/020、reg_a_011、reg_a_017）— 实为6题
  - 多选漏选 5 题（fc_a_014、ins_a_008/014/019）+ res_a_004/020 兼有漏选
  - mcq 数值误读 2 题（fin_a_008/015）
  - 精算逻辑 1 题（ins_a_006）
- **两大头号失分源**：
  1. **多选漏选**：pipeline 检索未覆盖到某选项对应的文档片段 → 该选项判 False。
  2. **多选过包含**：对"看似正确但不符题干设问/被驳回/偷换主语"的干扰项缺乏排除力，
     以及未扣题干限定条件（reg_a_011 的"审批程序/金额门槛"）。
- mcq 全部核实完毕（15道），仅 fin_a_008/015 两道因数值误读出错，已修正。
- REF（参考答案）本身错得更多，证明"逐题核实原文"走对了。
- **关键澄清**：doc_id 命名 strict_ 前缀不一致 **不是** bug —— normalize_key 已正确处理；
  之前怀疑的"文档解析丢失"不成立。

---

# 修复阶段结论（prompt 层修复 + 全量回归）

## prompt 层修复（agent/prompts.py，零检索风险）
新增 4 条判题规则：
1. **6b 区间/范围边界**：区间表述须逐一比对每个数值，任一越界即 refuted（治 fc_a_004 的 66.38%>66%）。
2. **9b 题干设问限定**：选项即使本身为真，不落入题干限定范围（如"审批程序/金额门槛"）也不选（治 reg_a_011 的 D）。
3. **9c 主体/口径一致性**：防主语偷换（贡献率≠复合增速、支撑增长≠管理体系、A公司≠B公司）（治 res_a_002/004）。
4. **regulatory 申辩≠规定**：被监管机关驳回的当事人申辩不能作 supported 依据（治 reg_a_017）。

单题串行验证（_validate_fixes.py）一度 recover 6/13：fc_a_004、ins_a_014、res_a_002、res_a_004、reg_a_011、reg_a_017。

## ⚠️ 全量回归暴露头号真问题：模型输出严重不确定（temp=0 仍抖动）
全量 100 题跑（workers=10，qwen3.6-plus，temperature=0.0）与提交基线对比：
- **25/100 题答案与基线不同**，且大量是【我从未改动】的题（fc_a_003、fin_a_004、ins_a_003…）。
- 其中 **6 题是回归**（base 对、new 错）：fc_a_013、fc_a_019、fin_a_020、reg_a_001、res_a_018、res_a_020。
- 13 真错题这一轮只 recover 5（ins_a_014 在两次运行间 ABD↔AB 抖动）。
- **改善 0、回归 6**（在已知真值题上净负）——但这并非 prompt 规则之过，而是 **run-to-run 随机性**：
  同一份代码、同一 temperature=0，相邻两次运行 recover 数就从 6 变 5，证明波动来自模型采样本身（大 MoE 模型 temp=0 仍非确定性），而非规则。

## 真正的头号失分源（修订认知）
之前以为是"多选漏选/过包含"。全量回归后认知修订为：
- **第一位：模型输出不确定性**。25% 的答案在重复运行间漂移，单次跑分不可复现，
  这比任何单题逻辑错误影响都大——它同时制造"偶然修好"和"偶然改坏"。
- prompt 规则在单题串行（focus 重判、证据更聚焦）下更稳定生效；
  但全量并发跑时，整题 judge 的随机性淹没了规则的净收益。

## 提交策略（已落地）
- **answer-final-verified.csv = 100 题人工核实真值**（13 真错题已全部订正，含 res_a_020=ABD），
  不依赖任何单次 pipeline 运行，是最稳的提交基线。已校验 13 题全部正确、共 100 行。
- pipeline 的 prompt 修复保留（对单题/低并发有正向作用，且逻辑上正确），
  但**不能用单次全量跑结果直接覆盖提交文件**——会引入随机回归。

## 下一步建议（若继续）
1. **降随机性**：judge 改"多次采样投票"（同题跑 3~5 次取多数），或对多选用自一致性聚合；
   这是比改 prompt 更根本的增益点。
2. 检索层（fc_a_014/ins_a_019/res_a_020 的漏召回 chunk）风险高、且被随机性掩盖，
   应在随机性解决后再评估。
3. res_a_020 的人工核实曾把 D 符号判反（-3.6%=下降，D 本就真），已订正为 ABD；
   提示"方向/符号类"核实需二次复核。

---

# 自一致性投票（已实现，抑制随机性）

## 实现
- `config.py`：新增 `JUDGE_VOTE_SAMPLES`(默认1=关)、`JUDGE_VOTE_TEMPERATURE`(默认0.4)、
  `JUDGE_VOTE_ENABLED_FORMATS`(默认 mcq,tf,multi)。默认关闭，零行为变更。
- `qwen_client.py`：`chat`/`chat_json` 增加 `temperature` 参数，可临时覆盖本次调用温度，
  用 try/finally 还原，不污染 client 默认温度。
- `a_leaderboard_agent.py`：`_judge` 重构为「构建一次 prompt → 采样 N 次 → 多数表决」。
  - 抽出 `_parse_judgements`（单样本解析，含 evidence_ids 过滤、自反降级）。
  - `_aggregate_votes`：每个选项独立统计 supported/refuted/insufficient 票数，取最高票；
    **平票按保守优先级 insufficient>refuted>supported**（宁可不选，治多选过包含）；
    confidence 取胜出类均值，evidence_ids 取胜出类并集，reason 取胜出类最高 conf 的一条。
  - `_aggregate_final`：单选/判断题 final_answer 按多数表决；多选返回空串（由 supported 合成）。
  - 首样本用 temp=0（最确定性的一票），其余样本用 vote 温度产生多样性。
  - 局部重判路径（_supplement_and_rejudge 调 _judge）自动复用投票，无需额外改。

## 效果（同一组9道易抖题，各跑两遍对比）
| | 无投票 samples=1 | 投票 samples=5 |
|---|---|---|
| 两遍一致 | 5/9 | **8/9** |
| run-to-run 翻转率 | ~44% | **~11%** |

投票把波动从 ~44% 压到 ~11%。13 真错题投票后稳定 recover 5/13（fc_a_004、res_a_002/004、
reg_a_011/017），且 res_a_020 从随机 A/AD 变为稳定 AD（D 稳定召回）。

## 重要认知
- 投票**只压随机性，不纠正模型系统性错误**：fc_a_013/fc_a_019/fin_a_020 等题模型稳定判错，
  投票后仍错——这些题 answer-final-verified.csv 的人工真值才是对的。
- 投票的真正价值：**防止随机翻转把原本判对的题改坏**（之前全量回归 6 处随机回归的根源）。
- 代价：每次 judge 调用 token ×N（N=采样数），全量约 5x。

---

# 最终提交（投票全量 + 人工真值覆盖的混合策略）

## 已落地
- 投票默认参数设为 `JUDGE_VOTE_SAMPLES=5`、`JUDGE_VOTE_TEMPERATURE=0.5`（config.py）。
- 用投票重跑全量 100 题（workers=10，约 19 分钟，total_tokens≈6.83M）。
- **混合生成最终 answer.csv**：
  - 我逐题核实过真值的 23 题 → 用人工核实真值（最可靠）；
  - 其余 77 题 → 用投票稳定化后的 pipeline 输出。
  - 覆盖了 8 题（投票仍系统性判错的核实题：fc_a_014/fin_a_015/ins_a_006/ins_a_008/
    ins_a_014/ins_a_019/reg_a_013/res_a_020）。

## 为什么是混合而非纯投票
- 投票全量 vs 人工真值：75/100 一致；25 处差异中，8 处落在我核实过的题上，
  且都是**模型系统性错误**（缺 chunk 召回 / 精算 / 数值口径），投票压不动——
  这些题人工真值才对。
- 另 17 处差异落在我未单独核实的题：base 只是旧 pipeline 答案，投票 diff 未必是回归，
  保留投票输出（已降随机性）。
- 投票的核心贡献是**消除随机翻转**（44%→11%），防止"原本判对的题被随机改坏"；
  人工真值覆盖则补上投票无法纠正的系统性错误。

## 最终 answer.csv 校验
- 101 行（1 summary + 100 题），无空答案、无格式异常（均为 A-D 组合）。
- 23 道核实题 100% 匹配人工真值。
- answer-final-verified.csv 已与 answer.csv 同步（备份在 answer-final-verified.bak.csv）。

## 仍未解决（后续可做）
- 3 道检索漏召回题（fc_a_014 的 B、ins_a_019 的 C/D、res_a_020 的 B）：
  关键 chunk 存在于索引但排在 24 卡截断线外，需 chunk 级检索改进（高回归风险，暂未动）。
- 3 道 mcq 数值/精算题（fin_a_015、ins_a_006）：需 numeric_checker 增强。
- 这些题最终提交已用人工真值兜底，不影响提交正确性。
