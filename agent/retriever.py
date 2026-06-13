from __future__ import annotations

from .bm25_index import BM25Index, tokenize

try:
    from rank_bm25 import BM25Okapi
except Exception:  # noqa: BLE001
    BM25Okapi = None


class Retriever:
    def __init__(self, index: BM25Index):
        self.index = index
        self._doc_index = None
        self._doc_ids: list[str] = []
        self._doc_domains: list = []

    def retrieve(self, query: str, domain: str | None = None, doc_ids: list[str] | None = None, top_k: int = 10) -> list[dict]:
        return self.index.search(query=query, domain=domain, doc_ids=doc_ids, top_k=top_k)

    def retrieve_many(self, queries: list[str], domain: str | None = None, doc_ids: list[str] | None = None, top_k: int = 10) -> list[dict]:
        seen = set()
        merged = []
        for query in queries:
            for item in self.retrieve(query, domain=domain, doc_ids=doc_ids, top_k=top_k):
                key = item.get("chunk_id")
                if key in seen:
                    continue
                seen.add(key)
                merged.append(item)
        merged.sort(key=lambda item: item.get("score", 0), reverse=True)
        return merged[:top_k]

    def retrieve_option_wise(
        self,
        question: str,
        options: dict[str, str],
        extra_queries: list[str] | None = None,
        domain: str | None = None,
        doc_ids: list[str] | None = None,
        global_top_k: int = 8,
        option_top_k: int = 4,
        final_top_k: int = 24,
        min_per_option: int = 2,
        force_option_wise: bool = False,
    ) -> list[dict]:
        merged: dict[str, dict] = {}
        global_queries = [question, question + " " + " ".join(options.values())]
        for query in global_queries:
            self._merge_results(merged, self.retrieve(query, domain=domain, doc_ids=doc_ids, top_k=global_top_k), query, "global", "global")
        for query in extra_queries or []:
            if str(query).strip():
                self._merge_results(merged, self.retrieve(query, domain=domain, doc_ids=doc_ids, top_k=global_top_k), query, "global", "extra")
        option_buckets: dict[str, list[str]] = {}
        for letter, option_text in sorted(options.items()):
            option_queries = [question + " " + option_text, option_text, " ".join(extract_query_terms(option_text))]
            for query in option_queries:
                if query.strip():
                    keys = self._merge_results(merged, self.retrieve(query, domain=domain, doc_ids=doc_ids, top_k=option_top_k), query, letter, "option")
                    option_buckets.setdefault(letter, []).extend(keys)
        for item in merged.values():
            item["rerank_score"] = _rerank_score(item)
        ranked = sorted(merged.values(), key=lambda item: item.get("rerank_score", 0), reverse=True)
        selected: dict[str, dict] = {}
        for letter in sorted(options):
            bucket = sorted(
                (merged[key] for key in dict.fromkeys(option_buckets.get(letter, [])) if key in merged),
                key=lambda item: item.get("rerank_score", 0),
                reverse=True,
            )
            for item in bucket[:min_per_option]:
                selected[_item_key(item)] = item
        for item in ranked:
            if len(selected) >= final_top_k:
                break
            selected.setdefault(_item_key(item), item)
        return sorted(selected.values(), key=lambda item: item.get("rerank_score", 0), reverse=True)[:final_top_k]

    @staticmethod
    def _merge_results(merged: dict[str, dict], results: list[dict], source_query: str, source_option: str, channel: str) -> list[str]:
        keys = []
        for item in results:
            key = item.get("chunk_id") or f"{item.get('doc_id')}::{item.get('page')}::{item.get('text', '')[:40]}"
            keys.append(key)
            if key not in merged:
                new_item = dict(item)
                new_item["source_query"] = source_query
                new_item["source_option"] = source_option
                new_item["source_options"] = [] if source_option == "global" else [source_option]
                new_item["retrieval_channel"] = channel
                merged[key] = new_item
                continue
            existing = merged[key]
            existing["score"] = max(float(existing.get("score", 0) or 0), float(item.get("score", 0) or 0))
            if source_option != "global" and source_option not in existing.setdefault("source_options", []):
                existing["source_options"].append(source_option)
            if source_option != "global":
                existing["source_option"] = source_option
        return keys


    def retrieve_candidate_docs(self, query: str, domain: str | None = None, top_k: int = 5) -> list[str]:
        if BM25Okapi is None:
            return []
        if self._doc_index is None:
            self._build_doc_index()
        if not self._doc_index or not self._doc_ids:
            return []
        scores = self._doc_index.get_scores(tokenize(query))
        ranked = sorted(zip(self._doc_ids, scores, self._doc_domains), key=lambda item: item[1], reverse=True)
        result = []
        for doc_id, _score, doc_domain in ranked:
            if domain and doc_domain and doc_domain != domain:
                continue
            result.append(doc_id)
            if len(result) >= top_k:
                break
        return result

    def _build_doc_index(self) -> None:
        docs = _build_doc_corpus(getattr(self.index, "chunks", []) or [])
        self._doc_ids = list(docs.keys())
        self._doc_domains = [docs[doc_id].get("domain") for doc_id in self._doc_ids]
        corpus = [tokenize(docs[doc_id]["doc_text"]) for doc_id in self._doc_ids]
        self._doc_index = BM25Okapi(corpus) if corpus and BM25Okapi is not None else None


def _item_key(item: dict) -> str:
    return item.get("chunk_id") or f"{item.get('doc_id')}::{item.get('page')}::{item.get('text', '')[:40]}"


def _rerank_score(item: dict) -> float:
    import re

    score = float(item.get("score", 0) or 0)
    option_bonus = 2.0 if item.get("source_options") else 0.0
    multi_hit_bonus = 1.5 * max(0, len(item.get("source_options", [])) - 1)
    text = str(item.get("text", ""))
    number_bonus = 1.5 if re.search(r"\d{4}年|\d+(?:\.\d+)?%|\d+(?:\.\d+)?[万亿]?元|第[一二三四五六七八九十百零〇]+条", text) else 0.0
    return score + option_bonus + multi_hit_bonus + number_bonus


def _build_doc_corpus(chunks: list[dict]) -> dict[str, dict]:
    docs: dict[str, dict] = {}
    for chunk in chunks:
        doc_id = chunk.get("doc_id", "")
        if not doc_id:
            continue
        entry = docs.setdefault(doc_id, {"doc_id": doc_id, "domain": chunk.get("domain"), "sections": [], "text_parts": []})
        section = str(chunk.get("section", "")).strip()
        if section and section not in entry["sections"]:
            entry["sections"].append(section)
        if len(" ".join(entry["text_parts"])) < 2000:
            entry["text_parts"].append(str(chunk.get("text", ""))[:400])
    for entry in docs.values():
        entry["doc_text"] = f"{entry['doc_id']} {' '.join(entry['sections'][:20])} {' '.join(entry['text_parts'])}"
    return docs


def extract_query_terms(text: str) -> list[str]:
    import re

    terms = re.findall(r"\d{4}年|\d+(?:\.\d+)?%|\d+(?:\.\d+)?[万亿]?元|第[一二三四五六七八九十百零〇]+条|[\u4e00-\u9fff]{2,}", text)
    stop = {"下列", "正确", "错误", "关于", "根据", "以下", "选项"}
    return [term for term in terms if term not in stop][:12]
