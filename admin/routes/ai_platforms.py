from fastapi import APIRouter, Depends, HTTPException
from typing import List, Dict, Any

router = APIRouter()

@router.get("/ai-platforms")
async def get_ai_platforms() -> List[Dict[str, Any]]:
    """获取所有AI平台列表"""
    return []

@router.post("/ai-platforms/{platform_id}/enable")
async def enable_ai_platform(platform_id: str) -> Dict[str, Any]:
    """启用指定的AI平台"""
    return {"status": "success", "message": f"Platform {platform_id} enabled"}

@router.post("/ai-platforms/{platform_id}/disable")
async def disable_ai_platform(platform_id: str) -> Dict[str, Any]:
    """禁用指定的AI平台"""
    return {"status": "success", "message": f"Platform {platform_id} disabled"} 