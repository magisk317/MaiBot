"""
根据关键词在jargon库中查询 - 工具实现
"""

from src.common.logger import get_logger
from src.jargon.jargon_miner import search_jargon
from .tool_registry import register_memory_retrieval_tool

logger = get_logger("memory_retrieval_tools")


async def query_jargon(keyword: str, chat_id: str) -> str:
    """根据关键词在jargon库中查询

    Args:
        keyword: 关键词（黑话/俚语/缩写）
        chat_id: 聊天ID

    Returns:
        str: 查询结果
    """
    try:
        content = str(keyword).strip()
        if not content:
            return "关键词为空"

        # 先尝试精确匹配
        results = search_jargon(keyword=content, chat_id=chat_id, limit=10, case_sensitive=False, fuzzy=False)

        is_fuzzy_match = False

        # 如果精确匹配未找到，尝试模糊搜索
        if not results:
            results = search_jargon(keyword=content, chat_id=chat_id, limit=10, case_sensitive=False, fuzzy=True)
            is_fuzzy_match = True

        if results:
            # 如果是模糊匹配，显示找到的实际jargon内容
            if is_fuzzy_match:
                # 处理多个结果
                output_parts = [f"未精确匹配到'{content}'"]
                for result in results:
                    found_content = result.get("content", "").strip()
                    meaning = result.get("meaning", "").strip()
                    if found_content and meaning:
                        output_parts.append(f"找到 '{found_content}' 的含义为：{meaning}")
                output = "，".join(output_parts)
                logger.info(f"在jargon库中找到匹配（当前会话或全局，模糊搜索）: {content}，找到{len(results)}条结果")
            else:
                # 精确匹配，可能有多条（相同content但不同chat_id的情况）
                output_parts = []
                for result in results:
                    meaning = result.get("meaning", "").strip()
                    if meaning:
                        output_parts.append(f"'{content}' 为黑话或者网络简写，含义为：{meaning}")
                output = "；".join(output_parts) if len(output_parts) > 1 else output_parts[0]
                logger.info(f"在jargon库中找到匹配（当前会话或全局，精确匹配）: {content}，找到{len(results)}条结果")
            return output

        # 未命中
        logger.info(f"在jargon库中未找到匹配（当前会话或全局，精确匹配和模糊搜索都未找到）: {content}")
        return f"未在jargon库中找到'{content}'的解释"

    except Exception as e:
        logger.error(f"查询jargon失败: {e}")
        return f"查询失败: {str(e)}"


def register_tool():
    """注册工具"""
    register_memory_retrieval_tool(
        name="query_jargon",
        description="根据关键词在jargon库中查询黑话/俚语/缩写的含义。支持大小写不敏感搜索，默认会先尝试精确匹配，如果找不到则自动使用模糊搜索。仅搜索当前会话或全局jargon。",
        parameters=[{"name": "keyword", "type": "string", "description": "关键词（黑话/俚语/缩写）", "required": True}],
        execute_func=query_jargon,
    )
