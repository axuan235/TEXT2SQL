"""FastAPI 接口测试（使用 TestClient）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_parse_api():
    r = client.post("/api/v1/parse", json={"description": "找最近三个月与多位不同男性同住的年轻女性"})
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == 0
    assert "features" in body["data"]


def test_generate_api():
    r = client.post(
        "/api/v1/generate",
        json={
            "features": [
                {"dsl": "student.avg_score -> t_student_score.reduce('student_id', 'avg(score) as avg_score').avg_score"}
            ],
            "output_fields": ["student_id", "avg_score"],
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == 0
    assert "model_sql" in body["data"]


def test_register_api():
    r = client.post(
        "/api/v1/register",
        json={"dsl": "student.cnt -> t_demo.filter('a=1').reduce('k', 'count(1) as cnt').cnt"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == 0
    # 命名规范化：cnt -> count_1
    assert body["data"]["feature_struct"]["name"] == "count_1"
