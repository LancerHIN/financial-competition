from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent import document_parser as dp  # noqa: E402
from agent.evidence_selector import score_chunk  # noqa: E402
from agent.structured_records import (  # noqa: E402
    build_structured_records,
    compress_insurance_clause_block,
    records_to_chunks,
)


def _insurance_page(text, page=1, prev="", nxt=""):
    return {
        "doc_id": "ins_test",
        "domain": "insurance",
        "source_path": "ins_test.pdf",
        "page": page,
        "text": text,
        "parser_source": "pymupdf",
        "prev_page_tail": prev,
        "next_page_head": nxt,
    }


# ---------------------------------------------------------------------------
# 1. 责任险条款抽取：liability / exclusion / limit 覆盖
# ---------------------------------------------------------------------------

def test_insurance_clause_liability_exclusion_limit():
    text = (
        "第三条 保险责任。\n"
        "保险期间内，被保险人依法应承担的经济赔偿责任，保险人负责赔偿。\n"
        "第四条 责任免除。\n"
        "下列原因造成的损失，保险人不负责赔偿：投保人的故意行为。\n"
        "第五条 赔偿限额与免赔额。\n"
        "每次事故赔偿限额为100万元，免赔额为每次事故损失的10%。\n"
    )
    recs = build_structured_records([_insurance_page(text)])
    clauses = recs["insurance_clause_records"]
    assert clauses, "应抽出 insurance_clause 记录"
    types = {c["clause_type"] for c in clauses}
    assert "liability" in types
    assert "exclusion" in types
    assert "limit" in types


# ---------------------------------------------------------------------------
# 2. 免责但书保留：不承担 + 但...除外
# ---------------------------------------------------------------------------

def test_insurance_clause_keeps_exception_proviso():
    text = (
        "第八条 责任免除。\n"
        "被保险人自杀的，保险人不承担给付身故保险金的责任，"
        "但被保险人自杀时为无民事行为能力人的除外。\n"
    )
    recs = build_structured_records([_insurance_page(text)])
    clauses = recs["insurance_clause_records"]
    assert clauses
    raw = clauses[0]["raw"]
    assert "不承担" in raw
    assert "但" in raw and "除外" in raw


def test_compress_keeps_proviso_when_long():
    head = "第九条 保险责任。" + "保险人按约定承担保险责任。" * 200
    tail = "被保险人故意行为的，保险人不承担保险责任，但另有约定的除外。"
    block = head + tail
    assert len(block) > 2500
    compressed = compress_insurance_clause_block(block)
    assert "不承担" in compressed
    assert "但" in compressed and "除外" in compressed


def test_toc_block_skipped_but_real_clause_kept():
    # 目录页：编号小节标题罗列，命中关键词但无条款正文 -> 不应生成 insurance_clause
    toc = (
        "条款目录\n"
        "1.保险责任\n1.1 保险期间\n2.责任免除\n"
        "6.如何退保\n6.1 犹豫期\n7.现金价值\n"
    )
    # 真正条款：含成句正文与限制条件 -> 必须保留
    real = (
        "第三条 责任免除。\n"
        "因下列情形之一导致被保险人身故的，保险人不承担给付保险金的责任，"
        "但合同另有约定的除外。\n"
    )
    recs_toc = build_structured_records([_insurance_page(toc)])
    assert recs_toc["insurance_clause_records"] == [], "目录块不应生成条款记录"
    recs_real = build_structured_records([_insurance_page(real)])
    clauses = recs_real["insurance_clause_records"]
    assert clauses, "真正条款必须保留"
    assert any("不承担" in c["raw"] and "除外" in c["raw"] for c in clauses)


# ---------------------------------------------------------------------------
# 3. 低置信财报指标过滤
# ---------------------------------------------------------------------------

def _fr_page(records):
    return {
        "doc_id": "fr_test",
        "domain": "financial_reports",
        "source_path": "fr_test.pdf",
        "page": 1,
        "text": "财务摘要",
        "parser_source": "pdfplumber",
        "structured_records": records,
        "prev_page_tail": "",
        "next_page_head": "",
    }


def test_low_confidence_financial_metric_filtered():
    high = {"record_type": "financial_metric", "metric": "营业收入", "value_raw": "100", "value": 100.0, "confidence": "high", "year": "2024", "unit": "亿元"}
    low = {"record_type": "financial_metric", "metric": "净利润", "value_raw": "10", "value": 10.0, "confidence": "low"}
    empty_raw = {"record_type": "financial_metric", "metric": "总资产", "value_raw": "", "value": None, "confidence": "high"}
    recs = build_structured_records([_fr_page([high, low, empty_raw])])
    metrics = recs["financial_metric_records"]
    assert len(metrics) == 1
    assert metrics[0]["metric"] == "营业收入"
    assert len(recs["filtered_low_confidence_metrics"]) == 2
    # 低置信不进入 structured chunk
    chunks = records_to_chunks(recs)
    fm_chunks = [c for c in chunks if c.get("record_type") == "financial_metric"]
    assert len(fm_chunks) == 1


