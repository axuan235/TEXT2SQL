"""系统工作3：注册用户的计算路径到知识库。

产品 story Q3：用户提供计算路径
  student.avg_score -> t_student_score.reduce('student_id', 'avg(score) as avg_score').avg_score
系统工作3：注册用户的计算路径到知识库（特征结构库 + 限定词库，去重校验）
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from ..core.config import Settings
from ..dsl.ast import FeatureDSL, FilterOp, MapOp, ReduceOp
from ..dsl.parser import parse_dsl
from ..kb.store import FeatureStructKB, QualifierKB
from ..translate.translator import Translator


def _alias_of(cal_info: str) -> Optional[str]:
    """从 'expr as alias' 提取别名。"""
    if " as " in cal_info.lower():
        return cal_info.partition(" as ")[2].strip()
    return None


class RegisterService:
    """计算路径注册服务。"""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.feature_kb = FeatureStructKB(settings.feature_struct_file)
        self.qualifier_kb = QualifierKB(settings.qualifier_file)
        self.translator = Translator(settings)

    def register(self, dsl_text: str) -> Dict[str, Any]:
        """注册一条计算路径。

        处理：
        1. 解析 DSL
        2. 提取限定词（filter 条件，mask 参数后入限定词库）
        3. 提取特征结构（去除限定词，规范化命名后入特征结构库）
        4. MD5 去重校验
        """
        dsl = parse_dsl(dsl_text)
        qualifier_records: List[Dict[str, Any]] = []
        qualifier_new = 0
        for op in dsl.ops:
            if isinstance(op, FilterOp):
                for simple_cond in self._split_conditions(op.condition):
                    masked = self._mask(simple_cond)
                    rec, is_new = self.qualifier_kb.add_qualifier(
                        compare_struct=masked,
                        value_type=self._value_type(simple_cond),
                        rule_trans=self._rule_trans(simple_cond, dsl.table),
                        valid_tans="",
                        table=dsl.table,
                    )
                    qualifier_records.append(rec)
                    if is_new:
                        qualifier_new += 1

        # 特征结构：去除 filter 后序列化
        struct_ops = [o for o in dsl.ops if not isinstance(o, FilterOp)]
        struct_ops = self._prune_unused_maps(struct_ops, dsl.output)
        struct_ops = self._normalize_names(struct_ops)
        struct_dsl = dsl.copy()
        struct_dsl.ops = struct_ops
        if struct_ops and isinstance(struct_ops[-1], ReduceOp):
            # 输出字段名同步为规范化别名
            struct_dsl.output = _alias_of(struct_ops[-1].cal_info) or struct_dsl.output
        struct_text = self._struct_text(struct_dsl)
        name = struct_dsl.output or dsl.attr
        rule_trans = self.translator.translate_dsl(struct_dsl)
        rec, is_new = self.feature_kb.add_feature(
            feature_struct=struct_text,
            name=name,
            rule_trans=rule_trans,
            valid_tans="",
            table_list=[dsl.table],
        )
        return {
            "dsl": dsl_text,
            "registered": True,
            "feature_struct": rec,
            "feature_struct_new": is_new,
            "qualifiers": qualifier_records,
            "qualifiers_new": qualifier_new,
        }

    # ---------- 辅助 ----------

    def _prune_unused_maps(self, ops: List[Any], final_output: str) -> List[Any]:
        """移除与计算语义无关的 map（其产出字段不被后续算子或输出引用）。

        对应需求：字段生成部分若与计算的语义无关，移除后计算结果并不改变。
        """
        import re

        def ids(sql: str) -> set:
            return set(re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*", sql))

        # 从后向前收集"被需要"的字段
        needed = ids(final_output)
        kept: List[Any] = []
        for op in reversed(ops):
            if isinstance(op, MapOp):
                expr, _, alias = op.cal_info.partition(" as ")
                alias = alias.strip()
                if alias and alias not in needed:
                    continue  # 产出未被使用，移除
                needed |= ids(expr)
                kept.append(op)
            elif isinstance(op, ReduceOp):
                needed |= ids(op.key) | ids(op.cal_info)
                kept.append(op)
            else:
                kept.append(op)
        return list(reversed(kept))

    def _normalize_names(self, ops: List[Any]) -> List[Any]:
        """命名规范化：函数_参数字段（如 bar_score -> avg_score）。"""
        from ..feature.extractor import FeatureExtractor

        out = []
        for op in ops:
            if isinstance(op, ReduceOp):
                expr, _, alias = op.cal_info.partition(" as ")
                alias = alias.strip()
                func_m = re.match(r"([a-zA-Z_]+)\(", expr)
                func = func_m.group(1).lower() if func_m else ""
                args_m = re.search(r"\(([^)]*)\)", expr)
                arg_list = [a.strip() for a in args_m.group(1).split(",") if args_m and args_m.group(1).strip()] or []
                std = FeatureExtractor.normalize_name(func, arg_list) if func else alias
                # 别名不符合 函数_参数字段 规范（不以函数名开头）时，规范化为标准名
                if alias and std and not alias.startswith(func + "_"):
                    op = ReduceOp(key=op.key, cal_info=f"{expr} as {std}")
            out.append(op)
        return out

    @staticmethod
    def _split_conditions(condition: str) -> List[str]:
        """将 AND 组合条件拆为简单条件。"""
        return _split_and(condition)

    @staticmethod
    def _mask(cond: str) -> str:
        """将条件参数 mask 为 X。"""
        masked = re.sub(r"'[^']*'", "X", cond)
        masked = re.sub(r'"([^"]*)"', "X", masked)
        masked = re.sub(r"\b\d+(\.\d+)?\b", "X", masked)
        # 统一运算符两侧空格（与限定词库样例 grade = X 保持一致）
        masked = re.sub(r"\s*([<>=!]+)\s*", r" \1 ", masked)
        return " ".join(masked.split())

    @staticmethod
    def _value_type(cond: str) -> str:
        if re.search(r"'[^']*'", cond):
            return "string"
        return "number"

    def _rule_trans(self, cond: str, table: str) -> str:
        return self.translator.translate_condition(cond, table)

    @staticmethod
    def _struct_text(struct_dsl: FeatureDSL) -> str:
        text = struct_dsl.to_dsl(with_entity=False)
        if " -> " in text:
            text = text.split(" -> ", 1)[1]
        return text


def _split_and(condition: str) -> List[str]:
    """按顶层 and 拆分（引号与括号感知）。"""
    parts = []
    depth = 0
    quote = ""
    cur = ""
    i = 0
    n = len(condition)
    while i < n:
        ch = condition[i]
        if quote:
            cur += ch
            if ch == quote:
                quote = ""
            i += 1
            continue
        if ch in "'\"":
            quote = ch
            cur += ch
            i += 1
            continue
        if ch in "([":
            depth += 1
            cur += ch
            i += 1
            continue
        if ch in ")]":
            depth -= 1
            cur += ch
            i += 1
            continue
        if depth == 0 and condition[i : i + 3].lower() == "and":
            after = i + 3
            if after >= n or condition[after].isspace():
                if cur.strip():
                    parts.append(cur.strip())
                cur = ""
                i = after
                continue
        cur += ch
        i += 1
    if cur.strip():
        parts.append(cur.strip())
    return parts or [condition]
