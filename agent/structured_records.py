from __future__ import annotations

"""离线结构化证据抽取：把解析后的页面转成结构化记录，再转成可检索 chunk。

纯本地、不调用任何模型。产出：
  - financial_metric_records: 财报核心指标（来自表格结构化记录）
  - regulatory_clause_records: 法规条款（第X条 + 义务词）
  - insurance_formula_records: 保险公式/触发条件
  - contract_key_records: 合同关键条款
  - research_claim_records: 研报观点
所有记录统一转成 structured_chunks（record_type 保留），并入 BM25 索引参与检索。
"""

import re

try:
    from .chunker import DOMAIN_SECTION_HINTS, split_by_article, split_by_keywords
except Exception:  # pragma: no cover - 防御：chunker 接口缺失时退化为整页 block
    DOMAIN_SECTION_HINTS = {}

    def split_by_article(text):  # type: ignore
        return [("", text)] if text else []

    def split_by_keywords(text, hints):  # type: ignore
        return [("", text)] if text else []
from .domain_rules import (
    CONTRACT_KEY_TERMS,
    INSURANCE_FORMULA_TERMS,
    REGULATORY_OBLIGATION_TERMS,
    RESEARCH_CLAIM_TERMS,
)


ARTICLE_RE = re.compile(r"第[一二三四五六七八九十百零〇0-9]+条")
SENTENCE_SPLIT = re.compile(r"(?<=[。；！？])")

# insurance_clause 命中关键词：任一命中即生成条款记录。
INSURANCE_CLAUSE_HIT_TERMS = [
    "保险责任", "责任免除", "负责赔偿", "不负责赔偿", "不承担", "不承担保险责任",
    "不承担给付", "赔偿责任", "经济赔偿责任", "赔偿限额", "免赔额", "免赔率",
    "保险期间", "追溯期", "报告期", "保险费", "预收保险费", "实际保险费",
    "如实告知", "解除保险合同", "退保", "现金价值", "账户价值", "领取",
    "保险金", "给付", "索赔", "事故通知", "除外", "但", "但是",
    "另有约定", "另有规定", "不在此限",
]

# 压缩时必须优先保留的限制/条件词
_CLAUSE_KEEP_TERMS = [
    "但", "但是", "除外", "另有约定", "另有规定", "不承担", "不负责赔偿",
    "不适用", "不在此限", "免赔额", "免赔率", "以上", "以下", "不超过", "不低于",
]

_CLAUSE_NO_RE = re.compile(r"第[一二三四五六七八九十百零〇0-9]+条")
# 跨页拼到末尾 block 的下一页头部长度（保住溢出到下一页的但书/除外/限制）
_CLAUSE_SPILLOVER_TAIL = 200


def build_structured_records(pages: list[dict]) -> dict[str, list[dict]]:
    """从解析页面集合抽取各类结构化记录。

    财报指标只在 domain==financial_reports 时进 financial_metric_records（避免合同/募集
    说明书里的财务表污染财报指标文件）；非财报里的表格指标记录进 other_metric_records，
    仍会转成结构化 chunk 参与检索，但不写入 financial_metric_records.jsonl。
    """
    out: dict[str, list[dict]] = {
        "financial_metric_records": [],
        "other_metric_records": [],
        "regulatory_clause_records": [],
        "insurance_formula_records": [],
        "insurance_clause_records": [],
        "contract_key_records": [],
        "research_claim_records": [],
        "filtered_low_confidence_metrics": [],
    }
    for page in pages:
        domain = page.get("domain", "")
        # 财报指标：来自 table_extractor 的结构化记录
        for rec in page.get("structured_records", []) or []:
            if rec.get("record_type") != "financial_metric":
                continue
            # D：过滤低置信财报指标，避免污染结构化索引
            if not _financial_metric_passes(rec):
                out["filtered_low_confidence_metrics"].append(rec)
                continue
            if domain == "financial_reports":
                out["financial_metric_records"].append(rec)
            else:
                out["other_metric_records"].append(rec)
        if domain == "regulatory":
            out["regulatory_clause_records"].extend(_regulatory_clauses(page))
        elif domain == "insurance":
            out["insurance_formula_records"].extend(_insurance_formulas(page))
            out["insurance_clause_records"].extend(_insurance_clauses(page))
        elif domain == "financial_contracts":
            out["contract_key_records"].extend(_contract_keys(page))
        elif domain == "research":
            out["research_claim_records"].extend(_research_claims(page))
    return out


