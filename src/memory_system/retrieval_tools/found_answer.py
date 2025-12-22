"""
found_answer工具 - 用于在记忆检索过程中标记找到答案
"""

from src.common.logger import get_logger
from .tool_registry import register_memory_retrieval_tool

logger = get_logger("memory_retrieval_tools")


async def found_answer(answer: str) -> str:
    """标记已找到问题的答案

    Args:
        answer: 找到的答案内容

    Returns:
        str: 确认信息
    """
    # 这个工具主要用于标记，实际答案会通过返回值传递
    logger.info(f"找到答案: {answer}")
    return f"已确认找到答案: {answer}"


def register_tool():
    """注册found_answer工具"""
    register_memory_retrieval_tool(
        name="found_answer",
        description="当你在已收集的信息中找到了问题的明确答案时，调用此工具标记已找到答案。只有在检索到明确、具体的答案时才使用此工具，不要编造信息。",
        parameters=[
            {
                "name": "answer",
                "type": "string",
                "description": "找到的答案内容，必须基于已收集的信息，不要编造",
                "required": True,
            },
        ],
        execute_func=found_answer,
    )

