"""全局配置加载。

配置来源优先级：
1. 环境变量 TEXT2SQL_CONFIG（配置文件路径）
2. 默认项目根目录下的 config/settings.yaml
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "settings.yaml"


class Settings:
    """扁平化的配置对象，提供属性访问。"""

    def __init__(self, data: Dict[str, Any], root: Path = PROJECT_ROOT):
        self._data = data
        self.root = root

    # ---- 路径解析辅助 ----
    def _resolve(self, rel: str) -> Path:
        p = Path(rel)
        if not p.is_absolute():
            p = self.root / p
        return p

    # ---- app ----
    @property
    def app_name(self) -> str:
        return self._data.get("app", {}).get("name", "text2sql")

    @property
    def app_version(self) -> str:
        return self._data.get("app", {}).get("version", "0.1.0")

    @property
    def host(self) -> str:
        return self._data.get("app", {}).get("host", "0.0.0.0")

    @property
    def port(self) -> int:
        return int(self._data.get("app", {}).get("port", 8000))

    # ---- kb ----
    @property
    def feature_struct_file(self) -> Path:
        return self._resolve(self._data.get("kb", {}).get("feature_struct_file", "data/feature_struct_kb.jsonl"))

    @property
    def qualifier_file(self) -> Path:
        return self._resolve(self._data.get("kb", {}).get("qualifier_file", "data/qualifier_kb.jsonl"))

    @property
    def dict_dir(self) -> Path:
        return self._resolve(self._data.get("kb", {}).get("dict_dir", "data/dict"))

    # ---- schema / join ----
    @property
    def table_schema_file(self) -> Path:
        return self._resolve(self._data.get("table_schema_file", "config/table_schema.json"))

    @property
    def join_keys_file(self) -> Path:
        return self._resolve(self._data.get("join_keys_file", "config/join_keys.json"))

    # ---- translate ----
    @property
    def compare_rules_file(self) -> Path:
        return self._resolve(self._data.get("translate", {}).get("compare_rules_file", "config/compare_trans_rules.json"))

    @property
    def func_rules_file(self) -> Path:
        return self._resolve(self._data.get("translate", {}).get("func_rules_file", "config/func_trans_rules.json"))

    # ---- llm ----
    @property
    def llm_enabled(self) -> bool:
        return bool(self._data.get("llm", {}).get("enabled", False))

    @property
    def llm_chat(self) -> Dict[str, str]:
        return self._data.get("llm", {}).get("chat", {})

    @property
    def llm_embedding(self) -> Dict[str, str]:
        return self._data.get("llm", {}).get("embedding", {})

    # ---- retrieval / candidate ----
    @property
    def retrieval_top_k(self) -> int:
        return int(self._data.get("retrieval", {}).get("top_k", 5))

    @property
    def retrieval_min_similarity(self) -> float:
        return float(self._data.get("retrieval", {}).get("min_similarity", 0.6))

    @property
    def candidate_max_combinations(self) -> int:
        return int(self._data.get("candidate", {}).get("max_combinations", 50))


def _load_config(path: Path) -> Dict[str, Any]:
    env_cfg = os.environ.get("TEXT2SQL_CONFIG")
    if env_cfg:
        path = Path(env_cfg)
    if not path.exists():
        raise FileNotFoundError(f"配置文件不存在: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings(_load_config(DEFAULT_CONFIG))
