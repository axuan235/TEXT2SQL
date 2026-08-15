"""系统工作2：基于确认后的特征生成模型 SQL 与模型描述。

产品 story Q2：基于结果生成模型和模型的描述（基于组合特征的翻译组织）
A2：模型 SQL 和 SQL 中主要的计算路径。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..core.config import Settings
from ..dsl.ast import FeatureDSL
from ..dsl.parser import parse_dsl
from ..dsl.sqlgen import fetch_to_sql
from ..feature.description import StructuredFeature
from ..feature.generator import FeatureGenerator
from ..feature.planner import ComputationPlanner
from ..llm.client import LLMClient
from ..translate.translator import Translator


class GenerateService:
    """特征 -> 模型 SQL + 计算路径 + 模型描述。"""

    def __init__(self, settings: Settings, llm: Optional[LLMClient] = None):
        self.settings = settings
        self.llm = llm or LLMClient(settings)
        self.generator = FeatureGenerator(settings, self.llm)
        self.planner = ComputationPlanner(settings)
        self.translator = Translator(settings)

    def generate(self, features: List[Dict[str, Any]], output_fields: Optional[List[str]] = None) -> Dict[str, Any]:
        """输入：结构化特征列表（或特征 DSL 文本） -> 模型 SQL 与描述。

        features 元素支持两种形态：
        - {"text": "学生_平均分：>60"} 自然语言特征描述
        - {"dsl": "student.avg_score -> t_student_score.reduce(...)"} 已确认的 DSL
        """
        dsls: List[FeatureDSL] = []
        details = []
        for feat in features:
            if feat.get("dsl"):
                dsl = parse_dsl(feat["dsl"])
                dsls.append(dsl)
                details.append(self._describe_feature(dsl))
            else:
                text = feat.get("text", "")
                result = self.generator.generate(text)
                details.append(result)
                if result.get("best"):
                    dsls.append(parse_dsl(result["best"]["dsl"]))
                elif result.get("features"):
                    dsls.append(parse_dsl(result["features"][0]["dsl"]))

        # 计算规划
        plan = self.planner.plan(dsls)
        # 取数逻辑
        fetch_sql = None
        if output_fields:
            fetch = self._build_fetch(dsls, output_fields)
            fetch_sql = fetch_to_sql(fetch[0], fetch[1], fetch[2]) if fetch else None

        return {
            "model_sql": plan.sql,
            "plan": plan.to_dict(),
            "calculation_paths": [d.to_dsl() for d in dsls],
            "feature_details": details,
            "model_description": self._build_model_description(plan, details),
            "fetch_sql": fetch_sql,
        }

    def _build_fetch(self, dsls: List[FeatureDSL], output_fields: List[str]) -> Optional[tuple]:
        """基于特征模型构建取数逻辑（主体为合并模型的别名）。"""
        from ..dsl.ast import FilterOp, LimitOp

        # 用第一个过滤条件作为取数条件（简化）
        conditions = []
        for d in dsls:
            for op in d.ops:
                if isinstance(op, FilterOp) and op.condition not in conditions:
                    conditions.append(op.condition)
        ops = []
        if conditions:
            ops.append(FilterOp(condition=" and ".join(f"({c})" for c in conditions)))
        ops.append(LimitOp(n=10))
        return "model", ops, output_fields

    def _describe_feature(self, dsl: FeatureDSL) -> Dict[str, Any]:
        return {
            "dsl": dsl.to_dsl(),
            "translation": self.translator.translate_dsl(dsl),
        }

    def _build_model_description(self, plan, details) -> str:
        parts = [plan.description]
        for d in details:
            if isinstance(d, dict) and d.get("translation"):
                parts.append(d["translation"])
        return "；".join(p for p in parts if p)
