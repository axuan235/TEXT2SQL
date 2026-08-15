"""特征提取：从 SQL 模型中提取每个字段的计算逻辑（DSL）。

对应需求场景1：企业有大量历史模型 SQL 日志，需要提取每个字段的 DSL 计算路径，
进行规范统一（计算优化 + 命名规范），并拆分为限定词与特征结构分别入库。

场景2：知识库无法检索到某维度属性时，由大模型生成 SQL 后走同一套提取流程入库。

处理流程：
1. 用 sqlglot 解析 SQL
2. 对每个输出字段提取计算路径（filter/map/reduce/join/union）
3. 计算优化：按 filter/map/union/reduce/join 规范顺序重排
4. 命名规范化：函数_参数字段
5. 拆分限定词（mask 参数）与特征结构
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import sqlglot
from sqlglot import exp

from ..core.exceptions import DSLParseError
from ..dsl.ast import (
    FeatureDSL,
    FilterOp,
    JoinOp,
    MapOp,
    ReduceOp,
    UnionOp,
)
from ..dsl.optimizer import optimize

AGG_FUNCS = {"avg", "sum", "count", "max", "min", "collect_list", "collect_set", "stddev", "variance"}


@dataclass
class ExtractedFeature:
    """从 SQL 中提取的单字段特征。"""

    dsl: FeatureDSL            # 完整 DSL（含限定词，已优化）
    qualifier_conds: List[str] = field(default_factory=list)  # 限定词条件（原始）
    feature_struct: str = ""   # 特征结构（去除限定词后的 DSL 文本）
    name: str = ""             # 规范化字段名

    def to_dict(self) -> Dict[str, Any]:
        return {
            "dsl": self.dsl.to_dsl(),
            "feature_struct": self.feature_struct,
            "name": self.name,
            "qualifier_conds": self.qualifier_conds,
        }


class FeatureExtractor:
    """SQL -> 特征 DSL 提取器。"""

    # ---------- 命名规范 ----------

    @staticmethod
    def normalize_name(func: str, args: List[str]) -> str:
        """字段命名规范：函数_参数字段。

        如 avg(score) -> avg_score；count(1) -> count_1；
        concat(first_name, last_name) -> concat_first_name_last_name
        """
        func = func.lower()
        arg_names = []
        for a in args:
            a = a.strip()
            if re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", a):
                arg_names.append(a)
            else:
                # 常量参数如 1
                arg_names.append(re.sub(r"[^a-zA-Z0-9_]", "_", a) or "1")
        if func in ("count",) and (not arg_names or arg_names[0] in ("1", "*")):
            return "count_1"
        return "_".join([func] + arg_names)

    # ---------- 主流程 ----------

    def extract_from_sql(self, sql: str) -> List[ExtractedFeature]:
        """从一条 SQL 中提取每个输出字段的特征 DSL。"""
        try:
            parsed = sqlglot.parse_one(sql)
        except Exception as e:
            raise DSLParseError(f"SQL 解析失败: {e}") from e
        if not isinstance(parsed, exp.Select):
            raise DSLParseError("仅支持 SELECT 查询的特征提取")

        table = self._extract_base_table(parsed)
        joins = self._extract_joins(parsed)
        where_conds = self._extract_where(parsed)
        group_keys = self._extract_group_keys(parsed)
        select_items = self._extract_select_items(parsed)

        results = []
        for item in select_items:
            dsl, out_name = self._build_dsl_for_item(
                item, table, joins, where_conds, group_keys
            )
            # 计算优化
            dsl = optimize(dsl)
            dsl.output = out_name or dsl.output
            results.append(self._finalize(dsl, where_conds))
        return results

    # ---------- SQL 解析辅助 ----------

    def _extract_base_table(self, select: exp.Select) -> str:
        from_part = select.args.get("from_") or select.args.get("from")
        if not from_part:
            raise DSLParseError("SQL 缺少 FROM")
        table = from_part.this
        if isinstance(table, exp.Table):
            return table.name
        if isinstance(table, exp.Subquery):
            alias = table.alias
            if alias:
                return alias
            return f"({table.this.sql()})"
        return table.sql()

    def _extract_joins(self, select: exp.Select) -> List[JoinOp]:
        joins = []
        for j in select.args.get("joins", []):
            table = j.this
            tname = table.name if isinstance(table, exp.Table) else table.sql()
            on = j.args.get("on")
            cond = on.sql() if on else "1=1"
            jt = j.args.get("kind", "").strip().upper()
            if jt not in ("INNER", "LEFT", "RIGHT", "FULL", "CROSS"):
                jt = "inner"
            joins.append(JoinOp(table=tname, join_type=jt.lower(), condition=cond))
        return joins

    def _extract_where(self, select: exp.Select) -> List[str]:
        where = select.args.get("where")
        if not where:
            return []
        return [where.this.sql()]

    def _extract_group_keys(self, select: exp.Select) -> List[str]:
        group = select.args.get("group")
        if not group:
            return []
        return [e.sql() for e in group.expressions]

    def _extract_select_items(self, select: exp.Select) -> List[exp.Expression]:
        return list(select.expressions)

    # ---------- 单字段 DSL 构建 ----------

    def _build_dsl_for_item(
        self,
        item: exp.Expression,
        table: str,
        joins: List[JoinOp],
        where_conds: List[str],
        group_keys: List[str],
    ) -> tuple:
        """返回 (FeatureDSL, 输出字段名)。"""
        expr = item
        alias = ""
        if isinstance(item, exp.Alias):
            expr = item.this
            alias = item.alias

        # 1) 聚合函数 -> reduce
        if self._contains_agg(expr):
            agg = self._agg_expression(expr)
            func, args, out_name = self._parse_agg(agg, alias)
            ops: List[Any] = []
            if where_conds:
                ops.append(FilterOp(condition=" and ".join(f"({c})" for c in where_conds)))
            for j in joins:
                ops.append(j)
            ops.append(ReduceOp(key=", ".join(group_keys) if group_keys else "1", cal_info=f"{func}({', '.join(args)}) as {out_name}"))
            dsl = FeatureDSL(table=table, ops=ops, output=out_name)
            return dsl, out_name

        # 2) 普通列引用 -> 原始字段
        if isinstance(expr, exp.Column):
            col_name = expr.name
            ops: List[Any] = []
            if where_conds:
                ops.append(FilterOp(condition=" and ".join(f"({c})" for c in where_conds)))
            for j in joins:
                ops.append(j)
            dsl = FeatureDSL(table=table, ops=ops, output=col_name, attr=alias or col_name)
            return dsl, alias or col_name

        # 3) 计算表达式 -> map
        out_name = alias or self._expr_name(expr)
        ops = []
        if where_conds:
            ops.append(FilterOp(condition=" and ".join(f"({c})" for c in where_conds)))
        for j in joins:
            ops.append(j)
        ops.append(MapOp(cal_info=f"{expr.sql()} as {out_name}"))
        dsl = FeatureDSL(table=table, ops=ops, output=out_name)
        return dsl, out_name

    # ---------- 聚合处理 ----------

    def _contains_agg(self, expr: exp.Expression) -> bool:
        return any(isinstance(a, exp.AggFunc) for a in expr.walk())

    def _agg_expression(self, expr: exp.Expression) -> exp.AggFunc:
        for a in expr.walk():
            if isinstance(a, exp.AggFunc):
                return a
        raise DSLParseError("未找到聚合函数")

    def _parse_agg(self, agg: exp.AggFunc, alias: str) -> tuple:
        func = agg.sql_name().lower()
        args = [a.sql() for a in agg.expressions]
        # 命名规范：函数_参数字段；无别名或别名不规范时采用规范名
        std_name = self.normalize_name(func, args)
        out_name = alias if alias and re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", alias) else std_name
        if alias and alias != std_name and not re.match(rf"^{func}_", alias):
            out_name = std_name  # 不规范命名（如 bar_score）改为规范名
        return func, args, out_name

    @staticmethod
    def _expr_name(expr: exp.Expression) -> str:
        sql = expr.sql()
        tokens = re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*", sql)
        return "_".join(tokens[:3]) or "computed"

    # ---------- 限定词 / 特征结构拆分 ----------

    def _finalize(self, dsl: FeatureDSL, where_conds: List[str]) -> ExtractedFeature:
        qualifiers = [c for c in where_conds]
        # 特征结构 = 去除 filter 算子后的 DSL 文本
        struct_ops = [op for op in dsl.ops if not isinstance(op, FilterOp)]
        struct_dsl = dsl.copy()
        struct_dsl.ops = struct_ops
        struct_text = struct_dsl.to_dsl(with_entity=False)
        # 去掉 "-> " 头部
        if " -> " in struct_text:
            struct_text = struct_text.split(" -> ", 1)[1]
        return ExtractedFeature(
            dsl=dsl,
            qualifier_conds=qualifiers,
            feature_struct=struct_text,
            name=dsl.output,
        )

    # ---------- 批量提取 ----------

    def extract_many(self, sqls: List[str]) -> List[ExtractedFeature]:
        out = []
        for sql in sqls:
            out.extend(self.extract_from_sql(sql))
        return out

    def mask_qualifier(self, cond: str) -> str:
        """将限定词条件中的具体参数 mask 为 X（参数实例化留待生成时处理）。"""
        # 将单引号字符串与数字替换为 X
        masked = re.sub(r"'[^']*'", "X", cond)
        masked = re.sub(r'"([^"]*)"', "X", masked)
        masked = re.sub(r"\b\d+(\.\d+)?\b", "X", masked)
        return masked
