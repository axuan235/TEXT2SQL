"""DSL 解析与 SQL 生成测试。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.dsl import fetch_to_sql, parse_dsl, parse_fetch, to_sql
from app.dsl.ast import FilterOp, JoinOp, MapOp, ReduceOp


def test_parse_reduce():
    d = parse_dsl("student.avg_score -> t_student.reduce('student_id', 'avg(score) as avg_score').avg_score")
    assert d.entity == "student"
    assert d.attr == "avg_score"
    assert d.table == "t_student"
    assert len(d.ops) == 1
    assert isinstance(d.ops[0], ReduceOp)
    assert d.output == "avg_score"


def test_parse_raw_field():
    d = parse_dsl("gender -> t_student.gender")
    assert d.table == "t_student"
    assert d.ops == []
    assert d.output == "gender"
    assert d.attr == "gender"


def test_parse_full_chain():
    d = parse_dsl(
        r"student.avg_score -> t_student.filter('grade=\'2\' and  class=\'3\'').map('score-60 as score').reduce('student_id', 'avg(score) as avg_score').avg_score"
    )
    assert isinstance(d.ops[0], FilterOp)
    assert isinstance(d.ops[1], MapOp)
    assert isinstance(d.ops[2], ReduceOp)
    assert d.ops[0].condition == "grade='2' and  class='3'"
    assert d.ops[1].cal_info == "score-60 as score"
    assert d.ops[2].key == "student_id"


def test_roundtrip():
    d = parse_dsl("student.avg_score -> t_student.reduce('student_id', 'avg(score) as avg_score').avg_score")
    text = d.to_dsl()
    d2 = parse_dsl(text)
    assert d2.table == d.table
    assert d2.output == d.output


def test_to_sql_reduce():
    d = parse_dsl("student.avg_score -> t_student_score.reduce('student_id', 'avg(score) as avg_score').avg_score")
    sql = to_sql(d)
    assert "GROUP BY student_id" in sql
    assert "avg(score) as avg_score" in sql


def test_to_sql_map_reduce():
    d = parse_dsl(
        r"student.avg_score -> t_student.filter('grade=\'2\'').map('score-60 as score').reduce('student_id', 'avg(score) as avg_score').avg_score"
    )
    sql = to_sql(d)
    assert "score-60 as score" in sql
    assert "GROUP BY student_id" in sql


def test_fetch():
    subject, ops, cols = parse_fetch(
        'student.filter("avg_score>60 and birth_year>1999 and gender=1").limit(10)[full_name, avg_score,test_type,count_1]'
    )
    sql = fetch_to_sql(subject, ops, cols)
    assert sql.startswith("SELECT full_name, avg_score, test_type, count_1 FROM student")
    assert "LIMIT 10" in sql


def test_join_union():
    d = parse_dsl("t1.union(t2).union(t3).filter('a>1').reduce('k', 'sum(x) as sx').sx")
    assert len([o for o in d.ops if o.name == "union"]) == 2
    d2 = parse_dsl("t1.join(t2, 'left', 't1.id = t2.id').filter('t1.a>1')")
    assert isinstance(d2.ops[0], JoinOp)
    assert d2.ops[0].join_type == "left"
