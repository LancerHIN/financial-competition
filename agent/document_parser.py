from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

from bs4 import BeautifulSoup

from .config import settings
from .domain_rules import GENERAL_RULE_KEYWORDS


FINANCIAL_KEYWORDS = set(GENERAL_RULE_KEYWORDS) | {
    "营业收入", "净利润", "现金流量", "资产", "负债", "股东", "条", "保险", "债券", "利率",
}
# 乱码信号：分两档处理。
#   严重乱码（强惩罚）：CID 缺字符、替换字符 \ufffd、连续方块/问号。
#   PUA 私用区：多为项目符号/图标，不再一刀切当严重乱码——先经 normalize_pua_symbols
#   归一为普通符号，剩余未知 PUA 仅极轻微扣分（见 quality_score）。
GARBLE_RE = re.compile(r"\(cid:\d+\)|cid:\d+|\ufffd|[\u25a0\u25a1\u2588]{2,}|\?{4,}")
# 残留 PUA（normalize 之后仍未识别的私用区字符），仅极轻微扣分
PUA_RE = re.compile(r"[\ue000-\uf8ff]")
CJK_RE = re.compile(r"[\u4e00-\u9fff]")
DIGIT_RE = re.compile(r"\d")
TABLE_HINT_RE = re.compile(r"\t| \| |单位[:：]|金额|项目|合计|本期|上年同期")

# ----------------------------------------------------------------------------
# PUA 项目符号 / 图标归一：阅读指引中的手形、箭头、圆点 bullet，不是正文缺字。
# 归一为普通可读符号，避免 raw/insurance/1.pdf 这类文档被误判为严重乱码。
# ----------------------------------------------------------------------------
PUA_BULLET_MAP = {
    "\uf043": "▶",
    "\uf076": "•",
    "\uf075": "•",
    "\uf077": "•",
    "\uf078": "•",
    "\uf079": "•",
    "\uf07a": "•",
    "\uf07b": "•",
    "\uf07c": "•",
    "\uf07d": "•",
}

def normalize_pua_symbols(text: str) -> str:
    """把常见 PUA 私用区项目符号/图标替换成普通可读符号。

    只替换已知 bullet/icon 码点，不触碰正文；未知 PUA 留给 quality_score 轻微扣分。
    """
    if not text:
        return text
    for src, dst in PUA_BULLET_MAP.items():
        if src in text:
            text = text.replace(src, dst)
    return text


# ----------------------------------------------------------------------------
# 解析层配置：MinerU-first（预处理阶段允许的非 Qwen 工具）。
#   PDF 默认走 MinerU；PyMuPDF 仅做轻量 sanity check；pdfplumber 默认不跑（可选表格兜底）。
#   不再维护任何单独 OCR fallback —— 扫描件统一交给 MinerU。
# ----------------------------------------------------------------------------

# 保护词：解析/清洗/分块不得删除；同时用于 native sanity check 漏词检测。
PROTECT_TERMS_FOR_SANITY = [
    "但", "但是", "除外", "不承担", "不负责赔偿", "不适用", "不在此限",
    "不超过", "不低于", "不少于", "以上", "以下", "另有约定", "另有规定",
    "应当", "不得", "须经", "特别决议", "普通决议",
]

CLAUSE_NO_SCAN_RE = re.compile(r"第[一二三四五六七八九十百零〇0-9]+条")


# ----------------------------------------------------------------------------
# 解析告警累积器：parse_pdf 把 mineru_* / sanity check 等告警写入模块级列表，
# build_index.py 构建结束时统一汇总打印并写入 parse_warnings.json。
# ----------------------------------------------------------------------------
_PARSE_WARNINGS: list[dict] = []
# parser_quality_report：每个文档一条，记录 parser_source / 文本长度对照 / 漏词等。
_QUALITY_REPORTS: list[dict] = []


def reset_parse_warnings() -> None:
    _PARSE_WARNINGS.clear()
    _QUALITY_REPORTS.clear()


def get_parse_warnings() -> list[dict]:
    return list(_PARSE_WARNINGS)


def get_quality_reports() -> list[dict]:
    return list(_QUALITY_REPORTS)


def _add_warning(warning: dict) -> None:
    _PARSE_WARNINGS.append(warning)


def _add_quality_report(report: dict) -> None:
    _QUALITY_REPORTS.append(report)