def _is_column_merge_value(value_raw: str) -> bool:
    """检测财报数值是否为 OCR 列粘连（相邻两列被并入一格），如 "150,181 945"、
    "427,609,892 8.1 384,322,141"。这类记录的 normalized_value_yuan 会被算成天文数字
    （如 1.48e15 元），污染跨文档数值比较，必须在入索引前剔除。

    保守：只把"两个完整数字组被空格隔开"判为粘连；放过合法的小数点后空格（"11. 12"）
    与千分位空格（"127, 665"），避免误杀正常记录。
    """
    s = str(value_raw or "").strip()
    if not s:
        return False
    # 合法：小数点后空格 "1,234. 56" / "11. 12"
    if re.fullmatch(r"-?[\d,]+\.\s*\d+", s):
        return False
    # 合法：纯千分位（逗号后可能跟空格）"127, 665" / "1, 234, 567"
    if re.fullmatch(r"-?\d{1,3}(?:,\s*\d{3})+(?:\.\d+)?", s):
        return False
    # 其余出现 "数字 空格 数字" 视为列粘连
    return bool(re.search(r"\d\s+\d", s))


def _financial_metric_passes(rec: dict) -> bool:
    """D：财报指标进入结构化索引的门槛。

    - confidence ∈ {high, medium}；
    - value_raw 非空；
    - value 或 normalized_value_yuan 至少一个非空；
    - value_raw 不是 OCR 列粘连（避免天文数字污染跨文档比较）。
    """
    if rec.get("confidence") not in {"high", "medium"}:
        return False
    if not str(rec.get("value_raw", "") or "").strip():
        return False
    if rec.get("value") is None and rec.get("normalized_value_yuan") is None:
        return False
    if _is_column_merge_value(rec.get("value_raw", "")):
        return False
    return True


def _meta(page: dict) -> dict:
    return {
        "doc_id": page.get("doc_id", ""),
        "domain": page.get("domain", ""),
        "page": page.get("page"),
        "source_path": page.get("source_path", ""),
        "parser_source": page.get("parser_source", ""),
    }


def _regulatory_clauses(page: dict) -> list[dict]:
    text = page.get("text", "")
    records: list[dict] = []
    starts = [m.start() for m in re.finditer(r"(?=第[一二三四五六七八九十百零〇0-9]+条)", text)]
    if not starts:
        return records
    starts.append(len(text))
    blocks = [text[left:right].strip() for left, right in zip(starts, starts[1:])]
    # 跨页续接：条文常跨页（如第四十七条头在本页底、其分项(二)-(六)续在下一页头），
    # 按页独立切分会丢失续页的分项/但书。把下一页头部（到下一个“第X条”为止）补到本页
    # 最后一个条文 block，使跨页分项重新归属到该条。
    next_head = str(page.get("next_page_head", "")).strip()
    if blocks and next_head and not _starts_with_article(next_head):
        cont = _article_continuation(next_head)
        if cont:
            blocks[-1] = (blocks[-1] + " " + cont).strip()
    for block in blocks:
        if not block:
            continue
        article = ARTICLE_RE.search(block)
        if not article:
            continue
        obligations = [t for t in REGULATORY_OBLIGATION_TERMS if t in block]
        records.append(
            {
                "record_type": "regulatory_clause",
                **_meta(page),
                "article_no": article.group(0),
                "obligations": obligations,
                # 保留较完整条文，避免“但是/除外/另有规定”被硬截；压缩交给 protected window
                "raw": block[:3000],
            }
        )
    return records


def _starts_with_article(text: str) -> bool:
    """下一页是否以新条文（第X条）开头：若是则本页条文已结束，不需续接。"""
    return bool(re.match(r"\s*第[一二三四五六七八九十百零〇0-9]+条", text))


