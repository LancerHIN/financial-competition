# 金融长文本 IRCoT + RECOMP Agent

本项目实现基于 Qwen API（阿里云百炼 dashscope）的金融长文本问答方案，不训练、不微调、不使用其他模型。

## 环境

```bash
conda create -n finance_rag python=3.10 -y
conda activate finance_rag
pip install -r requirements.txt
```

设置百炼 API Key：

```bash
set DASHSCOPE_API_KEY=your_key
```

默认模型为 `qwen-plus`，可切换：

```bash
set QWEN_MODEL=qwen3.6-plus
```

可选：本机已在 `finance_rag` 环境部署 MinerU `3.2.3`，pipeline 模型已下载到本地 ModelScope 缓存，并写入 `C:\Users\hp\mineru.json`。Windows 普通权限下建议固定使用本地模型源，避免 HuggingFace symlink 权限问题：

```powershell
$env:MINERU_MODEL_SOURCE="local"
mineru -p "raw\financial_contracts\text09.pdf" -o "processed_data\mineru_output" -b pipeline -s 0 -e 0
```

## 运行

构建解析结果和 BM25 索引：

```bash
python script/build_index.py
```

运行 A 榜 100 题：

```bash
python script/run_group_a.py
```

少量 smoke test：

```bash
python script/run_group_a.py --limit 5
python script/run_group_a.py --domain regulatory --limit 1
```

## 输出

- `answer.csv`：包含 `summary` 行和每题 `qid,answer,prompt_tokens,completion_tokens,total_tokens`。
- `evidence.json`：每题证据引用、页码、选项关联。
- `logs/token_usage.jsonl`：每次 Qwen 调用的 token 统计。
- `logs/run.log`：运行日志。

## 方法

- 文档解析采用 **MinerU-first**：PDF（含扫描件、图片页、复杂版面、表格）默认交给 MinerU（预处理阶段允许的非 Qwen 工具，仅用于版面分析/表格恢复/阅读顺序还原/OCR），输出统一为 `parsed_pages` / `parsed_blocks`，`parser_source="mineru"`。PyMuPDF 仅作轻量 sanity check（对照文本长度、条款号、保护词漏失），不覆盖 MinerU 主解析；MinerU 不可用或失败时回退 native 简单解析（`parser_source="native_fallback"`，仅 PyMuPDF 抽文本，不跑 OCR、不跑 pdfplumber）。HTML 走 DOM 解析（`html_dom`），TXT/MD 走文本解析。pdfplumber 默认关闭，仅 `ENABLE_PDFPLUMBER_TABLE_FALLBACK=1` 时作为可选表格兜底。已彻底移除单独 OCR fallback（无 pytesseract/easyocr/paddleocr 调用）。`table_extractor.py` 消费 MinerU 表格生成 `指标=.. 年份=.. 数值=.. 单位=..` 结构化记录与 `financial_metric`。
- 切分按条款、标题、页码和自然段进行，保留 `doc_id/domain/source_path/page/section/chunk_id/text/table_text`。
- 检索默认使用 `hybrid_retriever.py` 多路混合召回（BM25、BM25F 字段加权、字符 2/3-gram、TF-IDF、结构化规则召回），每路 Top20 后用 RRF（k=60）融合，再做规则重排压到 Top8-10；支持 `domain` 和 A 榜 `doc_ids` 限制。
- `doc_retriever.py` 为 B 榜提供文档级召回（doc_id/title/entity/keywords + BM25），A 榜直接用题目 doc_ids。
- `domain_rules.py` 按五个领域配置关键词、必要字段和保护词；`multi_doc_guard.py` 保证跨文档/跨年份/跨主体覆盖，不足时生成补检索 query。
- RECOMP-style 压缩先做本地句子/条款粗筛，再调用 Qwen 抽取结构化 evidence cards，保护否定词与例外条件。
- `evidence_validator.py` 本地校验 quote 是否来自原文、关键年份/指标/条款/触发条件是否齐备，不足则标记 insufficient 并触发补检索。
- `qwen_reranker.py` 仅在高风险题（多选、跨文档、Top8 分数接近、证据不足、出现 unknown）启用 Qwen 生成式重排（chat 后端，让 LLM 输出 JSON 排序）。**赛题禁止 embedding / 表示型 rerank 模型，因此不使用任何 rerank/embedding API，语义重排只走生成式 LLM，其余排序由本地词法/统计 RRF 负责。**
- IRCoT 最多 3 轮，交织查询扩展、混合检索、覆盖校验、压缩、证据校验、选项判断和缺口追问。
- 答案经 `answer_normalizer.py` 规范化，多选排序去重，单选/判断只保留首个大写字母。

## Token 成本控制

`agent/config.py` 新增可调项（均可用环境变量覆盖）：

- `HYBRID_RETRIEVAL_ENABLED=1` 是否启用混合检索（设 0 回退原 BM25 检索器）
- `RRF_K=60`、`INITIAL_TOP_K=20`、`RERANK_TOP_K=15`、`COMPRESSED_TOP_K=8`、`MAX_EVIDENCE_CARDS=10`
- `ENABLE_QWEN_RERANK=auto`（auto/always/never）：auto 仅高风险题启用
- `QWEN_RERANK_BACKEND=chat`（仅 `chat` 或 `off`；不提供 embedding/表示型 rerank 后端）
- `ENABLE_QWEN_QUERY_EXPANSION=auto`
- `MULTI_DOC_TOP_M=2`：跨文档比较每个 doc 至少保留的候选 chunk 数
- `EVIDENCE_VALIDATOR_ENABLED=1`

## 合规说明

- 全流程不使用任何 embedding / 表示型排序（rerank）模型；检索为 BM25/BM25F/字符 ngram/TF-IDF/规则召回 + RRF 的纯词法统计方案，语义重排仅用 Qwen 生成式 LLM。
- 文档离线解析（`build_index.py`）不调用任何 LLM，仅做 PDF/HTML/TXT 解析、表格结构化、切分、BM25 索引与规则摘要，离线阶段零 token 消耗；解析脚本随报告提交。
- `tests/test_generalization.py` 对全部源码做静态守卫：禁止出现 `TextReRank/TextEmbedding/bge/sentence-transformers` 等接口，禁止硬编码数据集公司/doc_id，默认 rerank 后端必须是 `chat`。

## 输出日志新增字段

`evidence.json` 每题新增：`retrieval_routes`、`rrf_scores`、`coverage`、`validation_status`、`validation_issues`、`rerank_used`、`query_expansion_used`、`candidate_doc_ids`、`missing_info`。
