"""特征生成。

流程（对应需求）：
1. 结构化特征描述：切分 主体_属性 与 限定词列表
2. 知识库检索：检索 主体_属性 与各个限定词（embedding，兜底字符相似度）
   若相似度过低，调用大模型生成 SQL 后通过特征提取获取 DSL
3. 限定词参数实例化：将 mask 参数替换为语义中提取的取值（含反向映射）
4. 生成候选集并筛选：特征结构 x 条件 全量组合，满足表一致性约束
5. 特征计算筛选：从候选集中选出语义最佳结果
6. 保留基于特征的取数逻辑
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from ..core.config import Settings
from ..dsl.ast import FeatureDSL, FilterOp, LimitOp
from ..dsl.sqlgen import fetch_to_sql
from ..kb.retriever import Retriever
from ..llm.client import LLMClient
from .description import DescriptionParser, StructuredFeature
from .extractor import FeatureExtractor


@dataclass
class FeatureCandidate:
    """特征候选：特征结构 + 条件组合。"""

    feature_struct: str
    name: str
    table: str
    qualifier_structs: List[str] = field(default_factory=list)  # 实例化后的条件
    score: float = 0.0
    description: str = ""

    @property
    def dsl_text(self) -> str:
        """组合成完整 DSL。"""
        ops = []
        for q in self.qualifier_structs:
            ops.append(f"filter({_q(q)})")
        ops.append(self.feature_struct)
        return ".".join(ops) if ops else self.feature_struct


def _q(s: str) -> str:
    return "'" + s.replace("'", "\\'") + "'"


class FeatureGenerator:
    """特征生成器。"""

    def __init__(self, settings: Settings, llm: Optional[LLMClient] = None):
        self.settings = settings
        self.llm = llm or LLMClient(settings)
        self.retriever = Retriever(settings, self.llm)
        self.desc_parser = DescriptionParser(self.llm)
        self.extractor = FeatureExtractor()

    # ---------- 步骤1：结构化特征描述 ----------

    def structure_description(self, text: str) -> StructuredFeature:
        return self.desc_parser.parse(text)

    # ---------- 步骤2：知识库检索 ----------

    def retrieve(self, sf: StructuredFeature) -> Dict[str, Any]:
        """检索特征结构与各限定词，返回命中结果与相似度。"""
        # 主体_属性
        subj_attr_hits = self.retriever.retrieve_feature_struct(sf.subject_attr)
        # 各限定词
        qual_hits = {q: self.retriever.retrieve_qualifier(q) for q in sf.qualifiers}
        return {
            "subject_attr_hits": subj_attr_hits,
            "qualifier_hits": qual_hits,
        }

    def generate_sql_from_llm(self, description: str) -> str:
        """大模型生成 SQL（相似度过低时的兜底）。"""
        if not self.llm.chat_available():
            raise RuntimeError("LLM 未配置，无法生成 SQL")
        prompt = f"""
