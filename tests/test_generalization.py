"""B 榜泛化保护测试：agent 源码不得对具体数据集的公司名/题目/doc_id 做硬编码。

这些测试描述的是系统行为契约——“对未知数据也要工作”，而不是实现细节。
任何把 A 榜公司、年份、条款号写死的实现都会让这些测试失败。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

AGENT_DIR = Path(__file__).resolve().parents[1] / "agent"

# A 榜数据集中出现的具体主体/标识，绝不允许出现在推理源码里
FORBIDDEN_DATASET_TOKENS = [
    "比亚迪",
    "宁德时代",
    "中国建筑",
    "美的集团",
    "招商银行",
    "中国移动",
    "annual_byd",
    "annual_catl",
    "annual_cscec",
    "annual_midea",
    "annual_cmb",
    "annual_chinamobile",
]

# 推理期会被加载的模块（不含离线评估脚本）
AGENT_SOURCE_FILES = sorted(p for p in AGENT_DIR.glob("*.py") if p.name != "__init__.py")


@pytest.mark.parametrize("source_file", AGENT_SOURCE_FILES, ids=lambda p: p.name)
def test_no_hardcoded_dataset_entities(source_file: Path):
    text = source_file.read_text(encoding="utf-8")
    hits = [token for token in FORBIDDEN_DATASET_TOKENS if token in text]
    assert not hits, f"{source_file.name} 含硬编码数据集标识: {hits}"


def test_structural_tokens_extracted_by_regex_not_literals():
    """年份/比例/条款号由正则动态抽取，对范围内任意取值都成立，而非依赖写死的样例值。"""
    from agent.domain_rules import extract_clause_numbers, extract_percents, extract_years

    # 年份覆盖 2000-2039 这一年报合理区间内的任意值，不是只认 2024/2025
    assert extract_years("2027 年与 2031 年对比") == ["2027", "2031"]
    assert "第九十九条" in extract_clause_numbers("依据第九十九条规定")
    assert "88.8%" in extract_percents("占比 88.8% 提升")


def test_question_stem_words_not_treated_as_subjects():
    """题干套话（如“下列关于”）不得被当作主体，否则污染覆盖判断。"""
    from agent.domain_rules import extract_subjects

    subjects = extract_subjects("下列关于公司经营业绩变化的描述中，哪些正确？")
    assert "下列关于" not in subjects
    assert "关于" not in subjects


# 赛题规则：禁止在任何场景下使用 embedding / 表示型排序模型（含 rerank 模型）。
# 仅允许 Qwen 生成式 LLM（chat）+ 本地词法/统计检索。
FORBIDDEN_MODEL_APIS = [
    "TextReRank",
    "TextEmbedding",
    "MultiModalEmbedding",
    "qwen3-rerank",
    "qwen3-reranker",
    "gte-rerank",
    "text-embedding",
    "bge",
    "sentence-transformers",
    "SentenceTransformer",
]

PROJECT_PY_FILES = sorted(
    p
    for p in (AGENT_DIR.parent).rglob("*.py")
    if "tests" not in p.parts and ".agents" not in p.parts
)


@pytest.mark.parametrize("source_file", PROJECT_PY_FILES, ids=lambda p: p.name)
def test_no_embedding_or_rerank_model_apis(source_file: Path):
    text = source_file.read_text(encoding="utf-8")
    hits = [token for token in FORBIDDEN_MODEL_APIS if token in text]
    assert not hits, f"{source_file.name} 使用了被禁止的 embedding/rerank 模型接口: {hits}"

