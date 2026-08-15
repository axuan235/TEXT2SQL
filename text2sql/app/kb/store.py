"""知识库存储：JSONL 纯文本管理 + MD5 去重。

知识库分为：
- 特征结构库（feature_struct）：feature_struct / name / rule_trans / valid_tans / table_list / MD5
- 限定词库（qualifier）：compare_struct / value_type / rule_trans / valid_tans / table / c_md5

需求要求：知识库用纯文本管理即可，方便人工核验后生效；入库前需要去重校验。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Optional


def md5_of(text: str) -> str:
    """结构体唯一 ID。"""
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def normalize_struct(struct: str) -> str:
    """规范化结构文本（压缩空白），保证去重的一致性。"""
    return " ".join(struct.split())


class KnowledgeBase:
    """基于 JSONL 的知识库。"""

    def __init__(self, path: Path, key_field: str):
        self.path = path
        self.key_field = key_field
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text("", encoding="utf-8")

    def load_all(self) -> List[Dict[str, Any]]:
        records = []
        if not self.path.exists():
            return records
        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return records

    def exists(self, key_value: str) -> bool:
        return any(r.get(self.key_field) == key_value for r in self.load_all())

    def add(self, record: Dict[str, Any], key_value: Optional[str] = None) -> Dict[str, Any]:
        """入库；若已存在则返回已有记录（不去重插入）。"""
        key = key_value or record.get(self.key_field)
        if not key:
            raise ValueError(f"知识库记录缺少主键字段 {self.key_field}")
        existing = self.find_by_key(key)
        if existing:
            return existing
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        return record

    def find_by_key(self, key_value: str) -> Optional[Dict[str, Any]]:
        for r in self.load_all():
            if r.get(self.key_field) == key_value:
                return r
        return None

    def search(self, **kwargs) -> List[Dict[str, Any]]:
        """按字段值筛选（OR 语义：任一字段命中即返回）。"""
        if not kwargs:
            return self.load_all()
        out = []
        for r in self.load_all():
            if any(r.get(k) == v for k, v in kwargs.items()):
                out.append(r)
        return out

    def remove(self, key_value: str) -> bool:
        records = self.load_all()
        kept = [r for r in records if r.get(self.key_field) != key_value]
        if len(kept) == len(records):
            return False
        self._rewrite(kept)
        return True

    def _rewrite(self, records: List[Dict[str, Any]]) -> None:
        with open(self.path, "w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    def __len__(self) -> int:
        return len(self.load_all())


class FeatureStructKB(KnowledgeBase):
    """特征结构库。"""

    def __init__(self, path: Path):
        super().__init__(path, key_field="MD5")

    @staticmethod
    def build_record(
        feature_struct: str,
        name: str,
        rule_trans: str = "",
        valid_tans: str = "",
        table_list: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        struct = normalize_struct(feature_struct)
        return {
            "feature_struct": struct,
            "name": name,
            "rule_trans": rule_trans,
            "valid_tans": valid_tans,  # 人工标注内容，首次入库留白
            "table_list": table_list or [],
            "MD5": md5_of(struct),
        }

    def add_feature(self, feature_struct: str, name: str, rule_trans: str = "", valid_tans: str = "", table_list=None):
        record = self.build_record(feature_struct, name, rule_trans, valid_tans, table_list)
        existed = self.find_by_key(record["MD5"])
        if existed is None:
            self.add(record, record["MD5"])
        return record, existed is None  # (record, 是否新增)


class QualifierKB(KnowledgeBase):
    """限定词库。"""

    def __init__(self, path: Path):
        super().__init__(path, key_field="c_md5")

    @staticmethod
    def build_record(
        compare_struct: str,
        value_type: str,
        rule_trans: str = "",
        valid_tans: str = "",
        table: str = "",
    ) -> Dict[str, Any]:
        struct = normalize_struct(compare_struct)
        return {
            "compare_struct": struct,
            "value_type": value_type,
            "rule_trans": rule_trans,
            "valid_tans": valid_tans,
            "table": table,
            "c_md5": md5_of(f"{table}|{struct}"),
        }

    def add_qualifier(self, compare_struct, value_type="string", rule_trans="", valid_tans="", table=""):
        record = self.build_record(compare_struct, value_type, rule_trans, valid_tans, table)
        existed = self.find_by_key(record["c_md5"])
        if existed is None:
            self.add(record, record["c_md5"])
        return record, existed is None
