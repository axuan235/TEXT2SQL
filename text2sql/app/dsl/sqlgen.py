"""DSL -> SQL 生成器。

按照需求：
- filter -> WHERE
- map -> 计算列（中间列通过子查询承载，供后续算子引用）
- reduce -> GROUP BY + 聚合函数
- union -> UNION ALL（字段对应，多表 union 逐级合并）
- join -> JOIN ... ON（一次只关联一张表）
- limit / sort -> 取数阶段最后应用

示例：
  t_student.filter('grade=\\'2\\' and  class=\\'3\\'').map('score-60 as score').reduce('student_id', 'avg(score) as avg_score').avg_score
  ==>
  SELECT student_id, avg(score) AS avg_score
  FROM (SELECT student_id, score - 60 AS score FROM t_student WHERE grade='2' AND class='3') _m
  GROUP BY student_id
"""
from __future__ import annotations

from typing import List

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


def _extract_identifiers(sql: str) -> List[str]:
    """从 SQL 表达式中提取字段名（简易实现，按空白和运算符切分）。"""
    import re

    if not sql:
        return []
    # 去掉 "expr as alias" 中的别名部分
    parts = re.split(r"\s+as\s+", sql, flags=re.I)
    sql = parts[0]
    tokens = re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*", sql)
    # 去掉 SQL 关键字
    kw = {
        "and", "or", "not", "as", "between", "in", "is", "null", "like", "asc",
        "desc", "limit", "group", "by", "order", "having", "where", "select",
        "from", "join", "on", "union", "all", "case", "when", "then", "else",
        "end", "distinct", "over", "partition", "row_number", "count", "avg",
        "sum", "max", "min",
    }
    return [t for t in tokens if t not in kw and not t.isdigit()]


def _map_aliases(ops: List[Op]) -> dict:
    """{别名: cal_info}，记录 map 产出的中间字段。"""
    aliases = {}
    for op in ops:
        if isinstance(op, MapOp):
            info = op.cal_info
            if " as " in info.lower():
                expr, _, alias = info.partition(" as ")
                aliases[alias.strip()] = expr.strip()
    return aliases


def _join_to_sql(op: JoinOp) -> str:
    jt = op.join_type.upper() if op.join_type else "INNER"
    if jt not in ("INNER", "LEFT", "RIGHT", "FULL", "LEFT OUTER", "RIGHT OUTER", "FULL OUTER", "CROSS"):
        jt = "INNER"
    if jt == "CROSS":
        return f"CROSS JOIN {op.table}"
    return f"{jt} JOIN {op.table} ON {op.condition}"


def to_sql(dsl: FeatureDSL) -> str:
    """将特征 DSL 转为 SQL（用于计算规划阶段的合并 SQL 由 planner 生成）。"""
    ops = list(dsl.ops)
    filters = [o for o in ops if isinstance(o, FilterOp)]
    maps = [o for o in ops if isinstance(o, MapOp)]
    reduces = [o for o in ops if isinstance(o, ReduceOp)]
    unions = [o for o in ops if isinstance(o, UnionOp)]
    joins = [o for o in ops if isinstance(o, JoinOp)]
    limits = [o for o in ops if isinstance(o, LimitOp)]
    sorts = [o for o in ops if isinstance(o, SortOp)]

    # ---- 基础 FROM（含 union 与 join）----
    from_sql = _build_from(dsl.table, unions, joins)

    where_sql = " AND ".join(f"({o.condition})" for o in filters)

    # ---- 无聚合：直接取数 ----
    if not reduces:
        if maps:
            # map 计算列直接作为 select 列
            select_cols = [m.cal_info for m in maps]
        else:
            select_cols = [dsl.output or "*"]
        sql = f"SELECT {', '.join(select_cols)} FROM {from_sql}"
        if where_sql:
            sql += f" WHERE {where_sql}"
    # ---- 有聚合 ----
    else:
        reduce = reduces[-1]
        key = reduce.key
        agg = reduce.cal_info
        map_aliases = _map_aliases(maps)
        # 聚合表达式引用的字段
        agg_cols = _extract_identifiers(agg)
        if maps:
            # 中间子查询需要：分组键 + map 计算列 + 聚合引用的原始列（未被 map 产出）
            inner_cols = list(dict.fromkeys(_extract_identifiers(key)))
            for m in maps:
                inner_cols.append(m.cal_info)
            for c in agg_cols:
                if c not in map_aliases and c not in inner_cols:
                    inner_cols.append(c)
            inner = f"SELECT {', '.join(inner_cols)} FROM {from_sql}"
            if where_sql:
                inner += f" WHERE {where_sql}"
            sql = f"SELECT {key}, {agg} FROM ({inner}) _m GROUP BY {key}"
        else:
            # 无 map：直接单层 SQL
            sql = f"SELECT {key}, {agg} FROM {from_sql}"
            if where_sql:
                sql += f" WHERE {where_sql}"
            sql += f" GROUP BY {key}"

    # ---- sort / limit 取数阶段 ----
    order_parts = []
    for o in sorts:
        order_parts.append(o.expr)
    if order_parts:
        sql += f" ORDER BY {', '.join(order_parts)}"
    for o in limits:
        sql += f" LIMIT {o.n}"

    return sql


def _build_from(table: str, unions: List[UnionOp], joins: List[JoinOp]) -> str:
    """构建 FROM 子句。union 优先（字段必须对应），随后 join。"""
    if not unions:
        base = table
        for j in joins:
            base = f"({base}) {_join_to_sql(j)}"
        return base
    # 逐级 union：t1.union(t2).union(t3)
    current = f"SELECT * FROM {table}"
    for u in unions:
        current = f"({current} UNION ALL SELECT * FROM {u.table})"
    for j in joins:
        current = f"({current}) {_join_to_sql(j)}"
    return current


def fetch_to_sql(subject: str, ops: List[Op], columns: List[str]) -> str:
    """将取数逻辑表达式转为 SQL。

    示例：student.filter("avg_score>60 and birth_year>1999 and gender=1").limit(10)
          [full_name, avg_score,test_type,count_1]
      ==>
      SELECT full_name, avg_score, test_type, count_1 FROM student
      WHERE avg_score>60 AND birth_year>1999 AND gender=1 LIMIT 10
    """
    filters = [o for o in ops if isinstance(o, FilterOp)]
    limits = [o for o in ops if isinstance(o, LimitOp)]
    sorts = [o for o in ops if isinstance(o, SortOp)]
    where_sql = " AND ".join(f"({o.condition})" for o in filters)
    cols = ", ".join(columns) if columns else "*"
    sql = f"SELECT {cols} FROM {subject}"
    if where_sql:
        sql += f" WHERE {where_sql}"
    order_parts = [o.expr for o in sorts]
    if order_parts:
        sql += f" ORDER BY {', '.join(order_parts)}"
    for o in limits:
        sql += f" LIMIT {o.n}"
    return sql
