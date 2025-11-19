"""WebUI API 路由"""
from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel, Field
from typing import Optional
from src.common.logger import get_logger
from .token_manager import get_token_manager
from .config_routes import router as config_router
from .statistics_routes import router as statistics_router
from .person_routes import router as person_router
from .expression_routes import router as expression_router
from .emoji_routes import router as emoji_router
from .plugin_routes import router as plugin_router
from .plugin_progress_ws import get_progress_router

logger = get_logger("webui.api")

# 创建路由器
router = APIRouter(prefix="/api/webui", tags=["WebUI"])

# 注册配置管理路由
router.include_router(config_router)
# 注册统计数据路由
router.include_router(statistics_router)
# 注册人物信息管理路由
router.include_router(person_router)
# 注册表达方式管理路由
router.include_router(expression_router)
# 注册表情包管理路由
router.include_router(emoji_router)
# 注册插件管理路由
router.include_router(plugin_router)
# 注册插件进度 WebSocket 路由
router.include_router(get_progress_router())


class TokenVerifyRequest(BaseModel):
    """Token 验证请求"""
    token: str = Field(..., description="访问令牌")


class TokenVerifyResponse(BaseModel):
    """Token 验证响应"""
    valid: bool = Field(..., description="Token 是否有效")
    message: str = Field(..., description="验证结果消息")


class TokenUpdateRequest(BaseModel):
    """Token 更新请求"""
    new_token: str = Field(..., description="新的访问令牌", min_length=10)


class TokenUpdateResponse(BaseModel):
    """Token 更新响应"""
    success: bool = Field(..., description="是否更新成功")
    message: str = Field(..., description="更新结果消息")


class TokenRegenerateResponse(BaseModel):
    """Token 重新生成响应"""
    success: bool = Field(..., description="是否生成成功")
    token: str = Field(..., description="新生成的令牌")
    message: str = Field(..., description="生成结果消息")


@router.get("/health")
async def health_check():
    """健康检查"""
    return {"status": "healthy", "service": "MaiBot WebUI"}


@router.post("/auth/verify", response_model=TokenVerifyResponse)
async def verify_token(request: TokenVerifyRequest):
    """
    验证访问令牌
    
    Args:
        request: 包含 token 的验证请求
        
    Returns:
        验证结果
    """
    try:
        token_manager = get_token_manager()
        is_valid = token_manager.verify_token(request.token)
        
        if is_valid:
            return TokenVerifyResponse(
                valid=True,
                message="Token 验证成功"
            )
        else:
            return TokenVerifyResponse(
                valid=False,
                message="Token 无效或已过期"
            )
    except Exception as e:
        logger.error(f"Token 验证失败: {e}")
        raise HTTPException(status_code=500, detail="Token 验证失败") from e


@router.post("/auth/update", response_model=TokenUpdateResponse)
async def update_token(
    request: TokenUpdateRequest,
    authorization: Optional[str] = Header(None)
):
    """
    更新访问令牌（需要当前有效的 token）
    
    Args:
        request: 包含新 token 的更新请求
        authorization: Authorization header (Bearer token)
        
    Returns:
        更新结果
    """
    try:
        # 验证当前 token
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="未提供有效的认证信息")
        
        current_token = authorization.replace("Bearer ", "")
        token_manager = get_token_manager()
        
        if not token_manager.verify_token(current_token):
            raise HTTPException(status_code=401, detail="当前 Token 无效")
        
        # 更新 token
        success, message = token_manager.update_token(request.new_token)
        
        return TokenUpdateResponse(
            success=success,
            message=message
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Token 更新失败: {e}")
        raise HTTPException(status_code=500, detail="Token 更新失败") from e


@router.post("/auth/regenerate", response_model=TokenRegenerateResponse)
async def regenerate_token(authorization: Optional[str] = Header(None)):
    """
    重新生成访问令牌（需要当前有效的 token）
    
    Args:
        authorization: Authorization header (Bearer token)
        
    Returns:
        新生成的 token
    """
    try:
        # 验证当前 token
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="未提供有效的认证信息")
        
        current_token = authorization.replace("Bearer ", "")
        token_manager = get_token_manager()
        
        if not token_manager.verify_token(current_token):
            raise HTTPException(status_code=401, detail="当前 Token 无效")
        
        # 重新生成 token
        new_token = token_manager.regenerate_token()
        
        return TokenRegenerateResponse(
            success=True,
            token=new_token,
            message="Token 已重新生成"
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Token 重新生成失败: {e}")
        raise HTTPException(status_code=500, detail="Token 重新生成失败") from e

