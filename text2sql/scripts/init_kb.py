"""初始化脚本：从需求目录下的 _tmp.txt / 样例 xls 初始化配置与知识库。

用法：
    python scripts/init_kb.py            # 只初始化缺失的配置
    python scripts/init_kb.py --force    # 强制覆盖已有配置

会把项目根（TEXT2SQL 仓库根）下的：
  - 比较翻译规则_tmp.txt -> config/compare_trans_rules.json
  - 表结构信息_tmp.txt  -> config/table_schema.json
  - 知识库样例.xls      -> 读取后打印提示（内容与上述 txt 一致）
导入到项目 config/ 目录（JSON 格式）。
默认不覆盖已存在的配置（项目自带更完善的默认配置，如额外比较规则与多表结构）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = PROJECT_ROOT.parent  # TEXT2SQL 仓库根目录


def parse_compare_rules(path: Path) -> list:
    """解析 比较翻译规则_tmp.txt（制表符分隔）。"""
    rules = []
    with open(path, "r", encoding="utf-8-sig") as f:
        lines = [l.strip() for l in f if l.strip()]
    header = lines[0].split("\t")
    for idx, line in enumerate(lines[1:], start=1):
        cols = line.split("\t")
        rule = {
            "ID": int(cols[0]),
            "operator": cols[1],
            "struct": cols[2],
            "rule1": cols[3] if len(cols) > 3 else "",
            "rule2": cols[4] if len(cols) > 4 else "",
        }
        rules.append(rule)
    return rules


def parse_table_schema(path: Path) -> dict:
    """解析 表结构信息_tmp.txt（制表符分隔）。"""
    tables: dict = {"tables": []}
    with open(path, "r", encoding="utf-8-sig") as f:
        lines = [l.strip() for l in f if l.strip()]
    header = lines[0].split("\t")
    col_idx = {name: i for i, name in enumerate(header)}
    current = None
    for line in lines[1:]:
        cols = line.split("\t")
        space, tname, tcn, col, col_cn, suffix, mapping, dtype = (
            cols[col_idx.get("表空间", 0)] if col_idx.get("表空间", 0) < len(cols) else "",
            cols[col_idx["表名称"]] if col_idx["表名称"] < len(cols) else "",
            cols[col_idx["中文表名"]] if col_idx["中文表名"] < len(cols) else "",
            cols[col_idx["列名"]] if col_idx["列名"] < len(cols) else "",
            cols[col_idx["中文列名"]] if col_idx["中文列名"] < len(cols) else "",
            cols[col_idx["翻译补全"]] if col_idx["翻译补全"] < len(cols) else "",
            cols[col_idx["字段映射信息"]] if col_idx["字段映射信息"] < len(cols) else "",
            cols[col_idx["数据类型"]] if col_idx["数据类型"] < len(cols) else "",
        )
        if current is None or current["table_name"] != tname:
            current = {
                "table_space": space,
                "table_name": tname,
                "table_cn": tcn,
                "columns": [],
            }
            tables["tables"].append(current)
        current["columns"].append(
            {
                "col": col,
                "col_cn": col_cn,
                "trans_suffix": suffix,
                "mapping": mapping,
                "data_type": dtype,
            }
        )
    return tables


def main() -> None:
    force = "--force" in sys.argv
    compare_src = REPO_ROOT / "比较翻译规则_tmp.txt"
    schema_src = REPO_ROOT / "表结构信息_tmp.txt"
    xls_src = REPO_ROOT / "知识库样例.xls"

    def write_if_missing(target: Path, data, label: str) -> None:
        if target.exists() and not force:
            print(f"[skip] {label} 已存在（{target}），如需覆盖请使用 --force")
            return
        target.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[ok] {label} -> {target}")

    if compare_src.exists():
        write_if_missing(
            PROJECT_ROOT / "config" / "compare_trans_rules.json",
            parse_compare_rules(compare_src),
            "比较翻译规则",
        )
    else:
        print(f"[skip] 未找到 {compare_src}")

    if schema_src.exists():
        write_if_missing(
            PROJECT_ROOT / "config" / "table_schema.json",
            parse_table_schema(schema_src),
            "表结构信息",
        )
    else:
        print(f"[skip] 未找到 {schema_src}")

    if xls_src.exists():
        print(f"[info] 知识库样例.xls 内容与上述 txt 一致（含比较翻译规则、表结构信息），"
              f"如需读取请安装 openpyxl/xlrd。")
    else:
        print(f"[skip] 未找到 {xls_src}")

    print("初始化完成。知识库文件（data/*.jsonl）已随项目附带种子数据，可直接启动。")


if __name__ == "__main__":
    sys.exit(main())
