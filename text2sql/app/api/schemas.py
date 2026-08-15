"""API 数据模型。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ---------- 系统工作1：用户描述拆解 ----------

class ParseRequest(BaseModel):
    description: str = Field(..., description="用户需求描述，如：找最近三个月与多位不同男性同住的年轻女性")


# ---------- 系统工作2：模型生成 ----------

class FeatureInput(BaseModel):
    text: Optional[str] = Field(None, description="自然语言特征描述，如 学生_平均分：>60")
    dsl: Optional[str] = Field(None, description="已确认的特征 DSL，如 student.avg_score -> t_student_score.reduce(...)")


class GenerateRequest(BaseModel):
    features: List[FeatureInput] = Field(..., description="特征列表（自然语言描述或已确认 DSL）")
    output_fields: Optional[List[str]] = Field(None, description="输出字段列表，如 [用户姓名, 身份证]")


# ---------- 系统工作3：计算路径注册 ----------

class RegisterRequest(BaseModel):
    dsl: str = Field(..., description="特征计算路径 DSL，如 student.avg_score -> t_student_score.reduce('student_id', 'avg(score) as avg_score').avg_score")


# ---------- 通用响应 ----------

class ApiResponse(BaseModel):
    code: int = 0
    message: str = "ok"
    data: Optional[Any] = None
