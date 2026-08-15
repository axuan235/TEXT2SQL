"""DSL 模块：AST、解析器、优化器、SQL 生成。"""
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
    op_to_text,
)
from .parser import parse_dsl, parse_fetch
from .optimizer import analyze_swap, can_merge_reduce, optimize
from .sqlgen import fetch_to_sql, to_sql

__all__ = [
    "FeatureDSL",
    "Op",
    "FilterOp",
    "MapOp",
    "ReduceOp",
    "UnionOp",
    "JoinOp",
    "LimitOp",
    "SortOp",
    "op_to_text",
    "parse_dsl",
    "parse_fetch",
    "analyze_swap",
    "can_merge_reduce",
    "optimize",
    "to_sql",
    "fetch_to_sql",
]