def _article_continuation(next_head: str) -> str:
    """取下一页头部中、属于上一条文延续的部分（截到下一个“第X条”之前）。"""
    m = re.search(r"第[一二三四五六七八九十百零〇0-9]+条", next_head)
    cont = next_head[: m.start()] if m else next_head
    return cont.strip()[:1200]


def _insurance_formulas(page: dict) -> list[dict]:
    text = page.get("text", "")
    sentences = _sentences(text)
    records: list[dict] = []
    for idx, sentence in enumerate(sentences):
        hits = [t for t in INSURANCE_FORMULA_TERMS if t in sentence]
        has_formula = bool(re.search(r"max|min|较大者|较小者|=|×|\*|给付|赔付", sentence, re.I))
        if len(hits) >= 1 and (has_formula or "现金价值" in sentence or "保险金" in sentence):
            records.append(
                {
                    "record_type": "insurance_formula",
                    **_meta(page),
                    "terms": hits,
                    # 命中句 + 前1句 + 后3句，保住免责/除外/等待期/退保等条件句
                    "raw": _context_window(sentences, idx, before=1, after=3, max_len=900),
                }
            )
    return records


def _insurance_clauses(page: dict) -> list[dict]:
    """抽取责任险/财险/医疗险/通用保险条款（insurance_clause）。

    切分策略复用 chunker 的条款/章节切分器（只在“第X条”或短标题行切分，绝不切句中），
    避免把公式句/条件句撕碎。只切分当前页正文；命中关键词的 block 生成 insurance_clause，
    保留但书、除外、不承担、免赔额、追溯期等限制条件。最后一个 block 追加下一页头部，
    保住溢出到下一页的限制/条件，不再整页拼接重切（避免跨页重复记录）。
    """
    text = str(page.get("text", "")).strip()
    if not text:
        return []
    blocks = _split_clause_blocks(text)
    if blocks:
        # 仅给最后一个 block 追加下一页头部（条款常溢出到下一页），其余 block 不跨页
        next_head = str(page.get("next_page_head", ""))[:_CLAUSE_SPILLOVER_TAIL]
        if next_head.strip():
            blocks[-1] = blocks[-1] + "\n" + next_head
    context_used = bool(str(page.get("next_page_head", "")).strip())
    records: list[dict] = []
    for block in blocks:
        block = block.strip()
        if len(block) < 8:
            continue
        terms = [t for t in INSURANCE_CLAUSE_HIT_TERMS if t in block]
        if not terms:
            continue
        # 跳过目录/标题清单 block（命中关键词但只是小节标题罗列，无条款正文）
        if _is_toc_block(block):
            continue
        raw = block if len(block) <= 2500 else compress_insurance_clause_block(block)
        records.append(
            {
                "record_type": "insurance_clause",
                **_meta(page),
                "section": _clause_section(block),
                "clause_no": _clause_no(block),
                "clause_type": _clause_type(block),
                "terms": terms,
                "raw": raw,
                "context_used": context_used,
            }
        )
    return records


def _split_clause_blocks(text: str) -> list[str]:
    """按条款/章节切分当前页文本为若干 block。

    复用 chunker 的切分器：优先“第X条”，否则按保险领域章节关键词所在的短标题行切分。
    两者都只在条款号或独立短标题行切，绝不在句中切，避免公式/条件句被撕碎。
    无可用边界时整页作为一个 block。
    """
    if _CLAUSE_NO_RE.search(text):
        parts = split_by_article(text)
    else:
        parts = split_by_keywords(text, DOMAIN_SECTION_HINTS.get("insurance", []))
    blocks = [block for _, block in parts if block and block.strip()]
    return blocks or [text]


# 目录/标题行：编号小节标题（如 "4.如何支付保险费"、"6.3 特殊退保 7.其他权益"），无句末标点。
_TOC_HEADING_RE = re.compile(r"^[▶•\s]*\d+(?:\.\d+){0,2}[\s、.]?\s*[^。；：,，]{0,30}$")
_SENTENCE_END_RE = re.compile(r"[。；：！？]")


