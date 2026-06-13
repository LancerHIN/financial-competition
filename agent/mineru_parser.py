from __future__ import annotations

"""MinerU PDF 解析适配层（预处理阶段允许的非 Qwen 工具）。

职责：
  - 调用 MinerU CLI 把 PDF 解析为结构化 content_list.json；
  - 支持目录批处理：一次 CLI 调用解析多份 PDF，模型只加载一次，避免逐文档重启；
  - 把 content_list.json 转成项目统一的 DocumentPage / DocumentBlock；
  - 结果按源文件 mtime/size + 解析配置缓存，避免重复解析；
  - 缓存与输出按 doc_key（stem + 绝对路径 hash）寻址，避免同名 PDF 串文档/缓存冲突。

严格边界：MinerU 仅用于把文档解析为更可读、更结构化的输入（文本/表格/版面/阅读顺序/OCR）。
不在此使用 MinerU 生成摘要、FAQ、结论或答案；正式答题阶段不调用任何非 Qwen 模型。
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from .config import settings


def _mineru_env() -> dict:
    """构造 MinerU 子进程环境变量：注入 GPU 设备模式与显存上限（如配置）。

    device_mode 留空时不强制，交给 MinerU 自动探测（有 CUDA torch 即用 cuda）。
    """
    env = dict(os.environ)
    if settings.mineru_device_mode:
        env["MINERU_DEVICE_MODE"] = settings.mineru_device_mode
    if settings.mineru_virtual_vram:
        env["MINERU_VIRTUAL_VRAM_SIZE"] = str(settings.mineru_virtual_vram)
    return env


# MinerU 是否可用（CLI 在 PATH 上，或 python -m mineru 可用）。
def mineru_available() -> bool:
    if shutil.which("mineru"):
        return True
    try:
        import mineru  # noqa: F401

        return True
    except Exception:
        return False


# ----------------------------------------------------------------------------
# doc_key：MinerU 输出子目录名 / 缓存 key 的唯一标识。
#   = 规范化 stem + 绝对路径 sha1 前 8 位，避免不同目录下同名 PDF 互相覆盖/误复用。
# 解析时把源 PDF 暂存为 {doc_key}.pdf 喂给 MinerU，使输出落在 {out}/{doc_key}/auto/。
# ----------------------------------------------------------------------------

def doc_key(pdf_path: Path) -> str:
    stem = re.sub(r"[^\w.-]", "_", pdf_path.stem) or "doc"
    h = hashlib.sha1(str(pdf_path.resolve()).encode("utf-8")).hexdigest()[:8]
    return f"{stem}_{h}"


def _content_list_path(out_dir: Path, key: str) -> Path:
    """MinerU 输出中该 doc_key 的 content_list.json 精确路径（不再 glob 全目录）。"""
    return out_dir / key / "auto" / f"{key}_content_list.json"


def _cli_base() -> list[str]:
    exe = shutil.which("mineru")
    if exe:
        return [exe]
    return [sys.executable, "-m", "mineru"]


def _run_cli(input_path: Path, out_dir: Path, timeout: int) -> str | None:
    """运行 MinerU CLI，输入可为单个 PDF 或目录。成功返回 None，失败返回 reason。"""
    cmd = [
        *_cli_base(),
        "-p", str(input_path),
        "-o", str(out_dir),
        "-b", settings.mineru_backend,
        "-l", settings.mineru_lang,
        "-m", "auto",
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=_mineru_env(),
        )
    except subprocess.TimeoutExpired:
        return "mineru_timeout"
    except Exception:
        return "mineru_parse_failed"
    if proc.returncode != 0:
        return "mineru_parse_failed"
    return None


def _read_content_list(out_dir: Path, key: str) -> tuple[list[dict] | None, str | None]:
    content_path = _content_list_path(out_dir, key)
    if not content_path.exists():
        return None, "mineru_empty_output"
    try:
        data = json.loads(content_path.read_text(encoding="utf-8"))
    except Exception:
        return None, "mineru_parse_failed"
    if not isinstance(data, list) or not data:
        return None, "mineru_empty_output"
    return data, None


def _cleanup_intermediate(out_dir: Path, key: str) -> None:
    """解析成功后只保留 {key}_content_list.json，删除 layout/span/origin/middle/model/md/images。

    避免 11k 页全量构建后把 processed_data / 提交包撑到数 GB。可用
    MINERU_KEEP_INTERMEDIATE=1 关闭清理（调试用）。
    """
    if settings.mineru_keep_intermediate:
        return
    auto_dir = out_dir / key / "auto"
    if not auto_dir.exists():
        return
    keep = {f"{key}_content_list.json"}
    try:
        for child in auto_dir.iterdir():
            if child.name in keep:
                continue
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                try:
                    child.unlink()
                except Exception:
                    pass
    except Exception:
        pass


def _cleanup_doc_dir(out_dir: Path, key: str) -> None:
    """解析失败/超时时，清掉该 doc_key 可能已生成的 partial 输出目录，避免残留半成品。"""
    if settings.mineru_keep_intermediate:
        return
    doc_dir = out_dir / key
    if doc_dir.exists():
        shutil.rmtree(doc_dir, ignore_errors=True)


# ----------------------------------------------------------------------------
# 缓存：按 doc_key 记录源文件 mtime/size，未变则复用已解析的 content_list.json。
# ----------------------------------------------------------------------------

def _cache_meta_path(out_dir: Path, key: str) -> Path:
    return out_dir / f"{key}.mineru_meta.json"


def _load_cached_content_list(pdf_path: Path, out_dir: Path, key: str) -> list[dict] | None:
    """源文件 mtime/size 未变且 content_list 存在 -> 返回缓存内容；否则 None。"""
    try:
        meta_path = _cache_meta_path(out_dir, key)
        if not meta_path.exists():
            return None
        stat = pdf_path.stat()
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if meta.get("mtime") != int(stat.st_mtime) or meta.get("size") != stat.st_size:
            return None
        data, err = _read_content_list(out_dir, key)
        return data if err is None else None
    except Exception:
        return None


def _save_cache_meta(pdf_path: Path, out_dir: Path, key: str) -> None:
    try:
        stat = pdf_path.stat()
        _cache_meta_path(out_dir, key).write_text(
            json.dumps(
                {"mtime": int(stat.st_mtime), "size": stat.st_size,
                 "source_path": str(pdf_path.resolve())},
                ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception:
        pass


def _staging_dir() -> Path:
    d = settings.mineru_staging_dir
    d.mkdir(parents=True, exist_ok=True)
    return d


def _stage_pdf(pdf_path: Path, key: str, staging: Path) -> Path:
    """把源 PDF 复制到暂存目录并命名为 {key}.pdf，使 MinerU 输出子目录即 doc_key。"""
    target = staging / f"{key}.pdf"
    shutil.copy2(pdf_path, target)
    return target


def _pdf_page_count(pdf_path: Path) -> int:
    """轻量取 PDF 页数（用于分批）。失败返回 0（按小文档处理）。"""
    try:
        import fitz

        with fitz.open(pdf_path) as doc:
            return doc.page_count
    except Exception:
        return 0


def _is_large_pdf(pdf_path: Path) -> bool:
    """巨型 PDF 判据：页数或体积超阈值，需单独成批，避免同批 OOM/超时。"""
    try:
        size_mb = pdf_path.stat().st_size / (1024 * 1024)
    except Exception:
        size_mb = 0.0
    if size_mb >= settings.mineru_large_pdf_mb:
        return True
    if _pdf_page_count(pdf_path) >= settings.mineru_large_pdf_pages:
        return True
    return False


# ----------------------------------------------------------------------------
# 批处理预解析：一次 CLI 调用解析一组未缓存 PDF，模型只加载一次。
# ----------------------------------------------------------------------------

def prepare_mineru_batch(pdf_paths: list[Path], log=None) -> dict[str, str]:
    """对一组 PDF 做批量预解析，命中缓存的跳过，未缓存的分批喂给 MinerU CLI。

    返回 {doc_key_or_path: reason} 的失败映射（成功项不在内）。失败项留待后续逐文件
    run_mineru 兜底重试（或最终回退 native）。纯填充缓存，不返回 content。
    """
    failures: dict[str, str] = {}
    if not (settings.enable_mineru and settings.parser_mode == "mineru_first"):
        return failures
    if not mineru_available():
        return failures
    out_dir = settings.mineru_output_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    uncached: list[Path] = []
    for p in pdf_paths:
        if p.suffix.lower() != ".pdf":
            continue
        key = doc_key(p)
        if _load_cached_content_list(p, out_dir, key) is None:
            uncached.append(p)
    if not uncached:
        return failures

    # 分批：巨型 PDF 单独成批（每批 1 个，超时用 batch_timeout）；小 PDF 按 batch_size 分组。
    large = [p for p in uncached if _is_large_pdf(p)]
    small = [p for p in uncached if p not in set(large)]
    batch_size = settings.mineru_batch_size or len(small) or 1
    groups: list[list[Path]] = [[p] for p in large]
    groups += [small[i:i + batch_size] for i in range(0, len(small), batch_size)]
    if log and large:
        log(f"[mineru] {len(large)} large PDF(s) will be parsed individually")
    for gi, group in enumerate(groups, start=1):
        if log:
            tag = "large" if len(group) == 1 and group[0] in set(large) else "batch"
            log(f"[mineru] {tag} group {gi}/{len(groups)} ({len(group)} pdfs)")
        _run_batch_group(group, out_dir, failures, log)
    return failures


def _run_batch_group(group: list[Path], out_dir: Path, failures: dict[str, str], log=None) -> None:
    staging = _staging_dir()
    # 清空暂存目录，避免上一组残留 PDF 被重复解析
    for old in staging.glob("*.pdf"):
        try:
            old.unlink()
        except Exception:
            pass
    key_to_path: dict[str, Path] = {}
    for p in group:
        key = doc_key(p)
        try:
            _stage_pdf(p, key, staging)
            key_to_path[key] = p
        except Exception:
            failures[str(p)] = "mineru_stage_failed"

    if not key_to_path:
        return

    # 单文档组（巨型 PDF）用单文件超时上限；多文档批用批超时上限。
    timeout = settings.mineru_timeout if len(key_to_path) == 1 else settings.mineru_batch_timeout
    err = _run_cli(staging, out_dir, timeout)
    # 不论 CLI 整体返回如何，逐 doc_key 检查输出：部分成功也要落缓存
    for key, p in key_to_path.items():
        data, read_err = _read_content_list(out_dir, key)
        if read_err is None and data:
            _save_cache_meta(p, out_dir, key)
            _cleanup_intermediate(out_dir, key)
        else:
            failures[key] = err or read_err or "mineru_empty_output"
            # 失败/超时：清掉该 doc_key 可能已生成的 partial 输出目录
            _cleanup_doc_dir(out_dir, key)

    # 清理暂存 PDF
    for key in key_to_path:
        staged = staging / f"{key}.pdf"
        try:
            staged.unlink()
        except Exception:
            pass


def run_mineru(pdf_path: Path) -> tuple[list[dict] | None, str | None]:
    """单文件解析：优先命中缓存（含 prepare_mineru_batch 预填充的）；未命中则单独跑一次。

    成功：返回 (list, None)。失败：返回 (None, reason)。reason 取值：
      mineru_unavailable / mineru_parse_failed / mineru_timeout / mineru_empty_output。
    """
    if not mineru_available():
        return None, "mineru_unavailable"
    out_dir = settings.mineru_output_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    key = doc_key(pdf_path)

    cached = _load_cached_content_list(pdf_path, out_dir, key)
    if cached is not None:
        return cached, None

    staging = _staging_dir()
    staged = staging / f"{key}.pdf"
    try:
        _stage_pdf(pdf_path, key, staging)
    except Exception:
        return None, "mineru_parse_failed"
    try:
        err = _run_cli(staged, out_dir, settings.mineru_timeout)
        if err is not None:
            _cleanup_doc_dir(out_dir, key)
            return None, err
        data, read_err = _read_content_list(out_dir, key)
        if read_err is not None:
            _cleanup_doc_dir(out_dir, key)
            return None, read_err
        _save_cache_meta(pdf_path, out_dir, key)
        _cleanup_intermediate(out_dir, key)
        return data, None
    finally:
        try:
            staged.unlink()
        except Exception:
            pass


# ----------------------------------------------------------------------------
# content_list.json -> 统一 blocks / pages（严格保持 MinerU 原始阅读顺序）
# ----------------------------------------------------------------------------

_CLAUSE_NO_RE = re.compile(r"^第[一二三四五六七八九十百零〇0-9]+条")
_NUMBERED_RE = re.compile(r"^\d+(?:\.\d+){0,3}[、.\s]")


def _table_html_to_text(html: str) -> str:
    """把 MinerU 的表格 HTML 转成 ' | ' 分隔的逐行文本（供 BM25 / table_extractor 消费）。"""
    if not html:
        return ""
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "lxml")
        lines: list[str] = []
        for tr in soup.find_all("tr"):
            cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
            cells = [c for c in cells if c]
            if cells:
                lines.append(" | ".join(cells))
        return "\n".join(lines)
    except Exception:
        # 退化：剥掉标签
        text = re.sub(r"<[^>]+>", " ", html)
        return re.sub(r"\s+", " ", text).strip()


def _table_html_to_grid(html: str) -> list[list[str]]:
    """把表格 HTML 转成二维 list，供 table_extractor.table_to_structured_records 消费。"""
    if not html:
        return []
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "lxml")
        grid: list[list[str]] = []
        for tr in soup.find_all("tr"):
            row = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
            if any(cell for cell in row):
                grid.append(row)
        return grid
    except Exception:
        return []


def _block_type_for(item: dict) -> str:
    t = item.get("type", "")
    if t == "table":
        return "table"
    if t in {"equation", "interline_equation"}:
        return "formula"
    if t == "image":
        return "figure_text"
    if t in {"header"}:
        return "footnote"  # 页眉页脚类，归到非正文
    if t == "list" or item.get("text_level") == 0 and _NUMBERED_RE.match(str(item.get("text", ""))):
        return "list_item"
    text = str(item.get("text", "")).strip()
    if item.get("text_level"):
        return "title"
    if _CLAUSE_NO_RE.match(text) or _NUMBERED_RE.match(text):
        return "clause"
    return "paragraph"


def _clause_no_of(text: str) -> str:
    m = re.match(r"第[一二三四五六七八九十百零〇0-9]+条", text)
    if m:
        return m.group(0)
    m = re.match(r"\d+(?:\.\d+){0,3}", text)
    return m.group(0) if m else ""


def content_list_to_pages(
    content_list: list[dict], doc_id: str, domain: str, source_path: str
) -> tuple[list[dict], list[dict]]:
    """把 MinerU content_list 转成 (pages, blocks)，严格保持原始阅读顺序。

    - 按 page_idx 聚合；page text 按 content_list 出现顺序原样拼接（表格/图注就地内联，
      不再移动到页尾），保证阅读顺序与版面一致。
    - 表格额外汇总到 table_text，并保留 grid 供 table_extractor 生成 financial_metric。
    - page_number（纯页码）跳过；其余文本类（含 header/footnote）按原序保留，不丢内容。
    """
    pages_map: dict[int, dict] = {}
    blocks: list[dict] = []
    section_state = {"title": "", "parent": ""}
    block_counter = 0

    for item in content_list:
        itype = item.get("type", "")
        if itype == "page_number":
            continue
        page_idx = int(item.get("page_idx", 0)) + 1
        page = pages_map.setdefault(
            page_idx,
            {"ordered": [], "tables": [], "grids": [], "blocks": []},
        )
        block_type = _block_type_for(item)

        if block_type == "table":
            html = item.get("table_body", "") or ""
            table_text = _table_html_to_text(html)
            grid = _table_html_to_grid(html)
            caption = " ".join(item.get("table_caption", []) or [])
            footnote = " ".join(item.get("table_footnote", []) or [])
            text_for_block = "\n".join(p for p in [caption, table_text, footnote] if p)
            if table_text:
                page["tables"].append(table_text)
            if grid:
                page["grids"].append(grid)
        elif block_type == "figure_text":
            caption = " ".join(item.get("image_caption", []) or [])
            footnote = " ".join(item.get("image_footnote", []) or [])
            text_for_block = "\n".join(p for p in [caption, footnote] if p)
        else:
            text_for_block = str(item.get("text", "")).strip()
            if block_type == "title" and text_for_block:
                section_state["parent"] = section_state["title"]
                section_state["title"] = text_for_block[:80]

        if not text_for_block:
            continue
        # 就地按原始阅读顺序追加到页文本
        page["ordered"].append(text_for_block)

        block_counter += 1
        clause_no = _clause_no_of(text_for_block) if block_type in {"clause", "list_item", "title"} else ""
        block = {
            "doc_id": doc_id,
            "domain": domain,
            "source_path": source_path,
            "page": page_idx,
            "block_id": f"{doc_id}::p{page_idx}::b{block_counter}",
            "block_type": block_type,
            "text": text_for_block,
            "table_text": _table_html_to_text(item.get("table_body", "")) if block_type == "table" else "",
            "structured_records": [],
            "section": section_state["title"],
            "parent_section": section_state["parent"],
            "clause_no": clause_no,
            "bbox": item.get("bbox"),
            "parser_source": "mineru",
            "parser_quality": 0.0,
            "warnings": [],
        }
        blocks.append(block)
        page["blocks"].append(block)  # 按页累积，避免后续按页回扫全部 blocks

    pages: list[dict] = []
    for page_idx in sorted(pages_map):
        agg = pages_map[page_idx]
        text = "\n".join(p for p in agg["ordered"] if p).strip()
        table_text = "\n".join(agg["tables"]).strip()
        pages.append(
            {
                "doc_id": doc_id,
                "domain": domain,
                "source_path": source_path,
                "page": page_idx,
                "text": text,
                "table_text": table_text,
                "blocks": agg["blocks"],
                "table_grids": agg["grids"],
                "structured_records": [],
                "parser_source": "mineru",
                "parser_quality": 0.0,
                "prev_page_tail": "",
                "next_page_head": "",
            }
        )
    return pages, blocks
