"""规则翻译测试。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import get_settings
from app.translate import Translator

t = Translator(get_settings())


def test_between():
    assert t.translate_condition("both_year between 2010 and 2012", "t_student") == "出生年在 2010年 与 2012年 之间"


def test_eq_mapping():
    assert t.translate_condition("gender = 1", "t_student") == "性别等于男"


def test_in_dict():
    assert t.translate_condition("city_code in (110000, 310000)", "t_student") == "城市编码在 北京、上海中"


def test_and_combine():
    r = t.translate_condition("test_type='期末' and score>60", "t_student_score")
    assert "考试类型等于期末" in r and "分数大于60分" in r


def test_suffix():
    assert t.translate_condition("grade >= 2", "t_student_score") == "年级大于等于2年级"


def test_function():
    assert t.translate_function("avg(score)", "t_student_score") == "分数的平均值"
    assert t.translate_function("count(1)", "t_student_score") == "数量"


def test_translate_dsl():
    from app.dsl import parse_dsl

    d = parse_dsl("student.avg_score -> t_student_score.reduce('student_id', 'avg(score) as avg_score').avg_score")
    text = t.translate_dsl(d)
    assert "分组" in text

test_translate_dsl()