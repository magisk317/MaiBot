"""WebUI 管理器 - 处理开发/生产环境的 WebUI 启动"""
import os
from pathlib import Path
from src.common.logger import get_logger
from .token_manager import get_token_manager

logger = get_logger("webui")


def setup_webui(mode: str = "production") -> bool:
    """
    设置 WebUI
    
    Args:
        mode: 运行模式，"development" 或 "production"
    
    Returns:
        bool: 是否成功设置
    """
    # 初始化 Token 管理器（确保 token 文件存在）
    token_manager = get_token_manager()
    current_token = token_manager.get_token()
    logger.info(f"🔑 WebUI Access Token: {current_token}")
    logger.info("💡 请使用此 Token 登录 WebUI")
    
    if mode == "development":
        return setup_dev_mode()
    else:
        return setup_production_mode()


def setup_dev_mode() -> bool:
    """设置开发模式 - 仅启用 CORS，前端自行启动"""
    logger.info("📝 WebUI 开发模式已启用")
    logger.info("🌐 请手动启动前端开发服务器: cd webui && npm run dev")
    logger.info("💡 前端将运行在 http://localhost:7999")
    return True


def setup_production_mode() -> bool:
    """设置生产模式 - 挂载静态文件"""
    try:
        from src.common.server import get_global_server
        from fastapi.staticfiles import StaticFiles
        from fastapi.responses import FileResponse
        
        server = get_global_server()
        base_dir = Path(__file__).parent.parent.parent
        static_path = base_dir / "webui" / "dist"
        
        if not static_path.exists():
            logger.warning(f"❌ WebUI 静态文件目录不存在: {static_path}")
            logger.warning("💡 请先构建前端: cd webui && npm run build")
            return False
        
        if not (static_path / "index.html").exists():
            logger.warning(f"❌ 未找到 index.html: {static_path / 'index.html'}")
            logger.warning("💡 请确认前端已正确构建")
            return False
        
        # 挂载静态资源
        if (static_path / "assets").exists():
            server.app.mount(
                "/assets",
                StaticFiles(directory=str(static_path / "assets")),
                name="assets"
            )
        
        # 处理 SPA 路由
        @server.app.get("/{full_path:path}")
        async def serve_spa(full_path: str):
            """服务单页应用"""
            # API 路由不处理
            if full_path.startswith("api/"):
                return None
            
            # 检查文件是否存在
            file_path = static_path / full_path
            if file_path.is_file():
                return FileResponse(file_path)
            
            # 返回 index.html（SPA 路由）
            return FileResponse(static_path / "index.html")
        
        host = os.getenv("HOST", "127.0.0.1")
        port = os.getenv("PORT", "8000")
        logger.info("✅ WebUI 生产模式已挂载")
        logger.info(f"🌐 访问 http://{host}:{port} 查看 WebUI")
        return True
        
    except Exception as e:
        logger.error(f"挂载 WebUI 静态文件失败: {e}")
        return False
