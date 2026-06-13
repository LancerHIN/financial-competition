from __future__ import annotations

import json
import threading
from collections import defaultdict
from pathlib import Path
from typing import Any


class TokenUsageTracker:
    def __init__(self, log_path: Path):
        self.log_path = log_path
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_path.write_text("", encoding="utf-8")
        self.by_qid: dict[str, dict[str, int]] = defaultdict(lambda: {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0})
        self.lock = threading.Lock()

    def record(self, qid: str, round_id: int, purpose: str, usage: dict[str, Any] | None) -> None:
        usage = usage or {}
        prompt_tokens = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
        completion_tokens = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
        total_tokens = int(usage.get("total_tokens") or prompt_tokens + completion_tokens)
        row = {
            "qid": qid,
            "round": round_id,
            "purpose": purpose,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
        }
        with self.lock:
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            sums = self.by_qid[qid]
            sums["prompt_tokens"] += prompt_tokens
            sums["completion_tokens"] += completion_tokens
            sums["total_tokens"] += total_tokens

    def get(self, qid: str) -> dict[str, int]:
        with self.lock:
            return dict(self.by_qid.get(qid, {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}))

    def summary(self) -> dict[str, int]:
        total = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        with self.lock:
            for usage in self.by_qid.values():
                for key in total:
                    total[key] += usage[key]
        return total