def normalize_doc_id(path: Path) -> str:
    stem = path.stem
    if path.parent.name == "attachments":
        return stem.replace("csrc_", "strict_csrc_")
    return stem


def parse_file(path: Path, domain: str) -> list[dict]:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return parse_pdf(path, domain)
    if suffix in {".html", ".htm"}:
        return parse_html(path, domain)
    if suffix in {".txt", ".text", ".md"}:
        return parse_txt(path, domain)
    return []


# ============================================================================
# PDF：MinerU-first 主解析；PyMuPDF 仅 sanity check；MinerU 失败回退 native。
# ============================================================================

def parse_pdf(path: Path, domain: str) -> list[dict]:
    """PDF 解析：默认 MinerU；MinerU 不可用/失败/空输出时回退 native 简单解析。

    - 不调用任何单独 OCR 引擎（扫描件由 MinerU 内部 OCR 处理）。
    - 不默认调用 pdfplumber（仅 ENABLE_PDFPLUMBER_TABLE_FALLBACK=1 时做表格兜底）。
    - PyMuPDF 仅用于 sanity check，不覆盖 MinerU 主解析文本（除非 MinerU 完全失败）。
    """
    from .mineru_parser import content_list_to_pages, run_mineru

    doc_id = normalize_doc_id(path)
    source = str(path)

    if settings.enable_mineru and settings.parser_mode == "mineru_first":
        content_list, error = run_mineru(path)
        if error is None and content_list:
            pages, blocks = content_list_to_pages(content_list, doc_id, domain, source)
            if not pages:
                _add_warning(_warn(doc_id, source, domain, "mineru_convert_failed",
                                   "MinerU content_list produced no pages."))
            else:
                pages = _finalize_mineru_pages(path, doc_id, domain, pages)
                return pages
        else:
            _add_warning(_warn(doc_id, source, domain, error or "mineru_parse_failed",
                               f"MinerU parse failed ({error}); falling back to native."))
    elif settings.enable_mineru and settings.parser_mode != "mineru_first":
        # 非 mineru_first 模式仍走 native（保留回退路径，便于 ablation）
        pass

    # ---- fallback: native 简单解析（PyMuPDF 抽文本；不跑 OCR；不跑 pdfplumber）----
    return _native_fallback_parse(path, doc_id, domain)


def _warn(doc_id: str, source: str, domain: str, reason: str, message: str, **extra) -> dict:
    return {"doc_id": doc_id, "source_path": source, "domain": domain,
            "reason": reason, "message": message, **extra}


def _finalize_mineru_pages(path: Path, doc_id: str, domain: str, pages: list[dict]) -> list[dict]:
    """MinerU 页面收尾：表格结构化、保护词保护、sanity check、上下文、quality report。"""
    # 财报/含表格页：用 MinerU 表格 grid 生成 financial_metric / table_value 结构化记录
    from .table_extractor import table_to_structured_records

    table_block_count = 0
    structured_record_count = 0
    for page in pages:
        records: list[dict] = []
        grids = page.pop("table_grids", []) or []
        for grid in grids:
            table_block_count += 1
            records.extend(
                table_to_structured_records(grid, doc_id, page["page"], domain, page.get("text", ""))
            )
        page["structured_records"] = records
        structured_record_count += len(records)

    # ---- 可选 pdfplumber 表格兜底 ----
    # 仅 ENABLE_PDFPLUMBER_TABLE_FALLBACK=1 且 MinerU 整篇未抽到任何表格记录时，
    #   用 pdfplumber 补充表格行/结构化记录（财报/合同等含表格文档救援），不覆盖 MinerU 文本。
    if settings.enable_pdfplumber_table_fallback and structured_record_count == 0:
        plumber_added = _supplement_with_pdfplumber(path, doc_id, domain, pages)
        if plumber_added:
            structured_record_count += plumber_added
            _add_warning(_warn(doc_id, str(path), domain, "pdfplumber_table_fallback_used",
                               f"MinerU produced no table records; pdfplumber supplemented "
                               f"{plumber_added} records."))

    # 去页眉页脚：MinerU 把 header 也按原序并入了 page text，跨页高频重复的短行需清理。
    #   白名单（标题/条款号/表头/各域关键词）不删。在结构化抽取之后做，避免影响表格 grid。
    _clean_headers_footers(pages)
    for page in pages:
        page["parser_quality"] = round(quality_score(page.get("text", "")), 3)

    for page in pages:
        page["section"] = infer_section(page.get("text", ""))
    _attach_page_context(pages)

    # ---- PyMuPDF sanity check（仅对照，不覆盖 MinerU 主文本）----
    report = {
        "doc_id": doc_id,
        "domain": domain,
        "source_path": str(path),
        "parser_source": "mineru",
        "table_block_count": table_block_count,
        "structured_record_count": structured_record_count,
        "warnings": [],
    }
    if settings.enable_native_sanity_check:
        sanity = native_sanity_check(path, pages)
        report.update(sanity)
        for w in sanity.get("warnings", []):
            _add_warning(_warn(doc_id, str(path), domain, w,
                               f"native sanity check: {w}"))
    _add_quality_report(report)
    return pages