请根据特征描述生成一条 SparkSQL 查询，只输出 SQL：
{description}
要求：表名使用 t_ 前缀；聚合字段命名遵循 函数_参数字段 规范。
"""
        sql = self.llm.complete(prompt).strip()
        sql = re.sub(r"^```(sql)?|```$", "", sql, flags=re.M).strip()
        return sql

    # ---------- 步骤3：限定词参数实例化 ----------

    def _extract_value(self, qualifier: str) -> str:
        """从限定词语义中提取取值（启发式）。"""
        # 形如 男性 -> 男；最近三天 -> 3
        m = re.search(r"(\d+)", qualifier)
        if m:
            return m.group(1)
        if "男" in qualifier:
            return "男"
        if "女" in qualifier:
            return "女"
        if "期末" in qualifier:
            return "期末"
        if "期中" in qualifier:
            return "期中"
        return qualifier

    def instantiate_qualifier(self, qualifier: str, table: str, col: str) -> str:
        """实例化限定词：检索 -> 取参数 -> 反向映射。"""
        hits = self.retriever.retrieve_qualifier(qualifier, top_k=1)
        if not hits:
            return ""
        rec, score = hits[0]
        if score < self.settings.retrieval_min_similarity:
            return ""
        value = self._extract_value(qualifier)
        return self.retriever.instantiate_qualifier(rec, value, col)

    # ---------- 步骤4：候选集生成与筛选 ----------

    def generate_candidates(self, sf: StructuredFeature) -> List[FeatureCandidate]:
        """生成候选集：特征结构 x 限定词条件全量组合（带表一致性约束）。"""
        # 特征结构命中
        struct_hits = self.retriever.retrieve_feature_struct(
            sf.subject_attr, top_k=self.settings.retrieval_top_k
        )
        struct_hits = [(r, s) for r, s in struct_hits if s >= self.settings.retrieval_min_similarity]

        # 限定词实例化（每个限定词取最优命中）
        qual_instances: List[List[str]] = []
        for q in sf.qualifiers:
            hits = self.retriever.retrieve_qualifier(q, top_k=1)
            if not hits or hits[0][1] < self.settings.retrieval_min_similarity:
                qual_instances.append([])
                continue
            rec, _ = hits[0]
            value = self._extract_value(q)
            inst = self.retriever.instantiate_qualifier(rec, value, sf.attr)
            if inst:
                qual_instances.append([inst])
            else:
                qual_instances.append([])

        candidates: List[FeatureCandidate] = []
        for rec, score in struct_hits:
            table_list = rec.get("table_list", [])
            if not table_list or not table_list[0]:
                continue
            table = table_list[0]
            # 表一致性：跳过无法匹配限定词表的情况（简化：限定词表为空或同表）
            ok = True
            for q_rec in self._qual_records(sf):
                q_table = q_rec.get("table", "")
                if q_table and q_table != table and q_table not in table_list:
                    ok = False
                    break
            if not ok:
                continue
            qual_structs = [q for q in qual_instances if q]
            combined = []
            if qual_structs:
                # 全量组合（去重，简单笛卡尔积）
                from itertools import product

                for combo in product(*qual_structs):
                    combined.append(list(combo))
            else:
                combined.append([])
            for combo in combined:
                candidates.append(
                    FeatureCandidate(
                        feature_struct=rec.get("feature_struct", ""),
                        name=rec.get("name", ""),
                        table=table,
                        qualifier_structs=combo,
                        score=score,
                    )
                )
            if len(candidates) >= self.settings.candidate_max_combinations:
                break
        return candidates

    def _qual_records(self, sf: StructuredFeature) -> List[Dict[str, Any]]:
        out = []
        for q in sf.qualifiers:
            hits = self.retriever.retrieve_qualifier(q, top_k=1)
            if hits:
                out.append(hits[0][0])
        return out

    # ---------- 步骤5：特征计算筛选 ----------

    def select_best(self, candidates: List[FeatureCandidate], sf: StructuredFeature) -> Optional[FeatureCandidate]:
        """从候选集中选出语义最佳（相似度加权 + 条件匹配加分）。"""
        if not candidates:
            return None
        best = None
        for c in candidates:
            # 基础分：特征结构相似度
            s = c.score
            # 限定词匹配加分
            target_quals = set(sf.qualifiers)
            matched = sum(1 for q in target_quals if self._qual_matches(q, c.qualifier_structs))
            s += 0.1 * matched
            c.score = s
            if best is None or s > best.score:
                best = c
        return best

    def _qual_matches(self, qualifier: str, qual_structs: List[str]) -> bool:
        if not qual_structs:
            return False
        value = self._extract_value(qualifier)
        return any(value in qs for qs in qual_structs)

    # ---------- 步骤6：取数逻辑 ----------

    def build_fetch(self, subject: str, conditions: List[str], columns: List[str], limit: int = 10) -> str:
        """构建基于特征的取数逻辑表达式。"""
        ops = []
        if conditions:
            ops.append(FilterOp(condition=" and ".join(f"({c})" for c in conditions)))
        ops.append(LimitOp(n=limit))
        cols = ", ".join(columns)
        return f'{subject}.filter("{ops[0].condition}").limit({limit})[{cols}]'

    def fetch_to_sql(self, fetch_text: str) -> str:
        """取数逻辑表达式 -> SQL。"""
        from ..dsl.parser import parse_fetch

        subject, ops, cols = parse_fetch(fetch_text)
        return fetch_to_sql(subject, ops, cols)

    # ---------- 端到端 ----------

    def generate(self, description: str) -> Dict[str, Any]:
        """从自然语言特征描述生成计算 DSL 候选与取数逻辑。"""
        sf = self.structure_description(description)
        candidates = self.generate_candidates(sf)
        if not candidates:
            # 知识库未命中 -> LLM 生成 SQL -> 特征提取
            if self.llm.chat_available():
                sql = self.generate_sql_from_llm(description)
                feats = self.extractor.extract_from_sql(sql)
                return {
                    "structured": sf.to_dict(),
                    "from_kb": False,
                    "generated_sql": sql,
                    "features": [f.to_dict() for f in feats],
                    "candidates": [],
                    "best": None,
                    "fetch": None,
                }
            return {
                "structured": sf.to_dict(),
                "from_kb": False,
                "generated_sql": None,
                "features": [],
                "candidates": [],
                "best": None,
                "fetch": None,
            }
        best = self.select_best(candidates, sf)
        return {
            "structured": sf.to_dict(),
            "from_kb": True,
            "generated_sql": None,
            "features": [{"dsl": c.dsl_text, "name": c.name, "table": c.table} for c in candidates],
            "candidates": [{"dsl": c.dsl_text, "name": c.name, "score": round(c.score, 4)} for c in candidates],
            "best": {"dsl": best.dsl_text, "name": best.name, "score": round(best.score, 4)} if best else None,
            "fetch": None,
        }
