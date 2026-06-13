from __future__ import annotations

import re


# 通用章节起点：第X章/节/条、一、二、、 1. 1.1
SECTION_RE = re.compile(r"(?=^\s*(第[一二三四五六七八九十百零〇]+[章节条]|[一二三四五六七八九十]+、|\d+(?:\.\d+)*[、.]))", re.M)
ARTICLE_RE = re.compile(r"第([一二三四五六七八九十百零〇0-9]+)条")
PARAGRAPH_RE = re.compile(r"第([一二三四五六七八九十0-9]+)款")
ITEM_RE = re.compile(r"第([一二三四五六七八九十0-9]+)项|[（(]([一二三四五六七八九十0-9]+)[)）]")

# 不可在这些条件词处切断条款（应当与前后文同处一个 chunk）
PROTECT_BREAK = re.compile(r"但是|除外|不得|应当|须经|除.*?外|另有规定")

DOMAIN_SECTION_HINTS = {
    "insurance": [
        "保险责任", "责任免除", "身故保险金", "现金价值", "退保", "领取", "犹豫期",
        "等待期", "宽限期", "保险金额", "保险费", "合同解除", "给付",
    ],
    "financial_contracts": [
        "发行条款", "发行规模", "票面利率", "期限", "评级", "担保", "增信",
        "回售", "赎回", "募集资金用途", "违约责任", "偿债保障",
    ],
    "research": [
        "核心观点", "行业趋势", "公司比较", "风险提示", "盈利预测", "投资建议",
        "投资评级", "目标价", "估值",
    ],
}


def chunk_pages(pages: list[dict], chunk_size: int = 850, overlap: int = 120) -> list[dict]:
    chunks: list[dict] = []
    counters: dict[str, int] = {}
    for page in pages:
        domain = page.get("domain", "")
        parts = split_by_domain(page["text"], domain)
        for section, text in parts:
            for piece in sliding_chunks(text, chunk_size=chunk_size, overlap=overlap):
                doc_id = page["doc_id"]
                counters[doc_id] = counters.get(doc_id, 0) + 1
                chunk = {
                    "doc_id": doc_id,
                    "domain": domain,
                    "source_path": page["source_path"],
                    "page": page["page"],
                    "section": section or page.get("section", ""),
                    "parent_section": page.get("section", ""),
                    "chunk_id": f"{doc_id}::p{page['page']}::{counters[doc_id]}",
                    "text": piece,
                    "prev_context": page.get("prev_page_tail", ""),
                    "next_context": page.get("next_page_head", ""),
                }
                _attach_clause_numbers(chunk, section, piece)
                page_table = page.get("table_text", "")
                if page_table:
                    overlap_lines = [line for line in page_table.splitlines() if line and line in piece]
                    if overlap_lines:
                        chunk["table_text"] = "\n".join(overlap_lines)
                chunks.append(chunk)
    return chunks


def split_by_domain(text: str, domain: str) -> list[tuple[str, str]]:
    """领域感知切分。regulatory 按条；insurance/contracts/research 按章节关键词；其余通用章节切分。"""
    text = (text or "").strip()
    if not text:
        return []
    if domain == "regulatory":
        return split_by_article(text)
    hints = DOMAIN_SECTION_HINTS.get(domain)
    if hints:
        keyword_split = split_by_keywords(text, hints)
        if len(keyword_split) > 1:
            return keyword_split
    return split_sections(text)


def split_by_article(text: str) -> list[tuple[str, str]]:
    """法规：优先按 '第X条' 切分，保留条款标题。"""
    starts = [m.start() for m in re.finditer(r"(?=第[一二三四五六七八九十百零〇0-9]+条)", text)]
    if not starts:
        return split_sections(text)
    starts.append(len(text))
    result = []
    for left, right in zip(starts, starts[1:]):
        block = text[left:right].strip()
        if not block:
            continue
        first = block.splitlines()[0].strip()[:80]
        result.append((first, block))
    return result


def split_by_keywords(text: str, hints: list[str]) -> list[tuple[str, str]]:
    """按领域章节关键词所在行切分。"""
    lines = text.splitlines()
    boundaries: list[int] = []
    headers: list[str] = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        if len(stripped) <= 30 and any(h in stripped for h in hints):
            boundaries.append(i)
            headers.append(stripped[:80])
    if not boundaries:
        return [("", text)]
    result: list[tuple[str, str]] = []
    # 第一个边界前的前言
    if boundaries[0] > 0:
        prefix = "\n".join(lines[: boundaries[0]]).strip()
        if prefix:
            result.append(("", prefix))
    boundaries.append(len(lines))
    for idx in range(len(headers)):
        block = "\n".join(lines[boundaries[idx]: boundaries[idx + 1]]).strip()
        if block:
            result.append((headers[idx], block))
    return result


def split_sections(text: str) -> list[tuple[str, str]]:
    text = text.strip()
    if not text:
        return []
    starts = [match.start() for match in SECTION_RE.finditer(text)]
    if not starts:
        return [("", para) for para in split_paragraph_blocks(text)]
    starts.append(len(text))
    result = []
    for left, right in zip(starts, starts[1:]):
        block = text[left:right].strip()
        first = block.splitlines()[0].strip()[:80] if block else ""
        result.append((first, block))
    return result


def split_paragraph_blocks(text: str) -> list[str]:
    paragraphs = [item.strip() for item in re.split(r"\n\s*\n", text) if item.strip()]
    if paragraphs:
        return paragraphs
    return [item.strip() for item in re.split(r"(?<=[。！？；])", text) if item.strip()]


def _attach_clause_numbers(chunk: dict, section: str, piece: str) -> None:
    head = (section or "") + "\n" + piece[:120]
    article = ARTICLE_RE.search(head)
    if article:
        chunk["article_no"] = article.group(0)
        chunk["clause_no"] = article.group(0)
    paragraph = PARAGRAPH_RE.search(head)
    if paragraph:
        chunk["paragraph_no"] = paragraph.group(0)
    item = ITEM_RE.search(head)
    if item:
        chunk["item_no"] = item.group(0)


def sliding_chunks(text: str, chunk_size: int, overlap: int) -> list[str]:
    """滑窗切分，但避免在条件词（但是/除外/应当/不得/须经）处切断条款。"""
    text = text.strip()
    if len(text) <= chunk_size:
        return [text] if text else []
    chunks = []
    start = 0
    while start < len(text):
        end = min(len(text), start + chunk_size)
        window = text[start:end]
        if end < len(text):
            cut = max(window.rfind("。"), window.rfind("\n"), window.rfind("；"))
            if cut > chunk_size * 0.55:
                candidate_end = start + cut + 1
                # 若切点紧邻条件词，则不在此切断，顺延到下一个句末
                tail = text[candidate_end: candidate_end + 12]
                if not PROTECT_BREAK.search(tail):
                    end = candidate_end
                    window = text[start:end]
        chunks.append(window.strip())
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return chunks