def test_column_merge_metric_filtered():
    # OCR 列粘连（"150,181 945"）会算成天文数字，必须在入索引前剔除；合法的小数点空格/千分位放过。
    from agent.structured_records import _is_column_merge_value
    assert _is_column_merge_value("150,181 945")          # 两列并入一格
    assert _is_column_merge_value("427,609,892 8.1 384,322,141")
    assert _is_column_merge_value("174 380")
    assert not _is_column_merge_value("11. 12")           # 小数点后空格，合法
    assert not _is_column_merge_value("1,234. 56")
    assert not _is_column_merge_value("127, 665")         # 千分位空格，合法
    assert not _is_column_merge_value("150181")
    assert not _is_column_merge_value("")
    # 端到端：列粘连记录不应进入结构化指标
    good = {"record_type": "financial_metric", "metric": "净利润", "value_raw": "150,181", "value": 150181.0, "confidence": "high", "year": "2025", "unit": "百万元"}
    merged = {"record_type": "financial_metric", "metric": "净利润", "value_raw": "150,181 945", "value": 150181945.0, "confidence": "high", "year": "2025", "unit": "百万元"}
    recs = build_structured_records([_fr_page([good, merged])])
    metrics = recs["financial_metric_records"]
    assert len(metrics) == 1 and metrics[0]["value_raw"] == "150,181"


# ---------------------------------------------------------------------------
# 4. zero_text_image_pdf warning（不抛异常）
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# 4. MinerU-first 解析层
# ---------------------------------------------------------------------------

def _fake_content_list():
    return [
        {"type": "text", "text": "第一条 保险责任", "text_level": 2, "page_idx": 0},
        {"type": "text", "text": "保险人按照约定承担给付保险金的责任，但另有约定的除外。", "page_idx": 0},
        {"type": "table", "table_body": "<table><tr><td>营业收入</td><td>2024年</td></tr><tr><td>金额</td><td>1,234</td></tr></table>",
         "table_caption": [], "table_footnote": [], "page_idx": 0},
        {"type": "page_number", "text": "1", "page_idx": 0},
    ]


def test_parse_pdf_uses_mineru_when_available(monkeypatch, tmp_path):
    fake = tmp_path / "doc.pdf"
    fake.write_bytes(b"%PDF-1.4 fake")
    import agent.mineru_parser as mp
    monkeypatch.setattr(mp, "run_mineru", lambda path: (_fake_content_list(), None))
    # sanity check 用空 native，避免依赖真实 PyMuPDF 解析
    monkeypatch.setattr(dp, "_extract_fitz", lambda path: {1: "第一条 保险责任 保险人按照约定承担给付保险金的责任，但另有约定的除外。"})
    dp.reset_parse_warnings()
    pages = dp.parse_pdf(fake, "insurance")
    assert pages, "MinerU 应产出页面"
    assert all(p["parser_source"] == "mineru" for p in pages)
    assert any("保险责任" in p["text"] for p in pages)


def test_parse_pdf_falls_back_when_mineru_unavailable(monkeypatch, tmp_path):
    fake = tmp_path / "doc.pdf"
    fake.write_bytes(b"%PDF-1.4 fake")
    import agent.mineru_parser as mp
    monkeypatch.setattr(mp, "run_mineru", lambda path: (None, "mineru_unavailable"))
    monkeypatch.setattr(dp, "_extract_fitz", lambda path: {1: "第一条 监管要求。应当依法披露。"})
    dp.reset_parse_warnings()
    pages = dp.parse_pdf(fake, "regulatory")
    assert pages and all(p["parser_source"] == "native_fallback" for p in pages)
    assert any(w["reason"] == "mineru_unavailable" for w in dp.get_parse_warnings())


def test_scanned_pdf_not_routed_to_standalone_ocr(monkeypatch, tmp_path):
    # 扫描件：MinerU 失败 + native 抽不到文本 -> 记 warning，不触发任何单独 OCR
    fake = tmp_path / "scan.pdf"
    fake.write_bytes(b"%PDF-1.4 fake")
    import agent.mineru_parser as mp
    monkeypatch.setattr(mp, "run_mineru", lambda path: (None, "mineru_parse_failed"))
    monkeypatch.setattr(dp, "_extract_fitz", lambda path: {1: ""})
    dp.reset_parse_warnings()
    pages = dp.parse_pdf(fake, "regulatory")
    assert pages == []
    reasons = {w["reason"] for w in dp.get_parse_warnings()}
    assert "mineru_parse_failed" in reasons
    assert "native_fallback_empty" in reasons


