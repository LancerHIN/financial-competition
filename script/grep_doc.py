"""分歧题核查工具：在 parsed_pages.jsonl 全文里按 doc_id + 关键词检索原文片段。

用法:
  python script/grep_doc.py <doc_id> <关键词1> [关键词2 ...] [--width N] [--max M]

示例:
  python script/grep_doc.py text03 董事 真实性 承担
  python script/grep_doc.py annual_chinamobile_2025_report 研发费用 --max 8

说明：
  - 任一关键词命中即返回该页命中处上下文（OR 语义）；多关键词同页会合并。
  - width 控制命中词左右上下文字符数（默认 80），max 控制最多返回片段数（默认 12）。
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAGES = ROOT / "processed_data" / "parsed_pages.jsonl"


def main() -> None:
    args = [a for a in sys.argv[1:]]
    width = 80
    max_hits = 12
    if "--width" in args:
        i = args.index("--width")
        width = int(args[i + 1])
        del args[i : i + 2]
    if "--max" in args:
        i = args.index("--max")
        max_hits = int(args[i + 1])
        del args[i : i + 2]
    if len(args) < 2:
        print("usage: python script/grep_doc.py <doc_id> <kw1> [kw2 ...] [--width N] [--max M]")
        return
    doc_id = args[0]
    keywords = args[1:]

    pages = defaultdict(list)
    for line in PAGES.open(encoding="utf-8"):
        o = json.loads(line)
        if o["doc_id"] == doc_id:
            pages[o["page"]].append(o["text"])

    if not pages:
        print(f"[no such doc_id] {doc_id}")
        return

    hits = 0
    for page in sorted(pages):
        text = "\n".join(pages[page])
        for kw in keywords:
            start = 0
            while True:
                idx = text.find(kw, start)
                if idx < 0:
                    break
                lo = max(0, idx - width)
                hi = min(len(text), idx + len(kw) + width)
                snippet = text[lo:hi].replace("\n", " ")
                print(f"[p{page}|{kw}] ...{snippet}...")
                hits += 1
                start = idx + len(kw)
                if hits >= max_hits:
                    print(f"... (truncated at {max_hits} hits)")
                    return
    if hits == 0:
        print(f"[no hits] doc={doc_id} keywords={keywords}")


if __name__ == "__main__":
    main()
