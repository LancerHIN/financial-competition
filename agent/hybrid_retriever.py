from __future__ import annotations

import copy
import logging
import math
import re
import threading
import time
from collections import Counter

from .bm25_index import BM25Index, tokenize
from .domain_rules import FIELD_WEIGHTS, GENERAL_RULE_KEYWORDS, get_domain_rules
from .retriever import Retriever, extract_query_terms


def char_ngrams(text: str, sizes=(2, 3)) -> list[str]:
    cleaned = re.sub(r"\s+", "", text.lower())
    grams: list[str] = []
    for size in sizes:
        if len(cleaned) >= size:
            grams.extend(cleaned[i : i + size] for i in range(len(cleaned) - size + 1))
    return grams


class HybridRetriever(Retriever):
    """金融特化多路混合检索 + RRF 融合，复用 BM25Index，不引入任何非 Qwen 向量模型。"""

    def __init__(self, index: BM25Index, rrf_k: int = 60):
        super().__init__(index)
        self.rrf_k = rrf_k
        self._ngram_corpus: list[Counter] | None = None
        self._field_corpus: list[dict] | None = None
        self._df: Counter | None = None
        # 并发安全：语料只构建一次，避免 10 线程开局各自重复构建 10.9 万 chunk 的语料
        self._ngram_lock = threading.Lock()
        self._field_lock = threading.Lock()

    def warmup(self) -> None:
        """提前构建检索语料，供多线程检索前调用一次。

        只是把懒加载提前到单线程阶段完成，不改变任何检索结果。
        优化：ngram 语料体积大、构建慢，只有当 ngram route 真的会跑（NGRAM_ROUTE_MODE=always）
        时才预构建；fallback 模式下 ngram 仅在 BM25 全程空命中时才懒加载，启动无需为它买单。
        可用 RETRIEVER_WARMUP_ENABLED=0 完全跳过 warmup。
        """
        from .config import settings

        if not getattr(settings, "retriever_warmup_enabled", True):
            return
        if getattr(settings, "ngram_route_mode", "fallback") == "always":
            self._ensure_ngram_corpus()
        self._ensure_field_corpus()

    def retrieve_candidate_docs(self, query: str, domain: str | None = None, top_k: int = 5) -> list[str]:
        return super().retrieve_candidate_docs(query, domain=domain, top_k=top_k)

    # ---- 主入口：多路召回 + RRF + 规则重排 ----
    def hybrid_search(
        self,
        query: str,
        sub_queries: list[str] | None = None,
        domain: str | None = None,
        doc_ids: list[str] | None = None,
        per_route_top_k: int = 20,
        fused_top_k: int = 20,
        final_top_k: int = 10,
    ) -> list[dict]:
        queries = [query, *(sub_queries or [])]
        route_rankings: list[list[str]] = []
        key_to_item: dict[str, dict] = {}

        from .config import settings

        timing_enabled = getattr(settings, "retrieval_timing_log_enabled", False)
        start = time.perf_counter() if timing_enabled else 0.0

        candidate_only = getattr(settings, "candidate_only_scoring", True)
        active_queries = [q for q in queries if str(q).strip()]
        # 优先级5: 只取前 N 条高质量 query 参与多路检索，避免原题+扩展+选项题全部完整跑 hybrid。
        #   主 query 一定保留；sub_queries 按给定顺序取前缀。去重后再截断。
        max_active = max(1, getattr(settings, "max_active_queries", 3))
        active_queries = _dedupe_keep_order(active_queries)[:max_active]

        # 每个 query 只对 BM25 打分一次（rank_query），同时派生：
        #   1) BM25 / BM25F route 命中（取前 per_route_top_k）
        #   2) 本地词法路由（ngram/tfidf/rule）的候选池（取正分下标）
        # 杜绝同一 query 被 candidate_indices + index.search 全量打分两次。
        cap = max(1, getattr(settings, "candidate_pool_size", getattr(settings, "hybrid_candidate_top_k", 500)))
        per_query = max(per_route_top_k, cap // max(1, len(active_queries))) if candidate_only else per_route_top_k
        pool: dict[int, None] = {}

        bm25_hit_total = 0
        for q in active_queries:
            ranked = self.index.rank_query(q, top_k=per_query, domain=domain, doc_ids=doc_ids)
            bm25_results = self.index.materialize(ranked[:per_route_top_k])
            route_rankings.append(self._bm25_route_from_results(q, bm25_results, key_to_item, field_weighted=False))
            route_rankings.append(self._bm25_route_from_results(q, bm25_results, key_to_item, field_weighted=True))
            if candidate_only:
                for idx, score in ranked:
                    if score > 0:
                        bm25_hit_total += 1
                        if idx not in pool:
                            pool[idx] = None

        candidate_idx: list[int] | None
        if candidate_only:
            candidate_idx = list(pool.keys())[:cap] if pool else self._fallback_candidate_idx(domain, doc_ids, cap)
            # item16：A 榜给定 doc_ids 时，把这些 doc 内的结构化 chunk（record_type 命中，如条款/指标表行）
            #   补进候选池，避免 BM25 正分命中池漏掉它们导致 rule/structured route 打不到分。
            #   仅在 doc_ids 限定范围内放宽，不做全库扩张。
            if doc_ids:
                candidate_idx = self._augment_structured_candidates(candidate_idx, domain, doc_ids)
        else:
            candidate_idx = None  # 关闭候选池：本地路由回退全库扫描

        # 优先级1: ngram route 模式。
        #   off = 不跑；always = 每个 query 都跑；fallback（默认）= 仅当 BM25 全程无正分命中时才跑（救 OOV/别名/错别字）。
        ngram_mode = getattr(settings, "ngram_route_mode", "fallback")
        run_ngram = ngram_mode == "always" or (ngram_mode == "fallback" and bm25_hit_total == 0)

        for q in active_queries:
            if run_ngram:
                route_rankings.append(self._ngram_route(q, candidate_idx, per_route_top_k, key_to_item))
            route_rankings.append(self._tfidf_route(q, candidate_idx, per_route_top_k, key_to_item))
        route_rankings.append(self._rule_route(query + " " + " ".join(sub_queries or []), domain, candidate_idx, per_route_top_k, key_to_item))

        fused = self._rrf_fuse(route_rankings, key_to_item)
        fused = fused[:fused_top_k]
        reranked = self._rule_rerank(query, sub_queries or [], domain, fused)
        if timing_enabled:
            logging.info(
                "hybrid_search queries=%d ngram=%s candidates=%s fused=%d elapsed=%.3fs",
                len(active_queries),
                run_ngram,
                len(candidate_idx) if candidate_idx is not None else "all",
                len(fused),
                time.perf_counter() - start,
            )
        return reranked[:final_top_k]

    def _fallback_candidate_idx(self, domain: str | None, doc_ids: list[str] | None, cap: int) -> list[int]:
        """BM25 无正分命中（如纯符号/年份查询）时的兜底候选池：取过滤后前缀，不全量扫描。"""
        allowed = self.index.resolve_doc_ids(doc_ids) if doc_ids else None
        fallback: list[int] = []
        fallback_cap = min(cap, 200)
        for idx, chunk in enumerate(self.index.chunks):
            if domain and chunk.get("domain") != domain:
                continue
            if allowed is not None and chunk.get("doc_id") not in allowed:
                continue
            fallback.append(idx)
            if len(fallback) >= fallback_cap:
                break
        return fallback

    def _augment_structured_candidates(self, candidate_idx: list[int], domain: str | None, doc_ids: list[str] | None) -> list[int]:
        """item16：在给定 doc_ids 内，把结构化 chunk（record_type 命中）补进候选池。

        只在 doc_ids 限定范围内扫描并追加，不全库扩张，不改变检索架构与排序权重——
        只是确保结构化证据进入本地路由（rule/tfidf）的打分范围，避免漏掉条款/指标表行。
        """
        from .config import settings

        boost = max(0, getattr(settings, "docids_structured_pool_boost", 0))
        if not boost or not doc_ids:
            return candidate_idx
        allowed = self.index.resolve_doc_ids(doc_ids)
        if not allowed:
            return candidate_idx
        existing = set(candidate_idx)
        extra: list[int] = []
        for idx, chunk in enumerate(self.index.chunks):
            if len(extra) >= boost:
                break
            if idx in existing:
                continue
            if chunk.get("doc_id") not in allowed:
                continue
            if domain and chunk.get("domain") not in (None, "", domain):
                continue
            if not chunk.get("record_type"):
                continue
            extra.append(idx)
        return candidate_idx + extra

    def _bm25_route_from_results(self, query, results, store, field_weighted: bool) -> list[str]:
        """从已检索的一批 BM25 结果生成 route ranking，避免对同一 query 重复调用 index.search。"""
        keys = []
        for item in results:
            key = _key(item)
            score = float(item.get("score", 0) or 0)
            if field_weighted:
                score = self._field_weighted_score(query, item)
            stored = store.setdefault(key, copy.deepcopy(item))
            routes = stored.setdefault("routes", {})
            channel = "bm25f" if field_weighted else "bm25"
            routes[channel] = max(routes.get(channel, 0.0), score)
            stored["score"] = max(float(stored.get("score", 0) or 0), float(item.get("score", 0) or 0))
            keys.append((key, score))
        keys.sort(key=lambda kv: kv[1], reverse=True)
        return [key for key, _ in keys]

    def _ngram_route(self, query, candidate_idx, top_k, store) -> list[str]:
        self._ensure_ngram_corpus()
        query_grams = Counter(char_ngrams(query))
        if not query_grams:
            return []
        # candidate_idx is None 表示关闭候选池（回退原始全库扫描）；否则只在候选池内打分
        scan_idx = candidate_idx if candidate_idx is not None else range(len(self.index.chunks))
        scored = []
        for idx in scan_idx:
            gram_counter = self._ngram_corpus[idx]
            overlap = sum(min(count, gram_counter.get(gram, 0)) for gram, count in query_grams.items())
            if overlap <= 0:
                continue
            norm = math.sqrt(sum(c * c for c in gram_counter.values())) or 1.0
            scored.append((idx, overlap / norm))
        scored.sort(key=lambda kv: kv[1], reverse=True)
        keys = []
        for idx, score in scored[:top_k]:
            item = copy.deepcopy(self.index.chunks[idx])
            item["score"] = float(item.get("score", 0) or 0)
            key = _key(item)
            stored = store.setdefault(key, item)
            routes = stored.setdefault("routes", {})
            routes["ngram"] = max(routes.get("ngram", 0.0), score)
            keys.append(key)
        return keys

    def _tfidf_route(self, query, candidate_idx, top_k, store) -> list[str]:
        self._ensure_field_corpus()
        query_terms = Counter(tokenize(query))
        if not query_terms or self._df is None:
            return []
        scan_idx = candidate_idx if candidate_idx is not None else range(len(self.index.chunks))
        n_docs = len(self.index.chunks) or 1
        scored = []
        for idx in scan_idx:
            entry = self._field_corpus[idx]
            tf = entry["tf"]
            score = 0.0
            for term, q_count in query_terms.items():
                if term in tf:
                    idf = math.log((n_docs + 1) / (self._df.get(term, 0) + 1)) + 1.0
                    score += (tf[term] / entry["len"]) * idf * q_count
            if score > 0:
                scored.append((idx, score))
        scored.sort(key=lambda kv: kv[1], reverse=True)
        keys = []
        for idx, score in scored[:top_k]:
            item = copy.deepcopy(self.index.chunks[idx])
            item["score"] = float(item.get("score", 0) or 0)
            key = _key(item)
            stored = store.setdefault(key, item)
            routes = stored.setdefault("routes", {})
            routes["tfidf"] = max(routes.get("tfidf", 0.0), score)
            keys.append(key)
        return keys

    def _rule_route(self, text, domain, candidate_idx, top_k, store) -> list[str]:
        from .config import settings

        rule = get_domain_rules(domain)
        keywords = set(rule.get("keywords", [])) | set(GENERAL_RULE_KEYWORDS)
        query_terms = set(extract_query_terms(text)) | {kw for kw in keywords if kw in text}
        if not query_terms:
            return []
        strict = getattr(settings, "rule_strict_scoring", True)
        # 目标年份：query 中出现的年份；strict 模式下要求 chunk 命中“目标指标/词 + 目标年份 + 数值单位”才给结构分
        target_years = set(re.findall(r"20[0-3]\d", text))
        scan_idx = candidate_idx if candidate_idx is not None else range(len(self.index.chunks))
        scored = []
        for idx in scan_idx:
            chunk = self.index.chunks[idx]
            blob = str(chunk.get("text", "")) + " " + str(chunk.get("section", ""))
            hits = sum(1 for term in query_terms if term and term in blob)
            if strict:
                # 结构分仅在“目标词命中 + 数值单位命中 +（若 query 有年份则需命中目标年份）”时才给
                has_unit = bool(re.search(r"\d+(?:\.\d+)?%|\d+(?:\.\d+)?[万亿]?元|第[一二三四五六七八九十百零〇]+条", blob))
                year_ok = (not target_years) or any(y in blob for y in target_years)
                structural_bonus = 1.0 if (hits > 0 and has_unit and year_ok) else 0.0
                score = hits * 1.0 + structural_bonus
            else:
                structural = len(re.findall(r"20[0-3]\d|\d+(?:\.\d+)?%|\d+(?:\.\d+)?[万亿]?元|第[一二三四五六七八九十百零〇]+条", blob))
                score = hits * 1.0 + structural * 0.5
            if score > 0:
                scored.append((idx, score))
        scored.sort(key=lambda kv: kv[1], reverse=True)
        keys = []
        for idx, score in scored[:top_k]:
            item = copy.deepcopy(self.index.chunks[idx])
            item["score"] = float(item.get("score", 0) or 0)
            key = _key(item)
            stored = store.setdefault(key, item)
            routes = stored.setdefault("routes", {})
            routes["rule"] = max(routes.get("rule", 0.0), score)
            keys.append(key)
        return keys

    def _rrf_fuse(self, route_rankings: list[list[str]], store: dict[str, dict]) -> list[dict]:
        rrf: dict[str, float] = {}
        for ranking in route_rankings:
            for rank, key in enumerate(ranking):
                rrf[key] = rrf.get(key, 0.0) + 1.0 / (self.rrf_k + rank + 1)
        items = []
        for key, score in rrf.items():
            item = store.get(key)
            if not item:
                continue
            item["rrf_score"] = round(score, 6)
            item["rerank_score"] = score
            items.append(item)
        items.sort(key=lambda item: item.get("rrf_score", 0), reverse=True)
        return items

    def _rule_rerank(self, query: str, sub_queries: list[str], domain: str | None, items: list[dict]) -> list[dict]:
        from .config import settings

        rule = get_domain_rules(domain)
        focus_terms = set(extract_query_terms(query))
        for sub in sub_queries:
            focus_terms.update(extract_query_terms(sub))
        keywords = set(rule.get("keywords", []))
        strict = getattr(settings, "rule_strict_scoring", True)
        query_text = query + " " + " ".join(sub_queries or [])
        target_years = set(re.findall(r"20[0-3]\d", query_text))
        for item in items:
            text = str(item.get("text", ""))
            bonus = 0.0
            term_hits = sum(1 for term in focus_terms if term and term in text)
            kw_hits = sum(1 for kw in keywords if kw in text)
            bonus += 0.02 * term_hits
            bonus += 0.03 * kw_hits
            if strict:
                # 仅当“目标指标/词命中 + 数值单位命中 +（若 query 有年份则命中目标年份）”时才给结构加分，
                # 避免命中任意年份/金额就加分带来的噪声。
                has_unit = bool(re.search(r"\d+(?:\.\d+)?%|\d+(?:\.\d+)?[万亿]?元|第[一二三四五六七八九十百零〇]+条", text))
                year_ok = (not target_years) or any(y in text for y in target_years)
                if (term_hits > 0 or kw_hits > 0) and has_unit and year_ok:
                    bonus += 0.05
            else:
                if re.search(r"20[0-3]\d", text):
                    bonus += 0.02
                if re.search(r"\d+(?:\.\d+)?%|\d+(?:\.\d+)?[万亿]?元|第[一二三四五六七八九十百零〇]+条", text):
                    bonus += 0.03
            item["rule_bonus"] = round(bonus, 4)
            item["rerank_score"] = round(float(item.get("rrf_score", 0) or 0) + bonus, 6)
        items.sort(key=lambda item: item.get("rerank_score", 0), reverse=True)
        return items

    def _field_weighted_score(self, query: str, item: dict) -> float:
        query_terms = set(tokenize(query))
        base = float(item.get("score", 0) or 0)
        fields = {
            "doc_id": str(item.get("doc_id", "")),
            "title": str(item.get("doc_id", "")),
            "section": str(item.get("section", "")),
            "table_text": " ".join(line for line in str(item.get("text", "")).splitlines() if "指标=" in line or "原始表格行=" in line),
            "text": str(item.get("text", "")),
        }
        score = base
        for field, value in fields.items():
            if not value:
                continue
            field_tokens = set(tokenize(value))
            hits = len(query_terms & field_tokens)
            score += hits * FIELD_WEIGHTS.get(field, 1.0) * 0.3
        return score

    def _ensure_ngram_corpus(self) -> None:
        if self._ngram_corpus is not None:
            return
        with self._ngram_lock:
            if self._ngram_corpus is None:
                self._ngram_corpus = [Counter(char_ngrams(str(chunk.get("text", "")))) for chunk in self.index.chunks]

    def _ensure_field_corpus(self) -> None:
        if self._field_corpus is not None:
            return
        with self._field_lock:
            if self._field_corpus is not None:
                return
            # 复用 BM25Index 建索引时已 tokenize 并 pickle 的 corpus，避免启动时用 jieba 对
            # 10 万+ chunk 全量重新分词（warmup 慢的主因）。corpus 与 chunks 一一对应、tokenize
            # 来源相同（bm25_index.tokenize(chunk["text"])），可安全复用。
            cached = getattr(self.index, "corpus", None)
            reuse = isinstance(cached, list) and len(cached) == len(self.index.chunks)
            corpus = []
            df: Counter = Counter()
            for idx, chunk in enumerate(self.index.chunks):
                tokens = cached[idx] if reuse else tokenize(str(chunk.get("text", "")))
                tf = Counter(tokens)
                corpus.append({"tf": tf, "len": max(1, len(tokens))})
                df.update(tf.keys())
            self._df = df
            self._field_corpus = corpus

    # ---- 与原 option-wise 接口兼容，但底层走 hybrid ----
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
        from .config import settings

        sub_queries: list[str] = []
        sub_queries.append(question + " " + " ".join(options.values()))
        sub_queries.extend(extra_queries or [])
        merged: dict[str, dict] = {}
        fused = self.hybrid_search(
            question,
            sub_queries=sub_queries,
            domain=domain,
            doc_ids=doc_ids,
            per_route_top_k=max(global_top_k, 20),
            fused_top_k=max(final_top_k, 20),
            final_top_k=final_top_k,
        )
        for item in fused:
            item.setdefault("source_options", [])
            merged[_key(item)] = item

        # 指标 query 兜底召回：财报数值题里“指标名+数字”表格 chunk 用题干/选项概念表述召不回，
        # 但用紧凑指标 query 作主 query 能命中。这里对首条 extra_query（约定为指标 query）单独跑
        # hybrid_search 并入候选池，确保数字表进池。仅当该 query 短且不是题干拼接时触发。
        # 跨文档对比题（doc_ids 多个）：对每个文档分别跑一次指标 query，否则检索会偏向其中一个
        # 文档，另一文档的对应指标表召不回（如两家公司分红对比，只召回了其中一侧）。
        if getattr(settings, "indicator_query_enabled", True) and extra_queries:
            probe = str(extra_queries[0]).strip()
            if probe and probe not in (question, question + " " + " ".join(options.values())) and len(probe) <= 60:
                multi_doc = bool(doc_ids) and len(doc_ids) > 1
                per_doc_cap = max(1, getattr(settings, "indicator_probe_per_doc_top_k", 5))
                if multi_doc:
                    probe_targets = [[d] for d in doc_ids]
                    probe_final_k = per_doc_cap
                else:
                    probe_targets = [doc_ids]
                    probe_final_k = max(8, final_top_k // 2)
                for target_docs in probe_targets:
                    probe_hits = self.hybrid_search(
                        probe,
                        sub_queries=[],
                        domain=domain,
                        doc_ids=target_docs,
                        per_route_top_k=max(global_top_k, 20),
                        fused_top_k=max(final_top_k, 20),
                        final_top_k=probe_final_k,
                    )
                    reserve = max(1, getattr(settings, "indicator_probe_reserve_per_doc", 2))
                    for rank, item in enumerate(probe_hits):
                        item.setdefault("source_options", [])
                        stored = merged.setdefault(_key(item), item)
                        # 为每个文档的指标命中保留 top-N，避免在最终排序里被概念段落挤掉
                        if rank < reserve:
                            stored["indicator_reserved"] = True

        mode = "always" if force_option_wise else getattr(settings, "option_wise_mode", "adaptive")
        # 优先级2: adaptive 模式——先看主检索覆盖与置信度，仅对未覆盖/低置信的选项补充召回，
        #   不再无条件对每个选项跑 hybrid 并强制 min_per_option。
        if mode != "always":
            top_score = max((float(c.get("rerank_score", c.get("score", 0)) or 0) for c in fused), default=0.0)
            low_conf = top_score < float(getattr(settings, "option_wise_low_conf_score", 0.02))
            covered = _covered_options(fused, options)
            options_to_retrieve = [letter for letter in sorted(options) if letter not in covered]
            if low_conf:
                options_to_retrieve = sorted(options)  # 主检索整体不可信时，所有选项补召回
            # 主检索已覆盖时给已覆盖选项打 source_options 标记
            for item in fused:
                hit = _option_terms_hit(str(item.get("text", "")), options)
                for letter in hit:
                    if letter not in item.setdefault("source_options", []):
                        item["source_options"].append(letter)
            return self._option_supplement(
                question, options, options_to_retrieve, domain, doc_ids, merged,
                option_top_k, final_top_k, min_per_option=0, force_min=False,
            )

        # always 模式：旧行为，每个选项单独召回并强制 min_per_option
        return self._option_supplement(
            question, options, sorted(options), domain, doc_ids, merged,
            option_top_k, final_top_k, min_per_option=min_per_option, force_min=True,
        )

    def _option_supplement(
        self,
        question: str,
        options: dict[str, str],
        option_letters: list[str],
        domain: str | None,
        doc_ids: list[str] | None,
        merged: dict[str, dict],
        option_top_k: int,
        final_top_k: int,
        min_per_option: int,
        force_min: bool,
    ) -> list[dict]:
        option_buckets: dict[str, list[str]] = {}
        for letter in option_letters:
            option_text = options.get(letter, "")
            if not str(option_text).strip():
                continue
            option_hits = self.hybrid_search(
                question + " " + option_text,
                sub_queries=[option_text, " ".join(extract_query_terms(option_text))],
                domain=domain,
                doc_ids=doc_ids,
                per_route_top_k=option_top_k * 3,
                fused_top_k=option_top_k * 2,
                final_top_k=max(option_top_k, min_per_option + 2),
            )
            for item in option_hits:
                key = _key(item)
                stored = merged.setdefault(key, item)
                if letter not in stored.setdefault("source_options", []):
                    stored["source_options"].append(letter)
                option_buckets.setdefault(letter, []).append(key)
        ranked = sorted(merged.values(), key=lambda item: item.get("rerank_score", item.get("score", 0)), reverse=True)
        selected: dict[str, dict] = {}
        if force_min and min_per_option > 0:
            for letter in option_letters:
                bucket = [merged[key] for key in dict.fromkeys(option_buckets.get(letter, [])) if key in merged]
                bucket.sort(key=lambda item: item.get("rerank_score", item.get("score", 0)), reverse=True)
                for item in bucket[:min_per_option]:
                    selected[_key(item)] = item
        # 先保留每个文档的指标命中（跨文档对比题的关键数字表），再按分数填满剩余槽位
        for item in ranked:
            if item.get("indicator_reserved") and len(selected) < final_top_k:
                selected.setdefault(_key(item), item)
        for item in ranked:
            if len(selected) >= final_top_k:
                break
            selected.setdefault(_key(item), item)
        return sorted(selected.values(), key=lambda item: item.get("rerank_score", item.get("score", 0)), reverse=True)[:final_top_k]


def _key(item: dict) -> str:
    return item.get("chunk_id") or f"{item.get('doc_id')}::{item.get('page')}::{str(item.get('text', ''))[:40]}"


def _dedupe_keep_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value).strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def _option_terms_hit(text: str, options: dict[str, str]) -> list[str]:
    """判断一段文本命中了哪些选项（选项关键词覆盖度达标即算命中）。"""
    hit: list[str] = []
    for letter, option_text in options.items():
        terms = [t for t in re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z0-9]+", str(option_text)) if len(t) >= 2]
        if not terms:
            continue
        matched = sum(1 for term in terms if term in text)
        if matched >= max(1, len(terms) // 3):
            hit.append(letter)
    return hit


def _covered_options(chunks: list[dict], options: dict[str, str]) -> set[str]:
    """主检索结果中已被覆盖的选项集合：已有 source_options 标记或文本命中选项关键词。"""
    covered: set[str] = set()
    for item in chunks:
        covered.update(item.get("source_options", []) or [])
        covered.update(_option_terms_hit(str(item.get("text", "")), options))
    return covered