def _native_fallback_parse(path: Path, doc_id: str, domain: str) -> list[dict]:
    """MinerU 不可用/失败时的兜底：仅用 PyMuPDF 抽文本。不跑 OCR，不跑 pdfplumber。

    只为保证构建不中断，不作为主推荐路径。parser_source="native_fallback"。
    """
    fitz_pages = _extract_fitz(path)
    page_count = len(fitz_pages)
    if page_count == 0 or not any((t or "").strip() for t in fitz_pages.values()):
        # 完全无文本（可能是扫描件且 MinerU 不可用）：记 warning，返回空，不中断
        _add_warning(_warn(doc_id, str(path), domain, "native_fallback_empty",
                           "Native fallback extracted zero text (image-only PDF without MinerU)."))
        _add_quality_report({
            "doc_id": doc_id, "domain": domain, "source_path": str(path),
            "parser_source": "native_fallback", "native_text_len": 0,
            "warnings": ["native_fallback_empty"],
        })
        return []

    pages: list[dict] = []
    for index in range(1, page_count + 1):
        text = clean_text(fitz_pages.get(index, ""))
        pages.append({
            "doc_id": doc_id,
            "domain": domain,
            "source_path": str(path),
            "page": index,
            "text": text,
            "table_text": "",
            "parser_source": "native_fallback",
            "parser_quality": round(quality_score(text), 3),
            "structured_records": [],
        })
    _clean_headers_footers(pages)
    for page in pages:
        page["section"] = infer_section(page["text"])
    _attach_page_context(pages)
    fallback_warnings: list[str] = []
    # 财报回退到 native：PyMuPDF 抽不出结构化表格，financial_metric 会很弱，显式告警。
    if domain == "financial_reports":
        fallback_warnings.append("native_fallback_financial_report_weak_tables")
        _add_warning(_warn(doc_id, str(path), domain, "native_fallback_financial_report_weak_tables",
                           "Financial report fell back to PyMuPDF; table structure is weak, "
                           "financial_metric extraction will be degraded."))
    _add_quality_report({
        "doc_id": doc_id, "domain": domain, "source_path": str(path),
        "parser_source": "native_fallback",
        "native_text_len": sum(len(p["text"]) for p in pages),
        "warnings": fallback_warnings,
    })
    return pages


# ============================================================================
# PyMuPDF sanity check：只对照，不做主解析，不自动切换结果。
# ============================================================================

def native_sanity_check(path: Path, mineru_pages: list[dict]) -> dict:
    """用 PyMuPDF 轻量抽取文本，与 MinerU 结果对照，产出漏文本/漏条款号/漏保护词告警。"""
    fitz_pages = _extract_fitz(path)
    native_text = "\n".join(fitz_pages.values())
    mineru_text = "\n".join(p.get("text", "") for p in mineru_pages)

    native_len = len(native_text)
    mineru_len = len(mineru_text)
    ratio = round(mineru_len / native_len, 3) if native_len else 1.0

    native_clauses = len(set(CLAUSE_NO_SCAN_RE.findall(native_text)))
    mineru_clauses = len(set(CLAUSE_NO_SCAN_RE.findall(mineru_text)))

    native_protect = {t: native_text.count(t) for t in PROTECT_TERMS_FOR_SANITY if t in native_text}
    mineru_protect = {t: mineru_text.count(t) for t in PROTECT_TERMS_FOR_SANITY if t in mineru_text}
    missing_protect = [t for t in native_protect if t not in mineru_protect]

    warnings: list[str] = []
    if native_len > 0 and mineru_len < native_len * 0.7:
        warnings.append("mineru_text_much_shorter_than_native")
    if missing_protect:
        warnings.append("mineru_missing_protect_terms")
    if native_clauses > 0 and mineru_clauses < native_clauses * 0.7:
        warnings.append("mineru_missing_clause_numbers")

    return {
        "native_text_len": native_len,
        "mineru_text_len": mineru_len,
        "text_len_ratio": ratio,
        "native_clause_count": native_clauses,
        "mineru_clause_count": mineru_clauses,
        "native_protect_terms": native_protect,
        "mineru_protect_terms": mineru_protect,
        "missing_protect_terms": missing_protect,
        "warnings": warnings,
    }


