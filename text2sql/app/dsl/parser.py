"""DSL 解析器：文本 <-> AST。

支持的输入形态：
1. 完整特征：  student.avg_score -> t_student.reduce('student_id', 'avg(score) as avg_score').avg_score
2. 原始字段：  gender -> t_student.gender
3. 计算路径（无特征头）： t_student.reduce('student_id', 'avg(score) as avg_score').avg_score
4. 取数逻辑：  student.filter("avg_score>60 and birth_year>1999").limit(10)[full_name, avg_score]
"""
from __future__ import annotations

import re
from typing import List, Tuple

from ..core.exceptions import DSLParseError
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

# 算子调用文本：.name(arg1, arg2, ...)
_OP_CALL = re.compile(r"^([a-zA-Z_][a-zA-Z0-9_]*)\s*\((.*)\)$", re.S)


class DSLParser:
    """特征 DSL 解析器。"""

    @staticmethod
    def parse(text: str) -> FeatureDSL:
        """解析完整特征 DSL（含 ->）。"""
        text = text.strip()
        if not text:
            raise DSLParseError("DSL 文本为空")
        if "->" in text:
            head, _, body = text.partition("->")
            entity, attr = DSLParser._parse_head(head.strip())
        else:
            entity, attr = "", ""
            body = text
        table, ops, output = DSLParser._parse_body(body.strip())
        return FeatureDSL(table=table, ops=ops, output=output, entity=entity, attr=attr)

    @staticmethod
    def parse_fetch(text: str) -> Tuple[str, List[Op], List[str]]:
        """解析取数逻辑：subject.filter(...).limit(n)[col1, col2]。

        返回 (主体, 算子列表, 输出字段列表)。
        """
        text = text.strip()
        m = re.match(r"^([a-zA-Z_][a-zA-Z0-9_]*)\s*(.*?)\s*\[\s*(.*?)\s*\]\s*$", text, re.S)
        if not m:
            raise DSLParseError(f"取数逻辑格式错误: {text}")
        subject, ops_part, cols_part = m.group(1), m.group(2), m.group(3)
        ops = DSLParser._parse_ops(ops_part)
        cols = [c.strip() for c in cols_part.split(",") if c.strip()]
        return subject, ops, cols

    # ---------- 内部方法 ----------

    @staticmethod
    def _parse_head(head: str) -> Tuple[str, str]:
        if "." in head:
            entity, _, attr = head.partition(".")
            return entity.strip(), attr.strip()
        return "", head.strip()

    @staticmethod
    def _parse_body(body: str) -> Tuple[str, List[Op], str]:
        """解析 -> 右侧：table.op1(...).op2(...).output"""
        # 找到第一个 '.' 之前的表名
        first_dot = DSLParser._find_top_dot(body)
        if first_dot < 0:
            # 只有表名 + 输出字段（如 t_student.gender 已被第一个分支处理；这里处理纯表名）
            if _looks_like_op(body):
                raise DSLParseError(f"DSL 缺少源表: {body}")
            return body, [], ""
        table = body[:first_dot].strip()
        rest = body[first_dot:]
        ops, output = DSLParser._parse_chain(rest)
        return table, ops, output

    @staticmethod
    def _find_top_dot(s: str) -> int:
        """找到字符串中不在引号内、不在括号内的第一个 '.' 的位置。"""
        depth = 0
        quote: str = ""
        i = 0
        while i < len(s):
            ch = s[i]
            if quote:
                if ch == "\\" and i + 1 < len(s):
                    i += 2
                    continue
                if ch == quote:
                    quote = ""
            else:
                if ch in "'\"":
                    quote = ch
                elif ch in "([":
                    depth += 1
                elif ch in ")]":
                    depth -= 1
                elif ch == "." and depth == 0:
                    return i
            i += 1
        return -1

    @staticmethod
    def _parse_chain(rest: str) -> Tuple[List[Op], str]:
        """解析 '.op(args).op(args).output' 链。"""
        ops: List[Op] = []
        output = ""
        i = 0
        n = len(rest)
        while i < n:
            # 跳过开头的 '.'
            if rest[i] != ".":
                break
            i += 1
            # 读取算子名
            j = i
            while j < n and (rest[j].isalnum() or rest[j] == "_"):
                j += 1
            name = rest[i:j]
            i = j
            if i < n and rest[i] == "(":
                # 读取括号内的参数（考虑引号和嵌套）
                args, i = DSLParser._read_paren_args(rest, i)
                ops.append(DSLParser._build_op(name, args))
            else:
                # 无括号：最终输出字段
                output = name
        return ops, output

    @staticmethod
    def _read_paren_args(s: str, start: int) -> Tuple[str, int]:
        """从 '(' 开始读取参数，返回 (参数原文, 结束位置)。"""
        assert s[start] == "("
        depth = 0
        quote: str = ""
        i = start
        while i < len(s):
            ch = s[i]
            if quote:
                if ch == "\\" and i + 1 < len(s):
                    i += 2
                    continue
                if ch == quote:
                    quote = ""
            else:
                if ch in "'\"":
                    quote = ch
                elif ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                    if depth == 0:
                        return s[start + 1 : i], i + 1
            i += 1
        raise DSLParseError(f"括号不匹配: {s[start:start+60]}...")

    @staticmethod
    def _split_args(args: str) -> List[str]:
        """按顶层逗号拆分参数，保留引号与嵌套括号。"""
        parts: List[str] = []
        depth = 0
        quote: str = ""
        cur = ""
        for ch in args:
            if quote:
                cur += ch
                if ch == "\\":
                    continue
                if ch == quote:
                    quote = ""
            else:
                if ch in "'\"":
                    quote = ch
                    cur += ch
                elif ch in "([":
                    depth += 1
                    cur += ch
                elif ch in ")]":
                    depth -= 1
                    cur += ch
                elif ch == "," and depth == 0:
                    parts.append(cur.strip())
                    cur = ""
                else:
                    cur += ch
        if cur.strip():
            parts.append(cur.strip())
        return parts

    @staticmethod
    def _unquote(s: str) -> str:
        """去掉外层单/双引号并反转义。"""
        s = s.strip()
        if len(s) >= 2 and s[0] in "'\"" and s[-1] == s[0]:
            return s[1:-1].replace("\\'", "'").replace('\\"', '"')
        return s

    @staticmethod
    def _parse_ops(ops_part: str) -> List[Op]:
        """解析取数逻辑中的算子串（无前导表名）。"""
        ops_part = ops_part.strip()
        if not ops_part:
            return []
        ops: List[Op] = []
        i = 0
        n = len(ops_part)
        while i < n:
            if ops_part[i] == ".":
                i += 1
            j = i
            while j < n and (ops_part[j].isalnum() or ops_part[j] == "_"):
                j += 1
            name = ops_part[i:j]
            i = j
            if i < n and ops_part[i] == "(":
                args, i = DSLParser._read_paren_args(ops_part, i)
                ops.append(DSLParser._build_op(name, args))
            else:
                raise DSLParseError(f"取数逻辑算子缺少参数: {name}")
        return ops

    @staticmethod
    def _build_op(name: str, args_text: str) -> Op:
        args = DSLParser._split_args(args_text)
        try:
            if name == "filter":
                if not args:
                    raise ValueError("filter 需要一个参数")
                return FilterOp(condition=DSLParser._unquote(args[0]))
            if name == "map":
                if not args:
                    raise ValueError("map 需要一个参数")
                return MapOp(cal_info=DSLParser._unquote(args[0]))
            if name == "reduce":
                if len(args) < 2:
                    raise ValueError("reduce 需要两个参数")
                return ReduceOp(
                    key=DSLParser._unquote(args[0]),
                    cal_info=DSLParser._unquote(args[1]),
                )
            if name == "union":
                if not args:
                    raise ValueError("union 需要一个参数")
                return UnionOp(table=DSLParser._unquote(args[0]))
            if name == "join":
                if len(args) < 3:
                    raise ValueError("join 需要三个参数")
                return JoinOp(
                    table=DSLParser._unquote(args[0]),
                    join_type=DSLParser._unquote(args[1]).lower(),
                    condition=DSLParser._unquote(args[2]),
                )
            if name == "limit":
                if not args:
                    raise ValueError("limit 需要一个参数")
                return LimitOp(n=int(DSLParser._unquote(args[0])))
            if name == "sort":
                if not args:
                    raise ValueError("sort 需要一个参数")
                return SortOp(expr=DSLParser._unquote(args[0]))
        except (ValueError, TypeError) as e:
            raise DSLParseError(f"算子 {name} 参数错误: {e}") from e
        raise DSLParseError(f"未知算子: {name}")


def _looks_like_op(s: str) -> bool:
    return bool(re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*\s*\(", s.strip()))


def parse_dsl(text: str) -> FeatureDSL:
    return DSLParser.parse(text)


def parse_fetch(text: str) -> Tuple[str, List[Op], List[str]]:
    return DSLParser.parse_fetch(text)
