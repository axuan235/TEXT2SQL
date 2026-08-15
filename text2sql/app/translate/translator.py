"""规则翻译门面：条件翻译、函数翻译、特征 DSL 翻译。

对应需求：
1. 条件的规则翻译 —— transCol / transValue / stringFormat
2. 函数的规则翻译 —— 通过配置文件配置翻译规则
3. 特征DSL规则翻译 —— 辅助打标用（可选实现）
"""
from __future__ import annotations

from typing import Optional

from ..core.config import Settings
from ..dsl.ast import FeatureDSL, FilterOp, MapOp, ReduceOp
from .rules import RuleEngine


class Translator:
    """翻译器：封装 RuleEngine，对外提供统一翻译入口。"""

    def __init__(self, settings: Settings):
        self.engine = RuleEngine(settings)

    # ---------- 条件翻译 ----------

    def translate_condition(self, condition: str, table: Optional[str] = None) -> str:
        """翻译条件表达式。

        示例：both_year between 2010 and 2012
              -> 出生年在 2010年 与 2012年 之间
        """
        return self.engine.translate_condition(condition, table)

    # ---------- 函数翻译 ----------

    def translate_function(self, expr: str, table: Optional[str] = None) -> str:
        return self.engine.translate_function(expr, table)

    # ---------- 特征 DSL 翻译（辅助打标） ----------

    def translate_dsl(self, dsl: FeatureDSL) -> str:
        """将特征 DSL 翻译为自然语言描述（辅助打标用）。

        输出形如：从{t_student}中按照学生编号分组，对分数取平均值
        """
        parts = []
        for op in dsl.ops:
            if isinstance(op, FilterOp):
                parts.append(f"筛选条件：{self.translate_condition(op.condition, dsl.table)}")
            elif isinstance(op, MapOp):
                parts.append(f"计算字段：{self._translate_map(op.cal_info, dsl.table)}")
            elif isinstance(op, ReduceOp):
                parts.append(
                    f"按照{self.engine.trans_col(op.key, dsl.table)}分组，"
                    f"{self._translate_agg(op.cal_info, dsl.table)}"
                )
        if not parts:
            parts.append(f"直接取用字段{self.engine.trans_col(dsl.output, dsl.table)}")
        return "；".join(parts)

    def _translate_map(self, cal_info: str, table: str) -> str:
        # 形如 'score-60 as score'
        if " as " in cal_info.lower():
            expr, _, alias = cal_info.partition(" as ")
            return f"{self.engine.trans_col(expr.strip(), table)}计算得到{alias.strip()}"
        return self.engine.trans_col(cal_info.strip(), table)

    def _translate_agg(self, cal_info: str, table: str) -> str:
        if " as " in cal_info.lower():
            expr, _, alias = cal_info.partition(" as ")
            inner = self.engine.translate_function(expr.strip(), table)
            return inner or expr.strip()
        return self.engine.translate_function(cal_info.strip(), table)
