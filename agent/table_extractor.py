from __future__ import annotations

import re

from .numeric_checker import parse_amount_to_yuan


CORE_INDICATORS = [
    "营业收入",
    "营业总收入",
    "归属于上市公司股东的净利润",
    "归母净利润",
    "扣非归母净利润",
    "扣除非经常性损益后的净利润",
    "净利润",
    "经营活动产生的现金流量净额",
    "投资活动产生的现金流量净额",
    "筹资活动产生的现金流量净额",
    "现金及现金等价物净增加额",
    "总资产",
    "归属于上市公司股东的净资产",
    "净资产",
    "基本每股收益",
    "稀释每股收益",
    "加权平均净资产收益率",
    "资产负债率",
    "毛利率",
    "研发投入",
    "研发费用",
    "研发投入占营业收入比例",
    "研发人员数量",
    "现金分红",
    "每10股派息",
    "分红比例",
    "股份回购",
]

YEAR_RE = re.compile(r"20[0-3]\d|上年同期|本期金额|上期金额|本期|报告期|上期|本年|上年|同比增减|同比增长|同比")
UNIT_RE = re.compile(r"人民币万元|人民币亿元|人民币元|百万元|亿元|万元|千元|亿|万|元")


# ============================================================================
# 文本行（BM25 检索用，向后兼容）
# ============================================================================

def table_to_lines(table: list[list]) -> list[str]:
    if not table:
        return []
    clean_rows = [[clean_cell(cell) for cell in row] for row in table if any(clean_cell(cell) for cell in row or [])]
    if not clean_rows:
        return []
    header_rows, body_rows = split_header_body(clean_rows)
    column_names = merge_header_rows(header_rows)
    year_columns = detect_year_columns(header_rows or clean_rows[:1])
    lines: list[str] = []
    for row in body_rows:
        structured = row_to_structured_line(column_names, row, year_columns)
        raw = " | ".join(cell for cell in row if cell)
        if structured:
            lines.append(structured)
        elif raw:
            lines.append(f"原始表格行={raw}")
    if not lines:
        lines.extend("原始表格行=" + " | ".join(cell for cell in row if cell) for row in clean_rows)
    return [line for line in lines if line.strip()]


# ============================================================================
# 结构化记录（本地计算/结构化检索用）
# ============================================================================