def _extract_fitz(path: Path) -> dict[int, str]:
    pages: dict[int, str] = {}
    try:
        import fitz

        with fitz.open(path) as pdf:
            for index, page in enumerate(pdf, start=1):
                pages[index] = page.get_text("text") or ""
    except Exception:
        return {}
    return pages


def _extract_pdfplumber(path: Path, doc_id: str, domain: str) -> dict[int, dict]:
    pages: dict[int, dict] = {}
    try:
        import pdfplumber

        from .table_extractor import table_to_lines, table_to_structured_records

        with pdfplumber.open(path) as pdf:
            for index, page in enumerate(pdf.pages, start=1):
                try:
                    text = page.extract_text() or ""
                except Exception:
                    text = ""
                table_lines: list[str] = []
                records: list[dict] = []
                try:
                    tables = page.extract_tables() or []
                except Exception:
                    tables = []
                for table in tables:
                    table_lines.extend(table_to_lines(table))
                    records.extend(table_to_structured_records(table, doc_id, index, domain, text))
                pages[index] = {"text": text, "table_lines": table_lines, "records": records}
    except Exception:
        return pages
    return pages


def _supplement_with_pdfplumber(path: Path, doc_id: str, domain: str, pages: list[dict]) -> int:
    """用 pdfplumber 抽取的表格行/结构化记录补充到对应 MinerU 页，返回新增记录数。

    只补充表格信息（table_text 行 + structured_records），不触碰 MinerU 主文本顺序。
    页号对齐：pdfplumber 与 MinerU 同为 1-based 页码。
    """
    plumber = _extract_pdfplumber(path, doc_id, domain)
    if not plumber:
        return 0
    pages_by_idx = {p["page"]: p for p in pages}
    added = 0
    for page_index, data in plumber.items():
        page = pages_by_idx.get(page_index)
        if page is None:
            continue
        lines = data.get("table_lines") or []
        records = data.get("records") or []
        if lines:
            extra = "\n".join(lines)
            page["table_text"] = (page.get("table_text", "") + "\n" + extra).strip()
            # 表格行并入页文本末尾，保证可被 BM25 检索到（不改动 MinerU 原始正文顺序）
            page["text"] = (page.get("text", "") + "\n" + extra).strip()
        if records:
            page["structured_records"] = (page.get("structured_records") or []) + records
            added += len(records)
    return added


def quality_score(text: str) -> float:
    """对抽取文本打质量分。文本长度、中文比例、数字比例、乱码比例、金融关键词、表格特征。

    PUA 项目符号先归一为普通符号；残留未知 PUA 只极轻微扣分（不再一刀切当严重乱码）。
    """
    if not text or not text.strip():
        return 0.0
    text = normalize_pua_symbols(text)
    length = len(text)
    cjk = len(CJK_RE.findall(text))
    digits = len(DIGIT_RE.findall(text))
    garble = len(GARBLE_RE.findall(text))
    residual_pua = len(PUA_RE.findall(text))

    score = 0.0
    # 长度（对数饱和，避免长但乱的文本霸榜）
    score += min(length, 4000) / 800.0
    # 中文比例（金融中文文档主体）
    cjk_ratio = cjk / max(1, length)
    score += cjk_ratio * 6.0
    # 数字比例（财报/合同含表格数据），适度加分
    digit_ratio = digits / max(1, length)
    score += min(digit_ratio, 0.25) * 4.0
    # 严重乱码惩罚（强惩罚：CID 缺字符、替换字符、连续方块/问号）
    garble_ratio = garble / max(1, length)
    score -= garble_ratio * 30.0
    # 残留 PUA（已归一已知 bullet 后剩下的私用区符号）极轻微扣分
    pua_ratio = residual_pua / max(1, length)
    score -= pua_ratio * 2.0
    # 金融关键词
    score += sum(1 for kw in FINANCIAL_KEYWORDS if kw in text) * 0.15
    # 表格特征
    if TABLE_HINT_RE.search(text):
        score += 1.0
    return score


