"""三个业务 API 路由。

- POST /api/v1/parse       系统工作1：用户描述 -> 结构化特征
- POST /api/v1/generate    系统工作2：特征 -> 模型 SQL + 计算路径 + 描述
- POST /api/v1/register    系统工作3：注册计算路径到知识库
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from ..core.config import Settings, get_settings
from ..core.exceptions import Text2SQLError
from ..services import GenerateService, ParseService, RegisterService
from .schemas import (
    ApiResponse,
    GenerateRequest,
    ParseRequest,
    RegisterRequest,
)

router = APIRouter(prefix="/api/v1", tags=["text2sql"])


def _services(settings: Settings = Depends(get_settings)):
    """按请求构造服务（共享全局单例）。"""
    return {
        "parse": ParseService(settings),
        "generate": GenerateService(settings),
        "register": RegisterService(settings),
    }


@router.post("/parse", response_model=ApiResponse)
def parse_description(req: ParseRequest, services=Depends(_services)) -> ApiResponse:
    """系统工作1：用户描述拆解确认，结构化输出特征。"""
    try:
        data = services["parse"].parse(req.description)
        return ApiResponse(data=data)
    except Text2SQLError as e:
        return ApiResponse(code=400, message=str(e))


@router.post("/generate", response_model=ApiResponse)
def generate_model(req: GenerateRequest, services=Depends(_services)) -> ApiResponse:
    """系统工作2：生成模型 SQL 和模型描述。"""
    try:
        feats = [f.model_dump() for f in req.features]
        data = services["generate"].generate(feats, req.output_fields)
        return ApiResponse(data=data)
    except Text2SQLError as e:
        return ApiResponse(code=400, message=str(e))


@router.post("/register", response_model=ApiResponse)
def register_path(req: RegisterRequest, services=Depends(_services)) -> ApiResponse:
    """系统工作3：注册用户的计算路径到知识库。"""
    try:
        data = services["register"].register(req.dsl)
        return ApiResponse(data=data)
    except Text2SQLError as e:
        return ApiResponse(code=400, message=str(e))