def table_to_structured_records(table: list[list], doc_id: str, page: int, domain: str, page_text: str = "") -> list[dict]:
    if not table:
        return []
    clean_rows = [[clean_cell(cell) for cell in row] for row in table if any(clean_cell(cell) for cell in row or [])]
    if not clean_rows:
        return []
    header_rows, body_rows = split_header_body(clean_rows)
    column_names = merge_header_rows(header_rows)
    year_columns = detect_year_columns(header_rows or clean_rows[:1])
    # 有序年份/期间标签（用于列错位时的位置对齐）
    ordered_labels = _ordered_period_labels(header_rows)
    default_unit = detect_unit(page_text) or detect_unit("\n".join(" ".join(r) for r in header_rows))
    records: list[dict] = []
    for row in body_rows:
        raw_row = " | ".join(cell for cell in row if cell)
        row_name = first_nonnumeric(row)
        metric = match_indicator(row) or (match_indicator([row_name]) if row_name else "")
        row_unit = detect_unit(" ".join(row)) or detect_unit(row_name) or default_unit
        emitted = False

        # 路径1：严格列对齐（year_columns 列索引与数值列吻合）
        aligned_pairs: list[tuple[str, str, str]] = []  # (label, cell, column_name)
        for col_index, year_label in (year_columns or {}).items():
            if col_index < len(row) and is_numeric(row[col_index]):
                col_name = column_names[col_index] if col_index < len(column_names) else year_label
                aligned_pairs.append((year_label, row[col_index], col_name))

        # 路径2：列错位时按出现顺序对齐（有序标签 vs 有序数值），财报多年份表常见
        if not aligned_pairs and ordered_labels:
            ordered_values = [c for c in row if is_numeric(c)]
            if 1 <= len(ordered_values) <= len(ordered_labels) + 1:
                for label, cell in zip(ordered_labels, ordered_values):
                    aligned_pairs.append((label, cell, label))

        # 金额行的百分比保护：当行单位是绝对金额（元/万元/亿元等）且指标不是比率类时，
        # 百分比单元格是占比列/同比列，不是某年的金额，剔除以免被错配成年度金额。
        row_is_amount = bool(row_unit) and row_unit != "%"
        metric_is_ratio = bool(metric) and _is_ratio_indicator(metric)
        if row_is_amount and not metric_is_ratio:
            aligned_pairs = [
                (label, cell, col_name)
                for (label, cell, col_name) in aligned_pairs
                if "%" not in cell or _is_change_header(label) or _is_change_header(col_name)
            ]

        for label, cell, col_name in aligned_pairs:
            value = parse_number(cell)
            if value is None:
                continue
            is_percent_cell = "%" in cell
            rec_type = "financial_metric" if metric else "table_value"
            cell_unit = "%" if is_percent_cell else (row_unit or "")
            value_yuan = None
            if cell_unit and cell_unit != "%":
                value_yuan = parse_amount_to_yuan(f"{value}{_unit_for_calc(cell_unit)}")
            year = _year_only(label, col_name)
            period = _period_only(label, col_name)
            # 同比/增减列：标为 period=同比，不当作某一年的数值
            if _is_change_header(label) or _is_change_header(col_name):
                year = ""
                period = "同比增减"
            records.append(
                {
                    "record_type": rec_type,
                    "doc_id": doc_id,
                    "domain": domain,
                    "page": page,
                    "metric": metric or row_name,
                    "year": year,
                    "period": period,
                    "value_raw": cell,
                    "value": value,
                    "unit": cell_unit,
                    "normalized_value_yuan": value_yuan,
                    "row_name": row_name,
                    "column_name": col_name,
                    "raw_row": raw_row,
                    "confidence": "high" if (metric and cell_unit and (year or period == "同比增减")) else ("medium" if metric else "low"),
                }
            )
            emitted = True
        if not emitted and metric:
            # 无法列对齐，但确实是核心指标行：保留行级低置信记录
            values = [c for c in row if is_numeric(c)]
            records.append(
                {
                    "record_type": "financial_metric",
                    "doc_id": doc_id,
                    "domain": domain,
                    "page": page,
                    "metric": metric,
                    "year": "",
                    "period": "",
                    "value_raw": values[0] if values else "",
                    "value": parse_number(values[0]) if values else None,
                    "unit": row_unit or "",
                    "normalized_value_yuan": None,
                    "row_name": row_name,
                    "column_name": "",
                    "raw_row": raw_row,
                    "confidence": "low",
                }
            )
    return records


# ============================================================================
# 表头处理
# ============================================================================

