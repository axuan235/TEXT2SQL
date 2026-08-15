"""端到端演示：走通 系统工作1 -> 2 -> 3 全流程（离线模式）。

用法：
    python scripts/demo.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import get_settings
from app.services import GenerateService, ParseService, RegisterService


def main() -> None:
    settings = get_settings()
    parse_svc = ParseService(settings)
    gen_svc = GenerateService(settings)
    reg_svc = RegisterService(settings)

    print("=" * 70)
    print("系统工作1：用户描述拆解")
    print("=" * 70)
    r1 = parse_svc.parse("找最近三个月与多位不同男性同住的年轻女性")
    for f in r1["features"]:
        print(f"  特征：{f}")
    print(f"  输出字段：{r1['output_fields']}")

    print()
    print("=" * 70)
    print("系统工作2：生成模型 SQL 与计算路径")
    print("=" * 70)
    r2 = gen_svc.generate(
        [
            {"dsl": r"student.avg_score -> t_student_score.filter('test_type=\'期末\'').reduce('student_id', 'avg(score) as avg_score').avg_score"},
            {"dsl": r"student.count_1 -> t_student_score.filter('test_type=\'期末\'').reduce('student_id', 'count(1) as count_1').count_1"},
            {"dsl": "gender -> t_student.gender"},
            {"dsl": "student_id -> t_student.student_id"},
        ],
        output_fields=["full_name", "avg_score", "test_type", "count_1"],
    )
    print("模型 SQL：")
    print(r2["model_sql"])
    print()
    print("计算路径：")
    for p in r2["calculation_paths"]:
        print(f"  {p}")
    print()
    print("模型描述：", r2["model_description"])
    print("取数 SQL：", r2["fetch_sql"])

    print()
    print("=" * 70)
    print("系统工作3：注册计算路径到知识库")
    print("=" * 70)
    r3 = reg_svc.register(
        r"student.bar_score -> t_student_score.filter('grade=\'2\' and  class=\'3\'').map('concat(first_name,last_name) as full_name').reduce('student_id', 'avg(score) as bar_score').bar_score"
    )
    print(f"  特征结构：{r3['feature_struct']['feature_struct']}")
    print(f"  字段名（规范化后）：{r3['feature_struct']['name']}")
    print(f"  限定词：{[q['compare_struct'] for q in r3['qualifiers']]}")
    print(f"  新增限定词数量：{r3['qualifiers_new']}")

    print()
    print("=" * 70)
    print("规则翻译示例")
    print("=" * 70)
    from app.translate import Translator

    t = Translator(settings)
    for cond, table in [
        ("both_year between 2010 and 2012", "t_student"),
        ("gender = 1", "t_student"),
        ("city_code in (110000, 310000)", "t_student"),
    ]:
        print(f"  {cond}  ->  {t.translate_condition(cond, table)}")


if __name__ == "__main__":
    main()