def _clean_headers_footers(pages: list[dict]) -> None:
    """过滤同一文档中高频重复出现的短行（页眉页脚），保留标题/条款号/表头。"""
    if len(pages) < 3:
        return
    counter: Counter = Counter()
    for page in pages:
        seen_in_page = set()
        for line in page["text"].splitlines():
            stripped = line.strip()
            if 0 < len(stripped) <= 40 and stripped not in seen_in_page:
                seen_in_page.add(stripped)
                counter[stripped] += 1
    threshold = max(3, int(len(pages) * 0.6))
    repeated = {
        line for line, count in counter.items()
        if count >= threshold and not _is_meaningful_short_line(line)
    }
    if not repeated:
        return
    for page in pages:
        kept = [line for line in page["text"].splitlines() if line.strip() not in repeated]
        page["text"] = "\n".join(kept).strip()


def _is_meaningful_short_line(line: str) -> bool:
    """页眉页脚清理白名单：标题/条款号/表头/各领域关键小标题不被当作页眉页脚删除。"""
    return bool(
        re.search(
            r"第[一二三四五六七八九十百零〇0-9]+[章节条]|指标=|原始表格行=|单位[:：]"
            r"|营业收入|净利润|现金流|资产负债"
            r"|保险责任|责任免除|现金价值|账户价值|退保|身故保险金|等待期|犹豫期"
            r"|发行规模|票面利率|募集资金用途|回售|赎回|担保|评级"
            r"|核心观点|风险提示|盈利预测|投资建议",
            line,
        )
    )


def _attach_page_context(pages: list[dict]) -> None:
    """为跨页条款补足上下文：prev_page_tail / next_page_head。

    next_head 取 400 字：法规条文常以多分项（一)~(十)）跨页延续，200 字不足以覆盖
    完整分项列表（如担保事项的资产负债率阈值项），放宽到 400 字保证跨页分项不丢。
    """
    for i, page in enumerate(pages):
        prev_tail = ""
        next_head = ""
        if i > 0:
            prev_tail = _tail(pages[i - 1]["text"], 200)
        if i + 1 < len(pages):
            next_head = _head(pages[i + 1]["text"], 400)
        page["prev_page_tail"] = prev_tail
        page["next_page_head"] = next_head


def _tail(text: str, n: int) -> str:
    return text.strip()[-n:] if text else ""


def _head(text: str, n: int) -> str:
    return text.strip()[:n] if text else ""


# ============================================================================
# HTML / TXT
# ============================================================================

def parse_html(path: Path, domain: str) -> list[dict]:
    """DOM 顺序解析 HTML（不走 MinerU，不一把梭 get_text）。

    - 删除 script/style/noscript/nav/footer/广告/分享按钮等噪声。
    - 按 DOM 顺序抽取 h1-h6=title、p/div=paragraph、li=list_item、table=table。
    - 表格转 table_text。输出统一 pages/blocks，parser_source="html_dom"。
    """
    return parse_html_dom(path, domain)


