"""DSL 抽象语法树。

特征 DSL 语法（示例）：
    student.avg_score -> t_student.reduce('student_id', 'avg(score) as avg_score').avg_score
    gender -> t_student.gender

7 个基础算子：map / reduce / filter / union / join / limit / sort
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

# 算子规范顺序：最优计算顺序（filter 最早，join 最晚，limit/sort 放取数阶段）
CANONICAL_OP_ORDER = ["filter", "map", "union", "reduce", "join", "sort", "limit"]


@dataclass
class Op:
    """DSL 算子基类。"""

    name: str = ""


@dataclass
class FilterOp(Op):
    """filter([condition])：条件过滤，条件与 SparkSQL 一致。"""

    condition: str = ""
    name: str = "filter"


@dataclass
class MapOp(Op):
    """map([cal_info])：一行进一行出，对应 select 计算列。"""

    cal_info: str = ""
    name: str = "map"


@dataclass
class ReduceOp(Op):
    """reduce([key], [cal_info])：shuffle 聚合，对应 group by。"""

    key: str = ""
    cal_info: str = ""
    name: str = "reduce"


@dataclass
class UnionOp(Op):
    """union([table])：两表合并，字段必须能对应。"""

    table: str = ""
    name: str = "union"


@dataclass
class JoinOp(Op):
    """join([table], [join_type], [join_condition])：一次只能关联一张表。"""

    table: str = ""
    join_type: str = "inner"
    condition: str = ""
    name: str = "join"


@dataclass
class LimitOp(Op):
    """limit([n])：取前 n 条。"""

    n: int = 0
    name: str = "limit"


@dataclass
class SortOp(Op):
    """sort([expr])：排序表达式，如 'score desc'。"""

    expr: str = ""
    name: str = "sort"


@dataclass
class FeatureDSL:
    """一条特征的计算路径。

    形如：entity.attr -> table.op1(...).op2(...).output
    entity/attr 为特征描述部分（可缺省，表示原始表字段直接取用）。
    table 为源表；ops 为计算算子链；output 为最终输出字段。
    """

    table: str
    ops: List[Op] = field(default_factory=list)
    output: str = ""
    entity: str = ""
    attr: str = ""

    def to_dsl(self, with_entity: bool = True) -> str:
        """序列化回 DSL 文本。"""
        head = f"{self.entity}.{self.attr}" if (with_entity and self.entity and self.attr) else (
            self.attr or self.output
        )
        body = self.table
        for op in self.ops:
            body += "." + op_to_text(op)
        if self.output:
            body += f".{self.output}"
        return f"{head} -> {body}" if head else body

    def op_types(self) -> List[str]:
        return [op.name for op in self.ops]

    def copy(self) -> "FeatureDSL":
        return FeatureDSL(
            table=self.table,
            ops=[op for op in self.ops],
            output=self.output,
            entity=self.entity,
            attr=self.attr,
        )


def op_to_text(op: Op) -> str:
    """将单个算子序列化为 DSL 文本。"""
    if isinstance(op, FilterOp):
        return f"filter({_q(op.condition)})"
    if isinstance(op, MapOp):
        return f"map({_q(op.cal_info)})"
    if isinstance(op, ReduceOp):
        return f"reduce({_q(op.key)}, {_q(op.cal_info)})"
    if isinstance(op, UnionOp):
        return f"union({_q(op.table)})"
    if isinstance(op, JoinOp):
        return f"join({_q(op.table)}, {_q(op.join_type)}, {_q(op.condition)})"
    if isinstance(op, LimitOp):
        return f"limit({op.n})"
    if isinstance(op, SortOp):
        return f"sort({_q(op.expr)})"
    raise ValueError(f"未知算子: {op}")


def _q(s: str) -> str:
    """给参数字符串加单引号（内部单引号转义）。"""
    return "'" + s.replace("'", "\\'") + "'"
