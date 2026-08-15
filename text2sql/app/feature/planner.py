"""计算规划。

任务：
1. 检测各特征是否包含共同计算基础（同表、可合并 filter、相同 shuffle 键），
   若包含则合并写入同一个 SQL。
2. 存在多种合并可能性时选择最优组合。
3. 分析计算需要的字段依赖，将计算结构树转成 SQL 模型。
4. 支持 A 字段聚合 -> A,B 字段聚合的转换。
5. 跨表特征：检索表字段含义找出关联字段，自动补充 join 到计算路径。

示例合并：
  学生期末考试的平均分 avg_score 与 考试次数 count_1：
  - 同表 t_student_score、filter 可合并、shuffle 键相同（student_id）
  - reduce 计算信息拼接：avg(score) as avg_score, count(1) as count_1
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from ..core.config import Settings
from ..dsl.ast import (
    FeatureDSL,
    FilterOp,
    JoinOp,
    MapOp,
    ReduceOp,
    UnionOp,
)
from ..dsl.optimizer import can_merge_reduce, optimize
from ..translate.rules import TableSchema


@dataclass
class MergePlan:
    """合并计划。"""

    features: List[FeatureDSL] = field(default_factory=list)
    sql: str = ""
    description: str = ""
    dependencies: Dict[str, List[str]] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "features": [f.to_dsl() for f in self.features],
            "sql": self.sql,
            "description": self.description,
            "dependencies": self.dependencies,
        }


class ComputationPlanner:
    """计算规划器。"""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.schema = TableSchema(settings.table_schema_file)
        self.join_config = self._load_join_keys()

    def _load_join_keys(self) -> List[Dict[str, str]]:
        path = self.settings.join_keys_file
        if not path.exists():
            return []
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("join_keys", [])

    # ---------- 公共基础检测 ----------

    def group_by_common_base(self, features: List[FeatureDSL]) -> List[List[FeatureDSL]]:
        """将特征按共同计算基础分组。

        共同基础判定：
        - 表相同
        - filter 条件可合并（求并集后不冲突）
        - reduce 的 shuffle 键可合并（相同键，或 A 聚合可升级为 A,B 聚合）
        """
        groups: List[List[FeatureDSL]] = []
        for f in features:
            placed = False
            for g in groups:
                if self._can_merge(f, g[0]):
                    g.append(f)
                    placed = True
                    break
            if not placed:
                groups.append([f])
        return groups

    def _can_merge(self, a: FeatureDSL, b: FeatureDSL) -> bool:
        if a.table != b.table:
            return False
        a_reduce = self._last_reduce(a)
        b_reduce = self._last_reduce(b)
        if a_reduce and b_reduce:
            if not can_merge_reduce(a_reduce, b_reduce):
                # A 聚合 -> A,B 聚合 兼容判定
                if not self._key_compatible(a_reduce, b_reduce):
                    return False
        return True

    @staticmethod
    def _last_reduce(dsl: FeatureDSL) -> Optional[ReduceOp]:
        for op in reversed(dsl.ops):
            if isinstance(op, ReduceOp):
                return op
        return None

    def _key_compatible(self, a: ReduceOp, b: ReduceOp) -> bool:
        """A 聚合与 A,B 聚合兼容：一个键集合是另一个的子集。"""
        import re

        keys_a = set(re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*", a.key))
        keys_b = set(re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*", b.key))
        return keys_a <= keys_b or keys_b <= keys_a

    # ---------- 合并 SQL 生成 ----------

    def merge_to_sql(self, features: List[FeatureDSL]) -> str:
        """将一组具备共同基础的 feature 合并为一条 SQL。"""
        if not features:
            return ""
        table = features[0].table
        # 合并 filter（并集、去重）
        filters: List[str] = []
        for f in features:
            for op in f.ops:
                if isinstance(op, FilterOp):
                    cond = op.condition
                    if cond not in filters:
                        filters.append(cond)
        # 合并 reduce（同键则拼接聚合表达式）
        reduces: List[ReduceOp] = [self._last_reduce(f) for f in features if self._last_reduce(f)]
        # 原始字段直接取用
        raw_cols = [f.output for f in features if not self._last_reduce(f)]

        # 分组键：取最长键（A 聚合升级为 A,B 聚合）
        import re

        key_tokens: Set[str] = set()
        for r in reduces:
            key_tokens |= set(re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*", r.key))
        group_key = ", ".join(sorted(key_tokens)) if key_tokens else ""

        # 聚合列
        agg_cols = []
        for r in reduces:
            agg_cols.append(r.cal_info)

        where_sql = " AND ".join(f"({c})" for c in filters) if filters else ""
        sql = f"SELECT {group_key}" if group_key else "SELECT "
        if raw_cols:
            sql += (", " if group_key else "") + ", ".join(raw_cols)
        if agg_cols:
            sql += (", " if (group_key or raw_cols) else "") + ", ".join(agg_cols)
        sql += f" FROM {table}"
        if where_sql:
            sql += f" WHERE {where_sql}"
        if group_key:
            sql += f" GROUP BY {group_key}"
        return sql

    # ---------- 字段依赖分析 ----------

    _SQL_KEYWORDS = {
        "as", "and", "or", "not", "select", "from", "where", "group", "by",
        "having", "order", "join", "on", "inner", "left", "right", "full",
        "outer", "cross", "union", "all", "over", "partition", "row_number",
        "count", "avg", "sum", "max", "min", "case", "when", "then", "else",
        "end", "distinct", "between", "in", "is", "null", "like",
    }

    def analyze_dependencies(self, features: List[FeatureDSL]) -> Dict[str, List[str]]:
        """分析各特征输出字段依赖的源表字段。"""
        import re

        deps: Dict[str, List[str]] = {}
        for f in features:
            cols: Set[str] = set()
            for op in f.ops:
                if isinstance(op, FilterOp):
                    cols |= set(re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*", op.condition))
                elif isinstance(op, MapOp):
                    cols |= set(re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*", op.cal_info))
                elif isinstance(op, ReduceOp):
                    cols |= set(re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*", op.key))
                    cols |= set(re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*", op.cal_info))
                elif isinstance(op, JoinOp):
                    cols |= set(re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*", op.condition))
            cols -= self._SQL_KEYWORDS
            cols.discard(f.output)
            deps[f.output] = sorted(cols)
        return deps

    # ---------- 跨表 join 补充 ----------

    def add_joins_for_tables(self, dsl: FeatureDSL, target_tables: List[str]) -> FeatureDSL:
        """为特征补充到目标表的关联（自动检索关联键或使用配置）。"""
        result = dsl.copy()
        current = dsl.table
        for target in target_tables:
            if target == current:
                continue
            key = self._find_join_key(current, target)
            if key:
                result.ops.append(
                    JoinOp(
                        table=target,
                        join_type="left",
                        condition=f"{current}.{key['left_col']} = {target}.{key['right_col']}",
                    )
                )
            current = target
        return result

    def _find_join_key(self, left: str, right: str) -> Optional[Dict[str, str]]:
        # 配置优先
        for jk in self.join_config:
            if {jk.get("left_table"), jk.get("right_table")} == {left, right}:
                return jk
        # 自动匹配：相同列名（学生编号 student_id 等）
        auto = self._auto_match_key(left, right)
        if auto:
            return auto
        return None

    def _auto_match_key(self, left: str, right: str) -> Optional[Dict[str, str]]:
        left_cols = self._columns_of(left)
        right_cols = self._columns_of(right)
        common = sorted(set(left_cols) & set(right_cols))
        if not common:
            return None
        return {
            "left_table": left,
            "left_col": common[0],
            "right_table": right,
            "right_col": common[0],
            "join_type": "left",
        }

    def _columns_of(self, table: str) -> List[str]:
        if table in self.schema._tables:
            return list(self.schema._tables[table]["columns"].keys())
        return []

    # ---------- 计算树 -> SQL 模型 ----------

    def plan(self, features: List[FeatureDSL]) -> MergePlan:
        """计算规划主入口。

        1. 按共同基础分组
        2. 每组生成一条合并 SQL
        3. 组间若跨表，补充 join
        4. 输出字段依赖
        """
        deps = self.analyze_dependencies(features)
        groups = self.group_by_common_base(features)
        sqls: List[str] = []
        used_tables: Set[str] = set()

        # 跨表特征先补充 join（在各自组内）
        planned: List[List[FeatureDSL]] = []
        for g in groups:
            g_tables = {f.table for f in g}
            if len(g_tables) > 1:
                # 补充 join 到每个特征
                base_table = g[0].table
                others = [t for t in g_tables if t != base_table]
                g = [self.add_joins_for_tables(f, others) for f in g]
            planned.append(g)

        for g in planned:
            sqls.append(self.merge_to_sql(g))
            for f in g:
                used_tables.add(f.table)

        # 组间联合：不同组可通过关联键串联（简化：生成 UNION/关联查询说明）
        description = self._describe_plan(planned)
        sql = ";\n".join(sqls) if len(sqls) > 1 else (sqls[0] if sqls else "")
        return MergePlan(features=features, sql=sql, description=description, dependencies=deps)

    def _describe_plan(self, groups: List[List[FeatureDSL]]) -> str:
        parts = []
        for idx, g in enumerate(groups, 1):
            tables = {f.table for f in g}
            names = [f.output for f in g]
            parts.append(f"第{idx}组：表 {'、'.join(sorted(tables))} 合并计算字段 {'、'.join(names)}")
        return "；".join(parts)
