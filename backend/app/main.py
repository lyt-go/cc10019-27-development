"""用户标签画像与行为分析平台 后端服务入口。

启动：uvicorn app.main:app --host 127.0.0.1 --port 8000
健康检查：GET /api/health
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.routers import ROUTERS
from app.store import store

app = FastAPI(title="用户标签画像与行为分析平台", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def underscore_path_alias(request, call_next):
    """兼容前端下划线模块路径（/api/attr_dict/... → /api/attr-dict/...）。

    后端路由统一使用连字符，前端页面历史上使用下划线，这里只改写 /api 后
    第一段模块名中的下划线，其余路径段与查询串保持不变。
    """
    path = request.scope.get("path", "")
    if path.startswith("/api/"):
        head, separator, tail = path[len("/api/"):].partition("/")
        if "_" in head:
            aliased = f"/api/{head.replace('_', '-')}"
            if separator:
                aliased = f"{aliased}/{tail}"
            request.scope["path"] = aliased
            raw_path = request.scope.get("raw_path")
            if raw_path is not None:
                request.scope["raw_path"] = aliased.encode()
    return await call_next(request)

for module in ROUTERS:
    app.include_router(module.router)


@app.get("/api/health")
def health() -> dict[str, object]:
    """健康检查：确认服务已经监听、示例数据已经就绪。"""
    return {"ok": True, "app": settings.app_name, "modules": len(store.module_names())}


@app.get("/api/overview")
def overview() -> dict[str, object]:
    """运营概览：把各业务模块的待处理量汇总成看板卡片。"""
    return store.overview()