def parse_html_dom(path: Path, domain: str) -> list[dict]:
    from .mineru_parser import _table_html_to_grid, _table_html_to_text
    from .table_extractor import table_to_structured_records

    doc_id = normalize_doc_id(path)
    source = str(path)
    html = path.read_text(encoding="utf-8", errors="ignore")
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript", "nav", "footer", "header", "aside", "form", "button"]):
        tag.decompose()
    # 常见广告/分享类容器（按 class/id 关键字）
    for tag in soup.find_all(attrs={"class": re.compile(r"(ad|advert|share|social|nav|footer|sidebar)", re.I)}):
        tag.decompose()
    for tag in soup.find_all(attrs={"id": re.compile(r"(ad|advert|share|social|nav|footer|sidebar)", re.I)}):
        tag.decompose()

    body = soup.body or soup
    blocks: list[dict] = []
    text_parts: list[str] = []
    table_parts: list[str] = []
    structured_records: list[dict] = []
    section_state = {"title": "", "parent": ""}
    counter = 0

    # 含 div：很多正文站点用 div 承载段落。只取“叶子内容 div”（不含其它块级子元素），
    # 避免父 div 与其内部 p/div 重复抽取同一段文本。
    block_tags = ["h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "div", "table"]
    for el in body.find_all(block_tags):
        name = el.name
        if name == "div":
            # 跳过包含块级子元素的容器 div，只保留叶子内容 div
            if el.find(block_tags):
                continue
        if name == "table":
            try:
                table_text = _table_html_to_text(str(el))
            except Exception:
                table_text = el.get_text(" | ", strip=True)
            if not table_text.strip():
                continue
            btype, btext = "table", table_text
            table_parts.append(table_text)
            text_parts.append(table_text)
            # HTML 表格也生成结构化记录（financial_metric / table_value 等）
            try:
                grid = _table_html_to_grid(str(el))
            except Exception:
                grid = []
            table_records = (
                table_to_structured_records(grid, doc_id, 1, domain, table_text) if grid else []
            )
            structured_records.extend(table_records)
        else:
            btext = el.get_text(" ", strip=True)
            if not btext:
                continue
            if name in {"h1", "h2", "h3", "h4", "h5", "h6"}:
                btype = "title"
                section_state["parent"] = section_state["title"]
                section_state["title"] = btext[:80]
            elif name == "li":
                btype = "list_item"
            else:
                btype = "clause" if CLAUSE_NO_SCAN_RE.match(btext) else "paragraph"
            text_parts.append(btext)
            table_records = []
        counter += 1
        blocks.append({
            "doc_id": doc_id, "domain": domain, "source_path": source, "page": 1,
            "block_id": f"{doc_id}::p1::b{counter}", "block_type": btype, "text": btext,
            "table_text": btext if btype == "table" else "",
            "structured_records": table_records if btype == "table" else [],
            "section": section_state["title"],
            "parent_section": section_state["parent"],
            "clause_no": "", "bbox": None, "parser_source": "html_dom",
            "parser_quality": 0.0, "warnings": [],
        })

    if not text_parts:  # DOM 抽取为空：退化为整体文本，保证不丢内容
        text_parts = [soup.get_text("\n")]
    full_text = clean_text("\n".join(text_parts))
    page = {
        "doc_id": doc_id, "domain": domain, "source_path": source, "page": 1,
        "section": infer_section(full_text), "text": full_text,
        "table_text": "\n".join(table_parts), "blocks": blocks,
        "structured_records": structured_records, "parser_source": "html_dom",
        "parser_quality": round(quality_score(full_text), 3),
        "prev_page_tail": "", "next_page_head": "",
    }
    return [page]


def parse_txt(path: Path, domain: str) -> list[dict]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    return [_page_record(normalize_doc_id(path), domain, path, 1, text)]


def discover_documents(raw_dir: Path) -> list[tuple[Path, str]]:
    docs: list[tuple[Path, str]] = []
    for domain_dir in raw_dir.iterdir():
        if not domain_dir.is_dir():
            continue
        for path in domain_dir.rglob("*"):
            if path.is_file() and path.suffix.lower() in {".pdf", ".txt", ".html", ".htm"}:
                docs.append((path, domain_dir.name))
    return docs


def _page_record(doc_id: str, domain: str, path: Path, page: int, text: str) -> dict:
    return {
        "doc_id": doc_id,
        "domain": domain,
        "source_path": str(path),
        "page": page,
        "section": infer_section(text),
        "text": clean_text(text),
        "table_text": "",
        "parser_source": "text",
        "parser_quality": quality_score(text),
        "structured_records": [],
        "prev_page_tail": "",
        "next_page_head": "",
    }


def clean_text(text: str) -> str:
    text = normalize_pua_symbols(text)
    text = text.replace("\u3000", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def infer_section(text: str) -> str:
    for line in text.splitlines()[:20]:
        line = line.strip()
        if re.match(r"^(第[一二三四五六七八九十百零〇]+[章节条]|[一二三四五六七八九十]+、|\d+(\.\d+)*[、.])", line):
            return line[:80]
        if 4 <= len(line) <= 80 and not re.search(r"[。；]$", line):
            return line
    return ""
