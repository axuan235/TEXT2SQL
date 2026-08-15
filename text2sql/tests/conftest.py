"""测试夹具：将知识库/数据文件重定向到临时目录，避免污染项目 data/ 目录。"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 沙箱环境下 mkdtemp 创建的目录 ACL 可能被拒绝访问，使用固定目录名
_TMP = Path(os.environ.get("TEXT2SQL_TEST_TMP", PROJECT_ROOT / ".test_tmp" / "text2sql_test"))
if _TMP.exists():
    shutil.rmtree(_TMP, ignore_errors=True)
_TMP_DATA = _TMP / "data"
_TMP_CONFIG = _TMP / "config"
_TMP_DATA.mkdir(parents=True, exist_ok=True)
_TMP_CONFIG.mkdir(parents=True, exist_ok=True)

# 复制现有数据（保证种子记录存在）与配置
for name in ("feature_struct_kb.jsonl", "qualifier_kb.jsonl"):
    src = PROJECT_ROOT / "data" / name
    if src.exists():
        shutil.copy(src, _TMP_DATA / name)
for name in ("compare_trans_rules.json", "func_trans_rules.json", "table_schema.json", "join_keys.json"):
    src = PROJECT_ROOT / "config" / name
    if src.exists():
        shutil.copy(src, _TMP_CONFIG / name)
shutil.copytree(PROJECT_ROOT / "data" / "dict", _TMP_DATA / "dict", dirs_exist_ok=True)

# 生成临时 settings.yaml
settings_template = (PROJECT_ROOT / "config" / "settings.yaml").read_text(encoding="utf-8")
data = yaml.safe_load(settings_template)
data["kb"]["feature_struct_file"] = str(_TMP_DATA / "feature_struct_kb.jsonl")
data["kb"]["qualifier_file"] = str(_TMP_DATA / "qualifier_kb.jsonl")
data["kb"]["dict_dir"] = str(_TMP_DATA / "dict")
data["table_schema_file"] = str(_TMP_CONFIG / "table_schema.json")
data["join_keys_file"] = str(_TMP_CONFIG / "join_keys.json")
data["translate"]["compare_rules_file"] = str(_TMP_CONFIG / "compare_trans_rules.json")
data["translate"]["func_rules_file"] = str(_TMP_CONFIG / "func_trans_rules.json")
with open(_TMP_CONFIG / "settings.yaml", "w", encoding="utf-8") as f:
    yaml.safe_dump(data, f, allow_unicode=True)

os.environ["TEXT2SQL_CONFIG"] = str(_TMP_CONFIG / "settings.yaml")

# 清空 get_settings 缓存（若 app 已被导入）
try:
    from app.core.config import get_settings

    get_settings.cache_clear()
except Exception:
    pass

sys.path.insert(0, str(PROJECT_ROOT))
