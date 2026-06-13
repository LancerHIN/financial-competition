from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from tqdm import tqdm

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.bm25_index import BM25Index  # noqa: E402
from agent.chunker import chunk_pages  # noqa: E402
from agent.config import settings  # noqa: E402
from agent.domain_rules import extract_entities  # noqa: E402
from agent.document_parser import (  # noqa: E402
    discover_documents,
    get_parse_warnings,
    get_quality_reports,
    parse_file,
    reset_parse_warnings,
)
from agent.mineru_parser import prepare_mineru_batch  # noqa: E402
from agent.structured_records import build_structured_records, records_to_chunks  # noqa: E402


def main() -> None:
    settings.processed_dir.mkdir(parents=True, exist_ok=True)
    reset_parse_warnings()
    docs = discover_documents(settings.raw_dir)

    # ---- MinerU 批量预解析：一次/分批 CLI 调用解析所有 PDF，模型只加载一次 ----
    pdf_paths = [path for path, _ in docs if path.suffix.lower() == ".pdf"]
    if pdf_paths:
        print(f"[mineru] pre-parsing {len(pdf_paths)} PDFs in batches "
              f"(batch_size={settings.mineru_batch_size or 'all'}) ...")
        batch_failures = prepare_mineru_batch(pdf_paths, log=lambda m: print(m))
        if batch_failures:
            print(f"[mineru] batch pre-parse failures: {len(batch_failures)} "
                  "(will retry per-file / fall back to native)")

    chunks = []
    records_by_type: dict[str, list[dict]] = {}
    metrics_before_filter = 0

    # 流式写出 parsed_pages / parsed_blocks，避免 11k 页全攒在内存。
    pages_handle = settings.parsed_pages_path.open("w", encoding="utf-8")
    blocks_handle = settings.parsed_blocks_path.open("w", encoding="utf-8")
    parsed_pages_count = 0
    parsed_blocks_count = 0
    try:
        for path, domain in tqdm(docs, desc="parse documents"):
            pages = parse_file(path, domain)
            chunks.extend(chunk_pages(pages))
            for page in pages:
                blocks = page.get("blocks") or []
                for block in blocks:
                    blocks_handle.write(json.dumps(block, ensure_ascii=False) + "\n")
                    parsed_blocks_count += 1
                # parsed_pages 去掉嵌套 blocks，避免重复膨胀
                page_copy = {k: v for k, v in page.items() if k != "blocks"}
                pages_handle.write(json.dumps(page_copy, ensure_ascii=False) + "\n")
                parsed_pages_count += 1
            # 过滤前财报指标计数（domain==financial_reports 的 financial_metric 原始记录数）
            if domain == "financial_reports":
                for page in pages:
                    metrics_before_filter += sum(
                        1 for r in (page.get("structured_records") or [])
                        if r.get("record_type") == "financial_metric"
                    )
            for rec_type, records in build_structured_records(pages).items():
                records_by_type.setdefault(rec_type, []).extend(records)
    finally:
        pages_handle.close()
        blocks_handle.close()

    # 结构化记录 -> 可检索 chunk，并入主语料
    structured_chunks = records_to_chunks(records_by_type)
    all_chunks = chunks + structured_chunks

    with settings.chunks_path.open("w", encoding="utf-8") as handle:
        for chunk in all_chunks:
            handle.write(json.dumps(chunk, ensure_ascii=False) + "\n")

    # 财报指标记录 JSONL
    with settings.financial_metric_records_path.open("w", encoding="utf-8") as handle:
        for rec in records_by_type.get("financial_metric_records", []):
            handle.write(json.dumps(rec, ensure_ascii=False) + "\n")

    # 全部结构化 chunk JSONL
    with settings.structured_chunks_path.open("w", encoding="utf-8") as handle:
        for chunk in structured_chunks:
            handle.write(json.dumps(chunk, ensure_ascii=False) + "\n")

    # 解析告警汇总写入 parse_warnings.json（不中断构建）
    parse_warnings = get_parse_warnings()
    settings.parse_warnings_path.write_text(
        json.dumps(parse_warnings, ensure_ascii=False, indent=2), encoding="utf-8")
    # parser_quality_report.json：每文档解析质量对照
    quality_reports = get_quality_reports()
    settings.parser_quality_report_path.write_text(
        json.dumps(quality_reports, ensure_ascii=False, indent=2), encoding="utf-8")

    index = BM25Index(all_chunks)
    index.save(settings.index_path)
    doc_records = build_doc_records(chunks)
    settings.doc_index_path.write_text(json.dumps(doc_records, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"built {len(all_chunks)} chunks ({len(structured_chunks)} structured) from {len(docs)} files")
    print(f"parsed_pages={parsed_pages_count} parsed_blocks={parsed_blocks_count}")
    print("structured records: " + ", ".join(f"{k}={len(v)}" for k, v in records_by_type.items()))
    _print_quality_summary(chunks, structured_chunks, records_by_type, parse_warnings, quality_reports, metrics_before_filter)
    print(f"chunks: {settings.chunks_path}")
    print(f"parsed_pages: {settings.parsed_pages_path}")
    print(f"parsed_blocks: {settings.parsed_blocks_path}")
    print(f"financial_metric_records: {settings.financial_metric_records_path}")
    print(f"structured_chunks: {settings.structured_chunks_path}")
    print(f"parse_warnings: {settings.parse_warnings_path}")
    print(f"parser_quality_report: {settings.parser_quality_report_path}")
    print(f"index: {settings.index_path}")
    print(f"doc_index: {settings.doc_index_path}")


def _print_quality_summary(
    chunks: list[dict],
    structured_chunks: list[dict],
    records_by_type: dict[str, list[dict]],
    parse_warnings: list[dict],
    quality_reports: list[dict],
    metrics_before_filter: int,
) -> None:
    from collections import Counter

    print("---- quality summary ----")
    domain_chunks = Counter(c.get("domain", "") for c in chunks)
    print("normal chunks by domain: " + ", ".join(f"{k}={v}" for k, v in sorted(domain_chunks.items())))

    struct_by_type = Counter(c.get("record_type", "") for c in structured_chunks)
    struct_by_domain = Counter(c.get("domain", "") for c in structured_chunks)
    print("structured chunks by record_type: " + ", ".join(f"{k}={v}" for k, v in sorted(struct_by_type.items())))
    print("structured chunks by domain: " + ", ".join(f"{k}={v}" for k, v in sorted(struct_by_domain.items())))
    # structured chunks 为 0 的 domain 告警
    all_domains = {"insurance", "regulatory", "financial_contracts", "financial_reports", "research"}
    for dom in sorted(all_domains - set(struct_by_domain)):
        print(f"[WARN] domain '{dom}' has 0 structured chunks")

    # ---- MinerU 解析统计 ----
    by_source = Counter(r.get("parser_source", "") for r in quality_reports)
    mineru_ok = by_source.get("mineru", 0)
    native_fb = by_source.get("native_fallback", 0)
    print(f"parser_source: mineru={mineru_ok} native_fallback={native_fb} "
          f"(others={ {k: v for k, v in by_source.items() if k not in {'mineru', 'native_fallback'}} })")
    # MinerU 失败 / 不可用 / 超时
    for reason in ("mineru_unavailable", "mineru_parse_failed", "mineru_timeout", "mineru_empty_output", "mineru_convert_failed"):
        hit = [w for w in parse_warnings if w.get("reason") == reason]
        if hit:
            print(f"[WARN] {reason} ({len(hit)}): " + ", ".join(w.get("doc_id", "") for w in hit))
    # native fallback 告警（含财报弱表格、空文本）
    for reason in ("native_fallback_financial_report_weak_tables", "native_fallback_empty"):
        hit = [w for w in parse_warnings if w.get("reason") == reason]
        if hit:
            print(f"[WARN] {reason} ({len(hit)}): " + ", ".join(w.get("doc_id", "") for w in hit))
    # native sanity warnings
    for reason in ("mineru_text_much_shorter_than_native", "mineru_missing_protect_terms", "mineru_missing_clause_numbers"):
        hit = [w for w in parse_warnings if w.get("reason") == reason]
        if hit:
            print(f"sanity {reason} ({len(hit)}): " + ", ".join(w.get("doc_id", "") for w in hit))
    sanity_total = sum(
        1 for w in parse_warnings
        if w.get("reason") in {"mineru_text_much_shorter_than_native", "mineru_missing_protect_terms", "mineru_missing_clause_numbers"}
    )
    print(f"native sanity warnings total: {sanity_total}")

    # ---- 每 domain parsed blocks 数 ----
    block_by_domain = Counter()
    for r in quality_reports:
        block_by_domain[r.get("domain", "")] += r.get("table_block_count", 0)
    # parsed blocks 真实计数从 quality 不可得，留给主流程已打印 parsed_blocks 总数

    # ---- insurance 结构化统计 ----
    ins_clause = records_by_type.get("insurance_clause_records", [])
    ins_formula = records_by_type.get("insurance_formula_records", [])
    print(f"insurance_clause_records={len(ins_clause)} insurance_formula_records={len(ins_formula)}")
    ins_by_doc = Counter()
    for rec in ins_clause + ins_formula:
        ins_by_doc[rec.get("doc_id", "")] += 1
    if ins_by_doc:
        print("insurance structured records by doc_id: " + ", ".join(f"{k}={v}" for k, v in sorted(ins_by_doc.items())))
    # structured records 为 0 的 insurance 文档
    ins_doc_ids = {c.get("doc_id") for c in chunks if c.get("domain") == "insurance"}
    for doc_id in sorted(ins_doc_ids - set(ins_by_doc)):
        print(f"[WARN] insurance doc '{doc_id}' has 0 structured records")

    # ---- 财报指标过滤统计 ----
    metrics = records_by_type.get("financial_metric_records", [])
    filtered_all = records_by_type.get("filtered_low_confidence_metrics", [])
    # before/after 口径均为 financial_reports，filtered 同样只取 financial_reports，保持一致
    filtered_fr = [r for r in filtered_all if r.get("domain") == "financial_reports"]
    print(f"financial_metric_records: before_filter={metrics_before_filter} after_filter={len(metrics)} "
          f"filtered_low_confidence={len(filtered_fr)} (all_domains_filtered={len(filtered_all)})")
    if metrics:
        conf = Counter(r.get("confidence", "") for r in metrics)
        empty_year = sum(1 for r in metrics if not r.get("year"))
        empty_unit = sum(1 for r in metrics if not r.get("unit"))
        empty_norm = sum(1 for r in metrics if r.get("normalized_value_yuan") is None)
        print(f"  conf={dict(conf)} empty year={empty_year} unit={empty_unit} normalized_value_yuan={empty_norm}")
    # 每个 financial_reports doc_id 的 high/medium 指标数
    fr_by_doc = Counter(r.get("doc_id", "") for r in metrics)
    if fr_by_doc:
        print("financial_reports high/medium metrics by doc_id: " + ", ".join(f"{k}={v}" for k, v in sorted(fr_by_doc.items())))
    # high/medium 指标过少的财报文档告警
    fr_doc_ids = {c.get("doc_id") for c in chunks if c.get("domain") == "financial_reports"}
    for doc_id in sorted(fr_doc_ids):
        if fr_by_doc.get(doc_id, 0) < 3:
            print(f"[WARN] financial_reports doc '{doc_id}' has only {fr_by_doc.get(doc_id, 0)} high/medium financial_metric records")

    other = records_by_type.get("other_metric_records", [])
    if other:
        print(f"other_metric_records (非财报指标，不写入 financial_metric_records.jsonl): {len(other)}")
    print("-------------------------")


def build_doc_records(chunks: list[dict]) -> dict[str, dict]:
    records: dict[str, dict] = {}
    for chunk in chunks:
        doc_id = chunk.get("doc_id", "")
        if not doc_id:
            continue
        record = records.setdefault(doc_id, {"doc_id": doc_id, "domain": chunk.get("domain"), "title": doc_id, "source_path": chunk.get("source_path", ""), "keywords": [], "entities": [], "summary": ""})
        section = str(chunk.get("section", "")).strip()
        if section and len(record["keywords"]) < 40:
            record["keywords"].append(section[:60])
        text = str(chunk.get("text", ""))
        if len(record["summary"]) < 1800:
            record["summary"] += "\n" + text[:500]
        for entity in extract_entities(text[:1000]):
            if entity not in record["entities"]:
                record["entities"].append(entity)
    for record in records.values():
        record["keywords"] = list(dict.fromkeys([*record.get("keywords", []), *top_terms(record.get("summary", ""))]))[:60]
        record["summary"] = record.get("summary", "").strip()[:2000]
    return records


def top_terms(text: str) -> list[str]:
    terms = re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z0-9_]+", text)
    stop = {"公司", "年度", "报告", "本期", "项目", "单位", "情况", "如下"}
    counts: dict[str, int] = {}
    for term in terms:
        if term not in stop:
            counts[term] = counts.get(term, 0) + 1
    return [term for term, _ in sorted(counts.items(), key=lambda item: item[1], reverse=True)[:30]]


if __name__ == "__main__":
    main()
