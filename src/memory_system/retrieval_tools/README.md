# 记忆检索工具模块

这个模块提供了统一的工具注册和管理系统，用于记忆检索功能。

## 目录结构

```
retrieval_tools/
├── __init__.py              # 模块导出
├── tool_registry.py         # 工具注册系统
├── tool_utils.py            # 工具函数库（共用函数）
├── query_jargon.py          # 查询jargon工具
├── query_chat_history.py    # 查询聊天历史工具
├── query_lpmm_knowledge.py  # 查询LPMM知识库工具
└── README.md                # 本文件
```

## 模块说明

### `tool_registry.py`
包含工具注册系统的核心类：
- `MemoryRetrievalTool`: 工具基类
- `MemoryRetrievalToolRegistry`: 工具注册器
- `register_memory_retrieval_tool()`: 便捷注册函数
- `get_tool_registry()`: 获取注册器实例

### `tool_utils.py`
包含所有工具共用的工具函数：
- `parse_datetime_to_timestamp()`: 解析时间字符串为时间戳
- `parse_time_range()`: 解析时间范围字符串

### 工具文件
每个工具都有独立的文件：
- `query_jargon.py`: 根据关键词在jargon库中查询
- `query_chat_history.py`: 根据时间或关键词在chat_history中查询（支持查询时间点事件、时间范围事件、关键词搜索）

## 如何添加新工具

1. 创建新的工具文件，例如 `query_new_tool.py`：

```python
"""
新工具 - 工具实现
"""

from src.common.logger import get_logger
from .tool_registry import register_memory_retrieval_tool
from .tool_utils import parse_datetime_to_timestamp  # 如果需要使用工具函数

logger = get_logger("memory_retrieval_tools")


async def query_new_tool(param1: str, param2: str, chat_id: str) -> str:
    """新工具的实现
    
    Args:
        param1: 参数1
        param2: 参数2
        chat_id: 聊天ID
        
    Returns:
        str: 查询结果
    """
    try:
        # 实现逻辑
        return "结果"
    except Exception as e:
        logger.error(f"新工具执行失败: {e}")
        return f"查询失败: {str(e)}"


def register_tool():
    """注册工具"""
    register_memory_retrieval_tool(
        name="query_new_tool",
        description="新工具的描述",
        parameters=[
            {
                "name": "param1",
                "type": "string",
                "description": "参数1的描述",
                "required": True
            },
            {
                "name": "param2",
                "type": "string",
                "description": "参数2的描述",
                "required": True
            }
        ],
        execute_func=query_new_tool
    )
```

2. 在 `__init__.py` 中导入并注册新工具：

```python
from .query_new_tool import register_tool as register_query_new_tool

def init_all_tools():
    """初始化并注册所有记忆检索工具"""
    register_query_jargon()
    register_query_chat_history()
    register_query_new_tool()  # 添加新工具
```

3. 工具会自动：
   - 出现在 ReAct Agent 的 prompt 中
   - 在动作类型列表中可用
   - 被 ReAct Agent 自动调用

## 使用示例

```python
from src.memory_system.retrieval_tools import init_all_tools, get_tool_registry

# 初始化所有工具
init_all_tools()

# 获取工具注册器
registry = get_tool_registry()

# 获取特定工具
tool = registry.get_tool("query_chat_history")

# 执行工具（查询时间点事件）
result = await tool.execute(time_point="2025-01-15 14:30:00", chat_id="chat123")

# 或者查询关键词
result = await tool.execute(keyword="小丑AI", chat_id="chat123")

# 或者查询时间范围
result = await tool.execute(time_range="2025-01-15 10:00:00 - 2025-01-15 20:00:00", chat_id="chat123")
```

## 现有工具说明

### query_jargon
根据关键词在jargon库中查询黑话/俚语/缩写的含义
- 参数：`keyword` (必填) - 关键词

### query_chat_history
根据时间或关键词在chat_history中查询相关聊天记录。可以查询某个时间点发生了什么、某个时间范围内的事件，或根据关键词搜索消息
- 参数：
  - `keyword` (可选) - 关键词，用于搜索消息内容
  - `time_point` (可选) - 时间点，格式：YYYY-MM-DD HH:MM:SS，用于查询某个时间点附近发生了什么（与time_range二选一）
  - `time_range` (可选) - 时间范围，格式：'YYYY-MM-DD HH:MM:SS - YYYY-MM-DD HH:MM:SS'（与time_point二选一）

### query_lpmm_knowledge
从LPMM知识库中检索与关键词相关的知识内容
- 参数：
  - `query` (必填) - 查询的关键词或问题描述

## 注意事项

- 所有工具函数必须是异步函数（`async def`）
- 如果工具函数签名需要 `chat_id` 参数，系统会自动添加（通过函数签名检测）
- 工具参数定义中的 `required` 字段用于生成 prompt 描述
- 工具执行失败时应返回错误信息字符串，而不是抛出异常
- 共用函数放在 `tool_utils.py` 中，避免代码重复