def split_header_body(clean_rows: list[list[str]]) -> tuple[list[list[str]], list[list[str]]]:
    """识别前 1~3 行表头（数值占比低的行视为表头），其余为数据行。"""
    header_rows: list[list[str]] = []
    for row in clean_rows[: min(3, len(clean_rows))]:
        numeric = sum(1 for c in row if c and is_numeric(c))
        non_empty = sum(1 for c in row if c)
        if non_empty == 0:
            continue
        # 表头行：数值单元格占比低
        if numeric <= max(0, non_empty // 3) or any(is_year_header(c) for c in row):
            header_rows.append(row)
        else:
            break
    body_rows = clean_rows[len(header_rows):] if header_rows else clean_rows[1:]
    if not header_rows:
        header_rows = [clean_rows[0]]
    return header_rows, body_rows


def merge_header_rows(header_rows: list[list[str]]) -> list[str]:
    """把多级表头按列合并为 column_name（'2025年/本期金额' 这类）。"""
    if not header_rows:
        return []
    width = max(len(r) for r in header_rows)
    columns: list[str] = []
    for col in range(width):
        parts: list[str] = []
        for row in header_rows:
            if col < len(row) and row[col] and row[col] not in parts:
                parts.append(row[col])
        columns.append("/".join(parts))
    return columns


def _ordered_period_labels(header_rows: list[list[str]]) -> list[str]:
    """按出现顺序提取列标签（年份/期间/同比增减），用于列错位时的位置对齐。

    必须包含同比/增减列，否则其下的百分比数值会占用年份槽位导致错位。
    """
    labels: list[str] = []
    for row in header_rows:
        for cell in row:
            cell = cell.strip()
            if not cell:
                continue
            if (is_year_header(cell) or _is_change_header(cell)) and cell not in labels:
                labels.append(cell)
    return labels


def _is_change_header(cell: str) -> bool:
    return bool(re.search(r"增减|增长|比上年|同比|环比", cell))


def detect_year_columns(rows: list[list[str]]) -> dict[int, str]:
    year_columns: dict[int, str] = {}
    for row in rows:
        for col_index, cell in enumerate(row):
            if col_index in year_columns or len(cell) > 14:
                continue
            match = YEAR_RE.search(cell)
            if match and is_year_header(cell):
                year_columns[col_index] = match.group(0)
    return year_columns


def is_year_header(cell: str) -> bool:
    cell = cell.strip()
    if cell in {"本期", "上年同期", "报告期", "上期", "本年", "上年", "本期金额", "上期金额", "同比增减", "同比增长", "同比"}:
        return True
    return bool(re.match(r"^20[0-3]\d\s*年?(?:度|末|\d+[-—]\d+月)?$", cell))


def _year_only(year_label: str, col_name: str) -> str:
    for source in (col_name, year_label):
        m = re.search(r"20[0-3]\d", str(source))
        if m:
            return m.group(0)
    return ""


def _period_only(year_label: str, col_name: str) -> str:
    for source in (col_name, year_label):
        for token in ("本期", "上年同期", "报告期", "上期", "本年", "上年", "同比增减", "同比"):
            if token in str(source):
                return token
    return "报告期" if not _year_only(year_label, col_name) else ""


# ============================================================================
# 行/单元格处理
# ============================================================================

def row_to_structured_line(column_names: list[str], row: list[str], year_columns: dict[int, str] | None = None) -> str:
    metric = match_indicator(row) or match_indicator(column_names)
    if not metric:
        return ""
    unit = first_match(UNIT_RE, column_names + row)
    parts = [f"指标={metric}"]
    aligned = []
    if year_columns:
        for col_index, year in year_columns.items():
            if col_index < len(row) and is_numeric(row[col_index]):
                label = column_names[col_index] if col_index < len(column_names) and column_names[col_index] else year
                aligned.append((label, row[col_index]))
    if aligned:
        for label, value in aligned[:4]:
            parts.append(f"列={label} 数值={value}")
        parts.append("对齐=列对齐")
    else:
        years = [cell for cell in column_names + row if YEAR_RE.search(cell)]
        values = [cell for cell in row if is_numeric(cell)]
        if not values:
            return ""
        if years:
            parts.append("年份=" + "/".join(dict.fromkeys(years[:4])))
        for index, value in enumerate(values[:6], start=1):
            label = column_names[index] if index < len(column_names) and column_names[index] else f"数值{index}"
            parts.append(f"{label}={value}")
        parts.append("对齐=未对齐请核对原始行")
    if unit:
        parts.append(f"单位={unit}")
    parts.append("原始表格行=" + " | ".join(cell for cell in row if cell))
    return " ".join(parts)


def is_numeric(cell: str) -> bool:
    text = str(cell).strip().replace(" ", "")
    return bool(re.search(r"[-+]?\d[\d,]*(?:\.\d+)?%?", text))


def parse_number(cell: str) -> float | None:
    """把表格单元格转为数值。支持千分位、括号负数、百分号（保留为数值本身）。"""
    text = str(cell).strip().replace(",", "").replace(" ", "")
    neg = text.startswith("(") and text.endswith(")")
    text = text.strip("()").rstrip("%")
    m = re.search(r"-?\d+(?:\.\d+)?", text)
    if not m:
        return None
    value = float(m.group(0))
    return -value if neg else value


def first_nonnumeric(row: list[str]) -> str:
    for cell in row:
        if cell and not is_numeric(cell):
            return cell
    return ""


def match_indicator(cells: list[str]) -> str:
    """仅当某单元格以核心指标为主才认定为指标行，避免长句误命中。

    - 最长指标优先：避免 "加权平均净资产收益率" 被子串 "净资产" 抢先命中。
    - 比率行保护：当单元格表达的是占比/比例/比率（"占比"、"占…比例"、"…率"）且
      其本身不是某个比率类核心指标时，不归并到绝对金额指标（如
      "研发投入占营业收入比例" 不应被当作 "营业收入"，"研发人员数量占比" 不应被当作
      "研发人员数量"），避免百分比值被错配成绝对金额。
    """
    indicators_by_len = sorted(CORE_INDICATORS, key=len, reverse=True)
    for cell in cells:
        text = str(cell).strip()
        for indicator in indicators_by_len:
            if indicator not in text:
                continue
            remainder = text.replace(indicator, "")
            non_filler = re.sub(r"[\s（）()：:、,，本期上年同期报告期合计%元万亿人民币\d\.\-]+", "", remainder)
            if not (len(text) <= 30 or len(non_filler) <= 12):
                continue
            # 比率行保护：指标本身不是比率（不以率结尾、不含"占…比例"），但单元格表达比率语义，
            # 则不认定为该绝对金额指标，避免占比/同比百分比被错配成年度金额。
            if not _is_ratio_indicator(indicator) and _is_ratio_text(text, indicator):
                continue
            return indicator
    return ""


def _is_ratio_indicator(indicator: str) -> bool:
    """核心指标本身是否为比率类（以…率结尾，或显式含占比/比例）。"""
    return indicator.endswith("率") or "占比" in indicator or "比例" in indicator


def _is_ratio_text(text: str, indicator: str) -> bool:
    """单元格文本（剔除指标名后）是否带占比/比例/比率语义。"""
    remainder = text.replace(indicator, "")
    return bool(re.search(r"占比|比例|比率|占.*比|收益率|增长率|负债率", remainder)) or (
        "率" in remainder and "率" not in indicator
    )



def detect_unit(text: str) -> str:
    """从标题/表头/行内文本识别单位，统一为标准写法。"""
    if not text:
        return ""
    # 优先匹配 "单位：万元" / "单位:人民币万元"
    m = re.search(r"单位\s*[:：]\s*(人民币)?\s*(百万元|亿元|万元|千元|元)", text)
    if m:
        return (m.group(2))
    m = UNIT_RE.search(text)
    if m:
        token = m.group(0).replace("人民币", "")
        return token or "元"
    return ""


def _unit_for_calc(unit: str) -> str:
    """把识别到的单位映射为 parse_amount_to_yuan 能识别的后缀。"""
    unit = unit.replace("人民币", "")
    if unit in {"亿", "亿元"}:
        return "亿元"
    if unit in {"万", "万元"}:
        return "万元"
    if unit == "千元":
        return "千元"
    if unit == "百万元":
        return "百万元"
    return "元"


def first_match(pattern: re.Pattern, cells: list[str]) -> str:
    for cell in cells:
        match = pattern.search(cell)
        if match:
            return match.group(0)
    return ""


def clean_cell(cell) -> str:
    text = str(cell or "").replace("\n", " ").strip()
    text = re.sub(r"\s+", " ", text)
    return text
