"""知识检索器。

需求建议使用 embedding 模型接口检索；未配置 LLM 时使用内置的关键词/字符相似度兜底，
保证离线可运行。检索目标：
- 主体_属性（对应特征结构库）
- 各个限定词（对应限定词库）
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Tuple

from ..core.config import Settings
from ..llm.client import LLMClient
from .store import FeatureStructKB, QualifierKB


def _tokenize(text: str) -> List[str]:
    """粗粒度分词：中文字符按字，英文/数字按词。"""
    tokens = []
    for part in re.findall(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]", text):
        if re.match(r"[\u4e00-\u9fff]", part):
            tokens.append(part)
        else:
            tokens.append(part)
    return tokens


def _char_similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


def _jaccard(a: List[str], b: List[str]) -> float:
    if not a or not b:
        return 0.0
    sa, sb = set(a), set(b)
    return len(sa & sb) / len(sa | sb)


class Retriever:
    """特征结构 / 限定词检索器。"""

    def __init__(self, settings: Settings, llm: Optional[LLMClient] = None):
        self.settings = settings
        self.llm = llm or LLMClient(settings)
        self.feature_kb = FeatureStructKB(settings.feature_struct_file)
        self.qualifier_kb = QualifierKB(settings.qualifier_file)

    # ---------- 通用 ----------

    def _embed_similarity(self, query: str, doc: str) -> float:
        if self.llm and self.llm.embedding_available():
            try:
                q = self.llm.embed(query)
                d = self.llm.embed(doc)
                return self._cosine(q, d)
            except Exception:
                pass
        # 兜底：字符相似度 + Jaccard
        return 0.5 * _char_similarity(query, doc) + 0.5 * _jaccard(_tokenize(query), _tokenize(doc))

    @staticmethod
    def _cosine(a: List[float], b: List[float]) -> float:
        import math

        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a)) or 1.0
        nb = math.sqrt(sum(x * x for x in b)) or 1.0
        return dot / (na * nb)

    # ---------- 特征结构检索 ----------

    def retrieve_feature_struct(self, query: str, top_k: Optional[int] = None) -> List[Tuple[Dict[str, Any], float]]:
        """检索特征结构，返回 [(记录, 相似度)] 降序。"""
        top_k = top_k or self.settings.retrieval_top_k
        scored = []
        for rec in self.feature_kb.load_all():
            text = " ".join(
                [
                    rec.get("name", ""),
                    rec.get("feature_struct", ""),
                    rec.get("rule_trans", ""),
                    rec.get("valid_tans", ""),
                ]
            )
            score = self._embed_similarity(query, text)
            scored.append((rec, score))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:top_k]

    # ---------- 限定词检索 ----------

    def retrieve_qualifier(self, query: str, top_k: Optional[int] = None) -> List[Tuple[Dict[str, Any], float]]:
        top_k = top_k or self.settings.retrieval_top_k
        scored = []
        for rec in self.qualifier_kb.load_all():
            text = " ".join(
                [
                    rec.get("rule_trans", ""),
                    rec.get("valid_tans", ""),
                    rec.get("compare_struct", ""),
                ]
            )
            score = self._embed_similarity(query, text)
            scored.append((rec, score))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:top_k]

    # ---------- 参数实例化 ----------

    def instantiate_qualifier(self, qualifier_rec: Dict[str, Any], value: str, col: str) -> str:
        """限定词参数实例化：将 compare_struct 中被 mask 的参数替换为取值。

        结合表字段信息中的映射配置反向转换（如 女 -> gender=2）。
        """
        compare_struct = qualifier_rec.get("compare_struct", "")
        # 支持形如 'grade = X' / 'test_type = X' 的掩码结构
        if "X" not in compare_struct:
            return compare_struct
        # 反向映射：先查表结构信息中的字段映射
        mapped = self._reverse_map(qualifier_rec.get("table", ""), col, value)
        return compare_struct.replace("X", mapped)

    def _reverse_map(self, table: str, col: str, value: str) -> str:
        from ..translate.rules import TableSchema

        schema = TableSchema(self.settings.table_schema_file)
        info = schema.get_column(table, col)
        if not info:
            return value
        mapping = info.get("mapping", "") or ""
        if mapping and not mapping.startswith("dict:"):
            for pair in mapping.split(";"):
                if ":" in pair:
                    k, _, label = pair.partition(":")
                    if label.strip() == value:
                        return k.strip()
        return value
