from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
except Exception:  # noqa: BLE001
    pass


ROOT_DIR = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Settings:
    root_dir: Path = ROOT_DIR
    raw_dir: Path = ROOT_DIR / "raw"
    questions_dir: Path = ROOT_DIR / "questions"
    processed_dir: Path = ROOT_DIR / "processed_data"
    logs_dir: Path = ROOT_DIR / "logs"
    answer_path: Path = ROOT_DIR / "answer.csv"
    evidence_path: Path = ROOT_DIR / "evidence.json"
    chunks_path: Path = ROOT_DIR / "processed_data" / "chunks.jsonl"
    index_path: Path = ROOT_DIR / "processed_data" / "bm25_index.pkl"
    token_log_path: Path = ROOT_DIR / "logs" / "token_usage.jsonl"
    run_log_path: Path = ROOT_DIR / "logs" / "run.log"
    model_name: str = os.getenv("QWEN_MODEL", "qwen-plus")
    temperature: float = 0.0
    max_rounds: int = int(os.getenv("MAX_ROUNDS", "3"))
    retrieve_top_k: int = int(os.getenv("RETRIEVE_TOP_K", "12"))
    local_sentence_top_n: int = int(os.getenv("LOCAL_SENTENCE_TOP_N", "18"))
    evidence_card_limit: int = int(os.getenv("EVIDENCE_CARD_LIMIT", "8"))
    token_budget_per_question: int = int(os.getenv("TOKEN_BUDGET_PER_QUESTION", "45000"))
    doc_summaries_path: Path = ROOT_DIR / "processed_data" / "doc_summaries.json"
    # 结构化离线产物：财报指标记录 + 全部结构化可检索 chunk
    financial_metric_records_path: Path = ROOT_DIR / "processed_data" / "financial_metric_records.jsonl"
    structured_chunks_path: Path = ROOT_DIR / "processed_data" / "structured_chunks.jsonl"
    # ---- 文档解析层（MinerU-first）配置 ----
    # PDF 默认用 MinerU 解析（预处理阶段允许非 Qwen 工具）；PyMuPDF 仅做轻量 sanity check；
    # pdfplumber 默认不跑，只在开关打开时作为表格兜底。正式答题阶段不使用任何非 Qwen 模型。
    parser_mode: str = os.getenv("PARSER_MODE", "mineru_first")
    enable_mineru: bool = os.getenv("ENABLE_MINERU", "1") not in {"0", "false", "False"}
    enable_native_sanity_check: bool = os.getenv("ENABLE_NATIVE_SANITY_CHECK", "1") not in {"0", "false", "False"}
    enable_pdfplumber_table_fallback: bool = os.getenv("ENABLE_PDFPLUMBER_TABLE_FALLBACK", "0") not in {"0", "false", "False"}
    mineru_output_dir: Path = Path(os.getenv("MINERU_OUTPUT_DIR", str(ROOT_DIR / "processed_data" / "mineru_output")))
    mineru_backend: str = os.getenv("MINERU_BACKEND", "pipeline")
    mineru_lang: str = os.getenv("MINERU_LANG", "ch")
    mineru_timeout: int = int(os.getenv("MINERU_TIMEOUT", "1800"))
    # 批处理：一次 CLI 调用解析一组 PDF（模型只加载一次），避免逐文档重启 MinerU。
    #   batch_size=0 表示一次性全部喂入；batch_timeout 是整组的超时上限（秒）。
    mineru_batch_size: int = int(os.getenv("MINERU_BATCH_SIZE", "8"))
    mineru_batch_timeout: int = int(os.getenv("MINERU_BATCH_TIMEOUT", "7200"))
    # 巨型 PDF（页数 >= large_pdf_pages 或 体积 >= large_pdf_mb）单独成批解析，
    #   避免与其它大文档同批导致显存 OOM / 整批超时。单跑用 mineru_timeout 上限。
    mineru_large_pdf_pages: int = int(os.getenv("MINERU_LARGE_PDF_PAGES", "200"))
    mineru_large_pdf_mb: float = float(os.getenv("MINERU_LARGE_PDF_MB", "10"))
    # 暂存目录：放系统临时目录（不在 processed_data 内），避免构建中断时残留 PDF 拷贝被打包。
    mineru_staging_dir: Path = Path(os.getenv(
        "MINERU_STAGING_DIR", str(Path(tempfile.gettempdir()) / "mineru_staging")))
    # 中间产物清理：解析成功后只保留 *_content_list.json，删除 layout/span/origin/middle/model。
    #   设 MINERU_KEEP_INTERMEDIATE=1 可保留（调试用）。
    mineru_keep_intermediate: bool = os.getenv("MINERU_KEEP_INTERMEDIATE", "0") not in {"0", "false", "False"}
    # GPU 加速：device_mode 留空时 MinerU 自动探测（有 CUDA torch 即用 cuda）。
    #   显式设 cuda 可强制；virtual_vram 给 8GB 卡（RTX 4060 Laptop）一个安全的批处理显存上限。
    mineru_device_mode: str = os.getenv("MINERU_DEVICE_MODE", "")
    mineru_virtual_vram: str = os.getenv("MINERU_VIRTUAL_VRAM_SIZE", "6")
    # 解析层离线产物
    parsed_pages_path: Path = ROOT_DIR / "processed_data" / "parsed_pages.jsonl"
    parsed_blocks_path: Path = ROOT_DIR / "processed_data" / "parsed_blocks.jsonl"
    parser_quality_report_path: Path = ROOT_DIR / "processed_data" / "parser_quality_report.json"
    parse_warnings_path: Path = ROOT_DIR / "processed_data" / "parse_warnings.json"
    query_expansion_enabled: bool = os.getenv("QUERY_EXPANSION_ENABLED", "1") not in {"0", "false", "False"}
    doc_level_retrieval_enabled: bool = os.getenv("DOC_LEVEL_RETRIEVAL_ENABLED", "1") not in {"0", "false", "False"}
    self_verifier_enabled: bool = os.getenv("SELF_VERIFIER_ENABLED", "1") not in {"0", "false", "False"}
    evidence_mode: str = os.getenv("EVIDENCE_MODE", "extractive")
    max_expanded_queries: int = int(os.getenv("MAX_EXPANDED_QUERIES", "8"))
    candidate_doc_top_k: int = int(os.getenv("CANDIDATE_DOC_TOP_K", "8"))
    min_per_option: int = int(os.getenv("MIN_PER_OPTION", "2"))
    verifier_only_uncertain: bool = os.getenv("VERIFIER_ONLY_UNCERTAIN", "1") not in {"0", "false", "False"}
    hybrid_retrieval_enabled: bool = os.getenv("HYBRID_RETRIEVAL_ENABLED", "1") not in {"0", "false", "False"}
    rrf_k: int = int(os.getenv("RRF_K", "60"))
    initial_top_k: int = int(os.getenv("INITIAL_TOP_K", "20"))
    compressed_top_k: int = int(os.getenv("COMPRESSED_TOP_K", "8"))
    max_evidence_cards: int = int(os.getenv("MAX_EVIDENCE_CARDS", "10"))
    # Qwen3 compatible 模式关闭 hidden reasoning：JSON 工具调用不需要长 thinking，默认关以省 completion/reasoning token。
    qwen_enable_thinking: bool = os.getenv("QWEN_ENABLE_THINKING", "0") not in {"0", "false", "False"}
    # Lazy query expansion：有 doc_ids 时先用本地 expansion，省掉每题的 Qwen expansion 调用；
    # 无 doc_ids（B 榜）或首轮检索质量差时再调用 Qwen expansion 兜底。
    lazy_query_expansion_enabled: bool = os.getenv("LAZY_QUERY_EXPANSION_ENABLED", "1") not in {"0", "false", "False"}
    local_first_query_expansion_enabled: bool = os.getenv("LOCAL_FIRST_QUERY_EXPANSION_ENABLED", "1") not in {"0", "false", "False"}
    # Delta reasoning：第 0 轮传完整 compact cards，后续轮只传本轮新增 cards，降低 ircot_reason prompt token。
    delta_reasoning_enabled: bool = os.getenv("DELTA_REASONING_ENABLED", "1") not in {"0", "false", "False"}
    enable_qwen_query_expansion: str = os.getenv("ENABLE_QWEN_QUERY_EXPANSION", "auto")
    multi_doc_top_m: int = int(os.getenv("MULTI_DOC_TOP_M", "2"))
    evidence_validator_enabled: bool = os.getenv("EVIDENCE_VALIDATOR_ENABLED", "1") not in {"0", "false", "False"}
    doc_index_path: Path = ROOT_DIR / "processed_data" / "doc_index.json"
    # 混合检索本地路由（ngram/tfidf/rule）的候选 chunk 上限，避免每查询全量扫描 10 万+ chunk
    hybrid_candidate_top_k: int = int(os.getenv("HYBRID_CANDIDATE_TOP_K", "500"))
    # 候选池大小（兼容 hybrid_candidate_top_k）：本地路由只在该池内打分
    candidate_pool_size: int = int(os.getenv("CANDIDATE_POOL_SIZE", os.getenv("HYBRID_CANDIDATE_TOP_K", "500")))
    # 是否启用候选池限定打分（默认开）。关闭后 ngram/tfidf/rule 回退原始全库扫描，便于 ablation
    candidate_only_scoring: bool = os.getenv("CANDIDATE_ONLY_SCORING", "1") not in {"0", "false", "False"}
    # 是否记录每次 hybrid_search 检索耗时日志（默认关，避免污染日志）
    retrieval_timing_log_enabled: bool = os.getenv("RETRIEVAL_TIMING_LOG_ENABLED", "0") not in {"0", "false", "False"}
    # ---- 审计 patch P1-P8 开关（全部带默认值，可通过环境变量回滚）----
    # P1: evidence_validator 是否拥有“阻止 round1 直接停止、独立触发补检索”的否决权。
    #   已接入主流程（a_leaderboard_agent）：
    #   - 1（默认）：validation.status=="insufficient" 即可【独立】触发一次补检索 + 局部重判；
    #   - 0：status=insufficient 不再单独触发，仅当 retry_queries 非空时才补检索（省 round/token）。
    #   注意：retry_queries 非空【始终】独立触发补检索，不受本开关影响（见 item1/item11）。
    validator_can_block_stop: bool = os.getenv("VALIDATOR_CAN_BLOCK_STOP", "1") not in {"0", "false", "False"}
    # P2: 无 doc_ids（B 榜）时的候选文档池大小。金融长文档建议 top 5~8 个 doc_id，
    #   再把 chunk 检索限定在这些 doc 内（优先级4）。默认 8。
    candidate_doc_top_k_no_docids: int = int(os.getenv("CANDIDATE_DOC_TOP_K_NO_DOCIDS", "8"))
    # P3: B 榜（无原始 doc_ids）连续多轮 validation insufficient 时，允许一次全库 fallback 检索。
    fullcorpus_fallback_enabled: bool = os.getenv("FULLCORPUS_FALLBACK_ENABLED", "0") not in {"0", "false", "False"}
    fullcorpus_fallback_after_rounds: int = int(os.getenv("FULLCORPUS_FALLBACK_AFTER_ROUNDS", "2"))
    # P5: evidence_selector 对高风险域（保险/监管/合同）保留更大的原文窗口，避免例外/否定条款被截断。
    selector_raw_window_domains: str = os.getenv("SELECTOR_RAW_WINDOW_DOMAINS", "insurance,regulatory,financial_contracts")
    selector_text_window_default: int = int(os.getenv("SELECTOR_TEXT_WINDOW_DEFAULT", "600"))
    selector_text_window_large: int = int(os.getenv("SELECTOR_TEXT_WINDOW_LARGE", "1000"))
    # P6: self_verifier 是否允许覆盖多选题的最终答案/判断。
    #   UNUSED（A 榜主流程 a_leaderboard_agent 不接入独立 self_verifier，多选答案由 supported 证据 +
    #   局部重判 + answer_review 决定）。保留仅为 B 榜/历史 ablation 兼容，当前不控制任何行为。
    verifier_override_multi: bool = os.getenv("VERIFIER_OVERRIDE_MULTI", "0") not in {"0", "false", "False"}
    # P7: 多选题诊断日志（supported/unknown/evidence_per_option/fallback_used），只增字段，不改答案。
    multi_diag_log_enabled: bool = os.getenv("MULTI_DIAG_LOG", "1") not in {"0", "false", "False"}

    # ---- 检索精简 patch（优先级 1-5）开关 ----
    # 优先级1: ngram route 模式。
    #   fallback（默认）= 仅当 BM25 在该 query 上无正分命中时才启用 ngram（救 OOV/别名/错别字）；
    #   off = 完全关闭 ngram route（最省 CPU）；always = 旧行为，每个 query 都跑 ngram。
    ngram_route_mode: str = os.getenv("NGRAM_ROUTE_MODE", "fallback")
    # 优先级5: hybrid_search 实际参与多路检索的 query 数量上限（含主 query）。
    #   只取前 N 条高质量 query，避免原题+扩展+选项题全部完整跑 hybrid。
    max_active_queries: int = int(os.getenv("MAX_ACTIVE_QUERIES", "3"))
    # 优先级2: option-wise 召回模式。
    #   adaptive（默认）= 先看主检索覆盖；仅当主检索置信度低 / 某选项未被覆盖时，才对这些选项补充召回。
    #   always = 旧行为，每个选项都单独跑 hybrid_search 并强制 min_per_option。
    #   （由 hybrid_retriever.retrieve_option_wise 接入；A 榜主流程走 evidence_selector.select。）
    option_wise_mode: str = os.getenv("OPTION_WISE_MODE", "adaptive")
    # 多选题无部分分，默认强制每个选项补召回并保留每选项最少证据，减少漏选。
    #   UNUSED（A 榜主流程 evidence_selector.select 已对每个选项做 option_top_k 召回 + 保底纳入，
    #   不经 retrieve_option_wise 的 force_option_wise 路径）。仅供 retrieve_option_wise 历史调用方参考。
    multi_force_option_wise: bool = os.getenv("MULTI_FORCE_OPTION_WISE", "1") not in {"0", "false", "False"}
    # UNUSED（同上，A 榜主流程的每选项保底纳入在 evidence_selector.select 内固定为 2 张）。
    multi_min_per_option: int = int(os.getenv("MULTI_MIN_PER_OPTION", "2"))
    # 多选逐选项二次裁决：只对待重判选项（insufficient/uncovered/low-conf/review-risk）做局部重判，
    #   不整题重判（保护 supported + token）。已接入 a_leaderboard_agent 的局部重判。
    multi_option_judge_enabled: bool = os.getenv("MULTI_OPTION_JUDGE_ENABLED", "1") not in {"0", "false", "False"}
    multi_option_judge_max_calls: int = int(os.getenv("MULTI_OPTION_JUDGE_MAX_CALLS", "4"))
    multi_option_judge_card_limit: int = int(os.getenv("MULTI_OPTION_JUDGE_CARD_LIMIT", "6"))
    # 多选补救：unknown/零证据选项追加定向 query。已接入 a_leaderboard_agent 的 targeted 补检索。
    multi_targeted_queries_enabled: bool = os.getenv("MULTI_TARGETED_QUERIES_ENABLED", "1") not in {"0", "false", "False"}
    multi_extra_query_limit: int = int(os.getenv("MULTI_EXTRA_QUERY_LIMIT", "12"))
    # 多选 low-confidence 阈值：confidence 低于该值的 supported/选项视为 review-risk，纳入第二轮局部重判。
    #   应修5：阈值不写死在代码里，由 config 接入 a_leaderboard_agent 的待重判选项集合。
    multi_low_confidence_threshold: float = float(os.getenv("MULTI_LOW_CONFIDENCE_THRESHOLD", "0.55"))
    # adaptive 模式下，主检索 top1 rerank_score 低于该阈值视为“主检索置信度低”，触发选项补召回。
    option_wise_low_conf_score: float = float(os.getenv("OPTION_WISE_LOW_CONF_SCORE", "0.02"))
    # 优先级3: rule_route / rule_rerank 严格打分。
    #   1（默认）= 只有“目标指标/关键词 + 目标年份 + 数值单位”共同命中才加分，减少“命中任意年份/金额就加分”的噪声；
    #   0 = 旧行为（命中年份/金额/条款即加分）。
    rule_strict_scoring: bool = os.getenv("RULE_STRICT_SCORING", "1") not in {"0", "false", "False"}
    # warmup 总开关：1（默认）= 多 worker 前预热共享语料；0 = 完全跳过 warmup（懒加载，启动更快）。
    #   注意：ngram 语料只有 NGRAM_ROUTE_MODE=always 时才在 warmup 预构建，fallback/off 不再为其买单。
    retriever_warmup_enabled: bool = os.getenv("RETRIEVER_WARMUP_ENABLED", "1") not in {"0", "false", "False"}
    # 证据压缩去重：同一 chunk 的滑动窗口高度重叠会占满证据卡、挤掉其它文档/选项证据。
    #   compress_max_candidates_per_chunk: 每个 chunk 最多贡献几条候选句（默认 2）。
    #   compress_span_overlap_threshold: 与同 chunk 已选 span 的 token 包含度超过该阈值则跳过（默认 0.6）。
    compress_max_candidates_per_chunk: int = int(os.getenv("COMPRESS_MAX_CANDIDATES_PER_CHUNK", "2"))
    compress_span_overlap_threshold: float = float(os.getenv("COMPRESS_SPAN_OVERLAP_THRESHOLD", "0.6"))
    # 财报数值题：把财务指标词+年份组成一条紧凑高命中检索 query 放在最前，让“指标名+数字”表格
    #   chunk 能浮上来（概念表述 query 召不回纯数字行）。默认开；纯本地、不耗 token。
    indicator_query_enabled: bool = os.getenv("INDICATOR_QUERY_ENABLED", "1") not in {"0", "false", "False"}
    # 跨文档对比题：对每个候选文档分别跑指标 query，每文档取 top-N 并入候选池；
    #   indicator_probe_reserve_per_doc 为每文档在最终结果里保留的指标命中数（防被概念段落挤掉）。
    indicator_probe_per_doc_top_k: int = int(os.getenv("INDICATOR_PROBE_PER_DOC_TOP_K", "5"))
    indicator_probe_reserve_per_doc: int = int(os.getenv("INDICATOR_PROBE_RESERVE_PER_DOC", "2"))
    # item16：A 榜给定 doc_ids 时，在【这些 doc 内】适度放宽 rule/structured route 的候选池上限，
    #   避免 BM25 候选池漏掉结构化证据（条款/指标表行）。仅在 doc_ids 限定下放宽，不全库扩大，
    #   不引入新检索架构。0 表示不放宽（回退旧行为）。
    docids_structured_pool_boost: int = int(os.getenv("DOCIDS_STRUCTURED_POOL_BOOST", "1500"))
    # 结构化事实记忆 + 跨文档比较 hint 启用领域（research 暂不开：观点/预测/风险提示易被错误结构化）。
    structured_fact_domains: tuple[str, ...] = tuple(
        d for d in os.getenv(
            "STRUCTURED_FACT_DOMAINS", "financial_reports,regulatory,financial_contracts,insurance"
        ).split(",") if d.strip()
    )
    # 跨文档比较 hint 上限（与 computed_hints 合并后总数另有上限，避免 prompt 膨胀）。
    comparison_hints_max: int = int(os.getenv("COMPARISON_HINTS_MAX", "4"))
    judge_hints_total_max: int = int(os.getenv("JUDGE_HINTS_TOTAL_MAX", "12"))
    # 多文档题每个 doc_id 至少保底纳入的相关证据卡数（保证每个文档都有证据进 prompt）。
    #   默认 1：仅保证“每个文档至少一条”，不额外堆证据，避免 token 膨胀。
    multi_doc_evidence_quota: int = int(os.getenv("MULTI_DOC_EVIDENCE_QUOTA", "1"))
    # 本地 hint debug 输出（computed/fact/comparison），便于错题归因；默认关。
    hints_debug_enabled: bool = os.getenv("HINTS_DEBUG_ENABLED", "0") not in {"0", "false", "False"}

    # ---- 自一致性投票（抑制模型 run-to-run 随机性）----
    # judge_vote_samples: 每次 judge 调用的采样次数。1（默认）= 关闭投票，保持原行为；
    #   >1 时对同一 prompt 采样 N 次，按选项多数表决 status（supported/refuted/insufficient），
    #   抑制 temp=0 大模型仍存在的输出抖动。N 建议取奇数（3/5）。
    judge_vote_samples: int = int(os.getenv("JUDGE_VOTE_SAMPLES", "1"))
    # 投票采样温度：>0 才能产生多样性（投票才有意义）。仅用于 judge_vote_samples>1 的额外采样，
    #   首样本仍用 settings.temperature(0.0) 保证至少有一票是最确定性的输出。
    judge_vote_temperature: float = float(os.getenv("JUDGE_VOTE_TEMPERATURE", "0.5"))
    # 投票时单选/判断题 final_answer 也按多数表决聚合（多选由 supported 选项自然合成）。
    judge_vote_enabled_formats: tuple[str, ...] = tuple(
        f for f in os.getenv("JUDGE_VOTE_ENABLED_FORMATS", "mcq,tf,multi").split(",") if f.strip()
    )


settings = Settings()


DOMAINS = {"insurance", "regulatory", "financial_contracts", "financial_reports", "research"}
