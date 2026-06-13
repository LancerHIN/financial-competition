from __future__ import annotations

import copy
import heapq
import pickle
import re
from pathlib import Path

import jieba
from rank_bm25 import BM25Okapi


def tokenize(text: str) -> list[str]:
    tokens = [token.strip().lower() for token in jieba.lcut(text) if token.strip()]
    tokens.extend(re.findall(r"[A-Za-z0-9_]+|\d+(?:\.\d+)?%?", text.lower()))
    return tokens


class BM25Index:
    def __init__(self, chunks: list[dict]):
        self.chunks = chunks
        self.doc_id_map = build_doc_id_map(chunks)
        self.corpus = [tokenize(chunk.get("text", "")) for chunk in chunks]
        self.bm25 = BM25Okapi(self.corpus) if self.corpus else None

    def search(self, query: str, top_k: int = 10, domain: str | None = None, doc_ids: list[str] | None = None) -> list[dict]:
        # nlargest 选 top_k 下标（O(N log K)），只对入选 chunk 深拷贝，避免对全部命中 chunk 深拷贝
        return self.materialize(self.rank_query(query, top_k, domain, doc_ids))

    def candidate_indices(self, query: str, top_k: int, domain: str | None = None, doc_ids: list[str] | None = None) -> list[int]:
        """返回 BM25 候选 chunk 的全局下标（按分数降序），供本地词法路由限定打分范围。

        只做一次廉价的 BM25 打分 + 过滤，不做深拷贝，用于避免 ngram/tfidf/rule 路由对全量 chunk 评分。
        只保留正分命中，避免 OOV/纯符号查询拿到无关的前缀 chunk。
        """
        return [idx for idx, score in self.rank_query(query, top_k, domain, doc_ids) if score > 0]

    def rank_query(self, query: str, top_k: int, domain: str | None = None, doc_ids: list[str] | None = None) -> list[tuple[int, float]]:
        """对单个 query 打分一次，返回 top_k 的 (全局下标, 分数)，按分数降序。

        供混合检索复用：同一 query 只调用一次 BM25 get_scores，既派生 BM25 命中结果，
        又派生本地路由候选池，避免对同一 query 全量打分两次。
        """
        if not self.bm25 or not self.chunks:
            return []
        allowed_doc_ids = self.resolve_doc_ids(doc_ids or []) if doc_ids else None
        scores = self.bm25.get_scores(tokenize(query))
        candidates: list[tuple[int, float]] = []
        for index, score in enumerate(scores):
            chunk = self.chunks[index]
            if domain and chunk.get("domain") != domain:
                continue
            if allowed_doc_ids is not None and chunk.get("doc_id") not in allowed_doc_ids:
                continue
            candidates.append((index, float(score)))
        return heapq.nlargest(top_k, candidates, key=lambda kv: kv[1])

    def materialize(self, ranked: list[tuple[int, float]]) -> list[dict]:
        """把 (下标, 分数) 列表物化为深拷贝 chunk 结果，供调用方原地改写 routes/source_options。"""
        results = []
        for index, score in ranked:
            item = copy.deepcopy(self.chunks[index])
            item["score"] = score
            results.append(item)
        return results

    def resolve_doc_ids(self, doc_ids: list[str]) -> set[str]:
        """把题目给的 doc_id 解析为索引内实际的 doc_id 集合。

        策略（避免误召回无关文档）：
          1. 先做归一化 key 的精确匹配；精确命中即返回，不再做子串扩张。
          2. 仅当精确无命中时，才退化为子串匹配；并跳过空 key / 过短 key（如纯数字
             "1"、"8"），避免短 key 命中大量无关文档。
        """
        resolved: set[str] = set()
        for doc_id in doc_ids:
            key = normalize_key(doc_id)
            if not key:
                continue
            exact = self.doc_id_map.get(key)
            if exact:
                resolved.update(exact)
                continue
            # 精确无命中：子串 fallback（双向），跳过空/过短 key 防污染
            if len(key) < 3:
                continue
            for known_key, known_ids in self.doc_id_map.items():
                if not known_key or len(known_key) < 3:
                    continue
                if key in known_key or known_key in key:
                    resolved.update(known_ids)
        return resolved

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as handle:
            pickle.dump(self, handle)

    @classmethod
    def load(cls, path: Path) -> "BM25Index":
        with path.open("rb") as handle:
            return pickle.load(handle)


def build_doc_id_map(chunks: list[dict]) -> dict[str, set[str]]:
    mapping: dict[str, set[str]] = {}
    for chunk in chunks:
        doc_id = chunk.get("doc_id", "")
        source_stem = Path(chunk.get("source_path", "")).stem
        # 跳过空 key：结构化 chunk（如 financial_metric）可能没有 source_path，
        # 空 key 会在 resolve 时污染所有查询，必须排除。
        keys = {k for k in (normalize_key(doc_id), normalize_key(source_stem)) if k}
        for key in keys:
            mapping.setdefault(key, set()).add(doc_id)
    return mapping


def normalize_key(value: str) -> str:
    """把 doc_id / 文件名归一化为匹配 key。

    - 去掉 strict_ / strict_v3_ 等内部前缀与 annual_ 前缀，对齐题目给的 doc_id；
    - 保留 attN 附件编号（att1 / att2 不可混淆，否则同一 csrc 下多个附件互相误召回）；
    - 数字去前导零（csrc_0038 与 csrc_38 视为同一）。
    """
    value = Path(str(value)).stem
    value = value.lower()
    value = re.sub(r"^strict_(v\d+_)?", "", value)
    value = value.replace("annual_", "")
    value = re.sub(r"\d+", lambda match: str(int(match.group(0))), value)
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", value)