def _is_toc_block(block: str) -> bool:
    """判断 block 是否为目录/标题清单（命中关键词但无条款正文）。

    判据：
    - 明确标注“目录/条款目录”且无成句正文，或
    - 绝大多数非空行是编号小节标题且整段几乎无句末标点，或
    - 整个 block 本身就是一行/数行纯编号小节标题（无任何句末标点、无条款正文动词）。
    避免误杀真正条款：只要含足量句末标点（成句正文），即不判为目录。
    """
    head = block[:60]
    explicit_toc = ("条款目录" in head) or ("目录" in head and len(block) < 400)
    lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
    if not lines:
        return False
    heading_like = sum(1 for ln in lines if _TOC_HEADING_RE.match(ln))
    heading_ratio = heading_like / len(lines)
    sentence_ends = len(_SENTENCE_END_RE.findall(block))
    # 成句正文密度：每 120 字至少 1 个句末标点视为有正文
    has_prose = sentence_ends >= max(1, len(block) // 120)
    if explicit_toc and not has_prose:
        return True
    # 标题行占比高且整段几乎无句末标点 -> 目录式罗列
    if heading_ratio >= 0.6 and sentence_ends <= 1 and len(lines) >= 3:
        return True
    # 短 block 且全部行都是编号标题、无任何句末标点 -> 单条/数条目录标题碎片
    if sentence_ends == 0 and heading_like == len(lines) and len(block) <= 60:
        return True
    return False


def _clause_no(block: str) -> str:
    m = _CLAUSE_NO_RE.search(block[:120])
    if m:
        return m.group(0)
    m = re.search(r"\d+(?:\.\d+){1,2}", block[:60])
    return m.group(0) if m else ""


def _clause_section(block: str) -> str:
    """从 block 头部识别小节标题。

    只在“短标题行”上识别 label：取首行，要求其足够短且无句末标点（像标题而非正文），
    才允许命中 label。避免正文里的“保险期间内，被保险人……”这类成句正文被误判为
    section（如 "保险期间"）。
    """
    first_line = block.splitlines()[0].strip() if block else ""
    labels = (
        "保险责任", "责任免除", "赔偿限额与免赔额", "赔偿限额", "免赔额",
        "保险期间", "保险费", "投保人、被保险人义务", "投保人义务", "被保险人义务",
        "保险金申请", "保险金给付", "退保", "现金价值", "账户价值", "释义",
    )
    # 标题行判据：短（<=20 字）且无句末/分隔标点
    is_heading_line = bool(first_line) and len(first_line) <= 20 and not re.search(r"[。；，：！？]", first_line)
    if is_heading_line:
        for label in labels:
            if label in first_line:
                return label
    # 条款号开头（"第X条 责任免除"）：允许在条款号后紧跟的标题词命中
    m = _CLAUSE_NO_RE.match(first_line)
    if m:
        after = first_line[m.end():].strip()[:20]
        for label in labels:
            if label in after:
                return label
    return first_line[:40]


def _clause_type(block: str) -> str:
    """按关键词归类 clause_type。

    顺序经过校准：exclusion 先于 liability（“不负责赔偿”含子串“负责赔偿”，
    且免责条款最关键不能误判）；liability 先于 limit/period（“保险期间内…负责赔偿”
    属责任描述，不应被时间词夺走）。
    """
    if any(t in block for t in ("责任免除", "不负责赔偿", "不承担", "除外", "不在此限")):
        return "exclusion"
    if any(t in block for t in ("保险责任", "负责赔偿", "给付保险金", "赔偿责任")):
        return "liability"
    if any(t in block for t in ("赔偿限额", "免赔额", "免赔率")):
        return "limit"
    if any(t in block for t in ("保险期间", "追溯期", "报告期")):
        return "period"
    if any(t in block for t in ("预收保险费", "实际保险费", "保险费")):
        return "premium"
    if any(t in block for t in ("如实告知", "义务", "通知", "协助", "提供资料")):
        return "duty"
    if any(t in block for t in ("索赔", "保险金申请", "保险金给付", "赔偿保险金")):
        return "claim"
    if any(t in block for t in ("退保", "解除合同")):
        return "surrender"
    if any(t in block for t in ("现金价值", "账户价值")):
        return "cash_value"
    if any(t in block for t in ("释义", "是指", "所述")):
        return "definition"
    return "other"


def compress_insurance_clause_block(block: str) -> str:
    """超长 block（>2500 字）压缩，但必须保留 clause_no 句、命中关键词句、限制条件句。

    绝不裁掉但书、免责、除外、阈值方向、义务条件。
    """
    sentences = [s for s in re.split(r"(?<=[。；！？\n])", block) if s.strip()]
    if not sentences:
        return block[:2500]
    clause_no = _clause_no(block)

    def must_keep(sentence: str) -> bool:
        if clause_no and clause_no in sentence:
            return True
        if any(t in sentence for t in INSURANCE_CLAUSE_HIT_TERMS):
            return True
        if any(t in sentence for t in _CLAUSE_KEEP_TERMS):
            return True
        return False

    kept: list[str] = []
    used = 0
    # 先保必留句
    for s in sentences:
        if must_keep(s):
            kept.append(s)
            used += len(s)
    # 再按序补充其它句直到上限
    for s in sentences:
        if s in kept:
            continue
        if used + len(s) > 2500:
            continue
        kept.append(s)
        used += len(s)
    # 还原顺序
    ordered = [s for s in sentences if s in kept]
    out = "".join(ordered)
    if len(out) > 2500:
        # 必留句已超限：只保留必留句（宁可多给以保住但书）
        out = "".join(s for s in sentences if must_keep(s))
    return out


def _contract_keys(page: dict) -> list[dict]:
    text = page.get("text", "")
    sentences = _sentences(text)
    records: list[dict] = []
    for idx, sentence in enumerate(sentences):
        hits = [t for t in CONTRACT_KEY_TERMS if t in sentence]
        if hits:
            records.append(
                {
                    "record_type": "contract_key",
                    **_meta(page),
                    "terms": hits,
                    # 命中句 + 前1句 + 后2句，保住“除本合同另有约定/不超过/不低于”等条件
                    "raw": _context_window(sentences, idx, before=1, after=2, max_len=900),
                }
            )
    return records


def _research_claims(page: dict) -> list[dict]:
    text = page.get("text", "")
    sentences = _sentences(text)
    records: list[dict] = []
    for idx, sentence in enumerate(sentences):
        hits = [t for t in RESEARCH_CLAIM_TERMS if t in sentence]
        if hits:
            records.append(
                {
                    "record_type": "research_claim",
                    **_meta(page),
                    "terms": hits,
                    "raw": _context_window(sentences, idx, before=1, after=2, max_len=700),
                }
            )
    return records


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in SENTENCE_SPLIT.split(text or "") if len(s.strip()) >= 8]