def test_no_standalone_ocr_engine_calls_in_source():
    # 源码中不应再出现单独 OCR 引擎的直接调用
    src = (ROOT / "agent" / "document_parser.py").read_text(encoding="utf-8")
    for token in ("pytesseract", "easyocr", "paddleocr", "_ocr_zero_text_pdf", "_select_ocr_engine"):
        assert token not in src, f"document_parser.py 不应再包含 {token}"


def test_enable_ocr_env_has_no_effect(monkeypatch, tmp_path):
    # ENABLE_OCR 不再影响解析流程
    assert not hasattr(dp, "_ocr_settings")
    assert not hasattr(dp, "_ocr_zero_text_pdf")


def test_native_sanity_check_flags_short_text(monkeypatch, tmp_path):
    fake = tmp_path / "doc.pdf"
    fake.write_bytes(b"%PDF-1.4 fake")
    long_native = "保险人不承担给付保险金的责任，但另有约定的除外。" * 30
    monkeypatch.setattr(dp, "_extract_fitz", lambda path: {1: long_native})
    mineru_pages = [{"page": 1, "text": "保险人承担责任。"}]
    report = dp.native_sanity_check(fake, mineru_pages)
    assert "mineru_text_much_shorter_than_native" in report["warnings"]


def test_native_sanity_check_flags_missing_protect_terms(monkeypatch, tmp_path):
    fake = tmp_path / "doc.pdf"
    fake.write_bytes(b"%PDF-1.4 fake")
    native = "保险人不承担给付保险金的责任，但另有约定的除外，赔偿不超过保险金额。"
    monkeypatch.setattr(dp, "_extract_fitz", lambda path: {1: native})
    # MinerU 文本丢了 "不承担/但/除外/不超过"
    mineru_pages = [{"page": 1, "text": "保险人承担给付保险金的责任，赔偿以保险金额为限。"}]
    report = dp.native_sanity_check(fake, mineru_pages)
    assert "mineru_missing_protect_terms" in report["warnings"]
    assert "不承担" in report["missing_protect_terms"]


def test_html_dom_produces_blocks(tmp_path):
    html = tmp_path / "doc.html"
    html.write_text(
        "<html><body><h1>标题</h1><p>第一段内容。</p>"
        "<ul><li>列表项一</li></ul>"
        "<table><tr><td>指标</td><td>2024</td></tr><tr><td>营收</td><td>100</td></tr></table>"
        "<script>ignore()</script></body></html>",
        encoding="utf-8",
    )
    pages = dp.parse_html(html, "research")
    assert len(pages) == 1
    page = pages[0]
    assert page["parser_source"] == "html_dom"
    btypes = {b["block_type"] for b in page["blocks"]}
    assert "title" in btypes and "paragraph" in btypes and "list_item" in btypes and "table" in btypes
    assert "ignore" not in page["text"]


# ---------------------------------------------------------------------------
# 6. PUA bullet 不被当作严重乱码
# ---------------------------------------------------------------------------

def test_pua_bullet_normalized():
    text = "\uf076 被保险人就是受保险合同保障的人"
    normalized = dp.normalize_pua_symbols(text)
    assert "\uf076" not in normalized
    assert "被保险人" in normalized
    # PUA bullet 不应被当作严重乱码：与普通项目符号 "•" 版本质量分基本一致
    score_pua = dp.quality_score(text)
    score_bullet = dp.quality_score("\u2022 被保险人就是受保险合同保障的人")
    assert abs(score_pua - score_bullet) < 0.01


# ---------------------------------------------------------------------------
# 财报低置信指标过滤
# ---------------------------------------------------------------------------

def test_low_confidence_metric_not_in_structured_chunk():
    page = {
        "doc_id": "fr", "domain": "financial_reports", "source_path": "fr.pdf", "page": 1,
        "text": "营业收入", "parser_source": "mineru",
        "structured_records": [
            {"record_type": "financial_metric", "doc_id": "fr", "domain": "financial_reports",
             "page": 1, "metric": "营业收入", "year": "", "period": "", "value_raw": "100",
             "value": 100.0, "unit": "", "normalized_value_yuan": None, "confidence": "low"},
        ],
        "prev_page_tail": "", "next_page_head": "",
    }
    recs = build_structured_records([page])
    assert recs["financial_metric_records"] == []
    assert len(recs["filtered_low_confidence_metrics"]) == 1
