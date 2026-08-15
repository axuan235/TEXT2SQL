"""特征生成 / 计算规划 / API 测试。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import get_settings
from app.dsl import parse_dsl
from app.feature import ComputationPlanner, DescriptionParser, FeatureGenerator
from app.services import GenerateService, ParseService

settings = get_settings()


def test_description_parse():
    p = DescriptionParser()
    sf = p.parse("链家访客_用户_最近三天_访问次数：>5")
    assert sf.domain == "链家访客" or sf.domain == "链家"
    assert sf.subject in ("用户",)
    assert sf.attr == "访问次数"
    assert sf.value_cond == ">5"


def test_description_parse_simple():
    p = DescriptionParser()
    sf = p.parse("学生_平均分：>60")
    assert sf.subject == "学生"
    assert sf.attr == "平均分"


def test_generator_kb_hit():
    g = FeatureGenerator(settings)
    result = g.generate("学生_平均分：>60")
    assert result["structured"]["subject"] == "学生"
    assert "from_kb" in result


def test_planner_merge():
    planner = ComputationPlanner(settings)
    f1 = parse_dsl(
        r"student.avg_score -> t_student_score.filter('test_type=\'期末\'').reduce('student_id', 'avg(score) as avg_score').avg_score"
    )
    f2 = parse_dsl(
        r"student.count_1 -> t_student_score.filter('test_type=\'期末\'').reduce('student_id', 'count(1) as count_1').count_1"
    )
    plan = planner.plan([f1, f2])
    assert "avg(score) as avg_score" in plan.sql
    assert "count(1) as count_1" in plan.sql
    assert plan.sql.count("GROUP BY") == 1  # 合并为一条 SQL


def test_planner_group():
    planner = ComputationPlanner(settings)
    f1 = parse_dsl("gender -> t_student.gender")
    f2 = parse_dsl("student_id -> t_student.student_id")
    groups = planner.group_by_common_base([f1, f2])
    assert len(groups) == 1  # 同表原始字段可合并


def test_parse_service():
    svc = ParseService(settings)
    result = svc.parse("找最近三个月与多位不同男性同住的年轻女性")
    assert result["features"]
    # A1 模板：同住人数 + 性别 + 年龄
    assert any("同住" in f["attr"] for f in result["features"])
    assert any(f["attr"] == "性别" for f in result["features"])
    assert any(f["attr"] == "年龄" for f in result["features"])


def test_generate_service():
    svc = GenerateService(settings)
    result = svc.generate(
        [{"dsl": r"student.avg_score -> t_student_score.reduce('student_id', 'avg(score) as avg_score').avg_score"}],
        output_fields=["student_id", "avg_score"],
    )
    assert result["model_sql"]
    assert result["fetch_sql"]