def _context_window(sentences: list[str], idx: int, before: int, after: int, max_len: int) -> str:
    """取命中句及其前后若干句，保住跨句的限制/条件。"""
    start = max(0, idx - before)
    end = min(len(sentences), idx + after + 1)
    window = "".join(sentences[start:end]).strip()
    if len(window) <= max_len:
        return window
    # 超长：保命中句，再尽量带后续条件句
    keep = sentences[idx]
    for nxt in sentences[idx + 1:end]:
        if len(keep) + len(nxt) > max_len:
            break
        keep += nxt
    return keep[:max_len]


# ============================================================================
# 结构化记录 -> 可检索 chunk
# ============================================================================

def records_to_chunks(records_by_type: dict[str, list[dict]]) -> list[dict]:
    chunks: list[dict] = []
    counters: dict[str, int] = {}
    # 这些记录类型不直接转 chunk（仅用于统计/告警）
    skip_types = {"filtered_low_confidence_metrics"}
    for rec_type, records in records_by_type.items():
        if rec_type in skip_types:
            continue
        for rec in records:
            if rec.get("record_type") == "insurance_clause":
                chunks.append(_insurance_clause_chunk(rec, counters))
                continue
            doc_id = rec.get("doc_id", "")
            key = f"{doc_id}::{rec.get('record_type')}"
            counters[key] = counters.get(key, 0) + 1
            text, raw = _record_to_text(rec)
            chunk = {
                "doc_id": doc_id,
                "domain": rec.get("domain", ""),
                "source_path": rec.get("source_path", ""),
                "page": rec.get("page"),
                "section": rec.get("article_no", "") or rec.get("metric", ""),
                "parent_section": "",
                "chunk_id": f"{doc_id}::struct::{rec.get('record_type')}::{counters[key]}",
                "record_type": rec.get("record_type"),
                "text": text,
                "table_text": "",
                # raw 字段供 evidence quote 取原文（结构化记录的原始片段）
                "raw": raw,
                "structured": True,
            }
            _attach_optional_meta(chunk, rec)
            chunks.append(chunk)
    return chunks


