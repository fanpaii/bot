"""
注册所有路由
"""
from fastapi import FastAPI
from loguru import logger

def register_all_routes(app: FastAPI):
    """注册所有路由"""
    try:
        # 注册AI平台路由
        from .ai_platforms import router as ai_platforms_router
        app.include_router(ai_platforms_router, prefix="/api/ai-platforms", tags=["AI平台"])
        logger.info("已注册AI平台路由")
    except Exception as e:
        logger.error(f"注册AI平台路由失败: {e}")
