"""规则翻译引擎。

依据配置文件（比较翻译规则 / 函数翻译规则）与表结构信息进行翻译。
支持模板函数：
  - transCol(col)      ：将列名翻译为中文列名
  - transValue(v)      ：按字段翻译补全/映射信息翻译取值
  - stringFormat(list) ：格式化参数列表（如 in 列表）
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..core.config import Settings
from ..core.exceptions import TranslationError


class TableSchema:
    """表结构信息（来自 table_schema.json）。"""

    def __init__(self, schema_path: Path):
        with open(schema_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self._tables: Dict[str, Dict[str, Any]] = {}
        for t in data.get("tables", []):
            cols = {c["col"]: c for c in t.get("columns", [])}
            self._tables[t["table_name"]] = {
                "table_cn": t.get("table_cn", ""),
                "columns": cols,
            }

    def tables(self) -> List[str]:
        return list(self._tables.keys())

    def get_column(self, table: str, col: str) -> Optional[Dict[str, Any]]:
        t = self._tables.get(table)
        if not t:
            return None
        return t["columns"].get(col)

    def table_cn(self, table: str) -> str:
        t = self._tables.get(table)
        return t["table_cn"] if t else table


class DictStore:
    """映射编码字典（如城市编码 F100）。"""

    def __init__(self, dict_dir: Path):
        self._dict_dir = dict_dir
        self._cache: Dict[str, Dict[str, str]] = {}

    def lookup(self, dict_code: str, key: str) -> Optional[str]:
        if dict_code not in self._cache:
            path = self._dict_dir / f"{dict_code}.json"
            if not path.exists():
                return None
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._cache[dict_code] = {str(k): str(v) for k, v in data.get("values", {}).items()}
        return self._cache[dict_code].get(str(key))


class RuleEngine:
    """基于配置文件的翻译规则引擎。"""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.schema = TableSchema(settings.table_schema_file)
        self.dicts = DictStore(settings.dict_dir)
        self._compare_rules = self._load_json(settings.compare_rules_file)
        self._func_rules = self._load_json(settings.func_rules_file)
        # 预编译规则：struct -> (regex, 模板函数)
        self._compiled = [self._compile_rule(r) for r in self._compare_rules]

    @staticmethod
    def _load_json(path: Path) -> List[Dict[str, Any]]:
        if not path.exists():
            return []
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    # ---------- 模板编译 ----------

    _PLACEHOLDER_RE = re.compile(r"\{([\w()]+)\}")

    def _compile_rule(self, rule: Dict[str, Any]) -> Dict[str, Any]:
        struct = rule.get("struct", "")
        # 1) 先处理 args 占位符（含其外层括号）：({args0}) -> \((?P<args0>...)\)
        regex_src = struct
        for m in re.finditer(r"\(\{(\w+)\}\)", struct):
            name = m.group(1)
            if name.startswith("args"):
                pat = (
                    r"\((?P<%s>(?:'[^']*'|\"[^\"]*\"|[0-9]+(?:\.[0-9]+)?)"
                    r"(?:\s*,\s*(?:'[^']*'|\"[^\"]*\"|[0-9]+(?:\.[0-9]+)?))*)\)"
                ) % name
                regex_src = regex_src.replace("({%s})" % name, pat)
        # 2) 再处理其余占位符：{col0} / {arg0}
        for m in self._PLACEHOLDER_RE.finditer(regex_src):
            name = m.group(1)
            if name.startswith("col"):
                pat = r"(?P<%s>[A-Za-z_][A-Za-z0-9_.]*)" % name
            elif name.startswith("args"):
                continue  # 已在第 1 步处理
            else:  # arg
                pat = r"(?P<%s>(?:'[^']*'|\"[^\"]*\"|[0-9]+(?:\.[0-9]+)?))" % name
            regex_src = regex_src.replace("{%s}" % name, pat)
        try:
            # 将字面空格替换为 \s*，容忍 "col=1" / "col = 1" 写法（不影响已转义的 \s）
            flexible = re.sub(r"(?<!\\) ", r"\\s*", regex_src)
            regex = re.compile(flexible, re.I)
        except re.error as e:
            raise TranslationError(f"翻译规则正则编译失败: {struct} -> {e}") from e
        return {
            "rule": rule,
            "regex": regex,
            "col_names": [m.group(1) for m in self._PLACEHOLDER_RE.finditer(struct) if m.group(1).startswith("col")],
            "arg_names": [m.group(1) for m in self._PLACEHOLDER_RE.finditer(struct) if m.group(1).startswith("arg")],
        }

    # ---------- 模板函数 ----------

    def trans_col(self, col: str, table: Optional[str] = None) -> str:
        """翻译列名。支持 'table.col' 前缀；返回中文列名。"""
        tbl, col_name = self._split_qualified(col, table)
        info = self.schema.get_column(tbl, col_name) if tbl else None
        if info and info.get("col_cn"):
            return info["col_cn"]
        # 未找到时回退：去掉表前缀
        return col_name if "." not in col else col.split(".")[-1]

    def trans_value(self, value: str, table: Optional[str] = None, col: Optional[str] = None) -> str:
        """翻译取值：先去引号，再按字段映射信息/翻译补全/字典翻译。"""
        v = value.strip()
        if (len(v) >= 2 and v[0] in "'\"" and v[-1] == v[0]):
            v = v[1:-1]
        tbl, col_name = self._split_qualified(col or "", table)
        info = self.schema.get_column(tbl, col_name) if tbl and col_name else None
        if not info:
            return v
        mapping = info.get("mapping", "") or ""
        trans_suffix = info.get("trans_suffix", "") or ""
        if mapping:
            # 字典编码：dict:F100
            if mapping.startswith("dict:"):
                code = mapping.split(":", 1)[1]
                hit = self.dicts.lookup(code, v)
                if hit:
                    return hit
            else:
                # 简单映射：1:男;2:女
                for pair in mapping.split(";"):
                    if ":" in pair:
                        k, _, label = pair.partition(":")
                        if k.strip() == v:
                            return label.strip()
        if trans_suffix and "{value}" in trans_suffix:
            return trans_suffix.replace("{value}", v)
        return v

    def string_format(self, args: str, table: Optional[str] = None, col: Optional[str] = None) -> str:
        """格式化参数列表：'a, b, c' -> 'a、b、c'（单个参数直接返回）。"""
        parts = [p.strip() for p in args.split(",") if p.strip()]
        trans = [self.trans_value(p, table, col) for p in parts]
        if not trans:
            return ""
        if len(trans) == 1:
            return trans[0]
        return "、".join(trans)

    @staticmethod
    def _split_qualified(col: str, table: Optional[str]) -> tuple:
        if "." in col:
            tbl, _, c = col.partition(".")
            return tbl, c
        return table or "", col

    # ---------- 翻译主流程 ----------

    def _fill_template(self, template: str, groups: Dict[str, str], table: Optional[str], cols: List[str]) -> str:
        """填充翻译模板，执行模板函数。"""

        def repl(m: re.Match) -> str:
            token = m.group(1)
            col_name = None
            if cols:
                col_name = groups.get(cols[0], "").split(".")[-1]
            # 嵌套调用：stringFormat(transValue(args0))
            nested = re.match(r"(\w+)\((\w+)\((\w+)\)\)", token)
            if nested:
                outer_fn, inner_fn, inner_arg = nested.group(1), nested.group(2), nested.group(3)
                raw = groups.get(inner_arg, "")
                if inner_fn == "transValue" and outer_fn == "stringFormat":
                    parts = [p.strip() for p in raw.split(",") if p.strip()]
                    trans = [self.trans_value(p, table, col_name) for p in parts]
                    return "、".join(trans) if len(trans) > 1 else (trans[0] if trans else "")
                if outer_fn == "stringFormat":
                    return self.string_format(raw, table, col_name)
            # 单层函数调用：transCol(col0) / transValue(arg0) / stringFormat(args0)
            func_match = re.match(r"(\w+)\((\w+)\)", token)
            if func_match:
                fn, arg_name = func_match.group(1), func_match.group(2)
                raw = groups.get(arg_name, "")
                if fn == "transCol":
                    return self.trans_col(raw, table)
                if fn == "transValue":
                    return self.trans_value(raw, table, col_name)
                if fn == "stringFormat":
                    return self.string_format(raw, table, col_name)
                return raw
            # 裸占位符：{arg0} 原样输出（去掉引号），{col0} 翻译列名
            raw = groups.get(token, "")
            if token.startswith("col"):
                return self.trans_col(raw, table)
            return self.trans_value(raw, table)

        return self._PLACEHOLDER_RE.sub(repl, template)

    def translate_condition(self, condition: str, table: Optional[str] = None) -> str:
        """翻译一个条件表达式（支持 and/or 组合）。"""
        if not condition or not condition.strip():
            return ""
        cond = condition.strip()
        # 1) 先尝试整条条件匹配规则（如 between 包含 and，需整体匹配）
        matched = self._match_rule(cond, table)
        if matched:
            return matched
        # 2) 再按顶层 and/or 拆分
        top_parts = self._split_top_level(cond)
        if len(top_parts) > 1:
            translated = [self.translate_condition(p, table) for p, _ in top_parts]
            seps = [sep for _, sep in top_parts if sep]
            out = translated[0]
            for i in range(1, len(translated)):
                out += f" {seps[i - 1]} " + translated[i]
            return out
        # 3) 未匹配任何规则：原样返回（去掉多余空白）
        return re.sub(r"\s+", " ", cond)

    def _match_rule(self, cond: str, table: Optional[str]) -> str:
        """用规则匹配单个条件，命中则返回翻译结果，否则返回空串。"""
        for compiled in self._compiled:
            m = compiled["regex"].fullmatch(cond.strip())
            if m:
                groups = m.groupdict()
                rule = compiled["rule"]
                for tpl_key in ("rule1", "rule2"):
                    tpl = rule.get(tpl_key) or ""
                    if not tpl:
                        continue
                    result = self._fill_template(tpl, groups, table, compiled["col_names"])
                    if result:
                        return result
        return ""

    @staticmethod
    def _split_top_level(s: str) -> List[tuple]:
        """按顶层 and/or 切分，返回 [(子串, 连接下一个子串的分隔符)]。"""
        depth = 0
        quote = ""
        chunks: List[str] = []
        seps: List[str] = []
        cur = ""
        i = 0
        n = len(s)
        while i < n:
            ch = s[i]
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
            if depth == 0:
                lower = s[i:].lower()
                sep = None
                if lower.startswith("and") and (i + 3 >= n or s[i + 3].isspace()):
                    sep = "and"
                elif lower.startswith("or") and (i + 2 >= n or s[i + 2].isspace()):
                    sep = "or"
                if sep:
                    if cur.strip():
                        chunks.append(cur.strip())
                    seps.append(sep)
                    cur = ""
                    i += len(sep)
                    continue
            cur += ch
            i += 1
        if cur.strip():
            chunks.append(cur.strip())
        if not chunks:
            return []
        result = []
        for idx, c in enumerate(chunks):
            sep = seps[idx] if idx < len(seps) else None
            result.append((c, sep))
        return result

    def translate_function(self, expr: str, table: Optional[str] = None) -> str:
        """翻译函数表达式，如 avg(score)、concat(first_name,last_name)。"""
        for rule in self._func_rules:
            struct = rule.get("struct", "")
            m = re.match(r"\{fn\}\((.*)\)", struct)
            if not m:
                continue
            args_part = m.group(1)
            arg_names = [x for x in re.findall(r"\{(\w+)\}", args_part)]
            # 匹配实际表达式
            call = re.match(r"([a-zA-Z_]\w*)\((.*)\)", expr.strip())
            if not call:
                continue
            fn_name, args_str = call.group(1), call.group(2)
            if fn_name.lower() != rule.get("name", "").lower():
                continue
            args = [a.strip() for a in args_str.split(",") if a.strip()]
            groups = {}
            for idx, name in enumerate(arg_names):
                if idx < len(args):
                    groups[name] = args[idx]
            if rule.get("struct") and "{fn}" in rule.get("struct", ""):
                groups["fn"] = fn_name
            tpl = rule.get("rule1") or ""
            if not tpl:
                return expr
            result = self._fill_template(tpl, groups, table, [n for n in arg_names if n.startswith("col")])
            # count(1) 等无列名聚合：使用 rule2 兜底
            if result and "的计数" in result and not any(
                self.schema.get_column(table, c) for c in re.findall(r"[A-Za-z_]\w*", args_str) if c != fn_name
            ):
                alt = rule.get("rule2") or ""
                if alt:
                    return alt
            return result
        return expr
