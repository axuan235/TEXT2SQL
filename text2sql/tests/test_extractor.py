"""特征提取 / 知识库 / 注册服务测试。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import get_settings
from app.feature import FeatureExtractor
from app.kb import FeatureStructKB, QualifierKB, md5_of
from app.services import RegisterService

settings = get_settings()


def test_extract_agg():
    ex = FeatureExtractor()
    feats = ex.extract_from_sql(
        "SELECT student_id, avg(score) as avg_score FROM t_student_score WHERE test_type='期末' GROUP BY student_id"
    )
    assert len(feats) == 2
    avg = [f for f in feats if f.name == "avg_score"][0]
    assert len(avg.qualifier_conds) == 1
    assert "test_type" in avg.qualifier_conds[0] and "期末" in avg.qualifier_conds[0]
    assert "reduce" in avg.feature_struct


def test_extract_raw_field():
    ex = FeatureExtractor()
    feats = ex.extract_from_sql("SELECT gender FROM t_student")
    assert feats[0].name == "gender"


def test_naming_normalize():
    assert FeatureExtractor.normalize_name("avg", ["score"]) == "avg_score"
    assert FeatureExtractor.normalize_name("count", ["1"]) == "count_1"
    assert FeatureExtractor.normalize_name("concat", ["first_name", "last_name"]) == "concat_first_name_last_name"


def test_md5_dedup():
    kb = FeatureStructKB(settings.feature_struct_file)
    struct = "t_student_score.reduce('student_id', 'avg(score) as avg_score').avg_score"
    rec, is_new = kb.add_feature(struct, "avg_score")
    # 已存在（初始化数据中）
    assert kb.find_by_key(rec["MD5"]) is not None
    rec2, is_new2 = kb.add_feature("t_x.reduce('a', 'sum(b) as sb').sb", "sb")
    assert is_new2 is True


def test_register_service():
    svc = RegisterService(settings)
    result = svc.register(
        r"student.avg_score -> t_student_score.filter('grade=\'2\'').reduce('student_id', 'avg(score) as avg_score').avg_score"
    )
    assert result["feature_struct"]["name"] == "avg_score"
    assert result["qualifiers"]


def test_qualifier_mask():
    svc = RegisterService(settings)
    assert svc._mask("grade='2'") == "grade = X"
    assert svc._mask("score > 60") == "score > X"
