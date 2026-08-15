"""FastAPI 应用入口。

启动：uvicorn app.main:app --reload
"""
from __future__ import annotations

from fastapi import FastAPI

from .api.routes import router
from .core.config import get_settings

settings = get_settings()

app = FastAPI(
    title="TEXT2SQL 用户描述转SQL模型系统",
    description=(
        "特征提取 / 特征生成 / 计算规划 / 用户描述转SQL模型 / 规则翻译\n\n"
        "三个业务接口：\n"
        "- POST /api/v1/parse     系统工作1：用户描述拆解 -> 结构化特征\n"
        "- POST /api/v1/generate  系统工作2：特征 -> 模型SQL + 计算路径 + 描述\n"
        "- POST /api/v1/register  系统工作3：注册计算路径到知识库"
    ),
    version=settings.app_version,
)

app.include_router(router)


@app.get("/")
def root():
    return {"service": settings.app_name, "version": settings.app_version}


@app.get("/health")
def health():
    return {"status": "ok", "llm_enabled": settings.llm_enabled}


def run():
    import uvicorn

    uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=False)


if __name__ == "__main__":
    run()
