"""计算优化：算子顺序重排与交换安全性分析。

需求规定最优计算顺序：filter -> map -> union -> reduce -> join
（sort / limit 一般出现在最后取数阶段，与维度计算无关）。

任意两个操作交换是否影响计算结果的分析详见项目 README《算子交换分析》一节，
本模块提供可编程的安全交换判断与优化器（仅做保证结果不变的交换）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

import sqlglot
from sqlglot import exp

from .ast import (
    FeatureDSL,
    FilterOp,
    JoinOp,
    LimitOp,
    MapOp,
    Op,
    ReduceOp,
    SortOp,
    UnionOp,
)

# 算子在规范顺序中的位置（越小越靠前）
CANONICAL_ORDER = {
    "filter": 0,
    "map": 1,
    "union": 2,
    "reduce": 3,
    "join": 4,
    "sort": 5,
    "limit": 6,
}


@dataclass
class SwapAnalysis:
    """两个相邻算子交换的安全性分析结果。"""

    a: Op
    b: Op
    swappable: bool
    reason: str = ""


def _identifiers(expr_sql: str) -> List[str]:
    """提取 SQL 表达式中的字段标识符。"""
    if not expr_sql or not expr_sql.strip():
        return []
    try:
        tree = sqlglot.parse_one(expr_sql)
        return [c.name for c in tree.find_all(exp.Column)]
    except Exception:
        return []


def _produced(op: Op) -> set:
    """算子产出的字段（别名）。"""
    if isinstance(op, MapOp):
        alias = _alias_of(op.cal_info)
        return {alias} if alias else set()
    if isinstance(op, ReduceOp):
        alias = _alias_of(op.cal_info)
        return {alias} if alias else set()
    if isinstance(op, FilterOp):
        return set()
    return set()


def _consumed(op: Op) -> set:
    """算子消费的字段。"""
    if isinstance(op, FilterOp):
        return set(_identifiers(op.condition))
    if isinstance(op, MapOp):
        return set(_identifiers(op.cal_info))
    if isinstance(op, ReduceOp):
        return set(_identifiers(op.key)) | set(_identifiers(op.cal_info))
    if isinstance(op, JoinOp):
        return set(_identifiers(op.condition))
    if isinstance(op, SortOp):
        return set(_identifiers(op.expr))
    return set()


def _alias_of(cal_info: str) -> Optional[str]:
    """从 'expr as alias' 中提取别名。"""
    if not cal_info:
        return None
    m = None
    try:
        tree = sqlglot.parse_one(cal_info)
        if isinstance(tree, exp.Alias):
            return tree.alias
        return None
    except Exception:
        return None


def analyze_swap(a: Op, b: Op) -> SwapAnalysis:
    """分析相邻算子 a、b 交换后是否影响计算结果。"""
    # ---- filter ----
    if isinstance(a, FilterOp) and isinstance(b, FilterOp):
        return SwapAnalysis(a, b, True, "filter 与 filter 满足 AND 交换律，交换不影响结果")
    if isinstance(a, FilterOp) and isinstance(b, MapOp):
        if _consumed(a) & _produced(b):
            return SwapAnalysis(a, b, False, "filter 引用了 map 产出的字段，必须先 map 再 filter")
        return SwapAnalysis(a, b, True, "filter 不依赖 map 产出，先过滤更优且结果不变")
    if isinstance(a, FilterOp) and isinstance(b, ReduceOp):
        if _consumed(a) & _produced(b):
            return SwapAnalysis(a, b, False, "filter 引用聚合结果（HAVING 语义），不能下推")
        return SwapAnalysis(a, b, True, "filter 仅引用原始字段（WHERE 语义），可下推到 reduce 之前")
    if isinstance(a, FilterOp) and isinstance(b, UnionOp):
        return SwapAnalysis(a, b, False, "union 需要两表字段对应，filter 需分别下推到两个分支，不做自动交换")
    if isinstance(a, FilterOp) and isinstance(b, JoinOp):
        if b.join_type != "inner" and _consumed(a) & set(_identifiers(b.table)):
            return SwapAnalysis(a, b, False, "外连接右表上的过滤下推会改变连接语义")
        return SwapAnalysis(a, b, True, "内连接下 filter 可下推，结果不变")

    # ---- map ----
    if isinstance(a, MapOp) and isinstance(b, MapOp):
        if _consumed(b) & _produced(a):
            return SwapAnalysis(a, b, False, "后一个 map 依赖前一个 map 的产出字段")
        return SwapAnalysis(a, b, True, "两个 map 无字段依赖，交换不影响结果")
    if isinstance(a, MapOp) and isinstance(b, ReduceOp):
        if _consumed(b) & _produced(a):
            return SwapAnalysis(a, b, False, "reduce 依赖 map 产出的字段，必须先 map")
        if _consumed(a) & _produced(b):
            return SwapAnalysis(a, b, False, "map 依赖聚合结果，必须先 reduce")
        return SwapAnalysis(a, b, True, "map 与 reduce 无字段依赖（map 仅基于分组键），交换结果一致")
    if isinstance(a, MapOp) and isinstance(b, UnionOp):
        return SwapAnalysis(a, b, False, "map 作用于单表字段，union 前交换需保证两分支字段一致，不做自动交换")
    if isinstance(a, MapOp) and isinstance(b, JoinOp):
        if _consumed(a) & set(_identifiers(b.table)):
            return SwapAnalysis(a, b, False, "map 依赖 join 引入的字段，必须先 join")
        return SwapAnalysis(a, b, True, "map 仅使用左表字段，可先 map 再 join，结果不变")

    # ---- reduce ----
    if isinstance(a, ReduceOp) and isinstance(b, ReduceOp):
        return SwapAnalysis(a, b, False, "多级聚合层级不同，交换会改变聚合语义")
    if isinstance(a, ReduceOp) and isinstance(b, UnionOp):
        return SwapAnalysis(a, b, False, "先 union 再 reduce 与 先 reduce 再 union 结果不同")
    if isinstance(a, ReduceOp) and isinstance(b, JoinOp):
        return SwapAnalysis(a, b, False, "聚合后 join 与 join 后聚合结果不同，需计算规划决策")
    if isinstance(a, ReduceOp) and isinstance(b, FilterOp):
        return analyze_swap(b, a)  # 对称情况
    if isinstance(a, ReduceOp) and isinstance(b, MapOp):
        return analyze_swap(b, a)

    # ---- union / join ----
    if isinstance(a, UnionOp) and isinstance(b, UnionOp):
        return SwapAnalysis(a, b, True, "union 满足结合律，连续 union 交换不影响结果")
    if isinstance(a, UnionOp) and isinstance(b, JoinOp):
        return SwapAnalysis(a, b, False, "union 与 join 顺序不同结果不同")
    if isinstance(a, JoinOp) and isinstance(b, JoinOp):
        if _consumed(a) & set(_identifiers(b.table)) or _consumed(b) & set(_identifiers(a.table)):
            return SwapAnalysis(a, b, False, "两个 join 存在关联字段依赖")
        return SwapAnalysis(a, b, True, "两个 join 无依赖，交换结果一致")
    if isinstance(a, JoinOp) and isinstance(b, FilterOp):
        return analyze_swap(b, a)
    if isinstance(a, JoinOp) and isinstance(b, MapOp):
        return analyze_swap(b, a)

    # ---- sort / limit（取数阶段，一般不与维度算子交换）----
    if isinstance(a, SortOp) and isinstance(b, LimitOp):
        return SwapAnalysis(a, b, False, "先 limit 再 sort 会丢失排序语义")
    if isinstance(a, LimitOp) and isinstance(b, SortOp):
        return SwapAnalysis(a, b, False, "先 sort 后 limit 的取数顺序不能颠倒")
    if isinstance(a, (SortOp, LimitOp)) and isinstance(b, (SortOp, LimitOp)):
        return SwapAnalysis(a, b, False, "sort/limit 与其它算子交换一般影响取数结果")
    if isinstance(a, (SortOp, LimitOp)) or isinstance(b, (SortOp, LimitOp)):
        return SwapAnalysis(a, b, False, "sort/limit 属于取数阶段算子，不与维度计算算子交换")

    return SwapAnalysis(a, b, False, f"暂不支持 {a.name} 与 {b.name} 的交换分析")


def optimize(dsl: FeatureDSL) -> FeatureDSL:
    """将算子按规范顺序（filter/map/union/reduce/join，sort/limit 最后）重排。

    仅执行经过 analyze_swap 判定为安全的交换，保证计算结果不变。
    """
    ops: List[Op] = list(dsl.ops)
    n = len(ops)
    changed = True
    while changed:
        changed = False
        for i in range(n - 1):
            a, b = ops[i], ops[i + 1]
            if CANONICAL_ORDER.get(a.name, 99) > CANONICAL_ORDER.get(b.name, 99):
                analysis = analyze_swap(a, b)
                if analysis.swappable:
                    ops[i], ops[i + 1] = ops[i + 1], ops[i]
                    changed = True
    result = dsl.copy()
    result.ops = ops
    return result


def can_merge_reduce(a: ReduceOp, b: ReduceOp) -> bool:
    """两个 reduce 是否可合并为同一个 group（key 相同）。"""
    return _normalize_key(a.key) == _normalize_key(b.key)


def _normalize_key(key: str) -> str:
    return ",".join(sorted(_identifiers(key)))
