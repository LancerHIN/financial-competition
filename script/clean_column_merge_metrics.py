"""一次性清洗：剔除已构建语料中的 OCR 列粘连财报指标 chunk（normalized_value_yuan 被算成
天文数字，污染跨文档数值比较），并重建 BM25 索引。不重跑 MinerU，仅基于已生成的
chunks.jsonl / structured_chunks.jsonl。

判定与 agent.structured_records._is_column_merge_value 一致：只剔除“两个完整数字组被空格
隔开”的财报指标 chunk，放过合法的小数点空格 / 千分位空格。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.bm25_index import BM25Index  # noqa: E402
from agent.config import settings  # noqa: E402
from agent.structured_records import _is_column_merge_value  # noqa: E402

_VALUE_RE = re.compile(r"数值=([0-9,\.\s]+?)\s*单位")


def _is_corrupt_metric_chunk(chunk: dict) -> bool:
    if chunk.get("record_type") != "financial_metric":
        return False
    m = _VALUE_RE.search(str(chunk.get("text", "")))
    if not m:
        return False
    return _is_column_merge_value(m.group(1))


def _filter_jsonl(path: Path) -> tuple[list[dict], int]:
    kept: list[dict] = []
    dropped = 0
    for line in path.open(encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        chunk = json.loads(line)
        if _is_corrupt_metric_chunk(chunk):
            dropped += 1
            continue
        kept.append(chunk)
    return kept, dropped


def main() -> None:
    all_chunks, d1 = _filter_jsonl(settings.chunks_path)
    struct_chunks, d2 = _filter_jsonl(settings.structured_chunks_path)
    print(f"chunks.jsonl: kept={len(all_chunks)} dropped={d1}")
    print(f"structured_chunks.jsonl: kept={len(struct_chunks)} dropped={d2}")

    with settings.chunks_path.open("w", encoding="utf-8") as handle:
        for chunk in all_chunks:
            handle.write(json.dumps(chunk, ensure_ascii=False) + "\n")
    with settings.structured_chunks_path.open("w", encoding="utf-8") as handle:
        for chunk in struct_chunks:
            handle.write(json.dumps(chunk, ensure_ascii=False) + "\n")

    index = BM25Index(all_chunks)
    index.save(settings.index_path)
    print(f"rebuilt BM25 index over {len(all_chunks)} chunks -> {settings.index_path}")


if __name__ == "__main__":
    main()