def _attach_optional_meta(chunk: dict, rec: dict) -> None:
    """把 parser_source 等可选元数据带到 chunk 上（如有）。"""
    if rec.get("parser_source"):
        chunk["parser_source"] = rec.get("parser_source")
    if rec.get("context_used") is not None:
        chunk["context_used"] = rec.get("context_used")


def _insurance_clause_chunk(rec: dict, counters: dict[str, int]) -> dict:
    doc_id = rec.get("doc_id", "")
    page = rec.get("page")
    key = f"{doc_id}::insurance_clause::{page}"
    index = counters.get(key, 0)
    counters[key] = index + 1
    terms = rec.get("terms", []) or []
    clause_no = rec.get("clause_no", "")
    raw = rec.get("raw", "")
    text = (
        f"保险条款 record_type=insurance_clause "
        f"clause_type={rec.get('clause_type', 'other')} "
        f"条款号={clause_no} terms={','.join(terms)} 原文={raw}"
    )
    chunk = {
        "doc_id": doc_id,
        "domain": rec.get("domain", "insurance"),
        "source_path": rec.get("source_path", ""),
        "page": page,
        "section": rec.get("section", ""),
        "parent_section": "",
        "clause_no": clause_no,
        "clause_type": rec.get("clause_type", "other"),
        "terms": terms,
        "chunk_id": f"{doc_id}::insurance_clause::{page}::{index}",
        "record_type": "insurance_clause",
        "text": text,
        "table_text": "",
        "raw": raw,
        "structured": True,
    }
    _attach_optional_meta(chunk, rec)
    return chunk


def _record_to_text(rec: dict) -> tuple[str, str]:
    """生成结构化 chunk 文本与原文 raw。文本包含标准字段+值，便于 BM25 命中。"""
    rt = rec.get("record_type")
    if rt == "financial_metric":
        parts = [
            f"指标={rec.get('metric', '')}",
            f"年份={rec.get('year', '')}" if rec.get("year") else "",
            f"期间={rec.get('period', '')}" if rec.get("period") else "",
            f"数值={rec.get('value_raw', '')}",
            f"单位={rec.get('unit', '')}" if rec.get("unit") else "",
            f"normalized_value_yuan={rec.get('normalized_value_yuan')}" if rec.get("normalized_value_yuan") is not None else "",
            f"来源页={rec.get('page')}",
        ]
        text = " ".join(p for p in parts if p)
        raw = rec.get("raw_row", "") or text
        return text, raw
    if rt == "regulatory_clause":
        obligations = "、".join(rec.get("obligations", []))
        raw = rec.get("raw", "")
        text = f"{rec.get('article_no', '')} 义务词={obligations} 来源页={rec.get('page')}\n{raw}"
        return text, raw
    if rt in {"insurance_formula", "contract_key", "research_claim"}:
        terms = "、".join(rec.get("terms", []))
        raw = rec.get("raw", "")
        text = f"{rt} 关键词={terms} 来源页={rec.get('page')}\n{raw}"
        return text, raw
    # table_value 等其它
    raw = rec.get("raw_row", "") or str(rec)
    return raw, raw
